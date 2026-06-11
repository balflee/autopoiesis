# Greek letters (α₁) mirror PRD §4 / §6.6 notation. Disambiguating
# them to Latin fallbacks would silently desync the code from the spec.
"""α₁ — Tennis technical engine (sprint_7 sport pivot per PRD §15 已决 #8).

Five typed analytical primitives over the Jeff Sackmann tennis corpus
(:mod:`data.sources.tennis_sackmann`). They are the foundation Track B's
α₁ stream will fuse over once the live-loop wiring lands (T-B-015 Day 4
Phase 1 training run on the tennis dataset).

This module is the **canonical α₁ analytics surface** post-pivot — the
previous ``nba_technical.py`` is deleted in the same commit so callers
have one unambiguous source of truth for α₁ feature math. The decision
engine + weight updater + Phase 2 launch orchestrator all rename their
"nba_technical" string constant to "tennis_technical" in lockstep.

Functional API (per brief):

* :func:`compute_elo_diff` — Sackmann rank-points-derived skill gap.
* :func:`compute_surface_advantage` — last-20-matches surface win-rate
  delta.
* :func:`compute_h2h` — head-to-head record between two players.
* :func:`compute_best_of_factor` — True iff ATP Grand Slam (best-of-5,
  higher variance). Pure; no IO.
* :func:`compute_days_since_last_match` — rest-days proxy; ``None`` if
  no prior match exists (per brief: must NOT raise).

Why functions, not an :class:`agent.engines.base.Engine` subclass? The
brief authorises the functional decomposition because the α₁ engine's
Signal-emitting layer is built in T-B-015 on top of these primitives;
this task only owns the analytical building blocks. The functional
shape also makes them trivially composable into a future
:class:`TennisTechnicalEngine` without coupling to any specific Signal
schema today.

Look-ahead discipline (PRD §14.1)
---------------------------------

Every public entrypoint that consumes data requires a timezone-aware
``asof_ts``. The :class:`data.sources.tennis_sackmann.SackmannLoader`
filters rankings rows by ``ranking_date <= asof_ts`` and matches by
``tourney_date <= asof_ts`` BEFORE the function sees them. The
``available_at`` chokepoint is re-asserted on materialised feature
frames as defence-in-depth — if a future refactor of the loader ever
drops the PIT cap, the chokepoint here fires the same way Track C's
calibration sweep would.

Sackmann rankings file shape
----------------------------

The brief mentions ``atp_rankings_YYYY.csv``; the vendored snapshot
ships ``atp_rankings_current.csv`` / ``wta_rankings_current.csv`` which
the loader stitches together by ``ranking_date`` (YYYYMMDD as string).
This module reads through ``SackmannLoader.load_*_rankings`` so any
future snapshot rotation (e.g. yearly Elo files) is a one-line change
in the loader, not here.

The Sackmann corpus does NOT ship a pre-computed Elo column — only
ATP/WTA rank + rank_points. We use rank_points as an Elo proxy:
points are the official tour's monotone composite of recent results,
and the ATP/WTA points → Elo mapping is approximately linear in the
middle of the distribution (top-500 players). When a full Sackmann
ELO snapshot is vendored, swap the body of :func:`compute_elo_diff`
to read it directly — the signature stays the same.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from data.etl.pit_correct import (
    LookaheadError,
    assert_no_lookahead,
    require_asof_ts,
)
from data.sources.tennis_sackmann import SackmannLoader, require_valid_player_ids

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    import pandas as pd

# ---------------------------------------------------------------------------
# Default loader — module-level lazy singleton so the cost of constructing
# the snapshot path / HttpClient is paid once per process. Tests that need
# a custom snapshot_dir override the loader via the ``loader`` kwarg on
# every public entrypoint.
# ---------------------------------------------------------------------------

_DEFAULT_LOADER: SackmannLoader | None = None


def _default_loader() -> SackmannLoader:
    """Return (and lazily create) the module-level SackmannLoader."""
    global _DEFAULT_LOADER
    if _DEFAULT_LOADER is None:
        _DEFAULT_LOADER = SackmannLoader()
    return _DEFAULT_LOADER


# ---------------------------------------------------------------------------
# Player-id → tour heuristic
# ---------------------------------------------------------------------------
#
# Sackmann ATP IDs live in roughly the 1xxxxx–2xxxxx range and WTA IDs
# in 2xxxxx, but the spaces overlap in practice. We try ATP first then
# WTA — the loader's per-tour calls are cheap (snapshot-first) and the
# two tours never share a single ID for any real player.

_ATP_ID_PREFIXES: tuple[str, ...] = ("1", "21", "20")
_WTA_ID_PREFIXES: tuple[str, ...] = ("22", "23")


def _tour_for_player(player_id: str) -> str:
    """Heuristic: ATP if id starts with one of the ATP prefixes, else WTA.

    Used to short-circuit when we KNOW the tour from the id alone. The
    public functions still fall through to the other tour on miss, so a
    mis-classified id never silently loses signal — it just costs one
    extra (cheap) snapshot lookup.
    """
    pid = player_id.strip()
    for prefix in _ATP_ID_PREFIXES:
        if pid.startswith(prefix):
            return "atp"
    for prefix in _WTA_ID_PREFIXES:
        if pid.startswith(prefix):
            return "wta"
    return "atp"  # default — caller can override via match data lookup


def _latest_rank_points(
    loader: SackmannLoader, player_id: str, asof_ts: datetime
) -> float | None:
    """Return the player's most-recent rank_points ≤ ``asof_ts``.

    Tries the heuristically-preferred tour first then falls back to the
    other tour so an ATP-style id (21xxxx) that's actually WTA still
    resolves. Returns ``None`` when neither tour has a row.
    """
    preferred = _tour_for_player(player_id)
    order = ("atp", "wta") if preferred == "atp" else ("wta", "atp")
    pid = player_id.strip()
    for tour in order:
        rankings = (
            loader.load_atp_rankings(asof_ts)
            if tour == "atp"
            else loader.load_wta_rankings(asof_ts)
        )
        if rankings.empty:
            continue
        # ``player`` column is the Sackmann player_id stringified.
        hits = rankings.loc[rankings["player"].astype(str).str.strip() == pid]
        if hits.empty:
            continue
        # ``ranking_date`` is YYYYMMDD as string. Lexical sort = chronological.
        latest = hits.sort_values("ranking_date", kind="stable").iloc[-1]
        try:
            return float(str(latest["points"]).strip() or "0")
        except ValueError:
            return None
    return None


def compute_elo_diff(
    p1_id: str,
    p2_id: str,
    asof_ts: datetime,
    *,
    loader: SackmannLoader | None = None,
) -> float:
    """Skill gap proxy: ``rank_points(p1) − rank_points(p2)`` at ``asof_ts``.

    The Sackmann corpus does NOT ship a pre-computed Elo column. ATP /
    WTA rank_points are the closest publicly-available monotone skill
    proxy — they're the official tour's composite of the last 52
    weeks of results, which is exactly what an Elo would integrate.
    When a vendored Elo snapshot lands the body of this function flips
    to read it directly; the signature is stable.

    Returns 0.0 when EITHER player has no ranking row ≤ ``asof_ts``
    (e.g. a wildcard / qualifier who hasn't broken into the rankings
    yet). The caller can distinguish "no signal" from "draw" by also
    inspecting :func:`compute_h2h` / :func:`compute_days_since_last_match`.

    Raises :class:`data.etl.pit_correct.LookaheadError` if ``asof_ts``
    is naive — PIT discipline is enforced at the boundary.
    """
    cutoff = require_asof_ts(asof_ts)
    ld = loader if loader is not None else _default_loader()

    p1_points = _latest_rank_points(ld, p1_id, cutoff)
    p2_points = _latest_rank_points(ld, p2_id, cutoff)
    if p1_points is None or p2_points is None:
        return 0.0
    return p1_points - p2_points


def _player_match_history(
    loader: SackmannLoader,
    player_id: str,
    asof_ts: datetime,
    year_range: tuple[int, int],
) -> pd.DataFrame:
    """Return a frame of all matches for ``player_id`` ≤ ``asof_ts``.

    Walks both tours and concatenates (a player only ever appears in
    one), filters by ``tourney_date`` ≤ ``asof_ts`` (YYYYMMDD as
    string lex-compare against the ts's YYYYMMDD), and runs the
    canonical look-ahead chokepoint on the materialised frame as
    defence-in-depth.

    ``year_range`` bounds the seasons we load — the default in callers
    is the snapshot's full range ``(2024, 2025)``. Tests can narrow the
    range to keep the fixture footprint tiny.
    """
    import pandas as pd

    pid = player_id.strip()
    frames: list[pd.DataFrame] = []
    for raw in (
        loader.load_atp_matches(year_range),
        loader.load_wta_matches(year_range),
    ):
        if raw.empty:
            continue
        clean = require_valid_player_ids(raw)
        mask = (clean["winner_id"].astype(str).str.strip() == pid) | (
            clean["loser_id"].astype(str).str.strip() == pid
        )
        sub = clean.loc[mask]
        if sub.empty:
            continue
        frames.append(sub)
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    # Filter by tourney_date ≤ asof_ts.
    asof_ymd = asof_ts.strftime("%Y%m%d")
    combined = combined.loc[
        combined["tourney_date"].astype(str).str.strip() <= asof_ymd
    ].copy()
    if combined.empty:
        return combined

    # PIT defence-in-depth: build an available_at column from
    # tourney_date and run the chokepoint. tourney_date is the
    # tournament's START date, which is the earliest moment any data
    # about the match could be known — strictly before the match itself.
    combined["available_at"] = pd.to_datetime(
        combined["tourney_date"].astype(str), format="%Y%m%d", utc=True
    )
    assert_no_lookahead(combined[["available_at"]], asof_ts)
    return combined.sort_values(by="tourney_date", kind="stable").reset_index(
        drop=True
    )


def compute_surface_advantage(
    p1_id: str,
    p2_id: str,
    surface: str,
    asof_ts: datetime,
    *,
    loader: SackmannLoader | None = None,
    window: int = 20,
    year_range: tuple[int, int] = (2024, 2025),
) -> float:
    """Surface-specific win-rate delta from each player's last ``window`` matches.

    ``surface`` is matched case-insensitively against Sackmann's
    canonical labels ("Hard", "Clay", "Grass", "Carpet"). Win rate is
    ``wins / matches_played`` over the last ``window`` (default 20)
    matches on that surface that ended ≤ ``asof_ts``.

    Return value is ``p1_rate − p2_rate`` ∈ [-1, 1]; positive favours
    p1. When either player has zero qualifying matches, that player's
    rate is treated as 0.5 (neutral prior) so the delta degrades
    gracefully toward zero rather than spiking.

    Raises :class:`data.etl.pit_correct.LookaheadError` if ``asof_ts``
    is naive.
    """
    cutoff = require_asof_ts(asof_ts)
    ld = loader if loader is not None else _default_loader()
    if window < 1:
        raise ValueError(f"window must be ≥ 1 (got {window})")
    surface_canonical = surface.strip().lower()

    def _surface_rate(player_id: str) -> float:
        hist = _player_match_history(ld, player_id, cutoff, year_range)
        if len(hist) == 0:
            return 0.5
        # Filter to the requested surface.
        on_surface = hist.loc[
            hist["surface"].astype(str).str.strip().str.lower() == surface_canonical
        ]
        if len(on_surface) == 0:
            return 0.5
        # Most recent ``window`` matches on this surface — tourney_date
        # asc-sorted already, so the tail is the latest.
        recent = on_surface.tail(window)
        pid = player_id.strip()
        wins = int((recent["winner_id"].astype(str).str.strip() == pid).sum())
        return wins / len(recent)

    return _surface_rate(p1_id) - _surface_rate(p2_id)


class H2HRecord(TypedDict):
    """Structured head-to-head summary returned by :func:`compute_h2h`."""

    p1_wins: int
    p2_wins: int
    total_matches: int
    # Win rate for p1; ``None`` when ``total_matches == 0`` so callers
    # can distinguish "p1 has won 0 of 0" from "p1 has won 0 of N".
    p1_win_rate: float | None


def compute_h2h(
    p1_id: str,
    p2_id: str,
    asof_ts: datetime,
    *,
    loader: SackmannLoader | None = None,
    year_range: tuple[int, int] = (2024, 2025),
) -> H2HRecord:
    """Head-to-head record between ``p1`` and ``p2`` up to ``asof_ts``.

    Returns a :class:`H2HRecord` with the count breakdown and p1's win
    rate (``None`` when they've never met). Walks both ATP and WTA
    match logs so mixed-tour searches (rare but legal — e.g. mixed
    doubles via separate IDs) still resolve.

    Raises :class:`data.etl.pit_correct.LookaheadError` if ``asof_ts``
    is naive.
    """
    cutoff = require_asof_ts(asof_ts)
    ld = loader if loader is not None else _default_loader()
    p1 = p1_id.strip()
    p2 = p2_id.strip()

    # Pull p1's full history then intersect with p2 to find joint rows.
    hist = _player_match_history(ld, p1, cutoff, year_range)
    if len(hist) == 0:
        return H2HRecord(
            p1_wins=0, p2_wins=0, total_matches=0, p1_win_rate=None
        )
    winner_col = hist["winner_id"].astype(str).str.strip()
    loser_col = hist["loser_id"].astype(str).str.strip()
    mask_p1_won = (winner_col == p1) & (loser_col == p2)
    mask_p2_won = (winner_col == p2) & (loser_col == p1)
    p1_wins = int(mask_p1_won.sum())
    p2_wins = int(mask_p2_won.sum())
    total = p1_wins + p2_wins
    win_rate: float | None = p1_wins / total if total > 0 else None
    return H2HRecord(
        p1_wins=p1_wins,
        p2_wins=p2_wins,
        total_matches=total,
        p1_win_rate=win_rate,
    )


# Canonical Grand-Slam labels we treat as ATP best-of-5. Sackmann's
# tour_level uses single-letter codes (G/M/A/F/D); the build_tennis_phase1
# parquet emits composite "<tour>-<level>" (e.g. "atp-G", "wta-G").
# We accept both forms PLUS the human-readable "Grand Slam" that the
# brief uses in its example so callers don't have to remember which
# layer of normalisation they're in.
_ATP_GRAND_SLAM_LABELS: frozenset[str] = frozenset(
    {
        "atp-g",
        "atp grand slam",
        "grand slam",  # brief example — assumed ATP per the docstring
        "grand_slam",
    }
)
_WTA_GRAND_SLAM_LABELS: frozenset[str] = frozenset(
    {
        "wta-g",
        "wta grand slam",
    }
)


def compute_best_of_factor(tour_level: str) -> bool:
    """Return True iff the match is a best-of-5 ATP Grand Slam.

    Per PRD §6.6 / §14.1 the bet-sizing pipeline cares about outcome
    variance — a best-of-5 final has materially lower upset variance
    than a best-of-3 because the longer format gives the favourite
    more chances to convert their edge. ATP Grand Slams (men's tour)
    are best-of-5; WTA Grand Slams (women's tour) and ALL other ATP /
    WTA events are best-of-3.

    Accepts three label forms (case-insensitive):

    * Composite ``"atp-G"`` / ``"wta-G"`` per the
      :func:`data.etl.build_training_set.build_tennis_phase1`
      column shape.
    * Human-readable ``"Grand Slam"`` (assumed ATP per the brief's
      example: ``compute_best_of_factor('Grand Slam') == True``).
    * Sackmann raw single-letter codes are NOT accepted alone — they
      lack the tour discriminator. Pre-compose with the tour as
      ``"atp-G"`` if you only have the raw code.

    Returns ``False`` for any unrecognised input — best-of-3 is the
    safer default for the variance model than spuriously claiming
    best-of-5.
    """
    label = tour_level.strip().lower()
    if label in _ATP_GRAND_SLAM_LABELS:
        return True
    if label in _WTA_GRAND_SLAM_LABELS:
        return False
    return False


def compute_days_since_last_match(
    player_id: str,
    asof_ts: datetime,
    *,
    loader: SackmannLoader | None = None,
    year_range: tuple[int, int] = (2024, 2025),
) -> int | None:
    """Days between ``asof_ts`` and ``player_id``'s most recent match.

    Returns ``None`` when the player has no recorded match ≤ ``asof_ts``
    in the snapshot. Per the brief: **do not raise** for the "no prior
    match" case — a fresh face is a legitimate state and the caller
    decides how to weight the missing-rest signal.

    The day count is computed from Sackmann's ``tourney_date`` which is
    the tournament START date; if the player played mid-tournament the
    true rest is a few days less. This bias is documented for the
    reflection layer; absolute precision is not required for a rest
    proxy whose downstream consumption is a coarse Elo-shift.

    Raises :class:`data.etl.pit_correct.LookaheadError` if ``asof_ts``
    is naive.
    """
    cutoff = require_asof_ts(asof_ts)
    ld = loader if loader is not None else _default_loader()
    hist = _player_match_history(ld, player_id, cutoff, year_range)
    if len(hist) == 0:
        return None

    last_date_raw = str(hist.iloc[-1]["tourney_date"]).strip()
    if len(last_date_raw) != 8:
        return None
    try:
        last_date = datetime.strptime(last_date_raw, "%Y%m%d").replace(
            tzinfo=cutoff.tzinfo
        )
    except ValueError:
        return None
    delta: timedelta = cutoff - last_date
    return max(0, delta.days)


__all__ = [
    "H2HRecord",
    "LookaheadError",
    "compute_best_of_factor",
    "compute_days_since_last_match",
    "compute_elo_diff",
    "compute_h2h",
    "compute_surface_advantage",
]
