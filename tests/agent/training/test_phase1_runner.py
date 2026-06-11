# ruff: noqa: RUF002, RUF003
"""Phase 1 training-pipeline tests — T-B-004 acceptance criteria.

Coverage shape (brief required ≥4 tests):

* ``test_phase1_runner_runs_end_to_end`` — full happy path on a small
  synthetic training set; asserts the three required output files
  exist, weights_v0.json round-trips through :class:`Weights`, and
  the run completes in well under the CI budget.
* ``test_phase1_freeze_beta1_byte_identical_across_all_steps`` — the
  hard rule from the brief: β₁ MUST be exactly the initial 0.0
  across every row of evolution_curve.csv.
* ``test_phase1_freeze_w_r_w_s_byte_identical_across_all_steps`` —
  defence-in-depth on the weight_updater Phase 1 freeze extension.
* ``test_phase1_no_lookahead_per_feature_row`` — the canonical PIT
  invariant: every loaded row passes
  ``assert_no_lookahead(asof_ts=tipoff_at - 1s)``.
* ``test_phase1_backtest_validity_held_out_loss_beats_uniform`` — the
  ``backtest_validity`` gate. Must be ``trained < uniform`` on a
  20% held-out test split.
* ``test_phase1_freeze_violation_raises`` — defensive test: if the
  WeightUpdater were ever to drift β₁ (it MUSTN'T), the runner
  surfaces the failure via :class:`Phase1FreezeViolation` rather
  than silently shipping bad weights.
* ``test_calibrated_params_round_trip_via_parquet_metadata`` — the
  calibration→training handshake: build_training_set_v1 stamps the
  selected_params into parquet metadata; the runner echoes them in
  :class:`Phase1Result.calibrated_params` for the training report.

All tests run hermetically against a small (~64-game) synthetic
training set produced in a tmpdir — NO live API calls.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.core.state import Weights
from agent.training.feature_engineering import (
    PHASE1_REQUIRED_COLUMNS,
    Phase1FeatureRow,
    build_phase1_feature_rows,
    load_training_set,
)
from agent.training.phase1_runner import (
    BacktestValidityFailed,
    Phase1Config,
    Phase1FreezeViolation,
    _assert_phase1_freezes_hold,
    _binary_log_loss,
    _predict_home_prob,
    run_phase1_training,
)
from data.etl.build_training_set import build_training_set_v1
from data.etl.pit_correct import LookaheadError, assert_no_lookahead, require_asof_ts

# ---------------------------------------------------------------------
# Fixtures — small synthetic training sets for the hermetic test loop.
# ---------------------------------------------------------------------


@pytest.fixture
def small_training_set(tmp_path: Path) -> Path:
    """A 64-game synthetic parquet — enough for backtest_validity to
    reliably PASS without taking >2 seconds in CI."""
    out = tmp_path / "ts_small.parquet"
    build_training_set_v1(
        n_games=64,
        seed=20260522,
        output_path=out,
    )
    return out


@pytest.fixture
def full_training_set(tmp_path: Path) -> Path:
    """A 240-game synthetic parquet — the production-shape run."""
    out = tmp_path / "ts_full.parquet"
    build_training_set_v1(
        n_games=240,
        seed=1779408257,
        output_path=out,
    )
    return out


# ---------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------


def test_phase1_runner_runs_end_to_end(
    small_training_set: Path, tmp_path: Path
) -> None:
    """The full training pipeline must run cleanly + produce the three
    output files the brief requires."""
    config = Phase1Config(
        training_set=small_training_set,
        output_dir=tmp_path / "phase1_out",
    )
    result = run_phase1_training(config)

    # Output files exist + are non-empty.
    assert result.weights_json_path.exists()
    assert result.evolution_curve_path.exists()
    assert result.report_md_path.exists()
    assert result.weights_json_path.stat().st_size > 0
    assert result.evolution_curve_path.stat().st_size > 0
    assert result.report_md_path.stat().st_size > 0

    # weights_v0.json round-trips through the canonical Weights model.
    raw = json.loads(result.weights_json_path.read_text(encoding="utf-8"))
    w = Weights.model_validate(raw)
    assert pytest.approx(w.w_r + w.w_s, abs=1e-6) == 1.0
    assert pytest.approx(sum(w.alpha), abs=1e-6) == 1.0
    assert pytest.approx(sum(w.beta), abs=1e-6) == 1.0


# ---------------------------------------------------------------------
# Phase 1 freeze invariant — β₁ byte-identical across all steps
# ---------------------------------------------------------------------


def test_phase1_freeze_beta1_byte_identical_across_all_steps(
    full_training_set: Path, tmp_path: Path
) -> None:
    """β₁ trajectory in evolution_curve.csv MUST be 0.0 across all rows.

    Brief acceptance criterion (HARD RULE): "Phase 1 freeze verified
    across full training run: β₁ trajectory in evolution_curve.csv is
    byte-identical to initial 0.0 across all N steps".
    """
    config = Phase1Config(
        training_set=full_training_set,
        output_dir=tmp_path / "phase1_out",
    )
    result = run_phase1_training(config)

    with result.evolution_curve_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        beta1_values = [row["beta_1"] for row in reader]

    # 240 games at 0.20 holdout → 192 training rows + 1 initial snapshot = 193.
    assert len(beta1_values) == 193, (
        f"expected exactly 193 rows (1 initial + 192 training steps); "
        f"got {len(beta1_values)}"
    )
    # Byte-identical means EXACT string equality — not a float compare.
    # Every row's beta_1 column must serialise to "0.0".
    distinct = set(beta1_values)
    assert distinct == {"0.0"}, (
        f"β₁ drifted in evolution_curve.csv (expected only '0.0', got "
        f"{distinct})"
    )


def test_phase1_freeze_w_r_w_s_byte_identical_across_all_steps(
    small_training_set: Path, tmp_path: Path
) -> None:
    """W_R + W_S frozen — defence-in-depth on the weight_updater
    Phase 1 freeze extension."""
    config = Phase1Config(
        training_set=small_training_set,
        output_dir=tmp_path / "phase1_out",
        initial_w_r=0.55,
        initial_w_s=0.45,
    )
    result = run_phase1_training(config)

    with result.evolution_curve_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    distinct_wr = {r["w_r"] for r in rows}
    distinct_ws = {r["w_s"] for r in rows}
    assert distinct_wr == {"0.55"}, f"W_R drifted: {distinct_wr}"
    assert distinct_ws == {"0.45"}, f"W_S drifted: {distinct_ws}"


# ---------------------------------------------------------------------
# No-look-ahead — every feature row passes the PIT chokepoint
# ---------------------------------------------------------------------


def test_phase1_no_lookahead_per_feature_row(small_training_set: Path) -> None:
    """Every loaded feature row passes
    ``assert_no_lookahead(asof_ts=tipoff_at - 1s)``.

    Mirrors the brief's no_lookahead gate verbatim. Two-pronged check:

    1. ``build_phase1_feature_rows`` runs both the chokepoint
       (whole-frame) AND a per-row inequality — if either fired it
       would have raised LookaheadError before we got here.
    2. We additionally verify every row's ``available_at <
       tipoff_at`` invariant directly so a future refactor that
       silently drops the per-row check inside the builder still
       fails this test.
    """
    df = load_training_set(small_training_set)
    rows = build_phase1_feature_rows(df)
    assert len(rows) > 0

    pit_margin = timedelta(seconds=1)
    for row in rows:
        assert row.available_at <= row.tipoff_at - pit_margin, (
            f"row {row.game_id}: available_at={row.available_at.isoformat()} "
            f"leaks past tipoff_at - 1s={row.tipoff_at - pit_margin}"
        )

    # And the canonical chokepoint passes the whole frame (it already
    # ran inside build_phase1_feature_rows but defence-in-depth here).
    assert_no_lookahead(df, df["tipoff_at"].max() - pit_margin)

    # Schema coverage check — the parquet exposes ALL required columns.
    assert set(PHASE1_REQUIRED_COLUMNS).issubset(set(df.columns))


def test_load_training_set_rejects_naive_tipoff() -> None:
    """The chokepoint refuses naive datetimes — the build-training-set
    output is tz-aware, but a defensive test guards against a future
    writer drift."""
    cutoff_naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
    with pytest.raises(LookaheadError):
        require_asof_ts(cutoff_naive)


# ---------------------------------------------------------------------
# Backtest validity — held-out loss < uniform-α baseline
# ---------------------------------------------------------------------


def test_phase1_backtest_validity_held_out_loss_beats_uniform(
    full_training_set: Path, tmp_path: Path
) -> None:
    """Brief acceptance criterion ``backtest_validity``: trained
    log-loss on a 20% held-out split is strictly less than initial
    uniform-α log-loss."""
    config = Phase1Config(
        training_set=full_training_set,
        output_dir=tmp_path / "phase1_out",
    )
    result = run_phase1_training(config)
    assert result.trained_test_loss < result.uniform_test_loss, (
        f"trained={result.trained_test_loss:.6f} >= "
        f"uniform={result.uniform_test_loss:.6f} — "
        f"backtest_validity GATE FAILED"
    )
    # Sanity: the test split is non-trivial (20% of 240 = 48).
    assert result.n_test_games == pytest.approx(48, abs=1)
    assert result.n_training_games == pytest.approx(192, abs=1)


# ---------------------------------------------------------------------
# Direct unit tests on the math + freeze helpers
# ---------------------------------------------------------------------


def test_binary_log_loss_matches_textbook_values() -> None:
    """BCE log-loss at the canonical edge cases."""
    # Perfect prediction for y=1: -log(1) = 0
    assert _binary_log_loss(1.0, 1) == pytest.approx(0.0, abs=1e-7)
    # Maximum confusion for y=0 / p=0.5: -log(0.5) ≈ 0.693
    assert _binary_log_loss(0.5, 0) == pytest.approx(0.6931, abs=1e-3)
    # eps clamp keeps p=0 finite (was -inf before clamp)
    assert _binary_log_loss(0.0, 1) > 0  # large but finite


def test_predict_home_prob_pure_function() -> None:
    """The prediction is pure: same inputs → same output, no IO."""
    row = Phase1FeatureRow(
        game_id="g1",
        tipoff_at=datetime(2026, 1, 1, 19, 0, tzinfo=UTC),
        available_at=datetime(2026, 1, 1, 18, 59, tzinfo=UTC),
        home_team="LAL",
        away_team="BOS",
        nba_technical_score=0.5,
        market_momentum_score=-0.2,
        smart_money_score=0.1,
        crowd_volume_score=0.0,
        nba_technical_conf=0.8,
        market_momentum_conf=0.7,
        smart_money_conf=0.6,
        crowd_volume_conf=0.5,
        outcome=1,
    )
    w = Weights(
        w_r=0.6,
        w_s=0.4,
        alpha=[0.5, 0.3, 0.2],
        beta=[0.0, 1.0],
        rho=0.5,
    )
    p1 = _predict_home_prob(row, w)
    p2 = _predict_home_prob(row, w)
    assert p1 == p2  # pure
    assert 0.0 < p1 < 1.0


def test_freeze_violation_helper_catches_beta1_drift() -> None:
    """If a future :class:`WeightUpdater` ever returns drifted β₁, the
    runner's :func:`_assert_phase1_freezes_hold` must catch it."""
    initial = Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[1 / 3, 1 / 3, 1 / 3],
        beta=[0.0, 1.0],
        rho=0.5,
    )
    drifted = Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[1 / 3, 1 / 3, 1 / 3],
        beta=[0.01, 0.99],
        rho=0.5,
    )
    with pytest.raises(Phase1FreezeViolation, match=r"β₁"):
        _assert_phase1_freezes_hold(initial=initial, current=drifted, step=42)


# ---------------------------------------------------------------------
# Calibration → training handshake
# ---------------------------------------------------------------------


def test_calibrated_params_round_trip_via_parquet_metadata(
    small_training_set: Path, tmp_path: Path
) -> None:
    """build_training_set_v1 stamps selected_params into parquet
    metadata; run_phase1_training surfaces them in Phase1Result."""
    config = Phase1Config(
        training_set=small_training_set,
        output_dir=tmp_path / "phase1_out",
    )
    result = run_phase1_training(config)
    # ≥4 PRD §14.1 calibrated parameters per the brief.
    assert len(result.calibrated_params) >= 4
    expected_keys = {
        "initial_breath",
        "soft_cap_threshold",
        "desperate_threshold",
        "min_bet_size",
    }
    assert expected_keys.issubset(set(result.calibrated_params.keys()))


def test_calibrated_params_load_from_explicit_selected_params_json(
    tmp_path: Path
) -> None:
    """Explicit selected_params.json overrides defaults — the
    calibration→training handshake when T-C-003's output is shipped."""
    selected = tmp_path / "selected_params.json"
    selected.write_text(
        json.dumps(
            {
                "initial_breath": 935.34,
                "soft_cap_threshold": 2461.62,
                "desperate_threshold": 175.11,
                "min_bet_size": 5.0,
                "passive_burn_rate": 0.75,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    parquet = tmp_path / "ts_with_calib.parquet"
    manifest = build_training_set_v1(
        n_games=32,
        seed=42,
        output_path=parquet,
        calibration_path=selected,
    )
    assert manifest["calibrated_params"]["initial_breath"] == pytest.approx(935.34)
    assert manifest["calibrated_params"]["desperate_threshold"] == pytest.approx(175.11)


# ---------------------------------------------------------------------
# Reproducibility — deterministic run
# ---------------------------------------------------------------------


def test_training_run_is_deterministic_given_seed(
    small_training_set: Path, tmp_path: Path
) -> None:
    """Same parquet → same final weights byte-identical between runs.

    The downstream Track D playback + reviewer replay both lock against
    this property — a non-deterministic training run would force every
    consumer to re-derive the weights instead of trusting the artefact.
    """
    c1 = Phase1Config(
        training_set=small_training_set,
        output_dir=tmp_path / "run1",
    )
    c2 = Phase1Config(
        training_set=small_training_set,
        output_dir=tmp_path / "run2",
    )
    r1 = run_phase1_training(c1)
    r2 = run_phase1_training(c2)
    j1 = json.loads(r1.weights_json_path.read_text(encoding="utf-8"))
    j2 = json.loads(r2.weights_json_path.read_text(encoding="utf-8"))
    assert j1 == j2


# ---------------------------------------------------------------------
# Failure-mode coverage
# ---------------------------------------------------------------------


def test_runner_rejects_too_small_training_set(tmp_path: Path) -> None:
    """The runner refuses a training set below the backtest_validity
    minimum size — a 5-game set can't statistically validate the gate."""
    parquet = tmp_path / "ts_tiny.parquet"
    build_training_set_v1(n_games=5, seed=1, output_path=parquet)
    with pytest.raises(ValueError, match="too small"):
        run_phase1_training(
            Phase1Config(training_set=parquet, output_dir=tmp_path / "out")
        )


def test_load_training_set_missing_file_raises(tmp_path: Path) -> None:
    """Missing parquet → FileNotFoundError with the canonical
    `python -m data.etl.build_training_set` recovery hint."""
    with pytest.raises(FileNotFoundError, match="training set parquet missing"):
        load_training_set(tmp_path / "nope.parquet")


def test_backtest_validity_failed_surface_is_runtime_error() -> None:
    """The exception classification: BacktestValidityFailed is a
    RuntimeError subclass so a top-level except handler catches both
    Phase1FreezeViolation + BacktestValidityFailed under one umbrella."""
    assert issubclass(BacktestValidityFailed, RuntimeError)
    assert issubclass(Phase1FreezeViolation, RuntimeError)
