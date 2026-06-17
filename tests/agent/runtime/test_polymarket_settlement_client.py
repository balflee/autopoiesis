"""V1.1 — PolymarketSettlementClient delegates to the real gamma-api parser.

Exercises the real `resolve_market`/`_project` path (Codex-9): a resolved
payload → SettlementResult; a not-yet-resolved payload → None; a transport
error propagates (so the poller's retry-on-Exception machinery fires)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.runtime.polymarket_settlement_client import PolymarketSettlementClient


class _FakeResp:
    def __init__(self, payload: Any, *, raises: BaseException | None = None) -> None:
        self.status_code = 200
        self._payload = payload
        self._raises = raises

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self._raises is not None:
            raise self._raises


class _FakeClient:
    """Minimal async `_HttpClient`: returns a pre-set response from `get`."""

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp
        self.calls: list[str] = []

    async def get(self, url: str, **_kwargs: Any) -> _FakeResp:
        self.calls.append(url)
        return self._resp


_RESOLVED_YES = {
    "id": "0xabc",
    "umaResolutionStatus": "resolved",
    "outcomePrices": '["1", "0"]',
    "closedTime": "2026-01-15T12:00:00Z",
    "endDate": "2026-01-15T11:00:00Z",
}


def test_resolved_market_projects_to_settlement_result() -> None:
    client = _FakeClient(_FakeResp(_RESOLVED_YES))
    sc = PolymarketSettlementClient(http=client)  # type: ignore[arg-type]
    result = asyncio.run(sc.resolve_market("0xabc"))
    assert result is not None
    assert result.resolved is True
    assert result.market_id == "0xabc"
    assert result.outcome == "yes"
    assert result.winning_price == pytest.approx(1.0)
    assert client.calls == ["https://gamma-api.polymarket.com/markets/0xabc"]


def test_unresolved_market_returns_none() -> None:
    pending = dict(_RESOLVED_YES, umaResolutionStatus="open")
    sc = PolymarketSettlementClient(http=_FakeClient(_FakeResp(pending)))  # type: ignore[arg-type]
    assert asyncio.run(sc.resolve_market("0xabc")) is None


def test_transport_error_propagates() -> None:
    boom = RuntimeError("gamma 503")
    sc = PolymarketSettlementClient(
        http=_FakeClient(_FakeResp(_RESOLVED_YES, raises=boom))  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="gamma 503"):
        asyncio.run(sc.resolve_market("0xabc"))
