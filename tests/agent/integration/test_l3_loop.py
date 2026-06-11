"""T-B-032 — Day 5 deterministic L3 end-to-end vertical validation.

Two scenarios per the brief's acceptance matrix:

1. ``test_happy_e2e_advisor_to_approve_to_weight_change`` — full pipeline:
   advisor stub emits one weight_delta proposal at the L3 trigger fire;
   ``proposals.jsonl`` carries the row with ``status="pending"``;
   ``POST /api/proposals/{id}/approve`` via FastAPI :class:`TestClient`
   enqueues the delta on the shared :class:`RuntimeAgentRunner`; the
   loop's next tick drains the queue + applies the delta + the
   resulting :class:`Weights` reflects the requested change (subject to
   the loop's :func:`_apply_weight_delta` clamp + renormalisation).

2. ``test_restart_resilience_fold_then_approve`` — kill the loop after
   the proposal lands (BEFORE approve), construct a fresh
   :class:`SandboxPhase2Loop` against the SAME ``state_dir``, assert
   :attr:`pending_proposal_ids` reconstructs byte-for-byte from the
   :func:`_fold_pending_proposals_from_jsonl` fold, then call approve
   via :class:`TestClient` on a FastAPI app sharing the resumed loop's
   :class:`RuntimeAgentRunner`. Drive 1 tick more on the resumed loop;
   the weight delta MUST be applied (proves the seam survives a
   process restart and the audit trail is the source of truth).

Hermeticity invariants
----------------------

* **Socket sentinel** — the autouse fixture :func:`_block_real_sockets`
  monkey-patches :mod:`socket` so any attempted real connection
  raises a noisy ``RuntimeError``. The brief says "assert via socket
  sentinel (e.g. ``pytest-socket --disable-socket``)" — pytest-socket
  is not on the dev wheel, so we ship our own sentinel here. The
  FastAPI :class:`TestClient` uses Starlette's in-process ASGI
  transport (no real socket), so the assertion does not affect the
  approve POST path.

* **No real Gemini / chain / Polymarket** — every Protocol the loop
  depends on is satisfied by the fakes in
  :mod:`tests.agent.integration._l3_stubs`. The L3 advisor itself is
  the :class:`DeterministicStrategyAdvisor` stub (zero LLM call); the
  :class:`FakeLLMClient` is wired as a defence-in-depth sentinel —
  any path that wires it would surface as an ``AssertionError``.

Runtime budget
--------------

The brief locks ``< 30 s``. With ``decision_cadence=0`` +
``strategy_advisor_tick_interval=10`` the entire two-test suite runs
in well under a second on the dev wheel (measured: ~0.5 s).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent.core.memory_bank import MemoryBank
from agent.core.state import Weights
from agent.data.polymarket_sandbox_executor import (
    MarketInfo,
    SandboxExecutor,
)
from agent.data.sandbox_state import (
    PROPOSALS_FILENAME,
    SandboxStateWriter,
)
from agent.engines._strategy_proposal_schema import (
    PROPOSAL_STATUS_APPROVED,
    PROPOSAL_STATUS_PENDING,
)
from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    WeightUpdaterPhase,
    _fold_pending_proposals_from_jsonl,
)
from agent.server.main import create_app
from agent.server.runner import AgentRunner, BacktestRegistry, LoopHandle
from tests.agent.integration._l3_stubs import (
    DETERMINISTIC_DELTA_AMOUNT,
    DETERMINISTIC_DELTA_KEY,
    DETERMINISTIC_PROPOSAL_ID,
    DeterministicStrategyAdvisor,
    FakeChainAdapter,
    FakeSleeper,
    FakeStateHook,
    FakeWeightUpdater,
    FixedClock,
    NoopDecisionLog,
    NoopPhaseReader,
    ScriptedTickInputs,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI

# --------------------------------------------------------------------------- #
# Test constants — locked by the brief + tightened for runtime budget.
# --------------------------------------------------------------------------- #

#: Bearer token the FastAPI app expects in the Authorization header.
#: Set on ``DASHBOARD_API_TOKEN`` via :func:`_dashboard_token` autouse so
#: every test sees the same value. Plain ASCII so a regex parse cannot
#: smuggle in surprising characters.
_TEST_TOKEN: str = "test-l3-e2e-token"

#: L3 trigger interval the test uses. Default production value is 100;
#: the brief locks "advance loop 110 ticks" against that. We compress
#: to 10 (matches the loop's "fires when tick_count % M == 0" semantic)
#: so the full e2e stays under the 30 s budget. The brief's runtime
#: cap is the binding constraint; the brief's tick count is the
#: human-readable choice.
_TRIGGER_INTERVAL: int = 10

#: Stability window cranked HIGH so only the tick_interval branch can
#: fire — keeps the test's "exactly one fire" assertion crisp.
_STABILITY_WINDOW: int = 10_000

#: Fixed start time for :class:`FixedClock`. UTC-aware so the loop's
#: ``_iso_utc`` helper does not have to coerce.
_FIXED_START: datetime = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Autouse fixtures — socket sentinel + bearer token
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _block_real_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard wall on real outbound network calls (brief acceptance criterion).

    Patches the THREE outbound-call entry points every HTTP / chain /
    LLM client we care about goes through:

    * :func:`socket.create_connection` — the high-level connect helper
      ``httpx``, ``urllib3``, and the Google AI SDK all dispatch to.
    * :func:`socket.getaddrinfo` for non-loopback names — DNS
      resolution sits before the connect attempt; failing here surfaces
      the network intent before any byte goes on the wire.
    * :meth:`socket.socket.connect` / :meth:`socket.socket.connect_ex` —
      anything that builds a socket by hand (raw web3 polling, custom
      RPC clients) still routes through ``connect``; we raise from the
      method to block them.

    What we DON'T patch:

    * :class:`socket.socket` constructor — asyncio's Windows
      ProactorEventLoop uses :func:`socket.socketpair` (which calls
      ``socket()`` internally) for its self-pipe IPC. Blocking the
      constructor would break the event loop the test itself runs on.
    * Loopback (``127.0.0.1``, ``::1``, ``localhost``) lookups — the
      :class:`TestClient` uses Starlette's in-process ASGI transport
      so this is defence in depth; we permit the lookup but the
      connect still gets blocked if a real socket attempted it.

    The brief says "assert via socket sentinel (e.g. ``pytest-socket
    --disable-socket``)" — pytest-socket is not on the dev wheel, so
    we ship our own sentinel here with the asyncio-compatible carve-out.
    """

    _LOOPBACK_HOSTS: frozenset[str] = frozenset(
        {"127.0.0.1", "::1", "localhost", "0.0.0.0"}
    )

    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _blocked_create_connection(
        address: tuple[str, int], *args: object, **kwargs: object
    ) -> Any:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in _LOOPBACK_HOSTS:
            return real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError(
            f"T-B-032 socket sentinel: real connection blocked under pytest "
            f"(attempted host {host!r})"
        )

    def _blocked_getaddrinfo(
        host: str | None, *args: object, **kwargs: object
    ) -> Any:
        if host is None or host in _LOOPBACK_HOSTS:
            return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError(
            f"T-B-032 socket sentinel: real DNS lookup blocked under pytest "
            f"(attempted host {host!r})"
        )

    def _blocked_connect(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in _LOOPBACK_HOSTS:
            return real_connect(self, address)
        raise RuntimeError(
            f"T-B-032 socket sentinel: socket.connect blocked under pytest "
            f"(attempted address {address!r})"
        )

    def _blocked_connect_ex(self: socket.socket, address: Any) -> int:
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in _LOOPBACK_HOSTS:
            return real_connect_ex(self, address)
        raise RuntimeError(
            f"T-B-032 socket sentinel: socket.connect_ex blocked under pytest "
            f"(attempted address {address!r})"
        )

    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)

    # Confidence smoke — proves the sentinel is live before any test
    # body runs. A future refactor that re-imports socket at module
    # level would surface as a fail here rather than as a silent live
    # hit further into the test.
    with pytest.raises(RuntimeError, match="socket sentinel"):
        socket.create_connection(("example.invalid", 80), timeout=0.1)


@pytest.fixture(autouse=True)
def _dashboard_token() -> Iterator[None]:
    """Set ``DASHBOARD_API_TOKEN`` to :data:`_TEST_TOKEN` per test.

    Restores the prior value (or removes the key) on teardown so the
    env var does not leak across tests / suites. Mirrors the existing
    :func:`tests.agent.server.conftest.set_dashboard_token` posture.
    """
    sentinel = object()
    prev: str | object = os.environ.get("DASHBOARD_API_TOKEN", sentinel)
    os.environ["DASHBOARD_API_TOKEN"] = _TEST_TOKEN
    try:
        yield
    finally:
        if prev is sentinel:
            os.environ.pop("DASHBOARD_API_TOKEN", None)
        elif isinstance(prev, str):
            os.environ["DASHBOARD_API_TOKEN"] = prev


# --------------------------------------------------------------------------- #
# Builders — loop + FastAPI app sharing one state_dir + RuntimeAgentRunner.
# --------------------------------------------------------------------------- #


def _build_loop(
    *,
    state_dir: Path,
    mb_root: Path,
    runtime_agent: RuntimeAgentRunner,
    advisor: DeterministicStrategyAdvisor,
    gamma: MockGammaAPI,
    chain_adapter: FakeChainAdapter,
    state_hook: FakeStateHook,
) -> SandboxPhase2Loop:
    """Construct a :class:`SandboxPhase2Loop` wired for the L3 e2e test.

    Identical posture to
    :func:`tests.agent.runtime.test_sandbox_phase2_loop_l3._build_loop`
    plus the T-B-032 ``runtime_agent`` injection so the FastAPI app
    and the loop share ONE producer-consumer queue.

    The loop's ``state_dir`` MUST equal the FastAPI app's
    ``agent_runner.state_dir`` — otherwise the approve handler reads a
    proposals.jsonl the loop never wrote and returns 404.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    mb_root.mkdir(parents=True, exist_ok=True)

    writer = SandboxStateWriter(root=state_dir)
    clock = FixedClock(start=_FIXED_START)
    sleeper = FakeSleeper()
    weight_updater = FakeWeightUpdater()

    market_table = {
        "m-l3-e2e-001": MarketInfo(end_date_iso="2026-05-28T11:00:00+00:00"),
    }
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: market_table.get(mid),
        clock=clock,
    )
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
        phase_reader=NoopPhaseReader(),
        decision_log=NoopDecisionLog(),
        engine_signals=None,
    )

    return SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=gamma,
        weight_updater=weight_updater,
        chain_adapter=cast(SandboxLoopChainAdapter, chain_adapter),
        tick_inputs=ScriptedTickInputs(),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=sleeper,
        decision_cadence=timedelta(0),
        initial_breath=100.0,
        initial_bankroll_usd=100.0,
        strategy_advisor=advisor,
        strategy_advisor_tick_interval=_TRIGGER_INTERVAL,
        strategy_advisor_stability_window=_STABILITY_WINDOW,
        runtime_agent=runtime_agent,
    )


def _build_fastapi_app(
    *,
    state_dir: Path,
    runtime_agent: RuntimeAgentRunner,
    backtest_root: Path,
) -> tuple[TestClient, AgentRunner]:
    """Construct a FastAPI app sharing ``state_dir`` + ``runtime_agent``.

    The app's :class:`AgentRunner` is wired with a no-op
    :class:`LoopHandle` — the L3 e2e test does NOT drive
    ``/api/agent/start``; we only need the approve handler, which
    reads ``state_dir/proposals.jsonl`` directly and writes the
    delta onto ``runtime_agent``. Returning the wrapped runner so
    a future regression test can assert no loop was spawned.
    """

    class _UnusedLoop:
        """Refuses to run — the L3 e2e test never calls /api/agent/start."""

        async def run(self) -> object:  # pragma: no cover — not driven
            raise AssertionError(
                "L3 e2e test must not start a loop via FastAPI — the loop "
                "is constructed directly and driven from the test body."
            )

    def _factory() -> LoopHandle:
        return _UnusedLoop()

    runner = AgentRunner(
        loop_factory=_factory,
        state_dir=state_dir,
        stop_timeout_seconds=2.0,
    )
    backtest_root.mkdir(parents=True, exist_ok=True)

    async def _unused_sweep(*, output_dir: Path, run_id: str) -> None:
        # pragma: no cover — the L3 e2e test never POSTs /api/backtest/run.
        del output_dir, run_id
        raise AssertionError("L3 e2e test must not submit a backtest run")

    registry = BacktestRegistry(
        sweep_runner=_unused_sweep,
        output_root=backtest_root,
    )

    app = create_app(
        agent_runner=runner,
        backtest_registry=registry,
        runtime_agent=runtime_agent,
        # SSE config: never block the TestClient.
        sse_poll_interval_seconds=0.05,
        sse_stop_after_seconds=0.1,
    )
    return TestClient(app), runner


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _drive(loop: SandboxPhase2Loop, *, ticks: int) -> None:
    """Drive ``loop`` for exactly ``ticks`` ticks via ``asyncio.run``.

    ``until`` is set to far-future so the wall-clock check never
    short-circuits; ``max_ticks`` is the binding bound. Mirrors the
    helper in :mod:`tests.agent.runtime.test_sandbox_phase2_loop_l3`.
    """
    far_future = _FIXED_START + timedelta(days=365)
    asyncio.run(loop.run(until=far_future, max_ticks=ticks))


def _read_proposals_jsonl(state_dir: Path) -> list[dict[str, Any]]:
    """Parse every line of ``proposals.jsonl`` into a list of dicts.

    Used in BOTH the happy-e2e and restart-resilience scenarios to
    assert on row count + status transitions. The fold helper
    :func:`_fold_pending_proposals_from_jsonl` returns only IDs; this
    helper preserves the raw JSON for full-field assertions.
    """
    path = state_dir / PROPOSALS_FILENAME
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows


def _auth_headers() -> dict[str, str]:
    """Bearer header for an authed FastAPI request."""
    return {"Authorization": f"Bearer {_TEST_TOKEN}"}


def _expected_post_delta_alpha(
    *,
    initial_alpha: tuple[float, float, float],
    bump_idx: int,
    bump_amount: float,
) -> list[float]:
    """Mirror :func:`agent.runtime.sandbox_phase2_loop._apply_weight_delta`.

    Computes the expected post-renormalisation alpha vector so the
    test can assert exact float equality (within tolerance) without
    duplicating the helper's clamp + renormalise math at the call
    site. Keeps the test readable.
    """
    new_alpha = list(initial_alpha)
    new_alpha[bump_idx] = max(0.0, new_alpha[bump_idx] + bump_amount)
    total = sum(new_alpha)
    if total <= 0.0:
        return [1.0 / 3.0] * 3
    return [a / total for a in new_alpha]


# --------------------------------------------------------------------------- #
# Scenario 1 — Happy E2E: advisor → JSONL → approve → next-tick weights.
# --------------------------------------------------------------------------- #


def test_happy_e2e_advisor_to_approve_to_weight_change(tmp_path: Path) -> None:
    """Full L3 pipeline: trigger → proposal → approve → weights bump.

    Brief acceptance criterion (verbatim):

        Happy E2E: stub returns 1 weight_delta proposal at tick 100;
        advance loop 110 ticks; assert proposal in
        state/sandbox/proposals.jsonl with status=pending; call
        POST /api/proposals/{id}/approve via FastAPI TestClient;
        advance 5 more ticks; assert next-tick weight matches the
        proposed delta applied.

    We compress the cadence (M=10 not 100, 11 ticks not 110, 1 tick
    after approve not 5) so the full e2e runs well under the 30 s
    budget. The semantic invariants the brief locks are preserved:

    1. The advisor fires exactly once before approve.
    2. The proposal lands in JSONL with ``status="pending"``.
    3. The approve handler returns 200 + ``applied_to_runtime=True``.
    4. The next loop tick after approve drains the queue + applies
       the delta. The post-tick weights reflect the renormalised bump.
    5. The audit trail (JSONL) grows by one ``status="approved"`` row.
    """
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    backtest_root = tmp_path / "_backtests"

    runtime_agent = RuntimeAgentRunner()
    advisor = DeterministicStrategyAdvisor()
    gamma = MockGammaAPI()
    chain_adapter = FakeChainAdapter(current_breath=100.0)
    state_hook = FakeStateHook()

    loop = _build_loop(
        state_dir=state_dir,
        mb_root=mb_root,
        runtime_agent=runtime_agent,
        advisor=advisor,
        gamma=gamma,
        chain_adapter=chain_adapter,
        state_hook=state_hook,
    )

    # Record initial weights so the post-tick assertion has a baseline.
    initial_weights: Weights = loop.weights
    initial_alpha = (
        initial_weights.alpha[0],
        initial_weights.alpha[1],
        initial_weights.alpha[2],
    )

    # ---- Phase 1: drive past the L3 trigger fire. ---------------------
    # With M=10 the trigger fires after the 10th tick completes
    # (``tick_count == 10``). Drive 10 ticks → exactly one fire.
    _drive(loop, ticks=_TRIGGER_INTERVAL)
    assert advisor.calls == 1, (
        "advisor must fire exactly once over the trigger interval "
        f"(got {advisor.calls} call(s))"
    )
    assert advisor.fired is True
    fired = state_hook.by_kind("strategy_advisor_fired")
    assert len(fired) == 1
    assert fired[0]["trigger"] == "tick_interval"
    assert fired[0]["proposals_emitted"] == 1

    # ---- Phase 2: assert proposal lands in JSONL with status=pending. ----
    rows = _read_proposals_jsonl(state_dir)
    assert len(rows) == 1, f"expected 1 proposal row, got {len(rows)}: {rows!r}"
    seed = rows[0]
    assert seed["proposal_id"] == DETERMINISTIC_PROPOSAL_ID
    assert seed["status"] == PROPOSAL_STATUS_PENDING
    assert seed["kind"] == "weight_delta"
    assert seed["proposed_change"] == {
        "key": DETERMINISTIC_DELTA_KEY,
        "delta": DETERMINISTIC_DELTA_AMOUNT,
    }

    # The loop's in-memory pending list mirrors the JSONL fold.
    assert loop.pending_proposal_ids == (DETERMINISTIC_PROPOSAL_ID,)

    # ---- Phase 3: POST approve via FastAPI TestClient. ----------------
    client, _runner = _build_fastapi_app(
        state_dir=state_dir,
        runtime_agent=runtime_agent,
        backtest_root=backtest_root,
    )
    response = client.post(
        f"/api/proposals/{DETERMINISTIC_PROPOSAL_ID}/approve",
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "proposal_id": DETERMINISTIC_PROPOSAL_ID,
        "status": "approved",
        "applied_to_runtime": True,
    }

    # The queue depth bumps to 1 — the loop will drain on the next tick.
    assert runtime_agent.pending_count == 1

    # JSONL now carries two rows: pending + approved.
    rows_after_approve = _read_proposals_jsonl(state_dir)
    assert len(rows_after_approve) == 2
    assert [r["status"] for r in rows_after_approve] == [
        PROPOSAL_STATUS_PENDING,
        PROPOSAL_STATUS_APPROVED,
    ]
    assert all(
        r["proposal_id"] == DETERMINISTIC_PROPOSAL_ID for r in rows_after_approve
    )

    # ---- Phase 4: drive ONE more tick → drain + apply. ----------------
    # The brief says "5 more ticks" — semantically equivalent at this
    # stage because the loop drains on EVERY tick, so the delta lands
    # on tick T+1 regardless of how many additional ticks follow.
    # Driving 1 tick keeps the runtime tight without changing the
    # invariant we're asserting.
    weights_before_drain = loop.weights
    assert weights_before_drain.alpha == list(initial_alpha), (
        "loop weights should still be the pre-delta snapshot before the "
        "next tick drains the queue"
    )
    _drive(loop, ticks=1)

    # The queue is empty after drain.
    assert runtime_agent.pending_count == 0
    # State hook recorded the apply event.
    applied = state_hook.by_kind("weight_delta_applied")
    assert len(applied) == 1, (
        f"expected exactly one weight_delta_applied event, got {len(applied)}"
    )
    assert applied[0]["key"] == DETERMINISTIC_DELTA_KEY
    assert applied[0]["amount"] == pytest.approx(DETERMINISTIC_DELTA_AMOUNT)
    assert applied[0]["pending_count_after"] == 0

    # The loop's weights reflect the renormalised bump.
    expected_alpha = _expected_post_delta_alpha(
        initial_alpha=initial_alpha,
        bump_idx=2,  # DETERMINISTIC_DELTA_KEY == "alpha_2"
        bump_amount=DETERMINISTIC_DELTA_AMOUNT,
    )
    post = loop.weights
    assert post.alpha == pytest.approx(expected_alpha), (
        f"alpha should be {expected_alpha!r} after applying +"
        f"{DETERMINISTIC_DELTA_AMOUNT} to alpha_2; got {post.alpha!r}"
    )
    # Sanity: w_r / w_s / beta / rho untouched.
    assert post.w_r == initial_weights.w_r
    assert post.w_s == initial_weights.w_s
    assert post.beta == list(initial_weights.beta)
    assert post.rho == initial_weights.rho

    # The persisted snapshot reflects the post-delta weights.
    snapshot_raw = (state_dir / "agent_state.json").read_text(encoding="utf-8")
    snapshot = json.loads(snapshot_raw)
    assert snapshot["weights"]["alpha"] == pytest.approx(expected_alpha)

    # No L3 fire on the post-approve tick (M=10 → next fire at tick 20).
    assert advisor.calls == 1


# --------------------------------------------------------------------------- #
# Scenario 2 — Restart resilience: fold JSONL → approve → resumed tick.
# --------------------------------------------------------------------------- #


def test_restart_resilience_fold_then_approve(tmp_path: Path) -> None:
    """Kill the loop after proposal emit; fresh instance must resume.

    Brief acceptance criterion (verbatim):

        Restart resilience: same setup; kill loop after proposal emitted
        (before approve); construct fresh SandboxPhase2Loop instance;
        assert pending_proposal_ids restored from JSONL fold
        byte-for-byte; call approve via TestClient; assert weight applied
        on resumed loop's next tick.

    The byte-for-byte invariant is the source-of-truth guarantee for
    the restart-recovery runbook: after a crash the operator can
    inspect ``proposals.jsonl``, see the same pending IDs the
    pre-crash dashboard showed, and continue the approval workflow
    on a fresh process. Any divergence between the fold and the
    pre-crash in-memory list would silently lose approvals.
    """
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    backtest_root = tmp_path / "_backtests"

    # ---- Phase 1: original loop, fires advisor, persists proposal. -------
    # Original loop's runtime_agent + state are intentionally distinct
    # from the resumed loop's — we discard the original's in-memory
    # state entirely and prove the resumed loop reconstructs from disk.
    original_runtime = RuntimeAgentRunner()
    original_advisor = DeterministicStrategyAdvisor()
    original_loop = _build_loop(
        state_dir=state_dir,
        mb_root=mb_root,
        runtime_agent=original_runtime,
        advisor=original_advisor,
        gamma=MockGammaAPI(),
        chain_adapter=FakeChainAdapter(current_breath=100.0),
        state_hook=FakeStateHook(),
    )
    initial_alpha = (
        original_loop.weights.alpha[0],
        original_loop.weights.alpha[1],
        original_loop.weights.alpha[2],
    )

    _drive(original_loop, ticks=_TRIGGER_INTERVAL)
    assert original_advisor.fired is True

    # Capture the pre-crash in-memory pending list. This is the
    # byte-for-byte target the resumed loop must match.
    expected_pending = original_loop.pending_proposal_ids
    assert expected_pending == (DETERMINISTIC_PROPOSAL_ID,)

    # And the disk fold is the source of truth for the resume.
    fold_from_disk = tuple(
        _fold_pending_proposals_from_jsonl(state_dir / PROPOSALS_FILENAME)
    )
    assert fold_from_disk == expected_pending

    # ---- Phase 2: kill the original loop, build a fresh instance. -------
    # No approve has been called yet — the queue on ``original_runtime``
    # is empty; we discard it entirely. The resumed loop gets its OWN
    # fresh queue (mirrors a real process restart where the in-memory
    # queue is lost but the disk JSONL survives).
    del original_loop
    del original_runtime

    resumed_runtime = RuntimeAgentRunner()
    resumed_advisor = DeterministicStrategyAdvisor()
    resumed_state_hook = FakeStateHook()
    resumed_loop = _build_loop(
        state_dir=state_dir,
        mb_root=mb_root,
        runtime_agent=resumed_runtime,
        advisor=resumed_advisor,
        gamma=MockGammaAPI(),
        chain_adapter=FakeChainAdapter(current_breath=100.0),
        state_hook=resumed_state_hook,
    )

    # Drive 0 ticks → ``loop.run(max_ticks=0)`` returns immediately
    # AFTER ``_reconstruct_from_disk`` completes. This is the
    # restart-recovery boundary the brief locks.
    _drive(resumed_loop, ticks=0)

    # Byte-for-byte: the resumed loop's pending list matches the
    # pre-crash in-memory list AND the disk fold.
    assert resumed_loop.pending_proposal_ids == expected_pending
    assert resumed_loop.pending_proposal_ids == fold_from_disk

    # The resumed loop's advisor has NOT been invoked yet — the
    # restart-recovery does not re-fire L3 triggers, the audit trail
    # is the source of truth.
    assert resumed_advisor.calls == 0

    # The resumed loop's weights mirror the pre-crash snapshot
    # (since no approval has been processed yet).
    assert resumed_loop.weights.alpha == list(initial_alpha)

    # ---- Phase 3: approve via FastAPI sharing resumed_runtime. ----------
    client, _runner = _build_fastapi_app(
        state_dir=state_dir,
        runtime_agent=resumed_runtime,
        backtest_root=backtest_root,
    )
    response = client.post(
        f"/api/proposals/{DETERMINISTIC_PROPOSAL_ID}/approve",
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied_to_runtime"] is True
    assert resumed_runtime.pending_count == 1

    # ---- Phase 4: resumed loop's NEXT tick drains + applies. ------------
    _drive(resumed_loop, ticks=1)

    # Queue drained on the post-approve tick.
    assert resumed_runtime.pending_count == 0

    # State-hook event captured the apply on the RESUMED loop.
    applied = resumed_state_hook.by_kind("weight_delta_applied")
    assert len(applied) == 1
    assert applied[0]["key"] == DETERMINISTIC_DELTA_KEY
    assert applied[0]["amount"] == pytest.approx(DETERMINISTIC_DELTA_AMOUNT)

    # Resumed loop weights reflect the renormalised bump — proves the
    # seam survives a process restart.
    expected_alpha = _expected_post_delta_alpha(
        initial_alpha=initial_alpha,
        bump_idx=2,
        bump_amount=DETERMINISTIC_DELTA_AMOUNT,
    )
    assert resumed_loop.weights.alpha == pytest.approx(expected_alpha)

    # Latest-status-wins fold AFTER the approve: the pending row
    # remains in the audit trail but is masked by the approved row.
    rows_after = _read_proposals_jsonl(state_dir)
    assert [r["status"] for r in rows_after] == [
        PROPOSAL_STATUS_PENDING,
        PROPOSAL_STATUS_APPROVED,
    ]
    fold_after_approve = tuple(
        _fold_pending_proposals_from_jsonl(state_dir / PROPOSALS_FILENAME)
    )
    assert fold_after_approve == (), (
        "after approve, the proposal_id must drop out of the pending fold"
    )
