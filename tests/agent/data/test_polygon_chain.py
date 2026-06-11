"""Tests for :mod:`agent.data.polygon_chain` — PolygonChainLive feed.

Coverage:

1. subscribe filter by NBA market topic — provider called with the
   configured contract + fill-topic filter.
2. smart-money whitelist intersect — non-whitelisted wallets dropped;
   whitelisted ones surface as SmartMoneyPosition.
3. gap recovery on missed block — non-contiguous block jump emits a
   DegradedFeedWarning that the consumer can route to backfill.
4. asof_ts / wire-arrival guard — ``available_at`` is the injected
   clock's reading at recv-time, NOT a block-time field.

Plus: reconnect on transient ConnectionError, default whitelist load.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.data import (
    DegradedFeedWarning,
    PolygonChainLive,
    SmartMoneyPosition,
)
from tests.agent.data.conftest import (
    FakeChainSubscription,
    FakeSubscriptionProvider,
    InstantSleep,
    SteppingClock,
)

WALLET_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WALLET_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
WALLET_C_NOT_WHITELISTED = "0xcccccccccccccccccccccccccccccccccccccccc"


def _make_whitelist_file(tmp_path: Path, wallets: list[str]) -> Path:
    f = tmp_path / "wallets.json"
    f.write_text(
        json.dumps({"wallets": [{"address": w} for w in wallets]}),
        encoding="utf-8",
    )
    return f


def _fill_log(
    *,
    block_number: int,
    wallet: str,
    side: str = "YES",
    size_usd: float = 1000.0,
    market_id: str = "0xnba_market",
    tx_hash: str = "0xdeadbeef",
    log_index: int = 0,
) -> dict[str, Any]:
    return {
        "block_number": block_number,
        "tx_hash": tx_hash,
        "log_index": log_index,
        "topic0": "0xtopic_fill",
        "decoded": {
            "market_id": market_id,
            "trader": wallet,
            "side": side,
            "size_usd": size_usd,
        },
    }


# --------------------------------------------------------------------------- #


def test_polygon_chain_provider_called_with_contract_and_topic(
    tmp_path: Path, instant_sleep: InstantSleep
) -> None:
    """Provider receives the configured contract address + fill topic filter."""
    sub = FakeChainSubscription(logs=[])
    provider = FakeSubscriptionProvider(queue=[sub])
    whitelist = _make_whitelist_file(tmp_path, [WALLET_A])

    async def run() -> None:
        async with PolygonChainLive(
            subscription_provider=provider,
            wallets_path=whitelist,
            contract_address="0xCONTRACT",
            fill_topic="0xTOPIC",
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            async for _evt in feed.smart_money_positions("0xnba_market"):
                break

    asyncio.run(run())
    assert len(provider.calls) == 1
    contract, topics = provider.calls[0]
    assert contract == "0xCONTRACT"
    assert topics == ["0xTOPIC"]


def test_polygon_chain_whitelist_intersection(
    tmp_path: Path, fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Non-whitelisted wallets dropped; whitelisted ones surface."""
    sub = FakeChainSubscription(
        logs=[
            _fill_log(block_number=100, wallet=WALLET_A, side="YES", size_usd=1500.0),
            _fill_log(block_number=101, wallet=WALLET_C_NOT_WHITELISTED, size_usd=999.0),
            _fill_log(block_number=102, wallet=WALLET_B, side="NO", size_usd=800.0),
        ]
    )
    provider = FakeSubscriptionProvider(queue=[sub])
    whitelist = _make_whitelist_file(tmp_path, [WALLET_A, WALLET_B])

    async def run() -> list[object]:
        async with PolygonChainLive(
            subscription_provider=provider,
            wallets_path=whitelist,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            out: list[object] = []
            async for evt in feed.smart_money_positions("0xnba_market"):
                out.append(evt)
                if len(out) >= 5:
                    break
            return out

    events = asyncio.run(run())
    positions = [e for e in events if isinstance(e, SmartMoneyPosition)]
    # Exactly 2 positions (A's YES + B's NO); C dropped.
    assert len(positions) == 2
    wallets_seen = {p.wallet for p in positions}
    assert wallets_seen == {WALLET_A.lower(), WALLET_B.lower()}
    # C never makes it through.
    assert WALLET_C_NOT_WHITELISTED.lower() not in wallets_seen
    # Sides preserved.
    sides_by_wallet = {p.wallet: p.side for p in positions}
    assert sides_by_wallet[WALLET_A.lower()] == "YES"
    assert sides_by_wallet[WALLET_B.lower()] == "NO"


def test_polygon_chain_gap_emits_warning(
    tmp_path: Path, fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """Non-contiguous block jump → DegradedFeedWarning before the position."""
    sub = FakeChainSubscription(
        logs=[
            _fill_log(block_number=100, wallet=WALLET_A),
            # Skip 101-104 — 5-block gap.
            _fill_log(block_number=105, wallet=WALLET_A),
        ]
    )
    provider = FakeSubscriptionProvider(queue=[sub])
    whitelist = _make_whitelist_file(tmp_path, [WALLET_A])

    async def run() -> list[object]:
        async with PolygonChainLive(
            subscription_provider=provider,
            wallets_path=whitelist,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            out: list[object] = []
            async for evt in feed.smart_money_positions("0xnba_market"):
                out.append(evt)
                if len(out) >= 5:
                    break
            return out

    events = asyncio.run(run())
    # We expect: pos1 (block 100), then a gap-warn followed by pos2 (block 105).
    warns = [e for e in events if isinstance(e, DegradedFeedWarning)]
    gap_warns = [w for w in warns if w.reason.startswith("block_gap")]
    assert gap_warns, "expected a block_gap warning"
    # Gap delta is 5 (105 - 100).
    assert gap_warns[0].reason == "block_gap:5"


def test_polygon_chain_available_at_is_wire_arrival(
    tmp_path: Path, instant_sleep: InstantSleep
) -> None:
    """``available_at`` on every position equals the injected clock reading.

    Decoded payload may carry block_time / created_at fields; the
    adapter must IGNORE them and use the clock-stamped wire-arrival
    instead. The look-ahead auditor reads this convention.
    """
    fixed_time = datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)

    class _FixedClock:
        def now(self) -> datetime:
            return fixed_time

    log = _fill_log(block_number=100, wallet=WALLET_A)
    # Pollute the log with a far-future block_time that the adapter MUST ignore.
    log["block_time"] = "2099-01-01T00:00:00Z"
    log["decoded"]["created_at"] = "2099-01-01T00:00:00Z"

    sub = FakeChainSubscription(logs=[log])
    provider = FakeSubscriptionProvider(queue=[sub])
    whitelist = _make_whitelist_file(tmp_path, [WALLET_A])

    async def run() -> SmartMoneyPosition:
        async with PolygonChainLive(
            subscription_provider=provider,
            wallets_path=whitelist,
            clock=_FixedClock(),
            sleep=instant_sleep,
            max_reconnect_attempts=1,
        ) as feed:
            async for evt in feed.smart_money_positions("0xnba_market"):
                if isinstance(evt, SmartMoneyPosition):
                    return evt
        raise AssertionError("no position emitted")

    pos = asyncio.run(run())
    parsed = datetime.fromisoformat(pos.available_at)
    assert parsed == fixed_time
    assert "2099" not in pos.available_at


def test_polygon_chain_reconnect_on_connection_error(
    tmp_path: Path, fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """ConnectionError on the subscription iterator → warning + reconnect."""
    sub1 = FakeChainSubscription(
        logs=[
            _fill_log(block_number=100, wallet=WALLET_A),
            ConnectionError("mid-stream drop"),
        ]
    )
    sub2 = FakeChainSubscription(
        logs=[_fill_log(block_number=101, wallet=WALLET_A)]
    )
    provider = FakeSubscriptionProvider(queue=[sub1, sub2])
    whitelist = _make_whitelist_file(tmp_path, [WALLET_A])

    async def run() -> list[object]:
        async with PolygonChainLive(
            subscription_provider=provider,
            wallets_path=whitelist,
            clock=fake_clock,
            sleep=instant_sleep,
            max_reconnect_attempts=2,
        ) as feed:
            out: list[object] = []
            async for evt in feed.smart_money_positions("0xnba_market"):
                out.append(evt)
                if len(out) >= 10:
                    break
            return out

    events = asyncio.run(run())
    positions = [e for e in events if isinstance(e, SmartMoneyPosition)]
    warns = [e for e in events if isinstance(e, DegradedFeedWarning)]
    assert len(positions) == 2
    recv_warns = [w for w in warns if "recv_failed" in w.reason]
    assert recv_warns
    assert recv_warns[0].attempt == 1
    assert len(provider.calls) >= 2  # reconnect


def test_polygon_chain_default_fixture_loads(
    fake_clock: SteppingClock, instant_sleep: InstantSleep
) -> None:
    """The shipped data/fixtures/smart_money_wallets.json loads via default path."""
    sub = FakeChainSubscription(logs=[])
    provider = FakeSubscriptionProvider(queue=[sub])
    feed = PolygonChainLive(
        subscription_provider=provider,
        clock=fake_clock,
        sleep=instant_sleep,
        max_reconnect_attempts=1,
    )
    wl = feed._load_whitelist()
    assert len(wl) >= 10
