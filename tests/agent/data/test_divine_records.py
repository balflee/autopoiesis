import json

import pytest
from pydantic import ValidationError

from agent.data.sandbox_state import (
    DEATHS_FILENAME,
    GODS_TREASURY_FILENAME,
    DeathRecord,
    TitheRecord,
    TributeRecord,
)


def test_filenames():
    assert GODS_TREASURY_FILENAME == "gods_treasury.jsonl"
    assert DEATHS_FILENAME == "deaths.jsonl"


def test_tribute_record_roundtrip_with_dice():
    rec = TributeRecord(
        tribute_id="t1",
        ts="2026-06-18T00:00:00+00:00",
        tick=812,
        amount_usd=2000.0,
        success=True,
        breath_after=35.0,
        bankroll_after=1240.0,
        dice_roll=0.42,
    )
    row = json.loads(rec.model_dump_json())
    assert row["type"] == "tribute"
    assert row["dice_roll"] == 0.42
    assert TributeRecord.model_validate(row) == rec


def test_tithe_record_breath_paid():
    rec = TitheRecord(
        tithe_id="h1",
        ts="2026-06-18T00:00:00+00:00",
        tick=20,
        paid_usd=0.0,
        breath_cost=5.0,
        breath_after=70.0,
        bankroll_after=0.0,
    )
    assert rec.type == "tithe"
    assert TitheRecord.model_validate(json.loads(rec.model_dump_json())) == rec


def test_death_record_defaults_incarnation_zero():
    rec = DeathRecord(
        death_id="d1",
        ts="2026-06-18T00:00:00+00:00",
        agent_id="agent-x",
        last_tick=999,
        final_bankroll_usd=12.5,
    )
    assert rec.incarnation_number == 0
    assert rec.cause == "breath_zero"


def test_extra_forbid_rejects_unknown_field():
    with pytest.raises(ValidationError):
        TributeRecord(
            tribute_id="t1",
            ts="x",
            tick=1,
            amount_usd=1.0,
            success=False,
            breath_after=0.0,
            bankroll_after=0.0,
            bogus=1,
        )
