"""Background-task error capture tests for the FastAPI control plane (T-B-034).

Five tests, each pinned to one acceptance criterion in the brief:

1. ``test_sweep_failure_surfaces_status_failed`` — sweep coro raises
   ``ValueError`` → ``GET /api/backtest/{run_id}`` returns 200 with
   ``{status:'failed', error.type:'ValueError'}``, NOT 404 'not ready'.
2. ``test_agent_loop_failure_surfaces_in_status`` — agent loop raises
   ``RuntimeError`` → ``GET /api/agent/status`` returns the same error
   shape under ``last_run_status='failed'`` + ``error.type='RuntimeError'``.
3. ``test_traceback_truncated_to_2kb`` — a deep-recursion failure
   produces a stored traceback whose UTF-8 byte length is ≤ 2048.
4. ``test_cancellation_logged_as_cancelled_not_failed`` — a coroutine
   raising :class:`asyncio.CancelledError` writes a ``status='cancelled'``
   envelope (NOT ``'failed'``) and emits an INFO log (NOT ERROR).
5. ``test_error_envelope_deserialises_through_pydantic`` — the persisted
   ``error`` sub-object round-trips through :class:`RegistryError` without
   raising :class:`pydantic.ValidationError`.

Tests use :class:`fastapi.testclient.TestClient` (sync wrapper around
httpx) so the framework drives the background task lifecycle. No real
network, chain, or LLM is touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner
from agent.server.main import create_app
from agent.server.runner import (
    AGENT_ERROR_FILENAME,
    BACKTEST_RESULT_FILENAME,
    MAX_TRACEBACK_BYTES,
    AgentRunner,
    BacktestRegistry,
    LoopHandle,
    RegistryError,
    RegistryErrorEnvelope,
    SweepRunnerProto,
)
from tests.agent.server.conftest import FakeLoop

pytestmark = pytest.mark.usefixtures("set_dashboard_token")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _wait_until(predicate: Any, *, timeout: float = 2.0) -> bool:
    """Poll ``predicate`` until truthy OR timeout. Returns last result.

    Matches the helper in :mod:`tests.agent.server.test_control_plane` so
    behaviour stays consistent across the suite — copied here rather
    than imported to keep each test module independently runnable.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


async def _raising_sweep(
    *, output_dir: Path, run_id: str, **_unused: Any
) -> None:
    """Sweep stub that always raises ``ValueError`` BEFORE writing anything.

    Mirrors the public failure mode the production sweep can hit on a
    bad config or a missing dependency; the registry must capture this
    into ``results.json`` rather than leave the file absent.

    ``**_unused`` swallows the T-B-037 ``configs`` / ``operator_note`` /
    ``cancel_event`` kwargs the registry now passes — this stub's
    failure mode is identical regardless of body shape.
    """
    del output_dir, run_id
    raise ValueError("sweep blew up on purpose")


async def _import_error_sweep(
    *, output_dir: Path, run_id: str, **_unused: Any
) -> None:
    """Sweep stub that raises ``ImportError`` to match the lazy-import path.

    The brief calls out :func:`agent.server.main._production_sweep_runner`
    lazy-imports its heavy dependency tree; if that import fails we want
    the failure to land in ``results.json`` rather than vanish into the
    BackgroundTask. This stub raises the same exception class at the same
    coroutine entrypoint so the test exercises the relevant code path.
    """
    del output_dir, run_id
    raise ImportError("simulated heavy-deps missing")


async def _cancelled_sweep(
    *, output_dir: Path, run_id: str, **_unused: Any
) -> None:
    """Sweep stub that raises :class:`asyncio.CancelledError` after one tick.

    We can't reliably call ``task.cancel()`` from the test thread — the
    asyncio docs flag :meth:`asyncio.Task.cancel` as not-thread-safe and
    TestClient runs the event loop on a background portal thread. So we
    have the coroutine itself raise CancelledError after one yield. This
    drives the SAME code path in :func:`_safe_run` (the helper doesn't
    care whether the cancellation came from outside or from inside the
    coroutine — both surface as a :class:`CancelledError` on ``await``).
    """
    del output_dir, run_id
    # Yield once so the task is observably scheduled and registered
    # before we cancel — mirrors a real-world cancellation arriving at
    # the next event-loop tick after submission.
    await asyncio.sleep(0)
    raise asyncio.CancelledError("simulated cancellation during sweep")


class _DeepFailingLoop:
    """Fake :class:`LoopHandle` whose ``run()`` raises a deep-stack error.

    Recursion-builds a tall stack BEFORE raising so the formatted
    traceback is reliably larger than the 2 KiB cap and the test can
    assert the truncation happened. The recursion depth is large enough
    to overflow the cap (each frame line is ~70 bytes) but small enough
    to stay well under Python's default recursion limit (1000).
    """

    def __init__(self, *, depth: int = 80) -> None:
        self._depth = depth

    async def run(self) -> object:
        self._recurse(self._depth)
        return None  # unreachable

    def _recurse(self, remaining: int) -> None:
        if remaining <= 0:
            raise RuntimeError(
                "deep loop failure: " + ("x" * 200)
                # Repeat the failure message a bit so even on a python
                # without verbose frames the formatted traceback exceeds
                # the cap. Combined with the deep stack this lands well
                # past 2 KiB.
            )
        self._recurse(remaining - 1)


class _RuntimeErrorLoop:
    """Fake :class:`LoopHandle` whose ``run()`` raises ``RuntimeError``.

    Mirrors the brief's test 2: a vanilla runtime failure inside the
    agent loop must surface through ``GET /api/agent/status``.
    """

    def __init__(self, *, message: str = "agent loop blew up") -> None:
        self._message = message

    async def run(self) -> object:
        # Yield once so the task is observably scheduled before raising.
        await asyncio.sleep(0)
        raise RuntimeError(self._message)


def _read_envelope(path: Path) -> dict[str, Any]:
    """Parse ``path`` as JSON, asserting it exists. Returns the dict."""
    assert path.exists(), f"expected envelope at {path}"
    decoded: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict), f"envelope at {path} is not a JSON object"
    return decoded


def _build_error_capture_app(
    *,
    tmp_path: Path,
    loop_handle: LoopHandle | None = None,
    sweep_runner: SweepRunnerProto | None = None,
) -> tuple[FastAPI, Path, Path]:
    """Wire up the FastAPI app with custom loop + sweep stubs.

    Returns ``(app, state_dir, backtest_root)``. The conftest's
    :func:`build_app` only swaps in a custom ``FakeLoop`` (it doesn't
    accept a sweep override) and its fixed success-shape sweep would
    paper over the failure-mode shapes T-B-034 needs to exercise — so
    this module-local helper bypasses it.

    ``loop_handle`` defaults to a quiet :class:`FakeLoop` that just
    parks; ``sweep_runner`` defaults to an async no-op. Each test
    overrides whichever side it's exercising.
    """
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    backtest_root = tmp_path / "backtests"
    backtest_root.mkdir(parents=True, exist_ok=True)

    handle: LoopHandle = loop_handle if loop_handle is not None else FakeLoop(
        state_dir=state_dir,
        synthetic_decisions=[],
        synthetic_reflections=[],
        synthetic_proposals=[],
        snapshot=None,
    )

    async def _noop_sweep(
        *, output_dir: Path, run_id: str, **_unused: Any
    ) -> None:
        del output_dir, run_id

    runner = AgentRunner(
        loop_factory=lambda: handle,
        state_dir=state_dir,
        stop_timeout_seconds=2.0,
    )
    registry = BacktestRegistry(
        sweep_runner=sweep_runner if sweep_runner is not None else _noop_sweep,
        output_root=backtest_root,
    )
    app = create_app(
        agent_runner=runner,
        backtest_registry=registry,
        runtime_agent=RuntimeAgentRunner(),
        sse_poll_interval_seconds=0.02,
        sse_stop_after_seconds=0.2,
    )
    return app, state_dir, backtest_root


# --------------------------------------------------------------------------- #
# 1. Sweep failure → results.json {status:'failed', error}
# --------------------------------------------------------------------------- #


def test_sweep_failure_surfaces_status_failed(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """POST + GET round-trip: a sweep raising ValueError surfaces as 200
    with the failure envelope under ``output_dir/results.json``.

    Acceptance criterion: GET /api/backtest/{run_id} returns 200 with
    ``{status:'failed', error:{type, message, traceback}}`` when the
    sweep coroutine raises — NOT 404 'result not ready'.
    """
    app, _state_dir, backtest_root = _build_error_capture_app(
        tmp_path=tmp_path, sweep_runner=_raising_sweep
    )

    with TestClient(app) as client:
        post = client.post(
            "/api/backtest/run", headers=auth_headers, json={"operator_note": "fail"}
        )
        assert post.status_code == 202, post.text
        run_id = post.json()["run_id"]

        # Wait for _safe_run to persist the failure envelope.
        results_path = backtest_root / run_id / BACKTEST_RESULT_FILENAME
        assert _wait_until(lambda: results_path.exists()), (
            "expected results.json with failure envelope to be written"
        )

        # GET surfaces 200 + the failure shape — NOT 404 "result not ready".
        get = client.get(f"/api/backtest/{run_id}", headers=auth_headers)
        assert get.status_code == 200, get.text
        body = get.json()
        assert body["status"] == "failed"
        assert isinstance(body["error"], dict)
        assert body["error"]["type"] == "ValueError"
        assert "blew up on purpose" in body["error"]["message"]
        assert "Traceback" in body["error"]["traceback"]
        assert body["completed_at"]  # ISO-8601 string


# --------------------------------------------------------------------------- #
# 2. Agent loop failure → /api/agent/status reflects {error: {...}}
# --------------------------------------------------------------------------- #


def test_agent_loop_failure_surfaces_in_status(
    tmp_path: Path, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """Agent loop raising RuntimeError → /api/agent/status returns
    ``{last_run_status:'failed', error:{type:'RuntimeError', ...}}``.
    """
    app, state_dir, _backtest_root = _build_error_capture_app(
        tmp_path=tmp_path,
        loop_handle=_RuntimeErrorLoop(message="loop blew up in test"),
    )

    with caplog.at_level(logging.ERROR, logger="agent.server.runner"):
        with TestClient(app) as client:
            post = client.post("/api/agent/start", headers=auth_headers)
            assert post.status_code == 202

            error_path = state_dir / AGENT_ERROR_FILENAME
            assert _wait_until(lambda: error_path.exists()), (
                "expected agent_error.json to be written when the "
                "loop raises RuntimeError"
            )

            # Poll /status until it reflects the crash (the task may
            # need an extra event-loop tick to mark itself done).
            def _crashed() -> bool:
                resp = client.get(
                    "/api/agent/status", headers=auth_headers
                )
                if resp.status_code != 200:
                    return False
                body: dict[str, Any] = resp.json()
                return bool(body.get("last_run_status") == "failed")

            assert _wait_until(_crashed), (
                "/api/agent/status did not surface the loop failure"
            )

            status_resp = client.get(
                "/api/agent/status", headers=auth_headers
            )
            body = status_resp.json()
            assert body["last_run_status"] == "failed"
            assert isinstance(body["error"], dict)
            assert body["error"]["type"] == "RuntimeError"
            assert "loop blew up in test" in body["error"]["message"]
            assert "Traceback" in body["error"]["traceback"]
            # is_running collapses to False as soon as the task ends.
            assert body["running"] is False

    # The brief locks ERROR-level logging for failures.
    failure_records = [
        rec for rec in caplog.records
        if rec.name == "agent.server.runner"
        and rec.levelno == logging.ERROR
        and "failed run_id=" in rec.getMessage()
    ]
    assert failure_records, (
        f"expected an ERROR log for the agent loop failure; got {caplog.records!r}"
    )


# --------------------------------------------------------------------------- #
# 3. Traceback ≤ 2 KiB
# --------------------------------------------------------------------------- #


def test_traceback_truncated_to_2kb(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """A deep-recursion failure's stored traceback is ≤ 2 KiB bytes.

    The brief locks ``len(error.traceback.encode()) <= 2048`` for stored
    failure envelopes. We build a tall stack so the FULL traceback
    blows past 2 KiB, then assert the persisted slice fits.
    """
    app, state_dir, _backtest_root = _build_error_capture_app(
        tmp_path=tmp_path,
        loop_handle=_DeepFailingLoop(depth=80),
    )

    with TestClient(app) as client:
        post = client.post("/api/agent/start", headers=auth_headers)
        assert post.status_code == 202

        error_path = state_dir / AGENT_ERROR_FILENAME
        assert _wait_until(lambda: error_path.exists()), (
            "expected agent_error.json to be written"
        )
        envelope = _read_envelope(error_path)
        assert envelope["status"] == "failed"
        error = envelope["error"]
        assert error is not None
        tb_text: str = error["traceback"]
        # The hard cap is in bytes, not characters — assert on the
        # UTF-8 byte length per the brief.
        assert len(tb_text.encode("utf-8")) <= MAX_TRACEBACK_BYTES, (
            f"traceback exceeds {MAX_TRACEBACK_BYTES} bytes "
            f"(got {len(tb_text.encode('utf-8'))})"
        )
        # And the bottom of the traceback was kept — that's where the
        # actual exception line lives, which is the highest-signal piece.
        assert "RuntimeError" in tb_text
        assert "deep loop failure" in tb_text


# --------------------------------------------------------------------------- #
# 4. CancelledError → status='cancelled', logged at INFO (NOT ERROR)
# --------------------------------------------------------------------------- #


def test_cancellation_logged_as_cancelled_not_failed(
    tmp_path: Path, auth_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """Cancelling a sweep yields ``status='cancelled'`` and an INFO log.

    The brief: "Cancelled tasks (asyncio.CancelledError) logged
    separately, NOT counted as failure". We submit a sweep that itself
    raises CancelledError after one yield (we can't reliably call
    task.cancel() from the test thread — see ``_cancelled_sweep`` for
    the rationale), then check both the persisted envelope AND the
    logger level.
    """
    app, _state_dir, backtest_root = _build_error_capture_app(
        tmp_path=tmp_path, sweep_runner=_cancelled_sweep
    )

    with caplog.at_level(logging.INFO, logger="agent.server.runner"):
        with TestClient(app) as client:
            post = client.post(
                "/api/backtest/run", headers=auth_headers, json={"operator_note": "cxl"}
            )
            assert post.status_code == 202
            run_id = post.json()["run_id"]

            results_path = backtest_root / run_id / BACKTEST_RESULT_FILENAME
            assert _wait_until(lambda: results_path.exists()), (
                "expected results.json with cancellation envelope to be written"
            )

            envelope = _read_envelope(results_path)
            assert envelope["status"] == "cancelled"
            # The brief: cancelled means NO error sub-object. Failure
            # carries a populated `error` dict; cancellation does not.
            assert envelope["error"] is None
            assert envelope["completed_at"]

    cancel_records = [
        rec for rec in caplog.records
        if rec.name == "agent.server.runner"
        and "cancelled run_id=" in rec.getMessage()
    ]
    assert cancel_records, "expected an INFO-level cancelled log record"
    # And no ERROR records for THIS run_id — cancellation is not a failure.
    error_records_for_this_run = [
        rec for rec in caplog.records
        if rec.name == "agent.server.runner"
        and rec.levelno == logging.ERROR
        and run_id in rec.getMessage()
    ]
    assert not error_records_for_this_run, (
        f"cancellation must NOT log at ERROR; got {error_records_for_this_run!r}"
    )
    # And every cancellation record is INFO-level.
    assert all(rec.levelno == logging.INFO for rec in cancel_records)


# --------------------------------------------------------------------------- #
# 5. Error envelope deserialises through RegistryError without ValidationError
# --------------------------------------------------------------------------- #


def test_error_envelope_deserialises_through_pydantic(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Persisted ``error`` round-trips through :class:`RegistryError`.

    We trigger an ``ImportError`` (matching the
    :func:`_production_sweep_runner` lazy-import failure mode) and
    validate the persisted envelope through both
    :class:`RegistryErrorEnvelope` AND its inner :class:`RegistryError`
    so a future schema-drift refactor breaks loudly.
    """
    app, _state_dir, backtest_root = _build_error_capture_app(
        tmp_path=tmp_path, sweep_runner=_import_error_sweep
    )

    with TestClient(app) as client:
        post = client.post(
            "/api/backtest/run", headers=auth_headers, json={"operator_note": "imp"}
        )
        assert post.status_code == 202
        run_id = post.json()["run_id"]

        results_path = backtest_root / run_id / BACKTEST_RESULT_FILENAME
        assert _wait_until(lambda: results_path.exists())

        raw = results_path.read_text(encoding="utf-8")
        # Outer envelope.
        envelope = RegistryErrorEnvelope.model_validate_json(raw)
        assert envelope.status == "failed"
        assert envelope.error is not None
        assert envelope.completed_at

        # Inner RegistryError — explicit re-validation per acceptance test 5.
        error_dict = json.loads(raw)["error"]
        validated = RegistryError.model_validate(error_dict)
        assert validated.type == "ImportError"
        assert "simulated heavy-deps missing" in validated.message
        assert validated.traceback  # non-empty

        # And the GET route returns 200 with the envelope content —
        # closing the loop with the dashboard-visible shape.
        get = client.get(f"/api/backtest/{run_id}", headers=auth_headers)
        assert get.status_code == 200
        body = get.json()
        assert body["status"] == "failed"
        assert body["error"]["type"] == "ImportError"
