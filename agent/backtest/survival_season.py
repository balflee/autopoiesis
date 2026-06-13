"""L5 Survival-Journey engine (Phase A).

This module is built INCREMENTALLY across Phase A:

* **A0 (this commit)** — :class:`SurvivalRow`, the joined per-market schema that
  the survival runner schedules over, plus :func:`build_survival_rows`. A
  ``SurvivalRow`` JOINs the cached signal scores/settlement copy (the
  :class:`~agent.backtest.cached_sweep.SignalRow`, embedded — NOT mutated) to
  the :class:`~agent.backtest.historical_fetcher.MarketSnapshot` settlement
  fields, a deterministically-RECOMPUTED ``entry_asof_ts_iso`` (asserted
  consistent with the row's cached ``entry_price``), and display players +
  surface (nullable, UI fallback).
* **A1 (this commit)** — :class:`SurvivalSchedule` /
  :func:`build_survival_schedule` (the entry-time-ordered list of decision +
  settle-only stops), :class:`SurvivalTickSource` (a ``TickInputSource``-
  compatible source serving the CACHED signals per market — no Sackmann
  recompute — and ``None`` on settle-only stops → NO_BET via
  ``no_eligible_market``), and :class:`_ControllableClock` (the forward-only
  clock the runner pins to each stop). Decisions are scheduled by ENTRY time
  (``entry_asof_ts_iso``), NOT settlement time, so the loop's single ``now``
  decides the current market while the poller settles earlier bets in flight.
* A2 — multi-life fresh-loop respawn (cross-death weight/EMA persistence).
* A3 — survival recorder + journey schema.
* A4 — CLI + export.

A0 introduces a NEW dataclass rather than extending ``SignalRow``: the cached
``reports/backtest/_signal_rows.json`` is serialised field-for-field off
``SignalRow`` (``cached_sweep.save_rows``/``load_rows``), so adding fields there
would break loading the existing 4925-row cache. The join is therefore by
COMPOSITION — the ``SignalRow`` is carried verbatim on ``SurvivalRow.signal``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random as _random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from agent.backtest.cached_sweep import (
    SignalRow,
    _entry_asof,
    compute_bet_pnl,
    effective_entry_price,
    load_rows,
    row_to_signals,
)
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    MarketSnapshotProvider,
    load_all_cached_markets,
)
from agent.backtest.replay_runner import (
    _BacktestDecisionLog,
    _BacktestPhaseReader,
    _cast_chain,
    _market_table_from_snapshots,
    _NoopSettlementWeightUpdater,
    _RecordingStateHook,
    _ReplayChainAdapter,
    _ReplaySettlementClient,
)
from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater
from agent.backtest.tennis_match_resolver import TennisMatchResolver, parse_slug
from agent.core.memory_bank import MemoryBank
from agent.core.state import Action, ActionKind, Phase, Weights
from agent.data.polymarket_sandbox_executor import (
    SandboxExecutor,
    _derive_expected_settle_ts,
)
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import SandboxStateWriter, iter_jsonl
from agent.engines._performance_window import PerformanceWindow
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.engines.decision import DecisionEngine
from agent.engines.reflection import ReflectionEngine, _LLMClient
from agent.engines.strategy_advisor import NoOpStrategyAdvisor, StrategyAdvisor
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.engines.weight_updater import WeightUpdater
from agent.llm.cost_guard import L3CostGuard
from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL,
    SandboxPhase2Loop,
    TickInputs,
    WeightUpdaterPhase,
)
from agent.runtime.tribute import TributePolicy

# A3 — the loss-multiplier knob default. ``1.0`` is the IDENTITY: with the knob
# at default the chain breath delta is the raw per-bet PnL, so the recorder is
# transparent and the season behaves byte-identically to A2 (the flag-default-OFF
# contract). A multiplier > 1.0 amplifies the magnitude of LOSING settlements fed
# to BREATH so a too-good seed can be calibrated into dying (codex R7 / A3b).
DEFAULT_LOSS_MULTIPLIER = 1.0

# Default settlement lag the survival schedule mirrors. MUST match the sandbox
# executor's ``settle_lag`` default (``polymarket_sandbox_executor.py:205``) so
# the schedule's settle checkpoints land where the loop's poller actually sees
# the bet become due (``expected_settle_ts = end_date_iso + settle_lag``).
DEFAULT_SETTLE_LAG = timedelta(hours=2)

# How long a within-tick clock cursor auto-advances per ``now()`` read. The loop
# reads ``clock.now()`` several times inside ONE decision tick (the ``run()``
# until-guard ``sandbox_phase2_loop.py:1249``, the ``_tick`` top ``:1431``, the
# poller's internal read, and the executor's order-stamp ``:294``); a tiny
# forward bump keeps those reads inside the same tick window while staying
# strictly below any real inter-stop gap (mirrors
# ``replay_runner._CompressedClock``). The runner re-pins the cursor to the next
# exact stop via :meth:`_ControllableClock.set_to` before each tick.
DEFAULT_WITHIN_TICK_ADVANCE = timedelta(seconds=1)

# Strictly-past margin the final-drain / settle-only checkpoints jump BEYOND the
# due settle time. The poller selects ``expected_settle_ts < now`` STRICTLY
# (``sandbox_settlement_poller.py:474``) and the replay settlement client gates
# on ``resolution_ts_iso <= now`` (``replay_runner.py:471``); a checkpoint that
# landed EXACTLY on the settle time would SKIP the bet, so checkpoints advance a
# whole second past it (well within float/second ISO resolution).
#
# This is intentionally INDEPENDENT of :data:`DEFAULT_WITHIN_TICK_ADVANCE`: the
# clock now keeps the within-tick drift in a separate scratch offset and
# validates :meth:`_ControllableClock.set_to` against the pinned base, so the
# margin no longer has to exceed the per-tick cursor drift (that coupling was the
# crash bug). The margin only needs to push the checkpoint STRICTLY past the due
# settle time; a settle checkpoint is also clamped to STAY STRICTLY BELOW the
# next entry (see :func:`build_survival_schedule`) so it can never overshoot and
# shadow the next decision stop.
_SETTLE_CHECKPOINT_MARGIN = timedelta(seconds=1)

# Minimum forward nudge between two DECISION stops that share (or round to) the
# same entry instant. Tied ``entry_asof_ts_iso`` values are plausible across the
# real cache (entry asof is a chosen ``PricePoint.ts`` and markets share cassette
# tick boundaries). Two decisions at the same instant are LEGAL — they are
# separate ticks — so the second must NOT be dropped; it is nudged a hair forward
# so every stop stays strictly increasing and the runner can always pin the clock
# FORWARD to it. The nudge is sub-second-scale and never crosses a real market
# (real inter-market gaps are minutes-to-days), so it does not distort
# settlement chronology.
_TIED_DECISION_NUDGE = timedelta(seconds=1)

# A2 — season caps / defaults surfaced for the A4 CLI + callers.
#
# ``DEFAULT_MAX_LIVES`` is a generous safety cap so an always-dying agent can't
# loop forever; ``DEFAULT_PHASE2_BANKROLL_USD`` mirrors the loop's own bankroll
# default (``sandbox_phase2_loop.DEFAULT_PHASE2_BANKROLL_USD``) so a caller that
# omits it lands on the same starting bankroll the prod loop uses.
DEFAULT_MAX_LIVES = 50
DEFAULT_PHASE2_BANKROLL_USD = 100.0

# Mid-market entry fraction. MUST match the fraction ``precompute_rows`` cached
# the rows with (``cached_sweep`` default + the ``precompute`` CLI default are
# both 0.5), otherwise the recomputed entry asof/price would disagree with the
# cached ``SignalRow.entry_price`` and :func:`build_survival_rows` would raise.
DEFAULT_ENTRY_FRACTION = 0.5

# --------------------------------------------------------------------------- #
# Realism rules (2026-06-11, user-locked) — shared by the numerical AND AI
# journeys (and the in-journey baselines), opt-out via None.
#
# Floor: drop untradeable extreme longshots. The cached universe's
# ``liquidity_cap_usd`` is uniformly $5, which caps bet SIZE — but the win
# payout ``size*(winning_price/price-1)`` is otherwise unbounded, so a $5 bet
# at $0.0005 "wins" $9,995 no $5-liquidity market could pay (observed: two such
# flukes were 62% of a full-run headline AND pumped breath to ~10k, faking
# "learned to survive"). Floor 0.05 keeps entry_price >= 0.05 (inclusive),
# dropping ~23/4925 rows (0.5%).
DEFAULT_ENTRY_PRICE_FLOOR: Final[float] = 0.05

# Cap: per-bet PROFIT ceiling = one life's starting bankroll ($100). Post-floor
# the max win for a $5 stake is 5*(1/0.05-1)=$95 < $100, so the cap can never
# clip a legitimate post-floor win — it is belt-and-suspenders guaranteeing no
# single fluke can ever dominate a season again. Losses are never clamped.
DEFAULT_MAX_BET_PNL_USD: Final[float] = 100.0

# Realism v3 (r5 M-1): typed sentinel for ``run_survival_export``'s
# ``effective_entry_price_floor`` default. ``None`` cannot carry both meanings
# ("omitted ⇒ mirror the row floor" AND "explicitly disabled"), so the export
# defaults to THIS impossible price: omitted ⇒ resolve to ``entry_price_floor``'s
# value; explicit ``None`` ⇒ the bet-level floor is disabled; any other float ⇒
# that value. The RESOLVED value is what threads downstream + lands in the
# summary key.
MIRROR_ROW_FLOOR: Final[float] = -1.0

# Float tolerance for the entry_price consistency check. Both the cached
# ``SignalRow.entry_price`` and the recomputed mid come from the SAME
# ``PricePoint.mid_price`` value (one round-tripped through JSON), so they are
# byte-identical in practice; the epsilon only guards JSON float repr drift.
_ENTRY_PRICE_EPS = 1e-9


@dataclass(frozen=True)
class SurvivalRow:
    """One market in the survival season — signals + settlement + entry + UI.

    Joined, read-only view the survival runner schedules over (by
    :attr:`entry_asof_ts_iso`). It does NOT duplicate the fusion math — the
    embedded :attr:`signal` is the authoritative source of scores/confidences,
    re-exposed via :attr:`scores`/:attr:`confidences`/:attr:`entry_price` for
    convenience.

    Fields
    ------
    market_id, slug
        Identity, copied off the cached row.
    signal
        The cached :class:`SignalRow`, carried verbatim (NOT mutated).
    entry_asof_ts_iso
        ISO-8601 UTC wall-clock at which the backtest ENTERS this market —
        recomputed deterministically from the snapshot ``price_ledger`` via the
        same mid-market logic the cache was built with, and asserted consistent
        with :attr:`entry_price`. This is the survival schedule key (A1).
    resolution_ts_iso, end_date_iso, outcome, winning_price, liquidity_cap
        Settlement facts copied off the :class:`MarketSnapshot`.
        ``resolution_ts_iso`` is nullable upstream (markets that closed without
        resolving), but :func:`build_survival_rows` only joins rows whose
        ``SignalRow`` exists, i.e. markets that DID resolve cleanly.
    players
        ``(p1_surname, p2_surname)`` normalised surnames from the slug, or
        ``None`` when the slug is not a ``-vs-`` match (UI fallback).
    surface
        ``"Hard"`` / ``"Clay"`` / ``"Grass"`` via the resolver, or ``None``
        when the slug does not resolve to two Sackmann players (UI fallback).
    """

    market_id: str
    slug: str
    signal: SignalRow
    entry_asof_ts_iso: str
    resolution_ts_iso: str | None
    end_date_iso: str
    outcome: str
    winning_price: float
    liquidity_cap: float
    players: tuple[str, str] | None
    surface: str | None

    # --- convenience re-exposure of the embedded SignalRow (no duplication) ---

    @property
    def scores(self) -> dict[str, float]:
        return self.signal.scores

    @property
    def confidences(self) -> dict[str, float]:
        return self.signal.confidences

    @property
    def entry_price(self) -> float:
        return self.signal.entry_price


def _recompute_entry_asof(
    snap: MarketSnapshot, *, entry_fraction: float
) -> tuple[str, float]:
    """Recompute ``(entry_asof_ts_iso, entry_price)`` off the snapshot ledger.

    Mirrors :func:`cached_sweep._entry_asof` over the snapshot's
    ``price_ledger`` (parsed to ``(datetime, mid_price)`` tuples exactly as
    ``precompute_rows`` does, ``cached_sweep.py:167-170``). The returned ISO
    string is the chosen ``PricePoint.ts`` verbatim (no reformatting — it is the
    cassette's own timestamp), so it round-trips losslessly.
    """
    if not snap.price_ledger:
        raise ValueError(
            f"market {snap.market_id!r} has an empty price_ledger — "
            "cannot recompute an entry asof"
        )
    ledger = [
        (datetime.fromisoformat(pp.ts), pp.mid_price) for pp in snap.price_ledger
    ]
    asof_ts, entry_price = _entry_asof(ledger, entry_fraction)
    # The chosen datetime corresponds 1:1 to a ledger point; recover its
    # original ISO string (verbatim) rather than re-serialising the datetime.
    chosen_idx = ledger.index((asof_ts, entry_price))
    return snap.price_ledger[chosen_idx].ts, entry_price


def build_survival_rows(
    rows: list[SignalRow],
    snapshots: list[MarketSnapshot],
    resolver: TennisMatchResolver,
    *,
    entry_fraction: float = DEFAULT_ENTRY_FRACTION,
    entry_price_floor: float | None = DEFAULT_ENTRY_PRICE_FLOOR,
) -> list[SurvivalRow]:
    """JOIN cached ``rows`` to ``snapshots`` + resolver into :class:`SurvivalRow`.

    For every cached :class:`SignalRow` (the resolved-market universe) the
    matching :class:`MarketSnapshot` supplies the settlement fields and the
    ``price_ledger`` from which ``entry_asof_ts_iso`` is recomputed; the
    recomputed entry price is asserted equal (within float epsilon) to the
    cached ``row.entry_price`` so the survival schedule can NOT silently desync
    from the cached PnL. Display players/surface come from the slug + resolver
    (nullable UI fallback). Output order follows ``rows`` (the cache's stable
    ``market_id`` order); A1 re-sorts by ``entry_asof_ts_iso``.

    ``entry_price_floor`` (realism rule, default
    :data:`DEFAULT_ENTRY_PRICE_FLOOR`): rows whose ``entry_price`` is BELOW the
    floor are dropped from the survival universe — extreme longshots whose
    unbounded win payout no $5-liquidity market could actually pay. Inclusive
    boundary: ``entry_price >= floor`` is KEPT. ``None`` disables (the legacy
    universe). Filtering HERE means every consumer — the learner season
    (numerical and AI alike), the recorder, and the in-journey static/archetype
    baselines — inherits the same filtered universe by construction.

    Raises
    ------
    KeyError
        A row references a ``market_id`` with no matching snapshot.
    ValueError
        The recomputed mid-market entry price disagrees with the cached
        ``row.entry_price`` (schedule/PnL desync), or the snapshot ledger is
        empty.
    """
    by_id = {snap.market_id: snap for snap in snapshots}
    out: list[SurvivalRow] = []
    for row in rows:
        if entry_price_floor is not None and row.entry_price < entry_price_floor:
            continue
        snap = by_id.get(row.market_id)
        if snap is None:
            raise KeyError(
                f"no MarketSnapshot for cached row market_id {row.market_id!r}"
            )
        entry_asof_ts_iso, recomputed_price = _recompute_entry_asof(
            snap, entry_fraction=entry_fraction
        )
        if abs(recomputed_price - row.entry_price) > _ENTRY_PRICE_EPS:
            raise ValueError(
                f"market {row.market_id!r}: recomputed mid-market entry_price "
                f"{recomputed_price!r} disagrees with cached SignalRow."
                f"entry_price {row.entry_price!r} — the survival schedule would "
                "desync from the cached PnL (entry_fraction mismatch?)"
            )
        parsed = parse_slug(snap.slug)
        players: tuple[str, str] | None = (
            (parsed.p1_surname, parsed.p2_surname) if parsed is not None else None
        )
        resolved = resolver.resolve(snap.slug)
        surface = resolved.surface if resolved is not None else None
        out.append(
            SurvivalRow(
                market_id=row.market_id,
                slug=snap.slug,
                signal=row,
                entry_asof_ts_iso=entry_asof_ts_iso,
                resolution_ts_iso=snap.resolution_ts_iso,
                end_date_iso=snap.end_date_iso,
                outcome=snap.outcome or row.outcome,
                winning_price=(
                    snap.winning_price
                    if snap.winning_price is not None
                    else row.winning_price
                ),
                liquidity_cap=snap.liquidity_cap_usd,
                players=players,
                surface=surface,
            )
        )
    return out


# =========================================================================== #
# A1 — entry-time-ordered cached tick source + controllable clock.
# =========================================================================== #


def _parse_ts(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp to a tz-aware UTC :class:`datetime`.

    All survival timestamps are tz-aware UTC at the cache layer, but a naive
    string would silently break the strict ``<`` settle comparisons, so we
    normalise defensively (a naive value is interpreted as UTC).
    """
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _due_settle_ts(row: SurvivalRow, *, settle_lag: timedelta) -> datetime:
    """The wall-clock at-or-after which ``row``'s bet can settle.

    A bet placed on ``row`` settles only once BOTH gates have passed as the
    clock advances forward:

    * the poller gate — ``expected_settle_ts = end_date_iso + settle_lag``
      (``polymarket_sandbox_executor.py:295``), selected STRICTLY ``< now``
      (``sandbox_settlement_poller.py:474``);
    * the replay settlement-client gate — cached ``resolution_ts_iso <= now``
      (``replay_runner.py:471``).

    So the earliest fully-drainable instant is ``max`` of the two. Returns that
    max; the caller adds a strictly-past margin for an actual checkpoint.
    """
    expected_settle = _parse_ts(
        _derive_expected_settle_ts(end_date_iso=row.end_date_iso, lag=settle_lag)
    )
    if row.resolution_ts_iso is not None:
        resolution = _parse_ts(row.resolution_ts_iso)
        return max(expected_settle, resolution)
    return expected_settle


@dataclass(frozen=True)
class ScheduleStop:
    """One stop the runner advances the clock to and then ticks the loop.

    ``market_id is not None`` → a DECISION stop: the clock sits at the market's
    ``entry_asof_ts_iso`` and :class:`SurvivalTickSource` serves that market's
    cached signals so the loop decides BET / NO_BET on it.

    ``market_id is None`` → a SETTLE-ONLY ("no-market") stop: the clock has
    advanced strictly past a due ``expected_settle_ts`` / ``resolution_ts`` so
    the loop's poller drains the in-flight bet, but the tick source returns
    ``None`` → the loop emits NO_BET (``no_eligible_market``) with no real
    decision. The A3 recorder filters these synthetic NO_BETs from the journey.
    """

    asof_ts: datetime
    market_id: str | None


@dataclass(frozen=True)
class SurvivalSchedule:
    """The ordered (strictly increasing) stops + the cached rows by id.

    :attr:`stops` interleaves entry-time DECISION stops with settle-only
    checkpoints so that, as the runner pins the clock to each stop in order,
    every open bet settles in flight (and the final bet drains at the trailing
    checkpoint) — the faithful continuous-time model the master plan locks.
    :attr:`rows_by_id` lets the tick source recover a market's cached signals.
    """

    stops: tuple[ScheduleStop, ...]
    rows_by_id: dict[str, SurvivalRow]


def build_survival_schedule(
    rows: list[SurvivalRow],
    *,
    settle_lag: timedelta = DEFAULT_SETTLE_LAG,
) -> SurvivalSchedule:
    """Build the entry-time-ordered schedule of decision + settle-only stops.

    Algorithm (two passes, so a nudged decision stop never confuses the
    checkpoint placement that follows it):

    1. Sort rows by ``(entry_asof_ts_iso, market_id)`` (the DECISION key — NOT
       settlement time; the loop uses one ``now`` to both decide the current
       market AND poll-settle earlier bets, so scheduling by settlement time
       would make the current decision happen AFTER the market resolved =
       lookahead). The ``market_id`` tie-break makes tied entries deterministic.
    2. **Pass 1 — decision stops (NEVER dropped).** Emit one decision stop per
       row at its entry time. A DECISION stop is load-bearing: two decisions at
       the SAME entry instant are legal (separate ticks; the within-tick clock
       advances between them), so a tied / non-increasing entry is NUDGED a hair
       forward (``_TIED_DECISION_NUDGE``) to keep every stop strictly increasing
       rather than silently dropped. (The original code dropped the second tied
       entry via the strict-increasing guard — data loss, not reordering.)
    3. **Pass 2 — settle-only checkpoints (deduped / clamped).** Between two
       consecutive ACTUAL (post-nudge) decision stops, if an already-entered
       bet's due settle time (``max(expected_settle_ts, resolution_ts)``) falls
       strictly between them, insert ONE settle-only checkpoint a margin past the
       LATEST such due time — but CLAMPED to stay strictly BELOW the next
       decision stop. If the margin would reach/overshoot the next stop the
       checkpoint is SKIPPED (the next decision tick's own strict-``<`` poll
       drains those bets anyway), so a near-boundary due time can never shadow
       and drop the next decision.
    4. After the last decision stop, always append a FINAL-DRAIN settle-only
       checkpoint a margin past ``max`` due settle time over ALL rows (codex R6)
       so the last bet settles — else PnL / learning / deaths are suppressed.

    The result is strictly increasing in ``asof_ts``; DECISION stops are all
    preserved (only redundant SETTLE-ONLY checkpoints are deduped/skipped).
    """
    ordered = sorted(
        rows, key=lambda r: (_parse_ts(r.entry_asof_ts_iso), r.market_id)
    )
    rows_by_id = {r.market_id: r for r in rows}

    # --- Pass 1: actual (post-nudge) decision stops, strictly increasing. ---
    # Each entry carries (actual_ts, market_id, due_settle_ts).
    decisions: list[tuple[datetime, str, datetime]] = []
    last_ts: datetime | None = None
    for row in ordered:
        entry_ts = _parse_ts(row.entry_asof_ts_iso)
        actual_ts = entry_ts
        if last_ts is not None and actual_ts <= last_ts:
            # Tied / non-increasing entry: nudge forward, NEVER drop.
            actual_ts = last_ts + _TIED_DECISION_NUDGE
        due_ts = _due_settle_ts(row, settle_lag=settle_lag)
        decisions.append((actual_ts, row.market_id, due_ts))
        last_ts = actual_ts

    # --- Pass 2: interleave settle-only checkpoints, clamped below next stop. ---
    stops: list[ScheduleStop] = []
    cursor: datetime | None = None  # last EMITTED stop time (strictly increasing)
    for i, (actual_ts, market_id, _due) in enumerate(decisions):
        stops.append(ScheduleStop(asof_ts=actual_ts, market_id=market_id))
        cursor = actual_ts

        next_ts = decisions[i + 1][0] if i + 1 < len(decisions) else None
        if next_ts is None:
            continue
        # Due settle times of bets entered AT-OR-BEFORE this decision that fall
        # strictly before the NEXT decision AND strictly after the cursor (those
        # not already drained by an emitted stop) need an in-flight checkpoint.
        gap_due = [
            due
            for _ts, _mid, due in decisions[: i + 1]
            if cursor < due < next_ts
        ]
        if not gap_due:
            continue
        checkpoint = max(gap_due) + _SETTLE_CHECKPOINT_MARGIN
        # Clamp STRICTLY below the next decision stop: if the margin reaches or
        # overshoots it, skip — the next decision's own strict-< poll settles
        # these bets, and emitting at/after next_ts would shadow + drop it.
        if checkpoint >= next_ts:
            continue
        stops.append(ScheduleStop(asof_ts=checkpoint, market_id=None))
        cursor = checkpoint

    # Final-drain checkpoint past the max due time across ALL rows (codex R6).
    if decisions:
        final_due = max(due for _ts, _mid, due in decisions)
        final_checkpoint = final_due + _SETTLE_CHECKPOINT_MARGIN
        # The final drain is after the LAST decision stop; only emit if strictly
        # forward of the cursor (it can coincide if a bet was already drained).
        if cursor is None or final_checkpoint > cursor:
            stops.append(ScheduleStop(asof_ts=final_checkpoint, market_id=None))

    return SurvivalSchedule(stops=tuple(stops), rows_by_id=rows_by_id)


@dataclass
class SurvivalTickSource:
    """A ``TickInputSource`` over the cached survival schedule (A1).

    Satisfies the loop's :class:`~agent.runtime.sandbox_phase2_loop.TickInputSource`
    Protocol (``inputs_for(asof_ts, tick) -> TickInputs | None``). Indexed by the
    loop's tick counter, it returns:

    * the scheduled market's CACHED signals (via
      :func:`~agent.backtest.cached_sweep.row_to_signals` — NO Sackmann
      recompute), cached ``entry_price`` and ``liquidity_cap``, as
      :class:`TickInputs`, on a DECISION stop;
    * ``None`` on a settle-only stop or a tick index past the schedule end →
      the loop emits NO_BET (``no_eligible_market``); the A3 recorder filters
      the synthetic settle-only NO_BETs.

    The ``asof_ts`` argument is accepted to satisfy the Protocol but the schedule
    is the authority on which market a given tick serves — the runner has already
    pinned the clock to ``stops[tick].asof_ts`` before calling, so they agree.
    """

    schedule: SurvivalSchedule

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        if tick < 0 or tick >= len(self.schedule.stops):
            return None
        stop = self.schedule.stops[tick]
        if stop.market_id is None:
            return None  # settle-only "no-market" tick → NO_BET
        row = self.schedule.rows_by_id[stop.market_id]
        return TickInputs(
            market_id=row.market_id,
            signals=row_to_signals(row.signal),
            price=row.entry_price,
            liquidity_cap_usd=row.liquidity_cap,
        )


@dataclass
class _ControllableClock:
    """Forward-only clock the survival runner pins to each schedule stop.

    Implements the structural :class:`~agent.data._realtime_buffer.Clock`
    Protocol (``now() -> datetime``). Unlike ``replay_runner._CompressedClock``
    (which only ever auto-advances), the survival runner needs to SET the cursor
    to each market's exact entry / settle time before ticking the loop — so the
    single ``now`` the loop uses for both the settlement poll AND the decision
    lands on the intended stop.

    **Pinned base vs within-tick scratch (review-fix).** A single ``_tick()``
    reads ``now()`` 2-4 times (the ``run()`` until-guard
    ``sandbox_phase2_loop.py:1249``, the ``_tick`` top ``:1431``, the poller
    ``sandbox_settlement_poller.py:406``, and — on a BET — the executor
    order-stamp ``polymarket_sandbox_executor.py:294``). Each read needs a hair
    of forward motion so those reads stay inside one window (mirroring the
    compressed clock). Naively folding that drift into a single cursor coupled
    the inter-stop monotonicity check to the per-tick read count: pinning the
    next stop only ~1s ahead after the cursor had already crept +2..4s called
    ``set_to(past)`` and crashed the run. So the clock keeps the drift in a
    SEPARATE scratch offset: :meth:`now` returns ``base + scratch`` and bumps
    only ``scratch``; :meth:`set_to` validates the next pin against the PINNED
    BASE (not the drifted cursor) and resets ``scratch`` to zero. A forward pin
    is therefore always accepted regardless of how far the within-tick reads
    drifted — only a pin strictly BEFORE the base (a true backwards move that
    would re-settle bets / reintroduce lookahead) is rejected.
    """

    _now: datetime
    within_tick_advance: timedelta = field(
        default_factory=lambda: DEFAULT_WITHIN_TICK_ADVANCE
    )
    _scratch: timedelta = field(default_factory=lambda: timedelta(0))

    def now(self) -> datetime:
        current = self._now + self._scratch
        if self.within_tick_advance > timedelta(0):
            self._scratch = self._scratch + self.within_tick_advance
        return current

    def set_to(self, ts: datetime) -> None:
        """Pin the base to ``ts`` (must be >= the pinned base) + reset scratch.

        Validates against the PINNED BASE (``self._now``), not the within-tick
        scratch cursor, so a forward pin a hair ahead of the base succeeds even
        after the prior tick's reads drifted the scratch cursor past it. Only a
        true backwards move (``ts`` strictly before the base) is rejected.
        """
        if ts < self._now:
            raise ValueError(
                f"_ControllableClock.set_to({ts!r}) moves BACKWARDS from base "
                f"{self._now!r} — the survival clock is forward-only"
            )
        self._now = ts
        self._scratch = timedelta(0)

    def advance(self, delta: timedelta) -> None:
        """Step the base forward by ``delta`` (negative is rejected) + reset scratch."""
        if delta < timedelta(0):
            raise ValueError(
                f"_ControllableClock.advance({delta!r}) is negative — the "
                "survival clock is forward-only"
            )
        self._now = self._now + delta
        self._scratch = timedelta(0)


# =========================================================================== #
# A3 — survival recorder (state_hook + settlement-update wrapper).
# =========================================================================== #


@dataclass(frozen=True)
class SurvivalStep:
    """One SETTLED bet — the per-settlement record the journey is built from.

    Captured by :class:`SurvivalRecorder` at the moment a bet settles (the
    settlement-learning ``update`` call), NOT reconstructed from
    ``decisions.jsonl`` (codex H3 — that omits the signal scores AND the
    settlement, so a derivation would invent history). Synthetic no-market
    NO_BETs (the A1 settle-only ticks) never reach this seam, so they are
    naturally FILTERED.

    Fields
    ------
    life_idx
        Which life produced this settlement (0-based, set by the season driver
        via :meth:`SurvivalRecorder.begin_life`).
    market_id / slug / players / surface / entry_price / outcome / winning_price
        Market metadata joined from the originating :class:`SurvivalRow`.
    side / size_usd
        The bet's direction (``"YES"`` / ``"NO"``, recovered from the
        ``bet_direction`` the poller flattens) and its stake.
    pnl_usd
        Realised P&L for this settlement (the poller's ``_compute_pnl``).
    signal_scores
        The decision-time per-engine scores (the ``score_<engine>`` keys the
        poller flattened off the originating ``BetRecord``).
    weights_before / weights_after
        The fusion :class:`Weights` immediately BEFORE and AFTER the
        settlement-learning update fired on this bet — the proof the agent
        learned (or, on the frozen baseline, did not).
    breath_after / bankroll_after
        The loop's BREATH / bankroll snapshot read right after the update.
    cum_pnl / running_win_rate
        Running cumulative PnL + win-rate across ALL settlements recorded so far
        (across lives — the season-level trajectory).
    reflection
        OPTIONAL Page-2 timeline annotation (Phase B / B3). When the L6
        reflect→advisor closure is live (``GENESIS_REAL_REFLECTION`` +
        ``GENESIS_REAL_STRATEGY_ADVISOR``) the loop emits ``reflection_emitted``
        / ``strategy_advisor_fired`` state-hook events; the recorder stashes the
        most recent such annotation and stamps it onto the NEXT settled step so
        the journey can surface "the agent reflected, then proposed an
        optimisation" on the timeline. ``None`` whenever no reflection / proposal
        event preceded this settlement (the default — and the ONLY value the
        survival season produces today, since it wires a ``NoOpStrategyAdvisor``
        + no reflection engine, so the flag-OFF journey is byte-unchanged).
    """

    life_idx: int
    market_id: str
    slug: str
    players: tuple[str, str] | None
    surface: str | None
    entry_price: float
    outcome: str
    winning_price: float
    side: str
    size_usd: float
    pnl_usd: float
    signal_scores: dict[str, float]
    weights_before: Weights
    weights_after: Weights
    breath_after: float
    bankroll_after: float
    cum_pnl: float
    running_win_rate: float
    reflection: str | None = None
    # A9 storm stamps — extracted from the poller-flattened settlement
    # signals (the BetRecord → poller → SurvivalStep durable path).
    # ``None`` = storm off; :func:`_step_to_dict` OMITS None keys so
    # flag-off artifacts keep the pre-kit keyset.
    storm_at_bet: float | None = None
    edge_at_bet: float | None = None
    min_edge_at_bet: float | None = None
    gamma_at_bet: float | None = None
    eff_min_edge_at_bet: float | None = None


@dataclass(frozen=True)
class DeathRecord:
    """Death facts captured by the recorder via the loop's ``agent_died`` hook.

    Distinct from :class:`DeathFacts` (which the season driver builds from the
    :class:`RunSummary`): this one is captured at the SOURCE (the
    ``state_hook.emit(kind="agent_died", ...)`` call at
    ``sandbox_phase2_loop.py:1729``) so the recorder owns the full death payload
    even before the driver folds the summary.
    """

    life_idx: int
    last_tick: int
    kill_tx_hash: str
    tombstone_token_id: str
    tombstone_tx_hash: str
    bankroll_usd: float
    final_weights_hash: str
    last_words: str


@dataclass
class _RecorderChainAdapter:
    """Chain adapter wrapper applying the recorder's loss-multiplier (A3b).

    Wraps the inner :class:`_ReplayChainAdapter` and, when
    ``loss_multiplier != 1.0``, AMPLIFIES the magnitude of a LOSING (negative)
    BREATH delta before it reaches the chain — so a too-good fragile seed can be
    calibrated into dying (codex R7). A multiplier of ``1.0`` is the IDENTITY
    (the delta passes through unchanged), preserving the flag-default-OFF
    behaviour. Winning (positive) deltas are NEVER scaled — the knob only
    sharpens downside survival pressure, it does not fabricate gains.
    """

    inner: _ReplayChainAdapter
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        effective = pnl_usd
        if pnl_usd < 0.0 and self.loss_multiplier != 1.0:
            effective = pnl_usd * self.loss_multiplier
        await self.inner.update_breath_from_pnl(effective)

    async def read_breath(self) -> float:
        return await self.inner.read_breath()

    async def kill_and_mint_tombstone(self, **kwargs: Any) -> Any:
        return await self.inner.kill_and_mint_tombstone(**kwargs)


@dataclass
class _RecordingSettlementUpdater:
    """Thin wrapper around ``_SettlementLearningWeightUpdater.update`` (codex H3).

    Implements the poller's ``WeightUpdater`` Protocol. On each settlement it:

    1. snapshots the loop's weights BEFORE the inner learning update;
    2. delegates to the wrapped :class:`_SettlementLearningWeightUpdater` (which
       re-assigns ``loop._weights`` from the realised PnL);
    3. snapshots the weights AFTER + reads the loop's breath / bankroll;
    4. joins the originating :class:`SurvivalRow` metadata + the flattened
       per-bet feedback (``pnl_usd`` / ``size_usd`` / ``bet_direction`` /
       ``score_<engine>``) into a :class:`SurvivalStep` appended to the recorder.

    The synthetic no-market NO_BETs never settle, so they never reach here — the
    journey is filtered by construction.
    """

    inner: _SettlementLearningWeightUpdater
    recorder: SurvivalRecorder

    async def update(
        self,
        *,
        phase: str | Phase,
        signals: dict[str, float],
        outcome: object,
    ) -> None:
        loop = self.inner.weights_holder
        weights_before = loop._weights
        await self.inner.update(phase=phase, signals=signals, outcome=outcome)
        weights_after = loop._weights
        self.recorder._on_settlement(
            signals=signals,
            outcome=outcome,
            weights_before=weights_before,
            weights_after=weights_after,
            breath_after=float(getattr(loop, "_breath", 0.0)),
            bankroll_after=float(getattr(loop, "_bankroll_usd", 0.0)),
        )


@dataclass
class _RecordingSurvivalStateHook:
    """A ``StateHook`` that records the loop's death + L6 reflection events.

    Captures five hook ``kind``s; every other kind is ignored (the underlying
    contract forbids raising into the caller):

    * ``agent_died`` — the death payload (:meth:`SurvivalRecorder._on_death`).
    * ``reflection_emitted`` — the L2/L6 reflection narrative
      (``sandbox_phase2_loop.py``'s ``_fire_reflection`` hook). Phase B / B3:
      stashed so the NEXT settled step carries the reflection annotation on the
      Page-2 timeline.
    * ``strategy_advisor_fired`` — the L3/L6 advisor fire (carries
      ``proposals_emitted``). When proposals were produced, the stash is enriched
      so the timeline annotation records that the reflection drove a proposal.
    * ``weight_delta_applied`` / ``weight_delta_apply_failed`` (T-D-018) — the
      loop's per-tick auto-approve drain result. Tallied into
      ``recorder.proposals_applied`` / ``proposals_apply_failed`` — the genuine-
      AI-divergence signal the journey summary + the ``require_applied_deltas``
      hard invariant read.

    With the L6 flags OFF (the survival season's default — ``NoOpStrategyAdvisor``
    + no reflection engine) NONE of the L6 kinds are ever emitted, so the stash
    stays empty, the tallies stay 0, and every recorded step's ``reflection`` is
    ``None`` (the journey is byte-unchanged).
    """

    recorder: SurvivalRecorder

    def emit(self, *, kind: str, **payload: Any) -> None:
        if kind == "agent_died":
            self.recorder._on_death(payload)
        elif kind == "reflection_emitted":
            self.recorder._on_reflection(payload)
        elif kind == "strategy_advisor_fired":
            self.recorder._on_strategy_advisor_fired(payload)
        elif kind == "weight_delta_applied":
            self.recorder._on_weight_delta_applied(payload)
        elif kind == "weight_delta_apply_failed":
            self.recorder._on_weight_delta_apply_failed(payload)
        elif kind == "tribute":
            self.recorder._on_tribute(payload)
        elif kind == "tithe":
            self.recorder._on_tithe(payload)


@dataclass
class SurvivalRecorder:
    """Explicit per-settlement + death recorder for the survival season (A3).

    Wires into a life's loop via :meth:`wrap_updater` (the settlement-update
    seam) and :meth:`state_hook` (the death + L6 reflection/proposal seam);
    :meth:`begin_life` stamps the current life id onto subsequent records. The
    accumulated :attr:`steps` + :attr:`deaths` are the source for
    :func:`build_survival_journey`.

    ``loss_multiplier`` is the A3b calibration knob (see
    :class:`_RecorderChainAdapter`): ``1.0`` (default) is transparent.
    """

    rows: list[SurvivalRow]
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER

    steps: list[SurvivalStep] = field(default_factory=list)
    deaths: list[DeathRecord] = field(default_factory=list)

    # Realism v3 (r8 H-1): ALL placed bets (settled or not), accumulated by
    # ``run_survival_season`` from each life's ``open_bets.jsonl`` right after
    # the life ends — BEFORE the temp state dir is cleaned up. ~55% of placed
    # bets never settle; the export's effective-floor invariant scans THIS
    # ledger (side + price are known at placement), while the pnl recompute
    # stays settled-only (``steps``). Each entry is the raw BetRecord dict.
    placed_bets: list[dict[str, Any]] = field(default_factory=list)

    # A7 tribute events (money for breath at the deathbed), stamped with
    # the season-local life index. Empty unless a tribute policy is wired
    # (the survival season default is none - byte-unchanged journeys).
    tributes: list[dict[str, Any]] = field(default_factory=list)

    # A10 divine-tithe events (periodic rent: $ paid from bankroll, or breath
    # taken when broke), stamped with the season-local life index. Empty
    # unless ``divine_tithe`` is enabled (the default is off - byte-unchanged).
    tithes: list[dict[str, Any]] = field(default_factory=list)

    # T-D-018 AI tally: how many auto-approved weight deltas the loop actually
    # APPLIED vs failed to apply across the whole season. ``proposals_applied``
    # is the genuine-AI-divergence signal — when it is 0 on an AI run, the LLM
    # never moved the weights (the original fail-soft failure mode), which the
    # ``require_applied_deltas`` hard invariant in ``run_survival_export`` checks.
    proposals_applied: int = 0
    proposals_apply_failed: int = 0

    _current_life: int = 0
    _wins: int = 0
    _cum_pnl: float = 0.0
    _rows_by_id: dict[str, SurvivalRow] = field(default_factory=dict)
    # Phase B / B3: the most recent reflection / proposal annotation captured
    # off the loop's state-hook stream, awaiting attachment to the NEXT settled
    # step. ``None`` whenever the L6 closure is off (the season default), so the
    # journey's ``reflection`` fields stay byte-unchanged.
    _pending_reflection: str | None = None

    def __post_init__(self) -> None:
        self._rows_by_id = {r.market_id: r for r in self.rows}

    # -- driver-facing seams ------------------------------------------------ #

    def begin_life(self, life_idx: int) -> None:
        """Stamp ``life_idx`` onto subsequent settlement / death records."""
        self._current_life = life_idx

    def state_hook(self) -> _RecordingSurvivalStateHook:
        """The death + L6-reflection-capturing :class:`StateHook` for the loop."""
        return _RecordingSurvivalStateHook(recorder=self)

    def wrap_updater(
        self, adapter: _SettlementLearningWeightUpdater
    ) -> _RecordingSettlementUpdater:
        """Wrap the life's settlement-learning adapter for per-bet capture."""
        return _RecordingSettlementUpdater(inner=adapter, recorder=self)

    def wrap_chain(self, adapter: _ReplayChainAdapter) -> _RecorderChainAdapter:
        """Wrap the life's chain adapter to apply the loss-multiplier knob."""
        return _RecorderChainAdapter(
            inner=adapter, loss_multiplier=self.loss_multiplier
        )

    # -- capture callbacks (called from the wrappers) ----------------------- #

    def _on_settlement(
        self,
        *,
        signals: dict[str, float],
        outcome: object,
        weights_before: Weights,
        weights_after: Weights,
        breath_after: float,
        bankroll_after: float,
    ) -> None:
        market_id = self._market_id_from_outcome(outcome)
        row = self._rows_by_id.get(market_id)
        pnl = float(signals.get("pnl_usd", 0.0))
        size = float(signals.get("size_usd", 0.0))
        side = "YES" if signals.get("bet_direction", 1.0) >= 0.0 else "NO"
        scores = {
            k[len("score_") :]: float(v)
            for k, v in signals.items()
            if k.startswith("score_")
        }
        # A9: the five storm stamps ride the same flattened channel.
        def _stamp(key: str) -> float | None:
            return float(signals[key]) if key in signals else None

        self._cum_pnl += pnl
        if pnl > 0.0:
            self._wins += 1
        n = len(self.steps) + 1
        win_rate = self._wins / n

        # Attach + CONSUME any pending L6 reflection annotation (B3) so a single
        # reflection event stamps exactly ONE step (the next settlement), not
        # every subsequent one. ``None`` whenever the L6 closure is off.
        reflection = self._pending_reflection
        self._pending_reflection = None

        self.steps.append(
            SurvivalStep(
                life_idx=self._current_life,
                market_id=market_id,
                slug=row.slug if row is not None else market_id,
                players=row.players if row is not None else None,
                surface=row.surface if row is not None else None,
                entry_price=row.entry_price if row is not None else 0.0,
                outcome=row.outcome if row is not None else "",
                winning_price=row.winning_price if row is not None else 0.0,
                side=side,
                size_usd=size,
                pnl_usd=pnl,
                signal_scores=scores,
                weights_before=weights_before,
                weights_after=weights_after,
                breath_after=breath_after,
                bankroll_after=bankroll_after,
                cum_pnl=self._cum_pnl,
                running_win_rate=win_rate,
                reflection=reflection,
                storm_at_bet=_stamp("storm_at_bet"),
                edge_at_bet=_stamp("edge_at_bet"),
                min_edge_at_bet=_stamp("min_edge_at_bet"),
                gamma_at_bet=_stamp("gamma_at_bet"),
                eff_min_edge_at_bet=_stamp("eff_min_edge_at_bet"),
            )
        )

    def _on_tribute(self, payload: dict[str, Any]) -> None:
        """A7: capture a deathbed tribute event (granted or kept).

        ``pnl_at_event`` (the recorder's running cum at the altar moment)
        makes the user's headline metric self-contained: revival earnings =
        pnl_at_death - pnl at the FIRST tribute - did buying life actually
        buy any income, or just a deeper grave?
        """
        self.tributes.append(
            {
                **payload,
                "life_idx": self._current_life,
                "pnl_at_event": self._cum_pnl,
            }
        )

    def _on_tithe(self, payload: dict[str, Any]) -> None:
        """A10: capture one periodic divine-tithe event (cash or breath)."""
        self.tithes.append(
            {
                **payload,
                "life_idx": self._current_life,
            }
        )

    def _on_death(self, payload: dict[str, Any]) -> None:
        self.deaths.append(
            DeathRecord(
                life_idx=self._current_life,
                last_tick=int(payload.get("last_tick", -1)),
                kill_tx_hash=str(payload.get("kill_tx_hash", "")),
                tombstone_token_id=str(payload.get("tombstone_token_id", "")),
                tombstone_tx_hash=str(payload.get("tombstone_tx_hash", "")),
                bankroll_usd=float(payload.get("bankroll_usd", 0.0)),
                final_weights_hash=str(payload.get("final_weights_hash", "")),
                last_words=str(payload.get("last_words", "")),
            )
        )

    def _on_reflection(self, payload: dict[str, Any]) -> None:
        """Stash the L6 reflection for the NEXT settled step (B3 annotation).

        The loop's ``reflection_emitted`` hook carries the ``reflection_id`` +
        ``trigger`` but NOT the narrative body (that is written to
        ``reflections.jsonl``); for the timeline annotation a compact marker is
        enough, so we record the trigger + id. A later
        ``strategy_advisor_fired`` enriches this marker in place.
        """
        reflection_id = str(payload.get("reflection_id", ""))
        trigger = str(payload.get("trigger", ""))
        self._pending_reflection = (
            f"reflected ({trigger})"
            if reflection_id == ""
            else f"reflected ({trigger}) #{reflection_id}"
        )

    def _on_strategy_advisor_fired(self, payload: dict[str, Any]) -> None:
        """Enrich the pending annotation when the advisor produced proposals (B3).

        ``strategy_advisor_fired`` carries ``proposals_emitted`` /
        ``pending_proposals_count``. Only when at least one proposal was produced
        (the reflection→advisor closure actually optimised) do we mark the
        timeline; a fire that emitted zero proposals (the ``NoOpStrategyAdvisor``
        path) leaves the annotation untouched, so the season default journey is
        byte-unchanged.
        """
        proposals_emitted = int(payload.get("proposals_emitted", 0))
        if proposals_emitted <= 0:
            return
        prefix = (
            self._pending_reflection + " -> "
            if self._pending_reflection is not None
            else ""
        )
        plural = "proposal" if proposals_emitted == 1 else "proposals"
        self._pending_reflection = (
            f"{prefix}proposed {proposals_emitted} {plural} (pending approval)"
        )

    def _on_weight_delta_applied(self, payload: dict[str, Any]) -> None:
        """Tally one auto-approved weight delta the loop APPLIED (T-D-018).

        ``weight_delta_applied`` carries ``key`` + ``amount``; the count is the
        genuine-AI-divergence signal surfaced in the journey summary and checked
        by the ``require_applied_deltas`` hard invariant.
        """
        self.proposals_applied += 1

    def _on_weight_delta_apply_failed(self, payload: dict[str, Any]) -> None:
        """Tally one auto-approved weight delta that FAILED to apply (T-D-018).

        ``weight_delta_apply_failed`` carries ``error`` + ``payload``; a high
        count signals the LLM emitted unapplicable proposals (the original
        ``proposed_change={}`` failure mode the strict advisor now prevents).
        """
        self.proposals_apply_failed += 1

    @staticmethod
    def _market_id_from_outcome(outcome: object) -> str:
        if isinstance(outcome, SettlementResult):
            return outcome.market_id
        mid = getattr(outcome, "market_id", None)
        return str(mid) if mid is not None else ""


# =========================================================================== #
# A2 — multi-life FRESH-loop respawn driver.
# =========================================================================== #


@dataclass
class _ScheduleDrivingClock:
    """A ``Clock`` that pins ``now()`` to ``schedule.stops[loop.tick_counter]``.

    The A1 :class:`_ControllableClock` requires the *runner* to call
    :meth:`_ControllableClock.set_to` once per stop — incompatible with a SINGLE
    :meth:`SandboxPhase2Loop.run` invocation, which owns its own internal tick
    loop and never exposes a per-tick hook. A2 must honour "a life = ONE
    ``loop.run()``", so the clock instead reads the loop's monotonic
    ``tick_counter`` and returns the matching stop's wall-clock.

    Why ``tick_counter`` is the right index. ``run()``'s while-guard reads
    ``now()`` (the ``until`` check) and then ``_tick()`` reads ``now()`` at its
    top (``sandbox_phase2_loop.py:1249,:1431``); both fire while
    ``tick_counter == t`` because ``_tick`` only advances the counter at step 7
    (``:1551``), AFTER the decision + settlement. So every ``now()`` read for the
    loop's tick ``t`` — guard, tick-top, the poller's read, and (on a BET) the
    executor's order-stamp — resolves to ``stops[t].asof_ts``, exactly the
    entry/settle instant A1 scheduled. A tiny per-read ``within_tick_advance``
    keeps successive reads inside one tick strictly increasing (mirroring
    ``replay_runner._CompressedClock``) without crossing into the next stop (real
    inter-stop gaps are seconds-to-days, the scratch is sub-second-scale).

    A ``tick_counter`` at or past the schedule end clamps to the LAST stop
    (defensive: the loop is bounded by ``max_ticks=len(stops)`` so this should
    not be reached, but a clamp is safer than an ``IndexError`` mid-run).
    """

    stops: tuple[ScheduleStop, ...]
    # Bound AFTER construction: the executor + settlement client need the clock
    # at THEIR construction, but the clock needs the loop (for its tick_counter),
    # and the loop needs the executor — so the clock is built first with a
    # deferred loop reference and ``loop`` is set once the loop exists.
    loop: SandboxPhase2Loop | None = None
    within_tick_advance: timedelta = field(
        default_factory=lambda: DEFAULT_WITHIN_TICK_ADVANCE
    )
    _scratch: timedelta = field(default_factory=lambda: timedelta(0))
    _last_tick: int = -1

    def now(self) -> datetime:
        if self.loop is None:
            # Pre-bind reads (none expected before run()) pin to the first stop.
            return self.stops[0].asof_ts if self.stops else datetime.now(tz=UTC)
        tick = self.loop.tick_counter
        if tick != self._last_tick:
            # A fresh tick began — reset the within-tick scratch so the new
            # tick's reads start at the stop's exact instant.
            self._scratch = timedelta(0)
            self._last_tick = tick
        idx = min(tick, len(self.stops) - 1) if self.stops else 0
        base = self.stops[idx].asof_ts
        current = base + self._scratch
        if self.within_tick_advance > timedelta(0):
            self._scratch = self._scratch + self.within_tick_advance
        return current


@dataclass(frozen=True)
class DeathFacts:
    """The facts captured at a life's death (codex H2)."""

    life_idx: int
    last_tick: int
    final_breath: float
    final_bankroll_usd: float
    kill_tx_hash: str
    tombstone_token_id: str
    tombstone_tx_hash: str


@dataclass(frozen=True)
class LifeOutcome:
    """One life in the season — its loop's run + the death/respawn metadata."""

    idx: int
    state_dir: Path
    start_ts: datetime
    initial_weights: Weights
    terminal_weights: Weights
    consumed_market_ids: tuple[str, ...]
    bets_placed: int
    no_bets_emitted: int
    settlements_processed: int
    final_breath: float
    final_bankroll_usd: float
    died: bool
    death: DeathFacts | None


@dataclass
class SeasonResult:
    """Aggregate output of :func:`run_survival_season`."""

    lives: tuple[LifeOutcome, ...]
    deaths: int
    seed: StrategyConfig
    # The ONE inner WeightUpdater shared across every life (its EMA is the
    # cross-death state the respawn must preserve). Exposed for proof/inspection.
    shared_weight_updater: WeightUpdater


# =========================================================================== #
# Opt-in AI mode (L6 reflect→optimize) — the REAL ReflectionEngine +
# StrategyAdvisorImpl wired into each life, with auto-approved weight deltas.
# =========================================================================== #


@dataclass(frozen=True)
class AISeasonContext:
    """The AI-mode dependencies a season threads into each life's loop.

    Constructed ONCE per season (the ``llm_client`` + ``l3_guard`` are SHARED
    across lives — the L3 budget cap is a season-long total, and the fake LLM in
    tests records every call). Per-life state (the ``RuntimeAgentRunner`` queue,
    the ``ReflectionEngine``, the ``StrategyAdvisorImpl``) is built FRESH inside
    :func:`_build_life_loop` so a dead life's undrained deltas can't leak into a
    respawn.

    Parameters
    ----------
    llm_client
        Protocol-conformant :class:`agent.engines.reflection._LLMClient` (the
        same shape both engines consume). Production passes a
        :class:`agent.llm.gemini_client.GeminiClient`; tests inject a FAKE so no
        live Gemini call fires (``GEMINI_API_KEY`` is unset under pytest). Typed
        as the Protocol — NOT ``GeminiClient`` — so a fake injects cleanly.
    l3_guard
        The L3 advisor's budget cap (:class:`L3CostGuard`). Shared across lives
        so the season-long L3 spend is bounded by one budget.
    strategy_advisor_tick_interval / reflection_tick_interval
        Optional cadence overrides. ``None`` ⇒ the loop's defaults (M=100 /
        N=10). A tiny override (e.g. ``1``) lets a test force a fire inside a
        short fixture so the closure is genuinely exercised.
    model
        Model id threaded into EVERY LLM-consuming construction (the advisor
        in :func:`_build_life_loop`, the ReflectionEngine's sonnet/opus pair,
        AND the :func:`preflight_ai_advisor_applicable` probe — review r6/r7).
        Default ``""`` = each client self-resolves its OWN default model (the
        shared empty-string convention of GeminiClient + MiniMaxClient), so a
        provider-pure leg can never be sent a foreign model id. ``kw_only``
        (r8 M-1): existing callers construct this dataclass POSITIONALLY, so
        a plain appended field would invite a silent positional shift.
    """

    llm_client: _LLMClient
    l3_guard: L3CostGuard
    strategy_advisor_tick_interval: int | None = None
    reflection_tick_interval: int | None = None
    model: str = field(default="", kw_only=True)


# --------------------------------------------------------------------------- #
# Live-Gemini PRE-FLIGHT guard (review M1).
#
# When ``--with-ai`` runs with NO working key (unset → ``MissingApiKeyError``,
# or a leaked/disabled key → ``google.genai`` ``ClientError`` 403), BOTH the
# ReflectionEngine and the StrategyAdvisorImpl fail-soft to EMPTY: zero
# proposals enqueued (no weight divergence), every reflection a
# ``fail_soft_unreachable`` placeholder that STILL emits ``reflection_emitted``.
# Net: the exported ``survival_journey_ai.json`` is byte-equivalent to the
# NUMERICAL journey but is PRESENTED as the AI run — a silent, dishonest
# mislabel. The pre-flight probe runs ONE minimal ``structured_call`` against the
# SAME client the engines will use and ABORTS LOUDLY on any failure so a
# mislabeled run can never be written.
# --------------------------------------------------------------------------- #

# The trivial probe schema + prompt — the cheapest possible structured call. The
# only thing under test is whether the client can complete a structured call at
# all; the returned value is intentionally ignored.
_PREFLIGHT_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}
_PREFLIGHT_PROBE_PROMPT = "Connectivity probe — reply with {\"ok\": true}."


class AIPreflightError(RuntimeError):
    """The live-Gemini pre-flight probe failed; the AI run is aborted (M1).

    Raised by :func:`preflight_ai_connectivity` when the season's
    ``ai.llm_client`` cannot complete a minimal :meth:`structured_call` (an unset
    key → :class:`~agent.llm.gemini_client.MissingApiKeyError`, a leaked/disabled
    key → a ``google.genai`` ``ClientError`` 403, a network error, or malformed
    JSON). The exception carries the underlying reason as its ``__cause__`` and is
    NEVER swallowed — aborting loudly is the whole point, so a numerical run can
    never be silently mislabeled as the AI run.
    """


async def _probe_gemini_async(client: _LLMClient) -> None:
    """Fire ONE trivial ``structured_call`` against ``client`` (raises on failure).

    Async because the client call is async; :func:`preflight_ai_connectivity`
    drives it via :func:`asyncio.run` (the same pattern
    :func:`run_survival_season` uses to drive ``loop.run``). Any exception
    propagates verbatim — the sync wrapper wraps it into an
    :class:`AIPreflightError`.
    """
    await client.structured_call(
        model="",
        prompt=_PREFLIGHT_PROBE_PROMPT,
        schema=dict(_PREFLIGHT_PROBE_SCHEMA),
    )


def preflight_ai_connectivity(client: _LLMClient) -> None:
    """Probe ``client`` once; raise :class:`AIPreflightError` if it is unreachable.

    Runs a single minimal :meth:`structured_call` (a ``{"ok": boolean}`` schema +
    a 1-line prompt) against the SAME client the engines will consume. On success
    it returns ``None`` (the season proceeds). On ANY exception — a
    :class:`~agent.llm.gemini_client.MissingApiKeyError`, a ``google.genai``
    ``ClientError`` / 403, a network error, or a JSON error — it raises a clear
    :class:`AIPreflightError` (with the underlying error chained) and does NOT
    swallow it, so the live ``--with-ai`` entry aborts loudly instead of writing a
    numerical run mislabeled as the AI run.

    Tests inject a FAKE client whose ``structured_call`` either succeeds or
    raises, so the probe operates with NO real network.
    """
    try:
        asyncio.run(_probe_gemini_async(client))
    except Exception as exc:  # surface ALL failures loudly (M1)
        raise AIPreflightError(
            "live Gemini is UNREACHABLE for the --with-ai run "
            f"({type(exc).__name__}: {exc}); the AI run is ABORTED to avoid "
            "mislabeling a numerical run as the AI run. Fix GEMINI_API_KEY / "
            "connectivity, or run the numerical season instead."
        ) from exc


# A synthetic LOSING / STALE window for the applicability gate: current weights
# == baseline (no learning yet) and a clearly negative recent PnL — exactly the
# regime the strict prompt tells the model it SHOULD respond to with >=1 concrete
# weight_delta. Built once, module-level, so the gate is a pure structural probe.
_GATE_PROBE_WINDOW: Final[PerformanceWindow] = PerformanceWindow(
    tick=100,
    ts=datetime(1970, 1, 1, tzinfo=UTC),
    agent_id="preflight-gate-probe",
    phase=Phase.PHASE_2_APPRENTICE,
    current_weights=Weights(
        w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[1.0, 0.0], rho=0.05
    ),
    baseline_weights=Weights(
        w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[1.0, 0.0], rho=0.05
    ),
    recent_pnl_window_usd=-42.0,
    trigger="tick_interval",
    recent_pnl=[-5.0, -8.0, -3.0, -12.0, -6.0, -8.0],
    tick_count=100,
)


def preflight_ai_advisor_applicable(
    client: _LLMClient,
    *,
    model: str = "",
    allowed_keys: tuple[str, ...] | None = None,
) -> None:
    """Probe the STRICT advisor once; raise :class:`AIPreflightError` if it can't
    produce a single APPLICABLE weight delta (T-D-018 fail-FAST gate).

    Connectivity (``preflight_ai_connectivity``) only proves the client can round-
    trip a structured call; it does NOT prove the model emits a *filled,
    applicable* ``{"key","delta"}`` (the original 2.5h failure: every
    ``proposed_change`` came back ``{}``). This gate builds the REAL strict
    :class:`StrategyAdvisorImpl` (``weight_delta_only=True``) against a synthetic
    losing/stale window and asserts it returns >=1 ``weight_delta`` proposal whose
    enforced ``proposed_change`` carries a valid ``key`` + numeric ``delta``. If
    not → :class:`AIPreflightError` (abort BEFORE a long run is wasted).

    Budget isolation (review H-1 / r2 M-2): the gate builds its OWN dedicated
    :class:`L3CostGuard` internally and takes ONLY ``client`` — it can NEVER touch
    the season's shared ``l3_guard``, so it cannot steal in-run budget. This is a
    fast structural check, NOT the runtime guarantee (the ``require_applied_deltas``
    invariant in :func:`run_survival_export` is that guarantee).

    Tests inject a FAKE client, so the probe operates with NO real network.
    """
    advisor = StrategyAdvisorImpl(
        llm_client=client,
        cost_guard=L3CostGuard.from_env(),  # OWN guard — never the season's
        weight_delta_only=True,
        # r7 H-1: the probe runs BEFORE _build_life_loop — without the model
        # override a provider-pure MiniMax leg's preflight would still send
        # DEFAULT_GEMINI_MODEL. "" ⇒ the client self-resolves its default.
        model=model,
        # A9 (r2 M-6): the probe uses the SAME key vocabulary the run will
        # use — a genome-mode run must preflight the genome schema.
        allowed_keys=allowed_keys,
    )
    try:
        proposals = advisor.review_window(_GATE_PROBE_WINDOW)
    except Exception as exc:  # the advisor is fail-soft, but be defensive
        raise AIPreflightError(
            "L3 advisor applicability probe RAISED "
            f"({type(exc).__name__}: {exc}); the AI run is ABORTED."
        ) from exc
    applicable = [
        p
        for p in proposals
        if p.kind == "weight_delta"
        and isinstance(p.proposed_change.get("key"), str)
        and isinstance(p.proposed_change.get("delta"), (int, float))
        and not isinstance(p.proposed_change.get("delta"), bool)
    ]
    if not applicable:
        raise AIPreflightError(
            "L3 advisor produced NO applicable weight_delta proposal for a "
            "synthetic losing/stale window (got "
            f"{len(proposals)} proposal(s), 0 applicable); the model is not "
            "emitting filled {'key','delta'} payloads, so the AI run would not "
            "move the agent's weights. The run is ABORTED - check the model / "
            "GEMINI_API_KEY / MINIMAX_API_KEY, or run the numerical season."
        )


class _AutoApprovingAdvisor:
    """A ``StrategyAdvisor`` wrapper that auto-applies weight deltas in the sim.

    Wraps a real :class:`StrategyAdvisorImpl` (``inner``). On each
    :meth:`review_window` call it asks the inner advisor for proposals, then —
    for every ``kind == "weight_delta"`` proposal — enqueues the
    ``proposed_change`` on the per-life :class:`RuntimeAgentRunner` (exactly what
    the FastAPI ``/api/proposals/{id}/approve`` handler does in prod). The loop
    drains that queue at the START of the next tick and applies the delta to its
    weights, so the AI genuinely DRIVES the weights in the backtest (the "auto-
    approve in the sim" leg of the L6 plan).

    The proposals are returned UNCHANGED so the loop still persists them pending
    + emits ``strategy_advisor_fired`` with the real count — which is what makes
    the survival recorder stamp the reflection annotation on the next settled
    step. Non-weight_delta proposals (``new_signal_idea`` / ``prompt_tweak``) are
    returned but NOT auto-applied (mirrors prod, which routes those to a TODO
    file); the operator/orchestrator decides on those out of band.
    """

    def __init__(
        self,
        *,
        inner: StrategyAdvisor,
        runtime_agent: RuntimeAgentRunner,
    ) -> None:
        self.inner = inner
        self.runtime_agent = runtime_agent

    def review_window(self, window: PerformanceWindow) -> list[StrategyProposal]:
        """Delegate to ``inner``; auto-enqueue weight deltas; return unchanged.

        Fail-soft: a malformed ``proposed_change`` (missing ``key`` / ``delta``)
        is still enqueued defensively, but the loop's drain skips bad deltas, so
        a single bad proposal can never crash the season.
        """
        proposals = list(self.inner.review_window(window))
        for proposal in proposals:
            if proposal.kind != "weight_delta":
                continue
            try:
                self.runtime_agent.apply_weight_delta(dict(proposal.proposed_change))
            except (TypeError, ValueError):  # pragma: no cover - defensive
                # A pathological proposed_change shape (e.g. not a mapping) must
                # not bring down the whole season; the loop's own drain is the
                # second line of defence that skips a delta missing key/delta.
                continue
        return proposals


def _decision_engine_from_seed(
    seed: StrategyConfig,
    *,
    effective_entry_price_floor: float | None = None,
) -> DecisionEngine:
    """Thread the seed's sizing/abstention knobs into a ``DecisionEngine``.

    Codex R8: a directly-constructed loop given ONLY ``initial_weights`` falls
    back to ``DecisionEngine()`` DEFAULTS, changing bet/no-bet behaviour vs the
    seed (which carries sizing/abstention knobs in ``StrategyConfig``). Sizing
    stays FIXED per seed across all lives; learning evolves the fusion
    ``Weights`` only.

    Realism v3: the seed's ``min_edge``/``kappa`` (value-mode knobs) thread
    through too — they only ACT when the loop passes ``price=`` into
    ``decide()`` (value_betting). ``effective_entry_price_floor`` arms the
    engine-level side-aware floor gate for value mode.
    """
    return DecisionEngine(
        max_breath_risk_pct=seed.max_breath_risk_pct,
        min_bet_size_usd=seed.min_bet_size_usd,
        min_confidence=seed.min_confidence,
        min_edge=seed.min_edge,
        kappa=seed.kappa,
        entry_price_floor=effective_entry_price_floor,
        # A9 genome: the storm-conditional gate levers ride the seed
        # (0.0 defaults = byte-identical pre-kit arithmetic).
        gate_storm_sensitivity=seed.gate_storm_sensitivity,
        risk_storm_sensitivity=seed.risk_storm_sensitivity,
    )


class _FrozenInnerUpdater(WeightUpdater):
    """A :class:`WeightUpdater` whose settlement learning is a NO-OP.

    The reincarnation experiment's cold-start pass (learning_enabled=False)
    swaps this in as the per-life adapter's inner so the FULL settlement path
    (poller → adapter → recorder capture, breath/death physics) runs
    unchanged while the weights stay byte-frozen at the seed. Its EMA buffer
    never populates and nothing is carried.
    """

    async def update_from_settlement(
        self,
        *,
        current: Weights,
        phase: Phase,
        pnl_usd: float,
        size_usd: float,
        signal_scores: dict[str, float],
        bet_direction: float,
        desperate: bool = False,
    ) -> Weights:
        return current


def _build_life_loop(
    *,
    idx: int,
    state_dir: Path,
    snapshots: list[MarketSnapshot],
    schedule: SurvivalSchedule,
    seed: StrategyConfig,
    initial_weights: Weights,
    initial_breath: float,
    initial_bankroll_usd: float,
    shared_inner: WeightUpdater,
    recorder: SurvivalRecorder | None = None,
    ai: AISeasonContext | None = None,
    max_bet_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
    effective_entry_price_floor: float | None = None,
    learning_enabled: bool = True,
    tribute_policy: TributePolicy | None = None,
    tribute_rng: _random.Random | None = None,
    tribute_breath: float = 35.0,
    storm_enabled: bool = False,
    storm_tau: float = 0.05,
    storm_scale: float | None = None,
    divine_tithe: bool = False,
    tithe_every: int = 20,
    tithe_amount_usd: float = 20.0,
    tithe_breath_cost: float = 5.0,
) -> SandboxPhase2Loop:
    """Construct ONE fresh life loop (codex H2 + R5 + R8 + R2-MED).

    Fresh per life: state dir, ``_ReplayChainAdapter`` (breath/bankroll reset),
    executor, settlement client, writer, clock, tick source, AND the
    ``_SettlementLearningWeightUpdater`` (loop-bound via ``weights_holder``).
    SHARED across lives: the inner :class:`WeightUpdater` (its EMA carries).

    The loop is constructed with ``strategy_advisor=NoOpStrategyAdvisor()`` (codex
    R5 — never the live Gemini advisor in a backtest) and a seed-threaded
    ``decision_engine`` (codex R8). ``initial_weights`` carries the previous
    life's evolved weights (codex H3).

    Opt-in AI mode (``ai is not None``): the NoOp advisor is replaced by an
    :class:`_AutoApprovingAdvisor` wrapping a REAL :class:`StrategyAdvisorImpl`,
    a REAL :class:`ReflectionEngine` is wired (so the loop fires
    ``reflection_emitted`` on its cadence), and a FRESH per-life
    :class:`RuntimeAgentRunner` carries the auto-approved weight deltas the loop
    drains each tick. The ``decision_engine`` + the shared-inner-WeightUpdater
    settlement-learning wiring are UNCHANGED — the numerical EMA backbone stays
    identical; the AI proposals are an ADDITIONAL force on top of it. With
    ``ai is None`` this branch is skipped entirely and the path is byte-unchanged.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    mb_root = state_dir / "_mb"
    mb_root.mkdir(parents=True, exist_ok=True)

    provider = MarketSnapshotProvider(snapshots)
    writer = SandboxStateWriter(root=state_dir)
    market_table = _market_table_from_snapshots(snapshots)

    tick_source = SurvivalTickSource(schedule)
    chain_adapter = _ReplayChainAdapter(current_breath=initial_breath)
    # A3b: when a recorder with a non-identity loss-multiplier is wired, the chain
    # adapter is wrapped so LOSING BREATH deltas are amplified (calibration). With
    # the default 1.0 multiplier the wrapper is transparent.
    chain_for_loop: Any = chain_adapter
    if recorder is not None:
        chain_for_loop = recorder.wrap_chain(chain_adapter)

    # The schedule-driving clock is built FIRST (with a deferred loop reference)
    # so the executor + settlement client can take it at construction; the loop
    # is built next, then ``clock.loop`` is bound so ``now()`` can read the
    # loop's tick_counter.
    clock = _ScheduleDrivingClock(stops=schedule.stops)
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: market_table.get(mid),
        clock=clock,
    )
    settlement_client = _ReplaySettlementClient(provider=provider, clock=clock)

    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
        phase_reader=_BacktestPhaseReader(),
        decision_log=_BacktestDecisionLog(),
        engine_signals=None,
    )

    # AI mode (opt-in): build the FRESH per-life L6 closure off the season's
    # shared AI context. ``ai is None`` ⇒ the NoOp scaffold (numerical-only),
    # byte-unchanged from the pre-AI path. The runtime_agent is FRESH per life
    # (a dead life's undrained deltas must NOT leak into a respawn); the loop
    # drains it at the START of each tick and applies the auto-approved deltas.
    strategy_advisor: StrategyAdvisor
    reflection_engine: ReflectionEngine | None
    runtime_agent: RuntimeAgentRunner | None
    populate_reflection_window: bool | None
    # Cadence: the loop's own defaults (advisor M=100; reflection N=10 via the
    # ``None`` sentinel). An AI context may override either to force an early
    # fire inside a short fixture.
    strategy_advisor_tick_interval: int = DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL
    reflection_tick_interval: int | None = None
    if ai is None:
        strategy_advisor = NoOpStrategyAdvisor()
        reflection_engine = None
        runtime_agent = None
        populate_reflection_window = None
    else:
        runtime_agent = RuntimeAgentRunner()
        reflection_engine = ReflectionEngine(
            llm_client=ai.llm_client,
            reflections_dir=mb_root / "reflections",
            # Provider-correct model routing (r6 H-1 / r7 H-2): the engine's
            # ctor has NO single `model` param — only the tiered pair. With
            # ctx.model="" each client self-resolves its own default, so a
            # provider-pure leg is never sent a foreign model id.
            sonnet_model=ai.model,
            opus_model=ai.model,
        )
        strategy_advisor = _AutoApprovingAdvisor(
            inner=StrategyAdvisorImpl(
                llm_client=ai.llm_client,
                cost_guard=ai.l3_guard,
                model=ai.model,
                # STRICT mode: in the auto-approve sim the LLM must always emit a
                # filled, applicable {"key","delta"} or the run isn't AI-driven.
                # _build_proposal enforces this locally (provider schema is only a
                # hint); non-conforming items are dropped before auto-approval.
                weight_delta_only=True,
            ),
            runtime_agent=runtime_agent,
        )
        populate_reflection_window = True
        if ai.strategy_advisor_tick_interval is not None:
            strategy_advisor_tick_interval = ai.strategy_advisor_tick_interval
        if ai.reflection_tick_interval is not None:
            reflection_tick_interval = ai.reflection_tick_interval

    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=settlement_client,
        # Inert placeholder satisfying the poller's flat-float WeightUpdater
        # Protocol at construction; the real settlement-learning adapter (wrapping
        # the SHARED EMA-carrying inner updater) is swapped onto the poller below.
        weight_updater=_NoopSettlementWeightUpdater(),
        chain_adapter=_cast_chain(chain_for_loop),
        tick_inputs=tick_source,
        state_hook=(
            recorder.state_hook() if recorder is not None else _RecordingStateHook()
        ),
        state_writer=writer,
        clock=clock,
        decision_cadence=timedelta(0),
        initial_breath=initial_breath,
        initial_bankroll_usd=initial_bankroll_usd,
        initial_weights=initial_weights,
        initial_phase=Phase.PHASE_2_APPRENTICE,
        strategy_advisor=strategy_advisor,
        reflection_engine=reflection_engine,
        runtime_agent=runtime_agent,
        populate_reflection_window=populate_reflection_window,
        strategy_advisor_tick_interval=strategy_advisor_tick_interval,
        reflection_tick_interval=reflection_tick_interval,
        decision_engine=_decision_engine_from_seed(
            seed, effective_entry_price_floor=effective_entry_price_floor
        ),
        # Realism cap (None = legacy/live physics): per-bet profit ceiling
        # enforced inside the settlement poller for learner AND breath alike.
        max_bet_pnl_usd=max_bet_pnl_usd,
        # Realism v3 (False/None = legacy/live physics): side-correct payouts,
        # value-mode decisions, bet-level effective-price floor.
        side_correct_pricing=side_correct_pricing,
        value_betting=value_betting,
        effective_entry_price_floor=effective_entry_price_floor,
        tribute_policy=tribute_policy,
        tribute_rng=tribute_rng,
        tribute_breath=tribute_breath,
        # A9 storm percept (False = byte-identical pre-kit physics).
        storm_enabled=storm_enabled,
        storm_tau=storm_tau,
        storm_scale=storm_scale,
        # A10 divine tithe (False = byte-identical; the gods charge no rent).
        divine_tithe=divine_tithe,
        tithe_every=tithe_every,
        tithe_amount_usd=tithe_amount_usd,
        tithe_breath_cost=tithe_breath_cost,
    )
    # Bind the deferred loop reference so the clock can pin now() to
    # stops[loop.tick_counter].
    clock.loop = loop

    # FRESH per-life settlement-learning adapter bound to THIS loop, wrapping the
    # SHARED inner updater (codex R2-MED): reusing the adapter would mutate the
    # DEAD previous loop; a fresh inner updater would drop the EMA.
    # Reincarnation cold-start (learning_enabled=False): a frozen inner takes
    # the SAME adapter seat — settlement physics (breath/death) and the
    # recorder's per-step capture run unchanged, but every gradient step
    # returns the current weights verbatim, so the seed stays byte-frozen.
    learning_adapter = _SettlementLearningWeightUpdater(
        inner=shared_inner if learning_enabled else _FrozenInnerUpdater(),
        weights_holder=loop,
    )
    # A3: when a recorder is wired, the settlement-update call is wrapped so each
    # settled bet is captured (pre/post weights + metadata) WITHOUT changing the
    # learning behaviour — the recorder delegates to ``learning_adapter`` first.
    loop._poller.weight_updater = (
        recorder.wrap_updater(learning_adapter)
        if recorder is not None
        else learning_adapter
    )
    return loop


def run_survival_season(
    *,
    rows: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    seed: StrategyConfig,
    state_root: Path,
    initial_breath: float,
    initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
    max_lives: int = DEFAULT_MAX_LIVES,
    settle_lag: timedelta = DEFAULT_SETTLE_LAG,
    max_bet_pnl_usd: float | None = None,
    recorder: SurvivalRecorder | None = None,
    ai: AISeasonContext | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
    effective_entry_price_floor: float | None = None,
    shared_inner: WeightUpdater | None = None,
    learning_enabled: bool = True,
    tribute_policy: TributePolicy | None = None,
    tribute_rng: _random.Random | None = None,
    tribute_breath: float = 35.0,
    storm_enabled: bool = False,
    storm_tau: float = 0.05,
    storm_scale: float | None = None,
    divine_tithe: bool = False,
    tithe_every: int = 20,
    tithe_amount_usd: float = 20.0,
    tithe_breath_cost: float = 5.0,
) -> SeasonResult:
    """Drive a multi-life FRESH-loop survival season (A2).

    A *life* is ONE :meth:`SandboxPhase2Loop.run` (settlement learning ON) over
    the remaining markets' entry-ordered schedule until ``breath<=0`` death. The
    SAME loop can NOT resurrect (``run()`` returns immediately once ``_alive`` is
    False, ``sandbox_phase2_loop.py:1210-1222``), so on death the driver:

    * captures the dead loop's evolved ``weights`` (``:1037``) + death facts;
    * constructs a BRAND-NEW loop (:func:`_build_life_loop`) with a fresh state
      dir + fresh chain adapter / breath / bankroll and
      ``initial_weights = previous.weights`` (codex H3);
    * advances the schedule cursor PAST the markets the dead life consumed
      (a market decided in a prior life is never re-decided);
    * voids any unsettled open bets (the fresh loop's cold start drops them).

    Cross-death learning persistence (codex H3 + R2-MED): ONE inner
    :class:`WeightUpdater` (the EMA owner) is SHARED across every life, wrapped by
    a FRESH per-life ``_SettlementLearningWeightUpdater`` bound to that life's
    loop. The loop keeps consuming markets across lives until the schedule is
    exhausted OR ``max_lives`` is reached.

    Parameters
    ----------
    rows / snapshots
        The season universe — the joined :class:`SurvivalRow` schedule keys and
        the matching :class:`MarketSnapshot` settlement copies (the executor's
        market table + the replay settlement client read these).
    seed
        The (fragile) :class:`StrategyConfig`: its ``weights`` seed life 0, and
        its sizing/abstention knobs are threaded into EVERY life's
        ``DecisionEngine`` (codex R8). Sizing is fixed; learning evolves
        ``Weights`` only.
    state_root
        Per-life state dirs are created under this root (``state_root/life_{i}``)
        so a dead life's persisted snapshot can't leak into a respawn.
    initial_breath / initial_bankroll_usd
        Reset at the START of every life (only weights + the shared EMA carry).
    max_lives
        Safety cap so an always-dying agent can't loop forever.
    settle_lag
        The executor's settle lag the schedule mirrors (``end_date + lag``).
    recorder
        Optional A3 :class:`SurvivalRecorder`. When supplied, each life's loop is
        wired to it (the settlement-update wrapper + the death state-hook + the
        loss-multiplier chain wrapper) so the season produces a per-settlement
        journey. With it omitted the season behaves exactly as A2.
    ai
        Optional opt-in AI context (the L6 reflect→optimize closure). ``None``
        (the default) keeps the pure-NUMERICAL path (``NoOpStrategyAdvisor`` +
        no reflection engine), byte-unchanged. When supplied, the SAME context
        (its shared ``llm_client`` + ``l3_guard``) is forwarded into EVERY
        life's :func:`_build_life_loop`, which builds a FRESH per-life
        ``RuntimeAgentRunner`` + real engines so the AI drives the weights on
        top of the numerical EMA backbone (auto-approved weight deltas).
    """
    snaps_by_id = {s.market_id: s for s in snapshots}
    # Reincarnation carry seam: an injected inner (its EMA buffer) survives
    # ACROSS seasons; the default fresh instance keeps every existing caller
    # byte-identical.
    if shared_inner is None:
        shared_inner = WeightUpdater()

    remaining = list(rows)
    lives: list[LifeOutcome] = []
    carry_weights = seed.weights
    state_root = Path(state_root)

    while remaining and len(lives) < max_lives:
        idx = len(lives)
        if recorder is not None:
            recorder.begin_life(idx)
        schedule = build_survival_schedule(remaining, settle_lag=settle_lag)
        # Only the snapshots for the remaining markets are needed for this life's
        # executor table / settlement client (a consumed market never re-enters).
        life_snaps = [snaps_by_id[r.market_id] for r in remaining]
        state_dir = state_root / f"life_{idx}"
        loop = _build_life_loop(
            idx=idx,
            state_dir=state_dir,
            snapshots=life_snaps,
            schedule=schedule,
            seed=seed,
            initial_weights=carry_weights,
            initial_breath=initial_breath,
            initial_bankroll_usd=initial_bankroll_usd,
            shared_inner=shared_inner,
            recorder=recorder,
            ai=ai,
            max_bet_pnl_usd=max_bet_pnl_usd,
            side_correct_pricing=side_correct_pricing,
            value_betting=value_betting,
            effective_entry_price_floor=effective_entry_price_floor,
            learning_enabled=learning_enabled,
            tribute_policy=tribute_policy,
            tribute_rng=tribute_rng,
            tribute_breath=tribute_breath,
            storm_enabled=storm_enabled,
            storm_tau=storm_tau,
            storm_scale=storm_scale,
            divine_tithe=divine_tithe,
            tithe_every=tithe_every,
            tithe_amount_usd=tithe_amount_usd,
            tithe_breath_cost=tithe_breath_cost,
        )

        max_ticks = len(schedule.stops)
        summary = asyncio.run(loop.run(until=None, max_ticks=max_ticks))

        # Realism v3 (r8 H-1): harvest this life's PLACED-bet ledger from its
        # open_bets.jsonl BEFORE the temp state dir can be cleaned up. ~55% of
        # placed bets never settle (the agent dies waiting), so the export's
        # effective-floor invariant scans this ledger — settled ``steps`` alone
        # would let a sub-floor placed-never-settled order evade the backstop.
        if recorder is not None:
            recorder.placed_bets.extend(
                iter_jsonl(loop._writer.open_bets_path)
            )

        # Which markets did this life actually DECIDE? The loop ran
        # ``ticks_completed`` ticks over the schedule's stops in order; the
        # decision stops among those are the consumed markets. (A death mid-tick
        # still consumed that tick's market — it was decided before the death
        # check, ``sandbox_phase2_loop.py:1484,:1579``.)
        consumed: list[str] = []
        for stop in schedule.stops[: summary.ticks_completed]:
            if stop.market_id is not None:
                consumed.append(stop.market_id)

        death: DeathFacts | None = None
        if summary.died and summary.death_receipt is not None:
            r = summary.death_receipt
            death = DeathFacts(
                life_idx=idx,
                last_tick=summary.ticks_completed - 1,
                final_breath=summary.final_breath,
                final_bankroll_usd=summary.final_bankroll_usd,
                kill_tx_hash=r.kill_tx_hash,
                tombstone_token_id=r.tombstone_token_id,
                tombstone_tx_hash=r.tombstone_tx_hash,
            )

        lives.append(
            LifeOutcome(
                idx=idx,
                state_dir=state_dir,
                start_ts=(
                    schedule.stops[0].asof_ts
                    if schedule.stops
                    else datetime.now(tz=UTC)
                ),
                initial_weights=carry_weights,
                terminal_weights=loop.weights,
                consumed_market_ids=tuple(consumed),
                bets_placed=summary.bets_placed,
                no_bets_emitted=summary.no_bets_emitted,
                settlements_processed=summary.settlements_processed,
                final_breath=summary.final_breath,
                final_bankroll_usd=summary.final_bankroll_usd,
                died=summary.died,
                death=death,
            )
        )

        # Carry the evolved weights into the next life (codex H3).
        carry_weights = loop.weights

        # Advance the cursor PAST the markets this life consumed. Open
        # (unsettled) bets at death are VOIDED implicitly: the next life is a
        # fresh loop with a fresh chain adapter + cold-start state dir, so those
        # bets never settle — their markets are simply NOT re-scheduled.
        consumed_set = set(consumed)
        remaining = [r for r in remaining if r.market_id not in consumed_set]

        if not summary.died:
            # The life ran to schedule exhaustion without dying — the season is
            # over (a surviving life consumes the rest of the markets).
            break

    return SeasonResult(
        lives=tuple(lives),
        deaths=sum(1 for life in lives if life.died),
        seed=seed,
        shared_weight_updater=shared_inner,
    )


# =========================================================================== #
# A3 — frozen STATIC baseline + archetype baselines + journey export.
# =========================================================================== #


@dataclass(frozen=True)
class BaselinePoint:
    """One point on a comparator cumulative-PnL curve (codex R3).

    A baseline is the SAME ``SurvivalRow`` entry order decided by a FROZEN
    comparator (no ``WeightUpdater`` touches the weights), so each point carries
    the row's per-bet PnL + the running cumulative. ``weights`` is the (frozen)
    fusion used to decide — on the static baseline it equals the seed's weights
    on EVERY point (the proof the baseline is frozen). ``is_bet`` distinguishes a
    bet from a NO_BET (a NO_BET point is flat: ``pnl_usd == 0``).
    """

    idx: int
    market_id: str
    is_bet: bool
    side: str | None
    size_usd: float
    pnl_usd: float
    cum_pnl: float
    weights: Weights


async def _static_baseline_curve_async(
    rows: list[SurvivalRow],
    seed: StrategyConfig,
    *,
    bankroll: float,
    breath: float,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
    effective_entry_price_floor: float | None = None,
) -> list[BaselinePoint]:
    """The FROZEN static baseline curve — seed weights, NO learning (codex R3).

    Iterates ``rows`` in their (entry) order, deciding each INDEPENDENTLY with
    the seed's frozen weights + seed-threaded ``DecisionEngine`` (mirrors
    ``cached_sweep.score_config`` but emits a per-row cumulative curve instead of
    aggregate metrics — the master plan needs a curve comparable to the learning
    run, NOT a ``SweepMetrics``). NO ``WeightUpdater`` is constructed: the weights
    are byte-frozen at the seed on every point.

    Realism v3: ``value_betting`` passes ``price=row.entry_price`` into
    decide() (the frozen twin must run the SAME decision policy as the
    learner — engine ctor params alone are a silent no-op, r2 H-1);
    ``effective_entry_price_floor`` applies the same post-decision gate as
    the loop (r4 M-1), and ``side_correct_pricing`` prices the taken leg.
    """
    engine = _decision_engine_from_seed(
        seed, effective_entry_price_floor=effective_entry_price_floor
    )
    w = seed.weights
    alpha = (w.alpha[0], w.alpha[1], w.alpha[2])
    beta = (w.beta[0], w.beta[1])

    out: list[BaselinePoint] = []
    cum = 0.0
    for i, row in enumerate(rows):
        action = await engine.decide(
            signals=row_to_signals(row.signal),
            weights_alpha=alpha,
            weights_beta=beta,
            w_r=w.w_r,
            w_s=w.w_s,
            rho=w.rho,
            bankroll_usd=bankroll,
            breath=breath,
            liquidity_cap_usd=row.liquidity_cap,
            market_id=row.market_id,
            desperate=False,
            **({"price": row.entry_price} if value_betting else {}),
        )
        # Post-decision effective-floor gate (r4 M-1): identical to the
        # loop's pre-place_order gate, so a legacy-mode static baseline can
        # never bet a sub-floor effective side.
        if (
            action.kind is ActionKind.BET
            and effective_entry_price_floor is not None
            and action.side is not None
        ):
            eff = effective_entry_price(
                side=action.side.value, yes_price=row.entry_price
            )
            if eff < effective_entry_price_floor:
                action = Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=f"effective_price_below_floor:{eff:.4f}",
                )
        if action.kind is ActionKind.BET:
            assert action.side is not None and action.size_usd is not None
            pnl = compute_bet_pnl(
                side=action.side.value,
                entry_price=row.entry_price,
                size_usd=action.size_usd,
                outcome=row.outcome,
                winning_price=row.winning_price,
                max_pnl_usd=max_pnl_usd,
                side_correct_pricing=side_correct_pricing,
            )
            cum += pnl
            out.append(
                BaselinePoint(
                    idx=i,
                    market_id=row.market_id,
                    is_bet=True,
                    side=action.side.value,
                    size_usd=action.size_usd,
                    pnl_usd=pnl,
                    cum_pnl=cum,
                    weights=w,
                )
            )
        else:
            out.append(
                BaselinePoint(
                    idx=i,
                    market_id=row.market_id,
                    is_bet=False,
                    side=None,
                    size_usd=0.0,
                    pnl_usd=0.0,
                    cum_pnl=cum,
                    weights=w,
                )
            )
    return out


def build_static_baseline_curve(
    rows: list[SurvivalRow],
    seed: StrategyConfig,
    *,
    bankroll: float = DEFAULT_PHASE2_BANKROLL_USD,
    breath: float = 100.0,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
    effective_entry_price_floor: float | None = None,
) -> list[BaselinePoint]:
    """Synchronous wrapper around :func:`_static_baseline_curve_async`."""
    return asyncio.run(
        _static_baseline_curve_async(
            rows,
            seed,
            bankroll=bankroll,
            breath=breath,
            max_pnl_usd=max_pnl_usd,
            side_correct_pricing=side_correct_pricing,
            value_betting=value_betting,
            effective_entry_price_floor=effective_entry_price_floor,
        )
    )


# Fixed archetype stake — a flat $5 bet (the $100-bankroll cap, ``real_signal_
# sweep.md`` finding #3) so the archetype curves are on the SAME PnL scale as the
# learner / static baseline without re-running the sizer.
_ARCHETYPE_STAKE_USD = 5.0

# A neutral placeholder weight for the signal-free archetype points (they never
# consult it; it exists only so ``BaselinePoint.weights`` stays non-optional).
_ARCHETYPE_PLACEHOLDER_WEIGHTS = Weights(
    w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=0.0
)

ArchetypeName = Literal["random", "always_favorite"]


def build_archetype_curve(
    rows: list[SurvivalRow],
    *,
    archetype: ArchetypeName,
    seed: int = 0,
    stake_usd: float = _ARCHETYPE_STAKE_USD,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    effective_entry_price_floor: float | None = None,
) -> list[BaselinePoint]:
    """A naive-archetype cumulative-PnL curve over the SAME entry order (context).

    * ``"always_favorite"`` — bet the implied FAVORITE every market (YES when the
      YES price ``entry_price >= 0.5``, else NO). The dumb-but-disciplined
      benchmark.
    * ``"random"`` — a SEEDED-deterministic coin flip per market (same ``seed``
      reproduces the curve byte-for-byte). The pure-noise floor.

    Both bet a flat ``stake_usd`` (no sizer) so the curve sits on the learner's
    PnL scale. These are deliberately naive, signal-free overlays — they never
    consult a fusion ``Weights``, so each point carries a neutral placeholder
    (:data:`_ARCHETYPE_PLACEHOLDER_WEIGHTS`) purely to keep
    :attr:`BaselinePoint.weights` non-optional.
    """
    rng = _random.Random(seed)
    out: list[BaselinePoint] = []
    cum = 0.0
    for i, row in enumerate(rows):
        # The RNG draw happens BEFORE the floor check so random's draw
        # sequence stays row-aligned whether or not the floor skips the bet.
        if archetype == "always_favorite":
            side = "YES" if row.entry_price >= 0.5 else "NO"
        else:  # random
            side = "YES" if rng.random() < 0.5 else "NO"
        # Bet-level effective floor (realism v3): a skipped bet contributes
        # pnl 0 / is_bet=False — curve length stays len(rows) so the x-axis
        # stays aligned with the universe. always_favorite never trips it
        # (its effective price is >= 0.5 by construction); random can.
        if effective_entry_price_floor is not None:
            eff = effective_entry_price(side=side, yes_price=row.entry_price)
            if eff < effective_entry_price_floor:
                out.append(
                    BaselinePoint(
                        idx=i,
                        market_id=row.market_id,
                        is_bet=False,
                        side=side,
                        size_usd=0.0,
                        pnl_usd=0.0,
                        cum_pnl=cum,
                        weights=_ARCHETYPE_PLACEHOLDER_WEIGHTS,
                    )
                )
                continue
        pnl = compute_bet_pnl(
            side=side,
            entry_price=row.entry_price,
            size_usd=stake_usd,
            outcome=row.outcome,
            winning_price=row.winning_price,
            max_pnl_usd=max_pnl_usd,
            side_correct_pricing=side_correct_pricing,
        )
        cum += pnl
        out.append(
            BaselinePoint(
                idx=i,
                market_id=row.market_id,
                is_bet=True,
                side=side,
                size_usd=stake_usd,
                pnl_usd=pnl,
                cum_pnl=cum,
                weights=_ARCHETYPE_PLACEHOLDER_WEIGHTS,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# A3b — fragile-seed calibration.
# --------------------------------------------------------------------------- #


def fragile_seed_from_config(
    base: StrategyConfig,
    *,
    max_breath_risk_pct: float = 1.0,
    min_confidence: float | None = None,
    min_bet_size_usd: float | None = None,
) -> StrategyConfig:
    """Derive a deliberately FRAGILE seed from a (too-good) config (codex R7).

    The static OPTIMUM (low ``max_breath_risk_pct``, 81.5% win) is too good to
    die — its worst drawdown stays under the breath line, so a survival run
    seeded with it would NEVER die and the "learns to survive across deaths"
    story would not exist. This cranks the sizing FRAGILE (full-breath risk by
    default) while PRESERVING the fusion ``weights`` — learning still evolves the
    same weight space; only the calibration differs. ``min_bet_size_usd`` is held
    BELOW $5 so the bet still clears the $100-bankroll cap (``real_signal_sweep``
    finding #3: a min-bet above the $5 cap places zero bets).
    """
    mb = base.min_bet_size_usd if min_bet_size_usd is None else min_bet_size_usd
    mb = min(mb, 4.0)
    mc = base.min_confidence if min_confidence is None else min_confidence
    # A9 (r11 L-3): derivative seeds use dataclasses.replace — a
    # field-by-field rebuild would silently DROP any genome field added
    # later (γ/γ2 today, anything tomorrow). Only the deliberately
    # overridden knobs are named; everything else carries verbatim.
    return dataclasses.replace(
        base,
        max_breath_risk_pct=max_breath_risk_pct,
        min_confidence=mc,
        min_bet_size_usd=mb,
    )


# --------------------------------------------------------------------------- #
# Journey export — down-sampled steps + summary + baseline overlays.
# --------------------------------------------------------------------------- #


def _weights_to_dict(w: Weights) -> dict[str, float]:
    """Project :class:`Weights` to a JSON-safe dict (the 6 fusion params)."""
    return {
        "w_r": w.w_r,
        "w_s": w.w_s,
        "alpha_0": w.alpha[0],
        "alpha_1": w.alpha[1],
        "alpha_2": w.alpha[2],
        "beta_0": w.beta[0],
        "beta_1": w.beta[1],
        "rho": w.rho,
    }


def _downsample(items: list[Any], max_n: int) -> list[Any]:
    """Evenly down-sample ``items`` to at most ``max_n`` (keeping first + last).

    The chart series is down-sampled for the dashboard; the FULL fidelity is kept
    for drill-down (the caller exposes ``total_steps``). With ``max_n <= 0`` or a
    list already within budget the list is returned unchanged.
    """
    n = len(items)
    if max_n <= 0 or n <= max_n:
        return list(items)
    if max_n == 1:
        return [items[-1]]
    # Pick max_n indices spread across [0, n-1], always including 0 and n-1.
    step = (n - 1) / (max_n - 1)
    idxs = sorted({round(i * step) for i in range(max_n)})
    return [items[i] for i in idxs]


def _step_to_dict(step: SurvivalStep) -> dict[str, Any]:
    out: dict[str, Any] = {
        "idx": step.life_idx,
        "life_idx": step.life_idx,
        "market": {
            "market_id": step.market_id,
            "slug": step.slug,
            "players": list(step.players) if step.players is not None else None,
            "surface": step.surface,
            "entry_price": step.entry_price,
            "outcome": step.outcome,
        },
        "side": step.side,
        "size": step.size_usd,
        "pnl": step.pnl_usd,
        "cum_pnl": step.cum_pnl,
        "weights": _weights_to_dict(step.weights_after),
        "weights_before": _weights_to_dict(step.weights_before),
        "breath": step.breath_after,
        "win_rate": step.running_win_rate,
        "signals": step.signal_scores,
    }
    # Page-2 data contract: ``reflection?`` is OPTIONAL — only emit the key when
    # an L6 reflection / proposal annotation is present so the flag-OFF journey
    # (no reflection field at all) is byte-identical to the pre-B3 export.
    if step.reflection is not None:
        out["reflection"] = step.reflection
    # A9 (r5 M-4): the storm stamps are OPTIONAL keys — omitted when None
    # so flag-off journey/reincarnation artifacts keep the pre-kit keyset.
    for stamp_key in (
        "storm_at_bet",
        "edge_at_bet",
        "min_edge_at_bet",
        "gamma_at_bet",
        "eff_min_edge_at_bet",
    ):
        stamp_val = getattr(step, stamp_key)
        if stamp_val is not None:
            out[stamp_key] = stamp_val
    return out


def _baseline_to_dicts(curve: list[BaselinePoint]) -> list[dict[str, Any]]:
    return [
        {
            "idx": p.idx,
            "market_id": p.market_id,
            "is_bet": p.is_bet,
            "side": p.side,
            "size": p.size_usd,
            "pnl": p.pnl_usd,
            "cum_pnl": p.cum_pnl,
        }
        for p in curve
    ]


def build_survival_journey(
    *,
    result: SeasonResult,
    recorder: SurvivalRecorder,
    rows: list[SurvivalRow],
    seed: StrategyConfig,
    max_steps: int = 500,
    bankroll: float = DEFAULT_PHASE2_BANKROLL_USD,
    breath: float = 100.0,
    random_seed: int = 0,
    entry_price_floor: float | None = None,
    max_bet_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
    effective_entry_price_floor: float | None = None,
) -> dict[str, Any]:
    """Build the Page-2 ``survival_journey`` dict (down-sampled + baselines).

    Assembles the explicit recorder's per-settlement :attr:`SurvivalRecorder.steps`
    (down-sampled to ``max_steps`` for the chart series; ``summary.total_steps``
    keeps the full count for drill-down) + the per-life metadata + the FROZEN
    static baseline + the random / always-favorite archetype overlays + a summary
    carrying the death count and the headline learner-vs-static cumulative-PnL
    delta. The whole dict is JSON-serialisable (the A4 export payload).

    Realism rules (T-rules 2026-06-11): ``max_bet_pnl_usd`` is forwarded to all
    three baseline builders so the baselines obey the SAME per-bet profit cap the
    learner ran under (the learner's cap lives in the settlement poller). Both
    rule knobs are also DISCLOSED in the summary (``entry_price_floor`` /
    ``max_bet_pnl_usd``) together with compact full-data EVIDENCE keys
    (``rows_after_floor``, ``min_entry_price``, ``max_step_pnl``,
    ``max_baseline_pnl``) so a consumer can verify the rules held without
    trusting the down-sampled ``steps`` (review r1 M-1). ``rows`` must be the
    same (already-floored) list the season consumed — membership-by-construction.
    """
    static_curve = build_static_baseline_curve(
        rows,
        seed,
        bankroll=bankroll,
        breath=breath,
        max_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=side_correct_pricing,
        value_betting=value_betting,
        effective_entry_price_floor=effective_entry_price_floor,
    )
    random_curve = build_archetype_curve(
        rows,
        archetype="random",
        seed=random_seed,
        max_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=side_correct_pricing,
        effective_entry_price_floor=effective_entry_price_floor,
    )
    favorite_curve = build_archetype_curve(
        rows,
        archetype="always_favorite",
        max_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=side_correct_pricing,
        effective_entry_price_floor=effective_entry_price_floor,
    )

    learner_final = recorder.steps[-1].cum_pnl if recorder.steps else 0.0
    static_final = static_curve[-1].cum_pnl if static_curve else 0.0

    lives_payload: list[dict[str, Any]] = []
    for life in result.lives:
        life_steps = [s for s in recorder.steps if s.life_idx == life.idx]
        lives_payload.append(
            {
                "idx": life.idx,
                "start_ts": life.start_ts.isoformat(),
                "bets": life.bets_placed,
                "settlements": life.settlements_processed,
                "final_breath": life.final_breath,
                "final_bankroll_usd": life.final_bankroll_usd,
                "pnl": sum(s.pnl_usd for s in life_steps),
                "death": (
                    {
                        "cause": "breath_depleted",
                        "last_tick": life.death.last_tick,
                        "breath": life.death.final_breath,
                        "kill_tx_hash": life.death.kill_tx_hash,
                        "tombstone_token_id": life.death.tombstone_token_id,
                    }
                    if life.died and life.death is not None
                    else None
                ),
            }
        )

    sampled = _downsample(recorder.steps, max_steps)
    best_life = (
        max(
            range(len(result.lives)),
            key=lambda i: sum(
                s.pnl_usd for s in recorder.steps if s.life_idx == i
            ),
        )
        if result.lives
        else None
    )

    # Full-data realism EVIDENCE (review r1 M-1): computed over the COMPLETE
    # recorder steps + all baseline curves + the full row universe — NOT the
    # down-sampled ``steps`` — so a consumer can verify the rules held without
    # re-deriving the full data. None-safe when the corresponding list is empty.
    all_baseline_pnls = [
        p.pnl_usd for curve in (static_curve, random_curve, favorite_curve) for p in curve
    ]
    max_step_pnl = max((s.pnl_usd for s in recorder.steps), default=None)
    max_baseline_pnl = max(all_baseline_pnls, default=None)
    min_entry_price = min((r.entry_price for r in rows), default=None)

    # Realism v3 physics invariant (r1 H-1 + r4 M-1 + r5 M-2 + r8 H-1):
    # recompute EVERY settled learner step and EVERY baseline bet from first
    # principles, so a journey violating its own physics can never be built
    # (the export writes only what this returns). Gated on
    # ``side_correct_pricing`` so legacy (v1/v2) journeys stay byte-unchanged.
    rows_by_id = {r.market_id: r for r in rows}
    if side_correct_pricing:
        for s in recorder.steps:
            expected = compute_bet_pnl(
                side=s.side,
                entry_price=s.entry_price,
                size_usd=s.size_usd,
                outcome=s.outcome,
                winning_price=s.winning_price,
                max_pnl_usd=max_bet_pnl_usd,
                side_correct_pricing=True,
            )
            if abs(expected - s.pnl_usd) > 1e-6:
                raise RuntimeError(
                    f"physics invariant violated: learner step pnl "
                    f"{s.pnl_usd!r} != recomputed {expected!r} for "
                    f"{s.market_id}; journey NOT built"
                )
        for curve_name, curve in (
            ("static", static_curve),
            ("random", random_curve),
            ("always_favorite", favorite_curve),
        ):
            for p in curve:
                if not p.is_bet:
                    if p.size_usd != 0.0 or p.pnl_usd != 0.0:
                        raise RuntimeError(
                            f"physics invariant violated: skipped "
                            f"{curve_name} point {p.market_id} carries "
                            f"size={p.size_usd!r}/pnl={p.pnl_usd!r}; "
                            "journey NOT built"
                        )
                    continue
                assert p.side is not None
                row = rows_by_id[p.market_id]
                expected = compute_bet_pnl(
                    side=p.side,
                    entry_price=row.entry_price,
                    size_usd=p.size_usd,
                    outcome=row.outcome,
                    winning_price=row.winning_price,
                    max_pnl_usd=max_bet_pnl_usd,
                    side_correct_pricing=True,
                )
                if abs(expected - p.pnl_usd) > 1e-6:
                    raise RuntimeError(
                        f"physics invariant violated: {curve_name} point pnl "
                        f"{p.pnl_usd!r} != recomputed {expected!r} for "
                        f"{p.market_id}; journey NOT built"
                    )

    # Effective-price evidence (r5 M-2 + r8 H-1): the minimum EFFECTIVE side
    # price across ALL PLACED learner bets (settled or not — the recorder's
    # placed_bets ledger, harvested from each life's open_bets.jsonl) and all
    # baseline bets. The floor backstop scans THIS — settled steps alone would
    # let a sub-floor placed-never-settled order evade it.
    eff_prices: list[float] = []
    for b in recorder.placed_bets:
        side_v = b.get("side")
        price_v = b.get("price")
        if isinstance(side_v, str) and isinstance(price_v, (int, float)):
            eff_prices.append(
                effective_entry_price(side=side_v, yes_price=float(price_v))
            )
    for curve in (static_curve, random_curve, favorite_curve):
        for p in curve:
            if p.is_bet and p.side is not None:
                eff_prices.append(
                    effective_entry_price(
                        side=p.side,
                        yes_price=rows_by_id[p.market_id].entry_price,
                    )
                )
    min_effective_entry_price = min(eff_prices, default=None)
    if (
        effective_entry_price_floor is not None
        and min_effective_entry_price is not None
        and min_effective_entry_price < effective_entry_price_floor
    ):
        raise RuntimeError(
            f"physics invariant violated: a placed bet's effective entry "
            f"price {min_effective_entry_price!r} is below "
            f"effective_entry_price_floor {effective_entry_price_floor!r}; "
            "journey NOT built"
        )

    return {
        "seed": {
            "weights": _weights_to_dict(seed.weights),
            "max_breath_risk_pct": seed.max_breath_risk_pct,
            "min_confidence": seed.min_confidence,
            "min_bet_size_usd": seed.min_bet_size_usd,
            "min_edge": seed.min_edge,
            "kappa": seed.kappa,
        },
        "lives": lives_payload,
        "steps": [_step_to_dict(s) for s in sampled],
        "baselines": {
            "static": _baseline_to_dicts(static_curve),
            "random": _baseline_to_dicts(random_curve),
            "always_favorite": _baseline_to_dicts(favorite_curve),
        },
        "summary": {
            "deaths": result.deaths,
            "lives": len(result.lives),
            "best_life": best_life,
            "total_steps": len(recorder.steps),
            "learner_final_pnl": learner_final,
            "static_final_pnl": static_final,
            "learning_vs_static_delta": learner_final - static_final,
            # T-D-018 genuine-AI-divergence signal: how many auto-approved weight
            # deltas the loop actually applied vs failed. 0/0 on the numerical
            # (non-AI) default — byte-unchanged for that path.
            "proposals_applied": recorder.proposals_applied,
            "proposals_apply_failed": recorder.proposals_apply_failed,
            # Realism-rule DISCLOSURE (None = rule off, legacy physics) +
            # full-data EVIDENCE (r1 M-1) — see docstring.
            "entry_price_floor": entry_price_floor,
            "max_bet_pnl_usd": max_bet_pnl_usd,
            "rows_after_floor": len(rows),
            "min_entry_price": min_entry_price,
            "max_step_pnl": max_step_pnl,
            "max_baseline_pnl": max_baseline_pnl,
            # Realism v3 DISCLOSURE + EVIDENCE (6 keys, typed: two booleans,
            # four numeric-or-null) — side-correct payouts, value-mode
            # decisions, the seed's value knobs, the bet-level effective
            # floor, and the full-ledger effective-price minimum.
            "side_correct_pricing": side_correct_pricing,
            "value_betting": value_betting,
            "min_edge": seed.min_edge,
            "kappa": seed.kappa,
            "effective_entry_price_floor": effective_entry_price_floor,
            "min_effective_entry_price": min_effective_entry_price,
        },
    }


# =========================================================================== #
# A4 — CLI + export to dashboard/public/backtest/survival_journey.json.
# =========================================================================== #

# The static-sweep OPTIMUM (``real_signal_sweep.md`` — n=256, seed=0, >=50 bets):
# the per-bet-Sharpe winner the static baseline rides on. The survival run does
# NOT seed from this directly (it is "too good to die", codex R7); it is the BASE
# the FRAGILE seed is derived from (sizing cranked fragile, fusion weights
# preserved) so learning can rescue the agent "from fragile to survivor".
_OPTIMUM_WEIGHTS = Weights(
    w_r=0.564,
    w_s=0.436,
    alpha=[0.486, 0.328, 0.186],
    beta=[0.443, 0.557],
    rho=0.186,
)
DEFAULT_OPTIMUM_SEED = StrategyConfig(
    weights=_OPTIMUM_WEIGHTS,
    max_breath_risk_pct=0.232,
    min_confidence=0.049,
    min_bet_size_usd=4.0,
)

# Default down-sample budget for the exported chart series (the master plan caps
# the journey steps at <= 2000 for the dashboard chart; the full fidelity is kept
# behind ``summary.total_steps`` for drill-down).
DEFAULT_MAX_STEPS = 2000

# Default fragile-seed initial BREATH (calibration knob, A3b). Low enough that the
# fragile seed's early losses can drain it to a death, so the "learns to survive
# across deaths" story can occur; tunable via ``--initial-breath`` at A5.
DEFAULT_INITIAL_BREATH = 100.0

# Default export path (relative to repo root) — the Page-2 data contract.
DEFAULT_OUT_PATH = Path("dashboard/public/backtest/survival_journey.json")

# Default output for the opt-in AI run (``--with-ai``). A SEPARATE artifact from
# the numerical journey so the dashboard can render both side-by-side (numerical
# vs AI-driven). Like its sibling it is a large regenerated artifact (gitignored).
DEFAULT_AI_OUT_PATH = Path(
    "dashboard/public/backtest/survival_journey_ai.json"
)


def _build_corpus_resolver() -> TennisMatchResolver:
    """Build the resolver off the re-vendored Sackmann corpus (offline).

    Mirrors ``cached_sweep._cmd_precompute`` / ``find_optimal_config.
    _make_real_signal_source_factory``: the full ``DEFAULT_CORPUS_DIR`` so slug
    resolution is offline + ~65.8%. Imported lazily (not at module import) to
    avoid the ``data.sources`` package's import-order circular and to keep the
    test path (which injects an empty resolver) from parsing the corpus.
    """
    from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

    loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
    return TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2026))


def run_survival_export(
    *,
    rows_path: Path,
    cache_dir: Path,
    out_path: Path,
    base_seed: StrategyConfig = DEFAULT_OPTIMUM_SEED,
    fragile_max_breath_risk_pct: float = 1.0,
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER,
    initial_breath: float = DEFAULT_INITIAL_BREATH,
    initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
    max_lives: int = DEFAULT_MAX_LIVES,
    max_steps: int = DEFAULT_MAX_STEPS,
    settle_lag: timedelta = DEFAULT_SETTLE_LAG,
    state_root: Path | None = None,
    resolver: TennisMatchResolver | None = None,
    random_seed: int = 0,
    with_ai: bool = False,
    ai: AISeasonContext | None = None,
    preflight: bool = True,
    max_markets: int | None = None,
    require_applied_deltas: bool = False,
    entry_price_floor: float | None = DEFAULT_ENTRY_PRICE_FLOOR,
    max_bet_pnl_usd: float | None = DEFAULT_MAX_BET_PNL_USD,
    side_correct_pricing: bool = True,
    value_betting: bool = True,
    effective_entry_price_floor: float | None = MIRROR_ROW_FLOOR,
) -> dict[str, Any]:
    """Load → run the survival season → build + write the Page-2 journey (A4).

    The ``run_sweep``-style helper extracted from :func:`main` so callers (and
    tests) can drive the export over a TINY cache and assert on the returned
    journey dict directly. It:

    1. loads the cached signal rows (``rows_path``) + the cassette snapshots
       (``cache_dir``) and JOINs them into :class:`SurvivalRow` (A0);
    2. derives the deliberately FRAGILE seed from ``base_seed`` (A3b — sizing
       cranked fragile, fusion weights preserved) so the agent can die + learn;
    3. drives the multi-life FRESH-loop respawn season with a wired
       :class:`SurvivalRecorder` (A2 + A3);
    4. builds the down-sampled :func:`build_survival_journey` dict (A3) and
       writes it as UTF-8 JSON to ``out_path`` (parent dirs created).

    Returns the in-memory journey dict it wrote.

    Parameters
    ----------
    rows_path / cache_dir
        The cached ``_signal_rows.json`` + the ``_cache_tennis`` snapshot dir.
    out_path
        Where the down-sampled ``survival_journey.json`` is written.
    base_seed
        The (too-good) base the fragile seed is derived from. Defaults to the
        documented static optimum (``DEFAULT_OPTIMUM_SEED``).
    fragile_max_breath_risk_pct / loss_multiplier / initial_breath
        The A3b calibration knobs (full-breath risk + an optional loss-magnitude
        multiplier + the starting breath) tuned so deaths occur.
    max_steps
        The chart-series down-sample budget (<= this many steps; the full count
        is kept under ``summary.total_steps``).
    state_root
        Per-life state dirs root; a temp dir is used when omitted (the season's
        loop state is transient — only the journey JSON is the deliverable).
    resolver
        The slug->player/surface resolver. ``None`` builds the corpus resolver;
        the test injects an empty one to skip the Sackmann parse.
    with_ai
        When True (and no ``ai`` is injected), construct the DEFAULT production
        AI context: a lazily-imported :class:`GeminiClient` + ``L3CostGuard``
        from env, and run the L6 reflect→optimize closure (real engines +
        auto-approved weight deltas). Default False keeps the pure-numerical
        path byte-unchanged.
    ai
        An explicit :class:`AISeasonContext` (the TEST seam — inject a FAKE LLM
        client so no live Gemini call fires). Wins over ``with_ai``; when
        supplied the SDK is never imported. ``None`` + ``with_ai=False`` ⇒
        numerical-only.
    preflight
        When the AI run is active (``ai`` injected OR ``with_ai=True``), run two
        one-shot probes against the season's ``ai.llm_client`` BEFORE the season
        runs and ABORT with :class:`AIPreflightError` on failure (so a numerical
        run is never silently written as the AI run): (1) the live-Gemini
        CONNECTIVITY probe (:func:`preflight_ai_connectivity`, review M1); (2) the
        ADVISOR-APPLICABILITY fail-fast gate (:func:`preflight_ai_advisor_applicable`,
        T-D-018 — the strict advisor must produce >=1 applicable weight delta).
        Defaults True; set False to disable both probes (the test seam, and the
        numerical path never probes regardless). NEVER runs on the numerical path
        (``ai is None`` and ``with_ai=False``).
    max_markets
        Optional cap on the market universe for a FAST verification run. When set,
        the joined ``survival_rows`` are sliced to the first ``max_markets`` (in
        ``build_survival_rows`` / cache order; the season re-sorts by entry time
        internally, so the temporal arc differs from the full run — acceptable for
        verification). The recorder, season, and journey (incl. its frozen static
        baseline) all consume the SAME sliced rows, so the comparison stays
        apples-to-apples. The full ``snapshots`` dict is kept (by-id lookup —
        extra entries are harmless). ``None`` ⇒ the full universe (unchanged).
    require_applied_deltas
        HARD zero-delta invariant (review H-1). When True AND the AI run is active
        (``season_ai is not None``), after the season but BEFORE writing the
        artifact, raise :class:`AIPreflightError` if ``recorder.proposals_applied
        == 0`` (the LLM never moved the weights — the original fail-soft failure
        mode). No bad artifact is written. Default False keeps every existing AI
        test + prod path byte-unchanged; the verification run passes True.
    entry_price_floor / max_bet_pnl_usd
        Realism rules (2026-06-11, user-locked), DEFAULTED ON at this level only
        (the loop/poller/sweep layers default ``None`` so the live runtime and
        the config sweep are byte-unchanged). The floor drops untradeable
        extreme longshots from the universe (see :func:`build_survival_rows`);
        the cap clamps a single bet's PROFIT inside the settlement poller (so
        the journey AND the breath update both see the capped value) and is
        forwarded to the baseline builders for identical physics. Both rules
        apply to the numerical and AI paths alike (shared chokepoints). An
        in-export invariant validates the FULL data against both rules BEFORE
        the artifact is written (review r1 M-1) and the summary discloses the
        knobs + full-data evidence. ``None`` disables (legacy physics).
    """
    # Realism v3 (r5 M-1): resolve the MIRROR_ROW_FLOOR sentinel — omitted ⇒
    # the bet-level floor mirrors the row floor's value; explicit None ⇒
    # disabled; any other float ⇒ that value.
    eff_floor: float | None
    if (
        effective_entry_price_floor is not None
        and effective_entry_price_floor == MIRROR_ROW_FLOOR
    ):
        eff_floor = entry_price_floor
    else:
        eff_floor = effective_entry_price_floor

    rows_raw = load_rows(rows_path)
    snapshots = load_all_cached_markets(cache_dir=cache_dir)
    if resolver is None:
        resolver = _build_corpus_resolver()
    survival_rows = build_survival_rows(
        rows_raw, snapshots, resolver, entry_price_floor=entry_price_floor
    )
    # Evidence: how many cached rows the floor dropped (the journey summary
    # discloses it so the artifact is self-describing about its universe).
    rows_dropped_by_floor = len(rows_raw) - len(survival_rows)

    # Subset cap (verification): slice ONCE here, in build order. All three
    # downstream consumers read ``survival_rows`` (recorder, season, journey +
    # its frozen static baseline) so they inherit the slice automatically and
    # stay apples-to-apples. ``snapshots`` is a by-id lookup — keep it whole
    # (extra entries are harmless; the season only indexes the rows it's given).
    if max_markets is not None:
        survival_rows = survival_rows[:max_markets]

    seed = fragile_seed_from_config(
        base_seed, max_breath_risk_pct=fragile_max_breath_risk_pct
    )
    recorder = SurvivalRecorder(rows=survival_rows, loss_multiplier=loss_multiplier)

    # AI mode: an explicitly-injected ``ai`` context wins (the test path — a fake
    # LLM, never live Gemini). Otherwise ``with_ai=True`` builds the DEFAULT
    # production context: a lazy GeminiClient (the SDK import is deferred to here
    # so the default/test path never pays for it) + the env-driven L3 budget cap.
    season_ai = ai
    if season_ai is None and with_ai:
        from agent.llm.factory import make_llm_client

        season_ai = AISeasonContext(
            llm_client=make_llm_client(),
            l3_guard=L3CostGuard.from_env(),
        )

    # Live-Gemini PRE-FLIGHT (review M1): when the AI run is active, probe the
    # SAME client the engines will use with ONE minimal structured_call BEFORE
    # the season runs. If it is unreachable, abort LOUDLY (AIPreflightError) so a
    # numerical run can never be silently written as the AI run. NEVER runs on the
    # numerical path (``season_ai is None``) or when ``preflight`` is disabled.
    if season_ai is not None and preflight:
        preflight_ai_connectivity(season_ai.llm_client)
        # Fail-FAST applicability gate (T-D-018): connectivity alone doesn't prove
        # the model emits FILLED, applicable weight deltas. Probe the strict
        # advisor once (its OWN isolated budget — never the season's) and abort
        # BEFORE a long run if it can't produce a single applicable delta.
        # ctx.model threads in (r7 H-1) so a provider-pure leg's probe never
        # sends a foreign model id.
        preflight_ai_advisor_applicable(season_ai.llm_client, model=season_ai.model)

    import tempfile

    def _run(root: Path) -> SeasonResult:
        return run_survival_season(
            rows=survival_rows,
            snapshots=snapshots,
            seed=seed,
            state_root=root,
            initial_breath=initial_breath,
            initial_bankroll_usd=initial_bankroll_usd,
            max_lives=max_lives,
            settle_lag=settle_lag,
            max_bet_pnl_usd=max_bet_pnl_usd,
            recorder=recorder,
            ai=season_ai,
            side_correct_pricing=side_correct_pricing,
            value_betting=value_betting,
            effective_entry_price_floor=eff_floor,
        )

    if state_root is not None:
        result = _run(Path(state_root))
    else:
        with tempfile.TemporaryDirectory(prefix="survival_season_") as tmp:
            result = _run(Path(tmp))

    # HARD zero-delta invariant (review H-1): on an AI run that asked for it,
    # refuse to write the artifact if the LLM never moved the weights. Keyed off
    # ``season_ai is not None`` (an injected ``ai`` also activates AI mode, so
    # ``with_ai`` would mis-key it). This catches the case the fail-fast gate
    # can't: the gate passed but in-run LLM failures fail-softed every real
    # proposal — exactly the original 2.5h-run failure mode. Raised BEFORE the
    # write so no bad artifact ever lands.
    if (
        season_ai is not None
        and require_applied_deltas
        and recorder.proposals_applied == 0
    ):
        raise AIPreflightError(
            "AI run applied ZERO weight deltas "
            f"(applied={recorder.proposals_applied}, "
            f"failed={recorder.proposals_apply_failed}); the LLM never moved the "
            "agent's weights, so this run is NOT genuinely AI-driven. The "
            "artifact is NOT written. Check the model / connectivity, or relax "
            "require_applied_deltas for a deliberately-degraded run."
        )

    journey = build_survival_journey(
        result=result,
        recorder=recorder,
        rows=survival_rows,
        seed=seed,
        max_steps=max_steps,
        bankroll=initial_bankroll_usd,
        breath=initial_breath,
        random_seed=random_seed,
        entry_price_floor=entry_price_floor,
        max_bet_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=side_correct_pricing,
        value_betting=value_betting,
        effective_entry_price_floor=eff_floor,
    )
    # Universe-drop evidence (computed here — only this scope knows the
    # pre-floor row count; ``build_survival_journey`` only ever sees the
    # already-floored list).
    journey["summary"]["rows_dropped_by_floor"] = rows_dropped_by_floor

    # Realism INVARIANT (review r1 M-1): validate the FULL in-memory data —
    # not the down-sampled ``steps`` — against both rules BEFORE the write, so
    # an artifact violating its own physics can never land on disk. The journey
    # summary already carries the full-data maxima/minima (evidence keys), so
    # the check is O(1) reads of those plus the recorder scan they came from.
    if max_bet_pnl_usd is not None:
        max_step = journey["summary"]["max_step_pnl"]
        max_base = journey["summary"]["max_baseline_pnl"]
        if max_step is not None and max_step > max_bet_pnl_usd:
            raise RuntimeError(
                f"realism invariant violated: a learner step's pnl {max_step!r} "
                f"exceeds max_bet_pnl_usd {max_bet_pnl_usd!r}; artifact NOT written"
            )
        if max_base is not None and max_base > max_bet_pnl_usd:
            raise RuntimeError(
                f"realism invariant violated: a baseline point's pnl {max_base!r} "
                f"exceeds max_bet_pnl_usd {max_bet_pnl_usd!r}; artifact NOT written"
            )
    if entry_price_floor is not None:
        min_entry = journey["summary"]["min_entry_price"]
        if min_entry is not None and min_entry < entry_price_floor:
            raise RuntimeError(
                f"realism invariant violated: a survival row's entry_price "
                f"{min_entry!r} is below entry_price_floor {entry_price_floor!r}; "
                "artifact NOT written"
            )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False so unicode player surnames survive; UTF-8 explicit so the
    # Windows host's default cp1252 can't corrupt the bytes the dashboard reads.
    out_path.write_text(
        json.dumps(journey, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return journey


def _cmd_run(args: Any) -> int:
    # ``--no-resolver`` injects the EMPTY resolver (no corpus parse) for fast /
    # offline runs; otherwise ``run_survival_export`` builds the corpus resolver.
    resolver = TennisMatchResolver(name_index={}) if args.no_resolver else None
    # ``--out`` defaults to None so we can pick the AI-vs-numerical default based
    # on ``--with-ai`` ONLY when the operator didn't pass an explicit path.
    out = args.out or (DEFAULT_AI_OUT_PATH if args.with_ai else DEFAULT_OUT_PATH)
    journey = run_survival_export(
        rows_path=args.rows,
        cache_dir=args.cache_dir,
        out_path=out,
        fragile_max_breath_risk_pct=args.fragile_seed,
        loss_multiplier=args.loss_multiplier,
        initial_breath=args.initial_breath,
        initial_bankroll_usd=args.initial_bankroll,
        max_lives=args.max_lives,
        max_steps=args.max_steps,
        resolver=resolver,
        with_ai=args.with_ai,
    )
    summary = journey["summary"]
    print(
        f"wrote {out} — lives={summary['lives']} deaths={summary['deaths']} "
        f"steps={len(journey['steps'])}/{summary['total_steps']} "
        f"learning_vs_static_delta=${summary['learning_vs_static_delta']:.2f}",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint — ``run`` drives the season + writes the journey JSON.

    Returns a process exit code. UTF-8 safe (slugs / players may be unicode; the
    export writes ``ensure_ascii=False`` UTF-8).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent.backtest.survival_season",
        description="L5 survival-journey engine: drive the multi-life respawn "
        "season over the cached real signals + write the Page-2 journey.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser(
        "run", help="Run the survival season + export the journey JSON."
    )
    p_run.add_argument("--rows", type=Path, required=True)
    p_run.add_argument("--cache-dir", type=Path, required=True)
    # Default None so ``_cmd_run`` can route to the AI-vs-numerical default path
    # based on ``--with-ai`` ONLY when no explicit ``--out`` was given.
    p_run.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output journey JSON path. Defaults to the numerical artifact, or "
        "the AI artifact when --with-ai is set.",
    )
    p_run.add_argument(
        "--with-ai",
        action="store_true",
        help="Opt-in AI mode (L6 reflect->optimize): wire the REAL "
        "ReflectionEngine + StrategyAdvisorImpl into each life and auto-approve "
        "the advisor's weight_delta proposals. Requires GEMINI_API_KEY. "
        "Default OFF (pure numerical EMA learning).",
    )
    p_run.add_argument(
        "--fragile-seed",
        type=float,
        default=1.0,
        help="The fragile seed's max_breath_risk_pct (1.0 = full-breath risk).",
    )
    p_run.add_argument(
        "--loss-multiplier",
        type=float,
        default=DEFAULT_LOSS_MULTIPLIER,
        help="Amplify the magnitude of LOSING settlements fed to BREATH (A3b "
        "calibration; 1.0 = identity).",
    )
    p_run.add_argument(
        "--initial-breath", type=float, default=DEFAULT_INITIAL_BREATH
    )
    p_run.add_argument(
        "--initial-bankroll", type=float, default=DEFAULT_PHASE2_BANKROLL_USD
    )
    p_run.add_argument("--max-lives", type=int, default=DEFAULT_MAX_LIVES)
    p_run.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help="Down-sample the chart series to <= this many steps.",
    )
    p_run.add_argument(
        "--no-resolver",
        action="store_true",
        help="Inject an EMPTY slug resolver (skip the Sackmann corpus parse) — "
        "players/surface fall back to None.",
    )
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    func: object = args.func
    assert callable(func)
    result = func(args)
    assert isinstance(result, int)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
