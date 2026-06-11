"""Jeff Sackmann tennis_atp / tennis_wta source-adapter — READ-ONLY.

Per PRD §7 the canonical tennis match-and-rankings dataset is Jeff
Sackmann's MIT-licensed GitHub corpus
(``JeffSackmann/tennis_atp`` + ``JeffSackmann/tennis_wta``,
1968-present). This module is the offline-first loader Track B's α₁
``tennis_technical`` engine and Track C's calibration replay both
consume.

Two-tier data path
------------------

1. **Snapshot-first** (``data/sources/sackmann_snapshot/``) — a
   vendored copy of the canonical CSV layout. No network. Always the
   primary read.
2. **GitHub raw fallback** — used only when the requested year is not
   in the snapshot. Wrapped by the shared
   :class:`data.sources._http.HttpClient` so the same exponential
   3-retry schedule the other Track E feeds use applies here too.

PRD §12 lists *"Sackmann CSV upstream 失效"* as a project risk; the
snapshot is the mitigation. The snapshot-first ordering means that
even when GitHub raw is throttled, down, or returns 503, the loader
still works for the vendored year range. Tests assert this property
explicitly.

Hard rules (per the brief and PRD §14.1)
----------------------------------------

* ``load_*_rankings`` requires a timezone-aware ``asof_date``. Naive →
  :class:`LookaheadError`. Rows past ``asof_date`` are filtered before
  return so the chokepoint never sees them.
* ``load_*_matches`` returns deterministic frames sorted by
  ``(tourney_date, match_num)``. The ``available_at`` column the
  parquet builder later attaches is derived from ``tourney_date`` —
  always **before** the live match start time (matches inside a
  tournament start at most 14 days after the tournament's listed
  start date).
* No write-side calls anywhere; pure read.
* No module-level network I/O — the HTTP client is constructed on
  demand.

The returned frames preserve the Sackmann column layout 1:1 — see
``MATCH_COLUMNS`` / ``RANKING_COLUMNS`` for the canonical schema. The
downstream ``data.etl.build_training_set`` is responsible for projecting
into the Phase 1 parquet shape.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from data.etl.pit_correct import LookaheadError, require_asof_ts
from data.sources._http import HttpClient

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    import pandas as pd

# Sackmann's canonical column order (sprint_7 snapshot is faithful to it).
MATCH_COLUMNS: tuple[str, ...] = (
    "tourney_id", "tourney_name", "surface", "draw_size", "tourney_level",
    "tourney_date", "match_num", "winner_id", "winner_seed", "winner_entry",
    "winner_name", "winner_hand", "winner_ht", "winner_ioc", "winner_age",
    "loser_id", "loser_seed", "loser_entry", "loser_name", "loser_hand",
    "loser_ht", "loser_ioc", "loser_age", "score", "best_of", "round",
    "minutes", "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
    "w_SvGms", "w_bpSaved", "w_bpFaced", "l_ace", "l_df", "l_svpt",
    "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced",
    "winner_rank", "winner_rank_points", "loser_rank", "loser_rank_points",
)

RANKING_COLUMNS: tuple[str, ...] = ("ranking_date", "rank", "player", "points")

# GitHub raw base URLs — only used when the snapshot path misses.
SACKMANN_ATP_BASE_URL = (
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
)
SACKMANN_WTA_BASE_URL = (
    "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"
)

# Default snapshot directory (relative to repo root). This holds the small
# SYNTHETIC, edge-case-bearing TEST fixtures (see
# ``data/sources/_internal/build_sackmann_snapshot.py``). It is the default the
# hermetic ETL test-suite reads. Tests can override via the ``snapshot_dir``
# parameter to point at a tmp_path.
DEFAULT_SNAPSHOT_DIR: Path = (
    Path(__file__).resolve().parent / "sackmann_snapshot"
)

# The REAL vendored Sackmann corpus (2024-2026), kept SEPARATE from the
# synthetic fixtures so re-vendoring (``scripts/vendor_sackmann_corpus.py``)
# never clobbers the test fixtures. The flag-on real-signal path constructs a
# ``SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)`` to read it offline.
DEFAULT_CORPUS_DIR: Path = (
    Path(__file__).resolve().parent / "sackmann_corpus"
)


class SackmannLoader:
    """Two-tier loader for the Jeff Sackmann tennis corpus.

    Constructor is cheap; the HTTP client is lazy and never contacted
    when the snapshot covers the requested year(s).
    """

    def __init__(
        self,
        *,
        snapshot_dir: Path | None = None,
        atp_base_url: str = SACKMANN_ATP_BASE_URL,
        wta_base_url: str = SACKMANN_WTA_BASE_URL,
        http: HttpClient | None = None,
    ) -> None:
        self._snapshot_dir = (
            snapshot_dir if snapshot_dir is not None else DEFAULT_SNAPSHOT_DIR
        )
        self._atp_base = atp_base_url.rstrip("/")
        self._wta_base = wta_base_url.rstrip("/")
        self._http = http if http is not None else HttpClient()

    @property
    def http(self) -> HttpClient:
        """Expose the underlying HttpClient so tests can inject a session."""
        return self._http

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir

    # ------------------------------------------------------------------
    # Public match loaders.
    # ------------------------------------------------------------------
    def load_atp_matches(self, year_range: tuple[int, int]) -> pd.DataFrame:
        """Return ATP singles matches for the inclusive ``year_range``."""
        return self._load_matches("atp", year_range, base_url=self._atp_base)

    def load_wta_matches(self, year_range: tuple[int, int]) -> pd.DataFrame:
        """Return WTA singles matches for the inclusive ``year_range``."""
        return self._load_matches("wta", year_range, base_url=self._wta_base)

    # ------------------------------------------------------------------
    # Public ranking loaders.
    # ------------------------------------------------------------------
    def load_atp_rankings(self, asof_date: datetime) -> pd.DataFrame:
        """Return ATP singles rankings ≤ ``asof_date`` (timezone-aware)."""
        return self._load_rankings("atp", asof_date, base_url=self._atp_base)

    def load_wta_rankings(self, asof_date: datetime) -> pd.DataFrame:
        """Return WTA singles rankings ≤ ``asof_date`` (timezone-aware)."""
        return self._load_rankings("wta", asof_date, base_url=self._wta_base)

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------
    def _load_matches(
        self,
        tour: str,
        year_range: tuple[int, int],
        *,
        base_url: str,
    ) -> pd.DataFrame:
        import pandas as pd

        start_year, end_year = year_range
        if start_year > end_year:
            raise ValueError(
                f"year_range={year_range!r} is empty (start > end)"
            )

        frames: list[pd.DataFrame] = []
        for year in range(start_year, end_year + 1):
            filename = f"{tour}_matches_{year}.csv"
            df = self._read_csv_two_tier(filename, base_url=base_url)
            if df.empty:
                continue
            # Tag the tour so the downstream join can distinguish ATP vs WTA
            # without re-deriving from the filename.
            df = df.copy()
            df["tour"] = tour
            frames.append(df)

        if not frames:
            # Defensive: empty result still has the canonical column set so
            # downstream `df[col]` reads don't KeyError.
            empty = pd.DataFrame(columns=[*MATCH_COLUMNS, "tour"])
            return empty

        joined = pd.concat(frames, ignore_index=True)
        # Stable deterministic sort — every reviewer who reads the parquet
        # gets the same row order.
        joined = joined.sort_values(
            by=["tourney_date", "match_num"], kind="stable"
        ).reset_index(drop=True)
        return joined

    def _load_rankings(
        self,
        tour: str,
        asof_date: datetime,
        *,
        base_url: str,
    ) -> pd.DataFrame:
        import pandas as pd

        cutoff = require_asof_ts(asof_date)

        filename = f"{tour}_rankings_current.csv"
        df = self._read_csv_two_tier(filename, base_url=base_url)
        if df.empty:
            return pd.DataFrame(columns=list(RANKING_COLUMNS))

        # ranking_date is Sackmann's YYYYMMDD integer-as-string. Parse into
        # tz-aware UTC datetime so the PIT compare is engine-consistent
        # with the other Track E feeds.
        parsed = pd.to_datetime(
            df["ranking_date"].astype(str), format="%Y%m%d", utc=True
        )
        # cutoff comes in tz-aware; pandas datetime64 comparison via Timestamp.
        cutoff_ts = pd.Timestamp(cutoff).tz_convert("UTC")
        mask = parsed <= cutoff_ts
        filtered = df.loc[mask].copy()
        filtered = filtered.sort_values(
            by=["ranking_date", "rank"], kind="stable"
        ).reset_index(drop=True)
        return filtered

    # ------------------------------------------------------------------
    # Two-tier read: snapshot → GitHub raw fallback.
    # ------------------------------------------------------------------
    def _read_csv_two_tier(
        self,
        filename: str,
        *,
        base_url: str,
    ) -> pd.DataFrame:
        """Snapshot-first read; GitHub raw fallback only on snapshot miss.

        Why snapshot-first? See module docstring + PRD §12 risk row. The
        invariant is: a successful snapshot read MUST mean we did not
        touch the network. Tests assert this by mocking GitHub raw to
        503 and verifying the loader still returns a valid frame.
        """
        import pandas as pd

        # Tier 1 — vendored snapshot.
        snapshot_path = self._snapshot_dir / filename
        if snapshot_path.exists():
            return pd.read_csv(snapshot_path, dtype=str, keep_default_na=False)

        # Tier 2 — GitHub raw. We download as text so we can build the
        # frame in a single pandas pass that doesn't need a tempfile.
        url = f"{base_url}/{filename}"
        try:
            resp = self._http.get(url, timeout=15.0)
        except requests.HTTPError as exc:
            # Translate to LookaheadError-adjacent semantics: caller knows
            # the year is genuinely unavailable rather than a transient.
            raise FileNotFoundError(
                f"Sackmann CSV {filename!r} not in snapshot and GitHub raw "
                f"returned {exc!r}. Vendor it under "
                f"{self._snapshot_dir} or wait for upstream to recover."
            ) from exc
        return pd.read_csv(io.StringIO(resp.text), dtype=str, keep_default_na=False)


# ---------------------------------------------------------------------------
# Module-level convenience entrypoints — preserve the brief's literal API
# surface. Internally they delegate to a default ``SackmannLoader``; tests
# that need to inject a snapshot dir or session use ``SackmannLoader``
# directly.
# ---------------------------------------------------------------------------


def load_atp_matches(year_range: tuple[int, int]) -> pd.DataFrame:
    """Default-loader entrypoint: ATP matches for ``year_range``."""
    return SackmannLoader().load_atp_matches(year_range)


def load_wta_matches(year_range: tuple[int, int]) -> pd.DataFrame:
    """Default-loader entrypoint: WTA matches for ``year_range``."""
    return SackmannLoader().load_wta_matches(year_range)


def load_atp_rankings(asof_date: datetime) -> pd.DataFrame:
    """Default-loader entrypoint: ATP rankings ≤ ``asof_date``."""
    return SackmannLoader().load_atp_rankings(asof_date)


def load_wta_rankings(asof_date: datetime) -> pd.DataFrame:
    """Default-loader entrypoint: WTA rankings ≤ ``asof_date``."""
    return SackmannLoader().load_wta_rankings(asof_date)


def require_valid_player_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows whose ``winner_id`` or ``loser_id`` are missing/empty.

    The Sackmann corpus occasionally has rows where one or both player
    IDs are blank (retired-before-record, qualifier-promoted-late, etc.).
    Those rows cannot participate in the rankings join the Phase 1
    training pipeline performs, so we strip them at the loader boundary
    and return a logically-valid frame. Caller can compare row counts
    to detect drops if needed.

    Raises :class:`LookaheadError` if the input frame lacks the
    ``winner_id`` / ``loser_id`` columns — that's a schema-shape bug
    upstream of this function (PIT keys are themselves schema metadata
    and we treat their absence the same way we treat ``available_at``
    absence in the join chokepoint).
    """
    if "winner_id" not in df.columns or "loser_id" not in df.columns:
        raise LookaheadError(
            "DataFrame is missing 'winner_id' / 'loser_id' columns — "
            "Sackmann player IDs are required for the rankings join "
            "(loader contract)."
        )
    w = df["winner_id"].astype(str).str.strip()
    l_ = df["loser_id"].astype(str).str.strip()
    keep = (w != "") & (l_ != "") & (w != "nan") & (l_ != "nan")
    return df.loc[keep].reset_index(drop=True)


__all__ = [
    "DEFAULT_CORPUS_DIR",
    "DEFAULT_SNAPSHOT_DIR",
    "MATCH_COLUMNS",
    "RANKING_COLUMNS",
    "SACKMANN_ATP_BASE_URL",
    "SACKMANN_WTA_BASE_URL",
    "SackmannLoader",
    "load_atp_matches",
    "load_atp_rankings",
    "load_wta_matches",
    "load_wta_rankings",
    "require_valid_player_ids",
]
