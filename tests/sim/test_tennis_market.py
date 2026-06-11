"""Sprint_7 T-C-004 — tests for :mod:`sim.tennis_market_generator`.

Acceptance focus mirrors the existing :mod:`sim.market` tests:

* :func:`build_arrays` is deterministic for a given seed.
* The price stream stays inside the clip band ``[0.05, 0.95]``.
* Match boundaries land at the documented cadence (3-9 ticks each).
* :func:`build_replay` wraps into a :class:`sim.market.MarketReplay`
  that still satisfies the no-look-ahead contract.
* Tennis-vs-basketball: the tennis stream's within-match volatility is
  measurably higher than the basketball stream's — a sanity check on
  the σ tuning baked into the module-level constants.
"""

from __future__ import annotations

import numpy as np
import pytest

from sim.market import LookaheadError, MarketReplay
from sim.tennis_market_generator import (
    _MATCH_TICKS_HIGH,
    _MATCH_TICKS_LOW,
    _PRICE_CEIL,
    _PRICE_FLOOR,
    _WITHIN_MATCH_SIGMA,
    build_arrays,
    build_replay,
)


def test_build_arrays_is_deterministic_for_seed() -> None:
    a = build_arrays(42, n_ticks=2_000)
    b = build_arrays(42, n_ticks=2_000)
    assert np.array_equal(a.prices, b.prices)
    assert np.array_equal(a.depths, b.depths)
    assert np.array_equal(a.match_boundaries, b.match_boundaries)


def test_different_seeds_produce_different_arrays() -> None:
    a = build_arrays(1, n_ticks=2_000)
    b = build_arrays(2, n_ticks=2_000)
    assert not np.array_equal(a.prices, b.prices)


def test_prices_stay_within_clip_band() -> None:
    arrays = build_arrays(0, n_ticks=5_000)
    assert arrays.prices.min() >= _PRICE_FLOOR - 1e-9
    assert arrays.prices.max() <= _PRICE_CEIL + 1e-9


def test_match_cadence_within_documented_range() -> None:
    """Per PRD §14.3 + module docstring, each match lasts 3-9 ticks.

    Only the non-terminal matches are checked: the final match in the
    stream can be truncated by ``min(tick + match_len, n_ticks)`` and
    therefore land below ``_MATCH_TICKS_LOW`` legitimately.
    """
    arrays = build_arrays(7, n_ticks=10_000)
    boundaries = arrays.match_boundaries
    inner_gaps = list(np.diff(boundaries))
    for gap in inner_gaps:
        assert _MATCH_TICKS_LOW <= gap <= _MATCH_TICKS_HIGH, (
            f"match gap {gap} outside [{_MATCH_TICKS_LOW}, {_MATCH_TICKS_HIGH}]"
        )
    # Sanity: a 10K-tick stream should produce many matches at this cadence.
    assert len(boundaries) >= 10_000 // _MATCH_TICKS_HIGH


def test_build_replay_returns_marketreplay() -> None:
    replay = build_replay(0, n_ticks=1_000)
    assert isinstance(replay, MarketReplay)
    # The replay still enforces the no-look-ahead contract.
    replay.step()
    with pytest.raises(LookaheadError):
        replay.tick_at(5)  # cursor=0, asking for ticks ahead must raise


def test_build_replay_step_through_works() -> None:
    replay = build_replay(0, n_ticks=50)
    for expected in range(50):
        tick = replay.step()
        assert tick.tick == expected
        assert _PRICE_FLOOR - 1e-9 <= tick.price <= _PRICE_CEIL + 1e-9
        assert tick.depth > 0.0


def test_tennis_higher_within_match_volatility_than_basketball() -> None:
    """Sanity check that the tuning σ for tennis actually shows up as
    higher per-tick price-step variance than the basketball stream
    used by :meth:`MarketReplay.from_synthetic`."""
    tennis = build_arrays(0, n_ticks=5_000)
    basketball = MarketReplay.from_synthetic(seed=0, n_ticks=5_000)
    tennis_steps = np.diff(tennis.prices)
    basketball_steps = np.diff(np.array([
        basketball.step().price for _ in range(5_000)
    ]))
    # Compare stdev of per-tick price moves. Tennis is 2.5× basketball's
    # base σ + occasional point-shocks; we expect the empirical ratio to
    # be ≥1.5 even with the mean-reversion damping.
    ratio = float(np.std(tennis_steps) / max(np.std(basketball_steps), 1e-9))
    assert ratio >= 1.5, (
        f"tennis/basketball stdev ratio {ratio:.2f} below 1.5 — tuning regression?"
    )
    # Module-level sigma is still the canonical baseline; assert it is
    # at least 1.5× the basketball generator's hard-coded 0.02.
    assert _WITHIN_MATCH_SIGMA >= 1.5 * 0.02


def test_build_replay_injects_into_runner() -> None:
    """The Runner accepts a tennis factory and produces deterministic
    lifetimes when threaded through. This is the runner-side smoke
    test for the sprint_7 calibration entrypoint."""
    from functools import partial

    from sim.params import ParamSpace
    from sim.runner import Runner

    factory = partial(build_replay, n_ticks=2_500)
    r = Runner(max_ticks=500, market_factory=factory)
    a = r.simulate_lifetime(params=ParamSpace(), archetype="optimist", seed=11)
    b = r.simulate_lifetime(params=ParamSpace(), archetype="optimist", seed=11)
    assert a.breath_curve == b.breath_curve
    assert a.terminal_phase == b.terminal_phase
