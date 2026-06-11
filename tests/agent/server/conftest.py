"""Shared fixtures for the FastAPI control-plane test suite (T-B-027).

The fixtures here build the smallest possible drop-in replacements for
the production wiring:

* :class:`FakeLoop` — a :class:`agent.server.runner.LoopHandle` that
  optionally appends synthetic decision rows to ``decisions.jsonl``
  before going to sleep. Tests can use it to validate /status + /stream
  without booting the real SandboxPhase2Loop (which needs Polymarket
  + chain + LLM Protocols).

* :func:`build_app` — wires a fresh :class:`AgentRunner` and
  :class:`BacktestRegistry` against a per-test ``tmp_path`` + a stub
  sweep runner that writes a minimal ``results.json``.

The fixture sets ``DASHBOARD_API_TOKEN`` to a known value so every
test can authenticate; tests that want to exercise the unauthorised
path delete + restore the env var locally.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Opt out of the module-level `app = _build_default_app()` side effect
# BEFORE :mod:`agent.server.main` is imported below. The default app
# would mkdir(state/sandbox) + (data/backtest/runs) under the repo root
# at import time — fine in production, polluting in pytest. T-B-028
# added the autobuild guard; this is its first consumer.
os.environ.setdefault("GENESIS_SERVER_AUTOBUILD", "0")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner  # noqa: E402
from agent.server.main import create_app  # noqa: E402
from agent.server.runner import (  # noqa: E402
    AgentRunner,
    BacktestRegistry,
    LoopHandle,
)

TEST_TOKEN = "test-token-not-a-real-secret"
"""Bearer token the fixtures + tests use. Set on the env var via fixture
so the auth dependency sees it; tests that want 401 paths override."""


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class FakeLoop:
    """Minimal :class:`LoopHandle` for the test suite.

    Behaviour
    ---------

    * On ``run()``, first writes ``synthetic_lines`` to the relevant
      JSONL streams (so the SSE + status tests have content to read).
    * Then sleeps until cancelled — mimics the production loop's
      between-tick await that :class:`AgentRunner.stop` cancels.
    * Updates ``ran_count`` so tests can assert the loop was started.
    """

    state_dir: Path
    synthetic_decisions: list[dict[str, Any]]
    synthetic_reflections: list[dict[str, Any]]
    synthetic_proposals: list[dict[str, Any]]
    snapshot: dict[str, Any] | None
    ran_count: int = 0
    cancelled_count: int = 0

    async def run(self) -> object:
        self.ran_count += 1
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Snapshot first so /status reads it.
        if self.snapshot is not None:
            (self.state_dir / "agent_state.json").write_text(
                json.dumps(self.snapshot, sort_keys=True),
                encoding="utf-8",
            )
        # JSONL streams: append one line per synthetic row.
        for row in self.synthetic_decisions:
            _append_jsonl(self.state_dir / "decisions.jsonl", row)
        for row in self.synthetic_reflections:
            _append_jsonl(self.state_dir / "reflections.jsonl", row)
        for row in self.synthetic_proposals:
            _append_jsonl(self.state_dir / "proposals.jsonl", row)
        # Park forever — :meth:`AgentRunner.stop` will cancel us.
        try:
            await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            self.cancelled_count += 1
            raise
        return None


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


@dataclass
class FakeSweepRunner:
    """Async callable that writes a minimal ``results.json`` and exits.

    Mimics :func:`agent.backtest.sweep_runner.run_sweep` at the file
    level — the registry doesn't care what's inside results.json, only
    that the file exists when the GET fires.

    T-B-037 — accepts the new ``configs`` + ``operator_note`` +
    ``cancel_event`` kwargs to match the widened
    :class:`SweepRunnerProto`. ``last_configs`` records the last
    invocation's projected configs so the typed-body test can assert
    the route forwarded the list verbatim. ``cancel_event`` is
    captured on the side so tests can verify the registry passed a
    fresh latch.
    """

    payload: dict[str, Any]
    invocations: list[str]
    last_configs: list[Any] = field(default_factory=list)
    last_operator_note: str | None = None
    last_cancel_event: asyncio.Event | None = None

    async def __call__(
        self,
        *,
        output_dir: Path,
        run_id: str,
        configs: list[Any] | None = None,
        operator_note: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.invocations.append(run_id)
        self.last_configs = list(configs or [])
        self.last_operator_note = operator_note
        self.last_cancel_event = cancel_event
        output_dir.mkdir(parents=True, exist_ok=True)
        configs_for_payload = (
            [
                cfg.model_dump() if hasattr(cfg, "model_dump") else cfg
                for cfg in self.last_configs
            ]
            if self.last_configs
            else self.payload.get("results", [])
        )
        body = {
            **self.payload,
            "run_id": run_id,
            "results": configs_for_payload,
            "configs_run": len(self.last_configs)
            if self.last_configs
            else self.payload.get("configs_run", 0),
        }
        if operator_note is not None:
            body["operator_note"] = operator_note
        (output_dir / "results.json").write_text(
            json.dumps(body, sort_keys=True),
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def build_loop_factory(loop: FakeLoop) -> Any:
    """Wrap a FakeLoop in a 0-arg factory the AgentRunner can call."""

    def _factory() -> LoopHandle:
        return loop

    return _factory


def build_app(
    *,
    tmp_path: Path,
    loop: FakeLoop | None = None,
    sweep_payload: dict[str, Any] | None = None,
    sse_poll_interval_seconds: float = 0.02,
    sse_stop_after_seconds: float | None = 0.5,
    llm_cost: float = 0.0,
    runtime_agent: RuntimeAgentRunner | None = None,
) -> tuple[FastAPI, FakeLoop, FakeSweepRunner]:
    """Build a fully-wired FastAPI app + the fakes it depends on.

    Returns ``(app, loop, sweep)`` so the test can introspect ran_count
    + invocations after exercising the routes.

    ``runtime_agent`` (T-B-031) is the optional weight-delta seam. When
    a caller passes one, the FastAPI app shares the SAME instance the
    test's consumer-side fake loop will drain — which is how the
    approve_proposal pickup test exercises the producer→consumer
    hand-off without standing up the full :class:`SandboxPhase2Loop`.
    Default ``None`` lets :func:`create_app` construct a fresh instance
    so the existing T-B-027/-028 test suite keeps working unchanged.
    """
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    backtest_root = tmp_path / "backtests"
    backtest_root.mkdir(parents=True, exist_ok=True)

    loop = loop or FakeLoop(
        state_dir=state_dir,
        synthetic_decisions=[],
        synthetic_reflections=[],
        synthetic_proposals=[],
        snapshot=None,
    )
    sweep = FakeSweepRunner(
        payload=sweep_payload or {"configs_run": 4, "results": []},
        invocations=[],
    )

    runner = AgentRunner(
        loop_factory=build_loop_factory(loop),
        state_dir=state_dir,
        stop_timeout_seconds=2.0,
    )
    registry = BacktestRegistry(sweep_runner=sweep, output_root=backtest_root)

    app = create_app(
        agent_runner=runner,
        backtest_registry=registry,
        runtime_agent=runtime_agent,
        llm_cost_provider=lambda: llm_cost,
        sse_poll_interval_seconds=sse_poll_interval_seconds,
        sse_stop_after_seconds=sse_stop_after_seconds,
    )
    return app, loop, sweep


# --------------------------------------------------------------------------- #
# pytest fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def set_dashboard_token() -> Iterator[None]:
    """Set ``DASHBOARD_API_TOKEN`` to :data:`TEST_TOKEN` for the duration
    of the test, restoring the prior value afterwards."""
    sentinel = object()
    prev: str | object = os.environ.get("DASHBOARD_API_TOKEN", sentinel)
    os.environ["DASHBOARD_API_TOKEN"] = TEST_TOKEN
    try:
        yield
    finally:
        if prev is sentinel:
            os.environ.pop("DASHBOARD_API_TOKEN", None)
        elif isinstance(prev, str):
            os.environ["DASHBOARD_API_TOKEN"] = prev


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Standard ``Authorization`` header for an authed request."""
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
