"""End-to-end tests for the proposal approve/reject FastAPI routes — T-B-031.

Seven tests covering the brief's acceptance matrix:

1. ``test_approve_weight_delta_pickup`` —  approving a ``weight_delta``
   proposal enqueues the delta on the :class:`RuntimeAgentRunner` seam;
   a fake "loop tick" calling :meth:`drain_pending_deltas` recovers it.
2. ``test_approve_new_signal_idea_writes_todo`` — approving a
   ``new_signal_idea`` writes ONE line to ``proposal_todos.jsonl`` and
   never touches the runtime seam.
3. ``test_approve_prompt_tweak_writes_todo`` — same shape for
   ``prompt_tweak``.
4. ``test_reject_with_reason_persists`` — POST body with ``reason``
   surfaces on the rejected audit row.
5. ``test_double_approve_returns_409`` — a second approve after the
   first succeeds returns 409 + does NOT re-enqueue.
6. ``test_proposal_not_found_returns_404`` — unknown id → 404.
7. ``test_malformed_body_returns_422`` — bad JSON / extra key on the
   reject body → 422.

The fixtures + builder come from :mod:`tests.agent.server.conftest`;
this suite only adds a tiny helper to write seed proposals into the
sandbox state dir before the route fires.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.data.sandbox_state import PROPOSALS_FILENAME
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner
from agent.server.main import PROPOSAL_TODOS_FILENAME
from tests.agent.server.conftest import build_app

pytestmark = pytest.mark.usefixtures("set_dashboard_token")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_proposal(
    *,
    state_dir: Path,
    proposal_id: str,
    kind: str,
    proposed_change: dict[str, Any] | None = None,
) -> StrategyProposal:
    """Append one pending :class:`StrategyProposal` to ``proposals.jsonl``.

    Returns the constructed proposal so tests can refer to its fields
    without re-parsing. The proposal is shaped to satisfy the schema's
    minimum requirements:

    * ``proposal_id``               — caller-controlled (so the route can
      look it up by id).
    * ``ts``                        — UTC now.
    * ``rationale``                 — non-empty (schema enforces).
    * ``confidence_pct``            — 50 (mid-range, no semantic meaning).
    * ``requires_human_approval``   — True (production default).
    * ``status``                    — defaults to ``"pending"``.

    The line is written via the same JSON serialisation pydantic's
    :meth:`model_dump_json` produces so the on-disk shape mirrors what
    the L3 advisor would have written.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    proposal = StrategyProposal(
        proposal_id=proposal_id,
        ts=datetime.now(UTC),
        kind=kind,  # type: ignore[arg-type]
        rationale=f"test rationale for {kind}",
        proposed_change=proposed_change or {},
        confidence_pct=50,
        requires_human_approval=True,
    )
    with (state_dir / PROPOSALS_FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(proposal.model_dump_json() + "\n")
    return proposal


def _new_id() -> str:
    """Generate a fresh UUID4 hex — matches the L3 advisor's id shape."""
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# 1. weight_delta approve → loop pickup
# --------------------------------------------------------------------------- #


def test_approve_weight_delta_pickup(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Approving a weight_delta proposal enqueues on the runtime seam.

    The fake "loop tick" below mirrors the sprint_10 sandbox loop
    contract: drain pending deltas at the start of every decision tick
    and apply each one to the on-loop weight updater. The test stands
    in for that drain by calling :meth:`drain_pending_deltas` directly
    and asserting the producer→consumer hand-off carried the payload
    intact.
    """
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    state_dir = tmp_path / "sandbox"
    proposal_id = _new_id()
    delta_payload = {"key": "w_r", "delta": 0.03}
    _seed_proposal(
        state_dir=state_dir,
        proposal_id=proposal_id,
        kind="weight_delta",
        proposed_change=delta_payload,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposal_id"] == proposal_id
    assert body["status"] == "approved"
    assert body["applied_to_runtime"] is True

    # "Loop pickup" — the consumer side of the seam recovers the delta.
    drained = runtime_agent.drain_pending_deltas()
    assert drained == [delta_payload]
    # And a second drain returns empty — the queue is empty after one
    # consumer cycle (the brief's "loop pickup" must consume, not peek).
    assert runtime_agent.drain_pending_deltas() == []

    # The on-disk audit row carries status="approved".
    raw = (state_dir / PROPOSALS_FILENAME).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in raw if line.strip()]
    statuses_for_id = [r["status"] for r in rows if r["proposal_id"] == proposal_id]
    assert statuses_for_id == ["pending", "approved"]


# --------------------------------------------------------------------------- #
# 2. new_signal_idea approve → TODO write
# --------------------------------------------------------------------------- #


def test_approve_new_signal_idea_writes_todo(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """new_signal_idea approval writes one line to proposal_todos.jsonl,
    leaves the runtime seam untouched, and emits the audit row."""
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    state_dir = tmp_path / "sandbox"
    proposal_id = _new_id()
    proposed_change = {
        "name": "twitter_volume",
        "description": "add new engine tracking @NBA tweet volume",
    }
    _seed_proposal(
        state_dir=state_dir,
        proposal_id=proposal_id,
        kind="new_signal_idea",
        proposed_change=proposed_change,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["applied_to_runtime"] is False

    # The runtime seam was NOT touched — non-weight_delta proposals
    # never auto-apply per PRD §11 sprint_10 CEO decision 6.
    assert runtime_agent.drain_pending_deltas() == []

    todos_path = state_dir / PROPOSAL_TODOS_FILENAME
    assert todos_path.exists()
    todo_lines = [
        json.loads(line)
        for line in todos_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(todo_lines) == 1
    todo = todo_lines[0]
    assert todo["proposal_id"] == proposal_id
    assert todo["kind"] == "new_signal_idea"
    assert todo["proposed_change"] == proposed_change
    assert todo["rationale"] == "test rationale for new_signal_idea"


# --------------------------------------------------------------------------- #
# 3. prompt_tweak approve → TODO write
# --------------------------------------------------------------------------- #


def test_approve_prompt_tweak_writes_todo(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """prompt_tweak approval mirrors new_signal_idea: TODO write +
    audit row, no runtime seam touch."""
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    state_dir = tmp_path / "sandbox"
    proposal_id = _new_id()
    proposed_change = {"target": "L1_sentiment", "patch": "be more skeptical"}
    _seed_proposal(
        state_dir=state_dir,
        proposal_id=proposal_id,
        kind="prompt_tweak",
        proposed_change=proposed_change,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["applied_to_runtime"] is False

    assert runtime_agent.drain_pending_deltas() == []

    todos_path = state_dir / PROPOSAL_TODOS_FILENAME
    todo_lines = [
        json.loads(line)
        for line in todos_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(todo_lines) == 1
    assert todo_lines[0]["kind"] == "prompt_tweak"
    assert todo_lines[0]["proposed_change"] == proposed_change


# --------------------------------------------------------------------------- #
# 4. reject with reason
# --------------------------------------------------------------------------- #


def test_reject_with_reason_persists(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """A reject body with ``reason`` ends up on the rejected audit row."""
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    state_dir = tmp_path / "sandbox"
    proposal_id = _new_id()
    _seed_proposal(
        state_dir=state_dir,
        proposal_id=proposal_id,
        kind="weight_delta",
        proposed_change={"key": "w_r", "delta": 0.05},
    )

    rejection_reason = "Delta would push w_r past the brief's [0,1] bound."
    with TestClient(app) as client:
        response = client.post(
            f"/api/proposals/{proposal_id}/reject",
            headers=auth_headers,
            json={"reason": rejection_reason},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposal_id"] == proposal_id
    assert body["status"] == "rejected"
    assert body["applied_to_runtime"] is False

    # Reject never touches the runtime seam — even for weight_delta kind.
    assert runtime_agent.drain_pending_deltas() == []

    raw = (state_dir / PROPOSALS_FILENAME).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in raw if line.strip()]
    rejected_rows = [
        r for r in rows
        if r["proposal_id"] == proposal_id and r["status"] == "rejected"
    ]
    assert len(rejected_rows) == 1
    # The reason is folded into proposed_change so the audit trail
    # survives the schema's extra='ignore' posture.
    assert rejected_rows[0]["proposed_change"]["reject_reason"] == rejection_reason


# --------------------------------------------------------------------------- #
# 5. double approve → 409
# --------------------------------------------------------------------------- #


def test_double_approve_returns_409(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """A second approve on a proposal that already won the fold returns
    409 + does NOT re-enqueue on the runtime seam.

    The audit-trail correctness for the latest-status-wins fold depends
    on this 409 path firing reliably — the brief calls this out as a
    hard rule. Re-applying the delta would silently re-rewrite the
    weights, which is the exact operator-hostile failure the L3 human-
    approval gate exists to prevent.
    """
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    state_dir = tmp_path / "sandbox"
    proposal_id = _new_id()
    delta_payload = {"key": "alpha", "delta": -0.01}
    _seed_proposal(
        state_dir=state_dir,
        proposal_id=proposal_id,
        kind="weight_delta",
        proposed_change=delta_payload,
    )

    with TestClient(app) as client:
        first = client.post(
            f"/api/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )
        assert first.status_code == 200

        # Drain BEFORE the second approve so we can assert the second
        # approve doesn't add anything (rather than two-by-luck still
        # equal a single payload).
        drained_first = runtime_agent.drain_pending_deltas()
        assert drained_first == [delta_payload]

        second = client.post(
            f"/api/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

    assert second.status_code == 409, second.text
    body = second.json()
    assert "already approved" in body["detail"]

    # The 409 path MUST NOT have enqueued a second delta.
    assert runtime_agent.drain_pending_deltas() == []


# --------------------------------------------------------------------------- #
# 6. proposal_id not found → 404
# --------------------------------------------------------------------------- #


def test_proposal_not_found_returns_404(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """Both approve and reject collapse to 404 when the id is unknown.

    A 404 is the right error here — a 4xx code lets the dashboard
    distinguish "id doesn't exist" (operator typo, stale URL) from
    "id exists but cannot be transitioned" (409). The runtime seam
    must NOT be touched on either route.
    """
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    # Note: state_dir is created by build_app but contains no proposals.

    with TestClient(app) as client:
        approve_resp = client.post(
            "/api/proposals/does-not-exist/approve", headers=auth_headers
        )
        reject_resp = client.post(
            "/api/proposals/does-not-exist/reject",
            headers=auth_headers,
            json={"reason": "n/a"},
        )

    assert approve_resp.status_code == 404, approve_resp.text
    assert approve_resp.json()["detail"] == "unknown proposal_id"
    assert reject_resp.status_code == 404, reject_resp.text
    assert reject_resp.json()["detail"] == "unknown proposal_id"

    assert runtime_agent.drain_pending_deltas() == []


# --------------------------------------------------------------------------- #
# 7. malformed body → 422
# --------------------------------------------------------------------------- #


def test_malformed_body_returns_422(
    tmp_path: Path, auth_headers: dict[str, str]
) -> None:
    """A reject body with an unknown key trips Pydantic's extra='forbid'
    and surfaces as HTTP 422 BEFORE the handler runs.

    A 422 (vs 400) is FastAPI's locked convention for request-body
    validation failures — see PRD §8. The handler being bypassed is
    important: the runtime seam must NOT be touched on a malformed
    request even when the proposal_id resolves cleanly.
    """
    runtime_agent = RuntimeAgentRunner()
    app, _, _ = build_app(tmp_path=tmp_path, runtime_agent=runtime_agent)
    state_dir = tmp_path / "sandbox"
    proposal_id = _new_id()
    _seed_proposal(
        state_dir=state_dir,
        proposal_id=proposal_id,
        kind="weight_delta",
        proposed_change={"key": "w_r", "delta": 0.01},
    )

    with TestClient(app) as client:
        bad_body = client.post(
            f"/api/proposals/{proposal_id}/reject",
            headers=auth_headers,
            json={"reasn": "typo"},  # extra='forbid' rejects this key
        )

    assert bad_body.status_code == 422, bad_body.text

    assert runtime_agent.drain_pending_deltas() == []
