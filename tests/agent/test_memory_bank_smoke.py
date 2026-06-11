"""MemoryBank smoke tests.

Covers the acceptance criterion that ``write_tick`` performs an atomic
temp + rename onto ``.agent_state/memory_bank/ticks/tick_<N>.json``.
Plus: round-trip read + schema-version round-trip + the contract that
already-written ticks are immutable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.memory_bank import MemoryBank
from agent.core.memory_bank_migrations import CURRENT_VERSION, upgrade
from agent.core.state import (
    Action,
    ActionKind,
    Phase,
    TickPayload,
    Vitals,
    Weights,
)


def _sample_payload(tick: int = 1) -> TickPayload:
    """Minimal valid payload — used across the smoke tests so each test
    is small and the canonical fixture is one definition."""
    return TickPayload(
        tick=tick,
        ts="2026-05-19T20:00:00Z",
        agent_id="genesis_v1",
        phase=Phase.PHASE_1_INFANCY,
        vitals=Vitals(breath=100.0, bankroll_usd=1000.0, phase_age_days=0.0),
        weights=Weights(
            w_r=0.6,
            w_s=0.4,
            alpha=[0.34, 0.33, 0.33],
            beta=[0.5, 0.5],
            rho=0.5,
        ),
        action=Action(kind=ActionKind.NO_BET, no_bet_reason="scaffolding-tick"),
        narrative="Tick 1: passed (scaffold).",
    )


def test_write_tick_atomic_temp_then_rename(tmp_path: Path) -> None:
    """The writer MUST stage to ``.tick_<N>.json.tmp`` and rename onto
    the final ``tick_<N>.json`` so a crash mid-write never leaves a
    partial tick file. Verified by:

    * after the call, only the final file exists (no stray .tmp left)
    * the final file is fully valid JSON
    * the staging filename pattern (the leading dot + .tmp suffix) does
      not collide with the final filename pattern recognised by
      ``list_ticks``.
    """
    mb = MemoryBank(root=tmp_path / "mb")
    payload = _sample_payload(tick=7)
    final = mb.write_tick(payload)

    # Final file exists at the canonical path
    assert final == mb.ticks_dir / "tick_7.json"
    assert final.is_file()

    # No orphan .tmp left in the ticks directory
    leftovers = [p.name for p in mb.ticks_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"unexpected tmp files: {leftovers}"

    # The written file is real, parseable JSON conforming to schema
    data = json.loads(final.read_text(encoding="utf-8"))
    assert data["tick"] == 7
    assert data["schema_version"] == CURRENT_VERSION
    assert data["agent_id"] == "genesis_v1"

    # The staging filename pattern (leading dot) is rejected by list_ticks
    assert mb.list_ticks() == [7]


def test_write_tick_then_read_round_trip(tmp_path: Path) -> None:
    """Round-trip equality on every meaningful field."""
    mb = MemoryBank(root=tmp_path / "mb")
    original = _sample_payload(tick=42)
    mb.write_tick(original)
    loaded = mb.read_tick(42)

    assert loaded.tick == original.tick
    assert loaded.ts == original.ts
    assert loaded.phase == original.phase
    assert loaded.vitals == original.vitals
    assert loaded.weights == original.weights
    assert loaded.action == original.action
    assert loaded.narrative == original.narrative
    assert loaded.schema_version == CURRENT_VERSION


def test_write_tick_refuses_duplicate(tmp_path: Path) -> None:
    """Ticks are append-only — writing the same tick number twice must
    raise rather than silently overwrite."""
    mb = MemoryBank(root=tmp_path / "mb")
    mb.write_tick(_sample_payload(tick=3))
    with pytest.raises(ValueError, match="already exists"):
        mb.write_tick(_sample_payload(tick=3))


def test_summarise_on_empty_bank_returns_count_zero(tmp_path: Path) -> None:
    """Surface used by ``agent inspect-memory-bank``; must not raise on
    a fresh root."""
    mb = MemoryBank(root=tmp_path / "mb")
    summary = mb.summarise(last=5)
    assert summary["count"] == 0
    assert summary["latest_tick"] is None
    assert summary["schema_version"] == CURRENT_VERSION


def test_list_ticks_sorted_numerically_not_lexically(tmp_path: Path) -> None:
    """Contract writer_notes warns consumers MUST sort numerically; verify
    list_ticks does the right thing for ticks 2 and 11 (where lexical
    sort would reverse them)."""
    mb = MemoryBank(root=tmp_path / "mb")
    mb.write_tick(_sample_payload(tick=2))
    mb.write_tick(_sample_payload(tick=11))
    assert mb.list_ticks() == [2, 11]


def test_last_k_ticks_returns_most_recent_in_ascending_order(
    tmp_path: Path,
) -> None:
    """V2 boot reads the last K=50 ticks of the ancestor and injects them
    ascending into the new agent's reflection context (PRD §13). Verify
    the K=2 case here so the slice + sort logic is locked."""
    mb = MemoryBank(root=tmp_path / "mb")
    for t in (1, 2, 3, 4):
        mb.write_tick(_sample_payload(tick=t))
    last_two = mb.last_k_ticks(2)
    assert [p.tick for p in last_two] == [3, 4]


def test_migration_passthrough_on_current_version(tmp_path: Path) -> None:
    """Sprint_1 has no migrations registered; payloads at CURRENT_VERSION
    pass through unchanged."""
    raw = _sample_payload(tick=5).model_dump(mode="json")
    upgraded = upgrade(raw)
    assert upgraded == raw


def test_migration_rejects_newer_major(tmp_path: Path) -> None:
    """Forward-only policy: a payload tagged with a newer version than
    the runtime MUST fail loud (PRD §13 / TECHNICAL_PLAN §4.6)."""
    raw = _sample_payload(tick=6).model_dump(mode="json")
    raw["schema_version"] = "2.0.0"
    with pytest.raises(ValueError, match="newer than runtime"):
        upgrade(raw)


def test_schema_file_validates_written_tick_payload(tmp_path: Path) -> None:
    """The published JSON Schema MUST accept the in-package writer's
    output. Catches drift between agent.core.state.TickPayload and
    .dev/contracts/memory_bank_schema.v1.0.0.json."""
    from jsonschema import Draft202012Validator

    schema_path = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / "core"
        / "memory_bank_schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    mb = MemoryBank(root=tmp_path / "mb")
    final = mb.write_tick(_sample_payload(tick=99))
    body = json.loads(final.read_text(encoding="utf-8"))
    errors = sorted(validator.iter_errors(body), key=lambda e: e.path)
    assert errors == [], f"written tick fails published schema: {errors}"


def test_in_package_schema_matches_contracts_canonical() -> None:
    """The in-package ``agent/core/memory_bank_schema.json`` MUST be the
    exact bytes of the canonical contract at
    ``.dev/contracts/memory_bank_schema.v<version>.json`` (resolved via
    ``.dev/contracts/_registry.json``). A drift here would silently
    invalidate the producer/consumer boundary — Track C replay and
    Track D playback both read the canonical file."""
    root = Path(__file__).resolve().parents[2]
    in_package = root / "agent" / "core" / "memory_bank_schema.json"
    registry = json.loads(
        (root / ".dev" / "contracts" / "_registry.json").read_text(encoding="utf-8")
    )
    canonical_filename = registry["active_versions"]["memory_bank_schema"]["file"]
    canonical = root / ".dev" / "contracts" / canonical_filename

    assert in_package.read_bytes() == canonical.read_bytes(), (
        "agent/core/memory_bank_schema.json drifted from the canonical "
        f"contract at .dev/contracts/{canonical_filename}; re-copy to "
        "restore producer/consumer parity"
    )
