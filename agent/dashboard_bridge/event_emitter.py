"""WsEventEmitter — produces Track-D-compatible WS frames in-memory.

Wire-schema anchor: ``.dev/contracts/dashboard_ws_message.v0.4.0.json``.

Twelve frame kinds are supported per the v0.4.0 union (v0.4.0 is a
BREAKING bump over v0.3.0 — it renames 3 of the 5 ``signals`` enum keys
to the Sackmann/CLOB payloads they carry: smart_money->surface_advantage,
sentiment_llm->head_to_head, crowd_volume->rest_recency; no kind added):

    vitals, thought, decision, reflection, weights_updated,
    llm_activated, desperate_mode_entered, terminal_lucidity_start,
    last_words, death, phase_transition, decision_feed

T-B-008 only exercises six of them for the Phase 2 launch smoke +
the Demo §9 1:30-2:30 tape:

    vitals, thought, decision, reflection, weights_updated,
    llm_activated, phase_transition

…but every kind is wired so future sprints can extend without
re-touching the emitter. The unused kinds carry minimum-viable typed
payload classes + ``emit_*`` methods marked ``# pragma: no cover`` until
their consumers land.

Idempotency for ``llm_activated``: the emitter latches a one-shot flag
matching the dashboard's ``llmActivated`` boolean (PRD §4.4, T-D-003 wire
schema ``llm_activated_once`` consumer note). A second call is a no-op
returning the original frame so a buggy Phase 2 boot that fires the
event twice does NOT desync the dashboard overlay.

Non-goals for this sprint:
* Real WebSocket / HTTP transport — sprint_5 ships ``dashboard_ws_server``.
* Backpressure / pruning — the in-memory queue grows unbounded; that's
  fine for a 90-minute hackathon run + a Demo tape that's ≤30 frames.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.core.state import Phase
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES

# Mirror of dashboard/lib/wsContract.ts WS_CONTRACT_VERSION. The Track D
# interface registry pin is "0.4.0"; we re-export the string so the
# emitter's identity matches the consumer's.
WS_CONTRACT_VERSION: Final[str] = "0.4.0"

# The 5 LOWERCASE persisted slot keys — the only legal keys in a
# ``signals`` map (mirrors ``$defs.decision_payload.signals.propertyNames``
# in dashboard_ws_message.v0.4.0.json and agent.engines.decision.*). Derived
# from decision.py's SoT (RATIONAL_ENGINES + SENTIENT_ENGINES) so a slot rename
# touches one definition site. The producer does NOT enforce the enum (a
# {str: float} map is accepted) — the WIRE schema is the guard — but the tuple
# is exported so call sites can build a coverage-checked map.
SIGNAL_ENGINE_KEYS: Final[tuple[str, ...]] = (*RATIONAL_ENGINES, *SENTIENT_ENGINES)

# Demo §9 1:30-2:30 PLAYBACK overlay copy. Pinned here so the emitter's
# default activation frame is byte-stable for the captured tape — Track D
# reads this exact string off the wire and renders it.
DEFAULT_LLM_ACTIVATION_NOTE: Final[str] = (
    "β₁ unfrozen — sentient stream wakes up at the Phase 2 boundary."
)


# ---------------------------------------------------------------------------
# Pydantic frame models — mirror $defs in v0.2.0 wire schema.
# ---------------------------------------------------------------------------


class _BaseFrame(BaseModel):
    """Shared base for every WS frame. ``kind`` is set on the subclass."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    seq: Annotated[int, Field(ge=0)]


class VitalsPayload(BaseModel):
    """Mirrors ``$defs.vitals_payload`` (5 required fields)."""

    model_config = ConfigDict(extra="forbid")

    breath: Annotated[float, Field(ge=0.0)]
    bankroll: float
    countdown_s: Annotated[float, Field(ge=0.0)]
    gas_per_min: Annotated[float, Field(ge=0.0)]
    phase: Phase


class VitalsFrame(_BaseFrame):
    kind: Literal["vitals"] = "vitals"
    payload: VitalsPayload


class ThoughtFrame(_BaseFrame):
    kind: Literal["thought"] = "thought"
    text: Annotated[str, Field(min_length=1)]


class DecisionPayload(BaseModel):
    """Mirrors ``$defs.decision_payload`` (v0.3.0).

    v0.3.0 adds three OPTIONAL fields — ``market_id``, ``bet_id`` and
    ``signals`` (a name->score map keyed by the 5 lowercase persisted
    engine names). ``bet_id`` is the executor-minted uuid that equals
    the on-chain ``order_id`` (settlement<->decision correlation key).
    ``signals`` is a flat ``{engine_name: score}`` map; the wire schema
    constrains the keys to the 5-engine enum.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["BET", "NO_BET"]
    side: str | None = None
    size_usd: Annotated[float, Field(ge=0.0)] | None = None
    edge_pct: float | None = None
    kelly_fraction: float | None = None
    # minLength:1 parity with the wire schema's ``decision_payload`` —
    # an empty string is rejected at the PRODUCER (pydantic
    # ValidationError) rather than slipping through to a schema-invalid
    # frame downstream.
    market_id: str | None = Field(default=None, min_length=1)
    bet_id: str | None = Field(default=None, min_length=1)
    signals: dict[str, float] | None = None


class DecisionFrame(_BaseFrame):
    kind: Literal["decision"] = "decision"
    payload: DecisionPayload


class ReflectionFrame(_BaseFrame):
    kind: Literal["reflection"] = "reflection"
    insight: Annotated[str, Field(min_length=1)]


class WeightsPayload(BaseModel):
    """Mirrors ``$defs.weights_payload`` — scalar alpha/beta proxies.

    The wire schema flattens ``alpha`` / ``beta`` to single scalars (the
    α₁ / β₁ canonical proxies) rather than the engine's 3-/2- tuples.
    Producer responsibility: hand the first component of each tuple
    here; engineering parity with ``agent.core.state.Weights`` is
    asserted in :func:`WsEventEmitter._weights_from_state`.
    """

    model_config = ConfigDict(extra="forbid")

    w_r: Annotated[float, Field(ge=0.0, le=1.0)]
    w_s: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(ge=0.0, le=1.0)]
    beta: Annotated[float, Field(ge=0.0, le=1.0)]
    rho: Annotated[float, Field(ge=-1.0, le=1.0)]


class WeightsUpdatedFrame(_BaseFrame):
    kind: Literal["weights_updated"] = "weights_updated"
    weights: WeightsPayload


class LlmActivatedFrame(_BaseFrame):
    kind: Literal["llm_activated"] = "llm_activated"
    note: str | None = None


class PhaseTransitionPayload(BaseModel):
    """Mirrors ``$defs.phase_transition_payload``.

    ``from`` / ``to`` are Python keywords (well, ``from`` is) so the
    fields are declared with explicit aliases. ``populate_by_name=True``
    lets producers pass either spelling — the JSON dump uses the
    schema-mandated ``from`` / ``to`` keys via ``by_alias=True`` in
    :meth:`WsEventEmitter._append`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_phase: Phase = Field(alias="from")
    to_phase: Phase = Field(alias="to")
    reason: str | None = None


class PhaseTransitionFrame(_BaseFrame):
    kind: Literal["phase_transition"] = "phase_transition"
    payload: PhaseTransitionPayload


class DecisionFeedEntry(BaseModel):
    """Mirrors ``$defs.decision_feed_entry`` (v0.3.0).

    One row of the bounded recent-decisions feed. ``id`` is the canonical
    decision id (duplicates dropped by id on the consumer). Every field
    other than ``id`` / ``ts`` / ``action`` is optional and only present
    once Track B has it. v0.3.0 adds ``market_id`` / ``bet_id`` /
    ``signals`` exactly mirroring :class:`DecisionPayload`.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    ts: str
    action: Literal["BET", "NO_BET"]
    side: str | None = None
    size_usd: Annotated[float, Field(ge=0.0)] | None = None
    edge_pct: float | None = None
    kelly_fraction: float | None = None
    result: Literal["WIN", "LOSS", "PENDING"] | None = None
    pnl_usd: float | None = None
    reasoning: str | None = None
    reflection: str | None = None
    # minLength:1 parity with the wire schema's ``decision_feed_entry`` —
    # an empty string is rejected at the PRODUCER (pydantic
    # ValidationError) to match the producer-side guard on
    # :class:`DecisionPayload`.
    market_id: str | None = Field(default=None, min_length=1)
    bet_id: str | None = Field(default=None, min_length=1)
    signals: dict[str, float] | None = None


class DecisionFeedFrame(_BaseFrame):
    kind: Literal["decision_feed"] = "decision_feed"
    entries: list[DecisionFeedEntry]


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


class WsEventEmitter:
    """In-memory producer of dashboard WS frames.

    Frames are appended to :attr:`frames` (a list of plain dicts, each
    already conforming to ``dashboard_ws_message.v0.4.0.json``). The
    emitter does NOT serialise to a socket — that's the sprint_5 WS
    server's job. Tests + the Demo §9 PLAYBACK tape consume
    :attr:`frames` directly.

    The class is intentionally synchronous: every ``emit_*`` returns the
    dict synchronously. Async only buys us pre-emption between frames,
    which the Phase 2 launch smoke doesn't need (the smoke is sequenced
    by the launch orchestrator).
    """

    def __init__(self) -> None:
        self._frames: list[dict[str, Any]] = []
        self._seq = 0

    # ----- Public read surface ---------------------------------------------

    @property
    def frames(self) -> list[dict[str, Any]]:
        """Read-only view of the captured tape.

        Returns a shallow copy of the list (callers iterating can't
        truncate it), but the frame dicts themselves are shared
        references — mutate-in-place would still corrupt the tape.
        """
        return list(self._frames)

    @property
    def llm_activated_emitted(self) -> bool:
        """One-shot latch — True after the first :meth:`emit_llm_activated`.

        Derived from ``_frames`` (no separate state to keep in sync).
        """
        return self._find_llm_activated_frame() is not None

    def _find_llm_activated_frame(self) -> dict[str, Any] | None:
        return next(
            (f for f in self._frames if f["kind"] == "llm_activated"),
            None,
        )

    def kinds_emitted(self) -> set[str]:
        """Distinct ``kind`` values seen so far. Used by the smoke test
        to assert ≥3 kinds in the Demo tape."""
        return {f["kind"] for f in self._frames}

    # ----- Internal --------------------------------------------------------

    def _next_seq(self) -> int:
        n = self._seq
        self._seq += 1
        return n

    def _now_iso(self, ts: datetime | None) -> str:
        return (ts if ts is not None else datetime.now(UTC)).isoformat()

    def _append(self, frame: BaseModel) -> dict[str, Any]:
        # by_alias=True so PhaseTransitionPayload's "from"/"to" survive
        # serialisation (Python keywords forced the field rename).
        # exclude_none=True so UNSET optional fields (e.g. a NO_BET
        # decision's side/size_usd, or v0.3.0's market_id/bet_id/signals)
        # are OMITTED from the wire frame rather than emitted as JSON
        # null — the wire schema declares those optionals as concrete
        # types (string/object) and rejects null, and additionalProperties
        # is false on the payload objects.
        body: dict[str, Any] = frame.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        self._frames.append(body)
        return body

    # ----- Emit methods ----------------------------------------------------

    def emit_vitals(
        self,
        *,
        breath: float,
        bankroll: float,
        countdown_s: float,
        gas_per_min: float,
        phase: Phase,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``vitals`` frame (PRD §8 right-rail vitals strip)."""
        return self._append(
            VitalsFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                payload=VitalsPayload(
                    breath=breath,
                    bankroll=bankroll,
                    countdown_s=countdown_s,
                    gas_per_min=gas_per_min,
                    phase=phase,
                ),
            )
        )

    def emit_thought(
        self,
        *,
        text: str,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``thought`` frame (Consciousness Stream diary line)."""
        return self._append(
            ThoughtFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                text=text,
            )
        )

    def emit_decision(
        self,
        *,
        action: Literal["BET", "NO_BET"],
        side: str | None = None,
        size_usd: float | None = None,
        edge_pct: float | None = None,
        kelly_fraction: float | None = None,
        market_id: str | None = None,
        bet_id: str | None = None,
        signals: dict[str, float] | None = None,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``decision`` frame.

        v0.3.0 adds three OPTIONAL fields: ``market_id`` (Polymarket
        market id), ``bet_id`` (executor-minted uuid == on-chain
        order_id; the settlement<->decision correlation key) and
        ``signals`` (a flat ``{engine_name: score}`` map keyed by the 5
        lowercase persisted engine names). Unset fields are omitted from
        the wire frame (``exclude_none``).
        """
        return self._append(
            DecisionFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                payload=DecisionPayload(
                    action=action,
                    side=side,
                    size_usd=size_usd,
                    edge_pct=edge_pct,
                    kelly_fraction=kelly_fraction,
                    market_id=market_id,
                    bet_id=bet_id,
                    signals=signals,
                ),
            )
        )

    def emit_reflection(
        self,
        *,
        insight: str,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``reflection`` frame (post-tick reflection)."""
        return self._append(
            ReflectionFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                insight=insight,
            )
        )

    def emit_weights_updated(
        self,
        *,
        w_r: float,
        w_s: float,
        alpha: float,
        beta: float,
        rho: float,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``weights_updated`` frame.

        Note: the wire schema's ``alpha`` / ``beta`` are scalar proxies
        (α₁ / β₁ canonical). Producer flattens its tuple-shaped state
        BEFORE calling this method.
        """
        return self._append(
            WeightsUpdatedFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                weights=WeightsPayload(
                    w_r=w_r,
                    w_s=w_s,
                    alpha=alpha,
                    beta=beta,
                    rho=rho,
                ),
            )
        )

    def emit_llm_activated(
        self,
        *,
        note: str | None = DEFAULT_LLM_ACTIVATION_NOTE,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit the one-shot ``llm_activated`` frame.

        Idempotent — a second call returns the original frame and does
        NOT append a duplicate. The dashboard's overlay rule is
        "render once per session"; emitting twice would race the
        ``sessionStorage`` handshake key (T-D-003 consumer note).
        """
        existing = self._find_llm_activated_frame()
        if existing is not None:
            return existing
        return self._append(
            LlmActivatedFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                note=note,
            )
        )

    def emit_phase_transition(
        self,
        *,
        from_phase: Phase,
        to_phase: Phase,
        reason: str | None = None,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``phase_transition`` frame (drives PhaseTransitionBanner)."""
        payload = PhaseTransitionPayload.model_validate(
            {"from": from_phase, "to": to_phase, "reason": reason}
        )
        return self._append(
            PhaseTransitionFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                payload=payload,
            )
        )

    def emit_decision_feed(
        self,
        *,
        entries: list[DecisionFeedEntry | dict[str, Any]],
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Emit a ``decision_feed`` frame (v0.3.0 — first Python emitter).

        ``entries`` is the bounded recent-decisions list the dashboard
        merges by ``id`` (newest-first). Each entry may be a
        :class:`DecisionFeedEntry` or a plain dict that validates into
        one; v0.3.0 entries may carry the new ``market_id`` / ``bet_id``
        / ``signals`` fields. Unset optionals are omitted from the wire
        frame (``exclude_none`` in :meth:`_append`).

        NOTE: the producer accepts an arbitrary ``{str: float}`` signals
        map (it does NOT enforce the 5-engine enum) — the WIRE schema is
        the guard, so a non-enum key produces a frame the consumer's
        validator rejects.
        """
        validated = [
            e if isinstance(e, DecisionFeedEntry) else DecisionFeedEntry.model_validate(e)
            for e in entries
        ]
        return self._append(
            DecisionFeedFrame(
                ts=self._now_iso(ts),
                seq=self._next_seq(),
                entries=validated,
            )
        )


__all__ = [
    "DEFAULT_LLM_ACTIVATION_NOTE",
    "SIGNAL_ENGINE_KEYS",
    "WS_CONTRACT_VERSION",
    "DecisionFeedEntry",
    "DecisionPayload",
    "PhaseTransitionPayload",
    "VitalsPayload",
    "WeightsPayload",
    "WsEventEmitter",
]
