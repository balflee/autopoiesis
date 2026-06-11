"""Genesis Experiment Data Pipelines — Track E.

The ``data`` package is the **point-in-time-correct** ETL layer that
feeds both:

* Track B (the live agent runtime, ``agent/``) at decision time, and
* Track C (the calibration sim, ``sim/``) on replay.

Per PRD §14.1 and TECHNICAL_PLAN §6, the cardinal rule is:

    No feature row may be visible to the agent before its
    ``available_at`` timestamp has elapsed in wall-clock terms.

The :func:`data.etl.pit_correct.assert_no_lookahead` chokepoint enforces
that rule once Sprint_2 lands the actual feature joins. This sprint_1
scaffold ships the package skeleton + four source-adapter stubs (NBA,
Polymarket market history, Polygon chain reads, Reddit sentiment) so the
dependency tree, ``mypy --strict`` posture, and pytest import resolution
are stable before any real network calls are wired.

This package is intentionally side-effect free at import time — no
network connections, no credential loading, no module-scope I/O. Every
``fetch_*`` method raises :class:`NotImplementedError` until sprint_2.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0-sprint2"
