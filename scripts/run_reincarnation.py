"""Run the Phase-2 reincarnation experiment from the committed v3 seed.

The agent lives the SAME 70% chronological training window ``--passes``
times, carrying weights + the EMA learner's inner state across passes (the
gemini provider adds a sanitized strict-advisor "rebirth retrospective" at
each pass boundary), then runs ONE learning-frozen cold-start pass on the
held-out 30% window + the three baselines. v3 physics throughout.

Provider purity (v3 convention): each LLM leg injects its client directly
(``RetryLLMClient(inner=GeminiClient())`` / ``RetryLLMClient(inner=
MiniMaxClient())``) with ``model=""`` so the client self-resolves its own
default — never ``make_llm_client()``.

A9 experiment arms (plan 2026-06-13; treatment provider = MiniMax-M3)::

    # N  — numerical control: the published artifact, never rerun.
    # G0 — kit-off LLM ablation (six-weight advisor + tribute, NO kit):
    python scripts/run_reincarnation.py --provider minimax \\
        --out dashboard/public/backtest/reincarnation_g0.json
    # G1 — full-kit treatment (storm percept + genome + ledger):
    python scripts/run_reincarnation.py --provider minimax --storm \\
        --out dashboard/public/backtest/reincarnation_g1.json
    # G2 — falsification (full kit on the timestamp-shuffled season):
    python scripts/run_reincarnation.py --provider minimax --storm \\
        --shuffle-timestamps-seed 1 \\
        --out dashboard/public/backtest/reincarnation_g2.json

Keys come from ``./.env`` (never committed, never printed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.cached_sweep import load_rows
from agent.backtest.historical_fetcher import load_all_cached_markets
from agent.backtest.reincarnation import (
    run_groundhog_export,
    run_reincarnation_export,
)
from agent.backtest.survival_season import (
    _build_corpus_resolver,
    build_survival_rows,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_v3_numerical import load_v3_seed

# The primary filenames belong to the CURRENT design (groundhog). A v1
# 3-pass rerun writes to the _3pass archive names so it can never overwrite
# the v2 page's input with a schema the v2 validator rejects.
_OUT = {
    "groundhog": {
        "numerical": Path("dashboard/public/backtest/reincarnation.json"),
        "gemini": Path("dashboard/public/backtest/reincarnation_ai.json"),
        "minimax": Path(
            "dashboard/public/backtest/reincarnation_ai_minimax.json"
        ),
    },
    "passes": {
        "numerical": Path("dashboard/public/backtest/reincarnation_3pass.json"),
        "gemini": Path("dashboard/public/backtest/reincarnation_ai_3pass.json"),
        "minimax": Path(
            "dashboard/public/backtest/reincarnation_ai_minimax_3pass.json"
        ),
    },
}
# The v3 realism row floor — rows are LOADED under it and the export
# fail-closed re-validates the same value.
_FLOOR = 0.05


def main(argv: list[str] | None = None) -> int:
    # Keys live in ./.env (never committed); the repo's best-effort loader
    # hydrates os.environ without overriding existing values.
    from agent.llm._smoke import _load_dotenv_if_present

    _load_dotenv_if_present()

    parser = argparse.ArgumentParser(
        description=(
            "Phase-2 reincarnation runner. A9 arms: G0 = --provider "
            "minimax (kit-off LLM ablation); G1 = + --storm (full kit); "
            "G2 = + --storm --shuffle-timestamps-seed N (falsification)."
        )
    )
    parser.add_argument(
        "--provider", choices=("numerical", "gemini", "minimax"), required=True
    )
    parser.add_argument(
        "--design", choices=("groundhog", "passes"), default="groundhog"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--max-incarnations", type=int, default=120)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    # A7: the CLI is the tribute opt-in choke point (library default OFF).
    parser.add_argument("--no-tribute", action="store_true")
    # A9: the ONE explicit kit flag (storm percept + genome vocabulary +
    # counterfactual ledger + participation split + falsification metric).
    parser.add_argument("--storm", action="store_true")
    # A9 K7: the falsification leg's paired time-shift (G2 only).
    parser.add_argument("--shuffle-timestamps-seed", type=int, default=None)
    args = parser.parse_args(argv)

    out = args.out or _OUT[args.design][args.provider]
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
    elif args.provider == "minimax":
        # Provider-pure (v3 convention): direct MiniMaxClient injection,
        # model="" self-resolves to MiniMax-M3. Never make_llm_client().
        from agent.llm.cost_guard import L3CostGuard
        from agent.llm.factory import RetryLLMClient
        from agent.llm.minimax_client import MiniMaxClient

        rebirth_llm = RetryLLMClient(inner=MiniMaxClient())
        rebirth_guard = L3CostGuard.from_env()

    if args.design == "groundhog":
        artifact = run_groundhog_export(
            rows=rows,
            snapshots=snapshots,
            base_seed=seed,
            out_path=out,
            max_incarnations=args.max_incarnations,
            train_fraction=args.train_fraction,
            # The REAL v3 journey knobs — passed explicitly (the
            # orchestrator's own defaults mirror the cheap-test defaults).
            fragile_max_breath_risk_pct=0.95,
            loss_multiplier=5.0,
            initial_breath=35.0,
            holdout_max_lives=12,
            entry_price_floor=_FLOOR,
            rebirth_llm=rebirth_llm,  # type: ignore[arg-type]
            rebirth_guard=rebirth_guard,
            rebirth_model="",  # each client self-resolves its OWN default
            tribute=not args.no_tribute,
            storm=args.storm,
            shuffle_timestamps_seed=args.shuffle_timestamps_seed,
        )
        for inc in artifact["incarnations"]:
            note = " note=yes" if inc["rebirth_note"] else ""
            paid = inc.get("tributes_paid", 0.0)
            trib = (
                f" tributes=${paid:.0f}({len(inc['tributes'])})"
                if paid
                else ""
            )
            fate = (
                "SURVIVED"
                if not inc["died"]
                else f"died@{inc['settled']} settled"
            )
            print(
                f"inc {inc['incarnation']}: {fate} "
                f"progress={inc['progress_pct']:.1f}% "
                f"pnl_at_death=${inc['pnl_at_death']:.2f} "
                f"-> scored ${inc['scored_pnl']:.2f}{trib}{note}",
                flush=True,
            )
        r = artifact["rebirth"]
        print(
            f"verdict: survived={artifact['survived']} "
            f"at_incarnation={artifact['surviving_incarnation']} "
            f"headline=${artifact['headline_pnl']:.2f} "
            f"gods=${artifact.get('gods_revenue', 0.0):.0f} | rebirth "
            f"calls={r['calls']}/{r['expected']} productive={r['productive']} "
            f"applied={r['applied']}",
            flush=True,
        )
        fm = artifact.get("falsification_metric")
        if fm is not None:
            # Neutral readout — the arm-specific verdict (G1 wants γ to
            # move, G2 wants γ≈0) belongs to the page, not the runner.
            print(
                f"falsification_metric: {fm['key']}={fm['value']:+.3f} "
                f"(threshold {fm['threshold']:g}) "
                f"productive_calls={fm['productive_calls']}/"
                f"{fm['min_productive_required']} "
                + (
                    "evaluable"
                    if fm["evaluable"]
                    else "NOT evaluable -> INCONCLUSIVE"
                ),
                flush=True,
            )
    else:
        artifact = run_reincarnation_export(
            rows=rows,
            snapshots=snapshots,
            base_seed=seed,
            out_path=out,
            passes=args.passes,
            train_fraction=args.train_fraction,
            fragile_max_breath_risk_pct=0.95,
            loss_multiplier=5.0,
            initial_breath=35.0,
            max_lives=12,
            entry_price_floor=_FLOOR,
            rebirth_llm=rebirth_llm,  # type: ignore[arg-type]
            rebirth_guard=rebirth_guard,
            rebirth_model="",
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
