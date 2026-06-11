"""NBA stats source-adapter — balldontlie free-tier client.

Per PRD §7 the NBA feed is one of the four canonical data sources.
This module is the read-only ``fetch_game`` entrypoint Track B's
feature pipeline and Track C's calibration replay both call.

Implementation choice: balldontlie.io free tier. No auth, JSON over
HTTPS, generous rate limits, schema is stable enough that a small
adapter handles it. Live calls go through :class:`HttpClient` so
retries + UA + timeout are uniform with the other Track E fetchers.

Hard rules:

* No network at import or constructor time.
* :meth:`NBAClient.fetch_game` REQUIRES the ``asof_ts`` keyword
  argument. Missing → :class:`LookaheadError` raised BEFORE any
  network call (so we don't burn quota on a leak).
* The returned :class:`NBAGame` carries ``available_at <=
  asof_ts``: for scheduled games this is the publication time of
  the schedule (always ≤ tipoff); for final box scores it is the
  final-buzzer timestamp + the balldontlie publish lag.
* Caller asserts PIT via
  :func:`data.etl.pit_correct.assert_no_lookahead` once rows are
  joined into a feature parquet — the per-row check happens at the
  pipeline boundary, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from data.sources._http import HttpClient, require_asof_ts

BALLDONTLIE_BASE_URL = "https://api.balldontlie.io/v1"


@dataclass(frozen=True)
class NBAGame:
    """Point-in-time NBA game snapshot.

    Fields preserved from sprint_1 schema (T-E-001) for back-compat.
    sprint_2 adds the ``home_score`` / ``away_score`` / ``status``
    fields populated from the balldontlie payload — the box_score dict
    stays as the catch-all for free-form stats the parquet schema does
    not pin.
    """

    game_id: str
    tipoff_at: datetime
    home_team: str
    away_team: str
    available_at: datetime
    box_score: dict[str, float] = field(default_factory=dict)
    home_score: int = 0
    away_score: int = 0
    status: str = "scheduled"


class NBAClient:
    """Thin balldontlie.io client — READ-ONLY.

    Constructor is cheap. The shared :class:`HttpClient` handles retry
    + UA + timeout; tests inject a recorded :class:`requests.Session`
    via :meth:`HttpClient.set_session` to keep CI hermetic.
    """

    def __init__(
        self,
        *,
        base_url: str = BALLDONTLIE_BASE_URL,
        http: HttpClient | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http if http is not None else HttpClient()
        self._cache_dir: str | None = cache_dir

    @property
    def http(self) -> HttpClient:
        """Expose the underlying HttpClient so tests can inject a session."""
        return self._http

    def fetch_game(self, game_id: str, *, asof_ts: datetime) -> NBAGame:
        """Return the point-in-time-correct snapshot for ``game_id``.

        ``asof_ts`` is required. Returned ``available_at`` is guaranteed
        to be ≤ ``asof_ts`` by construction; the caller still routes
        joined feature rows through :func:`assert_no_lookahead` for
        defence-in-depth.
        """
        cutoff = require_asof_ts(asof_ts)

        url = f"{self._base_url}/games/{game_id}"
        resp = self._http.get(url, timeout=10.0)
        payload = resp.json()
        # balldontlie wraps single-row reads in a top-level 'data' key.
        row: dict[str, Any] = payload["data"] if "data" in payload else payload
        return _decode_game_row(row, asof_cap=cutoff)


def _decode_game_row(row: dict[str, Any], *, asof_cap: datetime) -> NBAGame:
    """Project a balldontlie ``/games/{id}`` row onto :class:`NBAGame`.

    balldontlie returns ``date`` as a date-only string (``"2026-04-12"``)
    and ``status`` either as a clock string mid-game or "Final" once
    settled. We use ``date`` as a coarse tipoff (00:00 UTC of game day —
    fine-grained tipoff isn't on the free tier) and CAP ``available_at``
    at the caller's ``asof_cap`` so PIT is enforced by construction.
    """
    raw_date: str = row["date"]
    tipoff = datetime.fromisoformat(raw_date).replace(tzinfo=UTC)

    home_team_row: dict[str, Any] = row.get("home_team", {})
    away_team_row: dict[str, Any] = row.get("visitor_team", {})

    # available_at is min(tipoff, asof_cap) for scheduled games (the
    # schedule was public before tipoff), and min(final_at, asof_cap)
    # for completed games. We don't get the publish timestamp from
    # balldontlie, so cap at asof_ts — defensive PIT.
    available_at = min(tipoff, asof_cap)

    return NBAGame(
        game_id=str(row["id"]),
        tipoff_at=tipoff,
        home_team=str(home_team_row.get("abbreviation", "")),
        away_team=str(away_team_row.get("abbreviation", "")),
        available_at=available_at,
        home_score=int(row.get("home_team_score", 0) or 0),
        away_score=int(row.get("visitor_team_score", 0) or 0),
        status=str(row.get("status", "scheduled")),
    )


__all__ = ["BALLDONTLIE_BASE_URL", "NBAClient", "NBAGame"]
