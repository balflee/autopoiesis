"""Build the committed Stage-1 "能学" (can-learn) fixture for the /survival panel.

Merges the gitignored reports/learning_demo/{pilot_frozen_ema,minimax}.json into
ONE committed dashboard/public/stage1/stage1_learning.json (static-imported by
dashboard/lib/load_learning_demo.ts). Also bakes in two things that are NOT in the
raw run JSON: the gain->survival->net_vs_seed table (docs/divinity-mechanism-spec.md
§4.2) and the EMA seed-1 weight-ratchet + MiniMax rationale (§6 + a captured
inspection run).

Mirrors dashboard/scripts/build_static_sweep.py: committed output, regenerate (do
NOT hand-edit). The output is tiny (~few KB) and travels on a plain `git push`.

Run: python dashboard/scripts/build_stage1.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (code/)
PILOT = ROOT / "reports" / "learning_demo" / "pilot_frozen_ema.json"
MINIMAX = ROOT / "reports" / "learning_demo" / "minimax.json"
OUT = ROOT / "dashboard" / "public" / "stage1" / "stage1_learning.json"

SCHEMA_VERSION = "0.1.0"

ARM_LABEL = {
    "frozen": "Frozen — no learning (null)",
    "ema": "EMA — numerical learner",
    "minimax": "MiniMax — LLM self-evolution",
}


def _arm(arm_obj: dict, label: str) -> dict:
    agg = arm_obj["aggregate"]
    return {
        "label": label,
        "survival_rate": agg["survival_rate"],
        "mean_best_progress_pct": agg["mean_best_progress_pct"],
        "mean_rise": agg["mean_rise"],
        "mean_surviving_incarnation": agg["mean_surviving_incarnation"],
        # Per-seed progress-% curves (variable length — each is one life-line).
        "curves": [s["progress_pct"] for s in arm_obj["per_seed"]],
    }


def main() -> None:
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    mm = json.loads(MINIMAX.read_text(encoding="utf-8"))
    cfg = pilot["config"]

    fixture = {
        "schema_version": SCHEMA_VERSION,
        "config": {
            "gain": cfg["gain"],
            "n_rows": cfg["n_rows"],
            "max_incarnations": cfg["max_incarnations"],
            "edge_engine": cfg["edge_engine"],
            "seeds": cfg["seeds"],
            "economy": cfg["economy"],
        },
        "arms": {
            "frozen": _arm(pilot["arms"]["frozen"], ARM_LABEL["frozen"]),
            "ema": _arm(pilot["arms"]["ema"], ARM_LABEL["ema"]),
            "minimax": _arm(mm["arms"]["minimax"], ARM_LABEL["minimax"]),
        },
        # docs/divinity-mechanism-spec.md §6 + a captured EMA seed-1 run: the
        # learner ratchets the hidden-edge fusion slot up while cutting the
        # over-trusted noise slot, until it crosses the survival threshold.
        "weight_trajectory": {
            "edge_slot_label": "edge slot α₂ (market_momentum key) — where the hidden edge lives",
            "noise_slot_label": "over-trusted slot α₃ (surface_advantage key)",
            "incarnations": [0, 1, 2, 3, 4, 5],
            "edge_weight": [0.070, 0.082, 0.095, 0.140, 0.191, 0.265],
            "noise_weight": [0.752, 0.709, 0.674, 0.608, 0.572, 0.533],
            "survived_at": 5,
            "minimax_quote": (
                "alpha_2 is overwhelmingly dominant, meaning the agent is "
                "over-relying on a single signal class that produced 6 losses."
            ),
        },
        # docs/divinity-mechanism-spec.md §4.2 — the deployed (locked) economy
        # swept over edge strength: survival rises with edge and the god's
        # net_vs_seed flips positive at gain≈0.2.
        "gain_sweep": [
            {"gain": 0.0, "death_rate": 1.00, "survival_rate": 0.0, "net_vs_seed": -155},
            {"gain": 0.1, "death_rate": 0.75, "survival_rate": 0.25, "net_vs_seed": -45},
            {"gain": 0.2, "death_rate": 0.25, "survival_rate": 0.75, "net_vs_seed": 15},
            {"gain": 0.3, "death_rate": 0.25, "survival_rate": 0.75, "net_vs_seed": 15},
            {"gain": 0.5, "death_rate": 0.00, "survival_rate": 1.0, "net_vs_seed": 100},
        ],
        "caveat": (
            "The edge here is SYNTHETIC — injected into one fusion slot as a known "
            "test target. This proves the LEARNING MACHINERY works (it discovers and "
            "up-weights a hidden edge); whether a REAL edge exists in live markets is "
            "Stage 2's job — public-info backtest edge came back NO-GO."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
