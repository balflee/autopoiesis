"""Run a v3 AI survival journey with a PROVIDER-PURE client.

Realism v3 / review r2 M-2 + r3 M-2 + r6 H-1: each leg injects its provider
client DIRECTLY — never ``make_llm_client()``, which returns the
Gemini-primary fallback composite whenever ``MINIMAX_API_KEY`` is set (that
would mislabel the MiniMax leg AND make both legs near-identical Gemini
runs, destroying the provider comparison). ``AISeasonContext.model=""``
threads the empty-string convention into every LLM-consuming construction
(advisor, reflection, preflight) so each client self-resolves its OWN
default model id.

Usage::

    python scripts/run_v3_ai.py --provider minimax
    python scripts/run_v3_ai.py --provider gemini

Keys come from the environment (`.env` loaded by the clients themselves);
they are never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.survival_season import (
    AISeasonContext,
    run_survival_export,
)
from agent.llm.cost_guard import L3CostGuard
from agent.llm.factory import RetryLLMClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_v3_numerical import load_v3_seed

_OUT = {
    "minimax": Path("dashboard/public/backtest/survival_journey_ai.json"),
    "gemini": Path("dashboard/public/backtest/survival_journey_ai_gemini.json"),
}


def _provider_client(provider: str) -> object:
    """Build the provider-pure, retry-wrapped client."""
    if provider == "minimax":
        from agent.llm.minimax_client import MiniMaxClient

        return RetryLLMClient(inner=MiniMaxClient())
    from agent.llm.gemini_client import GeminiClient

    return RetryLLMClient(inner=GeminiClient())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("minimax", "gemini"), required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out = args.out or _OUT[args.provider]
    seed = load_v3_seed()
    ai = AISeasonContext(
        _provider_client(args.provider),  # type: ignore[arg-type]
        L3CostGuard.from_env(),
        120,
        120,
        model="",  # each client self-resolves its OWN default (r6 H-1)
    )
    journey = run_survival_export(
        rows_path=Path("reports/backtest/_signal_rows.json"),
        cache_dir=Path("agent/backtest/_cache_tennis"),
        out_path=out,
        base_seed=seed,
        fragile_max_breath_risk_pct=0.95,
        loss_multiplier=5.0,
        initial_breath=35.0,
        max_lives=12,
        ai=ai,
        require_applied_deltas=True,
        preflight=True,
    )
    s = journey["summary"]
    print(
        f"wrote {out} [{args.provider}] — lives={s['lives']} deaths={s['deaths']} "
        f"learner=${s['learner_final_pnl']:.2f} "
        f"delta=${s['learning_vs_static_delta']:+.2f} "
        f"applied={s['proposals_applied']} failed={s['proposals_apply_failed']} "
        f"side_correct={s['side_correct_pricing']} value={s['value_betting']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
