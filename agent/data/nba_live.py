"""NBA live-game adapter — balldontlie live-score poller.

Wires the α₁ NBA technical engine to a live game-state feed in Phase 2
per PRD §7 + TECHNICAL_PLAN §8 Day 8. Polls the balldontlie
``/games?live=true`` endpoint at a configurable cadence (default 30s)
and emits one :class:`GameSnapshot` per game per tick.

Exposes an **async context manager** API::

    async with NBALive(rest_fetcher=fetcher) as feed:
        async for evt in feed.live_games():
            if isinstance(evt, DegradedFeedWarning):
                ...
            # evt is a GameSnapshot.

Hard rules:

* ``available_at`` on every emitted :class:`GameSnapshot` is the
  **response-received** wall-clock timestamp (from the injected
  clock) — NEVER the box score's clock or any payload field.
* Polling failures (timeout, 5xx) trigger exponential-backoff retry,
  surfaced as :class:`DegradedFeedWarning` to the consumer, capped at
  ``max_reconnect_attempts``.
* Idempotent: re-emitting the same ``(game_id, quarter, time_left)``
  tuple is allowed (the consumer dedups via available_at if it
  needs to); we never silently drop a refresh.
* Zero live network at import / construct time. The
  :class:`_RestFetcher` Protocol is injected; production wires
  ``httpx``-backed fetcher in T-B-007.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.data._realtime_buffer import (
    Clock,
    DegradedFeedWarning,
    RealtimeBuffer,
    UtcClock,
)


class GameSnapshot(BaseModel):
    """Point-in-time NBA game state from balldontlie live endpoint.

    ``available_at`` is the **response-received** ISO-8601 UTC
    timestamp set after the REST call returned — NOT a payload field
    (per the module-level look-ahead contract).

    ``time_left`` is the on-court game clock string the upstream
    returns (e.g. ``"5:32"``); we keep it as a string because the
    balldontlie shape isn't strict (some legs return ``""`` between
    quarters). Consumers parse defensively.
    """

    model_config = ConfigDict(extra="forbid")

    game_id: str
    home_team: str
    away_team: str
    score_home: int = Field(ge=0)
    score_away: int = Field(ge=0)
    quarter: int = Field(ge=0)
    time_left: str
    available_at: str
    status: str = "live"


# ----- Transport Protocol -----


class _RestFetcher(Protocol):
    """Minimal async JSON GET — injected; production = httpx-backed."""

    async def get_json(self, url: str) -> dict[str, Any]: ...


NBALiveEvent = GameSnapshot | DegradedFeedWarning


# ----- Defaults -----


DEFAULT_BASE_URL = "https://api.balldontlie.io/v1"
DEFAULT_POLL_SECONDS = 30.0
_DEFAULT_MAX_RECONNECT = 5
_DEFAULT_BUFFER_MAXSIZE = 1024
_DEFAULT_WINDOW = 256
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_CAP = 30.0


class NBALive:
    """Async-context-manager live-game adapter."""

    def __init__(
        self,
        *,
        rest_fetcher: _RestFetcher,
        base_url: str = DEFAULT_BASE_URL,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_reconnect_attempts: int = _DEFAULT_MAX_RECONNECT,
        buffer_maxsize: int = _DEFAULT_BUFFER_MAXSIZE,
        window: int = _DEFAULT_WINDOW,
    ) -> None:
        if poll_seconds <= 0.0:
            raise ValueError(f"poll_seconds must be > 0 (got {poll_seconds})")
        if max_reconnect_attempts < 1:
            raise ValueError(
                f"max_reconnect_attempts must be ≥ 1 (got {max_reconnect_attempts})"
            )
        self._rest = rest_fetcher
        self._base_url = base_url.rstrip("/")
        self._poll_seconds = poll_seconds
        self._clock: Clock = clock if clock is not None else UtcClock()
        self._sleep = sleep
        self._max_attempts = max_reconnect_attempts
        self._buffer: RealtimeBuffer[GameSnapshot] = RealtimeBuffer(
            maxsize=buffer_maxsize, window=window,
        )
        self._closed = False

    @property
    def buffer(self) -> RealtimeBuffer[GameSnapshot]:
        return self._buffer

    async def __aenter__(self) -> NBALive:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True

    async def live_games(self) -> AsyncIterator[NBALiveEvent]:
        """Poll the live-games endpoint and yield snapshots.

        Each tick fans out one snapshot per live game returned by the
        upstream. Empty windows are silently skipped (no live games is
        the typical state mid-day).

        On poll failure, yields a :class:`DegradedFeedWarning` and
        retries with exponential backoff up to
        ``max_reconnect_attempts`` consecutive failures.
        """
        attempt = 0
        backoff = _DEFAULT_BACKOFF_BASE
        url = f"{self._base_url}/games?live=true"

        while not self._closed:
            try:
                payload = await self._rest.get_json(url)
            except Exception as exc:
                attempt += 1
                yield DegradedFeedWarning(
                    feed="nba_live",
                    reason=f"poll_failed:{type(exc).__name__}",
                    attempt=attempt,
                    available_at=self._clock.now().isoformat(),
                )
                if self._closed or attempt >= self._max_attempts:
                    return
                await self._sleep(backoff)
                backoff = min(backoff * 2.0, _DEFAULT_BACKOFF_CAP)
                continue

            # ---- Response-received timestamp captured HERE, BEFORE payload
            # parsing. Per the module-level look-ahead contract.
            arrival = self._clock.now()
            # Successful poll → reset attempt counter & backoff.
            attempt = 0
            backoff = _DEFAULT_BACKOFF_BASE

            rows = payload.get("data") or payload.get("games") or []
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                snap = _project_row(row, arrival)
                if snap is None:
                    continue
                await self._buffer.put(snap)
                yield snap

            if self._closed:
                return
            await self._sleep(self._poll_seconds)


# ----- Pure helpers -----


def _project_row(row: dict[str, Any], arrival: datetime) -> GameSnapshot | None:
    """Project one balldontlie game row onto :class:`GameSnapshot`.

    Returns ``None`` if the row lacks required keys. Defensive about
    missing fields because balldontlie's free-tier responses
    occasionally lack ``status`` mid-quarter.
    """
    game_id = str(row.get("id") or row.get("game_id") or "")
    if not game_id:
        return None
    home_team_row = row.get("home_team") or {}
    away_team_row = row.get("visitor_team") or row.get("away_team") or {}
    if not isinstance(home_team_row, dict) or not isinstance(away_team_row, dict):
        return None
    home_team = str(home_team_row.get("abbreviation") or "")
    away_team = str(away_team_row.get("abbreviation") or "")
    if not home_team or not away_team:
        return None

    score_home = _coerce_nonneg_int(row.get("home_team_score"))
    score_away = _coerce_nonneg_int(row.get("visitor_team_score"))
    quarter = _coerce_nonneg_int(row.get("period") or row.get("quarter"))
    time_left = str(row.get("time") or row.get("time_left") or "")
    status = str(row.get("status") or "live")

    return GameSnapshot(
        game_id=game_id,
        home_team=home_team,
        away_team=away_team,
        score_home=score_home,
        score_away=score_away,
        quarter=quarter,
        time_left=time_left,
        available_at=arrival.isoformat(),
        status=status,
    )


def _coerce_nonneg_int(raw: Any) -> int:
    """Decode a non-negative int defensively. Negative / malformed → 0."""
    if raw is None:
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return v if v >= 0 else 0


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_POLL_SECONDS",
    "DegradedFeedWarning",
    "GameSnapshot",
    "NBALive",
    "NBALiveEvent",
]
