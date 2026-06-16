"""Tests for the LHS strategy-config generator (sweep driver).

A strategy config spans BOTH parameter families being optimized:

* ① fusion weights (``Weights``: w_r, the alpha 2-simplex, the beta split, rho)
* ② bet sizing / abstention (max_breath_risk_pct, min_confidence, min_bet_size)
"""

from __future__ import annotations

import numpy as np
import pytest

from agent.backtest.find_optimal_config import (
    KAPPA_BOUNDS,
    KAPPA_XM_BOUNDS,
    MAX_BREATH_RISK_BOUNDS,
    MIN_BET_SIZE_BOUNDS,
    MIN_CONFIDENCE_BOUNDS,
    MIN_EDGE_BOUNDS,
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


# ---------------------------------------------------------------------------
# Task 3: kappa_xm field + 11-dim LHS tests
# ---------------------------------------------------------------------------


def test_strategy_config_kappa_xm_defaults_to_zero() -> None:
    """StrategyConfig without kappa_xm defaults to 0.0 (byte-identical to v3)."""
    cfg = StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=1.0
        ),
        max_breath_risk_pct=0.30,
        min_confidence=0.05,
        min_bet_size_usd=5.0,
    )
    assert cfg.kappa_xm == 0.0


def test_strategy_config_kappa_xm_explicit() -> None:
    """StrategyConfig accepts explicit kappa_xm."""
    cfg = StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=1.0
        ),
        max_breath_risk_pct=0.30,
        min_confidence=0.05,
        min_bet_size_usd=5.0,
        kappa_xm=0.5,
    )
    assert cfg.kappa_xm == 0.5


def test_kappa_xm_bounds_includes_zero() -> None:
    """KAPPA_XM_BOUNDS low end must be 0.0 (kappa_xm=0 is signal-off / v3-equivalent)."""
    lo, hi = KAPPA_XM_BOUNDS
    assert lo == 0.0
    assert hi > lo


def test_lhs_kappa_xm_within_bounds() -> None:
    """All generated configs have kappa_xm within KAPPA_XM_BOUNDS."""
    configs = generate_lhs_strategy_configs(20, seed=0)
    lo, hi = KAPPA_XM_BOUNDS
    for cfg in configs:
        assert lo <= cfg.kappa_xm <= hi


def test_lhs_kappa_xm_varies() -> None:
    """kappa_xm must not be constant across the LHS sample (it's a swept dim)."""
    configs = generate_lhs_strategy_configs(20, seed=0)
    values = {cfg.kappa_xm for cfg in configs}
    assert len(values) > 1


def test_lhs_v3_fields_determinism_unchanged() -> None:
    """Adding col 10 (kappa_xm) must not perturb cols 0-9 (all v3 fields).

    We reconstruct what the OLD 10-dim LHS produced for the same seed by
    consuming the same RNG with exactly 10 permutations in column order.
    The new 11-dim generator adds ONE more permutation after — columns 0-9
    must be byte-identical.
    """
    seed = 7
    n = 16

    def _sc(unit: float, bounds: tuple[float, float]) -> float:
        lo, hi = bounds
        return lo + unit * (hi - lo)

    # Reconstruct the old 10-dim LHS manually (same RNG, same 10 columns).
    rng_old = np.random.default_rng(seed)
    centres = (np.arange(n, dtype=np.float64) + 0.5) / n
    cube_old = np.empty((n, 10), dtype=np.float64)
    for j in range(10):
        cube_old[:, j] = rng_old.permutation(centres)

    # New 11-dim generator under test.
    configs = generate_lhs_strategy_configs(n, seed=seed)

    for i, (row, cfg) in enumerate(zip(cube_old, configs, strict=True)):
        # dim 0: w_r
        assert cfg.weights.w_r == pytest.approx(float(row[0])), f"row {i}: w_r"
        # dims 1-2: alpha 2-simplex
        u1, u2 = sorted((float(row[1]), float(row[2])))
        expected_alpha = [u1, u2 - u1, 1.0 - u2]
        assert cfg.weights.alpha == pytest.approx(expected_alpha), f"row {i}: alpha"
        # dim 3: beta split
        b = float(row[3])
        assert cfg.weights.beta == pytest.approx([b, 1.0 - b]), f"row {i}: beta"
        # dim 4: rho
        assert cfg.weights.rho == pytest.approx(float(row[4])), f"row {i}: rho"
        # dim 5: max_breath_risk_pct
        assert cfg.max_breath_risk_pct == pytest.approx(
            _sc(float(row[5]), MAX_BREATH_RISK_BOUNDS)
        ), f"row {i}: max_breath_risk_pct"
        # dim 6: min_confidence
        assert cfg.min_confidence == pytest.approx(
            _sc(float(row[6]), MIN_CONFIDENCE_BOUNDS)
        ), f"row {i}: min_confidence"
        # dim 7: min_bet_size_usd
        assert cfg.min_bet_size_usd == pytest.approx(
            _sc(float(row[7]), MIN_BET_SIZE_BOUNDS)
        ), f"row {i}: min_bet_size_usd"
        # dim 8: min_edge
        assert cfg.min_edge == pytest.approx(
            _sc(float(row[8]), MIN_EDGE_BOUNDS)
        ), f"row {i}: min_edge"
        # dim 9: kappa
        assert cfg.kappa == pytest.approx(
            _sc(float(row[9]), KAPPA_BOUNDS)
        ), f"row {i}: kappa"


# ---------------------------------------------------------------------------
# Active Survival (Hand 1) Task 2: exploration_epsilon — a NON-advisable,
# NON-swept floor knob riding on StrategyConfig. It is the LAST field, defaults
# 0.0 (byte-identical to a pre-Hand-1 config), and the rebirth advisor must
# NEVER be able to tune it (see test_reincarnation for the GENOME_KEYS guard).
# ---------------------------------------------------------------------------


def test_strategy_config_exploration_epsilon_defaults_to_zero() -> None:
    """StrategyConfig without exploration_epsilon defaults to 0.0."""
    cfg = StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=1.0
        ),
        max_breath_risk_pct=0.30,
        min_confidence=0.05,
        min_bet_size_usd=5.0,
    )
    assert cfg.exploration_epsilon == 0.0


def test_strategy_config_exploration_epsilon_explicit() -> None:
    """StrategyConfig accepts an explicit exploration_epsilon."""
    cfg = StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=1.0
        ),
        max_breath_risk_pct=0.30,
        min_confidence=0.05,
        min_bet_size_usd=5.0,
        exploration_epsilon=0.07,
    )
    assert cfg.exploration_epsilon == 0.07


def test_lhs_exploration_epsilon_stays_zero() -> None:
    """exploration_epsilon is NOT an LHS sweep dim — every generated config
    keeps the 0.0 default (it is a hand-set floor, never tuned by the sweep)."""
    configs = generate_lhs_strategy_configs(20, seed=0)
    assert all(cfg.exploration_epsilon == 0.0 for cfg in configs)
