"""Source-adapter stubs for the four Genesis data feeds.

Each adapter is a thin client wrapper around an external data source:

* :mod:`data.sources.nba` — NBA box-score / player-stat history.
* :mod:`data.sources.polymarket` — Polymarket market-history reads
  (orderbook snapshots, settled-market archive).
* :mod:`data.sources.polygon` — Polygon chain RPC reads (event-log
  scans for Smart Money wallet identification).
* :mod:`data.sources.reddit` — Reddit / Pushshift sentiment snapshots.

Sprint_2 wires real read-only network I/O for all four adapters. Each
``.fetch_*`` entrypoint requires the ``asof_ts`` keyword and rejects
naive or missing timestamps via :class:`data.etl.pit_correct.LookaheadError`
BEFORE any upstream call — burning a quota token on a leak is itself a
bug.

Live network calls are gated behind the ``live`` pytest marker; CI
runs replay recorded fixtures under ``tests/data/fixtures/`` via the
injected :class:`requests.Session` path each client exposes.
"""

from __future__ import annotations

# Warm ``data.etl`` BEFORE ``data.sources._http`` to resolve the cold-start import
# cycle (``_http`` imports ``data.etl.pit_correct``; ``data.etl.__init__`` imports
# ``build_training_set`` → ``data.sources.nba`` → ``_http``). Importing data.etl first
# caches pit_correct so ``_http`` initializes cleanly no matter which data.sources
# submodule is the cold entry point (Codex Phase-3 LOW — removes the per-caller
# warm-up that polymarket_trades + the P2 probe previously needed).
import data.etl.pit_correct  # noqa: F401
from data.sources._http import HttpClient, require_asof_ts
from data.sources.nba import NBAClient, NBAGame
from data.sources.polygon import ChainEvent, PolygonChainClient
from data.sources.polymarket import MarketHistory, PolymarketHistoryClient
from data.sources.reddit import RedditSentimentClient, SentimentSnapshot

__all__ = [
    "ChainEvent",
    "HttpClient",
    "MarketHistory",
    "NBAClient",
    "NBAGame",
    "PolygonChainClient",
    "PolymarketHistoryClient",
    "RedditSentimentClient",
    "SentimentSnapshot",
    "require_asof_ts",
]
