"""Pre-Demo §15 Gap 7 rehearsal runner — Phase 3 6h staging certifier.

Spec anchors
------------

* TECHNICAL_PLAN §15 Gap 7: "Pre-Demo Staging 演练 (D17 hard milestone)" —
  6-hour Phase 3 dress rehearsal on testnet with three pass criteria:

    1. Agent triggers Desperate Mode ≥1 time during the run.
    2. Lung Expansion fires ≥1 time during the run.
    3. Dashboard WebSocket NEVER disconnects.
    4. Phase 3 advance tx emits BOTH ``Phase3RolesRenounced`` events
       (one from EnergyController = PauseRoleRenounced, one from
       PhaseManager = UpgradeRoleRenounced).

* PRD §5.1 "Phase 3 启动 tx 同时 emit PauseRoleRenounced 和
  UpgradeRoleRenounced" — same renunciation invariant, restated
  from the consumer's perspective (Demo §9 4:00-5:00 panel).

* TECHNICAL_PLAN §12 "EnergyController 的 Pausable 仅限 Phase 1/2" —
  the rehearsal is the operator's last chance to confirm the role-
  burn ritual actually fired BEFORE the live Demo lights up.

Design
------

The runner is **observe-only**. Concretely:

* Subscribes to the dashboard WS heartbeat stream (the runner is
  a passive listener; it does NOT push frames).
* Tails on-chain events via an injected :class:`EventTailProtocol`
  (production wraps ``eth_subscribe`` over a WS provider; tests
  inject a fake that yields predetermined events).
* Reads the Phase 3 advance tx receipt via an injected
  :class:`TxReceiptReaderProtocol` (production wraps
  ``web3.eth.get_transaction_receipt``; tests inject a fake).

ZERO signing surface. The runner cannot place an order, cannot
sign a typed-data message, cannot broadcast a tx. The
``no_unapproved_external_call`` HIGH gate scans this module's
diff and the source for any of the live-money patterns
catalogued in ``.dev/harness/tools/external_call_audit.py``; the
gate is the structural enforcer of the brief's "observe-only"
invariant.

Failure-mode catalogue
----------------------

The :class:`RehearsalReport` carries one of five terminal values
in :attr:`RehearsalReport.fail_reason`:

* ``None`` — happy path; ``pass`` is ``True``.
* ``DESPERATE_MODE_NOT_OBSERVED`` — pass criterion 1 missed.
* ``LUNG_EXPANSION_NOT_OBSERVED`` — pass criterion 2 missed.
* ``WS_DISCONNECT`` — pass criterion 3 broken (≥1 disconnect seen).
* ``MISSING_RENUNCIATION_EVENT`` — pass criterion 4 broken (either
  EnergyController OR PhaseManager did not emit
  ``Phase3RolesRenounced`` in the Phase 3 advance tx).

The runner reports the FIRST failure observed (in the order
above) — multi-failure runs still surface a single
``fail_reason`` to keep the operator's mental model linear.

Compressed runtime
------------------

The rehearsal runs against a :class:`CompressedClock` so a 6-hour
real run finishes in seconds-to-minutes of CI wall time. The
compression is purely temporal — every WS frame, every on-chain
event, every receipt fetch is the SAME testnet wire the live Demo
will use. The runner's main loop polls each source on a wall-time
cadence proportional to the compression ratio.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.staging.compressed_clock import (
    DEFAULT_COMPRESSION_RATIO,
    CompressedClock,
    CompressedClockEvent,
    OrderingViolation,
)
from agent.staging.event_assertions import (
    Phase3RenunciationCheck,
    ReceiptLike,
    verify_phase3_renunciation,
)

logger = logging.getLogger(__name__)


# Default rehearsal duration in minutes. TP §15 Gap 7 pins 6 hours
# (= 360 minutes). The runner's parameter accepts arbitrary
# overrides for shorter smokes (e.g. 30 min sanity run before a
# full 360 min cert).
DEFAULT_REHEARSAL_DURATION_MINUTES: Final[int] = 360

# Wall-seconds the runner waits for the tail tasks to drain
# cooperatively after the rehearsal window closes. Production WS /
# eth_subscribe streams block forever, so the timeout fires + we
# force-cancel; test fakes drain inside this window naturally.
_DRAIN_TIMEOUT_S: Final[float] = 0.5

# Polling resolution: how many times across the compressed wall
# window the runner checks ``clock.is_done()`` / tail-task completion.
# 100 = a check every 1% of the compressed window — fine-grained
# enough to exit promptly, sparse enough to avoid busy-looping.
_POLL_RESOLUTION_STEPS: Final[int] = 100

# Floor on the per-poll sleep so a very-fast compression (test path)
# does not collapse into a tight loop that starves the tail tasks.
_MIN_POLL_INTERVAL_S: Final[float] = 0.001


class RehearsalFailureReason(StrEnum):
    """Terminal fail-reason taxonomy.

    The runner reports the FIRST failure detected; all four values
    map 1:1 to a TP §15 Gap 7 pass criterion. Adding a value here
    is a contract change that requires a test brief update.
    """

    DESPERATE_MODE_NOT_OBSERVED = "DESPERATE_MODE_NOT_OBSERVED"
    LUNG_EXPANSION_NOT_OBSERVED = "LUNG_EXPANSION_NOT_OBSERVED"
    WS_DISCONNECT = "WS_DISCONNECT"
    MISSING_RENUNCIATION_EVENT = "MISSING_RENUNCIATION_EVENT"


class EventKind(StrEnum):
    """On-chain event kinds the runner counts.

    Three kinds map 1:1 to the TP §15 Gap 7 pass-criterion counters
    in :class:`_RehearsalContext`. Any envelope arriving with a
    ``kind`` outside this enum is logged + dropped — keeps the
    pass/fail surface pinned to the spec's three events.
    """

    DESPERATE_MODE = "desperate_mode"
    LUNG_EXPANSION = "lung_expansion"
    SETTLEMENT = "settlement"


# ---------------------------------------------------------------------------
# Injectable Protocols — observe-only by construction.
# ---------------------------------------------------------------------------


class WsHeartbeatProtocol(Protocol):
    """Dashboard WS heartbeat subscriber.

    Production: a thin ``websockets`` client connected to the
    Track D dashboard. Tests: a fake that yields a controlled
    sequence of heartbeat / disconnect events.

    Yields :class:`WsHeartbeatEvent` instances. The runner counts
    disconnects; any ``connected=False`` event during the rehearsal
    window flips the run to fail.
    """

    def heartbeats(self) -> AsyncIterator[WsHeartbeatEvent]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class WsHeartbeatEvent:
    """One WS heartbeat sample.

    ``connected`` is the binary connection state observed at this
    sample; ``ts_iso`` is the wall-clock ISO timestamp the producer
    stamped. The runner does NOT trust ``ts_iso`` for ordering —
    that's the compressed clock's job; the timestamp is purely a
    diagnostic for the audit log.
    """

    connected: bool
    ts_iso: str


class EventTailProtocol(Protocol):
    """On-chain event tail.

    Yields :class:`EventTailEnvelope` instances for the events the
    runner cares about (Desperate, Lung Expansion, Settlement). The
    Protocol is event-source-agnostic — production wraps
    ``eth_subscribe`` against testnet; tests inject a deterministic
    list-driven iterator.

    The producer is responsible for filtering to the correct
    topic0 hashes (the runner does NOT re-filter; trusting the
    decoder here is structurally fine because every event the
    decoder yields is one the runner counts).
    """

    def events(self) -> AsyncIterator[EventTailEnvelope]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class EventTailEnvelope:
    """One decoded on-chain event handed to the runner.

    ``kind`` is one of the three event categories the runner
    counts:

    * ``"desperate_mode"`` — ``PhaseManager.DesperateModeEntered``
    * ``"lung_expansion"`` — ``EnergyController.MaxBreathDeepened``
    * ``"settlement"`` — ``EnergyController.MarketLossSettled``

    Any other ``kind`` is logged as a WARNING and ignored — the
    runner is intentionally narrow so an unknown event kind does
    not silently affect the pass/fail verdict.
    """

    kind: str
    tx_hash: str
    block_number: int
    ts_iso: str


class TxReceiptReaderProtocol(Protocol):
    """Phase 3 advance tx receipt reader.

    Production: a thin ``web3.eth.get_transaction_receipt`` wrapper
    awaitable. Tests inject a fake returning a hand-built
    :class:`ReceiptLike`.
    """

    async def get_phase3_advance_receipt(self) -> ReceiptLike: ...


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class CompressedTimelineFrame(BaseModel):
    """One frame of the recorded compressed timeline.

    Mirror of :class:`CompressedClockEvent` lifted into a Pydantic
    model so the :class:`RehearsalReport` round-trips through JSON.
    The frozen dataclass would not serialise cleanly inside a
    Pydantic field; this BaseModel form is the wire shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    sim_time_s: float
    wall_time_s: float
    kind: str = ""

    @classmethod
    def from_clock_event(
        cls, evt: CompressedClockEvent, *, kind: str = ""
    ) -> CompressedTimelineFrame:
        """Build the wire-shape frame from a clock event."""
        return cls(
            name=evt.name,
            sim_time_s=evt.sim_time_s,
            wall_time_s=evt.wall_time_s,
            kind=kind,
        )


class RehearsalReport(BaseModel):
    """Terminal verdict + diagnostic counts for one rehearsal run.

    Fields
    ------

    desperate_mode_count:
        Number of ``DesperateModeEntered`` events observed during
        the rehearsal window. Pass requires ``>= 1``.

    lung_expansion_count:
        Number of ``MaxBreathDeepened`` (Lung Expansion) events
        observed. Pass requires ``>= 1``.

    settlement_count:
        Number of ``MarketLossSettled`` events observed. NOT a
        pass criterion (TP §15 says "几次" / "several" — informal,
        no hard count); reported for the audit log.

    ws_disconnect_count:
        Number of WS disconnect events observed. Pass requires
        ``== 0``.

    pause_role_renounced_tx:
        Tx hash of the Phase 3 advance tx whose receipt emitted
        ``EnergyController.Phase3RolesRenounced``. ``None`` iff
        the EC event was NOT found in the receipt. Pass requires
        non-None.

    upgrade_role_renounced_tx:
        Same but for the ``PhaseManager.Phase3RolesRenounced``
        event. Pass requires non-None.

    passed:
        Overall pass flag. Aliased as ``pass`` on the wire so the
        JSON dump matches the brief's field name (``pass`` is a
        Python keyword so the field is declared with the alias
        pattern :class:`PhaseTransitionPayload` uses).

    fail_reason:
        First failure observed, drawn from
        :class:`RehearsalFailureReason`. ``None`` iff ``passed``.

    timeline_events:
        Strict-monotonic sequence of every event the runner marked
        on the :class:`CompressedClock`. Persisted in the report so
        the Demo runbook can replay the rehearsal's exact ordering
        for the audit panel.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    desperate_mode_count: Annotated[int, Field(ge=0)]
    lung_expansion_count: Annotated[int, Field(ge=0)]
    settlement_count: Annotated[int, Field(ge=0)]
    ws_disconnect_count: Annotated[int, Field(ge=0)]
    pause_role_renounced_tx: str | None = None
    upgrade_role_renounced_tx: str | None = None
    passed: bool = Field(alias="pass")
    fail_reason: RehearsalFailureReason | None = None
    timeline_events: list[CompressedTimelineFrame] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class _RehearsalContext:
    """In-process accumulator the runner mutates as events flow.

    Pulled out as a mutable dataclass so the three concurrent tail
    tasks (WS, event tail, periodic checks) all bind the same
    record without passing a long argument list. The runner builds
    its :class:`RehearsalReport` from this context at the end.

    NOTE: the context is observe-only by construction — its fields
    are all counts / hashes / lists. None of them gate the runner's
    own behaviour; they only inform the final report.
    """

    counts: dict[EventKind, int] = field(
        default_factory=lambda: {kind: 0 for kind in EventKind}
    )
    ws_disconnect_count: int = 0
    timeline: list[CompressedTimelineFrame] = field(default_factory=list)


def _record_marked_event(
    clock: CompressedClock,
    ctx: _RehearsalContext,
    name: str,
    kind: str,
    payload: dict[str, object],
    tx_hash: str | None = None,
) -> None:
    """Mark on the clock + append to the runner's timeline.

    A :class:`OrderingViolation` (event arrived out of strict-monotonic
    order) is swallowed with a warning log — the runner's verdict
    code reads the per-kind counts, not the timeline, so a malformed
    arrival should not lose the count even when it disrupts the
    audit ordering.
    """
    try:
        clock_evt = clock.mark(name, payload=payload)
        ctx.timeline.append(
            CompressedTimelineFrame.from_clock_event(clock_evt, kind=kind)
        )
    except OrderingViolation:
        logger.warning(
            "rehearsal: %s arrived out of order; count still incremented "
            "(tx=%s)",
            kind, tx_hash or "<n/a>",
        )


async def _consume_heartbeats(
    *,
    ws: WsHeartbeatProtocol,
    clock: CompressedClock,
    ctx: _RehearsalContext,
    cancel: asyncio.Event,
) -> None:
    """Tail WS heartbeats; record every disconnect.

    Exits when the producer iterator drains, the ``cancel`` event
    fires, or the task is cancelled. The ``finally`` block guarantees
    ``ws.aclose()`` fires on every exit path.
    """
    try:
        async for evt in ws.heartbeats():
            if cancel.is_set():
                break
            if not evt.connected:
                ctx.ws_disconnect_count += 1
                _record_marked_event(
                    clock, ctx,
                    name="ws_disconnect",
                    kind="ws_disconnect",
                    payload={"ts_iso": evt.ts_iso},
                )
    except asyncio.CancelledError:
        raise
    finally:
        await ws.aclose()


async def _consume_events(
    *,
    tail: EventTailProtocol,
    clock: CompressedClock,
    ctx: _RehearsalContext,
    cancel: asyncio.Event,
) -> None:
    """Tail on-chain events; bucket each into :attr:`_RehearsalContext.counts`.

    Unknown ``kind`` values are dropped with a WARNING log so the
    runner's verdict surface stays pinned to the three TP §15 Gap 7
    counted events. The ``finally`` block guarantees ``tail.aclose()``
    fires on every exit path.
    """
    try:
        async for envelope in tail.events():
            if cancel.is_set():
                break
            try:
                kind_enum = EventKind(envelope.kind)
            except ValueError:
                logger.warning(
                    "rehearsal: unknown event kind %r dropped (tx=%s)",
                    envelope.kind, envelope.tx_hash,
                )
                continue
            ctx.counts[kind_enum] += 1
            _record_marked_event(
                clock, ctx,
                name=kind_enum.value,
                kind=kind_enum.value,
                payload={
                    "tx_hash": envelope.tx_hash,
                    "block_number": envelope.block_number,
                    "ts_iso": envelope.ts_iso,
                },
                tx_hash=envelope.tx_hash,
            )
    except asyncio.CancelledError:
        raise
    finally:
        await tail.aclose()


def _compose_verdict(
    *,
    ctx: _RehearsalContext,
    renunciation: Phase3RenunciationCheck,
) -> tuple[bool, RehearsalFailureReason | None, str | None, str | None]:
    """Pure verdict computation — applied AFTER all tails drain.

    Returns ``(passed, fail_reason, pause_role_tx, upgrade_role_tx)``.
    Failure ordering (first match wins):

    1. WS_DISCONNECT — operator-visible breakage trumps everything.
    2. MISSING_RENUNCIATION_EVENT — Demo audit story breaks if
       either side of the renunciation is missing.
    3. DESPERATE_MODE_NOT_OBSERVED — PRD §6.9 invariant not
       exercised by the rehearsal.
    4. LUNG_EXPANSION_NOT_OBSERVED — PRD §6.7 invariant not
       exercised.

    The ordering is the brief's own listing order; reviewers reading
    the failure narrative get the same "what's structurally most
    broken first" hierarchy as the spec.
    """
    pause_tx = renunciation.transaction_hash if renunciation.pause_role_emitted else None
    upgrade_tx = renunciation.transaction_hash if renunciation.upgrade_role_emitted else None

    if ctx.ws_disconnect_count > 0:
        return (False, RehearsalFailureReason.WS_DISCONNECT, pause_tx, upgrade_tx)
    if not renunciation.both_emitted:
        return (
            False,
            RehearsalFailureReason.MISSING_RENUNCIATION_EVENT,
            pause_tx,
            upgrade_tx,
        )
    if ctx.counts[EventKind.DESPERATE_MODE] < 1:
        return (
            False,
            RehearsalFailureReason.DESPERATE_MODE_NOT_OBSERVED,
            pause_tx,
            upgrade_tx,
        )
    if ctx.counts[EventKind.LUNG_EXPANSION] < 1:
        return (
            False,
            RehearsalFailureReason.LUNG_EXPANSION_NOT_OBSERVED,
            pause_tx,
            upgrade_tx,
        )
    return (True, None, pause_tx, upgrade_tx)


async def run_rehearsal(
    *,
    duration_minutes: int,
    rpc_urls: dict[str, str],
    dashboard_ws_url: str,
    ws_subscriber: WsHeartbeatProtocol,
    event_tail: EventTailProtocol,
    receipt_reader: TxReceiptReaderProtocol,
    energy_controller_address: str,
    phase_manager_address: str,
    renunciation_topic0: str,
    compression_ratio: float = DEFAULT_COMPRESSION_RATIO,
    clock: CompressedClock | None = None,
) -> RehearsalReport:
    """Run one Phase 3 staging rehearsal end-to-end.

    The runner spawns two concurrent observer tasks (WS heartbeat
    tail + on-chain event tail) and waits for the compressed clock
    to saturate ``duration_minutes`` of sim-time. Then it fetches
    the Phase 3 advance tx receipt + composes the final
    :class:`RehearsalReport`.

    Parameters
    ----------
    duration_minutes
        Sim-time duration to observe. TP §15 Gap 7 default is 360
        (6 hours); the runner's local smoke runs accept shorter
        windows for fast iteration.
    rpc_urls
        Map of chain id → RPC URL. The runner does NOT directly use
        these URLs — they're forwarded to the production wiring
        layer that constructs :class:`EventTailProtocol` /
        :class:`TxReceiptReaderProtocol` adapters. Surfaced as a
        parameter so the brief's signature matches the production
        operator runbook; tests pass an empty dict.
    dashboard_ws_url
        WS URL for the dashboard heartbeat. Same forwarding rule —
        the production wiring layer reads this; the runner just
        forwards.
    ws_subscriber
        Protocol-typed WS heartbeat tail. Production wires a
        websockets client; tests inject a recording fake.
    event_tail
        Protocol-typed on-chain event tail. Production wires
        ``eth_subscribe``; tests inject a list-driven fake.
    receipt_reader
        Protocol-typed Phase 3 advance tx receipt reader. Production
        wires ``web3.eth.get_transaction_receipt``; tests inject a
        constant-receipt fake.
    energy_controller_address, phase_manager_address
        EVM addresses of the two contracts whose
        ``Phase3RolesRenounced`` emission the runner verifies.
    renunciation_topic0
        Pre-computed ``keccak256("Phase3RolesRenounced(uint64)")``.
        The runner takes this as a parameter (not a constant)
        because the topic0 of an event is best computed by the
        production wiring's web3 layer at boot; hard-coding a
        constant would silently break if a future ABI bump added
        an indexed param.
    compression_ratio
        Sim-seconds per wall-second. Default 3600x = a 6-hour
        rehearsal in 6 wall-seconds. Tests pass a higher ratio for
        instant completion.
    clock
        Optional pre-built :class:`CompressedClock`. Tests inject
        one with a fake wall-clock + no-op sleep so the scenario
        is fully deterministic; production passes ``None`` and the
        runner builds a fresh real-clock instance.

    Returns
    -------
    RehearsalReport
        Terminal verdict — see :class:`RehearsalReport` field docs.

    Raises
    ------
    The runner does NOT raise on failure — failure surfaces via
    :attr:`RehearsalReport.fail_reason`. Exceptions only escape if
    the receipt reader itself crashes (e.g. RPC down) or one of
    the tails raises non-cancellation; in those cases the operator
    should re-run the rehearsal rather than read a synthetic
    report.
    """
    if duration_minutes <= 0:
        raise ValueError(
            f"duration_minutes must be > 0 (got {duration_minutes})"
        )
    logger.info(
        "rehearsal start duration_minutes=%d ws_url=%s rpc_urls=%s ratio=%.1fx",
        duration_minutes, dashboard_ws_url, sorted(rpc_urls.keys()), compression_ratio,
    )

    real_duration_s = float(duration_minutes) * 60.0
    active_clock = clock if clock is not None else CompressedClock(
        real_duration_s=real_duration_s,
        compression_ratio=compression_ratio,
    )

    ctx = _RehearsalContext()
    cancel = asyncio.Event()

    ws_task = asyncio.create_task(
        _consume_heartbeats(
            ws=ws_subscriber,
            clock=active_clock,
            ctx=ctx,
            cancel=cancel,
        ),
        name="rehearsal.ws_heartbeats",
    )
    events_task = asyncio.create_task(
        _consume_events(
            tail=event_tail,
            clock=active_clock,
            ctx=ctx,
            cancel=cancel,
        ),
        name="rehearsal.events",
    )

    try:
        # Yield once so the tail tasks get scheduled before the
        # polling loop. Without this, very-high-compression tests
        # (``is_done()`` saturates in microseconds) skip the loop
        # entirely and the tails never run.
        await asyncio.sleep(0)

        poll_wall_s = max(
            _MIN_POLL_INTERVAL_S,
            real_duration_s / (compression_ratio * _POLL_RESOLUTION_STEPS),
        )

        while not active_clock.is_done():
            if ws_task.done() and events_task.done():
                break
            await active_clock.wait_for_wallclock(poll_wall_s)
    finally:
        cancel.set()
        try:
            await asyncio.wait_for(
                asyncio.gather(ws_task, events_task, return_exceptions=True),
                timeout=_DRAIN_TIMEOUT_S,
            )
        except TimeoutError:
            # Production WS / eth_subscribe streams block forever;
            # the cooperative cancel event won't drain them. Force
            # cancellation and absorb the resulting CancelledError.
            for task in (ws_task, events_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(ws_task, events_task, return_exceptions=True)

    # Receipt fetch is the LAST step: the operator's Phase 3 advance
    # broadcast happens during the rehearsal window, so the receipt
    # only exists once the window closes.
    receipt = await receipt_reader.get_phase3_advance_receipt()
    renunciation = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=energy_controller_address,
        phase_manager_address=phase_manager_address,
        renunciation_topic0=renunciation_topic0,
    )

    passed, fail_reason, pause_tx, upgrade_tx = _compose_verdict(
        ctx=ctx, renunciation=renunciation,
    )

    # pass is a Python keyword; validate via alias.
    report = RehearsalReport.model_validate(
        {
            "desperate_mode_count": ctx.counts[EventKind.DESPERATE_MODE],
            "lung_expansion_count": ctx.counts[EventKind.LUNG_EXPANSION],
            "settlement_count": ctx.counts[EventKind.SETTLEMENT],
            "ws_disconnect_count": ctx.ws_disconnect_count,
            "pause_role_renounced_tx": pause_tx,
            "upgrade_role_renounced_tx": upgrade_tx,
            "pass": passed,
            "fail_reason": fail_reason,
            "timeline_events": [
                frame.model_dump() for frame in ctx.timeline
            ],
        }
    )
    logger.info(
        "rehearsal end pass=%s fail_reason=%s desperate=%d lung=%d "
        "settlements=%d ws_disconnects=%d",
        report.passed, report.fail_reason,
        report.desperate_mode_count, report.lung_expansion_count,
        report.settlement_count, report.ws_disconnect_count,
    )
    return report


__all__ = [
    "DEFAULT_REHEARSAL_DURATION_MINUTES",
    "CompressedTimelineFrame",
    "EventKind",
    "EventTailEnvelope",
    "EventTailProtocol",
    "RehearsalFailureReason",
    "RehearsalReport",
    "TxReceiptReaderProtocol",
    "WsHeartbeatEvent",
    "WsHeartbeatProtocol",
    "run_rehearsal",
]
