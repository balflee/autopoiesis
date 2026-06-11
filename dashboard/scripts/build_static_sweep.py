"""Build the static config-sweep fixture for the dashboard /backtest route — T-D-001.

Reads the REAL signal-cached sweep artefacts and emits a single small static
blob the dashboard imports as an ES-module JSON (same pattern as
``build_training_journey.py`` → ``lib/load_training_journey.ts``):

    dashboard/public/backtest/static_sweep.json

Sources
-------
* ``reports/backtest/real_signal_sweep.md`` — the human-authored sweep report.
  The optimal-seed weights/sizing/metrics + the top-10 robust frontier + the
  65.7% resolution coverage are transcribed into this script as constants (the
  report is the source of truth for those rolled-up numbers).
* ``reports/backtest/_signal_rows.json`` — the gitignored precomputed
  per-market REAL 5-slot signals (config-independent), written by
  ``python -m agent.backtest.cached_sweep precompute``. We REPLAY the optimal
  seed config over these rows through the FAITHFUL ``DecisionEngine.decide``
  (real fusion + 4-constraint sizing) + the faithful settlement PnL formula
  (``compute_bet_pnl``) to recover the actual per-bet side/size/pnl, then pick a
  representative ~12-bet sample (players from ``parse_slug``, surface from the
  resolver) for the sample-bets table.

The sample bets are therefore NOT hand-written — they are the literal bets the
optimal seed places over the real cached signals, so the table is reproducible
and byte-faithful to the engine.

Run (from repo root, UTF-8)::

    PYTHONUTF8=1 python dashboard/scripts/build_static_sweep.py

Idempotent. Read-only w.r.t. all sources.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
# When run as a script (``python dashboard/scripts/build_static_sweep.py``)
# Python puts the script's dir on sys.path, not the repo root, so the ``agent``
# package is invisible. Prepend the repo root so the faithful engine imports
# resolve regardless of the working directory the build is invoked from.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.backtest.cached_sweep import (  # noqa: E402
    compute_bet_pnl,
    load_rows,
    row_to_signals,
)
from agent.backtest.find_optimal_config import StrategyConfig  # noqa: E402
from agent.backtest.tennis_match_resolver import parse_slug  # noqa: E402
from agent.core.state import ActionKind, Weights  # noqa: E402
from agent.engines.decision import DecisionEngine  # noqa: E402

SIGNAL_ROWS_PATH = ROOT / "reports" / "backtest" / "_signal_rows.json"
OUT_PATH = ROOT / "dashboard" / "public" / "backtest" / "static_sweep.json"

SCHEMA_VERSION = "0.1.0"
TASK_ID = "T-D-001"
SPRINT = "phase_d"

# Resolution coverage of the cassette universe (real_signal_sweep.md header).
COVERAGE_PCT = 65.7

# The OPTIMAL SEED config — transcribed verbatim from real_signal_sweep.md
# ("The optimal SEED config (n=256, seed=0, ≥50 bets)").
OPTIMAL_WEIGHTS = Weights(
    w_r=0.564,
    w_s=0.436,
    alpha=[0.486, 0.328, 0.186],
    beta=[0.443, 0.557],
    rho=0.186,
)
OPTIMAL_SIZING = {
    "max_breath_risk_pct": 0.232,
    "min_confidence": 0.049,
    "min_bet_size_usd": 4.0,
}
# Rolled-up metrics for the optimal seed (real_signal_sweep.md metrics table).
OPTIMAL_METRICS = {
    "sharpe": 0.649,
    "bets": 65,
    "win_rate": 0.815,
    "net_pnl": 852.56,
    "avg_bet_size": 4.63,
}

# Robust frontier (top-10 by Sharpe, ≥50 bets) — real_signal_sweep.md table.
# alpha is the displayed [α₁, α₂, α₃]; `risk` = max_breath_risk_pct; `mb` =
# min_bet_size_usd. Values are the report's rounded display values.
FRONTIER: list[dict[str, Any]] = [
    {"rank": 1, "sharpe": 0.649, "net_pnl": 852, "win_rate": 0.815, "bets": 65, "w_r": 0.56, "alpha": [0.49, 0.33, 0.19], "rho": 0.19, "max_breath_risk_pct": 0.23, "min_bet_size_usd": 4.0},
    {"rank": 2, "sharpe": 0.526, "net_pnl": 344, "win_rate": 0.823, "bets": 62, "w_r": 0.02, "alpha": [0.54, 0.25, 0.20], "rho": 0.14, "max_breath_risk_pct": 0.22, "min_bet_size_usd": 4.0},
    {"rank": 3, "sharpe": 0.502, "net_pnl": 1100, "win_rate": 0.785, "bets": 130, "w_r": 0.64, "alpha": [0.45, 0.14, 0.41], "rho": 0.19, "max_breath_risk_pct": 0.25, "min_bet_size_usd": 4.0},
    {"rank": 4, "sharpe": 0.443, "net_pnl": 1893, "win_rate": 0.743, "bets": 284, "w_r": 0.85, "alpha": [0.61, 0.25, 0.15], "rho": 0.15, "max_breath_risk_pct": 0.35, "min_bet_size_usd": 3.9},
    {"rank": 5, "sharpe": 0.427, "net_pnl": 1293, "win_rate": 0.789, "bets": 313, "w_r": 0.23, "alpha": [0.75, 0.05, 0.20], "rho": 0.17, "max_breath_risk_pct": 0.35, "min_bet_size_usd": 2.2},
    {"rank": 6, "sharpe": 0.424, "net_pnl": 2566, "win_rate": 0.771, "bets": 397, "w_r": 0.55, "alpha": [0.41, 0.39, 0.20], "rho": 0.30, "max_breath_risk_pct": 0.46, "min_bet_size_usd": 4.0},
    {"rank": 7, "sharpe": 0.417, "net_pnl": 1891, "win_rate": 0.785, "bets": 474, "w_r": 0.33, "alpha": [0.64, 0.11, 0.25], "rho": 0.18, "max_breath_risk_pct": 0.10, "min_bet_size_usd": 2.1},
    {"rank": 8, "sharpe": 0.408, "net_pnl": 3836, "win_rate": 0.765, "bets": 750, "w_r": 0.06, "alpha": [0.73, 0.15, 0.12], "rho": 0.52, "max_breath_risk_pct": 0.48, "min_bet_size_usd": 4.0},
    {"rank": 9, "sharpe": 0.405, "net_pnl": 1591, "win_rate": 0.783, "bets": 267, "w_r": 0.26, "alpha": [0.51, 0.02, 0.48], "rho": 0.26, "max_breath_risk_pct": 0.34, "min_bet_size_usd": 4.0},
    {"rank": 10, "sharpe": 0.396, "net_pnl": 3517, "win_rate": 0.753, "bets": 697, "w_r": 0.02, "alpha": [0.72, 0.05, 0.23], "rho": 0.56, "max_breath_risk_pct": 0.41, "min_bet_size_usd": 4.0},
]

# The 5 engine-slot keys, in stable display order. Slot KEYS carry repurposed
# payloads (see the "Slot-name repurpose" caveat in real_signal_sweep.md):
#   tennis_technical = elo, market_momentum = CLOB momentum,
#   smart_money = surface, sentiment_llm = h2h, crowd_volume = rest.
SLOT_KEYS = (
    "tennis_technical",
    "market_momentum",
    "smart_money",
    "sentiment_llm",
    "crowd_volume",
)

# How many sample bets to surface in the table.
N_SAMPLE_BETS = 12


def _titlecase_surname(norm: str) -> str:
    """``parse_slug`` lower-cases + strips accents; present it title-cased."""
    return norm[:1].upper() + norm[1:] if norm else norm


def _optimal_config() -> StrategyConfig:
    return StrategyConfig(
        weights=OPTIMAL_WEIGHTS,
        max_breath_risk_pct=OPTIMAL_SIZING["max_breath_risk_pct"],
        min_confidence=OPTIMAL_SIZING["min_confidence"],
        min_bet_size_usd=OPTIMAL_SIZING["min_bet_size_usd"],
    )


async def _replay_optimal_bets() -> list[dict[str, Any]]:
    """Replay the optimal seed over the cached rows; return per-BET records.

    Mirrors ``cached_sweep.score_config`` exactly (same engine construction,
    same decide args, same faithful PnL) so the bets recovered here ARE the
    optimal seed's bets — not an approximation. Each record carries the slug,
    parsed players + surface, the 5 slot scores, side, size, entry price,
    outcome, and realised PnL.
    """
    rows = load_rows(SIGNAL_ROWS_PATH)
    cfg = _optimal_config()
    engine = DecisionEngine(
        max_breath_risk_pct=cfg.max_breath_risk_pct,
        min_bet_size_usd=cfg.min_bet_size_usd,
        min_confidence=cfg.min_confidence,
    )
    w = cfg.weights
    alpha = (w.alpha[0], w.alpha[1], w.alpha[2])
    beta = (w.beta[0], w.beta[1])

    bets: list[dict[str, Any]] = []
    for row in rows:
        action = await engine.decide(
            signals=row_to_signals(row),
            weights_alpha=alpha,
            weights_beta=beta,
            w_r=w.w_r,
            w_s=w.w_s,
            rho=w.rho,
            bankroll_usd=100.0,
            breath=100.0,
            liquidity_cap_usd=row.liquidity_cap_usd,
            market_id=row.market_id,
        )
        if action.kind is not ActionKind.BET:
            continue
        assert action.side is not None and action.size_usd is not None
        pnl = compute_bet_pnl(
            side=action.side.value,
            entry_price=row.entry_price,
            size_usd=action.size_usd,
            outcome=row.outcome,
            winning_price=row.winning_price,
        )
        parsed = parse_slug(row.slug)
        if parsed is None:
            # Should not happen — precompute only kept resolvable slugs — but
            # be defensive so a stray row never crashes the build.
            continue
        bets.append(
            {
                "market_id": row.market_id,
                "players": [
                    _titlecase_surname(parsed.p1_surname),
                    _titlecase_surname(parsed.p2_surname),
                ],
                "surface": parsed.surface,
                "entry_price": round(row.entry_price, 4),
                "outcome": row.outcome,
                "signals": {k: round(row.scores[k], 4) for k in SLOT_KEYS},
                "side": action.side.value,
                "size": round(action.size_usd, 2),
                "pnl": round(pnl, 2),
            }
        )
    return bets


def _select_sample(bets: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Pick ``n`` representative bets: keep every loss (they are rare + the most
    interesting), then fill the remainder with wins spread evenly across the run.

    Deterministic given ``bets`` input order (which is stable market_id order
    from the precompute). Returned in the original run order.
    """
    if len(bets) <= n:
        return bets
    losses = [b for b in bets if b["pnl"] <= 0.0]
    wins = [b for b in bets if b["pnl"] > 0.0]
    # Reserve room for up to all losses (but never more than half the sample so
    # the table still reads as a winning strategy).
    max_losses = min(len(losses), max(1, n // 3))
    chosen_losses = _evenly(losses, max_losses)
    remaining = n - len(chosen_losses)
    chosen_wins = _evenly(wins, remaining)
    chosen_ids = {b["market_id"] for b in chosen_losses + chosen_wins}
    return [b for b in bets if b["market_id"] in chosen_ids]


def _evenly(items: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Pick ``k`` items spread evenly across ``items`` (endpoints included)."""
    if k <= 0 or not items:
        return []
    if k >= len(items):
        return list(items)
    if k == 1:
        return [items[0]]
    step = (len(items) - 1) / (k - 1)
    idxs = sorted({round(i * step) for i in range(k)})
    return [items[i] for i in idxs]


def build_fixture() -> dict[str, Any]:
    import asyncio

    all_bets = asyncio.run(_replay_optimal_bets())
    sample_bets = _select_sample(all_bets, N_SAMPLE_BETS)

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "sprint": SPRINT,
        "coverage_pct": COVERAGE_PCT,
        "optimal_seed": {
            "weights": {
                "w_r": OPTIMAL_WEIGHTS.w_r,
                "w_s": OPTIMAL_WEIGHTS.w_s,
                "alpha": list(OPTIMAL_WEIGHTS.alpha),
                "beta": list(OPTIMAL_WEIGHTS.beta),
                "rho": OPTIMAL_WEIGHTS.rho,
            },
            "sizing": dict(OPTIMAL_SIZING),
            "sharpe": OPTIMAL_METRICS["sharpe"],
            "bets": OPTIMAL_METRICS["bets"],
            "win_rate": OPTIMAL_METRICS["win_rate"],
            "net_pnl": OPTIMAL_METRICS["net_pnl"],
            "avg_bet_size": OPTIMAL_METRICS["avg_bet_size"],
        },
        "frontier": FRONTIER,
        "sample_bets": sample_bets,
    }


def main() -> int:
    fixture = build_fixture()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    n_sample = len(fixture["sample_bets"])
    n_wins = sum(1 for b in fixture["sample_bets"] if b["pnl"] > 0.0)
    print(
        f"wrote static_sweep.json: {len(fixture['frontier'])} frontier rows, "
        f"{n_sample} sample bets ({n_wins} wins) -> {OUT_PATH}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
