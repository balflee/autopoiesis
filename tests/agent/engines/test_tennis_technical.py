"""α₁ tennis technical engine tests — T-B-014 acceptance criteria.

Brief acceptance: ≥15 cases (5 funcs × {happy, nil, PIT-boundary})
PLUS PIT-boundary tests on ``compute_elo_diff`` and
``compute_surface_advantage``. The function table:

* ``compute_elo_diff`` — happy / nil-missing-player / PIT-naive /
  PIT-boundary (asof_ts == ranking_date) / cross-tour fallback.
* ``compute_surface_advantage`` — happy / nil-no-matches /
  PIT-naive / PIT-boundary (asof_ts == tourney_date) / window cap.
* ``compute_h2h`` — happy / nil-never-met / PIT-naive.
* ``compute_best_of_factor`` — happy ATP-G / happy WTA-G / happy
  non-Slam / happy human-readable "Grand Slam".
* ``compute_days_since_last_match`` — happy / nil-no-prior-match /
  PIT-naive.

Hermetic: every test uses a tmp_path snapshot dir populated with CSVs
that exercise the relevant edge case. No network / no real Sackmann
snapshot leaks in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.engines.tennis_technical import (
    H2HRecord,
    LookaheadError,
    compute_best_of_factor,
    compute_days_since_last_match,
    compute_elo_diff,
    compute_h2h,
    compute_surface_advantage,
)
from data.sources.tennis_sackmann import SackmannLoader

# ---------------------------------------------------------------------------
# Snapshot fixtures — tiny CSVs vendored at runtime under tmp_path so the
# tests don't touch the real data/sources/sackmann_snapshot/ files.
# ---------------------------------------------------------------------------


_ATP_RANKINGS_CSV = """ranking_date,rank,player,points
20240601,1,210001,9000
20240601,2,210002,8500
20240601,3,210003,8000
20240801,1,210001,9500
20240801,2,210002,8700
20240801,3,210003,8100
"""

_WTA_RANKINGS_CSV = """ranking_date,rank,player,points
20240601,1,220001,9100
20240601,2,220002,8400
20240801,1,220001,9300
20240801,2,220002,8600
"""

# Match CSV header mirrors data.sources.tennis_sackmann.MATCH_COLUMNS so the
# loader's snapshot reader returns a frame with the canonical schema.
_MATCH_HEADER = (
    "tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,"
    "match_num,winner_id,winner_seed,winner_entry,winner_name,winner_hand,"
    "winner_ht,winner_ioc,winner_age,loser_id,loser_seed,loser_entry,"
    "loser_name,loser_hand,loser_ht,loser_ioc,loser_age,score,best_of,"
    "round,minutes,w_ace,w_df,w_svpt,w_1stIn,w_1stWon,w_2ndWon,w_SvGms,"
    "w_bpSaved,w_bpFaced,l_ace,l_df,l_svpt,l_1stIn,l_1stWon,l_2ndWon,"
    "l_SvGms,l_bpSaved,l_bpFaced,winner_rank,winner_rank_points,"
    "loser_rank,loser_rank_points"
)


def _match_row(
    *,
    tourney_id: str,
    surface: str,
    tourney_date: str,
    match_num: int,
    winner_id: str,
    loser_id: str,
    best_of: int = 3,
    tourney_level: str = "A",
) -> str:
    """Build one Sackmann-shaped match CSV row. Most columns are filler;
    the loader only consumes a small subset for our tests."""
    return (
        f"{tourney_id},Test Open,{surface},32,{tourney_level},"
        f"{tourney_date},{match_num},{winner_id},,,Winner,R,185,USA,"
        f"25.0,{loser_id},,,Loser,R,185,USA,26.0,6-4 6-3,{best_of},"
        "R32,90,5,1,50,30,25,12,10,2,4,3,2,45,28,22,11,9,2,5,"
        "10,1500,20,1200"
    )


_ATP_MATCHES_2024 = "\n".join(
    [
        _MATCH_HEADER,
        # Player 210001 (p1): 3 Hard wins, 1 Hard loss, 1 Clay loss.
        _match_row(
            tourney_id="2024-001",
            surface="Hard",
            tourney_date="20240301",
            match_num=1,
            winner_id="210001",
            loser_id="210099",
        ),
        _match_row(
            tourney_id="2024-001",
            surface="Hard",
            tourney_date="20240305",
            match_num=2,
            winner_id="210001",
            loser_id="210098",
        ),
        _match_row(
            tourney_id="2024-002",
            surface="Hard",
            tourney_date="20240405",
            match_num=1,
            winner_id="210001",
            loser_id="210002",
        ),
        _match_row(
            tourney_id="2024-003",
            surface="Hard",
            tourney_date="20240505",
            match_num=1,
            winner_id="210002",
            loser_id="210001",
        ),
        _match_row(
            tourney_id="2024-004",
            surface="Clay",
            tourney_date="20240605",
            match_num=1,
            winner_id="210099",
            loser_id="210001",
        ),
        # Player 210002 (p2): 1 Hard win (above), 1 Hard loss (above), 1 Clay win.
        _match_row(
            tourney_id="2024-005",
            surface="Clay",
            tourney_date="20240705",
            match_num=1,
            winner_id="210002",
            loser_id="210099",
        ),
    ]
)

_ATP_MATCHES_2025 = "\n".join(
    [
        _MATCH_HEADER,
        # One 2025 match — gives us the "most recent match" anchor for rest
        # days computation.
        _match_row(
            tourney_id="2025-001",
            surface="Hard",
            tourney_date="20250115",
            match_num=1,
            winner_id="210001",
            loser_id="210003",
        ),
    ]
)

# WTA matches — the surface/h2h/rest computes walk BOTH tours (a player only
# appears in one), so the loader reads wta_matches_{year}.csv for every year in
# the default range. We vendor tiny WTA files with WTA-only ids (220xxx) so the
# fixture is OFFLINE-hermetic (no GitHub fallback) WITHOUT touching the ATP-player
# assertions — the ATP test ids (210xxx) never match a WTA row, so WTA contributes
# nothing to those computations. (Previously WTA was omitted, which forced a live
# GitHub fetch that 404s in offline/CI envs → the 9 known failures.)
_WTA_MATCHES_2024 = "\n".join(
    [
        _MATCH_HEADER,
        _match_row(
            tourney_id="2024-w01",
            surface="Hard",
            tourney_date="20240310",
            match_num=1,
            winner_id="220001",
            loser_id="220099",
        ),
        _match_row(
            tourney_id="2024-w02",
            surface="Clay",
            tourney_date="20240610",
            match_num=1,
            winner_id="220002",
            loser_id="220099",
        ),
    ]
)

_WTA_MATCHES_2025 = "\n".join(
    [
        _MATCH_HEADER,
        _match_row(
            tourney_id="2025-w01",
            surface="Hard",
            tourney_date="20250120",
            match_num=1,
            winner_id="220001",
            loser_id="220002",
        ),
    ]
)


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    """Write the tiny CSVs and return the snapshot directory path."""
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "atp_rankings_current.csv").write_text(_ATP_RANKINGS_CSV, encoding="utf-8")
    (snap / "wta_rankings_current.csv").write_text(_WTA_RANKINGS_CSV, encoding="utf-8")
    (snap / "atp_matches_2024.csv").write_text(_ATP_MATCHES_2024, encoding="utf-8")
    (snap / "atp_matches_2025.csv").write_text(_ATP_MATCHES_2025, encoding="utf-8")
    # WTA matches vendored too (WTA-only ids) so the both-tours computes read them
    # OFFLINE — no live GitHub fallback (which 404s in offline/CI envs).
    (snap / "wta_matches_2024.csv").write_text(_WTA_MATCHES_2024, encoding="utf-8")
    (snap / "wta_matches_2025.csv").write_text(_WTA_MATCHES_2025, encoding="utf-8")
    return snap


@pytest.fixture
def loader(snapshot_dir: Path) -> SackmannLoader:
    return SackmannLoader(snapshot_dir=snapshot_dir)


# ---------------------------------------------------------------------------
# compute_elo_diff
# ---------------------------------------------------------------------------


def test_elo_diff_happy_path_returns_signed_points_delta(
    loader: SackmannLoader,
) -> None:
    """Latest ranking ≤ asof_ts wins. p1=9500 - p2=8700 = 800."""
    asof = datetime(2024, 9, 1, 12, 0, tzinfo=UTC)
    diff = compute_elo_diff("210001", "210002", asof, loader=loader)
    assert diff == pytest.approx(800.0)


def test_elo_diff_nil_missing_player_returns_zero(loader: SackmannLoader) -> None:
    """Player with no ranking row → no signal, return 0.0 (do not raise)."""
    asof = datetime(2024, 9, 1, 12, 0, tzinfo=UTC)
    diff = compute_elo_diff("210001", "999999", asof, loader=loader)
    assert diff == 0.0


def test_elo_diff_pit_naive_asof_raises(loader: SackmannLoader) -> None:
    """Naive datetime is itself a PIT discipline violation."""
    naive = datetime(2024, 9, 1, 12, 0)  # no tzinfo
    with pytest.raises(LookaheadError):
        compute_elo_diff("210001", "210002", naive, loader=loader)


def test_elo_diff_pit_boundary_excludes_future_ranking_dates(
    loader: SackmannLoader,
) -> None:
    """asof_ts == 20240701 must NOT see the 20240801 row.

    With the cutoff at 2024-07-01, only the 2024-06-01 rankings are
    visible: p1=9000, p2=8500, diff=500.
    """
    asof = datetime(2024, 7, 1, 0, 0, tzinfo=UTC)
    diff = compute_elo_diff("210001", "210002", asof, loader=loader)
    assert diff == pytest.approx(500.0)


def test_elo_diff_cross_tour_fallback_resolves_wta_id(
    loader: SackmannLoader,
) -> None:
    """A WTA-prefixed id (22xxxx) resolves via the WTA fallback path
    even when the heuristic guesses ATP first."""
    asof = datetime(2024, 9, 1, 12, 0, tzinfo=UTC)
    diff = compute_elo_diff("220001", "220002", asof, loader=loader)
    # WTA latest @ 2024-09-01: 9300 - 8600 = 700.
    assert diff == pytest.approx(700.0)


# ---------------------------------------------------------------------------
# compute_surface_advantage
# ---------------------------------------------------------------------------


def test_surface_advantage_happy_path_returns_signed_rate_delta(
    loader: SackmannLoader,
) -> None:
    """p1 on Hard: 3 wins / 4 played = 0.75. p2 on Hard: 1 / 2 = 0.5.
    Delta = +0.25."""
    asof = datetime(2024, 12, 31, 0, 0, tzinfo=UTC)
    delta = compute_surface_advantage(
        "210001", "210002", "Hard", asof, loader=loader
    )
    assert delta == pytest.approx(0.25)


def test_surface_advantage_nil_when_no_matches_on_surface(
    loader: SackmannLoader,
) -> None:
    """Grass has zero matches in the snapshot → both rates degrade to
    0.5 neutral prior → delta 0."""
    asof = datetime(2024, 12, 31, 0, 0, tzinfo=UTC)
    delta = compute_surface_advantage(
        "210001", "210002", "Grass", asof, loader=loader
    )
    assert delta == 0.0


def test_surface_advantage_pit_naive_asof_raises(
    loader: SackmannLoader,
) -> None:
    naive = datetime(2024, 9, 1, 12, 0)
    with pytest.raises(LookaheadError):
        compute_surface_advantage("210001", "210002", "Hard", naive, loader=loader)


def test_surface_advantage_pit_boundary_excludes_future_matches(
    loader: SackmannLoader,
) -> None:
    """asof_ts at 2024-04-01 hides the 2024-04-05 match between p1+p2.

    Before April 5 p1 has 2 Hard wins and 0 Hard losses (1.0 win-rate);
    p2 has zero Hard matches (0.5 neutral). Delta = +0.5.
    """
    asof = datetime(2024, 4, 1, 0, 0, tzinfo=UTC)
    delta = compute_surface_advantage(
        "210001", "210002", "Hard", asof, loader=loader
    )
    assert delta == pytest.approx(0.5)


def test_surface_advantage_window_cap_keeps_only_last_n(
    loader: SackmannLoader,
) -> None:
    """With window=2 p1's surface rate uses only the 2 most recent Hard
    matches (one win, one loss → 0.5). p2 stays at 1/2 = 0.5. Delta = 0.0."""
    asof = datetime(2024, 12, 31, 0, 0, tzinfo=UTC)
    delta = compute_surface_advantage(
        "210001", "210002", "Hard", asof, loader=loader, window=2
    )
    assert delta == 0.0


def test_surface_advantage_rejects_nonpositive_window(
    loader: SackmannLoader,
) -> None:
    asof = datetime(2024, 12, 31, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="window"):
        compute_surface_advantage(
            "210001", "210002", "Hard", asof, loader=loader, window=0
        )


# ---------------------------------------------------------------------------
# compute_h2h
# ---------------------------------------------------------------------------


def test_h2h_happy_path_returns_structured_record(
    loader: SackmannLoader,
) -> None:
    """p1 beat p2 once (2024-04-05), p2 beat p1 once (2024-05-05).
    1-1 record, win_rate = 0.5."""
    asof = datetime(2024, 12, 31, 0, 0, tzinfo=UTC)
    rec: H2HRecord = compute_h2h("210001", "210002", asof, loader=loader)
    assert rec["p1_wins"] == 1
    assert rec["p2_wins"] == 1
    assert rec["total_matches"] == 2
    assert rec["p1_win_rate"] == pytest.approx(0.5)


def test_h2h_nil_when_never_met(loader: SackmannLoader) -> None:
    """Two players who never faced each other → zeros + None win_rate
    (distinguishes 'never met' from '0-of-N')."""
    asof = datetime(2024, 12, 31, 0, 0, tzinfo=UTC)
    rec = compute_h2h("210001", "999999", asof, loader=loader)
    assert rec["p1_wins"] == 0
    assert rec["p2_wins"] == 0
    assert rec["total_matches"] == 0
    assert rec["p1_win_rate"] is None


def test_h2h_pit_naive_asof_raises(loader: SackmannLoader) -> None:
    naive = datetime(2024, 9, 1, 12, 0)
    with pytest.raises(LookaheadError):
        compute_h2h("210001", "210002", naive, loader=loader)


# ---------------------------------------------------------------------------
# compute_best_of_factor — pure function, no IO
# ---------------------------------------------------------------------------


def test_best_of_factor_atp_grand_slam_returns_true() -> None:
    """ATP composite ``atp-G`` and the brief's example ``Grand Slam``
    both flag the best-of-5 path."""
    assert compute_best_of_factor("atp-G") is True
    assert compute_best_of_factor("Grand Slam") is True


def test_best_of_factor_wta_grand_slam_returns_false() -> None:
    """WTA Grand Slams are best-of-3 — the variance factor MUST flip."""
    assert compute_best_of_factor("wta-G") is False


def test_best_of_factor_non_grand_slam_returns_false() -> None:
    """ATP Masters / WTA 1000 / anything not a Slam → best-of-3."""
    assert compute_best_of_factor("atp-M") is False
    assert compute_best_of_factor("wta-A") is False
    assert compute_best_of_factor("unrecognised") is False
    assert compute_best_of_factor("") is False


def test_best_of_factor_case_insensitive() -> None:
    """Mixed-case labels normalise to the canonical lowercase set."""
    assert compute_best_of_factor("ATP-G") is True
    assert compute_best_of_factor("WTA-G") is False
    assert compute_best_of_factor("grand_slam") is True


# ---------------------------------------------------------------------------
# compute_days_since_last_match
# ---------------------------------------------------------------------------


def test_days_since_last_match_happy_path(loader: SackmannLoader) -> None:
    """Player 210001's most recent match in the snapshot is 2025-01-15.
    asof=2025-02-01 → 17 days."""
    asof = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
    days = compute_days_since_last_match("210001", asof, loader=loader)
    assert days == 17


def test_days_since_last_match_returns_none_when_no_prior(
    loader: SackmannLoader,
) -> None:
    """Per brief: a player with no recorded match returns None, NOT
    raises. (Fresh face / wildcard entrant case.)"""
    asof = datetime(2025, 2, 1, 0, 0, tzinfo=UTC)
    days = compute_days_since_last_match("999999", asof, loader=loader)
    assert days is None


def test_days_since_last_match_pit_naive_asof_raises(
    loader: SackmannLoader,
) -> None:
    naive = datetime(2025, 2, 1, 0, 0)
    with pytest.raises(LookaheadError):
        compute_days_since_last_match("210001", naive, loader=loader)


def test_days_since_last_match_pit_boundary_excludes_future_match(
    loader: SackmannLoader,
) -> None:
    """asof = 2024-07-01 hides the 2025-01-15 match; falls back to
    2024-06-05 (the Clay loss). 26 days difference."""
    asof = datetime(2024, 7, 1, 0, 0, tzinfo=UTC)
    days = compute_days_since_last_match("210001", asof, loader=loader)
    assert days == 26


# ---------------------------------------------------------------------------
# Module-level sanity — exports + docstring discipline
# ---------------------------------------------------------------------------


def test_all_public_functions_have_docstrings() -> None:
    """Reviewer-friendly: every public surface carries a non-trivial
    docstring."""
    from agent.engines import tennis_technical as mod

    for name in (
        "compute_elo_diff",
        "compute_surface_advantage",
        "compute_h2h",
        "compute_best_of_factor",
        "compute_days_since_last_match",
    ):
        fn = getattr(mod, name)
        assert fn.__doc__ is not None
        assert len(fn.__doc__.strip()) > 20, (
            f"{name} docstring is too short to be useful"
        )
