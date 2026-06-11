"""Lifecycle controller — owns the persistent process boot/shutdown sequence.

Sprint_1 ships a stub interface only. The real implementation in sprint_2
will:

* read on-chain phase via :class:`agent.chain.EnergyControllerAdapter`
* hydrate MemoryBank from disk + (if ancestor lineage exists) IPFS
* wire the asyncio scheduler that fires :func:`agent.core.agent.agent_loop`
  every 45 minutes per PRD §6
* register SIGTERM handlers that flush in-flight ticks atomically before
  exit (atomicity per TECHNICAL_PLAN §4.6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class LifecycleHooks(Protocol):
    """Sprint_2+ hook surface. Sprint_1 doesn't need an implementation —
    documenting the interface here lets engine stubs reference it for
    typing without an import cycle."""

    def on_boot(self) -> None: ...
    def on_tick_begin(self, tick: int) -> None: ...
    def on_tick_end(self, tick: int) -> None: ...
    def on_shutdown(self) -> None: ...


class Lifecycle:
    """Sprint_1 stub lifecycle. Methods raise NotImplementedError until
    sprint_2 lands the real scheduler.

    The constructor accepts ``memory_bank_root`` so tests that exercise
    instantiation don't trip on filesystem assumptions.
    """

    def __init__(self, memory_bank_root: Path) -> None:
        self.memory_bank_root = memory_bank_root

    def boot(self) -> None:
        """Sprint_2: phase fetch + MemoryBank hydrate + scheduler start."""
        raise NotImplementedError("Lifecycle.boot lands in sprint_2")

    def run_forever(self) -> None:
        """Sprint_2: blocking asyncio scheduler with 45-min tick cadence."""
        raise NotImplementedError("Lifecycle.run_forever lands in sprint_2")

    def shutdown(self) -> None:
        """Sprint_2: SIGTERM handler — flush pending tick atomically."""
        raise NotImplementedError("Lifecycle.shutdown lands in sprint_2")
