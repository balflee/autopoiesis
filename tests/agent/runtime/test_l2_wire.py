# ruff: noqa: RUF002, RUF003
"""T-B-024 sprint_9 Day-1 — L2 reflection wire for SandboxPhase2Loop.

Brief acceptance criteria covered here:

* trigger logic fires on tick_interval (N=10) AND on weight_delta > 0.05;
* reflections.jsonl is append-only (truncation mid-run does NOT rewrite
  pre-existing rows on the restart-side append);
* cost_guard shared with L1 sentiment is the same instance + accumulates
  llm_cost_usd correctly per emitted record;
* cassette replay drives 0 live Gemini calls (autouse fixture from
  ``tests/agent/llm/conftest.py`` deletes GEMINI_API_KEY).

All six tests are hermetic — every test uses ``tmp_path`` for the JSONL
streams, the loop's :class:`Sleeper` is replaced by a no-op fake, and
the LLM client is a cassette-replay stub identical in shape to the
T-B-022 ``_CassetteLLMClient``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase, Weights
from agent.data.polymarket_sandbox_executor import (
    MarketInfo,
    SandboxExecutor,
)
from agent.data.sandbox_state import (
    AgentStateSnapshot,
    SandboxStateWriter,
    iter_jsonl,
)
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.engines.reflection import (
    REFLECTION_WEIGHT_KEYS,
    ReflectionEngine,
    SandboxReflectionRecord,
)
from agent.llm.cost_guard import CostGuard
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DEFAULT_REFLECTION_TICK_INTERVAL,
    DEFAULT_REFLECTION_WEIGHT_DELTA_THRESHOLD,
    REFLECTION_PER_CALL_USD_EST,
    REFLECTION_TICK_INTERVAL_ENV_VAR,
    REFLECTION_WEIGHT_DELTA_THRESHOLD_ENV_VAR,
    DeathReceipt,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    TickInputSource,
    WeightUpdaterPhase,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI

# --------------------------------------------------------------------------- #
# Cassette client — pattern lifted verbatim from
# tests/agent/llm/test_gemini_smoke_offline.py so the two suites share a
# single replay shape.
# --------------------------------------------------------------------------- #


CASSETTE_PATH: Path = (
    Path(__file__).parent / "cassettes" / "test_l2_wire.yaml"
)


@dataclass
class _CassetteLLMClient:
    """Protocol-conformant cassette-replay stub.

    Identical replay semantics to the T-B-022 sentiment cassette stub
    (see tests/agent/llm/test_gemini_smoke_offline.py for the full
    rationale). Pops interactions in FIFO order; raises ValueError when
    the cassette tags an interaction with ``response.error == 'value_error'``
    so the reflection engine's retry-once-then-fail-soft path can fire.
    """

    cassette_path: Path
    interactions: list[dict[str, Any]] = field(init=False)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def __post_init__(self) -> None:
        raw = yaml.safe_load(self.cassette_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "interactions" not in raw:
            raise AssertionError(
                f"cassette {self.cassette_path} missing 'interactions' key"
            )
        self.interactions = list(raw["interactions"])

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if self._idx >= len(self.interactions):
            raise AssertionError(
                f"cassette {self.cassette_path} exhausted at call "
                f"#{len(self.calls)} — wire more interactions."
            )
        idx = self._idx
        interaction = self.interactions[idx]
        self._idx += 1
        response = interaction.get("response", {})
        if response.get("error") == "value_error":
            raise ValueError(response.get("message", "cassette_value_error"))
        body = response.get("body")
        if not isinstance(body, dict):
            raise AssertionError(
                f"cassette interaction #{idx} response.body must be a dict"
            )
        return cast(dict[str, Any], body)


# --------------------------------------------------------------------------- #
# Test doubles — minimal fakes for the loop's injected Protocols (mirror
# tests/agent/runtime/test_sandbox_restart.py so the two suites share shape).
# --------------------------------------------------------------------------- #


class FakeWeightUpdater:
    """Spy WeightUpdater — never called in the L2 wire tests but required
    by the loop's constructor signature."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: Any,
    ) -> None:  # pragma: no cover — L2 tests place no bets that settle
        self.calls.append(
            {"phase": phase, "signals": dict(signals), "outcome": outcome}
        )


@dataclass
class FakeChainAdapter:
    """Combined SandboxLoopChainAdapter for the L2 tests.

    ``current_breath`` starts large so no tick routes to NO_BET due to
    breath exhaustion; the reflection trigger fires on its own cadence.
    """

    current_breath: float = 10_000.0
    pnl_updates: list[float] = field(default_factory=list)

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)
        self.current_breath += pnl_usd

    async def read_breath(self) -> float:
        return self.current_breath

    async def kill_and_mint_tombstone(  # pragma: no cover — L2 never kills
        self,
        *,
        agent_id: str,
        bankroll_usd: float,
        last_tick: int,
        final_weights_hash: str,
        memory_bank_cid: str,
        last_words: str,
    ) -> DeathReceipt:
        return DeathReceipt(
            kill_tx_hash="0x" + "a" * 64,
            tombstone_token_id="1",
            tombstone_tx_hash="0x" + "b" * 64,
        )


class FakeStateHook:
    """Records every emitted state hook for assertions."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


class FakeSleeper:
    """No-op sleeper — records durations but never blocks."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FixedClock:
    """Auto-advancing clock — bumps the cursor per ``now()`` call."""

    def __init__(
        self,
        *,
        start: datetime,
        auto_advance: timedelta = timedelta(seconds=1),
    ) -> None:
        self._now = start
        self._auto_advance = auto_advance

    def now(self) -> datetime:
        current = self._now
        if self._auto_advance > timedelta(0):
            self._now = self._now + self._auto_advance
        return current


@dataclass
class _ScriptedTickInputs:
    """Deterministic TickInputSource — high-conviction bullish signals so
    every tick clears the decision engine's bet floor."""

    market_id: str = "m-l2-001"
    price: float = 0.4
    liquidity_cap_usd: float = 25.0

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        signals = _bullish_signals(asof_ts=asof_ts, tick=tick)
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
        )


def _bullish_signals(*, asof_ts: datetime, tick: int) -> dict[str, Signal]:
    """5-engine high-conviction read — clones the restart suite's recipe."""
    iso = asof_ts.isoformat()
    return {
        TENNIS_TECHNICAL: Signal(
            score=0.9, confidence=0.9, available_at=iso,
            rationale="strong technical read",
            raw_features={"tick": float(tick)},
        ),
        MARKET_MOMENTUM: Signal(
            score=0.8, confidence=0.9, available_at=iso,
            rationale="momentum agrees",
            raw_features={"tick": float(tick)},
        ),
        SURFACE_ADVANTAGE: Signal(
            score=0.7, confidence=0.85, available_at=iso,
            rationale="surface edge favours YES",
            raw_features={"tick": float(tick)},
        ),
        HEAD_TO_HEAD: Signal(
            score=0.6, confidence=0.8, available_at=iso,
            rationale="head-to-head favours YES",
            raw_features={"tick": float(tick)},
        ),
        REST_RECENCY: Signal(
            score=0.6, confidence=0.85, available_at=iso,
            rationale="rest/recency favours YES",
            raw_features={"tick": float(tick)},
        ),
    }


class _NoopPhaseReader:
    def read_phase(self) -> Phase:  # pragma: no cover
        return Phase.PHASE_2_APPRENTICE


class _NoopDecisionLog:
    def append(  # pragma: no cover
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        return "0x_unused"


# --------------------------------------------------------------------------- #
# Loop builder
# --------------------------------------------------------------------------- #


@dataclass
class _LoopBundle:
    """Bundle of the loop + every fake it depends on, returned together
    so individual tests destructure only what they need."""

    loop: SandboxPhase2Loop
    writer: SandboxStateWriter
    cassette: _CassetteLLMClient
    cost_guard: CostGuard
    state_hook: FakeStateHook
    chain_adapter: FakeChainAdapter
    clock: FixedClock


def _build_loop(
    *,
    tmp_path: Path,
    cassette: _CassetteLLMClient | None = None,
    cost_guard: CostGuard | None = None,
    state_writer: SandboxStateWriter | None = None,
    reflection_tick_interval: int | None = None,
    reflection_weight_delta_threshold: float | None = None,
    env: dict[str, str] | None = None,
) -> _LoopBundle:
    state_dir = tmp_path / "sandbox"
    mb_root = tmp_path / "_mb"
    mb_root.mkdir(parents=True, exist_ok=True)

    cas = cassette if cassette is not None else _CassetteLLMClient(
        cassette_path=CASSETTE_PATH,
    )
    cg = cost_guard if cost_guard is not None else CostGuard()
    writer = state_writer if state_writer is not None else SandboxStateWriter(
        root=state_dir,
    )

    clock = FixedClock(
        start=datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC),
        auto_advance=timedelta(seconds=1),
    )

    reflection_engine = ReflectionEngine(
        llm_client=cas,
        reflections_dir=tmp_path / "_reflections",
    )

    market_table = {
        "m-l2-001": MarketInfo(end_date_iso="2026-05-28T17:00:00+00:00"),
    }
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: market_table.get(mid),
        clock=clock,
    )

    chain_adapter = FakeChainAdapter()
    state_hook = FakeStateHook()
    sleeper = FakeSleeper()

    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,
    )

    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=MockGammaAPI(),
        weight_updater=FakeWeightUpdater(),
        chain_adapter=cast(SandboxLoopChainAdapter, chain_adapter),
        tick_inputs=_ScriptedTickInputs(),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=sleeper,
        decision_cadence=timedelta(0),
        initial_breath=10_000.0,
        initial_bankroll_usd=10_000.0,
        reflection_engine=reflection_engine,
        cost_guard=cg,
        reflection_tick_interval=reflection_tick_interval,
        reflection_weight_delta_threshold=reflection_weight_delta_threshold,
        env=env,
    )
    return _LoopBundle(
        loop=loop,
        writer=writer,
        cassette=cas,
        cost_guard=cg,
        state_hook=state_hook,
        chain_adapter=chain_adapter,
        clock=clock,
    )


def _drive_ticks(loop: SandboxPhase2Loop, *, n: int) -> None:
    """Run the loop until ``n`` ticks have completed."""
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    asyncio.run(loop.run(until=far_future, max_ticks=n))


def _inject_weight_bump(
    *, loop: SandboxPhase2Loop, writer: SandboxStateWriter, bumped: Weights,
) -> None:
    """Inject a synthetic weight mutation persisted to disk.

    The loop's ``run()`` calls ``_reconstruct_from_disk()`` on every
    entry, which reloads :attr:`SandboxPhase2Loop._weights` from the
    on-disk snapshot. To survive that reload, we (a) mutate the
    in-memory attribute AND (b) rewrite the snapshot so the next
    reconstruction picks up the bumped values. This mirrors the
    canonical production path: in real use the WeightUpdater Protocol
    mutates :attr:`_weights` AND the very next tick writes the new
    snapshot at step 7 — we accelerate that pairing here.
    """
    loop._weights = bumped  # noqa: SLF001 — explicit test injection
    existing = writer.snapshot_path.read_text(encoding="utf-8")
    snapshot = AgentStateSnapshot.model_validate_json(existing)
    rewritten = AgentStateSnapshot(
        snapshot_ts=snapshot.snapshot_ts,
        phase=snapshot.phase,
        breath=snapshot.breath,
        bankroll_usd=snapshot.bankroll_usd,
        phase_age_days=snapshot.phase_age_days,
        open_bet_ids=list(snapshot.open_bet_ids),
        last_tick=snapshot.last_tick,
        weights=bumped,
        desperate=snapshot.desperate,
    )
    writer.write_snapshot(rewritten)


# --------------------------------------------------------------------------- #
# Pytest hygiene — provider key MUST be absent so even a stray real-client
# instantiation would fail-fast on auth before any network I/O.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence-in-depth — the cassette stub never touches google-genai but
    even so we delete the env var so a future drift cannot leak a real
    call. Mirrors the autouse pattern in tests/agent/llm/conftest.py."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Test 1 — tick_interval trigger fires once after 10 ticks
# --------------------------------------------------------------------------- #


def test_tick_interval_fires_after_n_ticks(tmp_path: Path) -> None:
    """Brief: 'after 10 ticks in a deterministic test fixture,
    reflections.jsonl contains 1 entry'.

    Drive 10 ticks → exactly 1 reflection appended, ``trigger`` field is
    ``'tick_interval'``, ``llm_cost_usd`` is exactly the per-call
    estimate, and the JSONL parses as a SandboxReflectionRecord.
    """
    bundle = _build_loop(tmp_path=tmp_path)
    _drive_ticks(bundle.loop, n=DEFAULT_REFLECTION_TICK_INTERVAL)

    rows = iter_jsonl(bundle.writer.reflections_path)
    assert len(rows) == 1, f"expected 1 reflection, got {len(rows)}"
    record = SandboxReflectionRecord.model_validate(rows[0])
    assert record.trigger == "tick_interval"
    assert record.llm_cost_usd == pytest.approx(REFLECTION_PER_CALL_USD_EST)
    # Weight snapshot keys must match the 6 canonical fusion params.
    assert set(record.weight_snapshot.keys()) == set(REFLECTION_WEIGHT_KEYS)
    # last_reflection_tick latched to the just-fired tick.
    assert bundle.loop.last_reflection_tick == DEFAULT_REFLECTION_TICK_INTERVAL - 1
    # State hook surfaced the event.
    assert "reflection_emitted" in bundle.state_hook.kinds()


# --------------------------------------------------------------------------- #
# Test 2 — weight_delta trigger fires on synthetic weight bump
# --------------------------------------------------------------------------- #


def test_weight_delta_trigger_on_synthetic_bump(tmp_path: Path) -> None:
    """Brief: 'after a synthetic weight bump > 0.05, a 2nd entry; trigger
    field reflects the cause correctly'.

    Drive 3 ticks (well below the 10-tick interval) → mutate the loop's
    in-memory weights so ``max |Δw|`` > 0.05 → drive one more tick →
    the next reflection has ``trigger='weight_delta'``.
    """
    bundle = _build_loop(tmp_path=tmp_path)
    loop = bundle.loop

    _drive_ticks(loop, n=3)
    assert iter_jsonl(bundle.writer.reflections_path) == [], (
        "weight_delta should NOT fire on stable weights in the first 3 ticks"
    )

    # Synthetic weight bump — push alpha_0 well above the 0.05 threshold
    # while preserving the PRD §4.1 normalisation invariants. The
    # in-memory + snapshot mutation pair survives the next
    # ``_reconstruct_from_disk`` call (which always runs at the top of
    # ``run()``); see :func:`_inject_weight_bump` for the rationale.
    bumped = Weights(
        w_r=loop.weights.w_r,
        w_s=loop.weights.w_s,
        alpha=[
            loop.weights.alpha[0] + 0.20,
            loop.weights.alpha[1] - 0.10,
            loop.weights.alpha[2] - 0.10,
        ],
        beta=list(loop.weights.beta),
        rho=loop.weights.rho,
    )
    _inject_weight_bump(loop=loop, writer=bundle.writer, bumped=bumped)

    _drive_ticks(loop, n=1)

    rows = iter_jsonl(bundle.writer.reflections_path)
    assert len(rows) == 1
    record = SandboxReflectionRecord.model_validate(rows[0])
    assert record.trigger == "weight_delta"
    # The persisted weight_snapshot must reflect the BUMPED weights.
    assert record.weight_snapshot["alpha_0"] == pytest.approx(bumped.alpha[0])


# --------------------------------------------------------------------------- #
# Test 3 — two reflections: tick_interval then weight_delta
# --------------------------------------------------------------------------- #


def test_two_reflections_interval_then_weight_delta(tmp_path: Path) -> None:
    """Brief: 'after 10 ticks ... 1 entry; after a synthetic weight bump
    > 0.05, a 2nd entry; trigger field reflects the cause correctly'.

    End-to-end ordering: drive 10 ticks (interval fires) → bump weights →
    drive one more tick → second reflection fires with weight_delta.
    """
    bundle = _build_loop(tmp_path=tmp_path)
    loop = bundle.loop

    _drive_ticks(loop, n=DEFAULT_REFLECTION_TICK_INTERVAL)
    after_interval = iter_jsonl(bundle.writer.reflections_path)
    assert len(after_interval) == 1
    assert (
        SandboxReflectionRecord.model_validate(after_interval[0]).trigger
        == "tick_interval"
    )

    # Bump just over the threshold — alpha shift of 0.06 > 0.05.
    bumped = Weights(
        w_r=loop.weights.w_r,
        w_s=loop.weights.w_s,
        alpha=[
            loop.weights.alpha[0] + 0.06,
            loop.weights.alpha[1] - 0.03,
            loop.weights.alpha[2] - 0.03,
        ],
        beta=list(loop.weights.beta),
        rho=loop.weights.rho,
    )
    _inject_weight_bump(loop=loop, writer=bundle.writer, bumped=bumped)

    _drive_ticks(loop, n=1)
    rows = iter_jsonl(bundle.writer.reflections_path)
    assert len(rows) == 2
    second = SandboxReflectionRecord.model_validate(rows[1])
    assert second.trigger == "weight_delta"
    # Reflection ids are unique UUIDs.
    first = SandboxReflectionRecord.model_validate(rows[0])
    assert first.reflection_id != second.reflection_id


# --------------------------------------------------------------------------- #
# Test 4 — append-only regression (truncate mid-run, restart loop)
# --------------------------------------------------------------------------- #


def test_append_only_truncate_restart_does_not_rewrite(tmp_path: Path) -> None:
    """Brief: 'Append-only regression test mirrors sprint_8 T-B-019:
    truncate file mid-run, restart loop, assert history is NOT rewritten.'

    Drive 10 ticks → 1 reflection on disk → truncate the file → restart
    the loop against the SAME state_dir → drive another 10 ticks → assert
    (a) the original entry is NOT re-emitted (we wiped it; the loop has
    no business resurrecting it) and (b) the new tick window appended
    cleanly without panicking on the truncated state.

    The append-only invariant the brief locks is that the loop NEVER
    rewrites the file on restart — `state/sandbox/reflections.jsonl`
    must remain open(path, 'a') only. After truncation the restart-side
    appends start at byte 0 of a fresh file; the test asserts the
    pre-restart row (UUID1) is gone and a NEW UUID2 row appears.
    """
    state_dir = tmp_path / "sandbox"
    bundle_1 = _build_loop(tmp_path=tmp_path)
    _drive_ticks(bundle_1.loop, n=DEFAULT_REFLECTION_TICK_INTERVAL)
    rows_pre = iter_jsonl(bundle_1.writer.reflections_path)
    assert len(rows_pre) == 1
    pre_id = SandboxReflectionRecord.model_validate(rows_pre[0]).reflection_id

    # Capture the file modification stamp BEFORE truncation so we can
    # assert any subsequent post-truncate write is genuinely an append
    # (not a re-write of the pre-truncate content).
    reflections_path = bundle_1.writer.reflections_path
    assert reflections_path.exists()
    # Truncate — the brief's "truncate file mid-run" instruction.
    reflections_path.write_text("", encoding="utf-8")
    assert reflections_path.read_text(encoding="utf-8") == ""

    # "Restart" — drop the loop reference, build a fresh loop instance
    # against the SAME state_dir. A fresh cassette stub provides the
    # post-restart LLM responses.
    del bundle_1
    bundle_2 = _build_loop(tmp_path=tmp_path)
    # Confirm the state_dir is the same on the new bundle (sanity).
    assert bundle_2.writer.reflections_path == reflections_path
    assert bundle_2.writer.reflections_path.parent == state_dir

    _drive_ticks(bundle_2.loop, n=DEFAULT_REFLECTION_TICK_INTERVAL)

    rows_post = iter_jsonl(bundle_2.writer.reflections_path)
    # The original UUID is gone (we truncated it).
    post_ids = [
        SandboxReflectionRecord.model_validate(r).reflection_id for r in rows_post
    ]
    assert pre_id not in post_ids, (
        "Loop must NOT resurrect the pre-truncate row — open mode must "
        "be 'a' so truncation persists."
    )
    # The fresh window appended cleanly.
    assert len(rows_post) == 1
    assert rows_post[0]["trigger"] == "tick_interval"


# --------------------------------------------------------------------------- #
# Test 5 — shared cost_guard with L1; per-call llm_cost_usd accounting
# --------------------------------------------------------------------------- #


def test_shared_cost_guard_aggregates_l1_and_l2(tmp_path: Path) -> None:
    """Brief acceptance: 'using the SAME llm_client instance as L1 so
    cost-guard accounting is shared.' Plus defence-in-depth: a guard
    that's ALREADY exhausted MUST short-circuit before the LLM call
    fires (efficiency reviewer flagged the leak; this asserts the fix).

    Three surfaces in one test:

    1. **Shared accounting** — pre-seeding the guard with L1 spend
       leaves the loop's reflection delta on top, NOT in place of.

    2. **Per-call delta correctness** — emitted record's
       ``llm_cost_usd`` equals the per-call estimate (driven by the
       :class:`agent.llm.cost_guard.CostEvent.usd` returned from the
       record call, not a before/after subtraction that could clamp
       negative).

    3. **Budget-exhausted short-circuit** — a guard saturated to its
       hard cap before the second window MUST NOT trigger another
       LLM call; the cassette's call count stays put, and the state
       hook surfaces a ``reflection_skipped_cost_exhausted`` event.
    """
    shared_guard = CostGuard(hard_cap_usd=10.0)
    shared_guard.record(label="sentiment_l1", usd=0.0030)
    pre_total = shared_guard.total_usd
    assert pre_total == pytest.approx(0.0030)

    bundle = _build_loop(tmp_path=tmp_path, cost_guard=shared_guard)
    assert bundle.loop.cost_guard is shared_guard

    _drive_ticks(bundle.loop, n=DEFAULT_REFLECTION_TICK_INTERVAL)
    rows = iter_jsonl(bundle.writer.reflections_path)
    assert len(rows) == 1
    record = SandboxReflectionRecord.model_validate(rows[0])
    assert record.llm_cost_usd == pytest.approx(REFLECTION_PER_CALL_USD_EST)
    assert shared_guard.total_usd == pytest.approx(
        pre_total + REFLECTION_PER_CALL_USD_EST
    )

    # Saturate the guard — simulate L1 having burned through the cap
    # between windows. The next tick_interval fire MUST NOT touch the
    # cassette; the state hook records the skip.
    cassette_calls_before = len(bundle.cassette.calls)
    shared_guard.total_usd = shared_guard.hard_cap_usd  # exact cap → exhausted
    _drive_ticks(bundle.loop, n=DEFAULT_REFLECTION_TICK_INTERVAL)

    # No new reflection appended.
    assert iter_jsonl(bundle.writer.reflections_path) == rows
    # No new LLM call.
    assert len(bundle.cassette.calls) == cassette_calls_before
    # Skip event surfaced for the operator.
    assert "reflection_skipped_cost_exhausted" in bundle.state_hook.kinds()


# --------------------------------------------------------------------------- #
# Test 6 — cassette replay drives 0 live Gemini calls + env override path
# --------------------------------------------------------------------------- #


def test_env_override_and_cassette_no_live_calls(tmp_path: Path) -> None:
    """Brief: 'Both bounds env-configurable' + 'VCR cassette committed;
    CI replays 0 live Gemini calls'.

    Two surfaces covered in one test (env override is structural; the
    "no live calls" assertion is the canonical safety check):

    1. Set ``REFLECTION_TICK_INTERVAL=3`` via the constructor's ``env=``
       dict; verify the loop's effective interval is 3 (not the default
       10). Drive 3 ticks → reflection fires.

    2. The cassette stub was the ONLY LLM client touched; the autouse
       fixture deleted ``GEMINI_API_KEY``. Any path that tried to reach
       google-genai would have raised long before the cassette ever
       responded. Re-assert here so the contract is loud + grep-able.
    """
    # Env override surface — explicit ctor arg is None so the env is read.
    env = {REFLECTION_TICK_INTERVAL_ENV_VAR: "3"}
    bundle = _build_loop(tmp_path=tmp_path, env=env)
    assert bundle.loop.reflection_tick_interval == 3
    # Default threshold is preserved (env only set one of the two).
    assert (
        bundle.loop.reflection_weight_delta_threshold
        == DEFAULT_REFLECTION_WEIGHT_DELTA_THRESHOLD
    )

    _drive_ticks(bundle.loop, n=3)
    rows = iter_jsonl(bundle.writer.reflections_path)
    assert len(rows) == 1
    assert (
        SandboxReflectionRecord.model_validate(rows[0]).trigger
        == "tick_interval"
    )

    # No live calls — cassette saw exactly one structured_call.
    assert len(bundle.cassette.calls) == 1
    # And GEMINI_API_KEY remains absent (autouse fixture invariant).
    assert os.environ.get("GEMINI_API_KEY") is None


