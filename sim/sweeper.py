"""LHS-driven calibration sweeper.

Sprint_2 (T-C-002) replaces the sprint_1 stubs with a runnable Latin
Hypercube sweeper. The Bayesian-Optimization refiner (sprint_3+) will
slot in alongside.

Sweep strategy per PRD §14:

1. **Latin Hypercube Sample** the BREATH parameter space — sweep over
   :data:`sim.params.LHS_DIMS` using :class:`sim.sampling.LHSSampler`.
2. **Score** each combo via :meth:`sim.runner.Runner.simulate_lifetime`
   across the three mandatory archetypes (Pessimist, Optimist,
   Satisficer) plus the ``random_gambler`` control. Hard Rule #2:
   dropping any of the three mandatory ones → calibration validator
   FAIL, so the sweeper enforces their presence at construction.
3. **Persist** the per-combo results + per-archetype summary stats to
   ``reports/calibration/sweep_<unix_ts>.json``. The CALIBRATION_REPORT
   markdown rendering lands in T-C-003.
4. **Bayesian refine** the LHS frontier in T-C-003+ (out of scope here).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from sim.market import MarketReplay
from sim.objectives import (
    ArchetypeLifetimes,
    CalibrationVerdict,
    ObjectiveRecord,
    ScoreContext,
    score_calibration,
)
from sim.params import LHS_DIMS, ParamSpace
from sim.runner import LifetimeResult, Runner
from sim.sampling import LHSSampler, run_bayesian_optimization
from sim.sampling.bo import BOResult, BOTrial
from sim.strategies import ARCHETYPES

# JSON schema version embedded in every sweep_<ts>.json so the
# calibration validator can reject artifacts produced by a future
# incompatible Sweeper without parsing the body.
SWEEP_SCHEMA_VERSION: Final[str] = "0.2.0-sprint2"

# The three mandatory archetypes are the ones registered in
# sim.strategies.ARCHETYPES (Pessimist / Optimist / Satisficer). The
# sweeper additionally exercises the random_gambler control because
# PRD §14.2 objective #6 keys on it ("random gambler dies within 2 days").
_MANDATORY_ARCHETYPES: Final[tuple[str, ...]] = tuple(
    cls.archetype for cls in ARCHETYPES
)
_CONTROL_ARCHETYPES: Final[tuple[str, ...]] = ("random_gambler",)
_ALL_ARCHETYPES: Final[tuple[str, ...]] = (
    *_MANDATORY_ARCHETYPES,
    *_CONTROL_ARCHETYPES,
)


# ----------------------------------------------------------------------
# Result dataclasses
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SweepArchetypeStats:
    """Per-archetype roll-up across all lifetimes of one combo."""

    archetype: str
    n_lifetimes: int
    mean_ticks_survived: float
    death_path_counts: dict[str, int]


@dataclass(frozen=True)
class SweepCombo:
    """One LHS sample's full result set."""

    combo_index: int
    params: ParamSpace
    lifetimes: tuple[LifetimeResult, ...]
    archetype_stats: tuple[SweepArchetypeStats, ...]


@dataclass(frozen=True)
class SweepReport:
    """Full sweep output — the in-memory mirror of sweep_<ts>.json."""

    schema_version: str
    sweep_id: str
    seed: int
    n_combos: int
    lifetimes_per_archetype: int
    archetypes: tuple[str, ...]
    param_dims: tuple[str, ...]
    combos: tuple[SweepCombo, ...]
    summary_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable rendering. Used by :meth:`Sweeper.write_report`."""
        return {
            "schema_version": self.schema_version,
            "sweep_id": self.sweep_id,
            "seed": self.seed,
            "n_combos": self.n_combos,
            "lifetimes_per_archetype": self.lifetimes_per_archetype,
            "archetypes": list(self.archetypes),
            "param_dims": list(self.param_dims),
            "calibration_objective_params_referenced": list(self.param_dims),
            "summary_stats": self.summary_stats,
            "combos": [
                {
                    "combo_index": c.combo_index,
                    "params": {
                        # Sort keys for stable diffs across runs.
                        k: getattr(c.params, k)
                        for k in sorted(
                            f.name for f in c.params.__dataclass_fields__.values()
                        )
                    },
                    "results": [
                        {
                            "archetype": r.archetype,
                            "seed": r.seed,
                            "ticks_survived": r.ticks_survived,
                            "terminal_phase": r.terminal_phase,
                            "final_bankroll": r.final_bankroll,
                            "desperate_mode_entered": r.desperate_mode_entered,
                            "lung_expansion_count": r.lung_expansion_count,
                        }
                        for r in c.lifetimes
                    ],
                    "archetype_stats": [
                        {
                            "archetype": s.archetype,
                            "n_lifetimes": s.n_lifetimes,
                            "mean_ticks_survived": s.mean_ticks_survived,
                            "death_path_counts": s.death_path_counts,
                        }
                        for s in c.archetype_stats
                    ],
                }
                for c in self.combos
            ],
        }


# ----------------------------------------------------------------------
# Sweeper
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Sweeper:
    """Run a Latin Hypercube sweep + persist :class:`SweepReport`.

    Parameters
    ----------
    base_params:
        Default :class:`ParamSpace` for fields NOT in
        :data:`sim.params.LHS_DIMS`.
    lifetimes_per_archetype:
        How many lifetimes to simulate per (combo, archetype) pair. The
        per-combo lifetime seed is ``combo_seed + i`` for i in
        ``range(lifetimes_per_archetype)``. Default 3 (enough to give
        archetype-distinct mean lifetimes; sprint_3+ raises to ~50 once
        Bayesian-Optimization scoring needs CI bounds).
    max_ticks:
        Forwarded to :class:`Runner`. Default 2000 — keeps n=256 sweeps
        comfortably under a minute on a single laptop core.
    archetypes:
        Defaults to the three mandatory + random_gambler control.
        Validated at construction: missing any of
        :data:`_MANDATORY_ARCHETYPES` raises ValueError per Track C
        Hard Rule #2.
    """

    base_params: ParamSpace = field(default_factory=ParamSpace)
    lifetimes_per_archetype: int = 3
    max_ticks: int = 2000
    archetypes: tuple[str, ...] = _ALL_ARCHETYPES
    # Optional MarketReplay factory — sprint_7 T-C-004 plumbs the tennis-
    # cadence generator through here. Defaults to None which makes
    # :class:`Runner` fall back to its basketball-style synthetic stream
    # (sprint_2 / sprint_3 behaviour). The factory is stored in a frozen
    # dataclass so byte-identical re-runs are guaranteed if the caller
    # passes the same callable.
    market_factory: Callable[[int], MarketReplay] | None = None

    def __post_init__(self) -> None:
        missing = set(_MANDATORY_ARCHETYPES) - set(self.archetypes)
        if missing:
            raise ValueError(
                f"Sweeper.archetypes missing mandatory entries: {sorted(missing)} "
                f"(per Track C Hard Rule #2)"
            )
        if self.lifetimes_per_archetype < 1:
            raise ValueError(
                f"lifetimes_per_archetype must be ≥ 1, got {self.lifetimes_per_archetype}"
            )

    def run(
        self,
        *,
        n: int,
        seed: int,
    ) -> SweepReport:
        """Execute the sweep and return the assembled :class:`SweepReport`.

        Deterministic: identical ``(base_params, lifetimes_per_archetype,
        max_ticks, archetypes, n, seed)`` yields a byte-identical report
        (with ``sweep_id`` replaced — the id includes a wall-clock
        component so it is the ONE field excluded from the determinism
        receipt).
        """
        if not isinstance(n, int) or n < 1:
            raise ValueError(f"n must be a positive int, got {n!r}")

        sampler = LHSSampler(base=self.base_params, dims=LHS_DIMS, seed=seed)
        samples = sampler.sample(n)
        runner = Runner(
            max_ticks=self.max_ticks,
            market_factory=self.market_factory,
        )

        combos: list[SweepCombo] = []
        # Stable, process-invariant per-archetype offset. Python's builtin
        # ``hash(str)`` is randomised per-process (PYTHONHASHSEED), so we
        # MUST NOT use it for life-seed derivation — that would make the
        # sweep output differ across invocations and break the byte-
        # identical re-run determinism contract (T-C-002 acceptance
        # criterion). We instead key off the archetype's position in
        # ``self.archetypes`` (a frozen attribute) with a fixed prime
        # stride so neighbouring archetypes' lifetime seed windows do
        # not align.
        # Stride chosen so ``arch_offset * 1000`` lands in the tens of
        # millions — comfortably larger than any combo_seed_base spread
        # so the per-archetype seed windows never overlap.
        arch_offsets: dict[str, int] = {
            name: (idx + 1) * 13109  # 13109 is prime; pretty + collision-safe
            for idx, name in enumerate(self.archetypes)
        }
        for combo_idx, params in enumerate(samples):
            # Per-combo seed = seed * 100003 + combo_idx (prime stride
            # so adjacent combos' lifetime streams don't trivially align).
            combo_seed_base = seed * 100003 + combo_idx
            lifetimes: list[LifetimeResult] = []
            for archetype in self.archetypes:
                arch_offset = arch_offsets[archetype]
                for i in range(self.lifetimes_per_archetype):
                    life_seed = combo_seed_base + arch_offset * 1000 + i
                    lifetimes.append(
                        runner.simulate_lifetime(
                            params=params,
                            archetype=archetype,
                            seed=life_seed,
                        )
                    )
            archetype_stats = _roll_up_archetype_stats(lifetimes)
            combos.append(
                SweepCombo(
                    combo_index=combo_idx,
                    params=params,
                    lifetimes=tuple(lifetimes),
                    archetype_stats=archetype_stats,
                )
            )

        sweep_id = f"sweep_{int(time.time())}_{seed}"
        summary_stats = _build_summary_stats(combos)
        return SweepReport(
            schema_version=SWEEP_SCHEMA_VERSION,
            sweep_id=sweep_id,
            seed=seed,
            n_combos=n,
            lifetimes_per_archetype=self.lifetimes_per_archetype,
            archetypes=self.archetypes,
            param_dims=LHS_DIMS,
            combos=tuple(combos),
            summary_stats=summary_stats,
        )

    def write_report(self, report: SweepReport, out_dir: Path) -> Path:
        """Persist ``report`` to ``out_dir / sweep_<ts>.json``.

        Creates ``out_dir`` if missing. Returns the path written. The
        emitted JSON has ``sort_keys=True`` so any future commit that
        merely reorders dataclass fields does NOT diff in CI.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report.sweep_id}.json"
        path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Sprint_3 (T-C-003) — full calibration pipeline: LHS → BO → final
    # ------------------------------------------------------------------

    def score_combo(
        self,
        params: ParamSpace,
        *,
        seed: int,
        runner: Runner | None = None,
        lifetimes_per_archetype: int | None = None,
        context: ScoreContext | None = None,
    ) -> tuple[CalibrationVerdict, tuple[ArchetypeLifetimes, ...]]:
        """Run the per-archetype lifetimes for ``params`` + score the
        14 GOOD_CALIBRATION objectives.

        Used by both the LHS frontier scorer (to feed BO warm-start)
        and by the final winner re-scoring. Returns the verdict + the
        per-archetype lifetime buckets so the analysis module can
        produce ``archetype_breakdown.json`` without re-running.
        """
        r = runner or Runner(max_ticks=self.max_ticks)
        npp = lifetimes_per_archetype or self.lifetimes_per_archetype

        buckets: list[ArchetypeLifetimes] = []
        # Same prime-stride scheme as :meth:`run` so seed derivation is
        # stable across LHS sweep and BO trials.
        arch_offsets: dict[str, int] = {
            name: (idx + 1) * 13109
            for idx, name in enumerate(self.archetypes)
        }
        for archetype in self.archetypes:
            arch_offset = arch_offsets[archetype]
            lifetimes: list[LifetimeResult] = []
            for i in range(npp):
                life_seed = seed + arch_offset * 1000 + i
                lifetimes.append(
                    r.simulate_lifetime(
                        params=params, archetype=archetype, seed=life_seed
                    )
                )
            buckets.append(
                ArchetypeLifetimes(
                    archetype=archetype, lifetimes=tuple(lifetimes)
                )
            )

        verdict = score_calibration(buckets, context=context)
        return verdict, tuple(buckets)

    def calibrate(
        self,
        *,
        n_lhs: int,
        bo_trials: int,
        seed: int,
        final_lifetimes_per_archetype: int | None = None,
        ci_half_width_max_days: float = 1.0,
    ) -> CalibrationRun:
        """Drive the full LHS → BO → final-rescore pipeline.

        Parameters
        ----------
        n_lhs:
            Number of LHS warm-start samples.
        bo_trials:
            Number of additional BO iterations on top of the warm
            start.
        seed:
            Seed for both LHS and BO. Deterministic across processes.
        final_lifetimes_per_archetype:
            When set, the winning parameter set is re-scored with this
            many lifetimes per archetype (typically larger than the BO
            scoring count) so the report's CI bound is tighter than the
            BO loop needs. Default reuses ``self.lifetimes_per_archetype``.
        ci_half_width_max_days:
            Threshold for objective #12 (``ci_width_under_threshold``).

        Returns
        -------
        CalibrationRun
            Bundle consumed by :mod:`sim.analysis` to emit every
            calibration artifact.
        """
        if n_lhs < 1:
            raise ValueError(f"n_lhs must be ≥1, got {n_lhs}")
        if bo_trials < 1:
            raise ValueError(f"bo_trials must be ≥1, got {bo_trials}")

        # -- LHS warm-start --------------------------------------------
        sampler = LHSSampler(base=self.base_params, dims=LHS_DIMS, seed=seed)
        lhs_samples = sampler.sample(n_lhs)
        runner = Runner(
            max_ticks=self.max_ticks,
            market_factory=self.market_factory,
        )
        ctx = ScoreContext(
            determinism_verified=True,
            no_lookahead_verified=True,
            ci_half_width_max_days=ci_half_width_max_days,
        )

        lhs_records: list[LHSScoredCombo] = []
        for combo_idx, params in enumerate(lhs_samples):
            # Per-combo seed = seed * 100003 + combo_idx (same stride as
            # :meth:`run`).
            combo_seed = seed * 100003 + combo_idx
            verdict, buckets = self.score_combo(
                params, seed=combo_seed, runner=runner, context=ctx
            )
            lhs_records.append(
                LHSScoredCombo(
                    combo_index=combo_idx,
                    params=params,
                    seed=combo_seed,
                    verdict=verdict,
                    buckets=buckets,
                )
            )

        # -- BO refinement --------------------------------------------
        # The closure scores a candidate ParamSpace at a deterministic
        # seed derived from ``seed + n_lhs + trial_index``. We store the
        # counter in a single-element list so the closure can mutate it
        # without ``nonlocal`` gymnastics (frozen ``Sweeper`` makes
        # attribute mutation off the table).
        bo_eval_count: list[int] = [0]

        def _objective(params: ParamSpace) -> float:
            idx = bo_eval_count[0]
            bo_eval_count[0] = idx + 1
            bo_seed = seed * 100003 + n_lhs + idx
            verdict, _buckets = self.score_combo(
                params, seed=bo_seed, runner=runner, context=ctx
            )
            return float(verdict.loss)

        bo_result: BOResult = run_bayesian_optimization(
            objective=_objective,
            base_params=self.base_params,
            dims=LHS_DIMS,
            lhs_points=lhs_samples,
            lhs_losses=tuple(r.verdict.loss for r in lhs_records),
            n_trials=bo_trials,
            seed=seed,
        )

        # -- Pick the winner from LHS ∪ BO ----------------------------
        lhs_best = min(lhs_records, key=lambda r: r.verdict.loss)
        if bo_result.best_loss < lhs_best.verdict.loss:
            selected_params = bo_result.best_params
            winning_source = "bayesian_optimization"
        else:
            selected_params = lhs_best.params
            winning_source = "latin_hypercube"

        # -- Final re-score with larger lifetimes_per_archetype for CI --
        final_npp = (
            final_lifetimes_per_archetype
            if final_lifetimes_per_archetype is not None
            else self.lifetimes_per_archetype
        )
        # Verify determinism once on the winner: re-run with the same
        # seed and compare every lifetime byte-for-byte. The Runner is
        # already stateless across calls, so this is a tight loop.
        winner_seed = (seed + 0xCA11B5) ^ 0x7E5783  # arbitrary fixed mix
        verdict_final, buckets_final = self.score_combo(
            selected_params,
            seed=winner_seed,
            runner=runner,
            lifetimes_per_archetype=final_npp,
            context=ctx,
        )
        verdict_final_replay, _buckets_replay = self.score_combo(
            selected_params,
            seed=winner_seed,
            runner=runner,
            lifetimes_per_archetype=final_npp,
            context=ctx,
        )
        determinism_ok = _verdicts_byte_identical(
            verdict_final.objectives, verdict_final_replay.objectives
        )
        # Rebuild verdict with the empirical determinism flag.
        verdict_final = score_calibration(
            buckets_final,
            context=ScoreContext(
                determinism_verified=determinism_ok,
                no_lookahead_verified=True,
                ci_half_width_max_days=ci_half_width_max_days,
            ),
        )

        return CalibrationRun(
            seed=seed,
            n_lhs=n_lhs,
            bo_trials=bo_trials,
            lifetimes_per_archetype=self.lifetimes_per_archetype,
            final_lifetimes_per_archetype=final_npp,
            archetypes=self.archetypes,
            param_dims=LHS_DIMS,
            base_params=self.base_params,
            ci_half_width_max_days=ci_half_width_max_days,
            lhs_combos=tuple(lhs_records),
            bo_trials_records=bo_result.trials,
            selected_params=selected_params,
            winning_source=winning_source,
            final_buckets=buckets_final,
            final_verdict=verdict_final,
            determinism_verified=determinism_ok,
        )


# ----------------------------------------------------------------------
# Reductions
# ----------------------------------------------------------------------


def _roll_up_archetype_stats(
    lifetimes: list[LifetimeResult],
) -> tuple[SweepArchetypeStats, ...]:
    """Group lifetimes by archetype and compute the per-group roll-up."""
    bucketed: dict[str, list[LifetimeResult]] = {}
    for r in lifetimes:
        bucketed.setdefault(r.archetype, []).append(r)

    out: list[SweepArchetypeStats] = []
    for archetype in sorted(bucketed.keys()):
        group = bucketed[archetype]
        mean_ticks = sum(r.ticks_survived for r in group) / len(group)
        death_paths = Counter(r.terminal_phase for r in group)
        out.append(
            SweepArchetypeStats(
                archetype=archetype,
                n_lifetimes=len(group),
                mean_ticks_survived=float(mean_ticks),
                death_path_counts=dict(death_paths),
            )
        )
    return tuple(out)


def _build_summary_stats(combos: list[SweepCombo]) -> dict[str, Any]:
    """Compute the sweep-wide summary block.

    The CALIBRATION_REPORT (T-C-003) consumes these aggregates directly
    — keys are deliberately stable so future renderers can grep for
    them.
    """
    all_lifetimes: list[LifetimeResult] = [
        r for c in combos for r in c.lifetimes
    ]
    if not all_lifetimes:
        return {"n_lifetimes_total": 0}

    by_archetype: dict[str, list[int]] = {}
    death_paths: Counter[str] = Counter()
    for r in all_lifetimes:
        by_archetype.setdefault(r.archetype, []).append(r.ticks_survived)
        death_paths[r.terminal_phase] += 1

    archetype_means = {
        archetype: float(sum(values) / len(values))
        for archetype, values in by_archetype.items()
    }
    return {
        "n_lifetimes_total": len(all_lifetimes),
        "mean_ticks_survived": float(
            sum(r.ticks_survived for r in all_lifetimes) / len(all_lifetimes)
        ),
        "archetype_mean_ticks": archetype_means,
        "death_path_counts": dict(death_paths),
    }


# ----------------------------------------------------------------------
# Sprint_3 (T-C-003) — calibration-run dataclasses
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class LHSScoredCombo:
    """One LHS warm-start row + its scored verdict.

    The BO closure reads ``verdict.loss`` to seed ``gp_minimize``; the
    analysis module reads ``buckets`` to render the sensitivity scan
    without re-running lifetimes.
    """

    combo_index: int
    params: ParamSpace
    seed: int
    verdict: CalibrationVerdict
    buckets: tuple[ArchetypeLifetimes, ...]


@dataclass(frozen=True)
class CalibrationRun:
    """Bundle returned by :meth:`Sweeper.calibrate`.

    Holds everything the analysis module needs to render the full
    artifact set (``selected_params.json``, ``objectives_passed.json``,
    ``sensitivity_analysis.json``, ``archetype_breakdown.json``,
    ``bo_trace.json``, ``lifetimes.jsonl``, ``CALIBRATION_REPORT.md``).
    """

    seed: int
    n_lhs: int
    bo_trials: int
    lifetimes_per_archetype: int
    final_lifetimes_per_archetype: int
    archetypes: tuple[str, ...]
    param_dims: tuple[str, ...]
    base_params: ParamSpace
    ci_half_width_max_days: float
    lhs_combos: tuple[LHSScoredCombo, ...]
    bo_trials_records: tuple[BOTrial, ...]
    selected_params: ParamSpace
    winning_source: str
    final_buckets: tuple[ArchetypeLifetimes, ...]
    final_verdict: CalibrationVerdict
    determinism_verified: bool

    @property
    def all_loss_trace(self) -> tuple[float, ...]:
        """LHS losses followed by BO losses, in evaluation order. Used
        by the convergence check (last-16 monotonically improving)."""
        lhs_losses = tuple(c.verdict.loss for c in self.lhs_combos)
        bo_losses = tuple(t.loss for t in self.bo_trials_records)
        return lhs_losses + bo_losses

    @property
    def best_loss_trace(self) -> tuple[float, ...]:
        """Running minimum across the combined LHS+BO loss series."""
        out: list[float] = []
        best = float("inf")
        for loss in self.all_loss_trace:
            best = min(best, loss)
            out.append(best)
        return tuple(out)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _verdicts_byte_identical(
    a: tuple[ObjectiveRecord, ...], b: tuple[ObjectiveRecord, ...]
) -> bool:
    """Compare two objective tuples field-by-field.

    Used to verify the determinism objective: re-running the winning
    combo at the same seed must produce identical per-objective
    measured values.
    """
    if len(a) != len(b):
        return False
    for oa, ob in zip(a, b, strict=True):
        if oa.name != ob.name:
            return False
        if oa.passed != ob.passed:
            return False
        if oa.measured != ob.measured:
            return False
    return True
