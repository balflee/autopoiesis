# Greek letters mirror PRD §4.1 / §6.6 notation; see agent/engines/weight_updater.py.
"""``python -m agent.training`` — Phase 1 historical training CLI.

Example::

    python -m data.etl.build_training_set --output data/parquet/training_set_v1.parquet
    python -m agent.training \
        --training-set data/parquet/training_set_v1.parquet \
        --output reports/phase1/

The training-set parquet is byte-deterministic given the seed; the
runner is byte-deterministic given the parquet — so two consecutive
invocations on a clean checkout produce byte-identical
``weights_v0.json`` + ``evolution_curve.csv``.

Exit codes:

* 0 — training completed + backtest_validity gate passed.
* 1 — fatal error (missing parquet, schema mismatch, etc.).
* 2 — Phase 1 freeze violation (β₁ / W_R / W_S drifted — this is the
  hard rule the brief calls out as Tier 1 if breached).
* 3 — backtest_validity failed (trained log-loss ≥ uniform baseline).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.training.phase1_runner import (
    BacktestValidityFailed,
    Phase1Config,
    Phase1FreezeViolation,
    run_phase1_training,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m agent.training",
        description=(
            "Phase 1 historical training entrypoint (T-B-004 — sprint_3 D6). "
            "Reads a PIT-correct training-set parquet, runs softmax-reparam SGD "
            "on the 4-dim (α, ρ) subspace, emits weights_v0.json + "
            "evolution_curve.csv + PHASE1_TRAINING_REPORT.md."
        ),
    )
    p.add_argument(
        "--training-set",
        type=Path,
        default=Path("data/parquet/training_set_v1.parquet"),
        help="Path to the training-set parquet (default: data/parquet/training_set_v1.parquet).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("reports/phase1/"),
        help="Output directory (default: reports/phase1/).",
    )
    p.add_argument(
        "--learning-rate",
        type=float,
        default=None,
        help="Override the WeightUpdater base learning rate.",
    )
    p.add_argument(
        "--holdout-fraction",
        type=float,
        default=None,
        help="Holdout test split fraction (default 0.20 = last 20%% chronologically).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    kwargs: dict[str, object] = {
        "training_set": args.training_set,
        "output_dir": args.output,
    }
    if args.learning_rate is not None:
        kwargs["learning_rate"] = args.learning_rate
    if args.holdout_fraction is not None:
        kwargs["holdout_fraction"] = args.holdout_fraction
    config = Phase1Config(**kwargs)  # type: ignore[arg-type]

    try:
        result = run_phase1_training(config)
    except Phase1FreezeViolation as exc:
        print(f"FATAL: Phase 1 freeze violation: {exc}", file=sys.stderr)
        return 2
    except BacktestValidityFailed as exc:
        print(f"FATAL: backtest_validity gate failed: {exc}", file=sys.stderr)
        return 3
    except (FileNotFoundError, ValueError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    summary = {
        "task_id": "T-B-004",
        "n_training_games": result.n_training_games,
        "n_test_games": result.n_test_games,
        "n_total_games": result.n_total_games,
        "mean_training_loss": result.mean_training_loss,
        "uniform_test_loss": result.uniform_test_loss,
        "trained_test_loss": result.trained_test_loss,
        "backtest_improvement_pct": result.backtest_improvement_pct,
        "duration_seconds": result.duration_seconds,
        "final_weights": {
            "w_r": result.final_weights.w_r,
            "w_s": result.final_weights.w_s,
            "alpha": list(result.final_weights.alpha),
            "beta": list(result.final_weights.beta),
            "rho": result.final_weights.rho,
        },
        "outputs": {
            "weights_v0_json": str(result.weights_json_path),
            "evolution_curve_csv": str(result.evolution_curve_path),
            "training_report_md": str(result.report_md_path),
        },
        "seed_metadata": result.seed_metadata,
        "calibrated_params": result.calibrated_params,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    sys.exit(main(sys.argv[1:]))
