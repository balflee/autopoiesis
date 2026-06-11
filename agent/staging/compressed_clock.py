"""Compressed wall-clock for the §15 Gap 7 Phase 3 rehearsal.

The Phase 3 dress rehearsal nominally runs 6 hours on testnet (TP §15
Gap 7 pass criterion: "6 hours Phase 3 实盘演练"). The CI/local
operator does NOT actually want to wait 6 hours; the rehearsal is a
deterministic compressed run where the same event ordering invariants
hold but each "sim second" maps to ``1 / compression_ratio`` wall
seconds.

Two invariants this clock owns
------------------------------

1. **Strict-monotonic sim time.** Once :meth:`mark` records an event
   at sim-time ``t``, the next mark MUST satisfy ``t' > t``. Out of
   order arrivals raise :class:`OrderingViolation` — the runner
   surfaces this as the rehearsal's structural fail mode (the
   on-chain event tail must produce events in causal order; if it
   doesn't, the WS subscriber is dropping or reordering frames and
   the rehearsal cannot certify the Demo).
2. **Wall-clock compression factor is a constant.** Construction
   pins ``real_duration_s`` (e.g. 6 hours = 21600s) and a
   ``compression_ratio`` (e.g. 3600x → the rehearsal completes in
   6 wall-seconds). The clock NEVER renegotiates these mid-run; a
   dynamic ratio would let a long event interval mask a slow-tick
   bug that would surface as drift in the real 6-hour run.

The clock is **driven**, not **driving** — :meth:`mark` is the only
API the runner calls; the runner produces events from the WS / RPC
tails and feeds them in. There is no internal scheduler firing
imaginary events; the compressed factor exists purely so an idle
:meth:`wait_for_wallclock` blocks for a SHORT real interval while the
underlying observation streams are queried in the background. The
runner's WS / RPC adapters are themselves real (testnet WS, testnet
RPC) — only the WAIT between samples is compressed.

Why a class instead of a free function
--------------------------------------

The runner needs an injectable clock the test suite can drive
deterministically (no real sleep, no real wall-clock). The
:meth:`now_wallclock_s_getter` + :meth:`sleep` constructor injections
let the test pin both surfaces; production wiring uses
``time.monotonic`` + ``asyncio.sleep``.

Type-strict mypy: all fields + methods are typed; no Any escape
hatches (the marker payload is a generic ``dict[str, object]`` because
event payloads are heterogeneous — but the clock itself never
introspects payload contents, so ``object`` is structurally accurate).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final

# Default compression ratio: 1 wall-second represents
# DEFAULT_COMPRESSION_RATIO sim-seconds. 3600x maps a 6-hour rehearsal
# into 6 wall-seconds — fast enough for CI, slow enough that the WS
# heartbeat still has multiple sampling windows per sim "minute".
DEFAULT_COMPRESSION_RATIO: Final[float] = 3600.0

# Default real duration of a Phase 3 rehearsal — 6 hours per TP §15
# Gap 7. The rehearsal_runner overrides this from its
# ``duration_minutes`` parameter; the constant is here as the
# spec-anchored canonical value.
DEFAULT_REHEARSAL_REAL_DURATION_S: Final[float] = 6 * 60 * 60  # 21600s


class OrderingViolation(RuntimeError):
    """Strict-monotonic sim time invariant breached.

    Raised by :meth:`CompressedClock.mark` when an event arrives at
    or before the previously-marked sim time. The runner catches
    this and tags the rehearsal as failed with diagnostic context —
    a reorder usually means the WS subscriber dropped + replayed
    frames out of order, which would break the Demo's audit story.
    """


@dataclass(frozen=True)
class CompressedClockEvent:
    """One marked event with both sim-time and wall-time stamps.

    ``sim_time_s`` is the strict-monotonic timeline the rehearsal
    runs against; ``wall_time_s`` is the actual wall-clock at the
    moment :meth:`CompressedClock.mark` was called (for post-hoc
    drift analysis — divergence between expected sim-from-wall and
    observed wall-time surfaces compression slippage).

    The ``payload`` carries event-specific data the runner attaches.
    The clock NEVER reads payload contents; it preserves them
    verbatim so the audit timeline reproduces the on-chain order.
    """

    name: str
    sim_time_s: float
    wall_time_s: float
    payload: dict[str, object] = field(default_factory=dict)


# Type aliases — narrow callable signatures so probes type cleanly
# without resorting to ``Callable[..., Any]``.
_GetWallSeconds = Callable[[], float]
_Sleep = Callable[[float], Awaitable[None]]


class CompressedClock:
    """Wall-clock compressor with strict-monotonic sim-time marker.

    The clock has two state vars:

    * ``_start_wall_s`` — wall-clock at construction (or last reset).
    * ``_last_sim_s`` — most recently marked sim-time. ``-inf`` at
      boot so the first :meth:`mark` always succeeds.

    Both are private — the only readers are :meth:`elapsed_sim_s`
    and :meth:`mark`. Construction parameters:

    real_duration_s:
        The nominal length of the rehearsal in REAL seconds (6 hours
        for Gap 7's default). Used by :meth:`progress_fraction` so
        the runner knows when to stop the loop without re-computing
        the ratio.
    compression_ratio:
        How many sim-seconds each wall-second represents. >1.0 means
        compressed (fast); 1.0 means real-time; <1.0 means dilated
        (debugger-friendly slow-mo).
    now_wallclock_s_getter:
        Sync callable returning monotonic wall-seconds. Production
        wires :func:`time.monotonic`; tests inject a deterministic
        counter so the rehearsal is reproducible.
    sleep:
        Async sleep used by :meth:`wait_for_sim_seconds`. Production
        wires :func:`asyncio.sleep`; tests inject a no-op so the
        scenario completes in deterministic time.

    Thread-safety
    -------------

    The clock is NOT thread-safe — it's intended to be driven from a
    single asyncio task (the runner's main loop). The WS / RPC tail
    feeders push events through the runner's asyncio.Queue, which
    serialises into the same task before invoking :meth:`mark`.
    """

    def __init__(
        self,
        *,
        real_duration_s: float = DEFAULT_REHEARSAL_REAL_DURATION_S,
        compression_ratio: float = DEFAULT_COMPRESSION_RATIO,
        now_wallclock_s_getter: _GetWallSeconds | None = None,
        sleep: _Sleep | None = None,
    ) -> None:
        if real_duration_s <= 0.0:
            raise ValueError(
                f"real_duration_s must be > 0 (got {real_duration_s})"
            )
        if compression_ratio <= 0.0:
            raise ValueError(
                f"compression_ratio must be > 0 (got {compression_ratio})"
            )
        self._real_duration_s = real_duration_s
        self._ratio = compression_ratio
        self._now: _GetWallSeconds = (
            now_wallclock_s_getter
            if now_wallclock_s_getter is not None
            else time.monotonic
        )
        self._sleep: _Sleep = sleep if sleep is not None else asyncio.sleep
        self._start_wall_s: float = self._now()
        self._last_sim_s: float = float("-inf")
        self._events: list[CompressedClockEvent] = []

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    @property
    def real_duration_s(self) -> float:
        """The pinned real-clock duration of the rehearsal (seconds)."""
        return self._real_duration_s

    @property
    def compression_ratio(self) -> float:
        """Sim-seconds per wall-second (>1.0 = compressed)."""
        return self._ratio

    @property
    def events(self) -> list[CompressedClockEvent]:
        """Read-only copy of the marked-event timeline.

        Returns a shallow list copy so the caller iterating cannot
        truncate the internal log — but the frozen dataclass elements
        cannot be mutated, so the return is structurally immutable.
        """
        return list(self._events)

    def elapsed_wall_s(self) -> float:
        """Wall seconds since construction (or the most recent reset)."""
        return max(0.0, self._now() - self._start_wall_s)

    def elapsed_sim_s(self) -> float:
        """Sim seconds elapsed = wall_elapsed * compression_ratio."""
        return self.elapsed_wall_s() * self._ratio

    def progress_fraction(self) -> float:
        """Fraction of the nominal real-duration that has elapsed.

        Clamped to ``[0, 1]``. ``1.0`` means the compressed rehearsal
        has finished — the runner's outer loop reads this each
        iteration and exits when it reaches 1.0 (no buffer; the
        compression factor is exact).
        """
        if self._real_duration_s <= 0.0:
            return 1.0
        fraction = self.elapsed_sim_s() / self._real_duration_s
        return max(0.0, min(1.0, fraction))

    def is_done(self) -> bool:
        """``True`` once :meth:`progress_fraction` saturates at 1.0."""
        return self.progress_fraction() >= 1.0

    # ------------------------------------------------------------------
    # Mutating API — the runner's only write surface on the clock.
    # ------------------------------------------------------------------

    def mark(
        self,
        name: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> CompressedClockEvent:
        """Record an event at the current sim time.

        The recorded sim-time is :meth:`elapsed_sim_s` at call time.
        If that value is ``<=`` the last recorded sim-time, raise
        :class:`OrderingViolation` — the strict-monotonic invariant
        is the runner's primary safety net. Equal sim-times are
        rejected because two events at the same instant break the
        Demo's audit log replay (the dashboard timeline collapses
        identical-instant frames into one).

        Returns the recorded :class:`CompressedClockEvent` so the
        runner can stash it in its own append-only log.
        """
        sim_now = self.elapsed_sim_s()
        wall_now = self.elapsed_wall_s()
        if sim_now <= self._last_sim_s:
            raise OrderingViolation(
                f"event {name!r} at sim_time={sim_now:.6f}s is not "
                f"strictly after last marked sim_time={self._last_sim_s:.6f}s"
            )
        evt = CompressedClockEvent(
            name=name,
            sim_time_s=sim_now,
            wall_time_s=wall_now,
            payload=dict(payload or {}),
        )
        self._last_sim_s = sim_now
        self._events.append(evt)
        return evt

    async def wait_for_wallclock(self, wall_seconds: float) -> None:
        """Async sleep for ``wall_seconds`` of real wall time.

        Thin wrapper around the injected sleep — exists so the
        runner's main loop reads as ``await clock.wait_for_wallclock``
        instead of ``await asyncio.sleep`` (clarity at a glance about
        which loop step is the rehearsal-cadence wait vs. a generic
        async-await). Tests inject a no-op sleep so the test runs at
        full Python speed regardless of cadence parameter.
        """
        if wall_seconds <= 0.0:
            return
        await self._sleep(wall_seconds)

    async def wait_for_sim_seconds(self, sim_seconds: float) -> None:
        """Async sleep for the wall-time equivalent of ``sim_seconds``.

        Computes ``wall = sim / compression_ratio`` and forwards to
        :meth:`wait_for_wallclock`. The runner uses this for cadence-
        anchored waits — "advance 1 sim-minute" maps to
        ``1/compression_ratio`` wall-seconds.
        """
        if sim_seconds <= 0.0:
            return
        await self.wait_for_wallclock(sim_seconds / self._ratio)


__all__ = [
    "DEFAULT_COMPRESSION_RATIO",
    "DEFAULT_REHEARSAL_REAL_DURATION_S",
    "CompressedClock",
    "CompressedClockEvent",
    "OrderingViolation",
]
