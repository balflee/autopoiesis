"""Tests for :mod:`agent.runtime.sandbox_settlement_poller` — T-B-019.

Sixteen cases covering the locked acceptance criteria:

1.  Smart poll filters: 100 open bets / 5 due → exactly 5 gamma-api queries.
2.  Pure formula: ``_compute_pnl`` covers winner / loser / void / YES vs NO.
3.  Append-only invariant: original open_bets row preserved + settled marker
    appended; second pass excludes the bet from due set.
4.  Settled bet → settled_bets.jsonl line + parallel open_bets.jsonl marker.
5.  Winner payout uses the symmetric formula:
    ``pnl = size * (winning_price / entry_price - 1)``.
6.  Loser payout is ``-size_usd``.
7.  Void payout is 0 and outcome='void'.
8.  ``weight_updater.update`` called EXACTLY ONCE per settlement (spy).
9.  ``chain_adapter.update_breath_from_pnl`` called with the computed pnl.
10. Chain retries with exponential backoff 2/4/8s then alerts via STATE_HOOK.
11. Lag > 6h emits ``settlement_lag_warning`` STATE_HOOK.
12. Lag > 24h emits ``settlement_lag_critical`` (not both).
13. gamma-api 5xx → 1/2/4/8s backoff, then ``settlement_query_failed`` STATE_HOOK.
14. gamma-api pending (umaResolutionStatus != resolved) → no JSONL writes,
    weight_updater NOT called, no chain call.
15. VCR happy-path cassette → end-to-end settle through real polymarket_settlement.
16. VCR 5xx-then-recover cassette → 4 failures + 1 success → settled correctly.
17. VCR void cassette → outcome='void', pnl=0.

Plus a Protocol structural-typing smoke for ``WeightUpdater`` / ``ChainAdapter``
/ ``StateHook`` to keep the producer ↔ consumer contract honest.

Hermetic invariants:

* Every test uses a ``tmp_path``-rooted :class:`SandboxStateWriter`.
* The default :class:`Sleeper` is replaced by an instrumented fake that records
  call durations + returns immediately. Tests assert the backoff sequence.
* VCR ``record_mode='none'`` blocks every non-cassette request — a cassette
  miss is a test-suite bug, not a live network call.
* No real :class:`asyncio.sleep` ever runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import vcr

from agent.data.polymarket_settlement import (
    SettlementResult,
    _HttpClient,
    resolve_market,
)
from agent.data.sandbox_state import (
    BetRecord,
    SandboxStateWriter,
    iter_jsonl,
)
from agent.runtime.sandbox_settlement_poller import (
    CHAIN_RETRY_DELAYS,
    GAMMA_RETRY_DELAYS,
    SANDBOX_PHASE_LABEL,
    STATE_HOOK_KIND_CHAIN_FAILED,
    STATE_HOOK_KIND_GAMMA_FAILED,
    STATE_HOOK_KIND_LAG_CRITICAL,
    STATE_HOOK_KIND_LAG_WARNING,
    ChainAdapter,
    PollTickResult,
    SandboxSettlementPoller,
    StateHook,
    WeightUpdater,
    _compute_pnl,
)

# --------------------------------------------------------------------------- #
# Cassette wiring — replay only, zero live network.
# --------------------------------------------------------------------------- #


CASSETTE_DIR = Path(__file__).parent / "vcr" / "sandbox_settlement"

_replay_vcr = vcr.VCR(
    serializer="yaml",
    record_mode="none",
    cassette_library_dir=str(CASSETTE_DIR),
    decode_compressed_response=True,
    match_on=("method", "scheme", "host", "port", "path", "query"),
    # The 5xx-then-recover cassette has 5 interactions for the SAME URL;
    # vcrpy needs ``allow_playback_repeats=False`` (the default) AND the
    # ordering to walk through them in sequence. With that default each
    # GET pops the next matching interaction.
)


# --------------------------------------------------------------------------- #
# Test doubles — small enough to inline, structural Protocols.
# --------------------------------------------------------------------------- #


class FakeSettlementClient:
    """Scripted :class:`SettlementClient` — returns / raises per market_id.

    The ``script`` maps ``market_id`` → list of responses, popped in order.
    Each response is either:

    * a :class:`SettlementResult`  — returned verbatim.
    * ``None``                     — returned (means "not yet resolved").
    * an :class:`Exception`        — raised (simulates transient failure).

    Records every call so tests can assert the per-bet query count
    (smart-poll guarantee).
    """

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self._script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        self.calls.append(market_id)
        if market_id not in self._script or not self._script[market_id]:
            raise AssertionError(
                f"FakeSettlementClient: no scripted response for {market_id!r}"
            )
        nxt = self._script[market_id].pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        # Type narrowed by elimination — either SettlementResult or None.
        return cast(SettlementResult | None, nxt)


class FakeWeightUpdater:
    """Spy :class:`WeightUpdater` — captures every call's kwargs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None:
        self.calls.append(
            {"phase": phase, "signals": dict(signals), "outcome": outcome}
        )


class FakeChainAdapter:
    """Spy :class:`ChainAdapter` — by default succeeds; can be told to fail N times."""

    def __init__(self, *, fail_first_n: int = 0) -> None:
        self.calls: list[float] = []
        self._fail_remaining = fail_first_n

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.calls.append(pnl_usd)
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError(f"chain RPC failed (simulated; remaining={self._fail_remaining})")


class FakeStateHook:
    """Records every ``emit(kind=..., **payload)`` call."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


class FakeSleeper:
    """Records sleep durations + returns instantly. No real wall-clock wait."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FixedClock:
    """Returns the same ``datetime`` on every ``.now()`` call."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def writer(tmp_path: Path) -> SandboxStateWriter:
    return SandboxStateWriter(root=tmp_path / "sandbox")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)


def _seed_bet(
    writer: SandboxStateWriter,
    *,
    bet_id: str = "bet-001",
    market_id: str = "m-001",
    side: str = "YES",
    price: float = 0.4,
    size_usd: float = 10.0,
    expected_settle_ts: str = "2026-05-26T18:00:00+00:00",
    status: str = "open",
    signal_scores: dict[str, float] | None = None,
    fill_price: float | None = None,
    fee_bps: float | None = None,
    spread_paid_usd: float | None = None,
    liquidity_cap_usd: float | None = None,
) -> BetRecord:
    bet = BetRecord(
        bet_id=bet_id,
        ts="2026-05-26T16:00:00+00:00",
        market_id=market_id,
        side=cast(Any, side),
        price=price,
        size_usd=size_usd,
        expected_settle_ts=expected_settle_ts,
        status=cast(Any, status),
        signal_scores=signal_scores or {},
        fill_price=fill_price,
        fee_bps=fee_bps,
        spread_paid_usd=spread_paid_usd,
        liquidity_cap_usd=liquidity_cap_usd,
    )
    writer.append_open_bet(bet)
    return bet


def _resolved_result(
    *,
    market_id: str = "m-001",
    outcome: str = "yes",
    winning_price: float = 1.0,
    resolution_ts: datetime | None = None,
    end_date: datetime | None = None,
) -> SettlementResult:
    return SettlementResult(
        market_id=market_id,
        resolved=True,
        outcome=cast(Any, outcome),
        winning_price=winning_price,
        resolution_ts=resolution_ts or datetime(2026, 5, 25, 23, 57, 11, tzinfo=UTC),
        end_date=end_date or datetime(2026, 5, 31, 9, 0, 0, tzinfo=UTC),
    )


def _build_poller(
    writer: SandboxStateWriter,
    *,
    settlement_client: Any,
    weight_updater: Any | None = None,
    chain_adapter: Any | None = None,
    state_hook: Any | None = None,
    sleeper: Any | None = None,
    clock_now: datetime | None = None,
    max_bet_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    require_cost_fields: bool = False,
) -> tuple[SandboxSettlementPoller, FakeWeightUpdater, FakeChainAdapter, FakeStateHook, FakeSleeper]:
    wu = weight_updater or FakeWeightUpdater()
    ca = chain_adapter or FakeChainAdapter()
    sh = state_hook or FakeStateHook()
    sl = sleeper or FakeSleeper()
    poller = SandboxSettlementPoller(
        state_writer=writer,
        settlement_client=settlement_client,
        weight_updater=wu,
        chain_adapter=ca,
        state_hook=sh,
        clock=FixedClock(clock_now or datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)),
        sleeper=sl,
        max_bet_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=side_correct_pricing,
        require_cost_fields=require_cost_fields,
    )
    return poller, wu, ca, sh, sl


def _run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. Smart poll — 100 bets / 5 due → exactly 5 queries.
# --------------------------------------------------------------------------- #


def test_smart_poll_queries_only_due_bets(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """100 open bets, 5 with ``expected_settle_ts < now`` → 5 gamma queries."""
    # 95 future bets (not due).
    future_ts = "2026-05-27T20:00:00+00:00"
    for i in range(95):
        _seed_bet(
            writer,
            bet_id=f"future-{i:03d}",
            market_id=f"m-future-{i:03d}",
            expected_settle_ts=future_ts,
        )
    # 5 due bets.
    past_ts = "2026-05-26T18:00:00+00:00"
    due_market_ids: list[str] = []
    for i in range(5):
        mid = f"m-due-{i:02d}"
        due_market_ids.append(mid)
        _seed_bet(writer, bet_id=f"due-{i:02d}", market_id=mid, expected_settle_ts=past_ts)

    script: dict[str, list[Any]] = {mid: [None] for mid in due_market_ids}
    fake_client = FakeSettlementClient(script)
    poller, _, _, _, _ = _build_poller(writer, settlement_client=fake_client, clock_now=now)

    result = _run(poller.tick())

    assert sorted(fake_client.calls) == sorted(due_market_ids)
    assert result.queried_count == 5
    assert result.pending_count == 5  # all scripted as None
    assert result.settled_count == 0


# --------------------------------------------------------------------------- #
# 2. Pure formula chokepoint — winner / loser / void / YES vs NO.
# --------------------------------------------------------------------------- #


def test_compute_pnl_winner_yes_side() -> None:
    """YES bet at 0.4 → outcome=yes, winning_price=1.0 → pnl = 10 * (1/0.4 - 1) = 15.0"""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.4, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="yes", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(15.0)


def test_compute_pnl_winner_no_side() -> None:
    """NO bet at 0.6 → outcome=no → pnl = 10 * (1/0.6 - 1) ≈ 6.667"""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="NO", price=0.6, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="no", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(10.0 * (1 / 0.6 - 1))


def test_compute_pnl_loser_yes_side_outcome_no() -> None:
    """YES bet, outcome=no → pnl = -size = -10.0"""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.4, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="no", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(-10.0)


def test_compute_pnl_loser_no_side_outcome_yes() -> None:
    """NO bet, outcome=yes → pnl = -size = -10.0"""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="NO", price=0.6, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="yes", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(-10.0)


def test_compute_pnl_void_market_zero() -> None:
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.5, size_usd=12.34,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="void", winning_price=0.5)
    assert _compute_pnl(bet=bet, outcome=out) == 0.0


# --------------------------------------------------------------------------- #
# 2b. Optional per-bet PROFIT cap (survival-backtest realism rule).
# --------------------------------------------------------------------------- #


def test_compute_pnl_cap_clamps_extreme_longshot_win() -> None:
    """A $5 win at price 0.0005 is $9,995 uncapped → clamps to exactly the cap."""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="NO", price=0.0005, size_usd=5.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="no", winning_price=1.0)
    # Sanity: uncapped is the unbounded lottery payout.
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(9995.0)
    assert _compute_pnl(bet=bet, outcome=out, max_pnl_usd=100.0) == 100.0


def test_compute_pnl_cap_default_none_is_byte_identical() -> None:
    """``max_pnl_usd=None`` (default) → the locked formulas, unchanged."""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.4, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="yes", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(15.0)
    assert _compute_pnl(bet=bet, outcome=out, max_pnl_usd=None) == pytest.approx(15.0)


def test_compute_pnl_cap_never_clamps_a_loss() -> None:
    """Cap is on PROFIT only: a loss stays -size even under a tiny cap."""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.4, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="no", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out, max_pnl_usd=0.01) == pytest.approx(-10.0)


def test_compute_pnl_cap_clamps_degenerate_price_branch() -> None:
    """The defensive price<=0 winner branch is also capped."""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.0, size_usd=500.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="yes", winning_price=1.0)
    # Uncapped degenerate clip = size * winning_price = 500.
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(500.0)
    assert _compute_pnl(bet=bet, outcome=out, max_pnl_usd=100.0) == 100.0


def test_poller_cap_threads_to_settled_record_and_breath(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """A poller built with ``max_bet_pnl_usd`` writes a CLAMPED
    ``SettledBetRecord.pnl_usd`` AND the chain breath update receives the SAME
    clamped value — the one chokepoint covers both downstreams."""
    _seed_bet(
        writer,
        market_id="m-001",
        side="NO",
        price=0.0005,
        size_usd=5.0,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
    )
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="no", winning_price=1.0)]
    }
    poller, _, ca, _, _ = _build_poller(
        writer,
        settlement_client=FakeSettlementClient(script),
        clock_now=now,
        max_bet_pnl_usd=100.0,
    )
    _run(poller.tick())

    settled = iter_jsonl(writer.settled_bets_path)
    assert len(settled) == 1
    assert settled[0]["pnl_usd"] == pytest.approx(100.0)
    # Breath update saw the SAME clamped value (not the $9,995 lottery).
    assert ca.calls == [pytest.approx(100.0)]


# --------------------------------------------------------------------------- #
# 2c. Optional side-correct pricing (realism rule #3).
# --------------------------------------------------------------------------- #


def test_compute_pnl_side_correct_no_winner_paid_at_complement() -> None:
    """NO win at yes-mid 0.10: legacy pays $45 (YES odds); corrected pays the
    NO leg's odds — 5*(1/0.9-1) ≈ $0.556."""
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="NO", price=0.10, size_usd=5.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="no", winning_price=1.0)
    assert _compute_pnl(
        bet=bet, outcome=out, side_correct_pricing=True
    ) == pytest.approx(5.0 * (1.0 / 0.90 - 1.0))


def test_compute_pnl_side_correct_default_off_is_legacy() -> None:
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="NO", price=0.10, size_usd=5.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="no", winning_price=1.0)
    assert _compute_pnl(bet=bet, outcome=out) == pytest.approx(45.0)


def test_compute_pnl_side_correct_loser_unchanged() -> None:
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="NO", price=0.10, size_usd=5.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="yes", winning_price=1.0)
    assert _compute_pnl(
        bet=bet, outcome=out, side_correct_pricing=True
    ) == pytest.approx(-5.0)


def test_compute_pnl_side_correct_yes_winner_unchanged() -> None:
    bet = BetRecord(
        bet_id="b", ts="t", market_id="m", side="YES", price=0.4, size_usd=10.0,
        expected_settle_ts="t", status="open",
    )
    out = _resolved_result(outcome="yes", winning_price=1.0)
    assert _compute_pnl(
        bet=bet, outcome=out, side_correct_pricing=True
    ) == pytest.approx(15.0)


def test_poller_side_correct_threads_to_settled_record_and_breath(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """A poller built with ``side_correct_pricing=True`` writes the CORRECTED
    ``SettledBetRecord.pnl_usd`` AND the chain breath update receives the SAME
    corrected value (clone of the max_bet_pnl_usd threading test)."""
    _seed_bet(
        writer,
        market_id="m-001",
        side="NO",
        price=0.10,
        size_usd=5.0,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
    )
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="no", winning_price=1.0)]
    }
    poller, _, ca, _, _ = _build_poller(
        writer,
        settlement_client=FakeSettlementClient(script),
        clock_now=now,
        side_correct_pricing=True,
    )
    _run(poller.tick())

    settled = iter_jsonl(writer.settled_bets_path)
    assert len(settled) == 1
    expected = 5.0 * (1.0 / 0.90 - 1.0)
    assert settled[0]["pnl_usd"] == pytest.approx(expected)
    assert ca.calls == [pytest.approx(expected)]


# --------------------------------------------------------------------------- #
# Codex Phase-3 — fail-closed cost guard (HIGH) + cost-stamp flip copy (MED).
# --------------------------------------------------------------------------- #


def test_require_cost_fields_raises_on_resolved_cost_blind_bet(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """LIVE/probe path (``require_cost_fields=True``): a RESOLVED bet missing its
    execution-cost stamps RAISES at settlement (fail-closed) — never booked
    cost-blind. The legacy/replay path (flag False) tolerates it."""
    _seed_bet(writer, market_id="m-001", expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {"m-001": [_resolved_result(market_id="m-001")]}
    poller, _, _, _, _ = _build_poller(
        writer,
        settlement_client=FakeSettlementClient(script),
        clock_now=now,
        require_cost_fields=True,
    )
    with pytest.raises(ValueError, match="missing execution-cost stamps"):
        _run(poller.tick())
    # nothing was booked (fail-closed before _compute_pnl / settled write).
    assert iter_jsonl(writer.settled_bets_path) == []


def test_require_cost_fields_settles_when_stamps_present(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """With every cost stamp present, the LIVE guard passes and the bet settles."""
    _seed_bet(
        writer, market_id="m-001", price=0.5, size_usd=10.0,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
        fill_price=0.5, fee_bps=0.0, spread_paid_usd=0.0, liquidity_cap_usd=20.0,
    )
    script: dict[str, list[Any]] = {"m-001": [_resolved_result(market_id="m-001")]}
    poller, _, _, _, _ = _build_poller(
        writer,
        settlement_client=FakeSettlementClient(script),
        clock_now=now,
        require_cost_fields=True,
    )
    _run(poller.tick())
    settled = iter_jsonl(writer.settled_bets_path)
    assert len(settled) == 1 and settled[0]["pnl_usd"] == pytest.approx(10.0)


def test_settled_flip_marker_carries_cost_stamps(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """Codex Phase-3 MED: the status-flip open-bet marker preserves the V1.4 cost
    stamps so the latest open-bet row keeps execution-cost provenance."""
    _seed_bet(
        writer, market_id="m-001", price=0.5, size_usd=10.0,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
        fill_price=0.52, fee_bps=200.0, spread_paid_usd=0.1, liquidity_cap_usd=20.0,
    )
    script: dict[str, list[Any]] = {"m-001": [_resolved_result(market_id="m-001")]}
    poller, _, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    # The latest open-bet row (the status="settled" flip marker) keeps the stamps.
    rows = [r for r in iter_jsonl(writer.open_bets_path) if r["status"] == "settled"]
    assert len(rows) == 1
    flip = rows[0]
    assert flip["fill_price"] == pytest.approx(0.52)
    assert flip["fee_bps"] == pytest.approx(200.0)
    assert flip["spread_paid_usd"] == pytest.approx(0.1)
    assert flip["liquidity_cap_usd"] == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# 3. Append-only invariant + settled marker.
# --------------------------------------------------------------------------- #


def test_append_only_invariant_settled_marker(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """After settlement, open_bets.jsonl has TWO lines: the original ``open``
    line AND a new ``settled`` line — NO in-place edit."""
    _seed_bet(writer, expected_settle_ts="2026-05-26T18:00:00+00:00")

    script: dict[str, list[Any]] = {"m-001": [_resolved_result(market_id="m-001", outcome="yes")]}
    poller, _, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())

    rows = iter_jsonl(writer.open_bets_path)
    assert len(rows) == 2
    assert rows[0]["status"] == "open"
    assert rows[1]["status"] == "settled"
    # Same bet_id — not a new bet.
    assert rows[0]["bet_id"] == rows[1]["bet_id"]
    # Settled record also written to its own stream.
    settled = iter_jsonl(writer.settled_bets_path)
    assert len(settled) == 1
    assert settled[0]["bet_id"] == "bet-001"
    assert settled[0]["status"] == "settled"


def test_settled_bet_not_re_polled_on_next_tick(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """After a bet settles, the next tick MUST NOT include it in the due set."""
    _seed_bet(writer, expected_settle_ts="2026-05-26T18:00:00+00:00")

    script: dict[str, list[Any]] = {"m-001": [_resolved_result(market_id="m-001", outcome="yes")]}
    fake_client = FakeSettlementClient(script)
    poller, _, _, _, _ = _build_poller(writer, settlement_client=fake_client, clock_now=now)
    _run(poller.tick())
    assert len(fake_client.calls) == 1

    # Second tick — no new scripted responses; the client would raise on
    # any unscripted call. If the bet is correctly excluded the tick is
    # a no-op.
    result = _run(poller.tick())
    assert result.queried_count == 0
    assert len(fake_client.calls) == 1  # unchanged


# --------------------------------------------------------------------------- #
# 4–7. Outcome-shape acceptance criteria.
# --------------------------------------------------------------------------- #


def test_winner_writes_correct_pnl_to_settled_record(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """YES bet at 0.4, size 10 → settled record pnl_usd = 15.0."""
    _seed_bet(writer, side="YES", price=0.4, size_usd=10.0,
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes", winning_price=1.0)],
    }
    poller, _, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    settled = iter_jsonl(writer.settled_bets_path)
    assert len(settled) == 1
    assert settled[0]["pnl_usd"] == pytest.approx(15.0)
    assert settled[0]["outcome"] == "yes"
    assert settled[0]["winning_price"] == pytest.approx(1.0)


def test_loser_writes_negative_pnl(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    _seed_bet(writer, side="YES", price=0.4, size_usd=10.0,
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="no", winning_price=1.0)],
    }
    poller, _, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    settled = iter_jsonl(writer.settled_bets_path)
    assert settled[0]["pnl_usd"] == pytest.approx(-10.0)
    assert settled[0]["outcome"] == "no"


def test_void_writes_zero_pnl(writer: SandboxStateWriter, now: datetime) -> None:
    _seed_bet(writer, expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="void", winning_price=0.5)],
    }
    poller, _, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    settled = iter_jsonl(writer.settled_bets_path)
    assert settled[0]["pnl_usd"] == 0.0
    assert settled[0]["outcome"] == "void"


# --------------------------------------------------------------------------- #
# 8. weight_updater spy — called EXACTLY ONCE per settlement.
# --------------------------------------------------------------------------- #


def test_weight_updater_called_exactly_once_per_settlement(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    _seed_bet(writer, bet_id="b1", market_id="m-001",
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    _seed_bet(writer, bet_id="b2", market_id="m-002",
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
        "m-002": [_resolved_result(market_id="m-002", outcome="no")],
    }
    poller, wu, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    assert len(wu.calls) == 2
    # Phase label is the locked sandbox-extended label.
    assert all(c["phase"] == SANDBOX_PHASE_LABEL for c in wu.calls)
    # Signals carry pnl_usd + size_usd + bet_direction (Task L3 — direction
    # is +1 YES / -1 NO). No score_<engine> keys here because these seeded
    # bets carry no signal_scores. Still no outcome* / settled_at* — those
    # flow via the explicit ``outcome=`` kwarg, NOT the signals dict, so no
    # lookahead-auditor concern.
    for c in wu.calls:
        assert set(c["signals"].keys()) == {"pnl_usd", "size_usd", "bet_direction"}
        assert isinstance(c["outcome"], SettlementResult)
    # b1 is a YES bet (+1), b2 is a YES bet (+1) by the _seed_bet default.
    assert all(c["signals"]["bet_direction"] == 1.0 for c in wu.calls)


def test_signal_scores_flattened_into_signals_with_no_direction(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """A NO bet with signal_scores flattens to score_<engine> keys and
    bet_direction=-1.0 (Task L3 credit-assignment channel)."""
    _seed_bet(
        writer,
        side="NO",
        expected_settle_ts="2026-05-26T18:00:00+00:00",
        signal_scores={"tennis_technical": -0.7, "market_momentum": 0.1},
    )
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="no")],
    }
    poller, wu, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    assert len(wu.calls) == 1
    sig = wu.calls[0]["signals"]
    assert sig["bet_direction"] == -1.0
    assert sig["score_tennis_technical"] == pytest.approx(-0.7)
    assert sig["score_market_momentum"] == pytest.approx(0.1)
    assert "pnl_usd" in sig and "size_usd" in sig


def test_signal_scores_persisted_on_status_flip_record(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """The settled status-flip BetRecord copies the open row's signal_scores
    verbatim (append-only — the open row is never mutated)."""
    scores = {"tennis_technical": 0.5, "surface_advantage": -0.3}
    _seed_bet(
        writer,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
        signal_scores=scores,
    )
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
    }
    poller, _, _, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    rows = iter_jsonl(writer.open_bets_path)
    assert len(rows) == 2
    assert rows[0]["status"] == "open"
    assert rows[0]["signal_scores"] == scores
    assert rows[1]["status"] == "settled"
    assert rows[1]["signal_scores"] == scores


# --------------------------------------------------------------------------- #
# 9. chain_adapter receives pnl.
# --------------------------------------------------------------------------- #


def test_chain_breath_update_called_with_computed_pnl(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    _seed_bet(writer, side="YES", price=0.5, size_usd=20.0,
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes", winning_price=1.0)],
    }
    poller, _, ca, _, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    # pnl = 20 * (1.0 / 0.5 - 1) = 20.0
    assert ca.calls == [pytest.approx(20.0)]


# --------------------------------------------------------------------------- #
# 10. Chain retries on failure → exponential backoff 2/4/8s → alert.
# --------------------------------------------------------------------------- #


def test_chain_breath_update_retries_then_alerts(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """Chain fails on all 4 attempts → STATE_HOOK ``chain_breath_update_failed``."""
    _seed_bet(writer, side="YES", price=0.4, size_usd=10.0,
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
    }
    chain = FakeChainAdapter(fail_first_n=99)  # always fail
    poller, _, _ca, sh, sleeper = _build_poller(
        writer, settlement_client=FakeSettlementClient(script),
        chain_adapter=chain, clock_now=now,
    )
    result = _run(poller.tick())

    # 4 attempts total (1 + 3 retries).
    assert len(chain.calls) == 4
    # Sleeper called with 2.0, 4.0, 8.0 between attempts.
    assert sleeper.calls == list(CHAIN_RETRY_DELAYS)
    # STATE_HOOK alert emitted with the failed kind.
    assert STATE_HOOK_KIND_CHAIN_FAILED in sh.kinds()
    failed = next(e for e in sh.events if e["kind"] == STATE_HOOK_KIND_CHAIN_FAILED)
    assert failed["bet_id"] == "bet-001"
    assert failed["attempts"] == 1 + len(CHAIN_RETRY_DELAYS)
    # The settlement still recorded (JSONL is the source of truth) but
    # chain_breath_updated=False to mark the gap for reconciliation.
    assert len(result.settlements) == 1
    assert result.settlements[0].chain_breath_updated is False


def test_chain_breath_update_succeeds_on_retry(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """Chain fails twice, succeeds on third → no STATE_HOOK alert; 2 sleeps."""
    _seed_bet(writer, side="YES", price=0.4, size_usd=10.0,
              expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
    }
    chain = FakeChainAdapter(fail_first_n=2)  # 1st + 2nd fail, 3rd succeeds
    poller, _, _ca, sh, sleeper = _build_poller(
        writer, settlement_client=FakeSettlementClient(script),
        chain_adapter=chain, clock_now=now,
    )
    result = _run(poller.tick())

    assert len(chain.calls) == 3
    assert sleeper.calls == [CHAIN_RETRY_DELAYS[0], CHAIN_RETRY_DELAYS[1]]
    assert STATE_HOOK_KIND_CHAIN_FAILED not in sh.kinds()
    assert result.settlements[0].chain_breath_updated is True


# --------------------------------------------------------------------------- #
# 11–12. Lag alerts at 6h / 24h.
# --------------------------------------------------------------------------- #


def test_lag_warning_at_6h(writer: SandboxStateWriter) -> None:
    """Bet expected to settle 7h ago → ``settlement_lag_warning`` emitted."""
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    seven_hours_ago = now - timedelta(hours=7)
    _seed_bet(writer, expected_settle_ts=seven_hours_ago.isoformat())
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
    }
    poller, _, _, sh, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    assert STATE_HOOK_KIND_LAG_WARNING in sh.kinds()
    warn = next(e for e in sh.events if e["kind"] == STATE_HOOK_KIND_LAG_WARNING)
    assert warn["bet_id"] == "bet-001"
    assert warn["market_id"] == "m-001"
    # And NOT critical.
    assert STATE_HOOK_KIND_LAG_CRITICAL not in sh.kinds()


def test_lag_critical_at_24h(writer: SandboxStateWriter) -> None:
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    yesterday = now - timedelta(hours=26)
    _seed_bet(writer, expected_settle_ts=yesterday.isoformat())
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
    }
    poller, _, _, sh, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    assert STATE_HOOK_KIND_LAG_CRITICAL in sh.kinds()
    # Exclusive — warning is NOT also fired.
    assert STATE_HOOK_KIND_LAG_WARNING not in sh.kinds()


def test_no_lag_alert_under_6h(writer: SandboxStateWriter) -> None:
    """A bet that's only 1h late emits NO lag STATE_HOOK."""
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    one_hour_ago = now - timedelta(hours=1)
    _seed_bet(writer, expected_settle_ts=one_hour_ago.isoformat())
    script: dict[str, list[Any]] = {
        "m-001": [_resolved_result(market_id="m-001", outcome="yes")],
    }
    poller, _, _, sh, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    _run(poller.tick())
    assert STATE_HOOK_KIND_LAG_WARNING not in sh.kinds()
    assert STATE_HOOK_KIND_LAG_CRITICAL not in sh.kinds()


# --------------------------------------------------------------------------- #
# 13. gamma 5xx retry then alert.
# --------------------------------------------------------------------------- #


def test_gamma_5xx_retries_with_backoff_then_alerts(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """All 5 attempts raise → STATE_HOOK ``settlement_query_failed``."""
    _seed_bet(writer, expected_settle_ts="2026-05-26T18:00:00+00:00")
    err = httpx.HTTPStatusError(
        "500 Server Error", request=httpx.Request("GET", "https://x"),
        response=httpx.Response(500),
    )
    script: dict[str, list[Any]] = {"m-001": [err, err, err, err, err]}
    poller, wu, ca, sh, sleeper = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    result = _run(poller.tick())

    assert result.failed_count == 1
    assert result.settled_count == 0
    # 5 attempts; 4 sleeps between them (1, 2, 4, 8s).
    assert sleeper.calls == list(GAMMA_RETRY_DELAYS)
    # JSONL not touched.
    assert iter_jsonl(writer.settled_bets_path) == []
    # weight_updater / chain NOT called.
    assert wu.calls == []
    assert ca.calls == []
    # STATE_HOOK fired.
    assert STATE_HOOK_KIND_GAMMA_FAILED in sh.kinds()
    failed = next(e for e in sh.events if e["kind"] == STATE_HOOK_KIND_GAMMA_FAILED)
    assert failed["market_id"] == "m-001"
    assert failed["attempts"] == 1 + len(GAMMA_RETRY_DELAYS)


def test_gamma_5xx_recovers_on_retry(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """3 transient failures then success → no STATE_HOOK alert; settled."""
    _seed_bet(writer, expected_settle_ts="2026-05-26T18:00:00+00:00")
    err = httpx.HTTPStatusError(
        "503", request=httpx.Request("GET", "https://x"),
        response=httpx.Response(503),
    )
    script: dict[str, list[Any]] = {
        "m-001": [err, err, err, _resolved_result(market_id="m-001", outcome="yes")],
    }
    poller, _, _, sh, sleeper = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    result = _run(poller.tick())

    assert result.settled_count == 1
    assert sleeper.calls == list(GAMMA_RETRY_DELAYS[:3])
    assert STATE_HOOK_KIND_GAMMA_FAILED not in sh.kinds()


# --------------------------------------------------------------------------- #
# 14. Pending market — no JSONL writes, no weight/chain calls.
# --------------------------------------------------------------------------- #


def test_pending_market_skipped_no_side_effects(
    writer: SandboxStateWriter, now: datetime,
) -> None:
    """gamma-api returns None (umaResolutionStatus != resolved) → no writes."""
    _seed_bet(writer, expected_settle_ts="2026-05-26T18:00:00+00:00")
    script: dict[str, list[Any]] = {"m-001": [None]}
    poller, wu, ca, _sh, _ = _build_poller(
        writer, settlement_client=FakeSettlementClient(script), clock_now=now,
    )
    result = _run(poller.tick())

    assert result.pending_count == 1
    assert result.settled_count == 0
    assert iter_jsonl(writer.settled_bets_path) == []
    # open_bets unchanged — only the seeded line is present.
    assert len(iter_jsonl(writer.open_bets_path)) == 1
    assert wu.calls == []
    assert ca.calls == []


# --------------------------------------------------------------------------- #
# 15–17. VCR cassette integration tests.
# --------------------------------------------------------------------------- #


class HttpSettlementClient:
    """Production-shaped :class:`SettlementClient` backed by the real
    :func:`agent.data.polymarket_settlement.resolve_market` over an
    injected httpx.AsyncClient.

    Used in the VCR cassette tests so the recorded gamma-api responses
    flow through the real parser — drift in the cassette schema gets
    caught here.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        return await resolve_market(
            market_id, client=cast(_HttpClient, self._http),
        )


def _run_vcr_tick(
    *,
    writer: SandboxStateWriter,
    bet: BetRecord,
    cassette: str,
    clock_now: datetime,
    weight_updater: Any | None = None,
    chain_adapter: Any | None = None,
) -> tuple[PollTickResult, FakeWeightUpdater, FakeChainAdapter, FakeStateHook]:
    """Drive ONE tick using a VCR-replayed gamma-api response."""
    wu = weight_updater or FakeWeightUpdater()
    ca = chain_adapter or FakeChainAdapter()
    sh = FakeStateHook()

    async def _go() -> PollTickResult:
        async with httpx.AsyncClient(timeout=10.0) as http:
            client = HttpSettlementClient(http)
            poller = SandboxSettlementPoller(
                state_writer=writer,
                settlement_client=client,
                weight_updater=wu,
                chain_adapter=ca,
                state_hook=sh,
                clock=FixedClock(clock_now),
                sleeper=FakeSleeper(),
            )
            return await poller.tick()

    with _replay_vcr.use_cassette(cassette):
        result = asyncio.run(_go())
    return result, wu, ca, sh


def test_vcr_happy_path_winner_end_to_end(writer: SandboxStateWriter) -> None:
    """Real gamma-api response (Hugo Gaston cassette) flows through the
    poller and produces a winner SettledBetRecord."""
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    bet = _seed_bet(
        writer, bet_id="vcr-winner", market_id="2328096", side="YES",
        price=0.4, size_usd=10.0,
        # 1h before now → due but no lag alert (under the 6h threshold).
        expected_settle_ts="2026-05-26T19:00:00+00:00",
    )
    result, wu, ca, sh = _run_vcr_tick(
        writer=writer, bet=bet, cassette="happy_path_winner.yaml", clock_now=now,
    )
    assert result.settled_count == 1
    settled = iter_jsonl(writer.settled_bets_path)
    assert len(settled) == 1
    assert settled[0]["bet_id"] == "vcr-winner"
    assert settled[0]["outcome"] == "yes"
    # winning_price=1.0, entered at 0.4, size=10 → pnl=15.0
    assert settled[0]["pnl_usd"] == pytest.approx(15.0)
    # weight_updater + chain both got the signal.
    assert len(wu.calls) == 1
    assert ca.calls == [pytest.approx(15.0)]
    # No lag alert (the gameStart→close was within 6h).
    assert STATE_HOOK_KIND_LAG_WARNING not in sh.kinds()


def test_vcr_void_cassette_pnl_zero(writer: SandboxStateWriter) -> None:
    """Void market cassette → outcome='void', pnl_usd=0."""
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    bet = _seed_bet(
        writer, bet_id="vcr-void", market_id="9990001", side="YES",
        price=0.55, size_usd=12.5,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
    )
    result, _, ca, _ = _run_vcr_tick(
        writer=writer, bet=bet, cassette="void_market.yaml", clock_now=now,
    )
    assert result.settled_count == 1
    settled = iter_jsonl(writer.settled_bets_path)
    assert settled[0]["outcome"] == "void"
    assert settled[0]["pnl_usd"] == 0.0
    assert ca.calls == [0.0]


def test_vcr_pending_cassette_no_settlement(writer: SandboxStateWriter) -> None:
    """umaResolutionStatus='proposed' → no settlement, no JSONL writes."""
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    bet = _seed_bet(
        writer, bet_id="vcr-pending", market_id="9990002", side="YES",
        price=0.5, size_usd=5.0,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
    )
    result, wu, ca, _ = _run_vcr_tick(
        writer=writer, bet=bet, cassette="pending_market.yaml", clock_now=now,
    )
    assert result.pending_count == 1
    assert result.settled_count == 0
    assert iter_jsonl(writer.settled_bets_path) == []
    assert wu.calls == []
    assert ca.calls == []


def test_vcr_5xx_then_recover_cassette(writer: SandboxStateWriter) -> None:
    """4 sequential 5xx responses followed by 1 200 → settled on the 5th attempt."""
    now = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)
    bet = _seed_bet(
        writer, bet_id="vcr-5xx", market_id="9990003", side="YES",
        price=0.4, size_usd=10.0,
        expected_settle_ts="2026-05-26T18:00:00+00:00",
    )
    # NOTE: httpx.AsyncClient does NOT auto-raise on 4xx/5xx; resolve_market
    # calls resp.raise_for_status() which raises HTTPStatusError. The poller
    # retries on any Exception → 4 retries → 5th attempt is the 200.
    result, _, _, sh = _run_vcr_tick(
        writer=writer, bet=bet, cassette="gamma_5xx_then_recover.yaml", clock_now=now,
    )
    assert result.settled_count == 1
    assert STATE_HOOK_KIND_GAMMA_FAILED not in sh.kinds()


# --------------------------------------------------------------------------- #
# Protocol compatibility smoke — structural typecheck guard.
# --------------------------------------------------------------------------- #


def test_fakes_satisfy_protocols() -> None:
    """Asserts the test fakes are structurally compatible with the Protocols.

    isinstance() against a Protocol with non-method members isn't supported
    in 3.11 without ``@runtime_checkable``; the static check below is what
    mypy sees in CI. The presence of this test ensures the import succeeds.
    """
    wu: WeightUpdater = FakeWeightUpdater()
    ca: ChainAdapter = FakeChainAdapter()
    sh: StateHook = FakeStateHook()
    # All three Protocols have the methods we call below.
    assert callable(wu.update)
    assert callable(ca.update_breath_from_pnl)
    assert callable(sh.emit)
