"""Pure unit tests for the backward-compat slot-key alias map.

These assert the OLD->NEW upgrade + identity passthrough directly, so they pass
independent of whether decision.py's constants have been renamed yet (Task 2
Step 1 lands them before the Step 3 rename)."""

from __future__ import annotations

from agent.engines.slot_aliases import SLOT_KEY_ALIASES, alias_slot


def test_alias_map_covers_exactly_the_three_renamed_slots() -> None:
    assert SLOT_KEY_ALIASES == {
        "smart_money": "surface_advantage",
        "sentiment_llm": "head_to_head",
        "crowd_volume": "rest_recency",
    }


def test_alias_slot_upgrades_old_keys() -> None:
    assert alias_slot("smart_money") == "surface_advantage"
    assert alias_slot("sentiment_llm") == "head_to_head"
    assert alias_slot("crowd_volume") == "rest_recency"


def test_alias_slot_is_identity_for_new_and_unrelated_keys() -> None:
    for k in (
        "surface_advantage",
        "head_to_head",
        "rest_recency",
        "tennis_technical",
        "market_momentum",
        "anything_else",
    ):
        assert alias_slot(k) == k
