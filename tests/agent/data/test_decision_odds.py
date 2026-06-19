import json

from agent.data.sandbox_state import (
    DECISIONS_FILENAME,
    DecisionRecord,
    SandboxStateWriter,
    iter_jsonl,
)


def _dec(**kw):
    d = dict(
        tick=1,
        ts="t",
        market_id="m1",
        kind="BET",
        size_usd=50.0,
        side="YES",
        edge_pct=0.04,
        no_bet_reason=None,
        breath_after=72.0,
        bankroll_usd_after=1240.0,
    )
    d.update(kw)
    return DecisionRecord(**d)


def test_living_fields_present_serialized(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_decision(
        _dec(
            odds_yes=0.58,
            odds_no=0.42,
            fee_floor_pct=0.018,
            signal_scores={"tennis_technical": 0.12},
        )
    )
    row = iter_jsonl(tmp_path / DECISIONS_FILENAME)[0]
    assert row["odds_yes"] == 0.58 and row["odds_no"] == 0.42
    assert row["fee_floor_pct"] == 0.018
    assert row["signal_scores"] == {"tennis_technical": 0.12}


def test_living_fields_absent_omitted_AND_byte_identical(tmp_path):
    # HIGH-1: with no living fields, the on-disk line must be the COMPACT
    # Pydantic JSON with the 4 living keys excluded — byte-identical to pre-P1.
    w = SandboxStateWriter(root=tmp_path)
    dec = _dec()  # no odds, empty signal_scores
    w.append_decision(dec)
    raw = (tmp_path / DECISIONS_FILENAME).read_text(encoding="utf-8").strip()
    assert raw == dec.model_dump_json(
        exclude={"odds_yes", "odds_no", "fee_floor_pct", "signal_scores"}
    )
    assert "odds_yes" not in raw and "signal_scores" not in raw and "fee_floor_pct" not in raw
    assert ", " not in raw and '": ' not in raw  # compact: no separator spaces
    parsed = json.loads(raw)
    assert parsed["no_bet_reason"] is None  # existing nullable still serialized as null
    assert "odds_no" not in parsed


def test_no_bet_idle_tick_byte_identical(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_decision(
        _dec(
            kind="NO_BET",
            side=None,
            edge_pct=None,
            no_bet_reason="no_eligible_market",
            market_id=None,
            size_usd=0.0,
        )
    )
    row = iter_jsonl(tmp_path / DECISIONS_FILENAME)[0]
    assert "odds_yes" not in row and "signal_scores" not in row
    assert row["no_bet_reason"] == "no_eligible_market"
