"""Fast-forwardable :class:`SettlementClient` fake.

Why this lives here and not in :file:`tests/agent/runtime/test_sandbox_settlement_poller.py`:

The poller tests use a scripted ``FakeSettlementClient`` whose response
sequence is fixed at construction time — that fake can't model the
"market resolved BETWEEN process kill and restart" timeline the T-B-020
restart scenario (b) requires.

:class:`MockGammaAPI` is a STATEFUL fake. Each market starts in a
``pending`` posture (``resolve_market(...)`` returns ``None``). Calling
:meth:`MockGammaAPI.resolve_now` flips the market's state so subsequent
``resolve_market`` calls return a :class:`SettlementResult` with
``resolved=True``. The flip is the "fast-forward gamma-api between
ticks" that simulates a market resolving while the sandbox loop was
killed.

The fake is intentionally minimal — no network simulation, no retry
budgets, no schema-drift checks. Those concerns live in T-B-019's VCR
cassettes; this fake exists ONLY to drive the restart scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from agent.data.polymarket_settlement import SettlementResult


@dataclass
class _MarketState:
    """One market's resolution posture inside :class:`MockGammaAPI`.

    ``pending`` means :meth:`MockGammaAPI.resolve_market` returns
    ``None`` (gamma-api hasn't surfaced the resolution yet).
    ``resolved`` means a :class:`SettlementResult` is returned with the
    pinned outcome + winning price.
    """

    outcome_when_resolved: Literal["yes", "no", "void"]
    winning_price_when_resolved: float
    end_date: datetime
    resolution_ts: datetime
    is_resolved: bool = False


@dataclass
class MockGammaAPI:
    """Stateful gamma-api fake for the T-B-020 restart scenario.

    Use :meth:`register_market` to seed a market in the ``pending``
    posture. Use :meth:`resolve_now` to flip a market to resolved
    (simulates real gamma-api seeing the UMA resolution land).

    Records every :meth:`resolve_market` call in :attr:`calls` so tests
    can assert per-bet query counts (the smart-poll guarantee
    inherited from T-B-019 still holds — the loop only queries due
    bets).
    """

    _state: dict[str, _MarketState] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def register_market(
        self,
        *,
        market_id: str,
        outcome: Literal["yes", "no", "void"] = "yes",
        winning_price: float = 1.0,
        end_date: datetime | None = None,
        resolution_ts: datetime | None = None,
    ) -> None:
        """Seed a market in the ``pending`` posture.

        The outcome + winning_price + resolution_ts are pinned NOW but
        only become observable to :meth:`resolve_market` after
        :meth:`resolve_now` flips the market. Tests use this so the
        "what does the resolved outcome look like" decision is made up
        front, then the fast-forward simulates the gamma-api seeing it.
        """
        self._state[market_id] = _MarketState(
            outcome_when_resolved=outcome,
            winning_price_when_resolved=winning_price,
            end_date=end_date or datetime(2026, 5, 31, 9, 0, 0, tzinfo=UTC),
            resolution_ts=resolution_ts
            or datetime(2026, 5, 26, 19, 30, 0, tzinfo=UTC),
        )

    def resolve_now(self, market_id: str) -> None:
        """Flip the market to resolved — the "fast-forward" entry point.

        Idempotent: calling twice is a no-op. Raises :class:`KeyError`
        on an unknown market — that's a test-suite bug, not a runtime
        path the loop ever exercises.
        """
        if market_id not in self._state:
            raise KeyError(
                f"MockGammaAPI: cannot resolve unregistered market {market_id!r}"
            )
        self._state[market_id].is_resolved = True

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        """:class:`SettlementClient` Protocol entry point.

        Returns ``None`` if the market is still pending; a
        :class:`SettlementResult` once :meth:`resolve_now` has fired.
        Raises :class:`KeyError` on an unknown market — the loop would
        never query an unknown market in production (the executor's
        ``market_resolver`` guard catches that), so this guard is a
        defensive check for test correctness only.
        """
        self.calls.append(market_id)
        if market_id not in self._state:
            raise KeyError(
                f"MockGammaAPI: no scripted state for market {market_id!r}"
            )
        ms = self._state[market_id]
        if not ms.is_resolved:
            return None
        return SettlementResult(
            market_id=market_id,
            resolved=True,
            outcome=ms.outcome_when_resolved,
            winning_price=ms.winning_price_when_resolved,
            resolution_ts=ms.resolution_ts,
            end_date=ms.end_date,
        )


__all__ = ["MockGammaAPI"]
