"""``/api/agent/start`` SSE smoke against the real production loop (T-B-041).

Companion to :mod:`tests.agent.server.test_main_prod_loop_factory` — that
suite asserts the factory wiring at the introspection level; this suite
asserts the boot-time *observable*: an operator-facing curl against
``/api/agent/start`` + ``/api/state/stream`` MUST NOT see the
``placeholder: True`` marker the sprint_9 :class:`_PlaceholderLoop` used
to emit. The brief locks this as the load-bearing visible evidence
that the seam swap landed.

The test asks the production app to:

1. Boot via :func:`agent.server.main._build_default_app` against a
   ``tmp_path`` volume (the conftest sets ``GENESIS_SERVER_AUTOBUILD=0``
   so module-level ``app`` is None; we build fresh per-test).
2. Hit ``POST /api/agent/start`` with a valid bearer.
3. Walk ``decisions.jsonl`` straight off disk for ≤ 5 seconds, looking
   for the first emitted line.

The SSE stream is captured via direct JSONL read rather than the
``/api/state/stream`` endpoint because ``StreamingResponse`` against
the FastAPI TestClient blocks the event loop until the inner generator
yields — which the prod loop won't do for ≥ tick_interval_seconds. The
on-disk JSONL is the same source of truth the SSE endpoint tails, so
asserting absence of the placeholder marker on the file IS asserting
it on the operator-visible stream.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import agent.server.main as server_main
from agent.data.sandbox_state import DECISIONS_FILENAME
from agent.server.bootstrap import (
    BACKTEST_CACHE_DIR_ENV_VAR,
    BACKTEST_OUTPUT_ROOT_ENV_VAR,
    PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR,
    PROD_LOOP_TIME_COMPRESSION_ENV_VAR,
    SANDBOX_STATE_DIR_ENV_VAR,
)


@pytest.fixture
def hermetic_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    set_dashboard_token: None,
) -> Iterator[Path]:
    """Point all state-path env vars at ``tmp_path`` + speed up the loop.

    ``PROD_LOOP_TIME_COMPRESSION=600`` divides the 60s tick interval to
    100 ms so the first tick happens within the test's 5 s budget; the
    factory's safety floor (1 ms minimum) prevents underflow. The
    ``set_dashboard_token`` fixture (from conftest) seeds
    ``DASHBOARD_API_TOKEN`` so the bearer-auth check passes.
    """
    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv(SANDBOX_STATE_DIR_ENV_VAR, str(sandbox))
    monkeypatch.setenv(
        BACKTEST_OUTPUT_ROOT_ENV_VAR, str(tmp_path / "backtest" / "runs")
    )
    monkeypatch.setenv(
        BACKTEST_CACHE_DIR_ENV_VAR, str(tmp_path / "backtest" / "cache")
    )
    # Drive the tick cadence to 100 ms so the test's 5 s budget is safe.
    monkeypatch.setenv(PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR, "60")
    monkeypatch.setenv(PROD_LOOP_TIME_COMPRESSION_ENV_VAR, "600")
    yield sandbox


def _read_jsonl_lines(path: Path) -> list[dict[str, object]]:
    """Read every parseable JSON line under ``path``. Empty / missing → []."""
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def test_first_decision_event_after_start_lacks_placeholder_marker(
    hermetic_env: Path, auth_headers: dict[str, str]
) -> None:
    """T-B-041 contract — the placeholder marker is GONE.

    Sprint_9 sequence (now removed) was:
      ``POST /api/agent/start`` → ``_PlaceholderLoop.run()`` → write
      ONE decision row carrying ``placeholder: True`` → sleep forever.

    Sprint_13 T-B-041 sequence (this test asserts):
      ``POST /api/agent/start`` → real :class:`SandboxPhase2Loop` boots,
      reconstructs from disk, begins ticking. The first decision row
      (a NO_BET against the sprint_13 idle TickInputSource) carries the
      normal decision schema — NO ``placeholder`` key, NO ``loop``
      field equal to ``"placeholder"``.

    Asserts on the on-disk JSONL file that the SSE endpoint tails:
      * at least ONE decision row was written within 5 s
      * NO row carries ``placeholder == True``
      * NO row carries ``loop == "placeholder"``
    """
    # FastAPI TestClient lives under starlette + httpx — local import keeps
    # the test module cheap to collect for the rest of the suite.
    from fastapi.testclient import TestClient

    app = server_main._build_default_app()
    client = TestClient(app)

    response = client.post("/api/agent/start", headers=auth_headers)
    # HTTP contract unchanged — 202 + {"run_id": ..., "status": "accepted"}
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["run_id"], "expected non-empty run_id"

    decisions_path = hermetic_env / DECISIONS_FILENAME

    # Poll the on-disk JSONL until either (a) at least one row lands,
    # or (b) the 5 s budget elapses. The hermetic env's
    # PROD_LOOP_TIME_COMPRESSION=600 over a 60 s base cadence gives us
    # a 100 ms inter-tick window; the loop's own boot work (disk fold +
    # chain read) typically lands the first decision within ~1 s.
    # Monotonic clock — wall-clock change (NTP step) cannot poison the
    # deadline mid-poll.
    deadline = time.monotonic() + 5.0
    rows: list[dict[str, object]] = []
    while True:
        rows = _read_jsonl_lines(decisions_path)
        if rows:
            break
        if time.monotonic() >= deadline:
            break
        # Tight loop OK — file read is cheap + the goal is fast detection.
        time.sleep(0.05)

    # Clean up the background task BEFORE asserting so a hung-test
    # diagnostic doesn't leave a runaway loop in the test process.
    stop_response = client.post("/api/agent/stop", headers=auth_headers)
    assert stop_response.status_code == 200, stop_response.text

    assert rows, (
        "production loop did not emit any decision rows within 5 s — "
        "the seam swap from _PlaceholderLoop to SandboxPhase2Loop may "
        "have introduced a boot-blocking change"
    )
    # The brief-locked invariant — no placeholder marker on any row.
    for row in rows:
        assert row.get("placeholder") is not True, (
            f"placeholder marker survived the seam swap: {row!r}"
        )
        assert row.get("loop") != "placeholder", (
            f"loop=='placeholder' marker survived the seam swap: {row!r}"
        )
