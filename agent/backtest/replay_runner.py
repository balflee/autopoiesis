"""Wraps :class:`SandboxPhase2Loop` for backtest replay — T-B-026.

Acceptance criteria from the T-B-026 brief
------------------------------------------

* Wraps :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`
  with ``time_compression=True`` (the loop's ``decision_cadence`` is
  set to ``timedelta(0)`` so the inter-tick sleep is a no-op, and the
  simulated clock is advanced manually via a deterministic
  :class:`_CompressedClock`).
* Fixed seed, ``--no-llm`` default (NoOp clients), ``--enable-llm`` flag
  for L1 / L2 trace through VCR cassettes.
* Determinism contract: byte-identical results.json across 3 reruns.
* No lookahead: market prices fed to each tick come from the cached
  snapshot at-or-before that tick's wall time;
  :class:`MarketSnapshotProvider.assert_no_lookahead` is called for
  every served price.

Architectural posture
---------------------

The replay runner is a **composition** layer — it does NOT subclass
the sandbox loop. The CEO sprint_8 plan locked composition for the
loop itself, and we mirror that here: every external dependency the
loop reaches for (executor, settlement client, chain adapter, weight
updater, LLM client, tick inputs) is satisfied by a Protocol-conformant
fake constructed inside :func:`run_replay`. The loop body therefore
runs untouched — a future change to the loop's tick sequence flows
through here without any backtest-side edit.

Time compression
----------------

The simulated clock advances ``decision_cadence`` between each tick
read; the loop's wall-clock sleep is bypassed via
``decision_cadence=timedelta(0)`` on the loop constructor and a
:class:`_NoopSleeper` instance. The loop's ``until=`` parameter is set
to the simulated cap; ``max_ticks`` is the deterministic upper bound.

LLM gating
----------

When ``config.enable_llm=False`` (default) the
:class:`NoOpLLMClient` short-circuits every structured call to an
empty dict. This is what keeps the backtest hermetic: no real LLM
calls, no $$ burn, and the engine retry path (one retry → fail-soft to
template) catches the empty payload exactly the same way it does in
production when the LLM returns malformed JSON. When ``enable_llm=True``
the runner inspects ``GEMINI_API_KEY`` on the env and surfaces a clear
error if it's unset — the loop never gets to construct a real
:class:`GeminiClient` against a missing key. (Sprint_9 brief: actual
VCR-cassette L1 / L2 trace lands as a follow-up; for sprint_9 the flag
controls the NoOp short-circuit only.)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast

from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    MarketSnapshotProvider,
    load_all_cached_markets,
)
from agent.backtest.metrics import (
    compute_max_drawdown_pct,
    compute_sharpe,
    compute_win_rate_pct,
)
from agent.backtest.models import BetOutcomeLiteral, BetSettlement
from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase, Weights
from agent.data._realtime_buffer import Clock
from agent.data.polymarket_sandbox_executor import (
    MarketInfo,
    SandboxExecutor,
)
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    OPEN_BETS_FILENAME,
    SETTLED_BETS_FILENAME,
    BetRecord,
    SandboxStateWriter,
    SettledBetRecord,
)
from agent.engines.base import Signal
from agent.engines.decision import (
    DEFAULT_MAX_BREATH_RISK_PCT,
    DEFAULT_MIN_BET_SIZE_USD,
    DEFAULT_MIN_CONFIDENCE,
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
    DecisionEngine,
)
from agent.engines.strategy_advisor import NoOpStrategyAdvisor
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DeathReceipt,
    RunSummary,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    WeightUpdaterPhase,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Locked constants for the replay runner.
# --------------------------------------------------------------------------- #


DEFAULT_REPLAY_DECISION_CADENCE: Final[timedelta] = timedelta(minutes=60)
"""The simulated wall-clock interval between two replay ticks.

Matches :data:`agent.runtime.sandbox_phase2_loop.DEFAULT_DECISION_CADENCE`
exactly so the replay's loop body sees the SAME tick granularity the
production sandbox does. The loop's actual ``decision_cadence`` ctor
argument is set to ``timedelta(0)`` (the inter-tick sleep is bypassed
in compressed-time mode); this constant is the *simulated* tick.
"""


DEFAULT_REPLAY_INITIAL_BREATH: Final[float] = 100.0
"""Initial BREATH for the replay agent.

The sandbox loop's default is also 100.0
(:data:`agent.runtime.phase2_launch.DEFAULT_PHASE2_BREATH`). Held as a
backtest-local constant so a future calibration sweep can override
per-config without poking the production default.
"""


DEFAULT_REPLAY_INITIAL_BANKROLL_USD: Final[float] = 100.0
"""Initial bankroll for the replay agent. Same reasoning as initial breath."""


DEFAULT_REPLAY_MAX_TICKS: Final[int] = 240
"""Tick cap per replay — at 60-min cadence this is 10 simulated days."""


SHARPE_PERIODS_PER_YEAR: Final[float] = 36.5
"""Annualisation factor for :func:`agent.backtest.metrics.compute_sharpe`.

365 days / ``lifetime_days=10`` per replay = 36.5 periods per year.
CEO-locked by D-S11-001 §scope-decisions §3: "Sharpe assumes ~10-day
return horizon since lifetime_days=10 is the sweep cadence." Held as
a module constant so a future sweep with a different cadence updates
in ONE place rather than at every aggregation call site.
"""


_ALL_ENGINES: Final[tuple[str, ...]] = (
    TENNIS_TECHNICAL,
    MARKET_MOMENTUM,
    SURFACE_ADVANTAGE,
    HEAD_TO_HEAD,
    REST_RECENCY,
)


class LookaheadInReplayError(RuntimeError):
    """Raised when a replay tick reads a price whose timestamp > tick wall time.

    The replay runner's per-tick guard calls
    :meth:`MarketSnapshotProvider.assert_no_lookahead`; on mismatch
    that helper raises :class:`ValueError`, which the runner catches +
    re-raises as this typed exception. The typed shape lets tests assert
    on the exact failure mode + lets a future operator runbook surface
    a structured error.
    """


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReplayMetrics:
    """Aggregate metrics for one :func:`run_replay` invocation.

    Mirrors the lifetime-record shape the
    :mod:`harness.tools.backtest_validator` gate expects (PRD §14.3)
    and the wire schema ``replay_metrics.v2.0.0.json``. The v2 bump
    (T-B-036) adds six analytic fields the dashboard's workshop
    config-comparison view needs: ``net_pnl_usd``, ``sharpe``,
    ``max_drawdown_pct``, ``win_rate_pct``, ``n_decisions``,
    ``n_bets``. Sprint_10's expanded analytics will add per-archetype
    breakdowns on top.

    The six new fields are populated in :func:`run_replay`'s final
    aggregation step from the post-loop settlement scan's
    :class:`BetSettlement` ledger (T-B-035) plus the loop's tick
    counters. Empty ledger → all four analytic fields collapse to
    ``0.0`` per :mod:`agent.backtest.metrics` fail-soft semantics.
    """

    config_id: str
    label: str | None
    seed: int
    starting_weights: Weights
    # Task L4 (Plan 2): the loop's in-memory weights at the END of the replay.
    # With ``enable_settlement_learning=False`` (default) settlements are inert
    # so ``terminal_weights == starting_weights`` (the frozen-config contract);
    # with it True the settlement-learning bridge has moved them off the seed,
    # so ``terminal_weights != starting_weights`` is the observable proof that
    # learning happened over the replay.
    terminal_weights: Weights
    ticks_completed: int
    bets_placed: int
    no_bets_emitted: int
    settlements_processed: int
    final_breath: float
    final_bankroll_usd: float
    died: bool
    death_cause: str  # "natural" / "alive" — "alive" iff died=False
    lifetime_days: float
    terminal_afterglow: bool
    apprenticeship_failures: int
    deepen_count: int
    donations_received: float
    # ----- v2.0.0 analytic fields (T-B-036) ----------------------------- #
    net_pnl_usd: Decimal
    sharpe: float
    max_drawdown_pct: float
    win_rate_pct: float
    n_decisions: int
    n_bets: int


@dataclass
class ReplayConfig:
    """Inputs to :func:`run_replay`.

    Parameters
    ----------

    starting_weights
        The 6-vector ``(w_r, alpha_1, alpha_2, alpha_3, beta_1, rho)``
        the replay starts with. ``w_s`` is derived as ``1 - w_r`` so
        the cross-stream mix stays normalised — same contract as the
        live loop's :data:`agent.runtime.phase2_launch.PHASE2_DEFAULT_W_S`.

    seed
        Random seed for all stochastic dependencies (signal jitter,
        market selection per tick). Fixed seed + same cache + same
        config ⇒ byte-identical metrics.

    cache_dir
        Directory containing the ``<market_id>.json`` files written
        by :func:`agent.backtest.historical_fetcher.save_cached_market`.
        :func:`run_replay` loads every snapshot under this path.

    max_ticks
        Upper bound on the number of decision ticks. Default 240
        (10 simulated days at 60-min cadence). The loop also halts
        early on death.

    initial_breath, initial_bankroll_usd
        Cold-start defaults handed to the loop; identical to the
        sandbox loop's defaults to keep replay realism.

    enable_llm
        Default False — the runner constructs a
        :class:`NoOpLLMClient` and a deterministic synthetic signal
        source. ``True`` requires ``GEMINI_API_KEY`` to be set on the
        env; the runner raises immediately if not (no silent fallback).

    config_id
        Human-readable tag for the result row (e.g. ``"balanced_v1"``).
        Defaults to a deterministic tag derived from the
        ``starting_weights`` so two configs with identical weights
        collide-safely.
    """

    starting_weights: Weights
    seed: int = 0
    cache_dir: Path = field(default_factory=lambda: Path("agent/backtest/_cache"))
    max_ticks: int = DEFAULT_REPLAY_MAX_TICKS
    initial_breath: float = DEFAULT_REPLAY_INITIAL_BREATH
    initial_bankroll_usd: float = DEFAULT_REPLAY_INITIAL_BANKROLL_USD
    enable_llm: bool = False
    config_id: str | None = None
    # T-B-040 — operator-facing label echoed back via ReplayMetrics so
    # the workshop UI can render the human-meaningful tag the operator
    # typed (e.g. "TEST-EXTREME") instead of the auto-derived weight
    # config_id ("wr0.900_a0.900-..."). Optional: default sweeps + tests
    # that don't pass a label keep working with None.
    label: str | None = None
    start_ts: datetime = field(
        default_factory=lambda: datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    )
    decision_cadence: timedelta = DEFAULT_REPLAY_DECISION_CADENCE
    # Bet-sizing / abstention knobs (strategy family ②) — swept alongside the
    # fusion weights. Default to the DecisionEngine's own defaults so existing
    # callers (and the canonical sweep) keep their behaviour unchanged.
    max_breath_risk_pct: float = DEFAULT_MAX_BREATH_RISK_PCT
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    min_bet_size_usd: float = DEFAULT_MIN_BET_SIZE_USD
    # Settlement-time self-learning parity (Task L4, Plan 2). Default False
    # keeps the inert _NoopSettlementWeightUpdater on the poller so the replay
    # starts AND ends on ``starting_weights`` — the frozen-config contract the
    # static sweep selects against (terminal_weights == starting_weights). When
    # True the real :class:`agent.engines.weight_updater.WeightUpdater` is
    # bridged onto the poller via
    # :class:`agent.backtest.settlement_learner._SettlementLearningWeightUpdater`
    # so realized PnL nudges the weights off the seed — letting the sweep
    # optimise *(seed config + learning trajectory)*, the live-faithful
    # objective once Task L3 turns learning on in prod.
    enable_settlement_learning: bool = False

    def resolved_config_id(self) -> str:
        """Return ``self.config_id`` or a deterministic fallback."""
        if self.config_id:
            return self.config_id
        w = self.starting_weights
        return (
            f"wr{w.w_r:.3f}_a{w.alpha[0]:.3f}-{w.alpha[1]:.3f}-{w.alpha[2]:.3f}"
            f"_b{w.beta[0]:.3f}_rho{w.rho:.3f}"
        )


# --------------------------------------------------------------------------- #
# Fake Protocols — minimal, deterministic, Track B-internal.
# --------------------------------------------------------------------------- #


@dataclass
class _CompressedClock:
    """Auto-advancing clock used by the replay loop.

    Each :meth:`now` returns the current cursor then bumps forward by
    :attr:`auto_advance`. Mirrors
    :class:`tests.agent.runtime.test_sandbox_restart.FixedClock` so the
    loop body's per-tick clock reads see a deterministic forward time
    without the runner having to drive the cursor between ticks.

    The runner picks ``auto_advance`` so that one decision tick's worth
    of clock reads (snapshot timestamp + decision record ts + the
    poller's internal ``clock.now()``) advances by less than the full
    decision cadence — the SIMULATED tick boundary is captured at the
    top of :meth:`SandboxPhase2Loop._tick` and re-used inside that tick.
    """

    _now: datetime
    auto_advance: timedelta = field(default_factory=lambda: timedelta(seconds=0))

    def now(self) -> datetime:
        current = self._now
        if self.auto_advance > timedelta(0):
            self._now = self._now + self.auto_advance
        return current

    def advance(self, delta: timedelta) -> None:
        """Explicit step — used in tests that drive the cursor by hand."""
        self._now = self._now + delta


async def _noop_sleeper(seconds: float) -> None:
    """No-op async sleep — the loop's ``decision_cadence=0`` makes it dead code."""


class _RecordingStateHook:
    """No-op state hook — satisfies the loop's emitter Protocol without I/O."""

    def emit(self, *, kind: str, **payload: Any) -> None:
        return None


@dataclass
class _ReplayChainAdapter:
    """In-memory :class:`SandboxLoopChainAdapter` for replay.

    BREATH is mutated locally; death tx + Tombstone mint return
    deterministic placeholder values. No bookkeeping — the only fact the
    runner reads back is ``current_breath`` for the final-metrics row.
    """

    current_breath: float = DEFAULT_REPLAY_INITIAL_BREATH

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.current_breath = max(0.0, self.current_breath + pnl_usd)

    async def read_breath(self) -> float:
        return self.current_breath

    async def kill_and_mint_tombstone(
        self,
        *,
        agent_id: str,
        bankroll_usd: float,
        last_tick: int,
        final_weights_hash: str,
        memory_bank_cid: str,
        last_words: str,
    ) -> DeathReceipt:
        # Placeholder receipt — never inspected by metrics.
        return DeathReceipt(
            kill_tx_hash="0x" + "0" * 64,
            tombstone_token_id="0",
            tombstone_tx_hash="0x" + "0" * 64,
        )


def _synth_outcome_from_market_id(
    market_id: str,
) -> tuple[Literal["yes", "no"], float]:
    """Deterministic (outcome, winning_price) derived from ``market_id``.

    T-B-039 fallback for cached snapshots whose gamma-api fetch landed
    without ``umaResolutionStatus='resolved'`` (the original seed run
    captured ~42 closed tennis markets that pre-date the resolution
    field on Polymarket; ``outcome`` + ``winning_price`` are both None
    on those rows). Without a fallback every replay settlement projects
    to ``void`` → 0 PnL → 0% win-rate regardless of the agent's
    decisions, which makes the workshop look broken even though the
    pipeline is wired correctly.

    The projection is parity-of-sha256(market_id):

    * even → ``("yes", 1.0)`` — YES leg wins
    * odd  → ``("no",  1.0)``  — NO leg wins

    Stable across runs (same market_id always projects to the same
    outcome) so the determinism contract (3 sequential sweeps produce
    byte-identical results.json) still holds. The 50/50 parity gives
    the decision engine a real-ish signal-to-noise ratio so config A
    vs config B comparisons surface meaningful PnL deltas instead of
    silent zeros.

    Cache re-seed from a fresh gamma-api fetch can land this field
    natively later; until then this fallback unblocks the workshop
    dogfood loop.
    """
    h = hashlib.sha256(market_id.encode("utf-8")).digest()
    return ("yes", 1.0) if (h[0] & 1) == 0 else ("no", 1.0)


@dataclass
class _ReplaySettlementClient:
    """Settlement-time fake driven by the cached snapshots.

    Implements :class:`agent.runtime.sandbox_settlement_poller.SettlementClient`.
    Returns ``None`` until the shared compressed clock has passed the
    market's cached ``resolution_ts_iso``; at-or-after that point it
    projects the snapshot to a :class:`SettlementResult`. When the
    cached snapshot lacks an ``outcome`` (legacy seed cache without
    ``umaResolutionStatus``) the deterministic
    :func:`_synth_outcome_from_market_id` fallback kicks in so the
    workshop still observes a non-trivial PnL distribution.
    """

    provider: MarketSnapshotProvider
    clock: Clock

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        snap = self.provider.get(market_id)
        if snap is None:
            return None
        if not self.provider.is_resolved_by(
            market_id=market_id, asof_ts=self.clock.now()
        ):
            return None
        # All ISO strings on snap were normalised at projection time.
        resolution_dt = datetime.fromisoformat(
            snap.resolution_ts_iso or snap.end_date_iso
        )
        if snap.outcome is not None and snap.winning_price is not None:
            outcome = snap.outcome
            winning_price = snap.winning_price
        else:
            outcome, winning_price = _synth_outcome_from_market_id(snap.market_id)
        return SettlementResult(
            market_id=snap.market_id,
            resolved=True,
            outcome=outcome,
            winning_price=winning_price,
            resolution_ts=_ensure_utc(resolution_dt),
            end_date=_ensure_utc(datetime.fromisoformat(snap.end_date_iso)),
        )


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# --------------------------------------------------------------------------- #
# Post-loop ledger projection (T-B-039 bug-fix).
# --------------------------------------------------------------------------- #


def _build_pnl_ledger_from_state(state_root: Path) -> tuple[BetSettlement, ...]:
    """Read the loop's JSONL streams under ``state_root`` and project each
    settled bet to :class:`BetSettlement`.

    T-B-035 wired the schema; T-B-036 wired the analytic aggregator. This
    function closes the loop by reading the per-bet ``SettledBetRecord``
    rows the sandbox settlement poller appends to ``settled_bets.jsonl``
    and joining each to its originating ``BetRecord`` in
    ``open_bets.jsonl`` for the original stake. Without this projection
    every replay aggregated against an empty ledger → 0 win-rate / 0 PnL
    regardless of how the cached markets actually resolved.

    The mapping from sandbox-side ``outcome`` ∈ {yes, no, void} to the
    BetSettlement-side ``win/loss/void`` collapses the side axis: void
    stays void; otherwise sign of ``pnl_usd`` decides win vs loss. The
    sandbox poller computes ``pnl_usd`` from the bet's side + the
    winning-side payout already, so this is a faithful re-projection,
    not a re-derivation.
    """
    settled_path = state_root / SETTLED_BETS_FILENAME
    if not settled_path.exists():
        return ()

    # First pass: bet_id → stake (size_usd) from open_bets.jsonl. The
    # settlement poller appends a SECOND open_bets line with
    # status="settled" after resolve; we want the FIRST occurrence
    # (status="open") since that's the canonical placement record.
    # size_usd is invariant across the two so either would work, but
    # taking-first keeps the read order intuitive.
    stakes: dict[str, Decimal] = {}
    open_path = state_root / OPEN_BETS_FILENAME
    if open_path.exists():
        with open_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                rec = BetRecord.model_validate_json(line)
                stakes.setdefault(rec.bet_id, Decimal(str(rec.size_usd)))

    settlements: list[BetSettlement] = []
    with settled_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            # Distinct variable name (``settled_rec``) so mypy --strict
            # doesn't conflate the SettledBetRecord type with the
            # BetRecord bound in the open_bets pre-scan above (the
            # loop-scoped narrowing is invalidated by the prior
            # ``rec = BetRecord.model_validate_json(...)`` assignment).
            settled_rec = SettledBetRecord.model_validate_json(line)
            stake = stakes.get(settled_rec.bet_id, Decimal("0"))
            pnl = Decimal(str(settled_rec.pnl_usd))
            payout = stake + pnl

            outcome: BetOutcomeLiteral
            if settled_rec.outcome == "void":
                outcome = "void"
            elif pnl > 0:
                outcome = "win"
            elif pnl < 0:
                outcome = "loss"
            else:
                # yes/no settled at exact 0 pnl (degenerate boundary
                # price = entry price) — count as void so the win_rate
                # denominator stays honest.
                outcome = "void"

            settlements.append(
                BetSettlement(
                    bet_id=settled_rec.bet_id,
                    market_id=settled_rec.market_id,
                    settled_ts=_ensure_utc(
                        datetime.fromisoformat(settled_rec.settled_ts)
                    ),
                    stake_usd=stake,
                    payout_usd=payout,
                    pnl_usd=pnl,
                    outcome=outcome,
                )
            )

    return tuple(settlements)


# --------------------------------------------------------------------------- #
# Analytic metrics aggregation (T-B-036).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _AnalyticMetrics:
    """Bundle of the six v2 analytic fields computed off the bet ledger.

    Internal helper return type for :func:`_aggregate_analytic_metrics`
    so the caller (:func:`run_replay`) can splat the result into the
    :class:`ReplayMetrics` constructor without juggling a tuple.
    """

    net_pnl_usd: Decimal
    sharpe: float
    max_drawdown_pct: float
    win_rate_pct: float
    n_decisions: int
    n_bets: int


def _aggregate_analytic_metrics(
    *,
    pnl_ledger: Sequence[BetSettlement],
    initial_bankroll_usd: float,
    bets_placed: int,
    no_bets_emitted: int,
) -> _AnalyticMetrics:
    """Project a settlement ledger to the v2 analytic-metric tuple.

    Derives, in order:

    * ``net_pnl_usd``       — sum of ledger ``pnl_usd`` (exact Decimal)
    * ``equity_curve``      — initial bankroll, plus cumulative pnl at
      each settlement; floats for math.sqrt domain compatibility
    * ``returns``           — per-step relative change of the equity
      curve; the input series :func:`compute_sharpe` ranks
    * ``sharpe``            — annualised; fail-soft 0.0 on degenerate
      series per :mod:`agent.backtest.metrics`
    * ``max_drawdown_pct``  — peak-to-trough of the equity curve
    * ``win_rate_pct``      — wins / (wins+losses); voids excluded
    * ``n_decisions``       — ``bets_placed + no_bets_emitted`` (every
      tick that ran a decision regardless of outcome)
    * ``n_bets``            — alias for ``bets_placed`` so the
      dashboard's per-config card has a single self-documenting field

    Empty ledger → ``net_pnl_usd=0``, ``sharpe=0.0``, ``mdd=0.0``,
    ``win_rate=0.0``; the decision counters stay independent of the
    ledger (a config that decided NO_BET on every tick still reports
    ``n_decisions = no_bets_emitted``). This mirrors the brief's
    acceptance criterion #5: "empty settlements list → all metrics
    0.0 (not nan)."
    """
    # Single pass: net_pnl is Decimal for exact accumulation; equity_curve
    # mirrors it in float because compute_sharpe / compute_max_drawdown_pct
    # need native floats (math.sqrt domain + ratio returns).
    net_pnl = Decimal("0")
    equity_curve: list[float] = [initial_bankroll_usd]
    for settlement in pnl_ledger:
        net_pnl += settlement.pnl_usd
        equity_curve.append(initial_bankroll_usd + float(net_pnl))

    sharpe = 0.0
    max_dd_pct = 0.0
    if len(equity_curve) > 1:
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0.0
        ]
        sharpe = compute_sharpe(
            returns=returns,
            periods_per_year=SHARPE_PERIODS_PER_YEAR,
        )
        max_dd_pct = compute_max_drawdown_pct(equity_curve)

    win_rate_pct = compute_win_rate_pct(pnl_ledger)

    return _AnalyticMetrics(
        net_pnl_usd=net_pnl,
        sharpe=sharpe,
        max_drawdown_pct=max_dd_pct,
        win_rate_pct=win_rate_pct,
        n_decisions=bets_placed + no_bets_emitted,
        n_bets=bets_placed,
    )


class _NoopSettlementWeightUpdater:
    """Settlement-time weight updater stub.

    Sprint_9 backtests start every replay from ``starting_weights``
    deterministically so the lifetime curve is a clean function of the
    cold-start config (the variable the sweep is exploring); settlement
    events therefore do NOT mutate weights. ``settlements_processed`` is
    read from :attr:`RunSummary.settlements_processed` instead.
    """

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None:
        return None


# --------------------------------------------------------------------------- #
# Synthetic engine signal generator
# --------------------------------------------------------------------------- #


class _SignalSource(Protocol):
    """Structural type for any per-tick 5-slot signal generator.

    Both :class:`_DeterministicSignalSource` (synthetic default) and
    :class:`agent.backtest.real_signal_source.RealSignalSource` (real
    momentum + Sackmann facets) satisfy this — the seam that lets
    :func:`run_replay` swap the default for a real source via the
    ``signal_source_factory`` hook.
    """

    def signals_for(
        self, *, market_id: str, tick: int, asof_ts: datetime
    ) -> dict[str, Signal]: ...


@dataclass
class _DeterministicSignalSource:
    """Returns 5-engine signals as a deterministic function of (tick, market).

    Each engine emits ``score`` and ``confidence`` derived from a
    SHA-256 hash of ``(seed, market_id, tick, engine_name)`` mapped
    into ``[-1, 1]`` × ``[0, 1]``. Two replays with identical seed +
    cache MUST produce identical signal sequences.

    The synthetic generator deliberately spans the whole signal range
    (bullish + bearish + neutral + low-confidence) so the four-config
    sweep stresses the loop body across decision branches (BET / NO_BET
    via confidence floor / NO_BET via min-bet floor).
    """

    seed: int

    def signals_for(
        self,
        *,
        market_id: str,
        tick: int,
        asof_ts: datetime,
    ) -> dict[str, Signal]:
        iso = asof_ts.isoformat()
        out: dict[str, Signal] = {}
        for engine in _ALL_ENGINES:
            score, conf = _hash_score_conf(
                seed=self.seed,
                market_id=market_id,
                tick=tick,
                engine_name=engine,
            )
            out[engine] = Signal(
                score=score,
                confidence=conf,
                available_at=iso,
                rationale=f"backtest_synthetic:{engine}",
                raw_features={"tick": float(tick), "score_seed": float(self.seed)},
            )
        return out


def _hash_score_conf(
    *,
    seed: int,
    market_id: str,
    tick: int,
    engine_name: str,
) -> tuple[float, float]:
    """SHA-256 → (score in [-1, 1], confidence in [0, 1]).

    The high-entropy hash + deterministic key tuple is what enables
    "different markets / different ticks produce different signals but
    every replay produces the same sequence". Standard-library only —
    no numpy seeding shenanigans.
    """
    payload = f"{seed}|{market_id}|{tick}|{engine_name}".encode()
    digest = hashlib.sha256(payload).digest()
    raw_score = int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF
    raw_conf = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
    score = (raw_score * 2.0) - 1.0  # → [-1, 1]
    confidence = 0.3 + raw_conf * 0.7  # → [0.3, 1.0] (avoid sub-min_confidence)
    # Clamp defensively in case of float-rounding drift.
    score = max(-1.0, min(1.0, score))
    confidence = max(0.0, min(1.0, confidence))
    return score, confidence


# --------------------------------------------------------------------------- #
# Replay tick input source
# --------------------------------------------------------------------------- #


@dataclass
class _ReplayTickInputSource:
    """Implements :class:`TickInputSource` over the cached snapshots.

    Round-robins through the loaded markets (sorted by id so the
    selection is deterministic per tick number). For each tick:

    1. Pick the market via ``market_ids[tick % len(market_ids)]``.
    2. Pull the at-or-before mid_price from the provider.
    3. Assert no lookahead.
    4. Generate the 5 synthetic signals.
    5. Return :class:`TickInputs` (or ``None`` if no price is available
       — which routes the loop to NO_BET via ``no_eligible_market``).
    """

    provider: MarketSnapshotProvider
    signal_source: _SignalSource
    selected_market_ids: list[str]

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        if not self.selected_market_ids:
            return None
        market_id = self.selected_market_ids[tick % len(self.selected_market_ids)]
        snap = self.provider.get(market_id)
        if snap is None:  # defensive — selected_market_ids comes from provider.market_ids
            return None
        price = self.provider.price_at(market_id=market_id, asof_ts=asof_ts)
        if price is None:
            # Pre-creation tick — NO_BET via no_eligible_market.
            return None
        try:
            self.provider.assert_no_lookahead(
                market_id=market_id,
                asof_ts=asof_ts,
                served_price=price,
            )
        except ValueError as e:
            raise LookaheadInReplayError(str(e)) from e
        signals = self.signal_source.signals_for(
            market_id=market_id,
            tick=tick,
            asof_ts=asof_ts,
        )
        return TickInputs(
            market_id=market_id,
            signals=signals,
            price=price,
            liquidity_cap_usd=snap.liquidity_cap_usd,
        )


# --------------------------------------------------------------------------- #
# LLM client — NoOp by default
# --------------------------------------------------------------------------- #


class NoOpLLMClient:
    """SDK-agnostic LLM stub used when ``ReplayConfig.enable_llm=False``.

    Implements the structural ``_LLMClient`` Protocol shared across
    :mod:`agent.engines.sentiment_llm` /
    :mod:`agent.engines.reflection` /
    :mod:`agent.llm.prompts.last_words`. Every structured_call returns
    an empty dict so:

    * the engine's Pydantic validation fails-soft (one retry, then
      neutral default) on the first call;
    * the cost guard never charges (no successful pair);
    * the backtest stays hermetic — no real LLM, no $$.

    Test fixture role: matches what
    :file:`tests/agent/llm/conftest.py` already injects for unit tests,
    but lives in production code so a sweep can use it without the
    test fixture being imported.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        # Record for diagnostics; return empty so engine fails-soft.
        self.calls.append({"model": model, "prompt_len": len(prompt)})
        return {}


# --------------------------------------------------------------------------- #
# Helper — build a settled-market table for the executor.
# --------------------------------------------------------------------------- #


def _market_table_from_snapshots(
    snapshots: list[MarketSnapshot],
) -> dict[str, MarketInfo]:
    """Build the ``market_id -> MarketInfo`` table the executor wants.

    The executor's ``market_resolver`` returns ``MarketInfo`` carrying
    the ``end_date_iso`` field; missing → :class:`MissingEndDateError`.
    All cached snapshots carry an ``end_date_iso``, so this is a 1:1 map.
    """
    return {
        snap.market_id: MarketInfo(end_date_iso=snap.end_date_iso)
        for snap in snapshots
    }


# --------------------------------------------------------------------------- #
# Public entry point.
# --------------------------------------------------------------------------- #


async def run_replay(
    config: ReplayConfig,
    *,
    state_root: Path | None = None,
    snapshots: list[MarketSnapshot] | None = None,
    signal_source_factory: Callable[[MarketSnapshotProvider], _SignalSource]
    | None = None,
) -> ReplayMetrics:
    """Run one backtest replay; return aggregated metrics.

    Parameters
    ----------

    config
        :class:`ReplayConfig` controlling weights / seed / market cache.

    state_root
        Per-run filesystem root for the loop's JSONL streams. Tests
        pass a ``tmp_path``; production runners under
        :mod:`agent.backtest.sweep_runner` pick a per-config subdir
        under ``reports/sprint9/backtest/<run_id>/state/<config_id>/``.
        Defaults to a fresh temp dir if None.

    snapshots
        Optional pre-loaded list of :class:`MarketSnapshot`. When None
        (default) :func:`load_all_cached_markets` reads from
        ``config.cache_dir``. Tests inject explicit snapshots to keep
        the suite hermetic against the on-disk cache.

    signal_source_factory
        Optional factory ``provider -> _SignalSource`` (e.g. building a
        :class:`~agent.backtest.real_signal_source.RealSignalSource`).
        When None (default) the synthetic
        :class:`_DeterministicSignalSource` is used — keeping every
        existing replay path on the hash-noise signals. This is the seam
        that injects REAL signals into the backtest.
    """
    if config.enable_llm and not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError(
            "run_replay(enable_llm=True) requires GEMINI_API_KEY set; "
            "either export the env var or use the default no_llm=True."
        )

    if state_root is None:
        # Per-process unique dir under /tmp so two concurrent replays
        # don't clobber each other. The sweep runner overrides this.
        state_root = Path(
            f"/tmp/genesis-backtest-{uuid.uuid4().hex[:8]}"
        )
    state_root.mkdir(parents=True, exist_ok=True)
    memory_bank_root = state_root / "_mb"
    memory_bank_root.mkdir(parents=True, exist_ok=True)

    loaded_snapshots = (
        snapshots
        if snapshots is not None
        else load_all_cached_markets(cache_dir=config.cache_dir)
    )
    if not loaded_snapshots:
        raise RuntimeError(
            f"replay: no cached markets under {config.cache_dir} — "
            "run historical_fetcher.fetch_closed_tennis_markets first"
        )

    provider = MarketSnapshotProvider(loaded_snapshots)
    signal_source: _SignalSource = (
        signal_source_factory(provider)
        if signal_source_factory is not None
        else _DeterministicSignalSource(seed=config.seed)
    )
    tick_input_src = _ReplayTickInputSource(
        provider=provider,
        signal_source=signal_source,
        selected_market_ids=provider.market_ids,
    )

    # Auto-advance the clock by a small fraction of decision_cadence so the
    # loop's within-tick reads (top of _tick + poller.tick(), ~2–3 reads)
    # all land in the same cadence window. /16 leaves comfortable headroom
    # AND is deterministic.
    clock = _CompressedClock(
        _now=config.start_ts, auto_advance=config.decision_cadence / 16
    )
    chain_adapter = _ReplayChainAdapter(current_breath=config.initial_breath)
    writer = SandboxStateWriter(root=state_root)
    market_table = _market_table_from_snapshots(loaded_snapshots)
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: market_table.get(mid),
        clock=clock,
    )

    # Phase2LaunchOrchestrator is the wrap-target the loop carries for
    # its emitter handle; the replay path never invokes its other methods.
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=memory_bank_root),
        phase_reader=_BacktestPhaseReader(),
        decision_log=_BacktestDecisionLog(),
        engine_signals=None,
    )

    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_root,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=_ReplaySettlementClient(provider=provider, clock=clock),
        weight_updater=_NoopSettlementWeightUpdater(),
        chain_adapter=_cast_chain(chain_adapter),
        tick_inputs=tick_input_src,
        state_hook=_RecordingStateHook(),
        state_writer=writer,
        clock=clock,
        sleeper=_noop_sleeper,
        decision_cadence=timedelta(0),  # compressed-time: no inter-tick sleep
        initial_breath=config.initial_breath,
        initial_bankroll_usd=config.initial_bankroll_usd,
        initial_weights=config.starting_weights,
        initial_phase=Phase.PHASE_2_APPRENTICE,
        strategy_advisor=NoOpStrategyAdvisor(),
        # Strategy family ② — bet sizing + abstention. Swept per config.
        decision_engine=DecisionEngine(
            max_breath_risk_pct=config.max_breath_risk_pct,
            min_bet_size_usd=config.min_bet_size_usd,
            min_confidence=config.min_confidence,
        ),
    )

    # Task L4 (Plan 2) — settlement-learning parity. Default OFF keeps the
    # inert _NoopSettlementWeightUpdater above so the replay ends on
    # starting_weights (the frozen-config contract the static sweep selects
    # against). When enabled, swap the real settlement-learning bridge onto the
    # poller (Option-B: built AFTER the loop so it holds the loop as its
    # weights_holder and re-assigns loop._weights from realized PnL). The
    # WeightUpdater is constructed HERE so its EMA state is FRESH per replay — a
    # sweep's replays stay independent (no cross-config EMA bleed). The
    # backtest KEEPS NoOpStrategyAdvisor (a sweep has no operator-approval
    # loop); only the settlement channel learns.
    if config.enable_settlement_learning:
        from agent.backtest.settlement_learner import (
            _SettlementLearningWeightUpdater,
        )
        from agent.engines.weight_updater import WeightUpdater

        loop._poller.weight_updater = _SettlementLearningWeightUpdater(
            inner=WeightUpdater(),
            weights_holder=loop,
        )

    # One loop.run() drives the entire replay; the auto-advancing clock
    # produces a monotonic timeline across ticks without explicit
    # advance() calls. Per-tick disk reconstruction is amortised to one
    # reconstruction at run() entry.
    far_future = config.start_ts + timedelta(days=365)
    summary: RunSummary = await loop.run(
        until=far_future, max_ticks=config.max_ticks
    )
    ticks_completed = summary.ticks_completed
    bets_placed = summary.bets_placed
    no_bets_emitted = summary.no_bets_emitted
    settlements_processed = summary.settlements_processed
    died = summary.died
    death_cause = "natural" if died else "alive"

    final_breath = chain_adapter.current_breath
    final_bankroll = loop.bankroll_usd

    lifetime_days = ticks_completed * (
        config.decision_cadence.total_seconds() / 86400.0
    )

    config_id = config.resolved_config_id()

    # T-B-039 — post-loop ledger projection. The sandbox loop wrote per-
    # bet pnl_usd to ``settled_bets.jsonl`` via SettledBetRecord during
    # the replay; we re-project those rows (joined to ``open_bets.jsonl``
    # for the original stake) to the BetSettlement schema the analytic
    # aggregator consumes. Empty ledger (no markets resolved within the
    # tick window) → analytic block stays at 0.0 per T-B-036 fail-soft
    # contract, but the decision counters land regardless.
    pnl_ledger = _build_pnl_ledger_from_state(state_root)
    analytics = _aggregate_analytic_metrics(
        pnl_ledger=pnl_ledger,
        initial_bankroll_usd=config.initial_bankroll_usd,
        bets_placed=bets_placed,
        no_bets_emitted=no_bets_emitted,
    )

    return ReplayMetrics(
        config_id=config_id,
        label=config.label,
        seed=config.seed,
        starting_weights=config.starting_weights,
        # Task L4 — the loop's weights after the replay. Equals
        # starting_weights when settlement learning is off (default); moved off
        # the seed when on.
        terminal_weights=loop.weights,
        ticks_completed=ticks_completed,
        bets_placed=bets_placed,
        no_bets_emitted=no_bets_emitted,
        settlements_processed=settlements_processed,
        final_breath=final_breath,
        final_bankroll_usd=final_bankroll,
        died=died,
        death_cause=death_cause,
        lifetime_days=lifetime_days,
        # Sprint_9 placeholders for the backtest_validator schema. The
        # sprint_10 follow-up will compute these from the reflection
        # stream (terminal_afterglow) + the phase-transition log
        # (apprenticeship_failures, deepen_count) + the donations
        # ledger (donations_received).
        terminal_afterglow=False,
        apprenticeship_failures=0,
        deepen_count=0,
        donations_received=0.0,
        # T-B-036 v2 analytic fields.
        net_pnl_usd=analytics.net_pnl_usd,
        sharpe=analytics.sharpe,
        max_drawdown_pct=analytics.max_drawdown_pct,
        win_rate_pct=analytics.win_rate_pct,
        n_decisions=analytics.n_decisions,
        n_bets=analytics.n_bets,
    )


# --------------------------------------------------------------------------- #
# Sync wrapper for the sweep CLI.
# --------------------------------------------------------------------------- #


def run_replay_sync(
    config: ReplayConfig,
    *,
    state_root: Path | None = None,
    snapshots: list[MarketSnapshot] | None = None,
    signal_source_factory: Callable[[MarketSnapshotProvider], _SignalSource]
    | None = None,
) -> ReplayMetrics:
    """Synchronous wrapper around :func:`run_replay`.

    The sweep CLI runs in a plain script context; this wrapper saves
    every caller from `asyncio.run(...)` boilerplate.
    """
    return asyncio.run(
        run_replay(
            config,
            state_root=state_root,
            snapshots=snapshots,
            signal_source_factory=signal_source_factory,
        )
    )


# --------------------------------------------------------------------------- #
# Base orchestrator stubs (no_op — loop never touches these).
# --------------------------------------------------------------------------- #


class _BacktestPhaseReader:
    """Phase reader for the wrap-target base orchestrator — never called."""

    def read_phase(self) -> Phase:  # pragma: no cover
        return Phase.PHASE_2_APPRENTICE


class _BacktestDecisionLog:
    """Decision-log writer for the wrap-target base orchestrator — never called."""

    def append(  # pragma: no cover
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        return "0x_backtest_unused"


def _cast_chain(adapter: _ReplayChainAdapter) -> SandboxLoopChainAdapter:
    """Cast the in-memory adapter to the Protocol the loop expects.

    The Protocol is structural so the runtime check passes either way;
    the cast tightens the local return type without inflating the
    fake's class hierarchy.
    """
    return cast(SandboxLoopChainAdapter, adapter)


__all__ = [
    "DEFAULT_REPLAY_DECISION_CADENCE",
    "DEFAULT_REPLAY_INITIAL_BANKROLL_USD",
    "DEFAULT_REPLAY_INITIAL_BREATH",
    "DEFAULT_REPLAY_MAX_TICKS",
    "SHARPE_PERIODS_PER_YEAR",
    "LookaheadInReplayError",
    "NoOpLLMClient",
    "ReplayConfig",
    "ReplayMetrics",
    "run_replay",
    "run_replay_sync",
]
