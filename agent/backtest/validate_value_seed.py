"""Validate top-K value-sweep candidates in the REAL sequential survival season.

The fast scorer (``cached_sweep --realism --value --rank pnl``) ranks
INDEPENDENT bets; the season adds breath, death, respawn and settlement lag.
A config must EARN under sequence AND SURVIVE to become the v3 seed.

Usage::

    python -m agent.backtest.validate_value_seed --top 5

Re-derives the same LHS grid + fast ranking (deterministic in ``--seed``),
runs ``run_survival_export`` for each of the top ``--top`` candidates with the
EXACT journey knobs (fragile 0.95, loss multiplier 5.0, breath 35, max lives
12, v3 physics defaulted ON), ranks candidates by (finished-alive DESC,
season terminal PnL DESC), prints the comparison table, and writes the
winning config to ``docs/backtest/value_seed_v3.json`` (COMMITTED — a clean
checkout must be able to reproduce the v3 journeys; r4 M-2).
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from agent.backtest.cached_sweep import (
    load_rows,
    rank_configs_by_pnl,
    run_cached_sweep,
)
from agent.backtest.find_optimal_config import (
    StrategyConfig,
    generate_lhs_strategy_configs,
)
from agent.backtest.survival_season import run_survival_export

# The journey calibration knobs (chapter-2/3 contract) — the validation MUST
# run the same physics the journeys will, or the chosen seed is optimal for a
# world the journeys don't simulate.
_JOURNEY_KNOBS: dict[str, Any] = {
    "fragile_max_breath_risk_pct": 0.95,
    "loss_multiplier": 5.0,
    "initial_breath": 35.0,
    "max_lives": 12,
}

_SEED_OUT = Path("docs/backtest/value_seed_v3.json")


def _clamp_min_bet(cfg: StrategyConfig) -> StrategyConfig:
    from dataclasses import replace

    if cfg.min_bet_size_usd <= 4.0:
        return cfg
    return replace(cfg, min_bet_size_usd=4.0)


def _seed_payload(cfg: StrategyConfig) -> dict[str, Any]:
    return {
        "weights": cfg.weights.model_dump(),
        "max_breath_risk_pct": cfg.max_breath_risk_pct,
        "min_confidence": cfg.min_confidence,
        "min_bet_size_usd": cfg.min_bet_size_usd,
        "min_edge": cfg.min_edge,
        "kappa": cfg.kappa,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.backtest.validate_value_seed",
        description="Sequential-survival validation of the top value-sweep "
        "candidates; writes the winning v3 seed json.",
    )
    parser.add_argument("--rows", type=Path, default=Path("reports/backtest/_signal_rows.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("agent/backtest/_cache_tennis"))
    parser.add_argument("--n", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-bets", type=int, default=200)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--out", type=Path, default=_SEED_OUT)
    args = parser.parse_args(argv)

    rows = load_rows(args.rows)
    configs = [
        _clamp_min_bet(c)
        for c in generate_lhs_strategy_configs(args.n, seed=args.seed)
    ]
    scored = run_cached_sweep(
        rows,
        configs,
        entry_price_floor=0.05,
        effective_entry_price_floor=0.05,
        max_pnl_usd=100.0,
        side_correct_pricing=True,
        value_betting=True,
    )
    ranked = rank_configs_by_pnl(scored, min_bets=args.min_bets)
    candidates = ranked[: args.top]
    print(f"validating top {len(candidates)} of {len(ranked)} gated configs "
          f"in the sequential season ({_JOURNEY_KNOBS})...")

    results: list[dict[str, Any]] = []
    for i, (cfg, fast_m) in enumerate(candidates):
        with tempfile.TemporaryDirectory(prefix=f"value_seed_cand{i}_") as tmp:
            journey = run_survival_export(
                rows_path=args.rows,
                cache_dir=args.cache_dir,
                out_path=Path(tmp) / "journey.json",
                base_seed=cfg,
                **_JOURNEY_KNOBS,
            )
        s = journey["summary"]
        finished_alive = s["deaths"] < s["lives"]
        results.append(
            {
                "candidate": i,
                "cfg": cfg,
                "fast_pnl": fast_m.net_pnl,
                "fast_t": fast_m.t_stat,
                "season_pnl": s["learner_final_pnl"],
                "lives": s["lives"],
                "deaths": s["deaths"],
                "finished_alive": finished_alive,
                "vs_static": s["learning_vs_static_delta"],
            }
        )
        print(
            f"  cand {i}: fast=${fast_m.net_pnl:.0f} (t={fast_m.t_stat:.1f}) "
            f"-> season=${s['learner_final_pnl']:.2f} "
            f"lives={s['lives']} deaths={s['deaths']} "
            f"alive_at_end={finished_alive} vs_static={s['learning_vs_static_delta']:+.2f}",
            flush=True,
        )

    # Rank: finished-alive first, then season terminal PnL.
    results.sort(key=lambda r: (r["finished_alive"], r["season_pnl"]), reverse=True)
    winner = results[0]
    cfg = winner["cfg"]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(_seed_payload(cfg), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"\nWINNER: candidate {winner['candidate']} — season "
        f"${winner['season_pnl']:.2f}, {winner['lives']} lives / "
        f"{winner['deaths']} deaths, alive_at_end={winner['finished_alive']}"
    )
    print(f"seed written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
