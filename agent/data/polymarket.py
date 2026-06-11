# §-references mirror PRD / TECHNICAL_PLAN notation.
"""Polymarket realtime adapter — WS orderbook subscriber + REST fallback.

Wires the α₂ 盘口动量 engine to a live feed in Phase 2 per PRD §7 +
TECHNICAL_PLAN §4.5: subscribe over WebSocket to one NBA market's
orderbook, aggregate to the five canonical features
(``implied_prob_drift``, ``velocity_1h`` / ``velocity_4h``,
``depth_imbalance``, ``volume_acceleration``, ``spread_tightness``),
emit :class:`OrderbookSnapshot` frames the decision loop consumes.

The adapter exposes an **async context manager**::

    async with PolymarketLive(websocket_factory=ws_factory) as feed:
        async for evt in feed.orderbook("0xmarket"):
            if isinstance(evt, DegradedFeedWarning):
                log.warning("polymarket feed degraded: %s", evt.reason)
                continue
            # evt is an OrderbookSnapshot — pipe to market_momentum.

Hard rules enforced inline:

* ``available_at`` on EVERY emitted record is the **wire arrival**
  timestamp from the injected clock — NEVER a payload field. The
  look-ahead auditor (PRD §14.1) treats payload-derived timestamps
  as look-ahead by default. The wire-arrival convention is mechanical:
  the timestamp is taken AFTER recv returns, BEFORE any payload
  parsing.

* WebSocket disconnect (or factory failure) triggers exponential-
  backoff reconnect — 1s, 2s, 4s, 8s, 16s — capped at five attempts.
  Each failure surfaces a :class:`DegradedFeedWarning` alongside the
  data stream so the agent loop can downweight or pause.

* Zero live network at import / construct time. All I/O is mediated
  through injected :class:`_WebSocketFactory` and :class:`_RestFetcher`
  Protocols; production wiring lands in T-B-007.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.data._realtime_buffer import (
    Clock,
    DegradedFeedWarning,
    RealtimeBuffer,
    UtcClock,
)

# ----- Wire types -----


class OrderbookSnapshot(BaseModel):
    """Aggregated Polymarket orderbook snapshot, wire-stable.

    One snapshot per processed WS frame. Bids + asks are sorted
    descending / ascending by price respectively (best price first).
    The five aggregate features mirror TECHNICAL_PLAN §4.5; the
    market_momentum engine fuses them into its α₂ score.

    ``available_at`` is the **wire arrival** ISO-8601 UTC timestamp
    set at the moment :meth:`_WebSocketLike.recv` returned — NOT a
    payload field. This is the contract the look-ahead auditor reads.
    """

    model_config = ConfigDict(extra="forbid")

    market_id: str
    bids: list[tuple[float, float]] = Field(default_factory=list)
    asks: list[tuple[float, float]] = Field(default_factory=list)
    mid: float
    spread: float
    available_at: str
    # Aggregated features (TECHNICAL_PLAN §4.5).
    implied_prob_drift: float = 0.0
    velocity_1h: float = 0.0
    velocity_4h: float = 0.0
    depth_imbalance: float = 0.0
    volume_acceleration: float = 0.0
    spread_tightness: float = 0.0


# ----- Transport Protocols (injected; never imported as concrete deps) -----


class _WebSocketLike(Protocol):
    """The minimal websocket surface :class:`PolymarketLive` consumes."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str: ...
    async def close(self) -> None: ...


class _WebSocketFactory(Protocol):
    """Async callable returning a connected websocket-like."""

    async def __call__(self, url: str) -> _WebSocketLike: ...


class _RestFetcher(Protocol):
    """Minimal async JSON GET — used for upcoming-markets discovery."""

    async def get_json(self, url: str) -> dict[str, Any]: ...


# The yielded event union — either a data frame or a degradation warning.
PolymarketEvent = OrderbookSnapshot | DegradedFeedWarning


# ----- Defaults -----


_DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_DEFAULT_REST_URL = "https://clob.polymarket.com"
_DEFAULT_MAX_RECONNECT = 5
_DEFAULT_BUFFER_MAXSIZE = 1024
_DEFAULT_WINDOW = 256
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_CAP = 30.0


# ----- Public class -----


class PolymarketLive:
    """Async-context-manager Polymarket realtime feed.

    Construct with an injected ``websocket_factory`` (and optionally a
    REST fetcher for market-discovery). Production wiring lands in
    T-B-007; tests inject fakes via :mod:`tests.agent.data.conftest`.

    All public methods are coroutines. The async context manager
    protocol is mandatory: ``aclose`` releases the underlying socket.
    """

    def __init__(
        self,
        *,
        websocket_factory: _WebSocketFactory,
        rest_fetcher: _RestFetcher | None = None,
        ws_url: str = _DEFAULT_WS_URL,
        rest_url: str = _DEFAULT_REST_URL,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_reconnect_attempts: int = _DEFAULT_MAX_RECONNECT,
        buffer_maxsize: int = _DEFAULT_BUFFER_MAXSIZE,
        window: int = _DEFAULT_WINDOW,
    ) -> None:
        if max_reconnect_attempts < 1:
            raise ValueError(
                f"max_reconnect_attempts must be ≥ 1 (got {max_reconnect_attempts})"
            )
        self._ws_factory = websocket_factory
        self._rest = rest_fetcher
        self._ws_url = ws_url
        self._rest_url = rest_url.rstrip("/")
        self._clock: Clock = clock if clock is not None else UtcClock()
        self._sleep = sleep
        self._max_attempts = max_reconnect_attempts
        self._buffer: RealtimeBuffer[OrderbookSnapshot] = RealtimeBuffer(
            maxsize=buffer_maxsize, window=window,
        )
        self._ws: _WebSocketLike | None = None
        self._closed = False

    @property
    def buffer(self) -> RealtimeBuffer[OrderbookSnapshot]:
        """Exposed so engines can call :meth:`RealtimeBuffer.snapshot`
        for windowed analytics without consuming the queue."""
        return self._buffer

    async def __aenter__(self) -> PolymarketLive:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Mark feed closed + best-effort tear down the current socket."""
        self._closed = True
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # pragma: no cover — best-effort
                pass

    async def upcoming_markets(self, *, sport: str = "nba") -> list[dict[str, Any]]:
        """REST fallback: list upcoming markets for ``sport``.

        Used by the agent main loop (T-B-007) when the WS market-list
        push isn't available. Returns a list of raw market metadata
        dicts; callers project to whatever shape they need. Empty
        list if the response is malformed (defensive).
        """
        if self._rest is None:
            raise RuntimeError(
                "PolymarketLive.upcoming_markets requires a rest_fetcher — "
                "construct with rest_fetcher=… to enable REST fallback."
            )
        url = f"{self._rest_url}/markets?sport={sport}"
        payload = await self._rest.get_json(url)
        rows = payload.get("markets") or payload.get("data") or []
        if not isinstance(rows, list):
            return []
        # Mypy: rows is list[Any]; we want list[dict[str, Any]] for the contract.
        out: list[dict[str, Any]] = [r for r in rows if isinstance(r, dict)]
        return out

    async def orderbook(self, market_id: str) -> AsyncIterator[PolymarketEvent]:
        """Subscribe + yield aggregated orderbook snapshots until close.

        On the happy path: yields :class:`OrderbookSnapshot` per frame.
        On WS disconnect or factory failure: yields
        :class:`DegradedFeedWarning`, sleeps with exponential backoff,
        and reconnects. After ``max_reconnect_attempts`` consecutive
        failures the iterator terminates (the agent loop will route
        through the REST fallback or pause the engine).
        """
        attempt = 0
        backoff = _DEFAULT_BACKOFF_BASE

        while not self._closed:
            ws: _WebSocketLike | None = None
            try:
                ws = await self._ws_factory(self._ws_url)
                self._ws = ws
                subscribe_msg = json.dumps(
                    {"type": "subscribe", "channel": "market", "market_id": market_id}
                )
                await ws.send(subscribe_msg)
                # Connected: reset backoff counters.
                attempt = 0
                backoff = _DEFAULT_BACKOFF_BASE

                # Stream frames until the connection closes.
                while not self._closed:
                    raw: str
                    try:
                        raw = await ws.recv()
                    except (ConnectionError, OSError, EOFError) as exc:
                        # Mid-stream disconnect — break out to reconnect.
                        attempt += 1
                        warn = DegradedFeedWarning(
                            feed="polymarket_ws",
                            reason=f"recv_failed:{type(exc).__name__}",
                            attempt=attempt,
                            available_at=self._clock.now().isoformat(),
                        )
                        yield warn
                        break
                    except StopAsyncIteration:
                        # The fake websocket signals end-of-stream this way
                        # (so tests don't need to raise ConnectionError).
                        attempt += 1
                        warn = DegradedFeedWarning(
                            feed="polymarket_ws",
                            reason="stream_ended",
                            attempt=attempt,
                            available_at=self._clock.now().isoformat(),
                        )
                        yield warn
                        break

                    # ---- CRITICAL: wire-arrival timestamp captured HERE,
                    # BEFORE payload parsing. Look-ahead auditor reads
                    # this — see module docstring contract.
                    arrival = self._clock.now()
                    snap = self._aggregate_frame(market_id, raw, arrival)
                    if snap is None:
                        # Non-orderbook frame (heartbeat, subscribe-ack);
                        # ignore but keep the connection live.
                        continue
                    await self._buffer.put(snap)
                    yield snap
                else:
                    # self._closed flipped while inside the inner loop.
                    return

                # Inner loop exited via `break` — fall through to backoff.
                if self._closed:
                    return
                if attempt >= self._max_attempts:
                    return
                await self._sleep(backoff)
                backoff = min(backoff * 2.0, _DEFAULT_BACKOFF_CAP)

            except Exception as exc:
                # Factory raised, send raised, or anything else upstream.
                attempt += 1
                yield DegradedFeedWarning(
                    feed="polymarket_ws",
                    reason=f"connect_failed:{type(exc).__name__}",
                    attempt=attempt,
                    available_at=self._clock.now().isoformat(),
                )
                if self._closed or attempt >= self._max_attempts:
                    return
                await self._sleep(backoff)
                backoff = min(backoff * 2.0, _DEFAULT_BACKOFF_CAP)
            finally:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:  # pragma: no cover — best-effort
                        pass
                self._ws = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _aggregate_frame(
        self, market_id: str, raw: str, arrival: datetime,
    ) -> OrderbookSnapshot | None:
        """Parse one WS frame into an :class:`OrderbookSnapshot`.

        ``arrival`` is the wire-arrival timestamp captured BEFORE this
        function ran — the snapshot's ``available_at`` MUST be derived
        from it (per the module-level look-ahead contract).
        """
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None

        evt_type = payload.get("event_type") or payload.get("type")
        # Subscribe-acks, heartbeats, error frames → drop, keep stream live.
        if evt_type not in (None, "book", "orderbook", "level2"):
            return None
        # Some heartbeat frames lack a market_id but still match the above;
        # require either bids or asks to be present so we don't synthesise
        # zero-depth snapshots.
        raw_bids = payload.get("bids") or []
        raw_asks = payload.get("asks") or []
        if not raw_bids and not raw_asks:
            return None

        bids = _coerce_levels(raw_bids)
        asks = _coerce_levels(raw_asks)

        # Sort: bids descending (best = highest), asks ascending.
        bids.sort(key=lambda r: r[0], reverse=True)
        asks.sort(key=lambda r: r[0])

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        if best_bid > 0.0 and best_ask > 0.0:
            mid = (best_bid + best_ask) / 2.0
            spread = max(best_ask - best_bid, 0.0)
        elif best_bid > 0.0:
            mid = best_bid
            spread = 0.0
        elif best_ask > 0.0:
            mid = best_ask
            spread = 0.0
        else:
            mid = 0.0
            spread = 0.0

        bid_depth = sum(sz for _, sz in bids)
        ask_depth = sum(sz for _, sz in asks)
        total_depth = bid_depth + ask_depth
        depth_imbalance = (
            (bid_depth - ask_depth) / total_depth if total_depth > 0.0 else 0.0
        )
        spread_tightness = 1.0 / (1.0 + spread)

        # Cross-frame analytics: implied_prob_drift / velocity / volume_acc
        # are computed against the rolling window snapshot.
        window = self._buffer.snapshot()
        implied_prob_drift = _drift_from_window(window, mid)
        velocity_1h = _velocity_over_window(window, mid, arrival, hours=1.0)
        velocity_4h = _velocity_over_window(window, mid, arrival, hours=4.0)
        volume_acceleration = _volume_acceleration(window, total_depth)

        return OrderbookSnapshot(
            market_id=market_id,
            bids=bids,
            asks=asks,
            mid=mid,
            spread=spread,
            available_at=arrival.isoformat(),
            implied_prob_drift=implied_prob_drift,
            velocity_1h=velocity_1h,
            velocity_4h=velocity_4h,
            depth_imbalance=depth_imbalance,
            volume_acceleration=volume_acceleration,
            spread_tightness=spread_tightness,
        )


# ----- Pure helpers — unit-testable independently -----


def _coerce_levels(raw: list[Any]) -> list[tuple[float, float]]:
    """Project a Polymarket bids/asks list onto ``[(price, size), …]``.

    Polymarket emits levels as ``[[price_str, size_str], …]`` or as
    ``[{"price": …, "size": …}, …]`` depending on channel. Both are
    handled; malformed rows are skipped.
    """
    out: list[tuple[float, float]] = []
    for row in raw:
        try:
            if isinstance(row, dict):
                price = float(row.get("price", 0.0))
                size = float(row.get("size", 0.0))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                price = float(row[0])
                size = float(row[1])
            else:
                continue
        except (TypeError, ValueError):
            continue
        if price <= 0.0 or size < 0.0:
            continue
        out.append((price, size))
    return out


def _drift_from_window(
    window: tuple[OrderbookSnapshot, ...], latest_mid: float,
) -> float:
    """EWMA-flavoured drift of the latest mid vs window mean.

    Returns ``(latest_mid - mean_window_mid) / mean_window_mid`` clipped
    by tanh to [-1, 1] so a runaway move on a thin market can't escape
    the engine's normalisation invariant.
    """
    if not window:
        return 0.0
    mean_mid = sum(s.mid for s in window) / len(window)
    if mean_mid <= 0.0:
        return 0.0
    raw = (latest_mid - mean_mid) / mean_mid
    return math.tanh(raw)


def _velocity_over_window(
    window: tuple[OrderbookSnapshot, ...],
    latest_mid: float,
    arrival: datetime,
    *,
    hours: float,
) -> float:
    """Mid-price velocity over the last ``hours``-hour subset of the window.

    Defined as ``(latest_mid - earliest_in_window_mid) / hours``. Empty
    or single-frame window → 0.0 (no velocity to compute).
    """
    if not window:
        return 0.0
    horizon_seconds = hours * 3600.0
    # Pick the earliest snapshot inside the window that is within
    # ``hours`` of arrival — that's the velocity baseline.
    baseline_mid: float | None = None
    for s in window:
        ts = _parse_iso(s.available_at)
        if ts is None:
            continue
        delta = (arrival - ts).total_seconds()
        if 0.0 < delta <= horizon_seconds:
            baseline_mid = s.mid
            break
    if baseline_mid is None or hours <= 0.0:
        return 0.0
    return (latest_mid - baseline_mid) / hours


def _volume_acceleration(
    window: tuple[OrderbookSnapshot, ...], latest_total_depth: float,
) -> float:
    """How much the latest total depth has grown vs the window mean.

    Returns ``(latest - mean) / max(mean, 1.0)``. Used by the engine
    to detect liquidity inflow that often precedes a price move.
    """
    if not window:
        return 0.0
    depths = [sum(sz for _, sz in s.bids) + sum(sz for _, sz in s.asks) for s in window]
    if not depths:
        return 0.0
    mean_d = sum(depths) / len(depths)
    return (latest_total_depth - mean_d) / max(mean_d, 1.0)


def _parse_iso(s: str) -> datetime | None:
    """Defensive ISO-8601 decode; returns None on malformed strings."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


__all__ = [
    "DegradedFeedWarning",
    "OrderbookSnapshot",
    "PolymarketEvent",
    "PolymarketLive",
]
