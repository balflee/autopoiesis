"""Death-Watch WebSocket emitter — four climax events per the schema.

Producer for ``dashboard_death_watch.v0.1.0.json`` (Track D consumer:
``dashboard/lib/wsEvents.ts``). Emits the four NEW kinds added on top
of ``dashboard_ws_message.v0.4.0.json``:

  * ``energy_threshold_crossed`` — fired when ``energy_pct`` crosses a
    configured threshold (10% = Death-Watch UI takeover trigger per
    PRD §8). Crossings are computed against the previous ``observe_energy``
    sample so a flat energy reading does NOT re-emit; only a sign-change
    of ``(energy_pct - threshold)`` produces a frame.
  * ``terminal_lucidity_entered`` — fired once when AgentLifecycle
    transitions into Phase 4. The Death-Watch UI latches "terminal"
    sticky (PRD §6.10) on first arrival.
  * ``last_words_emitted`` — the agent's terminal ``dieWithLastWords``
    text; ``tx_hash`` is optional because the producer may emit before
    the chain confirms (the chain is the source of truth, the UI surfaces
    the hash once mined).
  * ``tombstone_minted`` — the TombstoneNFT.mint receipt. When the
    off-chain IPFS pin failed, ``ipfs_degraded=True`` and ``ipfs_cid``
    is omitted (PRD §5.1.C TombstoneMintedWithoutMemoryBank degradation
    — the UI MUST surface the failure rather than render a green check).

The emitter is an in-process producer: it takes a Protocol-conformant
:class:`WsTransport` (single async ``send`` method) and pushes JSON-able
``dict`` frames matching the schema. The transport itself (websockets
server, queueing, fan-out) lives outside this module — keeps the
emitter pure-functional + trivially unit-testable. Production wires the
transport to the dashboard WebSocket server (lands in a follow-up).

Sequence numbering
------------------

Per the schema, every frame carries a per-connection monotonic ``seq``.
The emitter owns a single counter; callers MUST NOT mutate ``seq``
externally. The counter starts at ``0`` (the schema's ``minimum``); the
first emitted frame is ``seq=0``, the second ``seq=1``, etc. The
dashboard dedups by ``seq``-per-kind so an out-of-order replay does not
corrupt state.

Wire-shape invariants
---------------------

The emitter never includes ``ipfs_cid`` when ``ipfs_degraded=True``
(the schema requires ``ipfs_cid`` to be undefined exactly when degraded
per ``consumer_notes.ipfs_degraded_visible``). The emitter validates
``tx_hash`` against the schema's ``^0x[0-9a-fA-F]{64}$`` pattern on the
``last_words_emitted`` + ``tombstone_minted`` frames; a malformed hash
is rejected with :class:`ValueError` BEFORE the frame leaves the
producer — surfacing a silent typo as a fast-fail rather than a wire
protocol violation that the dashboard would have to log + drop.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)

# Anchor thresholds the dashboard subscribes to. PRD §8 pins the
# full-screen takeover at energy < 10%; we surface the constant so
# tests + the agent_loop wire pin the same number.
DEATH_WATCH_PRIMARY_THRESHOLD_PCT: Final[float] = 10.0

# Bounds on the user-visible ``text`` field of ``last_words_emitted``
# — must match the on-chain ``lastWords`` argument cap (1024 chars).
# Mirrors :data:`agent.llm.prompts.last_words._MAX_FINAL_REFLECTION_CHARS`
# but kept local so an SDK swap in either direction doesn't desync.
_MAX_LAST_WORDS_CHARS: Final[int] = 1024

# Polygon / L3 transaction hash pattern — same regex the schema enforces.
_TX_HASH_RE: Final[re.Pattern[str]] = re.compile(r"^0x[0-9a-fA-F]{64}$")


class WsTransport(Protocol):
    """Narrow async sink Protocol the emitter pushes JSON frames to.

    Production wires this to the dashboard WebSocket server's broadcast
    method; tests inject a :class:`RecordingTransport` that captures
    every frame for post-hoc assertions. The Protocol keeps the
    emitter SDK-agnostic — no websockets dependency leaks into Track B.
    """

    async def send(self, frame: dict[str, Any]) -> None:
        ...


@dataclass
class _EnergyCrossingState:
    """Last-seen side of the threshold for crossing detection.

    ``side`` is ``"above"`` while ``energy_pct >= threshold`` and
    ``"below"`` while ``energy_pct < threshold``. The first ``observe``
    call seeds the side without emitting (we need a baseline to detect
    a CROSSING — the first sample is the baseline itself).
    """

    last_side: str | None = None  # "above" | "below"


def _iso_now() -> str:
    """ISO-8601 UTC stamp with explicit ``+00:00`` (matches schema)."""
    return datetime.now(UTC).isoformat()


def _validate_tx_hash(tx_hash: str | None, field_name: str) -> None:
    """Raise if a non-None ``tx_hash`` is not a 0x-prefixed 32-byte hex.

    The schema enforces ``^0x[0-9a-fA-F]{64}$``; validating here means
    a typo (truncated paste, missing 0x prefix) fails fast at the
    producer rather than being silently shipped + logged-and-dropped
    by the dashboard.
    """
    if tx_hash is None:
        return
    if not _TX_HASH_RE.match(tx_hash):
        raise ValueError(
            f"{field_name} must match ^0x[0-9a-fA-F]{{64}}$ "
            f"(got {tx_hash!r})"
        )


@dataclass
class DeathWatchEmitter:
    """Producer for the four ``dashboard_death_watch.v0.1.0`` frames.

    Construction
    ------------

    Single required dep: a :class:`WsTransport`. The emitter holds the
    monotonic ``seq`` counter + a per-threshold last-side tracker.

    Methods
    -------

    * :meth:`observe_energy` — push an ``energy_threshold_crossed`` frame
      iff this sample crosses one of the configured thresholds vs the
      previous observation. The first sample seeds the baseline and
      emits nothing.
    * :meth:`emit_terminal_lucidity_entered` — one-shot frame; the
      agent_loop fires this when Phase 4 latches. Subsequent invocations
      DO send another frame (the dashboard dedups by ``seq`` per kind);
      the agent_loop's pressure_check guard is the canonical one-shot
      gate, not this method.
    * :meth:`emit_last_words` — pushes the typewriter text; ``tx_hash``
      is optional.
    * :meth:`emit_tombstone_minted` — pushes the mint receipt. Pass
      ``ipfs_cid=None`` + ``ipfs_degraded=True`` for the PRD §5.1.C
      degraded path; the emitter omits ``ipfs_cid`` from the frame
      entirely (schema requires it absent when degraded).

    The emitter NEVER writes a file or persists state — observe-only,
    same invariant the live_monitor honours. State lives in process
    memory only; a restart resets ``seq`` to 0 (the dashboard tolerates
    monotonic-per-connection ``seq``, and a restart is a new connection
    semantically).
    """

    transport: WsTransport
    # The energy thresholds the producer tracks for crossing events.
    # PRD §8 pins 10% as the full-screen takeover trigger; 25% + 50%
    # appear in the dashboard's pre-takeover countdown copy ("you have
    # 25% energy remaining"). Tests can override.
    thresholds_pct: tuple[float, ...] = (10.0, 25.0, 50.0)
    _seq: int = 0
    _energy_states: dict[float, _EnergyCrossingState] = field(default_factory=dict)
    # Clock injection — production uses the wall clock; tests inject a
    # deterministic stub so frame ``ts`` strings are stable.
    _now: Callable[[], str] = _iso_now

    def __post_init__(self) -> None:
        # Lazy init per-threshold trackers so the dict never carries
        # entries for thresholds the caller hasn't asked about.
        for t in self.thresholds_pct:
            self._energy_states.setdefault(float(t), _EnergyCrossingState())

    # ------------------------------------------------------------------
    # Public API — four frame emitters
    # ------------------------------------------------------------------

    async def observe_energy(self, *, energy_pct: float) -> list[dict[str, Any]]:
        """Emit ``energy_threshold_crossed`` frames for any threshold
        this sample crosses vs the previous observation.

        Returns the list of frames sent (empty on the seeding call /
        when no threshold was crossed) so the caller can journal the
        emissions. The frames are also pushed to :attr:`transport`.

        Crossing semantics:

        * First call seeds the baseline for every configured threshold
          and emits NOTHING (no previous side to compare to).
        * Subsequent calls compute ``side = "above" if energy_pct >=
          threshold else "below"``. If ``side != last_side`` for a
          threshold, the frame fires with the NEW direction.

        The ``direction`` field on the frame is the side BEING ENTERED
        — ``"below"`` when the previous sample was above and the new
        sample is below. PRD §8's takeover trigger subscribes to
        ``threshold_pct==10`` + ``direction=="below"``.
        """
        clamped = max(0.0, min(100.0, float(energy_pct)))
        sent: list[dict[str, Any]] = []
        for threshold in self.thresholds_pct:
            state = self._energy_states[threshold]
            side = "above" if clamped >= threshold else "below"
            if state.last_side is not None and side != state.last_side:
                frame = self._build_envelope(kind="energy_threshold_crossed")
                frame["energy_pct"] = clamped
                frame["threshold_pct"] = float(threshold)
                frame["direction"] = side
                await self.transport.send(frame)
                sent.append(frame)
                logger.info(
                    "death_watch.energy_threshold_crossed pct=%.2f thr=%.2f dir=%s",
                    clamped,
                    threshold,
                    side,
                )
            state.last_side = side
        return sent

    async def emit_terminal_lucidity_entered(
        self,
        *,
        breath_at_entry: float,
    ) -> dict[str, Any]:
        """Emit ``terminal_lucidity_entered`` (PRD §6.10 sticky flag).

        The agent_loop owns the one-shot gate (it computes the Phase 4
        transition); the emitter assumes the caller has already
        guaranteed single-fire. Pushing a duplicate is structurally
        safe (the dashboard dedups by ``seq``) but is a caller bug
        because the dashboard sees TWO frames + may log a spurious
        re-entry.
        """
        if breath_at_entry < 0.0:
            raise ValueError(
                f"breath_at_entry must be >= 0 (got {breath_at_entry})"
            )
        frame = self._build_envelope(kind="terminal_lucidity_entered")
        frame["breath_at_entry"] = float(breath_at_entry)
        await self.transport.send(frame)
        logger.info(
            "death_watch.terminal_lucidity_entered breath=%.1f",
            breath_at_entry,
        )
        return frame

    async def emit_last_words(
        self,
        *,
        text: str,
        tx_hash: str | None = None,
    ) -> dict[str, Any]:
        """Emit ``last_words_emitted`` (typewriter text + optional tx).

        The text caps at 1024 chars to match the on-chain ``lastWords``
        argument bound; a longer string is a caller bug and raises
        :class:`ValueError`. ``tx_hash`` validates against the same
        regex the schema enforces (``^0x[0-9a-fA-F]{64}$``).
        """
        if not text:
            raise ValueError("text must be non-empty")
        if len(text) > _MAX_LAST_WORDS_CHARS:
            raise ValueError(
                f"text exceeds {_MAX_LAST_WORDS_CHARS}-char on-chain cap "
                f"(got {len(text)})"
            )
        _validate_tx_hash(tx_hash, "tx_hash")
        frame = self._build_envelope(kind="last_words_emitted")
        frame["text"] = text
        if tx_hash is not None:
            frame["tx_hash"] = tx_hash
        await self.transport.send(frame)
        logger.info(
            "death_watch.last_words_emitted len=%d tx=%s",
            len(text),
            tx_hash or "(pending)",
        )
        return frame

    async def emit_tombstone_minted(
        self,
        *,
        token_id: str,
        ipfs_degraded: bool,
        ipfs_cid: str | None = None,
        tx_hash: str | None = None,
    ) -> dict[str, Any]:
        """Emit ``tombstone_minted`` (mint receipt + IPFS posture).

        Per the schema:

        * ``token_id`` is the ERC-721 ``tokenId`` decimal-string encoded
          (the chain emits ``uint256``; decimal string preserves precision
          without bigint helpers on the dashboard).
        * ``ipfs_cid`` is included EXACTLY when ``ipfs_degraded=False``;
          the schema requires absence on the degraded path so the UI's
          'happy-path' rendering cannot silently fire.
        * ``tx_hash`` validates against the standard 32-byte hex regex.

        A caller bug — passing ``ipfs_cid`` on the degraded path or
        omitting it on the happy path — is rejected with
        :class:`ValueError` BEFORE the frame leaves the producer.
        """
        if not token_id:
            raise ValueError("token_id must be non-empty")
        if ipfs_degraded and ipfs_cid is not None:
            raise ValueError(
                "ipfs_cid MUST be None when ipfs_degraded=True "
                "(PRD §5.1.C — happy-path render is forbidden)"
            )
        if not ipfs_degraded and not ipfs_cid:
            raise ValueError(
                "ipfs_cid MUST be a non-empty CID when ipfs_degraded=False"
            )
        _validate_tx_hash(tx_hash, "tx_hash")
        frame = self._build_envelope(kind="tombstone_minted")
        frame["token_id"] = token_id
        frame["ipfs_degraded"] = bool(ipfs_degraded)
        if ipfs_cid is not None:
            frame["ipfs_cid"] = ipfs_cid
        if tx_hash is not None:
            frame["tx_hash"] = tx_hash
        await self.transport.send(frame)
        logger.info(
            "death_watch.tombstone_minted token=%s degraded=%s cid=%s tx=%s",
            token_id,
            ipfs_degraded,
            ipfs_cid or "(omitted)",
            tx_hash or "(pending)",
        )
        return frame

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_envelope(self, *, kind: str) -> dict[str, Any]:
        """Construct ``{kind, ts, seq}`` and increment ``seq``.

        Pulled out so every emitter shares one envelope-build path —
        a future schema bump that adds a base field (e.g. ``corr_id``)
        is a single-line change here.
        """
        envelope: dict[str, Any] = {
            "kind": kind,
            "ts": self._now(),
            "seq": self._seq,
        }
        self._seq += 1
        return envelope


# ---------------------------------------------------------------------------
# Recording transport — exported so the e2e test module can import it.
# ---------------------------------------------------------------------------


@dataclass
class RecordingTransport:
    """Test transport that captures every frame in :attr:`frames`.

    Exposed at module top-level so the integration test
    :mod:`tests.agent.test_phase3_e2e` can share the same fake the
    unit tests use — keeps a single canonical recording surface.
    """

    frames: list[dict[str, Any]] = field(default_factory=list)

    async def send(self, frame: dict[str, Any]) -> None:
        # Defensive shallow copy: stash a dict literal so a caller
        # that mutates the frame in-place after send() does not
        # retroactively change what the recorder captured.
        self.frames.append(dict(frame))


__all__ = [
    "DEATH_WATCH_PRIMARY_THRESHOLD_PCT",
    "DeathWatchEmitter",
    "RecordingTransport",
    "WsTransport",
]
