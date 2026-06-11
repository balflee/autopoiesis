"""Tests for the T-B-030 L3 wire — :mod:`agent.runtime.sandbox_phase2_loop`.

Six tests per the T-B-030 brief acceptance criteria:

1. ``test_tick_interval_trigger_fires_at_tick_100`` — with the default
   ``strategy_advisor_tick_interval=100`` (overridden via constructor for
   test speed), the advisor fires exactly once on the 100th tick
   (``tick_count == M``).

2. ``test_weight_convergence_trigger_fires`` — with a default-sized ring
   buffer (capped at ``strategy_advisor_stability_window`` weights) the
   advisor fires when the buffer is full AND the cross-window max |Δw|
   is below the threshold.

3. ``test_no_double_trigger_same_tick`` — at the boundary tick that hits
   BOTH conditions simultaneously the advisor fires exactly ONCE; the
   tick_interval branch wins per the brief's "whichever first" lock.

4. ``test_restart_reconstructs_pending_proposal_ids_byte_for_byte`` —
   on restart the loop folds ``proposals.jsonl`` latest-status-wins
   into ``pending_proposal_ids``; ids whose latest status is
   ``"approved"`` / ``"rejected"`` drop out; insertion order preserved.

5. ``test_noop_strategy_advisor_back_compat`` — sprint_9
   :class:`NoOpStrategyAdvisor` injection still works; trigger fires,
   advisor returns ``[]``, no rows on disk, no crash.

6. ``test_cost_guard_tripped_no_crash`` — :class:`StrategyAdvisorImpl`
   with an exhausted :class:`L3CostGuard` returns ``[]`` without
   calling the LLM; loop continues, no row appended, no crash.

Hermetic invariants
-------------------

* Every test uses a ``tmp_path``-rooted :class:`SandboxStateWriter` so
  the repo's real ``state/sandbox/`` is never touched.
* No real Gemini call: tests inject either a fake advisor (Protocol-
  conformant stub) OR a :class:`StrategyAdvisorImpl` whose ``_LLMClient``
  is a Protocol-conformant fake.
* No real chain / Polymarket call: fakes implement the loop's injected
  Protocols. Identical posture to
  :mod:`tests.agent.engines.test_strategy_advisor_scaffold`.
* ``decision_cadence=timedelta(0)`` so the loop's inter-tick sleep is
  a no-op.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from agent.core.memory_bank import MemoryBank
from agent.core.state import Phase
from agent.data.polymarket_sandbox_executor import (
    MarketInfo,
    SandboxExecutor,
)
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    AgentStateSnapshot,
    SandboxStateWriter,
    SettledBetRecord,
    iter_jsonl,
)
from agent.engines._strategy_proposal_schema import (
    PROPOSAL_STATUS_APPROVED,
    PROPOSAL_STATUS_PENDING,
    PROPOSAL_STATUS_REJECTED,
    StrategyProposal,
)
from agent.engines.base import Signal
from agent.engines.decision import (
    CROWD_VOLUME,
    MARKET_MOMENTUM,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
)
from agent.engines.reflection import SandboxReflectionRecord
from agent.engines.strategy_advisor import (
    NoOpStrategyAdvisor,
    PerformanceWindow,
    StrategyAdvisor,
)
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.llm.cost_guard import L3CostGuard
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DEFAULT_STRATEGY_ADVISOR_STABILITY_THRESHOLD,
    DEFAULT_STRATEGY_ADVISOR_STABILITY_WINDOW,
    DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL,
    DeathReceipt,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    WeightUpdaterPhase,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI

# --------------------------------------------------------------------------- #
# Shared fakes — identical posture to the sprint_9 scaffold tests.
# --------------------------------------------------------------------------- #


class _FakeWeightUpdater:
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
class _FakeChainAdapter:
    """:class:`SandboxLoopChainAdapter` fake — read-breath only matters here."""

    current_breath: float = 100.0
    pnl_updates: list[float] = field(default_factory=list)

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)
        self.current_breath += pnl_usd

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
        raise NotImplementedError("L3 wire tests never hit the kill path")


class _FakeStateHook:
    """Records every emitted state hook (for trigger fire assertions)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == kind]


class _FakeSleeper:
    async def __call__(self, seconds: float) -> None:
        del seconds


class _FixedClock:
    def __init__(self, *, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now


@dataclass
class _ScriptedTickInputs:
    """Deterministic :class:`TickInputSource` — bullish signals every tick."""

    market_id: str = "m-l3-001"
    price: float = 0.4
    liquidity_cap_usd: float = 50.0

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        iso = asof_ts.isoformat()
        signals: dict[str, Signal] = {
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
            SMART_MONEY: Signal(
                score=0.7, confidence=0.85, available_at=iso,
                rationale="wallets favour YES",
                raw_features={"tick": float(tick)},
            ),
            SENTIMENT_LLM: Signal(
                score=0.6, confidence=0.8, available_at=iso,
                rationale="sentiment positive",
                raw_features={"tick": float(tick)},
            ),
            CROWD_VOLUME: Signal(
                score=0.6, confidence=0.85, available_at=iso,
                rationale="crowd volume rising sharply",
                raw_features={"tick": float(tick)},
            ),
        }
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
        )


class _NoopPhaseReader:
    def read_phase(self) -> Phase:  # pragma: no cover
        return Phase.PHASE_2_APPRENTICE


class _NoopDecisionLog:
    def append(  # pragma: no cover
        self,
        *,
        market_id: str,
        action: Any,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        return "0x_unused"


@dataclass
class _FakeLLM:
    """Protocol-conformant ``_LLMClient`` for the cost-guard test.

    Records every call so the test can assert the LLM was NOT invoked
    when the cost guard tripped pre-check. Mirrors
    :class:`tests.agent.llm.conftest.FakeGeminiClient` shape minus the
    autouse fixture (this module is in ``tests/agent/runtime/`` so the
    LLM-package conftest fixture does not apply).
    """

    calls: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt})
        if not self.responses:
            raise AssertionError(
                "FakeLLM exhausted — test wired fewer responses than calls. "
                "If the test expected NO LLM call, the cost guard precheck "
                "is not firing as intended."
            )
        return self.responses.pop(0)


def _build_loop(
    *,
    tmp_path: Path,
    strategy_advisor: StrategyAdvisor | None = None,
    strategy_advisor_tick_interval: int = (
        DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL
    ),
    strategy_advisor_stability_window: int = (
        DEFAULT_STRATEGY_ADVISOR_STABILITY_WINDOW
    ),
    strategy_advisor_stability_threshold: float = (
        DEFAULT_STRATEGY_ADVISOR_STABILITY_THRESHOLD
    ),
    populate_reflection_window: bool | None = None,
    env: dict[str, str] | None = None,
    max_bet_pnl_usd: float | None = None,
) -> tuple[
    SandboxPhase2Loop,
    _FakeStateHook,
    SandboxStateWriter,
    _FixedClock,
]:
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    mb_root.mkdir(parents=True, exist_ok=True)

    writer = SandboxStateWriter(root=state_dir)
    clock = _FixedClock(start=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC))
    sleeper = _FakeSleeper()
    chain_adapter = _FakeChainAdapter(current_breath=100.0)
    state_hook = _FakeStateHook()
    weight_updater = _FakeWeightUpdater()

    table = {
        "m-l3-001": MarketInfo(end_date_iso="2026-05-28T11:00:00+00:00"),
    }
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: table.get(mid),
        clock=clock,
    )
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,
    )

    gamma = MockGammaAPI()
    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=gamma,
        weight_updater=weight_updater,
        chain_adapter=cast(SandboxLoopChainAdapter, chain_adapter),
        tick_inputs=_ScriptedTickInputs(),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=sleeper,
        decision_cadence=timedelta(0),
        initial_breath=100.0,
        initial_bankroll_usd=100.0,
        strategy_advisor=strategy_advisor,
        strategy_advisor_tick_interval=strategy_advisor_tick_interval,
        strategy_advisor_stability_window=strategy_advisor_stability_window,
        strategy_advisor_stability_threshold=strategy_advisor_stability_threshold,
        populate_reflection_window=populate_reflection_window,
        env=env,
        max_bet_pnl_usd=max_bet_pnl_usd,
    )
    return loop, state_hook, writer, clock


def test_max_bet_pnl_usd_threads_to_poller_and_defaults_none(
    tmp_path: Path,
) -> None:
    """Loop ctor ``max_bet_pnl_usd`` lands on the poller; default stays None
    (live-runtime contract: locked formulas byte-unchanged)."""
    loop_default, _, _, _ = _build_loop(tmp_path=tmp_path / "a")
    assert loop_default._poller.max_bet_pnl_usd is None

    loop_capped, _, _, _ = _build_loop(
        tmp_path=tmp_path / "b", max_bet_pnl_usd=100.0
    )
    assert loop_capped._poller.max_bet_pnl_usd == 100.0


def _drive(loop: SandboxPhase2Loop, *, n: int, clock: _FixedClock) -> None:
    """Drive the loop for exactly ``n`` ticks."""
    far_future = clock.now() + timedelta(days=365)
    asyncio.run(loop.run(until=far_future, max_ticks=n))


class _CountingAdvisor:
    """Fake advisor that records every ``review_window`` call.

    Returns ``[]`` by default so the trigger fires + book-keeping
    advances without the JSONL stream growing. Tests that need a
    non-empty result subclass / override per-call.
    """

    def __init__(
        self,
        *,
        proposals_per_call: int = 0,
    ) -> None:
        self.calls: list[PerformanceWindow] = []
        self._proposals_per_call = proposals_per_call

    def review_window(
        self, window: PerformanceWindow,
    ) -> list[StrategyProposal]:
        self.calls.append(window)
        return [
            StrategyProposal(
                proposal_id=uuid.uuid4().hex,
                ts=window.ts,
                kind="weight_delta",
                rationale=f"fake proposal at tick {window.tick}",
                proposed_change={"key": "alpha_2", "delta": 0.04},
                expected_impact=None,
                confidence_pct=60,
                requires_human_approval=True,
            )
            for _ in range(self._proposals_per_call)
        ]


# --------------------------------------------------------------------------- #
# Test 1 — tick_interval trigger fires at tick_count == M (default 100).
# --------------------------------------------------------------------------- #


def test_tick_interval_trigger_fires_at_tick_100(tmp_path: Path) -> None:
    """``tick_count % 100 == 0`` fires exactly once over 100 ticks.

    We use the production default M=100 so the test exercises the
    actual brief number (not a compressed test override). Stability
    window cranked high so only the tick_interval branch can fire —
    isolating the test to one trigger source.
    """
    advisor = _CountingAdvisor()
    loop, state_hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=100,
        strategy_advisor_stability_window=10_000,
    )
    # Drive 100 ticks → tick_count goes 1..100; 100 % 100 == 0 → fires
    # exactly once on the 100th tick. Drive in ONE chunk so the
    # restart-reconstruct that runs on every fresh ``loop.run()`` call
    # does not reset the in-process trigger state midway.
    _drive(loop, n=100, clock=clock)
    assert len(advisor.calls) == 1, (
        "advisor must fire exactly once on the 100th tick "
        f"(got {len(advisor.calls)} call(s))"
    )
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["trigger"] == "tick_interval"
    assert fired[0]["proposals_emitted"] == 0

    # No rows on disk (NoOp-shaped advisor returns []).
    assert len(iter_jsonl(writer.proposals_path)) == 0


# --------------------------------------------------------------------------- #
# Test 2 — weight_stability trigger fires on full-buffer convergence.
# --------------------------------------------------------------------------- #


def test_weight_convergence_trigger_fires(tmp_path: Path) -> None:
    """Ring-buffer full + cross-window max |Δw| below threshold fires.

    Default stability window = 20; the loop's scripted tick inputs do
    not perturb weights (no settlement plumbing), so every buffered
    weight is byte-equal → range == 0 < 1e-3 → fires on the 20th tick.
    Tick interval cranked high so only the stability branch can fire.
    """
    advisor = _CountingAdvisor()
    loop, state_hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        # Crank tick_interval HIGH so only the stability trigger can fire.
        strategy_advisor_tick_interval=10_000,
        strategy_advisor_stability_window=20,
        strategy_advisor_stability_threshold=1e-3,
    )
    # Drive 20 ticks in ONE chunk so the restart-reconstruct (which
    # clears the in-process ring buffer) does not reset trigger state
    # midway. After tick 20: buffer is full + range == 0 → fires.
    _drive(loop, n=20, clock=clock)
    assert len(advisor.calls) == 1
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["trigger"] == "weight_stability"

    # Buffer cleared on fire — the next fire requires another 20 stable
    # ticks; no immediate re-trigger.
    assert loop.weight_ring_buffer_size == 0
    assert len(iter_jsonl(writer.proposals_path)) == 0


# --------------------------------------------------------------------------- #
# Test 3 — no double-trigger on a tick that meets BOTH conditions.
# --------------------------------------------------------------------------- #


def test_no_double_trigger_same_tick(tmp_path: Path) -> None:
    """A tick where both conditions are simultaneously met fires ONCE.

    Construct a loop with the SAME M and W so the boundary at
    ``tick_count == M == W`` simultaneously satisfies the tick_interval
    AND the weight_stability checks. The brief locks the resolution:
    tick_interval wins, fire exactly once.
    """
    advisor = _CountingAdvisor()
    loop, state_hook, _writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=20,
        strategy_advisor_stability_window=20,
        strategy_advisor_stability_threshold=1e-3,
    )
    # Drive 20 ticks. At tick_count=20:
    #   - tick_interval: 20 % 20 == 0 → satisfied.
    #   - stability:     buffer full (20) + range == 0 → satisfied.
    _drive(loop, n=20, clock=clock)

    assert len(advisor.calls) == 1, (
        "advisor must fire exactly ONCE despite both triggers being "
        f"simultaneously satisfied (got {len(advisor.calls)} call(s))"
    )
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    # Per the brief's "whichever first" lock, tick_interval wins the
    # boundary race so the operator can plan around the deterministic
    # cadence.
    assert fired[0]["trigger"] == "tick_interval"


# --------------------------------------------------------------------------- #
# Test 4 — restart reconstructs pending_proposal_ids byte-for-byte.
# --------------------------------------------------------------------------- #


def test_restart_reconstructs_pending_proposal_ids_byte_for_byte(
    tmp_path: Path,
) -> None:
    """Latest-status-wins fold of ``proposals.jsonl`` on restart.

    Seed the writer with 4 proposals: A (pending), B (pending), C
    (later updated to approved), D (later updated to rejected). On
    fresh-loop reconstruction the pending_proposal_ids list must be
    exactly ``[A, B]`` (insertion order preserved; C and D filtered).
    """
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    writer = SandboxStateWriter(root=state_dir)

    pid_a = "a" * 32
    pid_b = "b" * 32
    pid_c = "c" * 32
    pid_d = "d" * 32
    ts = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)

    def _proposal(
        pid: str,
        status: Literal["pending", "approved", "rejected"],
    ) -> StrategyProposal:
        return StrategyProposal(
            proposal_id=pid,
            ts=ts,
            kind="weight_delta",
            rationale=f"seed for {pid}",
            proposed_change={"key": "alpha_2", "delta": 0.03},
            expected_impact=None,
            confidence_pct=55,
            requires_human_approval=True,
            status=status,
        )

    # Insertion order: A B C D (pending), then C → approved, D → rejected.
    writer.append_proposal(_proposal(pid_a, PROPOSAL_STATUS_PENDING))
    writer.append_proposal(_proposal(pid_b, PROPOSAL_STATUS_PENDING))
    writer.append_proposal(_proposal(pid_c, PROPOSAL_STATUS_PENDING))
    writer.append_proposal(_proposal(pid_d, PROPOSAL_STATUS_PENDING))
    writer.append_proposal(_proposal(pid_c, PROPOSAL_STATUS_APPROVED))
    writer.append_proposal(_proposal(pid_d, PROPOSAL_STATUS_REJECTED))

    # Spin up a fresh loop against the same state_dir → reconstruct.
    loop, _hook, _writer2, _clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=_CountingAdvisor(),
    )
    # Trigger reconstruction (run with max_ticks=0 returns immediately
    # after _reconstruct_from_disk).
    asyncio.run(loop.run(max_ticks=0, until=datetime(2099, 1, 1, tzinfo=UTC)))

    # Byte-for-byte: A and B remain, in insertion order. C / D dropped
    # because their latest status is not "pending".
    assert loop.pending_proposal_ids == (pid_a, pid_b), (
        f"expected ({pid_a!r}, {pid_b!r}); got {loop.pending_proposal_ids}"
    )
    # Sprint_9 compatibility alias returns the same tuple.
    assert loop.pending_proposals == (pid_a, pid_b)


# --------------------------------------------------------------------------- #
# Test 5 — NoOpStrategyAdvisor injection still works (sprint_9 back-compat).
# --------------------------------------------------------------------------- #


def test_noop_strategy_advisor_back_compat(tmp_path: Path) -> None:
    """Sprint_9 :class:`NoOpStrategyAdvisor` injection is still supported.

    The trigger fires (tick_count % M == 0); the NoOp returns ``[]``;
    no JSONL row is appended; pending_proposal_ids stays empty; no
    exception escapes. Mirrors the sprint_9 swap-test contract — the
    T-B-030 default-bump must NOT break the explicit injection path.
    """
    loop, state_hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=NoOpStrategyAdvisor(),
        strategy_advisor_tick_interval=5,
        strategy_advisor_stability_window=10_000,
    )
    _drive(loop, n=5, clock=clock)

    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["trigger"] == "tick_interval"
    assert fired[0]["proposals_emitted"] == 0
    assert fired[0]["pending_proposals_count"] == 0
    # No JSONL rows; pending list empty.
    assert len(iter_jsonl(writer.proposals_path)) == 0
    assert loop.pending_proposal_ids == ()

    # Snapshot rehydration of pending_proposal_ids works (computed_field
    # mirrors pending_proposals on output).
    snap = AgentStateSnapshot.model_validate_json(
        writer.snapshot_path.read_text(encoding="utf-8"),
    )
    assert snap.pending_proposal_ids == []
    assert snap.pending_proposals == []  # sprint_9 alias still works


# --------------------------------------------------------------------------- #
# Test 6 — cost-guard tripped → no proposal appended but no crash.
# --------------------------------------------------------------------------- #


def test_cost_guard_tripped_no_crash(tmp_path: Path) -> None:
    """Exhausted L3 cost guard → impl short-circuits to ``[]``; loop OK.

    Construct :class:`StrategyAdvisorImpl` against a fake LLM (so no
    real network call is even possible) and an :class:`L3CostGuard`
    that's already at the cap. The impl's precheck fires BEFORE the
    LLM call (asserted via ``len(fake_llm.calls) == 0``), the loop
    sees ``review_window() == []``, no row lands on disk, and the
    tick completes without raising.
    """
    fake_llm = _FakeLLM(responses=[])  # no responses; would assert on call
    guard = L3CostGuard(hard_cap_usd=0.01)
    guard.record(label="prime", usd=0.01)  # exhausts the cap
    assert guard.is_exhausted()

    impl = StrategyAdvisorImpl(
        llm_client=fake_llm,
        cost_guard=guard,
    )
    loop, state_hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=impl,
        strategy_advisor_tick_interval=3,
        strategy_advisor_stability_window=10_000,
    )
    # Drive 3 ticks → trigger fires; impl precheck returns [].
    _drive(loop, n=3, clock=clock)

    # The LLM must NOT have been called (precheck must fire first).
    assert len(fake_llm.calls) == 0, (
        "cost-guard exhausted → LLM call must be short-circuited "
        f"(got {len(fake_llm.calls)} call(s))"
    )
    # The trigger fired (state hook records the event).
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["trigger"] == "tick_interval"
    assert fired[0]["proposals_emitted"] == 0

    # No row on disk (impl returned []).
    assert len(iter_jsonl(writer.proposals_path)) == 0

    # No failure hook (the fail-soft branch is for raised exceptions,
    # which the cost-guard precheck never triggers — it returns [] cleanly).
    assert state_hook.by_kind("strategy_advisor_failed") == []

    # Cost guard total stays at the prime value (no successful call
    # recorded — short-circuit happened pre-record).
    assert guard.total_usd == pytest.approx(0.01)


# --------------------------------------------------------------------------- #
# Test 7 — decision-time signal_scores are threaded onto the open BetRecord.
# --------------------------------------------------------------------------- #


def test_signal_scores_threaded_onto_open_bet(tmp_path: Path) -> None:
    """When the loop BETs, the per-engine ``signal.score`` map is persisted
    on the open ``BetRecord`` (Task L3 — credit-assignment provenance)."""
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=_CountingAdvisor(),
        # Crank advisor triggers high so they don't interfere.
        strategy_advisor_tick_interval=10_000,
        strategy_advisor_stability_window=10_000,
    )
    # One tick → the scripted bullish inputs route to a BET on m-l3-001.
    _drive(loop, n=1, clock=clock)

    rows = iter_jsonl(writer.open_bets_path)
    assert len(rows) == 1, f"expected one open bet; got {len(rows)}"
    scores = rows[0]["signal_scores"]
    # Matches the _ScriptedTickInputs scores for every engine.
    assert scores[TENNIS_TECHNICAL] == pytest.approx(0.9)
    assert scores[MARKET_MOMENTUM] == pytest.approx(0.8)
    assert scores[SMART_MONEY] == pytest.approx(0.7)
    assert scores[SENTIMENT_LLM] == pytest.approx(0.6)
    assert scores[CROWD_VOLUME] == pytest.approx(0.6)


# --------------------------------------------------------------------------- #
# Test 8 (Phase B / B1) — reflection-informed advisor window.
#
# The advisor-window call site folds recent reflections.jsonl + the
# recent settled-bet PnL + the weight trajectory into the
# PerformanceWindow it hands to ``review_window`` — but ONLY behind the
# ``GENESIS_REAL_REFLECTION`` seam (default OFF). The flag-OFF path must
# leave the new history fields EMPTY (byte-unchanged advisor input), so
# the existing 143 Plan-2 tests + frozen-config smoke stay green.
# --------------------------------------------------------------------------- #


def _seed_reflection_and_settlement_streams(writer: SandboxStateWriter) -> None:
    """Pre-write reflections.jsonl + settled_bets.jsonl the fold reads.

    Three reflections (so ``recent_reflections`` has narratives + the
    ``weight_snapshot``s feed ``weight_trajectory``) and two settled
    bets (so ``recent_pnl`` has two PnL floats).
    """
    for idx, (narrative, w_r) in enumerate(
        [
            ("early run: testing the waters", 0.50),
            ("momentum engine is carrying me", 0.55),
            ("trimming sentiment weight after a loss streak", 0.60),
        ]
    ):
        writer.append_reflection(
            SandboxReflectionRecord(
                reflection_id=f"refl-{idx}",
                ts=f"2026-05-28T1{idx}:00:00+00:00",
                trigger="tick_interval",
                narrative=narrative,
                weight_snapshot={
                    "w_r": w_r,
                    "alpha_0": 1.0 / 3.0,
                    "alpha_1": 1.0 / 3.0,
                    "alpha_2": 1.0 / 3.0,
                    "beta_0": 1.0,
                    "rho": 0.05,
                },
                recent_pnl_window=0.0,
                llm_cost_usd=0.0,
            )
        )
    for idx, pnl in enumerate([2.5, -1.25]):
        writer.append_settled_bet(
            SettledBetRecord(
                bet_id=f"bet-{idx}",
                market_id="m-l3-001",
                settled_ts=f"2026-05-28T1{idx}:30:00+00:00",
                outcome="yes" if pnl >= 0 else "no",
                winning_price=0.6,
                pnl_usd=pnl,
            )
        )


def test_advisor_window_reflection_informed_flag_on(tmp_path: Path) -> None:
    """``GENESIS_REAL_REFLECTION`` on → window carries reflections + PnL + traj.

    The reflect→learn→optimize closure: at the advisor-window call site
    the loop folds recent reflections.jsonl, the recent settled-bet PnL,
    and the weight trajectory into the ``PerformanceWindow`` via the
    existing fold helpers. The ``_CountingAdvisor`` captures the window so
    we can assert the new fields are populated. Proposals still flow
    through the normal queue — the advisor is NOT called from
    ``_fire_reflection`` (no extra calls).
    """
    advisor = _CountingAdvisor()
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=3,
        strategy_advisor_stability_window=10_000,
        populate_reflection_window=True,
    )
    _seed_reflection_and_settlement_streams(writer)

    _drive(loop, n=3, clock=clock)

    assert len(advisor.calls) == 1, "advisor should fire exactly once"
    window = advisor.calls[0]
    # Recent reflection narratives folded in, oldest-first.
    assert window.recent_reflections == [
        "early run: testing the waters",
        "momentum engine is carrying me",
        "trimming sentiment weight after a loss streak",
    ]
    # Recent settled-bet PnL floats folded in, oldest-first.
    assert window.recent_pnl == pytest.approx([2.5, -1.25])
    # Weight trajectory projected from the reflection weight snapshots.
    assert len(window.weight_trajectory) == 3
    assert [round(w.w_r, 2) for w in window.weight_trajectory] == [0.50, 0.55, 0.60]


def test_advisor_window_flag_off_leaves_history_empty(tmp_path: Path) -> None:
    """Flag OFF (default) → the window's history fields stay EMPTY.

    Byte-unchanged advisor input vs the pre-B1 behaviour even when the
    JSONL streams are present on disk. This is the guarantee that keeps
    the existing Plan-2 tests + frozen-config smoke green.
    """
    advisor = _CountingAdvisor()
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=3,
        strategy_advisor_stability_window=10_000,
        # populate_reflection_window defaults to None → resolves OFF
        # (no env var set in this hermetic loop).
        env={},
    )
    _seed_reflection_and_settlement_streams(writer)

    _drive(loop, n=3, clock=clock)

    assert len(advisor.calls) == 1
    window = advisor.calls[0]
    assert window.recent_reflections == []
    assert window.recent_pnl == []
    assert window.weight_trajectory == []


def test_advisor_window_env_flag_drives_population(tmp_path: Path) -> None:
    """``GENESIS_REAL_REFLECTION=1`` via env (no explicit ctor arg) → ON.

    The env seam mirrors the established ``GENESIS_REAL_*`` flag pattern
    (exact ``"1"`` flips it). The injected ``env`` keeps the test
    hermetic — no real ``os.environ`` mutation.
    """
    advisor = _CountingAdvisor()
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=3,
        strategy_advisor_stability_window=10_000,
        env={"GENESIS_REAL_REFLECTION": "1"},
    )
    _seed_reflection_and_settlement_streams(writer)

    _drive(loop, n=3, clock=clock)

    assert len(advisor.calls) == 1
    window = advisor.calls[0]
    assert len(window.recent_reflections) == 3
    assert window.recent_pnl == pytest.approx([2.5, -1.25])


def test_advisor_window_env_flag_non_one_stays_off(tmp_path: Path) -> None:
    """Only the exact env value ``"1"`` flips the seam — ``"0"`` is OFF."""
    advisor = _CountingAdvisor()
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=3,
        strategy_advisor_stability_window=10_000,
        env={"GENESIS_REAL_REFLECTION": "0"},
    )
    _seed_reflection_and_settlement_streams(writer)

    _drive(loop, n=3, clock=clock)

    assert len(advisor.calls) == 1
    window = advisor.calls[0]
    assert window.recent_reflections == []
    assert window.recent_pnl == []
    assert window.weight_trajectory == []
