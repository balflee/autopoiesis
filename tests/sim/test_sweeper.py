"""Sprint_2 (T-C-002) tests for :mod:`sim.sweeper` + the CLI sweep
subcommand.

Acceptance criteria covered:

* ``Sweeper.run(n=N)`` yields exactly N combos × (3 mandatory + 1 control)
  archetypes × ``lifetimes_per_archetype`` lifetimes total.
* The written ``sweep_<ts>.json`` has the documented schema shape
  (top-level keys + per-combo shape + summary_stats).
* ``calibration_objective_params_referenced`` enumerates the LHS dims
  (T-C-002 calib_objectives gate: ≥4 of PRD §14.1 parameters).
* The sweeper rejects an ``archetypes`` argument missing any of the
  three mandatory archetypes (Hard Rule #2).
* ``python -m sim.cli sweep --n 4`` runs end-to-end and writes a
  valid JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sim.params import LHS_DIMS, ParamSpace
from sim.sweeper import SWEEP_SCHEMA_VERSION, Sweeper


def test_sweep_produces_expected_number_of_combos() -> None:
    sweeper = Sweeper(
        base_params=ParamSpace(),
        lifetimes_per_archetype=2,
        max_ticks=300,
    )
    report = sweeper.run(n=4, seed=0)
    assert report.n_combos == 4
    assert len(report.combos) == 4
    # Each combo: (3 mandatory + 1 control) × 2 lifetimes = 8 lifetimes.
    for combo in report.combos:
        assert len(combo.lifetimes) == 4 * 2


def test_sweep_writes_valid_schema(tmp_path: Path) -> None:
    sweeper = Sweeper(
        base_params=ParamSpace(),
        lifetimes_per_archetype=2,
        max_ticks=200,
    )
    report = sweeper.run(n=3, seed=42)
    path = sweeper.write_report(report, tmp_path)
    assert path.exists()
    assert path.stat().st_size > 100  # non-empty

    payload = json.loads(path.read_text(encoding="utf-8"))
    # Top-level required keys.
    expected = {
        "schema_version",
        "sweep_id",
        "seed",
        "n_combos",
        "lifetimes_per_archetype",
        "archetypes",
        "param_dims",
        "calibration_objective_params_referenced",
        "summary_stats",
        "combos",
    }
    assert expected <= set(payload.keys())
    assert payload["schema_version"] == SWEEP_SCHEMA_VERSION
    assert payload["n_combos"] == 3
    assert len(payload["combos"]) == 3

    # ≥4 PRD §14.1 parameters referenced (calib_objectives gate).
    referenced = set(payload["calibration_objective_params_referenced"])
    mandatory = {
        "e_decision_tax",
        "e_time_tax_per_tick",
        "soft_cap_threshold",
        "desperate_threshold",
    }
    assert mandatory <= referenced, (
        f"calib_objectives gate: missing {mandatory - referenced}"
    )

    # Per-combo shape spot-check.
    combo0 = payload["combos"][0]
    assert {"combo_index", "params", "results", "archetype_stats"} <= set(
        combo0.keys()
    )
    # Every LHS dim must be present in the persisted params block.
    for dim in LHS_DIMS:
        assert dim in combo0["params"]


def test_sweeper_rejects_missing_mandatory_archetype() -> None:
    """Track C Hard Rule #2 — dropping Pessimist / Optimist / Satisficer
    must fail at construction time."""
    with pytest.raises(ValueError, match="mandatory"):
        Sweeper(archetypes=("optimist", "satisficer"))  # missing pessimist


def test_sweeper_rejects_zero_n() -> None:
    sweeper = Sweeper()
    with pytest.raises(ValueError, match="positive int"):
        sweeper.run(n=0, seed=0)


def test_sweeper_run_is_byte_identical_for_same_seed() -> None:
    """The acceptance criterion 'All numerical results are deterministic
    given the same seed (test asserts byte-identical re-run)' MUST hold
    for the full sweeper, not just for individual lifetimes. Catches
    regressions where a process-randomised primitive (``hash(str)`` with
    PYTHONHASHSEED) leaks into life-seed derivation.
    """
    sweeper_a = Sweeper(lifetimes_per_archetype=2, max_ticks=200)
    sweeper_b = Sweeper(lifetimes_per_archetype=2, max_ticks=200)
    r1 = sweeper_a.run(n=3, seed=99)
    r2 = sweeper_b.run(n=3, seed=99)
    # Compare the full report payload except sweep_id (it embeds the
    # wall-clock and is the ONE field exempted from the determinism
    # receipt, per the SweepReport docstring).
    d1 = r1.to_dict()
    d2 = r2.to_dict()
    d1.pop("sweep_id")
    d2.pop("sweep_id")
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)


def test_sweeper_run_is_byte_identical_across_processes(tmp_path: Path) -> None:
    """Cross-process variant of the determinism test. Catches regressions
    that the same-process test would mask — e.g. ``hash(str)`` with
    PYTHONHASHSEED was a real T-C-002 round-2 bug found exactly this way."""
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    args = [
        sys.executable, "-m", "sim.cli", "sweep",
        "--n", "3", "--seed", "13",
        "--lifetimes-per-archetype", "2", "--max-ticks", "150",
    ]
    proc_a = subprocess.run(args + ["--output", str(out_a)], capture_output=True, text=True, check=True)
    proc_b = subprocess.run(args + ["--output", str(out_b)], capture_output=True, text=True, check=True)
    assert proc_a.returncode == 0 and proc_b.returncode == 0
    file_a = next(out_a.glob("sweep_*.json"))
    file_b = next(out_b.glob("sweep_*.json"))
    payload_a = json.loads(file_a.read_text(encoding="utf-8"))
    payload_b = json.loads(file_b.read_text(encoding="utf-8"))
    payload_a.pop("sweep_id")
    payload_b.pop("sweep_id")
    assert json.dumps(payload_a, sort_keys=True) == json.dumps(payload_b, sort_keys=True), (
        "cross-process sweep output differs — a non-deterministic primitive "
        "(likely hash(str) with PYTHONHASHSEED) leaked into seed derivation"
    )


def test_cli_sweep_subprocess_writes_file(tmp_path: Path) -> None:
    """End-to-end gate: `python -m sim.cli sweep --n 4` writes a JSON
    file under --output and exits 0. Mirrors the T-C-002 acceptance
    criterion 'produces a valid sweep_<ts>.json on a clean checkout'."""
    out_dir = tmp_path / "calibration"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sim.cli",
            "sweep",
            "--n", "4",
            "--output", str(out_dir),
            "--seed", "0",
            "--lifetimes-per-archetype", "2",
            "--max-ticks", "200",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "WROTE " in result.stdout
    # Find the written file.
    written = sorted(out_dir.glob("sweep_*.json"))
    assert len(written) == 1
    assert written[0].stat().st_size > 100
