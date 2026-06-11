"""Shared rolling buffer + degradation event for the realtime data adapters.

Private module — :mod:`agent.data.polymarket`, :mod:`agent.data.polygon_chain`,
and :mod:`agent.data.nba_live` use the same shape so the agent main loop
(lands T-B-007) can apply uniform backpressure + windowed analytics
across all three feeds.

:class:`RealtimeBuffer` pairs:

* an :class:`asyncio.Queue` (producer/consumer backpressure — the agent
  loop drains at its own cadence without losing frames when bursts
  arrive),
* a bounded :class:`collections.deque` (rolling-window analytics — the
  market_momentum engine reads the last N snapshots to compute drift
  + velocity without re-querying upstream).

:class:`DegradedFeedWarning` is the canonical surface signal the
adapters yield when a feed degrades (websocket disconnect, missed
block, REST 5xx). It's a Pydantic model so the dashboard bridge
(:mod:`agent.dashboard_bridge`, T-D-XXX) can mirror it on the
Consciousness Stream verbatim.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Clock(Protocol):
    """Wire-arrival timestamp source — injected so tests can pin it.

    Shared by :mod:`agent.data.polymarket`, :mod:`agent.data.polygon_chain`,
    and :mod:`agent.data.nba_live` so the wire-arrival timestamp convention
    (PRD §14.1) has one canonical surface and tests stub one Protocol.
    """

    def now(self) -> datetime: ...


class UtcClock:
    """Default :class:`Clock` — :func:`datetime.now(UTC)` per call."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class DegradedFeedWarning(BaseModel):
    """A realtime feed has degraded — emitted alongside data frames.

    Yielded by :class:`agent.data.polymarket.PolymarketLive`,
    :class:`agent.data.polygon_chain.PolygonChainLive`, and
    :class:`agent.data.nba_live.NBALive` when a transient failure
    triggers the reconnect / gap-recovery codepath. ``attempt`` is
    1-indexed; ``attempt == max_reconnect_attempts`` is the final
    warning before the adapter gives up.

    ``available_at`` is the wire-arrival timestamp at which the
    degradation was observed — NEVER a payload field — so the
    look-ahead auditor (PRD §14.1) classifies the event the same way
    as a data frame.
    """

    model_config = ConfigDict(extra="forbid")

    feed: str
    reason: str
    attempt: int = Field(ge=1)
    available_at: str


class RealtimeBuffer(Generic[T]):
    """Async-safe rolling buffer combining a queue + bounded deque.

    Two concurrent surfaces:

    * :meth:`put` / :meth:`get` — backpressured FIFO over an
      :class:`asyncio.Queue`. The decision loop awaits :meth:`get`
      and processes frames one at a time; a fast producer cannot
      starve memory because the queue is bounded by ``maxsize``.

    * :meth:`snapshot` — instantaneous tuple of the most-recent
      ``window`` items. Used by the engines for windowed analytics
      (drift, velocity, depth) without consuming the queue.

    Items pushed via :meth:`put` land in BOTH surfaces atomically
    from the caller's perspective — once the put returns, the snapshot
    includes the new item and the queue is ready to deliver it.
    """

    def __init__(self, *, maxsize: int = 1024, window: int = 256) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0 (got {maxsize})")
        if window <= 0:
            raise ValueError(f"window must be > 0 (got {window})")
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._window: deque[T] = deque(maxlen=window)

    async def put(self, item: T) -> None:
        """Push ``item`` onto the queue + the rolling window.

        Blocks if the queue is full — that's the backpressure contract
        the decision loop relies on (a slow consumer slows the
        producer; we never silently drop frames).
        """
        await self._queue.put(item)
        self._window.append(item)

    async def get(self) -> T:
        """Pop one item off the queue (FIFO). Blocks until one is available."""
        return await self._queue.get()

    def snapshot(self) -> tuple[T, ...]:
        """Instantaneous tuple of the most-recent ``window`` items."""
        return tuple(self._window)

    def qsize(self) -> int:
        """Current queue depth — useful for adapter health metrics."""
        return self._queue.qsize()

    def window_size(self) -> int:
        """Current rolling-window depth (≤ configured ``window``)."""
        return len(self._window)


__all__ = ["Clock", "DegradedFeedWarning", "RealtimeBuffer", "UtcClock"]
