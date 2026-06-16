# tests/agent/backtest/test_survival_recorder.py
"""A3 — survival recorder + journey schema + frozen static baseline + calibration.

The loop today emits only aggregate ``RunSummary`` / ``TickResult`` — no
per-settlement metadata, signal snapshot, or post-settlement weights. A3 adds an
explicit :class:`SurvivalRecorder` (a ``state_hook`` + a thin wrapper around the
settlement-learning ``_SettlementLearningWeightUpdater.update`` call) that captures
per SETTLED bet: market metadata (players / surface / price / outcome), side /
size, PnL, the decision-time signal scores, pre/post weights, running win-rate,
cumulative PnL, life id, and the death facts — and FILTERS the A1 synthetic
no-market NO_BETs (those never reach the settlement wrapper).

The journey is emitted as a ``survival_journey`` dict (down-sampled steps + a
summary + baseline overlays). The STATIC baseline is the SAME seed run over the
``SurvivalRow`` entry order with NO ``WeightUpdater`` (frozen weights) producing a
cumulative-PnL curve comparable to the learning run; random / always-favorite
archetypes ride along for context. A3b exposes a deliberately FRAGILE seed
selection + ``initial_breath`` + an optional loss-multiplier knob so deaths occur.

TDD on a TINY 3-market fixture engineered to DIE (mirrors test_survival_respawn).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.survival_season import (
    SurvivalRecorder,
    SurvivalRow,
    build_archetype_curve,
    build_static_baseline_curve,
    build_survival_journey,
    fragile_seed_from_config,
    run_survival_season,
)
from agent.core.state import Weights

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "surface_advantage",
    "head_to_head",
    "rest_recency",
)


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


def _row(
    snap: MarketSnapshot,
    *,
    score: float = 0.8,
    players: tuple[str, str] | None = ("alpha", "bravo"),
    surface: str | None = "Hard",
) -> SurvivalRow:
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
        players=players,
        surface=surface,
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
    rows = [_row(s) for s in snaps]
    return rows, snaps


# --------------------------------------------------------------------------- #
# Recorder — per-settlement capture + death facts + synthetic-NO_BET filter.
# --------------------------------------------------------------------------- #


def test_recorder_captures_per_settlement_metadata(tmp_path: Path) -> None:
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

    steps = recorder.steps
    # At least one settlement was recorded (each life bets + settles >=1).
    assert len(steps) >= 1
    # Every recorded step is a SETTLED bet — never a synthetic no-market NO_BET
    # (those never reach the settlement wrapper). Each carries the full payload.
    for st in steps:
        assert st.market_id in {"m1", "m2", "m3"}
        assert st.side in {"YES", "NO"}
        assert st.size_usd > 0.0
        # Market metadata joined from the SurvivalRow.
        assert st.players == ("alpha", "bravo")
        assert st.surface == "Hard"
        assert st.entry_price == 0.50
        assert st.outcome == "no"
        # Decision-time per-engine scores carried through (5 slots).
        assert set(st.signal_scores) == set(_SLOTS)
        # Pre/post fusion weights captured around the settlement update.
        assert isinstance(st.weights_before, Weights)
        assert isinstance(st.weights_after, Weights)
        # Running aggregates present.
        assert 0.0 <= st.running_win_rate <= 1.0
        assert st.life_idx >= 0

    # A YES bet on a "no" market is a full-stake LOSS -> negative pnl, post-weights
    # must have MOVED off the pre-weights (settlement learning fired on it).
    losing = [s for s in steps if s.pnl_usd < 0.0]
    assert losing, "expected at least one losing settlement on the dying fixture"
    assert any(s.weights_after != s.weights_before for s in losing)

    # cumulative PnL is the running sum of step pnls in record order.
    running = 0.0
    for st in steps:
        running += st.pnl_usd
        assert abs(st.cum_pnl - running) < 1e-9

    # Death facts captured for the life(s) that died.
    assert recorder.deaths, "a death must have been recorded via the state hook"
    d = recorder.deaths[0]
    assert d.kill_tx_hash
    assert d.life_idx >= 0


# --------------------------------------------------------------------------- #
# Frozen static baseline — NO WeightUpdater, comparable cumulative-PnL curve.
# --------------------------------------------------------------------------- #


def test_static_baseline_is_frozen_and_yields_cumulative_curve() -> None:
    rows, _snaps = _dying_fixture()
    seed = _fragile_seed()

    curve = build_static_baseline_curve(rows, seed)
    # One cumulative point per row decided (entry order preserved). The bullish
    # seed bets every fixture market (all "no" -> all full-stake losses), so the
    # curve is monotonically DECREASING and strictly comparable to the learner.
    assert len(curve) == len(rows)
    assert [p.market_id for p in curve] == ["m1", "m2", "m3"]
    # Each point carries the per-row pnl + the running cumulative.
    running = 0.0
    for p in curve:
        running += p.pnl_usd
        assert abs(p.cum_pnl - running) < 1e-9
    # All losses -> final cumulative is negative.
    assert curve[-1].cum_pnl < 0.0
    # FROZEN: the weights used to decide each row are IDENTICAL to the seed (no
    # WeightUpdater touched them) -> the per-row decision is the same as deciding
    # the seed independently.
    assert all(p.weights == seed.weights for p in curve)


def test_static_baseline_records_no_bet_rows() -> None:
    # A seed whose min_confidence is impossibly high NO_BETs everything -> the
    # frozen curve still has one point per row, all flat (0 pnl), no bets.
    rows, _snaps = _dying_fixture()
    abstaining = StrategyConfig(
        weights=_bullish_weights(),
        max_breath_risk_pct=1.0,
        min_confidence=0.99,  # above the fixture's 0.95 confidence -> NO_BET
        min_bet_size_usd=1.0,
    )
    curve = build_static_baseline_curve(rows, abstaining)
    assert len(curve) == len(rows)
    assert all(p.pnl_usd == 0.0 for p in curve)
    assert all(p.is_bet is False for p in curve)
    assert curve[-1].cum_pnl == 0.0


def test_archetype_curves_over_entry_order() -> None:
    rows, _snaps = _dying_fixture()
    # always_favorite bets the cheaper (more-favored) side every market in entry
    # order; random is seeded-deterministic. Both produce a comparable curve.
    fav = build_archetype_curve(rows, archetype="always_favorite")
    rnd = build_archetype_curve(rows, archetype="random", seed=0)
    assert [p.market_id for p in fav] == ["m1", "m2", "m3"]
    assert [p.market_id for p in rnd] == ["m1", "m2", "m3"]
    # random is deterministic in its seed.
    rnd2 = build_archetype_curve(rows, archetype="random", seed=0)
    assert [p.cum_pnl for p in rnd] == [p.cum_pnl for p in rnd2]


# --------------------------------------------------------------------------- #
# Journey dict — down-sampled steps + summary + baseline overlays.
# --------------------------------------------------------------------------- #


def test_build_survival_journey_dict_shape(tmp_path: Path) -> None:
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

    journey = build_survival_journey(
        result=result,
        recorder=recorder,
        rows=rows,
        seed=seed,
        max_steps=2,  # force down-sampling
    )

    # Top-level keys per the master-plan Page-2 data contract.
    assert set(journey) >= {"seed", "lives", "steps", "baselines", "summary"}

    # seed echoes the (fragile) config weights + sizing.
    assert "weights" in journey["seed"]
    assert "max_breath_risk_pct" in journey["seed"]

    # lives carry idx / death / pnl.
    assert journey["lives"], "at least one life"
    life0 = journey["lives"][0]
    assert set(life0) >= {"idx", "death", "pnl"}

    # steps are DOWN-SAMPLED to <= max_steps but the full-fidelity count is kept.
    assert len(journey["steps"]) <= 2
    assert journey["summary"]["total_steps"] >= len(journey["steps"])
    if journey["steps"]:
        s0 = journey["steps"][0]
        assert set(s0) >= {
            "idx", "market", "side", "size", "pnl", "cum_pnl",
            "weights", "win_rate", "life_idx",
        }

    # baselines carry the frozen static curve + archetypes, each a cum-PnL series.
    assert set(journey["baselines"]) >= {"static", "random", "always_favorite"}
    static_curve = journey["baselines"]["static"]
    assert isinstance(static_curve, list) and static_curve
    assert "cum_pnl" in static_curve[0]

    # summary carries the headline learning-vs-static delta + death count.
    assert set(journey["summary"]) >= {
        "deaths", "learning_vs_static_delta", "total_steps",
    }
    assert journey["summary"]["deaths"] == result.deaths

    # The whole dict is JSON-serialisable (it is the export payload).
    import json

    json.dumps(journey)


def test_journey_seed_block_discloses_exploration_epsilon(tmp_path: Path) -> None:
    """Active Survival (Hand 1) Task 2: the journey ``seed`` disclosure block
    carries exploration_epsilon so a downstream loader can round-trip the floor.
    """
    import dataclasses

    rows, snaps = _dying_fixture()
    seed = dataclasses.replace(_fragile_seed(), exploration_epsilon=0.07)
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
    journey = build_survival_journey(
        result=result, recorder=recorder, rows=rows, seed=seed, max_steps=2
    )
    assert "exploration_epsilon" in journey["seed"]
    assert journey["seed"]["exploration_epsilon"] == 0.07


# --------------------------------------------------------------------------- #
# A3b — fragile-seed calibration knobs.
# --------------------------------------------------------------------------- #


def test_fragile_seed_calibration_knobs() -> None:
    # The static OPTIMUM (low risk) is too good to die; the fragile derivation
    # cranks the risk up + (optionally) shrinks the confidence gate so the seed
    # bets big + loses big -> deaths occur. The loss-multiplier knob is exposed
    # for the runner's calibration.
    optimum = StrategyConfig(
        weights=_bullish_weights(),
        max_breath_risk_pct=0.232,
        min_confidence=0.049,
        min_bet_size_usd=4.0,
    )
    fragile = fragile_seed_from_config(optimum, max_breath_risk_pct=1.0)
    # Sizing is cranked fragile; the fusion weights are preserved (learning still
    # evolves the SAME weight space, only the calibration differs).
    assert fragile.max_breath_risk_pct == 1.0
    assert fragile.weights == optimum.weights
    # min_bet_size_usd stays sub-$5 so bets still clear the $100-bankroll cap.
    assert fragile.min_bet_size_usd < 5.0


def test_recorder_loss_multiplier_amplifies_losses(tmp_path: Path) -> None:
    # The optional loss-multiplier knob (codex R7 / A3b) amplifies the MAGNITUDE
    # of LOSING settlements fed to the chain BREATH delta so deaths occur even
    # when the raw stake would NOT drain breath — the calibration mechanism that
    # makes a too-good seed die. A multiplier of 1.0 is the identity.
    #
    # Calibration setup: a seed that risks only ~30% of breath per bet, started
    # with breath 10, SURVIVES every fixture market on the raw (1.0) PnL — each
    # full-stake loss is only ~3 breath, so it never drains to 0. Amplifying the
    # losses (4.0) drains the whole breath on the FIRST loss → the agent now dies
    # repeatedly. This proves the amplification branch (``_RecorderChainAdapter``,
    # ``effective = pnl_usd * self.loss_multiplier``) drives death pressure.
    rows, snaps = _dying_fixture()
    survivable_seed = StrategyConfig(
        weights=_bullish_weights(),
        max_breath_risk_pct=0.3,  # fractional risk -> a single loss survives at 1.0
        min_confidence=0.05,
        min_bet_size_usd=1.0,
    )

    # Identity multiplier: the recorded losses equal the raw per-bet pnl AND the
    # agent SURVIVES (no death) — the loss is too small to drain breath 10.
    rec1 = SurvivalRecorder(rows=rows, loss_multiplier=1.0)
    res1 = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=survivable_seed,
        state_root=tmp_path / "season1",
        initial_breath=10.0,
        initial_bankroll_usd=100.0,
        max_lives=6,
        recorder=rec1,
    )
    losses1 = [s.pnl_usd for s in rec1.steps if s.pnl_usd < 0.0]
    assert losses1, "fixture must produce losses"
    # At 1.0 the survivable seed rides out every loss — no death pressure yet —
    # and ends with breath to spare (each ~30%-of-breath loss can't drain it).
    assert res1.deaths == 0
    surviving_breath = res1.lives[-1].final_breath
    assert surviving_breath > 0.0
    # The first settled bet is the apples-to-apples reference: a FRESH life at
    # breath 10 bets 30% (3.0) on market m1 and loses the full stake.
    first_loss_1 = rec1.steps[0]
    assert first_loss_1.market_id == "m1"
    assert first_loss_1.pnl_usd == -3.0

    # Amplified multiplier (4.0): the SAME seed + breath + first bet, but the loss
    # is fed to the chain at 4x magnitude (12 > 10) so breath 10 drains past 0 and
    # the agent DIES — repeatedly — where the 1.0 run survived.
    rec2 = SurvivalRecorder(rows=rows, loss_multiplier=4.0)
    res2 = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=survivable_seed,
        state_root=tmp_path / "season2",
        initial_breath=10.0,
        initial_bankroll_usd=100.0,
        max_lives=6,
        recorder=rec2,
    )
    losses2 = [s.pnl_usd for s in rec2.steps if s.pnl_usd < 0.0]
    assert losses2, "amplified run must still produce losses"
    # The RAW recorded PnL of the first bet is IDENTICAL to the identity run —
    # amplification is a BREATH-pressure knob, it never fabricates the recorded
    # settlement PnL; the divergence is in the chain breath delta / death only.
    first_loss_2 = rec2.steps[0]
    assert first_loss_2.market_id == "m1"
    assert first_loss_2.pnl_usd == first_loss_1.pnl_usd == -3.0

    # Death pressure is amplified: the 4.0 run DIES where the 1.0 run survived.
    assert res2.deaths > res1.deaths
    assert res2.deaths >= 1
    # The amplified chain delta drains the WHOLE breath to 0 on the loss (the
    # proof the chain delta itself was amplified — 4 * 3.0 = 12 >= breath 10 — and
    # not merely that the death count happened to rise).
    first_dead_life = next(life for life in res2.lives if life.died)
    assert first_dead_life.final_breath == 0.0
