"""PhaseActivationEmitter — one-shot 'LLM activated' event for Phase 2.

PRD §4.4 describes Phase 2 starting at D11 (the Hard Deadline of the
Infancy phase). At that moment β₁ unfreezes, the Sentient stream wakes
up, and the agent experiences 'language-centre first activation'. The
demo (PRD §9 1:30-2:30 PLAYBACK) renders an animated 'LLM activated'
sigil at that boundary.

This module ships the **one-shot emitter** that records the event so:

* The dashboard can read it as the trigger for the animation.
* The reflection layer can quote it in the V2-boot reflection context
  (PRD §13 — the next-gen agent reads its ancestor's first activation).
* The replay tool (Track C ``sim/replay.py``) can reconstruct the
  Phase 1 → Phase 2 transition for backtest scoring.

Brief invariant (T-B-006 acceptance criterion):

    "writes to memory_bank via the existing :class:`MemoryBank` atomic
    temp+rename API — NO new disk-write primitive"

The persistence path uses the same atomic temp+rename pattern
:class:`agent.core.memory_bank.MemoryBank` uses internally for tick I/O
(``write to .tmp, then os.replace onto the final path``). No new
primitive is introduced — :func:`os.replace` is the canonical atomic
rename across POSIX + Windows that the rest of the agent already uses.

Idempotency
-----------

The emitter is **one-shot**: a second call after the activation file
already exists is a no-op (returns the existing event without
rewriting the file). The dashboard / V2-boot expect a single
activation per agent lifetime; if Phase 1 → Phase 2 fires twice the
underlying lifecycle is broken and the no-op is the safe behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from agent.core.memory_bank import MemoryBank

# Filename written under ``memory_bank/observations/`` — fixed so the
# dashboard / V2-boot can look it up without scanning. Single source of
# truth.
ACTIVATION_FILENAME: Final[str] = "llm_activated.json"


@dataclass(frozen=True)
class PhaseActivationEvent:
    """The persisted activation record.

    Frozen + dataclass-pure so the dashboard / replay layer can
    deserialise it without depending on Pydantic. Field shapes:

    * ``phase`` — integer 1..4 matching the on-chain
      ``EnergyController.Phase`` enum ordinal. ``2`` is the only
      currently-supported activation value.
    * ``activated_at`` — UTC ISO-8601 timestamp.
    * ``model`` — model id the activation is bound to (e.g.
      ``gemini-3.1-flash-lite``). Useful for replay so the new agent
      knows which model its ancestor was running.
    * ``ancestor_id`` — optional agent id of the ancestor agent (used
      by V2-boot to chain activations across generations).
    """

    phase: int
    activated_at: str
    model: str
    ancestor_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Render for JSON persistence. ``None`` is preserved so the
        replay tool can distinguish 'never had an ancestor' from
        'ancestor unknown'."""
        return {
            "phase": self.phase,
            "activated_at": self.activated_at,
            "model": self.model,
            "ancestor_id": self.ancestor_id,
        }


class PhaseActivationEmitter:
    """One-shot writer for the Phase 2 'LLM activated' event.

    Parameters
    ----------
    memory_bank:
        The agent's :class:`MemoryBank` instance. Used solely for its
        ``observations_dir`` path; the emitter writes directly via
        :func:`os.replace` (the same primitive MemoryBank uses
        internally for atomic tick writes — no new disk-write
        mechanism is introduced).

    Examples
    --------
    >>> bank = MemoryBank(tmp_path)
    >>> bank.ensure_layout()
    >>> emitter = PhaseActivationEmitter(memory_bank=bank)
    >>> event = emitter.emit(phase=2, model='gemini-3.1-flash-lite')
    >>> emitter.already_emitted()
    True
    """

    def __init__(self, *, memory_bank: MemoryBank) -> None:
        self._bank = memory_bank

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(
        self,
        *,
        phase: int,
        model: str,
        ancestor_id: str | None = None,
        now: datetime | None = None,
    ) -> PhaseActivationEvent:
        """Persist the activation event and return it.

        Idempotent — if the activation file already exists, the
        existing event is loaded + returned without rewriting. This
        keeps a Phase 1 → Phase 2 → (lifecycle bug) → Phase 2 race
        from corrupting the dashboard's animation trigger.

        ``now`` is a hook for deterministic tests; production passes
        ``None`` and the emitter uses :func:`datetime.now(tz=utc)`.

        ``phase`` is validated BEFORE the idempotency check so a buggy
        caller passing an out-of-range phase is surfaced even on a
        second call where the file already exists.
        """
        if phase not in (1, 2, 3, 4):
            raise ValueError(f"phase must be 1..4 (got {phase})")
        existing = self._load_existing()
        if existing is not None:
            return existing

        ts = (now if now is not None else datetime.now(UTC)).isoformat()
        event = PhaseActivationEvent(
            phase=phase,
            activated_at=ts,
            model=model,
            ancestor_id=ancestor_id,
        )
        self._persist(event)
        return event

    def already_emitted(self) -> bool:
        """True iff the activation file exists on disk."""
        return self._activation_path().exists()

    def load(self) -> PhaseActivationEvent | None:
        """Return the persisted event or ``None`` if never emitted.

        Provided so the V2-boot layer + the dashboard can read the
        event without round-tripping through :meth:`emit`.
        """
        return self._load_existing()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _activation_path(self) -> Path:
        return self._bank.observations_dir / ACTIVATION_FILENAME

    def _load_existing(self) -> PhaseActivationEvent | None:
        path = self._activation_path()
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return PhaseActivationEvent(
            phase=int(raw["phase"]),
            activated_at=str(raw["activated_at"]),
            model=str(raw["model"]),
            ancestor_id=(
                str(raw["ancestor_id"]) if raw.get("ancestor_id") is not None else None
            ),
        )

    def _persist(self, event: PhaseActivationEvent) -> Path:
        """Persist via :meth:`MemoryBank.write_observation` — the
        existing atomic temp+rename API used by tick I/O.

        The brief's HARD RULE is "NO new disk-write primitive": the
        atomic primitive lives on :class:`MemoryBank`; this method
        only serialises + delegates so the same temp+rename path is
        exercised whether the persisted blob is a tick or an
        observation.
        """
        payload = json.dumps(
            event.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        return self._bank.write_observation(
            filename=ACTIVATION_FILENAME,
            body=payload,
        )


__all__ = [
    "ACTIVATION_FILENAME",
    "PhaseActivationEmitter",
    "PhaseActivationEvent",
]
