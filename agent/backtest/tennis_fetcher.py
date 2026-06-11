"""Per-match tennis cassette fetcher — real CLOB intraday price history.

This is the sprint_10 follow-up the :mod:`agent.backtest.historical_fetcher`
docstring anticipates ("sprint_10 will replace [the synthetic ledger] with a
real CLOB tick stream"). It exists alongside — and does NOT modify — the
sprint_9 :func:`historical_fetcher.fetch_closed_tennis_markets`, whose
``/markets?tag=tennis`` query is a known dead filter (gamma-api ignores the
tag on the markets route and returns generic markets — verified live
2026-05-26 and again 2026-06-08).

The working discovery surface is the **events** route, exactly as
:mod:`agent.runtime.sprint7_dryrun` documents for the live agent:

    GET https://gamma-api.polymarket.com/events?tag_slug=tennis&closed=true

Each event carries per-event sub-markets; the per-match ones (slug ``...-vs-...``)
are the agent's real domain (single-match winner prediction), so this fetcher
keeps those and drops tournament-winner markets.

Real intraday prices come from the CLOB ``prices-history`` endpoint keyed by the
match window (``startTs``/``endTs`` from the market's ``startDate``/``endDate``)
at a configurable ``fidelity``. ``interval=max`` is deliberately NOT used — it
bins over the market's whole lifetime and returns a near-empty series for the
short per-match window.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    PricePoint,
    _project_market,
    save_cached_market,
)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history"

logger = logging.getLogger(__name__)


def is_per_match_market(market: dict[str, Any]) -> bool:
    """True iff ``market`` is a single-match (A-vs-B) tennis market.

    Per-match slugs are ``<tournament>-<playerA>-vs-<playerB>``; tournament
    winner markets are ``will-<player>-win-the-<tournament>``. The ``-vs-``
    infix is the discriminator.
    """
    slug = str(market.get("slug", "")).lower()
    return "-vs-" in slug


def clob_history_to_ledger(history: list[dict[str, Any]]) -> list[PricePoint]:
    """Convert a CLOB ``prices-history`` stream to a PIT price ledger.

    Each raw entry is ``{"t": <epoch seconds>, "p": <YES prob in [0,1]>}``.
    Returns :class:`PricePoint` rows sorted ascending by timestamp.
    """
    points = [
        PricePoint(
            ts=datetime.fromtimestamp(int(entry["t"]), tz=UTC).isoformat(),
            mid_price=float(entry["p"]),
        )
        for entry in history
    ]
    points.sort(key=lambda p: p.ts)
    return points


def project_tennis_market(
    market: dict[str, Any], history: list[dict[str, Any]]
) -> MarketSnapshot:
    """Project a gamma-api per-match market + its CLOB stream to a cassette.

    Reuses :func:`historical_fetcher._project_market` for the resolution
    projection (outcome / winning_price / liquidity / dates), then swaps the
    synthetic ledger for the real CLOB stream. Constructed fresh (not mutated)
    so :class:`MarketSnapshot`'s monotonic-ledger validation runs on the real
    ledger.
    """
    base = _project_market(market)
    ledger = clob_history_to_ledger(history)
    return MarketSnapshot(
        market_id=base.market_id,
        slug=base.slug,
        end_date_iso=base.end_date_iso,
        resolution_ts_iso=base.resolution_ts_iso,
        outcome=base.outcome,
        winning_price=base.winning_price,
        liquidity_cap_usd=base.liquidity_cap_usd,
        price_ledger=ledger,
    )


class _JsonHttpClient(Protocol):
    """A GET-url-return-decoded-JSON transport (injected; sync)."""

    def get_json(self, url: str) -> Any: ...


def _first_clob_token(raw: Any) -> str | None:
    """gamma-api returns ``clobTokenIds`` as a JSON-encoded string."""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, list) and raw:
        return str(raw[0])
    return None


def _to_epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


# A tennis match (incl. settlement lag) fits comfortably inside 8h. Used as the
# CLOB window end when gamma's ``endDate`` is unusable (set to match-day
# midnight, i.e. before ``startDate`` — observed on US/Australian Open matches).
_MATCH_WINDOW_SECONDS = 8 * 3600


def _clob_window(start_iso: str, end_iso: str) -> tuple[int, int]:
    """Resolve the ``(startTs, endTs)`` CLOB query window for a match.

    gamma's ``endDate`` is normally the real close (after ``startDate``) and is
    used verbatim. When it precedes ``startDate`` (a coarse match-day-midnight
    value) the window is inverted and the CLOB endpoint 400s, so fall back to
    ``startDate + _MATCH_WINDOW_SECONDS``.
    """
    start_ts = _to_epoch(start_iso)
    end_ts = _to_epoch(end_iso)
    if end_ts <= start_ts:
        end_ts = start_ts + _MATCH_WINDOW_SECONDS
    return start_ts, end_ts


def _fetch_events_page(
    client: _JsonHttpClient, *, page_size: int, offset: int
) -> list[dict[str, Any]]:
    """One ``/events`` page. gamma caps ``limit`` at 100, hence pagination."""
    url = (
        f"{GAMMA_EVENTS_URL}?tag_slug=tennis&closed=true&active=false"
        f"&limit={page_size}&offset={offset}"
    )
    payload = client.get_json(url)
    if isinstance(payload, dict):
        page = payload.get("data", [])
    elif isinstance(payload, list):
        page = payload
    else:
        page = []
    return [e for e in page if isinstance(e, dict)]


def _process_market(
    client: _JsonHttpClient,
    market: dict[str, Any],
    *,
    cache_dir: Path,
    fidelity: int,
) -> MarketSnapshot | None:
    """Project + cache one market, or ``None`` if it is skipped.

    Skipped when: not per-match, unresolved, missing token/dates, or the CLOB
    fetch / projection fails (best-effort — one bad market never aborts the
    batch).
    """
    if not is_per_match_market(market):
        return None
    if market.get("umaResolutionStatus") != "resolved":
        return None
    token = _first_clob_token(market.get("clobTokenIds"))
    start = market.get("startDate")
    end = market.get("endDate")
    if not token or not start or not end:
        return None
    start_ts, end_ts = _clob_window(start, end)
    clob_url = (
        f"{CLOB_PRICES_HISTORY_URL}?market={token}"
        f"&startTs={start_ts}&endTs={end_ts}&fidelity={fidelity}"
    )
    try:
        clob_payload = client.get_json(clob_url)
        history = (
            clob_payload.get("history", [])
            if isinstance(clob_payload, dict)
            else (clob_payload or [])
        )
        snapshot = project_tennis_market(market, history)
    except Exception as exc:
        # CLOB 400 on inverted startTs/endTs, missing 'createdAt', malformed
        # payload, rate-limit, … — skip this one market, keep harvesting.
        logger.warning(
            "tennis_fetcher: skipping market %s (%s) — %s",
            market.get("id"),
            market.get("slug"),
            exc,
        )
        return None
    save_cached_market(snapshot=snapshot, cache_dir=cache_dir)
    return snapshot


def fetch_per_match_tennis_cassettes(
    *,
    client: _JsonHttpClient,
    cache_dir: Path,
    event_limit: int | None = None,
    start_offset: int = 0,
    page_size: int = 100,
    fidelity: int = 10,
) -> list[MarketSnapshot]:
    """Fetch resolved per-match tennis markets into backtest cassettes.

    Pages through ``/events?tag_slug=tennis&closed=true`` (gamma caps ``limit``
    at 100 and 422s past its offset ceiling, so a single request only sees the
    first page), keeps the resolved per-match (A-vs-B) sub-markets, pulls each
    market's real CLOB intraday price stream over its match window at
    ``fidelity`` minutes, and writes one cassette per market under
    ``cache_dir``.

    Each page is processed (and its cassettes saved) BEFORE the next page is
    fetched, so a late pagination error (gamma's offset-ceiling 422) never
    discards already-harvested markets. Pagination stops at the first empty
    page, on a page-fetch error, or once ``event_limit`` events have been
    seen (``None`` = all). Returns the snapshots sorted by ``market_id``.
    """
    snapshots: list[MarketSnapshot] = []
    seen_market_ids: set[str] = set()
    n_events = 0
    offset = start_offset
    while True:
        try:
            page = _fetch_events_page(client, page_size=page_size, offset=offset)
        except Exception as exc:
            logger.warning(
                "tennis_fetcher: stopping pagination at offset %d — %s",
                offset,
                exc,
            )
            break
        if not page:
            break
        for event in page:
            for market in event.get("markets") or []:
                market_id = str(market.get("id"))
                if market_id in seen_market_ids:
                    continue
                seen_market_ids.add(market_id)
                snapshot = _process_market(
                    client, market, cache_dir=cache_dir, fidelity=fidelity
                )
                if snapshot is not None:
                    snapshots.append(snapshot)
        n_events += len(page)
        offset += page_size
        if event_limit is not None and n_events >= event_limit:
            break

    snapshots.sort(key=lambda s: s.market_id)
    return snapshots


# --------------------------------------------------------------------------- #
# Live runner — thin requests adapter + CLI (integration boundary)
# --------------------------------------------------------------------------- #


class _RequestsJsonClient:
    """Real :class:`_JsonHttpClient` over ``requests`` (lazy-imported).

    Throttled: sleeps ``delay_seconds`` after every request, and on a 429/5xx
    rate-limit response retries with exponential backoff (2/4/8 s) up to
    ``max_retries`` times so a transient limit skips a wait, not a market.
    """

    _RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        timeout: float = 25.0,
        delay_seconds: float = 0.5,
        max_retries: int = 3,
    ) -> None:
        import time  # local imports keep the module dependency-light

        import requests

        self._requests = requests
        self._time = time
        self._timeout = timeout
        self._delay = delay_seconds
        self._max_retries = max_retries

    def get_json(self, url: str) -> Any:
        for attempt in range(self._max_retries + 1):
            resp = self._requests.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": "genesis-tennis-fetcher"},
            )
            if resp.status_code in self._RETRY_STATUS and attempt < self._max_retries:
                backoff = 2 ** (attempt + 1)
                logger.warning(
                    "tennis_fetcher: HTTP %d (rate limit?) — backoff %ds",
                    resp.status_code,
                    backoff,
                )
                self._time.sleep(backoff)
                continue
            resp.raise_for_status()
            data = resp.json()
            if self._delay > 0:
                self._time.sleep(self._delay)
            return data
        raise RuntimeError("unreachable")  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent.backtest.tennis_fetcher",
        description="Fetch resolved per-match tennis markets into backtest "
        "cassettes (real CLOB intraday price history).",
    )
    parser.add_argument(
        "--cache-dir",
        required=True,
        help="Output dir for the .json cassettes (one per market).",
    )
    parser.add_argument(
        "--event-limit",
        type=int,
        default=None,
        help="Max events to page through this run (default: all available).",
    )
    parser.add_argument(
        "--start-offset",
        type=int,
        default=0,
        help="Events offset to resume from (skip an already-harvested range).",
    )
    parser.add_argument(
        "--fidelity",
        type=int,
        default=10,
        help="CLOB price-history bucket size in minutes (smaller = denser).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to sleep after each request (rate-limit throttle).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cache_dir = Path(args.cache_dir)
    snapshots = fetch_per_match_tennis_cassettes(
        client=_RequestsJsonClient(delay_seconds=args.delay),
        cache_dir=cache_dir,
        event_limit=args.event_limit,
        start_offset=args.start_offset,
        fidelity=args.fidelity,
    )
    n_with_ledger = sum(1 for s in snapshots if s.price_ledger)
    print(
        f"wrote {len(snapshots)} per-match tennis cassettes to {cache_dir} "
        f"({n_with_ledger} with a non-empty CLOB ledger)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
