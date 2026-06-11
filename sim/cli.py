"""``python -m sim.cli`` — calibration sweeper entrypoint.

Sprint_1 (T-C-001) shipped ``--help`` / ``--version`` only. Sprint_2
(T-C-002) added the ``sweep`` subcommand (LHS only). Sprint_3 (T-C-003)
extends ``sweep`` with the Bayesian-Optimization refiner + the full
report renderer — the same command now produces every artifact under
``reports/calibration/<run_id>/`` including ``CALIBRATION_REPORT.md``
and ``selected_params.json``.

Usage
-----

::

    # Sprint_1 still-supported surface
    python -m sim.cli --help
    python -m sim.cli --version

    # Sprint_2 LHS-only sweep (still supported)
    python -m sim.cli sweep --n 8 --output reports/calibration/ --seed 0

    # Sprint_3 full calibration run (LHS + BO + report)
    python -m sim.cli sweep --n 256 --bo-trials 64 \\
        --output reports/calibration/ --seed 0

Output
------

When ``--bo-trials > 0``, ``sweep`` writes a full calibration run
directory (timestamped) and prints ``WROTE <run_dir>``. When
``--bo-trials == 0`` (sprint_2 path, kept for back-compat with
existing tests) it writes only ``sweep_<ts>.json`` and prints
``WROTE <path>``.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from sim import __version__
from sim.analysis import write_full_report
from sim.params import ParamSpace
from sim.sweeper import Sweeper


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sim",
        description=(
            "Genesis Track C — Layer 2 calibration sim. Sprint_3 ships the "
            "full LHS + Bayesian-Optimization sweep with CALIBRATION_REPORT.md "
            "rendering. See PRD.md §14 and TECHNICAL_PLAN.md §4."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sim {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="subcommands",
        # We intentionally do NOT mark required=True so bare
        # ``python -m sim.cli`` still prints help + exits 0 — the
        # orchestrator's sprint_1 gate depends on that behaviour.
    )

    sweep = subparsers.add_parser(
        "sweep",
        help=(
            "Run a Latin-Hypercube + Bayesian-Optimization sweep over the "
            "BREATH parameter space."
        ),
        description=(
            "Sample N parameter combinations via LHS, score each against "
            "the 14 GOOD_CALIBRATION objectives, then refine the frontier "
            "with `bo_trials` BO iterations. When `--bo-trials > 0` the "
            "full report set (CALIBRATION_REPORT.md, selected_params.json, "
            "objectives_passed.json, archetype_breakdown.json, "
            "sensitivity_analysis.json, bo_trace.json, lifetimes.jsonl, "
            "plus PNG plots) is written under --output."
        ),
    )
    sweep.add_argument(
        "--n",
        type=int,
        required=True,
        help="Number of LHS samples (parameter combinations) to draw.",
    )
    sweep.add_argument(
        "--output",
        type=Path,
        required=True,
        help=(
            "Directory to write the calibration artifacts into. When "
            "`--bo-trials > 0` a timestamped subdirectory is created."
        ),
    )
    sweep.add_argument(
        "--seed",
        type=int,
        default=0,
        help="RNG seed (default: 0). Same seed → byte-identical sweep.",
    )
    sweep.add_argument(
        "--bo-trials",
        type=int,
        default=0,
        help=(
            "Bayesian-Optimization iterations on top of the LHS warm-start. "
            "Default 0 (sprint_2 LHS-only path; kept for back-compat). "
            "Set to ≥1 to produce the full CALIBRATION_REPORT.md."
        ),
    )
    sweep.add_argument(
        "--lifetimes-per-archetype",
        type=int,
        default=3,
        help=(
            "Lifetimes simulated per (combo, archetype) pair during the "
            "LHS + BO sweep. Default 3. Higher → slower but tighter "
            "per-combo scoring."
        ),
    )
    sweep.add_argument(
        "--final-lifetimes-per-archetype",
        type=int,
        default=30,
        help=(
            "Lifetimes simulated per archetype on the WINNING combo's "
            "final re-score. Higher → tighter CI for objective #12. "
            "Default 30."
        ),
    )
    sweep.add_argument(
        "--max-ticks",
        type=int,
        default=2000,
        help=(
            "Hard cap on lifetime length. Lifetimes hitting the cap are "
            "classified Survival and counted against the no-immortality "
            "objective. Default 2000."
        ),
    )
    sweep.add_argument(
        "--ci-half-width-max-days",
        type=float,
        default=1.0,
        help=(
            "Objective #12 (`ci_width_under_threshold`) threshold in days. "
            "Default 1.0."
        ),
    )
    sweep.add_argument(
        "--run-id",
        type=str,
        default=None,
        help=(
            "Optional override for the run directory name. Default: "
            "`calib_<unix_ts>_<seed>`."
        ),
    )

    return parser


def _cmd_sweep_lhs_only(args: argparse.Namespace) -> int:
    """Sprint_2 back-compat path — LHS only, single sweep_<ts>.json output."""
    sweeper = Sweeper(
        base_params=ParamSpace(),
        lifetimes_per_archetype=args.lifetimes_per_archetype,
        max_ticks=args.max_ticks,
    )
    report = sweeper.run(n=args.n, seed=args.seed)
    out_path = sweeper.write_report(report, out_dir=args.output)
    print(f"WROTE {out_path}")
    return 0


def _cmd_sweep_with_bo(args: argparse.Namespace) -> int:
    """Sprint_3 full pipeline — LHS + BO + report."""
    sweeper = Sweeper(
        base_params=ParamSpace(),
        lifetimes_per_archetype=args.lifetimes_per_archetype,
        max_ticks=args.max_ticks,
    )
    run = sweeper.calibrate(
        n_lhs=args.n,
        bo_trials=args.bo_trials,
        seed=args.seed,
        final_lifetimes_per_archetype=args.final_lifetimes_per_archetype,
        ci_half_width_max_days=args.ci_half_width_max_days,
    )
    run_id = args.run_id or f"calib_{int(time.time())}_{args.seed}"
    run_dir = args.output / run_id
    artifacts = write_full_report(run=run, out_dir=run_dir)
    # Pretty stdout summary for the orchestrator's log capture.
    print(
        f"objectives_passed={run.final_verdict.passed_count}/"
        f"{run.final_verdict.total_count} "
        f"loss={run.final_verdict.loss:+.4f} "
        f"winning_source={run.winning_source}"
    )
    for name, path in sorted(artifacts.items()):
        print(f"  - {name}: {path}")
    print(f"WROTE {run_dir}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    """Run a sweep + emit produced path(s). Returns exit code."""
    if args.bo_trials <= 0:
        return _cmd_sweep_lhs_only(args)
    return _cmd_sweep_with_bo(args)


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        # Bare invocation: print help, exit 0 (sprint_1 contract).
        parser.print_help()
        return 0

    if args.command == "sweep":
        return _cmd_sweep(args)

    # Should be unreachable — argparse rejects unknown subcommands.
    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI itself
    sys.exit(main())
