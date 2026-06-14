"""scripts/setprob_augment.py — augment _signal_rows.json with cross-market signal.

Reads ``reports/backtest/_signal_rows.json`` (the first-set universe, ~4925
rows) and emits **three** row files:

``_signal_rows_v4.json`` (active)
    Real ``cross_market_signal`` per matched row. Unmatched rows carry 0.0.

``_signal_rows_v4_neutral.json`` (neutral)
    Every ``cross_market_signal`` forced to 0.0; ``cluster_key`` preserved.
    Byte-identity anchor / rollback check — NOT the experimental placebo
    control.

``_signal_rows_v4_placebo.json`` (placebo)
    Signal values *permuted* among matched rows only (seed-deterministic).
    Preserves the matched/unmatched partition and the marginal multiset of
    signal values; destroys the alignment between a row's signal and its
    outcome.  The journey driver (Task 7b) will call
    :func:`make_placebo_rows` for multiple seeds.

Entity matching is fully **offline**: surname-pair (from
``tennis_match_resolver.parse_slug`` → ordered p1/p2) + slug date ±1 day.
Orientation (which player is YES / slug-first) comes from ``parse_slug``;
``cross_market_signal.py`` enforces fail-closed → 0.0 when ambiguous.

**No look-ahead**: ``row.outcome`` / ``winning_price`` are never read to
construct the signal; they are carried through verbatim.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allow ``python scripts/setprob_augment.py`` from the repo root.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.cached_sweep import SignalRow, load_rows, save_rows
from agent.backtest.cross_market_signal import cross_market_signal as _cross_market_signal
from agent.backtest.sharp_line import iso_week_key, tennis_data_surname
from agent.backtest.tennis_match_resolver import parse_slug

# ---------------------------------------------------------------------------
# Date regex (reuses same pattern as probe_setprob_signal.py)
# ---------------------------------------------------------------------------

_DATE_RE: re.Pattern[str] = re.compile(r"(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# Drop-bucket label constants (exported so tests can reference them)
# ---------------------------------------------------------------------------

BUCKET_NO_SLUG_PARSE: str = "no_slug_parse"
"""Slug does not contain a ``-<P1>-vs-<P2>`` suffix (parse_slug returns None)."""

BUCKET_NO_DATE: str = "no_slug_date"
"""No ISO date (YYYY-MM-DD) found anywhere in the slug."""

BUCKET_PAIR_NOT_IN_TD: str = "pair_not_in_td"
"""The normalised surname pair is absent from the tennis-data index."""

BUCKET_DATE_NO_UNIQUE: str = "date_no_unique_match"
"""Zero or multiple td rows within ±1 day of the slug date."""

BUCKET_FAIL_CLOSED: str = "fail_closed"
"""Entity-matched but ``cross_market_signal()`` returned 0.0 (ambiguous
orientation or missing consensus odds)."""

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

#: ``frozenset{winner_surname, loser_surname}`` → list of tennis-data rows.
TdIndex = dict[frozenset[str], list[dict[str, Any]]]

# ---------------------------------------------------------------------------
# Tennis-data index loader
# ---------------------------------------------------------------------------


def load_td_index(years: list[int], raw_dir: Path) -> TdIndex:
    """Load tennis-data Excel files and build a surname-pair → rows index.

    Keyed by ``frozenset{tennis_data_surname(Winner), tennis_data_surname(Loser)}``,
    mirroring the approach in ``probe_setprob_signal._load_td``.  Rows with
    unparseable surnames are silently dropped.

    **Inject a pre-built dict in tests** to avoid hitting the file system.
    """
    import pandas as pd

    idx: TdIndex = {}
    for year in years:
        for tour in ("ATP", "WTA"):
            f = raw_dir / f"{year}_{tour}.xlsx"
            if not f.exists():
                continue
            df = pd.read_excel(io.BytesIO(f.read_bytes()))
            df = df.where(df.notna(), None)
            for r in df.to_dict(orient="records"):
                w = tennis_data_surname(str(r.get("Winner", "")))
                loser = tennis_data_surname(str(r.get("Loser", "")))
                if not w or not loser:
                    continue
                key: frozenset[str] = frozenset({w, loser})
                idx.setdefault(key, []).append(r)
    return idx


# ---------------------------------------------------------------------------
# Per-row date parsing (no pandas in the hot loop)
# ---------------------------------------------------------------------------


def _td_date(row: dict[str, Any]) -> datetime | None:
    """Parse the tennis-data ``Date`` field to an aware UTC :class:`datetime`.

    Handles:
    * ``pandas.Timestamp`` (produced by ``df.to_dict(orient="records")``)
    * ISO date string ``YYYY-MM-DD``
    * ``DD/MM/YYYY`` string
    * ``YYYY-MM-DD HH:MM:SS`` string (pandas Timestamp ``str()``)

    Returns ``None`` on failure so callers can skip the row.
    """
    val = row.get("Date")
    if val is None:
        return None
    # pandas Timestamp has a .to_pydatetime() method
    if hasattr(val, "to_pydatetime"):
        try:
            dt: datetime = val.to_pydatetime()
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
        except Exception:
            return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Core augmentation (pure — no I/O, injectable td_index for tests)
# ---------------------------------------------------------------------------


def augment_rows(
    rows: list[SignalRow],
    td_index: TdIndex,
) -> tuple[list[SignalRow], dict[str, int]]:
    """Augment each :class:`SignalRow` with ``cross_market_signal`` and
    ``cluster_key``.  Pure function — no file I/O.

    **Matched** (entity-match found by surname-pair + date ±1 day):
      ``cluster_key = iso_week_key(td.Tournament, td.Date)``
      ``cross_market_signal`` = real signal (may be ``0.0`` if fail-closed).

    **Unmatched** (any gate fails):
      ``cross_market_signal = 0.0``, ``cluster_key = ""``  (sentinel).
      Downstream GO-CI excludes rows with empty ``cluster_key``.

    No look-ahead: ``row.outcome`` / ``winning_price`` are carried through
    verbatim and never read here.

    Parameters
    ----------
    rows:
        Input :class:`SignalRow` list (e.g. from :func:`load_rows`).
    td_index:
        Pre-built surname-pair index (use :func:`load_td_index` for production;
        inject a fake dict in offline unit tests).

    Returns
    -------
    ``(augmented_rows, bucket_counts)`` where ``bucket_counts`` tallies drops
    by reason.
    """
    buckets: dict[str, int] = {}
    result: list[SignalRow] = []

    for row in rows:
        # Gate 1 — slug must parse to ordered surnames.
        parsed = parse_slug(row.slug)
        if parsed is None:
            buckets[BUCKET_NO_SLUG_PARSE] = buckets.get(BUCKET_NO_SLUG_PARSE, 0) + 1
            result.append(replace(row, cross_market_signal=0.0, cluster_key=""))
            continue

        # Gate 2 — slug must contain an ISO date.
        dm = _DATE_RE.search(row.slug)
        if not dm:
            buckets[BUCKET_NO_DATE] = buckets.get(BUCKET_NO_DATE, 0) + 1
            result.append(replace(row, cross_market_signal=0.0, cluster_key=""))
            continue

        # Gate 3 — surname pair must be in the tennis-data index.
        # parse_slug normalises via _norm_surname (strip accents, lowercase),
        # which for purely-alpha slug tokens is equivalent to normalize_name
        # used by tennis_data_surname — both yield the same lowercase key.
        pair: frozenset[str] = frozenset({parsed.p1_surname, parsed.p2_surname})
        if pair not in td_index:
            buckets[BUCKET_PAIR_NOT_IN_TD] = buckets.get(BUCKET_PAIR_NOT_IN_TD, 0) + 1
            result.append(replace(row, cross_market_signal=0.0, cluster_key=""))
            continue

        # Gate 4 — exactly one td row within ±1 day.
        sdate = datetime.fromisoformat(dm.group(1)).replace(tzinfo=UTC)
        cands: list[dict[str, Any]] = []
        for td_row in td_index[pair]:
            td_dt = _td_date(td_row)
            if td_dt is not None and abs((td_dt - sdate).days) <= 1:
                cands.append(td_row)

        if len(cands) != 1:
            buckets[BUCKET_DATE_NO_UNIQUE] = (
                buckets.get(BUCKET_DATE_NO_UNIQUE, 0) + 1
            )
            result.append(replace(row, cross_market_signal=0.0, cluster_key=""))
            continue

        matched_td = cands[0]

        # Cluster key: pre-computed from td.Tournament + td.Date so the GO-CI
        # driver can cluster-bootstrap per-row PnL deltas without re-joining.
        ck = iso_week_key(matched_td.get("Tournament"), matched_td.get("Date"))

        # Signal (fail-closed to 0.0 by cross_market_signal itself when
        # orientation is ambiguous or consensus odds are missing).
        sig = _cross_market_signal(
            slug_first_surname=parsed.p1_surname,
            td_row=matched_td,
        )
        if sig == 0.0:
            buckets[BUCKET_FAIL_CLOSED] = buckets.get(BUCKET_FAIL_CLOSED, 0) + 1

        result.append(replace(row, cross_market_signal=sig, cluster_key=ck))

    return result, buckets


# ---------------------------------------------------------------------------
# Neutral variant (byte-identity anchor)
# ---------------------------------------------------------------------------


def make_neutral_rows(rows: list[SignalRow]) -> list[SignalRow]:
    """Return a copy of *rows* with every ``cross_market_signal`` set to ``0.0``.

    ``cluster_key`` is **preserved** so downstream callers can still use the
    cluster structure (e.g. to verify the matched-row partition).  This is a
    byte-identity rollback anchor, **not** the experimental placebo control.
    """
    return [replace(row, cross_market_signal=0.0) for row in rows]


# ---------------------------------------------------------------------------
# Placebo variant (permutation-based control)
# ---------------------------------------------------------------------------


def make_placebo_rows(rows: list[SignalRow], *, seed: int) -> list[SignalRow]:
    """Permute ``cross_market_signal`` values among *matched* rows.

    **Matched definition**: ``cluster_key != ""``.  Unmatched rows
    (``cluster_key == ""``) are never touched — they retain ``0.0``.

    The permutation:

    * is **deterministic** per ``seed``;
    * **preserves the matched/unmatched partition** — the same rows that carry
      a signal in the active variant carry a (permuted) signal in the placebo;
    * **preserves the marginal multiset** of signal values over matched rows
      (it is a permutation, not a resample);
    * **destroys the alignment** between a row's signal value and that row's
      own outcome — so any ``κ_xm`` learned on placebo data reflects only
      winner's-curse / in-sample overfitting, not real predictive edge;
    * leaves ``cluster_key`` with its original row (not permuted).

    Different ``seed`` values generally produce different permutations.

    Parameters
    ----------
    rows:
        Augmented :class:`SignalRow` list (typically the output of
        :func:`augment_rows`).
    seed:
        Integer seed for :class:`numpy.random.Generator` (via
        ``numpy.random.default_rng``).

    Returns
    -------
    A new list of :class:`SignalRow` objects; the input is not mutated.
    """
    import numpy as np

    rng = np.random.default_rng(seed)

    matched_idx = [i for i, r in enumerate(rows) if r.cluster_key != ""]
    if not matched_idx:
        return list(rows)  # nothing to permute; return a shallow copy

    matched_sigs = [rows[i].cross_market_signal for i in matched_idx]
    permuted: list[float] = [float(v) for v in rng.permutation(matched_sigs)]

    # Build result starting from a shallow copy (unmatched rows reused as-is).
    result: list[SignalRow] = list(rows)
    for idx, new_sig in zip(matched_idx, permuted, strict=True):
        result[idx] = replace(rows[idx], cross_market_signal=new_sig)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_DEFAULT_ROWS_IN = Path("reports/backtest/_signal_rows.json")
_DEFAULT_ROWS_V4 = Path("reports/backtest/_signal_rows_v4.json")
_DEFAULT_ROWS_NEUTRAL = Path("reports/backtest/_signal_rows_v4_neutral.json")
_DEFAULT_ROWS_PLACEBO = Path("reports/backtest/_signal_rows_v4_placebo.json")
_DEFAULT_YEARS = [2024, 2025, 2026]
_DEFAULT_RAW_DIR = Path("reports/a17/raw")
_DEFAULT_PLACEBO_SEED = 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Augment _signal_rows.json with cross-market signal (B′ κ_xm). "
            "Emits active / neutral / placebo row files."
        )
    )
    ap.add_argument(
        "--rows-in",
        type=Path,
        default=_DEFAULT_ROWS_IN,
        help="Input signal rows JSON (default: %(default)s).",
    )
    ap.add_argument(
        "--rows-v4",
        type=Path,
        default=_DEFAULT_ROWS_V4,
        help="Output active rows (default: %(default)s).",
    )
    ap.add_argument(
        "--rows-neutral",
        type=Path,
        default=_DEFAULT_ROWS_NEUTRAL,
        help="Output neutral rows (default: %(default)s).",
    )
    ap.add_argument(
        "--rows-placebo",
        type=Path,
        default=_DEFAULT_ROWS_PLACEBO,
        help="Output placebo rows (default: %(default)s).",
    )
    ap.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=_DEFAULT_YEARS,
        help="Tennis-data years to load (default: %(default)s).",
    )
    ap.add_argument(
        "--raw-dir",
        type=Path,
        default=_DEFAULT_RAW_DIR,
        help="Directory containing {{year}}_{{ATP,WTA}}.xlsx files (default: %(default)s).",
    )
    ap.add_argument(
        "--placebo-seed",
        type=int,
        default=_DEFAULT_PLACEBO_SEED,
        help="RNG seed for the default placebo permutation (default: %(default)s).",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the augment script."""
    args = _build_parser().parse_args(argv)

    print(f"[augment] loading rows from {args.rows_in} …", flush=True)
    rows = load_rows(args.rows_in)
    print(f"[augment] {len(rows)} rows loaded", flush=True)

    print(f"[augment] loading tennis-data from {args.raw_dir} (years={args.years}) …",
          flush=True)
    td_index = load_td_index(args.years, args.raw_dir)
    print(f"[augment] {len(td_index)} surname-pairs in index", flush=True)

    print("[augment] running per-row augmentation …", flush=True)
    active, buckets = augment_rows(rows, td_index)
    matched = sum(1 for r in active if r.cluster_key != "")
    total = len(active)

    neutral = make_neutral_rows(active)
    placebo = make_placebo_rows(active, seed=args.placebo_seed)

    save_rows(active, args.rows_v4)
    save_rows(neutral, args.rows_neutral)
    save_rows(placebo, args.rows_placebo)

    report: dict[str, object] = {
        "total": total,
        "matched": matched,
        "match_rate": round(matched / total, 4) if total else 0.0,
        "drop_buckets": buckets,
        "out_active": str(args.rows_v4),
        "out_neutral": str(args.rows_neutral),
        "out_placebo": str(args.rows_placebo),
        "placebo_seed": args.placebo_seed,
    }
    print(json.dumps(report, indent=2), flush=True)
    print(
        f"\n[augment] done — matched {matched}/{total} "
        f"({report['match_rate']:.1%}) rows",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
