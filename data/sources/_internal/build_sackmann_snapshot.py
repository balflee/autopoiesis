"""Build deterministic Sackmann snapshot CSVs for the offline test corpus.

Run with ``python data/sources/_internal/build_sackmann_snapshot.py`` from
the repo root; re-runnable to regenerate. (Direct-script invocation avoids
the pre-existing ``data.sources.__init__`` import cascade — the generator
has zero in-repo dependencies, so it's safe to bypass package init.)
The output goes to ``data/sources/sackmann_snapshot/`` and is committed to
the repo as the vendored corpus per
``data/sources/sackmann_snapshot/README.md``.

These rows are synthetic but follow Jeff Sackmann's canonical CSV header
verbatim (see https://github.com/JeffSackmann/tennis_atp for the column
spec). Player IDs are 6-digit integers in the Sackmann namespace; we use
a contiguous synthetic range (200000+) so we never collide with real
Sackmann IDs.

Why a generator under ``data/sources/_internal/`` instead of hand-edited
CSVs? Snapshot rows are mechanical permutations of a small player pool +
tournament-date schedule; vendoring the generator means a reviewer can
re-derive the corpus bit-for-bit when bumping the snapshot. The literal
CSVs stay committed (so tests work without running the generator); the
generator is the *source of truth* for HOW they were derived.
"""
from __future__ import annotations

from pathlib import Path

# data/sources/_internal/<this file>.py → data/sources/sackmann_snapshot/
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "sackmann_snapshot"

MATCH_COLUMNS = [
    "tourney_id", "tourney_name", "surface", "draw_size", "tourney_level",
    "tourney_date", "match_num", "winner_id", "winner_seed", "winner_entry",
    "winner_name", "winner_hand", "winner_ht", "winner_ioc", "winner_age",
    "loser_id", "loser_seed", "loser_entry", "loser_name", "loser_hand",
    "loser_ht", "loser_ioc", "loser_age", "score", "best_of", "round",
    "minutes", "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
    "w_SvGms", "w_bpSaved", "w_bpFaced", "l_ace", "l_df", "l_svpt",
    "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced",
    "winner_rank", "winner_rank_points", "loser_rank", "loser_rank_points",
]

RANKING_COLUMNS = ["ranking_date", "rank", "player", "points"]


def _atp_tournaments_2024() -> list[tuple[str, str, str, str, str, int]]:
    """(tourney_id, name, surface, level, start_date_YYYYMMDD, best_of)."""
    return [
        ("2024-580", "Australian Open", "Hard", "G", "20240114", 5),
        ("2024-301", "Indian Wells Masters", "Hard", "M", "20240306", 3),
        ("2024-403", "Miami Open", "Hard", "M", "20240320", 3),
        ("2024-410", "Monte Carlo Masters", "Clay", "M", "20240407", 3),
        ("2024-540", "Roland Garros", "Clay", "G", "20240526", 5),
        ("2024-610", "Wimbledon", "Grass", "G", "20240701", 5),
        ("2024-560", "US Open", "Hard", "G", "20240826", 5),
    ]


def _wta_tournaments_2024() -> list[tuple[str, str, str, str, str, int]]:
    return [
        ("2024-W580", "Australian Open", "Hard", "G", "20240114", 3),
        ("2024-W301", "Indian Wells", "Hard", "P1000", "20240306", 3),
        ("2024-W403", "Miami Open", "Hard", "P1000", "20240320", 3),
        ("2024-W540", "Roland Garros", "Clay", "G", "20240526", 3),
        ("2024-W610", "Wimbledon", "Grass", "G", "20240701", 3),
        ("2024-W560", "US Open", "Hard", "G", "20240826", 3),
    ]


def _atp_tournaments_2025() -> list[tuple[str, str, str, str, str, int]]:
    return [
        ("2025-580", "Australian Open", "Hard", "G", "20250112", 5),
        ("2025-301", "Indian Wells Masters", "Hard", "M", "20250305", 3),
        ("2025-410", "Monte Carlo Masters", "Clay", "M", "20250406", 3),
        ("2025-540", "Roland Garros", "Clay", "G", "20250525", 5),
        ("2025-610", "Wimbledon", "Grass", "G", "20250630", 5),
    ]


def _wta_tournaments_2025() -> list[tuple[str, str, str, str, str, int]]:
    return [
        ("2025-W580", "Australian Open", "Hard", "G", "20250112", 3),
        ("2025-W301", "Indian Wells", "Hard", "P1000", "20250305", 3),
        ("2025-W540", "Roland Garros", "Clay", "G", "20250525", 3),
        ("2025-W610", "Wimbledon", "Grass", "G", "20250630", 3),
    ]


ATP_PLAYERS = [
    (210001, "Jannik Sinner", "R", 188, "ITA"),
    (210002, "Carlos Alcaraz", "R", 183, "ESP"),
    (210003, "Alexander Zverev", "R", 198, "GER"),
    (210004, "Daniil Medvedev", "R", 198, "RUS"),
    (210005, "Novak Djokovic", "R", 188, "SRB"),
    (210006, "Andrey Rublev", "R", 188, "RUS"),
    (210007, "Casper Ruud", "R", 183, "NOR"),
    (210008, "Hubert Hurkacz", "R", 196, "POL"),
    (210009, "Stefanos Tsitsipas", "R", 193, "GRE"),
    (210010, "Taylor Fritz", "R", 196, "USA"),
    (210011, "Holger Rune", "R", 188, "DEN"),
    (210012, "Tommy Paul", "R", 188, "USA"),
    (210013, "Grigor Dimitrov", "R", 191, "BUL"),
    (210014, "Alex de Minaur", "R", 183, "AUS"),
    (210015, "Ben Shelton", "L", 193, "USA"),
    (210016, "Frances Tiafoe", "R", 188, "USA"),
]

WTA_PLAYERS = [
    (220001, "Iga Swiatek", "R", 176, "POL"),
    (220002, "Aryna Sabalenka", "R", 182, "BLR"),
    (220003, "Coco Gauff", "R", 175, "USA"),
    (220004, "Elena Rybakina", "R", 184, "KAZ"),
    (220005, "Jessica Pegula", "R", 170, "USA"),
    (220006, "Jasmine Paolini", "R", 163, "ITA"),
    (220007, "Qinwen Zheng", "R", 178, "CHN"),
    (220008, "Emma Navarro", "R", 170, "USA"),
    (220009, "Madison Keys", "R", 178, "USA"),
    (220010, "Daria Kasatkina", "R", 170, "RUS"),
    (220011, "Barbora Krejcikova", "R", 178, "CZE"),
    (220012, "Beatriz Haddad Maia", "L", 185, "BRA"),
    (220013, "Mirra Andreeva", "R", 178, "RUS"),
    (220014, "Diana Shnaider", "L", 184, "RUS"),
]


def _row(
    *,
    tourney_id: str,
    tourney_name: str,
    surface: str,
    level: str,
    start_date: str,
    best_of: int,
    match_num: int,
    winner: tuple[int, str, str, int, str],
    loser: tuple[int, str, str, int, str],
    round_label: str,
    days_from_start: int,
    minutes: int,
    score: str,
    winner_rank: int,
    loser_rank: int,
) -> list[str]:
    """Synthesise one Sackmann match row. ``tourney_date`` is the
    *start* of the tournament (Sackmann convention); per-match offsets
    inside the tournament are carried by ``round`` + ``match_num``.

    For PIT-correctness the loader derives ``match_start_time`` as
    ``tourney_date + days_from_start days`` so each row's
    ``asof_ts < match_start_time`` strictly.
    """
    # Sackmann stat columns: empty strings when unknown (matches real Sackmann
    # CSV behaviour where some retired-mid-match games have NA stat cells).
    w_id, w_name, w_hand, w_ht, w_ioc = winner
    l_id, l_name, l_hand, l_ht, l_ioc = loser
    return [
        tourney_id, tourney_name, surface, "128" if level == "G" else "64",
        level, start_date, str(match_num),
        str(w_id), "", "", w_name, w_hand, str(w_ht), w_ioc, "25.0",
        str(l_id), "", "", l_name, l_hand, str(l_ht), l_ioc, "26.0",
        score, str(best_of), round_label, str(minutes),
        "8", "2", "60", "40", "30", "15", "10", "3", "5",
        "6", "3", "55", "35", "26", "12", "9", "2", "6",
        str(winner_rank), str(2000 - winner_rank * 10),
        str(loser_rank), str(1500 - loser_rank * 8),
    ]


def _build_match_file(
    tournaments: list[tuple[str, str, str, str, str, int]],
    players: list[tuple[int, str, str, int, str]],
    *,
    include_missing_player_id_row: bool = False,
) -> list[list[str]]:
    """Emit ~30 rows: 5 matches per tournament, drawing from the player pool.

    The match grid is deterministic: tournament i + match j picks
    winner = players[(i*5 + j) % N] and loser = players[(i*5 + j + 7) % N],
    so we never have a self-match (offset of 7 is coprime with our pool
    sizes 14/16).
    """
    rows: list[list[str]] = []
    rounds = ["R128", "R64", "R32", "R16", "QF", "SF", "F"]
    for ti, (tid, tname, surf, lvl, sdate, bo) in enumerate(tournaments):
        for j in range(5):
            wi = (ti * 5 + j) % len(players)
            li = (ti * 5 + j + 7) % len(players)
            w = players[wi]
            lo = players[li]
            rows.append(_row(
                tourney_id=tid, tourney_name=tname, surface=surf, level=lvl,
                start_date=sdate, best_of=bo, match_num=j + 1,
                winner=w, loser=lo,
                round_label=rounds[j % len(rounds)],
                days_from_start=j,
                minutes=110 + (j * 7) % 80,
                score="6-4 6-3" if bo == 3 else "6-4 6-3 7-6(5)",
                winner_rank=1 + wi,
                loser_rank=1 + li,
            ))
    if include_missing_player_id_row and tournaments:
        # One row with empty winner_id — the loader must drop it (or surface
        # it explicitly per the test's expectation).
        tid, tname, surf, lvl, sdate, bo = tournaments[0]
        row = _row(
            tourney_id=tid, tourney_name=tname, surface=surf, level=lvl,
            start_date=sdate, best_of=bo, match_num=99,
            winner=players[0], loser=players[1],
            round_label="R128", days_from_start=0, minutes=90,
            score="6-0 6-0", winner_rank=1, loser_rank=2,
        )
        # Blank out winner_id at column index 7.
        row[7] = ""
        rows.append(row)
    return rows


def _build_ranking_rows(
    players: list[tuple[int, str, str, int, str]],
    *,
    ranking_dates: list[str],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for d in ranking_dates:
        for rank, p in enumerate(players, start=1):
            pid, _name, _h, _ht, _ioc = p
            rows.append([d, str(rank), str(pid), str(10000 - rank * 100)])
    return rows


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)]
    lines.extend(",".join(r) for r in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(
        SNAPSHOT_DIR / "atp_matches_2024.csv", MATCH_COLUMNS,
        _build_match_file(
            _atp_tournaments_2024(), ATP_PLAYERS,
            include_missing_player_id_row=True,
        ),
    )
    _write_csv(
        SNAPSHOT_DIR / "atp_matches_2025.csv", MATCH_COLUMNS,
        _build_match_file(_atp_tournaments_2025(), ATP_PLAYERS),
    )
    _write_csv(
        SNAPSHOT_DIR / "wta_matches_2024.csv", MATCH_COLUMNS,
        _build_match_file(_wta_tournaments_2024(), WTA_PLAYERS),
    )
    _write_csv(
        SNAPSHOT_DIR / "wta_matches_2025.csv", MATCH_COLUMNS,
        _build_match_file(_wta_tournaments_2025(), WTA_PLAYERS),
    )
    _write_csv(
        SNAPSHOT_DIR / "atp_rankings_current.csv", RANKING_COLUMNS,
        _build_ranking_rows(
            ATP_PLAYERS,
            ranking_dates=["20240101", "20240701", "20250101"],
        ),
    )
    _write_csv(
        SNAPSHOT_DIR / "wta_rankings_current.csv", RANKING_COLUMNS,
        _build_ranking_rows(
            WTA_PLAYERS,
            ranking_dates=["20240101", "20240701", "20250101"],
        ),
    )
    print(f"Wrote 6 snapshot CSVs to {SNAPSHOT_DIR}")


if __name__ == "__main__":
    main()
