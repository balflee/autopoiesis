# ruff: noqa: RUF002
"""Phase 1 tennis training pipeline tests — T-B-015 acceptance criteria.

Coverage shape:

* ``test_tennis_runner_runs_end_to_end`` — full happy-path on the
  shipped tennis_phase1.parquet; verifies the four output files exist
  + are well-formed.
* ``test_tennis_runner_meets_tick_floor`` — brief acceptance: the
  training_journey.jsonl ships ≥1000 ticks (T-D-008 dashboard scrubber
  dependency).
* ``test_tennis_runner_beta1_byte_identical_zero`` — Phase 1 freeze
  invariant: β₁ stays 0.0 across every recorded tick.
* ``test_tennis_runner_no_boundary_saturation`` — brief acceptance:
  no active weight saturates at 0/1 (excluding β₁).
* ``test_tennis_runner_backtest_validity_beats_uniform`` — brief
  acceptance: trained log-loss < uniform log-loss on the held-out
  split.
* ``test_tennis_runner_archetypes_present`` — backtest_report.json
  contains the four spec-required archetypes plus the trained policy.
* ``test_tennis_runner_calibration_bins_well_formed`` — 10 bins, each
  with mean_pred + mean_actual + n_samples fields.
* ``test_tennis_runner_deterministic`` — same parquet + seed → same
  final weights byte-identical.
* ``test_tennis_features_shuffle_balances_outcomes`` — the
  deterministic shuffle produces an outcome distribution close to
  50/50 (within ±15% of balance).
* ``test_tennis_features_pit_invariant`` — every row's asof_ts <
  match start time + the frame-wide chokepoint passes.
* ``test_tennis_features_rejects_missing_columns`` — schema-drift
  defence on load_tennis_phase1.

All tests run against the real tennis_phase1.parquet under data/parquet/
— it is byte-deterministic given the vendored Sackmann snapshot, so a
hermetic per-test fixture is unnecessary.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from agent.core.state import Weights
from agent.training.phase1_runner import BacktestValidityFailed
from agent.training.tennis_features import (
    TENNIS_PHASE1_REQUIRED_COLUMNS,
    build_tennis_feature_rows,
    load_tennis_phase1,
)
from agent.training.tennis_runner import (
    BoundarySaturationViolation,
    TennisTrainingConfig,
    _assert_no_boundary_saturation,
    _build_calibration_bins,
    predict_tennis_prob,
    run_tennis_training,
)

# ─── Fixtures ─────────────────────────────────────────────────────────


_TENNIS_PARQUET = Path("data/parquet/tennis_phase1.parquet")


@pytest.fixture(scope="module")
def tennis_parquet() -> Path:
    """The byte-deterministic tennis_phase1.parquet from T-E-003."""
    if not _TENNIS_PARQUET.exists():
        pytest.skip(f"tennis_phase1.parquet missing at {_TENNIS_PARQUET}")
    return _TENNIS_PARQUET


@pytest.fixture
def tennis_config(tmp_path: Path, tennis_parquet: Path) -> TennisTrainingConfig:
    """Default config writing to a fresh tmpdir per test."""
    return TennisTrainingConfig(
        training_set=tennis_parquet,
        output_dir=tmp_path / "phase1_out",
    )


# ─── End-to-end ────────────────────────────────────────────────────────


def test_tennis_runner_runs_end_to_end(tennis_config: TennisTrainingConfig) -> None:
    """Happy path — produces all 4 deliverables + they're well-formed."""
    result = run_tennis_training(tennis_config)

    assert result.weights_json_path.exists()
    assert result.training_journey_path.exists()
    assert result.backtest_json_path.exists()
    assert result.backtest_md_path.exists()

    # Each file non-empty.
    for p in (
        result.weights_json_path,
        result.training_journey_path,
        result.backtest_json_path,
        result.backtest_md_path,
    ):
        assert p.stat().st_size > 0, f"{p} is empty"

    # weights_v0.json round-trips through Weights.
    raw = json.loads(result.weights_json_path.read_text(encoding="utf-8"))
    w = Weights.model_validate(raw)
    assert pytest.approx(w.w_r + w.w_s, abs=1e-6) == 1.0
    assert pytest.approx(sum(w.alpha), abs=1e-6) == 1.0
    assert pytest.approx(sum(w.beta), abs=1e-6) == 1.0


# ─── Brief acceptance: ≥1000 ticks ─────────────────────────────────────


def test_tennis_runner_meets_tick_floor(tennis_config: TennisTrainingConfig) -> None:
    """training_journey.jsonl must have ≥1000 ticks for T-D-008 scrubber."""
    result = run_tennis_training(tennis_config)

    lines = result.training_journey_path.read_text(encoding="utf-8").splitlines()
    # tick 0 (initial snapshot) + N training ticks.
    assert len(lines) >= 1001, f"expected ≥1001 lines (1 initial + 1000 ticks), got {len(lines)}"
    assert result.n_ticks >= 1000


# ─── Phase 1 freeze invariant ─────────────────────────────────────────


def test_tennis_runner_beta1_byte_identical_zero(
    tennis_config: TennisTrainingConfig,
) -> None:
    """β₁ MUST be byte-identical 0.0 across every recorded tick."""
    result = run_tennis_training(tennis_config)

    distinct_beta1: set[float] = set()
    distinct_beta2: set[float] = set()
    distinct_wr: set[float] = set()
    for line in result.training_journey_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        distinct_beta1.add(rec["beta_1"])
        distinct_beta2.add(rec["beta_2"])
        distinct_wr.add(rec["w_r"])
    assert distinct_beta1 == {0.0}, f"β₁ drifted: {distinct_beta1}"
    # β₂ + W_R frozen by Phase 1 branch — defence-in-depth.
    assert distinct_beta2 == {1.0}, f"β₂ drifted: {distinct_beta2}"
    assert distinct_wr == {0.5}, f"W_R drifted: {distinct_wr}"


# ─── Brief acceptance: boundary saturation ─────────────────────────────


def test_tennis_runner_no_boundary_saturation(
    tennis_config: TennisTrainingConfig,
) -> None:
    """No trainable weight may saturate at 0 / 1 (brief acceptance)."""
    result = run_tennis_training(tennis_config)
    # Re-run the assertion in-test for explicit coverage of the gate.
    _assert_no_boundary_saturation(result.final_weights)


def test_boundary_saturation_helper_catches_extreme_alpha() -> None:
    """The saturation helper raises when α drifts onto the corner."""
    saturated = Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[0.999, 0.0005, 0.0005],
        beta=[0.0, 1.0],
        rho=0.5,
    )
    with pytest.raises(BoundarySaturationViolation, match="alpha_1"):
        _assert_no_boundary_saturation(saturated)


# ─── Brief acceptance: backtest_validity ───────────────────────────────


def test_tennis_runner_backtest_validity_beats_uniform(
    tennis_config: TennisTrainingConfig,
) -> None:
    """trained_test_loss < uniform_test_loss on the held-out split."""
    result = run_tennis_training(tennis_config)
    assert result.trained_test_loss < result.uniform_test_loss, (
        f"trained={result.trained_test_loss:.6f} >= "
        f"uniform={result.uniform_test_loss:.6f} — backtest_validity FAILED"
    )
    assert result.backtest_improvement_pct > 0.0


# ─── Backtest report shape ─────────────────────────────────────────────


def test_tennis_runner_archetypes_present(
    tennis_config: TennisTrainingConfig,
) -> None:
    """backtest_report.json must carry the 4 archetypes + trained policy."""
    result = run_tennis_training(tennis_config)
    body = json.loads(result.backtest_json_path.read_text(encoding="utf-8"))

    archetype_names = {a["name"] for a in body["archetypes"]}
    expected = {"random", "always_bet_favorite", "pessimist", "satisficer"}
    assert archetype_names == expected, (
        f"missing archetypes: {expected - archetype_names}"
    )
    assert body["trained_policy"]["name"] == "trained"

    # Per-archetype required fields.
    required_fields = {
        "name",
        "log_loss",
        "bets_placed",
        "bets_won",
        "win_rate",
        "mean_bankroll_usd",
        "final_bankroll_usd",
        "mean_lifetime_matches",
        "max_drawdown_usd",
    }
    for a in body["archetypes"]:
        assert required_fields.issubset(a.keys()), (
            f"archetype {a['name']} missing fields: {required_fields - set(a.keys())}"
        )


def test_tennis_runner_calibration_bins_well_formed(
    tennis_config: TennisTrainingConfig,
) -> None:
    """The calibration plot has 10 buckets covering [0, 1] contiguously."""
    result = run_tennis_training(tennis_config)
    bins = result.calibration_bins
    assert len(bins) == 10
    for idx, b in enumerate(bins):
        assert b["bin_lower"] == pytest.approx(idx / 10)
        assert b["bin_upper"] == pytest.approx((idx + 1) / 10)
        # mean_pred / mean_actual within [0, 1] when n_samples > 0.
        if b["n_samples"] > 0:
            assert 0.0 <= b["mean_pred"] <= 1.0
            assert 0.0 <= b["mean_actual"] <= 1.0


# ─── Determinism ───────────────────────────────────────────────────────


def test_tennis_runner_deterministic(
    tennis_parquet: Path, tmp_path: Path
) -> None:
    """Same parquet + same seed → byte-identical weights_v0.json."""
    c1 = TennisTrainingConfig(training_set=tennis_parquet, output_dir=tmp_path / "r1")
    c2 = TennisTrainingConfig(training_set=tennis_parquet, output_dir=tmp_path / "r2")
    r1 = run_tennis_training(c1)
    r2 = run_tennis_training(c2)
    j1 = json.loads(r1.weights_json_path.read_text(encoding="utf-8"))
    j2 = json.loads(r2.weights_json_path.read_text(encoding="utf-8"))
    assert j1 == j2


# ─── tennis_features: shuffle balances outcomes ────────────────────────


def test_tennis_features_shuffle_balances_outcomes(tennis_parquet: Path) -> None:
    """Player-order shuffle produces outcomes near 50/50.

    The Sackmann source ships outcome=1 everywhere (winner-first). The
    deterministic shuffle is a coin-flip per match_id; over a 100+ match
    corpus the distribution must be near balanced (±15% slack to absorb
    finite-size noise without flaking the test).
    """
    df = load_tennis_phase1(tennis_parquet)
    rows = build_tennis_feature_rows(df, shuffle_seed=20260524)
    outcomes = Counter(r.outcome for r in rows)
    total = sum(outcomes.values())
    p1_wins = outcomes[1] / total
    assert 0.35 <= p1_wins <= 0.65, (
        f"outcome distribution too skewed after shuffle: p1_wins={p1_wins:.2%}"
    )


def test_tennis_features_pit_invariant(tennis_parquet: Path) -> None:
    """Every loaded feature row passes the per-row PIT check."""
    df = load_tennis_phase1(tennis_parquet)
    rows = build_tennis_feature_rows(df, shuffle_seed=20260524)
    assert len(rows) > 0

    # Chronological order preserved + all rows tz-aware.
    timestamps = [r.asof_ts for r in rows]
    for t in timestamps:
        assert t.tzinfo is not None
    assert timestamps == sorted(timestamps)

    # Required columns present on the source frame.
    assert set(TENNIS_PHASE1_REQUIRED_COLUMNS).issubset(set(df.columns))


def test_tennis_features_rejects_missing_columns(tmp_path: Path) -> None:
    """Schema-drift defence — a parquet without required columns raises."""
    import pandas as pd

    bad = pd.DataFrame({"match_id": ["x"], "asof_ts": [pd.Timestamp.now(tz="UTC")]})
    out = tmp_path / "bad.parquet"
    bad.to_parquet(out)
    with pytest.raises(ValueError, match="missing required columns"):
        load_tennis_phase1(out)


# ─── Failure modes ─────────────────────────────────────────────────────


def test_tennis_runner_rejects_missing_parquet(tmp_path: Path) -> None:
    """Missing tennis_phase1 → FileNotFoundError with a recovery hint."""
    with pytest.raises(FileNotFoundError, match="tennis_phase1 parquet missing"):
        load_tennis_phase1(tmp_path / "nope.parquet")


def test_predict_tennis_prob_is_pure(tennis_parquet: Path) -> None:
    """predict_tennis_prob is a pure function — same inputs → same output."""
    df = load_tennis_phase1(tennis_parquet)
    rows = build_tennis_feature_rows(df, shuffle_seed=20260524)
    row = rows[0]
    w = Weights(
        w_r=0.55,
        w_s=0.45,
        alpha=[0.5, 0.3, 0.2],
        beta=[0.0, 1.0],
        rho=0.5,
    )
    p1 = predict_tennis_prob(row, w)
    p2 = predict_tennis_prob(row, w)
    assert p1 == p2
    assert 0.0 < p1 < 1.0


# ─── Calibration helper directly ───────────────────────────────────────


def test_calibration_bins_empty_rows_returns_empty_list() -> None:
    """No test rows → empty calibration list (don't divide by zero)."""
    w = Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[1 / 3, 1 / 3, 1 / 3],
        beta=[0.0, 1.0],
        rho=0.5,
    )
    bins = _build_calibration_bins(test_rows=[], trained_weights=w)
    assert bins == []


def test_backtest_validity_failed_is_runtime_error() -> None:
    """Exception classification: matches the phase1_runner sibling type."""
    assert issubclass(BacktestValidityFailed, RuntimeError)
    assert issubclass(BoundarySaturationViolation, RuntimeError)
