"""V1.7 — Polymarket ACTUAL-trades source (the real-fills provenance gate).

Every returned price carries the SINGLE canonical provenance ``actual_trade``
(Codex-r2-M5). The source NEVER synthesizes/interpolates and has no midpoint fallback
(Codex-2), so the P2 probe can FAIL CLOSED on a market with no real trades.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Warm ``data.etl.pit_correct`` BEFORE ``data.sources`` to sidestep the repo's
# cold-start import cycle (``data.sources._http`` ↔ ``data.etl`` via nba). Importing
# data.etl first caches pit_correct, so _http resolves cleanly. (Same ordering the
# data.etl tests rely on; only matters when this test runs in isolation.)
import data.etl.pit_correct  # noqa: F401
from data.sources.polymarket_trades import (
    PROVENANCE_ACTUAL_TRADE,
    PolymarketTradesClient,
    TradePrice,
)


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeHttp:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, url: str, *, params: dict[str, Any] | None = None, timeout: float = 10.0) -> _FakeResp:
        self.calls.append((url, params))
        return _FakeResp(self._payload)


_T0 = 1_717_240_200  # 2024-06-01T11:50:00Z
_T1 = 1_717_243_800  # 2024-06-01T12:50:00Z


def _client(payload: object) -> tuple[PolymarketTradesClient, _FakeHttp]:
    http = _FakeHttp(payload)
    return PolymarketTradesClient(http=http), http


def test_provenance_constant_is_single_canonical_spelling() -> None:
    assert PROVENANCE_ACTUAL_TRADE == "actual_trade"


def test_fetch_trades_parses_and_tags_actual_trade_provenance() -> None:
    payload = [
        {"price": "0.62", "timestamp": _T1, "size": "10"},
        {"price": "0.58", "timestamp": _T0, "size": "5"},  # earlier
    ]
    client, http = _client(payload)
    trades = client.fetch_trades("0xcond")
    assert [t.provenance for t in trades] == [PROVENANCE_ACTUAL_TRADE] * 2
    # sorted ascending by ts → earliest first
    assert trades[0].price == 0.58
    assert trades[1].price == 0.62
    assert trades[0].ts == datetime.fromtimestamp(_T0, tz=UTC)
    # queried the Data API /trades for the right market
    assert http.calls[0][1] == {"market": "0xcond", "limit": 1000}


def test_fetch_trades_pit_filters_future_trades() -> None:
    payload = [
        {"price": "0.55", "timestamp": _T0, "size": "5"},
        {"price": "0.70", "timestamp": _T1, "size": "5"},  # after asof
    ]
    client, _ = _client(payload)
    asof = datetime.fromtimestamp(_T0 + 60, tz=UTC)  # between T0 and T1
    trades = client.fetch_trades("0xc", asof_ts=asof)
    assert [t.price for t in trades] == [0.55]  # the future trade is excluded


def test_fetch_trades_tolerates_data_envelope_and_skips_malformed() -> None:
    payload = {"data": [
        {"price": "0.61", "timestamp": _T0},
        {"price": "not-a-number", "timestamp": _T1},  # malformed → skipped
        {"timestamp": _T1},                            # no price → skipped
    ]}
    client, _ = _client(payload)
    trades = client.fetch_trades("0xc")
    assert [t.price for t in trades] == [0.61]


def test_entry_price_returns_first_real_trade() -> None:
    payload = [
        {"price": "0.62", "timestamp": _T1},
        {"price": "0.58", "timestamp": _T0},
    ]
    client, _ = _client(payload)
    entry = client.entry_price("0xc")
    assert isinstance(entry, TradePrice)
    assert entry.price == 0.58  # earliest = entry
    assert entry.provenance == PROVENANCE_ACTUAL_TRADE


def test_entry_price_none_when_no_real_trades_fail_closed() -> None:
    # Empty trade history → None → the probe must SKIP (never synthesize an entry).
    client, _ = _client([])
    assert client.entry_price("0xc") is None
    client2, _ = _client({"data": []})
    assert client2.entry_price("0xc") is None
