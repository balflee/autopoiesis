"""Run the Phase-2 reincarnation experiment from the committed v3 seed.

The agent lives the SAME 70% chronological training window ``--passes``
times, carrying weights + the EMA learner's inner state across passes (the
gemini provider adds a sanitized strict-advisor "rebirth retrospective" at
each pass boundary), then runs ONE learning-frozen cold-start pass on the
held-out 30% window + the three baselines. v3 physics throughout.

Provider purity (v3 convention): the gemini leg injects
``RetryLLMClient(inner=GeminiClient())`` directly with ``model=""`` so the
client self-resolves its own default — never ``make_llm_client()``.

Usage::

    python scripts/run_reincarnation.py --provider numerical
    python scripts/run_reincarnation.py --provider gemini

Keys come from ``./.env`` (never committed, never printed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.cached_sweep import load_rows
from agent.backtest.historical_fetcher import load_all_cached_markets
from agent.backtest.reincarnation import run_reincarnation_export
from agent.backtest.survival_season import (
    _build_corpus_resolver,
    build_survival_rows,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_v3_numerical import load_v3_seed

_OUT = {
    "numerical": Path("dashboard/public/backtest/reincarnation.json"),
    "gemini": Path("dashboard/public/backtest/reincarnation_ai.json"),
}
# The v3 realism row floor — rows are LOADED under it and the export
# fail-closed re-validates the same value.
_FLOOR = 0.05


def main(argv: list[str] | None = None) -> int:
    # Keys live in ./.env (never committed); the repo's best-effort loader
    # hydrates os.environ without overriding existing values.
    from agent.llm._smoke import _load_dotenv_if_present

    _load_dotenv_if_present()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider", choices=("numerical", "gemini"), required=True
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    args = parser.parse_args(argv)

    out = args.out or _OUT[args.provider]
    seed = load_v3_seed()
    rows_raw = load_rows(Path("reports/backtest/_signal_rows.json"))
    snapshots = load_all_cached_markets(
        cache_dir=Path("agent/backtest/_cache_tennis")
    )
    rows = build_survival_rows(
        rows_raw, snapshots, _build_corpus_resolver(), entry_price_floor=_FLOOR
    )

    rebirth_llm = None
    rebirth_guard = None
    if args.provider == "gemini":
        from agent.llm.cost_guard import L3CostGuard
        from agent.llm.factory import RetryLLMClient
        from agent.llm.gemini_client import GeminiClient

        rebirth_llm = RetryLLMClient(inner=GeminiClient())
        rebirth_guard = L3CostGuard.from_env()

    artifact = run_reincarnation_export(
        rows=rows,
        snapshots=snapshots,
        base_seed=seed,
        out_path=out,
        passes=args.passes,
        train_fraction=args.train_fraction,
        # The REAL v3 journey knobs — passed explicitly (the orchestrator's
        # own defaults mirror run_survival_export's cheap-test defaults).
        fragile_max_breath_risk_pct=0.95,
        loss_multiplier=5.0,
        initial_breath=35.0,
        max_lives=12,
        entry_price_floor=_FLOOR,
        rebirth_llm=rebirth_llm,  # type: ignore[arg-type]
        rebirth_guard=rebirth_guard,
        rebirth_model="",  # each client self-resolves its OWN default
    )

    for p in artifact["passes"]:
        s = p["summary"]
        note = " note=yes" if p["rebirth_note"] else ""
        print(
            f"pass {p['pass']}: pnl=${s['pnl']:.2f} deaths={s['deaths']} "
            f"lives={s['lives']} settled={s['settled']} "
            f"coverage={s['coverage_pct']:.1f}% win={s['win_rate']:.3f}"
            f"{note}",
            flush=True,
        )
    h = artifact["holdout"]
    hs = h["summary"]
    b = h["baselines"]
    print(
        f"holdout (frozen): pnl=${hs['pnl']:.2f} deaths={hs['deaths']} "
        f"lives={hs['lives']} settled={hs['settled']} "
        f"coverage={hs['coverage_pct']:.1f}% win={hs['win_rate']:.3f} | "
        f"static=${b['static']:.2f} random=${b['random']:.2f} "
        f"favorite=${b['always_favorite']:.2f}",
        flush=True,
    )
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
