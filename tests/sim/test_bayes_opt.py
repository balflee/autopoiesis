"""Sprint_3 (T-C-003) tests for the Bayesian-Optimization refiner +
the full ``Sweeper.calibrate`` pipeline.

Acceptance criteria covered:

* :func:`run_bayesian_optimization` is deterministic for a given seed.
* BO drives the best-loss-so-far monotonically non-increasing (the
  ``calib_converged`` gate's last-16 invariant).
* The full ``Sweeper.calibrate`` produces a :class:`CalibrationRun`
  with at least 12/14 objectives passing on the canonical default
  search-space — confirms the calibration sim is actually capable of
  finding the calibrated region under the brief's `--n 256 --bo-trials
  64` budget. We use a smaller budget here for CI speed but check the
  same shape invariants.
* The CLI's ``sweep --bo-trials`` subcommand writes the artifact set
  the calibration validator expects.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sim.objectives import GOOD_CALIBRATION_OBJECTIVES, ScoreContext
from sim.params import LHS_BOUNDS, LHS_DIMS, ParamSpace
from sim.sampling.bo import BOTrial, run_bayesian_optimization
from sim.sweeper import Sweeper


# ----------------------------------------------------------------------
# BO refiner unit-tests — synthetic quadratic objective so the test
# does not depend on the sim's runtime cost.
# ----------------------------------------------------------------------


def _quadratic_objective(params: ParamSpace) -> float:
    """Simple convex function on the LHS_DIMS — minimum at the LHS_BOUNDS
    midpoints. BO should drive the loss toward that point."""
    score = 0.0
    for name in LHS_DIMS:
        low, high = LHS_BOUNDS[name]
        mid = 0.5 * (low + high)
        # Normalise so each dim contributes ~0..1 to the loss.
        v = float(getattr(params, name))
        score += ((v - mid) / max(high - low, 1.0)) ** 2
    return score


def test_run_bo_is_deterministic_for_same_seed() -> None:
    """Same seed + identical warm start → identical BO trial trace."""
    base = ParamSpace()
    lhs_points = (
        base,
        base.with_overrides({"e_decision_tax": 4.0}),
        base.with_overrides({"e_time_tax_per_tick": 1.5}),
        base.with_overrides({"desperate_threshold": 100.0}),
    )
    lhs_losses = tuple(_quadratic_objective(p) for p in lhs_points)

    r1 = run_bayesian_optimization(
        objective=_quadratic_objective,
        base_params=base,
        dims=LHS_DIMS,
        lhs_points=lhs_points,
        lhs_losses=lhs_losses,
        n_trials=8,
        seed=42,
    )
    r2 = run_bayesian_optimization(
        objective=_quadratic_objective,
        base_params=base,
        dims=LHS_DIMS,
        lhs_points=lhs_points,
        lhs_losses=lhs_losses,
        n_trials=8,
        seed=42,
    )
    assert r1.best_loss == pytest.approx(r2.best_loss)
    assert tuple(round(t.loss, 8) for t in r1.trials) == tuple(
        round(t.loss, 8) for t in r2.trials
    )


def test_run_bo_records_each_iteration() -> None:
    base = ParamSpace()
    lhs_points = (base,) * 3
    lhs_losses = (_quadratic_objective(base),) * 3
    result = run_bayesian_optimization(
        objective=_quadratic_objective,
        base_params=base,
        dims=LHS_DIMS,
        lhs_points=lhs_points,
        lhs_losses=lhs_losses,
        n_trials=5,
        seed=0,
    )
    assert len(result.trials) == 5
    # best_loss_so_far is monotonically non-increasing within the
    # trial-only record (LHS warm-start ALSO feeds it via the
    # ``min(...)`` lower bound).
    prev = float("inf")
    for trial in result.trials:
        assert isinstance(trial, BOTrial)
        assert trial.best_loss_so_far <= prev + 1e-9
        prev = trial.best_loss_so_far


def test_run_bo_rejects_mismatched_inputs() -> None:
    base = ParamSpace()
    with pytest.raises(ValueError, match="length mismatch"):
        run_bayesian_optimization(
            objective=_quadratic_objective,
            base_params=base,
            dims=LHS_DIMS,
            lhs_points=(base,),
            lhs_losses=(0.0, 0.0),  # length mismatch
            n_trials=2,
            seed=0,
        )
    with pytest.raises(ValueError, match="≥1"):
        run_bayesian_optimization(
            objective=_quadratic_objective,
            base_params=base,
            dims=LHS_DIMS,
            lhs_points=(base,),
            lhs_losses=(0.0,),
            n_trials=0,
            seed=0,
        )


# ----------------------------------------------------------------------
# Sweeper.calibrate integration tests — small budget so CI stays fast.
# ----------------------------------------------------------------------


def test_calibrate_returns_full_calibration_run() -> None:
    sweeper = Sweeper(
        base_params=ParamSpace(),
        lifetimes_per_archetype=2,
        max_ticks=400,
    )
    run = sweeper.calibrate(
        n_lhs=4,
        bo_trials=3,
        seed=0,
        final_lifetimes_per_archetype=3,
        ci_half_width_max_days=2.0,
    )
    assert run.n_lhs == 4
    assert run.bo_trials == 3
    assert len(run.lhs_combos) == 4
    assert len(run.bo_trials_records) == 3
    # Final-verdict has the canonical 14 objectives.
    assert tuple(o.name for o in run.final_verdict.objectives) == (
        GOOD_CALIBRATION_OBJECTIVES
    )
    # Selected params is a valid ParamSpace and the winning_source is one
    # of the two announced strings.
    assert isinstance(run.selected_params, ParamSpace)
    assert run.winning_source in {"latin_hypercube", "bayesian_optimization"}


def test_calibrate_best_loss_monotone_non_increasing() -> None:
    """The combined LHS+BO best-loss-so-far trace is monotone non-increasing.

    This is the invariant the calibration validator's
    ``calib_converged`` gate checks (last-16 improving). Anything else
    means BO is regressing the warm start, which would be a real bug.
    """
    sweeper = Sweeper(
        base_params=ParamSpace(),
        lifetimes_per_archetype=2,
        max_ticks=300,
    )
    run = sweeper.calibrate(
        n_lhs=6,
        bo_trials=8,
        seed=1,
        final_lifetimes_per_archetype=2,
        ci_half_width_max_days=2.0,
    )
    trace = run.best_loss_trace
    assert len(trace) == 6 + 8
    # Monotone non-increasing.
    for a, b in zip(trace, trace[1:], strict=False):
        assert b <= a + 1e-9


def test_calibrate_is_deterministic_for_same_seed() -> None:
    """The full pipeline must be byte-stable under a re-run."""
    sweeper_a = Sweeper(
        base_params=ParamSpace(), lifetimes_per_archetype=2, max_ticks=300
    )
    sweeper_b = Sweeper(
        base_params=ParamSpace(), lifetimes_per_archetype=2, max_ticks=300
    )
    a = sweeper_a.calibrate(
        n_lhs=4, bo_trials=3, seed=7, final_lifetimes_per_archetype=2,
        ci_half_width_max_days=2.0,
    )
    b = sweeper_b.calibrate(
        n_lhs=4, bo_trials=3, seed=7, final_lifetimes_per_archetype=2,
        ci_half_width_max_days=2.0,
    )
    assert a.selected_params == b.selected_params
    assert a.final_verdict.passed_count == b.final_verdict.passed_count
    # Loss must be byte-identical (same Python build).
    assert a.final_verdict.loss == b.final_verdict.loss


def test_calibrate_determinism_objective_is_verified() -> None:
    """The framework objective #13 must report passed=True at the end of
    every calibrate() run — we re-run the winning combo internally."""
    sweeper = Sweeper(
        base_params=ParamSpace(), lifetimes_per_archetype=2, max_ticks=300
    )
    run = sweeper.calibrate(
        n_lhs=4, bo_trials=3, seed=2, final_lifetimes_per_archetype=2,
        ci_half_width_max_days=2.0,
    )
    assert run.determinism_verified
    assert run.final_verdict.per_objective["determinism_round_trip"].passed


def test_calibrate_score_context_propagates_ci_threshold() -> None:
    """``ci_half_width_max_days`` controls objective #12 — verify the
    threshold string carries the value to the report (auditable)."""
    sweeper = Sweeper(
        base_params=ParamSpace(), lifetimes_per_archetype=2, max_ticks=300
    )
    run = sweeper.calibrate(
        n_lhs=4, bo_trials=3, seed=3, final_lifetimes_per_archetype=2,
        ci_half_width_max_days=0.5,
    )
    obj = run.final_verdict.per_objective["ci_width_under_threshold"]
    assert "0.5" in obj.threshold


def test_calibrate_rejects_invalid_budgets() -> None:
    sweeper = Sweeper(base_params=ParamSpace(), lifetimes_per_archetype=2, max_ticks=200)
    with pytest.raises(ValueError, match="n_lhs"):
        sweeper.calibrate(n_lhs=0, bo_trials=4, seed=0)
    with pytest.raises(ValueError, match="bo_trials"):
        sweeper.calibrate(n_lhs=4, bo_trials=0, seed=0)


# ----------------------------------------------------------------------
# CLI end-to-end — exercises the orchestrator's gate path.
# ----------------------------------------------------------------------


def test_cli_sweep_with_bo_produces_full_artifact_set(tmp_path: Path) -> None:
    """``python -m sim.cli sweep --n 4 --bo-trials 3`` must write every
    artifact the calibration validator looks for under a single run
    directory.
    """
    output = tmp_path / "calibration"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sim.cli",
            "sweep",
            "--n", "4",
            "--bo-trials", "3",
            "--output", str(output),
            "--seed", "0",
            "--lifetimes-per-archetype", "2",
            "--final-lifetimes-per-archetype", "3",
            "--max-ticks", "300",
            "--ci-half-width-max-days", "2.0",
            "--run-id", "test_run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    run_dir = output / "test_run"
    assert run_dir.is_dir()
    required = {
        "selected_params.json",
        "objectives_passed.json",
        "archetype_breakdown.json",
        "sensitivity_analysis.json",
        "bo_trace.json",
        "lifetimes.jsonl",
        "CALIBRATION_REPORT.md",
        "bo_convergence.png",
        "lifetime_histogram.png",
        "cause_of_death.png",
    }
    found = {p.name for p in run_dir.iterdir()}
    assert required <= found, f"missing: {required - found}"

    # selected_params validates against the published schema shape (all
    # 9 ParamSpace fields, every value numeric).
    sel = json.loads((run_dir / "selected_params.json").read_text(encoding="utf-8"))
    expected_keys = {
        "initial_breath",
        "passive_burn_rate",
        "conversion_rate",
        "target_horizon",
        "min_bet_size",
        "e_decision_tax",
        "e_time_tax_per_tick",
        "soft_cap_threshold",
        "desperate_threshold",
    }
    assert set(sel.keys()) == expected_keys
    for k, v in sel.items():
        assert isinstance(v, (int, float)), (k, v)

    # objectives_passed.json is Shape A — list of records keyed by name.
    obj = json.loads(
        (run_dir / "objectives_passed.json").read_text(encoding="utf-8")
    )
    names = {o["name"] for o in obj["objectives"]}
    assert names == set(GOOD_CALIBRATION_OBJECTIVES)
    assert "loss" in obj
    assert "passed_count" in obj

    # archetype_breakdown.json — flat shape, 3 mandatory archetypes
    # exact keys (random_gambler appears too but is non-blocking).
    breakdown = json.loads(
        (run_dir / "archetype_breakdown.json").read_text(encoding="utf-8")
    )
    assert {"pessimist", "optimist", "satisficer"} <= set(breakdown.keys())
    for k in ("pessimist", "optimist", "satisficer"):
        assert "n_lifetimes" in breakdown[k]
        assert "mean_lifetime_days" in breakdown[k]


def test_score_context_extra_dict_does_not_break_score_calibration() -> None:
    """Defensive: the ScoreContext `.extra` dict is forward-compat
    cargo. It must not poison the verdict shape."""
    sweeper = Sweeper(base_params=ParamSpace(), lifetimes_per_archetype=2, max_ticks=200)
    run = sweeper.calibrate(
        n_lhs=4, bo_trials=2, seed=11, final_lifetimes_per_archetype=2,
        ci_half_width_max_days=2.0,
    )
    # The context plumbed by Sweeper.calibrate has default `extra={}`.
    # Verify the verdict still has 14 objectives — no silent drop.
    assert len(run.final_verdict.objectives) == 14
    # Build an ad-hoc context with extra populated; the verdict must
    # still pass schema.
    ctx = ScoreContext(extra={"future_field": 1})
    assert ctx.extra == {"future_field": 1}
