import inspect

from agent.runtime import sandbox_phase2_loop as L


def test_tribute_emit_carries_dice_roll():
    src = inspect.getsource(L.SandboxPhase2Loop._attempt_tribute)
    # the inline rng draw is hoisted to a named `roll` and forwarded to the emit
    assert "roll = self._tribute_rng.random()" in src
    assert "success = roll < p" in src
    assert "dice_roll=roll" in src


def test_constructor_accepts_record_living_stage_fields():
    sig = inspect.signature(L.SandboxPhase2Loop.__init__)
    assert "record_living_stage_fields" in sig.parameters
    assert sig.parameters["record_living_stage_fields"].default is False  # default OFF


def test_decision_record_living_fields_gated():
    src = inspect.getsource(L.SandboxPhase2Loop._tick)
    # odds + signal_scores are stamped ONLY behind the flag (byte-identical OFF)
    assert "self._record_living_stage_fields" in src
    assert "odds_yes=" in src and "odds_no=" in src and "fee_floor_pct=" in src
    assert "signal_scores=" in src
