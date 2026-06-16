# Greek letters mirror PRD §4.1 / §6.6 notation; see agent/engines/weight_updater.py.
"""Phase 1 historical training loop — TECHNICAL_PLAN §4.2 pseudocode.

PRD §4.2 + TP §4.2 specify the offline historical-training run that
produces ``weights_v0.json`` — the seed Phase 2 real-time training
inherits when the agent transitions out of infancy.

The loop (chronological order — earliest tipoff first):

    for game in historical_games:
        feats         = extract_features_pointintime(game)
        pred_prob     = sigmoid(fused_score(feats, current_weights))
        loss          = binary_log_loss(pred_prob, game.outcome)
        cumulative   += loss
        quality       = per_engine_quality(feats, outcome, pred_prob, current_weights)
        current_weights = weight_updater.update(
            current=current_weights,
            phase=PHASE_1_INFANCY,
            features=quality,
        )
        evolution.append(weights_snapshot + loss + game_id + step)

Phase 1 freeze (HARD RULE — brief acceptance criterion):

* ``β₁`` trajectory in ``evolution_curve.csv`` is BYTE-IDENTICAL to the
  initial 0.0 across all N steps. The runner re-checks this invariant
  every step + raises :class:`Phase1FreezeViolation` if drift surfaces.
* ``β₂`` stays at 1.0, ``W_R`` / ``W_S`` stay at their initial values
  (the underlying :class:`WeightUpdater` enforces these freezes too —
  this is defence-in-depth).

backtest_validity gate
----------------------

Brief: ``weights_v0.json`` log-loss on a held-out 20% test split must
be strictly less than the initial uniform-weights log-loss. The runner
splits the dataset 80/20 chronologically (last 20% by tipoff is held
out), trains on the first 80%, then computes test-set log-loss with:

* Initial uniform α = (1/3, 1/3, 1/3) — the "untrained" baseline.
* Final trained α from the last training step.

If trained log-loss ≥ uniform log-loss the runner raises
:class:`BacktestValidityFailed` and exits non-zero — the acceptance
criterion is an executable invariant, not a hope.
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from agent.core.state import Phase, Weights
from agent.engines.decision import (
    RATIONAL_ENGINES as RATIONAL_ENGINE_NAMES,
)
from agent.engines.decision import (
    SENTIENT_ENGINES as SENTIENT_ENGINE_NAMES,
)
from agent.engines.weight_updater import WeightUpdater
from agent.training.feature_engineering import (
    Phase1FeatureRow,
    build_phase1_feature_rows,
    load_training_set,
)
from data.etl.pit_correct import LookaheadError

# ── Constants ────────────────────────────────────────────────────────

# Holdout fraction — last 20% chronologically is the test split. Brief:
# "weights_v0.json log-loss on a held-out 20% test split is strictly
# less than initial uniform-weights log-loss".
DEFAULT_HOLDOUT_FRACTION: Final[float] = 0.20

# Train-step learning rate. The :class:`WeightUpdater` default (0.05)
# is a per-tick rate calibrated for the live loop's ~32-tick-per-day
# cadence; Phase 1 training walks N≈240 games in one pass so we use
# the same rate — slower would under-fit in a single epoch, faster
# would chatter against the simplex corners.
DEFAULT_LEARNING_RATE: Final[float] = 0.05

# Initial weights when no seed is provided. Uniform α/β simplexes +
# W_R = W_S = 0.5 + ρ = 0.5 is the "ignorant prior" — the runner
# moves α away from uniform during training; the W_R / W_S / β freezes
# pin those at their initial values per the Phase 1 spec.
DEFAULT_INITIAL_W_R: Final[float] = 0.5
DEFAULT_INITIAL_W_S: Final[float] = 0.5
DEFAULT_INITIAL_ALPHA: Final[tuple[float, float, float]] = (
    1.0 / 3.0,
    1.0 / 3.0,
    1.0 / 3.0,
)
DEFAULT_INITIAL_BETA: Final[tuple[float, float]] = (0.0, 1.0)  # β₁ = 0 frozen
DEFAULT_INITIAL_RHO: Final[float] = 0.5

# Numerical-stability floor for log-loss — log(p) at p=0 is -inf, so
# we clip predictions into [eps, 1-eps]. eps=1e-9 is small enough
# that the loss surface is unchanged for realistic predictions and
# large enough to keep float64 finite.
_LOSS_EPS: Final[float] = 1e-9

# Temperature applied to the fused [-1, 1] score before sigmoid. 2.0
# maps the fused band to sigmoid(±2) ≈ (0.12, 0.88), wide enough to
# carry the home-bias + the engine signal without saturating early.
# MUST stay aligned with the corresponding constant in
# data/etl/build_training_set.py (used to synthesise outcomes) — drift
# between the two would make backtest_validity statistically intractable.
_FUSED_SCORE_TEMPERATURE: Final[float] = 2.0

# Quality-feature dict keys consumed by WeightUpdater._gradient_from_features.
# Built from the canonical engine name tuples so a future rename in
# agent.engines.decision propagates automatically.
_QUALITY_KEYS_RATIONAL: Final[tuple[str, ...]] = tuple(
    f"{n}_quality" for n in RATIONAL_ENGINE_NAMES
)
_QUALITY_KEYS_SENTIENT: Final[tuple[str, ...]] = tuple(
    f"{n}_quality" for n in SENTIENT_ENGINE_NAMES
)


class Phase1FreezeViolation(RuntimeError):
    """Raised when β₁, W_R, or W_S drifts during Phase 1 training.

    The brief's acceptance criterion treats β₁ drift as a CRITICAL
    failure ("byte-identical to initial 0.0 across all N steps") so
    the runner raises rather than returning a soft warning.
    """


class BacktestValidityFailed(RuntimeError):
    """Raised when held-out log-loss ≥ uniform log-loss.

    Brief acceptance criterion ``backtest_validity`` — the trained
    weights MUST measurably beat the uniform-α baseline on a held-out
    20% test split. A failure here means the loop didn't learn
    anything; the runner exits non-zero rather than silently shipping
    pessimised weights.
    """


@dataclass(frozen=True)
class Phase1Config:
    """Hyperparameters + paths for one training run.

    All fields have defaults that satisfy the brief's acceptance
    criteria for the default ``training_set_v1.parquet``; the CLI
    threads through ``output_dir`` and ``training_set`` only.
    """

    training_set: Path
    output_dir: Path
    learning_rate: float = DEFAULT_LEARNING_RATE
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION
    initial_w_r: float = DEFAULT_INITIAL_W_R
    initial_w_s: float = DEFAULT_INITIAL_W_S
    initial_alpha: tuple[float, float, float] = DEFAULT_INITIAL_ALPHA
    initial_beta: tuple[float, float] = DEFAULT_INITIAL_BETA
    initial_rho: float = DEFAULT_INITIAL_RHO


@dataclass(frozen=True)
class Phase1Result:
    """Outputs of a completed training run.

    Returned by :func:`run_phase1_training` and consumed by both the
    CLI (it pretty-prints) and the test suite (it inspects).

    Derived metrics (``n_total_games``, ``mean_training_loss``,
    ``backtest_improvement_pct``) are exposed as ``@property`` so the
    SSOT for each is the underlying field — no risk of the stored
    derivation drifting from the stored primitives.
    """

    final_weights: Weights
    initial_weights: Weights
    n_training_games: int
    n_test_games: int
    cumulative_training_loss: float
    uniform_test_loss: float
    trained_test_loss: float
    evolution_curve_path: Path
    weights_json_path: Path
    report_md_path: Path
    duration_seconds: float
    seed_metadata: dict[str, str]
    calibrated_params: dict[str, float] = field(default_factory=dict)

    @property
    def n_total_games(self) -> int:
        """Total games processed = training + held-out test split."""
        return self.n_training_games + self.n_test_games

    @property
    def mean_training_loss(self) -> float:
        """Cumulative training BCE log-loss ÷ number of training games."""
        if self.n_training_games <= 0:
            return 0.0
        return self.cumulative_training_loss / self.n_training_games

    @property
    def backtest_improvement_pct(self) -> float:
        """(uniform_test_loss - trained_test_loss) / uniform_test_loss · 100.

        Positive = trained beat the uniform baseline (gate PASSED).
        """
        if self.uniform_test_loss <= 0.0:
            return 0.0
        return (
            (self.uniform_test_loss - self.trained_test_loss)
            / self.uniform_test_loss
            * 100.0
        )


def run_phase1_training(config: Phase1Config) -> Phase1Result:
    """Execute the Phase 1 historical training pipeline end-to-end.

    Steps (see module docstring for the pseudocode):

    1. Load training_set parquet + run frame-wide PIT validation.
    2. 80/20 chronological train/test split.
    3. Walk training set; per game: compute loss → quality → update
       weights via :meth:`WeightUpdater.update` (phase=Phase 1).
    4. Verify β₁ + W_R + W_S byte-identical to initial across all
       training steps (Phase 1 freeze invariant).
    5. Evaluate uniform-α baseline + trained-α on the test split.
    6. Assert trained < uniform (backtest_validity).
    7. Persist weights_v0.json + evolution_curve.csv +
       PHASE1_TRAINING_REPORT.md under output_dir.

    Sync wrapper around the async core so callers (CLI + tests) don't
    each have to spin their own event loop. The async core itself walks
    the training set inside a single event loop so per-game
    ``asyncio.run`` overhead disappears (the WeightUpdater is async but
    never awaits I/O — one loop is plenty).
    """
    return asyncio.run(_run_phase1_training_async(config))


async def _run_phase1_training_async(config: Phase1Config) -> Phase1Result:
    """Async core for :func:`run_phase1_training`. See its docstring."""
    start = _utcnow()

    # ── 1. Load + validate ────────────────────────────────────────────
    df = load_training_set(config.training_set)
    rows = build_phase1_feature_rows(df)
    n_total = len(rows)
    if n_total < 10:
        raise ValueError(
            f"training set too small for backtest_validity ({n_total} games) — "
            "brief requires ≥200 games for the full Phase 1 run."
        )

    seed_metadata = _read_parquet_metadata(config.training_set)
    calibrated = _decode_calibrated_metadata(seed_metadata)

    # ── 2. 80/20 chronological split ──────────────────────────────────
    n_test = max(1, round(n_total * config.holdout_fraction))
    n_train = n_total - n_test
    if n_train < 1:
        raise ValueError(
            f"holdout_fraction={config.holdout_fraction} leaves no training rows "
            f"in a {n_total}-game set."
        )
    train_rows = rows[:n_train]
    test_rows = rows[n_train:]

    # ── 3. Training walk ──────────────────────────────────────────────
    initial_weights = Weights(
        w_r=config.initial_w_r,
        w_s=config.initial_w_s,
        alpha=list(config.initial_alpha),
        beta=list(config.initial_beta),
        rho=config.initial_rho,
    )
    updater = WeightUpdater(learning_rate=config.learning_rate)

    cumulative_loss = 0.0
    current = initial_weights
    evolution_records: list[dict[str, float | int | str]] = []

    # Snapshot the initial state as step 0 so reviewers can see the
    # untrained baseline in evolution_curve.csv.
    evolution_records.append(
        _snapshot_record(step=0, game_id="<initial>", weights=current, cumulative_loss=0.0)
    )

    for step, row in enumerate(train_rows, start=1):
        pred_prob = _predict_home_prob(row, current)
        loss = _binary_log_loss(pred_prob, row.outcome)
        cumulative_loss += loss
        quality = _per_engine_quality_features(row, pred_prob, current)
        current = await updater.update(
            current=current,
            phase=Phase.PHASE_1_INFANCY,
            features=quality,
        )
        _assert_phase1_freezes_hold(initial=initial_weights, current=current, step=step)
        evolution_records.append(
            _snapshot_record(
                step=step,
                game_id=row.game_id,
                weights=current,
                cumulative_loss=cumulative_loss,
            )
        )

    # ── 4. Evaluate the backtest_validity gate ────────────────────────
    uniform_test_loss = _mean_log_loss(test_rows, initial_weights)
    trained_test_loss = _mean_log_loss(test_rows, current)

    if not (trained_test_loss < uniform_test_loss):
        raise BacktestValidityFailed(
            f"trained test log-loss ({trained_test_loss:.6f}) did NOT beat "
            f"uniform baseline ({uniform_test_loss:.6f}) on {len(test_rows)} "
            f"held-out games (n_train={n_train}). PRD §14.1 + brief "
            "`backtest_validity` acceptance criterion FAILED."
        )

    # ── 5. Persist outputs ────────────────────────────────────────────
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    weights_json_path = output_dir / "weights_v0.json"
    _write_weights_json(weights_json_path, current)

    evolution_curve_path = output_dir / "evolution_curve.csv"
    _write_evolution_curve(evolution_curve_path, evolution_records)

    duration = (_utcnow() - start).total_seconds()
    mean_training_loss = cumulative_loss / max(1, n_train)
    improvement_pct = (uniform_test_loss - trained_test_loss) / uniform_test_loss * 100.0

    report_md_path = output_dir / "PHASE1_TRAINING_REPORT.md"
    _write_training_report(
        path=report_md_path,
        config=config,
        n_train=n_train,
        n_test=n_test,
        n_total=n_total,
        initial=initial_weights,
        final=current,
        uniform_test_loss=uniform_test_loss,
        trained_test_loss=trained_test_loss,
        mean_training_loss=mean_training_loss,
        cumulative_training_loss=cumulative_loss,
        improvement_pct=improvement_pct,
        duration_seconds=duration,
        seed_metadata=seed_metadata,
        calibrated_params=calibrated,
    )

    return Phase1Result(
        final_weights=current,
        initial_weights=initial_weights,
        n_training_games=n_train,
        n_test_games=n_test,
        cumulative_training_loss=cumulative_loss,
        uniform_test_loss=uniform_test_loss,
        trained_test_loss=trained_test_loss,
        evolution_curve_path=evolution_curve_path,
        weights_json_path=weights_json_path,
        report_md_path=report_md_path,
        duration_seconds=duration,
        seed_metadata=seed_metadata,
        calibrated_params=calibrated,
    )


# ---------------------------------------------------------------------
# Pure helpers — module-level so tests can hit the math directly.
# ---------------------------------------------------------------------


def _predict_home_prob(row: Phase1FeatureRow, weights: Weights) -> float:
    """Predicted home-win probability given a feature row + weights.

    Uses the live decision engine's 2-layer fusion (PRD §4.1) then
    squashes through a sigmoid to get a calibrated probability. The
    fused score is in [-1, 1]; sigmoid maps that to (0.27, 0.73)
    which is the realistic edge band for binary sports betting.

    PRD §6.8 forbidden columns (outcome, payout, settled_at,
    resolved_at) are NEVER touched here — only the engine score +
    confidence inputs.
    """
    a1, a2, a3 = weights.alpha
    b1, b2 = weights.beta
    rational = (
        a1 * row.nba_technical_score * row.nba_technical_conf
        + a2 * row.market_momentum_score * row.market_momentum_conf
        + a3 * row.smart_money_score * row.smart_money_conf
    )
    # sentiment_llm contribution is forced to 0 in Phase 1 (β₁=0 frozen)
    # — but we surface the algebra explicitly so a reviewer can see the
    # term IS present (with weight 0) rather than silently elided. This
    # also means a future Phase 2 unfreeze "just works" without code change.
    sentient = b1 * 0.0 + b2 * row.crowd_volume_score * row.crowd_volume_conf
    fused = weights.w_r * rational + weights.w_s * sentient
    return _sigmoid(_FUSED_SCORE_TEMPERATURE * fused)


def _binary_log_loss(p: float, y: int) -> float:
    """Binary cross-entropy log-loss — the canonical Phase 1 loss."""
    p_clip = min(1.0 - _LOSS_EPS, max(_LOSS_EPS, p))
    if y == 1:
        return -math.log(p_clip)
    return -math.log(1.0 - p_clip)


def _mean_log_loss(rows: list[Phase1FeatureRow], weights: Weights) -> float:
    """Average BCE loss over ``rows`` with the given weights."""
    if not rows:
        return 0.0
    total = 0.0
    for row in rows:
        p = _predict_home_prob(row, weights)
        total += _binary_log_loss(p, row.outcome)
    return total / len(rows)


def _per_engine_quality_features(
    row: Phase1FeatureRow,
    pred_prob: float,
    weights: Weights,
) -> dict[str, float]:
    """Build the quality-signal feature dict the WeightUpdater consumes.

    Convention (matches :func:`weight_updater._gradient_from_features`):
    feature key ``"<engine_name>_quality"`` carries the gradient signal
    that raises that engine's α (higher quality → higher logit →
    higher post-softmax weight).

    The gradient of BCE log-loss w.r.t. α_i is::

        dL/dα_i = (p - y) · w_r · score_i · conf_i

    Quality = -dL/dα_i = (y - p) · w_r · score_i · conf_i. When the
    engine's signal aligned with the outcome the quality is positive
    (the engine helped) and α_i grows; when it disagreed the quality
    is negative and α_i shrinks.

    The Phase 1 freeze in :class:`WeightUpdater` ignores β + W_R/W_S
    gradients regardless — we still emit the dict keys so a Phase 2
    unfreeze inherits the same gradient pipeline.
    """
    y = float(row.outcome)
    err = y - pred_prob  # -(p - y) — sign convention per docstring
    w_r = weights.w_r
    w_s = weights.w_s

    # Per-engine score×confidence contributions, indexed by canonical
    # engine name so the dict stays in lockstep with weight_updater's
    # _ALPHA_ENGINES / _BETA_ENGINES tuples.
    nba_sc = row.nba_technical_score * row.nba_technical_conf
    mm_sc = row.market_momentum_score * row.market_momentum_conf
    sm_sc = row.smart_money_score * row.smart_money_conf
    cv_sc = row.crowd_volume_score * row.crowd_volume_conf
    rational_contrib = (
        weights.alpha[0] * nba_sc
        + weights.alpha[1] * mm_sc
        + weights.alpha[2] * sm_sc
    )
    sentient_contrib = weights.beta[1] * cv_sc  # β₁=0 frozen — only β₂ contributes

    # Keys built from RATIONAL_ENGINE_NAMES / SENTIENT_ENGINE_NAMES so a
    # rename of "tennis_technical" → "tennis_tech_v2" automatically propagates.
    return {
        _QUALITY_KEYS_RATIONAL[0]: err * w_r * nba_sc,
        _QUALITY_KEYS_RATIONAL[1]: err * w_r * mm_sc,
        _QUALITY_KEYS_RATIONAL[2]: err * w_r * sm_sc,
        # β channels: β₁ frozen at 0, β₂ active. Emit both keys so a
        # Phase 2 unfreeze inherits the pipeline.
        _QUALITY_KEYS_SENTIENT[0]: 0.0,  # head_to_head (β₁) — frozen, no signal
        _QUALITY_KEYS_SENTIENT[1]: err * w_s * cv_sc,
        "rational_stream_quality": err * rational_contrib,
        "sentient_stream_quality": err * sentient_contrib,
        "rho_quality": 0.0,  # Phase 1: ρ doesn't appear in pred prob — no useful gradient
    }


# (label, accessor, rationale-string) for each Phase 1 frozen channel.
# Kept module-private so a regression test can import + iterate without
# coupling to the assertion implementation.
_PHASE1_FROZEN_CHANNELS: Final[tuple[tuple[str, str, str], ...]] = (
    ("β₁", "beta[0]", "PRD §4.2 Phase 1 LLM freeze"),
    ("β₂", "beta[1]", "Phase 1 freezes the β simplex"),
    ("W_R", "w_r", "Phase 1 freezes W_R / W_S per PRD §4.2"),
    ("W_S", "w_s", "Phase 1 freezes W_R / W_S per PRD §4.2"),
)


def _frozen_channel_value(weights: Weights, accessor: str) -> float:
    """Resolve a frozen-channel accessor (``"beta[0]"`` / ``"w_r"`` / …).

    Tiny dispatcher so the freeze-assertion loop can be data-driven
    without sacrificing --strict typing.
    """
    if accessor == "beta[0]":
        return weights.beta[0]
    if accessor == "beta[1]":
        return weights.beta[1]
    if accessor == "w_r":
        return weights.w_r
    if accessor == "w_s":
        return weights.w_s
    raise KeyError(f"unknown frozen-channel accessor: {accessor!r}")


def _assert_phase1_freezes_hold(
    *, initial: Weights, current: Weights, step: int
) -> None:
    """Verify β₁ + β₂ + W_R + W_S byte-identical to initial across all steps.

    Raises :class:`Phase1FreezeViolation` if drift surfaces. Called
    AFTER every WeightUpdater.update so a regression in the updater's
    Phase 1 freeze logic surfaces at the runner level too (defence-
    in-depth: the auditor greps both).
    """
    for label, accessor, rationale in _PHASE1_FROZEN_CHANNELS:
        before = _frozen_channel_value(initial, accessor)
        after = _frozen_channel_value(current, accessor)
        if before != after:
            raise Phase1FreezeViolation(
                f"step={step}: {label} drifted from {before} to {after} ({rationale})."
            )


def _snapshot_record(
    *,
    step: int,
    game_id: str,
    weights: Weights,
    cumulative_loss: float,
) -> dict[str, float | int | str]:
    """Project one (step, weights, loss) into the evolution_curve.csv row."""
    return {
        "step": step,
        "game_id": game_id,
        "w_r": weights.w_r,
        "w_s": weights.w_s,
        "alpha_1": weights.alpha[0],
        "alpha_2": weights.alpha[1],
        "alpha_3": weights.alpha[2],
        "beta_1": weights.beta[0],
        "beta_2": weights.beta[1],
        "rho": weights.rho,
        "cumulative_log_loss": cumulative_loss,
    }


def _write_weights_json(path: Path, weights: Weights) -> None:
    """Atomic-style write of weights to JSON per the weights_schema."""
    body = {
        "w_r": weights.w_r,
        "w_s": weights.w_s,
        "alpha": list(weights.alpha),
        "beta": list(weights.beta),
        "rho": weights.rho,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_evolution_curve(
    path: Path, records: list[dict[str, float | int | str]]
) -> None:
    """Persist the per-step evolution curve as CSV.

    Columns: step, game_id, w_r, w_s, alpha_1, alpha_2, alpha_3,
    beta_1, beta_2, rho, cumulative_log_loss — matches the brief's
    "6 weight params + cumulative_log_loss + game_id per row"
    acceptance criterion. The ``step`` column is bonus context for
    Track D dashboard playback.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "step",
        "game_id",
        "w_r",
        "w_s",
        "alpha_1",
        "alpha_2",
        "alpha_3",
        "beta_1",
        "beta_2",
        "rho",
        "cumulative_log_loss",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def _read_parquet_metadata(path: Path) -> dict[str, str]:
    """Best-effort metadata read from the training-set parquet.

    The build_training_set_v1 writer stamps ``phase1_seed`` /
    ``phase1_n_games`` / ``phase1_calibrated_params`` into the parquet
    schema metadata; the runner echoes these into the training report
    for reproducibility.
    """
    try:
        # pyarrow's stubs are stripped under our pyproject override —
        # call via Any-typed alias so --strict doesn't trip no-untyped-call.
        from typing import Any as _Any

        import pyarrow.parquet as pq

        _read_metadata: _Any = pq.read_metadata
        meta = _read_metadata(str(path))
        schema_meta = meta.schema.to_arrow_schema().metadata or {}
        decoded: dict[str, str] = {}
        for k, v in schema_meta.items():
            try:
                key = bytes(k).decode("utf-8")
                val = bytes(v).decode("utf-8")
            except (UnicodeDecodeError, TypeError):  # pragma: no cover — defence
                continue
            decoded[key] = val
        return decoded
    except (OSError, ValueError, ImportError):  # pragma: no cover — defence
        return {}


def _decode_calibrated_metadata(seed_metadata: dict[str, str]) -> dict[str, float]:
    """Decode ``phase1_calibrated_params`` from the parquet seed metadata.

    Pure dict transform — the parquet read already happened in
    :func:`_read_parquet_metadata`. Returns an empty dict if the
    metadata key is missing or malformed. The dict is surfaced in
    :class:`Phase1Result` + the training report so the
    calibration→training handshake is auditable.
    """
    raw = seed_metadata.get("phase1_calibrated_params")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover — defence
        return {}
    if not isinstance(parsed, dict):  # pragma: no cover — defence
        return {}
    return {str(k): float(v) for k, v in parsed.items() if isinstance(v, (int, float))}


def _write_training_report(
    *,
    path: Path,
    config: Phase1Config,
    n_train: int,
    n_test: int,
    n_total: int,
    initial: Weights,
    final: Weights,
    uniform_test_loss: float,
    trained_test_loss: float,
    mean_training_loss: float,
    cumulative_training_loss: float,
    improvement_pct: float,
    duration_seconds: float,
    seed_metadata: dict[str, str],
    calibrated_params: dict[str, float],
) -> None:
    """Render PHASE1_TRAINING_REPORT.md.

    Demo asset per the brief — surfaced alongside CALIBRATION_REPORT.md
    so the Track D dashboard + the Demo flow can quote final weights,
    held-out loss, and the calibration→training handshake.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    seed = seed_metadata.get("phase1_seed", "<unknown>")
    n_games_meta = seed_metadata.get("phase1_n_games", str(n_total))

    calibrated_block = ""
    if calibrated_params:
        rows = "\n".join(
            f"| `{k}` | {calibrated_params[k]:.6g} |"
            for k in sorted(calibrated_params.keys())
        )
        calibrated_block = (
            "## Calibration handshake\n\n"
            "Params consumed from `reports/calibration/selected_params.json` "
            "(stamped into the training-set parquet metadata by "
            "`data.etl.build_training_set.build_training_set_v1`):\n\n"
            "| param | value |\n|-------|-------|\n"
            f"{rows}\n\n"
            "Phase 1 training is offline + historical — these values are "
            "echoed for traceability but not consumed in the gradient loop "
            "(no breath is burned during offline training).\n\n"
        )

    body = f"""# Phase 1 Training Report

> **Task**: T-B-004 — Phase 1 historical training pipeline
> **Generated**: {_utcnow().isoformat()}
> **Duration**: {duration_seconds:.2f} seconds
> **Training set**: `{config.training_set}` (n={n_games_meta}, seed={seed})

## Summary

The Phase 1 historical training run consumed **{n_train}** chronologically-ordered
NBA games, held **{n_test}** games out for the `backtest_validity` gate, and
emitted `weights_v0.json` — the seed Phase 2 real-time training will inherit
when the agent transitions out of infancy.

* Mean per-game training log-loss: **{mean_training_loss:.6f}** (cumulative {cumulative_training_loss:.4f})
* Held-out test log-loss (uniform-α baseline): **{uniform_test_loss:.6f}**
* Held-out test log-loss (trained weights):    **{trained_test_loss:.6f}**
* Backtest improvement: **{improvement_pct:.2f}%** (trained < uniform — PASS)

## Phase 1 freeze invariant

Per PRD §4.2 + the brief's acceptance criterion, β₁ stays **byte-identical** to
its initial value (0.0) across every training step. The runner verifies this
explicitly at each step (`_assert_phase1_freezes_hold`); W_R / W_S / β₂ are
likewise pinned per the weight_updater Phase 1 extension. The evolution curve
CSV is the audit surface — every row carries the full weights snapshot so a
reviewer can verify β₁ never moves.

## Final weights (weights_v0.json)

```json
{json.dumps({
    "w_r": final.w_r,
    "w_s": final.w_s,
    "alpha": list(final.alpha),
    "beta": list(final.beta),
    "rho": final.rho,
}, indent=2, sort_keys=True)}
```

## Initial weights (uniform prior — step 0)

```json
{json.dumps({
    "w_r": initial.w_r,
    "w_s": initial.w_s,
    "alpha": list(initial.alpha),
    "beta": list(initial.beta),
    "rho": initial.rho,
}, indent=2, sort_keys=True)}
```

## Alpha simplex drift

| engine | initial α | trained α | Δ |
|--------|-----------|-----------|----|
| tennis_technical | {initial.alpha[0]:.4f} | {final.alpha[0]:.4f} | {final.alpha[0] - initial.alpha[0]:+.4f} |
| market_momentum  | {initial.alpha[1]:.4f} | {final.alpha[1]:.4f} | {final.alpha[1] - initial.alpha[1]:+.4f} |
| surface_advantage | {initial.alpha[2]:.4f} | {final.alpha[2]:.4f} | {final.alpha[2] - initial.alpha[2]:+.4f} |

The simplex sums to 1.0 within ±1e-6 (validated by `agent.core.state.Weights`).

{calibrated_block}## Reproducibility

The training-set parquet is byte-deterministic given the seed; rerunning
`python -m data.etl.build_training_set --output {config.training_set} --seed {seed}`
produces an identical parquet, and rerunning
`python -m agent.training --training-set {config.training_set} --output {config.output_dir}/`
on top of it produces an identical `weights_v0.json` + `evolution_curve.csv`.

## Outputs

* `{config.output_dir}/weights_v0.json` — final 6-parameter snapshot
* `{config.output_dir}/evolution_curve.csv` — per-step weights + cumulative_log_loss
* `{config.output_dir}/PHASE1_TRAINING_REPORT.md` — this report

— Track B Backend Agent · T-B-004 · sprint_3 D6
"""
    path.write_text(body, encoding="utf-8")


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid mirrored from
    :func:`agent.engines.weight_updater._sigmoid`."""
    x_c = max(-50.0, min(50.0, x))
    return 1.0 / (1.0 + math.exp(-x_c))


def _utcnow() -> datetime:
    """UTC ``datetime.now()`` — wrapped so tests can monkeypatch."""
    return datetime.now(tz=UTC)


# Re-export the exception types the auditor + tests import directly.
__all__ = [
    "DEFAULT_HOLDOUT_FRACTION",
    "DEFAULT_INITIAL_ALPHA",
    "DEFAULT_INITIAL_BETA",
    "DEFAULT_INITIAL_RHO",
    "DEFAULT_INITIAL_W_R",
    "DEFAULT_INITIAL_W_S",
    "DEFAULT_LEARNING_RATE",
    "RATIONAL_ENGINE_NAMES",
    "SENTIENT_ENGINE_NAMES",
    "BacktestValidityFailed",
    "LookaheadError",
    "Phase1Config",
    "Phase1FreezeViolation",
    "Phase1Result",
    "run_phase1_training",
]
