"""Shared deterministic stubs for the T-B-032 L3 e2e integration test.

What this module ships
----------------------

* :class:`DeterministicStrategyAdvisor` — Protocol-conformant
  :class:`StrategyAdvisor` that emits a pre-scripted
  :class:`StrategyProposal` exactly once, at a caller-chosen
  ``trigger_tick``. Every other call returns ``[]``. The brief locks
  "stub returns 1 weight_delta proposal at tick 100" — this is that
  stub, parameterised so the test can pick a smaller trigger tick to
  keep runtime under the 30 s budget.

* :class:`FakeLLMClient` — Protocol-conformant ``_LLMClient`` stub the
  test wires in case any code path constructs a real
  :class:`StrategyAdvisorImpl`. It asserts on call (we never want the
  LLM to actually fire under pytest); the deterministic advisor
  injection above means we expect zero calls in the happy path.

* :class:`FakeWeightUpdater` — settlement-time updater spy (the L3
  test does not exercise settlement gradient, but the loop requires
  the Protocol).

* :class:`FakeChainAdapter` — :class:`SandboxLoopChainAdapter` fake
  exposing a mutable ``current_breath`` so the loop's reconstruction
  step 4 (chain-is-source-of-truth) sees a positive balance and the
  death path stays dormant for the multi-tick e2e run.

* :class:`FakeStateHook` — captures every ``emit`` so tests can
  assert on the ``weight_delta_applied`` / ``strategy_advisor_fired``
  state-hook events the loop produces.

* :class:`FakeSleeper` — async no-op sleeper so ``decision_cadence``
  inter-tick waits do not burn wall-clock.

* :class:`FixedClock` — non-advancing :class:`Clock` whose ``now``
  returns a constant. Tests use this together with a
  far-future ``until`` + an explicit ``max_ticks`` bound (matching the
  T-B-030 L3 unit test pattern).

* :class:`ScriptedTickInputs` — :class:`TickInputSource` that returns
  the SAME bullish 5-engine signal bundle every tick. The exact signal
  values don't matter for the L3 e2e (we only care that the loop ticks
  + the L3 trigger fires) — bullish signals route to a BET, low
  confidence routes to NO_BET, either is fine for the test.

* :class:`NoopPhaseReader` / :class:`NoopDecisionLog` — Phase 2
  orchestrator dependencies the loop wraps but doesn't actually drive
  in tests; both are no-op spies. Mirrors the existing L3 unit-test
  fixtures in :mod:`tests.agent.runtime.test_sandbox_phase2_loop_l3`.

Hermetic invariants
-------------------

* Zero outbound calls — no Gemini, no Polymarket, no chain RPC. The
  socket sentinel in :mod:`tests.agent.integration.test_l3_loop`
  monkey-patches :mod:`socket` so any attempted network call fails
  loud rather than silently hitting the live API.
* No real LLM in :class:`FakeLLMClient` — calls always raise
  ``AssertionError`` to surface a misconfigured wiring.
* Deterministic IDs — every proposal carries a hex string we control
  so byte-for-byte JSONL assertions stay stable across runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from agent.core.state import Phase
from agent.data.polymarket_settlement import SettlementResult
from agent.engines._strategy_proposal_schema import (
    PROPOSAL_STATUS_PENDING,
    StrategyProposal,
)
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.engines.strategy_advisor import PerformanceWindow
from agent.runtime.sandbox_phase2_loop import (
    DeathReceipt,
    TickInputs,
)

# --------------------------------------------------------------------------- #
# Deterministic IDs — hand-picked so JSONL byte assertions stay stable.
# --------------------------------------------------------------------------- #

#: 32-char hex identifier emitted by :class:`DeterministicStrategyAdvisor`
#: for its one-and-only weight_delta proposal. Hand-picked over
#: :func:`uuid.uuid4` so the test can assert on the exact id after the
#: producer→consumer round-trip without snapshot fixtures.
DETERMINISTIC_PROPOSAL_ID: Final[str] = "deadbeef" * 4

#: Canonical weight key + amount the stub proposes. Picked ``alpha_2``
#: because it's the L3 advisor's most-tweaked axis in the e2e prompt
#: examples (``agent.engines._strategy_prompts``); +0.05 is large
#: enough that the apply step is visible in a float comparison without
#: bumping past the [0, 1] clamp.
DETERMINISTIC_DELTA_KEY: Final[str] = "alpha_2"
DETERMINISTIC_DELTA_AMOUNT: Final[float] = 0.05


# --------------------------------------------------------------------------- #
# DeterministicStrategyAdvisor — the brief's "stub returns 1 proposal".
# --------------------------------------------------------------------------- #


@dataclass
class DeterministicStrategyAdvisor:
    """Emit one weight_delta proposal exactly once at ``trigger_tick``.

    Brief acceptance criterion (Happy E2E scenario):

        "stub returns 1 weight_delta proposal at tick 100"

    The brief picks 100 to match the production
    :data:`DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL`; the e2e test
    overrides that to a smaller value via the loop's constructor so the
    integration test stays under the 30 s budget. We parameterise
    ``trigger_tick`` so the test can pick whatever cadence keeps
    runtime tight without re-implementing this stub.

    Idempotency: the stub fires at the FIRST call where
    ``window.tick >= trigger_tick``. Subsequent calls return ``[]`` so
    the loop's downstream book-keeping (ring buffer reset, baseline
    advance) exercises both the "fire" and "no-fire" branches in a
    single run.

    Attributes
    ----------
    trigger_tick
        Tick number at or beyond which the proposal fires. Defaults to
        ``0`` (fire on the FIRST :meth:`review_window` call) — the test
        controls the actual fire moment via the loop's
        ``strategy_advisor_tick_interval`` constructor knob, so this
        stub does not need to re-gate. Override to a positive value
        when a test wants to delay the proposal past several loop-side
        L3 trigger fires.
    proposal_id
        Stable id for the emitted proposal — defaults to
        :data:`DETERMINISTIC_PROPOSAL_ID`.
    delta_key
        Which weight component to bump. Must be a valid L3 key
        (``w_r``, ``alpha_0..2``, ``beta_0``, ``rho``).
    delta_amount
        Float to add to the chosen component (subject to the loop's
        clamp + renormalisation).
    calls
        Public counter of how many times :meth:`review_window` was
        invoked. Tests assert ``calls >= 1`` after the trigger tick.
    fired
        Public flag set ``True`` once the stub has emitted its
        single proposal. Tests assert this is ``True`` after the
        trigger window has been crossed.
    """

    trigger_tick: int = 0
    proposal_id: str = DETERMINISTIC_PROPOSAL_ID
    delta_key: str = DETERMINISTIC_DELTA_KEY
    delta_amount: float = DETERMINISTIC_DELTA_AMOUNT
    calls: int = 0
    fired: bool = False

    def review_window(
        self,
        window: PerformanceWindow,
    ) -> list[StrategyProposal]:
        """Return one :class:`StrategyProposal` at the trigger tick.

        Mirrors the production :class:`StrategyAdvisorImpl` contract
        (``status="pending"`` on emission, ``requires_human_approval``
        locked to ``True``). The ``ts`` is pulled from the window so
        the on-disk JSONL row carries the same timestamp the loop's
        state-hook event records — useful for cross-event correlation
        in tests.
        """
        self.calls += 1
        if self.fired or window.tick < self.trigger_tick:
            return []
        self.fired = True
        return [
            StrategyProposal(
                proposal_id=self.proposal_id,
                ts=window.ts,
                kind="weight_delta",
                rationale=(
                    f"deterministic e2e stub: bumping {self.delta_key} by "
                    f"{self.delta_amount:+.4f} at tick {window.tick}"
                ),
                proposed_change={
                    "key": self.delta_key,
                    "delta": self.delta_amount,
                },
                expected_impact="e2e validation seed",
                confidence_pct=60,
                requires_human_approval=True,
                status=PROPOSAL_STATUS_PENDING,
            )
        ]


# --------------------------------------------------------------------------- #
# FakeLLMClient — Protocol-conformant ``_LLMClient`` that refuses to fire.
# --------------------------------------------------------------------------- #


@dataclass
class FakeLLMClient:
    """``_LLMClient`` Protocol stub — raises on any structured_call.

    The e2e test injects a :class:`DeterministicStrategyAdvisor` (not
    :class:`StrategyAdvisorImpl`), so no LLM call path should ever
    execute. This stub is wired into the loop as a defence-in-depth
    sentinel: if a future refactor wires the production advisor by
    mistake, the test fails LOUD with this assertion rather than
    silently hitting the real Gemini endpoint.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        raise AssertionError(
            "FakeLLMClient invoked — the e2e test should never call the LLM "
            "(the deterministic advisor stub bypasses the LLM call path)."
        )


# --------------------------------------------------------------------------- #
# Loop-Protocol fakes — settlement, chain, state hook, clock, sleeper.
# --------------------------------------------------------------------------- #


class FakeWeightUpdater:
    """Settlement-time :class:`WeightUpdater` Protocol spy.

    The L3 e2e test does not drive any bets to settlement (the
    scripted tick inputs route to BETs but the gamma-api mock never
    resolves them within the test window), so this spy's ``calls``
    list stays empty in the happy path. Kept as a spy (not a no-op)
    so a future test wiring that DOES drive settlement can assert
    on the captured calls.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None:
        self.calls.append(
            {"phase": phase, "signals": dict(signals), "outcome": outcome}
        )


@dataclass
class FakeChainAdapter:
    """:class:`SandboxLoopChainAdapter` fake with mutable breath.

    The loop's ``_reconstruct_from_disk`` step 4 reads breath from the
    chain as source-of-truth; the test pins ``current_breath`` high
    enough that breath never goes to zero across the e2e tick window
    (death path is out of scope for the L3 e2e).
    """

    current_breath: float = 100.0
    pnl_updates: list[float] = field(default_factory=list)
    kill_calls: list[dict[str, Any]] = field(default_factory=list)

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)
        self.current_breath += pnl_usd

    async def read_breath(self) -> float:
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
        """Death-path stub — the L3 e2e test never reaches this branch.

        Captures the call args + returns a sentinel receipt so a
        regression that hits this path produces a deterministic crash
        signature (the test would then assert on ``kill_calls == []``).
        """
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
            kill_tx_hash="0xL3STUB_KILL",
            tombstone_token_id="0",
            tombstone_tx_hash="0xL3STUB_MINT",
        )


class FakeStateHook:
    """Records every ``emit`` call so tests can introspect state events.

    The L3 e2e test relies on three state-hook events:

    * ``strategy_advisor_fired`` — the L3 trigger fired AND the advisor
      returned at least one proposal.
    * ``weight_delta_applied``   — the loop drained an approved delta
      from :class:`RuntimeAgentRunner` and applied it to ``self._weights``.
    * (not in this test:) ``weight_delta_apply_failed``,
      ``strategy_advisor_failed``.

    :meth:`by_kind` filters the event log for ergonomic assertions.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == kind]


class FakeSleeper:
    """Async no-op sleeper — drops the seconds arg on the floor.

    Wired into the loop so ``decision_cadence`` does not burn
    wall-clock between ticks (the loop's brief locks this seam — see
    :data:`agent.runtime.sandbox_settlement_poller._real_sleep`).
    """

    async def __call__(self, seconds: float) -> None:
        del seconds


class FixedClock:
    """Non-advancing clock — every ``now()`` call returns the SAME ts.

    Tests use this together with ``decision_cadence=timedelta(0)`` and
    an explicit ``max_ticks`` bound so the loop's ``while now < until``
    guard never short-circuits the test mid-run.
    """

    def __init__(self, *, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now


@dataclass
class ScriptedTickInputs:
    """:class:`TickInputSource` returning a bullish-signal bundle every tick.

    The exact signal values are loop-irrelevant for the L3 e2e (the
    decision tick's BET/NO_BET branch fires regardless); they're set
    to high confidence so the loop exercises the BET path at least
    once per tick. Identical posture to
    :class:`tests.agent.runtime.test_sandbox_phase2_loop_l3._ScriptedTickInputs`.
    """

    market_id: str = "m-l3-e2e-001"
    price: float = 0.4
    liquidity_cap_usd: float = 50.0

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        iso = asof_ts.isoformat()
        signals: dict[str, Signal] = {
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
                rationale="wallets favour YES",
                raw_features={"tick": float(tick)},
            ),
            HEAD_TO_HEAD: Signal(
                score=0.6, confidence=0.8, available_at=iso,
                rationale="sentiment positive",
                raw_features={"tick": float(tick)},
            ),
            REST_RECENCY: Signal(
                score=0.6, confidence=0.85, available_at=iso,
                rationale="crowd volume rising sharply",
                raw_features={"tick": float(tick)},
            ),
        }
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
        )


class NoopPhaseReader:
    """Phase 2 launch dependency — always reports the apprentice phase."""

    def read_phase(self) -> Phase:  # pragma: no cover — not driven in test
        return Phase.PHASE_2_APPRENTICE


class NoopDecisionLog:
    """Phase 2 launch dependency — append is a no-op spy.

    The :class:`SandboxPhase2Loop` doesn't call this directly (only
    the wrapped :class:`Phase2LaunchOrchestrator` does, and tests do
    not drive its boot path), but the orchestrator constructor needs
    it. Return a sentinel tx ref so any accidental call surfaces in
    the test output.
    """

    def append(  # pragma: no cover — not driven in test
        self,
        *,
        market_id: str,
        action: Any,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        return "0xL3_STUB_TX"


# --------------------------------------------------------------------------- #
# Public exports
# --------------------------------------------------------------------------- #

__all__ = [
    "DETERMINISTIC_DELTA_AMOUNT",
    "DETERMINISTIC_DELTA_KEY",
    "DETERMINISTIC_PROPOSAL_ID",
    "DeterministicStrategyAdvisor",
    "FakeChainAdapter",
    "FakeLLMClient",
    "FakeSleeper",
    "FakeStateHook",
    "FakeWeightUpdater",
    "FixedClock",
    "NoopDecisionLog",
    "NoopPhaseReader",
    "ScriptedTickInputs",
]
