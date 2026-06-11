"""Tests for the per-match tennis cassette fetcher (real CLOB price history).

The fixture ``fixtures/tennis_real_capture.json`` is a verbatim live capture
(2026-06-08) of:

* ``per_match_market``       — gamma-api ``/events?tag_slug=tennis&closed=true``
  sub-market ``us-open-shelton-vs-tiafoe`` (outcomes ["Shelton","Tiafoe"],
  outcomePrices ["0","1"] → Tiafoe won → ``outcome="no"``).
* ``per_match_clob_history`` — the real CLOB ``prices-history`` stream for that
  market keyed by ``startTs``/``endTs`` (the match window) at ``fidelity=10``
  (75 intraday points).
* ``tournament_market``      — ``will-novak-djokovic-win-the-2024-french-open``
  (a tournament-winner market the per-match filter must reject).

These exercise the pure projection surface. Network orchestration
(:func:`fetch_per_match_tennis_cassettes`) is covered separately with a fake
client.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    PricePoint,
    load_all_cached_markets,
)
from agent.backtest.tennis_fetcher import (
    _clob_window,
    clob_history_to_ledger,
    fetch_per_match_tennis_cassettes,
    is_per_match_market,
    project_tennis_market,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "tennis_real_capture.json").read_text(
        encoding="utf-8"
    )
)
PER_MATCH: dict = _FIXTURE["per_match_market"]
CLOB_HISTORY: list[dict] = _FIXTURE["per_match_clob_history"]
TOURNAMENT: dict = _FIXTURE["tournament_market"]


# --------------------------------------------------------------------------- #
# is_per_match_market
# --------------------------------------------------------------------------- #


def test_is_per_match_market_true_for_vs_slug() -> None:
    assert is_per_match_market(PER_MATCH) is True


def test_is_per_match_market_false_for_tournament_winner() -> None:
    assert is_per_match_market(TOURNAMENT) is False


# --------------------------------------------------------------------------- #
# clob_history_to_ledger
# --------------------------------------------------------------------------- #


def test_clob_history_to_ledger_maps_epoch_and_price() -> None:
    ledger = clob_history_to_ledger(CLOB_HISTORY)

    assert len(ledger) == len(CLOB_HISTORY)
    assert all(isinstance(p, PricePoint) for p in ledger)
    # First raw point is {'t': 1724974202, 'p': 0.5}.
    assert ledger[0].mid_price == CLOB_HISTORY[0]["p"]
    assert ledger[0].ts.startswith("2024-08-29T")  # 1724974202 UTC
    assert ledger[0].ts.endswith("+00:00")


def test_clob_history_to_ledger_is_ascending_by_ts() -> None:
    ledger = clob_history_to_ledger(CLOB_HISTORY)
    assert [p.ts for p in ledger] == sorted(p.ts for p in ledger)


def test_clob_history_to_ledger_empty_returns_empty() -> None:
    assert clob_history_to_ledger([]) == []


# --------------------------------------------------------------------------- #
# project_tennis_market
# --------------------------------------------------------------------------- #


def test_project_tennis_market_carries_real_outcome() -> None:
    snap = project_tennis_market(PER_MATCH, CLOB_HISTORY)

    assert isinstance(snap, MarketSnapshot)
    assert snap.market_id == str(PER_MATCH["id"])
    assert snap.slug == PER_MATCH["slug"]
    # outcomePrices ["0","1"]: yes(=outcomes[0]=Shelton)=0 < no(=Tiafoe)=1.
    assert snap.outcome == "no"
    assert snap.winning_price == 1.0


def test_project_tennis_market_uses_real_clob_ledger() -> None:
    snap = project_tennis_market(PER_MATCH, CLOB_HISTORY)

    # Real CLOB stream, not a 3-point synthetic ramp.
    assert len(snap.price_ledger) == len(CLOB_HISTORY)
    assert snap.price_ledger[0].mid_price == CLOB_HISTORY[0]["p"]


# --------------------------------------------------------------------------- #
# _clob_window — match-window resolution (handles gamma's coarse endDate)
# --------------------------------------------------------------------------- #


def test_clob_window_keeps_valid_forward_window() -> None:
    start, end = _clob_window("2024-08-29T22:10:00Z", "2024-08-30T12:00:00Z")
    assert start < end
    # Unchanged: real endDate is after startDate.
    assert end == 1725019200  # 2024-08-30T12:00:00Z


def test_clob_window_falls_back_when_enddate_precedes_startdate() -> None:
    # gamma sometimes sets endDate to the match-day midnight (before the
    # actual startDate) → inverted window. Fall back to start + a match cap.
    start, end = _clob_window("2024-08-29T22:10:00Z", "2024-08-29T00:00:00Z")
    assert end > start


# --------------------------------------------------------------------------- #
# fetch_per_match_tennis_cassettes — orchestration (fake client, no network)
# --------------------------------------------------------------------------- #


def _offset_of(url: str) -> int:
    match = re.search(r"offset=(\d+)", url)
    return int(match.group(1)) if match else 0


class _FakeJsonClient:
    """Records requested URLs; serves the events page-0 + CLOB history.

    Events are returned ONLY at ``offset=0`` (or no offset); any later page is
    empty, so a paginating fetcher terminates after one event page.
    """

    def __init__(self, events: list[dict], clob_history: list[dict]) -> None:
        self._events = events
        self._clob = clob_history
        self.urls: list[str] = []

    def get_json(self, url: str) -> Any:
        self.urls.append(url)
        if "/events" in url:
            return self._events if _offset_of(url) == 0 else []
        if "/prices-history" in url:
            return {"history": self._clob}
        raise AssertionError(f"unexpected url: {url}")


def test_fetch_keeps_per_match_drops_tournament(tmp_path) -> None:
    event = {"slug": "us-open", "markets": [PER_MATCH, TOURNAMENT]}
    client = _FakeJsonClient([event], CLOB_HISTORY)

    snaps = fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, event_limit=10, fidelity=10
    )

    assert len(snaps) == 1  # tournament-winner market filtered out
    assert snaps[0].slug == PER_MATCH["slug"]
    assert len(snaps[0].price_ledger) == len(CLOB_HISTORY)


def test_fetch_writes_cassette_to_cache_dir(tmp_path) -> None:
    event = {"slug": "us-open", "markets": [PER_MATCH]}
    client = _FakeJsonClient([event], CLOB_HISTORY)

    fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, event_limit=10, fidelity=10
    )

    reloaded = load_all_cached_markets(cache_dir=tmp_path)
    assert [s.market_id for s in reloaded] == [str(PER_MATCH["id"])]
    assert len(reloaded[0].price_ledger) == len(CLOB_HISTORY)


def test_fetch_skips_market_that_fails_to_project(tmp_path) -> None:
    # A per-match market missing 'createdAt' makes _project_market raise;
    # the batch must skip it and still return the healthy market.
    broken = dict(PER_MATCH)
    broken["id"] = 999999
    broken.pop("createdAt", None)
    event = {"slug": "us-open", "markets": [broken, PER_MATCH]}
    client = _FakeJsonClient([event], CLOB_HISTORY)

    snaps = fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, event_limit=10, fidelity=10
    )

    assert [s.market_id for s in snaps] == [str(PER_MATCH["id"])]


class _FlakyClient(_FakeJsonClient):
    """Raises on the CLOB request for a specific token (simulates a 400)."""

    def __init__(
        self, events: list[dict], clob_history: list[dict], fail_token: str
    ) -> None:
        super().__init__(events, clob_history)
        self._fail_token = fail_token

    def get_json(self, url: str) -> Any:
        if "/prices-history" in url and self._fail_token in url:
            self.urls.append(url)
            raise RuntimeError("400 Bad Request (startTs > endTs)")
        return super().get_json(url)


class _PagedJsonClient:
    """Serves a different event page per ``offset`` (simulates pagination)."""

    def __init__(self, pages: dict[int, list[dict]], clob_history: list[dict]) -> None:
        self._pages = pages
        self._clob = clob_history
        self.event_offsets: list[int] = []

    def get_json(self, url: str) -> Any:
        if "/events" in url:
            offset = _offset_of(url)
            self.event_offsets.append(offset)
            return self._pages.get(offset, [])
        if "/prices-history" in url:
            return {"history": self._clob}
        raise AssertionError(f"unexpected url: {url}")


def test_fetch_paginates_events_until_empty_page(tmp_path) -> None:
    page0 = [{"slug": "e0", "markets": [dict(PER_MATCH, id=1)]}]
    page1 = [{"slug": "e1", "markets": [dict(PER_MATCH, id=2)]}]
    client = _PagedJsonClient({0: page0, 100: page1, 200: []}, CLOB_HISTORY)

    snaps = fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, page_size=100, fidelity=10
    )

    # Both pages' markets are harvested; pagination stops at the empty page.
    assert {s.market_id for s in snaps} == {"1", "2"}
    assert client.event_offsets == [0, 100, 200]


class _ErrorOnPageClient(_PagedJsonClient):
    """Raises on the events request for a specific offset (simulates a 422)."""

    def __init__(
        self,
        pages: dict[int, list[dict]],
        clob_history: list[dict],
        error_offset: int,
    ) -> None:
        super().__init__(pages, clob_history)
        self._error_offset = error_offset

    def get_json(self, url: str) -> Any:
        if "/events" in url and _offset_of(url) == self._error_offset:
            self.event_offsets.append(_offset_of(url))
            raise RuntimeError("422 Unprocessable Entity (offset ceiling)")
        return super().get_json(url)


def test_fetch_resumes_from_start_offset(tmp_path) -> None:
    # start_offset lets a later batch continue past an already-harvested range
    # without re-fetching offset 0.
    page = [{"slug": "e", "markets": [dict(PER_MATCH, id=5)]}]
    client = _PagedJsonClient({100: page, 200: []}, CLOB_HISTORY)

    snaps = fetch_per_match_tennis_cassettes(
        client=client,
        cache_dir=tmp_path,
        start_offset=100,
        page_size=100,
        fidelity=10,
    )

    assert client.event_offsets[0] == 100  # first request is the resume point
    assert [s.market_id for s in snaps] == ["5"]


def test_fetch_stops_gracefully_on_pagination_error(tmp_path) -> None:
    # gamma 422s past its offset ceiling. Markets harvested from earlier pages
    # must already be saved — a late pagination error never loses prior work.
    page0 = [{"slug": "e0", "markets": [dict(PER_MATCH, id=1)]}]
    client = _ErrorOnPageClient({0: page0}, CLOB_HISTORY, error_offset=100)

    snaps = fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, page_size=100, fidelity=10
    )

    assert [s.market_id for s in snaps] == ["1"]


def test_fetch_skips_market_when_clob_request_fails(tmp_path) -> None:
    # A market whose CLOB request 400s (e.g. inverted startTs/endTs) must be
    # skipped so one bad market never aborts the whole harvest.
    bad = dict(PER_MATCH)
    bad["id"] = 888
    bad["clobTokenIds"] = '["BADTOKEN"]'
    event = {"slug": "us-open", "markets": [bad, PER_MATCH]}
    client = _FlakyClient([event], CLOB_HISTORY, fail_token="BADTOKEN")

    snaps = fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, event_limit=10, fidelity=10
    )

    assert [s.market_id for s in snaps] == [str(PER_MATCH["id"])]


def test_fetch_queries_clob_with_match_window(tmp_path) -> None:
    event = {"slug": "us-open", "markets": [PER_MATCH]}
    client = _FakeJsonClient([event], CLOB_HISTORY)

    fetch_per_match_tennis_cassettes(
        client=client, cache_dir=tmp_path, event_limit=10, fidelity=10
    )

    clob_calls = [u for u in client.urls if "/prices-history" in u]
    assert len(clob_calls) == 1
    # Window-keyed (startTs/endTs), NOT interval=max.
    assert "startTs=" in clob_calls[0]
    assert "endTs=" in clob_calls[0]
    assert "fidelity=10" in clob_calls[0]
    assert "interval=max" not in clob_calls[0]
