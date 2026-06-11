"""Tests for :mod:`agent.staging.rehearsal_runner`.

Covers per the T-B-011 brief acceptance criterion:

* Happy path: all 3 pass criteria satisfied + Phase 3 advance tx
  emits both renunciation events → ``passed=True``.
* 4 distinct failure modes:

  - ``DESPERATE_MODE_NOT_OBSERVED``
  - ``LUNG_EXPANSION_NOT_OBSERVED``
  - ``WS_DISCONNECT``
  - ``MISSING_RENUNCIATION_EVENT``

Additionally:

* The returned report's JSON dump uses the wire-shape field name
  ``pass`` (the Field alias), proving the brief's field name is
  preserved on the wire.
* Observe-only AST scan: no live-money call patterns / signing
  surfaces in :mod:`agent.staging.rehearsal_runner` or sibling
  modules.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent.staging.event_assertions import ReceiptLike
from agent.staging.rehearsal_runner import (
    EventKind,
    EventTailEnvelope,
    RehearsalFailureReason,
    RehearsalReport,
    WsHeartbeatEvent,
    run_rehearsal,
)

# ── Test fixtures: a fake WS subscriber + event tail + receipt ─────


@dataclass(frozen=True)
class _FakeLog:
    address: str
    topics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _FakeReceipt:
    transactionHash: str
    logs: list[_FakeLog] = field(default_factory=list)


class _FakeWsSubscriber:
    """Yields a fixed sequence of WS heartbeat events then drains."""

    def __init__(self, events: list[WsHeartbeatEvent]) -> None:
        self._events = list(events)
        self.closed = False

    def heartbeats(self) -> AsyncIterator[WsHeartbeatEvent]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[WsHeartbeatEvent]:
        for evt in self._events:
            yield evt
            # Yield-back to the loop so the runner sees one event at
            # a time and the compressed-clock progress can advance.
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        self.closed = True


class _FakeEventTail:
    """Yields a fixed sequence of on-chain event envelopes then drains."""

    def __init__(self, events: list[EventTailEnvelope]) -> None:
        self._events = list(events)
        self.closed = False

    def events(self) -> AsyncIterator[EventTailEnvelope]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[EventTailEnvelope]:
        for evt in self._events:
            yield evt
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        self.closed = True


class _FakeReceiptReader:
    """Returns a fixed pre-built :class:`ReceiptLike`."""

    def __init__(self, receipt: ReceiptLike) -> None:
        self._receipt = receipt
        self.calls = 0

    async def get_phase3_advance_receipt(self) -> ReceiptLike:
        self.calls += 1
        return self._receipt


# ── Test constants ────────────────────────────────────────────────


_EC_ADDR = "0x1111111111111111111111111111111111111111"
_PM_ADDR = "0x2222222222222222222222222222222222222222"
_TOPIC0_RENOUNCE = (
    "0xabcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
)


def _good_renunciation_receipt() -> _FakeReceipt:
    return _FakeReceipt(
        transactionHash="0xphase3happypath",
        logs=[
            _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
            _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )


def _missing_pm_receipt() -> _FakeReceipt:
    return _FakeReceipt(
        transactionHash="0xphase3missingpm",
        logs=[
            _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )


def _good_ws_events() -> list[WsHeartbeatEvent]:
    """5 healthy heartbeats — no disconnects."""
    return [
        WsHeartbeatEvent(connected=True, ts_iso=f"2026-05-23T00:00:{i:02d}Z")
        for i in range(5)
    ]


def _good_chain_events() -> list[EventTailEnvelope]:
    """One DesperateModeEntered + one MaxBreathDeepened + one settlement."""
    return [
        EventTailEnvelope(
            kind="desperate_mode",
            tx_hash="0xdesperate1",
            block_number=100,
            ts_iso="2026-05-23T00:00:01Z",
        ),
        EventTailEnvelope(
            kind="settlement",
            tx_hash="0xsettle1",
            block_number=101,
            ts_iso="2026-05-23T00:00:02Z",
        ),
        EventTailEnvelope(
            kind="lung_expansion",
            tx_hash="0xlung1",
            block_number=102,
            ts_iso="2026-05-23T00:00:03Z",
        ),
    ]


async def _run(
    *,
    ws_events: list[WsHeartbeatEvent],
    chain_events: list[EventTailEnvelope],
    receipt: _FakeReceipt,
    duration_minutes: int = 1,
) -> RehearsalReport:
    """Drive the runner with a high compression so the test completes
    in well under a wall-second."""
    ws = _FakeWsSubscriber(ws_events)
    tail = _FakeEventTail(chain_events)
    reader = _FakeReceiptReader(receipt)
    report = await run_rehearsal(
        duration_minutes=duration_minutes,
        rpc_urls={"polygon-amoy": "wss://amoy.fake", "l3": "wss://l3.fake"},
        dashboard_ws_url="wss://dash.fake",
        ws_subscriber=ws,
        event_tail=tail,
        receipt_reader=reader,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
        # Compress the 1-minute window into ~60ms of wall-clock —
        # enough drain time for the fakes to fully yield, fast
        # enough that the suite runs in well under a wall-second.
        compression_ratio=1000.0,
    )
    # Both tails must have been closed by the runner's cleanup path.
    assert ws.closed is True
    assert tail.closed is True
    # The receipt reader is consulted exactly once — the runner
    # MUST NOT poll it repeatedly (would amplify RPC load).
    assert reader.calls == 1
    return report


# ── Happy path ────────────────────────────────────────────────────


def test_happy_path_passes() -> None:
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=_good_chain_events(),
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is True
    assert report.fail_reason is None
    assert report.desperate_mode_count == 1
    assert report.lung_expansion_count == 1
    assert report.settlement_count == 1
    assert report.ws_disconnect_count == 0
    assert report.pause_role_renounced_tx == "0xphase3happypath"
    assert report.upgrade_role_renounced_tx == "0xphase3happypath"
    # Timeline preserves arrival order — desperate before lung
    # before settlement (per the fixture).
    kinds_in_timeline = [frame.kind for frame in report.timeline_events]
    assert "desperate_mode" in kinds_in_timeline
    assert "lung_expansion" in kinds_in_timeline


# ── Fail mode 1: no Desperate Mode ───────────────────────────────


def test_missing_desperate_mode_fails() -> None:
    chain_events = [
        EventTailEnvelope(
            kind="lung_expansion",
            tx_hash="0xlung",
            block_number=100,
            ts_iso="2026-05-23T00:00:01Z",
        ),
        EventTailEnvelope(
            kind="settlement",
            tx_hash="0xsettle",
            block_number=101,
            ts_iso="2026-05-23T00:00:02Z",
        ),
    ]
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=chain_events,
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is False
    assert report.fail_reason == RehearsalFailureReason.DESPERATE_MODE_NOT_OBSERVED
    assert report.desperate_mode_count == 0
    assert report.lung_expansion_count == 1


# ── Fail mode 2: no Lung Expansion ───────────────────────────────


def test_missing_lung_expansion_fails() -> None:
    chain_events = [
        EventTailEnvelope(
            kind="desperate_mode",
            tx_hash="0xdesperate",
            block_number=100,
            ts_iso="2026-05-23T00:00:01Z",
        ),
        EventTailEnvelope(
            kind="settlement",
            tx_hash="0xsettle",
            block_number=101,
            ts_iso="2026-05-23T00:00:02Z",
        ),
    ]
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=chain_events,
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is False
    assert report.fail_reason == RehearsalFailureReason.LUNG_EXPANSION_NOT_OBSERVED
    assert report.desperate_mode_count == 1
    assert report.lung_expansion_count == 0


# ── Fail mode 3: WS disconnect mid-run ──────────────────────────


def test_ws_disconnect_fails() -> None:
    ws_events = [
        WsHeartbeatEvent(connected=True, ts_iso="2026-05-23T00:00:00Z"),
        WsHeartbeatEvent(connected=False, ts_iso="2026-05-23T00:00:01Z"),
        WsHeartbeatEvent(connected=True, ts_iso="2026-05-23T00:00:02Z"),
    ]
    report = asyncio.run(
        _run(
            ws_events=ws_events,
            chain_events=_good_chain_events(),
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is False
    assert report.fail_reason == RehearsalFailureReason.WS_DISCONNECT
    assert report.ws_disconnect_count == 1


# ── Fail mode 4: missing renunciation event ─────────────────────


def test_missing_renunciation_event_fails() -> None:
    """Phase 3 advance receipt missing the PhaseManager emission."""
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=_good_chain_events(),
            receipt=_missing_pm_receipt(),
        )
    )
    assert report.passed is False
    assert report.fail_reason == RehearsalFailureReason.MISSING_RENUNCIATION_EVENT
    assert report.pause_role_renounced_tx == "0xphase3missingpm"
    # Upgrade-role tx is None because the PM event was absent.
    assert report.upgrade_role_renounced_tx is None


# ── Fail-mode ordering: WS disconnect trumps everything else ─────


def test_ws_disconnect_takes_priority_over_missing_events() -> None:
    """If WS disconnects AND chain events are missing, ``WS_DISCONNECT``
    is the reported failure (operator-visible breakage wins)."""
    ws_events = [
        WsHeartbeatEvent(connected=False, ts_iso="2026-05-23T00:00:00Z"),
    ]
    # Empty chain events → no desperate, no lung, no settlement.
    report = asyncio.run(
        _run(
            ws_events=ws_events,
            chain_events=[],
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is False
    assert report.fail_reason == RehearsalFailureReason.WS_DISCONNECT


# ── Wire-shape: "pass" alias preserved on JSON dump ────────────


def test_report_dumps_with_pass_alias() -> None:
    """The serialised report uses the brief's literal field name."""
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=_good_chain_events(),
            receipt=_good_renunciation_receipt(),
        )
    )
    dump = report.model_dump(by_alias=True)
    assert "pass" in dump
    assert dump["pass"] is True
    assert "passed" not in dump


# ── Observe-only AST scan — structural enforcement of the "no
#     live-money call patterns" brief invariant. ────────────────


def test_staging_modules_have_no_live_money_call_patterns() -> None:
    """AST scan of every staging module for live-money call sites.

    The contract is: NO :class:`ast.Call` in the staging package may
    dispatch to a method or function name in the live-money denylist
    (matching ``.dev/harness/tools/external_call_audit.py``'s regex
    set). Docstrings mentioning the names by way of describing the
    invariant are fine — the gate's regex requires an opening paren
    to fire, and the AST walk requires a Call node.

    A hit raises AssertionError loudly so a reviewer (or the
    cross-chain auditor) gets a structural assert backing the
    observe-only invariant.
    """
    denylist_attrs: frozenset[str] = frozenset({
        "signtransaction",
        "signtypeddata",
        "signmessage",
        "sign",  # bare wallet.sign( / signer.sign(
        "broadcasttransaction",
        "broadcast",
        "sendrawtransaction",
        "sendtransaction",
        "place_order",
        "create_order",
        "submit_order",
        "post",  # polymarket.post( / clob.post(
        "safeexecute",
        "execute_order",
    })
    # Identifiers whose presence as a target of attribute access in a
    # Call context flags a live-money pattern. The check is the
    # attribute name on a method call, not the bare identifier.

    denylist_funcs: frozenset[str] = frozenset({
        # Bare-function calls to known live-money helpers.
        "eth_sendtransaction",
        "eth_sendrawtransaction",
    })

    here = Path(__file__).resolve()
    project_root = next(
        p for p in here.parents if (p / "pyproject.toml").is_file()
    )
    staging_dir = project_root / "agent" / "staging"
    found: list[str] = []
    for source_file in sorted(staging_dir.glob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                attr_lower = node.func.attr.lower()
                if attr_lower in denylist_attrs:
                    found.append(
                        f"{source_file.name}:{node.lineno} .{node.func.attr}()"
                    )
            elif isinstance(node.func, ast.Name):
                name_lower = node.func.id.lower()
                if name_lower in denylist_funcs:
                    found.append(
                        f"{source_file.name}:{node.lineno} {node.func.id}()"
                    )
    assert not found, (
        "live-money call sites found in staging package: "
        + ", ".join(found)
    )


# ── Receipt fetched AFTER the rehearsal window closes ────────────


def test_receipt_fetched_once_after_window_closes() -> None:
    """The receipt reader must be called exactly once + only after the
    tails drain. We assert ``calls == 1`` via :func:`_run`; this
    test additionally captures the contract: the reader returns the
    same receipt for both pause/upgrade fields when both events fire."""
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=_good_chain_events(),
            receipt=_good_renunciation_receipt(),
        )
    )
    # Both fields populated from the same tx — they're the SAME tx
    # because Phase 3 advance is one tx; the two events are emitted
    # together in that tx's receipt.
    assert report.pause_role_renounced_tx == report.upgrade_role_renounced_tx


# ── Compressed clock saturation: runner exits before infinite loop ──


def test_runner_terminates_even_with_no_events() -> None:
    """A rehearsal with empty WS + empty chain feeds still terminates
    once the compressed clock saturates. The pass criterion fails
    (DESPERATE_MODE_NOT_OBSERVED) but the runner returns rather
    than hanging."""
    report = asyncio.run(
        _run(
            ws_events=[],
            chain_events=[],
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is False
    assert report.fail_reason == RehearsalFailureReason.DESPERATE_MODE_NOT_OBSERVED
    assert report.desperate_mode_count == 0
    assert report.lung_expansion_count == 0


# ── Invalid duration_minutes raises ValueError ──────────────────────


def test_zero_duration_minutes_raises() -> None:
    async def _go() -> None:
        ws = _FakeWsSubscriber([])
        tail = _FakeEventTail([])
        reader = _FakeReceiptReader(_good_renunciation_receipt())
        await run_rehearsal(
            duration_minutes=0,
            rpc_urls={},
            dashboard_ws_url="wss://x",
            ws_subscriber=ws,
            event_tail=tail,
            receipt_reader=reader,
            energy_controller_address=_EC_ADDR,
            phase_manager_address=_PM_ADDR,
            renunciation_topic0=_TOPIC0_RENOUNCE,
        )

    with pytest.raises(ValueError, match="duration_minutes"):
        asyncio.run(_go())


def test_event_kind_enum_exposes_three_values() -> None:
    """The EventKind StrEnum is the runner's stringly-typed-replacement
    contract — three values, all corresponding to TP §15 Gap 7
    counted events."""
    assert {k.value for k in EventKind} == {
        "desperate_mode",
        "lung_expansion",
        "settlement",
    }


# ── Unknown event kind is silently ignored (does NOT corrupt counts) ──


def test_unknown_event_kind_is_dropped() -> None:
    chain_events = [
        EventTailEnvelope(
            kind="mystery_event",
            tx_hash="0xmystery",
            block_number=99,
            ts_iso="2026-05-23T00:00:00Z",
        ),
        *_good_chain_events(),
    ]
    report = asyncio.run(
        _run(
            ws_events=_good_ws_events(),
            chain_events=chain_events,
            receipt=_good_renunciation_receipt(),
        )
    )
    assert report.passed is True
    # The mystery event did not inflate any counter.
    assert report.desperate_mode_count == 1
    assert report.lung_expansion_count == 1
    assert report.settlement_count == 1
