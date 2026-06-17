"""V1.4b — place_order threads the execution-cost stamps onto the persisted BetRecord.

Legacy calls (no cost kwargs) omit the stamps on disk (byte-identical to pre-V1.4)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent.data.polymarket_sandbox_executor import MarketInfo, SandboxExecutor
from agent.data.sandbox_state import SandboxStateWriter, iter_jsonl

_TABLE = {"m1": MarketInfo(end_date_iso="2026-05-31T09:00:00Z")}


def _executor(tmp_path: Path) -> tuple[SandboxExecutor, SandboxStateWriter]:
    writer = SandboxStateWriter(root=tmp_path / "sandbox")
    ex = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: _TABLE.get(mid),
    )
    return ex, writer


def test_place_order_persists_cost_stamps(tmp_path: Path) -> None:
    ex, writer = _executor(tmp_path)
    asyncio.run(
        ex.place_order(
            market_id="m1", side="YES", price=0.55, size_usd=10.0,
            fill_price=0.56, fee_bps=200.0, spread_paid_usd=0.1, liquidity_cap_usd=20.0,
        )
    )
    row = iter_jsonl(writer.open_bets_path)[0]
    assert row["fill_price"] == 0.56
    assert row["fee_bps"] == 200.0
    assert row["spread_paid_usd"] == 0.1
    assert row["liquidity_cap_usd"] == 20.0


def test_place_order_without_cost_stamps_omits_them(tmp_path: Path) -> None:
    ex, writer = _executor(tmp_path)
    asyncio.run(ex.place_order(market_id="m1", side="YES", price=0.55, size_usd=10.0))
    row = iter_jsonl(writer.open_bets_path)[0]
    for k in ("fill_price", "fee_bps", "spread_paid_usd", "liquidity_cap_usd"):
        assert k not in row
