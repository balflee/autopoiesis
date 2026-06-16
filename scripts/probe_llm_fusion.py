"""A13 feasibility PROBE: would an LLM reasoning over the 5 raw signals turn
them into a better win-probability than our fixed linear fusion?

This is a CHEAP go/no-go before any architecture change. It does NOT touch the
backtest loop — it replays a deterministic SAMPLE of real decision points and
compares three estimators of the true YES probability against the realized
outcome:

  1. market-only  — the market's own implied probability (entry_price). The
     calibration floor; the forensics already showed this market is calibrated.
  2. linear fusion — our v3 seed: p_model = clamp(price + kappa * fused), using
     the REAL ``_fuse_signals`` (not re-derived) so the baseline is faithful.
  3. LLM fusion   — the 5 (engine, score, confidence) pairs + the price handed
     to the LLM, which returns its own YES probability. The outcome is NEVER
     shown (no look-ahead).

Scored by Brier (mean (p - outcome)^2; lower = better calibrated) + the win
rate of the bets each estimator WOULD place (edge >= min_edge). Batched 5
markets per LLM call to stay cheap under MiniMax latency.

Keys come from ./.env (never printed). Usage:
    python scripts/probe_llm_fusion.py --n 50 --batch 5 --seed 0
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.cached_sweep import load_rows
from agent.engines.base import EngineSignal
from agent.engines.decision import (
    RATIONAL_ENGINES,
    SENTIENT_ENGINES,
    _fuse_signals,
)

_ENGINES = (*RATIONAL_ENGINES, *SENTIENT_ENGINES)

_ENGINE_DESC = {
    "tennis_technical": "pre-match technical read (elo/surface/h2h-class)",
    "market_momentum": "intraday CLOB price drift",
    "surface_advantage": "Sackmann surface-specific edge",
    "head_to_head": "Sackmann head-to-head record",
    "rest_recency": "Sackmann rest / recent-form signal",
}

_PRED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "predictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "win_probability": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": ["idx", "win_probability"],
            },
        }
    },
    "required": ["predictions"],
}


def _linear_p(row: Any, seed: dict[str, Any]) -> float:
    """Faithful linear-fusion p_model using the REAL _fuse_signals."""
    w = seed["weights"]
    signals = {
        name: EngineSignal(
            score=float(row.scores.get(name, 0.0)),
            confidence=float(row.confidences.get(name, 0.0)),
            available_at="1970-01-01T00:00:00+00:00",
            rationale="probe",
            raw_features={},
        )
        for name in _ENGINES
    }
    fusion = _fuse_signals(
        signals=signals,
        alpha=(w["alpha"][0], w["alpha"][1], w["alpha"][2]),
        beta=(w["beta"][0], w["beta"][1]),
        w_r=w["w_r"],
        w_s=w["w_s"],
    )
    price = float(row.entry_price)
    return max(0.0, min(1.0, price + seed["kappa"] * fusion.fused))


def _build_prompt(batch: list[Any]) -> str:
    lines = [
        "You are estimating the TRUE probability that the YES side wins each of "
        "the following tennis prediction markets. For each market you are given "
        "the current market price (the market's own implied YES probability) and "
        "5 independent signals, each a score in [-1, 1] (positive leans YES, "
        "negative leans NO) with a confidence in [0, 1].",
        "",
        "Signals:",
    ]
    for name in _ENGINES:
        lines.append(f"  - {name}: {_ENGINE_DESC.get(name, name)}")
    lines.append("")
    lines.append(
        "For EACH market below, output your best estimate of the true YES win "
        "probability (0-1). You may agree with or deviate from the market price "
        "based on the signals. Return one prediction per market by its idx."
    )
    lines.append("")
    for i, row in enumerate(batch):
        sig = ", ".join(
            f"{name}={float(row.scores.get(name,0.0)):+.2f}"
            f"(c{float(row.confidences.get(name,0.0)):.1f})"
            for name in _ENGINES
        )
        lines.append(f"market idx={i}: price={float(row.entry_price):.3f} | {sig}")
    return "\n".join(lines)


def _brier(ps: list[float], ys: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / len(ps)


def main(argv: list[str] | None = None) -> int:
    from agent.llm._smoke import _load_dotenv_if_present

    _load_dotenv_if_present()

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--out", type=Path, default=Path("reports/a13/probe_report.json")
    )
    args = ap.parse_args(argv)

    seed = json.loads(
        Path("docs/backtest/value_seed_v3.json").read_text(encoding="utf-8")
    )
    rows = load_rows(Path("reports/backtest/_signal_rows.json"))
    # Deterministic spread sample across the whole universe.
    rng = random.Random(args.seed)
    idxs = sorted(rng.sample(range(len(rows)), min(args.n, len(rows))))
    sample = [rows[i] for i in idxs]

    from agent.llm.factory import RetryLLMClient
    from agent.llm.minimax_client import MiniMaxClient

    llm = RetryLLMClient(inner=MiniMaxClient())

    import asyncio

    market_p: list[float] = []
    linear_p: list[float] = []
    llm_p: list[float] = []
    ys: list[int] = []
    llm_failures = 0

    for start in range(0, len(sample), args.batch):
        batch = sample[start : start + args.batch]
        prompt = _build_prompt(batch)
        try:
            raw = asyncio.run(
                llm.structured_call(model="", prompt=prompt, schema=_PRED_SCHEMA)
            )
            preds = {
                int(p["idx"]): float(p["win_probability"])
                for p in raw.get("predictions", [])
                if isinstance(p, dict) and "idx" in p and "win_probability" in p
            }
        except Exception as exc:  # probe is fail-soft
            print(f"  batch {start}: LLM failed ({type(exc).__name__}); "
                  "falling back to market price for this batch", flush=True)
            preds = {}
            llm_failures += 1
        for i, row in enumerate(batch):
            price = float(row.entry_price)
            market_p.append(price)
            linear_p.append(_linear_p(row, seed))
            llm_p.append(preds.get(i, price))  # missing ⇒ market (neutral)
            ys.append(1 if str(row.outcome).lower() == "yes" else 0)
        print(f"  done {min(start+args.batch, len(sample))}/{len(sample)}",
              flush=True)

    n = len(ys)
    base_rate = sum(ys) / n
    report = {
        "n": n,
        "batch": args.batch,
        "seed": args.seed,
        "llm_batch_failures": llm_failures,
        "yes_base_rate": base_rate,
        "brier": {
            "market_only": _brier(market_p, ys),
            "linear_fusion": _brier(linear_p, ys),
            "llm_fusion": _brier(llm_p, ys),
        },
        # How often the LLM materially deviated from the market (|Δ|>0.03).
        "llm_vs_market_mean_abs_dev": sum(
            abs(lp - m) for lp, m in zip(llm_p, market_p, strict=True)
        ) / n,
        "linear_vs_market_mean_abs_dev": sum(
            abs(lf - m) for lf, m in zip(linear_p, market_p, strict=True)
        ) / n,
    }
    # Bet win-rate: when each estimator's edge clears min_edge, does its side win?
    me = seed["min_edge"]

    def _bet_winrate(ps: list[float]) -> dict[str, Any]:
        wins = bets = 0
        for p, price, y in zip(ps, market_p, ys, strict=True):
            edge = p - price
            if abs(edge) < me:
                continue
            bets += 1
            side_yes = edge > 0
            if (side_yes and y == 1) or (not side_yes and y == 0):
                wins += 1
        return {"bets": bets, "win_rate": (wins / bets) if bets else None}

    report["bet_winrate"] = {
        "linear_fusion": _bet_winrate(linear_p),
        "llm_fusion": _bet_winrate(llm_p),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
