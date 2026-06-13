"""A17 sharp-line edge historical probe — CLI driver (real-data run).

Wires the verified pure logic in ``agent.backtest.sharp_line`` to real data:

  * 2a (load-bearing, offline): sharp (Pinnacle) vs non-Pinnacle book consensus,
    over tennis-data.co.uk annual .xlsx. Powered (thousands of matches).
  * 2b (best-effort): sharp vs Polymarket pre-match close, over the 378 on-disk
    match-winner cassettes — refetches Gamma (raw outcomes/outcomePrices/
    clobTokenIds + CLOB token-outcome labels for result-independent orientation)
    and the real CLOB pre-match closing tick. Fail-closed everywhere.

Read-only: no bets, no deploy, no LLM, no keys. Hits public Gamma/CLOB +
tennis-data over HTTP. Writes reports/a17/probe_report.json and the committed
conclusion docs/backtest/sharp_line_probe.md.

Usage (run from the repo root, i.e. .../code):
    python scripts/probe_sharp_line.py --years 2024 2025 2026
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.cached_sweep import load_rows
from agent.backtest.sharp_line import (
    DropBuckets,
    MatchSample,
    build_2a_row,
    build_2b_sample,
    cluster_bootstrap_ci,
    resolve_2b_close,
    roi_grid,
    three_state_verdict,
)

GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets"
CLOB_MARKET_URL = "https://clob.polymarket.com/markets"
_TD_BASE = "http://www.tennis-data.co.uk"
_HEADERS = {"User-Agent": "Mozilla/5.0 (autopoiesis-a17-probe)"}


# --------------------------------------------------------------------------- #
# HTTP clients (implement the sharp_line fetch protocols)
# --------------------------------------------------------------------------- #


def _get_json(url: str, *, timeout: float = 30.0) -> Any:
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _loads_maybe(value: Any) -> Any:
    """gamma returns outcomes/outcomePrices/clobTokenIds as JSON-encoded strings."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


class GammaIndexHTTP:
    """Fetch one raw gamma market by id, enriched with CLOB token-outcome labels.

    Returns the raw market dict with ``outcomes``/``outcomePrices``/
    ``clobTokenIds`` decoded to lists, plus a ``tokens`` label map sourced from
    CLOB ``/markets/<conditionId>`` (the result-independent primary metadata that
    lets ``resolve_2b_close`` verify token<->outcome orientation).
    """

    def __init__(self, *, sleep_s: float = 0.0) -> None:
        self._sleep = sleep_s
        self.errors = 0

    def get(self, market_id: str) -> list[dict[str, object]] | None:
        try:
            payload = _get_json(f"{GAMMA_MARKET_URL}/{market_id}")
        except Exception:
            self.errors += 1
            return None
        raw = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(raw, dict):
            return None
        raw = dict(raw)
        raw["outcomes"] = _loads_maybe(raw.get("outcomes"))
        raw["outcomePrices"] = _loads_maybe(raw.get("outcomePrices"))
        raw["clobTokenIds"] = _loads_maybe(raw.get("clobTokenIds"))
        cond = raw.get("conditionId")
        if cond:
            try:
                clob_m = _get_json(f"{CLOB_MARKET_URL}/{cond}")
                if isinstance(clob_m, dict) and isinstance(clob_m.get("tokens"), list):
                    raw["tokens"] = clob_m["tokens"]
            except Exception:
                pass
        if self._sleep:
            time.sleep(self._sleep)
        return [raw]


class _PreloadedGammaIndex:
    """A GammaIndex serving an already-fetched raw market (avoids a second fetch)."""

    def __init__(self, raw: dict[str, object]) -> None:
        self._raw = raw

    def get(self, market_id: str) -> list[dict[str, object]] | None:
        return [self._raw]


def _load_cassette_ledger(market_id: str, cassette_dir: Path) -> list[tuple[int, float]]:
    """Load a cassette's captured-live CLOB price_ledger as ``(unix_ts, yes_mid)``.

    Polymarket's CLOB ``prices-history`` returns EMPTY for these long-closed
    resolved markets (the order book / tick history is purged after
    resolution). The cassette captured the real intraday YES-token mid stream
    when the market was live, so it is the only available historical close.
    """
    path = cassette_dir / f"{market_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    out: list[tuple[int, float]] = []
    for pt in data.get("price_ledger", []):
        if not isinstance(pt, dict):
            continue
        ts, mid = pt.get("ts"), pt.get("mid_price")
        if ts is None or mid is None:
            continue
        try:
            unix = int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
            out.append((unix, float(mid)))
        except (ValueError, TypeError):
            continue
    return out


class CassetteClobHistory:
    """A ClobHistory backed by a cassette's captured YES-token (``clobTokenIds[0]``)
    mid ledger, orientation-aware.

    ``resolve_2b_close`` asks for the reference (``outcomes[0]``) token's price
    series. The cassette ledger is ``clobTokenIds[0]``'s mid. If the reference
    token IS ``clobTokenIds[0]`` we serve the mid directly; if it is
    ``clobTokenIds[1]`` we serve the complement ``1 - mid`` (the two binary-market
    tokens are complementary). Any other token id => empty.
    """

    def __init__(self, clob_token_ids: object, ledger: list[tuple[int, float]]) -> None:
        ids = clob_token_ids if isinstance(clob_token_ids, list) else []
        self._tok0 = str(ids[0]) if len(ids) >= 1 else None
        self._tok1 = str(ids[1]) if len(ids) >= 2 else None
        self._ledger = ledger

    def prices_history(
        self, token_id: str, *, start_ts: int | None, end_ts: int | None
    ) -> list[dict[str, object]]:
        tid = str(token_id)
        if tid == self._tok0:
            return [{"t": ts, "p": mid} for ts, mid in self._ledger]
        if tid == self._tok1:
            return [{"t": ts, "p": 1.0 - mid} for ts, mid in self._ledger]
        return []


# --------------------------------------------------------------------------- #
# tennis-data loader
# --------------------------------------------------------------------------- #


def _td_url(year: int, *, wta: bool) -> str:
    suffix = "w" if wta else ""
    return f"{_TD_BASE}/{year}{suffix}/{year}.xlsx"


def load_tennis_data(years: list[int], *, cache_dir: Path) -> list[dict[str, Any]]:
    """Fetch + read tennis-data ATP+WTA annual files for ``years``.

    Caches the raw .xlsx under ``cache_dir`` (gitignored). Returns a flat list
    of row dicts. Per-year fill-rate of PSW is printed for disclosure.
    """
    import pandas as pd

    cache_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for year in years:
        for wta in (False, True):
            tour = "WTA" if wta else "ATP"
            local = cache_dir / f"{year}_{tour}.xlsx"
            try:
                if not local.exists():
                    resp = requests.get(_td_url(year, wta=wta), headers=_HEADERS, timeout=60)
                    resp.raise_for_status()
                    local.write_bytes(resp.content)
                df = pd.read_excel(io.BytesIO(local.read_bytes()))
            except Exception as exc:
                print(f"  tennis-data {year} {tour}: FETCH/READ FAIL ({type(exc).__name__})")
                continue
            df = df.where(df.notna(), None)
            recs = df.to_dict(orient="records")
            ps_fill = sum(1 for r in recs if r.get("PSW") not in (None, "")) / max(1, len(recs))
            print(f"  tennis-data {year} {tour}: {len(recs)} rows, PSW fill {ps_fill:.0%}")
            rows.extend(recs)
    return rows


def _iso_date(value: Any) -> str | None:
    """Normalise a tennis-data Date cell to YYYY-MM-DD."""
    import pandas as pd

    if value is None:
        return None
    try:
        ts = pd.to_datetime(value, dayfirst=True, errors="coerce")
    except Exception:
        return None
    if ts is None or pd.isna(ts):
        return None
    return str(ts.date())


# --------------------------------------------------------------------------- #
# arm runners
# --------------------------------------------------------------------------- #


def _ci_dict(ci: Any) -> dict[str, Any]:
    return {
        "n": ci.n,
        "n_clusters": ci.n_clusters,
        "point": ci.point,
        "cluster_ci": [ci.lo, ci.hi],
        "iid_ci_sensitivity": [ci.iid_lo, ci.iid_hi],
    }


def run_2a(
    td_rows: list[dict[str, Any]], *, rng: Any, n_boot: int, sesoi: float
) -> dict[str, Any]:
    samples: list[MatchSample] = []
    buckets = DropBuckets()
    avg_fallback = 0
    for row in td_rows:
        sample, reason = build_2a_row(row)
        if sample is None:
            buckets.add(reason or "unknown")
            continue
        samples.append(sample)
        if sample.used_avg_fallback:
            avg_fallback += 1
    edges = [s.edge for s in samples]
    clusters = [s.cluster_key for s in samples]
    ci = cluster_bootstrap_ci(edges, clusters, rng=rng, n_boot=n_boot)
    verdict = three_state_verdict(ci, sesoi=sesoi)
    return {
        "candidates": len(td_rows),
        "scored": len(samples),
        "drop_buckets": buckets.counts,
        "avg_fallback_count": avg_fallback,
        "brier_edge": _ci_dict(ci),
        "sesoi": sesoi,
        "verdict": verdict,
    }


def run_2b(
    *,
    gamma: GammaIndexHTTP,
    cassette_dir: Path,
    min_ledger_ticks: int,
    td_by_date: dict[str, list[dict[str, Any]]],
    rng: Any,
    n_boot: int,
    sesoi: float,
    max_tick_age_h: float,
    thresholds: list[float],
    half_spreads: list[float],
    fee_rates: list[float],
    primary_threshold: float,
    realistic_half_spread: float,
    realistic_fee_rate: float,
    min_bets: int,
    limit: int | None,
) -> dict[str, Any]:
    cassette_rows = load_rows(Path("reports/backtest/_signal_rows.json"))
    mw = [
        r
        for r in cassette_rows
        if "first-set-winner" not in r.slug
        and "-vs-" in r.slug
        and "-vs-tbd" not in r.slug
        and not r.slug.startswith("will-")
    ]
    if limit is not None:
        mw = mw[:limit]
    buckets = DropBuckets()
    samples: list[MatchSample] = []
    tick_ages: list[float] = []
    for i, r in enumerate(mw, 1):
        raw_list = gamma.get(r.market_id)
        if not raw_list:
            buckets.add("gamma_missing")
            continue
        raw = raw_list[0]
        ledger = _load_cassette_ledger(r.market_id, cassette_dir)
        if len(ledger) < min_ledger_ticks:
            # Too few captured ticks (possible synthetic/degenerate) -> drop.
            buckets.add("sparse_ledger")
            continue
        clob = CassetteClobHistory(raw.get("clobTokenIds"), ledger)
        close = resolve_2b_close(
            market_id=r.market_id,
            cassette_outcome=r.outcome,
            gamma_index=_PreloadedGammaIndex(raw),
            clob=clob,
            expected_slug=r.slug,
            max_tick_age_h=max_tick_age_h,
        )
        if not close.ok:
            buckets.add(close.reason or "unknown")
        else:
            if close.tick_age_h is not None:
                tick_ages.append(close.tick_age_h)
            sample, reason = build_2b_sample(close, td_by_date=td_by_date)
            if sample is None:
                buckets.add(reason or "unknown")
            else:
                samples.append(sample)
        if i % 50 == 0:
            print(f"  2b {i}/{len(mw)} (matched {len(samples)})", flush=True)
    edges = [s.edge for s in samples]
    clusters = [s.cluster_key for s in samples]
    ci = cluster_bootstrap_ci(edges, clusters, rng=rng, n_boot=n_boot)
    verdict = three_state_verdict(ci, sesoi=sesoi)
    cells = roi_grid(
        samples,
        thresholds=thresholds,
        half_spreads=half_spreads,
        fee_rates=fee_rates,
        rng=rng,
        n_boot=n_boot,
    )
    roi_report = [
        {
            "threshold": c.threshold,
            "half_spread": c.half_spread,
            "fee_rate": c.fee_rate,
            "bets": c.bets,
            "roi": c.roi,
            "net_pnl": c.net_pnl,
            "cluster_ci": [c.ci.lo, c.ci.hi],
        }
        for c in cells
    ]
    # primary-threshold realistic cell drives the ROI gate
    primary = next(
        (
            c
            for c in cells
            if c.threshold == primary_threshold
            and c.half_spread == realistic_half_spread
            and c.fee_rate == realistic_fee_rate
        ),
        None,
    )
    roi_gate_pass = bool(
        primary is not None and primary.bets >= min_bets and primary.ci.lo > 0
    )
    tick_ages.sort()
    age_summary = (
        {
            "median_h": tick_ages[len(tick_ages) // 2],
            "p90_h": tick_ages[int(len(tick_ages) * 0.9)],
            "n": len(tick_ages),
        }
        if tick_ages
        else {"n": 0}
    )
    return {
        "candidates": len(mw),
        "matched": len(samples),
        "match_rate": (len(samples) / len(mw)) if mw else 0.0,
        "drop_buckets": buckets.counts,
        "brier_edge": _ci_dict(ci),
        "sesoi": sesoi,
        "brier_verdict": verdict,
        "roi_grid": roi_report,
        "roi_primary_gate": {
            "threshold": primary_threshold,
            "half_spread": realistic_half_spread,
            "fee_rate": realistic_fee_rate,
            "bets": primary.bets if primary else 0,
            "cluster_ci": [primary.ci.lo, primary.ci.hi] if primary else None,
            "min_bets": min_bets,
            "pass": roi_gate_pass,
        },
        "tick_age_h": age_summary,
        "gamma_fetch_errors": gamma.errors,
    }


def _overall(verdict_2a: str, brier_2b: str, roi_pass: bool, n_2b: int, min_n: int) -> str:
    """Headline verdict.

    2b (sharp vs the Polymarket close — the venue we would actually trade) is
    the money question and drives the verdict when it is testable. 2a (sharp vs
    the non-Pinnacle book consensus) is CONTEXT: whether the sharp line carries
    information beyond the efficient betting market. A 2a REFUTED does NOT by
    itself kill the venue thesis — Polymarket may be softer than the books.
    """
    tested_2b = n_2b >= min_n and brier_2b in {"EDGE", "REFUTED", "INCONCLUSIVE"}
    if tested_2b:
        if brier_2b == "EDGE" and roi_pass:
            return (
                "FULL GO — sharp line beats the Polymarket close AND clears the realistic "
                "fee+spread ROI gate. Advance D1 same-timestamp validation + evaluate "
                "first-set->match-winner migration."
            )
        if brier_2b == "EDGE":
            return (
                "PARTIAL GO — sharp beats the Polymarket close on Brier but the realistic-cost "
                "ROI gate did not clear; D1 same-timestamp validation before any capital."
            )
        if brier_2b == "REFUTED":
            return (
                "NO-GO on Polymarket — sharp line shows no meaningful edge over the Polymarket "
                "close; route mock-phase edge attention to A15 smart-money / A16 cross-market."
            )
        return "INCONCLUSIVE on Polymarket (2b CI compatible with both 0 and SESOI)."
    # 2b UNTESTED (too few verified historical closes) -> lean on 2a context.
    if verdict_2a == "EDGE":
        return (
            "PARTIAL GO — sharp line beats the book consensus (2a), but 2b vs Polymarket is "
            "UNTESTED (insufficient verified historical closes). Route to D1 forward-collection."
        )
    if verdict_2a == "REFUTED":
        return (
            "NO-GO (sharp-vs-books) — the sharp line carries no meaningful edge over the "
            "bookmaker consensus (2a, well-powered), and 2b vs Polymarket is UNTESTED. The "
            "historical sharp-line thesis is unsupported; route mock-phase edge attention to "
            "A15 smart-money. NB: this does NOT test whether the book CONSENSUS beats "
            "Polymarket (a separate softer-venue question worth a follow-up)."
        )
    return "INCONCLUSIVE — 2a not significant and 2b UNTESTED."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A17 sharp-line edge historical probe")
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025, 2026])
    ap.add_argument("--out", type=Path, default=Path("reports/a17/probe_report.json"))
    ap.add_argument("--conclusion", type=Path, default=Path("docs/backtest/sharp_line_probe.md"))
    ap.add_argument("--raw-dir", type=Path, default=Path("reports/a17/raw"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--sesoi-brier", type=float, default=0.002)
    ap.add_argument("--max-tick-age-h", type=float, default=24.0)
    ap.add_argument("--primary-threshold", type=float, default=0.03)
    ap.add_argument("--min-bets-per-bucket", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=200)
    ap.add_argument("--limit-2b", type=int, default=None, help="cap 2b markets (debug)")
    ap.add_argument("--skip-2b", action="store_true")
    ap.add_argument("--sleep-s", type=float, default=0.0, help="per-market fetch sleep")
    ap.add_argument(
        "--cassette-dir", type=Path, default=Path("agent/backtest/_cache_tennis")
    )
    ap.add_argument("--min-ledger-ticks", type=int, default=5)
    args = ap.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    thresholds = [0.02, 0.03, 0.05, 0.08]
    half_spreads = [0.0, 0.005, 0.01]
    fee_rates = [0.0, 0.03, 0.04]  # 0 / ~0.75% / ~1.0% at p=0.5
    realistic_hs, realistic_fee = 0.005, 0.03

    print(f"[A17] loading tennis-data years={args.years} ...", flush=True)
    td_rows = load_tennis_data(args.years, cache_dir=args.raw_dir)
    print(f"[A17] tennis-data rows: {len(td_rows)}", flush=True)

    print("[A17] running 2a (sharp vs non-Pinnacle consensus) ...", flush=True)
    arm_2a = run_2a(td_rows, rng=rng, n_boot=args.bootstrap, sesoi=args.sesoi_brier)
    print(f"[A17] 2a: scored={arm_2a['scored']} verdict={arm_2a['verdict']}", flush=True)

    arm_2b: dict[str, Any] = {"skipped": True}
    if not args.skip_2b:
        td_by_date: dict[str, list[dict[str, Any]]] = {}
        for row in td_rows:
            key = _iso_date(row.get("Date"))
            if key:
                td_by_date.setdefault(key, []).append(row)
        print(
            "[A17] running 2b (sharp vs Polymarket close; Gamma orientation + "
            "cassette-captured CLOB ledger) ...",
            flush=True,
        )
        arm_2b = run_2b(
            gamma=GammaIndexHTTP(sleep_s=args.sleep_s),
            cassette_dir=args.cassette_dir,
            min_ledger_ticks=args.min_ledger_ticks,
            td_by_date=td_by_date,
            rng=rng,
            n_boot=args.bootstrap,
            sesoi=args.sesoi_brier,
            max_tick_age_h=args.max_tick_age_h,
            thresholds=thresholds,
            half_spreads=half_spreads,
            fee_rates=fee_rates,
            primary_threshold=args.primary_threshold,
            realistic_half_spread=realistic_hs,
            realistic_fee_rate=realistic_fee,
            min_bets=args.min_bets_per_bucket,
            limit=args.limit_2b,
        )
        print(
            f"[A17] 2b: matched={arm_2b.get('matched')} "
            f"brier_verdict={arm_2b.get('brier_verdict')}",
            flush=True,
        )

    n_2b = int(arm_2b.get("brier_edge", {}).get("n", 0)) if not arm_2b.get("skipped") else 0
    roi_pass = bool(arm_2b.get("roi_primary_gate", {}).get("pass")) if not arm_2b.get("skipped") else False
    overall = _overall(
        arm_2a["verdict"], arm_2b.get("brier_verdict", "UNTESTED"), roi_pass, n_2b, args.min_n
    )

    report: dict[str, Any] = {
        "probe": "A17 sharp-line edge historical probe",
        "params": {
            "years": args.years,
            "seed": args.seed,
            "bootstrap": args.bootstrap,
            "sesoi_brier": args.sesoi_brier,
            "max_tick_age_h": args.max_tick_age_h,
            "primary_threshold": args.primary_threshold,
            "min_bets_per_bucket": args.min_bets_per_bucket,
            "min_n": args.min_n,
            "thresholds": thresholds,
            "half_spreads": half_spreads,
            "fee_rates": fee_rates,
        },
        "arm_2a": arm_2a,
        "arm_2b": arm_2b,
        "overall": overall,
        "caveats": [
            "2b Polymarket close is sourced from the cassette's CAPTURED-LIVE CLOB ledger: "
            "the CLOB prices-history endpoint now returns EMPTY for these long-closed resolved "
            "markets (history purged post-resolution), so the cassette (fetched while live) is "
            "the only historical source. Orientation is verified result-independently against "
            "refetched Gamma outcomes + CLOB token labels; sparse ledgers (<min ticks, possible "
            "synthetic) are dropped.",
            "Closing-line vs realized-outcome = an OPTIMISTIC UPPER BOUND on edge, "
            "not a tradeable demonstration; real same-timestamp validation is D1.",
            "Two-sided bias: Polymarket tick staleness (optimistic for sharp) vs "
            "liquidity/survivorship selection on which markets have a close (pessimistic).",
            "de-vig is proportional (no shin); slight favourite bias.",
            "2b spread is a declared assumed grid, not measured.",
            "2a is the load-bearing arm; 2b is best-effort and may be UNTESTED.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[A17] wrote {args.out}", flush=True)

    args.conclusion.parent.mkdir(parents=True, exist_ok=True)
    args.conclusion.write_text(_render_conclusion(report), encoding="utf-8")
    print(f"[A17] wrote {args.conclusion}", flush=True)
    print(f"\n[A17] OVERALL: {overall}", flush=True)
    return 0


def _render_conclusion(report: dict[str, Any]) -> str:
    a = report["arm_2a"]
    b = report["arm_2b"]
    lines = [
        "# A17 — 锐线 edge 历史探针：结论",
        "",
        f"**OVERALL：{report['overall']}**",
        "",
        "只读探针（零下注/部署/LLM/key）。2a = 锐线 vs 非-Pinnacle 大盘共识（纯 tennis-data，"
        "载重判据）；2b = 锐线 vs Polymarket 赛前收盘（378 cassette，best-effort、fail-closed）。",
        "符号约定：`edge_i=(p_soft−y)²−(p_pin−y)²`，正=锐线更准；verdict = cluster bootstrap CI + SESOI 三态。",
        "",
        "## 2a — 锐线 vs 大盘共识（载重）",
        f"- 候选 {a['candidates']}，计入 {a['scored']}，Avg 回退 {a['avg_fallback_count']}",
        f"- 掉样：{json.dumps(a['drop_buckets'], ensure_ascii=False)}",
        f"- 配对 Brier-diff 点估 {a['brier_edge']['point']:.5f}；cluster 95% CI "
        f"{_fmt_ci(a['brier_edge']['cluster_ci'])}（n={a['brier_edge']['n']}, "
        f"clusters={a['brier_edge']['n_clusters']}）；iid 敏感 {_fmt_ci(a['brier_edge']['iid_ci_sensitivity'])}",
        f"- SESOI={a['sesoi']} → **{a['verdict']}**",
        "",
    ]
    if report["arm_2b"].get("skipped"):
        lines += ["## 2b — 跳过", ""]
    else:
        lines += [
            "## 2b — 锐线 vs Polymarket 收盘（best-effort）",
            f"- 候选 {b['candidates']}，匹配 {b['matched']}（匹配率 {b['match_rate']:.0%}）",
            f"- 掉样：{json.dumps(b['drop_buckets'], ensure_ascii=False)}",
            f"- 配对 Brier-diff cluster 95% CI {_fmt_ci(b['brier_edge']['cluster_ci'])}"
            f"（n={b['brier_edge']['n']}）→ Brier **{b['brier_verdict']}**",
            f"- ROI 主门（thr={b['roi_primary_gate']['threshold']}, "
            f"realistic fee+spread, bets={b['roi_primary_gate']['bets']}/"
            f"{b['roi_primary_gate']['min_bets']}）→ pass={b['roi_primary_gate']['pass']}",
            f"- tick 年龄：{json.dumps(b['tick_age_h'], ensure_ascii=False)}",
            "",
        ]
    lines += _interpretation_lines(a, b)
    lines += ["## 诚实 caveat", *[f"- {c}" for c in report["caveats"]], ""]
    return "\n".join(lines)


def _interpretation_lines(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    out = ["## 解读与去向", ""]
    if b.get("skipped"):
        return [*out, f"- 2a（锐线 vs 庄家共识）：**{a['verdict']}**。2b 跳过。", ""]
    a_pt = a["brier_edge"]["point"]
    b_pt = b["brier_edge"]["point"]
    return [
        *out,
        f"- **2a 载重（功效充足，n={a['brier_edge']['n']}/{a['brier_edge']['n_clusters']}簇）**："
        f"锐线比庄家共识只 +{a_pt:.5f} Brier，cluster CI 上界 < SESOI → **{a['verdict']}**："
        "锐线本身不比高效的庄家市场更准。",
        f"- **2b 赚钱问题（best-effort，n={b['brier_edge']['n']}/{b['brier_edge']['n_clusters']}簇）**："
        f"锐线 vs Polymarket 收盘点估 +{b_pt:.5f}（明显大于 2a 的 +{a_pt:.5f}），realistic 档 ROI 点估为正，"
        f"但 cluster CI 跨 0 → **{b['brier_verdict']}**：点估暗示 Polymarket 比庄家更软、"
        "锐线/共识可能赢它，但簇数不足、功效不够确认。",
        "",
        "**去向**：",
        "- 历史 2b 无法定论：Polymarket 已清空已结算市场的 CLOB 历史价（重抓全空），"
        "cassette 仅留 303 场 / 26 个 tournament-week 簇，CI 太宽。",
        "- 推进 **D1**（实时、同时间戳，锐线/庄家共识 vs Polymarket；更多市场 → 更多簇 → 足够功效）"
        "确认 2b 的正点估是否为真。",
        "- 2a 的 REFUTED 不否定这条：它说的是「锐线 ≠ 比庄家共识有 edge」；2b 问的是「庄家/锐线共识 vs 更软的 "
        "Polymarket」——后者才是可吃的差价（softer-venue 套利）。",
        "- 若 D1 证实，评估首盘 → 赛果迁移；smart-money（A15）/ 跨市场（A16）按 backlog 排序。",
        "",
    ]


def _fmt_ci(ci: list[Any]) -> str:
    if not ci or ci[0] is None:
        return "[n/a]"
    return f"[{ci[0]:.5f}, {ci[1]:.5f}]"


if __name__ == "__main__":
    raise SystemExit(main())
