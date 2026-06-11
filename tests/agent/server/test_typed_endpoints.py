"""Typed-body + cancel + configure tests for the FastAPI control plane (T-B-037).

Nine tests pinned to each acceptance criterion in the brief:

1. ``test_backtest_run_typed_body_forwards_two_configs`` — POST
   ``/api/backtest/run`` with ``configs=[A, B]`` produces a sweep
   carrying exactly those two configs (NOT the 4 defaults).
2. ``test_backtest_run_empty_body_falls_back_to_defaults`` — empty
   body (or absent ``configs``) still kicks the default 4-config
   sweep (backward-compat locked by the brief).
3. ``test_backtest_run_invalid_rho_rejected`` — ``rho=2.0`` on one
   config short-circuits with HTTP 422 BEFORE any sweep submission;
   the registry sees no invocation.
4. ``test_backtest_cancel_flips_status_to_cancelled`` —
   ``POST /api/backtest/{run_id}/cancel`` causes the running sweep
   to write ``status='cancelled'`` to ``results.json`` within ≤5s.
5. ``test_backtest_cancel_unknown_id_returns_404`` — cancel of an
   unknown run_id returns 404 (NOT 500).
6. ``test_backtest_cancel_idempotent`` — second cancel call still
   returns 200 (the latch is one-way).
7. ``test_configure_writes_agent_config_atomically`` —
   ``POST /api/agent/configure`` returns 202 and writes
   ``state/sandbox/agent_config.json`` via an atomic temp+rename.
8. ``test_configure_rejects_rho_out_of_range`` — ``rho=1.5`` returns
   HTTP 400 with a ``validation_errors`` detail payload.
9. ``test_configure_accepts_weight_sum_drift_with_warning`` —
   ``w_r=0.8, w_s=0.4`` (sum=1.2, drift>0.01) writes the file with
   202 + emits a WARNING log line (the brief: warns but accepts).

The cancel test uses a custom sweep fake whose tick loop polls the
``cancel_event`` every 50 ms — that's the contract the brief's
"per-tick check granularity, no SIGKILL" CEO direction locks. The
production sweep checks between configs (the OUTER seam) since the
inner ``run_replay`` loop's per-tick hook lands in sprint_12.

Tests use FastAPI's :class:`TestClient` (sync wrapper around httpx).
No real network, chain, or LLM is touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner
from agent.server.main import (
    AGENT_CONFIG_FILENAME,
    create_app,
)
from agent.server.models import (
    AgentConfigureRequest,
    StartingWeightConfig,
)
from agent.server.runner import (
    BACKTEST_RESULT_FILENAME,
    AgentRunner,
    BacktestRegistry,
)
from tests.agent.server.conftest import (
    FakeLoop,
    build_app,
)

pytestmark = pytest.mark.usefixtures("set_dashboard_token")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _wait_until(predicate: Any, *, timeout: float = 5.0) -> bool:
    """Poll ``predicate`` until truthy OR timeout. Returns last result.

    Mirrors the helper in ``test_control_plane.py`` and
    ``test_error_capture.py`` so each test module stays independently
    runnable. Default timeout is 5 s — the cancel-granularity contract
    the brief locks.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _valid_config(label: str, *, rho: float = 0.5) -> dict[str, Any]:
    """Build a valid :class:`StartingWeightConfig` dict body."""
    return {
        "label": label,
        "w_r": 0.5,
        "w_s": 0.5,
        "alpha": 0.4,
        "beta": 0.7,
        "rho": rho,
    }


# --------------------------------------------------------------------------- #
# 1. typed body forwards configs through to sweep
# --------------------------------------------------------------------------- #


def test_backtest_run_typed_body_forwards_two_configs(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """``configs=[A, B]`` → registry hands the sweep exactly those two
    StartingWeightConfig objects, and the resulting ``results.json``
    carries 2 entries (NOT the 4 defaults).

    We assert at two layers:
    - the fake sweep's ``last_configs`` captures the two configs;
    - the resulting ``results.json``'s ``configs_run`` == 2.
    """
    app, _, sweep = build_app(tmp_path=tmp_path)
    body = {
        "configs": [
            _valid_config("A", rho=0.4),
            _valid_config("B", rho=0.6),
        ],
        "operator_note": "two-config typed-body smoke",
    }
    with TestClient(app) as client:
        resp = client.post(
            "/api/backtest/run", headers=auth_headers, json=body
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]
        assert _wait_until(
            lambda: (tmp_path / "backtests" / run_id / "results.json").exists()
        )
        # Sweep saw both configs and the operator_note.
        assert len(sweep.last_configs) == 2
        labels = [cfg.label for cfg in sweep.last_configs]
        assert labels == ["A", "B"]
        assert sweep.last_operator_note == "two-config typed-body smoke"
        # results.json reflects 2 configs.
        result_resp = client.get(
            f"/api/backtest/{run_id}", headers=auth_headers
        )
        assert result_resp.status_code == 200
        result_body = result_resp.json()
        assert result_body["configs_run"] == 2


# --------------------------------------------------------------------------- #
# 2. empty body keeps default sweep
# --------------------------------------------------------------------------- #


def test_backtest_run_empty_body_falls_back_to_defaults(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Empty body OR ``{"configs": []}`` falls back to the default sweep.

    The fake sweep records 0 configs through the typed-body path; the
    production sweep runner would substitute :data:`DEFAULT_SWEEP_WEIGHTS`.
    This test asserts the route forwards the empty list without raising —
    the production fallback is exercised by the sweep_runner test suite.
    """
    sweep_payload = {"configs_run": 4, "results": ["a", "b", "c", "d"]}
    app, _, sweep = build_app(tmp_path=tmp_path, sweep_payload=sweep_payload)
    with TestClient(app) as client:
        # Variant 1: completely empty body.
        resp1 = client.post("/api/backtest/run", headers=auth_headers, json={})
        assert resp1.status_code == 202, resp1.text
        run_id_1 = resp1.json()["run_id"]
        assert _wait_until(
            lambda: (tmp_path / "backtests" / run_id_1 / "results.json").exists()
        )
        # Empty configs → fake records [] but the registry call still
        # fired with configs=[].
        assert sweep.last_configs == []

        # Variant 2: explicit empty list. We capture run_id_2 + wait
        # for the second results.json to land BEFORE asserting on the
        # FakeSweepRunner state — without this wait the assertion fires
        # in a race window where variant 1's bookkeeping is still the
        # most recent invocation (variant 2's background task hasn't
        # been scheduled yet under loop contention from the broader
        # ``tests/agent/`` suite).
        resp2 = client.post(
            "/api/backtest/run",
            headers=auth_headers,
            json={"configs": [], "operator_note": "default sweep"},
        )
        assert resp2.status_code == 202, resp2.text
        run_id_2 = resp2.json()["run_id"]
        assert _wait_until(
            lambda: (tmp_path / "backtests" / run_id_2 / "results.json").exists()
        )
        assert sweep.last_configs == []
        assert sweep.last_operator_note == "default sweep"


# --------------------------------------------------------------------------- #
# 3. invalid rho on backtest body is rejected
# --------------------------------------------------------------------------- #


def test_backtest_run_invalid_rho_rejected(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """``rho=2.0`` on one StartingWeightConfig short-circuits at the
    Pydantic layer with HTTP 422; the sweep registry sees zero
    invocations because the route never calls submit().
    """
    app, _, sweep = build_app(tmp_path=tmp_path)
    body = {
        "configs": [
            _valid_config("A"),
            {**_valid_config("B"), "rho": 2.0},
        ]
    }
    with TestClient(app) as client:
        resp = client.post(
            "/api/backtest/run", headers=auth_headers, json=body
        )
        # FastAPI defaults: Pydantic validation -> 422.
        assert resp.status_code == 422, resp.text
        assert sweep.invocations == []


# --------------------------------------------------------------------------- #
# 4. cancel flips status to 'cancelled' within 5s
# --------------------------------------------------------------------------- #


async def _polling_sweep(
    *,
    output_dir: Path,
    run_id: str,
    cancel_event: asyncio.Event | None = None,
    **_unused: Any,
) -> None:
    """Sweep fake that polls ``cancel_event`` every 50 ms ("tick").

    When the latch is set, raises :class:`asyncio.CancelledError` so
    :func:`agent.server.runner._safe_run` writes the cancelled envelope
    to ``output_dir/results.json``. The fake never writes a success
    file — if cancel never fires the test will time out at the
    polling helper which is the desired failure mode for the cancel
    contract.
    """
    del output_dir, run_id
    for _ in range(200):  # up to 10 s — well beyond the 5 s cap
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        await asyncio.sleep(0.05)
    # If we hit the loop bound, fall through to a success write so
    # the test can detect a "cancel never observed" failure cleanly.
    raise RuntimeError("polling sweep ran to completion — cancel missed")


def test_backtest_cancel_flips_status_to_cancelled(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Cancel a running sweep — ``results.json`` carries
    ``status='cancelled'`` within ≤ 5 s wall-clock.
    """
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    backtest_root = tmp_path / "backtests"
    backtest_root.mkdir(parents=True, exist_ok=True)
    runner = AgentRunner(
        loop_factory=lambda: FakeLoop(
            state_dir=state_dir,
            synthetic_decisions=[],
            synthetic_reflections=[],
            synthetic_proposals=[],
            snapshot=None,
        ),
        state_dir=state_dir,
        stop_timeout_seconds=2.0,
    )
    registry = BacktestRegistry(
        sweep_runner=_polling_sweep, output_root=backtest_root
    )
    app = create_app(
        agent_runner=runner,
        backtest_registry=registry,
        runtime_agent=RuntimeAgentRunner(),
        sse_poll_interval_seconds=0.02,
        sse_stop_after_seconds=0.2,
    )
    with TestClient(app) as client:
        # Kick off a sweep.
        resp = client.post("/api/backtest/run", headers=auth_headers, json={})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]

        # Give the background task a moment to register.
        time.sleep(0.1)

        cancel_resp = client.post(
            f"/api/backtest/{run_id}/cancel", headers=auth_headers
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        cancel_body = cancel_resp.json()
        assert cancel_body["run_id"] == run_id
        assert cancel_body["cancelled"] is True

        # Wait for the cancelled envelope to land — must complete in ≤ 5 s.
        result_path = backtest_root / run_id / BACKTEST_RESULT_FILENAME
        deadline = time.monotonic() + 5.0
        envelope: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if result_path.exists():
                envelope = json.loads(result_path.read_text(encoding="utf-8"))
                if envelope.get("status") == "cancelled":
                    break
            time.sleep(0.05)
        assert envelope is not None, "results.json never appeared"
        assert envelope["status"] == "cancelled", envelope
        assert envelope["error"] is None


# --------------------------------------------------------------------------- #
# 5. cancel of unknown id returns 404
# --------------------------------------------------------------------------- #


def test_backtest_cancel_unknown_id_returns_404(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Cancel of an unknown run_id MUST return 404 (NOT 500).

    The dashboard polls cancel on stale history-pane ids; a 500 here
    would mark the UI in a broken state. 404 lets it gracefully retire
    the stale row.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/backtest/does-not-exist/cancel", headers=auth_headers
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "unknown run_id"


# --------------------------------------------------------------------------- #
# 6. cancel idempotent — second call still 200
# --------------------------------------------------------------------------- #


def test_backtest_cancel_idempotent(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Second cancel on the same run_id still returns 200 + cancelled=True.

    The latch is one-way; a second cancel is a no-op at the event
    level but the registry still has the record so the response is
    indistinguishable from the first call. The dashboard relies on
    this to retry a cancel without conditional logic.
    """
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    backtest_root = tmp_path / "backtests"
    backtest_root.mkdir(parents=True, exist_ok=True)
    runner = AgentRunner(
        loop_factory=lambda: FakeLoop(
            state_dir=state_dir,
            synthetic_decisions=[],
            synthetic_reflections=[],
            synthetic_proposals=[],
            snapshot=None,
        ),
        state_dir=state_dir,
    )
    registry = BacktestRegistry(
        sweep_runner=_polling_sweep, output_root=backtest_root
    )
    app = create_app(
        agent_runner=runner,
        backtest_registry=registry,
        runtime_agent=RuntimeAgentRunner(),
        sse_poll_interval_seconds=0.02,
        sse_stop_after_seconds=0.2,
    )
    with TestClient(app) as client:
        run_id = client.post(
            "/api/backtest/run", headers=auth_headers, json={}
        ).json()["run_id"]
        time.sleep(0.1)
        first = client.post(
            f"/api/backtest/{run_id}/cancel", headers=auth_headers
        )
        second = client.post(
            f"/api/backtest/{run_id}/cancel", headers=auth_headers
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["cancelled"] is True
        assert second.json()["cancelled"] is True


# --------------------------------------------------------------------------- #
# 7. configure writes agent_config.json atomically
# --------------------------------------------------------------------------- #


def test_configure_writes_agent_config_atomically(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """``POST /api/agent/configure`` returns 202 and writes
    ``<state_dir>/agent_config.json`` atomically. The response body
    echoes the persisted config + absolute path.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    body = {"starting_weights": _valid_config("staged")}
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/configure", headers=auth_headers, json=body
        )
        assert resp.status_code == 202, resp.text
        body_resp = resp.json()
        assert body_resp["status"] == "accepted"
        assert body_resp["starting_weights"]["label"] == "staged"

        # File on disk matches the request.
        config_path = tmp_path / "sandbox" / AGENT_CONFIG_FILENAME
        assert config_path.exists()
        # Atomic write left no .tmp behind.
        assert not config_path.with_suffix(config_path.suffix + ".tmp").exists()
        on_disk = json.loads(config_path.read_text(encoding="utf-8"))
        assert on_disk["label"] == "staged"
        assert on_disk["rho"] == pytest.approx(0.5)
        assert body_resp["persisted_path"].endswith(AGENT_CONFIG_FILENAME)


# --------------------------------------------------------------------------- #
# 8. configure rejects rho out of range with 400
# --------------------------------------------------------------------------- #


def test_configure_rejects_rho_out_of_range(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """``rho=1.5`` on configure returns HTTP 400 with the validation
    error detail. NO ``agent_config.json`` is written.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    body = {"starting_weights": _valid_config("bad", rho=1.5)}
    with TestClient(app) as client:
        resp = client.post(
            "/api/agent/configure", headers=auth_headers, json=body
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        # Detail must surface a validation_errors envelope so the
        # dashboard can render the per-field error message.
        assert "validation_errors" in detail
        # And nothing was written.
        config_path = tmp_path / "sandbox" / AGENT_CONFIG_FILENAME
        assert not config_path.exists()


# --------------------------------------------------------------------------- #
# 9. configure accepts weight-sum drift with WARN log
# --------------------------------------------------------------------------- #


def test_configure_accepts_weight_sum_drift_with_warning(
    tmp_path: Path,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``w_r=0.8 + w_s=0.4`` (sum=1.2, drift=0.2 > 0.01 tolerance) is
    accepted with 202 + a WARN log line; the file is written with the
    operator's original ratio preserved (the to_weights() projection
    renormalises only at the downstream sweep boundary).
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    body = {
        "starting_weights": {
            "label": "drifted",
            "w_r": 0.8,
            "w_s": 0.4,
            "alpha": 0.4,
            "beta": 0.7,
            "rho": 0.5,
        }
    }
    with caplog.at_level(logging.WARNING, logger="agent.server.models"):
        with TestClient(app) as client:
            resp = client.post(
                "/api/agent/configure", headers=auth_headers, json=body
            )
            assert resp.status_code == 202, resp.text
    # WARN log fired with the drift message.
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "w_r + w_s" in r.getMessage() and "drifted" in r.getMessage()
        for r in warn_records
    ), [r.getMessage() for r in warn_records]
    # Original ratio preserved on disk.
    config_path = tmp_path / "sandbox" / AGENT_CONFIG_FILENAME
    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert on_disk["w_r"] == pytest.approx(0.8)
    assert on_disk["w_s"] == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# Coverage shim — exercise the StartingWeightConfig.from_weights round-trip
# so the registry contract test can assume the inverse projection works
# without a separate model test file.
# --------------------------------------------------------------------------- #


def test_starting_weight_config_round_trip_through_weights() -> None:
    """``cfg → weights → cfg`` preserves ``rho`` exactly.

    Not in the brief's 9-test acceptance list but kept here as a
    coverage shim — the dashboard reads ``Weights`` from the durable
    snapshot and projects back to :class:`StartingWeightConfig` for
    the editor. The forward+inverse must be a no-op on canonical
    technical-led collapse inputs.
    """
    cfg = StartingWeightConfig(
        label="rt", w_r=0.5, w_s=0.5, alpha=0.4, beta=1.0, rho=0.3
    )
    weights = cfg.to_weights()
    cfg2 = StartingWeightConfig.from_weights("rt", weights)
    assert cfg2.rho == pytest.approx(0.3)
    assert cfg2.w_r == pytest.approx(0.5)
    assert cfg2.w_s == pytest.approx(0.5)
    assert cfg2.alpha == pytest.approx(0.4)
    assert cfg2.beta == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Module-local helpers for typed-body construction (re-exported in the
# delivery_report sample as canonical demo payloads).
# --------------------------------------------------------------------------- #


def _assert_request_model_validates(body: dict[str, Any]) -> None:
    """Smoke that the :class:`AgentConfigureRequest` round-trips
    through ``model_validate`` — pulled into its own helper because
    each test that wants a typed model uses the same idiom and the
    failure message points at the body dict cleanly."""
    AgentConfigureRequest.model_validate(body)
