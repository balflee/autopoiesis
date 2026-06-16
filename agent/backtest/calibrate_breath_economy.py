"""Sim-based JOINT calibration of the breath economy (Active Survival Hand 1, R9).

Phase-4 execution proved a single ``loss_multiplier`` recalibration on a
constant-signal world CANNOT make honest edge survive better than noise — the
agent can't select on a constant signal, and aggressive sizing (0.95) makes one
amplified loss fatal before edge compounds. The validated fix jointly tunes the
breath economy on a VARYING-edge world (which the agent CAN select on), running
the FULL tithe+tribute groundhog economy:

    sweep  (loss_multiplier, fragile_max_breath_risk_pct, initial_breath)
    on     a +edge world (gain>0, predictive) and a 0-edge noise world (gain=0)
    pick   params where death_rate(+edge) ≪ death_rate(noise)

Validated sweet spot: gain=0.5, breath≈70, fragile≈0.15–0.2, m 1.2–1.5 →
+edge death-rate 0.00, noise 1.00. The recommended ``fragile_max_breath_risk_pct``
is TAME (≈0.2), NOT the deployment 0.95 — that tame sizing IS the load-bearing
lever (the static form of A14's intent).

Numerical-only (``rebirth_llm=None``) — NO LLM, NO API keys.

Run: ``python -m agent.backtest.calibrate_breath_economy [--out PATH]``
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import tempfile
from itertools import product
from pathlib import Path
from statistics import mean

from agent.backtest.reincarnation import run_groundhog_export
from agent.backtest.survival_metrics import groundhog_metric
from agent.backtest.synthetic_edge import build_varying_world
from scripts.run_v3_numerical import load_v3_seed

#: How predictive the +edge world's signal is (gain=0 ⇒ pure noise control).
DEFAULT_EDGE_GAIN = 0.5
#: A small non-zero exploration floor recommended into the deployed seed.
DEFAULT_EXPLORATION_EPSILON = 0.05


@dataclasses.dataclass(frozen=True)
class CalibrationResult:
    """The recommended breath economy + the full sweep grid for the report."""

    loss_multiplier: float
    fragile_max_breath_risk_pct: float
    initial_breath: float
    exploration_epsilon: float
    edge_death_rate: float
    noise_death_rate: float
    grid: list[dict[str, float]]


def _mean_death_rate(
    gain: float,
    m: float,
    frac: float,
    ib: float,
    *,
    base_seed,
    n_rows: int,
    max_incarnations: int,
    seeds: tuple[int, ...],
) -> float:
    """Mean groundhog death-rate over ``seeds`` for one (gain, m, frac, ib) cell.

    Runs the FULL economy (tithe + tribute on), numerical (no LLM).
    """
    out = Path(tempfile.gettempdir()) / "calibrate_breath_economy.json"
    rates: list[float] = []
    for sd in seeds:
        rows, snaps = build_varying_world(n_rows, gain, sd)
        artifact = run_groundhog_export(
            rows=rows,
            snapshots=snaps,
            base_seed=base_seed,
            out_path=out,
            max_incarnations=max_incarnations,
            loss_multiplier=m,
            initial_breath=ib,
            fragile_max_breath_risk_pct=frac,
            rebirth_llm=None,
            rebirth_guard=None,
            preflight=False,
            tribute=True,
            divine_tithe=True,
        )
        rates.append(groundhog_metric(artifact)["death_rate"])
    return mean(rates)


def calibrate(
    *,
    loss_multiplier_grid,
    fragile_grid,
    initial_breath_grid,
    edge_gain: float = DEFAULT_EDGE_GAIN,
    n_rows: int = 300,
    max_incarnations: int = 6,
    seeds=(0, 1, 2),
    exploration_epsilon: float = DEFAULT_EXPLORATION_EPSILON,
) -> CalibrationResult:
    """Jointly calibrate the breath economy on the VARYING-edge world.

    For each ``(loss_multiplier, fragile_max_breath_risk_pct, initial_breath)``
    cell, measure the groundhog death-rate on a +edge (``edge_gain``) world and a
    0-edge noise world. A cell is FEASIBLE when honest edge survives more than
    noise: ``edge_death_rate < 0.5 AND noise_death_rate > 0.5``. Recommend the
    feasible cell with the largest separation. Raise ``ValueError`` if none
    separates (a surfaced result — widen the grid / lower breath, NOT a crash).
    """
    seeds = tuple(seeds)
    base_seed = dataclasses.replace(load_v3_seed(), exploration_epsilon=0.0)
    grid: list[dict[str, float]] = []
    for m, frac, ib in product(
        loss_multiplier_grid, fragile_grid, initial_breath_grid
    ):
        edge_dr = _mean_death_rate(
            edge_gain, m, frac, ib, base_seed=base_seed, n_rows=n_rows,
            max_incarnations=max_incarnations, seeds=seeds,
        )
        noise_dr = _mean_death_rate(
            0.0, m, frac, ib, base_seed=base_seed, n_rows=n_rows,
            max_incarnations=max_incarnations, seeds=seeds,
        )
        grid.append({
            "loss_multiplier": m,
            "fragile_max_breath_risk_pct": frac,
            "initial_breath": ib,
            "edge_death_rate": edge_dr,
            "noise_death_rate": noise_dr,
            "separation": noise_dr - edge_dr,
        })

    feasible = [
        g for g in grid
        if g["edge_death_rate"] < 0.5 and g["noise_death_rate"] > 0.5
    ]
    if not feasible:
        raise ValueError(
            "no (loss_multiplier, fragile, initial_breath) cell separates honest "
            "edge from noise (edge_dr<0.5 AND noise_dr>0.5) — widen the grid / "
            "lower initial_breath / raise edge_gain. This is a surfaced result."
        )
    best = max(feasible, key=lambda g: g["separation"])
    return CalibrationResult(
        loss_multiplier=best["loss_multiplier"],
        fragile_max_breath_risk_pct=best["fragile_max_breath_risk_pct"],
        initial_breath=best["initial_breath"],
        exploration_epsilon=exploration_epsilon,
        edge_death_rate=best["edge_death_rate"],
        noise_death_rate=best["noise_death_rate"],
        grid=grid,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="reports/calibration/breath_economy_hand1.json"
    )
    args = parser.parse_args()

    # Grid centered on the execution-validated sweet spot; small so the sweep is
    # minutes (numerical, no LLM).
    result = calibrate(
        loss_multiplier_grid=[1.2, 1.5],
        fragile_grid=[0.15, 0.2, 0.25],
        initial_breath_grid=[50, 70],
        seeds=(0, 1, 2, 3),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(result), indent=2), encoding="utf-8")
    print(
        f"recommended: loss_multiplier={result.loss_multiplier} "
        f"fragile_max_breath_risk_pct={result.fragile_max_breath_risk_pct} "
        f"initial_breath={result.initial_breath} "
        f"(edge_dr={result.edge_death_rate:.2f} noise_dr={result.noise_death_rate:.2f}); "
        f"wrote {out}"
    )


if __name__ == "__main__":
    main()
