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
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from agent.core.memory_bank import MemoryBank
from agent.core.state import Action, ActionKind, Phase, Side
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
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
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
    # V1.2 — optional execution-cost inputs a LIVE source would carry. Default
    # None ⇒ existing tests (which omit them) keep byte-identical open-bet rows.
    fill_price: float | None = None
    fee_bps: float | None = None
    half_spread_frac: float | None = None

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
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
            fill_price=self.fill_price,
            fee_bps=self.fee_bps,
            half_spread_frac=self.half_spread_frac,
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
    side_correct_pricing: bool = False,
    value_betting: bool = False,
    effective_entry_price_floor: float | None = None,
    decision_engine: Any | None = None,
    tick_inputs: Any | None = None,
    storm_enabled: bool = False,
    storm_tau: float = 0.05,
    storm_scale: float | None = None,
    chain_adapter: Any | None = None,
    divine_tithe: bool = False,
    tithe_every: int = 20,
    tithe_amount_usd: float = 20.0,
    tithe_breath_cost: float = 5.0,
    initial_breath: float = 100.0,
    initial_bankroll_usd: float = 100.0,
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
    if chain_adapter is None:
        chain_adapter = _FakeChainAdapter(current_breath=initial_breath)
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
        tick_inputs=tick_inputs if tick_inputs is not None else _ScriptedTickInputs(),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=sleeper,
        decision_cadence=timedelta(0),
        initial_breath=initial_breath,
        initial_bankroll_usd=initial_bankroll_usd,
        strategy_advisor=strategy_advisor,
        strategy_advisor_tick_interval=strategy_advisor_tick_interval,
        strategy_advisor_stability_window=strategy_advisor_stability_window,
        strategy_advisor_stability_threshold=strategy_advisor_stability_threshold,
        populate_reflection_window=populate_reflection_window,
        env=env,
        max_bet_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=side_correct_pricing,
        value_betting=value_betting,
        effective_entry_price_floor=effective_entry_price_floor,
        storm_enabled=storm_enabled,
        storm_tau=storm_tau,
        storm_scale=storm_scale,
        divine_tithe=divine_tithe,
        tithe_every=tithe_every,
        tithe_amount_usd=tithe_amount_usd,
        tithe_breath_cost=tithe_breath_cost,
        **({"decision_engine": decision_engine} if decision_engine is not None else {}),
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


def test_side_correct_pricing_threads_to_poller_and_defaults_false(
    tmp_path: Path,
) -> None:
    """Loop ctor ``side_correct_pricing`` lands on the poller; default stays
    False (live-runtime contract: locked legacy formulas byte-unchanged)."""
    loop_default, _, _, _ = _build_loop(tmp_path=tmp_path / "a")
    assert loop_default._poller.side_correct_pricing is False

    loop_corrected, _, _, _ = _build_loop(
        tmp_path=tmp_path / "b", side_correct_pricing=True
    )
    assert loop_corrected._poller.side_correct_pricing is True


class _RecordingDecisionEngine:
    """Duck-typed DecisionEngine spy: records decide() kwargs, returns a
    scripted Action."""

    def __init__(self, *, action: Action) -> None:
        self.calls: list[dict[str, Any]] = []
        self._action = action

    async def decide(self, **kwargs: Any) -> Action:
        self.calls.append(dict(kwargs))
        return self._action


def test_value_betting_passes_price_into_decide_iff_flag_on(
    tmp_path: Path,
) -> None:
    """``value_betting=True`` ⇒ decide() receives ``price=inputs.price``;
    default False ⇒ NO price kwarg (legacy decide signature, byte-unchanged)."""
    no_bet = Action(kind=ActionKind.NO_BET, no_bet_reason="scripted")

    eng_legacy = _RecordingDecisionEngine(action=no_bet)
    loop, _, _, clock = _build_loop(
        tmp_path=tmp_path / "legacy", decision_engine=eng_legacy
    )
    _drive(loop, n=1, clock=clock)
    assert len(eng_legacy.calls) == 1
    assert "price" not in eng_legacy.calls[0]

    eng_value = _RecordingDecisionEngine(action=no_bet)
    loop_v, _, _, clock_v = _build_loop(
        tmp_path=tmp_path / "value",
        decision_engine=eng_value,
        value_betting=True,
    )
    _drive(loop_v, n=1, clock=clock_v)
    assert len(eng_value.calls) == 1
    assert eng_value.calls[0]["price"] == pytest.approx(0.4)  # _ScriptedTickInputs


def test_value_betting_forwards_cross_market_signal_iff_flag_on(
    tmp_path: Path,
) -> None:
    """H3 seam (承重): ``value_betting=True`` ⇒ decide() receives
    ``cross_market_signal=inputs.cross_market_signal``; default False ⇒
    kwarg absent (legacy byte-unchanged).

    Pattern-matches ``test_value_betting_passes_price_into_decide_iff_flag_on``.
    Uses a bespoke tick source that injects ``cross_market_signal=0.77`` so the
    kwarg round-trip is distinguishable from the default 0.0.
    """
    no_bet = Action(kind=ActionKind.NO_BET, no_bet_reason="scripted")

    class _XmTickSource:
        """Deterministic tick source with non-zero cross_market_signal."""

        def inputs_for(
            self, *, asof_ts: datetime, tick: int
        ) -> TickInputs | None:
            iso = asof_ts.isoformat()
            signals: dict[str, Signal] = {
                TENNIS_TECHNICAL: Signal(
                    score=0.9, confidence=0.9, available_at=iso,
                    rationale="", raw_features={},
                ),
                MARKET_MOMENTUM: Signal(
                    score=0.8, confidence=0.9, available_at=iso,
                    rationale="", raw_features={},
                ),
                SURFACE_ADVANTAGE: Signal(
                    score=0.7, confidence=0.85, available_at=iso,
                    rationale="", raw_features={},
                ),
                HEAD_TO_HEAD: Signal(
                    score=0.6, confidence=0.8, available_at=iso,
                    rationale="", raw_features={},
                ),
                REST_RECENCY: Signal(
                    score=0.6, confidence=0.85, available_at=iso,
                    rationale="", raw_features={},
                ),
            }
            return TickInputs(
                market_id="m-l3-001",
                signals=signals,
                price=0.4,
                liquidity_cap_usd=50.0,
                cross_market_signal=0.77,
            )

    # --- legacy mode: cross_market_signal must NOT reach decide() ---
    eng_legacy = _RecordingDecisionEngine(action=no_bet)
    loop_leg, _, _, clock_leg = _build_loop(
        tmp_path=tmp_path / "legacy",
        decision_engine=eng_legacy,
        tick_inputs=_XmTickSource(),
    )
    _drive(loop_leg, n=1, clock=clock_leg)
    assert len(eng_legacy.calls) == 1
    assert "cross_market_signal" not in eng_legacy.calls[0]

    # --- value mode: cross_market_signal must reach decide() ---
    eng_value = _RecordingDecisionEngine(action=no_bet)
    loop_val, _, _, clock_val = _build_loop(
        tmp_path=tmp_path / "value",
        decision_engine=eng_value,
        value_betting=True,
        tick_inputs=_XmTickSource(),
    )
    _drive(loop_val, n=1, clock=clock_val)
    assert len(eng_value.calls) == 1
    assert eng_value.calls[0]["cross_market_signal"] == pytest.approx(0.77)


def test_effective_floor_gates_legacy_mode_bet_before_place_order(
    tmp_path: Path,
) -> None:
    """r4 M-1: legacy mode (value_betting=False) + effective floor — a forced
    NO bet at yes-price 0.97 (effective 0.03 < 0.05) is converted to a NO_BET
    record and NO order is placed."""
    forced_no_bet = Action(
        kind=ActionKind.BET, market_id="m-l3-001", side=Side.NO,
        size_usd=5.0, edge_pct=0.5,
    )
    eng = _RecordingDecisionEngine(action=forced_no_bet)
    loop, _, writer, clock = _build_loop(
        tmp_path=tmp_path,
        decision_engine=eng,
        effective_entry_price_floor=0.05,
        tick_inputs=_ScriptedTickInputs(price=0.97),
    )
    _drive(loop, n=1, clock=clock)

    assert iter_jsonl(writer.open_bets_path) == []  # nothing placed
    decisions = iter_jsonl(writer.decisions_path)
    assert len(decisions) == 1
    assert decisions[0]["kind"] == "NO_BET"
    reason = decisions[0]["no_bet_reason"]
    assert isinstance(reason, str)
    assert reason.startswith("effective_price_below_floor")


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
    assert scores[SURFACE_ADVANTAGE] == pytest.approx(0.7)
    assert scores[HEAD_TO_HEAD] == pytest.approx(0.6)
    assert scores[REST_RECENCY] == pytest.approx(0.6)


def test_execution_cost_stamps_threaded_onto_open_bet(tmp_path: Path) -> None:
    """V1.2: when the LIVE tick carries execution-cost inputs, the loop threads
    them through ``place_order`` onto the open ``BetRecord`` (so the LIVE
    cost-NET settlement + the fail-closed cost guard see them). The dollar
    ``spread_paid_usd`` is DERIVED from the size-independent half-spread fraction
    × the Kelly-sized stake (the live source cannot know the stake)."""
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor_tick_interval=10_000,
        strategy_advisor_stability_window=10_000,
        tick_inputs=_ScriptedTickInputs(
            fill_price=0.42, fee_bps=200.0, half_spread_frac=0.01,
            liquidity_cap_usd=20.0,
        ),
    )
    _drive(loop, n=1, clock=clock)

    rows = iter_jsonl(writer.open_bets_path)
    assert len(rows) == 1, f"expected one open bet; got {len(rows)}"
    row = rows[0]
    assert row["fill_price"] == pytest.approx(0.42)
    assert row["fee_bps"] == pytest.approx(200.0)
    assert row["liquidity_cap_usd"] == pytest.approx(20.0)
    # spread_paid_usd = half_spread_frac (0.01) × the actual staked size.
    assert row["spread_paid_usd"] == pytest.approx(0.01 * row["size_usd"])
    assert row["spread_paid_usd"] > 0.0


def test_no_cost_stamps_keeps_open_bet_row_byte_identical(tmp_path: Path) -> None:
    """The default (stamp-less) LIVE/replay tick leaves the open-bet row free of
    cost keys — the omit-when-None discipline keeps pre-V1.2 rows byte-identical."""
    loop, _hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor_tick_interval=10_000,
        strategy_advisor_stability_window=10_000,
    )
    _drive(loop, n=1, clock=clock)

    rows = iter_jsonl(writer.open_bets_path)
    assert len(rows) == 1
    for k in ("fill_price", "fee_bps", "spread_paid_usd", "liquidity_cap_usd"):
        assert k not in rows[0]


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


# --------------------------------------------------------------------------- #
# A9 storm percept (plan 2026-06-13) — loop-side wire, dynamics, stamps,
# restart safety.
# --------------------------------------------------------------------------- #


def test_storm_requires_value_betting(tmp_path: Path) -> None:
    """r9 M-4: legacy mode has no min-edge gate — the ctor rejects
    storm without value betting."""
    with pytest.raises(RuntimeError, match="value_betting"):
        _build_loop(tmp_path=tmp_path, storm_enabled=True)


def test_storm_ctor_validates_tau_and_scale(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="storm_tau"):
        _build_loop(
            tmp_path=tmp_path / "a", storm_enabled=True,
            value_betting=True, storm_tau=0.0,
        )
    with pytest.raises(RuntimeError, match="storm_tau"):
        _build_loop(
            tmp_path=tmp_path / "b", storm_enabled=True,
            value_betting=True, storm_tau=float("nan"),
        )
    with pytest.raises(RuntimeError, match="storm_scale"):
        _build_loop(
            tmp_path=tmp_path / "c", storm_enabled=True,
            value_betting=True, storm_scale=0.0,
        )


def test_storm_kwarg_passed_into_decide_iff_enabled(tmp_path: Path) -> None:
    """storm_enabled=True ⇒ decide() receives ``storm=``; default off ⇒
    NO storm kwarg (legacy decide signature, byte-unchanged)."""
    no_bet = Action(kind=ActionKind.NO_BET, no_bet_reason="scripted")

    eng_off = _RecordingDecisionEngine(action=no_bet)
    loop, _, _, clock = _build_loop(
        tmp_path=tmp_path / "off", decision_engine=eng_off,
        value_betting=True,
    )
    _drive(loop, n=1, clock=clock)
    assert "storm" not in eng_off.calls[0]

    eng_on = _RecordingDecisionEngine(action=no_bet)
    loop_on, _, _, clock_on = _build_loop(
        tmp_path=tmp_path / "on", decision_engine=eng_on,
        value_betting=True, storm_enabled=True,
    )
    _drive(loop_on, n=1, clock=clock_on)
    assert eng_on.calls[0]["storm"] == pytest.approx(0.0)


def test_storm_rises_on_loss_and_decays_by_wall_clock(tmp_path: Path) -> None:
    """Unit on the ONE update site: a breath loss blends in (τ=0.05);
    a 48 h calendar gap then halves the state — by the PUBLISHED
    half-life, not one blend step (r7 H-1 / r8 H-1)."""
    loop, _, _, _ = _build_loop(
        tmp_path=tmp_path, value_betting=True,
        storm_enabled=True, storm_scale=1.0,
    )
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    loop._breath = 35.0
    loop._update_storm(t0)  # first tick — baseline only
    assert loop._storm == 0.0
    loop._breath = 30.0  # −5 breath loss
    loop._update_storm(t0 + timedelta(hours=1))
    assert loop._storm == pytest.approx(0.25)  # τ·5 / scale 1.0
    # 48 h of quiet wall-clock time ⇒ exactly one half-life.
    loop._update_storm(t0 + timedelta(hours=49))
    assert loop._storm == pytest.approx(0.125)


def test_storm_same_gap_different_stop_density_identical(
    tmp_path: Path,
) -> None:
    """r9 H-1: zero-delta ticks decay by wall clock ALONE — the same
    calendar gap under different no-op stop density yields IDENTICAL
    storm (the (1−τ) blend factor must never run on no-event ticks)."""
    t0 = datetime(2026, 6, 1, tzinfo=UTC)

    def _storm_after(gap_stops: list[float]) -> float:
        loop, _, _, _ = _build_loop(
            tmp_path=tmp_path / f"d{len(gap_stops)}", value_betting=True,
            storm_enabled=True, storm_scale=1.0,
        )
        loop._breath = 35.0
        loop._update_storm(t0)
        loop._breath = 30.0
        loop._update_storm(t0 + timedelta(hours=1))
        for h in gap_stops:
            loop._update_storm(t0 + timedelta(hours=1 + h))
        return loop._storm

    sparse = _storm_after([48.0])
    dense = _storm_after([6.0, 12.0, 24.0, 36.0, 48.0])
    assert sparse == pytest.approx(dense)
    assert sparse == pytest.approx(0.125)


def test_storm_grant_baseline_reset_then_loss_rises(tmp_path: Path) -> None:
    """r6 M-2: a tribute grant resets the EMA baseline (the +33 never
    enters) but the NEXT real loss DOES raise storm."""
    loop, _, _, _ = _build_loop(
        tmp_path=tmp_path, value_betting=True,
        storm_enabled=True, storm_scale=1.0,
    )
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    loop._breath = 5.0
    loop._update_storm(t0)
    # The grant: breath 5 → 35; _attempt_tribute resets the baseline.
    loop._breath = 35.0
    loop._last_refreshed_breath = 35.0
    before = loop._storm
    loop._update_storm(t0 + timedelta(minutes=1))
    # The +30 jump never entered the EMA (delta computed off the reset
    # baseline is 0) — storm did not move except wall-clock decay.
    assert loop._storm <= before + 1e-12
    # An immediately following REAL loss raises storm.
    loop._breath = 30.0
    loop._update_storm(t0 + timedelta(minutes=2))
    assert loop._storm > before


@dataclass
class _ScriptedBreathChain:
    """Chain adapter whose read_breath() returns a scripted sequence —
    breath physics decoupled from settlements entirely."""

    breaths: list[float] = field(default_factory=list)
    pnl_updates: list[float] = field(default_factory=list)
    _i: int = 0

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)

    async def read_breath(self) -> float:
        b = self.breaths[min(self._i, len(self.breaths) - 1)]
        self._i += 1
        return b

    async def kill_and_mint_tombstone(self, **kwargs: Any) -> DeathReceipt:
        raise NotImplementedError


def test_storm_follows_breath_not_raw_pnl(tmp_path: Path) -> None:
    """r10 L-3: the EMA input is the refreshed BREATH delta — with ZERO
    settlements this tick (settlements_pnl_total == 0) a scripted breath
    drop must still raise storm."""
    no_bet = Action(kind=ActionKind.NO_BET, no_bet_reason="scripted")
    eng = _RecordingDecisionEngine(action=no_bet)
    # read_breath calls: reconstruct step 4, then one per tick.
    chain = _ScriptedBreathChain(breaths=[100.0, 100.0, 75.0])
    loop, _, _, clock = _build_loop(
        tmp_path=tmp_path, decision_engine=eng, value_betting=True,
        storm_enabled=True, storm_scale=1.0, chain_adapter=chain,
    )
    _drive(loop, n=2, clock=clock)
    # tick 1 set the baseline at 100; tick 2 saw 75 ⇒ delta −25 ⇒
    # state τ·25 = 1.25 ⇒ storm clamped/scaled = min(1, 1.25/1.0).
    assert loop._storm == pytest.approx(1.0)


def test_storm_stamps_written_on_bet(tmp_path: Path) -> None:
    """A BET under storm_enabled carries the five gate stamps onto the
    open_bets.jsonl row (the BetRecord → poller → SurvivalStep durable
    path starts here); flag-off rows never gain the keys."""
    loop, _, writer, clock = _build_loop(
        tmp_path=tmp_path, value_betting=True, storm_enabled=True,
    )
    _drive(loop, n=1, clock=clock)
    rows = [
        json.loads(line)
        for line in writer.open_bets_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    bets = [r for r in rows if r.get("status") == "open"]
    assert bets, "expected the bullish scripted tick to place a BET"
    row = bets[0]
    assert row["storm_at_bet"] == pytest.approx(0.0)
    assert row["gamma_at_bet"] == pytest.approx(0.0)
    assert row["min_edge_at_bet"] == pytest.approx(0.0)
    assert row["eff_min_edge_at_bet"] == pytest.approx(0.0)
    assert row["edge_at_bet"] > 0.0


def test_storm_restart_rejected_with_prior_state(tmp_path: Path) -> None:
    """r7 M-3 / r12 M-1: storm state is in-memory only — a storm loop
    must refuse to resume ANY non-empty durable stream, including the
    deleted-snapshot-with-JSONL-history case."""
    # Seed prior state with a storm run on a fresh dir (this is fine).
    loop_a, _, writer, clock = _build_loop(
        tmp_path=tmp_path, value_betting=True, storm_enabled=True,
    )
    _drive(loop_a, n=1, clock=clock)
    assert writer.decisions_path.exists()

    # A second storm loop on the SAME dir must refuse to start.
    loop_b, _, _, clock_b = _build_loop(
        tmp_path=tmp_path, value_betting=True, storm_enabled=True,
    )
    with pytest.raises(RuntimeError, match="not restart-safe"):
        _drive(loop_b, n=1, clock=clock_b)

    # r12 M-1: a missing snapshot is NOT "no prior state" — JSONL
    # history alone still rejects.
    if writer.snapshot_path.exists():
        writer.snapshot_path.unlink()
    loop_c, _, _, clock_c = _build_loop(
        tmp_path=tmp_path, value_betting=True, storm_enabled=True,
    )
    with pytest.raises(RuntimeError, match="not restart-safe"):
        _drive(loop_c, n=1, clock=clock_c)

    # Control: a NON-storm loop resumes the same dir without complaint.
    loop_d, _, _, clock_d = _build_loop(tmp_path=tmp_path)
    _drive(loop_d, n=1, clock=clock_d)


# --------------------------------------------------------------------------- #
# A10 divine tithe (plan 2026-06-13) — periodic rent: $X every N markets, or
# Y breath when broke; default off = byte-identical.
# --------------------------------------------------------------------------- #


def test_tithe_default_off_emits_no_event(tmp_path: Path) -> None:
    loop, hook, _, clock = _build_loop(tmp_path=tmp_path)
    _drive(loop, n=3, clock=clock)
    assert hook.by_kind("tithe") == []


def test_tithe_ctor_validation(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="tithe_every"):
        _build_loop(tmp_path=tmp_path / "a", divine_tithe=True, tithe_every=0)
    with pytest.raises(RuntimeError, match="tithe_amount_usd"):
        _build_loop(
            tmp_path=tmp_path / "b", divine_tithe=True, tithe_amount_usd=-1.0
        )
    with pytest.raises(RuntimeError, match="tithe_breath_cost"):
        _build_loop(
            tmp_path=tmp_path / "c",
            divine_tithe=True,
            tithe_breath_cost=float("nan"),
        )


def test_tithe_charges_cash_when_affordable(tmp_path: Path) -> None:
    """Every ``tithe_every`` markets the gods take cash from a solvent
    agent — breath untouched."""
    loop, hook, _, clock = _build_loop(
        tmp_path=tmp_path,
        divine_tithe=True,
        tithe_every=2,
        tithe_amount_usd=10.0,
        initial_bankroll_usd=500.0,
        initial_breath=100.0,
    )
    _drive(loop, n=4, clock=clock)
    events = hook.by_kind("tithe")
    # markets 2 and 4 → two tithes (every market is an eligible-market tick).
    assert len(events) == 2
    for e in events:
        assert e["amount_usd"] == 10.0
        assert e["breath_cost"] == 0.0
    # bankroll dropped by 2×$10; breath never touched by the tithe.
    assert loop._bankroll_usd <= 500.0 - 20.0 + 1e-9


def test_tithe_takes_breath_when_broke_and_drains_toward_death(
    tmp_path: Path,
) -> None:
    """A broke agent cannot pay cash → the gods take breath each market →
    breath drains monotonically toward 0 (the abstention-survival loophole:
    a do-nothing agent can no longer freeze its breath forever). The full
    death→advisor integration is covered at the reincarnation level with the
    real replay chain adapter; here we prove the drain mechanics short of the
    kill tick (this file's fake chain adapter has no tombstone mint)."""
    no_bet = Action(kind=ActionKind.NO_BET, no_bet_reason="scripted")
    eng = _RecordingDecisionEngine(action=no_bet)
    loop, hook, _, clock = _build_loop(
        tmp_path=tmp_path,
        decision_engine=eng,
        divine_tithe=True,
        tithe_every=1,
        tithe_amount_usd=1000.0,  # unaffordable → forces the breath path
        tithe_breath_cost=4.0,
        initial_bankroll_usd=10.0,
        initial_breath=20.0,
    )
    # breath 20, −4/market the agent never replenishes (it abstains):
    # market 1 → 16, market 2 → 12, market 3 → 8 (all still alive).
    _drive(loop, n=3, clock=clock)
    breath_events = [e for e in hook.by_kind("tithe") if e["breath_cost"] > 0]
    assert len(breath_events) == 3, "broke agent pays in breath every market"
    assert breath_events[0]["amount_usd"] == 0.0
    assert breath_events[0]["breath_cost"] == 4.0
    # Monotone drain toward 0 — no freezing.
    afters = [e["breath_after"] for e in breath_events]
    assert afters == sorted(afters, reverse=True)
    assert afters[-1] == pytest.approx(8.0)
    assert loop._breath == pytest.approx(8.0)
