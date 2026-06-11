"""Sprint_1 smoke tests for the sim/ package skeleton.

Acceptance criteria covered (per T-C-001 task brief):

* sim.params.ParamSpace round-trips to/from JSON across the five
  PRD §14.1 BREATH parameters shipped this sprint.
* sim.params.ParamSpace.from_json rejects unknown / missing keys
  (additive-schema-change guard).
* sim.economy.BreathEconomy accepts a ParamSpace and exposes the
  expected accessors; ``tick()`` raises NotImplementedError so accidental
  sprint_2 calls fail loud.
* sim.strategies exposes Pessimist / Optimist / Satisficer — Track C
  Hard Rule #2 (dropping any → calibration validator FAIL).
* sim.objectives.score_objectives returns the canonical 14 keys.
* python -m sim.cli --help exits 0.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sim.cli import main as cli_main
from sim.economy import BreathEconomy
from sim.objectives import GOOD_CALIBRATION_OBJECTIVES, score_objectives
from sim.params import ParamSpace
from sim.strategies import ARCHETYPES, Optimist, Pessimist, Satisficer

# ----------------------------------------------------------------------
# ParamSpace round-trip
# ----------------------------------------------------------------------


def test_paramspace_default_round_trips_through_json() -> None:
    """Defaults must survive ``to_json`` → ``from_json`` byte-equivalent."""
    p = ParamSpace()
    rebuilt = ParamSpace.from_json(p.to_json())
    assert rebuilt == p
    # Five PRD §14.1 BREATH parameters — well above the ≥3 acceptance bar.
    field_names = {f.name for f in dataclasses.fields(ParamSpace)}
    assert field_names >= {
        "initial_breath",
        "passive_burn_rate",
        "conversion_rate",
        "target_horizon",
        "min_bet_size",
    }


def test_paramspace_custom_values_round_trip() -> None:
    """Non-default values must survive ``to_json`` → ``from_json``."""
    p = ParamSpace(
        initial_breath=1234.5,
        passive_burn_rate=2.75,
        conversion_rate=0.8,
        target_horizon=4.0,
        min_bet_size=10.0,
    )
    rebuilt = ParamSpace.from_json(p.to_json())
    assert rebuilt == p


def test_paramspace_filesystem_round_trip(tmp_path: Path) -> None:
    """write_json/read_json mirror each other on disk."""
    p = ParamSpace(initial_breath=900.0, passive_burn_rate=1.5)
    out = p.write_json(tmp_path / "selected_params.json")
    assert out.exists()
    rebuilt = ParamSpace.read_json(out)
    assert rebuilt == p


def test_paramspace_rejects_unknown_key() -> None:
    p = ParamSpace()
    raw = json.loads(p.to_json())
    raw["future_field"] = 1.0
    with pytest.raises(ValueError, match="unknown keys"):
        ParamSpace.from_json(json.dumps(raw))


def test_paramspace_rejects_missing_key() -> None:
    p = ParamSpace()
    raw = json.loads(p.to_json())
    del raw["min_bet_size"]
    with pytest.raises(ValueError, match="missing required keys"):
        ParamSpace.from_json(json.dumps(raw))


def test_paramspace_rejects_non_number() -> None:
    p = ParamSpace()
    raw = json.loads(p.to_json())
    raw["initial_breath"] = "1000"
    with pytest.raises(ValueError, match="must be a number"):
        ParamSpace.from_json(json.dumps(raw))


# ----------------------------------------------------------------------
# BreathEconomy constructor
# ----------------------------------------------------------------------


def test_breath_economy_construction_freezes_params() -> None:
    p = ParamSpace(initial_breath=777.0)
    econ = BreathEconomy(p)
    assert econ.params is p
    assert econ.breath == pytest.approx(777.0)


def test_breath_economy_rejects_non_paramspace() -> None:
    with pytest.raises(TypeError, match="ParamSpace"):
        BreathEconomy(params={"initial_breath": 1000.0})  # type: ignore[arg-type]


def test_breath_economy_tick_is_sprint_2() -> None:
    econ = BreathEconomy(ParamSpace())
    with pytest.raises(NotImplementedError, match="sprint_2"):
        econ.tick()


# ----------------------------------------------------------------------
# Strategy archetypes
# ----------------------------------------------------------------------


def test_three_archetypes_are_registered() -> None:
    """Track C Hard Rule #2: dropping any → validator FAIL."""
    assert ARCHETYPES == (Pessimist, Optimist, Satisficer)
    assert {cls.archetype for cls in ARCHETYPES} == {
        "pessimist",
        "optimist",
        "satisficer",
    }


@pytest.mark.parametrize("cls", [Pessimist, Optimist, Satisficer])
def test_archetype_decide_is_sprint_2(cls: type) -> None:
    instance = cls()
    with pytest.raises(NotImplementedError, match="sprint_2"):
        instance.decide(context=None)


# ----------------------------------------------------------------------
# Objectives schema
# ----------------------------------------------------------------------


def test_objectives_have_canonical_14_keys() -> None:
    out = score_objectives()
    assert set(out.keys()) == set(GOOD_CALIBRATION_OBJECTIVES)
    assert len(GOOD_CALIBRATION_OBJECTIVES) == 14
    # Sprint_1 contract: every objective unevaluated → False.
    assert all(value is False for value in out.values())


# ----------------------------------------------------------------------
# CLI smoke
# ----------------------------------------------------------------------


def test_cli_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "sim" in captured.out.lower()


def test_cli_help_subprocess_exits_zero() -> None:
    """The orchestrator gate spawns ``python -m sim.cli --help`` directly;
    re-running it via subprocess catches any top-level import side-effect
    that would break the gate."""
    result = subprocess.run(
        [sys.executable, "-m", "sim.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "expected non-empty usage on --help"
