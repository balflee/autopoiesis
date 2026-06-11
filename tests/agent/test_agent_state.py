"""AgentState round-trip + persistence stub tests.

Brief deliverable: 'agent/core/state.py — AgentState dataclass;
persistence stub (full memory_bank lands in sprint_4)'. This module
covers the JSON save/load round-trip + Pydantic validation of the
Phase / Vitals / Weights composition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.state import AgentState, Phase, Vitals, Weights


def _sample_state() -> AgentState:
    return AgentState(
        agent_id="genesis_v1",
        tick=42,
        phase=Phase.PHASE_1_INFANCY,
        vitals=Vitals(breath=950.0, bankroll_usd=1200.0, phase_age_days=2.5),
        weights=Weights(
            w_r=0.6,
            w_s=0.4,
            alpha=[1 / 3, 1 / 3, 1 / 3],
            beta=[0.0, 1.0],
            rho=0.5,
        ),
        desperate=False,
    )


def test_agent_state_json_round_trip(tmp_path: Path) -> None:
    state = _sample_state()
    path = state.save_json(tmp_path / "agent_state.json")
    assert path.exists()
    loaded = AgentState.load_json(path)
    assert loaded.tick == 42
    assert loaded.phase == Phase.PHASE_1_INFANCY
    assert loaded.weights.alpha == [1 / 3, 1 / 3, 1 / 3]
    assert loaded.desperate is False
    # Bit-exact: persistence stub MUST round-trip without drift.
    assert loaded == state


def test_agent_state_atomic_temp_rename(tmp_path: Path) -> None:
    """Writer sweeps orphan tmp file from a prior crash."""
    state = _sample_state()
    target = tmp_path / "agent_state.json"
    orphan = tmp_path / ".agent_state.json.tmp"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("stale", encoding="utf-8")
    state.save_json(target)
    assert not orphan.exists()
    assert target.exists()


def test_agent_state_load_missing_raises(tmp_path: Path) -> None:
    """load_json on a missing file ⇒ FileNotFoundError so the lifecycle
    scheduler can interpret it as a cold-start."""
    with pytest.raises(FileNotFoundError):
        AgentState.load_json(tmp_path / "does_not_exist.json")


def test_agent_state_rejects_extra_fields() -> None:
    """extra='forbid' so a forward-incompatible JSON fails loudly."""
    with pytest.raises(ValueError):
        AgentState.model_validate(
            {
                "agent_id": "genesis_v1",
                "tick": 0,
                "phase": "PHASE_1_INFANCY",
                "vitals": {"breath": 100.0, "bankroll_usd": 100.0, "phase_age_days": 0.0},
                "weights": {
                    "w_r": 0.6,
                    "w_s": 0.4,
                    "alpha": [1 / 3, 1 / 3, 1 / 3],
                    "beta": [0.0, 1.0],
                    "rho": 0.5,
                },
                "desperate": False,
                "unknown_field": "should_fail",
            }
        )
