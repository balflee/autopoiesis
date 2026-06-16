# tests/agent/backtest/test_survival_reflection_journey.py
"""Phase B / B3 — reflection-driven proposal events on the Page-2 timeline.

B1 folded recent reflections into the advisor window; B2 wired the real
``ReflectionEngine`` + ``StrategyAdvisorImpl`` together behind the L6 flags. B3
proves the closure end-to-end at the two seams the demo cares about:

1. **Journey annotation (survival recorder).** The A3 :class:`SurvivalRecorder`
   now carries an OPTIONAL ``reflection`` slot per settled step so the Page-2
   timeline can surface "the agent reflected, then proposed an optimisation".
   The annotation is captured off the loop's ``reflection_emitted`` /
   ``strategy_advisor_fired`` state-hook stream. With the L6 closure OFF (the
   survival season's default — ``NoOpStrategyAdvisor`` + no reflection engine)
   NOTHING changes: every step's ``reflection`` is ``None`` and ``_step_to_dict``
   omits the key, so the exported journey is byte-identical to the pre-B3 shape.

2. **Reflect -> advisor -> approval queue (loop).** With a FAKE ``_LLMClient``
   (never live Gemini) + the ``GENESIS_REAL_REFLECTION`` seam ON, a reflection
   folds into the advisor window AND the real ``StrategyAdvisorImpl`` produces a
   proposal that is ROUTED THROUGH THE L1 APPROVAL QUEUE (persisted ``pending`` +
   tracked in ``pending_proposal_ids``) — NOT auto-applied (the loop's weights do
   not move; only an operator approval drains a delta).

All TDD with injected fakes. The 143 Plan-2 tests + frozen-config smoke stay
green because the whole closure is behind the default-OFF flag.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.survival_season import (
    SurvivalRecorder,
    SurvivalRow,
    _step_to_dict,
    build_survival_journey,
    run_survival_season,
)
from agent.core.memory_bank import MemoryBank
from agent.core.state import Phase, Weights
from agent.data.polymarket_sandbox_executor import MarketInfo, SandboxExecutor
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    SandboxStateWriter,
    SettledBetRecord,
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
from agent.engines.reflection import SandboxReflectionRecord
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.llm.cost_guard import L3CostGuard
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DeathReceipt,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    WeightUpdaterPhase,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "surface_advantage",
    "head_to_head",
    "rest_recency",
)


# =========================================================================== #
# Part 1 — survival recorder / journey annotation.
# =========================================================================== #


def _bullish_weights() -> Weights:
    return Weights(
        w_r=0.5, w_s=0.5, alpha=[0.34, 0.33, 0.33], beta=[1.0, 0.0], rho=0.6
    )


def _fragile_seed() -> StrategyConfig:
    return StrategyConfig(
        weights=_bullish_weights(),
        max_breath_risk_pct=1.0,
        min_confidence=0.05,
        min_bet_size_usd=1.0,
    )


def _snap(
    market_id: str,
    *,
    entry_ts: str,
    end_date: str,
    resolution: str,
    entry_price: float = 0.50,
    outcome: Literal["yes", "no", "void"] = "no",
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        slug=f"atp-{market_id}",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _row(snap: MarketSnapshot, *, score: float = 0.8) -> SurvivalRow:
    entry_ts = snap.price_ledger[0].ts
    entry_price = snap.price_ledger[0].mid_price
    signal = SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: score for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=entry_price,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
    )
    return SurvivalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        signal=signal,
        entry_asof_ts_iso=entry_ts,
        resolution_ts_iso=snap.resolution_ts_iso,
        end_date_iso=snap.end_date_iso,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap=snap.liquidity_cap_usd,
        players=("alpha", "bravo"),
        surface="Hard",
    )


def _dying_fixture() -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    snaps = [
        _snap(
            "m1",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
        _snap(
            "m2",
            entry_ts="2025-06-05T00:00:00+00:00",
            end_date="2025-06-05T12:00:00+00:00",
            resolution="2025-06-05T20:00:00+00:00",
        ),
        _snap(
            "m3",
            entry_ts="2025-06-10T00:00:00+00:00",
            end_date="2025-06-10T12:00:00+00:00",
            resolution="2025-06-10T20:00:00+00:00",
        ),
    ]
    return [_row(s) for s in snaps], snaps


def _fake_settlement(market_id: str = "m1") -> SettlementResult:
    """A minimal ``SettlementResult`` the recorder reads ``market_id`` off."""
    ts = datetime(2025, 6, 1, 20, 0, 0, tzinfo=UTC)
    return SettlementResult(
        market_id=market_id,
        resolved=True,
        outcome="no",
        winning_price=1.0,
        resolution_ts=ts,
        end_date=ts,
    )


def _settlement_signals(
    *, pnl: float, size: float = 2.0, direction: float = 1.0
) -> dict[str, float]:
    """The flat-float feedback the poller hands the settlement updater."""
    sig: dict[str, float] = {
        "pnl_usd": pnl,
        "size_usd": size,
        "bet_direction": direction,
    }
    for slot in _SLOTS:
        sig[f"score_{slot}"] = 0.8
    return sig


# --------------------------------------------------------------------------- #
# (a) Flag OFF — the season default journey is byte-unchanged (no reflection).
# --------------------------------------------------------------------------- #


def test_flag_off_season_steps_have_no_reflection(tmp_path: Path) -> None:
    """The survival season (NoOp advisor + no reflection engine) → no annotation.

    Every recorded step's ``reflection`` is ``None`` and the exported step dict
    OMITS the ``reflection`` key entirely — the Page-2 journey is byte-identical
    to the pre-B3 shape when the L6 closure is off (the season default).
    """
    rows, snaps = _dying_fixture()
    seed = _fragile_seed()
    recorder = SurvivalRecorder(rows=rows)

    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "season",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
        recorder=recorder,
    )
    assert result.deaths >= 1
    assert recorder.steps, "fixture must settle at least one bet"

    # No L6 events fired → every step carries no reflection annotation.
    assert all(s.reflection is None for s in recorder.steps)

    journey = build_survival_journey(
        result=result, recorder=recorder, rows=rows, seed=seed, max_steps=500
    )
    for step_dict in journey["steps"]:
        assert "reflection" not in step_dict


# --------------------------------------------------------------------------- #
# Annotation — reflection + proposal events stamp the NEXT settled step.
# --------------------------------------------------------------------------- #


def test_reflection_then_proposal_annotates_next_step() -> None:
    """A reflection + a proposal-bearing advisor fire → next step is annotated.

    Drives the recorder's state hook directly with the loop's L6 event payloads
    (``reflection_emitted`` then ``strategy_advisor_fired`` with
    ``proposals_emitted > 0``), then settles a bet. The settled step carries the
    enriched annotation and ``_step_to_dict`` emits the ``reflection`` key.
    """
    rows, _snaps = _dying_fixture()
    recorder = SurvivalRecorder(rows=rows)
    hook = recorder.state_hook()

    # The loop emits these two in order when the L6 closure is live.
    hook.emit(
        kind="reflection_emitted",
        reflection_id="r-7",
        trigger="tick_interval",
        tick=3,
    )
    hook.emit(
        kind="strategy_advisor_fired",
        trigger="tick_interval",
        tick=3,
        proposals_emitted=1,
        pending_proposals_count=1,
    )

    # The next settlement consumes the pending annotation.
    recorder._on_settlement(
        signals=_settlement_signals(pnl=-2.0),
        outcome=_fake_settlement("m1"),
        weights_before=_bullish_weights(),
        weights_after=_bullish_weights(),
        breath_after=1.0,
        bankroll_after=98.0,
    )

    assert len(recorder.steps) == 1
    refl = recorder.steps[0].reflection
    assert refl is not None
    assert "reflected (tick_interval)" in refl
    assert "proposed 1 proposal (pending approval)" in refl

    step_dict = _step_to_dict(recorder.steps[0])
    assert step_dict["reflection"] == refl


def test_annotation_is_consumed_by_one_step_only() -> None:
    """A single reflection annotates exactly ONE step, not every subsequent one."""
    rows, _snaps = _dying_fixture()
    recorder = SurvivalRecorder(rows=rows)
    hook = recorder.state_hook()

    hook.emit(
        kind="reflection_emitted",
        reflection_id="r-1",
        trigger="weight_stability",
        tick=2,
    )
    # First settlement consumes the annotation.
    recorder._on_settlement(
        signals=_settlement_signals(pnl=1.0),
        outcome=_fake_settlement("m1"),
        weights_before=_bullish_weights(),
        weights_after=_bullish_weights(),
        breath_after=4.0,
        bankroll_after=101.0,
    )
    # Second settlement (no new reflection) is NOT annotated.
    recorder._on_settlement(
        signals=_settlement_signals(pnl=-2.0),
        outcome=_fake_settlement("m2"),
        weights_before=_bullish_weights(),
        weights_after=_bullish_weights(),
        breath_after=2.0,
        bankroll_after=99.0,
    )

    assert recorder.steps[0].reflection is not None
    assert recorder.steps[1].reflection is None


def test_advisor_fire_with_zero_proposals_does_not_annotate() -> None:
    """A NoOp advisor fire (proposals_emitted == 0) leaves the step un-annotated.

    The survival season's default ``NoOpStrategyAdvisor`` fires the trigger but
    emits zero proposals; that must NOT mark the timeline (no optimisation
    happened), so the default journey stays byte-unchanged.
    """
    rows, _snaps = _dying_fixture()
    recorder = SurvivalRecorder(rows=rows)
    hook = recorder.state_hook()

    hook.emit(
        kind="strategy_advisor_fired",
        trigger="tick_interval",
        tick=5,
        proposals_emitted=0,
        pending_proposals_count=0,
    )
    recorder._on_settlement(
        signals=_settlement_signals(pnl=-1.5),
        outcome=_fake_settlement("m1"),
        weights_before=_bullish_weights(),
        weights_after=_bullish_weights(),
        breath_after=1.0,
        bankroll_after=98.5,
    )

    assert recorder.steps[0].reflection is None


def test_reflection_only_annotation_without_proposal() -> None:
    """A reflection with NO following proposal still annotates (reflect, no opt)."""
    rows, _snaps = _dying_fixture()
    recorder = SurvivalRecorder(rows=rows)
    hook = recorder.state_hook()

    hook.emit(
        kind="reflection_emitted",
        reflection_id="",  # empty id → compact marker without the "#..." suffix
        trigger="tick_interval",
        tick=4,
    )
    recorder._on_settlement(
        signals=_settlement_signals(pnl=0.5),
        outcome=_fake_settlement("m1"),
        weights_before=_bullish_weights(),
        weights_after=_bullish_weights(),
        breath_after=3.5,
        bankroll_after=100.5,
    )

    refl = recorder.steps[0].reflection
    assert refl == "reflected (tick_interval)"


def test_unrelated_state_hook_kinds_are_ignored() -> None:
    """A non-L6 hook kind never sets a pending annotation (defensive)."""
    rows, _snaps = _dying_fixture()
    recorder = SurvivalRecorder(rows=rows)
    hook = recorder.state_hook()

    hook.emit(kind="strategy_advisor_failed", trigger="tick_interval", tick=1)
    hook.emit(kind="weight_delta_apply_failed", tick=1, error="boom")
    recorder._on_settlement(
        signals=_settlement_signals(pnl=-1.0),
        outcome=_fake_settlement("m1"),
        weights_before=_bullish_weights(),
        weights_after=_bullish_weights(),
        breath_after=2.0,
        bankroll_after=99.0,
    )
    assert recorder.steps[0].reflection is None


# =========================================================================== #
# Part 2 — reflect -> real advisor -> approval queue (loop, fake LLM).
# =========================================================================== #


@dataclass
class _FakeChainAdapter:
    """Loop chain adapter fake — read-breath only; never kills in this test."""

    current_breath: float = 100.0

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.current_breath += pnl_usd

    async def read_breath(self) -> float:
        return self.current_breath

    async def kill_and_mint_tombstone(self, **_kwargs: Any) -> DeathReceipt:
        raise NotImplementedError("never reached — breath stays positive")


class _SpyWeightUpdater:
    """Settlement updater that does nothing (the test never settles a bet)."""

    async def update(
        self, *, phase: str, signals: dict[str, float], outcome: SettlementResult
    ) -> None:  # pragma: no cover - never called
        del phase, signals, outcome


class _RecordingStateHook:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == kind]


class _FixedClock:
    def __init__(self, *, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now


class _FakeSleeper:
    async def __call__(self, seconds: float) -> None:
        del seconds


@dataclass
class _ScriptedTickInputs:
    """Bullish signals every tick (drives a BET so the loop keeps ticking)."""

    market_id: str = "m-b3-001"
    price: float = 0.4
    liquidity_cap_usd: float = 50.0

    def inputs_for(
        self, *, asof_ts: datetime, tick: int
    ) -> TickInputs | None:
        iso = asof_ts.isoformat()
        signals: dict[str, Signal] = {
            slot: Signal(
                score=0.8,
                confidence=0.9,
                available_at=iso,
                rationale=f"{slot} bullish",
                raw_features={"tick": float(tick)},
            )
            for slot in (
                TENNIS_TECHNICAL,
                MARKET_MOMENTUM,
                SURFACE_ADVANTAGE,
                HEAD_TO_HEAD,
                REST_RECENCY,
            )
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
class _FakeAdvisorLLM:
    """Protocol-conformant ``_LLMClient`` — returns ONE weight_delta proposal.

    NEVER a live Gemini call (``GEMINI_API_KEY`` is not set locally). Records the
    rendered prompt so the test can assert the folded reflection reached the LLM.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt})
        return {
            "proposals": [
                {
                    "kind": "weight_delta",
                    "rationale": (
                        "the reflection flagged a loss streak — trim alpha_2"
                    ),
                    "proposed_change": {"key": "alpha_2", "delta": 0.04},
                    "expected_impact": "reduce drawdown",
                    "confidence_pct": 60,
                }
            ]
        }


def _seed_reflection_stream(writer: SandboxStateWriter) -> None:
    writer.append_reflection(
        SandboxReflectionRecord(
            reflection_id="refl-b3",
            ts="2026-05-28T11:00:00+00:00",
            trigger="tick_interval",
            narrative="loss streak detected — leaning too hard on sentiment",
            weight_snapshot={
                "w_r": 0.5,
                "alpha_0": 1.0 / 3.0,
                "alpha_1": 1.0 / 3.0,
                "alpha_2": 1.0 / 3.0,
                "beta_0": 1.0,
                "rho": 0.05,
            },
            recent_pnl_window=-3.0,
            llm_cost_usd=0.0,
        )
    )
    writer.append_settled_bet(
        SettledBetRecord(
            bet_id="bet-b3",
            market_id="m-b3-001",
            settled_ts="2026-05-28T11:30:00+00:00",
            outcome="no",
            winning_price=0.6,
            pnl_usd=-2.5,
        )
    )


def _build_l6_loop(
    *, tmp_path: Path, advisor: StrategyAdvisorImpl, flag_on: bool
) -> tuple[SandboxPhase2Loop, _RecordingStateHook, SandboxStateWriter, _FixedClock]:
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    mb_root.mkdir(parents=True, exist_ok=True)

    writer = SandboxStateWriter(root=state_dir)
    clock = _FixedClock(start=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC))
    table = {"m-b3-001": MarketInfo(end_date_iso="2026-05-28T11:00:00+00:00")}
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
    state_hook = _RecordingStateHook()
    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=MockGammaAPI(),
        weight_updater=_SpyWeightUpdater(),
        chain_adapter=cast(SandboxLoopChainAdapter, _FakeChainAdapter()),
        tick_inputs=_ScriptedTickInputs(),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=_FakeSleeper(),
        decision_cadence=timedelta(0),
        initial_breath=100.0,
        initial_bankroll_usd=100.0,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=3,
        strategy_advisor_stability_window=10_000,
        populate_reflection_window=flag_on,
        env={"GENESIS_REAL_REFLECTION": "1"} if flag_on else {},
    )
    return loop, state_hook, writer, clock


def _drive(loop: SandboxPhase2Loop, *, n: int, clock: _FixedClock) -> None:
    asyncio.run(
        loop.run(until=clock.now() + timedelta(days=365), max_ticks=n)
    )


def test_reflection_folds_and_proposal_routes_through_queue(
    tmp_path: Path,
) -> None:
    """Flag ON + fake LLM → reflection folds in, proposal queued (NOT applied).

    The full L6 closure: the recent reflection folds into the advisor window
    (the rendered prompt carries the narrative), the real ``StrategyAdvisorImpl``
    produces a ``weight_delta`` proposal, and the loop ROUTES it through the L1
    approval queue — persisted ``pending`` in ``proposals.jsonl`` + tracked in
    ``pending_proposal_ids``. It is NOT auto-applied: the loop's weights are
    UNCHANGED (only an operator approval would drain a delta onto them).
    """
    fake_llm = _FakeAdvisorLLM()
    advisor = StrategyAdvisorImpl(
        llm_client=fake_llm,
        cost_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    loop, state_hook, writer, clock = _build_l6_loop(
        tmp_path=tmp_path, advisor=advisor, flag_on=True
    )
    _seed_reflection_stream(writer)
    weights_before = loop.weights

    _drive(loop, n=3, clock=clock)

    # The advisor fired exactly once (tick_interval=3) and the LLM was called.
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["proposals_emitted"] == 1
    assert len(fake_llm.calls) == 1

    # The folded reflection narrative reached the LLM prompt (reflect→optimize).
    assert (
        "loss streak detected — leaning too hard on sentiment"
        in fake_llm.calls[0]["prompt"]
    )

    # The proposal is persisted PENDING + tracked in the approval queue.
    proposal_rows = iter_jsonl(writer.proposals_path)
    assert len(proposal_rows) == 1
    assert proposal_rows[0]["status"] == "pending"
    assert proposal_rows[0]["kind"] == "weight_delta"
    assert len(loop.pending_proposal_ids) == 1
    assert loop.pending_proposal_ids[0] == proposal_rows[0]["proposal_id"]

    # NOT auto-applied: no approval has drained a delta, so the fusion weights
    # are byte-unchanged from the loop's start (the proposal awaits human/sim
    # approval through the queue, not the advisor's own emission).
    assert loop.weights == weights_before


def test_flag_off_loop_emits_no_proposal_and_no_llm_call(
    tmp_path: Path,
) -> None:
    """Flag OFF → the advisor window is NOT reflection-populated.

    Even with a real ``StrategyAdvisorImpl`` injected, the flag-OFF path leaves
    the window's reflection history EMPTY (B1 contract). The fake LLM still
    returns a proposal when called (the advisor always fires on the trigger), but
    the rendered prompt carries NO folded reflection — proving the default path
    is byte-unchanged from the pre-B1 advisor input. The proposal still routes
    through the queue (the L2/L3 path is unchanged), and weights stay put.
    """
    fake_llm = _FakeAdvisorLLM()
    advisor = StrategyAdvisorImpl(
        llm_client=fake_llm,
        cost_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    loop, _hook, writer, clock = _build_l6_loop(
        tmp_path=tmp_path, advisor=advisor, flag_on=False
    )
    _seed_reflection_stream(writer)
    weights_before = loop.weights

    _drive(loop, n=3, clock=clock)

    assert len(fake_llm.calls) == 1
    # Flag OFF → the seeded reflection narrative is NOT in the prompt.
    assert (
        "loss streak detected — leaning too hard on sentiment"
        not in fake_llm.calls[0]["prompt"]
    )
    # Weights are still not auto-applied.
    assert loop.weights == weights_before


def test_proposal_not_applied_until_approved(tmp_path: Path) -> None:
    """A queued proposal moves the weights ONLY after an operator approval.

    Proves the "routed through the approval queue, not auto-applied" contract
    from the other side: enqueue the proposal's weight delta on the loop's
    runtime-agent queue (what the FastAPI approve handler does) and drive one
    more tick — only NOW do the weights move.
    """
    fake_llm = _FakeAdvisorLLM()
    advisor = StrategyAdvisorImpl(
        llm_client=fake_llm,
        cost_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    loop, _hook, writer, clock = _build_l6_loop(
        tmp_path=tmp_path, advisor=advisor, flag_on=True
    )
    _seed_reflection_stream(writer)
    weights_before = loop.weights

    _drive(loop, n=3, clock=clock)
    assert loop.weights == weights_before  # still pending

    # The operator approves: the FastAPI handler enqueues the delta on the
    # runtime agent; the loop drains + applies it at the START of the next tick.
    loop.runtime_agent.apply_weight_delta({"key": "alpha_2", "delta": 0.04})
    _drive(loop, n=1, clock=clock)

    assert loop.weights != weights_before, (
        "the approved delta must move the weights once drained through the queue"
    )
