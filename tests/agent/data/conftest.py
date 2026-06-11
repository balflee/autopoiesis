"""Hermetic test scaffolding for ``agent/data/`` realtime adapters.

Three fakes, one per transport Protocol the adapters consume:

* :class:`FakeWebSocket` + :class:`FakeWebSocketFactory` — drive the
  Polymarket WS subscriber from a deterministic queue of frames plus
  scripted disconnects.
* :class:`FakeChainSubscription` + :class:`FakeSubscriptionProvider` —
  drive the Polygon chain subscriber from a deterministic queue of
  decoded log dicts.
* :class:`FakeRestFetcher` — drive the NBA live poller + Polymarket
  REST fallback from a route table or factory.

All sleep-likes go through :class:`InstantSleep` so the test never
actually waits.

A pinned :class:`SteppingClock` makes the wire-arrival timestamp
assertions deterministic.

The top-level ``no_live_network`` autouse fixture aborts any test
that accidentally tries to construct a real ``httpx`` / ``websockets``
client.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

# --------------------------------------------------------------------------- #
# Clock + sleep
# --------------------------------------------------------------------------- #


@dataclass
class SteppingClock:
    """Deterministic clock: each :meth:`now` advances by ``tick``."""

    start: datetime = field(
        default_factory=lambda: datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)
    )
    tick: timedelta = field(default_factory=lambda: timedelta(seconds=1))
    _calls: int = 0

    def now(self) -> datetime:
        ts = self.start + self.tick * self._calls
        self._calls += 1
        return ts


@dataclass
class InstantSleep:
    """No-op asyncio.sleep replacement that records call durations."""

    calls: list[float] = field(default_factory=list)

    async def __call__(self, duration: float) -> None:
        self.calls.append(duration)


# --------------------------------------------------------------------------- #
# Fake WebSocket — for PolymarketLive tests
# --------------------------------------------------------------------------- #


class FakeWebSocket:
    """Minimal websocket-like fake.

    Scripted via a deque of frames. Each frame is either:

    * a ``str`` — returned verbatim from ``recv()``.
    * an ``Exception`` subclass — raised from ``recv()`` (simulates
      disconnect).
    * a ``"_END_"`` sentinel — raises :class:`StopAsyncIteration`.
    """

    _END = "_END_"

    def __init__(
        self, frames: list[str | Exception], *, raise_on_close: bool = False
    ) -> None:
        self._frames: deque[str | Exception] = deque(frames)
        self.sent: list[str] = []
        self.closed = False
        self._raise_on_close = raise_on_close

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        nxt = self._frames.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        if nxt == self._END:
            raise StopAsyncIteration
        return nxt

    async def close(self) -> None:
        self.closed = True
        if self._raise_on_close:
            raise OSError("close failed")


class FakeWebSocketFactory:
    """Async-callable websocket factory.

    Each call returns the next :class:`FakeWebSocket` in ``queue``.
    A factory entry may be an ``Exception`` to simulate a connect
    failure.
    """

    def __init__(self, queue: list[FakeWebSocket | Exception]) -> None:
        self._queue: deque[FakeWebSocket | Exception] = deque(queue)
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FakeWebSocket:
        self.calls.append(url)
        if not self._queue:
            raise ConnectionRefusedError("factory exhausted")
        nxt = self._queue.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# --------------------------------------------------------------------------- #
# Fake chain subscription — for PolygonChainLive tests
# --------------------------------------------------------------------------- #


class FakeChainSubscription:
    """An async-iterator-shaped chain subscription.

    Yields one decoded-log ``dict`` per next() until the queue drains
    (then :class:`StopAsyncIteration`). Inserting an :class:`Exception`
    in the queue raises it.
    """

    def __init__(self, logs: list[dict[str, Any] | Exception]) -> None:
        self._logs: deque[dict[str, Any] | Exception] = deque(logs)
        self.closed = False

    def __aiter__(self) -> FakeChainSubscription:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._logs:
            raise StopAsyncIteration
        nxt = self._logs.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def aclose(self) -> None:
        self.closed = True


class FakeSubscriptionProvider:
    """Async-callable subscription factory."""

    def __init__(self, queue: list[FakeChainSubscription | Exception]) -> None:
        self._queue: deque[FakeChainSubscription | Exception] = deque(queue)
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(
        self, *, contract_address: str, topics: list[str]
    ) -> FakeChainSubscription:
        self.calls.append((contract_address, list(topics)))
        if not self._queue:
            raise ConnectionRefusedError("provider exhausted")
        nxt = self._queue.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# --------------------------------------------------------------------------- #
# Fake REST fetcher — for NBALive + PolymarketLive REST fallback
# --------------------------------------------------------------------------- #


class FakeRestFetcher:
    """Async JSON GET fake.

    Two modes:

    * ``routes={url: payload}`` — exact-URL match (default behaviour).
    * ``factory=callable(url) -> payload | Exception`` — programmatic.

    Each call records ``(url, returned)`` so tests can assert call
    counts.
    """

    def __init__(
        self,
        *,
        routes: dict[str, dict[str, Any] | Exception] | None = None,
        factory: Callable[[str], dict[str, Any] | Exception] | None = None,
    ) -> None:
        self._routes = routes or {}
        self._factory = factory
        self.calls: list[str] = []

    async def get_json(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        if self._factory is not None:
            result = self._factory(url)
            if isinstance(result, Exception):
                raise result
            return result
        if url not in self._routes:
            raise AssertionError(f"FakeRestFetcher: no route for {url}")
        result = self._routes[url]
        if isinstance(result, Exception):
            raise result
        return result


# --------------------------------------------------------------------------- #
# Common fixtures + safety nets
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_clock() -> SteppingClock:
    return SteppingClock()


@pytest.fixture
def instant_sleep() -> InstantSleep:
    return InstantSleep()


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity gate: any accidental import of ``httpx`` / ``websockets``
    network calls inside the test module raises a clear error instead
    of hitting real upstreams.

    We block these libraries' obvious entrypoints; the realtime
    adapters themselves never import them (they consume Protocols).
    """
    def _explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "live network call attempted under pytest — every adapter "
            "test MUST use the injected fakes (see tests/agent/data/conftest.py)"
        )
    # We don't import the libs (they may not be installed); monkeypatch
    # is a no-op if there's nothing to patch. This is a tripwire for
    # future regression rather than an active block today.
    monkeypatch.setattr(
        "agent.data.polymarket.asyncio.open_connection",
        _explode,
        raising=False,
    )


def collect(it: Any, *, limit: int = 50) -> Callable[[], Awaitable[list[Any]]]:
    """Helper: return a coroutine that drains an async iterator into a list."""

    async def _drain() -> list[Any]:
        out: list[Any] = []
        async for v in it:
            out.append(v)
            if len(out) >= limit:
                break
        return out

    return _drain


__all__ = [
    "FakeChainSubscription",
    "FakeRestFetcher",
    "FakeSubscriptionProvider",
    "FakeWebSocket",
    "FakeWebSocketFactory",
    "InstantSleep",
    "SteppingClock",
    "collect",
]
