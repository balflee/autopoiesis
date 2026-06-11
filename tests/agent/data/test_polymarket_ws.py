"""Tests for :mod:`agent.data.polymarket` — PolymarketLive realtime feed.

Coverage:

1. subscribe — happy path emits one snapshot per WS frame, sends a
   subscribe message before any recv.
2. ws-disconnect-reconnect — ConnectionError mid-stream triggers a
   DegradedFeedWarning, exponential-backoff sleep, then a fresh
   factory call (max 5 attempts).
3. orderbook depth aggregation — bids/asks coerced + sorted, depth
   imbalance + spread_tightness computed.
4. ``available_at`` is the wire-arrival timestamp (NOT a payload
   field). Verified by feeding a frame with a far-future payload
   ``timestamp`` and asserting the snapshot's available_at matches
   the injected clock, not the payload.
5. REST fallback — upcoming_markets() returns parsed market dicts.

Plus: max-reconnect cap, idle frames (heartbeats) ignored, async-context-
manager close path.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from agent.data import (
    DegradedFeedWarning,
    OrderbookSnapshot,
    PolymarketLive,
)
from tests.agent.data.conftest import (
    FakeRestFetcher,
    FakeWebSocket,
    FakeWebSocketFactory,
    InstantSleep,
    SteppingClock,
)


def _ob_frame(
    *,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
    timestamp: int = 0,
) -> str:
    return json.dumps(
        {
            "event_type": "book",
            "market_id": "0xmarket",
            "bids": bids or [["0.55", "100"]],
            "asks": asks or [["0.58", "150"]],
            "timestamp": timestamp,
        }
    )


# --------------------------------------------------------------------------- #


def test_polymarket_subscribe_happy_path(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Subscribe + recv 2 frames → 2 OrderbookSnapshots; subscribe sent first."""
    ws = FakeWebSocket(
        frames=[
            _ob_frame(bids=[["0.55", "100"]], asks=[["0.58", "150"]]),
            _ob_frame(bids=[["0.56", "120"]], asks=[["0.57", "140"]]),
            "_END_",
        ]
    )
    factory = FakeWebSocketFactory(queue=[ws])

    async def run() -> list[object]:
        async with PolymarketLive(
            websocket_factory=factory,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=1,  # stop after 1 reconnect attempt
        ) as feed:
            out: list[object] = []
            async for evt in feed.orderbook("0xmarket"):
                out.append(evt)
                if len(out) >= 5:
                    break
            return out

    events = asyncio.run(run())
    snapshots = [e for e in events if isinstance(e, OrderbookSnapshot)]
    assert len(snapshots) == 2
    assert all(s.market_id == "0xmarket" for s in snapshots)
    # Subscribe message sent BEFORE first recv.
    assert len(ws.sent) == 1
    subscribe_payload = json.loads(ws.sent[0])
    assert subscribe_payload["type"] == "subscribe"
    assert subscribe_payload["market_id"] == "0xmarket"


def test_polymarket_reconnect_on_disconnect(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """ConnectionError mid-stream → DegradedFeedWarning + reconnect."""
    ws1 = FakeWebSocket(
        frames=[
            _ob_frame(),
            ConnectionError("simulated mid-stream drop"),
        ]
    )
    ws2 = FakeWebSocket(frames=[_ob_frame(), "_END_"])
    factory = FakeWebSocketFactory(queue=[ws1, ws2])

    async def run() -> list[object]:
        # Stop the test cleanly: with max=2 we get one reconnect, then
        # ws2 ends → second warn → adapter returns (attempt counter
        # incremented past max only AFTER ws2 hits StopAsyncIteration).
        async with PolymarketLive(
            websocket_factory=factory,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=2,
        ) as feed:
            out: list[object] = []
            async for evt in feed.orderbook("0xmarket"):
                out.append(evt)
                if len(out) >= 10:
                    break
            return out

    events = asyncio.run(run())
    snaps = [e for e in events if isinstance(e, OrderbookSnapshot)]
    warns = [e for e in events if isinstance(e, DegradedFeedWarning)]
    # First frame on ws1, then disconnect, then reconnect → second frame on ws2.
    assert len(snaps) == 2
    assert len(warns) >= 1
    # First warning is the recv_failed from the simulated mid-stream drop.
    recv_warns = [w for w in warns if "recv_failed" in w.reason]
    assert recv_warns
    assert recv_warns[0].feed == "polymarket_ws"
    assert recv_warns[0].attempt == 1
    # Factory was called at least twice (reconnect happened).
    assert len(factory.calls) >= 2
    # Sleep was invoked at least once for the exponential backoff.
    assert len(instant_sleep.calls) >= 1
    assert instant_sleep.calls[0] >= 1.0


def test_polymarket_max_reconnect_cap(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Factory keeps failing → adapter gives up after max_reconnect_attempts."""
    factory = FakeWebSocketFactory(
        queue=[ConnectionRefusedError("nope")] * 10  # always fails
    )

    async def run() -> list[object]:
        async with PolymarketLive(
            websocket_factory=factory,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=3,
        ) as feed:
            out: list[object] = []
            async for evt in feed.orderbook("0xmarket"):
                out.append(evt)
                if len(out) > 10:
                    break
            return out

    events = asyncio.run(run())
    warns = [e for e in events if isinstance(e, DegradedFeedWarning)]
    assert len(warns) == 3  # max_reconnect_attempts
    # The last warning's attempt counter is exactly max.
    assert warns[-1].attempt == 3
    assert all("connect_failed" in w.reason for w in warns)


def test_polymarket_depth_aggregation(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Bids/asks parsed, sorted, depth + spread_tightness computed."""
    # Unsorted input — adapter must sort: bids desc, asks asc.
    ws = FakeWebSocket(
        frames=[
            json.dumps(
                {
                    "event_type": "book",
                    "bids": [["0.55", "100"], ["0.58", "200"], ["0.57", "150"]],
                    "asks": [["0.62", "100"], ["0.60", "200"], ["0.61", "150"]],
                }
            ),
            "_END_",
        ]
    )
    factory = FakeWebSocketFactory(queue=[ws])

    async def run() -> OrderbookSnapshot:
        async with PolymarketLive(
            websocket_factory=factory,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            async for evt in feed.orderbook("0xmarket"):
                if isinstance(evt, OrderbookSnapshot):
                    return evt
        raise AssertionError("no snapshot emitted")

    snap = asyncio.run(run())
    # Best bid = 0.58 (highest), best ask = 0.60 (lowest).
    assert snap.bids[0][0] == pytest.approx(0.58)
    assert snap.asks[0][0] == pytest.approx(0.60)
    assert snap.mid == pytest.approx(0.59)
    assert snap.spread == pytest.approx(0.02)
    # Depths: 100+200+150=450 each side → balanced → 0 imbalance.
    assert snap.depth_imbalance == pytest.approx(0.0, abs=1e-9)
    # Spread_tightness = 1/(1+0.02) ≈ 0.9804
    assert snap.spread_tightness == pytest.approx(1.0 / 1.02)


def test_polymarket_available_at_is_wire_arrival_not_payload(
    instant_sleep: InstantSleep,
) -> None:
    """``available_at`` must come from the injected clock, NEVER the payload.

    We feed a frame whose payload ``timestamp`` is far in the future
    (year 2099). If the adapter incorrectly trusted the payload, the
    snapshot's ``available_at`` would also be 2099. The adapter MUST
    instead stamp wire-arrival ⇒ the injected fixed clock value.
    """
    fixed_time = datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)

    class _FixedClock:
        def now(self) -> datetime:
            return fixed_time

    far_future_ts = 4070908800  # year 2099 unix seconds
    ws = FakeWebSocket(
        frames=[
            json.dumps(
                {
                    "event_type": "book",
                    "bids": [["0.55", "100"]],
                    "asks": [["0.58", "150"]],
                    "timestamp": far_future_ts,
                    "created_at": "2099-01-01T00:00:00Z",
                }
            ),
            "_END_",
        ]
    )
    factory = FakeWebSocketFactory(queue=[ws])

    async def run() -> OrderbookSnapshot:
        async with PolymarketLive(
            websocket_factory=factory,
            clock=_FixedClock(),
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            async for evt in feed.orderbook("0xmarket"):
                if isinstance(evt, OrderbookSnapshot):
                    return evt
        raise AssertionError("no snapshot")

    snap = asyncio.run(run())
    parsed = datetime.fromisoformat(snap.available_at)
    assert parsed == fixed_time
    # Cross-check: NOT the payload field's value.
    assert "2099" not in snap.available_at


def test_polymarket_rest_fallback_upcoming_markets() -> None:
    """REST fallback returns the parsed markets list."""
    fetcher = FakeRestFetcher(
        routes={
            "https://example.com/markets?sport=nba": {
                "markets": [
                    {"id": "0xm1", "slug": "lakers-celtics"},
                    {"id": "0xm2", "slug": "nuggets-suns"},
                    "garbage_str_to_ignore",
                ],
            }
        }
    )

    async def run() -> list[dict[str, object]]:
        async with PolymarketLive(
            websocket_factory=FakeWebSocketFactory(queue=[]),
            rest_fetcher=fetcher,
            rest_url="https://example.com",
            max_reconnect_attempts=1,
        ) as feed:
            return await feed.upcoming_markets(sport="nba")

    markets = asyncio.run(run())
    assert len(markets) == 2
    assert {m["slug"] for m in markets} == {"lakers-celtics", "nuggets-suns"}
    assert len(fetcher.calls) == 1


def test_polymarket_heartbeats_and_acks_ignored(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Non-orderbook frames (heartbeat, subscribe_ack) MUST NOT emit a snapshot."""
    ws = FakeWebSocket(
        frames=[
            json.dumps({"event_type": "heartbeat"}),
            json.dumps({"event_type": "subscribed", "market_id": "0xmarket"}),
            _ob_frame(),
            "_END_",
        ]
    )
    factory = FakeWebSocketFactory(queue=[ws])

    async def run() -> list[object]:
        async with PolymarketLive(
            websocket_factory=factory,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            out: list[object] = []
            async for evt in feed.orderbook("0xmarket"):
                out.append(evt)
                if len(out) > 5:
                    break
            return out

    events = asyncio.run(run())
    snaps = [e for e in events if isinstance(e, OrderbookSnapshot)]
    # Exactly one snapshot from the single book frame.
    assert len(snaps) == 1


def test_polymarket_aclose_drops_socket() -> None:
    """Async-context-manager exit closes the underlying socket."""
    ws = FakeWebSocket(frames=[_ob_frame(), "_END_"])
    factory = FakeWebSocketFactory(queue=[ws])

    async def run() -> bool:
        async with PolymarketLive(
            websocket_factory=factory,
            sleep=InstantSleep(),
            max_reconnect_attempts=1,
        ) as feed:
            async for _evt in feed.orderbook("0xmarket"):
                break  # consume one then bail
        return ws.closed

    closed = asyncio.run(run())
    assert closed is True
