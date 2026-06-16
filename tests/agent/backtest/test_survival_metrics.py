"""Tests for the death-aware survival metrics, focused on the 能学 demo curve
accessors (``learning_curve`` + ``aggregate_curves``).
"""

from __future__ import annotations

import pytest

from agent.backtest.survival_metrics import aggregate_curves, learning_curve


def _climbing_artifact() -> dict:
    """A learner that gets further each life and finally survives at inc 6."""
    return {
        "incarnations": [
            {"progress_pct": 10.0, "pnl_at_death": -5.0, "died": True},
            {"progress_pct": 20.0, "pnl_at_death": -3.0, "died": True},
            {"progress_pct": 30.0, "pnl_at_death": -2.0, "died": True},
            {"progress_pct": 45.0, "pnl_at_death": -1.0, "died": True},
            {"progress_pct": 70.0, "pnl_at_death": 1.0, "died": True},
            {"progress_pct": 100.0, "pnl_at_death": 6.0, "died": False},
        ],
        "survived": True,
        "surviving_incarnation": 6,
    }


def test_learning_curve_reads_sequences_and_detects_rise() -> None:
    lc = learning_curve(_climbing_artifact())
    assert lc["n_incarnations"] == 6
    assert lc["progress_pct"][0] == 10.0
    assert lc["best_progress_pct"] == 100.0
    assert lc["final_progress_pct"] == 100.0
    assert lc["survived"] is True
    assert lc["surviving_incarnation"] == 6
    # Climbed materially from the first third to the last third.
    assert lc["rise"] > 30.0


def test_learning_curve_flat_arm_has_zero_rise_and_no_survival() -> None:
    artifact = {
        "incarnations": [
            {"progress_pct": 12.0, "pnl_at_death": -2.0, "died": True}
            for _ in range(9)
        ],
        "survived": False,
        "surviving_incarnation": None,
    }
    lc = learning_curve(artifact)
    assert lc["rise"] == pytest.approx(0.0)
    assert lc["survived"] is False
    assert lc["best_progress_pct"] == 12.0
    assert lc["surviving_incarnation"] is None


def test_learning_curve_empty_is_safe() -> None:
    lc = learning_curve({"incarnations": []})
    assert lc["n_incarnations"] == 0
    assert lc["rise"] == 0.0
    assert lc["best_progress_pct"] == 0.0
    assert lc["survived"] is False


def test_aggregate_curves_separates_arms_across_seeds() -> None:
    curves = [
        learning_curve(_climbing_artifact()),
        learning_curve(
            {
                "incarnations": [
                    {"progress_pct": 30.0, "pnl_at_death": -1.0, "died": True},
                    {"progress_pct": 40.0, "pnl_at_death": -1.0, "died": True},
                ],
                "survived": False,
                "surviving_incarnation": None,
            }
        ),
    ]
    agg = aggregate_curves(curves)
    assert agg["n_seeds"] == 2
    assert agg["survival_rate"] == pytest.approx(0.5)
    # best of the two: 100 and 40 ⇒ mean 70.
    assert agg["mean_best_progress_pct"] == pytest.approx(70.0)
    # Only the climbing seed survived (at incarnation 6).
    assert agg["mean_surviving_incarnation"] == pytest.approx(6.0)


def test_aggregate_curves_all_frozen_reports_none_surviving() -> None:
    curves = [
        {
            "survived": False,
            "surviving_incarnation": None,
            "best_progress_pct": 15.0,
            "final_progress_pct": 12.0,
            "rise": 0.5,
        }
    ]
    agg = aggregate_curves(curves)
    assert agg["survival_rate"] == 0.0
    assert agg["mean_surviving_incarnation"] is None
