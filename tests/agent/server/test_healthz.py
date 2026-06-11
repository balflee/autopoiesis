"""Tests for the unauthed ``GET /healthz`` liveness probe (T-B-028).

Six tests covering every acceptance criterion the brief locks:

1. happy path           — 200 + {status:'ok', uptime_s:int>=0, last_tick_ts:str|None}
2. unauthed             — works WITHOUT an ``Authorization`` header (Railway
                          + Docker healthchecks ship no creds)
3. no snapshot          — cold-start install → last_tick_ts is ``None``
4. snapshot present     — last_tick_ts reflects ``snapshot_ts`` field
5. snapshot torn/garbage — malformed JSON does NOT raise; collapses to ``None``
6. uptime monotonic     — repeated polls show non-decreasing uptime_s

Tests use FastAPI's :class:`fastapi.testclient.TestClient`. No real
network, chain, or LLM is touched. The fixtures (``build_app``,
``FakeLoop``) are shared with the T-B-027 control-plane suite via
:mod:`tests.agent.server.conftest`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.agent.server.conftest import build_app


# --------------------------------------------------------------------------- #
# 1. happy path — 200 + correct envelope
# --------------------------------------------------------------------------- #


def test_healthz_returns_200_and_envelope(tmp_path: Path) -> None:
    """GET /healthz → 200 with {status, uptime_s, last_tick_ts}.

    No bearer-token fixture: /healthz is intentionally unauthed so
    Railway + Docker healthchecks can hit it.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"status", "uptime_s", "last_tick_ts"}
    assert body["status"] == "ok"
    assert isinstance(body["uptime_s"], int)
    assert body["uptime_s"] >= 0
    assert body["last_tick_ts"] is None  # no snapshot was written


# --------------------------------------------------------------------------- #
# 2. unauthed — works WITHOUT Authorization header even when token configured
# --------------------------------------------------------------------------- #


def test_healthz_works_without_authorization_header(tmp_path: Path) -> None:
    """The /healthz route is OUTSIDE the AuthDep tree.

    Even when ``DASHBOARD_API_TOKEN`` is set (i.e. /api/agent/status
    would 401 without a bearer token), /healthz still returns 200 with
    no headers — proving Railway's anonymous healthcheck will succeed.
    """
    sentinel = object()
    prev: str | object = os.environ.get("DASHBOARD_API_TOKEN", sentinel)
    os.environ["DASHBOARD_API_TOKEN"] = "configured-token-but-not-sent"
    try:
        app, _, _ = build_app(tmp_path=tmp_path)
        with TestClient(app) as client:
            # Sanity: an /api route DOES 401 without the header.
            api_response = client.get("/api/agent/status")
            assert api_response.status_code == 401, api_response.text
            # /healthz is unauthed even with the token configured.
            health_response = client.get("/healthz")
            assert health_response.status_code == 200, health_response.text
            assert health_response.json()["status"] == "ok"
    finally:
        if prev is sentinel:
            os.environ.pop("DASHBOARD_API_TOKEN", None)
        elif isinstance(prev, str):
            os.environ["DASHBOARD_API_TOKEN"] = prev


# --------------------------------------------------------------------------- #
# 3. cold-start install — no snapshot file → last_tick_ts is None
# --------------------------------------------------------------------------- #


def test_healthz_last_tick_ts_is_none_when_no_snapshot(tmp_path: Path) -> None:
    """No ``agent_state.json`` on disk → ``last_tick_ts`` is ``None``."""
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["last_tick_ts"] is None


# --------------------------------------------------------------------------- #
# 4. snapshot present — last_tick_ts reflects snapshot_ts
# --------------------------------------------------------------------------- #


def test_healthz_reads_snapshot_ts_when_snapshot_present(tmp_path: Path) -> None:
    """``snapshot_ts`` field of the durable snapshot surfaces as ``last_tick_ts``."""
    app, _, _ = build_app(tmp_path=tmp_path)
    # Pre-seed the snapshot the runner reads from.
    snapshot_path = tmp_path / "sandbox" / "agent_state.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "phase": "PHASE_2_EXTENDED",
                "breath": 87.5,
                "snapshot_ts": "2026-05-27T18:30:00+00:00",
                "weights": {},
                "pending_proposals": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["last_tick_ts"] == "2026-05-27T18:30:00+00:00"


# --------------------------------------------------------------------------- #
# 5. malformed snapshot — does NOT crash; collapses to None
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "garbage",
    [
        "not-json-at-all",
        '{"snapshot_ts": 42}',  # wrong type
        "[]",                    # wrong root shape (list not dict)
        '{"snapshot_ts": null}', # explicit null
    ],
    ids=["garbage_string", "wrong_type", "wrong_root", "explicit_null"],
)
def test_healthz_handles_malformed_snapshot(tmp_path: Path, garbage: str) -> None:
    """A torn / garbage snapshot must NOT 500 the healthcheck.

    The probe MUST stay 200 even if the agent's state file is mid-write
    or schema-skewed — otherwise Railway would mark the deploy
    unhealthy + restart it during a snapshot write, which would corrupt
    the file further. Collapsing to ``last_tick_ts=None`` is the
    fail-soft contract.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    snapshot_path = tmp_path / "sandbox" / "agent_state.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(garbage, encoding="utf-8")
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200, response.text
    assert response.json()["last_tick_ts"] is None


# --------------------------------------------------------------------------- #
# 6. uptime monotonic — repeated polls return non-decreasing seconds
# --------------------------------------------------------------------------- #


def test_healthz_uptime_is_monotonic(tmp_path: Path) -> None:
    """Two consecutive /healthz reads → second uptime_s ≥ first.

    Uses :func:`time.monotonic` internally, so even if the system clock
    is stepped between calls the value cannot regress.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        first = client.get("/healthz").json()
        # Sleep a tick to make the assertion meaningful on fast CI boxes.
        time.sleep(1.01)
        second = client.get("/healthz").json()
    assert second["uptime_s"] >= first["uptime_s"]
    assert second["uptime_s"] >= 1
