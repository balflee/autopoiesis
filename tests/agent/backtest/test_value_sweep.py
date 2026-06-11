"""Earnings-aligned sweep (realism v3): 10-dim LHS, t-stat, rank-by-pnl,
legacy-mode effective-floor skip."""

from __future__ import annotations

import math

import pytest

from agent.backtest.cached_sweep import (
    SignalRow,
    SweepMetrics,
    _aggregate,
    rank_configs_by_pnl,
    score_config_sync,
)
from agent.backtest.find_optimal_config import (
    KAPPA_BOUNDS,
    MIN_EDGE_BOUNDS,
    StrategyConfig,
    generate_lhs_strategy_configs,
)
from agent.core.state import Weights


def test_lhs_samples_min_edge_and_kappa_in_bounds() -> None:
    cfgs = generate_lhs_strategy_configs(64, seed=0)
    assert all(MIN_EDGE_BOUNDS[0] <= c.min_edge <= MIN_EDGE_BOUNDS[1] for c in cfgs)
    assert all(KAPPA_BOUNDS[0] <= c.kappa <= KAPPA_BOUNDS[1] for c in cfgs)
    # Actually swept, not constant.
    assert len({round(c.min_edge, 6) for c in cfgs}) > 1
    assert len({round(c.kappa, 6) for c in cfgs}) > 1


def test_aggregate_t_stat_is_sharpe_times_sqrt_bets() -> None:
    m = _aggregate([1.0, 2.0, 3.0, 4.0], [5.0] * 4, wins=4)
    assert m.t_stat == pytest.approx(m.sharpe * math.sqrt(m.bets))


def test_rank_by_pnl_gates_on_t_stat_and_bets() -> None:
    def _m(*, bets: int, net_pnl: float, sharpe: float) -> SweepMetrics:
        return SweepMetrics(
            bets=bets,
            net_pnl=net_pnl,
            win_rate=0.6,
            sharpe=sharpe,
            avg_size=5.0,
            t_stat=sharpe * math.sqrt(bets),
        )

    hi_pnl_weak_t = ("a", _m(bets=300, net_pnl=9000.0, sharpe=0.05))  # t≈0.87
    solid = ("b", _m(bets=300, net_pnl=5000.0, sharpe=0.30))  # t≈5.2
    tiny_sample = ("c", _m(bets=30, net_pnl=99999.0, sharpe=2.0))  # bets gate

    ranked = rank_configs_by_pnl(
        [hi_pnl_weak_t, solid, tiny_sample],  # type: ignore[list-item]
        min_bets=50,
        min_t_stat=2.0,
    )
    assert ranked[0][0] == "b"
    assert len(ranked) == 1  # a fails t-stat, c fails bets


def test_rank_by_pnl_falls_back_to_full_pool_when_gate_empties() -> None:
    weak = ("only", SweepMetrics(bets=3, net_pnl=10.0, win_rate=1.0,
                                 sharpe=0.1, avg_size=5.0, t_stat=0.17))
    ranked = rank_configs_by_pnl([weak], min_bets=50, min_t_stat=2.0)  # type: ignore[list-item]
    assert ranked == [weak]


def _strong_row(market_id: str, *, entry_price: float, outcome: str) -> SignalRow:
    slots = (
        "tennis_technical",
        "market_momentum",
        "smart_money",
        "sentiment_llm",
        "crowd_volume",
    )
    return SignalRow(
        market_id=market_id,
        slug=f"t-{market_id}",
        scores={k: -0.9 for k in slots},  # strongly NO
        confidences={k: 0.9 for k in slots},
        entry_price=entry_price,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=5.0,
    )


def _cfg() -> StrategyConfig:
    return StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=1.0
        ),
        max_breath_risk_pct=0.5,
        min_confidence=0.0,
        min_bet_size_usd=1.0,
    )


def test_legacy_mode_effective_floor_skips_sub_floor_no_bet() -> None:
    """r1 M-3: WITHOUT value mode, a legacy NO bet at yes 0.97 (effective
    0.03) is skipped when the effective floor is set — `--realism` alone
    cannot harvest the mirrored NO-side lottery."""
    rows = [_strong_row("m1", entry_price=0.97, outcome="no")]
    cfg = _cfg()

    unfloored = score_config_sync(rows, cfg, side_correct_pricing=True)
    assert unfloored.bets == 1  # sanity: the signal DOES bet NO here

    floored = score_config_sync(
        rows, cfg, side_correct_pricing=True, effective_entry_price_floor=0.05
    )
    assert floored.bets == 0
