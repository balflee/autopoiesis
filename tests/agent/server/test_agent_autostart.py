"""Auto-start lifespan (persistent/Railway deploy) — env-gated, default OFF.

``GENESIS_AGENT_AUTOSTART=1`` makes the FastAPI lifespan call
``agent_runner.start()`` on server boot, so a set-and-forget deploy (and a
restart) re-launches the loop without an operator ``POST /api/agent/start``.
Default OFF keeps the manual-start flow byte-unchanged.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.server.main import (
    GENESIS_AGENT_AUTOSTART_ENV_VAR,
    _autostart_lifespan,
)


class _FakeRunner:
    """Records ``start()`` calls; mirrors the ``is_running`` guard."""

    def __init__(self, *, already_running: bool = False) -> None:
        self.start_calls = 0
        self._running = already_running

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> str:
        self.start_calls += 1
        self._running = True
        return "run-test-1"


def _app_with(runner: _FakeRunner) -> SimpleNamespace:
    """Minimal app double exposing ``app.state.deps.agent_runner``."""
    return SimpleNamespace(state=SimpleNamespace(deps=SimpleNamespace(agent_runner=runner)))


def _drive(app: SimpleNamespace) -> None:
    async def _run() -> None:
        async with _autostart_lifespan(app):  # type: ignore[arg-type]
            pass  # startup runs on enter, shutdown on exit

    asyncio.run(_run())


def test_autostart_launches_loop_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GENESIS_AGENT_AUTOSTART_ENV_VAR, "1")
    runner = _FakeRunner()
    _drive(_app_with(runner))
    assert runner.start_calls == 1


def test_no_autostart_when_flag_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GENESIS_AGENT_AUTOSTART_ENV_VAR, raising=False)
    runner = _FakeRunner()
    _drive(_app_with(runner))
    assert runner.start_calls == 0  # manual-start flow unchanged


def test_no_autostart_when_value_not_exactly_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GENESIS_AGENT_AUTOSTART_ENV_VAR, "true")  # only "1" triggers
    runner = _FakeRunner()
    _drive(_app_with(runner))
    assert runner.start_calls == 0


def test_autostart_idempotent_when_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GENESIS_AGENT_AUTOSTART_ENV_VAR, "1")
    runner = _FakeRunner(already_running=True)
    _drive(_app_with(runner))
    assert runner.start_calls == 0  # is_running guard → no double-start
