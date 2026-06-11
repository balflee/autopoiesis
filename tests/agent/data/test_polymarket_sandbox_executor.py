"""Tests for :mod:`agent.data.polymarket_sandbox_executor` — record-only executor.

Acceptance contract (T-B-018):

* ZERO network calls on any code path. Asserted via a
  ``socket.create_connection`` tripwire that raises if invoked.
* Synthetic ``order_id`` (UUID4) per accepted order; unique across
  1000 calls.
* Every accepted order → exactly ONE JSONL line in ``open_bets.jsonl``.
* ``expected_settle_ts`` = ``end_date_iso + timedelta(hours=2)`` (default
  lag from T-B-017 spike report; configurable).
* Missing ``end_date_iso`` raises :class:`MissingEndDateError`.
* Unknown market raises :class:`UnknownMarketError`.
* Duplicate ``order_id`` raises :class:`DuplicateOrderError`.
* ``Executor`` Protocol compatibility: a structural typecheck verifies
  :class:`SandboxExecutor` satisfies it.
* ``broadcast_count`` stays at 0 after N orders.
* Pydantic validation rejects out-of-range price / non-positive size /
  non-YES/NO side at the :class:`BetRecord` boundary.
"""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent.data.polymarket_sandbox_executor import (
    DuplicateOrderError,
    Executor,
    MarketInfo,
    MissingEndDateError,
    SandboxExecutor,
    SandboxOrderResult,
    UnknownMarketError,
    _derive_expected_settle_ts,
)
from agent.data.sandbox_state import BetRecord, SandboxStateWriter, iter_jsonl


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def writer(tmp_path: Path) -> SandboxStateWriter:
    return SandboxStateWriter(root=tmp_path / "sandbox")


def _resolver(
    table: dict[str, MarketInfo],
) -> Any:
    """Build a deterministic ``MarketResolver`` from a dict.

    Returns a plain function (not a class) so the Protocol's
    structural match is exercised without subclass plumbing.
    """

    def _impl(market_id: str) -> MarketInfo | None:
        return table.get(market_id)

    return _impl


def _default_market_table() -> dict[str, MarketInfo]:
    return {
        "m1": MarketInfo(end_date_iso="2026-05-31T09:00:00Z"),
        "m2": MarketInfo(end_date_iso="2026-06-01T15:00:00Z"),
        # m3: known market with no end date — used to test the
        # MissingEndDateError path.
        "m3": MarketInfo(end_date_iso=None),
    }


@pytest.fixture
def executor(writer: SandboxStateWriter) -> SandboxExecutor:
    return SandboxExecutor(
        state_writer=writer,
        market_resolver=_resolver(_default_market_table()),
    )


# --------------------------------------------------------------------------- #
# 1. Zero-network invariant — socket.create_connection raises if invoked.
# --------------------------------------------------------------------------- #


def test_place_order_makes_zero_network_calls(
    executor: SandboxExecutor,
    writer: SandboxStateWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monkeypatch socket.create_connection to AssertionError; place_order still succeeds."""

    def _explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "sandbox executor made a network call — broke the broadcast=False invariant"
        )

    monkeypatch.setattr(socket, "create_connection", _explode)

    result = asyncio.run(
        executor.place_order(
            market_id="m1", side="YES", price=0.55, size_usd=10.0,
        )
    )
    assert result.accepted is True
    assert result.broadcast is False
    assert executor.broadcast_count == 0
    # And the JSONL line was written.
    records = iter_jsonl(writer.open_bets_path)
    assert len(records) == 1


# --------------------------------------------------------------------------- #
# 2. Executor Protocol compatibility — structural typecheck.
# --------------------------------------------------------------------------- #


def test_sandbox_executor_satisfies_executor_protocol(
    executor: SandboxExecutor,
) -> None:
    """:class:`SandboxExecutor` is structurally compatible with
    :class:`Executor` Protocol (runtime check; mypy runs separately)."""

    def _accepts_executor(e: Executor) -> None:
        # Just a typecheck-shape assertion at runtime — actual usage
        # exercises the place_order signature elsewhere.
        assert hasattr(e, "place_order")
        assert callable(e.place_order)

    _accepts_executor(executor)


# --------------------------------------------------------------------------- #
# 3. JSONL append shape — every accepted order → exactly ONE line, well-formed.
# --------------------------------------------------------------------------- #


def test_one_order_one_jsonl_line(
    executor: SandboxExecutor, writer: SandboxStateWriter,
) -> None:
    """A single place_order call appends exactly one well-formed line."""
    result = asyncio.run(
        executor.place_order(
            market_id="m1", side="YES", price=0.55, size_usd=10.0,
        )
    )
    raw = writer.open_bets_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["bet_id"] == result.order_id
    assert payload["market_id"] == "m1"
    assert payload["side"] == "YES"
    assert payload["price"] == 0.55
    assert payload["size_usd"] == 10.0
    assert payload["status"] == "open"
    # expected_settle_ts is end_date + 2h.
    assert payload["expected_settle_ts"] == "2026-05-31T11:00:00+00:00"


# --------------------------------------------------------------------------- #
# 4. Synthetic order_id uniqueness across 1000 calls.
# --------------------------------------------------------------------------- #


def test_synthetic_order_id_unique_across_1000_calls(
    executor: SandboxExecutor, writer: SandboxStateWriter,
) -> None:
    """No two of 1000 orders share an order_id (uuid4 collision check)."""
    ids: set[str] = set()

    async def _run() -> None:
        for _ in range(1000):
            r = await executor.place_order(
                market_id="m1", side="YES", price=0.5, size_usd=1.0,
            )
            ids.add(r.order_id)

    asyncio.run(_run())
    assert len(ids) == 1000
    # And the JSONL stream has exactly 1000 lines.
    assert sum(1 for _ in writer.open_bets_path.open(encoding="utf-8")) == 1000


# --------------------------------------------------------------------------- #
# 5. Missing end_date raises.
# --------------------------------------------------------------------------- #


def test_missing_end_date_raises(executor: SandboxExecutor) -> None:
    """A market with end_date_iso=None refuses the order."""
    with pytest.raises(MissingEndDateError, match="end_date_iso"):
        asyncio.run(
            executor.place_order(
                market_id="m3", side="YES", price=0.5, size_usd=1.0,
            )
        )


def test_unknown_market_raises(executor: SandboxExecutor) -> None:
    """A market_id the resolver doesn't know refuses the order."""
    with pytest.raises(UnknownMarketError, match="unknown market_id"):
        asyncio.run(
            executor.place_order(
                market_id="nope", side="YES", price=0.5, size_usd=1.0,
            )
        )


# --------------------------------------------------------------------------- #
# 6. Duplicate order_id rejection — idempotency guard via injected uuid.
# --------------------------------------------------------------------------- #


def test_duplicate_order_id_raises(
    executor: SandboxExecutor, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject a uuid4 that returns the same value twice; second call raises."""
    canned_hex = "deadbeef" * 4

    class _FakeUUID:
        hex = canned_hex

    monkeypatch.setattr(
        "agent.data.polymarket_sandbox_executor.uuid.uuid4",
        lambda: _FakeUUID(),
    )

    asyncio.run(
        executor.place_order(
            market_id="m1", side="YES", price=0.5, size_usd=1.0,
        )
    )
    with pytest.raises(DuplicateOrderError, match="collision"):
        asyncio.run(
            executor.place_order(
                market_id="m1", side="YES", price=0.5, size_usd=1.0,
            )
        )


# --------------------------------------------------------------------------- #
# 7. Configurable settle lag — 2h default overridable.
# --------------------------------------------------------------------------- #


def test_settle_lag_configurable(writer: SandboxStateWriter) -> None:
    """A 6-hour lag overrides the 2-hour default."""
    ex = SandboxExecutor(
        state_writer=writer,
        market_resolver=_resolver(_default_market_table()),
        settle_lag=timedelta(hours=6),
    )
    asyncio.run(
        ex.place_order(
            market_id="m1", side="YES", price=0.5, size_usd=1.0,
        )
    )
    record = iter_jsonl(writer.open_bets_path)[0]
    # end_date_iso 2026-05-31T09:00:00Z + 6h = 15:00:00Z
    assert record["expected_settle_ts"] == "2026-05-31T15:00:00+00:00"


# --------------------------------------------------------------------------- #
# 8. Validation: out-of-range price + non-positive size + bad side.
# --------------------------------------------------------------------------- #


def test_price_out_of_range_raises(executor: SandboxExecutor) -> None:
    """Pydantic enforces price ∈ [0, 1] at the BetRecord boundary."""
    with pytest.raises(ValidationError):
        asyncio.run(
            executor.place_order(
                market_id="m1", side="YES", price=1.5, size_usd=1.0,
            )
        )


def test_size_must_be_positive(executor: SandboxExecutor) -> None:
    """size_usd must be > 0 (Pydantic ``gt=0.0``)."""
    with pytest.raises(ValidationError):
        asyncio.run(
            executor.place_order(
                market_id="m1", side="YES", price=0.5, size_usd=0.0,
            )
        )


def test_invalid_side_rejected(executor: SandboxExecutor) -> None:
    """Side must be ``YES`` or ``NO`` — Pydantic Literal."""
    with pytest.raises(ValidationError):
        asyncio.run(
            executor.place_order(
                market_id="m1",
                side="MAYBE",  # type: ignore[arg-type]
                price=0.5,
                size_usd=1.0,
            )
        )


# --------------------------------------------------------------------------- #
# 9. broadcast_count stays 0.
# --------------------------------------------------------------------------- #


def test_broadcast_count_stays_zero(executor: SandboxExecutor) -> None:
    """``broadcast_count`` is the reconciliation gate's read-only invariant."""

    async def _run() -> None:
        for _ in range(20):
            await executor.place_order(
                market_id="m1", side="YES", price=0.5, size_usd=1.0,
            )

    asyncio.run(_run())
    assert executor.broadcast_count == 0


# --------------------------------------------------------------------------- #
# 10. Deterministic clock — injected clock controls ts.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _FixedClock:
    """Deterministic :class:`Clock` for ``ts`` assertions."""

    ts: datetime

    def now(self) -> datetime:
        return self.ts


def test_injected_clock_controls_ts(writer: SandboxStateWriter) -> None:
    """The executor uses the injected clock for the bet's ``ts`` field."""
    fixed = datetime(2026, 5, 26, 20, 0, 0, tzinfo=timezone.utc)
    ex = SandboxExecutor(
        state_writer=writer,
        market_resolver=_resolver(_default_market_table()),
        clock=_FixedClock(fixed),
    )
    asyncio.run(
        ex.place_order(market_id="m1", side="NO", price=0.5, size_usd=1.0)
    )
    record = iter_jsonl(writer.open_bets_path)[0]
    assert record["ts"] == fixed.isoformat()


# --------------------------------------------------------------------------- #
# 11. expected_settle_ts derivation — pure helper unit test.
# --------------------------------------------------------------------------- #


def test_derive_expected_settle_ts_handles_z_suffix() -> None:
    """``Z`` suffix is normalised to ``+00:00`` before adding lag."""
    out = _derive_expected_settle_ts(
        end_date_iso="2026-05-31T09:00:00Z", lag=timedelta(hours=2),
    )
    assert out == "2026-05-31T11:00:00+00:00"


def test_derive_expected_settle_ts_handles_space_separator() -> None:
    """Space-separated naïve-UTC with ``+00`` tz also parses."""
    out = _derive_expected_settle_ts(
        end_date_iso="2026-05-25 23:57:11+00:00", lag=timedelta(hours=2),
    )
    assert out == "2026-05-26T01:57:11+00:00"


def test_derive_expected_settle_ts_rejects_garbage() -> None:
    """Malformed ISO string → ValueError, not silent zero."""
    with pytest.raises(ValueError, match="not ISO-8601"):
        _derive_expected_settle_ts(
            end_date_iso="not-a-date", lag=timedelta(hours=2),
        )


# --------------------------------------------------------------------------- #
# 12. Returned BetRecord matches the JSONL line.
# --------------------------------------------------------------------------- #


def test_returned_bet_record_matches_jsonl(
    executor: SandboxExecutor, writer: SandboxStateWriter,
) -> None:
    """``SandboxOrderResult.bet`` is the same record that was written."""
    result = asyncio.run(
        executor.place_order(
            market_id="m1", side="YES", price=0.42, size_usd=7.5,
        )
    )
    assert isinstance(result, SandboxOrderResult)
    assert isinstance(result.bet, BetRecord)
    # Round-trip the on-disk line through Pydantic and compare.
    on_disk = json.loads(writer.open_bets_path.read_text(encoding="utf-8").strip())
    assert on_disk == result.bet.model_dump()


# --------------------------------------------------------------------------- #
# 13. signal_scores threading (Task L3 settlement-time self-learning).
# --------------------------------------------------------------------------- #


def test_signal_scores_persisted_on_open_bet(
    executor: SandboxExecutor, writer: SandboxStateWriter,
) -> None:
    """Optional ``signal_scores`` is written onto the open BetRecord JSONL."""
    scores = {"tennis_technical": 0.8, "market_momentum": -0.2}
    result = asyncio.run(
        executor.place_order(
            market_id="m1",
            side="YES",
            price=0.55,
            size_usd=10.0,
            signal_scores=scores,
        )
    )
    payload = json.loads(writer.open_bets_path.read_text(encoding="utf-8").strip())
    assert payload["signal_scores"] == scores
    assert result.bet.signal_scores == scores


def test_signal_scores_default_empty_when_absent(
    executor: SandboxExecutor, writer: SandboxStateWriter,
) -> None:
    """Omitting ``signal_scores`` defaults to an empty dict (back-compat)."""
    asyncio.run(
        executor.place_order(
            market_id="m1", side="NO", price=0.4, size_usd=5.0,
        )
    )
    payload = json.loads(writer.open_bets_path.read_text(encoding="utf-8").strip())
    assert payload["signal_scores"] == {}
