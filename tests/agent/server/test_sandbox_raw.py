"""Tests for ``GET /api/sandbox/raw`` — the off-box dashboard data path.

The route exists so a dashboard deployed where the backend's state volume
is NOT mounted (Vercel ``/living`` while the loop runs on Railway) can still
fold a live SandboxStateBundle: it hands the raw file bytes over the wire and
the dashboard folds them with the same TypeScript fold the local-fs path uses.

Coverage:

1. authed empty dir   — 200, envelope shape, all 5 files null, dir_exists True
2. authed seeded      — the raw text of each seeded file round-trips verbatim
3. unauthed           — 401 (the route is inside the AuthDep tree)
4. helper / missing   — ``_read_sandbox_raw_files`` on an absent dir →
                        dir_exists False, every file null (no raise)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.server.main import _SANDBOX_RAW_FILENAMES, _read_sandbox_raw_files
from tests.agent.server.conftest import build_app


# --------------------------------------------------------------------------- #
# 1. authed empty dir — 200 + envelope, all files null
# --------------------------------------------------------------------------- #


def test_sandbox_raw_empty_dir(
    tmp_path: Path,
    set_dashboard_token: None,
    auth_headers: dict[str, str],
) -> None:
    """build_app mkdirs state_dir but writes no files → dir_exists True,
    every file null."""
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/sandbox/raw", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"dir_exists", "files"}
    assert body["dir_exists"] is True
    assert set(body["files"]) == set(_SANDBOX_RAW_FILENAMES)
    assert all(v is None for v in body["files"].values())


# --------------------------------------------------------------------------- #
# 2. authed seeded — raw text round-trips verbatim
# --------------------------------------------------------------------------- #


def test_sandbox_raw_returns_seeded_text(
    tmp_path: Path,
    set_dashboard_token: None,
    auth_headers: dict[str, str],
) -> None:
    """Each seeded file's raw UTF-8 text surfaces unchanged.

    The dashboard's TS fold parses these strings, so they must be passed
    through byte-for-byte (NOT re-serialised) — a torn JSONL tail must
    reach the fold's torn-line skip, not be silently fixed up here.
    """
    app, _, _ = build_app(tmp_path=tmp_path)
    state_dir = tmp_path / "sandbox"
    snapshot_text = '{"breath": 88, "incarnation_number": 2}'
    treasury_text = (
        '{"type": "tribute", "success": true, "amount_usd": 500.0}\n'
        '{"type": "tithe", "paid_usd": 12.5}\n'
    )
    (state_dir / "agent_state.json").write_text(snapshot_text, encoding="utf-8")
    (state_dir / "gods_treasury.jsonl").write_text(treasury_text, encoding="utf-8")

    with TestClient(app) as client:
        body = client.get("/api/sandbox/raw", headers=auth_headers).json()

    assert body["dir_exists"] is True
    assert body["files"]["agent_state.json"] == snapshot_text
    assert body["files"]["gods_treasury.jsonl"] == treasury_text
    # Unseeded files stay null.
    assert body["files"]["decisions.jsonl"] is None
    assert body["files"]["deaths.jsonl"] is None


# --------------------------------------------------------------------------- #
# 3. unauthed — 401
# --------------------------------------------------------------------------- #


def test_sandbox_raw_requires_auth(
    tmp_path: Path,
    set_dashboard_token: None,
) -> None:
    """No bearer token → 401 (the raw state surface stays behind /api auth)."""
    app, _, _ = build_app(tmp_path=tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/sandbox/raw")
    assert response.status_code == 401, response.text


# --------------------------------------------------------------------------- #
# 4. helper — absent dir → dir_exists False, no raise
# --------------------------------------------------------------------------- #


def test_read_sandbox_raw_files_missing_dir(tmp_path: Path) -> None:
    """``_read_sandbox_raw_files`` on a dir that does not exist must NOT
    raise — it reports dir_exists False + every file null so the dashboard
    paints a cold_boot banner instead of crashing."""
    missing = tmp_path / "never-created"
    payload = _read_sandbox_raw_files(state_dir=missing)
    assert payload["dir_exists"] is False
    assert set(payload["files"]) == set(_SANDBOX_RAW_FILENAMES)
    assert all(v is None for v in payload["files"].values())
