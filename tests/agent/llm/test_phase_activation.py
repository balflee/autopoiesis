"""PhaseActivationEmitter tests — one-shot D11 'LLM activated' event.

Brief acceptance criterion (T-B-006):

    ``_phase_activation.py`` writes to memory_bank via the existing
    :class:`MemoryBank` atomic temp+rename API — NO new disk-write
    primitive.

Tests verify:

* The emitter writes to ``observations_dir`` via the same atomic
  temp+rename pattern :meth:`MemoryBank.write_tick` uses.
* Subsequent ``emit`` calls are no-ops (idempotent).
* The persisted blob is JSON-parseable + round-trips through
  :meth:`load`.
* Invalid phase values are rejected at construction time.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.core.memory_bank import MemoryBank
from agent.llm._phase_activation import (
    ACTIVATION_FILENAME,
    PhaseActivationEmitter,
    PhaseActivationEvent,
)


def _bank(tmp_path: Path) -> MemoryBank:
    bank = MemoryBank(tmp_path / "memory_bank")
    bank.ensure_layout()
    return bank


def test_emit_writes_activation_file(tmp_path: Path) -> None:
    """Happy path — emit produces an event and writes the file."""
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)
    fixed = datetime(2026, 5, 23, 14, 0, 0, tzinfo=UTC)

    event = emitter.emit(
        phase=2,
        model="gemini-3.1-flash-lite",
        ancestor_id=None,
        now=fixed,
    )
    assert isinstance(event, PhaseActivationEvent)
    assert event.phase == 2
    assert event.model == "gemini-3.1-flash-lite"
    assert event.activated_at == fixed.isoformat()

    activation_file = bank.observations_dir / ACTIVATION_FILENAME
    assert activation_file.exists()
    payload = json.loads(activation_file.read_text(encoding="utf-8"))
    assert payload["phase"] == 2
    assert payload["model"] == "gemini-3.1-flash-lite"
    assert payload["activated_at"] == fixed.isoformat()


def test_emit_is_one_shot_idempotent(tmp_path: Path) -> None:
    """Brief: 'one-shot llm_activated event emitter'.

    A second ``emit`` after the file already exists is a no-op — the
    persisted event is returned untouched. Dashboard / V2-boot expect
    a single activation per agent lifetime.
    """
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)
    t1 = datetime(2026, 5, 23, 14, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 24, 9, 0, 0, tzinfo=UTC)

    first = emitter.emit(phase=2, model="m1", now=t1)
    second = emitter.emit(phase=2, model="m2-different", now=t2)
    assert first == second
    assert second.model == "m1"  # second emit ignored — m2 NOT written
    assert second.activated_at == t1.isoformat()


def test_already_emitted_and_load(tmp_path: Path) -> None:
    """``already_emitted`` and ``load`` reflect on-disk state."""
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)
    assert emitter.already_emitted() is False
    assert emitter.load() is None

    fixed = datetime(2026, 5, 23, tzinfo=UTC)
    written = emitter.emit(phase=2, model="m1", now=fixed)
    assert emitter.already_emitted() is True
    loaded = emitter.load()
    assert loaded == written


def test_emit_uses_atomic_temp_rename(tmp_path: Path) -> None:
    """The atomic write pattern leaves no orphan ``.tmp`` after success
    and cleans up an orphan from a prior crash before writing."""
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)

    # Drop an orphan tmp first; the writer must sweep it.
    orphan = bank.observations_dir / f".{ACTIVATION_FILENAME}.tmp"
    bank.observations_dir.mkdir(parents=True, exist_ok=True)
    orphan.write_text("stale", encoding="utf-8")

    emitter.emit(phase=2, model="m1", now=datetime(2026, 5, 23, tzinfo=UTC))

    assert not orphan.exists()
    assert (bank.observations_dir / ACTIVATION_FILENAME).exists()


def test_invalid_phase_rejected(tmp_path: Path) -> None:
    """The PRD §3 enum is 1..4; anything else is misuse."""
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)
    with pytest.raises(ValueError, match="phase"):
        emitter.emit(phase=0, model="m1")
    with pytest.raises(ValueError, match="phase"):
        emitter.emit(phase=5, model="m1")


def test_invalid_phase_rejected_even_after_first_emit(tmp_path: Path) -> None:
    """Phase validation must precede the idempotency check.

    Without this ordering, a buggy second call with ``phase=99`` would
    silently return the existing event instead of raising — masking
    the caller bug.
    """
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)
    emitter.emit(phase=2, model="m1", now=datetime(2026, 5, 23, tzinfo=UTC))
    with pytest.raises(ValueError, match="phase"):
        emitter.emit(phase=99, model="m1")


def test_ancestor_id_round_trips(tmp_path: Path) -> None:
    """V2-boot wires ancestor_id so the new agent can chain activations."""
    bank = _bank(tmp_path)
    emitter = PhaseActivationEmitter(memory_bank=bank)
    event = emitter.emit(
        phase=2,
        model="gemini-3.1-flash-lite",
        ancestor_id="genesis_v0",
        now=datetime(2026, 5, 23, tzinfo=UTC),
    )
    assert event.ancestor_id == "genesis_v0"
    loaded = emitter.load()
    assert loaded is not None
    assert loaded.ancestor_id == "genesis_v0"
