"""MemoryBank — the Agent's on-disk journal.

The MemoryBank is the canonical record of the Agent's lived experience.
Every tick of :func:`agent.core.agent.agent_loop` calls
:meth:`MemoryBank.write_tick` as step 9 of the loop, persisting the
TickPayload to ``.agent_state/memory_bank/ticks/tick_<N>.json``.

Three consumers depend on this file shape:

1. **Track C** — ``sim/replay.py`` walks ticks/ as a sequence to
   reconstruct decisions for backtest scoring.
2. **Track D** — the dashboard PLAYBACK loader transforms ticks/ into
   the curated ``dashboard_consciousness_stream`` payload.
3. **V2 boot** — :mod:`agent.core.v2_boot` reads the last K=50 ticks of
   an ancestor (referenced by ``memoryBankCid`` on the Tombstone NFT,
   PRD §5.1) and injects them into the new agent's reflection context.

The writer is **atomic**: each tick is staged at
``.tick_<N>.json.tmp`` then promoted via :func:`os.replace`, which is
atomic on POSIX and (since Python 3.3) on Windows. A crash mid-write
leaves the previous tick file intact + an orphan ``.tmp`` that the next
boot can sweep. Verified by :mod:`tests.agent.test_memory_bank_smoke`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent.core.memory_bank_migrations import CURRENT_VERSION, upgrade
from agent.core.state import TickPayload

# File layout per TECHNICAL_PLAN §4.6
_TICKS_DIR = "ticks"
_REFLECTIONS_DIR = "reflections"
_OBSERVATIONS_DIR = "observations"
_SUMMARY_DIR = "summary"
_POSTMORTEM_DIR = "postmortem"
_IDENTITY_FILE = "identity.md"
_GOAL_FILE = "goal.json"

_TICK_FILENAME_RE = re.compile(r"^tick_(\d+)\.json$")


class MemoryBank:
    """Filesystem-backed journal rooted at ``root`` (default
    ``.agent_state/memory_bank/``).

    Sprint_1 ships ``write_tick``, ``read_tick``, ``list_ticks``, and
    ``summarise``. The richer narrative / reflection helpers land in
    sprint_2 alongside the narrative module they pair with.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.ticks_dir = self.root / _TICKS_DIR
        self.reflections_dir = self.root / _REFLECTIONS_DIR
        self.observations_dir = self.root / _OBSERVATIONS_DIR
        self.summary_dir = self.root / _SUMMARY_DIR
        self.postmortem_dir = self.root / _POSTMORTEM_DIR

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ensure_layout(self) -> None:
        """Create the canonical subdirectory layout. Idempotent."""
        for d in (
            self.root,
            self.ticks_dir,
            self.reflections_dir,
            self.observations_dir,
            self.summary_dir,
            self.postmortem_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Tick I/O
    # ------------------------------------------------------------------

    def write_tick(self, payload: TickPayload) -> Path:
        """Atomically persist a tick row.

        Sequence:

        1. ``ensure_layout`` (so first-tick callers don't need to pre-create dirs).
        2. Serialise the Pydantic model to JSON bytes.
        3. Write to ``.tick_<N>.json.tmp``.
        4. ``os.replace`` onto ``tick_<N>.json``.

        Returns the final path. Raises :class:`ValueError` if the same
        tick number already exists — ticks are append-only by contract.
        """
        # Skip the mkdir storm after the first successful tick — by the
        # second call every directory below ``root`` already exists.
        if not self.ticks_dir.is_dir():
            self.ensure_layout()

        if payload.schema_version != CURRENT_VERSION:
            raise ValueError(
                f"refusing to write tick with schema_version={payload.schema_version}; "
                f"runtime emits {CURRENT_VERSION} (TECHNICAL_PLAN §4.6)"
            )

        final = self._tick_path(payload.tick)
        if final.exists():
            raise ValueError(
                f"tick {payload.tick} already exists at {final}; "
                "MemoryBank ticks are append-only"
            )

        body = payload.model_dump(mode="json", exclude_none=True)
        # sort_keys + indent=2 keeps tick files byte-stable diff targets
        # for Track C replay regression tests.
        data = json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)

        tmp = final.with_name(f".{final.name}.tmp")
        # Best-effort cleanup of an orphan tmp from a previous crash.
        if tmp.exists():
            tmp.unlink()
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, final)  # atomic on POSIX + modern Windows
        return final

    def read_tick(self, tick: int) -> TickPayload:
        """Load + migrate + validate a single tick. Raises FileNotFoundError
        if missing."""
        path = self._tick_path(tick)
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        migrated = upgrade(raw)
        return TickPayload.model_validate(migrated)

    def list_ticks(self) -> list[int]:
        """Return all known tick numbers, sorted ascending by integer
        value (NOT lexicographic — see contract writer_notes)."""
        return [t for t, _ in _scan_tick_dir(self.ticks_dir)]

    def last_k_ticks(self, k: int) -> list[TickPayload]:
        """Return the most recent ``k`` ticks sorted ascending. Used by
        :func:`agent.core.v2_boot.boot_from_ancestor` to seed reflection
        context (K=50 per PRD §13)."""
        if k <= 0:
            return []
        all_ticks = self.list_ticks()
        wanted = all_ticks[-k:]
        return [self.read_tick(t) for t in wanted]

    # ------------------------------------------------------------------
    # Summary helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Observation I/O — shared atomic temp+rename helper
    # ------------------------------------------------------------------

    def write_observation(self, *, filename: str, body: str) -> Path:
        """Atomically write ``body`` to ``observations_dir / filename``.

        Uses the same temp+rename pattern :meth:`write_tick` uses
        internally — stage to ``.<filename>.tmp`` then ``os.replace``
        onto the final path. Crash mid-write leaves the previous file
        intact + an orphan ``.tmp`` that the next boot can sweep.

        Exists so callers that need to persist non-tick blobs to the
        memory bank (sprint_4 ``agent.llm._phase_activation`` emits the
        D11 'LLM activated' event here) reuse the same atomic primitive
        instead of inlining yet another copy of ``os.replace``.

        Returns the final path. ``filename`` must not already contain
        path separators — observations live flat under
        ``observations_dir``.
        """
        if "/" in filename or "\\" in filename:
            raise ValueError(
                f"observation filename must be flat (no separators): {filename!r}"
            )
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        final = self.observations_dir / filename
        tmp = self.observations_dir / f".{filename}.tmp"
        if tmp.exists():
            tmp.unlink()
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, final)
        return final

    def summarise(self, *, last: int = 1) -> dict[str, Any]:
        """Compact dict for ops / CLI inspection. Does NOT raise on an
        empty bank — returns ``count = 0``."""
        ticks = self.list_ticks()
        recent = [self._tick_path(t).name for t in ticks[-last:]] if ticks else []
        return {
            "root": str(self.root),
            "schema_version": CURRENT_VERSION,
            "count": len(ticks),
            "latest_tick": ticks[-1] if ticks else None,
            "recent_files": recent,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tick_path(self, tick: int) -> Path:
        # V1: non-padded filename. The contract writer_notes flag that
        # consumers MUST sort numerically, not lexicographically.
        return self.ticks_dir / f"tick_{tick}.json"


def iter_tick_files(root: Path) -> Iterable[Path]:
    """Module-level helper for tools (sim/replay, tombstone builder)
    that need to walk a bank without instantiating the writer."""
    return [p for _, p in _scan_tick_dir(Path(root) / _TICKS_DIR)]


def _scan_tick_dir(tdir: Path) -> list[tuple[int, Path]]:
    """One regex pass over the tick directory. Returns ``[(tick_n, path)]``
    sorted ascending by tick number. Shared by :meth:`MemoryBank.list_ticks`
    and :func:`iter_tick_files` so the regex is applied exactly once per
    file."""
    if not tdir.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for entry in tdir.iterdir():
        if not entry.is_file():
            continue
        m = _TICK_FILENAME_RE.match(entry.name)
        if m is None:
            continue
        out.append((int(m.group(1)), entry))
    out.sort(key=lambda pair: pair[0])
    return out
