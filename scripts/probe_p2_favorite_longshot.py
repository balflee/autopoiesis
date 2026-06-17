"""P2 probe (plan-loop V1.7): Polymarket favorite-longshot calibration on thin tennis.

A documented PM bias says longshots are overpriced (~−26%) and favorites only mildly
mispriced (~−3.6%). P2 asks: does that hold on OUR thin ATP/WTA subset NET of fee — i.e.
do strong favorites win MORE often than their entry price implies, by enough to clear the
fee/half-spread dead zone? Backtest-only, no executor, low look-ahead (entry price only).

Honesty gates (mandatory):
  * REAL fills only — the entry price comes from :mod:`data.sources.polymarket_trades`
    (provenance ``actual_trade``); a market with NO real trade is SKIPPED + logged
    (FAIL-CLOSED), NEVER backfilled from a synthetic ledger or midpoint (Codex-2).
  * cost-NET — each favorite bet is charged a thin-book half-spread/fee haircut.
  * the favorite (top-price) decile must clear the V1.6 graduation gate
    (``gain >= threshold`` AND beats the market-efficiency placebo) or the verdict is
    NO_GO. A zero-edge / shuffled input therefore returns NO_GO by construction.

Emits a reusable calibration curve (per-decile implied vs empirical favorite win-rate +
net ROI) even when the verdict is NO_GO. All randomness is seeded → reproducible.
Run from the repo root (.../code): ``python scripts/probe_p2_favorite_longshot.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.graduation_gate import (
    DEFAULT_THRESHOLD,
    GraduationResult,
    ProbeBet,
    evaluate_graduation,
)
from data.sources.polymarket_trades import (
    PROVENANCE_ACTUAL_TRADE,
    PolymarketTradesClient,
)

logger = logging.getLogger("probe_p2")

# Thin tennis book half-spread crossing cost as a FRACTION of the $1 stake. Deliberately
# conservative — the whole point is to test whether any favorite edge survives it.
THIN_BOOK_HALF_SPREAD_FRAC = 0.01


@dataclass(frozen=True)
class ResolvedMarket:
    """One resolved tennis market: its id + whether the FAVORITE side won.

    The favorite entry PRICE is read live from the real-trades source; only the
    resolved outcome (which side won) is supplied here. ``asof_ts`` (ISO-8601, UTC) is
    an OPTIONAL per-market PIT cutoff — the decision/open time; when set, the entry is
    the earliest real trade AT/BEFORE it (Codex Phase-3 MED: pin the entry to a
    pre-decision timestamp rather than the earliest trade ever returned)."""

    market_id: str
    favorite_won: bool
    asof_ts: str | None = None


@dataclass(frozen=True)
class DecileStat:
    decile: int          # 0 = lowest favorite price, n-1 = strongest favorites
    n: int
    implied: float       # mean favorite entry price (the market's model prob)
    empirical: float     # realized favorite win-rate in the bin
    net_roi: float       # cost-net ROI of flat-staking the favorite across the bin


@dataclass(frozen=True)
class P2Result:
    deciles: list[DecileStat]
    favorite_bin: GraduationResult  # the strongest-favorite decile's go/no-go
    n_scored: int
    n_skipped: int                  # markets without a real actual_trade entry


def default_cost_model(favorite_price: float, stake: float) -> float:
    """Thin-book half-spread crossing haircut on a favorite bet (size-scaled)."""
    return THIN_BOOK_HALF_SPREAD_FRAC * stake


def _decile_index(rank: int, n: int, n_deciles: int) -> int:
    """Map a sorted rank (0..n-1) to a decile bucket 0..n_deciles-1 (equal-count)."""
    if n <= 0:
        return 0
    return min(n_deciles - 1, rank * n_deciles // n)


def run_p2_probe(
    markets: list[ResolvedMarket],
    *,
    trades_client: PolymarketTradesClient,
    cost_model: Callable[[float, float], float] = default_cost_model,
    n_deciles: int = 10,
    threshold: float = DEFAULT_THRESHOLD,
    seed: int = 0,
    asof_ts: object = None,
) -> P2Result:
    """Run the favorite-longshot calibration. Each market's favorite entry price is the
    real-trade implied prob (``max(p, 1-p)``); FAIL-CLOSED skip if there is no
    ``actual_trade`` entry. Bin favorites by price decile → calibration curve; the
    strongest-favorite decile is scored through the V1.6 graduation gate."""
    scored: list[tuple[float, bool, float]] = []  # (favorite_price, won, cost_usd)
    n_skipped = 0
    for m in markets:
        # Per-market PIT cutoff takes precedence over the run-wide default (Codex
        # Phase-3 MED) — a future trade can never be the entry. A naive ISO string
        # is coerced to UTC (the trades client requires tz-aware; Codex r2 MED-1:
        # else a naive per-market asof crashes the probe).
        cutoff: object
        if m.asof_ts:
            parsed = datetime.fromisoformat(m.asof_ts)
            cutoff = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        else:
            cutoff = asof_ts
        entry = trades_client.entry_price(m.market_id, asof_ts=cutoff)  # type: ignore[arg-type]
        # FAIL-CLOSED: no real trade, or a non-canonical provenance, → skip + log.
        if entry is None or entry.provenance != PROVENANCE_ACTUAL_TRADE:
            n_skipped += 1
            logger.info(
                "P2 skip market_id=%s — no actual_trade entry (provenance=%s)",
                m.market_id, getattr(entry, "provenance", None),
            )
            continue
        favorite_price = max(entry.price, 1.0 - entry.price)  # the favored side's prob
        cost = cost_model(favorite_price, 1.0)
        scored.append((favorite_price, m.favorite_won, cost))

    scored.sort(key=lambda t: t[0])  # ascending favorite price
    n = len(scored)

    # Per-decile calibration curve + the strongest-favorite (top) decile's bets.
    bins: list[list[tuple[float, bool, float]]] = [[] for _ in range(n_deciles)]
    for rank, row in enumerate(scored):
        bins[_decile_index(rank, n, n_deciles)].append(row)

    deciles: list[DecileStat] = []
    for d, rows in enumerate(bins):
        if not rows:
            continue
        prices = [r[0] for r in rows]
        wins = [r[1] for r in rows]
        bets = [ProbeBet(stake_usd=1.0, entry_price=p, won=w, cost_usd=c) for p, w, c in rows]
        # ROI = total net profit / total stake (== mean per-$1 net profit here).
        net_roi = evaluate_graduation(
            bets, threshold=threshold, n_placebo=1, seed=seed
        ).gain
        deciles.append(
            DecileStat(
                decile=d,
                n=len(rows),
                implied=sum(prices) / len(prices),
                empirical=sum(1 for w in wins if w) / len(wins),
                net_roi=net_roi,
            )
        )

    top_rows = bins[-1] if any(bins) else []
    favorite_bets = [
        ProbeBet(stake_usd=1.0, entry_price=p, won=w, cost_usd=c) for p, w, c in top_rows
    ]
    favorite_bin = evaluate_graduation(favorite_bets, threshold=threshold, seed=seed)
    return P2Result(
        deciles=deciles,
        favorite_bin=favorite_bin,
        n_scored=n,
        n_skipped=n_skipped,
    )


def _load_markets(path: Path) -> list[ResolvedMarket]:
    """Load resolved markets from a JSON list of ``{market_id, favorite_won,
    asof_ts?}`` (``asof_ts`` optional ISO-8601 PIT cutoff per market)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[ResolvedMarket] = []
    for r in raw:
        asof = r.get("asof_ts")
        out.append(
            ResolvedMarket(
                market_id=str(r["market_id"]),
                favorite_won=bool(r["favorite_won"]),
                asof_ts=str(asof) if asof else None,
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="P2 favorite-longshot calibration probe")
    parser.add_argument("markets_json", type=Path, help="JSON list of {market_id, favorite_won}")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--deciles", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    result = run_p2_probe(
        _load_markets(args.markets_json),
        trades_client=PolymarketTradesClient(),
        n_deciles=args.deciles,
        threshold=args.threshold,
        seed=args.seed,
    )
    print(f"P2 favorite-longshot calibration (scored={result.n_scored}, "
          f"skipped_no_real_trade={result.n_skipped})")
    print(f"{'decile':>6} {'n':>4} {'implied':>8} {'empirical':>9} {'net_roi':>8}")
    for s in result.deciles:
        print(f"{s.decile:>6} {s.n:>4} {s.implied:>8.3f} {s.empirical:>9.3f} {s.net_roi:>8.3f}")
    fb = result.favorite_bin
    print(f"\nFAVORITE BIN: gain={fb.gain:.4f} threshold={fb.threshold} "
          f"placebo_p95={fb.placebo_p95:.4f} beats_placebo={fb.beats_placebo} "
          f"VERDICT={fb.verdict}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
