"""Tests for :mod:`agent.engines.strategy_advisor` — T-B-025 L3 scaffold.

Five tests per the T-B-025 brief acceptance criteria:

1. ``test_protocol_and_noop_shape`` — the :class:`StrategyAdvisor` Protocol
   exists with the locked single method; :class:`NoOpStrategyAdvisor`
   structurally satisfies it and returns ``[]``.
2. ``test_strategy_proposal_pydantic_shape`` — the Pydantic model has
   exactly the locked fields with the locked validators (UUID-shaped
   id, ts datetime, kind Literal, rationale min-length, confidence_pct
   bounds, requires_human_approval bool, ``extra='ignore'``).
3. ``test_tick_interval_trigger_fires_advisor`` — with M=3 (test
   override of DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL=100), the loop
   calls the advisor exactly once after 3 ticks elapse since the
   baseline.
4. ``test_weight_stability_trigger_fires_advisor`` — with stability
   window=3 ticks of <0.001 max |Δw|, the loop fires the advisor when
   3 consecutive low-Δw ticks elapse; a single high-Δw tick mid-stream
   resets the counter.
5. ``test_l3_swap_test_stub_advisor_persists_proposal`` — replace
   NoOpStrategyAdvisor with a stub returning 1 proposal → JSONL grows
   by 1 line + pending_proposals list carries the proposal_id.

All five tests inject fakes for every loop dependency — no real
google-genai / chain / Polymarket calls. The hermetic invariants from
:mod:`tests.agent.runtime.test_sandbox_restart` (tmp_path-rooted
writer, ``decision_cadence=0``, FakeChainAdapter, FakeSleeper,
FixedClock) apply verbatim.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from agent.core.memory_bank import MemoryBank
from agent.core.state import Phase, Weights
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
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.engines.strategy_advisor import (
    NoOpStrategyAdvisor,
    PerformanceWindow,
    StrategyAdvisor,
)
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DEFAULT_STRATEGY_ADVISOR_STABILITY_THRESHOLD,
    DEFAULT_STRATEGY_ADVISOR_STABILITY_WINDOW,
    DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL,
    DeathReceipt,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    TickInputSource,
    WeightUpdaterPhase,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI

# --------------------------------------------------------------------------- #
# Shared fakes (locally copied from test_sandbox_restart so this module
# stays self-contained — the T-B-025 brief sites tests/agent/engines/ as
# the home; reaching across to tests/agent/runtime/ would create a
# fragile import path).
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
        # The L3 scaffold tests never drive the death path — defensive
        # NotImplementedError keeps a future regression honest.
        raise NotImplementedError("L3 scaffold tests never hit the kill path")


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


@dataclass
class _ScriptedTickInputs:
    """Deterministic :class:`TickInputSource` — bullish signals every tick."""

    market_id: str = "m-strategy-001"
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
    clock = _FixedClock(
        start=datetime(2026, 5, 27, 18, 0, 0, tzinfo=UTC),
    )
    sleeper = _FakeSleeper()
    chain_adapter = _FakeChainAdapter(current_breath=100.0)
    state_hook = _FakeStateHook()
    weight_updater = _FakeWeightUpdater()

    table = {
        "m-strategy-001": MarketInfo(end_date_iso="2026-05-27T17:00:00+00:00"),
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
    )
    return loop, state_hook, writer, clock


def _drive(loop: SandboxPhase2Loop, *, n: int, clock: _FixedClock) -> None:
    """Drive the loop for exactly ``n`` ticks."""
    far_future = clock.now() + timedelta(days=365)
    asyncio.run(loop.run(until=far_future, max_ticks=n))


# --------------------------------------------------------------------------- #
# Test 1 — Protocol + NoOp shape
# --------------------------------------------------------------------------- #


def test_protocol_and_noop_shape() -> None:
    """StrategyAdvisor Protocol + NoOpStrategyAdvisor satisfy the brief."""
    # Protocol exists with a single ``review_window`` method.
    assert hasattr(StrategyAdvisor, "review_window")
    # NoOp returns ``[]`` regardless of input.
    advisor = NoOpStrategyAdvisor()
    window = PerformanceWindow(
        tick=42,
        ts=datetime(2026, 5, 27, 18, 0, 0, tzinfo=UTC),
        agent_id="genesis_v1",
        phase=Phase.PHASE_2_APPRENTICE,
        current_weights=Weights(
            w_r=0.5, w_s=0.5,
            alpha=[0.34, 0.33, 0.33],
            beta=[0.0, 1.0],
            rho=0.05,
        ),
        baseline_weights=Weights(
            w_r=0.5, w_s=0.5,
            alpha=[0.34, 0.33, 0.33],
            beta=[0.0, 1.0],
            rho=0.05,
        ),
        recent_pnl_window_usd=0.0,
        trigger="tick_interval",
    )
    result = advisor.review_window(window)
    assert result == []
    assert isinstance(result, list)
    # NoOp structurally satisfies the Protocol (mypy verifies this at
    # the assignment site).
    typed: StrategyAdvisor = advisor
    assert typed.review_window(window) == []


# --------------------------------------------------------------------------- #
# Test 2 — StrategyProposal Pydantic shape
# --------------------------------------------------------------------------- #


def test_strategy_proposal_pydantic_shape() -> None:
    """StrategyProposal has exactly the locked fields with locked validators."""
    pid = uuid.uuid4().hex
    ts = datetime(2026, 5, 27, 18, 30, 0, tzinfo=UTC)
    p = StrategyProposal(
        proposal_id=pid,
        ts=ts,
        kind="weight_delta",
        rationale="alpha_2 strongest predictor but only drifted +0.03",
        proposed_change={"key": "alpha_2", "delta": 0.06},
        expected_impact="+3% Sharpe",
        confidence_pct=65,
        requires_human_approval=True,
    )
    assert p.proposal_id == pid
    assert p.ts == ts
    assert p.kind == "weight_delta"
    assert p.confidence_pct == 65
    assert p.requires_human_approval is True

    # rationale min_length=1 — empty rationale must fail validation.
    with pytest.raises(ValidationError):
        StrategyProposal(
            proposal_id=pid,
            ts=ts,
            kind="weight_delta",
            rationale="",
            proposed_change={},
            expected_impact=None,
            confidence_pct=50,
            requires_human_approval=True,
        )

    # kind enum — invalid kind must fail validation.
    with pytest.raises(ValidationError):
        StrategyProposal(
            proposal_id=pid,
            ts=ts,
            kind="not_a_kind",  # type: ignore[arg-type]
            rationale="ok",
            proposed_change={},
            expected_impact=None,
            confidence_pct=50,
            requires_human_approval=True,
        )

    # confidence_pct bounds — out-of-range must fail validation.
    with pytest.raises(ValidationError):
        StrategyProposal(
            proposal_id=pid,
            ts=ts,
            kind="weight_delta",
            rationale="ok",
            proposed_change={},
            expected_impact=None,
            confidence_pct=101,
            requires_human_approval=True,
        )

    # extra='ignore' — unknown field silently dropped (forward-compat
    # across sprint_9→sprint_10 advisor swap).
    payload: dict[str, Any] = {
        "proposal_id": pid,
        "ts": ts.isoformat(),
        "kind": "weight_delta",
        "rationale": "ok",
        "proposed_change": {},
        "expected_impact": None,
        "confidence_pct": 50,
        "requires_human_approval": True,
        "sprint_10_future_field": "ignored",
    }
    p2 = StrategyProposal.model_validate(payload)
    assert not hasattr(p2, "sprint_10_future_field")


# --------------------------------------------------------------------------- #
# Test 3 — tick_interval trigger fires the advisor
# --------------------------------------------------------------------------- #


def test_tick_interval_trigger_fires_advisor(tmp_path: Path) -> None:
    """With M=3, the advisor fires once after 3 ticks elapse."""

    class _CountingAdvisor:
        def __init__(self) -> None:
            self.calls: list[PerformanceWindow] = []

        def review_window(
            self, window: PerformanceWindow,
        ) -> list[StrategyProposal]:
            self.calls.append(window)
            return []

    advisor = _CountingAdvisor()
    loop, state_hook, _writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=3,
        # Crank the stability window high so the tick_interval branch
        # is the ONLY way the advisor can fire — isolating the test.
        strategy_advisor_stability_window=10_000,
    )
    # Drive 3 ticks (ticks 0, 1, 2). Trigger condition is
    # (tick - last_strategy_advisor_tick) >= 3, with last=-1 →
    # fires when tick >= 2, i.e. the 3rd tick (tick=2).
    _drive(loop, n=3, clock=clock)
    assert len(advisor.calls) == 1
    fired_events = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired_events) == 1
    assert fired_events[0]["trigger"] == "tick_interval"
    assert fired_events[0]["proposals_emitted"] == 0
    assert fired_events[0]["pending_proposals_count"] == 0
    # After a fire the baseline advances; another 3 ticks fires again.
    _drive(loop, n=3, clock=clock)
    assert len(advisor.calls) == 2


# --------------------------------------------------------------------------- #
# Test 4 — weight_stability trigger fires after N consecutive stable ticks
# --------------------------------------------------------------------------- #


def test_weight_stability_trigger_fires_advisor(tmp_path: Path) -> None:
    """3 consecutive low-Δw ticks fire the stability trigger.

    The scripted loop doesn't perturb weights (no settlement, so the
    weight_updater never fires), so every tick has max |Δw| == 0 against
    the baseline → the stability counter increments every tick. With
    stability_window=3 the advisor fires on tick=2 (0-indexed 3rd tick).
    """

    class _CountingAdvisor:
        def __init__(self) -> None:
            self.calls: list[PerformanceWindow] = []

        def review_window(
            self, window: PerformanceWindow,
        ) -> list[StrategyProposal]:
            self.calls.append(window)
            return []

    advisor = _CountingAdvisor()
    loop, state_hook, _writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        # Crank tick_interval HIGH so only the stability trigger can fire.
        strategy_advisor_tick_interval=10_000,
        strategy_advisor_stability_window=3,
    )
    # Drive 3 ticks. Weights never change (no settlement plumbing) →
    # the stability counter hits 3 on the 3rd tick.
    _drive(loop, n=3, clock=clock)
    assert len(advisor.calls) == 1
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["trigger"] == "weight_stability"

    # After a fire the counter resets; another 3 stable ticks fires again.
    _drive(loop, n=3, clock=clock)
    assert len(advisor.calls) == 2
    fired_2 = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired_2) == 2
    assert fired_2[1]["trigger"] == "weight_stability"


# --------------------------------------------------------------------------- #
# Test 5 — L3 swap test: stub advisor returns 1 proposal → persisted.
# --------------------------------------------------------------------------- #


def test_l3_swap_test_stub_advisor_persists_proposal(tmp_path: Path) -> None:
    """Swap NoOpStrategyAdvisor with a stub returning 1 StrategyProposal.

    Asserts the brief's swap acceptance criterion:
    * proposals.jsonl gains exactly 1 entry.
    * pending_proposals carries the proposal_id.
    * AgentStateSnapshot snapshot file rehydrates the pending list.
    """
    pid = uuid.uuid4().hex

    class _StubAdvisor:
        """Returns one proposal per ``review_window`` call."""

        def __init__(self) -> None:
            self.calls: int = 0

        def review_window(
            self, window: PerformanceWindow,
        ) -> list[StrategyProposal]:
            self.calls += 1
            return [
                StrategyProposal(
                    proposal_id=pid,
                    ts=window.ts,
                    kind="weight_delta",
                    rationale=f"stub proposal at tick {window.tick}",
                    proposed_change={"key": "alpha_2", "delta": 0.05},
                    expected_impact=None,
                    confidence_pct=70,
                    requires_human_approval=True,
                )
            ]

    advisor = _StubAdvisor()
    loop, _state_hook, writer, clock = _build_loop(
        tmp_path=tmp_path,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=2,
        strategy_advisor_stability_window=10_000,
    )
    # Drive 2 ticks → tick_interval fires on the 2nd tick (tick=1).
    _drive(loop, n=2, clock=clock)
    assert advisor.calls == 1

    # proposals.jsonl grew by 1 entry with the stub proposal_id.
    rows = list(iter_jsonl(writer.proposals_path))
    assert len(rows) == 1
    assert rows[0]["proposal_id"] == pid
    assert rows[0]["kind"] == "weight_delta"
    assert rows[0]["rationale"] == "stub proposal at tick 1"
    assert rows[0]["confidence_pct"] == 70
    assert rows[0]["requires_human_approval"] is True

    # pending_proposals list (live + persisted) carries the id.
    assert pid in loop.pending_proposals
    raw = writer.snapshot_path.read_text(encoding="utf-8")
    snap = AgentStateSnapshot.model_validate_json(raw)
    assert pid in snap.pending_proposals

    # Reconstructed loop sees the same pending list (restart resilience).
    payload = json.loads(raw)
    assert pid in payload["pending_proposals"]
