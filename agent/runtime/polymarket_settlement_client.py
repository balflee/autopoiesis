"""Production ``SettlementClient`` — wraps gamma-api over httpx.

Promotes the VCR-tested ``HttpSettlementClient`` shape (from
``tests/agent/runtime/test_sandbox_settlement_poller.py``) into runtime
(plan-loop V1.1, Codex-9): the real
:func:`agent.data.polymarket_settlement.resolve_market` parser is exercised, so
gamma-api schema drift is caught by the same code path prod runs. Returns
``None`` for a not-yet-resolved market; raises on transport errors (the
:class:`agent.runtime.sandbox_settlement_poller.SandboxSettlementPoller`
retry-on-5xx machinery handles those).

This is the LIVE settlement client, wired ONLY via the explicit ``live`` mode
selector (plan-loop V1.3) — never co-active with the cassette
``_ReplaySettlementClient``.
"""

from __future__ import annotations

from typing import cast

import httpx

from agent.data.polymarket_settlement import (
    SettlementResult,
    _HttpClient,
    resolve_market,
)


class PolymarketSettlementClient:
    """Production-shaped ``SettlementClient`` backed by gamma-api over httpx.

    ``http`` is an injected :class:`httpx.AsyncClient` (structural subtype of
    the :class:`agent.data.polymarket_settlement._HttpClient` Protocol), so the
    caller owns its lifecycle/timeout config and tests can inject a fake.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        return await resolve_market(market_id, client=cast(_HttpClient, self._http))


__all__ = ["PolymarketSettlementClient"]
