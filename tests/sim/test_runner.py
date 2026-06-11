"""Sprint_2 (T-C-002) tests for :mod:`sim.runner`.

Acceptance criteria covered:

* Same ``(params, archetype, seed)`` → byte-identical
  :class:`LifetimeResult` (determinism contract).
* Three mandatory archetypes (Pessimist / Optimist / Satisficer) yield
  distinct lifetime distributions across a small seed sweep.
* Every lifetime terminates within ``max_ticks`` with a valid
  ``terminal_phase``.
* Invalid archetype string raises :class:`ValueError`.
"""

from __future__ import annotations

import statistics

import pytest

from sim.params import ParamSpace
from sim.runner import TERMINAL_PHASES, LifetimeResult, Runner, run_lifetime


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


def test_simulate_lifetime_is_byte_identical() -> None:
    """The harness reproducibility check (DEV_FRAMEWORK §26 T2.7)
    replays a saved seed and asserts every field matches. We test the
    same invariant locally so a regression here fails fast."""
    runner = Runner(max_ticks=500)
    params = ParamSpace()
    r1 = runner.simulate_lifetime(params=params, archetype="optimist", seed=7)
    r2 = runner.simulate_lifetime(params=params, archetype="optimist", seed=7)
    assert r1.ticks_survived == r2.ticks_survived
    assert r1.terminal_phase == r2.terminal_phase
    assert r1.final_bankroll == r2.final_bankroll
    assert r1.breath_curve == r2.breath_curve
    assert r1.desperate_mode_entered == r2.desperate_mode_entered
    assert r1.lung_expansion_count == r2.lung_expansion_count


def test_simulate_lifetime_differs_for_different_seeds() -> None:
    """A different seed → at least the breath curve must change (else
    the seed is dead-weight)."""
    runner = Runner(max_ticks=500)
    params = ParamSpace()
    r1 = runner.simulate_lifetime(params=params, archetype="optimist", seed=1)
    r2 = runner.simulate_lifetime(params=params, archetype="optimist", seed=2)
    assert r1.breath_curve != r2.breath_curve


def test_back_compat_run_lifetime_wrapper() -> None:
    """``run_lifetime`` is the sprint_1 functional signature kept as a
    thin wrapper around :class:`Runner`."""
    a = run_lifetime(params=ParamSpace(), archetype="pessimist", seed=5)
    b = Runner().simulate_lifetime(
        params=ParamSpace(), archetype="pessimist", seed=5
    )
    assert a.ticks_survived == b.ticks_survived
    assert a.terminal_phase == b.terminal_phase


# ----------------------------------------------------------------------
# Archetype distinctness
# ----------------------------------------------------------------------


def test_three_archetypes_yield_distinct_mean_lifetimes() -> None:
    """Track C Hard Rule #2 protects archetype identity. The runner's
    job is to produce statistically distinct mean lifetimes across the
    three mandatory archetypes — a sanity check before the full
    calibration sweep stresses the same dimension via Hard Rule #2.

    The brief's acceptance criterion is "archetype-distinct" — we only
    assert non-collapse here. The specific lifetime ordering across
    the three is what the FULL calibration sweep is supposed to
    discover (the "Satisficer dies faster than Optimist" objective is
    a property of the *winning* parameter set, not of default params).
    """
    runner = Runner(max_ticks=2000)
    params = ParamSpace()
    means: dict[str, float] = {}
    for archetype in ("pessimist", "optimist", "satisficer"):
        lifetimes = [
            runner.simulate_lifetime(
                params=params, archetype=archetype, seed=s
            ).ticks_survived
            for s in range(20)
        ]
        means[archetype] = statistics.mean(lifetimes)
    # Three distinct means (round to 3 dp tolerates floating-point chop).
    assert len({round(v, 3) for v in means.values()}) == 3, (
        f"archetype mean lifetimes collapsed: {means}"
    )
    # Defensive: the spread must be non-trivial. If every archetype is
    # within 1 tick of every other, the "distinct" check above is
    # mostly luck-of-the-rounding rather than real signal.
    spread = max(means.values()) - min(means.values())
    assert spread >= 5.0, (
        f"archetype lifetime spread too small to claim distinctness: {means}"
    )


# ----------------------------------------------------------------------
# Lifetime termination + outcome enum
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "archetype", ["pessimist", "optimist", "satisficer", "random_gambler"]
)
def test_lifetime_always_terminates_with_valid_phase(archetype: str) -> None:
    """Every lifetime must end with one of the 4 published phases and
    a positive tick count."""
    runner = Runner(max_ticks=1000)
    result = runner.simulate_lifetime(
        params=ParamSpace(), archetype=archetype, seed=11
    )
    assert isinstance(result, LifetimeResult)
    assert result.terminal_phase in TERMINAL_PHASES
    assert result.ticks_survived >= 0
    assert result.ticks_survived <= 1000


def test_breath_curve_length_matches_ticks_survived() -> None:
    """Invariant: breath_curve includes the initial pre-tick balance,
    so len(curve) == ticks_survived + 1."""
    result = Runner(max_ticks=300).simulate_lifetime(
        params=ParamSpace(), archetype="satisficer", seed=3
    )
    assert len(result.breath_curve) == result.ticks_survived + 1
    # First entry must equal params.initial_breath.
    assert result.breath_curve[0] == pytest.approx(1000.0)


def test_random_gambler_has_higher_lifetime_variance_than_pessimist() -> None:
    """PRD §14.2 objective #6 is "random gambler dies within 2 days"
    — a *calibration target* the sweep is supposed to discover, not a
    default-params invariant. Under defaults the gambler can ride
    occasional Lung Expansion wins to stretch its lifetime.

    What we CAN assert at default params is that the random gambler
    produces much higher lifetime variance than the Pessimist (the
    Pessimist is a near-deterministic passive-burn floor). This serves
    as the sprint_2 sanity for objective #6 — the absolute 2-day bound
    comes online once T-C-003 ships the tick→day conversion + the
    sweep tunes the BREATH burn rates.
    """
    runner = Runner(max_ticks=2000)
    rand_lifetimes = [
        runner.simulate_lifetime(
            params=ParamSpace(), archetype="random_gambler", seed=s
        ).ticks_survived
        for s in range(15)
    ]
    pess_lifetimes = [
        runner.simulate_lifetime(
            params=ParamSpace(), archetype="pessimist", seed=s
        ).ticks_survived
        for s in range(15)
    ]
    assert statistics.stdev(rand_lifetimes) > statistics.stdev(pess_lifetimes), (
        f"random_gambler stdev {statistics.stdev(rand_lifetimes):.2f} "
        f"vs pessimist stdev {statistics.stdev(pess_lifetimes):.2f}"
    )


def test_simulate_lifetime_rejects_unknown_archetype() -> None:
    with pytest.raises(ValueError, match="archetype"):
        Runner().simulate_lifetime(
            params=ParamSpace(), archetype="messiah", seed=0
        )
