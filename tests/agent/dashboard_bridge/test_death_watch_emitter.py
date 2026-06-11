"""Tests for :mod:`agent.dashboard_bridge.death_watch_emitter`.

Covers schema-conformance for all four frame kinds + the threshold-
crossing detector (which is the most subtle piece — it must seed
without emitting, then fire only on a SIGN CHANGE of the
(energy_pct - threshold) delta).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from agent.dashboard_bridge.death_watch_emitter import (
    DEATH_WATCH_PRIMARY_THRESHOLD_PCT,
    DeathWatchEmitter,
    RecordingTransport,
)

# Load the schema once at module scope — the test below validates
# every emitted frame against it.
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / ".dev"
    / "contracts"
    / "dashboard_death_watch.v0.1.0.json"
)


@pytest.fixture(scope="module")
def death_watch_validator() -> Draft202012Validator:
    """Schema validator pinned to v0.1.0 of the Death-Watch contract."""
    raw = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(raw)


@pytest.fixture
def transport() -> RecordingTransport:
    return RecordingTransport()


def _frozen_clock() -> str:
    """Stable ISO-8601 stamp so frame ``ts`` is reproducible across runs."""
    return "2026-05-23T12:00:00+00:00"


def test_observe_energy_seeds_baseline_without_emitting(
    transport: RecordingTransport,
) -> None:
    """First observe_energy call MUST NOT emit — it seeds the state.

    A naive implementation would emit on the first sample because the
    last_side is None; the spec requires a baseline before any
    crossing is meaningful.
    """
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)
    sent = asyncio.run(emitter.observe_energy(energy_pct=95.0))
    assert sent == []
    assert transport.frames == []


def test_observe_energy_emits_on_threshold_cross_down(
    transport: RecordingTransport,
    death_watch_validator: Draft202012Validator,
) -> None:
    """Crossing the 10% PRIMARY threshold downward fires the takeover
    trigger frame (PRD §8)."""
    emitter = DeathWatchEmitter(
        transport=transport,
        thresholds_pct=(DEATH_WATCH_PRIMARY_THRESHOLD_PCT,),
        _now=_frozen_clock,
    )
    asyncio.run(emitter.observe_energy(energy_pct=20.0))  # seed
    sent = asyncio.run(emitter.observe_energy(energy_pct=8.0))  # cross
    assert len(sent) == 1
    frame = sent[0]
    assert frame["kind"] == "energy_threshold_crossed"
    assert frame["threshold_pct"] == DEATH_WATCH_PRIMARY_THRESHOLD_PCT
    assert frame["direction"] == "below"
    assert frame["energy_pct"] == 8.0
    death_watch_validator.validate(frame)


def test_observe_energy_does_not_re_emit_on_flat_sample(
    transport: RecordingTransport,
) -> None:
    """Same-side samples after a crossing MUST NOT re-emit (the dashboard
    dedups, but the producer should not waste frames)."""
    emitter = DeathWatchEmitter(
        transport=transport,
        thresholds_pct=(10.0,),
        _now=_frozen_clock,
    )
    asyncio.run(emitter.observe_energy(energy_pct=20.0))  # seed
    asyncio.run(emitter.observe_energy(energy_pct=8.0))  # cross down — 1 frame
    asyncio.run(emitter.observe_energy(energy_pct=5.0))  # still below — 0 frames
    asyncio.run(emitter.observe_energy(energy_pct=3.0))  # still below — 0 frames
    assert len(transport.frames) == 1


def test_observe_energy_emits_on_recovery_cross_up(
    transport: RecordingTransport,
) -> None:
    """An UP crossing also emits — the dashboard wants to know the
    indicator healed (e.g. Phase 3 → recovery)."""
    emitter = DeathWatchEmitter(
        transport=transport,
        thresholds_pct=(50.0,),
        _now=_frozen_clock,
    )
    asyncio.run(emitter.observe_energy(energy_pct=40.0))  # seed below
    sent = asyncio.run(emitter.observe_energy(energy_pct=60.0))  # cross up
    assert len(sent) == 1
    assert sent[0]["direction"] == "above"


def test_emit_terminal_lucidity_entered_schema(
    transport: RecordingTransport,
    death_watch_validator: Draft202012Validator,
) -> None:
    """The Phase 4 commit frame validates + carries breath_at_entry."""
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)
    frame = asyncio.run(emitter.emit_terminal_lucidity_entered(breath_at_entry=412.5))
    death_watch_validator.validate(frame)
    assert frame["kind"] == "terminal_lucidity_entered"
    assert frame["breath_at_entry"] == 412.5


def test_emit_terminal_lucidity_rejects_negative_breath(
    transport: RecordingTransport,
) -> None:
    """A negative breath_at_entry is a chain-adapter bug — reject fast."""
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)
    with pytest.raises(ValueError):
        asyncio.run(emitter.emit_terminal_lucidity_entered(breath_at_entry=-1.0))


def test_emit_last_words_validates_tx_hash(
    transport: RecordingTransport,
    death_watch_validator: Draft202012Validator,
) -> None:
    """A malformed tx_hash raises BEFORE the frame ships."""
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)

    # Happy path — full 0x-prefixed 32-byte hex.
    good_tx = "0x" + "ab" * 32
    frame = asyncio.run(emitter.emit_last_words(text="goodbye", tx_hash=good_tx))
    death_watch_validator.validate(frame)
    assert frame["tx_hash"] == good_tx

    # Truncated tx_hash — must reject.
    with pytest.raises(ValueError):
        asyncio.run(emitter.emit_last_words(text="goodbye", tx_hash="0xdeadbeef"))


def test_emit_last_words_caps_text_length(
    transport: RecordingTransport,
) -> None:
    """The 1024-char cap mirrors the on-chain lastWords argument."""
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)
    with pytest.raises(ValueError):
        asyncio.run(emitter.emit_last_words(text="x" * 1025))


def test_emit_tombstone_minted_ipfs_degraded_requires_no_cid(
    transport: RecordingTransport,
    death_watch_validator: Draft202012Validator,
) -> None:
    """PRD §5.1.C: when degraded, ipfs_cid MUST be absent — the UI
    cannot silently render the happy path."""
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)

    # Degraded happy-path: no CID, degraded=True.
    frame = asyncio.run(
        emitter.emit_tombstone_minted(
            token_id="42",
            ipfs_degraded=True,
        )
    )
    death_watch_validator.validate(frame)
    assert "ipfs_cid" not in frame
    assert frame["ipfs_degraded"] is True

    # Misuse: degraded=True + CID → fast-fail.
    with pytest.raises(ValueError):
        asyncio.run(
            emitter.emit_tombstone_minted(
                token_id="42",
                ipfs_degraded=True,
                ipfs_cid="bafy...",
            )
        )

    # Misuse: degraded=False + no CID → fast-fail.
    with pytest.raises(ValueError):
        asyncio.run(
            emitter.emit_tombstone_minted(
                token_id="42",
                ipfs_degraded=False,
            )
        )


def test_emit_tombstone_minted_happy_path_includes_cid(
    transport: RecordingTransport,
    death_watch_validator: Draft202012Validator,
) -> None:
    """The non-degraded path includes ipfs_cid + validates."""
    emitter = DeathWatchEmitter(transport=transport, _now=_frozen_clock)
    frame = asyncio.run(
        emitter.emit_tombstone_minted(
            token_id="42",
            ipfs_degraded=False,
            ipfs_cid="bafybeibwjzcbcatqsd2rjf2enr4mvgr3nfqkzy5gmmgkjxw3wfwqf6oixe",
            tx_hash="0x" + "cd" * 32,
        )
    )
    death_watch_validator.validate(frame)
    assert frame["ipfs_cid"].startswith("bafy")
    assert frame["ipfs_degraded"] is False


def test_seq_is_monotonic_across_frames(
    transport: RecordingTransport,
) -> None:
    """seq starts at 0 and increments per emitted frame, regardless of kind."""
    emitter = DeathWatchEmitter(
        transport=transport,
        thresholds_pct=(10.0,),
        _now=_frozen_clock,
    )

    async def _drive() -> None:
        # Seed + cross — 1 emission
        await emitter.observe_energy(energy_pct=20.0)
        await emitter.observe_energy(energy_pct=5.0)
        await emitter.emit_terminal_lucidity_entered(breath_at_entry=100.0)
        await emitter.emit_last_words(text="bye")
        await emitter.emit_tombstone_minted(token_id="1", ipfs_degraded=True)

    asyncio.run(_drive())
    seqs = [f["seq"] for f in transport.frames]
    assert seqs == [0, 1, 2, 3]


def test_multiple_thresholds_only_fire_the_crossed_one(
    transport: RecordingTransport,
) -> None:
    """If a sample crosses 50% downward but stays above 25%, only the
    50% frame fires — the 25% threshold's side is unchanged."""
    emitter = DeathWatchEmitter(
        transport=transport,
        thresholds_pct=(10.0, 25.0, 50.0),
        _now=_frozen_clock,
    )

    async def _drive() -> list[dict[str, Any]]:
        # Seed all thresholds at "above" (60%).
        await emitter.observe_energy(energy_pct=60.0)
        # Drop to 40% — crosses 50% only.
        return await emitter.observe_energy(energy_pct=40.0)

    sent = asyncio.run(_drive())
    assert len(sent) == 1
    assert sent[0]["threshold_pct"] == 50.0
    assert sent[0]["direction"] == "below"
