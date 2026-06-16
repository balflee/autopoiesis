"""F1 — dashboard_ws_message v0.4.0 contract-alignment tests.

The v0.4.0 BREAKING bump renames 3 of the 5 ``signals`` enum keys to the
Sackmann/CLOB payloads they actually carry: smart_money->surface_advantage,
sentiment_llm->head_to_head, crowd_volume->rest_recency. The three OPTIONAL
fields added in v0.3.0 — ``market_id``, ``bet_id`` and ``signals`` (a
name->score map keyed by the 5 lowercase persisted slot keys) — are
unchanged in shape; only the 3 slot-key names move. Backward-compat with
v0.2.0-shaped frames (no signals/market_id/bet_id) is preserved.

These tests are the LOCKSTEP guard: the JSON schema, the two TS mirrors
and the Pydantic producer all carry the same fields + slot keys. The Python
side is exercised here; the TS side has its own vitest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agent.dashboard_bridge.event_emitter import (
    WS_CONTRACT_VERSION,
    DecisionFeedEntry,
    DecisionPayload,
    WsEventEmitter,
)

# v0.4.0 schema — the canonical wire contract for this bump.
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / ".dev"
    / "contracts"
    / "dashboard_ws_message.v0.4.0.json"
)

# The 5 LOWERCASE persisted slot keys (NOT the uppercase display
# constants). These are the only legal keys in a ``signals`` map.
_ENGINE_KEYS = (
    "tennis_technical",
    "market_momentum",
    "surface_advantage",
    "head_to_head",
    "rest_recency",
)

# The 3 pre-2026-06-16 misnomers — must NOT appear ANYWHERE in the v0.4.0
# schema (enum keys OR description prose); this is the canonical-doc guard.
_LEGACY_SLOT_NAMES = ("smart_money", "sentiment_llm", "crowd_volume")


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    raw = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(raw)


def _signals_map() -> dict[str, float]:
    return {k: 0.5 for k in _ENGINE_KEYS}


# ---------------------------------------------------------------------------
# (0) version + module-constant alignment
# ---------------------------------------------------------------------------


def test_schema_file_pins_v0_4_0(validator: Draft202012Validator) -> None:
    raw = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert raw["version"] == "0.4.0"


def test_emitter_constant_is_v0_4_0() -> None:
    assert WS_CONTRACT_VERSION == "0.4.0"


def test_no_legacy_slot_names_anywhere_in_schema() -> None:
    """The whole rename exists to delete these misnomers — they must be gone
    from the canonical contract doc, including the description prose the
    propertyNames.enum gate cannot see."""
    raw = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    blob = json.dumps(raw)
    assert not any(name in blob for name in _LEGACY_SLOT_NAMES)


# ---------------------------------------------------------------------------
# (a) backward-compat — v0.2.0 frames still validate against v0.3.0
# ---------------------------------------------------------------------------


def test_legacy_decision_frame_still_validates(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "payload": {
            "action": "BET",
            "side": "YES",
            "size_usd": 40.0,
            "edge_pct": 0.12,
            "kelly_fraction": 0.2,
        },
    }
    validator.validate(frame)  # raises on failure


def test_legacy_decision_feed_frame_still_validates(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision_feed",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 1,
        "entries": [
            {
                "id": "row-1",
                "ts": "2026-05-23T12:00:00+00:00",
                "action": "NO_BET",
            }
        ],
    }
    validator.validate(frame)


# ---------------------------------------------------------------------------
# (b) new fields validate; bad signals key / unknown extra is rejected
# ---------------------------------------------------------------------------


def test_decision_frame_with_new_fields_validates(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "payload": {
            "action": "BET",
            "side": "YES",
            "size_usd": 40.0,
            "market_id": "0xmarket",
            "bet_id": "uuid-abc",
            "signals": _signals_map(),
        },
    }
    validator.validate(frame)


def test_decision_feed_entry_with_new_fields_validates(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision_feed",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 2,
        "entries": [
            {
                "id": "uuid-abc",
                "ts": "2026-05-23T12:00:00+00:00",
                "action": "BET",
                "market_id": "0xmarket",
                "bet_id": "uuid-abc",
                "signals": _signals_map(),
            }
        ],
    }
    validator.validate(frame)


def test_decision_signals_key_outside_enum_is_rejected(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "payload": {
            "action": "BET",
            "signals": {"nba_technical": 0.5},  # uppercase-era / wrong key
        },
    }
    assert not validator.is_valid(frame)


def test_decision_feed_signals_key_outside_enum_is_rejected(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision_feed",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "entries": [
            {
                "id": "x",
                "ts": "2026-05-23T12:00:00+00:00",
                "action": "BET",
                "signals": {"bogus_engine": 0.5},
            }
        ],
    }
    assert not validator.is_valid(frame)


def test_decision_signals_non_number_value_is_rejected(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "payload": {
            "action": "BET",
            "signals": {"tennis_technical": "high"},  # not a number
        },
    }
    assert not validator.is_valid(frame)


def test_decision_unknown_extra_field_still_rejected(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "payload": {"action": "BET", "totally_unknown": 1},
    }
    assert not validator.is_valid(frame)


def test_decision_feed_entry_unknown_extra_field_still_rejected(
    validator: Draft202012Validator,
) -> None:
    frame = {
        "kind": "decision_feed",
        "ts": "2026-05-23T12:00:00+00:00",
        "seq": 0,
        "entries": [
            {
                "id": "x",
                "ts": "2026-05-23T12:00:00+00:00",
                "action": "BET",
                "totally_unknown": 1,
            }
        ],
    }
    assert not validator.is_valid(frame)


# ---------------------------------------------------------------------------
# (c) emitters produce schema-valid frames carrying the new fields
# ---------------------------------------------------------------------------


def test_emit_decision_with_new_fields_is_schema_valid(
    validator: Draft202012Validator,
) -> None:
    em = WsEventEmitter()
    frame = em.emit_decision(
        action="BET",
        side="YES",
        size_usd=40.0,
        edge_pct=0.1,
        market_id="0xmarket",
        bet_id="uuid-abc",
        signals=_signals_map(),
    )
    validator.validate(frame)
    assert frame["payload"]["market_id"] == "0xmarket"
    assert frame["payload"]["bet_id"] == "uuid-abc"
    assert frame["payload"]["signals"] == _signals_map()


def test_emit_decision_without_new_fields_omits_them(
    validator: Draft202012Validator,
) -> None:
    em = WsEventEmitter()
    frame = em.emit_decision(action="NO_BET")
    validator.validate(frame)
    assert "market_id" not in frame["payload"]
    assert "bet_id" not in frame["payload"]
    assert "signals" not in frame["payload"]


def test_emit_decision_feed_produces_schema_valid_frame(
    validator: Draft202012Validator,
) -> None:
    em = WsEventEmitter()
    frame = em.emit_decision_feed(
        entries=[
            {
                "id": "uuid-abc",
                "ts": "2026-05-23T12:00:00+00:00",
                "action": "BET",
                "side": "YES",
                "size_usd": 40.0,
                "market_id": "0xmarket",
                "bet_id": "uuid-abc",
                "signals": _signals_map(),
            },
            {
                "id": "row-2",
                "ts": "2026-05-23T12:01:00+00:00",
                "action": "NO_BET",
            },
        ]
    )
    validator.validate(frame)
    assert frame["kind"] == "decision_feed"
    assert len(frame["entries"]) == 2
    assert frame["entries"][0]["bet_id"] == "uuid-abc"


def test_emit_decision_feed_rejects_bad_signals_key(
    validator: Draft202012Validator,
) -> None:
    """The Pydantic model is extra='forbid' on the entry signals map —
    a non-enum engine key must not produce a wire-valid frame."""
    em = WsEventEmitter()
    frame = em.emit_decision_feed(
        entries=[
            {
                "id": "x",
                "ts": "2026-05-23T12:00:00+00:00",
                "action": "BET",
                "signals": {"nba_technical": 0.5},
            }
        ]
    )
    # Producer happily serialises an arbitrary str->float map, but the
    # WIRE schema rejects the non-enum key — that's the guard.
    assert not validator.is_valid(frame)


# ---------------------------------------------------------------------------
# (d) F0-polish — producer minLength:1 parity for market_id / bet_id
#
# The wire schema declares minLength:1 on market_id + bet_id of BOTH
# decision_payload and decision_feed_entry. The producer Pydantic models
# must reject an EMPTY string at construction time with a pydantic
# ValidationError — NOT silently emit a schema-invalid frame downstream.
# ---------------------------------------------------------------------------


def test_decision_payload_rejects_empty_market_id() -> None:
    with pytest.raises(ValidationError):
        DecisionPayload(action="BET", market_id="")


def test_decision_payload_rejects_empty_bet_id() -> None:
    with pytest.raises(ValidationError):
        DecisionPayload(action="BET", bet_id="")


def test_decision_feed_entry_rejects_empty_market_id() -> None:
    with pytest.raises(ValidationError):
        DecisionFeedEntry(
            id="row-1",
            ts="2026-05-23T12:00:00+00:00",
            action="BET",
            market_id="",
        )


def test_decision_feed_entry_rejects_empty_bet_id() -> None:
    with pytest.raises(ValidationError):
        DecisionFeedEntry(
            id="row-1",
            ts="2026-05-23T12:00:00+00:00",
            action="BET",
            bet_id="",
        )


def test_emit_decision_with_empty_market_id_raises() -> None:
    """The emit path surfaces the pydantic ValidationError (the empty
    string is rejected at the producer, not via a schema-invalid frame)."""
    em = WsEventEmitter()
    with pytest.raises(ValidationError):
        em.emit_decision(action="BET", market_id="")


def test_emit_decision_with_empty_bet_id_raises() -> None:
    em = WsEventEmitter()
    with pytest.raises(ValidationError):
        em.emit_decision(action="BET", bet_id="")


def test_emit_decision_feed_with_empty_market_id_raises() -> None:
    """The emit_decision_feed validate step raises a pydantic
    ValidationError on an empty market_id (not a schema-invalid frame)."""
    em = WsEventEmitter()
    with pytest.raises(ValidationError):
        em.emit_decision_feed(
            entries=[
                {
                    "id": "row-1",
                    "ts": "2026-05-23T12:00:00+00:00",
                    "action": "BET",
                    "market_id": "",
                }
            ]
        )


def test_emit_decision_feed_with_empty_bet_id_raises() -> None:
    em = WsEventEmitter()
    with pytest.raises(ValidationError):
        em.emit_decision_feed(
            entries=[
                {
                    "id": "row-1",
                    "ts": "2026-05-23T12:00:00+00:00",
                    "action": "BET",
                    "bet_id": "",
                }
            ]
        )
