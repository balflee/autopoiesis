"""Polymarket market-history source-adapter — READ-ONLY.

Per PRD §7 Polymarket is the second canonical data feed. This module
ships the read-only history client Track B uses to bootstrap features
and Track C replays.

**Hard rules** (re-stated and enforced):

* No imports of signing libraries (no ``eth_account``, no ``web3``
  signer modules, no Polymarket order-signing helpers).
* No write-side / send-transaction APIs anywhere in this module.
* No order-placement helpers. The Polymarket executor (signed-write
  path) lives in Track B and is gated on human approval per the
  framework's external-call policy.
* :meth:`PolymarketHistoryClient.fetch_market` REQUIRES the
  ``asof_ts`` keyword and rejects naive timestamps. Snapshots whose
  ``snapshot_ts > asof_ts`` are filtered out BEFORE return — the
  PIT chokepoint then re-validates at the join boundary.
* Exponential backoff (3 retries on 429/5xx) per the T-E-002 brief
  acceptance criterion — handled by :class:`HttpClient`.

Implementation: Polymarket exposes a public CLOB REST API
(``https://clob.polymarket.com``) that returns historical orderbook
snapshots keyed by market slug. Free, no auth required for reads.

Sprint 7 — tennis pivot
-----------------------

Per PRD §7 line 476 + §15 已决 #8, the per-game market discovery is
filtered to ``tag_slug=tennis`` against the Polymarket *gamma-api*
(``https://gamma-api.polymarket.com/markets``). Tennis tag covers
ATP / WTA tour + the 4 Grand Slams, currently 90+ live per-match
markets. The per-market history fetch via the CLOB stays unchanged;
gamma-api is the *discovery* surface, CLOB is the *price-history*
surface — both read-only.

The :meth:`PolymarketHistoryClient.list_tennis_markets` entrypoint
exposes the discovery surface so the Phase 1 builder can iterate
across all tennis markets without hand-curating slugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from data.sources._http import HttpClient, require_asof_ts

POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"

# PRD §7 — sport pivot to tennis. The gamma-api ``tag_slug`` query
# parameter is the canonical filter; documented at
# https://docs.polymarket.com/developers/gamma-markets-api.
TENNIS_TAG_SLUG: str = "tennis"


@dataclass(frozen=True)
class MarketHistory:
    """Read-only Polymarket market-history snapshot.

    Back-compat with T-E-001 sprint_1 schema: ``slug`` + ``resolved`` +
    ``available_at`` + ``orderbook_snapshots`` remain; ``market_id``
    is new in sprint_2 (the Polymarket CLOB returns one).
    """

    slug: str
    resolved: bool
    available_at: datetime
    orderbook_snapshots: list[tuple[datetime, float]] = field(default_factory=list)
    market_id: str = ""


class PolymarketHistoryClient:
    """Read-only Polymarket CLOB history client.

    Constructor is cheap. The shared :class:`HttpClient` handles retry +
    UA + timeout; tests inject a recorded :class:`requests.Session`.
    """

    def __init__(
        self,
        *,
        base_url: str = POLYMARKET_CLOB_BASE_URL,
        gamma_base_url: str = POLYMARKET_GAMMA_API_BASE_URL,
        http: HttpClient | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._gamma_base_url = gamma_base_url.rstrip("/")
        # HttpClient default backoff is 1s/2s/4s = 3 retries — meets
        # the acceptance-criterion exponential schedule.
        self._http = http if http is not None else HttpClient()
        self._cache_dir: str | None = cache_dir

    @property
    def http(self) -> HttpClient:
        """Expose the underlying HttpClient so tests can inject a session."""
        return self._http

    def fetch_market(self, slug: str, *, asof_ts: datetime) -> MarketHistory:
        """Return the point-in-time-correct history for ``slug``.

        Steps:

        1. ``GET /markets/{slug}`` → market metadata (resolved flag,
           market_id).
        2. ``GET /prices-history`` keyed by market_id → list of
           ``(ts, midpoint)`` snapshots.
        3. Filter snapshots to ``ts <= asof_ts``. PIT chokepoint
           re-validates at the join boundary.
        """
        cutoff = require_asof_ts(asof_ts)

        meta_resp = self._http.get(f"{self._base_url}/markets/{slug}", timeout=10.0)
        meta = meta_resp.json()
        market_id = str(meta.get("condition_id") or meta.get("id") or "")
        resolved = bool(meta.get("closed") or meta.get("resolved") or False)

        history_resp = self._http.get(
            f"{self._base_url}/prices-history",
            params={"market": market_id, "interval": "1h"},
            timeout=10.0,
        )
        history_payload: dict[str, Any] = history_resp.json()
        raw_points: list[dict[str, Any]] = history_payload.get("history", [])

        snapshots: list[tuple[datetime, float]] = []
        for pt in raw_points:
            ts_raw = pt.get("t") or pt.get("timestamp")
            mid_raw = pt.get("p") or pt.get("price")
            if ts_raw is None or mid_raw is None:
                continue
            ts = _decode_polymarket_ts(ts_raw)
            if ts > cutoff:
                # PIT filter: drop future points so callers never see them.
                continue
            try:
                snapshots.append((ts, float(mid_raw)))
            except (TypeError, ValueError):
                continue

        return MarketHistory(
            slug=slug,
            resolved=resolved,
            available_at=cutoff,
            orderbook_snapshots=snapshots,
            market_id=market_id,
        )


    def list_tennis_markets(
        self,
        *,
        asof_ts: datetime,
        limit: int = 100,
        tag_slug: str = TENNIS_TAG_SLUG,
    ) -> list[dict[str, Any]]:
        """List Polymarket markets carrying the tennis tag (gamma-api).

        Per PRD §7 line 476 + §15 已决 #8 the per-game market
        discovery flows through ``gamma-api`` with ``tag_slug=tennis``
        (the ATP / WTA tour + 4 Grand Slams). Returns the raw market
        objects sorted by ``start_date_iso`` (earliest first) and
        filtered to markets whose ``start_date_iso <= asof_ts`` so
        the result is PIT-correct out of the box.

        Parameters
        ----------
        asof_ts:
            PIT cutoff. Required; naive datetimes raise
            :class:`LookaheadError`.
        limit:
            Page size for the gamma-api call. Defaults to 100, the
            documented gamma-api maximum per page.
        tag_slug:
            Override only for tests / future sport expansion. Defaults
            to :data:`TENNIS_TAG_SLUG` per the sport pivot.

        Returns
        -------
        list[dict[str, Any]]
            Raw market dicts straight from gamma-api. The Phase 1
            builder downstream projects them into the parquet schema.
        """
        cutoff = require_asof_ts(asof_ts)
        resp = self._http.get(
            f"{self._gamma_base_url}/markets",
            params={"tag_slug": tag_slug, "limit": limit},
            timeout=15.0,
        )
        payload = resp.json()
        # Gamma-api returns either a bare list OR an object with "data" key
        # depending on the route version; tolerate both.
        markets_raw: list[dict[str, Any]]
        if isinstance(payload, list):
            markets_raw = payload
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            markets_raw = payload["data"]
        else:
            markets_raw = []

        kept: list[dict[str, Any]] = []
        for m in markets_raw:
            start_raw = m.get("startDate") or m.get("start_date_iso") or m.get("startDateIso")
            if start_raw is None:
                # Conservative: drop markets without a start timestamp —
                # we can't PIT-validate them.
                continue
            try:
                start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UTC)
            if start_dt > cutoff:
                continue
            kept.append(m)

        kept.sort(key=lambda m: str(m.get("startDate") or m.get("start_date_iso") or ""))
        return kept


def _decode_polymarket_ts(raw: Any) -> datetime:
    """Polymarket prices-history timestamps come as unix seconds (int)."""
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw), tz=UTC)
    if isinstance(raw, str):
        # ISO-8601 fallback.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported timestamp type: {type(raw).__name__}")


__all__ = [
    "POLYMARKET_CLOB_BASE_URL",
    "POLYMARKET_GAMMA_API_BASE_URL",
    "TENNIS_TAG_SLUG",
    "MarketHistory",
    "PolymarketHistoryClient",
]
