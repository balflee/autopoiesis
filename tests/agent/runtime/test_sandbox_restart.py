"""Tests for :mod:`agent.runtime.sandbox_phase2_loop` — T-B-020.

CEO Day 3 V-gate: both restart scenarios MUST pass before the sprint
can land.

Scenarios
---------

(a) **Mid-run kill**: 5 decision ticks → "SIGKILL" (drop loop instance)
    → reconstruct a fresh loop against the SAME ``state_dir`` → assert
    rehydrated weights are byte-for-byte equal, tick counter resumes at
    ``last_tick + 1 == 6``, open_bet ids unchanged.

(b) **Settlement-during-downtime**: place 1 bet → kill loop → flip
    :class:`MockGammaAPI` so the bet's market resolves → restart loop
    → drive ONE poll cycle → assert (i) ``settled_bets.jsonl`` grew by
    1, (ii) ``weight_updater.update`` called exactly once with
    ``phase=PHASE_2_EXTENDED``, (iii) ``open_bets.jsonl`` grew by 1 new
    line carrying ``status="settled"`` for that bet_id.

Plus a small smoke test that the composition invariant
``not issubclass(SandboxPhase2Loop, Phase2LaunchOrchestrator)`` holds.

Hermetic invariants
-------------------

* Every test uses a ``tmp_path``-rooted :class:`SandboxStateWriter`.
* The loop's default :class:`Sleeper` is replaced with an instrumented
  fake that returns immediately — multi-day runs would otherwise burn
  real wall-clock.
* :class:`MockGammaAPI` is the SettlementClient; no real httpx calls.
* The chain adapter is a fake that records every call.
* The decision cadence is set to ``timedelta(0)`` so the loop's
  inter-tick sleep is a no-op (the test drives the loop with a finite
  ``until``).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase, Side, Weights
from agent.data.polymarket_sandbox_executor import (
    MarketInfo,
    SandboxExecutor,
)
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    AgentStateSnapshot,
    SandboxStateWriter,
    iter_jsonl,
)
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DEFAULT_DECISION_CADENCE,
    DeathReceipt,
    RunSummary,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    TickInputSource,
    WeightUpdaterPhase,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI

# --------------------------------------------------------------------------- #
# Test doubles — minimal fakes for the loop's injected Protocols.
# --------------------------------------------------------------------------- #


class FakeWeightUpdater:
    """Spy :class:`WeightUpdater` — captures every settlement update call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None:
        self.calls.append(
            {"phase": phase, "signals": dict(signals), "outcome": outcome}
        )


@dataclass
class FakeChainAdapter:
    """Combined chain adapter for the loop tests.

    Implements the broader :class:`SandboxLoopChainAdapter` Protocol:

    * :meth:`update_breath_from_pnl` — applied to ``current_breath``.
    * :meth:`read_breath` — returns ``current_breath`` verbatim
      (chain-as-source-of-truth).
    * :meth:`kill_and_mint_tombstone` — returns a fixed receipt + flips
      ``killed`` True so tests assert the death path fired.
    """

    current_breath: float = 100.0
    pnl_updates: list[float] = field(default_factory=list)
    read_calls: int = 0
    killed: bool = False
    last_kill_kwargs: dict[str, Any] | None = None

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)
        self.current_breath += pnl_usd

    async def read_breath(self) -> float:
        self.read_calls += 1
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
        # T-B-021 extended kwargs are recorded verbatim so the
        # forced-terminal test can assert non-empty PRD §5.1 metadata
        # without reaching into private fields.
        self.killed = True
        self.last_kill_kwargs = {
            "agent_id": agent_id,
            "bankroll_usd": bankroll_usd,
            "last_tick": last_tick,
            "final_weights_hash": final_weights_hash,
            "memory_bank_cid": memory_bank_cid,
            "last_words": last_words,
        }
        return DeathReceipt(
            kill_tx_hash="0x" + "a" * 64,
            tombstone_token_id="42",
            tombstone_tx_hash="0x" + "b" * 64,
        )


class FakeStateHook:
    """Records every emitted state hook."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


class FakeSleeper:
    """Records sleep durations + returns immediately."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FixedClock:
    """Tick-advancing clock — each :meth:`advance` bumps the cursor.

    When ``auto_advance`` is set, every :meth:`now` call advances the
    clock by that delta. Used by the loop tests so a ``decision_cadence
    = 0`` loop still has a notion of forward time for the
    settlement-due check inside the poller.
    """

    def __init__(
        self,
        *,
        start: datetime,
        auto_advance: timedelta = timedelta(0),
    ) -> None:
        self._now = start
        self._auto_advance = auto_advance

    def now(self) -> datetime:
        current = self._now
        if self._auto_advance > timedelta(0):
            self._now = self._now + self._auto_advance
        return current

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


@dataclass
class ScriptedTickInputs:
    """Deterministic :class:`TickInputSource` for the restart tests.

    On each ``inputs_for`` call we return a :class:`TickInputs` whose
    signals are a function of the tick number — high-confidence positive
    signals so the :class:`DecisionEngine` routes to BET on every tick.
    """

    market_id: str = "m-restart-001"
    price: float = 0.4
    liquidity_cap_usd: float = 50.0
    force_no_market: bool = False

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        if self.force_no_market:
            return None
        # All 5 engines bullish + high confidence → fused_score > 0,
        # the decision engine routes to BET.
        signals = _bullish_signals(asof_ts=asof_ts, tick=tick)
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
        )


def _bullish_signals(*, asof_ts: datetime, tick: int) -> dict[str, Signal]:
    """5-engine HIGH-conviction bullish read.

    Magnitudes intentionally large + confidences uniformly high so the
    4-constraint min in :class:`DecisionEngine` clears the $5 min_bet
    floor (see ``DEFAULT_MIN_BET_SIZE_USD``). The restart scenarios
    need every tick to route to BET so the open_bets ledger has
    something to fold across the kill / restart boundary.

    The tick number varies ``raw_features`` so two consecutive
    :class:`Signal` instances are not byte-identical — defends against
    a hypothetical caching path that would mask a re-evaluation bug.
    """
    iso = asof_ts.isoformat()
    return {
        TENNIS_TECHNICAL: Signal(
            score=0.9, confidence=0.9, available_at=iso,
            rationale="strong technical read",
            raw_features={"tick": float(tick)},
        ),
        MARKET_MOMENTUM: Signal(
            score=0.8, confidence=0.9, available_at=iso,
            rationale="momentum agrees",
            raw_features={"tick": float(tick)},
        ),
        SURFACE_ADVANTAGE: Signal(
            score=0.7, confidence=0.85, available_at=iso,
            rationale="surface edge favours YES",
            raw_features={"tick": float(tick)},
        ),
        HEAD_TO_HEAD: Signal(
            score=0.6, confidence=0.8, available_at=iso,
            rationale="head-to-head favours YES",
            raw_features={"tick": float(tick)},
        ),
        REST_RECENCY: Signal(
            score=0.6, confidence=0.85, available_at=iso,
            rationale="rest/recency favours YES",
            raw_features={"tick": float(tick)},
        ),
    }


class _NoopPhaseReader:
    """Phase reader for the base orchestrator — never called by the loop."""

    def read_phase(self) -> Phase:  # pragma: no cover
        return Phase.PHASE_2_APPRENTICE


class _NoopDecisionLog:
    """Decision-log writer for the base orchestrator — never called by the loop."""

    def append(  # pragma: no cover
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        return "0x_unused"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _build_base(memory_bank_root: Path) -> Phase2LaunchOrchestrator:
    """Construct a wrap-target :class:`Phase2LaunchOrchestrator`.

    The loop uses ONLY ``base.emitter`` from the wrapped orchestrator
    (and even that is optional for the restart tests). The fake
    PhaseManagerReader + DecisionLog satisfy the Protocols but are
    never invoked in the restart tests.
    """
    memory_bank_root.mkdir(parents=True, exist_ok=True)
    return Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=memory_bank_root),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,
    )


def _build_loop(
    *,
    state_dir: Path,
    memory_bank_root: Path,
    settlement_client: Any,
    weight_updater: FakeWeightUpdater | None = None,
    chain_adapter: FakeChainAdapter | None = None,
    state_hook: FakeStateHook | None = None,
    sleeper: FakeSleeper | None = None,
    clock: FixedClock | None = None,
    tick_inputs: TickInputSource | None = None,
    market_table: dict[str, MarketInfo] | None = None,
    decision_cadence: timedelta = timedelta(0),
    initial_breath: float = 100.0,
    initial_bankroll_usd: float = 100.0,
) -> tuple[
    SandboxPhase2Loop,
    FakeWeightUpdater,
    FakeChainAdapter,
    FakeStateHook,
    FakeSleeper,
    FixedClock,
    SandboxStateWriter,
    SandboxExecutor,
]:
    """Construct a :class:`SandboxPhase2Loop` wired to fakes."""
    wu = weight_updater if weight_updater is not None else FakeWeightUpdater()
    ca = chain_adapter if chain_adapter is not None else FakeChainAdapter(
        current_breath=initial_breath,
    )
    sh = state_hook if state_hook is not None else FakeStateHook()
    sl = sleeper if sleeper is not None else FakeSleeper()
    cl = clock if clock is not None else FixedClock(
        start=datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC),
    )
    ti: TickInputSource = (
        tick_inputs if tick_inputs is not None else ScriptedTickInputs()
    )

    writer = SandboxStateWriter(root=state_dir)
    # Default market table covers the canonical restart market id; tests
    # override via the ``market_table`` kwarg if they need a different set.
    table = market_table or {
        "m-restart-001": MarketInfo(end_date_iso="2026-05-26T17:00:00+00:00"),
    }
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: table.get(mid),
        clock=cl,
    )
    base = _build_base(memory_bank_root)
    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=settlement_client,
        weight_updater=wu,
        chain_adapter=cast(SandboxLoopChainAdapter, ca),
        tick_inputs=ti,
        state_hook=sh,
        state_writer=writer,
        clock=cl,
        sleeper=sl,
        decision_cadence=decision_cadence,
        initial_breath=initial_breath,
        initial_bankroll_usd=initial_bankroll_usd,
    )
    return loop, wu, ca, sh, sl, cl, writer, executor


_T = Any  # narrowed at call sites; mypy's asyncio.run is generic-typed already


def _run(coro: Any) -> Any:
    """Tiny ``asyncio.run`` wrapper for sync test bodies."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Composition invariant — the brief locks "NOT issubclass".
# --------------------------------------------------------------------------- #


def test_composition_invariant_holds() -> None:
    """The CEO plan locks composition; subclassing would break the contract."""
    assert not issubclass(SandboxPhase2Loop, Phase2LaunchOrchestrator)


def test_default_decision_cadence_is_60_min() -> None:
    """Sandbox extended Phase 2 cadence is 60 min per CEO Day 3 plan."""
    assert DEFAULT_DECISION_CADENCE == timedelta(minutes=60)


# --------------------------------------------------------------------------- #
# Helper — drive N ticks against a loop, return the RunSummary.
# --------------------------------------------------------------------------- #


def _drive_n_ticks(loop: SandboxPhase2Loop, *, n: int, clock: FixedClock) -> RunSummary:
    """Run the loop for exactly ``n`` ticks.

    Uses ``max_ticks`` (the test-only safety net the loop exposes) so
    the tick count is the deterministic bound regardless of the
    clock's advancement pattern.
    """
    # ``until`` is set to "far future" so the only stopping condition
    # is ``max_ticks``; the clock is read by the poller for the lag
    # threshold check but the loop iteration is bounded by tick count.
    far_future = clock.now() + timedelta(days=365)
    summary: RunSummary = asyncio.run(loop.run(until=far_future, max_ticks=n))
    return summary


# --------------------------------------------------------------------------- #
# Scenario (a) — mid-run kill, restart, weights byte-for-byte equal.
# --------------------------------------------------------------------------- #


def test_restart_scenario_a_mid_run_kill(tmp_path: Path) -> None:
    """5 ticks → kill → reconstruct → weights identical, counter resumes."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"

    # ---------------- Phase 1: run 5 ticks then "kill" -------------------
    gamma = MockGammaAPI()
    loop, wu, ca, sh, sleeper, clock, writer, executor = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
    )
    summary_1 = _drive_n_ticks(loop, n=5, clock=clock)
    assert summary_1.ticks_completed == 5
    assert summary_1.died is False

    # Capture state-of-the-world AT KILL.
    weights_at_kill = loop.weights
    open_bets_at_kill = loop.open_bet_ids
    tick_at_kill = loop.tick_counter
    bankroll_at_kill = loop.bankroll_usd
    snapshot_payload_at_kill = json.loads(
        writer.snapshot_path.read_text(encoding="utf-8")
    )
    open_bets_rows_at_kill = list(iter_jsonl(writer.open_bets_path))
    decisions_rows_at_kill = list(iter_jsonl(writer.decisions_path))

    # 5 decisions appended.
    assert len(decisions_rows_at_kill) == 5
    # tick_counter resumes one PAST the last tick.
    assert tick_at_kill == 5

    # "Kill" — drop the loop instance. Subsequent state read happens off
    # disk only; no shared in-memory references survive.
    del loop

    # ---------------- Phase 2: reconstruct from SAME state_dir -----------
    gamma_2 = MockGammaAPI()
    # Fresh fakes (sim'ing process restart — no in-memory carry-over).
    loop_2, wu_2, ca_2, sh_2, sleeper_2, clock_2, writer_2, executor_2 = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma_2,
        chain_adapter=FakeChainAdapter(current_breath=ca.current_breath),
    )
    reconstructed = _run(loop_2._reconstruct_from_disk())

    # ---- Acceptance: weights byte-for-byte equal -----------------------
    # JSON round-trip equality is the strictest "byte-for-byte" check —
    # the snapshot writes JSON; the model_validate_json reads JSON; if
    # the two payloads serialise identically the weights are identical.
    weights_at_kill_json = weights_at_kill.model_dump_json()
    weights_reconstructed_json = reconstructed.weights.model_dump_json()
    assert weights_reconstructed_json == weights_at_kill_json
    # AND structural equality at the Python level.
    assert reconstructed.weights == weights_at_kill

    # ---- Acceptance: tick_counter resumes at last_tick + 1 = 6 ---------
    assert reconstructed.last_tick == 4  # last APPENDED tick
    assert loop_2.tick_counter == 5      # internal counter pre-next-tick
    # The brief says "tick_counter == 6" — that's the value AFTER the
    # next _tick fires. Drive one more tick + assert.
    _drive_n_ticks(loop_2, n=1, clock=clock_2)
    assert loop_2.tick_counter == 6

    # ---- Acceptance: open_bets count unchanged across the restart ------
    open_bets_rows_after_reconstruct = list(iter_jsonl(writer_2.open_bets_path))
    # 5 BET ticks placed 5 bets; tick 6 placed another — so post-restart
    # open_bets.jsonl has either 5 or 6 lines depending on whether tick 6
    # was a BET. The RECONSTRUCTION result is the right gate for
    # "open_bets count unchanged":
    assert set(reconstructed.open_bet_ids) == set(open_bets_at_kill)
    # And the open_bets.jsonl rows AT KILL are a prefix of the rows
    # after the restart's first tick (append-only invariant).
    assert (
        open_bets_rows_after_reconstruct[: len(open_bets_rows_at_kill)]
        == open_bets_rows_at_kill
    )

    # Belt + braces: snapshot file's weights match the in-memory weights.
    assert snapshot_payload_at_kill.get("weights") is not None
    snapshot_weights = Weights.model_validate(snapshot_payload_at_kill["weights"])
    assert snapshot_weights == weights_at_kill

    # Phase + bankroll round-tripped too.
    assert reconstructed.bankroll_usd == bankroll_at_kill
    assert reconstructed.phase == Phase.PHASE_2_APPRENTICE
    assert reconstructed.cold_start is False


# --------------------------------------------------------------------------- #
# Scenario (b) — settlement-during-downtime.
# --------------------------------------------------------------------------- #


def test_restart_scenario_b_settlement_during_downtime(tmp_path: Path) -> None:
    """1 bet → kill → fast-forward gamma → restart → 1 poll cycle.

    Acceptance:
      (i)   settled_bets.jsonl grew by exactly 1.
      (ii)  weight_updater.update called exactly once with PHASE_2_EXTENDED.
      (iii) open_bets.jsonl has a new line carrying status='settled'
            for that bet_id.
    """
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"

    # ---------------- Phase 1: place 1 bet ------------------------------
    gamma = MockGammaAPI()
    # Register the market in PENDING posture; the kill happens BEFORE
    # the gamma-api sees the resolution.
    gamma.register_market(
        market_id="m-settle-001",
        outcome="yes",
        winning_price=1.0,
    )
    tick_inputs = ScriptedTickInputs(
        market_id="m-settle-001", price=0.4, liquidity_cap_usd=50.0,
    )
    market_table = {
        "m-settle-001": MarketInfo(end_date_iso="2026-05-26T17:00:00+00:00"),
    }
    loop, wu, ca, sh, sleeper, clock, writer, executor = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
        tick_inputs=tick_inputs,
        market_table=market_table,
    )
    # One tick → one BET placed.
    summary_1 = _drive_n_ticks(loop, n=1, clock=clock)
    assert summary_1.bets_placed == 1
    assert summary_1.died is False
    # No settlement happened pre-kill — settled_bets.jsonl is empty.
    assert iter_jsonl(writer.settled_bets_path) == []
    # weight_updater was NOT called yet (no settlement).
    assert wu.calls == []

    # Open bet recorded.
    assert len(loop.open_bet_ids) == 1
    bet_id = next(iter(loop.open_bet_ids))
    open_bets_rows_at_kill = list(iter_jsonl(writer.open_bets_path))
    assert len(open_bets_rows_at_kill) == 1

    # "Kill" the loop.
    del loop

    # ---------------- Fast-forward gamma-api ----------------------------
    # The market resolved while the agent was down. resolve_now flips
    # the market's state so subsequent gamma queries return a resolved
    # SettlementResult.
    gamma.resolve_now("m-settle-001")

    # ---------------- Phase 2: restart loop -----------------------------
    # Advance the clock by 24h so the original bet's expected_settle_ts
    # (2026-05-26 17:00 + 2h = 19:00) is definitively in the past on the
    # restart side (the start clock was 20:00; we advance to 26th 23:00).
    restart_clock = FixedClock(start=datetime(2026, 5, 26, 23, 0, 0, tzinfo=UTC))
    wu_2 = FakeWeightUpdater()
    loop_2, _, ca_2, sh_2, sleeper_2, clock_2, writer_2, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,  # same in-memory MockGammaAPI; resolved now
        weight_updater=wu_2,
        chain_adapter=ca,  # carry the same FakeChainAdapter (post-bet breath)
        clock=restart_clock,
        tick_inputs=tick_inputs,
        market_table=market_table,
    )

    # Drive reconstruction so the loop knows about the pre-kill open bet.
    reconstructed = _run(loop_2._reconstruct_from_disk())
    assert set(reconstructed.open_bet_ids) == {bet_id}

    # Drive ONE poll cycle — the brief's "run one poll cycle".
    poll_result = _run(loop_2.poller.tick())
    assert poll_result.settled_count == 1
    assert poll_result.pending_count == 0
    assert poll_result.failed_count == 0

    # ---- Acceptance (i): settled_bets.jsonl grew by 1 ------------------
    settled_rows = list(iter_jsonl(writer_2.settled_bets_path))
    assert len(settled_rows) == 1
    assert settled_rows[0]["bet_id"] == bet_id
    assert settled_rows[0]["outcome"] == "yes"
    # bet entered at 0.4, winning_price=1.0, size_usd > 0 → pnl > 0.
    pnl_usd_value = settled_rows[0]["pnl_usd"]
    assert isinstance(pnl_usd_value, (int, float))
    assert float(pnl_usd_value) > 0.0

    # ---- Acceptance (ii): weight_updater called once w/ PHASE_2_EXTENDED -
    assert len(wu_2.calls) == 1
    assert wu_2.calls[0]["phase"] == WeightUpdaterPhase.PHASE_2_EXTENDED.value
    assert wu_2.calls[0]["phase"] == "PHASE_2_EXTENDED"
    # The outcome was actually a SettlementResult; the loop's `outcome`
    # kwarg through the poller carries it.
    assert isinstance(wu_2.calls[0]["outcome"], SettlementResult)

    # ---- Acceptance (iii): open_bets.jsonl has a NEW settled line ------
    open_bets_rows_after_settle = list(iter_jsonl(writer_2.open_bets_path))
    assert len(open_bets_rows_after_settle) == len(open_bets_rows_at_kill) + 1
    # The pre-kill open row is preserved (append-only).
    assert open_bets_rows_after_settle[0] == open_bets_rows_at_kill[0]
    # The new line carries status='settled' for the same bet_id.
    assert open_bets_rows_after_settle[-1]["status"] == "settled"
    assert open_bets_rows_after_settle[-1]["bet_id"] == bet_id


# --------------------------------------------------------------------------- #
# Bonus — cold start sanity, β₁ unlock proof, NO_BET path.
# --------------------------------------------------------------------------- #


def test_cold_start_has_no_disk_artefacts(tmp_path: Path) -> None:
    """First-ever start: snapshot missing → cold_start=True; tick=0."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    gamma = MockGammaAPI()
    loop, _, _, _, _, _, _, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
    )
    reconstructed = _run(loop._reconstruct_from_disk())
    assert reconstructed.cold_start is True
    assert reconstructed.last_tick == -1
    assert reconstructed.open_bet_ids == []
    assert loop.tick_counter == 0


def test_chain_breath_overrides_disk_on_restart(tmp_path: Path) -> None:
    """CEO step 4 lock: chain.read_breath() is the source of truth."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    # Pre-seed a snapshot with disk_breath=42 — chain will return 88.
    writer = SandboxStateWriter(root=state_dir)
    weights = Weights(
        w_r=0.65, w_s=0.35,
        alpha=[0.5, 0.3, 0.2], beta=[0.6, 0.4], rho=0.25,
    )
    writer.write_snapshot(AgentStateSnapshot(
        snapshot_ts="2026-05-26T19:00:00+00:00",
        phase="PHASE_2_APPRENTICE",
        breath=42.0,
        bankroll_usd=88.0,
        phase_age_days=0.0,
        open_bet_ids=[],
        last_tick=2,
        weights=weights,
    ))
    gamma = MockGammaAPI()
    loop, _, ca, sh, _, _, _, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
        chain_adapter=FakeChainAdapter(current_breath=88.0),
    )
    reconstructed = _run(loop._reconstruct_from_disk())
    assert reconstructed.disk_breath == 42.0
    assert reconstructed.chain_breath == 88.0
    assert loop.breath == 88.0
    # The divergence emitted a state hook for operator visibility.
    assert "reconstruction_breath_divergence" in sh.kinds()


def test_no_eligible_market_routes_to_no_bet(tmp_path: Path) -> None:
    """tick_inputs returning None → NO_BET with structured reason."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    gamma = MockGammaAPI()
    loop, _, _, _, _, clock, writer, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
        tick_inputs=ScriptedTickInputs(force_no_market=True),
    )
    summary = _drive_n_ticks(loop, n=1, clock=clock)
    assert summary.bets_placed == 0
    assert summary.no_bets_emitted == 1
    decisions = list(iter_jsonl(writer.decisions_path))
    assert len(decisions) == 1
    assert decisions[0]["kind"] == "NO_BET"
    assert decisions[0]["no_bet_reason"] == "no_eligible_market"


def test_death_path_fires_at_zero_breath(tmp_path: Path) -> None:
    """BREATH == 0 → kill_and_mint_tombstone + alive=False; subsequent run is no-op."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    gamma = MockGammaAPI()
    # Chain breath starts at 0 — first tick should immediately die.
    loop, _, ca, sh, _, clock, _, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
        chain_adapter=FakeChainAdapter(current_breath=0.0),
        initial_breath=0.0,
    )
    summary = _drive_n_ticks(loop, n=1, clock=clock)
    assert summary.died is True
    assert summary.death_receipt is not None
    assert summary.death_receipt.tombstone_token_id == "42"
    assert ca.killed is True
    assert "agent_died" in sh.kinds()
    # Subsequent run is a no-op refusal.
    summary_2 = _run(loop.run(until=clock.now() + timedelta(hours=1)))
    assert summary_2.ticks_completed == 0
    assert summary_2.died is True


def test_settlement_phase_label_locks_to_phase_2_extended(tmp_path: Path) -> None:
    """The poller's sandbox_phase MUST equal PHASE_2_EXTENDED by construction."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    gamma = MockGammaAPI()
    loop, _, _, _, _, _, _, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
    )
    assert loop.poller.sandbox_phase == "PHASE_2_EXTENDED"
    assert loop.weight_updater_phase == WeightUpdaterPhase.PHASE_2_EXTENDED


def test_state_hook_kind_is_state_hook_filterable(tmp_path: Path) -> None:
    """State hooks the loop emits use kind= as the discriminator (v34 F8)."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    gamma = MockGammaAPI()
    loop, _, _, sh, _, clock, _, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
        chain_adapter=FakeChainAdapter(current_breath=0.0),
        initial_breath=0.0,
    )
    _drive_n_ticks(loop, n=1, clock=clock)
    # All hook events carry the `kind` field — that's the v34 F8 contract.
    for evt in sh.events:
        assert "kind" in evt and isinstance(evt["kind"], str)


def test_decision_record_breath_after_matches_chain(tmp_path: Path) -> None:
    """Every DecisionRecord's breath_after must equal the chain read value."""
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    gamma = MockGammaAPI()
    loop, _, ca, _, _, clock, writer, _ = _build_loop(
        state_dir=state_dir,
        memory_bank_root=mb_root,
        settlement_client=gamma,
    )
    _drive_n_ticks(loop, n=2, clock=clock)
    rows = list(iter_jsonl(writer.decisions_path))
    assert len(rows) == 2
    for r in rows:
        assert r["breath_after"] == ca.current_breath
