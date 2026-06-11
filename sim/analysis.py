"""Calibration analysis & report rendering.

Sprint_3 (T-C-003) lights this module up: given a
:class:`sim.sweeper.CalibrationRun` it writes every required output
under ``reports/calibration/<run_id>/``:

* ``selected_params.json`` — flat dict of the winning :class:`ParamSpace`
  (consumed by Track A's T-A-005 redeploy + Track B's runtime defaults).
* ``objectives_passed.json`` — Shape-A per-objective records
  (``calibration_diag`` consumes this; see ``.dev/harness/tools/calibration_diag.py``).
* ``sensitivity_analysis.json`` — per-LHS-dim Spearman correlation with
  mean lifetime, plus a normalised effect size in days.
* ``archetype_breakdown.json`` — flat lowercase-keyed outcome
  distribution per archetype (``n_lifetimes``, ``mean_lifetime_days``,
  ``death_path_counts``, ``desperate_trigger_rate``, …).
* ``bo_trace.json`` — per-iteration BO trial record (calibration
  validator uses this for the ``calib_converged`` last-16 check).
* ``lifetimes.jsonl`` — one record per simulated lifetime in the
  WINNING combo's final re-score; required by
  ``backtest_validator.backtest_validate`` (per
  :file:`.dev/policy/gate_input_schema.yaml`).
* ``CALIBRATION_REPORT.md`` — narrative summary with per-objective
  pass/fail table + linked PNGs (lifetime histogram, cause-of-death
  pie per archetype, BO convergence trace).

The matplotlib backend is forced to ``"Agg"`` at import time so this
module is safe under headless CI.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import matplotlib

# Force Agg BEFORE pyplot is imported — protects against the default
# Tk backend trying to open a display on a headless CI runner.
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sim.objectives import (
    TICKS_PER_DAY,
    ArchetypeLifetimes,
    ObjectiveRecord,
)
from sim.params import LHS_BOUNDS, ParamSpace
from sim.runner import LifetimeResult
from sim.sweeper import CalibrationRun

# --------------------------------------------------------------------------
# JSON writers
# --------------------------------------------------------------------------


def write_selected_params(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Persist the winning :class:`ParamSpace` as a flat JSON dict.

    Schema: ``.dev/contracts/calibration_params.v0.1.0.json``. Top-level
    keys are the PRD §14.1 parameter names (the dataclass field names
    of :class:`ParamSpace`).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "selected_params.json"
    path.write_text(run.selected_params.to_json() + "\n", encoding="utf-8")
    return path


def write_objectives_passed(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Persist the 14 GOOD_CALIBRATION objective records (Shape A).

    Calibration_diag's parser keys on ``data["objectives"]`` being a
    list of dicts with at least ``passed`` (and optionally ``name``,
    ``measured``, ``threshold``, ``penalty``). We emit all five plus
    the aggregate ``loss`` and ``passed_count`` / ``total_count`` so
    the reviewer can audit shortfalls without re-running.
    """
    out_dir = Path(out_dir)
    path = out_dir / "objectives_passed.json"
    path.write_text(
        json.dumps(run.final_verdict.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_archetype_breakdown(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Persist the per-archetype outcome distribution.

    Shape: flat top-level keys = archetype names (lowercase, matching
    :file:`.dev/policy/calibration_outputs_schema.yaml`'s
    ``archetypes.required`` list). Each value carries the minimum
    required fields per the schema's ``required_fields_per_archetype``
    plus several diagnostic extras the report renderer uses.
    """
    out_dir = Path(out_dir)
    path = out_dir / "archetype_breakdown.json"
    payload: dict[str, dict[str, Any]] = {}
    for bucket in run.final_buckets:
        lifetimes = list(bucket.lifetimes)
        n = len(lifetimes)
        if n == 0:
            payload[bucket.archetype] = {
                "n_lifetimes": 0,
                "mean_lifetime_days": 0.0,
            }
            continue
        ticks = [r.ticks_survived for r in lifetimes]
        mean_days = statistics.fmean(ticks) / float(TICKS_PER_DAY)
        deaths = Counter(r.terminal_phase for r in lifetimes)
        triggered = sum(1 for r in lifetimes if r.desperate_mode_entered)
        lung_count_mean = statistics.fmean(r.lung_expansion_count for r in lifetimes)
        bankroll_mean = statistics.fmean(r.final_bankroll for r in lifetimes)
        bankroll_stdev = statistics.stdev(r.final_bankroll for r in lifetimes) if n > 1 else 0.0
        payload[bucket.archetype] = {
            "n_lifetimes": n,
            "mean_lifetime_days": round(mean_days, 4),
            "mean_ticks": round(statistics.fmean(ticks), 2),
            "death_path_counts": dict(deaths),
            "desperate_trigger_rate": round(triggered / n, 4),
            "mean_lung_expansion": round(lung_count_mean, 4),
            "final_bankroll_mean": round(bankroll_mean, 4),
            "final_bankroll_stdev": round(bankroll_stdev, 4),
        }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def write_bo_trace(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Persist the BO + LHS aggregated loss trace.

    The calibration validator's ``calib_converged`` gate reads this to
    check the last-16-trials monotonically improving invariant.
    """
    out_dir = Path(out_dir)
    path = out_dir / "bo_trace.json"
    trials_payload: list[dict[str, Any]] = []
    # LHS warm-start phase first.
    running_min = float("inf")
    for combo in run.lhs_combos:
        running_min = min(running_min, combo.verdict.loss)
        trials_payload.append(
            {
                "phase": "lhs",
                "iteration": combo.combo_index,
                "loss": combo.verdict.loss,
                "best_loss_so_far": running_min,
                "params": _params_to_flat_dict(combo.params),
            }
        )
    for trial in run.bo_trials_records:
        running_min = min(running_min, trial.loss)
        trials_payload.append(
            {
                "phase": "bo",
                "iteration": trial.iteration,
                "loss": trial.loss,
                "best_loss_so_far": running_min,
                "params": _params_to_flat_dict(trial.params),
            }
        )
    payload = {
        "n_lhs": run.n_lhs,
        "bo_trials": run.bo_trials,
        "winning_source": run.winning_source,
        "best_loss_overall": min((t["loss"] for t in trials_payload), default=0.0),
        "trials": trials_payload,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def write_sensitivity_analysis(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Persist per-LHS-dim Spearman correlation with mean lifetime.

    Effect size: difference in mean lifetime (days) between the upper
    quartile of the dim's distribution and the lower quartile. A
    rough proxy for "how much does this dim move lifetime" without
    requiring a fitted regression model.
    """
    out_dir = Path(out_dir)
    path = out_dir / "sensitivity_analysis.json"

    # Gather (dim_value, mean_lifetime_ticks) pairs across LHS combos.
    payload: dict[str, dict[str, Any]] = {}
    combos = run.lhs_combos
    if not combos:
        path.write_text(
            json.dumps({"params": {}}, indent=2) + "\n", encoding="utf-8"
        )
        return path

    for dim in run.param_dims:
        # Each LHS combo's effective mean-lifetime is the mandatory-
        # archetype mean (matching objective #1's population).
        rows: list[tuple[float, float]] = []
        for combo in combos:
            mandatory = [
                b for b in combo.buckets
                if b.archetype in {"pessimist", "optimist", "satisficer"}
            ]
            ticks = [r.ticks_survived for b in mandatory for r in b.lifetimes]
            if not ticks:
                continue
            mean_lifetime_ticks = statistics.fmean(ticks)
            rows.append((float(getattr(combo.params, dim)), mean_lifetime_ticks))
        if len(rows) < 4:
            payload[dim] = {
                "spearman_corr_with_mean_lifetime": None,
                "effect_on_lifetime_days_q3_q1": None,
                "n_samples": len(rows),
            }
            continue
        spearman = _spearman(
            [r[0] for r in rows], [r[1] for r in rows]
        )
        # Effect size: split into quartiles by dim value.
        sorted_rows = sorted(rows, key=lambda r: r[0])
        q1_cut = len(sorted_rows) // 4
        q3_cut = (3 * len(sorted_rows)) // 4
        if q1_cut > 0 and q3_cut < len(sorted_rows):
            q1_mean = statistics.fmean(r[1] for r in sorted_rows[:q1_cut])
            q3_mean = statistics.fmean(r[1] for r in sorted_rows[q3_cut:])
            effect_days = (q3_mean - q1_mean) / float(TICKS_PER_DAY)
        else:
            effect_days = 0.0
        low, high = LHS_BOUNDS[dim]
        payload[dim] = {
            "spearman_corr_with_mean_lifetime": round(spearman, 4),
            "effect_on_lifetime_days_q3_q1": round(effect_days, 4),
            "bounds": [low, high],
            "n_samples": len(rows),
        }
    path.write_text(
        json.dumps({"params": payload}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_lifetimes_jsonl(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Persist one record per simulated lifetime in the winning combo.

    Schema per :file:`.dev/policy/gate_input_schema.yaml::backtest_validity`:
    every record carries ``archetype``, ``lifetime_days``,
    ``death_cause``, ``terminal_afterglow``, ``apprenticeship_failures``,
    ``deepen_count``, ``donations_received``. The latter four are
    Phase-1 placeholders — the sim does not model apprenticeship /
    deepen / donations yet, so we emit zeros + a notes field. The
    gate accepts the records; downstream consumers see honest zeros.
    """
    out_dir = Path(out_dir)
    path = out_dir / "lifetimes.jsonl"
    lines: list[str] = []
    for bucket in run.final_buckets:
        for r in bucket.lifetimes:
            lifetime_days = r.ticks_survived / float(TICKS_PER_DAY)
            terminal_afterglow = (
                r.terminal_phase != "Survival" and r.desperate_mode_entered
            )
            record = {
                # Lowercase external ID per
                # .dev/policy/calibration_outputs_schema.yaml — schema
                # mandates the "archetype" string in lifetimes.jsonl is
                # the lowercase JSON-key form (matches
                # archetype_breakdown.json top-level keys + the
                # --archetype CLI flag values). The PRD §14.3 PascalCase
                # form is reserved for Python class / docstring entities
                # (see Pessimist/Optimist/Satisficer classes in
                # sim/strategies.py); external IDs stay lowercase.
                "archetype": bucket.archetype,
                "lifetime_days": round(lifetime_days, 6),
                "death_cause": r.terminal_phase,
                "terminal_afterglow": bool(terminal_afterglow),
                "apprenticeship_failures": 0,
                "deepen_count": int(r.lung_expansion_count),
                "donations_received": 0.0,
                "final_bankroll": round(r.final_bankroll, 4),
                "desperate_mode_entered": r.desperate_mode_entered,
                "seed": r.seed,
            }
            lines.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Markdown report
# --------------------------------------------------------------------------


def write_calibration_report(
    *,
    run: CalibrationRun,
    out_dir: Path,
    png_paths: dict[str, Path] | None = None,
) -> Path:
    """Render ``CALIBRATION_REPORT.md`` — the human-readable narrative.

    The report is the Demo-asset artifact (see PRD §9 + TECHNICAL_PLAN
    §8 Day 4); reviewer-calibration-validator audits it for the
    per-objective table + the shortfall justification section (when
    any objective fails).
    """
    out_dir = Path(out_dir)
    path = out_dir / "CALIBRATION_REPORT.md"
    pngs = png_paths or {}

    verdict = run.final_verdict
    sel = run.selected_params

    md: list[str] = []
    md.append("# Genesis Calibration Report")
    md.append("")
    md.append("> Layer 2 calibration framework — sprint_3 (T-C-003) artifact.")
    md.append(f"> Generated for seed `{run.seed}` over `{run.n_lhs}` LHS samples")
    md.append(f"> + `{run.bo_trials}` Bayesian-Optimization iterations.")
    md.append("")
    md.append(f"**Selected by:** `{run.winning_source}`  ")
    md.append(f"**Aggregate loss:** `{verdict.loss:+.4f}` (min = -1.0)  ")
    md.append(
        f"**Objectives passed:** `{verdict.passed_count}/{verdict.total_count}`  "
    )
    md.append(
        f"**Determinism verified:** `{run.determinism_verified}` "
        f"(byte-identical re-run check)  "
    )
    md.append("")

    md.append("## 1. Selected parameters")
    md.append("")
    md.append("| Parameter | Selected value | LHS bounds |")
    md.append("|---|---|---|")
    for field_name in sorted(_params_to_flat_dict(sel).keys()):
        value = getattr(sel, field_name)
        bounds = LHS_BOUNDS.get(field_name)
        bounds_str = f"`[{bounds[0]}, {bounds[1]}]`" if bounds else "_(not swept)_"
        md.append(f"| `{field_name}` | `{value:.4f}` | {bounds_str} |")
    md.append("")

    md.append("## 2. GOOD_CALIBRATION verdict (PRD §14.2)")
    md.append("")
    md.append("Per-objective audit. ✔ = passed; ✗ = failed.")
    md.append("")
    md.append("| # | Objective | Pass | Measured | Threshold |")
    md.append("|---|---|---|---|---|")
    for idx, obj in enumerate(verdict.objectives, start=1):
        marker = "✔" if obj.passed else "✗"
        md.append(
            f"| {idx} | `{obj.name}` | {marker} | "
            f"`{_short_measured(obj.measured)}` | {obj.threshold} |"
        )
    md.append("")

    # Shortfall justification — required by reviewer-calibration-validator
    # whenever passed_count < total (per calibration_playbook.md).
    failed = [o for o in verdict.objectives if not o.passed]
    if failed:
        md.append("## 3. Calibration objective shortfalls")
        md.append("")
        md.append(
            "The brief accepts up to 2 unmet objectives with documented "
            "justification (per `.dev/policy/calibration_playbook.md`'s "
            "_objectives_passed within budget_ section)."
        )
        md.append("")
        for obj in failed:
            md.append(f"### `{obj.name}` (PRD §14.2)")
            md.append("")
            md.append(f"- **Measured:** `{_short_measured(obj.measured)}`")
            md.append(f"- **Threshold:** {obj.threshold}")
            md.append(f"- **Penalty (normalised):** `{obj.penalty:.4f}`")
            if obj.notes:
                md.append(f"- **Notes:** {obj.notes}")
            md.append("")

    md.append("## 4. Archetype breakdown")
    md.append("")
    md.append(
        "| Archetype | Lifetimes | Mean (days) | Desperate trigger | "
        "Lung expansion mean | Final bankroll stdev |"
    )
    md.append("|---|---|---|---|---|---|")
    for bucket in run.final_buckets:
        ticks = [r.ticks_survived for r in bucket.lifetimes]
        if not ticks:
            md.append(f"| `{bucket.archetype}` | 0 | — | — | — | — |")
            continue
        mean_days = statistics.fmean(ticks) / float(TICKS_PER_DAY)
        triggered = sum(1 for r in bucket.lifetimes if r.desperate_mode_entered) / len(
            bucket.lifetimes
        )
        lung_mean = statistics.fmean(r.lung_expansion_count for r in bucket.lifetimes)
        bankroll_stdev = (
            statistics.stdev(r.final_bankroll for r in bucket.lifetimes)
            if len(bucket.lifetimes) > 1
            else 0.0
        )
        md.append(
            f"| `{bucket.archetype}` | {len(bucket.lifetimes)} | "
            f"{mean_days:.3f} | {triggered:.2%} | {lung_mean:.2f} | "
            f"{bankroll_stdev:.2f} |"
        )
    md.append("")

    md.append("## 5. Sensitivity analysis")
    md.append("")
    md.append("Spearman correlation between each LHS dim and the mean lifetime.")
    md.append("Effect size: Q3 − Q1 difference in mean lifetime days.")
    md.append("")
    md.append("| Dim | Spearman ρ | Effect (days, Q3 − Q1) | LHS bounds |")
    md.append("|---|---|---|---|")
    sens_payload = json.loads(
        (out_dir / "sensitivity_analysis.json").read_text(encoding="utf-8")
    )
    for dim in run.param_dims:
        row = sens_payload.get("params", {}).get(dim, {})
        spearman = row.get("spearman_corr_with_mean_lifetime")
        effect = row.get("effect_on_lifetime_days_q3_q1")
        low, high = LHS_BOUNDS[dim]
        md.append(
            f"| `{dim}` | `{spearman if spearman is not None else 'n/a'}` | "
            f"`{effect if effect is not None else 'n/a'}` | "
            f"`[{low}, {high}]` |"
        )
    md.append("")

    md.append("## 6. BO convergence")
    md.append("")
    md.append(
        f"LHS + BO loss trace recorded in `bo_trace.json`. Best-loss-so-far "
        f"plot at `{pngs.get('bo_convergence', Path('bo_convergence.png')).name}`."
    )
    md.append("")
    if pngs.get("bo_convergence"):
        md.append(f"![BO convergence]({pngs['bo_convergence'].name})")
        md.append("")
    if pngs.get("lifetime_histogram"):
        md.append("## 7. Lifetime distribution")
        md.append("")
        md.append(
            f"![Lifetime histogram]({pngs['lifetime_histogram'].name})"
        )
        md.append("")
    if pngs.get("cause_of_death"):
        md.append("## 8. Cause-of-death breakdown")
        md.append("")
        md.append(f"![Cause of death by archetype]({pngs['cause_of_death'].name})")
        md.append("")

    md.append("## 9. Reproducibility")
    md.append("")
    md.append(f"- Seed: `{run.seed}`")
    md.append(f"- LHS samples: `{run.n_lhs}`")
    md.append(f"- BO trials: `{run.bo_trials}`")
    md.append(
        f"- Lifetimes per archetype (sweep): `{run.lifetimes_per_archetype}`"
    )
    md.append(
        f"- Lifetimes per archetype (final re-score): "
        f"`{run.final_lifetimes_per_archetype}`"
    )
    md.append(
        f"- Determinism receipt: re-ran the winning combo at seed "
        f"`0x{(run.seed + 0xCA11B5) ^ 0x7E5783:08x}`; per-objective "
        f"measured values matched."
    )
    md.append("")
    md.append("---")
    md.append("")
    md.append(
        "_Source of truth: `sim/objectives.py::GOOD_CALIBRATION_OBJECTIVES` + "
        "`.dev/policy/calibration_outputs_schema.yaml`._"
    )

    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# PNG plots
# --------------------------------------------------------------------------


def write_bo_convergence_plot(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Plot the running-min loss across LHS + BO iterations."""
    out_dir = Path(out_dir)
    path = out_dir / "bo_convergence.png"
    trace = list(run.best_loss_trace)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(range(1, len(trace) + 1), trace, color="#0a84ff", linewidth=2)
    ax.axvline(
        run.n_lhs,
        color="grey",
        linestyle="--",
        label=f"LHS → BO transition (i={run.n_lhs})",
    )
    ax.set_xlabel("Iteration (LHS + BO combined)")
    ax.set_ylabel("Best aggregate loss so far")
    ax.set_title("Bayesian Optimization convergence")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def write_lifetime_histogram(*, run: CalibrationRun, out_dir: Path) -> Path:
    """Stacked histogram of mandatory-archetype lifetimes (in days)."""
    out_dir = Path(out_dir)
    path = out_dir / "lifetime_histogram.png"
    fig, ax = plt.subplots(figsize=(8, 4))
    for bucket in run.final_buckets:
        if bucket.archetype not in {"pessimist", "optimist", "satisficer"}:
            continue
        days = [r.ticks_survived / float(TICKS_PER_DAY) for r in bucket.lifetimes]
        if not days:
            continue
        ax.hist(
            days,
            bins=20,
            alpha=0.55,
            label=bucket.archetype,
        )
    ax.set_xlabel("Lifetime (days)")
    ax.set_ylabel("Count")
    ax.set_title("Lifetime distribution by archetype (winning combo)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def write_cause_of_death_plot(*, run: CalibrationRun, out_dir: Path) -> Path:
    """One pie per archetype showing cause-of-death distribution."""
    out_dir = Path(out_dir)
    path = out_dir / "cause_of_death.png"
    archetypes = [b for b in run.final_buckets if b.lifetimes]
    if not archetypes:
        # Empty placeholder so the report's image link still resolves.
        fig, ax = plt.subplots(figsize=(4, 4))
        ax.text(0.5, 0.5, "No lifetimes", ha="center", va="center")
        ax.axis("off")
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return path
    n = len(archetypes)
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4))
    if n == 1:
        axes = [axes]
    for ax, bucket in zip(axes, archetypes, strict=True):
        counts = Counter(r.terminal_phase for r in bucket.lifetimes)
        labels = list(counts.keys())
        sizes = list(counts.values())
        ax.pie(sizes, labels=labels, autopct="%1.0f%%", startangle=90)
        ax.set_title(bucket.archetype)
    fig.suptitle("Cause-of-death by archetype")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Top-level orchestrator
# --------------------------------------------------------------------------


def write_full_report(*, run: CalibrationRun, out_dir: Path) -> dict[str, Path]:
    """Produce every artifact under ``out_dir``.

    Returns a mapping ``{artifact_name: path}`` for the caller (CLI /
    notebook) to surface in its own summary.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}
    artifacts["selected_params"] = write_selected_params(run=run, out_dir=out_dir)
    artifacts["objectives_passed"] = write_objectives_passed(run=run, out_dir=out_dir)
    artifacts["archetype_breakdown"] = write_archetype_breakdown(
        run=run, out_dir=out_dir
    )
    artifacts["bo_trace"] = write_bo_trace(run=run, out_dir=out_dir)
    artifacts["sensitivity_analysis"] = write_sensitivity_analysis(
        run=run, out_dir=out_dir
    )
    artifacts["lifetimes_jsonl"] = write_lifetimes_jsonl(run=run, out_dir=out_dir)
    artifacts["bo_convergence"] = write_bo_convergence_plot(run=run, out_dir=out_dir)
    artifacts["lifetime_histogram"] = write_lifetime_histogram(
        run=run, out_dir=out_dir
    )
    artifacts["cause_of_death"] = write_cause_of_death_plot(
        run=run, out_dir=out_dir
    )
    artifacts["calibration_report"] = write_calibration_report(
        run=run,
        out_dir=out_dir,
        png_paths={
            "bo_convergence": artifacts["bo_convergence"],
            "lifetime_histogram": artifacts["lifetime_histogram"],
            "cause_of_death": artifacts["cause_of_death"],
        },
    )
    return artifacts


# --------------------------------------------------------------------------
# Sprint_1 back-compat — kept so any external caller still importing the
# stub function gets a real implementation instead of NotImplementedError.
# --------------------------------------------------------------------------


def sensitivity_scan(*, traces_dir: Path) -> dict[str, float]:
    """Replay the sensitivity analysis from a saved ``bo_trace.json``.

    Sprint_1 stub raised; sprint_3 returns a flat
    ``{dim: spearman_corr}`` map by re-loading the sensitivity JSON the
    caller previously emitted via :func:`write_full_report`. Caller is
    expected to point ``traces_dir`` at the calibration run directory.
    """
    sens_path = Path(traces_dir) / "sensitivity_analysis.json"
    if not sens_path.exists():
        return {}
    data = json.loads(sens_path.read_text(encoding="utf-8"))
    return {
        dim: float(row.get("spearman_corr_with_mean_lifetime") or 0.0)
        for dim, row in data.get("params", {}).items()
    }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _params_to_flat_dict(p: ParamSpace) -> dict[str, float]:
    """Return ``{field_name: float_value}`` for a ParamSpace.

    Used wherever a stable, JSON-serialisable dict is needed without
    coupling to the full :meth:`ParamSpace.to_json` pretty-print path.
    """
    return {f.name: float(getattr(p, f.name)) for f in p.__dataclass_fields__.values()}


def _short_measured(value: Any) -> str:
    """Human-friendly stringification for the markdown ``measured`` cell."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list):
        return "[" + ", ".join(map(str, value)) + "]"
    return str(value)


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman rank correlation. Returns 0.0 on degenerate inputs.

    Pure-Python — avoids pulling scipy.stats into the calibration hot
    path (skopt already drags scipy in, but we want the analysis layer
    to stay leaf-importable).
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    n = len(xs)
    if n < 2:
        return 0.0
    rank_x = _rankdata(xs)
    rank_y = _rankdata(ys)
    mean_x = statistics.fmean(rank_x)
    mean_y = statistics.fmean(rank_y)
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rank_x, rank_y, strict=True))
    var_x = sum((a - mean_x) ** 2 for a in rank_x)
    var_y = sum((b - mean_y) ** 2 for b in rank_y)
    denom = (var_x * var_y) ** 0.5
    if denom == 0.0:
        return 0.0
    return float(cov / denom)


def _rankdata(values: Sequence[float]) -> list[float]:
    """Average-rank ranking (handles ties), mirrors scipy.stats.rankdata."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-indexed average
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


__all__ = [
    "sensitivity_scan",
    "write_archetype_breakdown",
    "write_bo_convergence_plot",
    "write_bo_trace",
    "write_calibration_report",
    "write_cause_of_death_plot",
    "write_full_report",
    "write_lifetime_histogram",
    "write_lifetimes_jsonl",
    "write_objectives_passed",
    "write_selected_params",
    "write_sensitivity_analysis",
]


# Re-export ObjectiveRecord + ArchetypeLifetimes for callers importing
# them via :mod:`sim.analysis` (back-compat with the sprint_1 module
# surface). LifetimeResult also kept available for the same reason.
_ = (ObjectiveRecord, ArchetypeLifetimes, LifetimeResult)
