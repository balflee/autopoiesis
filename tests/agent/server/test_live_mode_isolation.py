"""V1.3 — LIVE mode isolation (Codex-1, HIGH).

The mode selector ``_select_loop_sources`` must be MUTUALLY EXCLUSIVE: with
``SANDBOX_LIVE=1`` it builds the LIVE sources and NEVER constructs the replay/synthetic
path — even when cached cassettes exist on disk. Unset ⇒ the existing default
(replay-when-cassettes-present, else idle), unchanged.
"""

from __future__ import annotations

import asyncio

from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.replay_runner import _ReplaySettlementClient, _ReplayTickInputSource
from agent.data._realtime_buffer import UtcClock
from agent.runtime.live_tick_input_source import LiveTickInputSource
from agent.runtime.polymarket_settlement_client import PolymarketSettlementClient
from agent.server.main import (
    _build_live_sources,
    _IdleTickInputSource,
    _select_loop_sources,
)


def _snap() -> MarketSnapshot:
    """One cached cassette (cassettes present on disk)."""
    return MarketSnapshot(
        market_id="m-cassette-1",
        slug="test-open-2025-06-01-Sinner-vs-Shelton",
        end_date_iso="2025-06-01T20:00:00+00:00",
        resolution_ts_iso="2025-06-01T19:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts="2025-05-31T00:00:00+00:00", mid_price=0.5)],
    )


class _SentinelTick:
    def inputs_for(self, *, asof_ts, tick):  # pragma: no cover - never called
        return None


class _SentinelSettlement:
    async def resolve_market(self, market_id):  # pragma: no cover - never called
        return None


def _fake_live_builder(*, wall_clock):
    """Records that the LIVE path was taken; returns sentinels (no corpus/httpx)."""
    _fake_live_builder.calls += 1  # type: ignore[attr-defined]
    return _SentinelTick(), _SentinelSettlement(), (lambda mid: None)


_fake_live_builder.calls = 0  # type: ignore[attr-defined]


def test_sandbox_live_never_builds_replay_even_with_cassettes() -> None:
    """SANDBOX_LIVE=1 + cassettes present → LIVE path, replay/synthetic NOT built."""
    _fake_live_builder.calls = 0  # type: ignore[attr-defined]
    tick, settlement, _resolver = _select_loop_sources(
        sandbox_live=True,
        snapshots=[_snap()],  # cassettes DO exist on disk
        wall_clock=UtcClock(),
        live_builder=_fake_live_builder,
    )
    # The LIVE builder was the ONLY path taken.
    assert _fake_live_builder.calls == 1  # type: ignore[attr-defined]
    assert isinstance(tick, _SentinelTick)
    assert isinstance(settlement, _SentinelSettlement)
    # The replay/synthetic components were NEVER constructed.
    assert not isinstance(tick, _ReplayTickInputSource)
    assert not isinstance(settlement, _ReplaySettlementClient)


def test_default_uses_replay_when_cassettes_present() -> None:
    """SANDBOX_LIVE unset + cassettes present → the EXISTING replay default (unchanged).
    The live builder is NOT invoked."""
    _fake_live_builder.calls = 0  # type: ignore[attr-defined]
    tick, settlement, resolver = _select_loop_sources(
        sandbox_live=False,
        snapshots=[_snap()],
        wall_clock=UtcClock(),
        live_builder=_fake_live_builder,
    )
    assert _fake_live_builder.calls == 0  # type: ignore[attr-defined]
    assert isinstance(tick, _ReplayTickInputSource)
    assert isinstance(settlement, _ReplaySettlementClient)
    assert resolver is not None


def test_default_idle_when_no_cassettes() -> None:
    """SANDBOX_LIVE unset + NO cassettes → idle fallback (loop boots, places no bets)."""
    tick, settlement, resolver = _select_loop_sources(
        sandbox_live=False,
        snapshots=[],
        wall_clock=UtcClock(),
        live_builder=_fake_live_builder,
    )
    assert isinstance(tick, _IdleTickInputSource)
    assert settlement is None
    assert resolver is None


def test_build_live_sources_constructs_real_live_components() -> None:
    """The real LIVE builder wires a LiveTickInputSource + PolymarketSettlementClient +
    an end-date market_resolver (catches wiring/import typos in the V1.3 seam)."""
    tick, settlement, resolver = _build_live_sources(wall_clock=UtcClock())
    assert isinstance(tick, LiveTickInputSource)
    assert isinstance(settlement, PolymarketSettlementClient)
    assert callable(resolver)
    # Close the httpx client so the test leaves no unclosed-resource warning.
    asyncio.run(settlement._http.aclose())
