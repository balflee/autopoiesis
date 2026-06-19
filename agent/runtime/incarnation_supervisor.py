"""Living Stage Phase 2 — live reincarnation supervisor + its crash-recovery
manifest. See docs/superpowers/plans/2026-06-19-living-stage-phase2.md."""

from __future__ import annotations

import hashlib
import json  # noqa: F401 — kept for parity with the sibling JSONL helpers
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from agent.data.sandbox_state import (
    DECISIONS_FILENAME,
    OPEN_BETS_FILENAME,
    PROPOSALS_FILENAME,
    REFLECTIONS_FILENAME,
    SETTLED_BETS_FILENAME,
    SNAPSHOT_FILENAME,
)
from agent.runtime.sandbox_phase2_loop import RunSummary, SandboxPhase2Loop

logger = logging.getLogger(__name__)

INCARNATION_MANIFEST_FILENAME: Final[str] = "incarnation_manifest.json"


class IncarnationManifest(BaseModel):
    """Crash-recovery breadcrumb at the root state_dir. Lets the supervisor
    resume the lineage at the right incarnation after a server restart instead
    of restarting at 0."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    current_incarnation_idx: int = Field(ge=0)
    carry_weights_hash: str
    max_incarnations: int = Field(ge=1)


def write_manifest(state_dir: Path, manifest: IncarnationManifest) -> None:
    """Atomically (temp + os.replace) write the manifest to the root state_dir."""
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / INCARNATION_MANIFEST_FILENAME
    with tempfile.NamedTemporaryFile(
        "w", dir=state_dir, delete=False, encoding="utf-8", suffix=".tmp"
    ) as f:
        f.write(manifest.model_dump_json())
        f.flush()
        os.fsync(f.fileno())
        tmp = Path(f.name)
    os.replace(tmp, target)


def read_manifest(state_dir: Path) -> IncarnationManifest | None:
    """Read the manifest; None on absent OR corrupt (→ cold start at idx 0)."""
    path = state_dir / INCARNATION_MANIFEST_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return IncarnationManifest.model_validate_json(raw)
    except Exception:  # noqa: BLE001 — corrupt manifest must not crash boot
        return None


# Per-life streams reset between incarnations (cold-start clean). The cumulative
# divine streams (gods_treasury.jsonl, deaths.jsonl) + the manifest are NOT here
# — they accumulate across lives so the treasury grows + the lineage builds.
_PER_LIFE_STREAMS: Final[tuple[str, ...]] = (
    OPEN_BETS_FILENAME,
    SETTLED_BETS_FILENAME,
    DECISIONS_FILENAME,
    REFLECTIONS_FILENAME,
    PROPOSALS_FILENAME,
    SNAPSHOT_FILENAME,
)

_DEFAULT_MAX_INCARNATIONS: Final[int] = 10


def _weights_hash(weights: object) -> str:
    """Stable SHA-256 of a weights object for the manifest (Round-1 MED-1 —
    Python hash() is process-randomized; mirror the death path's
    _sha256_hex_prefixed over weights.model_dump_json()). '0x0' for None
    (incarnation 0's default-weights case)."""
    if weights is None:
        return "0x0"
    try:
        payload = weights.model_dump_json()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        payload = repr(weights)
    return "0x" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_snapshot_weights(state_dir: Path) -> object | None:
    """Read terminal weights from the root agent_state.json (None on
    absent/corrupt/no-weights). Used on manifest resume to carry the prior
    incarnation's evolved weights forward BEFORE the per-life reset wipes the
    snapshot (Round-1 HIGH-2)."""
    from agent.data.sandbox_state import AgentStateSnapshot

    try:
        snap = AgentStateSnapshot.model_validate_json(
            (state_dir / SNAPSHOT_FILENAME).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001
        return None
    return snap.weights


class LiveIncarnationSupervisor:
    """Phase 2 — a LoopHandle that respawns the single-life loop across deaths.

    Cloned from agent/backtest/survival_season.run_survival_season: build one
    incarnation, await its run(), and on death carry loop.weights into the next
    life. Implements the LoopHandle protocol (async run()) so AgentRunner is
    unchanged — the production factory returns THIS when SANDBOX_REINCARNATION=1.
    """

    def __init__(
        self,
        *,
        build_incarnation: Callable[..., SandboxPhase2Loop],
        build_chain_adapter: Callable[[], Any],
        state_dir: Path,
        max_incarnations: int = _DEFAULT_MAX_INCARNATIONS,
        run_id: str = "live",
    ) -> None:
        self._build_incarnation = build_incarnation
        self._build_chain_adapter = build_chain_adapter
        self._state_dir = Path(state_dir)
        self._max_incarnations = max(1, max_incarnations)
        self._run_id = run_id

    def _reset_per_life_streams(self) -> None:
        for name in _PER_LIFE_STREAMS:
            try:
                (self._state_dir / name).unlink(missing_ok=True)
            except OSError:
                logger.warning("supervisor: could not reset per-life stream %s", name)
        # Round-1 MED-4: per-incarnation memory bank + reflections (spec §7:
        # memory CID is per-incarnation for v1). Safe to reset here — called
        # between lives, after the dead life returned + finalized its tombstone.
        shutil.rmtree(self._state_dir / "_mb", ignore_errors=True)

    async def run(self) -> RunSummary:
        # Resume from the manifest if present (crash recovery); else start at 0.
        m = read_manifest(self._state_dir)
        start_idx = m.current_incarnation_idx if m is not None else 0
        # Round-1 HIGH-2: on resume, recover the prior terminal weights from the
        # root snapshot BEFORE any reset wipes agent_state.json — else the
        # resumed incarnation cold-starts with default weights, discarding what
        # it learned. None (cold boot) → incarnation 0 uses the loop's default.
        carry_weights: object | None = (
            _read_snapshot_weights(self._state_dir) if m is not None else None
        )
        last_summary = RunSummary(
            ticks_completed=0,
            bets_placed=0,
            no_bets_emitted=0,
            settlements_processed=0,
            died=False,
            death_receipt=None,
            final_breath=0.0,
            final_bankroll_usd=0.0,
        )
        idx = start_idx
        while idx < self._max_incarnations:
            # Fresh chain_adapter per life — reusing a dead adapter (breath=0)
            # would re-die instantly. Reset per-life streams so the loop
            # cold-starts; the cumulative divine streams are preserved.
            self._reset_per_life_streams()
            chain_adapter = self._build_chain_adapter()
            loop = self._build_incarnation(
                incarnation_idx=idx,
                chain_adapter=chain_adapter,
                initial_weights=carry_weights,
                incarnation_number=idx,
            )
            write_manifest(
                self._state_dir,
                IncarnationManifest(
                    run_id=self._run_id,
                    current_incarnation_idx=idx,
                    carry_weights_hash=_weights_hash(carry_weights),
                    max_incarnations=self._max_incarnations,
                ),
            )
            logger.info("supervisor: incarnation %d starting", idx)
            # CancelledError (operator stop) propagates OUT untouched → no respawn.
            summary = await loop.run()
            last_summary = summary
            carry_weights = loop.weights
            if not summary.died:
                # Survived to a self-decided stop (live: only happens on cancel,
                # which raises above; this guards backtest-style finite runs).
                break
            idx += 1
        logger.info(
            "supervisor: terminal after %d incarnation(s)", idx - start_idx + 1
        )
        return last_summary
