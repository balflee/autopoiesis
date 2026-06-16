"""Run the v3 NUMERICAL survival journey from the committed value seed.

Reads the canonical seed from ``docs/backtest/value_seed_v3.json`` (written by
``python -m agent.backtest.validate_value_seed``; COMMITTED so a clean
checkout reproduces v3 — review r4 M-2) and drives ``run_survival_export``
with the chapter-2/3 journey knobs. The v3 physics (side-correct payouts,
value-mode decisions, effective floor) are the export's DEFAULTS.

Usage::

    python scripts/run_v3_numerical.py [--out dashboard/public/backtest/survival_journey.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.survival_season import run_survival_export
from agent.core.state import Weights

SEED_PATH = Path("docs/backtest/value_seed_v3.json")


def load_v3_seed(path: Path = SEED_PATH) -> StrategyConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return StrategyConfig(
        weights=Weights(**raw["weights"]),
        max_breath_risk_pct=raw["max_breath_risk_pct"],
        min_confidence=raw["min_confidence"],
        min_bet_size_usd=raw["min_bet_size_usd"],
        min_edge=raw["min_edge"],
        kappa=raw["kappa"],
        kappa_xm=raw.get("kappa_xm", 0.0),
        exploration_epsilon=raw.get("exploration_epsilon", 0.0),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dashboard/public/backtest/survival_journey.json"),
    )
    args = parser.parse_args(argv)

    seed = load_v3_seed()
    journey = run_survival_export(
        rows_path=Path("reports/backtest/_signal_rows.json"),
        cache_dir=Path("agent/backtest/_cache_tennis"),
        out_path=args.out,
        base_seed=seed,
        fragile_max_breath_risk_pct=0.95,
        loss_multiplier=5.0,
        initial_breath=35.0,
        max_lives=12,
    )
    s = journey["summary"]
    print(
        f"wrote {args.out} — lives={s['lives']} deaths={s['deaths']} "
        f"learner=${s['learner_final_pnl']:.2f} "
        f"static=${s['static_final_pnl']:.2f} "
        f"delta=${s['learning_vs_static_delta']:+.2f} "
        f"side_correct={s['side_correct_pricing']} value={s['value_betting']} "
        f"eff_floor={s['effective_entry_price_floor']} "
        f"min_eff={s['min_effective_entry_price']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
