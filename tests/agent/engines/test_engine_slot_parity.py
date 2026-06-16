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
    # NOTE: these are the OLD values in Task 1 (pre-rename); Task 2 Step 5 updates
    # them to the new names — this is the assertion that PINS the rename.
    assert RATIONAL_ENGINES == ("tennis_technical", "market_momentum", "smart_money")
    assert SENTIENT_ENGINES == ("sentiment_llm", "crowd_volume")
