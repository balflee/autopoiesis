"""End-to-end tests for the FastAPI control plane (T-B-027).

Nine tests covering every acceptance criterion the brief locks:

1. start happy path        — POST /api/agent/start returns 202 + run_id
2. start conflict          — POST /api/agent/start while running → 409
3. stop idempotent         — POST /api/agent/stop twice both 200; no error
4. status reads disk       — GET /api/agent/status returns snapshot fields
5. unauthorized no token   — every route returns 401 with no Authorization
6. unauthorized wrong tok  — wrong bearer → 401
7. CORS allow regex        — vercel.app + localhost allowed; evil.com denied
8. backtest end-to-end     — POST + GET round-trip via fake sweep
9. SSE event ordering      — events emitted in append order with file-stem tag

Tests use FastAPI's :class:`fastapi.testclient.TestClient` (sync wrapper
around httpx). No real network, chain, or LLM is touched.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.agent.server.conftest import (
    FakeLoop,
    build_app,
)


pytestmark = pytest.mark.usefixtures("set_dashboard_token")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _wait_until(predicate: Any, *, timeout: float = 2.0) -> bool:
    """Poll ``predicate`` until truthy OR timeout. Returns the last result."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


# --------------------------------------------------------------------------- #
# 1. start happy path
# --------------------------------------------------------------------------- #


def test_start_returns_202_and_run_id(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """POST /api/agent/start → 202 + StartResponse with a fresh run_id.

    The fake loop's ``ran_count`` increments once when the background
    task picks up — we poll the runner via /status until ``running``
    flips true to confirm the task actually scheduled.
    """
    app, loop, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/agent/start", headers=auth_headers)
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["status"] == "accepted"
        assert isinstance(payload["run_id"], str) and len(payload["run_id"]) == 32

        # Wait for the background task to register itself + run.
        assert _wait_until(lambda: loop.ran_count >= 1)
        status_resp = client.get("/api/agent/status", headers=auth_headers)
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["running"] is True
        assert body["run_id"] == payload["run_id"]


# --------------------------------------------------------------------------- #
# 2. start conflict
# --------------------------------------------------------------------------- #


def test_start_returns_409_when_already_running(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """A second POST /api/agent/start while running → 409 + existing
    run_id surfaced in the response body so the dashboard can resume."""
    app, loop, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        first = client.post("/api/agent/start", headers=auth_headers)
        assert first.status_code == 202
        first_run_id = first.json()["run_id"]
        # Don't race — wait for the runner to actually mark itself running.
        assert _wait_until(lambda: loop.ran_count >= 1)

        second = client.post("/api/agent/start", headers=auth_headers)
        assert second.status_code == 409
        body = second.json()
        assert body["detail"] == "agent already running"
        assert body["run_id"] == first_run_id


# --------------------------------------------------------------------------- #
# 3. stop idempotent
# --------------------------------------------------------------------------- #


def test_stop_is_idempotent(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Stop after start → 200 + final_state_path; stop again → 200 still."""
    snapshot = {
        "snapshot_ts": "2026-05-27T00:00:00+00:00",
        "phase": "PHASE_2_APPRENTICE",
        "breath": 0.85,
        "bankroll_usd": 100.0,
        "phase_age_days": 0.5,
        "last_tick": 0,
        "weights": None,
        "pending_proposals": [],
    }
    loop = FakeLoop(
        state_dir=tmp_path / "sandbox",
        synthetic_decisions=[],
        synthetic_reflections=[],
        synthetic_proposals=[],
        snapshot=snapshot,
    )
    app, _, _ = build_app(tmp_path=tmp_path, loop=loop)
    with TestClient(app) as client:
        client.post("/api/agent/start", headers=auth_headers)
        assert _wait_until(lambda: loop.ran_count >= 1)
        # Give the loop a moment to write the snapshot.
        assert _wait_until(
            lambda: (tmp_path / "sandbox" / "agent_state.json").exists()
        )

        stop_resp = client.post("/api/agent/stop", headers=auth_headers)
        assert stop_resp.status_code == 200, stop_resp.text
        body = stop_resp.json()
        assert body["status"] == "stopped"
        assert body["final_state_path"] is not None
        assert "agent_state.json" in body["final_state_path"]

        # Second stop — idempotent, also 200, also same shape.
        stop_resp2 = client.post("/api/agent/stop", headers=auth_headers)
        assert stop_resp2.status_code == 200
        body2 = stop_resp2.json()
        assert body2["status"] == "stopped"
        assert body2["final_state_path"] == body["final_state_path"]

        # And the loop's CancelledError path fired exactly once.
        assert loop.cancelled_count == 1


# --------------------------------------------------------------------------- #
# 4. status reads disk
# --------------------------------------------------------------------------- #


def test_status_reads_snapshot_fields(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """All six brief-locked status fields surface correctly from the
    on-disk snapshot."""
    snapshot = {
        "snapshot_ts": "2026-05-27T03:14:15+00:00",
        "phase": "PHASE_2_APPRENTICE",
        "breath": 0.73,
        "bankroll_usd": 132.5,
        "phase_age_days": 1.25,
        "last_tick": 17,
        "weights": {
            "w_r": 0.5,
            "w_s": 0.5,
            "alpha": [0.34, 0.33, 0.33],
            "beta": [1.0, 0.0],
            "rho": 0.6,
        },
        "pending_proposals": ["prop-a", "prop-b"],
    }
    loop = FakeLoop(
        state_dir=tmp_path / "sandbox",
        synthetic_decisions=[],
        synthetic_reflections=[],
        synthetic_proposals=[],
        snapshot=snapshot,
    )
    app, _, _ = build_app(tmp_path=tmp_path, loop=loop, llm_cost=3.14)
    # Write the snapshot synchronously BEFORE the test client starts,
    # so /status reads it without depending on async loop wake-up.
    (tmp_path / "sandbox" / "agent_state.json").write_text(
        json.dumps(snapshot, sort_keys=True), encoding="utf-8"
    )
    with TestClient(app) as client:
        resp = client.get("/api/agent/status", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["phase"] == "PHASE_2_APPRENTICE"
        assert body["breath"] == pytest.approx(0.73)
        assert body["last_tick_ts"] == "2026-05-27T03:14:15+00:00"
        assert body["current_weights"]["w_r"] == pytest.approx(0.5)
        assert body["llm_cost_usd_this_month"] == pytest.approx(3.14)
        assert body["pending_proposals_count"] == 2
        assert body["running"] is False  # never started
        assert body["run_id"] is None


# --------------------------------------------------------------------------- #
# 5. unauthorized — no token
# --------------------------------------------------------------------------- #


def test_unauthorized_without_token(tmp_path: Path) -> None:
    """Every route returns 401 + ``{"detail": "unauthorized"}`` when the
    Authorization header is absent. We assert against the four routes
    that take simple GETs/POSTs (SSE 401 covered indirectly — auth
    fires before the StreamingResponse builds)."""
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        for method, url in [
            ("post", "/api/agent/start"),
            ("post", "/api/agent/stop"),
            ("get", "/api/agent/status"),
            ("post", "/api/backtest/run"),
            ("get", "/api/backtest/anything"),
            ("get", "/api/state/stream"),
        ]:
            resp = client.request(method, url)
            assert resp.status_code == 401, (method, url, resp.text)
            assert resp.json() == {"detail": "unauthorized"}


# --------------------------------------------------------------------------- #
# 6. unauthorized — wrong token
# --------------------------------------------------------------------------- #


def test_unauthorized_with_wrong_token(tmp_path: Path) -> None:
    """A bearer token that doesn't match the configured value → 401
    with the SAME shape as the missing-token case (no oracle leak)."""
    app, _, _ = build_app(tmp_path=tmp_path)
    bad_headers = {"Authorization": "Bearer wrong-token"}
    with TestClient(app) as client:
        resp = client.get("/api/agent/status", headers=bad_headers)
        assert resp.status_code == 401
        assert resp.json() == {"detail": "unauthorized"}

        # Also: a token without the "Bearer " prefix is rejected.
        bare = {"Authorization": "test-token-not-a-real-secret"}
        resp_bare = client.get("/api/agent/status", headers=bare)
        assert resp_bare.status_code == 401


# --------------------------------------------------------------------------- #
# 7. CORS — allowed regex + denied origin
# --------------------------------------------------------------------------- #


def test_cors_allows_vercel_and_localhost_rejects_others(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """CORS preflight from ``https://something.vercel.app`` and
    ``http://localhost:3000`` succeed; from ``https://evil.com`` no
    ``Access-Control-Allow-Origin`` header is returned."""
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        for origin in (
            "https://dashboard.vercel.app",
            "https://branch-preview.vercel.app",
            "http://localhost:3000",
            "http://localhost",
        ):
            resp = client.options(
                "/api/agent/status",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
            assert resp.headers.get("access-control-allow-origin") == origin, (
                origin, resp.headers
            )

        # Evil origin: starlette CORSMiddleware omits the
        # access-control-allow-origin header rather than 4xx-ing the
        # preflight. The browser enforces the rejection client-side.
        evil_resp = client.options(
            "/api/agent/status",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert "access-control-allow-origin" not in {
            k.lower() for k in evil_resp.headers.keys()
        }


# --------------------------------------------------------------------------- #
# 8. backtest run + result round-trip
# --------------------------------------------------------------------------- #


def test_backtest_run_and_fetch_result(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """POST /api/backtest/run → 202 + run_id; GET /api/backtest/{run_id}
    eventually returns the results.json the fake sweep wrote."""
    sweep_payload = {
        "configs_run": 2,
        "results": [{"config_id": "demo-a"}, {"config_id": "demo-b"}],
        "seed": 0,
    }
    app, _, sweep = build_app(tmp_path=tmp_path, sweep_payload=sweep_payload)
    with TestClient(app) as client:
        resp = client.post(
            "/api/backtest/run",
            headers=auth_headers,
            json={"operator_note": "smoke run"},
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]
        assert isinstance(run_id, str) and len(run_id) == 32

        # Wait for the fake sweep to materialise results.json.
        assert _wait_until(
            lambda: (tmp_path / "backtests" / run_id / "results.json").exists()
        )
        assert sweep.invocations == [run_id]

        result_resp = client.get(
            f"/api/backtest/{run_id}", headers=auth_headers
        )
        assert result_resp.status_code == 200, result_resp.text
        result_body = result_resp.json()
        assert result_body["configs_run"] == 2
        assert result_body["run_id"] == run_id

        # Unknown run id → 404.
        miss = client.get(
            "/api/backtest/does-not-exist", headers=auth_headers
        )
        assert miss.status_code == 404


# --------------------------------------------------------------------------- #
# 9. SSE event ordering
# --------------------------------------------------------------------------- #


def test_sse_stream_emits_events_in_order(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """SSE generator yields events in the order the JSONL lines were
    appended, with ``event:`` set to the file stem and ``data:`` the
    parsed JSON.

    We pre-populate the three streams BEFORE opening the SSE connection
    so the bytes are guaranteed to be flushed; the SSE generator's poll
    interval is set tight by build_app so the stop bound fires before
    the test times out.

    The decisions stream also ends in an UNTERMINATED trailing line
    (mid-write at sample time) — the SSE handler must NOT emit it AND
    must NOT advance the read offset past it, so a subsequent poll can
    pick it up once the newline lands. The total event count is exactly
    4 because the torn line stays buffered.
    """
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decisions.jsonl").write_text(
        json.dumps({"tick": 0, "kind": "NO_BET"}, sort_keys=True) + "\n"
        + json.dumps({"tick": 1, "kind": "BET"}, sort_keys=True) + "\n"
        # Trailing torn line — no terminating newline; must be deferred.
        + '{"tick": 2, "kind": "BE',
        encoding="utf-8",
    )
    (state_dir / "reflections.jsonl").write_text(
        json.dumps({"tick": 1, "kind": "tick_interval"}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (state_dir / "proposals.jsonl").write_text(
        json.dumps({"proposal_id": "p-0"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    app, _, _ = build_app(
        tmp_path=tmp_path,
        sse_poll_interval_seconds=0.01,
        sse_stop_after_seconds=0.15,
    )
    with TestClient(app) as client:
        resp = client.get("/api/state/stream", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text

        # Each event is "event: <name>\ndata: <json>\n\n". Strip and parse.
        records: list[tuple[str, dict[str, Any]]] = []
        for block in body.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            lines = block.split("\n")
            event_line = next(line for line in lines if line.startswith("event:"))
            data_line = next(line for line in lines if line.startswith("data:"))
            event_name = event_line.removeprefix("event:").strip()
            data_obj = json.loads(data_line.removeprefix("data:").strip())
            records.append((event_name, data_obj))

        # Per-file order is the brief invariant: decisions yields tick 0
        # before tick 1; reflections + proposals yield their single row.
        decisions_order = [
            obj for name, obj in records if name == "decisions"
        ]
        assert [r["tick"] for r in decisions_order] == [0, 1]

        # Every event_name is one of the three file stems.
        assert {name for name, _ in records} == {
            "decisions", "reflections", "proposals"
        }
        # And the three streams together produced exactly 4 events.
        assert len(records) == 4
