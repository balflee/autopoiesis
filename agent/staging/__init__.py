"""Track B staging package — Pre-Demo §15 Gap 7 rehearsal harness.

Builds the deterministic 6-hour Phase 3 testnet rehearsal that
TECHNICAL_PLAN §15 Gap 7 promotes to a hard milestone the week of
the Demo. The package is **observe-only** — every component reads
WebSocket / RPC streams, parses receipts, and produces a structured
:class:`RehearsalReport`. There is ZERO signing surface here, no
``polymarket.*post`` / ``eth_sendTransaction`` / ``signer.sign``
patterns; the ``no_unapproved_external_call`` gate enforces that
structurally on every diff.

Modules
-------

``compressed_clock``
    Wall-clock compressor — scales a 6-hour Phase 3 dress rehearsal
    into a parameterised runtime while preserving the strict-monotonic
    ordering of every marked event. The runner threads every event
    through the clock so a Late-Lung-Expansion that fires before
    Early-Desperate-Mode is a structural bug, not an arrival jitter.

``event_assertions``
    Phase 3 renunciation receipt verifier — confirms the Phase 3
    advance tx emits BOTH ``Phase3RolesRenounced`` topics (one from
    ``EnergyController`` = the pause-role burn, one from
    ``PhaseManager`` = the upgrade-role burn). The brief labels these
    semantically as ``PauseRoleRenounced`` + ``UpgradeRoleRenounced``;
    on-chain both surface as the canonical
    ``Phase3RolesRenounced(uint64)`` event emitted by each contract.

``rehearsal_runner``
    Async orchestrator that subscribes to the dashboard WS
    heartbeat, tails the on-chain event stream, and asserts the
    three TP §15 Gap 7 pass criteria. Returns a :class:`RehearsalReport`
    with diagnostic counts so the operator sees WHY a run failed,
    not just THAT one failed.
"""

from agent.staging.compressed_clock import (
    CompressedClock,
    CompressedClockEvent,
    OrderingViolation,
)
from agent.staging.event_assertions import (
    PHASE3_RENUNCIATION_SIGNATURE,
    Phase3RenunciationCheck,
    ReceiptLike,
    verify_phase3_renunciation,
)
from agent.staging.rehearsal_runner import (
    DEFAULT_REHEARSAL_DURATION_MINUTES,
    CompressedTimelineFrame,
    EventKind,
    EventTailEnvelope,
    EventTailProtocol,
    RehearsalFailureReason,
    RehearsalReport,
    TxReceiptReaderProtocol,
    WsHeartbeatEvent,
    WsHeartbeatProtocol,
    run_rehearsal,
)

__all__ = [
    "DEFAULT_REHEARSAL_DURATION_MINUTES",
    "PHASE3_RENUNCIATION_SIGNATURE",
    "CompressedClock",
    "CompressedClockEvent",
    "CompressedTimelineFrame",
    "EventKind",
    "EventTailEnvelope",
    "EventTailProtocol",
    "OrderingViolation",
    "Phase3RenunciationCheck",
    "ReceiptLike",
    "RehearsalFailureReason",
    "RehearsalReport",
    "TxReceiptReaderProtocol",
    "WsHeartbeatEvent",
    "WsHeartbeatProtocol",
    "run_rehearsal",
    "verify_phase3_renunciation",
]
