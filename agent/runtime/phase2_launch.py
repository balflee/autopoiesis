"""Phase 2 launch orchestrator — the D11 hard-deadline convergence (TP §8).

Spec anchors
------------

* PRD §6.13 ("Phase 分段激活"): Phase 2 turns ON Passive Metabolism at
  half rate, Action Cost enabled, 60-minute forced decision cadence,
  Lung Expansion enabled, USDC bankroll = shadow.
* PRD §9 Demo §1:30-2:30: PLAYBACK of "Phase 2 Day 4 first-Twitter-
  mistake" 5-tick arc. The captured tape lives in
  ``data/fixtures/phase2_demo_tape.json`` and is produced via the
  :meth:`Phase2LaunchOrchestrator.capture_demo_tape` helper.
* TECHNICAL_PLAN §8 D11: "Phase 1 → Phase 2 切换 + Phase 2 正式上线 🚨
  Hard Deadline 🚨". This file is the Track B half of that switch.
* TECHNICAL_PLAN §15 Gap 1: ``polymarket_executor.py`` real-money
  path deferred to Phase 3 sprint — the orchestrator routes BET
  actions through a dry-run executor protocol that records the call
  without broadcasting. The runbook calls this out explicitly.

Design
------

The orchestrator is the **single entry point** for the Phase 2 launch
sequence — both the test-driven smoke and the operator's live run go
through :meth:`Phase2LaunchOrchestrator.boot`. The construction
surface is built from Protocols so:

* tests inject :class:`_FakePhaseManagerReader` + a
  ``FakeGeminiClient`` + a fake decision-log writer (all under
  ``tests/agent/integration/conftest.py``)
* the live operator wires real chain / Gemini / Polygon adapters
  through the same Protocols

The boot sequence:

1. Read phase via :class:`_PhaseManagerReader` (Protocol). Assert
   the on-chain phase is now Phase 2; refuse to proceed otherwise.
   This is the hard "did the advancePhase tx actually land" guard.
2. Emit a ``phase_transition`` WS frame Phase 1 → Phase 2.
3. Emit the one-shot ``llm_activated`` WS frame +
   persist a :class:`PhaseActivationEvent` to MemoryBank (the dashboard
   reads the file as the overlay trigger).
4. Emit an initial ``vitals`` frame so the right-rail vitals strip
   renders within the brief's 60-second budget.
5. Run a single hermetic decision tick:
   * synthesise the 5 engine signals via the injected
     :class:`_EngineSignalSource` (real engines in production; a
     deterministic fake in tests).
   * fuse + size via the existing :class:`DecisionEngine`.
   * route the action through the injected :class:`_DecisionLogWriter`
     (the Protocol that wraps the on-chain ``DecisionLog.append``).
   * emit a ``decision`` frame.
6. Emit a templated ``reflection`` frame (NOT a live Gemini call —
   the Phase 2 launch smoke must never hit the network).
7. Emit a ``weights_updated`` frame so Track D's evolution curve
   renders the post-tick parameters.
8. Emit a closing ``vitals`` frame so the strip ticks again.

Dry-run mode (:meth:`Phase2LaunchOrchestrator.dry_run_plan`) returns
the **plan only** — a structured dict of what would happen, without
calling any of the injected adapters. The brief's acceptance
criterion "verified by zero-outbound-call test" tests that the
``--dry-run`` path makes ZERO calls on the chain reader / decision
log writer / LLM client.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from agent.core.memory_bank import MemoryBank
from agent.core.state import (
    Action,
    ActionKind,
    AgentState,
    Phase,
    Vitals,
    Weights,
)
from agent.dashboard_bridge import WsEventEmitter
from agent.dashboard_bridge.event_emitter import DEFAULT_LLM_ACTIVATION_NOTE
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
    DecisionEngine,
)
from agent.llm._phase_activation import PhaseActivationEmitter

# Default Phase 2 entry parameters — match TP §8 / PRD §6.13 spec.
# Sourced as defaults so a calibration-driven override is a single
# constructor arg without touching the boot sequence.
DEFAULT_PHASE2_BREATH: Final[float] = 100.0
DEFAULT_PHASE2_BANKROLL_USD: Final[float] = 100.0
DEFAULT_PHASE2_COUNTDOWN_S: Final[float] = 3600.0  # 60-min forced cadence
DEFAULT_PHASE2_GAS_PER_MIN: Final[float] = 0.5  # Passive Metabolism 半速

# Phase 2 entry weights — α₁/β₁ canonical proxies. β₁ unfreezes off 0.
# The arrays match the on-disk Weights schema; the scalar proxies the
# WS schema needs are pulled via ``alpha[0]`` / ``beta[0]``.
PHASE2_DEFAULT_W_R: Final[float] = 0.65  # Rational stream still dominant
PHASE2_DEFAULT_W_S: Final[float] = 0.35  # Sentient stream wakes up
PHASE2_DEFAULT_ALPHA: Final[tuple[float, float, float]] = (0.5, 0.3, 0.2)
PHASE2_DEFAULT_BETA: Final[tuple[float, float]] = (0.6, 0.4)  # β₁=0.6 (off 0)
PHASE2_DEFAULT_RHO: Final[float] = 0.25  # conservative Kelly scaler

# Demo §9 1:30-2:30 PLAYBACK arc length — 5 ticks per PRD §9.
DEMO_TAPE_TICK_COUNT: Final[int] = 5

# Demo tape default market id — matches the curated public snapshot
# ``public/snapshots/phase2_day4_first_twitter_mistake.json``.
DEMO_TAPE_MARKET_ID: Final[str] = "polymarket:nba:lakers_at_celtics:2026-04-12"


# ---------------------------------------------------------------------------
# Injectable Protocols
# ---------------------------------------------------------------------------


class _PhaseManagerReader(Protocol):
    """Reads the current phase from the on-chain ``PhaseManager``.

    Production impl wraps ``web3.py`` against the deployed contract;
    tests inject :class:`_FakePhaseManagerReader` that returns a
    fixed phase. Both impls are sync — phase reads are eth_call
    only and don't need an event loop.
    """

    def read_phase(self) -> Phase: ...


class _DecisionLogWriter(Protocol):
    """Records the decision on-chain (``DecisionLog.append`` v0.1.0).

    Production: a thin ``web3.py`` adapter signing + broadcasting a
    transaction. Tests: a fake that captures the call args. Returns
    an opaque tx ref (hash hex string) for log reporting; tests can
    return ``"0x_fake_tx_<n>"`` deterministically.

    The Phase 2 launch smoke MUST exercise this surface — the brief's
    success criterion is "first NO_BET decision logged on fake
    DecisionLog".
    """

    def append(
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str: ...


class _EngineSignalSource(Protocol):
    """Produces the 5 engine signals for the boot tick.

    Production: an :class:`agent.engines.base.Engine` fanout via
    :func:`asyncio.gather`. Tests: a deterministic dict generator.
    The Protocol is sync because the boot tick is sequenced — there's
    no parallelism win during a single-tick smoke.
    """

    def signals_for(self, *, asof_ts: datetime) -> dict[str, Signal]: ...


# ---------------------------------------------------------------------------
# Demo §9 1:30-2:30 PLAYBACK beats — typed for mypy --strict.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DemoBeat:
    """One curated tick of the Phase 2 Day 4 'first Twitter mistake' arc.

    Carries the canned narrative copy + the action shape. Frozen because
    these are static demo beats; mutating one would desync the captured
    fixture from the Playback story.
    """

    thought: str
    action: ActionKind
    reflection: str
    breath_after: float
    bankroll_after: float
    side: str | None = None
    size_usd: float | None = None
    edge_pct: float | None = None
    kelly_fraction: float | None = None


_DEMO_BEATS: Final[tuple[_DemoBeat, ...]] = (
    _DemoBeat(
        thought=(
            "Saw a sentiment spike on Twitter — Lakers fanboys piling in. "
            "The model says fade them, but the crowd feels right. "
            "Let's try a small BET on YES."
        ),
        action=ActionKind.BET,
        side="YES",
        size_usd=12.0,
        edge_pct=0.07,
        kelly_fraction=0.18,
        reflection=(
            "First real Phase 2 tick. β₁ is talking now — louder than "
            "expected. I weighted the LLM too high; lost the bet. "
            "Lesson logged."
        ),
        breath_after=92.0,
        bankroll_after=88.0,
    ),
    _DemoBeat(
        thought=(
            "Stinging from that loss. The 5 engines disagree wildly. "
            "NO_BET; let me read the next signal cycle first."
        ),
        action=ActionKind.NO_BET,
        reflection=(
            "Cooling off. Rational stream still trusts NBA technical; "
            "sentient stream is over-weighting Twitter. Want to see the "
            "second signal."
        ),
        breath_after=86.5,
        bankroll_after=88.0,
    ),
    _DemoBeat(
        thought=(
            "Smart money wallets are positioning against the public. "
            "Strong α₃ signal but β₁ disagrees. Holding."
        ),
        action=ActionKind.NO_BET,
        reflection=(
            "Smart money + NBA technical both bearish on the favourite. "
            "LLM still bullish. Conflict — stay flat until conviction "
            "lines up."
        ),
        breath_after=81.0,
        bankroll_after=88.0,
    ),
    _DemoBeat(
        thought=(
            "All three rational engines agree now. LLM tempered. Small "
            "BET on NO — half the size of mistake #1."
        ),
        action=ActionKind.BET,
        side="NO",
        size_usd=6.0,
        edge_pct=0.11,
        kelly_fraction=0.22,
        reflection=(
            "Took the convergence trade. Sized it conservatively. Win — "
            "bankroll back to 94. The lesson held."
        ),
        breath_after=76.0,
        bankroll_after=94.0,
    ),
    _DemoBeat(
        thought=(
            "Day 4 close. β₁ taught me something: don't let one loud "
            "channel drown the other four. Tomorrow: weight by agreement, "
            "not by volume."
        ),
        action=ActionKind.NO_BET,
        reflection=(
            "Day 4 retrospective. β₁ unfrozen helped — but only when the "
            "other four corroborated. The mistake was treating LLM as a "
            "leading signal; it's a corroborating one. Weight schedule "
            "update queued."
        ),
        breath_after=70.5,
        bankroll_after=94.0,
    ),
)


# ---------------------------------------------------------------------------
# Result + Plan dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Phase2LaunchPlan:
    """Structured "what would happen" descriptor for ``--dry-run`` mode.

    Every field is a plain string / int — JSON-serialisable so the
    dry-run CLI dumps it verbatim. Producing a Plan does NOT touch
    chain / Gemini / DecisionLog adapters; the test
    :func:`tests.agent.integration.test_phase2_launch_smoke.test_dry_run_makes_zero_outbound_calls`
    asserts that.
    """

    target_phase: str
    chain_read_calls_planned: int
    decision_log_writes_planned: int
    ws_frames_planned: int
    network_calls_planned: int
    actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_phase": self.target_phase,
            "chain_read_calls_planned": self.chain_read_calls_planned,
            "decision_log_writes_planned": self.decision_log_writes_planned,
            "ws_frames_planned": self.ws_frames_planned,
            "network_calls_planned": self.network_calls_planned,
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class Phase2BootResult:
    """Output of :meth:`Phase2LaunchOrchestrator.boot`.

    Carries the captured WS tape + the decision-log tx ref + the
    persisted activation event path so the smoke test can assert on
    every observable side effect of the boot.
    """

    target_phase: Phase
    ws_frames: list[dict[str, Any]]
    decision_action: Action
    decision_log_tx_ref: str
    llm_activation_path: Path
    agent_state: AgentState


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Phase2LaunchOrchestrator:
    """Convergence orchestrator for the Phase 2 launch.

    The constructor accepts Protocol-typed adapters so the same class
    drives both the test smoke (fakes) and the live launch (real
    chain / LLM / Polymarket clients).

    Parameters
    ----------
    memory_bank
        Where to persist the one-shot ``llm_activated.json`` blob +
        future tick journal. Required.
    phase_reader
        Reads the on-chain phase. Smoke / dry-run inject a fake.
    decision_log
        Writes the decision row to chain. Smoke / dry-run inject a fake.
    engine_signals
        Produces the 5 engine signals for the boot tick. Smoke / dry-run
        inject a deterministic generator. ``None`` is permitted in
        dry-run mode (the plan does not require signals).
    ws_emitter
        Captures dashboard frames. A fresh in-memory emitter is built
        if not provided.
    decision_engine
        Pure fusion + sizing math; defaults to a fresh instance.
    """

    def __init__(
        self,
        *,
        memory_bank: MemoryBank,
        phase_reader: _PhaseManagerReader,
        decision_log: _DecisionLogWriter,
        engine_signals: _EngineSignalSource | None = None,
        ws_emitter: WsEventEmitter | None = None,
        decision_engine: DecisionEngine | None = None,
        initial_breath: float = DEFAULT_PHASE2_BREATH,
        initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
        countdown_s: float = DEFAULT_PHASE2_COUNTDOWN_S,
        gas_per_min: float = DEFAULT_PHASE2_GAS_PER_MIN,
    ) -> None:
        self._bank = memory_bank
        self._phase_reader = phase_reader
        self._decision_log = decision_log
        self._signals_src = engine_signals
        self._ws = ws_emitter if ws_emitter is not None else WsEventEmitter()
        self._decision = decision_engine if decision_engine is not None else DecisionEngine()
        self._initial_breath = initial_breath
        self._initial_bankroll = initial_bankroll_usd
        self._countdown_s = countdown_s
        self._gas_per_min = gas_per_min

    # ----- Public read surface ---------------------------------------------

    @property
    def emitter(self) -> WsEventEmitter:
        """Read access to the WS emitter so callers can capture frames."""
        return self._ws

    # ----- Dry-run ---------------------------------------------------------

    def dry_run_plan(self) -> Phase2LaunchPlan:
        """Return the planned action list WITHOUT side effects.

        Hard contract — caller-observable invariants:

        * ``self._phase_reader.read_phase`` is NOT called.
        * ``self._decision_log.append`` is NOT called.
        * The WS emitter is NOT touched (frames list stays at whatever
          length it had at entry).
        * The MemoryBank is NOT written (no ``llm_activated.json``
          appears).
        * No environment variable is read; no network call is made.

        The boot smoke asserts these by binding **spy** versions of the
        adapters and confirming their call counters stay at zero across
        a ``dry_run_plan()`` invocation.
        """
        actions = [
            "[1/8] read on-chain phase via PhaseManager.currentPhase()",
            "[2/8] emit ws phase_transition: PHASE_1_INFANCY -> PHASE_2_APPRENTICE",
            "[3/8] emit ws llm_activated (one-shot) + persist memory_bank/observations/llm_activated.json",
            "[4/8] emit ws vitals (opening tick)",
            "[5/8] fanout 5 engines + run DecisionEngine.decide() (deterministic fake signals)",
            "[6/8] DecisionLog.append(...) for chosen action; consume BREATH per PRD §6",
            "[7/8] emit ws decision + reflection (template) + weights_updated",
            "[8/8] emit ws vitals (closing tick)",
        ]
        return Phase2LaunchPlan(
            target_phase=Phase.PHASE_2_APPRENTICE.value,
            chain_read_calls_planned=1,
            decision_log_writes_planned=1,
            # phase_transition + llm_activated + vitals_open + decision
            # + reflection + weights_updated + vitals_close = 7 frames.
            ws_frames_planned=7,
            network_calls_planned=0,  # all adapters are local / mocked
            actions=actions,
        )

    # ----- Full boot -------------------------------------------------------

    def boot(self, *, asof_ts: datetime | None = None) -> Phase2BootResult:
        """Execute the Phase 2 launch boot sequence.

        Returns a :class:`Phase2BootResult` carrying every observable
        side effect — the captured WS tape, the decision recorded on
        the (fake) decision log, the activation file path.

        Raises :class:`RuntimeError` if the on-chain phase is not
        Phase 2 — the operator runbook explicitly calls out that the
        ``advancePhase`` transaction must land BEFORE this boot is
        invoked. A pre-flight phase read confirms the chain state.
        """
        if self._signals_src is None:
            raise RuntimeError(
                "Phase2LaunchOrchestrator.boot requires engine_signals — "
                "construct with engine_signals=... or use dry_run_plan() "
                "for the no-side-effect path."
            )

        ts = asof_ts if asof_ts is not None else datetime.now(UTC)
        self._bank.ensure_layout()

        # Pre-flight: refuse if the chain hasn't actually transitioned.
        observed_phase = self._phase_reader.read_phase()
        if observed_phase != Phase.PHASE_2_APPRENTICE:
            raise RuntimeError(
                f"Phase 2 launch refused: on-chain phase is {observed_phase.value}; "
                "expected PHASE_2_APPRENTICE. Operator must broadcast the "
                "advancePhase transaction first (see phase2_launch.md §3)."
            )

        self._ws.emit_phase_transition(
            from_phase=Phase.PHASE_1_INFANCY,
            to_phase=Phase.PHASE_2_APPRENTICE,
            reason="β₁ unfreezes; passive metabolism resumes at half rate (PRD §6.13).",
            ts=ts,
        )

        # one-shot llm_activated: persisted file is the dashboard overlay
        # trigger; the WS frame is the live wake-up signal. Both fire so
        # the dashboard renders consistently whether the client connects
        # live or replays from disk.
        PhaseActivationEmitter(memory_bank=self._bank).emit(
            phase=2, model="gemini-3.1-flash-lite", now=ts,
        )
        activation_path = self._bank.observations_dir / "llm_activated.json"
        self._ws.emit_llm_activated(note=DEFAULT_LLM_ACTIVATION_NOTE, ts=ts)

        self._emit_vitals_snapshot(breath=self._initial_breath, ts=ts)

        signals = self._signals_src.signals_for(asof_ts=ts)
        _assert_signal_coverage(signals)
        action: Action = asyncio.run(
            self._decision.decide(
                signals=signals,
                weights_alpha=PHASE2_DEFAULT_ALPHA,
                weights_beta=PHASE2_DEFAULT_BETA,
                w_r=PHASE2_DEFAULT_W_R,
                w_s=PHASE2_DEFAULT_W_S,
                rho=PHASE2_DEFAULT_RHO,
                bankroll_usd=self._initial_bankroll,
                breath=self._initial_breath,
                liquidity_cap_usd=50.0,  # conservative cap for first Phase 2 tick
                market_id=DEMO_TAPE_MARKET_ID,
                desperate=False,
            )
        )

        # DecisionLog.append is the chain-side BREATH consumption per
        # PRD §6 (NO_BET is NOT a free skip).
        tx_ref = self._decision_log.append(
            market_id=action.market_id or DEMO_TAPE_MARKET_ID,
            action=action.kind,
            size_usd=float(action.size_usd or 0.0),
            side=action.side.value if action.side is not None else None,
            edge_pct=action.edge_pct,
        )

        # F0 (v0.3.0) — surface the decision-time per-engine score map +
        # the market id on the decision frame. Read-only telemetry: this
        # does NOT change the decision (the BET/NO_BET was already
        # computed above). ``signals`` is keyed by the 5 lowercase
        # persisted engine names (coverage asserted at line ~492). No
        # ``bet_id`` is threaded on this path — the Phase 2 launch tape
        # records on the DecisionLog (tx_ref) rather than minting an
        # executor order_id / BetRecord.bet_id.
        signal_scores = {name: signal.score for name, signal in signals.items()}
        self._ws.emit_decision(
            action=_action_kind_to_literal(action.kind),
            side=action.side.value if action.side is not None else None,
            size_usd=action.size_usd,
            edge_pct=action.edge_pct,
            market_id=action.market_id or DEMO_TAPE_MARKET_ID,
            signals=signal_scores,
            ts=ts,
        )
        self._ws.emit_reflection(insight=_template_reflection(action), ts=ts)
        self._ws.emit_weights_updated(
            w_r=PHASE2_DEFAULT_W_R,
            w_s=PHASE2_DEFAULT_W_S,
            alpha=PHASE2_DEFAULT_ALPHA[0],
            beta=PHASE2_DEFAULT_BETA[0],
            rho=PHASE2_DEFAULT_RHO,
            ts=ts,
        )

        # Closing vitals: half a tick of passive metabolism + action cost.
        post_breath = max(0.0, self._initial_breath - self._gas_per_min - 1.0)
        self._emit_vitals_snapshot(breath=post_breath, ts=ts)

        state = AgentState(
            tick=0,
            phase=Phase.PHASE_2_APPRENTICE,
            vitals=Vitals(
                breath=post_breath,
                bankroll_usd=self._initial_bankroll,
                phase_age_days=0.0,
            ),
            weights=Weights(
                w_r=PHASE2_DEFAULT_W_R,
                w_s=PHASE2_DEFAULT_W_S,
                alpha=list(PHASE2_DEFAULT_ALPHA),
                beta=list(PHASE2_DEFAULT_BETA),
                rho=PHASE2_DEFAULT_RHO,
            ),
            desperate=False,
        )

        return Phase2BootResult(
            target_phase=observed_phase,
            ws_frames=self._ws.frames,
            decision_action=action,
            decision_log_tx_ref=tx_ref,
            llm_activation_path=activation_path,
            agent_state=state,
        )

    def _emit_vitals_snapshot(self, *, breath: float, ts: datetime) -> None:
        """Emit a vitals frame with the orchestrator's fixed Phase 2
        bankroll / countdown / gas defaults — only ``breath`` varies
        between the opening and closing snapshots of a boot tick."""
        self._ws.emit_vitals(
            breath=breath,
            bankroll=self._initial_bankroll,
            countdown_s=self._countdown_s,
            gas_per_min=self._gas_per_min,
            phase=Phase.PHASE_2_APPRENTICE,
            ts=ts,
        )

    # ----- Demo tape -------------------------------------------------------

    def capture_demo_tape(
        self,
        *,
        boot_result: Phase2BootResult,
        tick_count: int = DEMO_TAPE_TICK_COUNT,
        asof_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Extend a boot result with curated PLAYBACK frames.

        Generates :data:`DEMO_TAPE_TICK_COUNT` follow-on ticks of
        canned (vitals / thought / decision / reflection / weights)
        frames covering the Phase 2 Day 4 first-Twitter-mistake arc.
        The fixture written to ``data/fixtures/phase2_demo_tape.json``
        is the output of this method.

        Returns the full tape (boot frames + follow-ons). The boot
        result is consumed so :meth:`capture_demo_tape` cannot run
        without a successful :meth:`boot`.
        """
        ts = asof_ts if asof_ts is not None else datetime.now(UTC)

        # Canned 5-tick arc: tick 1 is the "first Twitter mistake" BET
        # that loses; ticks 2-3 are NO_BET while breath recovers;
        # tick 4 takes a smaller BET that wins; tick 5 reflects.
        beats = _DEMO_BEATS[:tick_count]
        # Three weight refreshes (post-loss, mid-arc, close) so the
        # evolution-curve panel has three sample points to render.
        refresh_ticks = {0, len(beats) // 2, len(beats) - 1}

        for i, beat in enumerate(beats):
            self._ws.emit_thought(text=beat.thought, ts=ts)
            if beat.action == ActionKind.BET:
                self._ws.emit_decision(
                    action="BET",
                    side=beat.side,
                    size_usd=beat.size_usd,
                    edge_pct=beat.edge_pct,
                    kelly_fraction=beat.kelly_fraction,
                    ts=ts,
                )
            else:
                self._ws.emit_decision(action="NO_BET", ts=ts)
            self._ws.emit_reflection(insight=beat.reflection, ts=ts)
            self._ws.emit_vitals(
                breath=beat.breath_after,
                bankroll=beat.bankroll_after,
                countdown_s=self._countdown_s,
                gas_per_min=self._gas_per_min,
                phase=Phase.PHASE_2_APPRENTICE,
                ts=ts,
            )
            if i in refresh_ticks:
                self._ws.emit_weights_updated(
                    w_r=PHASE2_DEFAULT_W_R,
                    w_s=PHASE2_DEFAULT_W_S,
                    alpha=PHASE2_DEFAULT_ALPHA[0],
                    beta=PHASE2_DEFAULT_BETA[0] - (0.05 * (i + 1)),
                    rho=PHASE2_DEFAULT_RHO,
                    ts=ts,
                )

        # Return the full tape (boot frames + the curated follow-ons).
        return list(self._ws.frames)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_signal_coverage(signals: dict[str, Signal]) -> None:
    """All 5 engines must be present. A missing engine is a topology
    bug — the orchestrator refuses to proceed rather than route a
    NO_BET due to silent coverage gaps."""
    required = (
        TENNIS_TECHNICAL,
        MARKET_MOMENTUM,
        SURFACE_ADVANTAGE,
        HEAD_TO_HEAD,
        REST_RECENCY,
    )
    missing = [n for n in required if n not in signals]
    if missing:
        raise RuntimeError(
            f"Phase 2 boot requires all 5 engines, missing: {missing}"
        )


def _action_kind_to_literal(kind: ActionKind) -> Literal["BET", "NO_BET"]:
    """Narrow ``ActionKind`` (StrEnum) to the wire-schema Literal.

    Pure type narrowing — :attr:`ActionKind.value` is a plain ``str`` so
    mypy --strict refuses to assign it to a ``Literal["BET","NO_BET"]``
    field without an explicit branch.
    """
    return "BET" if kind == ActionKind.BET else "NO_BET"


def _template_reflection(action: Action) -> str:
    """Deterministic reflection text — Phase 2 launch smoke MUST NOT
    hit Gemini. Live reflections fire from the engines/reflection.py
    path once the persistent loop is wired in sprint_5.
    """
    if action.kind == ActionKind.BET:
        return (
            f"First Phase 2 decision: BET ${action.size_usd:.2f} on "
            f"{action.market_id} ({action.side and action.side.value}). "
            "β₁ is contributing for the first time — watch closely."
        )
    return (
        "First Phase 2 decision: NO_BET "
        f"({action.no_bet_reason}). β₁ is online but the 5 engines "
        "did not converge — patience wins this tick."
    )


__all__ = [
    "DEFAULT_PHASE2_BANKROLL_USD",
    "DEFAULT_PHASE2_BREATH",
    "DEFAULT_PHASE2_COUNTDOWN_S",
    "DEFAULT_PHASE2_GAS_PER_MIN",
    "DEMO_TAPE_MARKET_ID",
    "DEMO_TAPE_TICK_COUNT",
    "PHASE2_DEFAULT_ALPHA",
    "PHASE2_DEFAULT_BETA",
    "PHASE2_DEFAULT_RHO",
    "PHASE2_DEFAULT_W_R",
    "PHASE2_DEFAULT_W_S",
    "Phase2BootResult",
    "Phase2LaunchOrchestrator",
    "Phase2LaunchPlan",
]
