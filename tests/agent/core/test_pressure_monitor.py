"""PressureMonitor tests — pressure formula + counter + phase resets.

Brief acceptance criteria covered here:

* pressure formula matches PRD §6.8 → golden vector in
  ``__fixtures__/pressure_golden.json``.
* Threshold-and-hold trigger fires after 2 consecutive cycles in Phase 3.
* Sub-threshold tick resets the counter (consecutive only).
* Phase 2 → Phase 3 transition resets the counter; Phase-2-only
  ApprenticeshipFailure never accrued so the explicit reset is
  unnecessary (and we pin the boundary).
* Latched behaviour: a second trigger never fires.
* Non-Phase-3 ticks never advance the counter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.pressure_monitor import (
    MIN_BURN_RATE_PER_HOUR,
    MIN_PRESSURE,
    MIN_PRESSURE_CYCLES,
    TARGET_HORIZON_HOURS,
    EnterDesperateModeIntent,
    PressureMonitor,
    PressureSample,
    compute_pressure,
)
from agent.core.state import Phase

_GOLDEN_PATH = Path(__file__).parent / "__fixtures__" / "pressure_golden.json"


# ── Golden vector ────────────────────────────────────────────────────


def test_pressure_golden_vector_matches_prd_formula() -> None:
    """Every row in pressure_golden.json must round-trip through
    :func:`compute_pressure` within 1e-9.

    This pins the PRD §6.8 formula against the placeholder
    TARGET_HORIZON=36h so a calibration sweep that tunes the horizon
    later updates the golden vector deliberately, not by accident.
    """
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["target_horizon_hours"] == TARGET_HORIZON_HOURS
    assert golden["min_burn_rate_per_hour"] == MIN_BURN_RATE_PER_HOUR
    for row in golden["samples"]:
        projected, pressure = compute_pressure(
            breath=row["breath"],
            effective_burn_rate_per_hour=row["effective_burn_rate_per_hour"],
        )
        assert projected == pytest.approx(row["projected_hours"], abs=1e-9), row["label"]
        assert pressure == pytest.approx(row["pressure"], abs=1e-9), row["label"]


# ── Trigger threshold-and-hold ───────────────────────────────────────


def test_intent_fires_after_two_consecutive_phase_3_cycles() -> None:
    """Brief acceptance: pressure >= 0.5 + 2 cycles + Phase 3."""
    monitor = PressureMonitor()
    # Cycle 1: pressure ≈ 0.5833 (above threshold) — counter advances.
    sample1, intent1 = monitor.observe(
        breath=1500.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert intent1 is None
    assert monitor.cycles_held == 1
    assert sample1.pressure >= MIN_PRESSURE

    # Cycle 2: again above threshold — counter hits 2, intent fires.
    sample2, intent2 = monitor.observe(
        breath=1400.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert isinstance(intent2, EnterDesperateModeIntent)
    assert intent2.phase == Phase.PHASE_3_MASTER
    assert intent2.cycles_held == MIN_PRESSURE_CYCLES
    assert intent2.pressure_at_entry == pytest.approx(sample2.pressure)
    assert monitor.latched is True


def test_sub_threshold_tick_resets_counter() -> None:
    """Hold must be CONSECUTIVE — a sub-threshold tick drops to zero."""
    monitor = PressureMonitor()
    monitor.observe(
        breath=1500.0,  # pressure ≈ 0.5833
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert monitor.cycles_held == 1
    # Now pressure < 0.5 — counter resets.
    monitor.observe(
        breath=2500.0,  # projected=25h → pressure≈0.305
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert monitor.cycles_held == 0


def test_non_phase_3_never_advances_counter() -> None:
    """Phase 1/2/4 ticks must reset the counter even if pressure ≥ 0.5."""
    monitor = PressureMonitor()
    for phase in (
        Phase.PHASE_1_INFANCY,
        Phase.PHASE_2_APPRENTICE,
        Phase.PHASE_4_TERMINAL,
    ):
        sample, intent = monitor.observe(
            breath=500.0,  # well above threshold
            effective_burn_rate_per_hour=100.0,
            phase=phase,
        )
        assert intent is None
        assert monitor.cycles_held == 0
        assert sample.pressure >= MIN_PRESSURE  # pressure still computed


def test_intent_latches_so_second_fire_never_happens() -> None:
    """Desperate Mode is irreversible (PRD §6.9)."""
    monitor = PressureMonitor()
    monitor.observe(
        breath=1500.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    _, first_intent = monitor.observe(
        breath=1400.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert first_intent is not None  # baseline
    # Another Phase-3 tick — intent must NOT re-fire.
    _, second_intent = monitor.observe(
        breath=1300.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert second_intent is None
    assert monitor.latched is True


# ── Phase transitions ────────────────────────────────────────────────


def test_phase_2_to_phase_3_transition_resets_counter() -> None:
    """Brief acceptance: Apprenticeship → Adulthood resets the counter.

    A counter that was non-zero before the transition (e.g. from a
    test mocking Phase 3 then rolling back to Phase 2) must drop to
    zero on 2→3.
    """
    monitor = PressureMonitor()
    # Force the counter to 1 via a synthetic Phase-3 tick.
    monitor.observe(
        breath=1500.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert monitor.cycles_held == 1
    monitor.handle_phase_transition(
        from_phase=Phase.PHASE_2_APPRENTICE,
        to_phase=Phase.PHASE_3_MASTER,
    )
    assert monitor.cycles_held == 0


def test_other_transitions_do_not_reset_counter() -> None:
    """Only Phase 2 → Phase 3 resets — every other transition is a no-op.

    Pins the boundary: ApprenticeshipFailure (a Phase-2-only terminal)
    must not reset because it never reached Phase 3 in the first place.
    """
    monitor = PressureMonitor()
    monitor.observe(
        breath=1500.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    assert monitor.cycles_held == 1

    # Phase 1 → Phase 2 (puberty): no reset.
    monitor.handle_phase_transition(
        from_phase=Phase.PHASE_1_INFANCY,
        to_phase=Phase.PHASE_2_APPRENTICE,
    )
    assert monitor.cycles_held == 1

    # Phase 3 → Phase 4 (terminal): no reset.
    monitor.handle_phase_transition(
        from_phase=Phase.PHASE_3_MASTER,
        to_phase=Phase.PHASE_4_TERMINAL,
    )
    assert monitor.cycles_held == 1


# ── Sample shape + dict round-trip ───────────────────────────────────


def test_sample_to_dict_contains_canonical_fields() -> None:
    monitor = PressureMonitor()
    sample, _ = monitor.observe(
        breath=2000.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )
    payload = sample.to_dict()
    assert set(payload) == {
        "breath",
        "effective_burn_rate_per_hour",
        "projected_hours",
        "pressure",
        "phase",
    }
    assert payload["phase"] == Phase.PHASE_3_MASTER.value
    assert isinstance(sample, PressureSample)


def test_constructor_input_validation() -> None:
    with pytest.raises(ValueError, match="min_pressure"):
        PressureMonitor(min_pressure=0.0)
    with pytest.raises(ValueError, match="min_pressure"):
        PressureMonitor(min_pressure=1.5)
    with pytest.raises(ValueError, match="min_cycles"):
        PressureMonitor(min_cycles=0)


# ── No-lookahead invariant ───────────────────────────────────────────


def test_observe_uses_only_tick_window_inputs() -> None:
    """The pressure formula consumes only `breath` and `burn_rate`.

    Both inputs come from the current tick's vitals — no
    post-settlement data flows in. This test is the structural pin
    the look-ahead auditor verifies; the function's signature
    forbids any other inputs.
    """
    monitor = PressureMonitor()
    # The accepted kwargs are pinned by signature — observe() refuses
    # anything else, so a leaky caller cannot smuggle settled_at /
    # resolved_at / outcome / payout fields through.
    with pytest.raises(TypeError):
        # mypy will also flag this; the runtime TypeError is the
        # defence in depth.
        monitor.observe(  # type: ignore[call-arg]
            breath=100.0,
            effective_burn_rate_per_hour=10.0,
            phase=Phase.PHASE_3_MASTER,
            settled_at_outcome=1.0,  # forbidden
        )
