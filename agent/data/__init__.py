"""Realtime data adapters consumed by the Agent decision loop (Phase 2+).

Three feeds wired per PRD §7 + TECHNICAL_PLAN §8 Day 8:

* :class:`PolymarketLive` — WebSocket orderbook subscriber for the
  α₂ 盘口动量 engine, plus REST fallback for upcoming-markets
  discovery.
* :class:`PolygonChainLive` — Polygon Amoy ``eth_subscribe`` reader
  for Polymarket CTF Exchange fills, intersected with the smart-money
  whitelist to feed the α₃ Smart Money engine.
* :class:`NBALive` — balldontlie live-game poller for the α₁ NBA
  technical engine.

All three:

* Expose an **async context manager** API (``async with X(...) as
  feed: …``).
* Stamp every emitted record's ``available_at`` with the **wire
  arrival** time captured BEFORE payload parsing — never a payload
  field. Look-ahead auditor reads this convention.
* Surface :class:`DegradedFeedWarning` on transient failures and
  attempt exponential-backoff reconnect (max 5 attempts).
* Have zero live network at import / construct time — all transports
  are injected Protocols; production wiring lands in T-B-007.

Sprint_4 (T-B-005) ships the adapters themselves. The agent main
loop body that consumes them is T-B-007's deliverable.
"""

from __future__ import annotations

from agent.data._realtime_buffer import DegradedFeedWarning, RealtimeBuffer
from agent.data.nba_live import GameSnapshot, NBALive, NBALiveEvent
from agent.data.polygon_chain import (
    PolygonChainEvent,
    PolygonChainLive,
    SmartMoneyPosition,
)
from agent.data.polymarket import OrderbookSnapshot, PolymarketEvent, PolymarketLive

__all__ = [
    "DegradedFeedWarning",
    "GameSnapshot",
    "NBALive",
    "NBALiveEvent",
    "OrderbookSnapshot",
    "PolygonChainEvent",
    "PolygonChainLive",
    "PolymarketEvent",
    "PolymarketLive",
    "RealtimeBuffer",
    "SmartMoneyPosition",
]
