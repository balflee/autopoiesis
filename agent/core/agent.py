# Greek letters in the 9-step docstring mirror PRD §4.1 / §6.6 notation —
# disambiguating to Latin fallbacks would silently desync code from spec.
"""The Agent decision loop — 9-step body per TECHNICAL_PLAN §4.1.

Sprint_1 lays the loop body out as numbered comments so reviewers can
diff against the spec without needing the sprint_2 engine implementations
to land yet. Each step is wired to a stub call that raises
NotImplementedError('sprint_2'); the orchestration shape is locked, the
business logic is not.

Sprint_5 (T-B-009) adds the **Desperate-Mode + Last-Words tick wiring**
helpers — :func:`run_pressure_check` and :func:`run_terminal_lucidity` —
that the lifecycle scheduler calls each tick after step 8 (passive
burn). The helpers are factored out of :func:`agent_loop` so they can
be exercised by unit tests without standing up the full 9-step loop.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent.core.lifecycle import Lifecycle
from agent.core.memory_bank import MemoryBank
from agent.core.narrative import write_narrative
from agent.core.pressure_monitor import (
    EnterDesperateModeIntent,
    PressureMonitor,
    PressureSample,
)
from agent.core.state import Phase, TickPayload
from agent.llm.prompts.last_words import LAST_WORDS_BREATH_COST, LastWordsCache, LastWordsService

logger = logging.getLogger(__name__)

# Energy fraction at which Last Words fires per PRD §5.1.B ("能量低于
# 5% 时进入 terminal lucidity"). Surfacing the constant lets unit tests
# pin the boundary without re-deriving it.
LAST_WORDS_ENERGY_PCT_THRESHOLD: float = 5.0

# Retry config for the on-chain enterDesperateMode dispatch. PRD's
# TP §4.1 invariant: a tick MUST NOT crash on RPC / gas failure.
# 3× retry with jittered backoff (per acceptance criterion).
_DEFAULT_CHAIN_RETRIES: int = 3
_DEFAULT_CHAIN_BACKOFF_BASE_S: float = 0.5
_DEFAULT_CHAIN_BACKOFF_JITTER_S: float = 0.25

# Re-exports — sprint_2's agent_loop body will call these at step 9
# (atomic temp+rename per TECHNICAL_PLAN §4.6).
__all__ = [
    "DesperateModeDispatcher",
    "PressureCheckResult",
    "TerminalLucidityResult",
    "agent_loop",
    "default_memory_bank",
    "run_pressure_check",
    "run_terminal_lucidity",
    "write_narrative",
]


# ---------------------------------------------------------------------------
# Sprint_5 wiring — pressure check + terminal lucidity helpers
# ---------------------------------------------------------------------------


class DesperateModeDispatcher(Protocol):
    """Narrow Protocol for the chain adapter's ``enterDesperateMode`` call.

    The real implementation in agent.chain (lands sprint_4+) signs +
    sends the ``PhaseManager.enterDesperateMode(pressureAtEntry, cyclesHeld)``
    transaction. The Protocol shape here keeps the wiring helper
    chain-free so unit tests inject a deterministic fake.

    A successful dispatch returns the transaction hash (or any opaque
    receipt id); a failure raises :class:`RPCError` or
    :class:`InsufficientGas` (both surface as
    :class:`DegradedFeedWarning` at the helper boundary).
    """

    async def __call__(
        self,
        *,
        pressure_at_entry: float,
        cycles_held: int,
    ) -> str: ...


@dataclass(frozen=True)
class PressureCheckResult:
    """Per-tick output of :func:`run_pressure_check`.

    Bundles the live :class:`PressureSample` (always present) + the
    dispatch outcome (optional). Consumers:

    * dashboard_bridge — emits ``terminal_lucidity_entered`` /
      vitals frames using ``sample`` + ``intent_dispatched``.
    * weight_updater wiring — passes ``desperate=True`` to the next
      tick's :meth:`WeightUpdater.update_with_delta` when
      ``intent_dispatched`` is True for the first time.
    """

    sample: PressureSample
    intent_dispatched: bool = False
    dispatch_tx: str | None = None
    dispatch_attempts: int = 0
    dispatch_error: str | None = None
    critical_op_failed: bool = False


async def run_pressure_check(
    *,
    monitor: PressureMonitor,
    breath: float,
    effective_burn_rate_per_hour: float,
    phase: Phase,
    chain_dispatcher: DesperateModeDispatcher | None = None,
    retries: int = _DEFAULT_CHAIN_RETRIES,
    backoff_base_s: float = _DEFAULT_CHAIN_BACKOFF_BASE_S,
    backoff_jitter_s: float = _DEFAULT_CHAIN_BACKOFF_JITTER_S,
) -> PressureCheckResult:
    """One-tick pressure check + on-chain dispatch wrapper.

    Sequence (TP §4.1 invariant "cannot crash tick"):

    1. ``monitor.observe(...)`` computes the current
       :class:`PressureSample` + optional :class:`EnterDesperateModeIntent`.
    2. If the intent fires AND a ``chain_dispatcher`` is wired, attempt
       the on-chain ``enterDesperateMode`` call. Retry up to
       ``retries`` times with jittered backoff. After exhaustion,
       surface the failure via ``critical_op_failed=True`` and the
       monitor remains latched (the next tick won't re-fire).
    3. NEVER raise. RPC / insufficient-gas exceptions are captured as
       ``DegradedFeedWarning`` posture and returned in the result.

    Even when ``chain_dispatcher`` is ``None`` (e.g. test harness or
    early sprint where the chain adapter isn't wired yet) the helper
    returns a populated :class:`PressureCheckResult` so callers can
    journal the sample to MemoryBank.
    """
    sample, intent = monitor.observe(
        breath=breath,
        effective_burn_rate_per_hour=effective_burn_rate_per_hour,
        phase=phase,
    )
    if intent is None:
        return PressureCheckResult(sample=sample)

    # IMPORTANT — the off-chain EnterDesperateModeIntent is "emitted"
    # at this point in the function. The dispatch wrapper below is
    # the *attempt to forward* it on chain; per the brief the intent
    # itself fires BEFORE the chain dispatch so a chain outage cannot
    # swallow it (the reflection layer will still see the intent in
    # the memory bank).
    logger.info(
        "EnterDesperateModeIntent emitted: pressure=%.3f cycles=%d",
        intent.pressure_at_entry,
        intent.cycles_held,
    )

    if chain_dispatcher is None:
        return PressureCheckResult(
            sample=sample,
            intent_dispatched=False,
            dispatch_attempts=0,
            dispatch_error="no_chain_dispatcher_wired",
            critical_op_failed=False,
        )

    return await _dispatch_with_retries(
        sample=sample,
        intent=intent,
        chain_dispatcher=chain_dispatcher,
        retries=max(1, int(retries)),
        backoff_base_s=float(backoff_base_s),
        backoff_jitter_s=float(backoff_jitter_s),
    )


async def _dispatch_with_retries(
    *,
    sample: PressureSample,
    intent: EnterDesperateModeIntent,
    chain_dispatcher: DesperateModeDispatcher,
    retries: int,
    backoff_base_s: float,
    backoff_jitter_s: float,
) -> PressureCheckResult:
    """Inner retry loop — pulled out so the public helper stays linear."""
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        try:
            tx = await chain_dispatcher(
                pressure_at_entry=intent.pressure_at_entry,
                cycles_held=intent.cycles_held,
            )
            return PressureCheckResult(
                sample=sample,
                intent_dispatched=True,
                dispatch_tx=tx,
                dispatch_attempts=attempt,
                dispatch_error=None,
                critical_op_failed=False,
            )
        except Exception as exc:
            # Surface as DegradedFeedWarning posture — caller renders
            # the warning on the dashboard; the agent_loop continues.
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "enterDesperateMode dispatch attempt %d/%d failed: %s",
                attempt,
                retries,
                last_error,
            )
            if attempt < retries:
                # Cryptographically-random jitter so concurrent agents
                # do not synchronise their retries against the same
                # failing RPC endpoint. ``secrets.randbits`` keeps the
                # source non-deterministic even when ``random`` is
                # seeded by a calibration sweep.
                jitter = (secrets.randbits(16) / 0xFFFF) * backoff_jitter_s
                await asyncio.sleep(backoff_base_s * attempt + jitter)
    return PressureCheckResult(
        sample=sample,
        intent_dispatched=False,
        dispatch_tx=None,
        dispatch_attempts=retries,
        dispatch_error=last_error,
        critical_op_failed=True,
    )


@dataclass(frozen=True)
class TerminalLucidityResult:
    """Per-tick output of :func:`run_terminal_lucidity`.

    * ``fired`` — True iff Last Words was generated/loaded this tick.
    * ``cache`` — the persisted :class:`LastWordsCache` when ``fired``.
    * ``was_cached`` — True iff the call short-circuited on the
      one-shot guard (no LLM call this tick).
    * ``breath_cost`` — total BREATH cost (always
      :data:`LAST_WORDS_BREATH_COST` when fired the first time; 0 on
      cache hit because the LLM was not called).
    """

    fired: bool = False
    cache: LastWordsCache | None = None
    was_cached: bool = False
    breath_cost: int = 0


async def run_terminal_lucidity(
    *,
    service: LastWordsService,
    agent_id: str,
    tick: int,
    breath: float,
    initial_breath: float,
    phase_age_days: float,
    notable_lessons: list[str] | None = None,
) -> TerminalLucidityResult:
    """Trigger Last Words when energy < 5% (PRD §5.1.B).

    The one-shot guard lives on :class:`LastWordsService` — calling
    ``emit`` after the cache exists short-circuits and the LLM is
    NOT invoked. This helper:

    1. Compute energy_pct = 100 · breath / initial_breath.
    2. If energy_pct ≥ threshold, return a no-op
       :class:`TerminalLucidityResult`.
    3. Otherwise call :meth:`LastWordsService.emit`. The service
       handles the one-shot guard + fallback path internally.
    4. Set ``breath_cost`` = :data:`LAST_WORDS_BREATH_COST` on the
       first-emit path; 0 on the cache-hit path.

    The function NEVER raises — even a malformed LLM response routes
    to the template fallback inside the service, so the demo climax
    always has text to render.
    """
    if initial_breath <= 0.0:
        return TerminalLucidityResult()

    energy_pct = 100.0 * breath / initial_breath
    if energy_pct >= LAST_WORDS_ENERGY_PCT_THRESHOLD:
        return TerminalLucidityResult()

    # The service's own one-shot guard tells us whether THIS call did
    # the LLM round-trip — emit() returns the existing cache verbatim
    # on a hit (same emitted_at) and a fresh row on a miss. Comparing
    # by tick is cheaper than the previous extra already_emitted() +
    # exists() syscall.
    cache = await service.emit(
        agent_id=agent_id,
        tick=tick,
        breath_remaining=breath,
        initial_breath=initial_breath,
        phase_age_days=phase_age_days,
        notable_lessons=notable_lessons,
    )
    was_cached = cache.tick != tick
    return TerminalLucidityResult(
        fired=True,
        cache=cache,
        was_cached=was_cached,
        # PRD §6.2 pins total action cost ≤ 200 BREATH. On a cache hit
        # no LLM call was made so the *additional* burn this tick is 0
        # — the one-shot accounting already happened on the first call.
        breath_cost=0 if was_cached else LAST_WORDS_BREATH_COST,
    )


# ---------------------------------------------------------------------------
# 9-step agent loop (sprint_2 stub retained — body lands when the chain
# adapter + scheduler do)
# ---------------------------------------------------------------------------


async def agent_loop(
    *,
    tick: int,
    memory_bank: MemoryBank,
    lifecycle: Lifecycle,
) -> TickPayload:
    """Run a single iteration of the Genesis Agent decision cycle.

    The body follows the canonical 9-step sequence from TECHNICAL_PLAN
    §4.1. Sprint_1 sketches each step as a numbered comment + stub call
    so the orchestration shape is auditable now; the production engine
    bodies + chain commits + reflection LLM land in sprint_2+.

    Steps:

    # 1. Energy check — read on-chain BREATH balance via
    #    EnergyControllerAdapter. If below survival threshold, short-
    #    circuit to a NO_BET tick with action.no_bet_reason='out_of_breath'.
    # 2. Market pick — pull eligible Polymarket markets within the
    #    horizon, filter by liquidity floor, rank by raw mid divergence
    #    against engine prior.
    # 3. Parallel signals — asyncio.gather() across the 5 engines:
    #       Tennis技术 / 盘口动量 / Smart Money / LLM情绪 / Reddit关注度
    #    (agent.engines.tennis_technical, market_momentum, smart_money,
    #     sentiment_llm, crowd_volume). Each returns a normalised score.
    #    (Sport pivot per PRD §15 已决 #8: α₁ is tennis post-sprint_7.)
    # 4. Fusion + decision — agent.engines.decision applies the
    #    6-parameter 2-layer fuse (W_R, α₁, α₂, α₃, β₁, ρ) and runs the
    #    4-constraint min: ρ·kelly·confidence·bankroll, breath-risk cap,
    #    bankroll hard cap, market liquidity cap (PRD §6.6).
    # 5. Chain commit + Polymarket order — call
    #    EnergyController.recordBetDecisionAndConsume(...) for BET,
    #    consumeAction(NO_BET) for NO_BET (PRD §6 — NO_BET is NOT free),
    #    then route the BET to py-clob-client.
    # 6. Reflection — agent.engines.reflection runs the Claude reflection
    #    pipeline (Sonnet 4.6 default; Opus 4.7 at key moments) writing
    #    to .agent_state/memory_bank/reflections/.
    # 7. Weight update — agent.engines.weight_updater applies softmax-
    #    reparameterised gradient descent on (W_R, α, β, ρ) per Phase 1/2
    #    schedule (frozen in Phase 3/4 per PRD §4.5).
    # 7b. Pressure check — agent.core.pressure_monitor consults the
    #    rolling counter; on EnterDesperateModeIntent dispatch
    #    PhaseManager.enterDesperateMode() (sprint_5 T-B-009 wiring;
    #    see :func:`run_pressure_check`).
    # 7c. Terminal lucidity — if energy_pct < 5 trigger Last Words via
    #    agent.llm.prompts.last_words.LastWordsService.emit (one-shot
    #    guard; see :func:`run_terminal_lucidity`).
    # 8. Passive burn — apply the per-tick BREATH decay (PRD §6.2).
    # 9. MemoryBank + narrative — atomic temp+rename per
    #    TECHNICAL_PLAN §4.6: memory_bank.write_tick(payload) +
    #    narrative.write_narrative(payload) → diary string for the
    #    dashboard Consciousness Stream.

    Returns the persisted :class:`TickPayload` so callers (tests, ops
    harness) can inspect the tick's outcome.
    """
    raise NotImplementedError(
        "agent.core.agent.agent_loop lands in sprint_2 — see the 9-step "
        "comment block above for the canonical step sequence "
        "(TECHNICAL_PLAN §4.1)"
    )


def default_memory_bank(root: Path | None = None) -> MemoryBank:
    """Convenience factory used by :mod:`agent.main` and tests.

    Keeping the default location centralised here so a future move from
    ``.agent_state/`` to (say) ``$XDG_STATE_HOME/genesis-agent/`` is a
    single-line change."""
    return MemoryBank(root=root or Path(".agent_state/memory_bank"))
