"""Genesis Experiment Agent runtime — Track B.

Sprint_1 scaffold. The Agent owns:

* A persistent process running a 45-min decision cycle (PRD §6).
* Five engines: NBA技术 / 盘口动量 / Smart Money / LLM情绪 / Reddit关注度
  (PRD §4) fused through a 6-parameter 2-layer model.
* A MemoryBank that journals each tick atomically to
  ``.agent_state/memory_bank/`` (TECHNICAL_PLAN §4.6) so Track C replay,
  Track D playback, and the V2 boot loader (PRD §13) can reconstruct the
  agent's lived experience.

This package is intentionally side-effect free at import time. The
:mod:`agent.main` module exposes the CLI entrypoint.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
