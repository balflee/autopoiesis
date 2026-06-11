"""T-B-021 forced-terminal E2E test.

CEO sprint_8 Day 5 V-gate (D-2026-05-26-PLAN-003):

    Force a controlled BREATH→0 in test mode (env var
    SANDBOX_FORCE_TERMINAL=1) to verify the kill() + Tombstone mint E2E
    path WITHOUT having to wait for natural death.

Scope of this test
------------------

This is the **deterministic, hermetic** twin of the natural-death path.
It exercises the same private :meth:`SandboxPhase2Loop._die` code path
as the T-B-020 restart tests but reaches it via the env-var hook
instead of waiting for an actual settlement-driven BREATH depletion.

Acceptance criteria from the T-B-021 task brief
-----------------------------------------------

1. Set ``SANDBOX_FORCE_TERMINAL=1`` (via the loop's ``env`` injection,
   NOT the real process env — keeps the test runner clean).
2. Run :class:`SandboxPhase2Loop` for ONE tick with a chain mock.
3. Assert ``kill_and_mint_tombstone`` called EXACTLY once with the
   correct ``agent_id``.
4. Assert the Tombstone mint tx was submitted with non-empty metadata:
   ``last_words`` / ``final_weights_hash`` / ``memory_bank_cid``.

Additional invariants we lock here (defense in depth)
-----------------------------------------------------

* The ``final_weights_hash`` is the deterministic SHA-256 of the
  weights JSON — recomputing it in the test must equal the value the
  chain adapter received (proves the hash is honest).
* The forced-terminal hook is ONE-SHOT — :attr:`force_terminal_pending`
  is True between ``run()`` entry and the first tick, then False
  forever after.
* :meth:`SandboxPhase2Loop._die` flips ``self._alive`` False and a
  second ``run()`` is a no-op (the "died-before-start" refusal). This
  protects against an operator-runbook resurrection bug.
* The default ``memory_bank_cid`` falls back to
  :data:`DEFAULT_MEMORY_BANK_CID_PLACEHOLDER` when no IPFS pin is
  configured — and that placeholder is non-empty by construction.
* Without the env var, the same construction sequence produces NO
  death — confirms the hook does not leak into the natural-flow path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase
from agent.data.polymarket_sandbox_executor import MarketInfo, SandboxExecutor
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    AgentStateSnapshot,
    SandboxStateWriter,
    iter_jsonl,
)
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DEFAULT_AGENT_ID,
    DEFAULT_MEMORY_BANK_CID_PLACEHOLDER,
    SANDBOX_FORCE_TERMINAL_ENV_VAR,
    DeathReceipt,
    RunSummary,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    TickInputSource,
    WeightUpdaterPhase,
    _default_last_words_template,
    _sha256_hex_prefixed,
)
from tests.agent.runtime.fixtures.mock_gamma_api import MockGammaAPI


# --------------------------------------------------------------------------- #
# Test doubles — minimal fakes covering the forced-terminal scenario.
# --------------------------------------------------------------------------- #


@dataclass
class _RecordingChainAdapter:
    """Chain adapter that records every call against the loop Protocol.

    Implements :class:`SandboxLoopChainAdapter`. Distinct from the
    T-B-020 ``FakeChainAdapter`` in ``test_sandbox_restart.py`` because
    this test asserts on per-call kwarg shapes the T-B-020 fake did
    not need to capture (the new T-B-021 fields).
    """

    current_breath: float = 80.0
    pnl_updates: list[float] = field(default_factory=list)
    read_calls: int = 0
    kill_calls: list[dict[str, Any]] = field(default_factory=list)

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)
        self.current_breath += pnl_usd

    async def read_breath(self) -> float:
        self.read_calls += 1
        return self.current_breath

    async def kill_and_mint_tombstone(
        self,
        *,
        agent_id: str,
        bankroll_usd: float,
        last_tick: int,
        final_weights_hash: str,
        memory_bank_cid: str,
        last_words: str,
    ) -> DeathReceipt:
        self.kill_calls.append(
            {
                "agent_id": agent_id,
                "bankroll_usd": bankroll_usd,
                "last_tick": last_tick,
                "final_weights_hash": final_weights_hash,
                "memory_bank_cid": memory_bank_cid,
                "last_words": last_words,
            }
        )
        return DeathReceipt(
            kill_tx_hash="0x" + "k" * 64,
            tombstone_token_id="ts-001",
            tombstone_tx_hash="0x" + "t" * 64,
        )


class _RecordingStateHook:
    """Records every emitted state hook for assertion."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]

    def first_with_kind(self, kind: str) -> dict[str, Any] | None:
        for evt in self.events:
            if evt["kind"] == kind:
                return evt
        return None


class _NopSleeper:
    async def __call__(self, seconds: float) -> None:  # pragma: no cover - trivial
        return None


class _FixedClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


@dataclass
class _NullTickInputs:
    """Force the decision engine to NO_BET so the kill path is the only
    side effect of the forced-terminal tick — keeps the test's
    assertion surface tight (no executor records to filter out).
    """

    market_id: str = "m-force-terminal-001"

    def inputs_for(self, *, asof_ts: datetime, tick: int) -> TickInputs | None:
        return None  # routes to NO_BET / no_eligible_market


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
# Builder
# --------------------------------------------------------------------------- #


def _build_loop(
    *,
    tmp_path: Path,
    env: dict[str, str] | None,
    chain_adapter: _RecordingChainAdapter,
    state_hook: _RecordingStateHook,
    agent_id: str = "genesis_v1_test",
    last_words: str | None = None,
    memory_bank_cid: str = DEFAULT_MEMORY_BANK_CID_PLACEHOLDER,
) -> tuple[SandboxPhase2Loop, SandboxStateWriter]:
    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    mb_root = tmp_path / "_mb"
    mb_root.mkdir(parents=True, exist_ok=True)

    writer = SandboxStateWriter(root=state_dir)
    clock = _FixedClock(start=datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC))
    sleeper = _NopSleeper()
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: MarketInfo(end_date_iso="2026-05-26T17:00:00+00:00"),
        clock=clock,
    )
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,
    )
    gamma = MockGammaAPI()
    # Mirror the T-B-020 settlement-poller-protocol FakeWeightUpdater
    # since we need a settlement client + a (non-firing) weight updater.

    class _SilentWeightUpdater:
        async def update(
            self,
            *,
            phase: str,
            signals: dict[str, float],
            outcome: SettlementResult,
        ) -> None:
            return None

    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=gamma,
        weight_updater=_SilentWeightUpdater(),
        chain_adapter=cast(SandboxLoopChainAdapter, chain_adapter),
        tick_inputs=_NullTickInputs(),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=sleeper,
        decision_cadence=timedelta(0),
        initial_breath=chain_adapter.current_breath,
        initial_bankroll_usd=80.0,
        agent_id=agent_id,
        memory_bank_cid=memory_bank_cid,
        last_words=last_words,
        env=env,
    )
    return loop, writer


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_force_terminal_env_var_constant_locks_canonical_name() -> None:
    """The canonical env var name is locked by CEO Day 5 plan."""
    assert SANDBOX_FORCE_TERMINAL_ENV_VAR == "SANDBOX_FORCE_TERMINAL"


def test_default_agent_id_constant_locks_genesis_v1() -> None:
    """``DEFAULT_AGENT_ID`` matches :meth:`SandboxPhase2Loop.to_agent_state`."""
    assert DEFAULT_AGENT_ID == "genesis_v1"


def test_default_memory_bank_cid_placeholder_is_non_empty() -> None:
    """PRD §5.1 metadata fields are required to be non-empty."""
    assert DEFAULT_MEMORY_BANK_CID_PLACEHOLDER != ""
    assert isinstance(DEFAULT_MEMORY_BANK_CID_PLACEHOLDER, str)


def test_default_last_words_template_renders_non_empty_string() -> None:
    """Fallback template MUST emit a deterministic non-empty string."""
    text = _default_last_words_template(last_tick=7, bankroll_usd=42.5)
    assert isinstance(text, str)
    assert len(text) > 20
    # Both inputs surfaced in the rendered text.
    assert "7" in text
    assert "42.5" in text


def test_sha256_hex_prefixed_matches_reference_implementation() -> None:
    """The helper's output equals an independent ``hashlib`` call."""
    payload = '{"weights":"deterministic"}'
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert _sha256_hex_prefixed(payload) == "0x" + digest


# --------------------------------------------------------------------------- #
# E2E — the CEO Day 5 V-gate.
# --------------------------------------------------------------------------- #


def test_force_terminal_drives_breath_to_zero_kills_and_mints(
    tmp_path: Path,
) -> None:
    """SANDBOX_FORCE_TERMINAL=1 → 1 tick → kill + Tombstone mint.

    Asserts:
      (i)    kill_and_mint_tombstone called EXACTLY once.
      (ii)   The single call carries ``agent_id`` = the constructor arg.
      (iii)  ``last_words``, ``final_weights_hash``, ``memory_bank_cid``
             are all non-empty strings.
      (iv)   The forced-terminal hook self-cleared (one-shot).
      (v)    The RunSummary reports died=True with the receipt.
      (vi)   ``alive`` flips False; a second run() is the no-op refusal.
      (vii)  A ``force_terminal_armed`` state hook fired at run entry.
      (viii) An ``agent_died`` state hook fired carrying the metadata
             (operator visibility — dashboards consume this).
      (ix)   The terminal snapshot persists ``phase = PHASE_4_TERMINAL``.
    """
    chain = _RecordingChainAdapter(current_breath=80.0)
    hook = _RecordingStateHook()
    loop, writer = _build_loop(
        tmp_path=tmp_path,
        env={SANDBOX_FORCE_TERMINAL_ENV_VAR: "1"},
        chain_adapter=chain,
        state_hook=hook,
        agent_id="genesis_v1_test",
        last_words="Sandbox extended Phase 2 terminated by V-gate hook.",
    )

    # Capture pre-kill weights so we can recompute the hash independently.
    pre_kill_weights = loop.weights

    summary: RunSummary = asyncio.run(loop.run(max_ticks=1))

    # ---- (i) + (ii): kill called exactly once with correct agent_id ----
    assert len(chain.kill_calls) == 1
    kill_kwargs = chain.kill_calls[0]
    assert kill_kwargs["agent_id"] == "genesis_v1_test"

    # ---- (iii): all four metadata fields non-empty ---------------------
    assert isinstance(kill_kwargs["last_words"], str)
    assert kill_kwargs["last_words"] != ""
    assert kill_kwargs["last_words"] == (
        "Sandbox extended Phase 2 terminated by V-gate hook."
    )
    assert isinstance(kill_kwargs["final_weights_hash"], str)
    assert kill_kwargs["final_weights_hash"].startswith("0x")
    assert len(kill_kwargs["final_weights_hash"]) == 66  # 0x + 64 hex chars
    assert isinstance(kill_kwargs["memory_bank_cid"], str)
    assert kill_kwargs["memory_bank_cid"] == DEFAULT_MEMORY_BANK_CID_PLACEHOLDER

    # Hash provenance — recompute independently from the weights JSON.
    expected_hash = _sha256_hex_prefixed(pre_kill_weights.model_dump_json())
    assert kill_kwargs["final_weights_hash"] == expected_hash

    # ---- (iv): forced-terminal hook is one-shot ------------------------
    assert loop.force_terminal_pending is False

    # ---- (v): RunSummary reports the death + receipt -------------------
    assert summary.died is True
    assert summary.ticks_completed == 1
    assert summary.death_receipt is not None
    assert summary.death_receipt.kill_tx_hash == "0x" + "k" * 64
    assert summary.death_receipt.tombstone_token_id == "ts-001"
    assert summary.death_receipt.tombstone_tx_hash == "0x" + "t" * 64
    assert summary.final_breath == 0.0

    # ---- (vi): alive flips False; second run is the refusal ------------
    assert loop.alive is False
    summary_2 = asyncio.run(loop.run(max_ticks=5))
    assert summary_2.ticks_completed == 0
    assert summary_2.died is True
    # The chain adapter is NOT called a second time (refusal is in-memory).
    assert len(chain.kill_calls) == 1

    # ---- (vii): force_terminal_armed state hook at run entry -----------
    armed_evt = hook.first_with_kind("force_terminal_armed")
    assert armed_evt is not None
    assert armed_evt["env_var"] == SANDBOX_FORCE_TERMINAL_ENV_VAR

    # ---- (viii): agent_died state hook carries the metadata ------------
    died_evt = hook.first_with_kind("agent_died")
    assert died_evt is not None
    assert died_evt["agent_id"] == "genesis_v1_test"
    assert died_evt["last_words"] == kill_kwargs["last_words"]
    assert died_evt["final_weights_hash"] == kill_kwargs["final_weights_hash"]
    assert died_evt["memory_bank_cid"] == kill_kwargs["memory_bank_cid"]
    assert died_evt["tombstone_token_id"] == "ts-001"

    # ---- (ix): terminal snapshot phase = PHASE_4_TERMINAL --------------
    snapshot_payload = json.loads(writer.snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_payload["phase"] == "PHASE_4_TERMINAL"
    assert snapshot_payload["breath"] == 0.0


# --------------------------------------------------------------------------- #
# Negative-control: env var NOT set → no forced kill, T-B-020 behaviour holds.
# --------------------------------------------------------------------------- #


def test_no_env_var_means_no_forced_kill(tmp_path: Path) -> None:
    """Without SANDBOX_FORCE_TERMINAL=1 the loop runs normally.

    This is the T-B-021 regression guard the brief acceptance criterion
    "Without the env var, T-B-020 restart resilience tests still pass —
    no regression" demands at the unit-test level.
    """
    chain = _RecordingChainAdapter(current_breath=80.0)
    hook = _RecordingStateHook()
    loop, writer = _build_loop(
        tmp_path=tmp_path,
        env={},  # empty — no SANDBOX_FORCE_TERMINAL key
        chain_adapter=chain,
        state_hook=hook,
    )
    summary = asyncio.run(loop.run(max_ticks=1))

    # No forced kill — the loop did NOT call kill_and_mint_tombstone.
    assert len(chain.kill_calls) == 0
    # No force_terminal_armed event.
    assert "force_terminal_armed" not in hook.kinds()
    # The loop completed 1 tick and is still alive.
    assert summary.ticks_completed == 1
    assert summary.died is False
    assert loop.alive is True
    # Chain breath was NOT zeroed — the hook never fired.
    assert chain.current_breath > 0.0


def test_env_var_value_other_than_one_does_not_trigger(tmp_path: Path) -> None:
    """The canonical value is exactly ``"1"`` — other truthy strings are ignored.

    CEO Day 5 plan locks the canonical string; honouring ``"true"`` /
    ``"yes"`` / ``"on"`` would make the testnet runbook ambiguous about
    which value the operator actually exported. This test pins the
    string-equality invariant.
    """
    # Each variant gets its own sub-dir; the variant strings themselves
    # include characters Windows pathlib cannot mkdir (spaces, empty),
    # so we use a safe enumeration prefix.
    variants = ["true", "yes", "on", "TRUE", "1 ", " 1", ""]
    for idx, non_canonical in enumerate(variants):
        chain = _RecordingChainAdapter(current_breath=80.0)
        hook = _RecordingStateHook()
        loop, _ = _build_loop(
            tmp_path=tmp_path / f"variant_{idx}",
            env={SANDBOX_FORCE_TERMINAL_ENV_VAR: non_canonical},
            chain_adapter=chain,
            state_hook=hook,
        )
        asyncio.run(loop.run(max_ticks=1))
        assert len(chain.kill_calls) == 0, (
            f"env value {non_canonical!r} unexpectedly triggered forced-terminal"
        )


def test_force_terminal_with_existing_open_bet_still_kills(tmp_path: Path) -> None:
    """Even with prior open bets, force-terminal drives breath to 0 → kill.

    The brief's Acceptance §3 mandates the E2E test runs through ONE
    tick with a chain mock. If the loop's prior state has open bets,
    the forced-terminal still has to fire — the kill path is the V-gate,
    not the open-bet count.

    We pre-seed an open-bet line into ``open_bets.jsonl`` (mirrors the
    T-B-020 reconstruction fold) and assert kill still fires.
    """
    chain = _RecordingChainAdapter(current_breath=80.0)
    hook = _RecordingStateHook()
    loop, writer = _build_loop(
        tmp_path=tmp_path,
        env={SANDBOX_FORCE_TERMINAL_ENV_VAR: "1"},
        chain_adapter=chain,
        state_hook=hook,
    )
    # Pre-seed a snapshot + an open-bet row so reconstruction folds them.
    writer.snapshot_path.write_text(
        AgentStateSnapshot(
            snapshot_ts="2026-05-26T19:00:00+00:00",
            phase="PHASE_2_APPRENTICE",
            breath=80.0,
            bankroll_usd=80.0,
            phase_age_days=0.0,
            open_bet_ids=["pre-existing-bet-001"],
            last_tick=3,
            weights=loop.weights,
        ).model_dump_json(),
        encoding="utf-8",
    )
    writer.open_bets_path.write_text(
        json.dumps(
            {
                "bet_id": "pre-existing-bet-001",
                "ts": "2026-05-26T18:00:00+00:00",
                "market_id": "m-pre-existing",
                "side": "YES",
                "price": 0.5,
                "size_usd": 10.0,
                "expected_settle_ts": "2026-05-26T19:00:00+00:00",
                "status": "open",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = asyncio.run(loop.run(max_ticks=1))

    assert len(chain.kill_calls) == 1
    assert summary.died is True
    assert chain.current_breath == 0.0
