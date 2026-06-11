"""Sprint_2 (T-C-002) tests for :mod:`sim.market`.

Acceptance criteria covered:

* :meth:`MarketReplay.from_synthetic` is deterministic for a given seed.
* :meth:`MarketReplay.step` advances exactly one tick and exposes only
  the now-current price/depth.
* :meth:`MarketReplay.tick_at` raises :class:`LookaheadError` on
  ``index > current_index`` — the calibration validator's no-lookahead
  scanner depends on this exception type.
* The price stream stays inside ``[0.05, 0.95]`` (clipped per
  PRD §14.1 sweep design).
"""

from __future__ import annotations

import pytest

from sim.market import LookaheadError, MarketReplay


def test_from_synthetic_is_deterministic_for_seed() -> None:
    m1 = MarketReplay.from_synthetic(seed=123, n_ticks=500)
    m2 = MarketReplay.from_synthetic(seed=123, n_ticks=500)
    # Step through both and compare every tick.
    for _ in range(500):
        t1 = m1.step()
        t2 = m2.step()
        assert t1 == t2


def test_step_exposes_only_current_tick_indices() -> None:
    m = MarketReplay.from_synthetic(seed=0, n_ticks=10)
    assert m.current_index == -1  # before any step()
    for expected in range(10):
        t = m.step()
        assert t.tick == expected
        assert m.current_index == expected


def test_tick_at_rejects_lookahead() -> None:
    """The validator's invariant: any read with index > current_index
    must raise LookaheadError. The runner never calls tick_at; the
    validator does."""
    m = MarketReplay.from_synthetic(seed=0, n_ticks=10)
    m.step()  # cursor = 0
    # Historical lookup at the current tick is allowed.
    _ = m.tick_at(0)
    # Look-ahead by ANY amount must raise.
    with pytest.raises(LookaheadError, match="peek past current_index"):
        m.tick_at(1)
    with pytest.raises(LookaheadError):
        m.tick_at(5)
    # Negative is a different category of error.
    with pytest.raises(IndexError):
        m.tick_at(-1)


def test_step_raises_when_exhausted() -> None:
    m = MarketReplay.from_synthetic(seed=0, n_ticks=3)
    m.step(); m.step(); m.step()  # noqa: E702 — three steps in one line for clarity
    assert m.exhausted
    with pytest.raises(LookaheadError, match="exhausted"):
        m.step()


def test_synthetic_prices_within_clip_bounds() -> None:
    m = MarketReplay.from_synthetic(seed=99, n_ticks=2000)
    for _ in range(2000):
        t = m.step()
        assert 0.05 <= t.price <= 0.95, (
            f"price {t.price} outside clip bounds at tick {t.tick}"
        )
        assert 50.0 <= t.depth <= 500.0


def test_market_arrays_are_writable_protected() -> None:
    """Mutating an array returned to a caller MUST NOT corrupt the replay
    (validator re-runs traces and expects byte-identical determinism)."""
    import numpy as np

    prices = np.linspace(0.4, 0.6, 5)
    depths = np.full(5, 100.0)
    m = MarketReplay.from_arrays(prices=prices, depths=depths)
    # Original arrays passed-in should not affect the replay after copy.
    prices[0] = 99.0
    t = m.step()
    assert t.price == pytest.approx(0.4)  # internal copy unchanged
