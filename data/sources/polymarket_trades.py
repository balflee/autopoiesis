"""Polymarket ACTUAL historical trades — READ-ONLY (plan-loop V1.7).

The ONLY source of REAL fill prices for the P2 favorite-longshot falsification probe.
Every price it returns carries provenance :data:`PROVENANCE_ACTUAL_TRADE` — the SINGLE
canonical spelling (Codex-r2-M5; no ``actual_fill`` alias). It NEVER synthesizes or
interpolates a ledger and has NO midpoint fallback (Codex-2: a synthetic entry price
would MANUFACTURE P2's result — `historical_fetcher` SYNTHESIZES a 3-point ledger and
`PolymarketHistoryClient.fetch_market` returns `/prices-history` MIDPOINTS, neither of
which is a real fill). When a market has no real trades, :meth:`entry_price` returns
``None`` so the probe FAILS CLOSED (skips + logs) rather than fabricating an entry.

**Hard rules** (mirrors `data.sources.polymarket`): read-only; no signing/write APIs;
``asof_ts`` PIT filtering when supplied (a future trade can never be the entry).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# NB: ``data.sources._http`` is imported LAZILY (in __post_init__) — eagerly importing
# it here triggers a pre-existing cold-start cycle (``_http`` → ``data.etl.pit_correct``
# → ``data.etl.__init__`` → ``build_training_set`` → ``data.sources.nba`` → ``_http``)
# that bites only when this module is the first import (isolated test collection). Prod
# always injects a client or has the graph warm, so the lazy import is free there.

POLYMARKET_DATA_API_BASE_URL = "https://data-api.polymarket.com"

# The SINGLE canonical provenance label for a price that came from a REAL executed
# trade. The probe asserts EXACTLY this string before scoring a bet (fail-closed).
PROVENANCE_ACTUAL_TRADE = "actual_trade"

_TRADES_PAGE_LIMIT = 1000


@dataclass(frozen=True)
class TradePrice:
    """One ACTUAL executed trade price, provenance-tagged.

    ``price`` is the executed price in (0, 1]; ``ts`` is the trade time (UTC).
    ``provenance`` is always :data:`PROVENANCE_ACTUAL_TRADE` — this type is never
    constructed from a synthetic/midpoint price.
    """

    market_id: str
    price: float
    ts: datetime
    provenance: str = PROVENANCE_ACTUAL_TRADE


def _coerce_ts(raw: object) -> datetime | None:
    """Parse a Polymarket trade timestamp: a unix-seconds int/str, or an ISO string."""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=UTC)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return None


@dataclass
class PolymarketTradesClient:
    """Read-only client for the Polymarket Data API ``/trades`` endpoint.

    ``http`` is any object exposing ``get(url, *, params, timeout) -> resp`` with
    ``resp.json()`` (a :class:`data.sources._http.HttpClient` in prod, a fake in tests).
    Left ``None`` ⇒ a default ``HttpClient`` is lazily constructed (see module note)."""

    base_url: str = POLYMARKET_DATA_API_BASE_URL
    http: Any = None

    def __post_init__(self) -> None:
        if self.http is None:
            from data.sources._http import HttpClient

            self.http = HttpClient()

    def fetch_trades(
        self, market_id: str, *, asof_ts: datetime | None = None
    ) -> list[TradePrice]:
        """Return the market's REAL trades (ascending by ``ts``), each tagged
        :data:`PROVENANCE_ACTUAL_TRADE`. When ``asof_ts`` is given, trades AFTER it are
        dropped (PIT — a future fill can never be the entry). Malformed rows are skipped,
        never synthesized."""
        if asof_ts is not None and asof_ts.tzinfo is None:
            raise ValueError("asof_ts must be timezone-aware (PIT contract)")
        cutoff = asof_ts

        resp = self.http.get(
            f"{self.base_url.rstrip('/')}/trades",
            params={"market": market_id, "limit": _TRADES_PAGE_LIMIT},
            timeout=10.0,
        )
        payload = resp.json()
        rows = _rows_from_payload(payload)

        out: list[TradePrice] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _coerce_price(row.get("price"))
            ts = _coerce_ts(row.get("timestamp") or row.get("matchTime") or row.get("ts"))
            if price is None or ts is None:
                continue  # malformed → skip (NEVER fabricate)
            if cutoff is not None and ts > cutoff:
                continue
            out.append(TradePrice(market_id=market_id, price=price, ts=ts))
        out.sort(key=lambda t: t.ts)
        return out

    def entry_price(
        self, market_id: str, *, asof_ts: datetime | None = None
    ) -> TradePrice | None:
        """The earliest REAL trade at/before ``asof_ts`` — the cost-realistic entry.
        ``None`` when the market has NO real trades, so the probe fails closed."""
        trades = self.fetch_trades(market_id, asof_ts=asof_ts)
        return trades[0] if trades else None


def _coerce_price(raw: object) -> float | None:
    """Parse a trade price; reject anything outside (0, 1] (binary-market price)."""
    try:
        px = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (0.0 < px <= 1.0):
        return None
    return px


def _rows_from_payload(payload: object) -> list[Any]:
    """Tolerate both a bare ``[trade, ...]`` list and a ``{"data": [...]}`` envelope."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return []


__all__ = [
    "POLYMARKET_DATA_API_BASE_URL",
    "PROVENANCE_ACTUAL_TRADE",
    "PolymarketTradesClient",
    "TradePrice",
]
