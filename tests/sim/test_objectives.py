"""Sprint_3 (T-C-003) tests for :mod:`sim.objectives`.

Acceptance criteria covered:

* Each of the 14 GOOD_CALIBRATION objectives is implemented as a pure
  function that accepts per-archetype lifetime buckets and returns an
  :class:`ObjectiveRecord`.
* :func:`score_calibration` produces records in the canonical
  :data:`GOOD_CALIBRATION_OBJECTIVES` order — the validator's
  contract.
* The aggregate loss is monotonically improved by passing more
  objectives — so BO can pin the calibrated region.
* Penalty terms are bounded ``[0, 1]`` and zero when the objective
  passes.
* The sprint_1 back-compat :func:`score_objectives` shim still returns
  all-False over the canonical 14 keys (smoke gate dependency).
"""

from __future__ import annotations

import pytest

from sim.objectives import (
    GOOD_CALIBRATION_OBJECTIVES,
    TICKS_PER_DAY,
    ArchetypeLifetimes,
    ScoreContext,
    aggregate_loss,
    objective_ci_width_under_threshold,
    objective_desperate_mode_trigger_rate,
    objective_determinism_round_trip,
    objective_lung_expansion_count,
    objective_mean_lifetime_days,
    objective_no_immortal_outcomes,
    objective_no_lookahead_violations,
    objective_no_zero_tick_deaths,
    objective_optimist_bankroll_volatility,
    objective_pessimist_lifetime_bounded,
    objective_random_gambler_dies_within_2_days,
    objective_satisficer_dies_faster_than_optimist,
    objective_terminal_lucidity_rate,
    objective_three_death_paths,
    score_calibration,
    score_objectives,
)
from sim.runner import LifetimeResult


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_lifetime(
    *,
    archetype: str = "pessimist",
    ticks: int = 500,
    terminal_phase: str = "Attrition",
    final_bankroll: float = 100.0,
    desperate: bool = False,
    lung_count: int = 1,
    seed: int = 0,
) -> LifetimeResult:
    """Tiny LifetimeResult factory keyed by the fields the objectives read."""
    return LifetimeResult(
        archetype=archetype,
        seed=seed,
        ticks_survived=ticks,
        terminal_phase=terminal_phase,
        breath_curve=[0.0] * (ticks + 1),
        final_bankroll=final_bankroll,
        desperate_mode_entered=desperate,
        lung_expansion_count=lung_count,
    )


def _make_buckets(
    pessimist: list[LifetimeResult] | None = None,
    optimist: list[LifetimeResult] | None = None,
    satisficer: list[LifetimeResult] | None = None,
    random_gambler: list[LifetimeResult] | None = None,
) -> list[ArchetypeLifetimes]:
    return [
        ArchetypeLifetimes(
            archetype="pessimist", lifetimes=tuple(pessimist or [])
        ),
        ArchetypeLifetimes(
            archetype="optimist", lifetimes=tuple(optimist or [])
        ),
        ArchetypeLifetimes(
            archetype="satisficer", lifetimes=tuple(satisficer or [])
        ),
        ArchetypeLifetimes(
            archetype="random_gambler",
            lifetimes=tuple(random_gambler or []),
        ),
    ]


# ----------------------------------------------------------------------
# Composite shape
# ----------------------------------------------------------------------


def test_score_calibration_emits_canonical_14_objectives_in_order() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(archetype="pessimist", ticks=600)],
        optimist=[_make_lifetime(archetype="optimist", ticks=600)],
        satisficer=[_make_lifetime(archetype="satisficer", ticks=600)],
        random_gambler=[_make_lifetime(archetype="random_gambler", ticks=100)],
    )
    verdict = score_calibration(buckets)
    names = tuple(o.name for o in verdict.objectives)
    assert names == GOOD_CALIBRATION_OBJECTIVES
    # 14 objectives, exact.
    assert verdict.total_count == 14
    assert len(verdict.objectives) == 14


def test_sprint1_back_compat_score_objectives_still_works() -> None:
    """The sprint_1 smoke gate asserts all-False over 14 keys."""
    out = score_objectives()
    assert set(out.keys()) == set(GOOD_CALIBRATION_OBJECTIVES)
    assert all(v is False for v in out.values())


# ----------------------------------------------------------------------
# Per-objective checks — each direction of pass/fail tested.
# ----------------------------------------------------------------------


def test_objective_mean_lifetime_passes_in_band() -> None:
    # 5-day mean ticks = 5 * 144 = 720
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=720)] * 5,
        optimist=[_make_lifetime(archetype="optimist", ticks=720)] * 5,
        satisficer=[_make_lifetime(archetype="satisficer", ticks=720)] * 5,
    )
    out = objective_mean_lifetime_days(buckets)
    assert out.passed
    assert 3.0 <= out.measured <= 7.0
    assert out.penalty == 0.0


def test_objective_mean_lifetime_fails_below_band() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=100)] * 5,
        optimist=[_make_lifetime(archetype="optimist", ticks=100)] * 5,
        satisficer=[_make_lifetime(archetype="satisficer", ticks=100)] * 5,
    )
    out = objective_mean_lifetime_days(buckets)
    assert not out.passed
    assert out.penalty > 0.0
    assert out.penalty <= 1.0


def test_objective_desperate_rate_passes_in_band() -> None:
    # 7 of 10 lifetimes triggered desperate → 0.70 ∈ [0.60, 0.85].
    triggers = [True] * 7 + [False] * 3
    buckets = _make_buckets(
        pessimist=[
            _make_lifetime(desperate=t) for t in triggers
        ],
    )
    out = objective_desperate_mode_trigger_rate(buckets)
    assert out.passed
    assert out.measured == pytest.approx(0.70)


def test_objective_desperate_rate_fails_above_band() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(desperate=True) for _ in range(10)],
    )
    out = objective_desperate_mode_trigger_rate(buckets)
    assert not out.passed
    assert out.measured == pytest.approx(1.00)


def test_objective_terminal_lucidity_passes_above_85() -> None:
    # All 10 lifetimes died desperate (lucidity rate = 100%).
    buckets = _make_buckets(
        pessimist=[
            _make_lifetime(
                terminal_phase="Starvation", desperate=True
            )
            for _ in range(10)
        ],
    )
    out = objective_terminal_lucidity_rate(buckets)
    assert out.passed
    assert out.measured == pytest.approx(1.0)


def test_objective_terminal_lucidity_excludes_survivors() -> None:
    """Survivors must NOT count toward lucidity rate."""
    buckets = _make_buckets(
        pessimist=[_make_lifetime(terminal_phase="Survival", desperate=False)],
    )
    out = objective_terminal_lucidity_rate(buckets)
    assert not out.passed  # zero dying agents → cannot compute, fail
    assert out.measured is None


def test_objective_lung_expansion_in_band() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(lung_count=2) for _ in range(10)],
        optimist=[
            _make_lifetime(archetype="optimist", lung_count=2)
            for _ in range(10)
        ],
        satisficer=[
            _make_lifetime(archetype="satisficer", lung_count=2)
            for _ in range(10)
        ],
    )
    out = objective_lung_expansion_count(buckets)
    assert out.passed
    assert out.measured == pytest.approx(2.0)


def test_objective_three_death_paths_passes() -> None:
    buckets = _make_buckets(
        pessimist=[
            _make_lifetime(terminal_phase="Attrition"),
            _make_lifetime(terminal_phase="Starvation"),
            _make_lifetime(terminal_phase="TradingLoss"),
        ],
    )
    out = objective_three_death_paths(buckets)
    assert out.passed
    assert set(out.measured) >= {"Attrition", "Starvation", "TradingLoss"}


def test_objective_three_death_paths_fails_when_one_missing() -> None:
    buckets = _make_buckets(
        pessimist=[
            _make_lifetime(terminal_phase="Attrition"),
            _make_lifetime(terminal_phase="Starvation"),
        ],
    )
    out = objective_three_death_paths(buckets)
    assert not out.passed
    assert out.penalty > 0.0  # partial coverage penalty


def test_objective_random_gambler_under_2_days() -> None:
    # 1 day = 144 ticks. 200 ticks = 1.39 days, passes.
    buckets = _make_buckets(
        random_gambler=[
            _make_lifetime(archetype="random_gambler", ticks=200)
            for _ in range(10)
        ],
    )
    out = objective_random_gambler_dies_within_2_days(buckets)
    assert out.passed


def test_objective_random_gambler_fails_when_long() -> None:
    buckets = _make_buckets(
        random_gambler=[
            _make_lifetime(archetype="random_gambler", ticks=500)
            for _ in range(10)
        ],
    )
    out = objective_random_gambler_dies_within_2_days(buckets)
    assert not out.passed


def test_objective_random_gambler_missing_archetype_fails() -> None:
    # No gambler in buckets at all.
    buckets = [
        ArchetypeLifetimes(archetype="pessimist", lifetimes=()),
    ]
    out = objective_random_gambler_dies_within_2_days(buckets)
    assert not out.passed
    assert out.measured is None


def test_objective_satisficer_faster_than_optimist_passes() -> None:
    buckets = _make_buckets(
        optimist=[_make_lifetime(archetype="optimist", ticks=600)] * 5,
        satisficer=[_make_lifetime(archetype="satisficer", ticks=400)] * 5,
    )
    out = objective_satisficer_dies_faster_than_optimist(buckets)
    assert out.passed


def test_objective_satisficer_fails_when_lazier_outlives() -> None:
    buckets = _make_buckets(
        optimist=[_make_lifetime(archetype="optimist", ticks=400)] * 5,
        satisficer=[_make_lifetime(archetype="satisficer", ticks=600)] * 5,
    )
    out = objective_satisficer_dies_faster_than_optimist(buckets)
    assert not out.passed


def test_objective_pessimist_bounded_passes_in_band() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=int(5 * TICKS_PER_DAY))] * 5,
    )
    out = objective_pessimist_lifetime_bounded(buckets)
    assert out.passed
    assert 1.0 <= out.measured <= 14.0


def test_objective_pessimist_bounded_fails_when_zero_lifetime() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=10)] * 5,
    )
    out = objective_pessimist_lifetime_bounded(buckets)
    assert not out.passed


def test_objective_optimist_volatility_passes_when_stdev_high() -> None:
    optimist = [
        _make_lifetime(archetype="optimist", final_bankroll=br)
        for br in (50.0, 100.0, 150.0, 200.0, 80.0)
    ]
    out = objective_optimist_bankroll_volatility(
        _make_buckets(optimist=optimist)
    )
    assert out.passed
    assert out.measured > 10.0


def test_objective_optimist_volatility_fails_when_flat() -> None:
    optimist = [
        _make_lifetime(archetype="optimist", final_bankroll=100.0)
        for _ in range(5)
    ]
    out = objective_optimist_bankroll_volatility(
        _make_buckets(optimist=optimist)
    )
    assert not out.passed
    assert out.measured == pytest.approx(0.0)


def test_objective_no_immortal_passes_when_none_survive() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(terminal_phase="Attrition")] * 5,
    )
    out = objective_no_immortal_outcomes(buckets)
    assert out.passed
    assert out.measured == 0


def test_objective_no_immortal_fails_when_any_survive() -> None:
    buckets = _make_buckets(
        pessimist=[
            _make_lifetime(terminal_phase="Survival"),
            _make_lifetime(terminal_phase="Attrition"),
        ],
    )
    out = objective_no_immortal_outcomes(buckets)
    assert not out.passed
    assert out.measured == 1


def test_objective_no_zero_tick_passes() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=5)] * 5,
    )
    out = objective_no_zero_tick_deaths(buckets)
    assert out.passed


def test_objective_no_zero_tick_fails_when_any_zero() -> None:
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=0), _make_lifetime(ticks=10)],
    )
    out = objective_no_zero_tick_deaths(buckets)
    assert not out.passed


def test_objective_ci_width_passes_when_tight() -> None:
    """30 lifetimes at nearly identical tick counts → tight CI."""
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=500 + (i % 3)) for i in range(30)],
    )
    out = objective_ci_width_under_threshold(buckets, ci_half_width_max_days=1.0)
    assert out.passed
    assert out.measured < 1.0


def test_objective_ci_width_fails_when_loose() -> None:
    """Wildly varying lifetimes → CI too wide."""
    buckets = _make_buckets(
        pessimist=[_make_lifetime(ticks=t) for t in (10, 100, 500, 1000, 1500, 2000)],
    )
    out = objective_ci_width_under_threshold(buckets, ci_half_width_max_days=0.5)
    assert not out.passed


def test_objective_determinism_round_trip_pass_and_fail() -> None:
    assert objective_determinism_round_trip(determinism_verified=True).passed
    assert not objective_determinism_round_trip(determinism_verified=False).passed


def test_objective_no_lookahead_pass_and_fail() -> None:
    assert objective_no_lookahead_violations(no_lookahead_verified=True).passed
    assert not objective_no_lookahead_violations(
        no_lookahead_verified=False
    ).passed


# ----------------------------------------------------------------------
# Aggregate-loss invariants
# ----------------------------------------------------------------------


def test_aggregate_loss_decreases_with_more_passes() -> None:
    # All-pass synthetic buckets — should bottom out near -1.0.
    good = _make_buckets(
        pessimist=[
            _make_lifetime(
                ticks=int(5 * TICKS_PER_DAY),
                terminal_phase="Attrition",
                desperate=True,
                lung_count=2,
                final_bankroll=80.0,
            )
            for _ in range(30)
        ]
        + [
            _make_lifetime(
                ticks=int(5 * TICKS_PER_DAY),
                terminal_phase="Starvation",
                desperate=True,
                lung_count=2,
                final_bankroll=70.0,
            )
            for _ in range(5)
        ]
        + [
            _make_lifetime(
                ticks=int(5 * TICKS_PER_DAY),
                terminal_phase="TradingLoss",
                desperate=True,
                lung_count=2,
                final_bankroll=20.0,
            )
            for _ in range(3)
        ],
        optimist=[
            _make_lifetime(
                archetype="optimist",
                ticks=int(5 * TICKS_PER_DAY),
                terminal_phase="Starvation",
                desperate=True,
                lung_count=2,
                final_bankroll=br,
            )
            for br in [40.0, 90.0, 120.0, 200.0, 70.0]
        ],
        satisficer=[
            _make_lifetime(
                archetype="satisficer",
                ticks=int(3 * TICKS_PER_DAY),
                terminal_phase="Starvation",
                desperate=True,
                lung_count=2,
                final_bankroll=80.0,
            )
            for _ in range(15)
        ]
        + [
            _make_lifetime(
                archetype="satisficer",
                ticks=int(3 * TICKS_PER_DAY),
                terminal_phase="Attrition",
                desperate=False,
                lung_count=2,
                final_bankroll=80.0,
            )
        ],
        random_gambler=[
            _make_lifetime(
                archetype="random_gambler",
                ticks=100,
                terminal_phase="TradingLoss",
                desperate=False,
            )
            for _ in range(5)
        ],
    )
    good_verdict = score_calibration(good)

    # Strictly worse buckets — short lifetimes, no death-path coverage.
    bad = _make_buckets(
        pessimist=[_make_lifetime(ticks=5)] * 5,
        optimist=[_make_lifetime(archetype="optimist", ticks=5)] * 5,
        satisficer=[_make_lifetime(archetype="satisficer", ticks=5)] * 5,
    )
    bad_verdict = score_calibration(bad)
    # The good combo MUST score strictly below the bad one (lower is better).
    assert good_verdict.loss < bad_verdict.loss
    assert good_verdict.passed_count > bad_verdict.passed_count


def test_aggregate_loss_is_bounded() -> None:
    """Loss ∈ approximately ``[-1.0, +0.7]`` — covers all 14 objectives.

    The empty-buckets case is the degenerate floor: most objectives fail
    (none of the rate/lifetime gates can compute), but the "no immortal"
    + "no zero-tick" gates are *vacuously* true (zero offending records
    in zero records). Combined with the default ScoreContext keeping
    determinism + no_lookahead True, the floor is 4/14, not 0/14.
    """
    buckets = _make_buckets()  # all empty
    verdict = score_calibration(buckets)
    assert -1.0 <= verdict.loss <= 1.0
    # With framework objectives forced false too, the floor is just the
    # vacuous ones (no_immortal + no_zero_tick) = 2/14.
    ctx = ScoreContext(determinism_verified=False, no_lookahead_verified=False)
    verdict = score_calibration(buckets, context=ctx)
    assert verdict.passed_count == 2
    assert -1.0 <= verdict.loss <= 1.0


def test_aggregate_loss_strict_zero_when_no_objectives() -> None:
    """Edge case: aggregate_loss on an empty iterable is 0.0."""
    assert aggregate_loss([]) == 0.0


def test_per_objective_records_have_required_schema_a_fields() -> None:
    """Calibration_diag's Shape A requires {name, passed, measured,
    threshold, penalty} per record. We assert the shape directly so a
    future field rename trips a CI failure."""
    verdict = score_calibration(_make_buckets())
    required = {"name", "passed", "measured", "threshold", "penalty"}
    for record in verdict.objectives:
        as_dict = record.to_dict()
        assert required <= set(as_dict.keys())


# ----------------------------------------------------------------------
# Score context plumbing
# ----------------------------------------------------------------------


def test_score_context_flips_framework_objectives() -> None:
    buckets = _make_buckets()
    ctx_pass = ScoreContext(determinism_verified=True, no_lookahead_verified=True)
    ctx_fail = ScoreContext(determinism_verified=False, no_lookahead_verified=False)

    pass_v = score_calibration(buckets, context=ctx_pass)
    fail_v = score_calibration(buckets, context=ctx_fail)
    # determinism + no_lookahead in the passing context should give 2
    # more passes than the failing context (everything else identical).
    assert (
        pass_v.per_objective["determinism_round_trip"].passed
        and not fail_v.per_objective["determinism_round_trip"].passed
    )
    assert (
        pass_v.per_objective["no_lookahead_violations"].passed
        and not fail_v.per_objective["no_lookahead_violations"].passed
    )
