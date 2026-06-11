"""One test per engine + chokepoint coverage.

Per the T-B-002 brief acceptance criterion ≥11 tests across the
engines directory. This module ships the per-engine smoke tests
(shape + no-lookahead + phase-1 wiring); the dedicated Phase-1
freeze tests live in :mod:`test_phase1_freeze`.

Sprint_7 sport pivot note (T-B-014): the previous three
``test_nba_technical_*`` cases tested :class:`NBATechnicalEngine`,
which is removed in lockstep with the α₁ engine pivot to tennis
(PRD §15 已决 #8). Equivalent PIT-discipline + signal-shape coverage
for the new α₁ tennis primitives lives in
:mod:`tests.agent.engines.test_tennis_technical` (23 cases — strictly
more coverage than the 3 cases removed here).

Every test is hermetic: data clients are mocked / faked, no network
I/O, no Anthropic API calls. The look-ahead chokepoint is exercised
on real DataFrames produced by the engines themselves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent.engines import (
    CrowdVolumeEngine,
    Engine,
    EngineSignal,
    LookaheadError,
    MarketMomentumEngine,
    SentimentLLMEngine,
    Signal,
    SmartMoneyEngine,
)
from agent.engines.smart_money import _WalletPosition
from data.sources.polymarket import MarketHistory
from data.sources.reddit import SentimentSnapshot

# --------------------------------------------------------------------------- #
# Fixtures + fakes
# --------------------------------------------------------------------------- #


CUTOFF = datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)


@dataclass
class _FakePolymarketClient:
    """Returns a canned MarketHistory."""

    history: MarketHistory

    def fetch_market(self, slug: str, *, asof_ts: datetime) -> MarketHistory:
        return self.history


@dataclass
class _FakePositionsClient:
    """Returns a canned list of wallet positions."""

    positions: list[_WalletPosition] = field(default_factory=list)

    def fetch_positions(
        self, market_id: str, *, asof_ts: datetime
    ) -> list[_WalletPosition]:
        return self.positions


@dataclass
class _FakeRedditClient:
    """Returns a canned SentimentSnapshot."""

    snapshot: SentimentSnapshot

    def fetch_subreddit(
        self,
        name: str,
        since: datetime,
        *,
        asof_ts: datetime,
        limit: int = 100,
    ) -> SentimentSnapshot:
        return self.snapshot


@dataclass
class _RecordingLLMClient:
    """Records every structured_call invocation.

    Used to assert Phase 1 NEVER calls the LLM (count == 0) and that
    Phase 2 calls the LLM exactly once on the happy path.
    """

    response: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        return self.response


# --------------------------------------------------------------------------- #
# α₁ engine coverage now lives in tests.agent.engines.test_tennis_technical
# (sprint_7 sport pivot — see module docstring).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Market momentum engine
# --------------------------------------------------------------------------- #


def test_market_momentum_with_uptrend() -> None:
    """Monotonic uptrend → positive score."""
    snapshots = [
        (CUTOFF - timedelta(hours=k), 0.45 + 0.005 * (24 - k)) for k in range(24, 0, -1)
    ]
    hist = MarketHistory(
        slug="lakers-celtics",
        resolved=False,
        available_at=CUTOFF,
        orderbook_snapshots=snapshots,
        market_id="0xabc",
    )
    engine = MarketMomentumEngine(polymarket_client=_FakePolymarketClient(history=hist))
    sig = asyncio.run(engine.evaluate(target="lakers-celtics", asof_ts=CUTOFF))
    assert sig.score > 0.0
    assert sig.raw_features["n_snapshots"] == 24.0
    assert "drift" in sig.raw_features
    assert "velocity" in sig.raw_features


def test_market_momentum_empty_history_returns_neutral() -> None:
    """Empty history is vacuously safe — returns zero score + zero confidence."""
    hist = MarketHistory(
        slug="x",
        resolved=False,
        available_at=CUTOFF,
        orderbook_snapshots=[],
    )
    engine = MarketMomentumEngine(polymarket_client=_FakePolymarketClient(history=hist))
    sig = asyncio.run(engine.evaluate(target="x", asof_ts=CUTOFF))
    assert sig.score == 0.0
    assert sig.confidence == 0.0


def test_market_momentum_rejects_future_snapshot() -> None:
    """A misbehaving client that hands back a future-dated snapshot
    MUST trip the chokepoint."""
    bad_snapshots = [
        (CUTOFF - timedelta(hours=1), 0.5),
        (CUTOFF + timedelta(hours=1), 0.6),  # leak
    ]
    hist = MarketHistory(
        slug="x",
        resolved=False,
        available_at=CUTOFF,
        orderbook_snapshots=bad_snapshots,
    )
    engine = MarketMomentumEngine(polymarket_client=_FakePolymarketClient(history=hist))
    with pytest.raises(LookaheadError):
        asyncio.run(engine.evaluate(target="x", asof_ts=CUTOFF))


# --------------------------------------------------------------------------- #
# Smart money engine
# --------------------------------------------------------------------------- #


def test_smart_money_with_yes_dominant_positions(tmp_path: Any) -> None:
    """Two whitelisted wallets long YES → positive score."""
    # Use a whitelist that includes our test wallets.
    wallets_file = tmp_path / "wallets.json"
    wallets_file.write_text(
        '{"wallets": ['
        '{"address": "0xa", "win_rate": 0.7, "net_pnl_usd": 10000, "settled_records": 40},'
        '{"address": "0xb", "win_rate": 0.7, "net_pnl_usd": 10000, "settled_records": 40}'
        "]}",
        encoding="utf-8",
    )
    positions = [
        _WalletPosition(
            wallet="0xa",
            side="YES",
            size_usd=1000.0,
            available_at=CUTOFF - timedelta(hours=1),
        ),
        _WalletPosition(
            wallet="0xb",
            side="YES",
            size_usd=500.0,
            available_at=CUTOFF - timedelta(hours=2),
        ),
    ]
    engine = SmartMoneyEngine(
        positions_client=_FakePositionsClient(positions=positions),
        wallets_path=wallets_file,
    )
    sig = asyncio.run(engine.evaluate(target="market1", asof_ts=CUTOFF))
    assert sig.score == pytest.approx(1.0)  # all YES, no NO
    assert sig.raw_features["yes_sum_usd"] == 1500.0
    assert sig.raw_features["n_whitelisted_positions"] == 2.0


def test_smart_money_ignores_non_whitelisted(tmp_path: Any) -> None:
    """A wallet NOT on the whitelist must not contribute to the aggregate."""
    wallets_file = tmp_path / "wallets.json"
    wallets_file.write_text('{"wallets": [{"address": "0xWHITELISTED"}]}', encoding="utf-8")
    positions = [
        _WalletPosition(
            wallet="0xother",  # not on whitelist
            side="YES",
            size_usd=1000.0,
            available_at=CUTOFF - timedelta(hours=1),
        ),
    ]
    engine = SmartMoneyEngine(
        positions_client=_FakePositionsClient(positions=positions),
        wallets_path=wallets_file,
    )
    sig = asyncio.run(engine.evaluate(target="market1", asof_ts=CUTOFF))
    # Zero whitelisted → neutral signal
    assert sig.score == 0.0
    assert sig.confidence == 0.0
    assert sig.raw_features["n_whitelisted_positions"] == 0.0


def test_smart_money_default_fixture_loads() -> None:
    """The shipped data/fixtures/smart_money_wallets.json loads + has ≥10
    wallets per the brief acceptance criterion."""
    engine = SmartMoneyEngine(positions_client=_FakePositionsClient(positions=[]))
    whitelist = engine._load_whitelist()
    assert len(whitelist) >= 10


# --------------------------------------------------------------------------- #
# Sentiment LLM engine (Phase 2 path — Phase 1 freeze lives in
# test_phase1_freeze.py)
# --------------------------------------------------------------------------- #


def test_sentiment_llm_phase2_calls_llm_with_valid_response() -> None:
    """Phase 2 calls the LLM once and parses a well-formed response."""
    llm = _RecordingLLMClient(
        response={
            "home_team_sentiment": 0.4,
            "away_team_sentiment": -0.2,
            "confidence": 0.7,
            "key_themes": ["injury_report", "trade_rumour"],
            "reasoning": "Home team has momentum.",
        }
    )
    engine = SentimentLLMEngine(phase=2, llm_client=llm)
    sig = asyncio.run(engine.evaluate(target="market1", asof_ts=CUTOFF))
    # score = (0.4 - -0.2) / 2 = 0.3
    assert sig.score == pytest.approx(0.3)
    assert sig.confidence == pytest.approx(0.7)
    assert len(llm.calls) == 1


def test_sentiment_llm_phase2_retries_then_failsoft() -> None:
    """Malformed response → retry once → fail-soft neutral signal."""

    class _BadLLM:
        calls = 0

        async def structured_call(
            self, *, model: str, prompt: str, schema: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls += 1
            # Schema violation: home_team_sentiment out of range.
            return {
                "home_team_sentiment": 2.0,
                "away_team_sentiment": 0.0,
                "confidence": 0.5,
                "key_themes": [],
                "reasoning": "bad",
            }

    bad = _BadLLM()
    engine = SentimentLLMEngine(phase=2, llm_client=bad)
    sig = asyncio.run(engine.evaluate(target="m", asof_ts=CUTOFF))
    assert sig.score == 0.0
    assert sig.confidence == 0.0
    assert sig.rationale == "malformed_llm_output"
    assert bad.calls == 2  # one + one retry


def test_sentiment_llm_phase2_requires_client() -> None:
    """Phase 2 constructor MUST refuse a None llm_client."""
    with pytest.raises(ValueError, match="llm_client"):
        SentimentLLMEngine(phase=2, llm_client=None)


# --------------------------------------------------------------------------- #
# Crowd volume engine
# --------------------------------------------------------------------------- #


def test_crowd_volume_spike_returns_negative_score() -> None:
    """High post velocity → negative (contrarian) score per PRD §4."""
    snap = SentimentSnapshot(
        subreddit="nba",
        since=CUTOFF - timedelta(hours=24),
        until=CUTOFF,
        available_at=CUTOFF,
        post_count=240,  # 10/hr vs 2/hr baseline → big spike
        comment_count=4800,
    )
    engine = CrowdVolumeEngine(reddit_client=_FakeRedditClient(snapshot=snap))
    sig = asyncio.run(engine.evaluate(target="nba", asof_ts=CUTOFF))
    assert sig.score < 0.0  # contrarian: spike biases against the crowd
    assert sig.confidence > 0.0
    assert sig.raw_features["spike_ratio"] > 1.0


def test_crowd_volume_quiet_window_returns_positive_score() -> None:
    """Below-baseline volume → positive score (mean reversion away from
    contrarian)."""
    snap = SentimentSnapshot(
        subreddit="nba",
        since=CUTOFF - timedelta(hours=24),
        until=CUTOFF,
        available_at=CUTOFF,
        post_count=12,  # 0.5/hr vs 2/hr baseline → quiet
        comment_count=20,
    )
    engine = CrowdVolumeEngine(reddit_client=_FakeRedditClient(snapshot=snap))
    sig = asyncio.run(engine.evaluate(target="nba", asof_ts=CUTOFF))
    assert sig.score > 0.0  # below baseline → contrarian-favourable
    assert sig.raw_features["spike_ratio"] < 1.0


def test_crowd_volume_rejects_future_snapshot() -> None:
    """A misbehaving reddit client returning future-dated rows trips the
    chokepoint."""
    snap = SentimentSnapshot(
        subreddit="nba",
        since=CUTOFF - timedelta(hours=24),
        until=CUTOFF,
        available_at=CUTOFF + timedelta(hours=1),  # leak
        post_count=50,
        comment_count=200,
    )
    engine = CrowdVolumeEngine(reddit_client=_FakeRedditClient(snapshot=snap))
    with pytest.raises(LookaheadError):
        asyncio.run(engine.evaluate(target="nba", asof_ts=CUTOFF))


def test_crowd_volume_constructor_rejects_nonpositive_window() -> None:
    with pytest.raises(ValueError, match="window_hours"):
        CrowdVolumeEngine(
            reddit_client=_FakeRedditClient(
                snapshot=SentimentSnapshot(
                    subreddit="x",
                    since=CUTOFF,
                    until=CUTOFF,
                    available_at=CUTOFF,
                )
            ),
            window_hours=0,
        )
