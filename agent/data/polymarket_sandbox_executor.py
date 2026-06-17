"""Sandbox executor — records-only, never broadcasts.

Spec anchors
------------

* CEO sprint_8 sandbox-pivot plan (2026-05-26): replace the
  Polymarket order broadcast with a local recorder while preserving
  the rest of the agent loop. Contract commit on Polygon still
  executes; the order leg is replaced by :func:`SandboxExecutor.place_order`.
* TECHNICAL_PLAN §4.1 line 750-761: "决策上链：BET 和 NO_BET 都消耗
  BREATH (防躺平), 都必须 log. 链上 commit 后才发 Polymarket 真实订单.
  → 沙盒模式：合约 commit 仍执行, polymarket 调用替换为
  SandboxExecutor.place_order (无广播)."
* PRD §6.6 line 372-383: the 4-constraint min() still binds upstream —
  the executor accepts whatever ``size_usd`` the sizer hands it and
  records it verbatim. Validation that the size is non-negative
  happens at the :class:`BetRecord` boundary (Pydantic ``gt=0.0``).
* T-B-017 spike report (``reports/sprint8/spike_settlement_lag_report.md``):
  median gameStart→closedTime lag is +4.89 h, max +5.62 h, all under
  6 h. The 2-hour ``settle_lag`` default below covers the dashboard's
  "we *should* see this resolved by then" UI hint — NOT the agent's
  real polling cadence (T-B-019 polls on ``umaResolutionStatus``,
  not on the heuristic).

Public surface
--------------

* :class:`Executor` — the Protocol the production polymarket
  executor (deferred to a later sprint) and the
  :class:`SandboxExecutor` both implement. Defined here because the
  task is the first artefact that needs a typed reference to it; the
  real `polymarket_executor.py` will import this Protocol when it
  lands and the static typecheck will catch any divergence.
* :class:`SandboxOrderResult` — Pydantic model returned by
  :meth:`SandboxExecutor.place_order`. Contains the synthetic
  ``order_id`` (UUID4 hex), ``accepted=True``, ``broadcast=False``,
  plus the :class:`BetRecord` that was written.
* :class:`MarketInfo` — minimal market metadata the executor needs
  (just ``end_date_iso`` for the ``expected_settle_ts`` derivation).
* :class:`SandboxExecutor` — the implementation. Constructed with a
  :class:`SandboxStateWriter` + a ``market_resolver`` callable that
  maps ``market_id → MarketInfo``. ``place_order`` is async to match
  the Executor Protocol — the body is synchronous (record-only), the
  ``async`` keyword is the type-compatibility hook.

Network contract
----------------

The module imports neither ``httpx`` nor ``websockets`` nor ``socket``.
Tests assert via a ``socket.create_connection`` tripwire that
:meth:`SandboxExecutor.place_order` never opens a TCP connection.

Single-writer architectural invariant
-------------------------------------

:class:`SandboxExecutor` MUST be constructed with a
:class:`SandboxStateWriter` that the rest of the runtime also uses —
two writer instances against the same ``state/sandbox/`` root would
break the "tail-followable JSONL" guarantee. The orchestrator wires
this in :mod:`agent.runtime`; tests inject an instance bound to a
``tmp_path``.

Idempotency
-----------

The executor maintains an in-memory ``_seen_bet_ids`` set so a
caller that double-submits the same ``order_id`` (e.g. on retry after
a partial-failure recovery) doesn't double-append the JSONL line.
Idempotency is *per-process*: if the sandbox restarts and replays an
order, the writer's append is the only source of truth — the in-memory
set is rebuilt from disk by the recovery boot (T-B-019 follow-up).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from agent.data._realtime_buffer import Clock, UtcClock
from agent.data.polymarket_settlement import _parse_polymarket_ts
from agent.data.sandbox_state import BetRecord, SandboxStateWriter

# --------------------------------------------------------------------------- #
# Wire shapes
# --------------------------------------------------------------------------- #


class SandboxOrderResult(BaseModel):
    """Return value of :meth:`SandboxExecutor.place_order`.

    * ``order_id``   — synthetic UUID4 hex, identifies the recorded bet.
    * ``accepted``   — always ``True`` for the sandbox; production
      executor returns ``False`` on liquidity / margin reject.
    * ``broadcast``  — always ``False``; the architectural invariant
      this module's network-free design guarantees.
    * ``bet``        — the :class:`BetRecord` that was appended to
      ``open_bets.jsonl``. Returned so callers can correlate without
      re-reading the JSONL.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: str
    accepted: bool
    broadcast: bool
    bet: BetRecord


@dataclass(frozen=True)
class MarketInfo:
    """Minimal market metadata the executor needs.

    Why a separate type instead of consuming a Polymarket-specific
    market model: the executor doesn't care about the question text,
    the slug, the outcome prices — only the resolution-window upper
    bound (``end_date_iso``) for the ``expected_settle_ts``
    derivation. Keeping the surface narrow lets tests inject literal
    strings without standing up a full :class:`TennisMarket`.

    ``end_date_iso`` is the gamma-api ``endDate`` field passed through
    verbatim. Per the T-B-017 spike report, ``endDate`` is misleadingly
    named (it's the tour-period upper bound, NOT the match end). This
    is fine for the sandbox's *expected* settlement heuristic — we
    add a +2 h cushion and the dashboard treats the value as a UI hint,
    not a contract. The real settlement comes from
    :class:`agent.data.polymarket_settlement.SettlementResult`.
    """

    end_date_iso: str | None


class MarketResolver(Protocol):
    """Async-free callable mapping ``market_id → MarketInfo | None``.

    Returns ``None`` when the market is unknown — the executor raises
    :class:`UnknownMarketError` so the caller's sizer can short-circuit
    a NO_BET. Returns a :class:`MarketInfo` with ``end_date_iso=None``
    when the market is known but has no end-date hint — the executor
    raises :class:`MissingEndDateError` in that case.
    """

    def __call__(self, market_id: str) -> MarketInfo | None: ...


# --------------------------------------------------------------------------- #
# Executor Protocol — the shape sandbox + production share.
# --------------------------------------------------------------------------- #


class Executor(Protocol):
    """The Polymarket executor surface the decision loop calls.

    Sandbox + production both implement this Protocol; the type
    checker enforces shape parity. The signature is the locked T-B-018
    acceptance contract — adding a parameter is a Protocol break and
    triggers a track_b_backend interface bump. (A9 2026-06-13: the five
    optional ``*_at_bet`` storm stamps were added as keyword params with
    ``None`` defaults across the Protocol, the executor, and every test
    fake — sanctioned by the A9 plan; ``None`` ⇒ pre-kit behaviour.)
    """

    async def place_order(
        self,
        *,
        market_id: str,
        side: Literal["YES", "NO"],
        price: float,
        size_usd: float,
        signal_scores: dict[str, float] | None = None,
        storm_at_bet: float | None = None,
        edge_at_bet: float | None = None,
        min_edge_at_bet: float | None = None,
        gamma_at_bet: float | None = None,
        eff_min_edge_at_bet: float | None = None,
        fill_price: float | None = None,
        fee_bps: float | None = None,
        spread_paid_usd: float | None = None,
        liquidity_cap_usd: float | None = None,
    ) -> SandboxOrderResult: ...


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class SandboxExecutorError(Exception):
    """Base class — exists so tests can ``pytest.raises(SandboxExecutorError)``."""


class UnknownMarketError(SandboxExecutorError):
    """Raised when ``market_resolver`` returns ``None`` for the market_id."""


class MissingEndDateError(SandboxExecutorError):
    """Raised when the market is known but has no ``end_date_iso``."""


class DuplicateOrderError(SandboxExecutorError):
    """Raised when the same ``order_id`` is presented twice within a process.

    Idempotency invariant — see module docstring. Defensive guard
    against caller bugs; production retry paths SHOULD generate a
    fresh UUID per attempt.
    """


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


_DEFAULT_SETTLE_LAG: Final[timedelta] = timedelta(hours=2)
"""+2h after ``end_date_iso`` is the dashboard "should-be-resolved-by"
hint. NOT the agent's real polling cadence — see module docstring."""


# --------------------------------------------------------------------------- #
# SandboxExecutor
# --------------------------------------------------------------------------- #


@dataclass
class SandboxExecutor:
    """Records-only Polymarket executor — zero network broadcast.

    Construct with a :class:`SandboxStateWriter` (shared with the rest
    of the sandbox runtime) and a :class:`MarketResolver`. Optionally
    inject a :class:`Clock` (from :mod:`agent.data._realtime_buffer`)
    for deterministic timestamps in tests and a ``settle_lag`` to
    override the 2-hour heuristic.

    ``broadcast_count`` is a read-only invariant the reconciliation
    gate asserts (== 0). Mirrors the
    :class:`agent.runtime.sprint7_dryrun.DryRunExecutor` convention so
    the sprint_8 sandbox harness can lift the same assertion verbatim.
    """

    state_writer: SandboxStateWriter
    market_resolver: MarketResolver
    settle_lag: timedelta = _DEFAULT_SETTLE_LAG
    clock: Clock = field(default_factory=UtcClock)
    broadcast_count: int = 0  # MUST stay 0 across the whole run
    _seen_bet_ids: set[str] = field(default_factory=set)

    async def place_order(
        self,
        *,
        market_id: str,
        side: Literal["YES", "NO"],
        price: float,
        size_usd: float,
        signal_scores: dict[str, float] | None = None,
        storm_at_bet: float | None = None,
        edge_at_bet: float | None = None,
        min_edge_at_bet: float | None = None,
        gamma_at_bet: float | None = None,
        eff_min_edge_at_bet: float | None = None,
        fill_price: float | None = None,
        fee_bps: float | None = None,
        spread_paid_usd: float | None = None,
        liquidity_cap_usd: float | None = None,
    ) -> SandboxOrderResult:
        """Record an order in the sandbox JSONL — never broadcast.

        Steps:

        1. Resolve the market to get ``end_date_iso``. Missing → raise.
        2. Mint a UUID4 ``order_id`` + check the in-memory dedup set.
        3. Compute ``expected_settle_ts`` = end_date + ``settle_lag``.
        4. Build the :class:`BetRecord` (Pydantic validates the price/size
           ranges + the side literal at this boundary).
        5. Append the record to ``open_bets.jsonl`` via the writer.
        6. Return :class:`SandboxOrderResult` with ``broadcast=False``.

        ``signal_scores`` (Task L3) is an optional decision-time per-engine
        score map ∈ [-1, 1] keyed by engine name; persisted verbatim on the
        :class:`BetRecord` so the settlement-time weight updater can do
        direction-aware credit assignment. ``None``/empty ⇒ ``{}`` (the
        pre-L3 behaviour).

        Raises:
            UnknownMarketError    — resolver returned ``None``.
            MissingEndDateError   — market info had no ``end_date_iso``.
            DuplicateOrderError   — synthetic ID collision (vanishingly
              unlikely; defensive belt for retries).
            pydantic.ValidationError — invalid side / price / size_usd.
        """
        info = self.market_resolver(market_id)
        if info is None:
            raise UnknownMarketError(
                f"sandbox executor: unknown market_id={market_id!r} "
                "(market_resolver returned None)"
            )
        if info.end_date_iso is None:
            raise MissingEndDateError(
                f"sandbox executor: market {market_id!r} has no end_date_iso "
                "(can't derive expected_settle_ts; refusing the order)"
            )

        order_id = uuid.uuid4().hex
        if order_id in self._seen_bet_ids:
            # uuid4 collisions are astronomically unlikely, but guard
            # against caller bugs that re-use this executor's
            # ``_seen_bet_ids`` set in a pathological way.
            raise DuplicateOrderError(
                f"sandbox executor: order_id collision {order_id!r}"
            )
        self._seen_bet_ids.add(order_id)

        now = self.clock.now()
        expected_settle_ts = _derive_expected_settle_ts(
            end_date_iso=info.end_date_iso, lag=self.settle_lag,
        )

        bet = BetRecord(
            bet_id=order_id,
            ts=_to_iso(now),
            market_id=market_id,
            side=side,
            price=price,
            size_usd=size_usd,
            expected_settle_ts=expected_settle_ts,
            status="open",
            signal_scores=dict(signal_scores) if signal_scores else {},
            storm_at_bet=storm_at_bet,
            edge_at_bet=edge_at_bet,
            min_edge_at_bet=min_edge_at_bet,
            gamma_at_bet=gamma_at_bet,
            eff_min_edge_at_bet=eff_min_edge_at_bet,
            fill_price=fill_price,
            fee_bps=fee_bps,
            spread_paid_usd=spread_paid_usd,
            liquidity_cap_usd=liquidity_cap_usd,
        )
        self.state_writer.append_open_bet(bet)

        # ``broadcast_count`` stays 0 by construction — there's no path
        # in this method that touches a network socket. The
        # reconciliation gate asserts this; the tests assert it via a
        # socket.create_connection tripwire.
        return SandboxOrderResult(
            order_id=order_id,
            accepted=True,
            broadcast=False,
            bet=bet,
        )


# --------------------------------------------------------------------------- #
# Pure helpers — unit-testable independently.
# --------------------------------------------------------------------------- #


def _to_iso(ts: datetime) -> str:
    """Coerce ``ts`` to a UTC ISO-8601 string with ``+00:00`` tz.

    Naïve datetimes are treated as UTC (defensive — production wiring
    always passes tz-aware, but tests sometimes don't and we'd rather
    log a tz-aware string than crash).
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _derive_expected_settle_ts(
    *, end_date_iso: str, lag: timedelta,
) -> str:
    """Compute ``expected_settle_ts = end_date_iso + lag`` as ISO-8601 UTC.

    Per T-B-017 spike: ``endDate`` is the misleadingly-named tour-period
    upper bound (NOT the match end). Adding the +2 h cushion is the
    dashboard UI hint, NOT the polling cadence. The settlement poller
    (T-B-019) ignores this field and polls on
    ``umaResolutionStatus`` directly.

    Delegates ISO parsing to
    :func:`agent.data.polymarket_settlement._parse_polymarket_ts` so
    the two-format normalisation (``Z`` and ``+00`` shapes) has one
    canonical implementation. Raises :class:`ValueError` if the input
    is unparseable.
    """
    dt = _parse_polymarket_ts(end_date_iso)
    if dt is None:
        raise ValueError(
            f"sandbox executor: end_date_iso={end_date_iso!r} is not ISO-8601"
        )
    return (dt + lag).isoformat()


__all__ = [
    "DuplicateOrderError",
    "Executor",
    "MarketInfo",
    "MarketResolver",
    "MissingEndDateError",
    "SandboxExecutor",
    "SandboxExecutorError",
    "SandboxOrderResult",
    "UnknownMarketError",
]
