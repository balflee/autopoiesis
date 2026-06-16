"""weight_updater + event_emitter slot lists MUST equal decision.py's SoT (guards a
future re-hardcode from drifting), AND the SoT tuples carry the EXPECTED slot
VALUES (pins the rename so it can actually fail if a key is wrong)."""
from __future__ import annotations

from agent.dashboard_bridge.event_emitter import SIGNAL_ENGINE_KEYS
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES
from agent.engines.weight_updater import _ALPHA_ENGINES, _BETA_ENGINES


def test_weight_updater_derives_from_decision_sot() -> None:
    assert _ALPHA_ENGINES == RATIONAL_ENGINES
    assert _BETA_ENGINES == SENTIENT_ENGINES


def test_event_emitter_signal_keys_derive_from_decision_sot() -> None:
    assert tuple(SIGNAL_ENGINE_KEYS) == (*RATIONAL_ENGINES, *SENTIENT_ENGINES)


def test_sot_carries_the_expected_slot_values() -> None:
    # Pins the post-2026-06-16-rename slot keys: the 3 formerly-misnamed slots
    # now carry the Sackmann payload they actually hold. This is the assertion
    # that fails loudly if a key ever drifts back to a dead-engine namesake.
    assert RATIONAL_ENGINES == ("tennis_technical", "market_momentum", "surface_advantage")
    assert SENTIENT_ENGINES == ("head_to_head", "rest_recency")
