"""Pydantic schemas for the four Genesis data streams.

These models are the parquet-on-disk contract for the four feeds:

* :class:`NBAGameRow` — one NBA game's box-score snapshot.
* :class:`PolymarketSnapshotRow` — one orderbook midpoint snapshot.
* :class:`PolygonEventRow` — one decoded chain event.
* :class:`RedditWindowRow` — one subreddit sentiment window.

Each model is :class:`pydantic.BaseModel`-based, frozen, and carries an
``available_at`` field — the cross-track PIT key that
:func:`data.etl.pit_correct.assert_no_lookahead` filters on.

Importing this module is cheap (no I/O, no network).
"""

from __future__ import annotations

from data.schemas.streams import (
    NBAGameRow,
    PolygonEventRow,
    PolymarketSnapshotRow,
    RedditWindowRow,
    arrow_schema_for,
    available_at_columns,
)

__all__ = [
    "NBAGameRow",
    "PolygonEventRow",
    "PolymarketSnapshotRow",
    "RedditWindowRow",
    "arrow_schema_for",
    "available_at_columns",
]
