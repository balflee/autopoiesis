"""GOOD_CALIBRATION objectives — full sprint_3 (T-C-003) implementation.

This module enumerates the **14 objectives** the calibration sweep is
graded against, per PRD §14.2 + DEV_FRAMEWORK §26 T2.7. Sprint_1 shipped
the canonical key set as a stub; sprint_3 wires each objective to a
scoring function that accepts the per-archetype lifetimes produced by
:mod:`sim.sweeper` + :mod:`sim.runner` and returns an
:class:`ObjectiveRecord` with measured value, threshold, and penalty.

The 14 objectives — DO NOT RENAME (the Track C calibration validator
keys verdict JSON on these exact strings)
-------------------------------------------------------------------

1. ``mean_lifetime_3_to_7_days`` — average agent lifetime falls in
   3-7 days.
2. ``desperate_mode_trigger_rate_60_to_85`` — Desperate Mode triggers
   in 60-85% of lifetimes.
3. ``terminal_lucidity_rate_above_85`` — Terminal Lucidity reached in
   >85% of dying agents. Proxied as: a dying agent that entered
   Desperate Mode before death "got a chance to think clearly" — see
   :func:`_terminal_lucidity_rate`.
4. ``lung_expansion_count_1_to_3`` — average Lung Expansion count
   1-3.
5. ``three_death_paths_all_observed`` — Attrition, Starvation,
   TradingLoss all observed.
6. ``random_gambler_dies_within_2_days`` — a pure-random control dies
   in <2 days.
7. ``satisficer_dies_faster_than_optimist`` — anti-laziness invariant.
8. ``pessimist_lifetime_bounded`` — Pessimist passive-burn bound
   sanity (1-14 days).
9. ``optimist_bankroll_volatility_high`` — Optimist drawdown stdev
   above an absolute floor.
10. ``no_immortal_outcomes`` — zero lifetimes exceed the cap.
11. ``no_zero_tick_deaths`` — every lifetime has ≥1 productive tick.
12. ``ci_width_under_threshold`` — mean-lifetime 95% CI half-width
    below threshold (no early-stopping without CI).
13. ``determinism_round_trip`` — re-running with the same seed yields
    identical traces.
14. ``no_lookahead_violations`` — ``feature_ts <= decision_ts``
    everywhere (no :class:`sim.market.LookaheadError` raised).

Tick → days conversion
----------------------

The runner counts ticks; PRD §14.2 thresholds quote days. We anchor
``1 day = TICKS_PER_DAY = 144`` ticks (one tick per ten minutes, the
PRD §6 default polling cadence — exact choice is consistent with
TECHNICAL_PLAN.md §3.5's market polling cycle). The conversion is a
module-level constant so the calibration validator can replay it and
auditors can find a single source of truth.

Scoring shape
-------------

:func:`score_calibration` returns a triple
``(passed: int, total: int, per_objective: dict[str, ObjectiveRecord])``.
The aggregate **loss** used by Bayesian Optimization is computed by
:func:`aggregate_loss`: ``-(passed / 14) + 0.05 * penalty_sum`` where
the per-objective ``penalty`` is the normalised distance from the
threshold (zero when the objective passes, otherwise in ``[0, 1]``).
This gives BO a smooth gradient toward the calibrated region even
before any objective starts passing.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from sim.runner import LifetimeResult

# Canonical objective names — kept as a module-level Final tuple so the
# validator can import the exact ordering without grepping docstrings.
GOOD_CALIBRATION_OBJECTIVES: Final[tuple[str, ...]] = (
    "mean_lifetime_3_to_7_days",
    "desperate_mode_trigger_rate_60_to_85",
    "terminal_lucidity_rate_above_85",
    "lung_expansion_count_1_to_3",
    "three_death_paths_all_observed",
    "random_gambler_dies_within_2_days",
    "satisficer_dies_faster_than_optimist",
    "pessimist_lifetime_bounded",
    "optimist_bankroll_volatility_high",
    "no_immortal_outcomes",
    "no_zero_tick_deaths",
    "ci_width_under_threshold",
    "determinism_round_trip",
    "no_lookahead_violations",
)

# Tick → days anchor. See module docstring "Tick → days conversion".
TICKS_PER_DAY: Final[int] = 144

# Aggregate-loss penalty scale. The penalty is normalised to ``[0, 1]``
# per objective; weighting at 0.05 keeps the integer ``-passed_count/14``
# term dominant (so BO always prefers a parameter set that passes one
# MORE objective over one that has a marginally tighter unmet objective).
_PENALTY_WEIGHT: Final[float] = 0.05


# ---------------------------------------------------------------------------
# Record + helper dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObjectiveRecord:
    """One row in ``objectives_passed.json``.

    Shape A (per ``calibration_diag.py`` line 200-218 docstring) —
    rich per-objective record. The compact Shape B is intentionally
    NOT produced; calibration_diag accepts both, and Shape A carries
    enough context that the reviewer-calibration-validator can audit a
    shortfall without re-running the sim.

    Attributes
    ----------
    name: PRD-canonical objective name (see :data:`GOOD_CALIBRATION_OBJECTIVES`).
    passed: True iff the measured value satisfies the threshold.
    measured: The empirical value — float, str, list, or dict depending
        on the objective. JSON-serialisable.
    threshold: Human-readable threshold string (e.g. ``"in [3.0, 7.0]"``).
    penalty: ``[0, 1]`` — distance from the threshold, normalised. Zero
        when the objective passes. Used by :func:`aggregate_loss`.
    notes: Optional free-form context the report renderer can surface.
    """

    name: str
    passed: bool
    measured: Any
    threshold: str
    penalty: float
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly rendering — matches the Shape A schema."""
        return asdict(self)


@dataclass(frozen=True)
class CalibrationVerdict:
    """Composite verdict returned by :func:`score_calibration`.

    Attributes
    ----------
    passed_count: Integer count of objectives whose ``passed`` is True.
    total_count: Always ``len(GOOD_CALIBRATION_OBJECTIVES) == 14``.
    objectives: Tuple of :class:`ObjectiveRecord`, in the canonical
        order. The downstream JSON writer iterates this directly.
    loss: Aggregate BO loss per :func:`aggregate_loss`.
    """

    passed_count: int
    total_count: int
    objectives: tuple[ObjectiveRecord, ...]
    loss: float

    @property
    def per_objective(self) -> dict[str, ObjectiveRecord]:
        """Name-indexed view for ergonomic test assertions."""
        return {o.name: o for o in self.objectives}

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable rendering for ``objectives_passed.json``."""
        return {
            "loss": self.loss,
            "passed_count": self.passed_count,
            "total_count": self.total_count,
            "objectives": [o.to_dict() for o in self.objectives],
        }


@dataclass(frozen=True)
class ArchetypeLifetimes:
    """Group of lifetimes for a single archetype, used as input to the
    per-objective scoring functions. Kept simple so tests can build it
    by hand without spinning up the full sweeper.
    """

    archetype: str
    lifetimes: tuple[LifetimeResult, ...]


# ---------------------------------------------------------------------------
# Per-objective scoring — one pure function per objective, all returning
# an :class:`ObjectiveRecord` so :func:`score_calibration` can stitch
# them without per-name special cases.
# ---------------------------------------------------------------------------


def _all_lifetimes(buckets: Sequence[ArchetypeLifetimes]) -> tuple[LifetimeResult, ...]:
    return tuple(r for b in buckets for r in b.lifetimes)


def _ticks_to_days(ticks: float) -> float:
    return ticks / float(TICKS_PER_DAY)


def _passing_penalty() -> float:
    """Penalty value for a passing objective. Zero — BO is rewarded
    only for objectives that actually clear the threshold."""
    return 0.0


def _ratio_penalty(measured: float, low: float, high: float) -> float:
    """Penalty when a measured value must fall in ``[low, high]``.

    Zero inside the band; otherwise grows linearly with distance,
    normalised by ``(high - low)`` so different-scale objectives
    contribute comparable signal to the BO loss. Clipped to ``[0, 1]``.
    """
    width = max(high - low, 1e-9)
    if measured < low:
        return float(min(1.0, (low - measured) / width))
    if measured > high:
        return float(min(1.0, (measured - high) / width))
    return 0.0


def _floor_penalty(measured: float, floor: float, *, scale: float) -> float:
    """Penalty when ``measured`` must be >= ``floor``."""
    if measured >= floor:
        return 0.0
    return float(min(1.0, (floor - measured) / max(scale, 1e-9)))


def _ceiling_penalty(measured: float, ceiling: float, *, scale: float) -> float:
    """Penalty when ``measured`` must be <= ``ceiling``."""
    if measured <= ceiling:
        return 0.0
    return float(min(1.0, (measured - ceiling) / max(scale, 1e-9)))


# Objective 1 ---------------------------------------------------------------


def objective_mean_lifetime_days(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Mean lifetime over the three mandatory archetypes lies in [3, 7] days.

    PRD §14.2 quotes the band on the canonical Pessimist/Optimist/Satisficer
    aggregate — the random_gambler control is deliberately excluded so
    its short lifetime does not deflate the headline number.
    """
    mandatory = [b for b in buckets if b.archetype in {"pessimist", "optimist", "satisficer"}]
    ticks = [r.ticks_survived for b in mandatory for r in b.lifetimes]
    if not ticks:
        return ObjectiveRecord(
            name="mean_lifetime_3_to_7_days",
            passed=False,
            measured=None,
            threshold="mean in [3.0, 7.0] days",
            penalty=1.0,
            notes="no lifetimes observed",
        )
    mean_days = _ticks_to_days(statistics.fmean(ticks))
    passed = 3.0 <= mean_days <= 7.0
    return ObjectiveRecord(
        name="mean_lifetime_3_to_7_days",
        passed=passed,
        measured=round(mean_days, 4),
        threshold="mean in [3.0, 7.0] days",
        penalty=_passing_penalty() if passed else _ratio_penalty(mean_days, 3.0, 7.0),
        notes=f"n_lifetimes={len(ticks)} mandatory archetypes only",
    )


# Objective 2 ---------------------------------------------------------------


def objective_desperate_mode_trigger_rate(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Desperate Mode triggers in 60-85% of mandatory-archetype lifetimes."""
    mandatory = [b for b in buckets if b.archetype in {"pessimist", "optimist", "satisficer"}]
    lifetimes = [r for b in mandatory for r in b.lifetimes]
    if not lifetimes:
        return ObjectiveRecord(
            name="desperate_mode_trigger_rate_60_to_85",
            passed=False,
            measured=None,
            threshold="rate in [0.60, 0.85]",
            penalty=1.0,
            notes="no lifetimes observed",
        )
    triggered = sum(1 for r in lifetimes if r.desperate_mode_entered)
    rate = triggered / len(lifetimes)
    passed = 0.60 <= rate <= 0.85
    return ObjectiveRecord(
        name="desperate_mode_trigger_rate_60_to_85",
        passed=passed,
        measured=round(rate, 4),
        threshold="rate in [0.60, 0.85]",
        penalty=_passing_penalty() if passed else _ratio_penalty(rate, 0.60, 0.85),
        notes=f"triggered={triggered}/{len(lifetimes)}",
    )


# Objective 3 ---------------------------------------------------------------


def objective_terminal_lucidity_rate(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Of dying mandatory-archetype agents, >85% reached Terminal Lucidity.

    Proxy used here: a dying agent (any death path) that **entered
    Desperate Mode before death** "had a moment of clarity" — this is
    the operational definition the sim can compute without instrumenting
    the LLM phases. PRD §5 documents the full lucidity arc; the
    proxy is calibrated so a fully passive-burn death (no desperate
    trigger) does NOT count as lucid, matching the PRD's intent.
    """
    mandatory = [b for b in buckets if b.archetype in {"pessimist", "optimist", "satisficer"}]
    dying = [r for b in mandatory for r in b.lifetimes if r.terminal_phase != "Survival"]
    if not dying:
        return ObjectiveRecord(
            name="terminal_lucidity_rate_above_85",
            passed=False,
            measured=None,
            threshold="lucidity_rate > 0.85",
            penalty=1.0,
            notes="no dying agents observed",
        )
    lucid = sum(1 for r in dying if r.desperate_mode_entered)
    rate = lucid / len(dying)
    passed = rate > 0.85
    return ObjectiveRecord(
        name="terminal_lucidity_rate_above_85",
        passed=passed,
        measured=round(rate, 4),
        threshold="lucidity_rate > 0.85",
        penalty=_passing_penalty() if passed else _floor_penalty(rate, 0.85, scale=0.85),
        notes=f"lucid={lucid}/{len(dying)} dying agents",
    )


# Objective 4 ---------------------------------------------------------------


def objective_lung_expansion_count(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Mean Lung Expansion count per lifetime in [1, 3] across mandatory archetypes."""
    mandatory = [b for b in buckets if b.archetype in {"pessimist", "optimist", "satisficer"}]
    counts = [r.lung_expansion_count for b in mandatory for r in b.lifetimes]
    if not counts:
        return ObjectiveRecord(
            name="lung_expansion_count_1_to_3",
            passed=False,
            measured=None,
            threshold="mean in [1.0, 3.0]",
            penalty=1.0,
            notes="no lifetimes observed",
        )
    mean_count = statistics.fmean(counts)
    passed = 1.0 <= mean_count <= 3.0
    return ObjectiveRecord(
        name="lung_expansion_count_1_to_3",
        passed=passed,
        measured=round(mean_count, 4),
        threshold="mean in [1.0, 3.0]",
        penalty=_passing_penalty() if passed else _ratio_penalty(mean_count, 1.0, 3.0),
        notes=f"n_lifetimes={len(counts)}",
    )


# Objective 5 ---------------------------------------------------------------


_REQUIRED_DEATH_PATHS: Final[frozenset[str]] = frozenset(
    {"Attrition", "Starvation", "TradingLoss"}
)


def objective_three_death_paths(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """All three documented death paths observed across the calibration set.

    PRD §6 documents Attrition / Starvation / TradingLoss. Survival
    outcomes don't count. The control archetype counts toward
    coverage — it's still observed behaviour.
    """
    lifetimes = _all_lifetimes(buckets)
    observed_paths = {r.terminal_phase for r in lifetimes if r.terminal_phase != "Survival"}
    missing = _REQUIRED_DEATH_PATHS - observed_paths
    passed = not missing
    # Penalty: 1 - (observed_required / 3).
    observed_required = len(_REQUIRED_DEATH_PATHS & observed_paths)
    penalty = 0.0 if passed else (3 - observed_required) / 3.0
    return ObjectiveRecord(
        name="three_death_paths_all_observed",
        passed=passed,
        measured=sorted(observed_paths),
        threshold="superset of {Attrition, Starvation, TradingLoss}",
        penalty=penalty,
        notes=(
            f"observed_required={observed_required}/3"
            + (f"; missing={sorted(missing)}" if missing else "")
        ),
    )


# Objective 6 ---------------------------------------------------------------


def objective_random_gambler_dies_within_2_days(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Random gambler's mean lifetime < 2 days. The control archetype
    is required by PRD §14.2; missing it is a calibration FAIL.
    """
    gamblers = [b for b in buckets if b.archetype == "random_gambler"]
    lifetimes = [r for b in gamblers for r in b.lifetimes]
    if not lifetimes:
        return ObjectiveRecord(
            name="random_gambler_dies_within_2_days",
            passed=False,
            measured=None,
            threshold="mean_lifetime_days < 2.0",
            penalty=1.0,
            notes="random_gambler control missing from sweep",
        )
    mean_days = _ticks_to_days(statistics.fmean(r.ticks_survived for r in lifetimes))
    passed = mean_days < 2.0
    return ObjectiveRecord(
        name="random_gambler_dies_within_2_days",
        passed=passed,
        measured=round(mean_days, 4),
        threshold="mean_lifetime_days < 2.0",
        penalty=(
            _passing_penalty() if passed else _ceiling_penalty(mean_days, 2.0, scale=2.0)
        ),
        notes=f"n_lifetimes={len(lifetimes)} (control archetype)",
    )


# Objective 7 ---------------------------------------------------------------


def objective_satisficer_dies_faster_than_optimist(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Anti-laziness invariant — Satisficer mean lifetime < Optimist mean lifetime.

    PRD §14.2: "If the easy-edge bettor outlives the careful one, the
    calibration encourages laziness — reject."
    """
    by_arch: dict[str, list[int]] = {b.archetype: [r.ticks_survived for r in b.lifetimes] for b in buckets}
    optimist = by_arch.get("optimist", [])
    satisficer = by_arch.get("satisficer", [])
    if not optimist or not satisficer:
        return ObjectiveRecord(
            name="satisficer_dies_faster_than_optimist",
            passed=False,
            measured=None,
            threshold="mean(satisficer_ticks) < mean(optimist_ticks)",
            penalty=1.0,
            notes="missing Optimist or Satisficer lifetimes",
        )
    mean_opt = statistics.fmean(optimist)
    mean_sat = statistics.fmean(satisficer)
    passed = mean_sat < mean_opt
    # Penalty: normalised by max(mean_opt, 1) — a saved gradient even
    # when satisficer hasn't moved below.
    penalty = 0.0 if passed else float(min(1.0, (mean_sat - mean_opt) / max(mean_opt, 1.0)))
    return ObjectiveRecord(
        name="satisficer_dies_faster_than_optimist",
        passed=passed,
        measured={
            "optimist_mean_ticks": round(mean_opt, 2),
            "satisficer_mean_ticks": round(mean_sat, 2),
        },
        threshold="mean(satisficer_ticks) < mean(optimist_ticks)",
        penalty=penalty,
        notes=f"optimist_n={len(optimist)} satisficer_n={len(satisficer)}",
    )


# Objective 8 ---------------------------------------------------------------


def objective_pessimist_lifetime_bounded(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Pessimist mean lifetime in [1, 14] days — neither instant death
    nor escape past the sweep's lifetime cap. Sanity bound on the
    passive-burn floor.
    """
    pessimist = [b for b in buckets if b.archetype == "pessimist"]
    lifetimes = [r for b in pessimist for r in b.lifetimes]
    if not lifetimes:
        return ObjectiveRecord(
            name="pessimist_lifetime_bounded",
            passed=False,
            measured=None,
            threshold="pessimist_mean in [1.0, 14.0] days",
            penalty=1.0,
            notes="missing Pessimist lifetimes",
        )
    mean_days = _ticks_to_days(statistics.fmean(r.ticks_survived for r in lifetimes))
    passed = 1.0 <= mean_days <= 14.0
    return ObjectiveRecord(
        name="pessimist_lifetime_bounded",
        passed=passed,
        measured=round(mean_days, 4),
        threshold="pessimist_mean in [1.0, 14.0] days",
        penalty=_passing_penalty() if passed else _ratio_penalty(mean_days, 1.0, 14.0),
        notes=f"n_lifetimes={len(lifetimes)}",
    )


# Objective 9 ---------------------------------------------------------------


def objective_optimist_bankroll_volatility(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Optimist final-bankroll stdev exceeds an absolute USD floor.

    PRD §14.2: the Optimist must visibly drawdown — if its bankroll is
    flat across seeds, the calibration is suppressing variance and the
    LLM's edge cannot show. Floor of $10 USD stdev across the lifetime
    population is the operational threshold.
    """
    optimist = [b for b in buckets if b.archetype == "optimist"]
    bankrolls = [r.final_bankroll for b in optimist for r in b.lifetimes]
    if len(bankrolls) < 2:
        return ObjectiveRecord(
            name="optimist_bankroll_volatility_high",
            passed=False,
            measured=None,
            threshold="stdev(final_bankroll) > 10.0 USD",
            penalty=1.0,
            notes="need ≥2 optimist lifetimes for stdev",
        )
    stdev = statistics.stdev(bankrolls)
    passed = stdev > 10.0
    return ObjectiveRecord(
        name="optimist_bankroll_volatility_high",
        passed=passed,
        measured=round(stdev, 4),
        threshold="stdev(final_bankroll) > 10.0 USD",
        penalty=_passing_penalty() if passed else _floor_penalty(stdev, 10.0, scale=10.0),
        notes=f"optimist_n={len(bankrolls)}",
    )


# Objective 10 --------------------------------------------------------------


def objective_no_immortal_outcomes(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Zero lifetimes terminate in the Survival cap."""
    lifetimes = _all_lifetimes(buckets)
    immortal = [r for r in lifetimes if r.terminal_phase == "Survival"]
    passed = len(immortal) == 0
    # Penalty: fraction of immortals — direct, capped at 1.
    rate = (len(immortal) / len(lifetimes)) if lifetimes else 1.0
    return ObjectiveRecord(
        name="no_immortal_outcomes",
        passed=passed,
        measured=len(immortal),
        threshold="count == 0",
        penalty=_passing_penalty() if passed else float(min(1.0, rate)),
        notes=f"immortal={len(immortal)}/{len(lifetimes)}",
    )


# Objective 11 --------------------------------------------------------------


def objective_no_zero_tick_deaths(
    buckets: Sequence[ArchetypeLifetimes],
) -> ObjectiveRecord:
    """Every lifetime survives at least one productive tick."""
    lifetimes = _all_lifetimes(buckets)
    zero_tick = [r for r in lifetimes if r.ticks_survived < 1]
    passed = len(zero_tick) == 0
    rate = (len(zero_tick) / len(lifetimes)) if lifetimes else 1.0
    return ObjectiveRecord(
        name="no_zero_tick_deaths",
        passed=passed,
        measured=len(zero_tick),
        threshold="count == 0",
        penalty=_passing_penalty() if passed else float(min(1.0, rate)),
        notes=f"zero_tick={len(zero_tick)}/{len(lifetimes)}",
    )


# Objective 12 --------------------------------------------------------------


def objective_ci_width_under_threshold(
    buckets: Sequence[ArchetypeLifetimes],
    *,
    ci_half_width_max_days: float = 1.0,
) -> ObjectiveRecord:
    """95% CI half-width of the mean lifetime is below the threshold.

    PRD §14.2 forbids early-stopping without a CI bound. We compute
    the 1.96·σ/√n half-width over the mandatory-archetype lifetimes
    (the same population objective #1 averages) and require it to be
    smaller than ``ci_half_width_max_days`` (default 1 day).
    """
    mandatory = [b for b in buckets if b.archetype in {"pessimist", "optimist", "satisficer"}]
    ticks = [r.ticks_survived for b in mandatory for r in b.lifetimes]
    if len(ticks) < 2:
        return ObjectiveRecord(
            name="ci_width_under_threshold",
            passed=False,
            measured=None,
            threshold=f"95% CI half-width < {ci_half_width_max_days} days",
            penalty=1.0,
            notes="need ≥2 mandatory-archetype lifetimes for CI",
        )
    stdev_ticks = statistics.stdev(ticks)
    half_width_ticks = 1.96 * stdev_ticks / math.sqrt(len(ticks))
    half_width_days = _ticks_to_days(half_width_ticks)
    passed = half_width_days < ci_half_width_max_days
    return ObjectiveRecord(
        name="ci_width_under_threshold",
        passed=passed,
        measured=round(half_width_days, 4),
        threshold=f"95% CI half-width < {ci_half_width_max_days} days",
        penalty=(
            _passing_penalty()
            if passed
            else _ceiling_penalty(
                half_width_days, ci_half_width_max_days, scale=ci_half_width_max_days
            )
        ),
        notes=f"n_lifetimes={len(ticks)} stdev_ticks={stdev_ticks:.2f}",
    )


# Objective 13 --------------------------------------------------------------


def objective_determinism_round_trip(
    *,
    determinism_verified: bool,
) -> ObjectiveRecord:
    """Framework-level — re-running with the same seed yields identical
    traces. Verified once per calibration run by re-running the selected
    combo with a fixed seed and comparing byte-for-byte; the boolean
    is plumbed in via the runner.
    """
    return ObjectiveRecord(
        name="determinism_round_trip",
        passed=determinism_verified,
        measured=determinism_verified,
        threshold="re-run with same seed == original",
        penalty=_passing_penalty() if determinism_verified else 1.0,
        notes="byte-identical re-run check",
    )


# Objective 14 --------------------------------------------------------------


def objective_no_lookahead_violations(
    *,
    no_lookahead_verified: bool,
) -> ObjectiveRecord:
    """Framework-level — no :class:`sim.market.LookaheadError` raised
    during the sweep. PRD §14 + Hard Rule #3.
    """
    return ObjectiveRecord(
        name="no_lookahead_violations",
        passed=no_lookahead_verified,
        measured=no_lookahead_verified,
        threshold="no LookaheadError raised in sweep",
        penalty=_passing_penalty() if no_lookahead_verified else 1.0,
        notes="market.py invariant",
    )


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreContext:
    """Auxiliary inputs to :func:`score_calibration` that are NOT derived
    from the lifetime traces alone.

    The two framework-level objectives (determinism, no_lookahead) are
    properties of the sweeper, not of any particular parameter combo.
    The sweeper sets them once per run; the per-combo BO scorer assumes
    them ``True`` (they would have raised mid-sweep otherwise).
    """

    determinism_verified: bool = True
    no_lookahead_verified: bool = True
    ci_half_width_max_days: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


def score_calibration(
    buckets: Sequence[ArchetypeLifetimes],
    *,
    context: ScoreContext | None = None,
) -> CalibrationVerdict:
    """Score the 14 GOOD_CALIBRATION objectives over ``buckets``.

    Parameters
    ----------
    buckets:
        Per-archetype groupings of lifetimes. The three mandatory
        archetypes (Pessimist / Optimist / Satisficer) MUST appear;
        the random_gambler control SHOULD appear (objective #6 fails
        without it). Order is irrelevant.
    context:
        Framework-level scoring auxiliary — determinism + no_lookahead
        booleans. Defaults to a context where both are True (the
        sweeper's nominal state).

    Returns
    -------
    CalibrationVerdict
        Composite verdict; iterate ``.objectives`` for per-objective
        records. ``.passed_count`` and ``.loss`` are the headline
        numbers BO + the report renderer consume.
    """
    ctx = context or ScoreContext()
    objectives: list[ObjectiveRecord] = [
        objective_mean_lifetime_days(buckets),
        objective_desperate_mode_trigger_rate(buckets),
        objective_terminal_lucidity_rate(buckets),
        objective_lung_expansion_count(buckets),
        objective_three_death_paths(buckets),
        objective_random_gambler_dies_within_2_days(buckets),
        objective_satisficer_dies_faster_than_optimist(buckets),
        objective_pessimist_lifetime_bounded(buckets),
        objective_optimist_bankroll_volatility(buckets),
        objective_no_immortal_outcomes(buckets),
        objective_no_zero_tick_deaths(buckets),
        objective_ci_width_under_threshold(
            buckets, ci_half_width_max_days=ctx.ci_half_width_max_days
        ),
        objective_determinism_round_trip(
            determinism_verified=ctx.determinism_verified
        ),
        objective_no_lookahead_violations(
            no_lookahead_verified=ctx.no_lookahead_verified
        ),
    ]

    # Defensive: the validator keys verdict JSON on the exact name set.
    # If a future commit forgets an objective, fail loud here instead of
    # silently shipping a 13-objective verdict.
    observed_names = tuple(o.name for o in objectives)
    if observed_names != GOOD_CALIBRATION_OBJECTIVES:
        raise RuntimeError(
            "score_calibration produced objectives in unexpected order: "
            f"{observed_names!r} (expected {GOOD_CALIBRATION_OBJECTIVES!r})"
        )

    passed_count = sum(1 for o in objectives if o.passed)
    loss = aggregate_loss(objectives)
    return CalibrationVerdict(
        passed_count=passed_count,
        total_count=len(GOOD_CALIBRATION_OBJECTIVES),
        objectives=tuple(objectives),
        loss=loss,
    )


def aggregate_loss(objectives: Iterable[ObjectiveRecord]) -> float:
    """Aggregate BO loss: ``-(passed_count / 14) + 0.05 * penalty_sum``.

    Both terms are bounded; the integer term dominates (one extra pass
    is worth ``1/14 ≈ 0.0714``, while the maximum penalty contribution
    is ``0.05 * 14 = 0.7`` total but in practice much smaller).
    The minimum (best) loss is ``-1.0`` (all 14 pass, zero penalty).
    """
    objs = tuple(objectives)
    if not objs:
        return 0.0
    passed = sum(1 for o in objs if o.passed)
    penalty_sum = sum(o.penalty for o in objs)
    return -(passed / len(objs)) + _PENALTY_WEIGHT * penalty_sum


# ---------------------------------------------------------------------------
# Sprint_1 back-compat stub — preserved so tests/sim/test_smoke.py
# continues to pass without modification.
# ---------------------------------------------------------------------------


def score_objectives() -> dict[str, bool]:
    """Sprint_1 back-compat: per-objective pass/fail map with every
    value False.

    The real scoring entrypoint is :func:`score_calibration`. This
    function is retained because ``tests/sim/test_smoke.py`` (sprint_1)
    asserts the no-args call returns the canonical 14 keys all-False;
    breaking that contract would crater the smoke gate. Future sprints
    may deprecate this in favour of an explicit
    ``score_calibration_empty()`` helper.
    """
    return {name: False for name in GOOD_CALIBRATION_OBJECTIVES}
