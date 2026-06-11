"""Tests for :mod:`agent.staging.compressed_clock`.

Covers:

* Strict-monotonic invariant — equal or descending sim-time mark
  raises :class:`OrderingViolation`.
* Compression ratio computes ``elapsed_sim_s`` = wall_elapsed * ratio.
* ``progress_fraction`` saturates at 1.0 when the rehearsal real-
  duration has elapsed.
* ``wait_for_sim_seconds`` divides by the ratio before forwarding to
  the injected sleep.
* Constructor refuses negative / zero durations + ratios.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.staging.compressed_clock import (
    CompressedClock,
    CompressedClockEvent,
    OrderingViolation,
)

# ── Helpers — deterministic wall-clock + recording sleep ─────────────


class _WallClock:
    """A pinned wall-clock the test advances by calling :meth:`tick`."""

    def __init__(self, start_s: float = 0.0) -> None:
        self.now_s = start_s

    def __call__(self) -> float:
        return self.now_s

    def tick(self, delta_s: float) -> None:
        self.now_s += delta_s


class _Sleeper:
    """Recording fake sleep that captures every requested wall duration."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ── Constructor guards ───────────────────────────────────────────────


def test_negative_duration_rejected() -> None:
    with pytest.raises(ValueError):
        CompressedClock(real_duration_s=-1.0)


def test_zero_duration_rejected() -> None:
    with pytest.raises(ValueError):
        CompressedClock(real_duration_s=0.0)


def test_negative_ratio_rejected() -> None:
    with pytest.raises(ValueError):
        CompressedClock(real_duration_s=10.0, compression_ratio=-2.0)


def test_zero_ratio_rejected() -> None:
    with pytest.raises(ValueError):
        CompressedClock(real_duration_s=10.0, compression_ratio=0.0)


# ── elapsed_sim_s / progress_fraction ────────────────────────────────


def test_elapsed_sim_scales_with_ratio() -> None:
    wall = _WallClock(start_s=100.0)
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=10.0,
        now_wallclock_s_getter=wall,
    )
    wall.tick(3.0)  # 3 wall seconds → 30 sim seconds
    assert clock.elapsed_wall_s() == pytest.approx(3.0)
    assert clock.elapsed_sim_s() == pytest.approx(30.0)
    assert clock.progress_fraction() == pytest.approx(0.5)


def test_progress_fraction_saturates_at_one() -> None:
    wall = _WallClock(start_s=0.0)
    clock = CompressedClock(
        real_duration_s=6.0, compression_ratio=1.0, now_wallclock_s_getter=wall,
    )
    wall.tick(100.0)  # way past the 6s window
    assert clock.is_done() is True
    assert clock.progress_fraction() == 1.0


# ── mark + strict-monotonic ──────────────────────────────────────────


def test_mark_records_event_at_current_sim_time() -> None:
    wall = _WallClock(start_s=0.0)
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=10.0,
        now_wallclock_s_getter=wall,
    )
    wall.tick(1.0)
    evt = clock.mark("a", payload={"k": "v"})
    assert isinstance(evt, CompressedClockEvent)
    assert evt.name == "a"
    assert evt.sim_time_s == pytest.approx(10.0)
    assert evt.wall_time_s == pytest.approx(1.0)
    assert evt.payload == {"k": "v"}
    assert len(clock.events) == 1


def test_mark_two_in_strict_order_succeeds() -> None:
    wall = _WallClock(start_s=0.0)
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=1.0,
        now_wallclock_s_getter=wall,
    )
    wall.tick(1.0)
    clock.mark("first")
    wall.tick(0.5)
    clock.mark("second")
    assert [e.name for e in clock.events] == ["first", "second"]
    assert clock.events[1].sim_time_s > clock.events[0].sim_time_s


def test_mark_at_same_sim_time_raises_ordering_violation() -> None:
    wall = _WallClock(start_s=0.0)
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=1.0,
        now_wallclock_s_getter=wall,
    )
    wall.tick(1.0)
    clock.mark("first")
    # Same wall time → same sim time → strict-monotonic violation.
    with pytest.raises(OrderingViolation):
        clock.mark("second")


def test_mark_backwards_in_time_raises_ordering_violation() -> None:
    wall = _WallClock(start_s=5.0)
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=1.0,
        now_wallclock_s_getter=wall,
    )
    wall.tick(2.0)
    clock.mark("first")
    # Rewind the wall clock — only possible in synthetic tests, but
    # is exactly what a buggy WS reorder looks like at the runner.
    wall.now_s -= 1.0
    with pytest.raises(OrderingViolation):
        clock.mark("backwards")


# ── wait_for_sim_seconds / wait_for_wallclock ────────────────────────


def test_wait_for_sim_seconds_divides_by_ratio() -> None:
    """1 sim-second at 10x compression should wait 0.1 wall-seconds."""
    wall = _WallClock(start_s=0.0)
    sleeper = _Sleeper()
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=10.0,
        now_wallclock_s_getter=wall,
        sleep=sleeper,
    )
    asyncio.run(clock.wait_for_sim_seconds(1.0))
    assert sleeper.calls == [pytest.approx(0.1)]


def test_wait_for_wallclock_passes_through() -> None:
    sleeper = _Sleeper()
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=10.0,
        now_wallclock_s_getter=_WallClock(0.0),
        sleep=sleeper,
    )
    asyncio.run(clock.wait_for_wallclock(0.5))
    assert sleeper.calls == [pytest.approx(0.5)]


def test_wait_for_zero_is_noop() -> None:
    sleeper = _Sleeper()
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=10.0,
        now_wallclock_s_getter=_WallClock(0.0),
        sleep=sleeper,
    )
    asyncio.run(clock.wait_for_sim_seconds(0.0))
    asyncio.run(clock.wait_for_wallclock(-1.0))
    assert sleeper.calls == []


# ── events copy semantics ────────────────────────────────────────────


def test_events_returns_shallow_copy() -> None:
    """Caller mutation of the returned list must NOT corrupt the log."""
    wall = _WallClock(start_s=0.0)
    clock = CompressedClock(
        real_duration_s=60.0,
        compression_ratio=1.0,
        now_wallclock_s_getter=wall,
    )
    wall.tick(1.0)
    clock.mark("first")
    snapshot = clock.events
    snapshot.clear()
    # Internal log unchanged.
    assert len(clock.events) == 1
