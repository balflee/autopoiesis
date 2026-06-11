# tests/agent/backtest/test_survival_tick_source_integration.py
"""A1 integration — the cached tick source + controllable clock drive the REAL
``SandboxPhase2Loop`` end-to-end on a tiny 2-market schedule.

This proves the A1 SEAMS against the actual loop (not a mock):

* a DECISION stop serves the cached signals → the loop decides + places a bet
  via the real ``SandboxExecutor`` (``expected_settle_ts = end_date + 2h``);
* advancing the clock forward to a later DECISION stop settles the earlier bet
  IN FLIGHT through the real poller + ``_ReplaySettlementClient`` (cached
  ``resolution_ts <= now`` AND ``expected_settle_ts < now`` STRICTLY);
* the FINAL-DRAIN settle-only stop is a NO-MARKET tick → the loop emits
  ``no_bet_reason="no_eligible_market"`` and the LAST bet settles.

It reuses the proven replay seams (``_ReplayChainAdapter`` /
``_ReplaySettlementClient`` / the backtest base orchestrator) so the test stays
hermetic — no Sackmann recompute, no live Gemini, no network. Mirrors the loop
construction in ``replay_runner.run_replay`` but swaps in the A1 schedule-driven
tick source + controllable clock (the A2 runner will do the same).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    MarketSnapshotProvider,
    PricePoint,
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
from agent.backtest.survival_season import (
    SurvivalRow,
    SurvivalTickSource,
    _ControllableClock,
    build_survival_schedule,
)
from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase, Weights
from agent.data.polymarket_sandbox_executor import SandboxExecutor
from agent.data.sandbox_state import SandboxStateWriter
from agent.engines.decision import DecisionEngine
from agent.engines.strategy_advisor import NoOpStrategyAdvisor
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    SandboxPhase2Loop,
    WeightUpdaterPhase,
)

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "smart_money",
    "sentiment_llm",
    "crowd_volume",
)


def _bullish_weights() -> Weights:
    return Weights(
        w_r=0.5, w_s=0.5, alpha=[0.34, 0.33, 0.33], beta=[1.0, 0.0], rho=0.6
    )


def _snap(
    market_id: str,
    *,
    entry_ts: str,
    end_date: str,
    resolution: str,
    entry_price: float,
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        slug=f"atp-{market_id}",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _survival_row(snap: MarketSnapshot, *, score: float) -> SurvivalRow:
    entry_ts = snap.price_ledger[0].ts
    entry_price = snap.price_ledger[0].mid_price
    signal = SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        # Strongly bullish, high-confidence cached signals so the seed config
        # actually BETS (the point of the integration test is a real bet that
        # settles in flight).
        scores={k: score for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=entry_price,
        outcome=snap.outcome or "yes",
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
        outcome=snap.outcome or "yes",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap=snap.liquidity_cap_usd,
        players=("alpha", "bravo"),
        surface="Hard",
    )


def _build_loop(
    *,
    snaps: list[MarketSnapshot],
    tick_source: SurvivalTickSource,
    clock: _ControllableClock,
    state_root: Path,
) -> tuple[SandboxPhase2Loop, _ReplayChainAdapter]:
    state_root.mkdir(parents=True, exist_ok=True)
    mb_root = state_root / "_mb"
    mb_root.mkdir(parents=True, exist_ok=True)
    provider = MarketSnapshotProvider(snaps)
    writer = SandboxStateWriter(root=state_root)
    market_table = _market_table_from_snapshots(snaps)
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: market_table.get(mid),
        clock=clock,
    )
    chain_adapter = _ReplayChainAdapter(current_breath=100.0)
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
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
        tick_inputs=tick_source,
        state_hook=_RecordingStateHook(),
        state_writer=writer,
        clock=clock,
        decision_cadence=timedelta(0),
        initial_breath=100.0,
        initial_bankroll_usd=100.0,
        initial_weights=_bullish_weights(),
        initial_phase=Phase.PHASE_2_APPRENTICE,
        # codex R5 — never the live Gemini advisor in a backtest.
        strategy_advisor=NoOpStrategyAdvisor(),
        # codex R8 — carry the seed's sizing/abstention knobs so the loop
        # doesn't silently fall back to DecisionEngine() defaults.
        decision_engine=DecisionEngine(
            max_breath_risk_pct=0.5,
            min_bet_size_usd=1.0,
            min_confidence=0.1,
        ),
    )
    return loop, chain_adapter


def test_schedule_drives_real_loop_bet_settles_in_flight(tmp_path: Path) -> None:
    # Two markets: mEarly enters 06-01, mLate enters 06-05. mEarly's bet settles
    # (end 06-01T12 +2h = 14:00; resolution 06-01T20) well before mLate's entry,
    # so it drains IN FLIGHT at mLate's decision tick. A final-drain no-market
    # tick settles mLate.
    snaps = [
        _snap(
            "mEarly",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
            entry_price=0.50,
        ),
        _snap(
            "mLate",
            entry_ts="2025-06-05T00:00:00+00:00",
            end_date="2025-06-05T12:00:00+00:00",
            resolution="2025-06-05T20:00:00+00:00",
            entry_price=0.50,
        ),
    ]
    rows = [
        _survival_row(snaps[0], score=0.8),
        _survival_row(snaps[1], score=0.8),
    ]
    schedule = build_survival_schedule(rows, settle_lag=timedelta(hours=2))
    tick_source = SurvivalTickSource(schedule)

    # Start the clock pinned to the first stop; within_tick_advance stays far
    # below the multi-day inter-stop gap so within-tick reads don't cross a stop.
    clock = _ControllableClock(
        schedule.stops[0].asof_ts, within_tick_advance=timedelta(seconds=1)
    )
    loop, chain = _build_loop(
        snaps=snaps, tick_source=tick_source, clock=clock, state_root=tmp_path / "s"
    )

    async def _drive() -> list[tuple[int, ActionKind, str | None]]:
        await loop._reconstruct_from_disk()
        seen: list[tuple[int, ActionKind, str | None]] = []
        for i, stop in enumerate(schedule.stops):
            clock.set_to(stop.asof_ts)
            result = await loop._tick()
            seen.append((i, result.action.kind, result.action.no_bet_reason))
        return seen

    seen = asyncio.run(_drive())

    # The schedule interleaves: mEarly entry (BET), a settle-only checkpoint
    # where mEarly drains in flight (NO_BET), mLate entry (BET), final drain
    # (NO_BET). Assert per-stop against the schedule's own market_ids so the
    # test is robust to checkpoint placement.
    kinds = {i: kind for i, kind, _ in seen}
    reasons = {i: reason for i, _, reason in seen}
    for i, stop in enumerate(schedule.stops):
        if stop.market_id is None:
            # Every no-market stop is a NO_BET via no_eligible_market.
            assert kinds[i] == ActionKind.NO_BET
            assert reasons[i] == "no_eligible_market"
        else:
            # Both market entries BET (bullish cached signals, seed sizing).
            assert kinds[i] == ActionKind.BET, stop.market_id

    # The trailing stop is the final-drain no-market settle tick.
    last = len(schedule.stops) - 1
    assert schedule.stops[last].market_id is None
    assert kinds[last] == ActionKind.NO_BET

    # Both bets settled (open set is empty after the final drain).
    assert loop._open_bet_ids == set()
    # YES leg wins at winning_price=1.0 from entry 0.50 -> realized PnL is
    # positive, so settlement-driven breath rose above the 100 start.
    assert chain.current_breath > 100.0


def test_settle_only_tick_does_not_place_an_order(tmp_path: Path) -> None:
    # A no-market settle tick must NOT place an order: inputs_for returns None,
    # so the loop takes the NO_BET branch and the executor is never called for it.
    snaps = [
        _snap(
            "mOnly",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
            entry_price=0.50,
        ),
    ]
    rows = [_survival_row(snaps[0], score=0.8)]
    schedule = build_survival_schedule(rows, settle_lag=timedelta(hours=2))
    tick_source = SurvivalTickSource(schedule)
    clock = _ControllableClock(
        schedule.stops[0].asof_ts, within_tick_advance=timedelta(seconds=1)
    )
    loop, chain = _build_loop(
        snaps=snaps, tick_source=tick_source, clock=clock, state_root=tmp_path / "s"
    )

    async def _drive() -> tuple[int, int]:
        await loop._reconstruct_from_disk()
        bets = 0
        no_bets = 0
        for stop in schedule.stops:
            clock.set_to(stop.asof_ts)
            result = await loop._tick()
            if result.action.kind == ActionKind.BET:
                bets += 1
            else:
                no_bets += 1
        return bets, no_bets

    bets, no_bets = asyncio.run(_drive())
    # Exactly one entry stop (a bet) + one final-drain settle tick (a no_bet).
    assert bets == 1
    assert no_bets == 1
    assert loop._open_bet_ids == set()
    assert chain.current_breath > 100.0  # the single bet won and settled


def test_tied_entry_markets_both_bet_and_settle_on_real_loop(tmp_path: Path) -> None:
    # Review-fix integration: two markets with the IDENTICAL entry instant drive
    # the REAL loop. The schedule nudges the second decision a hair forward (no
    # data loss), the controllable clock pins each 1s-apart stop FORWARD without
    # a set_to(past) crash (base/scratch decoupling), BOTH markets place a real
    # bet, and BOTH settle in flight + at the final drain. This is the tight-stop
    # case the original integration suite (all stops days apart) never exercised.
    snaps = [
        _snap(
            "mA",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
            entry_price=0.50,
        ),
        _snap(
            "mB",
            entry_ts="2025-06-01T00:00:00+00:00",  # TIED with mA
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
            entry_price=0.50,
        ),
    ]
    rows = [
        _survival_row(snaps[0], score=0.8),
        _survival_row(snaps[1], score=0.8),
    ]
    schedule = build_survival_schedule(rows, settle_lag=timedelta(hours=2))
    tick_source = SurvivalTickSource(schedule)
    clock = _ControllableClock(
        schedule.stops[0].asof_ts, within_tick_advance=timedelta(seconds=1)
    )
    loop, chain = _build_loop(
        snaps=snaps, tick_source=tick_source, clock=clock, state_root=tmp_path / "s"
    )

    # Both decision stops survive (no silent drop) and are 1s apart (nudged).
    decision_markets = [s.market_id for s in schedule.stops if s.market_id]
    assert sorted(decision_markets) == ["mA", "mB"]

    async def _drive() -> dict[str | None, ActionKind]:
        await loop._reconstruct_from_disk()
        kinds: dict[str | None, ActionKind] = {}
        for stop in schedule.stops:
            clock.set_to(stop.asof_ts)  # must NOT raise set_to(past)
            result = await loop._tick()
            kinds[stop.market_id] = result.action.kind
        return kinds

    kinds = asyncio.run(_drive())
    # Both tied markets placed a real bet on the actual SandboxExecutor.
    assert kinds["mA"] == ActionKind.BET
    assert kinds["mB"] == ActionKind.BET
    # Both bets drained; the YES legs won so settlement-breath rose.
    assert loop._open_bet_ids == set()
    assert chain.current_breath > 100.0
