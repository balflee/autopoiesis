"""Sandbox settlement poller — closes the bet → P&L → weights → BREATH loop.

Spec anchors
------------

* CEO sprint_8 sandbox-pivot plan (2026-05-26, locked architecture
  decision #1): "per-bet smart poll keyed by ``expected_settle_ts``;
  15 min interval, skip until past." Each :meth:`SandboxSettlementPoller.tick`
  call queries ONLY bets whose ``expected_settle_ts < now`` — a tick
  with 100 open bets and 5 due hits gamma-api exactly 5 times.
* CEO decision MED #1 (locked 2026-05-26): settlement-lag thresholds
  6h → ``settlement_lag_warning`` STATE_HOOK; 24h →
  ``settlement_lag_critical``. Per-bet lag is computed off
  ``now - expected_settle_ts`` and emitted BEFORE the gamma-api fetch
  so operators see a stuck bet even when the network is fine.
* PRD §6.5 (line 359-368) symmetric P&L conversion:

      pnl_usd = polymarket_payout_usd - bet_size_usd
      breath += pnl_usd × CONVERSION_RATE (if positive)
      breath -= |pnl_usd| × CONVERSION_RATE (if negative)

  The conversion → BREATH happens in the injected
  :class:`ChainAdapter`; this module computes ``pnl_usd`` from the
  three locked formulas (winner / loser / void) and hands the scalar
  to ``chain_adapter.update_breath_from_pnl``.

* TECHNICAL_PLAN §4.1 (line 760-775): "链上 commit 后才发 Polymarket
  真实订单 → outcome = wait_for_resolution(bet_id). 权重更新 (Phase 3
  跳过) — Phase 2 extended 仍学." Sandbox Phase 2 extended unlocks β₁
  so the post-settlement gradient signal flows back through the
  weight updater — we pass the canonical phase label
  ``PHASE_2_EXTENDED`` so the (separately injected) updater knows it's
  in the unlocked regime.

* TECHNICAL_PLAN §4.2 (line 806-820): 6 fusion parameters trained via
  softmax-reparameterised gradient descent. The settlement-time
  callback is the gradient feedback channel that the per-tick
  ``agent.engines.weight_updater.WeightUpdater`` (which consumes only
  decision-time features per PRD §6.8) cannot provide on its own.

Append-only invariant (CEO 2026-05-26 lock)
-------------------------------------------

On every resolution the poller appends EXACTLY two lines:

1. ``settled_bets.jsonl``  — a :class:`SettledBetRecord` carrying the
   outcome, winning price, and computed pnl_usd.
2. ``open_bets.jsonl``     — a :class:`BetRecord` with
   ``status="settled"``. Same bet_id as the original entry; the LAST
   observed status per bet_id is the bet's current state. NO in-place
   edits; readers fold left-to-right.

The folder in :meth:`_select_due_bets` reads every line and keeps only
those bet_ids whose latest status is ``"open"`` AND whose
``expected_settle_ts < now``.

Retry policies (locked)
-----------------------

* gamma-api 5xx → backoff 1s, 2s, 4s, 8s (4 retries = 5 total
  attempts); after the 5th failure emit a ``settlement_query_failed``
  STATE_HOOK and SKIP the bet (next tick will retry).
* chain ``update_breath_from_pnl`` → backoff 2s, 4s, 8s (3 retries =
  4 total attempts); after the 4th failure emit a
  ``chain_breath_update_failed`` STATE_HOOK. The JSONL is already
  written so the bet stays "settled" — the BREATH delta is lost for
  this cycle but a follow-up reconciliation job (sprint_9) can replay
  from settled_bets.jsonl.

Why two retry policies: gamma-api is a read-only public endpoint; the
chain write is more expensive (gas + nonce coordination) so we give
fewer attempts with longer waits.

Network contract
----------------

This module imports neither ``httpx`` nor ``web3``. The two transport
surfaces (``SettlementClient``, ``ChainAdapter``) are Protocols
injected by the runtime wiring. Tests pass in-memory fakes; the
production wiring will plug in the real gamma-api + RH-Chain testnet
adapters when those land in sprint_9.

The sleep used for retries is also a Protocol (``Sleeper``) so tests
can avoid real wall-clock waits — the default is :func:`asyncio.sleep`.

Lookahead bias
--------------

The poller's ``now`` is sourced from an injected :class:`Clock`; tests
pin a deterministic clock. The poller writes records that carry
``settled_at`` semantics, but those records are CONSUMED by the
dashboard + the weight updater's settlement channel — they NEVER feed
back into a per-tick feature dict (which would trip the look-ahead
auditor's ``settled_at*`` prefix block).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, Protocol

from agent.data._realtime_buffer import Clock, UtcClock
from agent.data.polymarket_settlement import SettlementResult, _parse_polymarket_ts
from agent.data.sandbox_state import (
    BetRecord,
    SandboxStateWriter,
    SettledBetRecord,
    assert_cost_fields_present,
    execution_cost_usd,
    iter_jsonl,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Locked constants — touch only via a CEO-plan amendment.
# --------------------------------------------------------------------------- #


LAG_WARNING: Final[timedelta] = timedelta(hours=6)
"""CEO 2026-05-26 decision MED #1: emit ``settlement_lag_warning`` when
``now - expected_settle_ts`` crosses 6 hours."""

LAG_CRITICAL: Final[timedelta] = timedelta(hours=24)
"""CEO 2026-05-26 decision MED #1: escalate to ``settlement_lag_critical``
at 24 hours. The two thresholds are EXCLUSIVE — a 26-hour lag emits the
critical event, not both."""

SANDBOX_PHASE_LABEL: Final[str] = "PHASE_2_EXTENDED"
"""TECHNICAL_PLAN §4.2 label for the sandbox-extended Phase 2 regime
where β₁ is unfrozen. The settlement-time weight updater consumes this
literal — distinct from :class:`agent.core.state.Phase` enum values
because the sandbox regime has no on-chain counterpart."""

GAMMA_RETRY_DELAYS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0, 8.0)
"""Acceptance criterion: ``5xx from gamma-api → exponential backoff:
1s, 2s, 4s, 8s; fail after 4 retries; alert via STATE_HOOK``. Four
delays = four retries = five total attempts (initial + retries)."""

CHAIN_RETRY_DELAYS: Final[tuple[float, ...]] = (2.0, 4.0, 8.0)
"""Acceptance criterion: chain ``update_breath_from_pnl`` failure →
``retry with exponential backoff (2s, 4s, 8s, then alert)``. Three
delays = three retries = four total attempts."""

STATE_HOOK_KIND_LAG_WARNING: Final[str] = "settlement_lag_warning"
STATE_HOOK_KIND_LAG_CRITICAL: Final[str] = "settlement_lag_critical"
STATE_HOOK_KIND_GAMMA_FAILED: Final[str] = "settlement_query_failed"
STATE_HOOK_KIND_CHAIN_FAILED: Final[str] = "chain_breath_update_failed"


# --------------------------------------------------------------------------- #
# Injected Protocols — every external dependency is one of these so tests
# inject deterministic fakes and the production wiring plugs real adapters
# behind the same shape.
# --------------------------------------------------------------------------- #


class SettlementClient(Protocol):
    """Wraps :func:`agent.data.polymarket_settlement.resolve_market`.

    Returns ``None`` for not-yet-resolved markets; raises on transport
    errors (httpx.HTTPStatusError on 5xx, etc.). The retry-on-5xx
    machinery in :class:`SandboxSettlementPoller` matches on a base
    :class:`Exception` so any transient failure shape works — the
    production wiring decides how to raise, the poller decides how to
    retry.
    """

    async def resolve_market(self, market_id: str) -> SettlementResult | None: ...


class WeightUpdater(Protocol):
    """Settlement-time weight updater channel.

    DISTINCT from :class:`agent.engines.weight_updater.WeightUpdater`
    (the per-tick decision-time updater): per PRD §6.8 the latter
    refuses keys named ``outcome*`` / ``settled_at*`` etc., but the
    settlement gradient signal is EXACTLY that post-game data. The
    settlement channel exists so the weight model can still learn
    from realised outcomes; the auditor stays clean because this
    Protocol is consumed by the settlement poller (a non-features
    location) and never by anything under ``features/``.
    """

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None: ...


class ChainAdapter(Protocol):
    """Subset of the RH-Chain testnet adapter the poller needs.

    The full chain adapter (sprint_9) exposes many surfaces; the poller
    only needs the BREATH delta channel keyed on settlement pnl_usd.
    Defined as a Protocol so a fake satisfies it without subclassing.
    """

    async def update_breath_from_pnl(self, pnl_usd: float) -> None: ...


class StateHook(Protocol):
    """Operator-visibility hook contract (v34 F8).

    Pattern: ``hook.emit(kind="settlement_lag_warning", bet_id=..., ...)``.
    The keyword ``kind`` is the discriminator the operator's filter
    matches on (``GENESIS_STATE_HOOK_FILTER`` env var); remaining
    kwargs are the structured payload. Implementations MUST NOT raise
    into the caller — the harness ``_fire_state_hook`` swallows all
    errors per the v34 F8 contract.
    """

    def emit(self, *, kind: str, **payload: Any) -> None: ...


class Sleeper(Protocol):
    """Async sleep — injectable so retry tests don't burn real seconds.

    Production default is :func:`asyncio.sleep`. Tests pass an
    instrumented fake that records call durations + returns
    immediately.
    """

    async def __call__(self, seconds: float) -> None: ...


async def _real_sleep(seconds: float) -> None:
    """Default :class:`Sleeper` — :func:`asyncio.sleep`."""
    await asyncio.sleep(seconds)


# --------------------------------------------------------------------------- #
# Result types — public so tests can assert on them + production runtime can
# log them.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SettlementOutcome:
    """One settled bet — returned by :meth:`SandboxSettlementPoller._resolve_and_settle`.

    Fields
    ------

    ``bet_id`` / ``market_id``:
        Identifiers — match the original :class:`BetRecord`.

    ``outcome``:
        ``"yes"`` / ``"no"`` / ``"void"`` — the
        :class:`SettlementResult` projection of the bet's market.

    ``pnl_usd``:
        Realised P&L per the locked formulas:

        * winner: ``size_usd * (winning_price / bet.price - 1)``
        * loser:  ``-size_usd``
        * void:   ``0``

    ``lag_hours``:
        How late settlement landed vs ``expected_settle_ts``. Useful
        for the operator dashboard's "tail latency" badge.

    ``chain_breath_updated``:
        ``True`` if ``chain_adapter.update_breath_from_pnl`` returned
        successfully (with or without retries). ``False`` if the chain
        adapter exhausted its retry budget — the JSONL was still
        written and a ``chain_breath_update_failed`` STATE_HOOK was
        emitted, but the BREATH delta is lost for this cycle.
    """

    bet_id: str
    market_id: str
    outcome: Literal["yes", "no", "void"]
    pnl_usd: float
    lag_hours: float
    chain_breath_updated: bool


@dataclass(frozen=True)
class PollTickResult:
    """Aggregate output of one :meth:`SandboxSettlementPoller.tick` call.

    ``queried_count``:
        Number of bets whose ``expected_settle_ts < now`` AND whose
        latest status is ``"open"``. Equals the number of gamma-api
        queries attempted this tick (minus retries).

    ``settled_count``:
        Number of bets that resolved this tick (gamma-api returned a
        :class:`SettlementResult` with ``resolved=True``).

    ``pending_count``:
        Bets that were due but not yet resolved on gamma-api side
        (``umaResolutionStatus != 'resolved'``). They stay in the
        open set; next tick will re-query.

    ``failed_count``:
        Bets whose gamma-api query exhausted retries. They stay in
        the open set; next tick will re-query. A
        ``settlement_query_failed`` STATE_HOOK was emitted per bet.

    ``settlements``:
        One :class:`SettlementOutcome` per settled bet, in
        first-resolved order.
    """

    queried_count: int
    settled_count: int
    pending_count: int
    failed_count: int
    settlements: tuple[SettlementOutcome, ...]


# --------------------------------------------------------------------------- #
# The poller itself.
# --------------------------------------------------------------------------- #


@dataclass
class SandboxSettlementPoller:
    """Smart per-bet settlement poller — runs every 15 min in production.

    The architectural contract: ONE :class:`SandboxStateWriter` shared
    with the rest of the sandbox runtime (single-writer invariant per
    T-B-018 docstring), separate Protocol-typed injection points for
    each external dependency.

    Construction
    ------------

    ``state_writer``:
        The :class:`SandboxStateWriter` instance. Reads
        ``open_bets.jsonl`` via the writer's :attr:`open_bets_path` and
        writes both the new ``status="settled"`` open-bets line AND
        the parallel :class:`SettledBetRecord` line.

    ``settlement_client``:
        Resolves a market_id to a :class:`SettlementResult` (or None
        if pending).

    ``weight_updater``:
        Settlement-time gradient feedback channel (see
        :class:`WeightUpdater`).

    ``chain_adapter``:
        BREATH delta channel (see :class:`ChainAdapter`).

    ``state_hook``:
        Operator-visibility hook (see :class:`StateHook`).

    ``clock`` / ``sleeper``:
        Injected for deterministic tests. Defaults are
        :class:`UtcClock` + :func:`asyncio.sleep`.

    ``sandbox_phase``:
        Phase label passed to ``weight_updater.update``. Defaults to
        :data:`SANDBOX_PHASE_LABEL` (``"PHASE_2_EXTENDED"``).

    Concurrency
    -----------

    :meth:`tick` is async + processes bets sequentially. Concurrent
    fan-out would amplify gamma-api load + complicate the retry
    accounting; the locked 15-min cadence has ample headroom for
    sequential processing of any realistic queue size.
    """

    state_writer: SandboxStateWriter
    settlement_client: SettlementClient
    weight_updater: WeightUpdater
    chain_adapter: ChainAdapter
    state_hook: StateHook

    clock: Clock = field(default_factory=UtcClock)
    sleeper: Sleeper = field(default=_real_sleep)
    sandbox_phase: str = SANDBOX_PHASE_LABEL
    # Optional per-bet PROFIT ceiling (USD) forwarded to ``_compute_pnl`` —
    # the survival backtest's liquidity-realism cap. ``None`` (default) is the
    # LIVE-runtime contract: locked formulas, byte-unchanged. See _compute_pnl.
    max_bet_pnl_usd: float | None = None

    # Optional side-correct pricing (realism rule #3) forwarded to
    # ``_compute_pnl`` — winners pay the taken leg's effective cost (NO pays
    # ``1 - price``). ``False`` (default) is the legacy contract: locked legacy
    # formulas, byte-unchanged. The LIVE mode (V1.3) sets it True so a NO bet
    # recorded at the YES mid is settled at the NO leg's effective odds.
    side_correct_pricing: bool = False

    # V1.4/V1.4b fail-closed cost guard (Codex Phase-3 HIGH). ``False`` (default)
    # = the legacy/replay path tolerates missing cost stamps (zero-cost, byte-
    # unchanged). The LIVE/probe path sets it True so a RESOLVED bet missing any
    # execution-cost stamp RAISES at settlement (``assert_cost_fields_present``)
    # rather than being silently booked cost-blind.
    require_cost_fields: bool = False

    # --------------------------------------------------------------------- #
    # Public entry point.
    # --------------------------------------------------------------------- #

    async def tick(self) -> PollTickResult:
        """Run one poll cycle. Called every 15 min by the runtime scheduler.

        Steps:

        1. Read ``open_bets.jsonl`` + fold to "still-open" view.
        2. Filter to bets with ``expected_settle_ts < now``.
        3. For each due bet: emit lag STATE_HOOK if applicable, query
           gamma-api with retries, on success: compute pnl_usd → write
           settled line + open-bets settled marker → call
           weight_updater → call chain_adapter (with retries).
        4. Return aggregated :class:`PollTickResult`.

        Sequencing matters: the file writes happen BEFORE the
        weight_updater + chain calls so a crash mid-tick leaves the
        ledger in a consistent state. The weight + chain updates can
        be replayed from settled_bets.jsonl by a future reconciliation
        job; the JSONL state is the source of truth.
        """
        now = self.clock.now()
        due = self._select_due_bets(now=now)

        settlements: list[SettlementOutcome] = []
        pending = 0
        failed = 0
        for bet in due:
            outcome = await self._resolve_and_settle(bet, now=now)
            if outcome is _PENDING:
                pending += 1
            elif outcome is _FAILED:
                failed += 1
            else:
                # Narrowed: the sentinels are the only non-SettlementOutcome
                # return values; outcome here MUST be a SettlementOutcome.
                assert isinstance(outcome, SettlementOutcome)
                settlements.append(outcome)

        return PollTickResult(
            queried_count=len(due),
            settled_count=len(settlements),
            pending_count=pending,
            failed_count=failed,
            settlements=tuple(settlements),
        )

    async def terminal_close(self, *, now: datetime) -> PollTickResult:
        """Fold EVERY still-open bet into a terminal ledger record at death.

        Unlike :meth:`tick` (which settles only DUE bets — ``expected_settle_ts
        < now``), terminal-close is the death-path fold-ALL (V1.4b / Codex-4):
        every open bet gets a terminal record regardless of
        ``expected_settle_ts``, so none dangles into reincarnation as ghost PnL
        (chained with the V1.3 LIVE-mode isolation).

        Per-bet disposition:

        * **RESOLVED** → the FULL :meth:`_resolve_and_settle` side-effect path
          (settled row + open-flip + weight update + chain breath delta,
          Codex-r2-H4) — NOT just a file marker, so terminal bankroll / breath /
          weights all see the realized PnL. The settled outcome is returned in
          :attr:`PollTickResult.settlements` so the caller can fold the realized
          PnL into the terminal bankroll.
        * **PENDING** (gamma responded, not yet resolved) →
          ``SettledBetRecord(outcome="void", pnl_usd=0)`` + the open-flip marker;
          NO economic side effect.
        * **QUERY FAILURE** (gamma retries exhausted) →
          ``SettledBetRecord(outcome="void", pnl_usd=0,
          reason="terminal_query_failed")`` (Codex-r4-2 / r5-1) — still a ledger
          record; never dangled, never cleared without one.

        Returns a :class:`PollTickResult` whose ``settlements`` are the RESOLVED
        bets (voids are not settlements); ``pending_count`` / ``failed_count``
        count the two void classes.
        """
        open_bets = self._select_open_bets()

        settlements: list[SettlementOutcome] = []
        pending = 0
        failed = 0
        for bet in open_bets:
            outcome = await self._resolve_and_settle(bet, now=now)
            if outcome is _PENDING:
                self._void_terminal(bet, now=now, reason=None)
                pending += 1
            elif outcome is _FAILED:
                self._void_terminal(bet, now=now, reason="terminal_query_failed")
                failed += 1
            else:
                assert isinstance(outcome, SettlementOutcome)
                settlements.append(outcome)

        return PollTickResult(
            queried_count=len(open_bets),
            settled_count=len(settlements),
            pending_count=pending,
            failed_count=failed,
            settlements=tuple(settlements),
        )

    def _void_terminal(
        self,
        bet: BetRecord,
        *,
        now: datetime,
        reason: str | None,
    ) -> None:
        """Write a terminal VOID ledger record for an unresolved open bet.

        Two append-only writes mirroring :meth:`_resolve_and_settle` steps 1+2
        but with NO economic side effect (``pnl_usd=0``, no weight / chain
        call): a ``SettledBetRecord(outcome="void", ...)`` and the
        ``status="settled"`` open-bets flip marker (storm stamps copied
        verbatim, matching the resolve path). ``reason`` is
        ``"terminal_query_failed"`` for an exhausted gamma query (Codex-r4-2);
        ``None`` for a still-pending market (omitted from the JSONL via
        ``exclude_none``).
        """
        settled_ts = _iso_utc(now)
        self.state_writer.append_settled_bet(
            SettledBetRecord(
                bet_id=bet.bet_id,
                market_id=bet.market_id,
                settled_ts=settled_ts,
                outcome="void",
                winning_price=0.0,
                pnl_usd=0.0,
                reason=reason,
            )
        )
        self.state_writer.append_open_bet(
            BetRecord(
                bet_id=bet.bet_id,
                ts=settled_ts,
                market_id=bet.market_id,
                side=bet.side,
                price=bet.price,
                size_usd=bet.size_usd,
                expected_settle_ts=bet.expected_settle_ts,
                status="settled",
                signal_scores=dict(bet.signal_scores),
                storm_at_bet=bet.storm_at_bet,
                edge_at_bet=bet.edge_at_bet,
                min_edge_at_bet=bet.min_edge_at_bet,
                gamma_at_bet=bet.gamma_at_bet,
                eff_min_edge_at_bet=bet.eff_min_edge_at_bet,
                # Cost stamps survive the void flip too (Codex Phase-3 MED).
                fill_price=bet.fill_price,
                fee_bps=bet.fee_bps,
                spread_paid_usd=bet.spread_paid_usd,
                liquidity_cap_usd=bet.liquidity_cap_usd,
            )
        )

    # --------------------------------------------------------------------- #
    # Selection — pure read + fold over open_bets.jsonl.
    # --------------------------------------------------------------------- #

    def _select_due_bets(self, *, now: datetime) -> list[BetRecord]:
        """Read ``open_bets.jsonl`` → return bets that are still open AND due.

        "Still open" = the LAST observed ``status`` field for that
        ``bet_id`` is ``"open"`` (not ``"settled"``). "Due" =
        ``expected_settle_ts < now``.

        The two-pass fold (latest-status map, then filter) is O(N)
        memory — fine for the sprint_8 sandbox where N rarely exceeds
        a few thousand. A streaming reader for the multi-million-row
        production case lands in sprint_10 (the dashboard's tail
        consumer needs the same primitive).
        """
        # Pass 1 — keep the LATEST row per bet_id. Status, expected_ts,
        # and all other fields are read off that row in pass 2.
        latest_row = self._fold_latest_rows()

        due: list[BetRecord] = []
        for bet_id, bet_row in latest_row.items():
            if bet_row.get("status") != "open":
                continue
            expected_raw = bet_row.get("expected_settle_ts")
            if not isinstance(expected_raw, str):
                continue
            expected_ts = _parse_polymarket_ts(expected_raw)
            if expected_ts is None:
                # Defensive against schema drift — executor's Pydantic guard
                # should have prevented an unparseable timestamp at write time.
                logger.warning(
                    "sandbox_settlement_poller: unparseable expected_settle_ts "
                    "%r on bet_id=%s — skipping",
                    expected_raw, bet_id,
                )
                continue
            if expected_ts >= now:
                continue
            try:
                due.append(BetRecord.model_validate(bet_row))
            except Exception as exc:
                # A malformed row shouldn't take down the whole tick.
                logger.warning(
                    "sandbox_settlement_poller: bet_id=%s failed BetRecord "
                    "validation: %s — skipping",
                    bet_id, exc,
                )
                continue

        return due

    def _fold_latest_rows(self) -> dict[str, dict[str, object]]:
        """Single fold over ``open_bets.jsonl`` → the LATEST row per ``bet_id``.

        Shared pass-1 for both :meth:`_select_due_bets` (which then filters to
        DUE bets) and :meth:`_select_open_bets` (the V1.4b terminal-close
        fold-ALL path). Readers take the last observed row per ``bet_id`` so
        the status / expected_ts / all other fields come off that row.
        """
        latest_row: dict[str, dict[str, object]] = {}
        for row in iter_jsonl(self.state_writer.open_bets_path):
            bet_id = row.get("bet_id")
            if isinstance(bet_id, str):
                latest_row[bet_id] = row
        return latest_row

    def _select_open_bets(self) -> list[BetRecord]:
        """Read ``open_bets.jsonl`` → ALL bets whose latest status is ``"open"``.

        Like :meth:`_select_due_bets` but with NO ``expected_settle_ts`` due
        filter (V1.4b / Codex-4): the death-path terminal-close needs EVERY
        open bet, including resolved-but-not-yet-due ones the per-tick poll
        deliberately skips, so none dangles into the next incarnation.
        """
        latest_row = self._fold_latest_rows()
        open_bets: list[BetRecord] = []
        for bet_id, bet_row in latest_row.items():
            if bet_row.get("status") != "open":
                continue
            try:
                open_bets.append(BetRecord.model_validate(bet_row))
            except Exception as exc:
                logger.warning(
                    "sandbox_settlement_poller: bet_id=%s failed BetRecord "
                    "validation: %s — skipping (terminal-close)",
                    bet_id, exc,
                )
                continue
        return open_bets

    # --------------------------------------------------------------------- #
    # Per-bet resolution.
    # --------------------------------------------------------------------- #

    async def _resolve_and_settle(
        self,
        bet: BetRecord,
        *,
        now: datetime,
    ) -> SettlementOutcome | _Sentinel:
        """Resolve one due bet end-to-end.

        Returns:
            :class:`SettlementOutcome`     — settled successfully.
            :data:`_PENDING`               — gamma-api says not yet resolved.
            :data:`_FAILED`                — gamma-api retries exhausted.
        """
        self._emit_lag_alert_if_needed(bet, now=now)

        result = await self._fetch_with_retry(bet.market_id)
        if result is _FAILED:
            return _FAILED
        if result is None:
            return _PENDING
        assert isinstance(result, SettlementResult)

        # Fail-closed cost guard (Codex Phase-3 HIGH): on the LIVE/probe path a
        # RESOLVED bet MUST carry every execution-cost stamp — else its cost-NET
        # PnL would be silently booked cost-blind. Raises here (a programming
        # error: a LIVE bet without cost stamps), BEFORE _compute_pnl's lenient
        # zero-cost fallback. The legacy/replay path leaves the flag False.
        #
        # Applies to EVERY resolved outcome, INCLUDING "void": _compute_pnl charges
        # the execution cost on every outcome ("void" → -cost; the entry fee/spread
        # is paid regardless of resolution, :990-998). A cost-blind void would
        # therefore silently book 0 instead of -cost, so void is NOT exempt. The
        # invariant is "a LIVE bet is always cost-stamped at placement"
        # (LiveTickInputSource guarantees it), so this can only fire on a genuine
        # bug — never on a well-formed LIVE void. (Codex r2: kept ON for void by
        # design — pushed back with this reasoning.)
        if self.require_cost_fields:
            assert_cost_fields_present(bet)

        # Compute pnl_usd per the three locked formulas (+ the optional
        # survival-backtest profit cap; None on the live path). This single
        # value feeds BOTH the SettledBetRecord and the chain breath update,
        # so the cap holds everywhere downstream.
        pnl_usd = _compute_pnl(
            bet=bet,
            outcome=result,
            max_pnl_usd=self.max_bet_pnl_usd,
            side_correct_pricing=self.side_correct_pricing,
        )

        settled_ts = _iso_utc(result.resolution_ts)
        # Lag in hours — useful for the SettlementOutcome record and the
        # dashboard's "tail latency" badge.
        expected_ts = _parse_polymarket_ts(bet.expected_settle_ts)
        if expected_ts is None:
            # Already logged + filtered out in _select_due_bets; defensive.
            lag_hours = 0.0
        else:
            lag_hours = (now - expected_ts).total_seconds() / 3600.0

        # === Step 1: settled_bets.jsonl ====================================
        self.state_writer.append_settled_bet(
            SettledBetRecord(
                bet_id=bet.bet_id,
                market_id=bet.market_id,
                settled_ts=settled_ts,
                outcome=result.outcome,
                winning_price=result.winning_price,
                pnl_usd=pnl_usd,
            )
        )

        # === Step 2: open_bets.jsonl status-flip marker ====================
        # Append-only: the original "open" row stays where it is; we add a
        # NEW row with status="settled". Readers fold left-to-right and
        # take the last observed status per bet_id (see _select_due_bets).
        # signal_scores is carried forward verbatim so the status-flip row
        # stays a faithful copy of the open row (Task L3 — never mutate the
        # appended open row; copy its fields onto the new line).
        self.state_writer.append_open_bet(
            BetRecord(
                bet_id=bet.bet_id,
                ts=settled_ts,
                market_id=bet.market_id,
                side=bet.side,
                price=bet.price,
                size_usd=bet.size_usd,
                expected_settle_ts=bet.expected_settle_ts,
                status="settled",
                signal_scores=dict(bet.signal_scores),
                # A9: storm stamps survive the status flip verbatim.
                storm_at_bet=bet.storm_at_bet,
                edge_at_bet=bet.edge_at_bet,
                min_edge_at_bet=bet.min_edge_at_bet,
                gamma_at_bet=bet.gamma_at_bet,
                eff_min_edge_at_bet=bet.eff_min_edge_at_bet,
                # V1.4 cost stamps survive the flip too (Codex Phase-3 MED) so the
                # latest open-bet row keeps the execution-cost provenance for
                # reconciliation. None on legacy/replay rows ⇒ omitted, byte-identical.
                fill_price=bet.fill_price,
                fee_bps=bet.fee_bps,
                spread_paid_usd=bet.spread_paid_usd,
                liquidity_cap_usd=bet.liquidity_cap_usd,
            )
        )

        # === Step 3: weight_updater (called exactly once per settlement) ==
        # The signals dict is a FLAT dict[str, float] carrying the per-bet
        # feedback channel: pnl_usd + size_usd, the bet's DIRECTION
        # (+1 YES / -1 NO — mandatory for direction-aware credit assignment,
        # Task L3), and the decision-time per-engine scores flattened into
        # ``score_<engine>`` keys (read off the due open BetRecord). The
        # settlement-learning adapter unflattens them; legacy NoOp updaters
        # ignore the extra keys.
        signals: dict[str, float] = {"pnl_usd": pnl_usd, "size_usd": bet.size_usd}
        signals["bet_direction"] = 1.0 if bet.side == "YES" else -1.0
        for engine, score in bet.signal_scores.items():
            signals[f"score_{engine}"] = float(score)
        # A9: flatten the storm stamps exactly like score_<engine> so the
        # recorder can extract them into SurvivalStep (the BetRecord →
        # poller → SurvivalStep path is the ONLY durable channel — the
        # state hook is observer telemetry). Absent when storm is off.
        for stamp_key in (
            "storm_at_bet",
            "edge_at_bet",
            "min_edge_at_bet",
            "gamma_at_bet",
            "eff_min_edge_at_bet",
        ):
            stamp_val = getattr(bet, stamp_key)
            if stamp_val is not None:
                signals[stamp_key] = float(stamp_val)
        await self.weight_updater.update(
            phase=self.sandbox_phase,
            signals=signals,
            outcome=result,
        )

        # === Step 4: chain_adapter.update_breath_from_pnl =================
        chain_updated = await self._update_chain_with_retry(
            bet_id=bet.bet_id, market_id=bet.market_id, pnl_usd=pnl_usd,
        )

        return SettlementOutcome(
            bet_id=bet.bet_id,
            market_id=bet.market_id,
            outcome=result.outcome,
            pnl_usd=pnl_usd,
            lag_hours=lag_hours,
            chain_breath_updated=chain_updated,
        )

    # --------------------------------------------------------------------- #
    # Lag alerting.
    # --------------------------------------------------------------------- #

    def _emit_lag_alert_if_needed(
        self,
        bet: BetRecord,
        *,
        now: datetime,
    ) -> None:
        """Emit ``settlement_lag_warning`` / ``_critical`` per CEO MED #1.

        Thresholds are exclusive: a 26h lag emits CRITICAL only, not both.
        Critical takes precedence because the operator's filter rules
        usually treat critical as a paging event vs warning as a digest
        line.
        """
        expected_ts = _parse_polymarket_ts(bet.expected_settle_ts)
        if expected_ts is None:
            return
        lag = now - expected_ts
        lag_hours = lag.total_seconds() / 3600.0
        if lag >= LAG_CRITICAL:
            self.state_hook.emit(
                kind=STATE_HOOK_KIND_LAG_CRITICAL,
                bet_id=bet.bet_id,
                market_id=bet.market_id,
                lag_hours=lag_hours,
                expected_settle_ts=bet.expected_settle_ts,
            )
        elif lag >= LAG_WARNING:
            self.state_hook.emit(
                kind=STATE_HOOK_KIND_LAG_WARNING,
                bet_id=bet.bet_id,
                market_id=bet.market_id,
                lag_hours=lag_hours,
                expected_settle_ts=bet.expected_settle_ts,
            )

    # --------------------------------------------------------------------- #
    # Retry helpers — gamma-api 5xx + chain transient failures.
    # --------------------------------------------------------------------- #

    async def _fetch_with_retry(
        self,
        market_id: str,
    ) -> SettlementResult | None | _Sentinel:
        """Call ``settlement_client.resolve_market`` with backoff on failure.

        Returns:
            :class:`SettlementResult`     — gamma-api returned resolved data.
            ``None``                      — gamma-api responded but market
                                            is still pending.
            :data:`_FAILED`               — all retries exhausted.

        ``None`` and successful results return on the FIRST successful
        call (no retries needed). The retry loop runs on any
        :class:`Exception` — production wiring raises
        :class:`httpx.HTTPStatusError` for 5xx; the poller treats any
        exception as a transient failure and retries with the locked
        delays.
        """
        attempts = 1 + len(GAMMA_RETRY_DELAYS)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self.settlement_client.resolve_market(market_id)
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    delay = GAMMA_RETRY_DELAYS[attempt]
                    logger.warning(
                        "sandbox_settlement_poller: gamma-api fetch failed "
                        "for market_id=%s (attempt %d/%d): %s — retry in %.1fs",
                        market_id, attempt + 1, attempts, exc, delay,
                    )
                    await self.sleeper(delay)
                    continue
        # Exhausted.
        logger.error(
            "sandbox_settlement_poller: gamma-api fetch FAILED for "
            "market_id=%s after %d attempts: %s",
            market_id, attempts, last_exc,
        )
        self.state_hook.emit(
            kind=STATE_HOOK_KIND_GAMMA_FAILED,
            market_id=market_id,
            attempts=attempts,
            error=str(last_exc) if last_exc is not None else "unknown",
        )
        return _FAILED

    async def _update_chain_with_retry(
        self,
        *,
        bet_id: str,
        market_id: str,
        pnl_usd: float,
    ) -> bool:
        """Call ``chain_adapter.update_breath_from_pnl`` with backoff.

        Returns ``True`` if the chain accepted the update (with or
        without retries), ``False`` if all attempts failed. On failure
        a ``chain_breath_update_failed`` STATE_HOOK fires; the JSONL
        is already written so the bet's settled marker remains —
        future reconciliation tooling can replay from settled_bets.jsonl
        to fix the BREATH delta.
        """
        attempts = 1 + len(CHAIN_RETRY_DELAYS)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                await self.chain_adapter.update_breath_from_pnl(pnl_usd)
                return True
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    delay = CHAIN_RETRY_DELAYS[attempt]
                    logger.warning(
                        "sandbox_settlement_poller: chain breath update "
                        "failed for bet_id=%s (attempt %d/%d): %s — "
                        "retry in %.1fs",
                        bet_id, attempt + 1, attempts, exc, delay,
                    )
                    await self.sleeper(delay)
                    continue
        logger.error(
            "sandbox_settlement_poller: chain breath update FAILED for "
            "bet_id=%s after %d attempts: %s",
            bet_id, attempts, last_exc,
        )
        self.state_hook.emit(
            kind=STATE_HOOK_KIND_CHAIN_FAILED,
            bet_id=bet_id,
            market_id=market_id,
            pnl_usd=pnl_usd,
            attempts=attempts,
            error=str(last_exc) if last_exc is not None else "unknown",
        )
        return False


# --------------------------------------------------------------------------- #
# Pure helpers — unit-testable independently.
# --------------------------------------------------------------------------- #


def _compute_pnl(
    *,
    bet: BetRecord,
    outcome: SettlementResult,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
) -> float:
    """P&L per the three locked formulas (PRD §6.5 + brief acceptance criteria).

    * void   →  0
    * winner →  ``size_usd * (winning_price / bet.price - 1)``
    * loser  → ``-size_usd``

    "Winner" = the bet's ``side`` matches the projected ``outcome``
    direction. Polymarket conventions: ``outcomes[0]`` is the YES leg,
    ``outcomes[1]`` is the NO leg, so:

    * BetRecord.side == "YES" AND outcome == "yes" → winner
    * BetRecord.side == "NO"  AND outcome == "no"  → winner
    * otherwise (and outcome != "void")            → loser

    The winner formula uses :attr:`SettlementResult.winning_price`
    (typically ``1.0`` for a clean resolution) — for a clean win on a
    bet entered at probability ``p`` the realised P&L is
    ``size * (1.0 / p - 1)`` per the standard binary-market accounting.

    ``max_pnl_usd`` (optional, default ``None`` = the locked formulas above,
    byte-unchanged): clamp a WINNER's profit to this ceiling. A crude
    liquidity-realism approximation for the survival BACKTEST — the winner
    formula is unbounded at extreme-longshot entry prices (a $5 bet at
    $0.0005 "wins" $9,995 no $5-liquidity market could pay), and the same
    ``pnl_usd`` also drives the breath update, so one fluke can both fake
    the headline AND make a life undieable. Losses and voids are NEVER
    clamped: the cap is on profit only. The LIVE runtime never sets this
    (the poller field defaults ``None``).

    ``side_correct_pricing`` (optional, default ``False`` = the locked legacy
    formulas, byte-unchanged): price the taken leg at its effective cost —
    ``bet.price`` for YES, ``1 - bet.price`` for NO (realism rule #3,
    2026-06-11). ``bet.price`` is the market YES-mid for BOTH sides at order
    time, so the legacy formula paid a winning NO bet at the YES leg's odds
    (81x overpaid at yes-mid 0.10). The LIVE runtime never sets this (the
    poller field defaults ``False``).
    """
    # V1.4 cost-NET: subtract the actual execution cost (fee + spread) from EVERY
    # outcome — it was paid at entry regardless. ``execution_cost_usd`` is 0.0 when
    # the cost stamps are absent (legacy / replay), so those rows stay byte-identical
    # to the pre-V1.4 formulas. The winner/entry price uses the ACTUAL ``fill_price``
    # when stamped (else ``bet.price``, byte-identical).
    cost = execution_cost_usd(bet)
    entry = bet.fill_price if bet.fill_price is not None else bet.price
    if outcome.outcome == "void":
        return -cost
    side_is_yes = bet.side == "YES"
    outcome_is_yes = outcome.outcome == "yes"
    is_winner = side_is_yes == outcome_is_yes
    if not is_winner:
        return -bet.size_usd - cost
    # Winner — symmetric formula. entry is in [0, 1].
    # Defensive: a bet entered at price 0 is degenerate; the executor's
    # Pydantic guard allows it but we still don't want a ZeroDivisionError.
    eff = entry if (side_is_yes or not side_correct_pricing) else 1.0 - entry
    if eff <= 0.0:
        # If we entered at effective price 0 and won, payout is unbounded —
        # clip to size_usd * winning_price as the sensible "we got full
        # contract value" floor. This branch is operationally unreachable
        # in production (the sizer would NO_BET) but the chokepoint
        # belongs here.
        pnl = bet.size_usd * outcome.winning_price
    else:
        pnl = bet.size_usd * (outcome.winning_price / eff - 1.0)
    if max_pnl_usd is not None and pnl > max_pnl_usd:
        return max_pnl_usd - cost
    return pnl - cost


def _iso_utc(ts: datetime) -> str:
    """Coerce ``ts`` to a UTC ISO-8601 string with ``+00:00`` tz.

    Mirrors :func:`agent.data.polymarket_sandbox_executor._to_iso`.
    Naïve datetimes are treated as UTC.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Sentinels — distinct singleton types for "pending" vs "failed" so the
# narrow union in _resolve_and_settle's return is precise without a
# multi-arm dataclass.
# --------------------------------------------------------------------------- #


class _Sentinel:
    """Tag type for poller sentinels (``_PENDING`` / ``_FAILED``).

    Singletons rather than module-level enum so ``isinstance(x, _Sentinel)``
    can narrow the union in tick()'s loop without importing an extra
    StrEnum. The class is module-private — callers compare against the
    singleton instances exposed below.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return f"<{self._name}>"


_PENDING: Final[_Sentinel] = _Sentinel("PENDING")
_FAILED: Final[_Sentinel] = _Sentinel("FAILED")


__all__ = [
    "CHAIN_RETRY_DELAYS",
    "GAMMA_RETRY_DELAYS",
    "LAG_CRITICAL",
    "LAG_WARNING",
    "SANDBOX_PHASE_LABEL",
    "STATE_HOOK_KIND_CHAIN_FAILED",
    "STATE_HOOK_KIND_GAMMA_FAILED",
    "STATE_HOOK_KIND_LAG_CRITICAL",
    "STATE_HOOK_KIND_LAG_WARNING",
    "ChainAdapter",
    "PollTickResult",
    "SandboxSettlementPoller",
    "SettlementClient",
    "SettlementOutcome",
    "Sleeper",
    "StateHook",
    "WeightUpdater",
]
