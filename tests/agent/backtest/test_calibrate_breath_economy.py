"""Tests for the sim-based joint breath-economy calibration (Hand 1, R9).

These run the REAL numerical groundhog economy (tithe+tribute on) over the
varying-edge world, so they are inherently sim-bound (a few seconds). They use
the execution-validated sweet spot, where honest +edge survives and noise dies.
"""

from __future__ import annotations

import pytest

from agent.backtest.calibrate_breath_economy import CalibrationResult, calibrate


def test_calibrate_recommends_tame_sizing_that_separates_edge_from_noise() -> None:
    # At the execution-validated sweet spot (m=1.2, fragile=0.2, breath=70,
    # gain=0.5) the honest +edge agent survives far more than the noise agent.
    result = calibrate(
        loss_multiplier_grid=[1.2],
        fragile_grid=[0.2],
        initial_breath_grid=[70],
        seeds=range(3),
        n_rows=300,
        max_incarnations=6,
    )
    assert isinstance(result, CalibrationResult)
    assert result.edge_death_rate < result.noise_death_rate
    # The recommended sizing is TAME (the load-bearing lever), not the
    # deployment 0.95.
    assert result.fragile_max_breath_risk_pct < 0.95
    assert result.loss_multiplier == 1.2
    assert result.initial_breath == 70


def test_calibrate_raises_when_no_cell_separates() -> None:
    # Legacy economy (m=5, aggressive sizing 0.95, low breath): both worlds die,
    # so no cell satisfies edge_dr<0.5 AND noise_dr>0.5 — a surfaced failure.
    with pytest.raises(ValueError):
        calibrate(
            loss_multiplier_grid=[5.0],
            fragile_grid=[0.95],
            initial_breath_grid=[35],
            seeds=range(2),
            n_rows=300,
            max_incarnations=6,
        )
