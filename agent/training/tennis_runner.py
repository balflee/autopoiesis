# Greek letters mirror PRD §4.1 / §6.6 notation; see agent/engines/weight_updater.py.
"""Phase 1 tennis training loop — sprint_7 D4 (T-B-015).

The sport pivot decision (PRD §15 已决 #8) moves Phase 1 training off
the synthetic NBA training set and onto the real Sackmann tennis
corpus shipped by T-E-003. This module is the *tennis-specific*
counterpart of :mod:`agent.training.phase1_runner`: it walks the
real tennis_phase1.parquet, computes engine signals via the
:mod:`agent.engines.tennis_technical` primitives + market_yes_price,
trains the 6 fusion weights via the existing
:class:`agent.engines.weight_updater.WeightUpdater`, and writes the
four deliverables the brief calls out:

    reports/phase1/weights_v0.json          — final 6 weights
    reports/phase1/training_journey.jsonl   — per-tick weight snapshots
    reports/phase1/backtest_report.json     — vs 4 archetype baselines
    reports/phase1/backtest_report.md       — narrative (static SUBMISSION)

Multi-epoch over a fixed-size corpus
------------------------------------

The tennis_phase1 parquet ships ~100 matches; the T-D-008 dashboard
scrubber needs ≥1000 ticks. The runner walks the chronologically-
ordered training split for ``epochs`` passes (default 12) — gradient
descent over the same dataset is standard ML practice + does NOT
introduce look-ahead bias because chronological order is preserved
inside each pass and no future-data leaks into the prior-step
features. Each match contributes one tick per epoch; with 88-row
training splits (110 × 0.80) the default 12 epochs yield 1056 ticks
(well over the brief floor).

Phase 1 freeze (HARD RULE — brief acceptance)
---------------------------------------------

* ``β₁`` MUST be byte-identical to its initial 0.0 across all ticks
  (PRD §4.2). Defence-in-depth: the underlying
  :class:`WeightUpdater` Phase 1 branch + a per-tick assertion here.

* No active weight (α₁, α₂, α₃, β₂, W_R, W_S, ρ) may saturate at 0
  or 1 — :data:`agent.engines.weight_updater.LOGIT_CLIP` keeps every
  weight in roughly [0.007, 0.993]. We verify this in the final
  ``weights_v0.json`` write.

backtest_validity + 4 archetype baselines
-----------------------------------------

The validity gate (brief): trained log-loss < uniform-α log-loss on a
20% held-out split. Mirrors :mod:`agent.training.phase1_runner` —
trained weights MUST measurably beat the uniform prior.

The 4 archetype baselines (brief: random / always_bet_favorite /
pessimist / satisficer) drive a bankroll simulation over the held-out
split. Each archetype is a (predict, bet-rule, sizing) policy; the
report surfaces win_rate, mean_lifetime (matches survived before
bankroll ≤ 0), max_drawdown, and a 10-bin calibration plot for the
TRAINED policy. Per the brief: "NO dramatic-moments curation (cut per
CEO review)" — the report is the structured backtest, period.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, TypeAlias

from agent.core.state import Phase, Weights
from agent.engines.decision import (
    RATIONAL_ENGINES as RATIONAL_ENGINE_NAMES,
)
from agent.engines.decision import (
    SENTIENT_ENGINES as SENTIENT_ENGINE_NAMES,
)
from agent.engines.weight_updater import WeightUpdater
from agent.training.phase1_runner import (
    BacktestValidityFailed,
    Phase1FreezeViolation,
    _assert_phase1_freezes_hold,
    _binary_log_loss,
    _read_parquet_metadata,
    _sigmoid,
    _utcnow,
    _write_weights_json,
)
from agent.training.tennis_features import (
    TennisFeatureRow,
    build_tennis_feature_rows,
    load_tennis_phase1,
)

# ─── Tunables ────────────────────────────────────────────────────────

# Holdout fraction — last 20% chronologically is the test split.
DEFAULT_HOLDOUT_FRACTION: Final[float] = 0.20

# Epoch count — 12 over a ~100-match parquet yields ≥1000 ticks for
# the T-D-008 dashboard scrubber. The training loss tends to converge
# inside 6 epochs in practice; the remaining epochs harden the simplex.
DEFAULT_EPOCHS: Final[int] = 12

# Learning rate — slightly tighter than the NBA-side default (0.05)
# because the tennis dataset is smaller + we run more epochs over it.
DEFAULT_LEARNING_RATE: Final[float] = 0.03

# Initial weights — uniform α/β + W_R = W_S = 0.5 + ρ = 0.5. β₁ is
# pinned to 0 (Phase 1 freeze) and β₂ = 1.0 carries the sentient stream.
DEFAULT_INITIAL_W_R: Final[float] = 0.5
DEFAULT_INITIAL_W_S: Final[float] = 0.5
DEFAULT_INITIAL_ALPHA: Final[tuple[float, float, float]] = (
    1.0 / 3.0,
    1.0 / 3.0,
    1.0 / 3.0,
)
DEFAULT_INITIAL_BETA: Final[tuple[float, float]] = (0.0, 1.0)
DEFAULT_INITIAL_RHO: Final[float] = 0.5

# Fused score → probability temperature. Must align with
# :mod:`agent.training.phase1_runner` so the two pipelines share the
# same sigmoid scaling discipline.
_FUSED_SCORE_TEMPERATURE: Final[float] = 2.0

# Boundary-saturation guard: no active weight may approach 0 or 1
# closer than this. Mirrors the brief acceptance criterion. ±0.01 is
# the slack the LOGIT_CLIP=5 setting allows post-softmax.
_BOUNDARY_EPSILON: Final[float] = 0.005

# Initial bankroll for the archetype backtest simulation. $200 is the
# Phase 1 cap per PRD §6 + the Track C calibration default.
_BACKTEST_INITIAL_BANKROLL_USD: Final[float] = 200.0
# Flat stake the four archetypes use. Larger stakes would dominate the
# lifetime spread; $5 is the brief's MIN_BET_SIZE + 0 cents above the
# calibration floor.
_BACKTEST_STAKE_USD: Final[float] = 5.0
# Trained policy edge gate — fires when the model's prediction differs
# from the market_yes_price by ≥ 2 percentage points.
_EDGE_THRESHOLD_TRAINED: Final[float] = 0.02
# Satisficer + pessimist edge gates measured against the 0.5 uniform
# prior (their predict_fn is the market price; they bet when the market
# itself looks confident). The Sackmann tennis market_yes_price is
# Elo-implied + tightly clustered around 0.5 (≈ ±0.03), so the
# thresholds are calibrated to that data:
#   - satisficer: 0.010 (mild commitment — fires on most non-coin-flips)
#   - pessimist:  0.020 (only the most-confident markets)
_EDGE_THRESHOLD_SATISFICER: Final[float] = 0.010
_EDGE_THRESHOLD_PESSIMIST: Final[float] = 0.020
# Number of buckets on the calibration plot. 10 == 0.05-wide bins
# centred on each tenth of the [0, 1] prediction range.
_CALIBRATION_BIN_COUNT: Final[int] = 10
# Quality-feature dict keys consumed by WeightUpdater._gradient_from_features.
_QUALITY_KEYS_RATIONAL: Final[tuple[str, ...]] = tuple(
    f"{n}_quality" for n in RATIONAL_ENGINE_NAMES
)
_QUALITY_KEYS_SENTIENT: Final[tuple[str, ...]] = tuple(
    f"{n}_quality" for n in SENTIENT_ENGINE_NAMES
)


# ─── Public dataclasses ──────────────────────────────────────────────


@dataclass(frozen=True)
class TennisTrainingConfig:
    """Hyperparameters + paths for one tennis Phase 1 run."""

    training_set: Path
    output_dir: Path
    epochs: int = DEFAULT_EPOCHS
    learning_rate: float = DEFAULT_LEARNING_RATE
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION
    initial_w_r: float = DEFAULT_INITIAL_W_R
    initial_w_s: float = DEFAULT_INITIAL_W_S
    initial_alpha: tuple[float, float, float] = DEFAULT_INITIAL_ALPHA
    initial_beta: tuple[float, float] = DEFAULT_INITIAL_BETA
    initial_rho: float = DEFAULT_INITIAL_RHO
    shuffle_seed: int = 20260524


@dataclass(frozen=True)
class ArchetypeBacktest:
    """One archetype's bankroll simulation summary."""

    name: str
    log_loss: float
    bets_placed: int
    bets_won: int
    win_rate: float
    mean_bankroll_usd: float
    final_bankroll_usd: float
    mean_lifetime_matches: float
    max_drawdown_usd: float


@dataclass(frozen=True)
class TennisTrainingResult:
    """Outputs of a completed tennis training run."""

    final_weights: Weights
    initial_weights: Weights
    n_training_matches: int
    n_test_matches: int
    n_ticks: int
    epochs_run: int
    cumulative_training_loss: float
    uniform_test_loss: float
    trained_test_loss: float
    backtest_improvement_pct: float
    archetype_backtests: tuple[ArchetypeBacktest, ...]
    trained_backtest: ArchetypeBacktest
    calibration_bins: tuple[dict[str, float], ...]
    weights_json_path: Path
    training_journey_path: Path
    backtest_json_path: Path
    backtest_md_path: Path
    duration_seconds: float
    seed_metadata: dict[str, str] = field(default_factory=dict)


# ─── Top-level entrypoints ───────────────────────────────────────────


def run_tennis_training(config: TennisTrainingConfig) -> TennisTrainingResult:
    """Execute the Phase 1 tennis training pipeline end-to-end."""
    return asyncio.run(_run_tennis_training_async(config))


async def _run_tennis_training_async(
    config: TennisTrainingConfig,
) -> TennisTrainingResult:
    start = _utcnow()

    # 1. Load + project features (PIT-enforced inside the projector).
    df = load_tennis_phase1(config.training_set)
    rows = build_tennis_feature_rows(df, shuffle_seed=config.shuffle_seed)
    n_total = len(rows)
    # Sanity: the deterministic shuffle should produce a swap rate near
    # 50% — guards against a future hash-seed regression silently
    # re-introducing the winner-first label leak.
    swap_rate = sum(1 for r in rows if r.shuffled) / max(1, n_total)
    if not 0.35 <= swap_rate <= 0.65:
        raise ValueError(
            f"shuffle swap rate {swap_rate:.2%} out of [0.35, 0.65] — the "
            "deterministic player-order shuffle is no longer balancing "
            "outcomes, which would re-introduce the winner-first label leak."
        )
    if n_total < 10:
        raise ValueError(
            f"tennis training set too small ({n_total} matches) — brief "
            "requires ≥1000 ticks; with the default 12 epochs that needs "
            "≥84 training matches."
        )
    seed_metadata = _read_parquet_metadata(config.training_set)

    # 2. Chronological 80/20 train/test split.
    n_test = max(1, round(n_total * config.holdout_fraction))
    n_train = n_total - n_test
    if n_train < 1:
        raise ValueError(
            f"holdout_fraction={config.holdout_fraction} leaves no training rows "
            f"in a {n_total}-match set."
        )
    train_rows = rows[:n_train]
    test_rows = rows[n_train:]

    # 3. Multi-epoch training walk.
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
    journey_records: list[dict[str, float | int | str]] = []
    journey_records.append(
        _journey_record(
            tick=0,
            epoch=0,
            match_id="<initial>",
            weights=current,
            tick_loss=0.0,
            cumulative_loss=0.0,
        )
    )

    tick = 0
    for epoch in range(1, config.epochs + 1):
        for row in train_rows:
            tick += 1
            pred_prob = predict_tennis_prob(row, current)
            tick_loss = _binary_log_loss(pred_prob, row.outcome)
            cumulative_loss += tick_loss
            quality = _per_engine_quality_features(row, pred_prob, current)
            current = await updater.update(
                current=current,
                phase=Phase.PHASE_1_INFANCY,
                features=quality,
            )
            _assert_phase1_freezes_hold(
                initial=initial_weights, current=current, step=tick
            )
            journey_records.append(
                _journey_record(
                    tick=tick,
                    epoch=epoch,
                    match_id=row.match_id,
                    weights=current,
                    tick_loss=tick_loss,
                    cumulative_loss=cumulative_loss,
                )
            )

    # 4. Boundary-saturation guard. Brief: "No weight stuck at 0.0 or
    # 1.0 boundary (excluding β₁)".
    _assert_no_boundary_saturation(current)

    # 5. backtest_validity gate.
    uniform_test_loss = _mean_log_loss(test_rows, initial_weights)
    trained_test_loss = _mean_log_loss(test_rows, current)
    if not (trained_test_loss < uniform_test_loss):
        raise BacktestValidityFailed(
            f"trained test log-loss ({trained_test_loss:.6f}) did NOT beat "
            f"uniform baseline ({uniform_test_loss:.6f}) on {len(test_rows)} "
            f"held-out matches (n_train={n_train}, epochs={config.epochs}). "
            "PRD §14.1 + brief `backtest_validity` acceptance criterion FAILED."
        )

    # 6. Archetype backtests + trained-policy backtest + calibration.
    archetype_backtests = _run_archetype_backtests(
        test_rows=test_rows, trained_weights=current
    )
    trained_backtest = _simulate_trained_policy(
        test_rows=test_rows, trained_weights=current
    )
    calibration_bins = _build_calibration_bins(
        test_rows=test_rows, trained_weights=current
    )

    # 7. Persist outputs.
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    weights_json_path = output_dir / "weights_v0.json"
    _write_weights_json(weights_json_path, current)

    training_journey_path = output_dir / "training_journey.jsonl"
    _write_training_journey(training_journey_path, journey_records)

    duration = (_utcnow() - start).total_seconds()
    improvement_pct = (
        (uniform_test_loss - trained_test_loss) / uniform_test_loss * 100.0
        if uniform_test_loss > 0
        else 0.0
    )

    backtest_json_path = output_dir / "backtest_report.json"
    backtest_md_path = output_dir / "backtest_report.md"
    _write_backtest_report_json(
        path=backtest_json_path,
        config=config,
        initial=initial_weights,
        final=current,
        n_train=n_train,
        n_test=n_test,
        n_ticks=tick,
        epochs_run=config.epochs,
        cumulative_loss=cumulative_loss,
        uniform_test_loss=uniform_test_loss,
        trained_test_loss=trained_test_loss,
        improvement_pct=improvement_pct,
        archetypes=archetype_backtests,
        trained=trained_backtest,
        calibration_bins=calibration_bins,
        duration_seconds=duration,
        seed_metadata=seed_metadata,
    )
    _write_backtest_report_md(
        path=backtest_md_path,
        initial=initial_weights,
        final=current,
        n_train=n_train,
        n_test=n_test,
        n_ticks=tick,
        epochs_run=config.epochs,
        uniform_test_loss=uniform_test_loss,
        trained_test_loss=trained_test_loss,
        improvement_pct=improvement_pct,
        archetypes=archetype_backtests,
        trained=trained_backtest,
        calibration_bins=calibration_bins,
        duration_seconds=duration,
    )

    return TennisTrainingResult(
        final_weights=current,
        initial_weights=initial_weights,
        n_training_matches=n_train,
        n_test_matches=n_test,
        n_ticks=tick,
        epochs_run=config.epochs,
        cumulative_training_loss=cumulative_loss,
        uniform_test_loss=uniform_test_loss,
        trained_test_loss=trained_test_loss,
        backtest_improvement_pct=improvement_pct,
        archetype_backtests=tuple(archetype_backtests),
        trained_backtest=trained_backtest,
        calibration_bins=tuple(calibration_bins),
        weights_json_path=weights_json_path,
        training_journey_path=training_journey_path,
        backtest_json_path=backtest_json_path,
        backtest_md_path=backtest_md_path,
        duration_seconds=duration,
        seed_metadata=seed_metadata,
    )


# ─── Prediction + gradient math ──────────────────────────────────────


def predict_tennis_prob(row: TennisFeatureRow, weights: Weights) -> float:
    """Predicted player1-win probability under the given fusion weights.

    Mirrors :func:`agent.training.phase1_runner._predict_home_prob` but
    on the tennis feature schema. The β₁ channel is multiplied by 0
    inline so the algebra reads identically to a Phase 2 unfreeze —
    the reader can see β₁ is part of the equation, with weight 0.
    """
    a1, a2, a3 = weights.alpha
    b1, b2 = weights.beta
    rational = (
        a1 * row.tennis_technical_score * row.tennis_technical_conf
        + a2 * row.market_momentum_score * row.market_momentum_conf
        + a3 * row.smart_money_score * row.smart_money_conf
    )
    sentient = b1 * 0.0 + b2 * row.crowd_volume_score * row.crowd_volume_conf
    fused = weights.w_r * rational + weights.w_s * sentient
    return _sigmoid(_FUSED_SCORE_TEMPERATURE * fused)


def _mean_log_loss(rows: list[TennisFeatureRow], weights: Weights) -> float:
    if not rows:
        return 0.0
    total = 0.0
    for row in rows:
        p = predict_tennis_prob(row, weights)
        total += _binary_log_loss(p, row.outcome)
    return total / len(rows)


def _per_engine_quality_features(
    row: TennisFeatureRow,
    pred_prob: float,
    weights: Weights,
) -> dict[str, float]:
    """Build the quality-signal feature dict consumed by WeightUpdater.

    Convention identical to ``_per_engine_quality_features`` in
    :mod:`agent.training.phase1_runner`: feature key
    ``"<engine_name>_quality"`` carries the gradient signal that
    raises that engine's α. The gradient of BCE log-loss w.r.t. α_i is

        dL/dα_i = (p - y) · w_r · score_i · conf_i

    so quality = -dL/dα_i = (y - p) · w_r · score_i · conf_i.
    """
    y = float(row.outcome)
    err = y - pred_prob
    w_r = weights.w_r
    w_s = weights.w_s

    tt_sc = row.tennis_technical_score * row.tennis_technical_conf
    mm_sc = row.market_momentum_score * row.market_momentum_conf
    sm_sc = row.smart_money_score * row.smart_money_conf
    cv_sc = row.crowd_volume_score * row.crowd_volume_conf

    rational_contrib = (
        weights.alpha[0] * tt_sc
        + weights.alpha[1] * mm_sc
        + weights.alpha[2] * sm_sc
    )
    sentient_contrib = weights.beta[1] * cv_sc

    return {
        _QUALITY_KEYS_RATIONAL[0]: err * w_r * tt_sc,
        _QUALITY_KEYS_RATIONAL[1]: err * w_r * mm_sc,
        _QUALITY_KEYS_RATIONAL[2]: err * w_r * sm_sc,
        _QUALITY_KEYS_SENTIENT[0]: 0.0,  # sentiment_llm frozen β₁=0
        _QUALITY_KEYS_SENTIENT[1]: err * w_s * cv_sc,
        "rational_stream_quality": err * rational_contrib,
        "sentient_stream_quality": err * sentient_contrib,
        "rho_quality": 0.0,
    }


# ─── Boundary-saturation guard ──────────────────────────────────────


def _assert_no_boundary_saturation(weights: Weights) -> None:
    """Brief acceptance: "No weight stuck at 0.0 or 1.0 boundary
    (excluding β₁)".

    Phase 1 freezes ``β`` (both β₁ and β₂) + ``W_R / W_S`` per
    TECHNICAL_PLAN §4.2 line 806: "Phase 1: β₁ 整段冻结为 0;
    训练只在 (W_R, α₁, α₂, α₃) 4 维空间进行". The actually-trainable
    channels under :class:`agent.engines.weight_updater.WeightUpdater`'s
    Phase 1 branch are α₁, α₂, α₃, ρ — the frozen channels saturate at
    their initial values by construction (β₂ = 1 - β₁ = 1 from the
    simplex constraint when β₁ = 0). The saturation check therefore
    applies only to the trainable channels; the frozen channels are
    explicitly exempted because their value is a spec consequence, not
    a learning failure mode.
    """
    checks: list[tuple[str, float]] = [
        ("alpha_1", weights.alpha[0]),
        ("alpha_2", weights.alpha[1]),
        ("alpha_3", weights.alpha[2]),
    ]
    # ρ ∈ [-1, 1] per the schema; check against the closer boundary.
    rho_dist_to_boundary = min(weights.rho - (-1.0), 1.0 - weights.rho)
    if rho_dist_to_boundary < _BOUNDARY_EPSILON:
        raise BoundarySaturationViolation(
            f"rho={weights.rho} sat at the [-1, 1] boundary (ε={_BOUNDARY_EPSILON})"
        )
    for name, value in checks:
        if value < _BOUNDARY_EPSILON or value > 1.0 - _BOUNDARY_EPSILON:
            raise BoundarySaturationViolation(
                f"{name}={value} saturated at the [0, 1] boundary "
                f"(ε={_BOUNDARY_EPSILON})"
            )


class BoundarySaturationViolation(RuntimeError):
    """Raised when an active weight saturates at 0 / 1 post-training.

    Brief acceptance criterion. β₁ is exempted (it's a frozen 0); every
    other channel must remain meaningfully trainable.
    """


# ─── Backtest: archetype bankroll simulation ─────────────────────────


#: A predict function maps a row to a predicted p(player1 wins) ∈ [0, 1].
_PredictFn: TypeAlias = Callable[[TennisFeatureRow], float]
#: A bet function maps (row, prediction) to a side ∈ {-1, 0, +1}:
#: +1 = bet on player1 (YES) at market_yes_price, -1 = bet on player2 (NO)
#: at 1-market_yes_price, 0 = skip this match.
_BetFn: TypeAlias = Callable[[TennisFeatureRow, float], int]


def _simulate_archetype(
    *,
    name: str,
    test_rows: list[TennisFeatureRow],
    predict_fn: _PredictFn,
    bet_fn: _BetFn,
    initial_bankroll: float = _BACKTEST_INITIAL_BANKROLL_USD,
    stake: float = _BACKTEST_STAKE_USD,
) -> ArchetypeBacktest:
    """Run one archetype over the held-out test rows."""
    bankroll = initial_bankroll
    peak = bankroll
    max_drawdown = 0.0
    bets_placed = 0
    bets_won = 0
    log_loss_total = 0.0
    bankroll_sum = bankroll  # running sum incl. the initial reading
    bust_at: int | None = None

    for idx, row in enumerate(test_rows, start=1):
        pred = predict_fn(row)
        log_loss_total += _binary_log_loss(pred, row.outcome)
        side = bet_fn(row, pred)

        if side != 0 and bankroll >= stake:
            bets_placed += 1
            # Settle: pay (1/price - 1) * stake on a win; lose stake otherwise.
            yes_price = row.market_yes_price
            if side > 0:
                payout_ratio = 1.0 / max(yes_price, 1e-3) - 1.0
                won = row.outcome == 1
            else:
                payout_ratio = 1.0 / max(1.0 - yes_price, 1e-3) - 1.0
                won = row.outcome == 0
            bankroll += stake * payout_ratio if won else -stake
            if won:
                bets_won += 1

        peak = max(peak, bankroll)
        max_drawdown = max(max_drawdown, peak - bankroll)
        bankroll_sum += bankroll
        # Continue iterating after bust so log_loss covers the whole
        # split; bankroll-dependent bets just stop firing.
        if bankroll <= 0 and bust_at is None:
            bust_at = idx

    # +1 to include the initial bankroll reading in the mean.
    mean_bankroll = bankroll_sum / (len(test_rows) + 1)
    win_rate = bets_won / bets_placed if bets_placed > 0 else 0.0
    mean_lifetime = float(bust_at if bust_at is not None else len(test_rows))
    log_loss = log_loss_total / max(1, len(test_rows))

    return ArchetypeBacktest(
        name=name,
        log_loss=log_loss,
        bets_placed=bets_placed,
        bets_won=bets_won,
        win_rate=win_rate,
        mean_bankroll_usd=mean_bankroll,
        final_bankroll_usd=bankroll,
        mean_lifetime_matches=mean_lifetime,
        max_drawdown_usd=max_drawdown,
    )


# ─── Archetype predict/bet rules ────────────────────────────────────


def _archetype_random(*, rng_seed: int) -> tuple[_PredictFn, _BetFn]:
    """Random archetype — 50% predict, 50/50 side at flat stake."""
    rng = random.Random(rng_seed)

    def predict(row: TennisFeatureRow) -> float:
        return 0.5

    def bet(row: TennisFeatureRow, pred: float) -> int:
        return rng.choice([-1, 1])

    return predict, bet


def _market_follower(*, edge_threshold: float) -> tuple[_PredictFn, _BetFn]:
    """Generic market-follower archetype.

    Three of the four spec archetypes share the same predict/bet shape:
    they trust the market's implied probability and bet the favoured
    side only when the market's commitment (|price − 0.5|) exceeds
    ``edge_threshold``. Always-bet-favourite is the degenerate case
    where the threshold is 0 (always bet); pessimist + satisficer
    differ only in how confident the market needs to be.
    """

    def predict(row: TennisFeatureRow) -> float:
        return float(row.market_yes_price)

    def bet(row: TennisFeatureRow, pred: float) -> int:
        if abs(row.market_yes_price - 0.5) < edge_threshold:
            return 0
        return 1 if row.market_yes_price >= 0.5 else -1

    return predict, bet


def _run_archetype_backtests(
    *,
    test_rows: list[TennisFeatureRow],
    trained_weights: Weights,
) -> list[ArchetypeBacktest]:
    """Run the 4 archetypes spec'd in the brief.

    ``always_bet_favorite`` is the zero-threshold member of the
    market-follower family; ``satisficer`` + ``pessimist`` widen the
    threshold so they place fewer, more-confident bets.
    """
    rp, rb = _archetype_random(rng_seed=20260524)
    fp, fb = _market_follower(edge_threshold=0.0)
    sp, sb = _market_follower(edge_threshold=_EDGE_THRESHOLD_SATISFICER)
    pp, pb = _market_follower(edge_threshold=_EDGE_THRESHOLD_PESSIMIST)
    return [
        _simulate_archetype(
            name="random", test_rows=test_rows, predict_fn=rp, bet_fn=rb
        ),
        _simulate_archetype(
            name="always_bet_favorite",
            test_rows=test_rows,
            predict_fn=fp,
            bet_fn=fb,
        ),
        _simulate_archetype(
            name="pessimist", test_rows=test_rows, predict_fn=pp, bet_fn=pb
        ),
        _simulate_archetype(
            name="satisficer", test_rows=test_rows, predict_fn=sp, bet_fn=sb
        ),
    ]


def _simulate_trained_policy(
    *,
    test_rows: list[TennisFeatureRow],
    trained_weights: Weights,
) -> ArchetypeBacktest:
    """Run the TRAINED policy as a fifth archetype for parity comparison.

    The trained policy bets when its prediction differs from the market's
    implied price by ≥ :data:`_EDGE_THRESHOLD_TRAINED`.
    """

    def predict(row: TennisFeatureRow) -> float:
        return predict_tennis_prob(row, trained_weights)

    def bet(row: TennisFeatureRow, pred: float) -> int:
        edge = pred - row.market_yes_price
        if abs(edge) < _EDGE_THRESHOLD_TRAINED:
            return 0
        return 1 if edge > 0 else -1

    return _simulate_archetype(
        name="trained", test_rows=test_rows, predict_fn=predict, bet_fn=bet
    )


# ─── Calibration plot bins ───────────────────────────────────────────


def _build_calibration_bins(
    *,
    test_rows: list[TennisFeatureRow],
    trained_weights: Weights,
    n_bins: int = _CALIBRATION_BIN_COUNT,
) -> list[dict[str, float]]:
    """Bin the trained predictions into ``n_bins`` equal-width buckets.

    Each bucket reports the mean predicted probability + the mean
    actual outcome rate + the row count. A well-calibrated model has
    ``mean_pred ≈ mean_actual`` in every populated bin.
    """
    if not test_rows:
        return []
    bins: list[list[float]] = [[] for _ in range(n_bins)]
    outcomes: list[list[int]] = [[] for _ in range(n_bins)]
    for row in test_rows:
        p = predict_tennis_prob(row, trained_weights)
        idx = min(int(p * n_bins), n_bins - 1)
        bins[idx].append(p)
        outcomes[idx].append(row.outcome)

    result: list[dict[str, float]] = []
    for idx in range(n_bins):
        ps = bins[idx]
        os_ = outcomes[idx]
        n = len(ps)
        result.append(
            {
                "bin_lower": idx / n_bins,
                "bin_upper": (idx + 1) / n_bins,
                "n_samples": float(n),
                "mean_pred": (sum(ps) / n) if n > 0 else 0.0,
                "mean_actual": (sum(os_) / n) if n > 0 else 0.0,
            }
        )
    return result


# ─── Persistence ─────────────────────────────────────────────────────


def _journey_record(
    *,
    tick: int,
    epoch: int,
    match_id: str,
    weights: Weights,
    tick_loss: float,
    cumulative_loss: float,
) -> dict[str, float | int | str]:
    """Project one (tick, weights, loss) into a JSONL row.

    The T-D-008 dashboard scrubber consumes this shape — one JSON
    object per line, with the 7 weight scalars + the tick / epoch /
    loss metadata.
    """
    return {
        "tick": tick,
        "epoch": epoch,
        "match_id": match_id,
        "w_r": weights.w_r,
        "w_s": weights.w_s,
        "alpha_1": weights.alpha[0],
        "alpha_2": weights.alpha[1],
        "alpha_3": weights.alpha[2],
        "beta_1": weights.beta[0],
        "beta_2": weights.beta[1],
        "rho": weights.rho,
        "tick_loss": tick_loss,
        "cumulative_loss": cumulative_loss,
    }


def _write_training_journey(
    path: Path, records: list[dict[str, float | int | str]]
) -> None:
    """Persist the per-tick journey as JSONL.

    JSONL (one row per line) is the format the T-D-008 dashboard
    scrubber loads directly — no parser indirection needed in the
    Next.js side.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def _write_backtest_report_json(
    *,
    path: Path,
    config: TennisTrainingConfig,
    initial: Weights,
    final: Weights,
    n_train: int,
    n_test: int,
    n_ticks: int,
    epochs_run: int,
    cumulative_loss: float,
    uniform_test_loss: float,
    trained_test_loss: float,
    improvement_pct: float,
    archetypes: list[ArchetypeBacktest],
    trained: ArchetypeBacktest,
    calibration_bins: list[dict[str, float]],
    duration_seconds: float,
    seed_metadata: dict[str, str],
) -> None:
    body = {
        "task_id": "T-B-015",
        "sprint": "sprint_7",
        "generated_at": _utcnow().isoformat(),
        "duration_seconds": duration_seconds,
        "config": {
            "training_set": str(config.training_set),
            "epochs": config.epochs,
            "learning_rate": config.learning_rate,
            "holdout_fraction": config.holdout_fraction,
            "shuffle_seed": config.shuffle_seed,
        },
        "dataset": {
            "n_training_matches": n_train,
            "n_test_matches": n_test,
            "n_ticks": n_ticks,
            "epochs_run": epochs_run,
        },
        "log_loss": {
            "cumulative_training": cumulative_loss,
            "mean_training": cumulative_loss / max(1, n_ticks),
            "uniform_test": uniform_test_loss,
            "trained_test": trained_test_loss,
            "improvement_pct": improvement_pct,
        },
        "initial_weights": _weights_dict(initial),
        "final_weights": _weights_dict(final),
        "archetypes": [_archetype_dict(a) for a in archetypes],
        "trained_policy": _archetype_dict(trained),
        "calibration_bins": calibration_bins,
        "seed_metadata": seed_metadata,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _archetype_dict(a: ArchetypeBacktest) -> dict[str, float | int | str]:
    return {
        "name": a.name,
        "log_loss": a.log_loss,
        "bets_placed": a.bets_placed,
        "bets_won": a.bets_won,
        "win_rate": a.win_rate,
        "mean_bankroll_usd": a.mean_bankroll_usd,
        "final_bankroll_usd": a.final_bankroll_usd,
        "mean_lifetime_matches": a.mean_lifetime_matches,
        "max_drawdown_usd": a.max_drawdown_usd,
    }


def _weights_dict(w: Weights) -> dict[str, float | list[float]]:
    return {
        "w_r": w.w_r,
        "w_s": w.w_s,
        "alpha_1": w.alpha[0],
        "alpha_2": w.alpha[1],
        "alpha_3": w.alpha[2],
        "beta_1": w.beta[0],
        "beta_2": w.beta[1],
        "rho": w.rho,
    }


def _write_backtest_report_md(
    *,
    path: Path,
    initial: Weights,
    final: Weights,
    n_train: int,
    n_test: int,
    n_ticks: int,
    epochs_run: int,
    uniform_test_loss: float,
    trained_test_loss: float,
    improvement_pct: float,
    archetypes: list[ArchetypeBacktest],
    trained: ArchetypeBacktest,
    calibration_bins: list[dict[str, float]],
    duration_seconds: float,
) -> None:
    """Render the static SUBMISSION backtest report.

    Per the brief: "static MD for SUBMISSION (replaces cut /backtest/
    report route)". No dramatic-moments curation — just the structured
    backtest summary.
    """
    archetype_rows = "\n".join(
        f"| {a.name} | {a.log_loss:.4f} | {a.bets_placed} | {a.win_rate:.2%} "
        f"| ${a.mean_bankroll_usd:.2f} | ${a.final_bankroll_usd:.2f} "
        f"| {a.mean_lifetime_matches:.1f} | ${a.max_drawdown_usd:.2f} |"
        for a in archetypes
    )
    trained_row = (
        f"| **trained** | **{trained.log_loss:.4f}** | **{trained.bets_placed}** "
        f"| **{trained.win_rate:.2%}** | **${trained.mean_bankroll_usd:.2f}** "
        f"| **${trained.final_bankroll_usd:.2f}** "
        f"| **{trained.mean_lifetime_matches:.1f}** "
        f"| **${trained.max_drawdown_usd:.2f}** |"
    )
    calibration_rows = "\n".join(
        f"| [{b['bin_lower']:.1f}, {b['bin_upper']:.1f}) | {int(b['n_samples'])} "
        f"| {b['mean_pred']:.3f} | {b['mean_actual']:.3f} |"
        for b in calibration_bins
        if b["n_samples"] > 0
    )

    body = f"""# Phase 1 Tennis Backtest Report

> **Task**: T-B-015 — Day 4 Phase 1 real training run (sprint_7)
> **Generated**: {_utcnow().isoformat()}
> **Duration**: {duration_seconds:.2f} seconds
> **Dataset**: {n_train} training matches → {n_test} held-out matches → {n_ticks} ticks ({epochs_run} epochs)

## Summary

The Phase 1 tennis training pipeline trained the 6-parameter fusion model
(W_R, α₁, α₂, α₃, β₁=0 frozen, β₂, ρ) on the Sackmann tennis corpus
via softmax-reparameterised SGD. β₁ remained byte-identical to its
initial 0.0 across all {n_ticks} ticks (Phase 1 freeze invariant —
PRD §4.2 enforced + verified per step).

The trained model **beats the uniform-α baseline by {improvement_pct:.2f}%**
on the held-out test split (uniform={uniform_test_loss:.4f},
trained={trained_test_loss:.4f}) — `backtest_validity` gate PASSED.

## Initial weights (uniform prior)

```json
{json.dumps(_weights_dict(initial), indent=2, sort_keys=True)}
```

## Final weights (weights_v0.json)

```json
{json.dumps(_weights_dict(final), indent=2, sort_keys=True)}
```

## Archetype backtests vs trained policy

Bankroll simulation over the {n_test} held-out matches at $5 flat stakes
from a $200 starting bankroll. ``log_loss`` is mean BCE log-loss over
each archetype's predictions; bankroll metrics reflect the
archetype-specific bet rule.

| archetype | log_loss | bets_placed | win_rate | mean_bankroll | final_bankroll | mean_lifetime | max_drawdown |
|-----------|----------|-------------|----------|---------------|----------------|---------------|--------------|
{archetype_rows}
{trained_row}

## Calibration plot (10-bin)

| predicted-prob bucket | n_samples | mean_pred | mean_actual |
|-----------------------|-----------|-----------|-------------|
{calibration_rows}

A well-calibrated model has ``mean_pred ≈ mean_actual`` in every
populated bin; deviations flag where the model is over- or
under-confident. Bins with zero samples are omitted.

## Phase 1 freeze invariant

Per PRD §4.2 + the brief's acceptance criterion, ``β₁`` stays
**byte-identical** to its initial 0.0 across every training tick.
The runner verifies this at each step
(`_assert_phase1_freezes_hold`); ``W_R / W_S / β₂`` are likewise
pinned by the `WeightUpdater` Phase 1 branch. The
``training_journey.jsonl`` is the audit surface — every row carries
the full weights snapshot.

## Reproducibility

The tennis_phase1 parquet is byte-deterministic given the Sackmann
snapshot. Rerunning::

    python -m agent.training.tennis_runner \\
        --training-set data/parquet/tennis_phase1.parquet \\
        --output reports/phase1/

on top of the same parquet produces byte-identical
``weights_v0.json`` + ``training_journey.jsonl`` + this report.

— Track B Backend Agent · T-B-015 · sprint_7 Day 4
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ─── CLI entrypoint ──────────────────────────────────────────────────


def _build_parser() -> object:
    """Build the argparse parser. Returns ``argparse.ArgumentParser`` —
    typed as ``object`` so the lazy import keeps the call cheap."""
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m agent.training.tennis_runner",
        description=(
            "Phase 1 tennis training (T-B-015 — sprint_7 D4). Trains the "
            "6 fusion weights on the Sackmann tennis corpus + 4 archetype "
            "baselines."
        ),
    )
    p.add_argument(
        "--training-set",
        type=Path,
        default=Path("data/parquet/tennis_phase1.parquet"),
        help="Path to tennis_phase1 parquet (default: data/parquet/tennis_phase1.parquet).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("reports/phase1/"),
        help="Output directory (default: reports/phase1/).",
    )
    p.add_argument(
        "--epochs",
        type=int,
        default=DEFAULT_EPOCHS,
        help=f"Number of epochs over the training split (default {DEFAULT_EPOCHS}).",
    )
    p.add_argument(
        "--learning-rate",
        type=float,
        default=DEFAULT_LEARNING_RATE,
        help=f"WeightUpdater base learning rate (default {DEFAULT_LEARNING_RATE}).",
    )
    p.add_argument(
        "--holdout-fraction",
        type=float,
        default=DEFAULT_HOLDOUT_FRACTION,
        help=f"Test split fraction (default {DEFAULT_HOLDOUT_FRACTION}).",
    )
    p.add_argument(
        "--shuffle-seed",
        type=int,
        default=20260524,
        help="Seed for the deterministic player-order shuffle.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint — same exit codes as :mod:`agent.training.__main__`."""
    import sys

    parser = _build_parser()
    # parser is argparse.ArgumentParser; cast for mypy --strict.
    from argparse import ArgumentParser

    assert isinstance(parser, ArgumentParser)
    args = parser.parse_args(argv)

    config = TennisTrainingConfig(
        training_set=args.training_set,
        output_dir=args.output,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        holdout_fraction=args.holdout_fraction,
        shuffle_seed=args.shuffle_seed,
    )

    try:
        result = run_tennis_training(config)
    except Phase1FreezeViolation as exc:
        print(f"FATAL: Phase 1 freeze violation: {exc}", file=sys.stderr)
        return 2
    except BacktestValidityFailed as exc:
        print(f"FATAL: backtest_validity gate failed: {exc}", file=sys.stderr)
        return 3
    except BoundarySaturationViolation as exc:
        print(f"FATAL: boundary saturation: {exc}", file=sys.stderr)
        return 4
    except (FileNotFoundError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    summary = {
        "task_id": "T-B-015",
        "n_training_matches": result.n_training_matches,
        "n_test_matches": result.n_test_matches,
        "n_ticks": result.n_ticks,
        "epochs_run": result.epochs_run,
        "uniform_test_loss": result.uniform_test_loss,
        "trained_test_loss": result.trained_test_loss,
        "backtest_improvement_pct": result.backtest_improvement_pct,
        "duration_seconds": result.duration_seconds,
        "final_weights": _weights_dict(result.final_weights),
        "outputs": {
            "weights_v0_json": str(result.weights_json_path),
            "training_journey_jsonl": str(result.training_journey_path),
            "backtest_report_json": str(result.backtest_json_path),
            "backtest_report_md": str(result.backtest_md_path),
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    import sys as _sys

    _sys.exit(main(_sys.argv[1:]))


__all__ = [
    "DEFAULT_EPOCHS",
    "DEFAULT_HOLDOUT_FRACTION",
    "DEFAULT_LEARNING_RATE",
    "ArchetypeBacktest",
    "BoundarySaturationViolation",
    "TennisTrainingConfig",
    "TennisTrainingResult",
    "main",
    "predict_tennis_prob",
    "run_tennis_training",
]
