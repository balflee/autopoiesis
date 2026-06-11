"""FastAPI control plane for the Genesis Experiment sandbox agent — T-B-027.

This package owns the single-tenant REST + SSE surface the dashboard talks
to. Six routes are exposed (start / stop / status / backtest run / backtest
result / state SSE stream), every one of them bearer-token authed via the
``DASHBOARD_API_TOKEN`` env var.

The package layout is deliberately small and Protocol-driven so the test
suite can swap in a fake loop / sweep without touching FastAPI internals:

* :mod:`agent.server.auth` — bearer-token dependency. Returns
  ``{"detail": "unauthorized"}`` with HTTP 401 on a missing OR wrong token.
  Auth fires BEFORE route handlers run, so payload validation never has
  a chance to leak shape detail to an unauthorised caller.

* :mod:`agent.server.runner` — :class:`AgentRunner` owns the asyncio
  background task for the :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`.
  Construction takes a ``loop_factory`` callable so tests inject a fake
  loop without wiring up the real Polymarket / chain / LLM Protocols.

* :mod:`agent.server.main` — :func:`create_app` returns a configured
  :class:`fastapi.FastAPI`. The dashboard fetches the OpenAPI doc to
  generate its typed wrapper (T-D-010 follow-up).

PRD anchors: §8 (Dashboard demo surface) + TECHNICAL_PLAN §5.1, §5.4.
"""

from __future__ import annotations

from agent.server.main import create_app

__all__ = ["create_app"]
