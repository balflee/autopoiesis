"""Cross-sprint smoke tests for the ``data/`` package skeleton.

T-E-001 (sprint_1) shipped the package layout + four ``fetch_*`` stubs
that raised ``NotImplementedError('sprint_2')``. T-E-002 (sprint_2)
lights up the real fetchers, so the original "raises NotImplementedError"
assertions naturally retire.

What stays from the T-E-001 smoke contract:

* Every source client + ETL helper module imports without side effects
  (no network at import, no env reads, no module-scope I/O).
* Every constructor is cheap and does not touch the network.
* The dataclass return-types still hold the cross-track PIT fields
  Track B and Track C lock against.
* :class:`LookaheadBiasError` remains a :class:`ValueError` subclass
  for back-compat with Track B's generic exception handlers.

These tests intentionally do NOT touch the network or external APIs.
The new ``test_fetchers.py`` exercises the real network paths via
injected ``FakeSession`` cassettes (see ``tests/data/conftest.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from data.etl.pit_correct import LookaheadBiasError, LookaheadError, assert_no_lookahead
from data.sources.nba import NBAClient, NBAGame
from data.sources.polygon import ChainEvent, PolygonChainClient
from data.sources.polymarket import MarketHistory, PolymarketHistoryClient
from data.sources.reddit import RedditSentimentClient, SentimentSnapshot

UTC_EPOCH = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# Import-time smoke — every public surface resolves and is instantiable.
# ----------------------------------------------------------------------


def test_all_clients_construct_without_network() -> None:
    """Constructors must be cheap — no network, no env reads."""
    assert NBAClient() is not None
    assert PolymarketHistoryClient() is not None
    assert PolygonChainClient() is not None
    assert RedditSentimentClient() is not None


def test_lookahead_bias_error_is_value_error_subclass() -> None:
    """Callers in Track B / Track C catch ``ValueError`` generically.

    Plus: the T-E-001 alias name :class:`LookaheadBiasError` still
    resolves to the same class as the new :class:`LookaheadError` so
    pinned imports continue to work.
    """
    assert issubclass(LookaheadBiasError, ValueError)
    assert LookaheadBiasError is LookaheadError


def test_assert_no_lookahead_callable_exists() -> None:
    """The PIT chokepoint must be importable + callable; smoke-only."""
    assert callable(assert_no_lookahead)


# ----------------------------------------------------------------------
# Dataclass schemas — round-trip the fields we promised in the brief.
# ----------------------------------------------------------------------


def test_nba_game_dataclass_holds_pit_field() -> None:
    """``available_at`` is the cross-track PIT join key — must exist."""
    g = NBAGame(
        game_id="0022300456",
        tipoff_at=UTC_EPOCH,
        home_team="LAL",
        away_team="BOS",
        available_at=UTC_EPOCH,
    )
    assert g.game_id == "0022300456"
    assert g.available_at == UTC_EPOCH
    assert g.box_score == {}


def test_market_history_dataclass_holds_pit_field() -> None:
    m = MarketHistory(
        slug="nba-lakers-vs-celtics-2026-04-12",
        resolved=False,
        available_at=UTC_EPOCH,
    )
    assert m.slug.startswith("nba-")
    assert m.available_at == UTC_EPOCH
    assert m.orderbook_snapshots == []


def test_chain_event_dataclass_holds_block_time() -> None:
    e = ChainEvent(
        block_number=12345,
        block_time=UTC_EPOCH,
        tx_hash="0xdeadbeef",
        log_index=0,
        contract_address="0x0000000000000000000000000000000000000000",
        event_name="OrderFilled",
    )
    assert e.block_number == 12345
    assert e.decoded_args == {}


def test_sentiment_snapshot_dataclass_holds_pit_field() -> None:
    s = SentimentSnapshot(
        subreddit="nba",
        since=UTC_EPOCH,
        until=UTC_EPOCH,
        available_at=UTC_EPOCH,
    )
    assert s.subreddit == "nba"
    assert s.post_count == 0
    assert s.mention_counts == {}
