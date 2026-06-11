"""Tests for the four Track E source-adapter fetchers.

Hermetic by design: every test wires a :class:`FakeSession` (built
from JSON fixtures under ``tests/data/fixtures/``) into the client's
``HttpClient`` via ``set_session``. No real upstream is contacted —
the ``live`` marker is the gate for that, and CI skips it.

``FakeSession`` and ``_FakeResponse`` live in ``conftest.py`` and are
exposed to tests via the ``fake_session_cls`` / ``fake_response_cls``
fixtures — that way we avoid the cross-package import that would
collide with the top-level ``data/`` package under
``pytest --import-mode=importlib``.

Coverage:

* ``test_*_requires_asof_ts_kwarg`` — every fetcher refuses to run
  without ``asof_ts`` (the cross-track PIT chokepoint, per PRD §14.1).
* ``test_*_naive_asof_raises`` — naive datetimes are themselves leaks.
* ``test_*_happy_path`` — replay a recorded payload through the
  decoder and assert shape.
* ``test_*_filters_future_rows`` — entries past ``asof_ts`` must NOT
  surface to the caller.
* ``test_polymarket_backoff_retries_on_429`` — exponential-retry
  schedule (≥3 retries on 429/5xx) per the T-E-002 acceptance
  criterion.
* ``test_polygon_*`` — block-time + confirmation-depth PIT semantics,
  + the source-grep read-only invariant.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

from data.etl.pit_correct import LookaheadError
from data.sources._http import HttpClient
from data.sources.nba import BALLDONTLIE_BASE_URL, NBAClient
from data.sources.polygon import (
    DEFAULT_CONFIRMATION_DEPTH,
    PolygonChainClient,
    READ_ONLY_RPC_METHODS,
)
from data.sources.polymarket import PolymarketHistoryClient
from data.sources.reddit import RedditSentimentClient

ASOF = datetime(2026, 4, 12, 19, 30, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# NBA
# ----------------------------------------------------------------------


def test_nba_fetcher_requires_asof_ts_kwarg() -> None:
    """``asof_ts`` must be keyword-only — the signature is the first guard."""
    sig = inspect.signature(NBAClient.fetch_game)
    asof_param = sig.parameters["asof_ts"]
    assert asof_param.kind == inspect.Parameter.KEYWORD_ONLY


def test_nba_fetcher_rejects_missing_asof_ts() -> None:
    with pytest.raises(LookaheadError, match="required"):
        NBAClient().fetch_game("15908525", asof_ts=None)  # type: ignore[arg-type]


def test_nba_fetcher_rejects_naive_asof_ts() -> None:
    naive = datetime(2026, 4, 12, 19, 30)
    with pytest.raises(LookaheadError, match="timezone-aware"):
        NBAClient().fetch_game("15908525", asof_ts=naive)


def test_nba_fetcher_happy_path(
    balldontlie_payload: dict[str, Any], fake_session_cls: type[Any]
) -> None:
    client = NBAClient()
    fake = fake_session_cls(routes={"/v1/games/15908525": (200, balldontlie_payload)})
    client.http.set_session(fake)

    game = client.fetch_game("15908525", asof_ts=ASOF)

    assert game.game_id == "15908525"
    assert game.home_team == "LAL"
    assert game.away_team == "BOS"
    assert game.home_score == 112
    assert game.away_score == 108
    assert game.status == "Final"
    assert game.available_at <= ASOF
    # Confirm the request hit the expected URL.
    assert fake.calls[0][0] == f"{BALLDONTLIE_BASE_URL}/games/15908525"


def test_nba_available_at_capped_at_asof(
    balldontlie_payload: dict[str, Any], fake_session_cls: type[Any]
) -> None:
    """The fetcher CAPs available_at at asof_ts so PIT holds by construction."""
    early_asof = datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc)
    client = NBAClient()
    client.http.set_session(
        fake_session_cls(routes={"/v1/games/15908525": (200, balldontlie_payload)})
    )

    game = client.fetch_game("15908525", asof_ts=early_asof)
    assert game.available_at <= early_asof


# ----------------------------------------------------------------------
# Polymarket
# ----------------------------------------------------------------------


def test_polymarket_fetcher_requires_asof_ts_kwarg() -> None:
    sig = inspect.signature(PolymarketHistoryClient.fetch_market)
    assert sig.parameters["asof_ts"].kind == inspect.Parameter.KEYWORD_ONLY


def test_polymarket_fetcher_rejects_missing_asof_ts() -> None:
    with pytest.raises(LookaheadError):
        PolymarketHistoryClient().fetch_market(
            "nba-lakers-vs-celtics-2026-04-12",
            asof_ts=None,  # type: ignore[arg-type]
        )


def test_polymarket_fetcher_happy_path(
    polymarket_meta_payload: dict[str, Any],
    polymarket_history_payload: dict[str, Any],
    fake_session_cls: type[Any],
) -> None:
    client = PolymarketHistoryClient()
    client.http.set_session(
        fake_session_cls(
            routes={
                "/markets/nba-lakers-vs-celtics-2026-04-12": (200, polymarket_meta_payload),
                "/prices-history": (200, polymarket_history_payload),
            }
        )
    )

    hist = client.fetch_market("nba-lakers-vs-celtics-2026-04-12", asof_ts=ASOF)

    assert hist.slug == "nba-lakers-vs-celtics-2026-04-12"
    assert hist.resolved is True
    assert hist.market_id.startswith("0x")
    # All returned snapshots must be ≤ asof_ts.
    assert all(ts <= ASOF for (ts, _) in hist.orderbook_snapshots)


def test_polymarket_filters_future_snapshots(
    polymarket_meta_payload: dict[str, Any],
    polymarket_history_payload: dict[str, Any],
    fake_session_cls: type[Any],
) -> None:
    """Snapshots whose timestamp > asof_ts must be dropped from the result."""
    fixture_points = polymarket_history_payload["history"]
    last_ts = max(p["t"] for p in fixture_points)
    cutoff = datetime.fromtimestamp(last_ts - 7200, tz=timezone.utc)

    client = PolymarketHistoryClient()
    client.http.set_session(
        fake_session_cls(
            routes={
                "/markets/nba-lakers-vs-celtics-2026-04-12": (200, polymarket_meta_payload),
                "/prices-history": (200, polymarket_history_payload),
            }
        )
    )

    hist = client.fetch_market("nba-lakers-vs-celtics-2026-04-12", asof_ts=cutoff)
    assert len(hist.orderbook_snapshots) < len(fixture_points)
    assert all(ts <= cutoff for (ts, _) in hist.orderbook_snapshots)


def test_polymarket_backoff_retries_on_429(
    polymarket_meta_payload: dict[str, Any],
    fake_session_cls: type[Any],
    fake_response_cls: type[Any],
) -> None:
    """≥3-retry exponential schedule on 429 (acceptance criterion)."""
    call_count = {"meta": 0}

    def factory(url: str, params: dict[str, Any] | None) -> Any:
        if "/markets/" in url:
            call_count["meta"] += 1
            if call_count["meta"] <= 3:
                return fake_response_cls(
                    status_code=429, payload={"error": "rate_limited"}
                )
            return fake_response_cls(status_code=200, payload=polymarket_meta_payload)
        if "/prices-history" in url:
            return fake_response_cls(status_code=200, payload={"history": []})
        raise AssertionError(f"unexpected url {url}")

    sleeps: list[float] = []
    http = HttpClient(sleep=sleeps.append)
    http.set_session(fake_session_cls(factory=factory))

    client = PolymarketHistoryClient(http=http)
    hist = client.fetch_market("nba-lakers-vs-celtics-2026-04-12", asof_ts=ASOF)

    # 4 total attempts on /markets/ (1 initial + 3 retries) per the acceptance criterion.
    assert call_count["meta"] == 4
    assert sleeps[:3] == [1.0, 2.0, 4.0]
    assert hist.slug == "nba-lakers-vs-celtics-2026-04-12"


def test_polymarket_backoff_exhausted_raises(
    fake_session_cls: type[Any], fake_response_cls: type[Any]
) -> None:
    """If 429 persists across all attempts, the final raise propagates."""

    def factory(url: str, params: dict[str, Any] | None) -> Any:
        return fake_response_cls(status_code=429, payload={"error": "rate_limited"})

    http = HttpClient(sleep=lambda _s: None)
    http.set_session(fake_session_cls(factory=factory))

    client = PolymarketHistoryClient(http=http)
    with pytest.raises(requests.HTTPError):
        client.fetch_market("nba-lakers-vs-celtics-2026-04-12", asof_ts=ASOF)


# ----------------------------------------------------------------------
# Polygon — READ-ONLY by manifest
# ----------------------------------------------------------------------


def test_polygon_fetcher_requires_asof_ts_kwarg() -> None:
    sig = inspect.signature(PolygonChainClient.fetch_events)
    assert sig.parameters["asof_ts"].kind == inspect.Parameter.KEYWORD_ONLY


def test_polygon_fetcher_rejects_missing_asof_ts() -> None:
    with pytest.raises(LookaheadError):
        PolygonChainClient().fetch_events(
            "0x0000000000000000000000000000000000000000",
            from_block=1,
            to_block=2,
            asof_ts=None,  # type: ignore[arg-type]
        )


def test_polygon_source_is_read_only_by_grep() -> None:
    """Static guarantee: the polygon.py source must not import or invoke
    any signer / write-side primitive. This is the cross-chain auditor's
    grep encoded as a test so a future refactor cannot quietly bypass it.
    """
    src = Path("data/sources/polygon.py").read_text(encoding="utf-8")
    body = re.sub(r'"""[\s\S]*?"""', "", src)
    body = re.sub(r"^\s*#.*$", "", body, flags=re.MULTILINE)

    forbidden_patterns = [
        r"\beth_account\b",
        r"\bLocalAccount\b",
        r"\bsend_transaction\b",
        r"\beth_sendTransaction\b",
        r"\beth_sendRawTransaction\b",
        r"\beth_sign\b",
        r"\bpersonal_sign\b",
        r"\bprivate_key\b",
    ]
    for pat in forbidden_patterns:
        assert not re.search(
            pat, body
        ), f"polygon.py code body contains forbidden write-side pattern: {pat}"


def test_polygon_method_allowlist_constant_is_read_only_only() -> None:
    """``READ_ONLY_RPC_METHODS`` must contain only known-read RPC methods."""
    write_methods = {
        "eth_sendTransaction",
        "eth_sendRawTransaction",
        "eth_sign",
        "personal_sign",
    }
    assert READ_ONLY_RPC_METHODS.isdisjoint(write_methods)


def test_polygon_default_confirmation_depth_is_reorg_safe() -> None:
    """12 blocks ≈ 26s on Polygon — enough to defeat single-block reorgs."""
    assert DEFAULT_CONFIRMATION_DEPTH >= 6


def test_polygon_fetcher_returns_events_in_range() -> None:
    """Wire a fake Web3 + iterable get_logs and assert decode + PIT filter."""

    class _FakeEth:
        def __init__(self, logs: list[dict[str, Any]]) -> None:
            self._logs = logs
            self.default_account = None

        def get_logs(self, params: dict[str, Any]) -> list[dict[str, Any]]:
            assert params["fromBlock"] == 50_000_000
            assert params["toBlock"] == 50_000_100
            return self._logs

    class _FakeW3:
        def __init__(self, logs: list[dict[str, Any]]) -> None:
            self.eth = _FakeEth(logs)
            self.middleware_onion = None

    confirmed_block = datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc)
    future_block = datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc)

    logs = [
        {
            "blockNumber": 50_000_010,
            "blockTime": confirmed_block,
            "transactionHash": "0x" + "a" * 64,
            "logIndex": 0,
            "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
            "topics": ["0x" + "b" * 64],
        },
        {
            "blockNumber": 50_000_050,
            "blockTime": future_block,  # past ASOF → filtered out
            "transactionHash": "0x" + "c" * 64,
            "logIndex": 1,
            "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
            "topics": ["0x" + "d" * 64],
        },
    ]

    client = PolygonChainClient(w3=_FakeW3(logs))  # type: ignore[arg-type]
    events = client.fetch_events(
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        from_block=50_000_000,
        to_block=50_000_100,
        asof_ts=ASOF,
    )
    assert len(events) == 1
    assert events[0].block_number == 50_000_010
    assert events[0].topic0.startswith("0x")


def test_polygon_assert_read_only_rejects_signing_middleware() -> None:
    """Defence-in-depth: signing middleware on the wired Web3 = refuse."""

    class _FakeMiddlewareEntry:
        __name__ = "signing_middleware"

    class _FakeOnion:
        middlewares = [_FakeMiddlewareEntry()]

    class _FakeEth:
        default_account = None

    class _FakeW3:
        eth = _FakeEth()
        middleware_onion = _FakeOnion()

    client = PolygonChainClient(w3=_FakeW3())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="signing-related middleware"):
        client.fetch_events(
            "0x0000000000000000000000000000000000000000",
            from_block=0,
            to_block=1,
            asof_ts=ASOF,
        )


# ----------------------------------------------------------------------
# Reddit
# ----------------------------------------------------------------------


def test_reddit_fetcher_requires_asof_ts_kwarg() -> None:
    sig = inspect.signature(RedditSentimentClient.fetch_subreddit)
    assert sig.parameters["asof_ts"].kind == inspect.Parameter.KEYWORD_ONLY


def test_reddit_fetcher_rejects_missing_asof_ts() -> None:
    since = datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(LookaheadError):
        RedditSentimentClient().fetch_subreddit(
            "nba", since, asof_ts=None  # type: ignore[arg-type]
        )


def test_reddit_fetcher_happy_path_and_pit_filter(
    reddit_payload: dict[str, Any], fake_session_cls: type[Any]
) -> None:
    since = datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc)

    client = RedditSentimentClient()
    client.http.set_session(
        fake_session_cls(routes={"/r/nba/new.json": (200, reddit_payload)})
    )

    snap = client.fetch_subreddit("nba", since, asof_ts=ASOF)

    # Fixture has 4 posts; 1 is past ASOF and must be filtered out.
    assert snap.post_count == 3
    assert snap.available_at == ASOF
    assert snap.until == ASOF
    # Token "lakers" appears in 2 in-window post titles.
    assert snap.mention_counts.get("lakers", 0) >= 2
    assert snap.comment_count >= 245


def test_reddit_since_after_asof_raises() -> None:
    """``since > asof_ts`` is a programming error — window is empty by construction."""
    client = RedditSentimentClient()
    later = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="window is empty"):
        client.fetch_subreddit("nba", later, asof_ts=ASOF)


# ----------------------------------------------------------------------
# Cross-track build_training_set smoke (acceptance: backtest_validity)
# ----------------------------------------------------------------------


def test_build_training_set_pit_roundtrip(
    balldontlie_payload: dict[str, Any],
    polymarket_meta_payload: dict[str, Any],
    polymarket_history_payload: dict[str, Any],
    reddit_payload: dict[str, Any],
    fake_session_cls: type[Any],
    tmp_path: Path,
) -> None:
    """E2E smoke: 4 fetchers → one bundle → parquet → re-validate PIT."""
    from data.etl.build_training_set import Clients, build_training_set

    nba_client = NBAClient()
    nba_client.http.set_session(
        fake_session_cls(routes={"/v1/games/15908525": (200, balldontlie_payload)})
    )

    pm_client = PolymarketHistoryClient()
    pm_client.http.set_session(
        fake_session_cls(
            routes={
                "/markets/nba-lakers-vs-celtics-2026-04-12": (200, polymarket_meta_payload),
                "/prices-history": (200, polymarket_history_payload),
            }
        )
    )

    class _FakeEth:
        default_account = None

        def get_logs(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "blockNumber": 50_000_010,
                    "blockTime": datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc),
                    "transactionHash": "0x" + "a" * 64,
                    "logIndex": 0,
                    "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
                    "topics": ["0x" + "b" * 64],
                }
            ]

    class _FakeW3:
        eth = _FakeEth()
        middleware_onion = None

    polygon_client = PolygonChainClient(w3=_FakeW3())  # type: ignore[arg-type]

    rd_client = RedditSentimentClient()
    rd_client.http.set_session(
        fake_session_cls(routes={"/r/nba/new.json": (200, reddit_payload)})
    )

    clients = Clients(
        nba=nba_client, polymarket=pm_client, polygon=polygon_client, reddit=rd_client
    )

    out_path = tmp_path / "training.parquet"
    bundle = build_training_set(
        game_id="15908525",
        market_slug="nba-lakers-vs-celtics-2026-04-12",
        subreddit="nba",
        polygon_contract="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        from_block=50_000_000,
        to_block=50_000_100,
        since=datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc),
        asof_ts=ASOF,
        output_path=out_path,
        clients=clients,
    )

    assert bundle.total_rows() >= 4
    assert out_path.exists()

    # Re-read parquet + assert PIT on the rejoined frame.
    import pandas as pd

    from data.etl.pit_correct import assert_no_lookahead

    df = pd.read_parquet(out_path)
    assert_no_lookahead(df, ASOF)


# ----------------------------------------------------------------------
# Live API marker — skipped unless RUN_LIVE_DATA_TESTS=1.
# ----------------------------------------------------------------------


@pytest.mark.live
def test_live_balldontlie_smoke() -> None:  # pragma: no cover - live only
    import os

    if os.environ.get("RUN_LIVE_DATA_TESTS") != "1":
        pytest.skip("set RUN_LIVE_DATA_TESTS=1 to run live data tests")
    game = NBAClient().fetch_game("15908525", asof_ts=ASOF)
    assert game.game_id


@pytest.mark.live
def test_live_polymarket_smoke() -> None:  # pragma: no cover - live only
    import os

    if os.environ.get("RUN_LIVE_DATA_TESTS") != "1":
        pytest.skip("set RUN_LIVE_DATA_TESTS=1 to run live data tests")
    PolymarketHistoryClient().fetch_market(
        "will-the-lakers-make-the-2026-playoffs", asof_ts=ASOF
    )


@pytest.mark.live
def test_live_polygon_smoke() -> None:  # pragma: no cover - live only
    import os

    if os.environ.get("RUN_LIVE_DATA_TESTS") != "1":
        pytest.skip("set RUN_LIVE_DATA_TESTS=1 to run live data tests")
    PolygonChainClient().fetch_events(
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        from_block=50_000_000,
        to_block=50_000_001,
        asof_ts=ASOF,
    )


@pytest.mark.live
def test_live_reddit_smoke() -> None:  # pragma: no cover - live only
    import os

    if os.environ.get("RUN_LIVE_DATA_TESTS") != "1":
        pytest.skip("set RUN_LIVE_DATA_TESTS=1 to run live data tests")
    since = datetime(2026, 4, 12, 0, 0, tzinfo=timezone.utc)
    RedditSentimentClient().fetch_subreddit("nba", since, asof_ts=ASOF)
