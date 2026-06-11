"""Tests for :mod:`agent.data.nba_live` — NBALive balldontlie poller.

Coverage:

1. parse snapshot — one balldontlie row → one GameSnapshot per game.
2. idempotent on rerun — same row at second poll re-emits with a
   fresh available_at (consumers dedup via timestamp).
3. ``available_at = response_received_at`` — verified by injecting
   a fixed clock + polluting the payload with a far-future field.

Plus: poll-failure DegradedFeedWarning + reconnect, async-context-
manager close.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from agent.data import DegradedFeedWarning, GameSnapshot, NBALive
from tests.agent.data.conftest import (
    FakeRestFetcher,
    InstantSleep,
    SteppingClock,
)


def _live_payload(*, games: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": games}


def _live_game(
    *,
    game_id: str = "g1",
    home: str = "LAL",
    away: str = "BOS",
    score_home: int = 50,
    score_away: int = 48,
    quarter: int = 2,
    time_left: str = "5:32",
    status: str = "live",
) -> dict[str, Any]:
    return {
        "id": game_id,
        "home_team": {"abbreviation": home},
        "visitor_team": {"abbreviation": away},
        "home_team_score": score_home,
        "visitor_team_score": score_away,
        "period": quarter,
        "time": time_left,
        "status": status,
    }


# --------------------------------------------------------------------------- #


def test_nba_live_parses_one_game_per_row(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Two rows in one poll → two GameSnapshots."""
    fetcher = FakeRestFetcher(
        routes={
            "https://api.example.com/games?live=true": _live_payload(
                games=[
                    _live_game(game_id="g1", home="LAL", away="BOS"),
                    _live_game(
                        game_id="g2", home="DEN", away="PHX", quarter=4, time_left="0:42"
                    ),
                ]
            )
        }
    )
    feed = NBALive(
        rest_fetcher=fetcher,
        base_url="https://api.example.com",
        poll_seconds=0.001,
        clock=fake_clock,
        sleep=instant_sleep,
        max_reconnect_attempts=1,
    )

    async def run() -> list[GameSnapshot]:
        out: list[GameSnapshot] = []
        async with feed:
            async for evt in feed.live_games():
                if isinstance(evt, GameSnapshot):
                    out.append(evt)
                if len(out) >= 2:
                    await feed.aclose()
                    break
        return out

    snaps = asyncio.run(run())
    assert len(snaps) == 2
    by_id = {s.game_id: s for s in snaps}
    assert by_id["g1"].home_team == "LAL"
    assert by_id["g1"].away_team == "BOS"
    assert by_id["g1"].score_home == 50
    assert by_id["g1"].score_away == 48
    assert by_id["g1"].quarter == 2
    assert by_id["g1"].time_left == "5:32"
    assert by_id["g2"].quarter == 4
    assert by_id["g2"].time_left == "0:42"
    # available_at populated as ISO-8601.
    for s in snaps:
        datetime.fromisoformat(s.available_at)  # parses cleanly


def test_nba_live_idempotent_on_rerun(instant_sleep: InstantSleep) -> None:
    """Polling the same game twice re-emits — each tick gets its own available_at."""
    fixed_times = [
        datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC),
        datetime(2026, 5, 22, 20, 0, 30, tzinfo=UTC),
    ]

    class _ScriptedClock:
        def __init__(self) -> None:
            self._i = 0

        def now(self) -> datetime:
            ts = fixed_times[min(self._i, len(fixed_times) - 1)]
            self._i += 1
            return ts

    poll_count = [0]
    same_game = _live_game(game_id="g1")

    def factory(url: str) -> dict[str, Any]:
        poll_count[0] += 1
        # Two successful polls, then exhausted to terminate the loop.
        if poll_count[0] > 2:
            raise StopAsyncIteration  # not caught by adapter → warning + retry
        return _live_payload(games=[same_game])

    fetcher = FakeRestFetcher(factory=factory)
    feed = NBALive(
        rest_fetcher=fetcher,
        base_url="https://api.example.com",
        poll_seconds=0.001,
        clock=_ScriptedClock(),
        sleep=instant_sleep,
        max_reconnect_attempts=1,
    )

    async def run() -> list[GameSnapshot]:
        out: list[GameSnapshot] = []
        async with feed:
            async for evt in feed.live_games():
                if isinstance(evt, GameSnapshot):
                    out.append(evt)
                if len(out) >= 2:
                    await feed.aclose()
                    break
        return out

    snaps = asyncio.run(run())
    assert len(snaps) == 2
    # Both snapshots are the same game.
    assert snaps[0].game_id == snaps[1].game_id == "g1"
    # available_at strictly advances — same row at two polls = two timestamps.
    assert snaps[0].available_at != snaps[1].available_at
    assert datetime.fromisoformat(snaps[0].available_at) < datetime.fromisoformat(
        snaps[1].available_at
    )


def test_nba_live_available_at_is_response_received(
    instant_sleep: InstantSleep,
) -> None:
    """``available_at`` equals the clock reading at recv time, NOT a payload field."""
    fixed_time = datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)

    class _FixedClock:
        def now(self) -> datetime:
            return fixed_time

    polluted_row = _live_game(game_id="g1")
    # Pollute the payload with a far-future field that the adapter MUST ignore.
    polluted_row["created_at"] = "2099-01-01T00:00:00Z"
    polluted_row["available_at"] = "2099-01-01T00:00:00Z"

    fetcher = FakeRestFetcher(
        routes={
            "https://api.example.com/games?live=true": _live_payload(
                games=[polluted_row]
            )
        }
    )
    feed = NBALive(
        rest_fetcher=fetcher,
        base_url="https://api.example.com",
        poll_seconds=0.001,
        clock=_FixedClock(),
        sleep=instant_sleep,
        max_reconnect_attempts=1,
    )

    async def run() -> GameSnapshot:
        async with feed:
            async for evt in feed.live_games():
                if isinstance(evt, GameSnapshot):
                    await feed.aclose()
                    return evt
        raise AssertionError("no snapshot emitted")

    snap = asyncio.run(run())
    parsed = datetime.fromisoformat(snap.available_at)
    assert parsed == fixed_time
    # Cross-check: NOT the payload field's value.
    assert "2099" not in snap.available_at


def test_nba_live_poll_failure_emits_warning_and_retries(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Poll error → DegradedFeedWarning + exponential-backoff retry."""
    poll_count = [0]

    def factory(url: str) -> dict[str, Any]:
        poll_count[0] += 1
        if poll_count[0] == 1:
            raise ConnectionError("first poll fails")
        # Then succeed.
        return _live_payload(games=[_live_game()])

    fetcher = FakeRestFetcher(factory=factory)
    feed = NBALive(
        rest_fetcher=fetcher,
        base_url="https://api.example.com",
        poll_seconds=0.001,
        clock=fake_clock,
        sleep=instant_sleep,
        max_reconnect_attempts=3,
    )

    async def run() -> list[object]:
        out: list[object] = []
        async with feed:
            async for evt in feed.live_games():
                out.append(evt)
                if len([e for e in out if isinstance(e, GameSnapshot)]) >= 1:
                    await feed.aclose()
                    break
        return out

    events = asyncio.run(run())
    warns = [e for e in events if isinstance(e, DegradedFeedWarning)]
    snaps = [e for e in events if isinstance(e, GameSnapshot)]
    assert warns
    assert warns[0].feed == "nba_live"
    assert "poll_failed" in warns[0].reason
    assert warns[0].attempt == 1
    assert snaps
    # Sleep called at least once for the backoff between failure + retry.
    assert len(instant_sleep.calls) >= 1
    assert instant_sleep.calls[0] >= 1.0


def test_nba_live_constructor_rejects_nonpositive_poll() -> None:
    """poll_seconds must be > 0."""
    fetcher = FakeRestFetcher(routes={})
    with pytest.raises(ValueError, match="poll_seconds"):
        NBALive(rest_fetcher=fetcher, poll_seconds=0.0)
