"""Validate top-K value-sweep candidates in the REAL sequential survival season.

The fast scorer (``cached_sweep --realism --value --rank pnl``) ranks
INDEPENDENT bets; the season adds breath, death, respawn and settlement lag.
A config must EARN under sequence AND SURVIVE to become the seed.

Usage::

    # v3 in-sample (default — byte-identical to the pre-walk-forward behavior):
    python -m agent.backtest.validate_value_seed --top 5

    # B′ walk-forward OOS (opt-in): select on the earlier window, evaluate the
    # winner on the held-out later window (post-floor SurvivalRow layer):
    python -m agent.backtest.validate_value_seed \
        --rows reports/backtest/_signal_rows_v4.json --walk-forward

Re-derives the same LHS grid + fast ranking (deterministic in ``--seed``),
validates the top ``--top`` candidates with the EXACT journey knobs (fragile
0.95, loss multiplier 5.0, breath 35, max lives 12, v3 physics defaulted ON),
ranks candidates by (finished-alive DESC, season terminal PnL DESC), and writes
the winning config to ``docs/backtest/value_seed_v4.json`` (COMMITTED — a clean
checkout must be able to reproduce the journeys; r4 M-2).

The select+validate body is the reusable :func:`select_winner` seam (r4 MED-C)
so the three-arm journey driver can re-select per LHS seed without shelling out.
``--walk-forward`` (r4/r5 HIGH-A/HIGH-1): the TEST evaluation reuses
``survival_season.run_survival_over_rows`` (the run-half: fragile crank +
``SurvivalRecorder`` + ``build_survival_journey``) so the held-out season runs
the SAME fragile physics + cumulative PnL the in-sample journey does — NOT a
bare ``run_survival_season`` (which lacks the fragile crank + cumulative PnL).
"""

from __future__ import annotations

import argparse
import json
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
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    load_all_cached_markets,
)
from agent.backtest.reincarnation import split_rows_by_time
from agent.backtest.survival_season import (
    SurvivalRow,
    _build_corpus_resolver,
    build_survival_rows,
    run_survival_over_rows,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver

# The journey calibration knobs (chapter-2/3 contract) — the validation MUST
# run the same physics the journeys will, or the chosen seed is optimal for a
# world the journeys don't simulate.
_JOURNEY_KNOBS: dict[str, Any] = {
    "fragile_max_breath_risk_pct": 0.95,
    "loss_multiplier": 5.0,
    "initial_breath": 35.0,
    "max_lives": 12,
}

# v3 (legacy) seed path kept as a constant for back-compat + roll-back; the CLI
# default is now the κ_xm-carrying v4 seed (B′ Task 6a).
_SEED_OUT = Path("docs/backtest/value_seed_v3.json")
_SEED_OUT_V4 = Path("docs/backtest/value_seed_v4.json")

# The realism-v3 fast-sweep knobs the selection scores under (must match the
# survival journey's physics, and the pre-flag behavior, byte-for-byte).
_ENTRY_PRICE_FLOOR = 0.05
_SWEEP_KW: dict[str, Any] = {
    "entry_price_floor": _ENTRY_PRICE_FLOOR,
    "effective_entry_price_floor": _ENTRY_PRICE_FLOOR,
    "max_pnl_usd": 100.0,
    "side_correct_pricing": True,
    "value_betting": True,
}


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
        # B′ Task 6a: carry κ_xm so the committed v4 seed round-trips the new
        # genome scalar (defaults 0.0 on a v3-shaped config — still valid).
        "kappa_xm": cfg.kappa_xm,
    }


def select_winner(
    rows: list[Any],
    snapshots: list[MarketSnapshot],
    lhs_seed: int,
    *,
    walk_forward: bool = False,
    resolver: TennisMatchResolver | None = None,
    n: int = 256,
    min_bets: int = 200,
    top: int = 5,
    fragile_max_breath_risk_pct: float = 0.95,
    loss_multiplier: float = 5.0,
    initial_breath: float = 35.0,
    max_lives: int = 12,
    entry_price_floor: float = _ENTRY_PRICE_FLOOR,
    train_fraction: float = 0.7,
    verbose: bool = True,
) -> tuple[StrategyConfig, dict[str, Any]]:
    """Select + validate the winning config over a LOADED row universe (r4 MED-C).

    The reusable select+validate seam shared by :func:`main` (one headline seed)
    and the three-arm journey driver (re-select per LHS seed). Callers load
    ``rows`` (cached :class:`~agent.backtest.cached_sweep.SignalRow`) + the
    cassette ``snapshots`` + a ``resolver`` ONCE, then call this per seed.

    ``walk_forward`` (r4/r5 HIGH-A/HIGH-1):

    * ``False`` (default) — the v3 IN-SAMPLE path: select on the full ``rows``
      via the fast sweep, validate each top-``top`` candidate over the full
      post-floor :class:`SurvivalRow` universe via
      :func:`~agent.backtest.survival_season.run_survival_over_rows`. Byte-
      identical to the pre-flag behavior (so the v3 seed reproduces).
    * ``True`` — the post-floor walk-forward OOS path: JOIN + apply the realism
      floor FIRST, then split chronologically (``split_rows_by_time``); map the
      TRAIN SurvivalRows back to their ``.signal`` SignalRows to run the fast
      sweep selection on TRAIN; evaluate each candidate winner on the held-out
      TEST SurvivalRows via the SAME run-half (fragile crank + recorder +
      cumulative-PnL journey). No look-ahead: the floor + split keys are
      config-independent.

    Returns ``(winner_cfg, season_summary)`` where ``season_summary`` is the
    winner's ``journey["summary"]`` (carries ``learner_final_pnl`` / ``deaths``
    / ``lives`` / ``learning_vs_static_delta``).
    """
    run_knobs: dict[str, Any] = {
        "fragile_max_breath_risk_pct": fragile_max_breath_risk_pct,
        "loss_multiplier": loss_multiplier,
        "initial_breath": initial_breath,
        "max_lives": max_lives,
    }

    if resolver is None:
        resolver = _build_corpus_resolver()

    # JOIN + apply the realism floor BEFORE any split (r4 HIGH-A: doing it after
    # the split would drift the train/test boundary).
    survival_all = build_survival_rows(
        rows, snapshots, resolver, entry_price_floor=entry_price_floor
    )

    sweep_rows: list[Any]
    eval_rows: list[SurvivalRow]
    if walk_forward:
        train_rows, test_rows = split_rows_by_time(
            survival_all, train_fraction=train_fraction
        )
        # Map TRAIN SurvivalRows back to their embedded SignalRows for the fast
        # sweep (the scorer consumes SignalRows; the run-half consumes
        # SurvivalRows).
        sweep_rows = [r.signal for r in train_rows]
        eval_rows = test_rows
        mode = "walk-forward OOS"
    else:
        # v3 in-sample: select on the full SignalRows, validate on the full
        # post-floor SurvivalRows.
        sweep_rows = rows
        eval_rows = survival_all
        mode = "v3 in-sample"

    configs = [
        _clamp_min_bet(c) for c in generate_lhs_strategy_configs(n, seed=lhs_seed)
    ]
    scored = run_cached_sweep(sweep_rows, configs, **_SWEEP_KW)
    ranked = rank_configs_by_pnl(scored, min_bets=min_bets)
    candidates = ranked[:top]
    if verbose:
        print(
            f"[{mode}] validating top {len(candidates)} of {len(ranked)} gated "
            f"configs in the sequential season ({run_knobs})...",
            flush=True,
        )

    results: list[dict[str, Any]] = []
    for i, (cfg, fast_m) in enumerate(candidates):
        journey = run_survival_over_rows(
            eval_rows, snapshots, base_seed=cfg, **run_knobs
        )
        s = journey["summary"]
        finished_alive = s["deaths"] < s["lives"]
        results.append(
            {
                "candidate": i,
                "cfg": cfg,
                "summary": s,
                "fast_pnl": fast_m.net_pnl,
                "fast_t": fast_m.t_stat,
                "season_pnl": s["learner_final_pnl"],
                "finished_alive": finished_alive,
            }
        )
        if verbose:
            print(
                f"  cand {i}: fast=${fast_m.net_pnl:.0f} (t={fast_m.t_stat:.1f}) "
                f"-> season=${s['learner_final_pnl']:.2f} "
                f"lives={s['lives']} deaths={s['deaths']} "
                f"alive_at_end={finished_alive} "
                f"vs_static={s['learning_vs_static_delta']:+.2f}",
                flush=True,
            )

    # Rank: finished-alive first, then season terminal PnL (stable on ties).
    results.sort(key=lambda r: (r["finished_alive"], r["season_pnl"]), reverse=True)
    winner = results[0]
    return winner["cfg"], winner["summary"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.backtest.validate_value_seed",
        description="Sequential-survival validation of the top value-sweep "
        "candidates; writes the winning v4 seed json.",
    )
    parser.add_argument(
        "--rows", type=Path, default=Path("reports/backtest/_signal_rows.json")
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("agent/backtest/_cache_tennis")
    )
    parser.add_argument("--n", type=int, default=256)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-bets", type=int, default=200)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        default=False,
        help="Opt-in post-floor walk-forward OOS selection (select on the "
        "earlier window, evaluate on the held-out later window). Default OFF "
        "= v3 in-sample (byte-identical to the pre-flag behavior).",
    )
    parser.add_argument("--out", type=Path, default=_SEED_OUT_V4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Load rows / snapshots / resolver ONCE (r4 MED-C) then delegate to the
    # reusable seam for the single headline seed.
    rows = load_rows(args.rows)
    snapshots = load_all_cached_markets(cache_dir=args.cache_dir)
    resolver = _build_corpus_resolver()

    winner_cfg, summary = select_winner(
        rows,
        snapshots,
        args.seed,
        walk_forward=args.walk_forward,
        resolver=resolver,
        n=args.n,
        min_bets=args.min_bets,
        top=args.top,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(_seed_payload(winner_cfg), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    finished_alive = summary["deaths"] < summary["lives"]
    print(
        f"\nWINNER: season ${summary['learner_final_pnl']:.2f}, "
        f"{summary['lives']} lives / {summary['deaths']} deaths, "
        f"alive_at_end={finished_alive}"
    )
    print(f"seed written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
