"""MemoryBank tarball replay — sprint_1 stub.

This module is the **canonical consumer** of the MemoryBank tarball
produced by Track B (``agent/core/memory_bank.py``). One of four
documented consumers per TECHNICAL_PLAN.md §4.6:

1. Track C — this module — backtest reconstruction.
2. Track D — dashboard PLAYBACK loader (``dashboard_consciousness_stream``).
3. Track B — :func:`agent.core.v2_boot.boot_from_ancestor` reading
   ancestor last-K=50 ticks (PRD §13).
4. Tombstone NFT builder — minting carries ``memoryBankCid``
   (PRD §5.1).

Schema contract
---------------

The tarball contains one JSON-per-tick row matching
``agent/core/memory_bank_schema.json`` (Track B SSOT; ``$id`` →
``contracts/memory_bank_schema.v1.0.0.json``). The shape is published
by Track B in this sprint; sim/replay is a **read-only consumer** and
MUST refuse to load a major-version mismatch (per the schema's own
``description`` field).

PRD anchors
-----------

* PRD §14 — Calibration Framework requires the sweep to replay a
  recorded agent run end-to-end against Polymarket history; the
  MemoryBank tarball is the input format.
* TECHNICAL_PLAN.md §4.6 — MemoryBank module API + the
  ``.agent_state/memory_bank/{ticks,summary,reflections,observations,
  postmortem}/`` directory layout that ``load_tarball`` will unpack.

The real loader lands in sprint_2 (T-C-002+ chain), gated on Track B's
real memory_bank output existing as test fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TickRecord:
    """Sprint_1 placeholder mirror of one tarball tick row.

    Sprint_2 replaces ``raw`` with strongly-typed fields parsed from
    ``agent/core/memory_bank_schema.json``. Until then we hold the
    parsed JSON dict + the tick number that named the file, which is
    all the type signature for :func:`load_tarball` needs.
    """

    tick: int
    raw: dict[str, Any]


def load_tarball(path: Path) -> list[TickRecord]:
    """Load a MemoryBank tarball into the sim trajectory format.

    Schema source of truth: ``agent/core/memory_bank_schema.json``.
    Calibration anchor: PRD.md §14 (Calibration Framework).
    File-layout anchor: TECHNICAL_PLAN.md §4.6 (MemoryBank module API +
    ``.agent_state/memory_bank/`` directory layout).

    Sprint_1 contract: NotImplementedError('sprint_2'). The function
    exists today so the public API surface is locked — every other
    sim module can import :func:`load_tarball` without forward
    references, and the calibration validator can grep for this
    symbol to confirm Track C has wired the consumer slot.

    Parameters
    ----------
    path:
        Filesystem path to a ``.tar.gz`` produced by Track B's
        MemoryBank serializer. Sprint_2 will accept either the tarball
        OR an already-unpacked directory matching the
        ``.agent_state/memory_bank/`` layout — the duck-typed loader is
        a sprint_2 design call.

    Returns
    -------
    list[TickRecord]
        One :class:`TickRecord` per tick row, ordered by tick number
        ascending. The contract here mirrors
        :func:`agent.core.memory_bank.MemoryBank.list_ticks` so a
        replay can be diffed against the live bank directly.

    Raises
    ------
    NotImplementedError
        Always, this sprint. Message body is the literal string
        ``'sprint_2'`` so the validator's grep-based readiness check
        can confirm the stub is shape-correct.

    See Also
    --------
    agent/core/memory_bank_schema.json
        Schema contract for each tick row inside the tarball.
    PRD.md §14
        Calibration framework — tarball replay is one input source.
    TECHNICAL_PLAN.md §4.6
        MemoryBank module API + on-disk file layout.
    """
    raise NotImplementedError("sprint_2")
