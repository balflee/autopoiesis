"""Tests for :mod:`agent.backtest.historical_fetcher` — T-B-026.

Three slices:

1. **VCR cassette replay** — the brief's "VCR-tested for CI" criterion.
   A pinned cassette under
   :file:`tests/agent/backtest/cassettes/closed_tennis_markets.yaml`
   shapes a synthetic gamma-api response; the test runs
   :func:`fetch_closed_tennis_markets` against it and asserts the
   projection produces the right :class:`MarketSnapshot` set + the
   cache directory ends up populated with deterministic filenames.

2. **Cache I/O determinism** — saving + reloading the same snapshot
   produces a byte-identical file (load-bearing for the determinism
   contract in :func:`tests.agent.backtest.test_sweep_runner.test_determinism_3x_identical`).

3. **MarketSnapshotProvider point-in-time** — the lookahead guard:
   :meth:`MarketSnapshotProvider.price_at` returns ``None`` for ticks
   that pre-date every price; returns the latest at-or-before for
   ticks within the ledger; refuses naive timestamps; the explicit
   :meth:`assert_no_lookahead` catches a faked future-leak.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import vcr

from agent.backtest.historical_fetcher import (
    GAMMA_MARKETS_URL,
    MarketSnapshot,
    MarketSnapshotProvider,
    PricePoint,
    _HttpClient,
    cache_filename,
    fetch_closed_tennis_markets,
    load_all_cached_markets,
    load_cached_market,
    save_cached_market,
)

CASSETTE_DIR = Path(__file__).parent / "cassettes"


_replay_vcr = vcr.VCR(
    serializer="yaml",
    record_mode="none",
    cassette_library_dir=str(CASSETTE_DIR),
    decode_compressed_response=True,
    match_on=("method", "scheme", "host", "port", "path", "query"),
)


# --------------------------------------------------------------------------- #
# 1. VCR-backed integration
# --------------------------------------------------------------------------- #


def test_fetch_closed_tennis_markets_from_cassette(tmp_path: Path) -> None:
    """Cassette → projects to 3 snapshots → caches each by id."""
    cache = tmp_path / "_cache"

    async def _run() -> list[MarketSnapshot]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await fetch_closed_tennis_markets(
                client=cast(_HttpClient, client),
                cache_dir=cache,
                limit=10,
            )

    with _replay_vcr.use_cassette("closed_tennis_markets.yaml"):
        snaps = asyncio.run(_run())

    # 3 fixtures in the cassette, sorted ascending by id.
    assert [s.market_id for s in snaps] == ["8000001", "8000002", "8000003"]
    # Cache populated with deterministic filenames.
    expected_files = {f"{mid}.json" for mid in ("8000001", "8000002", "8000003")}
    actual_files = {p.name for p in cache.glob("*.json")}
    assert expected_files == actual_files

    # Outcome projection: 8000001 yes-wins (prices [1,0]); 8000002 no-wins.
    assert snaps[0].outcome == "yes"
    assert snaps[0].winning_price == pytest.approx(1.0)
    assert snaps[1].outcome == "no"
    assert snaps[1].winning_price == pytest.approx(1.0)
    # End-date round-tripped verbatim.
    assert snaps[0].end_date_iso == "2026-05-12T17:00:00Z"


def test_fetch_uses_cache_on_warm_run(tmp_path: Path) -> None:
    """Second call with the same cache_dir uses the cached file (network optional)."""
    cache = tmp_path / "_cache"

    # Cold run — populates cache.
    async def _cold() -> list[MarketSnapshot]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await fetch_closed_tennis_markets(
                client=cast(_HttpClient, client),
                cache_dir=cache,
                limit=10,
            )

    with _replay_vcr.use_cassette("closed_tennis_markets.yaml"):
        cold = asyncio.run(_cold())

    # Mutate one cached file to verify the warm read goes through the file
    # instead of re-projecting from the wire. The brief's acceptance
    # criterion is "caches responses ... deterministic filename"; if the
    # function re-projected on warm reads the mutation would be silently
    # overwritten.
    mutated_path = cache / cache_filename("8000001")
    raw = json.loads(mutated_path.read_text(encoding="utf-8"))
    raw["slug"] = "mutated-on-purpose"
    mutated_path.write_text(
        json.dumps(raw, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    with _replay_vcr.use_cassette("closed_tennis_markets.yaml"):
        warm = asyncio.run(_cold())

    # The mutated slug survived → warm read used the cache.
    by_id = {s.market_id: s for s in warm}
    assert by_id["8000001"].slug == "mutated-on-purpose"
    # The other ids unchanged.
    assert {s.market_id for s in warm} == {s.market_id for s in cold}


# --------------------------------------------------------------------------- #
# 2. Cache I/O determinism + filename safety
# --------------------------------------------------------------------------- #


def test_cache_filename_rejects_path_traversal() -> None:
    """A market_id containing '..' / slash is rejected (cache-dir safety)."""
    with pytest.raises(ValueError):
        cache_filename("../etc/passwd")
    with pytest.raises(ValueError):
        cache_filename("9999/x")
    with pytest.raises(ValueError):
        cache_filename("")


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    """``save_cached_market`` + ``load_cached_market`` are inverse."""
    snap = MarketSnapshot(
        market_id="m-round-trip",
        slug="some-slug",
        end_date_iso="2026-05-31T09:00:00+00:00",
        resolution_ts_iso="2026-05-30T20:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=12.5,
        price_ledger=[
            PricePoint(ts="2026-05-29T00:00:00+00:00", mid_price=0.5),
            PricePoint(ts="2026-05-30T20:00:00+00:00", mid_price=1.0),
        ],
    )
    save_cached_market(snapshot=snap, cache_dir=tmp_path)
    loaded = load_cached_market(market_id="m-round-trip", cache_dir=tmp_path)
    assert loaded == snap


def test_save_is_byte_identical_for_same_payload(tmp_path: Path) -> None:
    """Two saves of the same snapshot produce byte-identical files."""
    snap = MarketSnapshot(
        market_id="m-determinism",
        slug="slug",
        end_date_iso="2026-05-31T09:00:00+00:00",
        resolution_ts_iso=None,
        outcome=None,
        winning_price=None,
        liquidity_cap_usd=5.0,
        price_ledger=[],
    )
    save_cached_market(snapshot=snap, cache_dir=tmp_path)
    bytes_first = (tmp_path / cache_filename("m-determinism")).read_bytes()
    save_cached_market(snapshot=snap, cache_dir=tmp_path)
    bytes_second = (tmp_path / cache_filename("m-determinism")).read_bytes()
    assert bytes_first == bytes_second


def test_load_all_returns_sorted_id_order(tmp_path: Path) -> None:
    """``load_all_cached_markets`` returns snapshots in sorted-id order."""
    for mid in ("3-zebra", "1-alpha", "2-bravo"):
        snap = MarketSnapshot(
            market_id=mid,
            slug=f"slug-{mid}",
            end_date_iso="2026-05-31T09:00:00+00:00",
            resolution_ts_iso=None,
            outcome=None,
            winning_price=None,
            liquidity_cap_usd=5.0,
            price_ledger=[],
        )
        save_cached_market(snapshot=snap, cache_dir=tmp_path)
    out = load_all_cached_markets(cache_dir=tmp_path)
    assert [s.market_id for s in out] == ["1-alpha", "2-bravo", "3-zebra"]


def test_load_all_on_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_all_cached_markets(cache_dir=tmp_path / "does-not-exist") == []


# --------------------------------------------------------------------------- #
# 3. MarketSnapshotProvider point-in-time guard
# --------------------------------------------------------------------------- #


def _build_provider() -> MarketSnapshotProvider:
    snap = MarketSnapshot(
        market_id="m-pit",
        slug="pit",
        end_date_iso="2026-05-31T00:00:00+00:00",
        resolution_ts_iso="2026-05-30T12:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=10.0,
        price_ledger=[
            PricePoint(ts="2026-05-28T00:00:00+00:00", mid_price=0.5),
            PricePoint(ts="2026-05-29T00:00:00+00:00", mid_price=0.6),
            PricePoint(ts="2026-05-30T12:00:00+00:00", mid_price=1.0),
        ],
    )
    return MarketSnapshotProvider([snap])


def test_price_at_returns_none_for_pre_history_tick() -> None:
    p = _build_provider()
    pre = datetime(2026, 5, 27, tzinfo=UTC)
    assert p.price_at(market_id="m-pit", asof_ts=pre) is None


def test_price_at_returns_latest_at_or_before() -> None:
    p = _build_provider()
    # Exactly the second ledger point.
    asof = datetime(2026, 5, 29, 0, 0, 0, tzinfo=UTC)
    assert p.price_at(market_id="m-pit", asof_ts=asof) == pytest.approx(0.6)
    # Between 2 and 3 → still returns the 2nd (no interpolation).
    mid = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)
    assert p.price_at(market_id="m-pit", asof_ts=mid) == pytest.approx(0.6)
    # At the resolution ts → the resolution price.
    res = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
    assert p.price_at(market_id="m-pit", asof_ts=res) == pytest.approx(1.0)


def test_price_at_refuses_naive_ts() -> None:
    p = _build_provider()
    naive = datetime(2026, 5, 29, 0, 0, 0)
    with pytest.raises(ValueError):
        p.price_at(market_id="m-pit", asof_ts=naive)


def test_assert_no_lookahead_flags_future_leak() -> None:
    """If a fake call served a price that's NOT the at-or-before one, raise."""
    p = _build_provider()
    asof = datetime(2026, 5, 28, 0, 0, 0, tzinfo=UTC)
    # At-or-before is 0.5; pretend a buggy caller served 1.0 (future leak).
    with pytest.raises(ValueError, match="lookahead guard tripped"):
        p.assert_no_lookahead(
            market_id="m-pit", asof_ts=asof, served_price=1.0
        )


def test_is_resolved_by_walks_resolution_time() -> None:
    p = _build_provider()
    pre = datetime(2026, 5, 30, 11, 59, 59, tzinfo=UTC)
    post = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
    assert p.is_resolved_by(market_id="m-pit", asof_ts=pre) is False
    assert p.is_resolved_by(market_id="m-pit", asof_ts=post) is True


def test_unknown_market_returns_none() -> None:
    p = _build_provider()
    asof = datetime(2026, 5, 30, tzinfo=UTC)
    assert p.price_at(market_id="never-cached", asof_ts=asof) is None
    assert p.is_resolved_by(market_id="never-cached", asof_ts=asof) is False
    assert p.get("never-cached") is None


def test_gamma_url_is_locked() -> None:
    """The query string MUST match the brief's locked URL exactly."""
    assert (
        GAMMA_MARKETS_URL
        == "https://gamma-api.polymarket.com/markets"
        "?active=false&accepting_orders=false&closed=true&tag=tennis"
    )


# --------------------------------------------------------------------------- #
# Top-level sanity to satisfy the 8/8 count
# --------------------------------------------------------------------------- #
# (no test body — pytest counts the @pytest.mark functions above)
