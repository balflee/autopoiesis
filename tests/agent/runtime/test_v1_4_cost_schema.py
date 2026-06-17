"""V1.4 — execution-cost schema + cost-NET settlement PnL.

Legacy / replay rows (no cost stamps) stay byte-identical to the pre-V1.4 formula;
only LIVE/probe rows (which set the stamps) take a fee+spread haircut, priced off the
ACTUAL fill_price. The fail-closed guard raises on a cost-blind row."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    BetRecord,
    SettledBetRecord,
    assert_cost_fields_present,
    bet_record_jsonl_dict,
    execution_cost_usd,
)
from agent.runtime.sandbox_settlement_poller import _compute_pnl

_TS = "2026-01-15T12:00:00+00:00"


def _bet(**over: object) -> BetRecord:
    base = dict(
        bet_id="b1", ts=_TS, market_id="m1", side="YES",
        price=0.5, size_usd=5.0, expected_settle_ts=_TS,
    )
    base.update(over)
    return BetRecord(**base)  # type: ignore[arg-type]


def _settle(outcome: str, winning_price: float = 1.0) -> SettlementResult:
    return SettlementResult(
        market_id="m1", resolved=True, outcome=outcome,  # type: ignore[arg-type]
        winning_price=winning_price,
        resolution_ts=datetime(2026, 1, 15, 12, tzinfo=UTC),
        end_date=datetime(2026, 1, 15, 11, tzinfo=UTC),
    )


# --- schema: legacy rows load + stay byte-identical -------------------------- #

def test_legacy_row_loads_without_cost_fields() -> None:
    b = _bet()
    assert b.fill_price is None and b.fee_bps is None
    # None cost stamps are OMITTED from the JSONL row (byte-identical to pre-V1.4).
    row = bet_record_jsonl_dict(b)
    for k in ("fill_price", "fee_bps", "spread_paid_usd", "liquidity_cap_usd"):
        assert k not in row


def test_cost_stamped_row_roundtrips() -> None:
    b = _bet(fill_price=0.52, fee_bps=200.0, spread_paid_usd=0.1, liquidity_cap_usd=20.0)
    row = bet_record_jsonl_dict(b)
    assert row["fill_price"] == 0.52 and row["fee_bps"] == 200.0
    assert BetRecord(**row).fill_price == 0.52  # type: ignore[arg-type]


def test_execution_cost_usd() -> None:
    assert execution_cost_usd(_bet()) == 0.0  # legacy: no haircut
    # 200 bps of $5 = $0.10, + $0.15 spread = $0.25
    b = _bet(fee_bps=200.0, spread_paid_usd=0.15)
    assert execution_cost_usd(b) == pytest.approx(0.25)


def test_assert_cost_fields_present_fail_closed() -> None:
    with pytest.raises(ValueError, match="missing execution-cost stamps"):
        assert_cost_fields_present(_bet())
    assert_cost_fields_present(
        _bet(fill_price=0.5, fee_bps=0.0, spread_paid_usd=0.0, liquidity_cap_usd=5.0)
    )  # all present → no raise


# --- cost-NET _compute_pnl --------------------------------------------------- #

def test_legacy_pnl_byte_identical_when_no_cost_stamps() -> None:
    # YES @ 0.5, wins @ 1.0 → 5*(1/0.5 - 1) = 5.0 ; loser → -5 ; void → 0
    assert _compute_pnl(bet=_bet(), outcome=_settle("yes")) == pytest.approx(5.0)
    assert _compute_pnl(bet=_bet(), outcome=_settle("no")) == pytest.approx(-5.0)
    assert _compute_pnl(bet=_bet(), outcome=_settle("void")) == pytest.approx(0.0)


def test_cost_net_pnl_subtracts_on_every_outcome() -> None:
    b = _bet(fill_price=0.5, fee_bps=200.0, spread_paid_usd=0.15)  # cost = 0.25
    assert _compute_pnl(bet=b, outcome=_settle("yes")) == pytest.approx(5.0 - 0.25)
    assert _compute_pnl(bet=b, outcome=_settle("no")) == pytest.approx(-5.0 - 0.25)
    assert _compute_pnl(bet=b, outcome=_settle("void")) == pytest.approx(-0.25)


def test_winner_uses_actual_fill_price_when_stamped() -> None:
    # decision price 0.5 but actual fill 0.4 → 5*(1/0.4 - 1) = 7.5, minus 0 cost
    b = _bet(fill_price=0.4, fee_bps=0.0, spread_paid_usd=0.0)
    assert _compute_pnl(bet=b, outcome=_settle("yes")) == pytest.approx(7.5)


# --- V1.4b SettledBetRecord.reason (omit-when-None → byte-identical) ---------- #

def _settled(**over: object) -> SettledBetRecord:
    base = dict(
        bet_id="b1", market_id="m1", settled_ts=_TS, outcome="yes",
        winning_price=1.0, pnl_usd=5.0,
    )
    base.update(over)
    return SettledBetRecord(**base)  # type: ignore[arg-type]


def test_settled_record_omits_reason_when_none() -> None:
    assert "reason" not in _settled().model_dump_json(exclude_none=True)


def test_settled_record_includes_reason_when_set() -> None:
    js = _settled(outcome="void", pnl_usd=0.0, reason="terminal_query_failed").model_dump_json(
        exclude_none=True
    )
    assert "terminal_query_failed" in js
