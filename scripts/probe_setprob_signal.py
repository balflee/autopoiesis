"""A18 probe: can the MATCH-WINNER consensus (bookmaker) predict the FIRST-SET
outcome better than Polymarket's own (thin) first-set price?

Idea (user, 2026-06-14): Polymarket's tradeable tennis liquidity is in FIRST-SET
markets, but the rich/efficient data is in the MATCH-WINNER world (Pinnacle /
bookmaker consensus). P(win set) is mathematically derivable from P(win match),
so the match-winner consensus -> implied P(first set) is an external reference
for the first-set market. If that implied first-set prob beats Polymarket's
first-set CLOSING price (Brier), there is a public-info, BACKTESTABLE edge on the
market we already trade.

Per first-set cassette (closing price + first-set outcome from the cassette):
  - entity-match to a tennis-data row (same players + date)
  - de-vig the match-winner odds for the reference player -> P(match)
  - match_to_set_prob(P(match), best_of) -> implied P(first set)
  - edge_i = (p_polymarket - y)^2 - (implied - y)^2   (+ve => match-implied beats
    the Polymarket first-set price); cluster bootstrap + SESOI three-state.

Read-only. First-set closing price comes from the cassette's captured-live ledger
(cut at gameStartTime); Gamma/CLOB are refetched only for orientation +
gameStartTime (same fail-closed gates as A17). Run from the repo root (.../code).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.cached_sweep import load_rows
from agent.backtest.sharp_line import (
    DropBuckets,
    brier_edge,
    cluster_bootstrap_ci,
    implied_prob_two_way,
    iso_week_key,
    match_to_set_prob,
    resolve_2b_close,
    surname_matches,
    tennis_data_surname,
    three_state_verdict,
)

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_MARKET = "https://clob.polymarket.com/markets"
CASSETTE = Path("agent/backtest/_cache_tennis")
_HEAD = {"User-Agent": "Mozilla/5.0 (autopoiesis-a18-probe)"}
_VS = re.compile(r"([A-Za-z]+)-vs-([A-Za-z]+)$", re.IGNORECASE)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _gj(url: str) -> Any:
    r = requests.get(url, headers=_HEAD, timeout=30)
    r.raise_for_status()
    return r.json()


def _norm(s: str) -> str:
    d = unicodedata.normalize("NFKD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


class GammaHTTP:
    def __init__(self, sleep_s: float = 0.0) -> None:
        self.sleep = sleep_s
        self.errors = 0

    def get(self, mid: str) -> list[dict[str, object]] | None:
        try:
            p = _gj(f"{GAMMA}/{mid}")
        except Exception:
            self.errors += 1
            return None
        markets = p if isinstance(p, list) else [p]
        out = [self._enrich(m) for m in markets if isinstance(m, dict)]
        if self.sleep:
            time.sleep(self.sleep)
        return out or None

    def _enrich(self, m: dict[str, object]) -> dict[str, object]:
        raw = dict(m)
        raw["outcomes"] = _loads(raw.get("outcomes"))
        raw["outcomePrices"] = _loads(raw.get("outcomePrices"))
        raw["clobTokenIds"] = _loads(raw.get("clobTokenIds"))
        cond = raw.get("conditionId")
        if cond:
            try:
                cm = _gj(f"{CLOB_MARKET}/{cond}")
                if isinstance(cm, dict) and isinstance(cm.get("tokens"), list):
                    raw["tokens"] = cm["tokens"]
            except Exception:
                pass
        return raw


class _Pre:
    def __init__(self, raw: dict[str, object]) -> None:
        self.raw = raw

    def get(self, mid: str) -> list[dict[str, object]] | None:
        return [self.raw]


class CassetteClob:
    def __init__(self, clob_ids: object, ledger: list[tuple[int, float]]) -> None:
        ids = clob_ids if isinstance(clob_ids, list) else []
        self.t0 = str(ids[0]) if len(ids) >= 1 else None
        self.t1 = str(ids[1]) if len(ids) >= 2 else None
        self.led = ledger

    def prices_history(self, token_id: str, *, start_ts: int | None, end_ts: int | None) -> list[dict[str, object]]:
        t = str(token_id)
        if t == self.t0:
            return [{"t": ts, "p": m} for ts, m in self.led]
        if t == self.t1:
            return [{"t": ts, "p": 1.0 - m} for ts, m in self.led]
        return []


def _ledger(mid: str) -> list[tuple[int, float]]:
    p = CASSETTE / f"{mid}.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    out = []
    for pt in d.get("price_ledger", []):
        if isinstance(pt, dict) and pt.get("ts") and pt.get("mid_price") is not None:
            try:
                u = int(datetime.fromisoformat(str(pt["ts"]).replace("Z", "+00:00")).timestamp())
                out.append((u, float(pt["mid_price"])))
            except (ValueError, TypeError):
                continue
    return out


def _load_td(years: list[int], raw_dir: Path) -> dict[frozenset[str], list[dict[str, Any]]]:
    import pandas as pd

    idx: dict[frozenset[str], list[dict[str, Any]]] = {}
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
                idx.setdefault(frozenset({w, loser}), []).append(r)
    return idx


def _td_date(row: dict[str, Any]) -> datetime | None:
    import pandas as pd

    try:
        ts = pd.to_datetime(row.get("Date"), dayfirst=True, errors="coerce")
        return None if ts is None or pd.isna(ts) else ts.to_pydatetime()
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025, 2026])
    ap.add_argument("--raw-dir", type=Path, default=Path("reports/a17/raw"))
    ap.add_argument("--out", type=Path, default=Path("reports/a17/setprob_report.json"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--sesoi-brier", type=float, default=0.002)
    ap.add_argument("--max-tick-age-h", type=float, default=24.0)
    ap.add_argument("--min-ledger-ticks", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offline-only", action="store_true")
    ap.add_argument("--sleep-s", type=float, default=0.05)
    ap.add_argument(
        "--progress-file",
        type=Path,
        default=Path("reports/a17/_a18_progress.txt"),
        help="live progress is rewritten here (flushed) — Read it anytime",
    )
    args = ap.parse_args(argv)

    rows = load_rows(Path("reports/backtest/_signal_rows.json"))
    fs = [r for r in rows if "first-set-winner" in r.slug]
    td = _load_td(args.years, args.raw_dir)
    print(f"first-set cassettes: {len(fs)}; tennis-data surname-pairs: {len(td)}", flush=True)

    # offline entity match by surname-pair + slug date
    matched = []
    for r in fs:
        m = _VS.search(r.slug)
        dm = _DATE.search(r.slug)
        if not m or not dm:
            continue
        pair = frozenset({_norm(m.group(1)), _norm(m.group(2))})
        if pair not in td:
            continue
        sdate = datetime.fromisoformat(dm.group(1))
        cands = [
            row for row in td[pair]
            if (_td_date(row) is not None and abs((_td_date(row) - sdate).days) <= 1)  # type: ignore[operator]
        ]
        if len(cands) == 1:
            matched.append((r, cands[0]))
    print(f"offline entity-matched (unique surname-pair+date): {len(matched)}", flush=True)
    if args.offline_only:
        return 0
    if args.limit:
        matched = matched[: args.limit]

    gamma = GammaHTTP(sleep_s=args.sleep_s)
    buckets = DropBuckets()
    edges: list[float] = []
    clusters: list[str] = []
    diffs: list[float] = []
    for i, (fs_row, td_row) in enumerate(matched, 1):
        raw_list = gamma.get(fs_row.market_id)
        if not raw_list:
            buckets.add("gamma_missing")
            continue
        if len(raw_list) > 1:
            buckets.add("gamma_duplicate")
            continue
        raw = raw_list[0]
        led = _ledger(fs_row.market_id)
        if len(led) < args.min_ledger_ticks:
            buckets.add("sparse_ledger")
            continue
        close = resolve_2b_close(
            market_id=fs_row.market_id,
            cassette_outcome=fs_row.outcome,
            gamma_index=_Pre(raw),
            clob=CassetteClob(raw.get("clobTokenIds"), led),
            expected_slug=fs_row.slug,
            max_tick_age_h=args.max_tick_age_h,
        )
        if not close.ok:
            buckets.add(close.reason or "unknown")
            continue
        ref = close.reference_display or ""
        w_sn = tennis_data_surname(str(td_row.get("Winner", "")))
        l_sn = tennis_data_surname(str(td_row.get("Loser", "")))
        # match-winner CONSENSUS for the reference (first-set YES) player:
        # AvgW/AvgL = the market-average two-way odds (100% filled across years;
        # Pinnacle PS is only ~5-8% filled for 2026, so Avg is the right
        # "match-winner consensus" source and recovers the full sample).
        if w_sn and surname_matches(w_sn, ref):
            p_match = implied_prob_two_way(td_row.get("AvgW"), td_row.get("AvgL"))
        elif l_sn and surname_matches(l_sn, ref):
            p_match = implied_prob_two_way(td_row.get("AvgL"), td_row.get("AvgW"))
        else:
            buckets.add("ref_not_in_td")
            continue
        if p_match is None:
            buckets.add("no_match_odds")
            continue
        try:
            best_of = int(td_row.get("Best of") or 3)
        except (TypeError, ValueError):
            best_of = 3
        implied = match_to_set_prob(p_match, best_of=best_of)
        p_pm = close.p_polymarket
        if p_pm is None or close.reference_won is None:
            buckets.add("close_incomplete")
            continue
        y = 1 if close.reference_won else 0
        edges.append(brier_edge(p_pin=implied, p_soft=p_pm, y=y))  # +ve => implied beats pm
        clusters.append(iso_week_key(td_row.get("Tournament"), td_row.get("Date")))
        diffs.append(implied - p_pm)
        if i % 25 == 0:
            msg = f"  {i}/{len(matched)} scored {len(edges)} drops {buckets.total()}"
            print(msg, flush=True)
            try:
                args.progress_file.parent.mkdir(parents=True, exist_ok=True)
                args.progress_file.write_text(msg.strip() + "\n", encoding="utf-8")
            except OSError:
                pass

    rng: Any = np.random.default_rng(args.seed)
    ci = cluster_bootstrap_ci(edges, clusters, rng=rng, n_boot=args.bootstrap)
    verdict = three_state_verdict(ci, sesoi=args.sesoi_brier)
    big = sum(1 for d in diffs if abs(d) >= 0.05)
    report = {
        "probe": "A18 match-winner-consensus -> implied first-set prob vs Polymarket first-set price",
        "offline_matched": len(matched),
        "scored": len(edges),
        "drop_buckets": buckets.counts,
        "edge_brier": {
            "n": ci.n, "n_clusters": ci.n_clusters, "point": ci.point,
            "cluster_ci": [ci.lo, ci.hi], "iid_ci": [ci.iid_lo, ci.iid_hi],
        },
        "sesoi": args.sesoi_brier,
        "verdict": verdict,
        "implied_vs_pm_meanabs": (sum(abs(d) for d in diffs) / len(diffs)) if diffs else 0.0,
        "implied_vs_pm_disagree_ge_0.05": big,
        "note": "+ve edge = match-consensus-implied first-set prob is MORE accurate "
                "than Polymarket's first-set closing price (= public-info edge on first-set).",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    print(f"\n[A18] verdict={verdict} (n={ci.n}/{ci.n_clusters} clusters, "
          f"point={ci.point:+.5f}, ci=[{ci.lo:+.5f},{ci.hi:+.5f}])", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
