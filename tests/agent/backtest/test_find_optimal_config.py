"""Tests for the LHS strategy-config generator (sweep driver).

A strategy config spans BOTH parameter families being optimized:

* ① fusion weights (``Weights``: w_r, the alpha 2-simplex, the beta split, rho)
* ② bet sizing / abstention (max_breath_risk_pct, min_confidence, min_bet_size)
"""

from __future__ import annotations

from agent.backtest.find_optimal_config import (
    MAX_BREATH_RISK_BOUNDS,
    MIN_BET_SIZE_BOUNDS,
    MIN_CONFIDENCE_BOUNDS,
    StrategyConfig,
    generate_lhs_strategy_configs,
)
from agent.core.state import Weights


def test_generates_n_configs_valid_weights_and_bounded_sizing() -> None:
    configs = generate_lhs_strategy_configs(16, seed=0)
    assert len(configs) == 16
    for c in configs:
        assert isinstance(c, StrategyConfig)
        assert isinstance(c.weights, Weights)  # construction validates ① simplex
        lo, hi = MAX_BREATH_RISK_BOUNDS
        assert lo <= c.max_breath_risk_pct <= hi
        lo, hi = MIN_CONFIDENCE_BOUNDS
        assert lo <= c.min_confidence <= hi
        lo, hi = MIN_BET_SIZE_BOUNDS
        assert lo <= c.min_bet_size_usd <= hi


def test_deterministic_given_seed() -> None:
    a = generate_lhs_strategy_configs(8, seed=42)
    b = generate_lhs_strategy_configs(8, seed=42)
    assert a == b


def test_different_seeds_differ() -> None:
    a = generate_lhs_strategy_configs(8, seed=1)
    b = generate_lhs_strategy_configs(8, seed=2)
    assert a != b


def test_lhs_strata_span_both_families() -> None:
    # 20 LHS samples must reach both ends of a ① dim (w_r) AND a ② dim
    # (max_breath_risk_pct) — proof both families are actually swept.
    configs = generate_lhs_strategy_configs(20, seed=0)
    w_rs = sorted(c.weights.w_r for c in configs)
    risks = sorted(c.max_breath_risk_pct for c in configs)
    assert w_rs[0] < 0.1 and w_rs[-1] > 0.9
    lo, hi = MAX_BREATH_RISK_BOUNDS
    span = hi - lo
    assert risks[0] < lo + 0.1 * span
    assert risks[-1] > hi - 0.1 * span
