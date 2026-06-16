"""能学 (can-learn) demo — does the agent IMPROVE across lives on a world whose
edge is HIDDEN in one under-weighted engine?

The locked breath economy (Active Survival Hand 1) proved the agent 能活
(survives when a selectable edge exists). This demo is the last Stage-1 piece:
proving 能学 (self-evolution) — that a learner DISCOVERS which engine carries
the edge and up-weights it across reincarnations, while a frozen prior can't.

World: ``build_subset_edge_world`` hides the predictive signal in ONE engine the
v3 prior under-weights (tennis_technical, α[0]=0.177); the other four engines are
independent noise. So the static prior fuses a noise-dominated signal and dies;
only a learner that raises the edge engine's weight survives.

Arms (all on the LOCKED economy: loss_multiplier=1.2, fragile=0.15, breath=70,
tithe+tribute on, exploration_epsilon=0.05; agent starts from value_seed_v3):

* ``frozen``  — learning_enabled=False, no LLM. The NULL: weights never adapt.
* ``ema``     — numerical EMA (death-blind), no LLM. The free informed baseline.
* ``minimax`` — the LLM rebirth advisor (death-aware self-evolution). aux_llm
                OFF, so MiniMax is spent on the LEARNING advisor ONLY (tribute
                numerical, prayer skipped) — the 精简 cost lever.

Free numerical arms (frozen / ema) run in seconds and TUNE the world before any
MiniMax spend. Keys come from ./.env (never committed, never printed).

Examples::

    # FREE pilot (tune the world; no LLM, no key):
    python scripts/run_learning_demo.py --arms frozen,ema --seeds 0,1,2,3,4

    # MiniMax learner arm (slow reasoning model; runs in the background):
    python scripts/run_learning_demo.py --arms minimax --provider minimax \\
        --seeds 0,1,2,3,4 --out reports/learning_demo/minimax.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.reincarnation import run_groundhog_export
from agent.backtest.survival_metrics import aggregate_curves, learning_curve
from agent.backtest.synthetic_edge import build_subset_edge_world

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_v3_numerical import load_v3_seed

# The LOCKED breath economy (reports/calibration/breath_economy_hand1.json).
_LOSS_MULTIPLIER = 1.2
_FRAGILE_MAX_BREATH_RISK_PCT = 0.15
_INITIAL_BREATH = 70.0
_EXPLORATION_EPSILON = 0.05

#: arm -> (learning_enabled, uses_llm). uses_llm=True consumes the rebirth_llm.
_ARMS: dict[str, tuple[bool, bool]] = {
    "frozen": (False, False),
    "ema": (True, False),
    "minimax": (True, True),
}


def _base_seed():
    """value_seed_v3 prior + the locked exploration floor (so it keeps sampling)."""
    return dataclasses.replace(
        load_v3_seed(), exploration_epsilon=_EXPLORATION_EPSILON
    )


def run_arm(
    arm: str,
    *,
    seeds: list[int],
    gain: float,
    n_rows: int,
    max_incarnations: int,
    edge_engine: str,
    rebirth_llm: Any | None = None,
    rebirth_guard: Any | None = None,
    rebirth_model: str = "",
) -> dict[str, Any]:
    """Run one arm over every seed; return per-seed curves + the aggregate."""
    learning_enabled, uses_llm = _ARMS[arm]
    if uses_llm and rebirth_llm is None:
        raise ValueError(f"arm {arm!r} needs a rebirth_llm but none was given")
    base = _base_seed()
    curves: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"learndemo_{arm}_") as tmp:
        for sd in seeds:
            rows, snaps = build_subset_edge_world(
                n_rows, gain, sd, edge_engine=edge_engine
            )
            artifact = run_groundhog_export(
                rows=rows,
                snapshots=snaps,
                base_seed=base,
                out_path=Path(tmp) / f"seed_{sd}.json",
                max_incarnations=max_incarnations,
                loss_multiplier=_LOSS_MULTIPLIER,
                initial_breath=_INITIAL_BREATH,
                fragile_max_breath_risk_pct=_FRAGILE_MAX_BREATH_RISK_PCT,
                learning_enabled=learning_enabled,
                rebirth_llm=rebirth_llm if uses_llm else None,
                rebirth_guard=rebirth_guard if uses_llm else None,
                rebirth_model=rebirth_model,
                # MiniMax is spent on the learning advisor ONLY.
                aux_llm=False,
                preflight=uses_llm,
                tribute=True,
                divine_tithe=True,
            )
            curve = learning_curve(artifact)
            curves.append(curve)
            surv = (
                f"SURVIVED@{curve['surviving_incarnation']}"
                if curve["survived"]
                else "died-all"
            )
            print(
                f"  [{arm}] seed {sd}: {surv} "
                f"best_progress={curve['best_progress_pct']:.1f}% "
                f"rise={curve['rise']:+.1f} "
                f"n_inc={curve['n_incarnations']}",
                flush=True,
            )
    return {"per_seed": curves, "aggregate": aggregate_curves(curves)}


def _make_rebirth_llm(provider: str):
    """Provider-pure client injection (v3 convention) + run-scoped cost guard."""
    from agent.llm.cost_guard import L3CostGuard
    from agent.llm.factory import RetryLLMClient

    if provider == "minimax":
        from agent.llm.minimax_client import MiniMaxClient

        return RetryLLMClient(inner=MiniMaxClient()), L3CostGuard.from_env()
    if provider == "gemini":
        from agent.llm.gemini_client import GeminiClient

        return RetryLLMClient(inner=GeminiClient()), L3CostGuard.from_env()
    raise ValueError(f"unknown provider {provider!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arms", default="frozen,ema",
        help="comma list of frozen,ema,minimax",
    )
    parser.add_argument(
        "--provider", choices=("minimax", "gemini"), default="minimax",
        help="LLM provider for the 'minimax' arm",
    )
    parser.add_argument("--seeds", default="0,1,2,3,4")
    parser.add_argument("--gain", type=float, default=0.5)
    parser.add_argument("--n-rows", type=int, default=400)
    parser.add_argument("--max-incarnations", type=int, default=20)
    parser.add_argument(
        "--edge-engine", default="market_momentum",
        help="engine the predictive signal hides in (the validated demo world "
        "buries it in market_momentum, the v3 prior's LEAST-weighted engine)",
    )
    parser.add_argument(
        "--out", type=Path,
        default=Path("reports/learning_demo/learning_demo.json"),
    )
    args = parser.parse_args(argv)

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in arms if a not in _ARMS]
    if bad:
        parser.error(f"unknown arm(s) {bad}; choose from {sorted(_ARMS)}")
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    rebirth_llm = rebirth_guard = None
    rebirth_model = ""
    if "minimax" in arms:
        # Keys live in ./.env (never committed / printed).
        from agent.llm._smoke import _load_dotenv_if_present

        _load_dotenv_if_present()
        rebirth_llm, rebirth_guard = _make_rebirth_llm(args.provider)

    print(
        f"能学 demo: arms={arms} seeds={seeds} gain={args.gain} "
        f"n_rows={args.n_rows} max_incarnations={args.max_incarnations}",
        flush=True,
    )
    results: dict[str, Any] = {}
    for arm in arms:
        print(f"arm: {arm}", flush=True)
        results[arm] = run_arm(
            arm,
            seeds=seeds,
            gain=args.gain,
            n_rows=args.n_rows,
            max_incarnations=args.max_incarnations,
            edge_engine=args.edge_engine,
            rebirth_llm=rebirth_llm,
            rebirth_guard=rebirth_guard,
            rebirth_model=rebirth_model,
        )

    report = {
        "experiment": "learning_demo",
        "config": {
            "arms": arms,
            "provider": args.provider if "minimax" in arms else None,
            "seeds": seeds,
            "gain": args.gain,
            "n_rows": args.n_rows,
            "max_incarnations": args.max_incarnations,
            "edge_engine": args.edge_engine,
            "economy": {
                "loss_multiplier": _LOSS_MULTIPLIER,
                "fragile_max_breath_risk_pct": _FRAGILE_MAX_BREATH_RISK_PCT,
                "initial_breath": _INITIAL_BREATH,
                "exploration_epsilon": _EXPLORATION_EPSILON,
                "tithe": True,
                "tribute": True,
            },
        },
        "arms": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== 能学 demo summary (survival_rate | mean_best_progress | mean_rise) ===")
    for arm in arms:
        agg = results[arm]["aggregate"]
        si = agg["mean_surviving_incarnation"]
        si_str = f"{si:.1f}" if si is not None else "—"
        print(
            f"  {arm:8s}: survive={agg['survival_rate']:.0%}  "
            f"best_progress={agg['mean_best_progress_pct']:.1f}%  "
            f"rise={agg['mean_rise']:+.1f}  surv_inc={si_str}",
            flush=True,
        )
    print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
