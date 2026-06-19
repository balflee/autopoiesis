# Greek letters (β₁ / α / ρ) mirror PRD §4.1 notation for the fusion weights.
"""Sandbox Phase 2 extended multi-day loop — T-B-020 runtime composite.

Spec anchors
------------

* CEO sprint_8 D-2026-05-26-PLAN-002 (Day 3): "WRAPS (composition, not
  extends) :class:`Phase2LaunchOrchestrator`. Reconstruction:
  ``agent_state.json`` → JSONL latest-wins → tick counter → on-chain
  BREATH refresh. Two restart scenarios MANDATORY." The brief
  explicitly forbids subclassing the launch orchestrator — that
  invariant is checked by
  ``not issubclass(SandboxPhase2Loop, Phase2LaunchOrchestrator)``.

* PRD §6.4 (line 353-357): "默认每 45 分钟必须输出一个决策. 仅 BET 或
  NO_BET, 都消耗 BREATH. 沉默不存在." The sandbox extended Phase 2
  cadence widens to **60 minutes** (vs PRD §6.4 default 45 min) per the
  CEO Day 3 plan to reduce settlement-poll churn over multi-day runs.
  The widening lives on a constructor argument so the PRD default
  remains the module-level constant — see :data:`DEFAULT_DECISION_CADENCE`.

* PRD §6.9 (line 404-414): "Death | breath==0 | die() + Tombstone mint |
  P3." Sandbox testnet activates the death path so the mechanic is
  proven E2E without real money — see :meth:`SandboxPhase2Loop._die`.

* TECHNICAL_PLAN §4.1 (line 709-804): the 9-step ``agent_loop`` body.
  This module is the **sandbox runtime composite** that finally wires
  steps 3-9 together for an extended multi-day run, calling the
  T-B-018 executor + T-B-019 settlement poller as the chain-side
  side-effect surfaces.

* TECHNICAL_PLAN §4.2 (line 806-820): "6 fusion parameters trained via
  softmax-reparameterised gradient descent. PHASE_2_EXTENDED unlocks
  β₁ via existing phase parameter (no new flag)." The
  :data:`WeightUpdaterPhase.PHASE_2_EXTENDED` enum value is the canonical
  phase label the loop hands to every settlement-time weight updater
  call; the existing :func:`agent.engines.weight_updater.WeightUpdater`
  per-tick code already unlocks β₁ when ``phase != Phase.PHASE_1_INFANCY``,
  so the loop's only β₁-unlock requirement is "pass PHASE_2_EXTENDED to
  every settlement update".

* T-B-018 (``agent/data/polymarket_sandbox_executor.py``) + T-B-019
  (``agent/runtime/sandbox_settlement_poller.py``): consumed verbatim
  through their :class:`Executor` / :class:`SettlementClient` /
  :class:`SandboxStateWriter` Protocols. This module owns the multi-day
  driver + the restart-resilience reconstruction logic; it does **not**
  duplicate the executor / poller bodies.

Architectural invariants enforced inline
----------------------------------------

* **Composition, not inheritance.** ``self.base`` holds the
  :class:`Phase2LaunchOrchestrator` instance; the loop never subclasses
  it. Reviewers can verify with
  ``not issubclass(SandboxPhase2Loop, Phase2LaunchOrchestrator)``.

* **Append-only JSONL.** Every per-tick write goes through the shared
  :class:`SandboxStateWriter` — the loop never opens any of the three
  JSONL streams (``open_bets`` / ``settled_bets`` / ``decisions``) in
  any mode other than append.

* **Single-writer per process.** The loop and the poller share ONE
  :class:`SandboxStateWriter` instance (the orchestrator wires this
  in the constructor); two writers against the same root would break
  the dashboard's tail-followable invariant.

* **Chain as source of truth for BREATH.** Step 4 of the
  reconstruction (:meth:`_reconstruct_from_disk`) explicitly calls
  ``chain_adapter.read_breath()`` after the disk fold so a divergent
  on-disk breath value (e.g. a snapshot written before a chain-side
  refund) is corrected on restart. The disk snapshot's breath stays
  for dashboard hint purposes; the loop's live ``breath`` becomes the
  chain value.

* **Death is one-way.** Once :meth:`_die` fires, the loop refuses to
  continue: the death-receipt is sealed into the run summary and
  ``alive`` flips to False. A subsequent ``run()`` call is a no-op
  that returns a "died-before-start" summary so the operator runbook
  cannot accidentally resurrect a tombstoned agent.

Reconstruction order (CEO 2026-05-26 lock)
------------------------------------------

On every process start :meth:`_reconstruct_from_disk` walks the four
steps in this exact order:

1. ``state/sandbox/agent_state.json`` — restore weights + phase +
   breath + bankroll + last_tick. Missing file ⇒ cold start (use
   the loop's initial-state defaults).
2. ``state/sandbox/open_bets.jsonl`` — fold left-to-right, keep the
   LATEST row per ``bet_id``, retain only ``status == "open"``. The
   in-memory ``_open_bet_ids`` set is rebuilt from this fold.
3. ``state/sandbox/decisions.jsonl`` — read the tail row's ``tick``
   field; resume the in-memory counter at ``last_tick + 1``. Empty
   file ⇒ start at 0.
4. ``chain_adapter.read_breath()`` — overrides the disk breath value
   so the chain remains the source of truth. The on-disk snapshot's
   breath stays for dashboard read continuity (the dashboard re-reads
   ``agent_state.json`` on a cold refresh).

Restart scenarios validated by tests
------------------------------------

Per the CEO Day 3 V-gate, two restart scenarios MUST pass before the
sprint can land:

(a) **Mid-run kill**: 5 decision ticks → SIGKILL → reconstruct → the
    rehydrated weights are *byte-for-byte equal* to the pre-kill
    snapshot, the tick counter resumes at ``last_tick + 1``, and the
    open-bets count is unchanged.

(b) **Settlement-during-downtime**: place 1 bet → SIGKILL → fast-
    forward :class:`MockGammaAPI` so the bet's market resolves while
    the loop is down → restart → one poll cycle → exactly ONE new
    ``settled_bets.jsonl`` line, ``weight_updater.update`` called
    exactly once with ``phase=PHASE_2_EXTENDED``, and ``open_bets.jsonl``
    grows by one row carrying ``status="settled"`` for that bet_id.

Both scenarios live in :mod:`tests.agent.runtime.test_sandbox_restart`.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import random
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from agent.core.state import (
    Action,
    ActionKind,
    AgentState,
    Phase,
    Side,
    TickPayload,
    Vitals,
    Weights,
)
from agent.data._realtime_buffer import Clock, UtcClock
from agent.data.polymarket_sandbox_executor import Executor
from agent.data.sandbox_state import (
    AgentStateSnapshot,
    DecisionRecord,
    SandboxStateWriter,
    iter_jsonl,
)
from agent.engines._performance_window import (
    fold_pnl_from_settled,
    fold_recent_reflections_from_jsonl,
    fold_weight_trajectory_from_jsonl,
)
from agent.engines._strategy_proposal_schema import PROPOSAL_STATUS_PENDING
from agent.engines.base import Signal
from agent.engines.decision import DecisionEngine
from agent.engines.reflection import (
    REFLECTION_WEIGHT_KEYS,
    ReflectionEngine,
    ReflectionRecord,
    SandboxReflectionRecord,
)
from agent.engines.strategy_advisor import (
    PerformanceWindow,
    StrategyAdvisor,
    StrategyProposal,
)
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.llm._smoke import PER_CALL_USD_EST
from agent.llm.cost_guard import CostExhaustedError, CostGuard, L3CostGuard
from agent.llm.gemini_client import GeminiClient
from agent.runtime.agent_runner import (
    AgentRunner as RuntimeAgentRunner,
)
from agent.runtime.agent_runner import (
    WeightDelta,
)
from agent.runtime.phase2_launch import (
    DEFAULT_PHASE2_BANKROLL_USD,
    DEFAULT_PHASE2_BREATH,
    PHASE2_DEFAULT_ALPHA,
    PHASE2_DEFAULT_BETA,
    PHASE2_DEFAULT_RHO,
    PHASE2_DEFAULT_W_R,
    PHASE2_DEFAULT_W_S,
    Phase2LaunchOrchestrator,
    _assert_signal_coverage,
)
from agent.runtime.sandbox_settlement_poller import (
    SandboxSettlementPoller,
    SettlementClient,
    Sleeper,
    StateHook,
    WeightUpdater,
    _real_sleep,
)
from agent.runtime.tribute import (
    TRIBUTE_MIN_USD,
    TributePolicy,
    tribute_success_probability,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Locked constants — touch only via a CEO-plan amendment.
# --------------------------------------------------------------------------- #


class WeightUpdaterPhase(StrEnum):
    """Settlement-time phase label passed to the sandbox WeightUpdater Protocol.

    DISTINCT from :class:`agent.core.state.Phase` (the on-chain phase
    enum). ``PHASE_2_EXTENDED`` is the sandbox-extended Phase 2 regime
    where β₁ is unfrozen and the settlement-time weight updater learns
    from realised outcomes per TECHNICAL_PLAN §4.2.

    The string values match the canonical labels the settlement
    :class:`WeightUpdater` Protocol expects (see
    :data:`agent.runtime.sandbox_settlement_poller.SANDBOX_PHASE_LABEL`).
    StrEnum membership is what lets the loop hand the enum to the
    string-typed Protocol parameter without a manual ``.value`` lookup
    (StrEnum instances ARE strings at runtime).
    """

    PHASE_2_EXTENDED = "PHASE_2_EXTENDED"


DEFAULT_DECISION_CADENCE: Final[timedelta] = timedelta(minutes=60)
"""Sandbox extended Phase 2 decision cadence — 60 min per CEO 2026-05-26
Day 3 plan. WIDER than the PRD §6.4 default of 45 min; the +15 min trade
reduces settlement-poll churn over multi-day runs without compromising
the "silence does not exist" invariant (every cadence still emits a
BET-or-NO_BET decision). The PRD constant is preserved at the
module level in :mod:`agent.runtime.phase2_launch`; this default is a
sandbox-loop constructor override, NOT a module-level overwrite of the
PRD spec."""


SETTLEMENT_POLL_CADENCE: Final[timedelta] = timedelta(minutes=15)
"""Per-tick settlement poll cadence inherited from T-B-019's locked
15-min interval. The loop calls :meth:`SandboxSettlementPoller.tick` at
the START of every decision tick (so settlement-time weight updates
land BEFORE the next decision uses the same weights), independent of
the wall-clock cadence — for sandbox the 60-min decision cadence
implicitly absorbs the 15-min poll guarantee 4× over."""


STORM_HALF_LIFE_HOURS: Final[float] = 48.0
"""A9 storm percept: wall-clock decay half-life (world parameter, disclosed).

The storm EMA state halves every 48 h of WORLD time regardless of how many
event-driven ticks elapse — the backtest schedule has no regular cadence, so
any per-tick decay would make the percept stop-density-dependent.
"""

SANDBOX_FORCE_TERMINAL_ENV_VAR: Final[str] = "SANDBOX_FORCE_TERMINAL"
"""Environment-variable name for the T-B-021 forced-terminal hook.

CEO sprint_8 Day 5 V-gate (D-2026-05-26-PLAN-003) mandates a controlled
``BREATH → 0`` path so the kill + Tombstone mint mechanic can be proved
E2E in test mode without waiting for natural death. The semantics are
locked:

* The loop reads ``os.environ[SANDBOX_FORCE_TERMINAL_ENV_VAR]`` exactly
  once, at :meth:`SandboxPhase2Loop.run` entry. The check is
  ``value == "1"``; any other value (including ``"true"``, ``"yes"``,
  ``"on"``) is intentionally NOT honoured — the gate is a single canonical
  string to keep the testnet runbook unambiguous.
* When the flag is set, the *first* subsequent :meth:`_tick` drives the
  chain-side BREATH balance to 0 via the existing
  ``chain_adapter.update_breath_from_pnl`` test seam (negate the current
  value), then proceeds with the normal tick body. The
  ``chain_adapter.read_breath`` call later in the same tick observes the
  zero balance, the decision routes to NO_BET (sizing constraints clamp
  to 0), and step 8's ``breath <= 0`` check fires the death path.
* The flag is one-shot per ``run()`` invocation — the loop self-clears
  the pending bit after the first tick. A second tick in the same run
  does NOT re-drive breath to 0; that's the operator's job (re-export
  the env var + restart).

Production sandbox runs MUST NOT set this variable. It exists to satisfy
the CEO Day 5 V-gate (forced-terminal E2E proof) on a dedicated testnet
agent_id; an unintended set on the long-running sandbox would
mid-air-kill the agent without warning."""


DEFAULT_REFLECTION_TICK_INTERVAL: Final[int] = 10
"""Default reflection cadence — every Nth tick fires a reflection.

Locked by T-B-024 brief: ``N=10``. PRD §4.6 / TECHNICAL_PLAN §4.4 cite
the same number ("每 N=10 ticks 或权重剧变时"). Env override at
construction time via :data:`REFLECTION_TICK_INTERVAL_ENV_VAR` keeps the
constant truly configurable without poking module state.
"""


DEFAULT_REFLECTION_WEIGHT_DELTA_THRESHOLD: Final[float] = 0.05
"""Default reflection trigger threshold on max |Δw|.

Locked by T-B-024 brief: 0.05. Computed across the 6 canonical fusion
weights (:data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS`) — the
non-derived parameters per PRD §4.1. When ``max(abs(w[i] -
last_reflection_weights[i]))`` exceeds this threshold the loop fires a
reflection with ``trigger="weight_delta"`` and resets both the tick
counter window AND the weight baseline (whichever-first semantics).
"""


REFLECTION_TICK_INTERVAL_ENV_VAR: Final[str] = "REFLECTION_TICK_INTERVAL"
"""Env-var name that overrides :data:`DEFAULT_REFLECTION_TICK_INTERVAL`.

Operators can widen the cadence on a long sandbox run without a code
change. Construction-time read (NOT per-tick) so changing the env var
mid-run requires a process restart — same posture as
:data:`SANDBOX_FORCE_TERMINAL_ENV_VAR`.
"""


REFLECTION_WEIGHT_DELTA_THRESHOLD_ENV_VAR: Final[str] = (
    "REFLECTION_WEIGHT_DELTA_THRESHOLD"
)
"""Env-var name that overrides :data:`DEFAULT_REFLECTION_WEIGHT_DELTA_THRESHOLD`."""


DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL: Final[int] = 100
"""L3 advisor cadence — every Nth tick a fresh advisor review fires.

Locked by T-B-025 brief: ``M=100``. PRD §4.6 cites "L3 — agent 看自己
一段时间表现, 提议策略修改" without a specific N; the brief picks 100 as
the sprint_9 scaffold default (roughly 100 hours of decision history at
the 60-min sandbox cadence — enough for meaningful regime detection).
The constant is the constructor default; tests inject shorter intervals
to keep the scaffold exercised in seconds rather than days."""


DEFAULT_STRATEGY_ADVISOR_STABILITY_WINDOW: Final[int] = 20
"""L3 advisor stability-trigger window — 20 consecutive low-Δw ticks fire.

Locked by T-B-025 brief: "max(abs(w[i] - w_at_last_advice[i])) < 0.001
for the last 20 consecutive ticks". The advisor fires whenever the
weights have stopped moving — interpreted as "the L0 SGD has converged
into a local basin; time for L3 to ask whether THIS basin is the right
one". 20 was chosen because at the 60-min cadence that's a full day's
worth of stable signals."""


DEFAULT_STRATEGY_ADVISOR_STABILITY_THRESHOLD: Final[float] = 1e-3
"""L3 advisor stability-trigger threshold — max |Δw[i]| below this counts as 'stable'.

Locked by T-B-025 brief: ``< 0.001``. Computed across the 6 canonical
fusion weights (:data:`REFLECTION_WEIGHT_KEYS`) the same way L2's
weight_delta trigger uses; the difference is the threshold direction —
L2 fires on LARGE Δw (regime shift); L3 fires on SUSTAINED SMALL Δw
(convergence into a basin)."""


REFLECTION_PER_CALL_USD_EST: Final[float] = PER_CALL_USD_EST
"""Per-reflection USD cost estimate.

Aliased to :data:`agent.llm._smoke.PER_CALL_USD_EST` so a sprint-level
re-estimate of the gemini-3.1-flash-lite per-call cost lands once and
flows through both L1 sentiment + L2 reflection accounting against the
shared :class:`CostGuard`. The reflection prompt for tennis-market
context is roughly the same token budget as the sentiment prompt
(~150 in / ~80 out), so the same constant is the right shape.
"""


REFLECTION_PNL_WINDOW: Final[int] = 50
"""Number of tail settlements summed for :attr:`SandboxReflectionRecord.recent_pnl_window`.

Locked by T-B-024 brief: "last 50 settled bets net P&L". The dashboard's
"what happened recently" panel renders ≤ 50 entries; using the same
bound here keeps the reflection narrative aligned with what the operator
sees on screen.
"""


REAL_REFLECTION_ENV_VAR: Final[str] = "GENESIS_REAL_REFLECTION"
"""Phase B / B1 (codex H4) — env seam that flips the reflection-informed
advisor window ON.

When the loop is constructed WITHOUT an explicit
``populate_reflection_window`` argument it reads this env var; the exact
value ``"1"`` flips the seam on (matching the established
``GENESIS_REAL_SIGNALS`` / ``GENESIS_REAL_STRATEGY_ADVISOR`` /
``GENESIS_REAL_LEARNING`` convention). Default OFF leaves the advisor's
:class:`PerformanceWindow` history fields (``recent_reflections`` /
``recent_pnl`` / ``weight_trajectory``) empty — byte-unchanged advisor
input, so the existing Plan-2 tests + frozen-config smoke stay green. The
flag does NOT itself wire the real :class:`ReflectionEngine` or the real
:class:`StrategyAdvisorImpl` — that combined wiring is the B2 task; B1 is
purely the window-population seam.
"""


DEFAULT_AGENT_ID: Final[str] = "genesis_v1"
"""Agent identifier passed to the chain adapter at death.

Matches :meth:`SandboxPhase2Loop.to_agent_state` (which uses the same
literal) — the chain adapter needs the identifier so the Tombstone NFT
mint can bind the on-chain memorial to the canonical agent record. The
constant is :data:`Final` so a downstream sprint-9 multi-agent push can
override at construction time WITHOUT silently mutating the default for
every collaborator already constructed."""


DEFAULT_MEMORY_BANK_CID_PLACEHOLDER: Final[str] = "sandbox://no-ipfs-pin"
"""Placeholder ``memory_bank_cid`` for sandbox runs that do not pin.

The Tombstone NFT metadata field is REQUIRED by PRD §5.1 (line 249-275),
but a sandbox run without a Pinata key configured cannot produce a real
CID. The placeholder preserves the chain-side schema (non-empty string)
and is visible in the Tombstone metadata as proof of provenance — the
dashboard's Death Watch surfaces this verbatim so an operator sees the
degraded state honestly (mirrors the PRD §5.1 fail-safe principle).
Production runs override at construction time with a real CID returned
by :class:`agent.llm.ipfs_pinner.IPFSPinner`."""


def _default_last_words_template(*, last_tick: int, bankroll_usd: float) -> str:
    """Deterministic last-words template used when no LLM provider is wired.

    PRD §5.1.B locks Last Words as a *one-shot* terminal reflection;
    production wires :class:`agent.llm.prompts.last_words.LastWordsService`
    so the climax of the Demo §9 4:30-5:00 moment is LLM-driven. The
    sandbox extended Phase 2 loop, however, has to survive a degraded
    LLM endpoint (PRD §5.1 fail-safe — "the typewriter ALWAYS plays").
    This deterministic template is the fail-safe payload — never empty,
    no PII, no LLM call, and varies per kill site (``last_tick`` +
    ``bankroll_usd`` produce distinguishable strings across runs)."""
    return (
        f"At tick {last_tick} the breath ran out. "
        f"Bankroll at terminal: ${bankroll_usd:.2f}. "
        "What I learned will outlive me — the weights I leave behind are "
        "the lesson."
    )


# --------------------------------------------------------------------------- #
# Injected Protocols — every external dependency is one of these.
# --------------------------------------------------------------------------- #


class SandboxLoopChainAdapter(Protocol):
    """Broader chain surface the multi-day loop needs.

    Superset of the T-B-019 :class:`agent.runtime.sandbox_settlement_poller.ChainAdapter`
    Protocol (which is settlement-only). The loop additionally needs:

    * :meth:`read_breath` — for the reconstruction step 4 "chain is
      source of truth" refresh.
    * :meth:`kill_and_mint_tombstone` — for the PRD §6.9 death path.

    The poller's narrower Protocol is structurally satisfied by anything
    that implements :meth:`update_breath_from_pnl`, so a single concrete
    chain adapter implementation in the runtime wiring satisfies both
    Protocols without subclassing.

    ``kill_and_mint_tombstone`` (T-B-021 forced-terminal hook expansion)
    carries the four PRD §5.1 (line 249-275) Tombstone metadata fields
    the on-chain mint requires:

    * ``agent_id``           — canonical identifier the Tombstone NFT
      binds the memorial to.
    * ``final_weights_hash`` — SHA-256 (hex, ``0x``-prefixed) of the
      :class:`agent.core.state.Weights` JSON at death. Lets a future
      forensic reader reconstruct provenance from the on-chain tombstone
      back to the off-chain weights snapshot.
    * ``memory_bank_cid``    — IPFS CID for the pinned reflection
      corpus. Sandbox runs without a Pinata key route to
      :data:`DEFAULT_MEMORY_BANK_CID_PLACEHOLDER` so the field is
      non-empty by construction.
    * ``last_words``         — the one-shot terminal reflection string.
      Production wiring pulls this from
      :class:`agent.llm.prompts.last_words.LastWordsService`; sandbox
      uses :func:`_default_last_words_template`.

    The on-chain Tombstone NFT ABI (``.dev/contracts/tombstone_nft_abi.v0.2.0.json``)
    is unchanged — this Protocol carries the metadata across the Python
    boundary; the concrete chain adapter is responsible for the
    ABI-level encoding.
    """

    async def update_breath_from_pnl(self, pnl_usd: float) -> None: ...

    async def read_breath(self) -> float: ...

    async def kill_and_mint_tombstone(
        self,
        *,
        agent_id: str,
        bankroll_usd: float,
        last_tick: int,
        final_weights_hash: str,
        memory_bank_cid: str,
        last_words: str,
    ) -> DeathReceipt: ...


class TickInputSource(Protocol):
    """Per-tick: produce (market_id, signals, price, liquidity_cap).

    The loop reaches out via this Protocol every decision tick to get:

    * which market to evaluate (``market_id``);
    * the 5 engine signals (``signals``);
    * the implied price on the chosen side (``price`` ∈ [0, 1]);
    * the per-market liquidity cap (``liquidity_cap_usd``).

    Production wiring: an adapter that ranks live tennis markets via
    gamma-api + runs the 5 engines in parallel (mirrors the sprint_7
    dry-run topology). Tests inject a deterministic fake that scripts
    inputs per tick.

    Returns ``None`` if NO market is eligible this tick — the loop
    emits a NO_BET with ``no_bet_reason="no_eligible_market"`` and
    still consumes BREATH (PRD §6 — NO_BET is NOT a free skip).
    """

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None: ...


@dataclass(frozen=True)
class TickInputs:
    """Bundle of one tick's decision inputs — see :class:`TickInputSource`."""

    market_id: str
    signals: dict[str, Signal]
    price: float
    liquidity_cap_usd: float
    # B′ cross-market scalar (Task 5). Defaulted so existing TickInputs
    # constructors in capture_money_shot.py / replay_runner.py / server/main.py
    # keep working unchanged (they omit it → 0.0).
    cross_market_signal: float = 0.0
    # V1.2 LIVE execution-cost inputs (plan-loop 2026-06-17): the cost
    # components a LIVE TickInputSource reads off the book, so the loop can
    # thread them onto the BetRecord for cost-NET settlement PnL + the
    # fail-closed LIVE-settlement guard (assert_cost_fields_present). All
    # Optional/None so the replay / idle / backtest TickInputs constructors
    # (which omit them) stay byte-identical — only the LIVE source sets them.
    # These are SIZE-INDEPENDENT (the live source cannot know the Kelly stake,
    # decided downstream): ``fill_price`` = the entry mid, ``fee_bps`` =
    # fee RATE, ``half_spread_frac`` = half the bid/ask spread as a FRACTION of
    # notional. ``_tick`` converts ``half_spread_frac`` → the dollar
    # ``spread_paid_usd`` once the stake is sized. ``liquidity_cap_usd`` above is
    # the fourth cost stamp (always present, used for decision-time sizing too).
    fill_price: float | None = None
    fee_bps: float | None = None
    half_spread_frac: float | None = None


# --------------------------------------------------------------------------- #
# Result types — public so tests can assert on them.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeathReceipt:
    """Receipt returned by :meth:`SandboxLoopChainAdapter.kill_and_mint_tombstone`.

    Carries the kill-tx + the Tombstone NFT mint-tx + the token id so
    the run summary surfaces every observable side effect of the death
    path for the operator runbook.
    """

    kill_tx_hash: str
    tombstone_token_id: str
    tombstone_tx_hash: str


@dataclass(frozen=True)
class TickResult:
    """Single decision-tick outcome.

    ``poll_settled`` / ``poll_pending`` carry the settlement-poll
    accounting for the SAME tick — the loop calls
    :meth:`SandboxSettlementPoller.tick` at the START of each decision
    tick so settlement-time weight updates land BEFORE the decision
    consumes the freshly-updated weights.

    ``died`` is True only on the tick where breath went to zero (which
    triggers :meth:`SandboxPhase2Loop._die`); subsequent ticks are
    never executed.
    """

    tick: int
    action: Action
    breath_after: float
    bankroll_after: float
    poll_settled: int
    poll_pending: int
    died: bool


@dataclass(frozen=True)
class RunSummary:
    """Aggregate output of :meth:`SandboxPhase2Loop.run`.

    Covers the entire run window (one ``run()`` invocation). On a
    multi-day run the final tick's snapshot + the death receipt (if
    any) are persisted to disk independently — this summary is the
    *in-memory* digest for the caller.
    """

    ticks_completed: int
    bets_placed: int
    no_bets_emitted: int
    settlements_processed: int
    died: bool
    death_receipt: DeathReceipt | None
    final_breath: float
    final_bankroll_usd: float


@dataclass(frozen=True)
class ReconstructedState:
    """Output of :meth:`SandboxPhase2Loop._reconstruct_from_disk`.

    Carries every scalar / list the loop needs to resume mid-run from
    a previous process's persisted state. ``last_tick == -1`` and
    ``open_bet_ids == []`` indicate a true cold start (no disk
    artefacts found).

    ``chain_breath`` is the value read from the chain in step 4 of the
    reconstruction — the source-of-truth override that supersedes the
    disk snapshot's ``breath`` field. Surfaced separately so tests can
    assert the override actually happened.

    ``disk_breath`` is the breath value the snapshot file recorded;
    captured so the loop can log a divergence between disk and chain
    (a non-fatal "we may have missed a chain-side refund" alert) without
    masking the chain-source-of-truth lock.
    """

    last_tick: int
    weights: Weights
    phase: Phase
    disk_breath: float
    chain_breath: float
    bankroll_usd: float
    open_bet_ids: list[str]
    desperate: bool
    cold_start: bool


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #


def _phase2_default_weights() -> Weights:
    """Construct the Phase 2 entry weights snapshot.

    Pulled out of :class:`SandboxPhase2Loop`'s constructor so a caller
    that wants to override the cold-start weights can pass a fresh
    :class:`Weights` without poking module-level constants. Mirrors the
    PHASE2_DEFAULT_* values in :mod:`agent.runtime.phase2_launch` so
    cold-start parity is byte-for-byte with the launch orchestrator's
    boot tick.
    """
    return Weights(
        w_r=PHASE2_DEFAULT_W_R,
        w_s=PHASE2_DEFAULT_W_S,
        alpha=list(PHASE2_DEFAULT_ALPHA),
        beta=list(PHASE2_DEFAULT_BETA),
        rho=PHASE2_DEFAULT_RHO,
    )


# --------------------------------------------------------------------------- #
# The loop itself.
# --------------------------------------------------------------------------- #


class SandboxPhase2Loop:
    """Multi-day extended-Phase-2 sandbox runtime composite.

    Wraps :class:`Phase2LaunchOrchestrator` via composition (NEVER
    subclassed — the brief locks this) and stitches the T-B-018
    executor + T-B-019 settlement poller into a single driver that:

    1. Reconstructs in-memory state from disk + chain on every start
       (4-step order locked by the CEO 2026-05-26 plan).
    2. Drives an asyncio decision-cadence loop until either the ``until``
       wall-clock deadline elapses, breath hits zero (PRD §6.9 Death),
       or the caller's runtime cancels the task.
    3. Per tick: poller.tick() → engine signals → fusion → executor
       (BET) or NO_BET, then append a decision row + snapshot the
       agent state.

    Construction is verbose (lots of injected Protocols) on purpose —
    the brief's hard requirement is that the composite owns NO direct
    network / chain / LLM logic of its own; every external dependency
    is a Protocol the runtime wiring satisfies and tests inject fakes
    against.

    Parameters
    ----------

    base
        The composed :class:`Phase2LaunchOrchestrator`. Held verbatim
        on ``self.base``; the loop calls only its public surface
        (currently :attr:`Phase2LaunchOrchestrator.emitter`) and never
        the private boot machinery.

    state_dir
        Filesystem root for the sandbox JSONL streams + snapshot. In
        production: ``state/sandbox/`` (the
        :data:`agent.data.sandbox_state.SANDBOX_DIR` default). In tests:
        a per-test ``tmp_path``.

    weight_updater_phase
        Phase label handed to every settlement-time
        ``weight_updater.update(phase=...)`` call. Default
        ``PHASE_2_EXTENDED`` — the only currently-supported sandbox
        regime. Constructor argument so a downstream phase-bump
        (PRD §3 Phase 3 Master) is a single-line override, NOT a
        constant overwrite.

    executor
        :class:`agent.data.polymarket_sandbox_executor.Executor` —
        records-only sandbox executor (T-B-018) in production; a fake
        in tests.

    settlement_client
        :class:`agent.runtime.sandbox_settlement_poller.SettlementClient`
        — gamma-api wrapper (T-B-019) in production; a scripted fake or
        :class:`tests.agent.runtime.fixtures.mock_gamma_api.MockGammaAPI`
        in tests.

    weight_updater
        Settlement-time gradient feedback channel (see
        :class:`agent.runtime.sandbox_settlement_poller.WeightUpdater`).
        Called by the poller with ``phase=weight_updater_phase`` for
        every settlement.

    chain_adapter
        Broader chain surface (:class:`SandboxLoopChainAdapter`)
        covering settlement BREATH delta + reconstruction
        ``read_breath`` + death-path ``kill_and_mint_tombstone``.

    tick_inputs
        Per-tick decision input source (:class:`TickInputSource`). The
        loop calls ``inputs_for(asof_ts=..., tick=...)`` once per tick.

    state_hook
        Operator-visibility hook (v34 F8 contract).

    decision_engine
        Fusion + sizing math. Defaults to a fresh
        :class:`DecisionEngine` so the loop does not depend on the
        wrapped orchestrator's private ``_decision`` attribute.

    state_writer
        :class:`SandboxStateWriter` rooted at ``state_dir``. Constructed
        for the caller when None; the same instance is shared with the
        poller AND the executor per the single-writer invariant. Tests
        pass in an explicit instance bound to a ``tmp_path`` to keep
        the suite hermetic.

    clock / sleeper
        Injected for deterministic tests. Defaults: :class:`UtcClock`
        + :func:`asyncio.sleep` wrapper.

    decision_cadence / poll_cadence
        Wall-clock between consecutive ticks / polls. Defaults match
        the sandbox extended Phase 2 plan (60 min decisions / 15 min
        settlement poll). Tests pass ``timedelta(0)`` to drive
        synchronous tick advancement.

    initial_breath / initial_bankroll_usd / initial_weights / initial_phase
        Cold-start defaults; ignored when the disk reconstruction finds
        a non-empty snapshot.
    """

    def __init__(
        self,
        *,
        base: Phase2LaunchOrchestrator,
        state_dir: Path,
        weight_updater_phase: WeightUpdaterPhase = WeightUpdaterPhase.PHASE_2_EXTENDED,
        # Required injected dependencies — all behind Protocols so the
        # runtime wiring picks production vs test impls without a flag.
        executor: Executor,
        settlement_client: SettlementClient,
        weight_updater: WeightUpdater,
        chain_adapter: SandboxLoopChainAdapter,
        tick_inputs: TickInputSource,
        state_hook: StateHook,
        # Optional shared collaborators
        decision_engine: DecisionEngine | None = None,
        state_writer: SandboxStateWriter | None = None,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
        decision_cadence: timedelta = DEFAULT_DECISION_CADENCE,
        poll_cadence: timedelta = SETTLEMENT_POLL_CADENCE,
        initial_breath: float = DEFAULT_PHASE2_BREATH,
        initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
        initial_weights: Weights | None = None,
        initial_phase: Phase = Phase.PHASE_2_APPRENTICE,
        # T-B-021 — Tombstone NFT metadata fields + forced-terminal hook.
        agent_id: str = DEFAULT_AGENT_ID,
        memory_bank_cid: str = DEFAULT_MEMORY_BANK_CID_PLACEHOLDER,
        last_words: str | None = None,
        force_terminal_env_var: str = SANDBOX_FORCE_TERMINAL_ENV_VAR,
        env: dict[str, str] | None = None,
        # T-B-024 L2 reflection wire — all four params optional so the
        # sprint_8 restart tests (no LLM wired) keep passing. When
        # ``reflection_engine`` is None the trigger pathway is a no-op.
        reflection_engine: ReflectionEngine | None = None,
        cost_guard: CostGuard | None = None,
        reflection_tick_interval: int | None = None,
        reflection_weight_delta_threshold: float | None = None,
        # Phase B / B1 (codex H4) — reflect→learn→optimize closure. When
        # ON the advisor-window call site folds the recent
        # ``reflections.jsonl`` narratives + the recent settled-bet PnL +
        # the weight trajectory into the :class:`PerformanceWindow` it
        # hands to :meth:`StrategyAdvisor.review_window`, so the proposals
        # are reflection-INFORMED. Explicit ctor arg wins; ``None`` falls
        # back to the ``GENESIS_REAL_REFLECTION`` env seam (exact ``"1"``).
        # Default OFF leaves the advisor input byte-unchanged (the history
        # fields stay empty), so the existing Plan-2 tests + frozen-config
        # smoke are unaffected. The advisor is NOT called from
        # ``_fire_reflection`` — proposals still flow through the existing
        # L1 approval queue on the normal advisor cadence.
        populate_reflection_window: bool | None = None,
        # T-B-025 L3 meta-optimizer wire (sprint_9) + T-B-030 default
        # bump (sprint_10): when no advisor is passed the loop wires
        # the real :class:`StrategyAdvisorImpl` against a lazy
        # :class:`GeminiClient` + the dedicated L3 cost guard. The
        # GeminiClient defers its first SDK call until ``structured_call``
        # is actually invoked, so a process that boots without
        # ``GEMINI_API_KEY`` set does NOT crash at constructor time
        # — only on the first trigger fire (which the advisor's fail-soft
        # wrapping converts to an empty proposal list + WARNING log).
        # Sprint_9 callers that explicitly want the NoOp scaffold can
        # still pass ``strategy_advisor=NoOpStrategyAdvisor()`` — the
        # back-compat test covers that path.
        strategy_advisor: StrategyAdvisor | None = None,
        strategy_advisor_tick_interval: int = (
            DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL
        ),
        strategy_advisor_stability_window: int = (
            DEFAULT_STRATEGY_ADVISOR_STABILITY_WINDOW
        ),
        strategy_advisor_stability_threshold: float = (
            DEFAULT_STRATEGY_ADVISOR_STABILITY_THRESHOLD
        ),
        # T-B-032 — operator-approval weight-delta seam (T-B-031). The
        # FastAPI ``/api/proposals/{id}/approve`` route enqueues approved
        # ``kind == "weight_delta"`` payloads on this instance; the loop
        # drains the queue at the START of every decision tick (BEFORE
        # the settlement poll so the freshly applied weights are visible
        # to both settlement-time gradient updates AND the same tick's
        # decision fusion). ``None`` → a fresh queue is constructed so
        # standalone runs (no FastAPI sharing) keep working unchanged.
        # Production wiring passes the SAME instance that
        # :func:`agent.server.main.create_app` holds on ``ServerState``
        # so producer-consumer hand-off is single-source.
        runtime_agent: RuntimeAgentRunner | None = None,
        # Survival-backtest liquidity-realism cap: optional per-bet PROFIT
        # ceiling (USD) forwarded to the settlement poller's ``_compute_pnl``.
        # ``None`` (default) = locked formulas, byte-unchanged — the LIVE
        # runtime never sets this; only the survival export turns it on.
        max_bet_pnl_usd: float | None = None,
        # Realism rule #3: side-correct pricing forwarded to the poller —
        # winners pay the taken leg's effective cost (NO pays 1 - price).
        # ``False`` (default) = locked legacy formulas, byte-unchanged; only
        # the survival export turns it on.
        side_correct_pricing: bool = False,
        # V1.4/V1.4b fail-closed cost guard (Codex Phase-3 HIGH) forwarded to the
        # poller — a RESOLVED bet missing any execution-cost stamp RAISES at
        # settlement. ``False`` (default) = legacy/replay tolerates None; the LIVE
        # mode (V1.3) sets it True.
        require_cost_fields: bool = False,
        # Value-betting mode (realism v3): when True the decide() call
        # receives ``price=inputs.price`` so the engine runs the
        # market-prior EV mode. ``False`` (default) = legacy signal
        # betting, byte-unchanged; only the survival export turns it on.
        value_betting: bool = False,
        # Bet-level side-aware floor: a BET whose EFFECTIVE side price
        # (yes price for YES, 1-price for NO) is below this floor is
        # converted to a NO_BET record BEFORE place_order — regardless of
        # value mode, so a legacy-mode run can never place sub-floor bets
        # that only the export invariant would catch, late. ``None``
        # (default) disables the gate (live-runtime contract).
        effective_entry_price_floor: float | None = None,
        tribute_policy: TributePolicy | None = None,
        tribute_rng: random.Random | None = None,
        tribute_breath: float = 35.0,
        # A9 storm percept (backtest-only; plan 2026-06-13): a single EMA
        # over the tick-level REAL breath delta with wall-clock half-life
        # decay, threaded into ``decide(storm=)``. ``False`` (default) =
        # byte-identical — no state, no decide kwarg, no bet stamps.
        storm_enabled: bool = False,
        storm_tau: float = 0.05,
        storm_scale: float | None = None,
        # A10 divine tithe (backtest-only; the gods charge periodic rent for
        # existence, extending the A7 deathbed tribute). Every
        # ``tithe_every`` markets the agent must pay ``tithe_amount_usd``
        # from bankroll; if it cannot afford it, the gods take
        # ``tithe_breath_cost`` breath instead (which can kill — a do-nothing
        # agent stops earning, runs out of cash, and bleeds out). ``False``
        # (default) = byte-identical; the live runtime never enables it.
        divine_tithe: bool = False,
        tithe_every: int = 20,
        tithe_amount_usd: float = 20.0,
        tithe_breath_cost: float = 5.0,
        # Living Stage P1 — when True, _tick stamps odds + signal_scores onto
        # the DecisionRecord. Default False ⇒ those fields stay None/{} and are
        # omitted on disk, keeping decisions.jsonl byte-identical (the prod
        # factory sets this = the SANDBOX_DIVINE_ECONOMY flag).
        record_living_stage_fields: bool = False,
        # Phase 2 — which incarnation this loop instance is (stamped into every
        # snapshot so the dashboard + manifest track the lineage). Default 0 =
        # single-life path (byte-identical: AgentStateSnapshot.incarnation_number
        # already defaults to 0).
        incarnation_number: int = 0,
    ) -> None:
        # Composition — NOT inheritance.
        self.base: Phase2LaunchOrchestrator = base
        self.state_dir: Path = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.weight_updater_phase: WeightUpdaterPhase = weight_updater_phase

        self._executor: Executor = executor
        self._settlement_client: SettlementClient = settlement_client
        self._weight_updater: WeightUpdater = weight_updater
        self._chain_adapter: SandboxLoopChainAdapter = chain_adapter
        self._tick_inputs: TickInputSource = tick_inputs
        self._state_hook: StateHook = state_hook

        self._decision: DecisionEngine = (
            decision_engine if decision_engine is not None else DecisionEngine()
        )
        self._value_betting: bool = value_betting
        self._effective_entry_price_floor: float | None = (
            effective_entry_price_floor
        )
        # A7 tribute (world rule; backtest-only wiring - live runtime
        # never passes a policy, keeping the death check byte-identical).
        if tribute_policy is not None and tribute_rng is None:
            raise RuntimeError(
                "tribute_policy requires tribute_rng (the gods' dice)"
            )
        if tribute_breath <= 0.0:
            raise RuntimeError("tribute_breath must be positive")
        self._tribute_policy: TributePolicy | None = tribute_policy
        self._tribute_rng: random.Random | None = tribute_rng
        self._tribute_breath: float = tribute_breath
        # A10 divine tithe — boundary validation.
        if divine_tithe:
            if tithe_every < 1:
                raise RuntimeError("tithe_every must be >= 1")
            if not math.isfinite(tithe_amount_usd) or tithe_amount_usd < 0.0:
                raise RuntimeError("tithe_amount_usd must be finite and >= 0")
            if (
                not math.isfinite(tithe_breath_cost)
                or tithe_breath_cost < 0.0
            ):
                raise RuntimeError("tithe_breath_cost must be finite and >= 0")
        self._divine_tithe: bool = divine_tithe
        self._tithe_every: int = tithe_every
        self._tithe_amount_usd: float = tithe_amount_usd
        self._tithe_breath_cost: float = tithe_breath_cost
        self._record_living_stage_fields: bool = record_living_stage_fields
        self._incarnation_number: int = incarnation_number
        # Counts MARKETS seen (decision ticks with a real market), not raw
        # ticks — settle-only stops do not advance the rent clock. Fires on
        # each multiple of ``tithe_every``.
        self._markets_since_start: int = 0
        # A9 storm percept — boundary validation (r2 M-5 / r4 M-2 / r9 M-4).
        if storm_enabled and not value_betting:
            raise RuntimeError(
                "storm_enabled requires value_betting — legacy mode has no "
                "min-edge gate for the storm wire to act on"
            )
        if storm_enabled and initial_breath <= 0.0:
            raise RuntimeError(
                "storm_enabled requires initial_breath > 0 (the default "
                "storm scale is initial_breath * 0.10)"
            )
        if not math.isfinite(storm_tau) or not 0.0 < storm_tau <= 1.0:
            raise RuntimeError(
                f"storm_tau must be finite in (0, 1] (got {storm_tau})"
            )
        if storm_scale is not None and (
            not math.isfinite(storm_scale) or storm_scale <= 0.0
        ):
            raise RuntimeError(
                f"storm_scale must be finite and > 0 (got {storm_scale})"
            )
        self._storm_enabled: bool = storm_enabled
        self._storm_tau: float = storm_tau
        self._storm_scale: float = (
            storm_scale if storm_scale is not None else initial_breath * 0.10
        )
        # In-memory ONLY by design — no snapshot field (restart-rejected
        # in _reconstruct_from_disk; backtest lives start in fresh dirs).
        self._storm_state: float = 0.0
        self._storm: float = 0.0
        self._last_refreshed_breath: float | None = None
        self._last_storm_ts: datetime | None = None
        self._storm_skip_next: bool = False
        self._writer: SandboxStateWriter = (
            state_writer
            if state_writer is not None
            else SandboxStateWriter(root=self.state_dir)
        )
        self._clock: Clock = clock if clock is not None else UtcClock()
        self._sleeper: Sleeper = sleeper if sleeper is not None else _real_sleep
        self._decision_cadence: timedelta = decision_cadence
        # ``poll_cadence`` is reserved for a future sprint that decouples
        # the settlement poll from the decision tick (today's
        # implementation calls poller.tick() at the top of every
        # decision tick — the 60-min decision cadence absorbs the locked
        # 15-min poll guarantee 4× over).
        self._poll_cadence: timedelta = poll_cadence

        # Cold-start defaults — superseded by disk reconstruction when present.
        self._initial_breath: float = initial_breath
        self._initial_bankroll_usd: float = initial_bankroll_usd
        self._initial_weights: Weights = (
            initial_weights if initial_weights is not None else _phase2_default_weights()
        )
        self._initial_phase: Phase = initial_phase

        # Composed poller — uses the SAME state_writer per the
        # single-writer invariant. The phase label is the StrEnum value
        # (StrEnum instances ARE strings, so no manual .value needed
        # at the type level — but be explicit for readability).
        self._poller: SandboxSettlementPoller = SandboxSettlementPoller(
            state_writer=self._writer,
            settlement_client=self._settlement_client,
            weight_updater=self._weight_updater,
            chain_adapter=self._chain_adapter,
            state_hook=self._state_hook,
            clock=self._clock,
            sleeper=self._sleeper,
            sandbox_phase=self.weight_updater_phase.value,
            max_bet_pnl_usd=max_bet_pnl_usd,
            side_correct_pricing=side_correct_pricing,
            require_cost_fields=require_cost_fields,
        )

        # Mutable in-memory state — populated by `_reconstruct_from_disk`.
        self._tick_counter: int = 0
        self._weights: Weights = self._initial_weights
        self._breath: float = self._initial_breath
        self._bankroll_usd: float = self._initial_bankroll_usd
        self._phase: Phase = self._initial_phase
        self._desperate: bool = False
        self._open_bet_ids: set[str] = set()
        self._alive: bool = True
        self._death_receipt: DeathReceipt | None = None

        # T-B-021 — Tombstone NFT metadata + forced-terminal hook.
        self._agent_id: str = agent_id
        self._memory_bank_cid: str = memory_bank_cid
        self._last_words_override: str | None = last_words
        # Resolve the V-gate env var ONCE at construction time so the loop
        # owns a deterministic snapshot (production: read from
        # ``os.environ``; tests: inject ``env={...}`` to keep the runner
        # hermetic). Storing only the resolved value — not the whole env
        # dict — keeps the loop instance compact.
        env_source = env if env is not None else os.environ
        self._force_terminal_env_value: str | None = env_source.get(
            force_terminal_env_var
        )
        self._force_terminal_env_var: str = force_terminal_env_var
        self._force_terminal_pending: bool = False

        # T-B-024 L2 reflection wire. Trigger configuration resolves
        # explicit-arg → env-var → DEFAULT_* in that order, once at
        # construction time (mid-run env changes require a restart —
        # same posture as the forced-terminal hook above). The
        # cost_guard is held even when no engine is wired so an L1
        # sentiment caller can share the same $25 monthly cap.
        self._reflection_engine: ReflectionEngine | None = reflection_engine
        self._cost_guard: CostGuard = (
            cost_guard if cost_guard is not None else CostGuard()
        )
        self._reflection_tick_interval: int = self._resolve_tick_interval(
            explicit=reflection_tick_interval,
            env_source=env_source,
        )
        self._reflection_weight_delta_threshold: float = (
            self._resolve_weight_delta_threshold(
                explicit=reflection_weight_delta_threshold,
                env_source=env_source,
            )
        )
        # ``_last_reflection_weights`` initialises to the cold-start
        # weights so a fresh boot can't fire a spurious weight_delta
        # reflection on the first tick.
        self._last_reflection_tick: int = -1
        self._last_reflection_weights: Weights = self._initial_weights

        # Phase B / B1 — resolve the reflection-informed-advisor seam ONCE
        # at construction (same env-snapshot posture as the forced-terminal
        # hook above). Explicit ctor arg wins; ``None`` reads the
        # ``GENESIS_REAL_REFLECTION`` env var (exact ``"1"`` flips it,
        # matching the established ``GENESIS_REAL_*`` flag convention).
        # Default OFF ⇒ the advisor-window fold is skipped and the history
        # fields stay empty (advisor input byte-unchanged).
        self._populate_reflection_window: bool = (
            populate_reflection_window
            if populate_reflection_window is not None
            else env_source.get(REAL_REFLECTION_ENV_VAR) == "1"
        )

        # T-B-025 L3 meta-optimizer wire + T-B-030 ring-buffer trigger.
        # Constructor argument validation first — failing fast keeps the
        # rest of the constructor body safe to assume positive integers.
        if strategy_advisor_tick_interval <= 0:
            raise ValueError(
                "strategy_advisor_tick_interval must be > 0 "
                f"(got {strategy_advisor_tick_interval})"
            )
        if strategy_advisor_stability_window <= 0:
            raise ValueError(
                "strategy_advisor_stability_window must be > 0 "
                f"(got {strategy_advisor_stability_window})"
            )
        if strategy_advisor_stability_threshold <= 0.0:
            raise ValueError(
                "strategy_advisor_stability_threshold must be > 0 "
                f"(got {strategy_advisor_stability_threshold})"
            )
        # T-B-030 production default: real Gemini-backed advisor against
        # the dedicated L3 budget (``L3_MONTHLY_BUDGET_USD`` env override
        # via :meth:`L3CostGuard.from_env`). Constructor side-effects
        # are zero — :class:`GeminiClient` defers SDK / API-key resolution
        # until first ``structured_call``, so a process that boots without
        # the env var only fails on the first L3 trigger fire (which
        # the impl's fail-soft wrapper collapses to ``[]`` + WARNING).
        # The sprint_9 :class:`NoOpStrategyAdvisor` path remains
        # available via explicit injection — tests cover both shapes.
        self._strategy_advisor: StrategyAdvisor = (
            strategy_advisor
            if strategy_advisor is not None
            else StrategyAdvisorImpl(
                llm_client=GeminiClient(),
                cost_guard=L3CostGuard.from_env(),
            )
        )
        self._strategy_advisor_tick_interval: int = strategy_advisor_tick_interval
        self._strategy_advisor_stability_window: int = (
            strategy_advisor_stability_window
        )
        self._strategy_advisor_stability_threshold: float = (
            strategy_advisor_stability_threshold
        )
        # Last tick at which the advisor was consulted. Kept for the
        # public read property + the state-hook payload; the ring-buffer
        # trigger no longer uses it for any decision.
        self._last_strategy_advisor_tick: int = -1
        # T-B-030 ring buffer — last ``strategy_advisor_stability_window``
        # weight snapshots, one per tick. ``deque(maxlen=window)`` keeps
        # the implementation O(1) per append; older entries fall off
        # automatically. On a fire (either trigger) the buffer is
        # CLEARED so the next stability fire requires a fresh window of
        # ``window`` low-Δw ticks (otherwise the buffer would always
        # carry stable history and the trigger would fire every tick
        # after the first convergence — the brief's "whichever first"
        # semantics demand reset-on-fire).
        self._weight_ring_buffer: deque[Weights] = deque(
            maxlen=strategy_advisor_stability_window,
        )
        # Pending proposals — proposal_ids that have not yet been
        # resolved (approved / rejected) by the operator. Source of
        # truth on restart is :file:`proposals.jsonl` (latest-status-wins
        # fold per :func:`_fold_pending_proposals_from_jsonl`); the
        # snapshot's ``pending_proposal_ids`` field is a fast-path
        # cache that the dashboard reads. Sprint_9 NoOp / sprint_10
        # Gemini-impl populate it identically — only the cardinality
        # changes between the two.
        self._pending_proposals: list[str] = []

        # T-B-032 — operator-approval weight-delta seam. Defaults to a
        # fresh queue so standalone runs (no FastAPI process sharing)
        # still construct cleanly; production wires the SAME instance
        # the :func:`agent.server.main.create_app` factory holds so the
        # producer-side approve handler and the consumer-side loop tick
        # share a single thread-safe channel. Drain runs at the START of
        # every :meth:`_tick` so an approval that lands between tick T
        # and tick T+1 is visible to T+1's decision (PRD §11 sprint_10
        # CEO decision 6 — "weight_delta approval is the only auto-apply
        # path; next tick must see the new weights").
        self._runtime_agent: RuntimeAgentRunner = (
            runtime_agent if runtime_agent is not None else RuntimeAgentRunner()
        )

    # ------------------------------------------------------------------ #
    # Public read surface — for tests + the operator runbook.
    # ------------------------------------------------------------------ #

    @property
    def poller(self) -> SandboxSettlementPoller:
        """Read access to the composed settlement poller.

        Restart test (b) needs to drive ``poller.tick()`` directly after
        a fast-forward of the gamma-api mock; exposing the poller here
        keeps the test from reaching into private attributes.
        """
        return self._poller

    @property
    def writer(self) -> SandboxStateWriter:
        """The shared :class:`SandboxStateWriter`.

        Tests + the operator dashboard read the JSONL paths off this
        instance; exposing it as a property avoids per-call ``state_dir``
        re-derivation and preserves the single-writer invariant.
        """
        return self._writer

    @property
    def runtime_agent(self) -> RuntimeAgentRunner:
        """Operator-approval weight-delta seam (T-B-032).

        The same instance the FastAPI ``/api/proposals/{id}/approve``
        handler enqueues on; the loop drains it once per :meth:`_tick`
        (before the settlement poll). Exposed so the e2e integration
        test can assert producer→consumer hand-off without reaching
        into private attributes.
        """
        return self._runtime_agent

    @property
    def tick_counter(self) -> int:
        """Monotonic tick counter — increments per :meth:`_tick` call."""
        return self._tick_counter

    @property
    def weights(self) -> Weights:
        """Current in-memory weights — mutated by settlement updates."""
        return self._weights

    @property
    def breath(self) -> float:
        """Current in-memory BREATH balance."""
        return self._breath

    @property
    def bankroll_usd(self) -> float:
        return self._bankroll_usd

    @property
    def open_bet_ids(self) -> frozenset[str]:
        """Snapshot of the still-open bet ids (closed/settled excluded)."""
        return frozenset(self._open_bet_ids)

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def death_receipt(self) -> DeathReceipt | None:
        return self._death_receipt

    @property
    def agent_id(self) -> str:
        """Stable agent identifier — Tombstone NFT binds the memorial to this string."""
        return self._agent_id

    @property
    def cost_guard(self) -> CostGuard:
        """Shared :class:`CostGuard` instance.

        Exposed read-only so the dashboard / operator can grep
        ``loop.cost_guard.total_usd`` directly. The L1 sentiment engine
        (T-B-023) is expected to be constructed with the SAME instance so
        the loop's $25 monthly cap aggregates BOTH layers' spending.
        """
        return self._cost_guard

    @property
    def reflection_tick_interval(self) -> int:
        """N for the tick-interval trigger (per T-B-024 brief)."""
        return self._reflection_tick_interval

    @property
    def reflection_weight_delta_threshold(self) -> float:
        """Threshold for the max-|Δw| trigger (per T-B-024 brief)."""
        return self._reflection_weight_delta_threshold

    @property
    def last_reflection_tick(self) -> int:
        """Tick number at which the most recent reflection fired (-1 if never)."""
        return self._last_reflection_tick

    @property
    def force_terminal_pending(self) -> bool:
        """True when the SANDBOX_FORCE_TERMINAL hook has armed but not yet fired.

        Read-only public surface so the forced-terminal E2E test can
        observe arming + clearing without poking the private flag.
        """
        return self._force_terminal_pending

    @property
    def strategy_advisor(self) -> StrategyAdvisor:
        """The wired :class:`StrategyAdvisor` (T-B-025).

        Public so tests can verify the swap-test fixture is the
        injected instance, and so operator tooling can introspect
        which advisor implementation is live.
        """
        return self._strategy_advisor

    @property
    def strategy_advisor_tick_interval(self) -> int:
        """M for the L3 tick-interval trigger (per T-B-025 brief)."""
        return self._strategy_advisor_tick_interval

    @property
    def strategy_advisor_stability_window(self) -> int:
        """Consecutive-stable-ticks bar for the L3 stability trigger."""
        return self._strategy_advisor_stability_window

    @property
    def strategy_advisor_stability_threshold(self) -> float:
        """Max |Δw| under which a tick counts as 'stable' for L3."""
        return self._strategy_advisor_stability_threshold

    @property
    def last_strategy_advisor_tick(self) -> int:
        """Tick at which the L3 advisor most recently fired (-1 if never)."""
        return self._last_strategy_advisor_tick

    @property
    def strategy_advisor_stability_count(self) -> int:
        """Length of the ring buffer (capped at the stability window).

        T-B-030 refactor: the sprint_9 ``_strategy_advisor_stability_count``
        counter was a manual consecutive-tick tally; the new ring-buffer
        trigger derives the same observable through ``len(buffer)``.
        Operator tooling that polled the count still gets a monotonically
        non-decreasing-until-fire integer, just sourced from the deque.
        """
        return len(self._weight_ring_buffer)

    @property
    def weight_ring_buffer_size(self) -> int:
        """T-B-030 — current ring-buffer occupancy (0..window inclusive).

        Public-read property so tests + the operator dashboard can
        observe trigger-readiness without poking the private deque.
        Equal to :attr:`strategy_advisor_stability_count` by design;
        the duplicate accessor exists for readability at the call site.
        """
        return len(self._weight_ring_buffer)

    @property
    def pending_proposals(self) -> tuple[str, ...]:
        """Snapshot of pending L3 proposal_ids (T-B-025).

        Returned as :class:`tuple` so callers can't mutate the loop's
        in-memory list. The dashboard renders one card per id; the
        operator approval/rejection flow lives in Track D and writes
        through a separate channel — the loop never removes ids from
        this list except by an explicit operator-resolution hook
        (sprint_10 follow-up).
        """
        return tuple(self._pending_proposals)

    @property
    def pending_proposal_ids(self) -> tuple[str, ...]:
        """T-B-030 canonical alias for :attr:`pending_proposals`.

        Mirrors :attr:`AgentStateSnapshot.pending_proposal_ids` so the
        loop's public read surface uses the same name as the on-disk
        snapshot. Returns a fresh :class:`tuple` view — callers cannot
        mutate the loop's queue.
        """
        return tuple(self._pending_proposals)

    # ------------------------------------------------------------------ #
    # Public driver — the multi-day extended Phase 2 loop.
    # ------------------------------------------------------------------ #

    async def run(
        self,
        *,
        until: datetime | None = None,
        max_ticks: int | None = None,
    ) -> RunSummary:
        """Drive the loop until ``until`` or BREATH==0 (death).

        Steps (in order):

        1. :meth:`_reconstruct_from_disk` — rebuild in-memory state.
        2. Sit-rep: log the reconstructed posture for the operator.
        3. While ``alive`` and ``now < until``:
           - :meth:`_tick` — one decision cycle.
           - If breath went to zero: :meth:`_die` then break.
           - Else sleep for ``decision_cadence`` (test injection: 0s).
        4. Return :class:`RunSummary`.

        Passing ``until=None`` runs forever — the operator runbook uses
        SIGTERM to stop; tests always pass a finite ``until`` AND/OR
        a finite ``max_ticks``. The ``max_ticks`` bound is the tick-
        count safety net for tests that pin ``decision_cadence=0`` and
        a non-advancing :class:`Clock` (the wall-clock check would
        otherwise loop forever).
        """
        # If a previous death sealed this instance, do not resurrect.
        # The "died-before-start" summary is the explicit refusal.
        if not self._alive:
            return RunSummary(
                ticks_completed=0,
                bets_placed=0,
                no_bets_emitted=0,
                settlements_processed=0,
                died=True,
                death_receipt=self._death_receipt,
                final_breath=self._breath,
                final_bankroll_usd=self._bankroll_usd,
            )

        # T-B-021 — forced-terminal hook (CEO Day 5 V-gate). The env
        # value was resolved ONCE at construction so re-entrant ``run()``
        # calls see the same snapshot; the first subsequent tick drives
        # chain-side BREATH to zero via the existing test seam, then
        # self-clears the pending flag. See SANDBOX_FORCE_TERMINAL_ENV_VAR.
        if self._force_terminal_env_value == "1":
            self._force_terminal_pending = True
            logger.warning(
                "sandbox_phase2_loop: SANDBOX_FORCE_TERMINAL=1 at run entry — "
                "next tick will drive BREATH to 0 and trigger kill + Tombstone mint",
            )
            self._state_hook.emit(
                kind="force_terminal_armed",
                env_var=self._force_terminal_env_var,
                pre_terminal_breath=self._breath,
            )

        await self._reconstruct_from_disk()

        ticks_completed = 0
        bets_placed = 0
        no_bets = 0
        settlements_total = 0

        while self._alive:
            now = self._clock.now()
            if until is not None and now >= until:
                break
            if max_ticks is not None and ticks_completed >= max_ticks:
                break

            tick_result = await self._tick()
            ticks_completed += 1
            settlements_total += tick_result.poll_settled
            if tick_result.action.kind == ActionKind.BET:
                bets_placed += 1
            else:
                no_bets += 1
            if tick_result.died:
                # `_die` has already flipped self._alive False and
                # sealed self._death_receipt; nothing more to do.
                break

            if self._decision_cadence > timedelta(0):
                await self._sleeper(self._decision_cadence.total_seconds())

        return RunSummary(
            ticks_completed=ticks_completed,
            bets_placed=bets_placed,
            no_bets_emitted=no_bets,
            settlements_processed=settlements_total,
            died=not self._alive,
            death_receipt=self._death_receipt,
            final_breath=self._breath,
            final_bankroll_usd=self._bankroll_usd,
        )

    # ------------------------------------------------------------------ #
    # Reconstruction — the 4-step reload per CEO Day 3 plan.
    # ------------------------------------------------------------------ #

    async def _reconstruct_from_disk(self) -> ReconstructedState:
        """Rebuild in-memory state from disk + chain.

        Step order is locked by the CEO 2026-05-26 plan:

        1. ``agent_state.json`` → weights / phase / breath / bankroll /
           last_tick / desperate.
        2. ``open_bets.jsonl`` → latest-status-wins fold; keep only
           ``status == "open"``.
        3. ``decisions.jsonl`` tail → resume tick counter at
           ``last_tick + 1``.
        4. ``chain_adapter.read_breath()`` → chain is source of truth.

        Missing snapshot ⇒ cold start (use the constructor's initial
        defaults). Returns the :class:`ReconstructedState` for tests +
        the operator-visibility log; also mutates the loop's in-memory
        fields verbatim.
        """
        # A9 restart safety (r7 M-3 / r12 M-1): storm state is in-memory
        # only, so a storm-enabled loop must never resume PRIOR state —
        # and "prior state" means ANY non-empty durable stream, not just
        # the snapshot (a missing agent_state.json still folds open_bets
        # and resumes decisions below). Checked BEFORE any folding.
        if self._storm_enabled:
            durable_streams = (
                self._writer.snapshot_path,
                self._writer.open_bets_path,
                self._writer.settled_bets_path,
                self._writer.decisions_path,
                self._writer.proposals_path,
                self._writer.reflections_path,
            )
            for stream in durable_streams:
                if stream.exists() and stream.stat().st_size > 0:
                    raise RuntimeError(
                        "storm state is not restart-safe — backtest-only "
                        f"feature (found prior state: {stream.name})"
                    )

        # --- Step 1: agent_state.json snapshot ----------------------------
        snapshot_path = self._writer.snapshot_path
        cold_start = not snapshot_path.exists()
        disk_breath: float
        if cold_start:
            self._weights = self._initial_weights
            self._phase = self._initial_phase
            self._bankroll_usd = self._initial_bankroll_usd
            self._desperate = False
            self._tick_counter = 0
            disk_breath = self._initial_breath
        else:
            raw = snapshot_path.read_text(encoding="utf-8")
            snapshot = AgentStateSnapshot.model_validate_json(raw)
            if snapshot.weights is not None:
                self._weights = snapshot.weights
            # else: pre-T-B-020 snapshot — keep current/initial weights.
            self._phase = Phase(snapshot.phase)
            self._bankroll_usd = snapshot.bankroll_usd
            self._desperate = snapshot.desperate
            # tick_counter resume — overridden by step 3 if decisions.jsonl
            # has fresher data, but the snapshot's last_tick is the
            # authoritative lower bound when decisions.jsonl is missing.
            self._tick_counter = snapshot.last_tick + 1 if snapshot.last_tick >= 0 else 0
            disk_breath = snapshot.breath

        # T-B-030 — rehydrate the L3 pending-proposal queue by folding
        # ``state/sandbox/proposals.jsonl`` latest-status-wins (same
        # pattern as step-2's open-bets fold below). The snapshot's
        # ``pending_proposal_ids`` field is a cache; the JSONL stream is
        # the source of truth, so we always fold from JSONL even on a
        # cold start (cold start with a populated JSONL is the
        # operator-runbook recovery case — e.g. agent_state.json was
        # nuked but the audit trail survives). Empty / missing file
        # collapses to an empty list — same posture as
        # :func:`agent.engines._performance_window.fold_pnl_from_settled`.
        self._pending_proposals = _fold_pending_proposals_from_jsonl(
            self._writer.proposals_path,
        )
        # Reset the trigger ring buffer on restart — we do not persist
        # the deque across runs; the brief's "ring buffer of last 20
        # weights" is in-process state. Restart resilience for the
        # *proposal queue* is the on-disk JSONL fold above; restart
        # resilience for the *trigger* is "wait for window to fill
        # again", same posture as L2 reflection's tick-interval reset.
        self._weight_ring_buffer.clear()

        # --- Step 2: open_bets.jsonl latest-status-wins fold --------------
        # Same pattern as SandboxSettlementPoller._select_due_bets but
        # for the OPEN-id set rather than the due-bet records.
        latest_status: dict[str, str] = {}
        for row in iter_jsonl(self._writer.open_bets_path):
            bet_id = row.get("bet_id")
            status = row.get("status")
            if isinstance(bet_id, str) and isinstance(status, str):
                latest_status[bet_id] = status
        self._open_bet_ids = {
            bet_id for bet_id, status in latest_status.items() if status == "open"
        }

        # --- Step 3: decisions.jsonl tail → tick counter -----------------
        decisions_path = self._writer.decisions_path
        if decisions_path.exists():
            last_tick_seen = -1
            for row in iter_jsonl(decisions_path):
                t = row.get("tick")
                if isinstance(t, int) and t > last_tick_seen:
                    last_tick_seen = t
            if last_tick_seen >= 0:
                # decisions.jsonl is the authoritative tick counter source.
                self._tick_counter = last_tick_seen + 1

        # --- Step 4: chain.read_breath() — source of truth ---------------
        chain_breath = await self._chain_adapter.read_breath()
        self._breath = chain_breath
        if not cold_start and chain_breath != disk_breath:
            # Non-fatal — surface as a state hook so the operator sees
            # the divergence without the loop crashing.
            logger.info(
                "sandbox_phase2_loop: chain breath %.4f differs from disk "
                "snapshot %.4f — chain takes precedence",
                chain_breath, disk_breath,
            )
            self._state_hook.emit(
                kind="reconstruction_breath_divergence",
                chain_breath=chain_breath,
                disk_breath=disk_breath,
                last_tick=self._tick_counter - 1,
            )

        return ReconstructedState(
            last_tick=self._tick_counter - 1,
            weights=self._weights,
            phase=self._phase,
            disk_breath=disk_breath,
            chain_breath=chain_breath,
            bankroll_usd=self._bankroll_usd,
            open_bet_ids=sorted(self._open_bet_ids),
            desperate=self._desperate,
            cold_start=cold_start,
        )

    # ------------------------------------------------------------------ #
    # Per-tick driver.
    # ------------------------------------------------------------------ #

    async def _tick(self) -> TickResult:
        """One decision tick.

        Sequence (locked by CEO plan + brief acceptance criteria):

        0. (T-B-032) Drain any operator-approved weight deltas from
           :attr:`runtime_agent` and apply each to ``self._weights`` —
           must precede settlement-poll so the settlement-time gradient
           sees the new weights too.
        1. ``poller.tick()`` — settle any due bets BEFORE this tick's
           decision so settlement-time weight updates land first.
        2. Refresh in-memory breath from chain — settlement updates
           changed the on-chain value via ``update_breath_from_pnl``.
        3. Generate per-tick inputs via :class:`TickInputSource`.
        4. Run :class:`DecisionEngine.decide` over the inputs +
           in-memory weights.
        5. If BET → ``executor.place_order``; add bet_id to open set.
        6. Append the :class:`DecisionRecord` to ``decisions.jsonl``.
        7. Snapshot agent state to ``agent_state.json`` (atomic).
        8. If breath == 0 → trigger :meth:`_die` (returns TickResult
           with ``died=True``).
        """
        now = self._clock.now()
        tick = self._tick_counter

        # Step 0 (T-B-032) — drain operator-approved weight deltas
        # BEFORE the settlement poll. Approvals that landed between
        # tick T-1 and tick T are applied here so:
        #   * the settlement-time WeightUpdater sees the new weights
        #     (settlement gradient flows against the post-delta basin);
        #   * the same tick's decision fusion uses the post-delta
        #     weights (the brief's "next tick must see the new weights"
        #     invariant).
        # The drain is non-blocking + lock-protected on the queue side
        # so a concurrent producer (FastAPI route handler) cannot
        # corrupt the in-flight tick.
        self._drain_and_apply_weight_deltas(tick=tick)

        # Step 1 + 2 — settlement poll FIRST so weight + breath are fresh.
        poll_result = await self._poller.tick()
        settlements_pnl_total = sum(s.pnl_usd for s in poll_result.settlements)
        # Settled bets transition out of the open set. The settled lines
        # appended by the poller are picked up by the next reconstruction
        # fold; for the in-memory loop we update directly.
        for s in poll_result.settlements:
            self._open_bet_ids.discard(s.bet_id)
            self._bankroll_usd += s.pnl_usd

        # T-B-021 forced-terminal hook — runs BETWEEN settlement-side
        # BREATH updates and the canonical step-2 read. Driving the
        # chain to zero here means:
        #   * step 2's read sees breath = 0 (decision routes NO_BET);
        #   * step 8's death check fires (BREATH <= 0 → _die);
        #   * any genuine settlement-side BREATH gains from this tick
        #     are still credited on-chain BEFORE we zero out — the
        #     forced-terminal is a deterministic OVERRIDE, not a race.
        if self._force_terminal_pending:
            current_chain_breath = await self._chain_adapter.read_breath()
            if current_chain_breath > 0.0:
                await self._chain_adapter.update_breath_from_pnl(
                    -current_chain_breath
                )
            # One-shot: clear AFTER the drive so a re-entry inside the
            # same tick is structurally impossible.
            self._force_terminal_pending = False
            # A9: the forced drive to zero is operator-domain, not world
            # physics — the storm EMA must not ingest it (one-shot skip).
            self._storm_skip_next = True
            logger.warning(
                "sandbox_phase2_loop tick=%d: forced-terminal hook fired — "
                "chain breath driven from %.4f to 0",
                tick, current_chain_breath,
            )

        # The chain has its own breath after settlement updates; refresh.
        self._breath = await self._chain_adapter.read_breath()

        # A9 storm percept — ONE update site, strictly post-refresh /
        # pre-decision (the settlement-learner ordering contract).
        if self._storm_enabled:
            self._update_storm(now)

        # Step 3 — per-tick decision inputs.
        inputs = self._tick_inputs.inputs_for(asof_ts=now, tick=tick)

        # Step 4 — decide.
        action: Action
        market_id_for_record: str | None
        # F0 (dashboard_ws_message v0.3.0) — decision-time telemetry the
        # WS decision frame carries: the per-engine score map (keyed by
        # the 5 lowercase persisted engine names) and the bet_id (==
        # executor order_id, only on BET ticks). Extracting these is
        # READ-ONLY — it does NOT alter the BET / NO_BET decision. The
        # signals map is surfaced for ANY tick with eligible inputs,
        # i.e. NO_BET ticks included, NOT only the if-BET branch.
        signal_scores: dict[str, float] | None = None
        bet_id_for_record: str | None = None
        if inputs is None:
            action = Action(
                kind=ActionKind.NO_BET,
                no_bet_reason="no_eligible_market",
            )
            market_id_for_record = None
        else:
            _assert_signal_coverage(inputs.signals)
            # Flat {engine_name: score} — built before decide() so it is
            # available regardless of the BET / NO_BET outcome.
            signal_scores = {
                name: signal.score for name, signal in inputs.signals.items()
            }
            action = await self._decision.decide(
                signals=inputs.signals,
                weights_alpha=(
                    self._weights.alpha[0],
                    self._weights.alpha[1],
                    self._weights.alpha[2],
                ),
                weights_beta=(self._weights.beta[0], self._weights.beta[1]),
                w_r=self._weights.w_r,
                w_s=self._weights.w_s,
                rho=self._weights.rho,
                bankroll_usd=self._bankroll_usd,
                breath=self._breath,
                liquidity_cap_usd=inputs.liquidity_cap_usd,
                market_id=inputs.market_id,
                desperate=self._desperate,
                **({"price": inputs.price} if self._value_betting else {}),
                # B′ Task 5: forward cross-market signal in value mode only
                # (legacy mode is byte-identical — kwarg absent → default 0.0).
                **(
                    {"cross_market_signal": inputs.cross_market_signal}
                    if self._value_betting
                    else {}
                ),
                **({"storm": self._storm} if self._storm_enabled else {}),
            )
            market_id_for_record = inputs.market_id

            # Bet-level side-aware floor (realism v3, r4 M-1): convert a BET
            # whose EFFECTIVE side price is below the floor into a NO_BET
            # BEFORE place_order — regardless of value mode, so a legacy-mode
            # run can never place sub-floor bets that only the export
            # invariant would catch, late. (Value mode also gates inside
            # decide(); this is the loop-boundary backstop.)
            if (
                action.kind == ActionKind.BET
                and self._effective_entry_price_floor is not None
                and action.side is not None
            ):
                eff_price = (
                    inputs.price
                    if action.side == Side.YES
                    else 1.0 - inputs.price
                )
                if eff_price < self._effective_entry_price_floor:
                    action = Action(
                        kind=ActionKind.NO_BET,
                        no_bet_reason=(
                            f"effective_price_below_floor:{eff_price:.4f}"
                        ),
                    )

        # Step 5 — place order if BET.
        if action.kind == ActionKind.BET and inputs is not None:
            assert action.side is not None  # narrowed by Action validator
            assert action.size_usd is not None
            # Carry the decision-time per-engine scores onto the bet so the
            # settlement-time weight updater (Task L3) can attribute realized
            # PnL to the engines that drove it. ``signal_scores`` was built
            # above (Step 4) so the same flat {engine_name: score} map feeds
            # both the executor and the v0.3.0 decision-frame telemetry.
            assert signal_scores is not None  # narrowed: inputs is not None
            # A9 stamps: the gate AS APPLIED at placement, read from the
            # engine's typed diagnostics companion immediately post-decide
            # (single-threaded per-life engine). Gated by the ONE explicit
            # flag — storm off ⇒ place_order never sees the kwargs and the
            # JSONL rows never gain the keys (r4 H-1 / r6 H-1).
            storm_stamps: dict[str, float] = {}
            if self._storm_enabled:
                diag = self._decision.last_gate_diagnostics
                if diag is None:
                    raise RuntimeError(
                        "storm stamps require gate diagnostics on a BET — "
                        "decide() must populate them in value mode"
                    )
                storm_stamps = {
                    "storm_at_bet": diag.storm,
                    "edge_at_bet": diag.edge_abs,
                    "min_edge_at_bet": diag.min_edge_base,
                    "gamma_at_bet": diag.gamma,
                    "eff_min_edge_at_bet": diag.eff_min_edge,
                }
            # V1.2 — thread the LIVE execution-cost stamps onto the BetRecord as
            # a SET so settlement is cost-NET + the fail-closed LIVE guard
            # (assert_cost_fields_present) is satisfied. ``fill_price`` is the
            # LIVE discriminator: a replay/idle/backtest tick leaves it None, so
            # NONE of the 4 stamps are threaded and the JSONL row stays
            # byte-identical to the pre-V1.2 shape (liquidity_cap_usd is always
            # present on TickInputs for decision-time sizing, but is only
            # PERSISTED as a cost stamp on the LIVE path). The dollar
            # ``spread_paid_usd`` is derived HERE — the live source only knows the
            # size-independent half-spread FRACTION; the stake (action.size_usd)
            # is decided just above by the Kelly gate. ``fee_bps`` is a RATE
            # passed through verbatim — never coerced — so an incomplete LIVE
            # tick still trips the fail-closed guard at settlement.
            cost_stamps: dict[str, float | None] = {}
            if inputs.fill_price is not None:
                spread_paid_usd = (
                    inputs.half_spread_frac * action.size_usd
                    if inputs.half_spread_frac is not None
                    else None
                )
                cost_stamps = {
                    "fill_price": inputs.fill_price,
                    "fee_bps": inputs.fee_bps,
                    "spread_paid_usd": spread_paid_usd,
                    "liquidity_cap_usd": inputs.liquidity_cap_usd,
                }
            order_result = await self._executor.place_order(
                market_id=inputs.market_id,
                side=_side_to_literal(action.side),
                price=inputs.price,
                size_usd=action.size_usd,
                signal_scores=signal_scores,
                **cost_stamps,
                **storm_stamps,
            )
            # bet_id == executor order_id (one uuid minted for both); this
            # is the settlement<->decision correlation key the WS decision
            # frame surfaces (v0.3.0).
            bet_id_for_record = order_result.order_id
            self._open_bet_ids.add(order_result.order_id)

        # Living Stage P1 — odds + per-engine scores for the dashboard, stamped
        # ONLY behind the flag so decisions.jsonl is byte-identical when off.
        # ``inputs.price`` is the YES implied probability (NO = 1 - price, per
        # the effective-price gate above). ``signal_scores`` was built in Step 4
        # (a dict when ``inputs`` is not None, else None). ``fee_floor_pct`` has
        # no clean local at this site (the edge floor lives in the engine's
        # gate diagnostics, only on a storm-enabled BET), so it stays None and
        # the Mind rail renders the floor only when present.
        if self._record_living_stage_fields and inputs is not None:
            odds_yes_for_record: float | None = float(inputs.price)
            odds_no_for_record: float | None = 1.0 - float(inputs.price)
            signal_scores_for_record: dict[str, float] = dict(signal_scores or {})
        else:
            odds_yes_for_record = None
            odds_no_for_record = None
            signal_scores_for_record = {}

        # Step 6 — write DecisionRecord.
        record = DecisionRecord(
            tick=tick,
            ts=_iso_utc(now),
            market_id=market_id_for_record,
            kind=_action_kind_literal(action.kind),
            size_usd=float(action.size_usd or 0.0),
            side=_side_to_literal(action.side) if action.side is not None else None,
            edge_pct=action.edge_pct,
            no_bet_reason=action.no_bet_reason,
            breath_after=self._breath,
            bankroll_usd_after=self._bankroll_usd,
            odds_yes=odds_yes_for_record,
            odds_no=odds_no_for_record,
            fee_floor_pct=None,
            signal_scores=signal_scores_for_record,
        )
        self._writer.append_decision(record)

        # F0 (dashboard_ws_message v0.3.0) — operator-visibility decision
        # telemetry. Surfaces the three v0.3.0 correlation fields
        # (market_id, bet_id, signals) on the state-hook stream so the
        # WS bridge / dashboard can build a v0.3.0 decision frame keyed by
        # bet_id. READ-ONLY: this emit does NOT touch the BET / NO_BET
        # decision or the DecisionRecord above — the StateHook contract
        # swallows any error and never feeds back into the loop. Fields
        # are optional: ``bet_id`` is None on NO_BET ticks; ``signals`` is
        # None only when no market was eligible.
        self._state_hook.emit(
            kind="decision_telemetry",
            tick=tick,
            action=_action_kind_literal(action.kind),
            market_id=market_id_for_record,
            bet_id=bet_id_for_record,
            signals=signal_scores,
        )

        # Step 7 — atomic snapshot.
        self._tick_counter = tick + 1
        snapshot = AgentStateSnapshot(
            snapshot_ts=_iso_utc(now),
            phase=_phase_to_literal(self._phase),
            breath=self._breath,
            bankroll_usd=self._bankroll_usd,
            phase_age_days=0.0,  # sandbox loop doesn't model phase age yet
            open_bet_ids=sorted(self._open_bet_ids),
            last_tick=tick,
            weights=self._weights,
            desperate=self._desperate,
            pending_proposals=list(self._pending_proposals),
            incarnation_number=self._incarnation_number,
        )
        self._writer.write_snapshot(snapshot)

        # Logging-only audit of settlements_pnl_total — surfaces in run logs
        # for the operator runbook. The bankroll_usd is the authoritative
        # number persisted in the DecisionRecord above.
        if settlements_pnl_total != 0.0:
            logger.info(
                "sandbox_phase2_loop tick=%d processed %d settlements "
                "(net pnl_usd=%.4f, post-settlement bankroll=%.4f)",
                tick, len(poll_result.settlements), settlements_pnl_total,
                self._bankroll_usd,
            )

        # Step 7.5 — A10 divine tithe: the gods charge periodic rent BEFORE
        # the death check, so a rent-induced breath drain is caught by the
        # same check below (and routes to _die → next incarnation's advisor).
        # Counts only markets actually seen (a real eligible market this
        # tick), so settle-only ticks do not advance the rent clock.
        if self._divine_tithe and inputs is not None:
            self._markets_since_start += 1
            if self._markets_since_start % self._tithe_every == 0:
                await self._attempt_tithe(tick=tick, now=now)

        # Step 8 — death check, with the optional tribute escape (A7): the
        # agent may buy breath from the gods at the deathbed. The policy
        # proposes, the gods' dice dispose, the offering is kept win or
        # lose. policy=None (the default, and the only live-runtime
        # configuration) is byte-identical to the bare death check.
        died = False
        if self._breath <= 0.0:
            saved = False
            if self._tribute_policy is not None:
                saved = await self._attempt_tribute(tick=tick, now=now)
            if not saved:
                await self._die(last_tick=tick)
                died = True

        # Step 9 (T-B-024) — L2 reflection trigger. Runs ONLY when alive
        # AND a reflection engine is wired. Death-path reflections are
        # explicitly out of scope here: the PRD §5.1.B "Last Words"
        # one-shot terminal reflection flows through ``_die`` already
        # (via the ``last_words`` field on the chain adapter call); a
        # second reflection on the same dying tick would be both
        # confusing and wasteful of the LLM budget.
        if not died and self._reflection_engine is not None:
            trigger = self._reflection_trigger(tick=tick)
            if trigger is not None:
                await self._fire_reflection(
                    trigger=trigger,
                    tick=tick,
                    now=now,
                    action=action,
                    market_id=market_id_for_record,
                )

        # Step 10 (T-B-025 → T-B-030) — L3 advisor trigger. Runs ONLY
        # when alive; same posture as the L2 reflection branch above
        # (the dying tick already has its "last words" narrative; an
        # L3 proposal on a tombstoned agent has no consumer). The
        # trigger appends current weights to the ring buffer every
        # tick regardless of whether it fires; the advisor itself
        # fires either on the M=100 interval (``tick_count % 100 == 0``)
        # OR when the ring buffer is full AND the cross-window max |Δw|
        # is below the threshold (default ``1e-3``), whichever first.
        # See :meth:`_strategy_advisor_trigger`.
        #
        # Snapshot-after-L3 invariant: the step-7 snapshot above captures
        # the pre-L3 ``pending_proposals`` list, so a fire that grew the
        # list mid-tick must re-snapshot atomically. We re-write only on
        # actual list growth — the no-op advisor path (Phase-1 default)
        # never grows the list, so the second write is the rare case.
        if not died:
            advisor_trigger = self._strategy_advisor_trigger(tick=tick)
            if advisor_trigger is not None:
                pre_count = len(self._pending_proposals)
                self._fire_strategy_advisor(
                    trigger=advisor_trigger,
                    tick=tick,
                    now=now,
                )
                if len(self._pending_proposals) != pre_count:
                    # Re-snapshot so the on-disk
                    # ``agent_state.json.pending_proposals`` reflects the
                    # post-L3 list. Defense in depth: a restart between
                    # ticks reads the up-to-date queue rather than the
                    # stale pre-L3 view.
                    post_l3_snapshot = AgentStateSnapshot(
                        snapshot_ts=_iso_utc(now),
                        phase=_phase_to_literal(self._phase),
                        breath=self._breath,
                        bankroll_usd=self._bankroll_usd,
                        phase_age_days=0.0,
                        open_bet_ids=sorted(self._open_bet_ids),
                        last_tick=tick,
                        weights=self._weights,
                        desperate=self._desperate,
                        pending_proposals=list(self._pending_proposals),
                        incarnation_number=self._incarnation_number,
                    )
                    self._writer.write_snapshot(post_l3_snapshot)

        return TickResult(
            tick=tick,
            action=action,
            breath_after=self._breath,
            bankroll_after=self._bankroll_usd,
            poll_settled=poll_result.settled_count,
            poll_pending=poll_result.pending_count,
            died=died,
        )

    # ------------------------------------------------------------------ #
    # A7 — the altar (deathbed tribute).
    # ------------------------------------------------------------------ #

    def _update_storm(self, now: datetime) -> None:
        """A9: one EMA over the tick-level REAL breath delta.

        The input is ``self._breath − last_refreshed`` — the physics
        that kills (settlement netting + loss multiplier included,
        nothing re-derived) — NEVER the loop's raw
        ``settlements_pnl_total``. Decay is WALL-CLOCK (the backtest
        tick is event-driven, not time-regular): state halves every
        ``STORM_HALF_LIFE_HOURS`` regardless of stop density, and the
        τ-blend runs ONLY on nonzero-delta ticks (the ``(1−τ)`` factor
        is itself an event-count decay and must never run on no-event
        ticks). A tribute grant resets the baseline in place (see
        :meth:`_attempt_tribute`); the forced-terminal drive keeps a
        one-shot skip flag (operator-domain, not world physics).
        """
        if self._last_storm_ts is not None:
            dt_hours = (now - self._last_storm_ts).total_seconds() / 3600.0
            if dt_hours > 0.0:
                self._storm_state *= 0.5 ** (dt_hours / STORM_HALF_LIFE_HOURS)
        prev = self._last_refreshed_breath
        delta = (
            0.0
            if (prev is None or self._storm_skip_next)
            else self._breath - prev
        )
        self._storm_skip_next = False
        self._last_refreshed_breath = self._breath
        self._last_storm_ts = now
        if delta != 0.0:
            tau = self._storm_tau
            # Clamped at 0 (r3 M-2): wins must not bank NEGATIVE storm
            # credit that delays the response to a later loss cluster.
            self._storm_state = max(
                0.0, (1.0 - tau) * self._storm_state + tau * (-delta)
            )
        self._storm = min(1.0, max(0.0, self._storm_state / self._storm_scale))

    async def _attempt_tribute(self, *, tick: int, now: datetime) -> bool:
        """Consult the tribute policy at the deathbed; roll the gods' dice.

        The altar validates the OFFER at the world-rule boundary — a
        malformed or malicious policy can never poison ``_bankroll_usd``
        (which drives sizing). The offering is deducted win or lose
        (greedy gods). On a grant, breath flows through the CANONICAL
        chain channel (the loop re-reads chain breath every tick — a
        loop-memory write would evaporate and re-trigger the altar each
        tick), and the snapshot is re-written so a same-dir re-entry can
        never refund the gods. (Breath itself resets per life on
        reconstruction by the PRE-EXISTING respawn semantic — the replay
        chain is in-memory; unchanged here.) A FAILED tribute writes no
        snapshot of its own: the very next statement is ``_die``, whose
        terminal snapshot persists the deducted bankroll; the
        deduction→die crash window is the same pre-existing mid-tick
        exposure class as every other tick mutation. The dying tick's
        DecisionRecord (bet domain, appended at step 6) is PRE-altar by
        design — the tribute hook event + snapshot are the post-altar
        authority.
        """
        assert self._tribute_policy is not None
        assert self._tribute_rng is not None
        try:
            amount = await self._tribute_policy.on_dying(
                tick=tick,
                breath=self._breath,
                bankroll_usd=self._bankroll_usd,
            )
        except Exception as exc:
            logger.warning(
                "tribute: policy raised %s: %s — silence is death",
                type(exc).__name__,
                exc,
            )
            return False
        if (
            amount is None
            or isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(float(amount))
            or float(amount) < TRIBUTE_MIN_USD
            or float(amount) > self._bankroll_usd
        ):
            return False
        offering = float(amount)
        self._bankroll_usd -= offering
        p = tribute_success_probability(offering)
        # Hoist the gods' dice draw to a named local so it can be surfaced on
        # the tribute event (Living Stage P1 dashboard "dice .NN → survived").
        roll = self._tribute_rng.random()
        success = roll < p
        if success:
            cur = await self._chain_adapter.read_breath()
            await self._chain_adapter.update_breath_from_pnl(
                self._tribute_breath - cur
            )
            self._breath = await self._chain_adapter.read_breath()
            # A9 (r6 M-2): the grant is operator-domain — reset the storm
            # baseline IN PLACE so the +35 never enters the EMA but the
            # NEXT tick's real settlement physics does (no skip flag).
            self._last_refreshed_breath = self._breath
            post_tribute_snapshot = AgentStateSnapshot(
                snapshot_ts=_iso_utc(now),
                phase=_phase_to_literal(self._phase),
                breath=self._breath,
                bankroll_usd=self._bankroll_usd,
                phase_age_days=0.0,
                open_bet_ids=sorted(self._open_bet_ids),
                last_tick=tick,
                weights=self._weights,
                desperate=self._desperate,
                pending_proposals=list(self._pending_proposals),
                incarnation_number=self._incarnation_number,
            )
            self._writer.write_snapshot(post_tribute_snapshot)
        self._state_hook.emit(
            kind="tribute",
            tick=tick,
            amount_usd=offering,
            success=success,
            breath_after=self._breath,
            bankroll_after=self._bankroll_usd,
            dice_roll=roll,
        )
        logger.info(
            "tribute: tick=%d offered $%.2f (p=%.2f) -> %s; bankroll=%.2f",
            tick,
            offering,
            p,
            "GRANTED" if success else "the gods kept the money",
            self._bankroll_usd,
        )
        return success

    async def _attempt_tithe(self, *, tick: int, now: datetime) -> None:
        """A10: the gods collect periodic rent for existence.

        Cash-preferred (the agent never burns its scarce survival currency
        when it can pay cash): deduct ``tithe_amount_usd`` from bankroll if
        affordable; otherwise the gods take ``tithe_breath_cost`` breath
        through the CANONICAL chain channel (so the loop re-reads it and the
        step-8 death check can fire). A do-nothing agent stops earning, runs
        out of cash, and bleeds out — which is exactly the metabolic pressure
        that closes the abstention-survival loophole. The breath path can
        drive breath <= 0; the immediately following death check handles it.
        Emits a ``tithe`` hook event (amount_usd XOR breath_cost) for the
        recorder's gods-revenue accounting.
        """
        if self._bankroll_usd >= self._tithe_amount_usd:
            self._bankroll_usd -= self._tithe_amount_usd
            paid_usd = self._tithe_amount_usd
            breath_cost = 0.0
        else:
            await self._chain_adapter.update_breath_from_pnl(
                -self._tithe_breath_cost
            )
            self._breath = await self._chain_adapter.read_breath()
            # A9 interplay: the rent is operator-domain (the gods' tax), NOT a
            # market-regime signal — reset the storm baseline in place (like a
            # tribute grant) so the −cost never enters the storm EMA and
            # masquerades as the market turning scary.
            self._last_refreshed_breath = self._breath
            paid_usd = 0.0
            breath_cost = self._tithe_breath_cost
        self._state_hook.emit(
            kind="tithe",
            tick=tick,
            amount_usd=paid_usd,
            breath_cost=breath_cost,
            breath_after=self._breath,
            bankroll_after=self._bankroll_usd,
        )
        logger.info(
            "tithe: tick=%d the gods took %s; breath=%.2f bankroll=%.2f",
            tick,
            f"${paid_usd:.2f}" if paid_usd else f"{breath_cost:.1f} breath",
            self._breath,
            self._bankroll_usd,
        )

    # ------------------------------------------------------------------ #
    # Death path — PRD §6.9.
    # ------------------------------------------------------------------ #

    async def _die(self, *, last_tick: int) -> DeathReceipt:
        """Trigger the death path: chain kill + Tombstone mint.

        Called when ``breath <= 0`` at the close of a tick. Calls the
        chain adapter's ``kill_and_mint_tombstone`` with the full PRD §5.1
        Tombstone metadata bundle (T-B-021):

        * ``agent_id``           — :attr:`agent_id`, constructor-injected.
        * ``final_weights_hash`` — SHA-256 over the canonical JSON
          serialisation of :attr:`weights`. Computed at kill time so a
          mid-life weight mutation that hadn't yet snapshotted is
          captured. Hex-encoded with a ``0x`` prefix so the on-chain
          adapter can hand the value straight to a ``bytes32`` slot.
        * ``memory_bank_cid``    — :attr:`_memory_bank_cid`, constructor-
          injected (placeholder when no IPFS pin is configured).
        * ``last_words``         — constructor-injected override OR the
          deterministic template from
          :func:`_default_last_words_template`. Production wiring routes
          :class:`agent.llm.prompts.last_words.LastWordsService` into the
          constructor; the sandbox loop never calls the LLM directly.

        After the chain adapter returns, the loop flips ``self._alive``
        False, persists a terminal snapshot (defense in depth — a
        hypothetical restart of a tombstoned agent reads ``PHASE_4_TERMINAL``
        immediately), and emits an ``agent_died`` state hook with the
        full receipt + the metadata bundle (operator visibility).

        V1.4b terminal-close (Codex-4): BEFORE the tombstone bankroll is read,
        fold EVERY still-open bet into a terminal ledger record via the poller's
        :meth:`~agent.runtime.sandbox_settlement_poller.SandboxSettlementPoller.terminal_close`.
        Resolved bets settle through the full ``_resolve_and_settle`` side
        effects (chain breath + weight update); their realized PnL is folded
        into the in-memory bankroll here (the poller owns the chain breath
        delta, the loop owns the bankroll scalar — the SAME split as the
        per-tick settlement fold). Unresolved bets are voided (pnl=0). This
        guarantees no open bet dangles into the next incarnation as ghost PnL
        and that the tombstone records a clean terminal bankroll.
        """
        if self._open_bet_ids:
            close_result = await self._poller.terminal_close(now=self._clock.now())
            for outcome in close_result.settlements:
                self._bankroll_usd += outcome.pnl_usd
            self._open_bet_ids.clear()

        final_weights_hash = _sha256_hex_prefixed(self._weights.model_dump_json())
        last_words = (
            self._last_words_override
            if self._last_words_override is not None
            else _default_last_words_template(
                last_tick=last_tick,
                bankroll_usd=self._bankroll_usd,
            )
        )
        receipt = await self._chain_adapter.kill_and_mint_tombstone(
            agent_id=self._agent_id,
            bankroll_usd=self._bankroll_usd,
            last_tick=last_tick,
            final_weights_hash=final_weights_hash,
            memory_bank_cid=self._memory_bank_cid,
            last_words=last_words,
        )
        self._death_receipt = receipt
        self._alive = False
        self._phase = Phase.PHASE_4_TERMINAL
        # Persist the terminal phase into the snapshot so a (hypothetical)
        # restart of a tombstoned agent reads the terminal state immediately
        # — defense in depth on top of the in-memory `_alive` flag.
        try:
            snapshot = AgentStateSnapshot(
                snapshot_ts=_iso_utc(self._clock.now()),
                phase=_phase_to_literal(self._phase),
                breath=0.0,
                bankroll_usd=self._bankroll_usd,
                phase_age_days=0.0,
                open_bet_ids=sorted(self._open_bet_ids),
                last_tick=last_tick,
                weights=self._weights,
                desperate=self._desperate,
                pending_proposals=list(self._pending_proposals),
                incarnation_number=self._incarnation_number,
            )
            self._writer.write_snapshot(snapshot)
        except Exception as exc:
            logger.error(
                "sandbox_phase2_loop._die: failed to write terminal snapshot: %s",
                exc,
            )
        self._state_hook.emit(
            kind="agent_died",
            agent_id=self._agent_id,
            last_tick=last_tick,
            kill_tx_hash=receipt.kill_tx_hash,
            tombstone_token_id=receipt.tombstone_token_id,
            tombstone_tx_hash=receipt.tombstone_tx_hash,
            bankroll_usd=self._bankroll_usd,
            final_weights_hash=final_weights_hash,
            memory_bank_cid=self._memory_bank_cid,
            last_words=last_words,
        )
        return receipt

    # ------------------------------------------------------------------ #
    # T-B-024 — L2 reflection trigger + emission helpers.
    # ------------------------------------------------------------------ #

    def _reflection_trigger(
        self, *, tick: int
    ) -> Literal["tick_interval", "weight_delta"] | None:
        """Return which trigger fired (or None) for the just-completed tick.

        Order of checks per T-B-024 brief "whichever first":

        1. **tick_interval** — every N=10 ticks since the LAST reflection
           (or since cold start). Concretely: ``(tick - last) >= N``.
           Using ``>=`` (not strict ``>``) is what makes the FIRST trigger
           fire on tick 9 (0-indexed N=10 ticks: 0..9 inclusive). On cold
           start ``_last_reflection_tick == -1`` so the check reduces to
           ``tick + 1 >= N`` — the 10th tick (``tick == 9``) fires.

        2. **weight_delta** — ``max(abs(w[i] - last[i])) > threshold``
           across the 6 canonical fusion weights
           (:data:`REFLECTION_WEIGHT_KEYS`). Strict ``>`` because
           ``= 0.05`` is on the boundary; we use strict-greater so the
           default threshold is the "delta has exceeded" interpretation
           from the brief.

        Two triggers can plausibly fire on the same tick; we return
        ``"tick_interval"`` first because that branch is the
        coarser-grained cadence the operator can plan around. A weight
        bump on the SAME tick that also crosses the interval gets coded
        as a tick_interval reflection (the rich :class:`ReflectionRecord`
        body still captures the weight movement narratively).
        """
        ticks_since_last = tick - self._last_reflection_tick
        if ticks_since_last >= self._reflection_tick_interval:
            return "tick_interval"
        if self._max_abs_weight_delta() > self._reflection_weight_delta_threshold:
            return "weight_delta"
        return None

    def _max_abs_weight_delta(self) -> float:
        """``max(|w[i] - last_reflection_weights[i]|)`` across the 6 canonical
        fusion weights per :data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS`.

        Returns 0.0 when the current and baseline weights are byte-equal
        (cold start fast-path). Pulled out as its own method so the
        operator-visibility test can introspect the value without poking
        the trigger function's branch logic. Delegates to
        :meth:`_max_abs_weight_delta_against` (T-B-025) — the L2
        reflection baseline is just one specific baseline against which
        the generic helper computes its max-|Δw|.
        """
        return self._max_abs_weight_delta_against(self._last_reflection_weights)

    async def _fire_reflection(
        self,
        *,
        trigger: Literal["tick_interval", "weight_delta"],
        tick: int,
        now: datetime,
        action: Action,
        market_id: str | None,
    ) -> SandboxReflectionRecord | None:
        """Run the LLM reflection + append the JSONL projection.

        Returns the persisted record so tests can assert against it
        without re-reading the JSONL file. Returns ``None`` when the
        cost-guard is already exhausted (the budget short-circuit fires
        BEFORE the LLM call — the dashboard's $25 monthly cap is the
        hard wall, and a leaked LLM call past that cap would be real
        money on the floor; mirrors the ``SmokeSentimentScorer.score``
        pre-check pattern in :mod:`agent.llm._smoke`). The trigger
        baselines still reset on the short-circuit path so the loop
        does NOT spin attempting the same exhausted reflection every
        tick.
        """
        assert self._reflection_engine is not None  # narrowed by caller
        if self._cost_guard.is_exhausted():
            logger.warning(
                "sandbox_phase2_loop tick=%d: cost_guard exhausted "
                "(total=$%.4f / cap=$%.4f) — skipping LLM call; "
                "baselines reset so loop does not spin",
                tick, self._cost_guard.total_usd, self._cost_guard.hard_cap_usd,
            )
            self._last_reflection_tick = tick
            self._last_reflection_weights = self._weights
            self._state_hook.emit(
                kind="reflection_skipped_cost_exhausted",
                trigger=trigger,
                tick=tick,
                total_usd=self._cost_guard.total_usd,
            )
            return None
        payload = _build_reflection_payload(
            tick=tick,
            now=now,
            phase=self._phase,
            vitals=Vitals(
                breath=self._breath,
                bankroll_usd=self._bankroll_usd,
                phase_age_days=0.0,
            ),
            weights=self._weights,
            action=action,
            agent_id=self._agent_id,
            trigger=trigger,
            market_id=market_id,
        )
        engine_record: ReflectionRecord = await self._reflection_engine.reflect(
            tick=payload,
            key_moment=(trigger == "weight_delta"),
        )
        # Record cost AFTER the call — engine.reflect() is fail-soft and
        # always returns a record, so the cost is real. The pre-check
        # above is the budget gate; this swallow protects against the
        # narrow race where two callers crossed the cap concurrently.
        try:
            cost_event = self._cost_guard.record(
                label="reflection", usd=REFLECTION_PER_CALL_USD_EST,
            )
            llm_cost_usd = cost_event.usd
        except CostExhaustedError:
            logger.warning(
                "sandbox_phase2_loop tick=%d: cost_guard raced past cap "
                "between pre-check and post-record — emitted record "
                "reports llm_cost_usd=0.0",
                tick,
            )
            llm_cost_usd = 0.0
        record = SandboxReflectionRecord(
            reflection_id=uuid.uuid4().hex,
            ts=_iso_utc(now),
            trigger=trigger,
            narrative=engine_record.body,
            weight_snapshot=_weight_snapshot_dict(self._weights),
            recent_pnl_window=self._recent_pnl_window_usd(),
            llm_cost_usd=llm_cost_usd,
        )
        self._writer.append_reflection(record)
        self._last_reflection_tick = tick
        self._last_reflection_weights = self._weights
        self._state_hook.emit(
            kind="reflection_emitted",
            reflection_id=record.reflection_id,
            trigger=trigger,
            tick=tick,
            llm_cost_usd=record.llm_cost_usd,
            recent_pnl_window=record.recent_pnl_window,
        )
        return record

    def _recent_pnl_window_usd(self) -> float:
        """Sum ``pnl_usd`` over the last :data:`REFLECTION_PNL_WINDOW`
        rows of ``settled_bets.jsonl``.

        Empty file ⇒ 0.0 (no settlements yet — the reflection narrative
        captures "early in the run" state without crashing). Malformed
        ``pnl_usd`` entries (non-numeric) are skipped silently per the
        same defence the :func:`iter_jsonl` reader applies (corrupt-line
        skip rather than raise). Read-once-per-call is the simplest
        model; for sandbox cadences (~minutes between fires) this is
        well under the perf budget.
        """
        rows = iter_jsonl(self._writer.settled_bets_path)
        tail = rows[-REFLECTION_PNL_WINDOW:]
        total = 0.0
        for row in tail:
            value = row.get("pnl_usd")
            if isinstance(value, (int, float)):
                total += float(value)
        return total

    # ------------------------------------------------------------------ #
    # T-B-025 — L3 advisor trigger + emission helpers.
    # ------------------------------------------------------------------ #

    def _strategy_advisor_trigger(
        self, *, tick: int,
    ) -> Literal["tick_interval", "weight_stability"] | None:
        """Return which L3 trigger fired (or None) for the just-completed tick.

        T-B-030 ring-buffer semantics — fires on whichever-first:

        1. **tick_interval** — every M=100 ticks (default). The check
           is ``tick_count > 0 AND tick_count % M == 0`` where
           ``tick_count = tick + 1`` is the post-increment 1-indexed
           number of completed ticks. With the default M=100 the first
           fire lands on the 100th tick (``tick == 99`` →
           ``tick_count == 100``).

        2. **weight_stability** — the ring buffer holds the last
           :attr:`strategy_advisor_stability_window` weight snapshots
           (default 20). The trigger fires when the buffer is FULL
           (``len(buffer) == window``) AND the max |Δw| across every
           pair of buffered weights, across the 6 canonical fusion
           parameters, is strictly less than the threshold (default
           ``1e-3``). The "range" check uses
           :func:`_max_abs_weight_delta_in_buffer` so the implementation
           is O(window·6) per tick — negligible at this scale.

        Side effect: appends the CURRENT weights to the ring buffer
        BEFORE the trigger checks so the just-completed tick is always
        in the window. On a fire (either trigger) the buffer is cleared
        in :meth:`_fire_strategy_advisor` so the next stability fire
        requires a fresh window of low-Δw ticks.

        No double-trigger semantics: when both conditions are met on
        the same tick (e.g. ``tick_count == 100`` AND
        ``len(buffer) == window`` AND range below threshold) the
        tick_interval branch wins — operators can plan around a
        deterministic cadence; the stability trigger is the fall-back
        for "weights have settled before the cadence hit". The clear in
        :meth:`_fire_strategy_advisor` drops the just-buffered window
        so the NEXT stability fire requires a fresh window.
        """
        # Step 1 — append the current weights to the ring buffer. We
        # do this BEFORE the trigger check so the just-completed tick
        # is in the window for the stability range computation.
        self._weight_ring_buffer.append(self._weights)

        # Step 2 — tick_interval check on the post-increment 1-indexed
        # tick count. ``tick`` is the 0-indexed in-progress tick number
        # (matches ``DecisionRecord.tick``); ``tick + 1`` is the count
        # of ticks completed including this one. The ``tick_count > 0``
        # guard is defensive — sprint_9 cold start can't actually
        # reach a tick where tick==-1, but the explicit check makes
        # the invariant readable.
        tick_count = tick + 1
        if (
            tick_count > 0
            and tick_count % self._strategy_advisor_tick_interval == 0
        ):
            return "tick_interval"

        # Step 3 — weight_stability check. The buffer must be FULL
        # (`maxlen` reached) AND the cross-window max |Δw| below the
        # threshold. ``len(buffer) == window`` because deque(maxlen=N)
        # caps occupancy at N — checking equality is equivalent to "at
        # least N entries" for this bounded deque.
        if (
            len(self._weight_ring_buffer) == self._strategy_advisor_stability_window
            and self._max_abs_weight_delta_in_buffer()
            < self._strategy_advisor_stability_threshold
        ):
            return "weight_stability"
        return None

    def _max_abs_weight_delta_in_buffer(self) -> float:
        """Max |Δw[i]| across every pair of weights in the ring buffer.

        Computed as ``max_k (max_i w_i[k] - min_i w_i[k])`` over the 6
        canonical fusion weights (:data:`REFLECTION_WEIGHT_KEYS`). This
        is the "range" of each parameter across the window; the max
        over parameters is the cross-window max |Δw|.

        Returns 0.0 for an empty buffer (defensive — the caller already
        guards on ``len == window > 0``, but the explicit return keeps
        a hypothetical mis-use safe).
        """
        if not self._weight_ring_buffer:
            return 0.0
        # Project each Weights to the 6-key snapshot dict once, then
        # take the range per key.
        snapshots = [
            _weight_snapshot_dict(w) for w in self._weight_ring_buffer
        ]
        max_delta = 0.0
        for key in REFLECTION_WEIGHT_KEYS:
            values = [snap[key] for snap in snapshots]
            spread = max(values) - min(values)
            if spread > max_delta:
                max_delta = spread
        return max_delta

    def _max_abs_weight_delta_against(
        self, baseline: Weights,
    ) -> float:
        """``max(|w[i] - baseline[i]|)`` across the 6 canonical fusion weights.

        Mirrors :meth:`_max_abs_weight_delta` but takes the baseline as
        a parameter. Used by the L2 reflection trigger
        (:meth:`_reflection_trigger` → :meth:`_max_abs_weight_delta` →
        :meth:`_max_abs_weight_delta_against`) against
        ``_last_reflection_weights``. The T-B-030 L3 trigger no longer
        uses this helper — see :meth:`_max_abs_weight_delta_in_buffer`
        for the cross-window range computation that replaced it.
        """
        current = _weight_snapshot_dict(self._weights)
        base = _weight_snapshot_dict(baseline)
        return max(
            abs(current[k] - base[k]) for k in REFLECTION_WEIGHT_KEYS
        )

    def _fire_strategy_advisor(
        self,
        *,
        trigger: Literal["tick_interval", "weight_stability"],
        tick: int,
        now: datetime,
    ) -> list[StrategyProposal]:
        """Run the advisor + persist every returned proposal (T-B-025).

        Calls :meth:`StrategyAdvisor.review_window` once with a
        :class:`PerformanceWindow` assembled from the loop's current
        scalar state (and the same recent-PnL window the L2 reflection
        uses — see :meth:`_recent_pnl_window_usd`). Each returned
        :class:`StrategyProposal` is appended to ``proposals.jsonl`` and
        its id is added to :attr:`_pending_proposals`.

        Book-keeping: this method advances BOTH the tick baseline AND
        the weight baseline AND resets the stability counter. The reset
        is what implements the brief's "whichever-first" semantics —
        either trigger consumes the cycle and the next fire requires
        meeting the conditions AFRESH from the new baseline.

        Returns the list of persisted proposals so tests can assert
        against it without re-reading the JSONL file. An empty list
        (the :class:`NoOpStrategyAdvisor` path) is the normal sprint_9
        default — the trigger still fires, the baselines still reset,
        no rows land on disk.

        The :class:`StrategyAdvisor` Protocol is synchronous; we wrap
        the call in a guard that converts any exception into a state-hook
        event + an empty result so a single broken advisor cannot crash
        the loop. Mirrors the fail-soft posture of
        :class:`ReflectionEngine.reflect`.
        """
        # T-B-030: the buffer-oldest weight is the "baseline" the
        # advisor compares against for the cross-window range. We pass
        # both for prompt-rendering convenience; ``baseline_weights``
        # falls back to current_weights on a degenerate empty buffer
        # (can't happen in practice — trigger requires non-empty buffer).
        baseline = (
            self._weight_ring_buffer[0]
            if self._weight_ring_buffer
            else self._weights
        )
        # Phase B / B1 (codex H4) — fold the recent reflections.jsonl
        # narratives + the recent settled-bet PnL + the weight trajectory
        # into the window so the advisor's proposals are reflection-
        # INFORMED (reflect→learn→optimize). Behind the
        # ``GENESIS_REAL_REFLECTION`` seam (default OFF) so the advisor
        # input stays byte-unchanged when the flag is off — the helpers
        # return [] on a missing/empty stream anyway, but skipping the
        # reads entirely keeps the default path free of the extra I/O and
        # preserves the pre-B1 contract exactly. The fold helpers are
        # PURE tail-reads (no look-ahead — strictly historical lines).
        recent_reflections: list[str] = []
        recent_pnl: list[float] = []
        weight_trajectory: list[Weights] = []
        # ``tick_count`` stays 0 on the flag-OFF path so the advisor's
        # :attr:`PerformanceWindow.tick_count_or_tick` falls back to
        # ``tick`` exactly as before B1 (byte-unchanged advisor input).
        tick_count = 0
        if self._populate_reflection_window:
            recent_reflections = fold_recent_reflections_from_jsonl(
                self._writer.reflections_path,
            )
            recent_pnl = fold_pnl_from_settled(
                self._writer.settled_bets_path,
            )
            weight_trajectory = fold_weight_trajectory_from_jsonl(
                self._writer.reflections_path,
            )
            # ``tick`` is the 0-indexed just-completed tick; the advisor's
            # prompt wants the 1-indexed completed-tick count (same number
            # the trigger uses, ``tick + 1``).
            tick_count = tick + 1
        window = PerformanceWindow(
            tick=tick,
            ts=now,
            agent_id=self._agent_id,
            phase=self._phase,
            current_weights=self._weights,
            baseline_weights=baseline,
            recent_pnl_window_usd=self._recent_pnl_window_usd(),
            trigger=trigger,
            recent_pnl=recent_pnl,
            weight_trajectory=weight_trajectory,
            recent_reflections=recent_reflections,
            tick_count=tick_count,
        )
        try:
            proposals = list(self._strategy_advisor.review_window(window))
        except Exception as exc:
            logger.warning(
                "sandbox_phase2_loop tick=%d: strategy_advisor.review_window "
                "raised %s — treating as empty result (fail-soft)",
                tick, exc,
            )
            proposals = []
            self._state_hook.emit(
                kind="strategy_advisor_failed",
                trigger=trigger,
                tick=tick,
                error=str(exc),
            )
        persisted: list[StrategyProposal] = []
        for proposal in proposals:
            # Brief locks the on-disk shape to status="pending" at
            # emission time. The advisor impl already defaults to
            # "pending", so the common path is a no-op; we only clone
            # when a custom advisor forgot to stamp it (rare). Avoids
            # the per-fire Pydantic full-field copy on the dominant
            # path while preserving the defensive guarantee.
            stamped = (
                proposal
                if proposal.status == PROPOSAL_STATUS_PENDING
                else proposal.model_copy(
                    update={"status": PROPOSAL_STATUS_PENDING},
                )
            )
            self._writer.append_proposal(stamped)
            self._pending_proposals.append(stamped.proposal_id)
            persisted.append(stamped)
        # Bookkeep AFTER the persist loop so a mid-loop crash leaves
        # the trigger ready to re-fire on the next tick (the fail-soft
        # branch above already swallows the exception; this is the
        # defence-in-depth invariant).
        self._last_strategy_advisor_tick = tick
        # Clear the buffer per T-B-030 "whichever-first": the next
        # stability fire requires a fresh window of low-Δw ticks.
        self._weight_ring_buffer.clear()
        self._state_hook.emit(
            kind="strategy_advisor_fired",
            trigger=trigger,
            tick=tick,
            proposals_emitted=len(persisted),
            pending_proposals_count=len(self._pending_proposals),
        )
        return persisted

    def _drain_and_apply_weight_deltas(self, *, tick: int) -> int:
        """Drain :attr:`_runtime_agent` and apply each weight delta (T-B-032).

        Producer side is the FastAPI ``/api/proposals/{id}/approve``
        handler (or any test that pokes the queue directly); the
        consumer side is THIS method, invoked at the START of every
        :meth:`_tick`. Drain is FIFO + lock-protected; each delta is
        applied to ``self._weights`` via :func:`_apply_weight_delta`
        and the result re-validated through :class:`Weights`'
        normalisation invariant so an out-of-range payload cannot
        silently land.

        Failure mode is fail-soft per the L3 wire posture:

        * an unknown ``key`` / malformed delta → ``ValueError`` →
          state-hook ``weight_delta_apply_failed`` + the loop continues
          with the prior weights snapshot;
        * a delta that produces a denormal :class:`Weights`
          (defence-in-depth — the apply helper already clamps) →
          same handling.

        Returns the number of deltas the drain processed (including
        failed ones, so the caller can detect "something happened this
        tick"). 0 on the dominant path (no pending approvals).
        """
        deltas = self._runtime_agent.drain_pending_deltas()
        if not deltas:
            return 0
        for delta in deltas:
            try:
                new_weights = _apply_weight_delta(self._weights, delta)
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning(
                    "sandbox_phase2_loop tick=%d: weight_delta apply failed "
                    "(%s: %s) — skipping payload=%r",
                    tick, type(exc).__name__, exc, delta,
                )
                self._state_hook.emit(
                    kind="weight_delta_apply_failed",
                    tick=tick,
                    error=str(exc),
                    payload=dict(delta),
                )
                continue
            self._weights = new_weights
            self._state_hook.emit(
                kind="weight_delta_applied",
                tick=tick,
                key=str(delta.get("key", "")),
                amount=float(delta.get("delta", 0.0)),
                pending_count_after=self._runtime_agent.pending_count,
            )
        return len(deltas)

    @staticmethod
    def _resolve_tick_interval(
        *, explicit: int | None, env_source: Any,
    ) -> int:
        """Explicit ctor arg → env override → default. Validates > 0."""
        if explicit is not None:
            value = explicit
        else:
            raw = env_source.get(REFLECTION_TICK_INTERVAL_ENV_VAR)
            value = int(raw) if raw is not None else DEFAULT_REFLECTION_TICK_INTERVAL
        if value <= 0:
            raise ValueError(
                f"reflection_tick_interval must be > 0 (got {value})"
            )
        return value

    @staticmethod
    def _resolve_weight_delta_threshold(
        *, explicit: float | None, env_source: Any,
    ) -> float:
        """Explicit ctor arg → env override → default. Validates > 0."""
        if explicit is not None:
            value = explicit
        else:
            raw = env_source.get(REFLECTION_WEIGHT_DELTA_THRESHOLD_ENV_VAR)
            value = (
                float(raw)
                if raw is not None
                else DEFAULT_REFLECTION_WEIGHT_DELTA_THRESHOLD
            )
        if value <= 0.0:
            raise ValueError(
                f"reflection_weight_delta_threshold must be > 0 (got {value})"
            )
        return value

    # ------------------------------------------------------------------ #
    # AgentState projection — for callers that want the canonical
    # in-memory shape (the dashboard bridge consumes this).
    # ------------------------------------------------------------------ #

    def to_agent_state(self) -> AgentState:
        """Project the loop's in-memory scalars to a canonical
        :class:`AgentState` envelope.

        The dashboard bridge + the V2-boot rehydrator read this shape;
        the on-disk snapshot is the same data through a narrower
        Pydantic model (``AgentStateSnapshot``) so the dashboard's tail
        consumer doesn't have to know about the loop's private fields.
        """
        return AgentState(
            agent_id=self._agent_id,
            tick=self._tick_counter,
            phase=self._phase,
            vitals=Vitals(
                breath=self._breath,
                bankroll_usd=self._bankroll_usd,
                phase_age_days=0.0,
            ),
            weights=self._weights,
            desperate=self._desperate,
        )


# --------------------------------------------------------------------------- #
# Pure helpers — kept module-level for ease of unit testing.
# --------------------------------------------------------------------------- #


def _iso_utc(ts: datetime) -> str:
    """ISO-8601 UTC string with ``+00:00`` tz; naive → UTC.

    Mirrors :func:`agent.runtime.sandbox_settlement_poller._iso_utc` so
    the timestamp format on disk is consistent across modules.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def _action_kind_literal(kind: ActionKind) -> Any:
    """Narrow :class:`ActionKind` to the wire ``Literal["BET", "NO_BET"]``.

    Returns ``Any`` so mypy --strict accepts the assignment into the
    :class:`DecisionRecord.kind` field; the value is one of the two
    canonical strings.
    """
    return "BET" if kind == ActionKind.BET else "NO_BET"


def _side_to_literal(side: Side) -> Any:
    """Narrow :class:`Side` to the executor's ``Literal["YES", "NO"]``."""
    return "YES" if side == Side.YES else "NO"


def _phase_to_literal(phase: Phase) -> Any:
    """Narrow :class:`Phase` to the snapshot's ``Literal[...]`` union."""
    # All four PHASE_* enum members are byte-for-byte equal to the
    # Literal arms in AgentStateSnapshot.phase, so .value is the
    # canonical hand-off.
    return phase.value


def _weight_snapshot_dict(weights: Weights) -> dict[str, float]:
    """Project :class:`Weights` to the 6-key dict the SandboxReflectionRecord uses.

    Keys MUST match :data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS`
    exactly — the dashboard's diff renderer keys off these literal strings.
    Only the 6 INDEPENDENT fusion parameters appear (w_s and beta_1 are
    derived per PRD §4.1 normalisation).
    """
    return {
        "w_r": weights.w_r,
        "alpha_0": weights.alpha[0],
        "alpha_1": weights.alpha[1],
        "alpha_2": weights.alpha[2],
        "beta_0": weights.beta[0],
        "rho": weights.rho,
    }


def _build_reflection_payload(
    *,
    tick: int,
    now: datetime,
    phase: Phase,
    vitals: Vitals,
    weights: Weights,
    action: Action,
    agent_id: str,
    trigger: Literal["tick_interval", "weight_delta"],
    market_id: str | None,
) -> TickPayload:
    """Assemble a :class:`TickPayload` for :meth:`ReflectionEngine.reflect`.

    The TickPayload is the agreed input shape for the engine; the
    sandbox loop builds one from its in-memory state per call. The
    ``narrative`` field carries the trigger label + market hint so the
    rich LLM prompt picks up the cadence cue.
    """
    market_hint = market_id or "no_market"
    narrative = (
        f"sandbox tick {tick} reflection trigger={trigger} "
        f"market={market_hint}"
    )
    return TickPayload(
        tick=tick,
        ts=_iso_utc(now),
        agent_id=agent_id,
        phase=phase,
        vitals=vitals,
        weights=weights,
        action=action,
        narrative=narrative,
    )


#: Valid keys for the L3 advisor's ``proposed_change`` payload on
#: ``kind="weight_delta"`` rows. Frozen as a :class:`frozenset` so an
#: unknown key short-circuits the fail-soft branch in O(1). Projected
#: from :data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS` (the
#: same 6 canonical fusion parameters L2 reflection emits) so this
#: module + reflection + the strategy_prompts renderer share one
#: vocabulary — a future addition to the L2 set automatically widens
#: the L3 apply surface.
_WEIGHT_DELTA_KEYS: Final[frozenset[str]] = frozenset(REFLECTION_WEIGHT_KEYS)


def _apply_weight_delta(weights: Weights, delta: WeightDelta) -> Weights:
    """Apply one ``{"key": str, "delta": float}`` payload to a :class:`Weights` snapshot.

    Mirrors the L3 advisor prompt contract in
    :mod:`agent.engines._strategy_prompts` (the prompt itself documents
    "the runtime renormalises but penalises proposals that drift far"):

    * ``key`` ∈ :data:`_WEIGHT_DELTA_KEYS`;
    * ``delta`` is a finite float (NaN / Inf rejected);
    * the result is clamped + renormalised so the returned
      :class:`Weights` always passes the normalised-sum validator.

    Renormalisation rules per axis:

    * ``w_r`` — bump w_r, clamp to [0, 1], set w_s = 1 - w_r.
    * ``alpha_i`` (i ∈ {0,1,2}) — bump alpha[i], clamp the bumped
      component to ≥ 0, then divide all three components by the new
      sum so they re-normalise to 1.0. Degenerate sum-to-zero collapses
      to the uniform 1/3 prior.
    * ``beta_0`` — bump beta[0], clamp to [0, 1], set beta[1] = 1 - beta[0].
    * ``rho``   — bump rho, clamp to [-1, 1].

    Re-validation is via the :class:`Weights` constructor (NOT
    ``model_copy``) so the model_validator's normalised-sum check
    runs as a defence-in-depth on the renormalised values — a future
    refactor that drops the clamp would surface as a ValueError here
    instead of a silent persist.

    Raises :class:`ValueError` on:

    * unknown / non-string ``key``;
    * non-numeric / NaN / Inf ``delta``;
    * any post-renormalisation result that fails Pydantic validation
      (should be unreachable given the clamps above, but the construct
      defends it).
    """
    raw_key = delta.get("key")
    if not isinstance(raw_key, str) or raw_key not in _WEIGHT_DELTA_KEYS:
        raise ValueError(
            f"weight delta key must be one of {sorted(_WEIGHT_DELTA_KEYS)} "
            f"(got {raw_key!r})"
        )
    raw_amount = delta.get("delta")
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)):
        raise ValueError(
            f"weight delta amount must be numeric (got {type(raw_amount).__name__})"
        )
    amount = float(raw_amount)
    if math.isnan(amount) or math.isinf(amount):
        raise ValueError("weight delta amount must be finite")

    if raw_key == "w_r":
        new_w_r = max(0.0, min(1.0, weights.w_r + amount))
        return Weights(
            w_r=new_w_r,
            w_s=1.0 - new_w_r,
            alpha=list(weights.alpha),
            beta=list(weights.beta),
            rho=weights.rho,
        )
    if raw_key in ("alpha_0", "alpha_1", "alpha_2"):
        idx = int(raw_key.split("_")[1])
        new_alpha = list(weights.alpha)
        new_alpha[idx] = max(0.0, new_alpha[idx] + amount)
        total = sum(new_alpha)
        if total <= 0.0:
            # Sum-to-zero would have been caught by the clamp above for
            # a single-axis bump, but a future multi-key composite call
            # could land here — collapse to uniform prior.
            new_alpha = [1.0 / 3.0] * 3
        else:
            new_alpha = [a / total for a in new_alpha]
        return Weights(
            w_r=weights.w_r,
            w_s=weights.w_s,
            alpha=new_alpha,
            beta=list(weights.beta),
            rho=weights.rho,
        )
    if raw_key == "beta_0":
        new_beta_0 = max(0.0, min(1.0, weights.beta[0] + amount))
        return Weights(
            w_r=weights.w_r,
            w_s=weights.w_s,
            alpha=list(weights.alpha),
            beta=[new_beta_0, 1.0 - new_beta_0],
            rho=weights.rho,
        )
    # raw_key == "rho" (the only remaining valid key per the guard above).
    new_rho = max(-1.0, min(1.0, weights.rho + amount))
    return Weights(
        w_r=weights.w_r,
        w_s=weights.w_s,
        alpha=list(weights.alpha),
        beta=list(weights.beta),
        rho=new_rho,
    )


def _fold_pending_proposals_from_jsonl(path: Path) -> list[str]:
    """Latest-status-wins fold of ``proposals.jsonl`` → list of pending ids.

    Mirrors the open-bets latest-status-wins pattern in
    :meth:`SandboxPhase2Loop._reconstruct_from_disk` step 2: walk the
    JSONL left-to-right, remember insertion order, keep the LATEST
    status per ``proposal_id``, retain only rows whose final status is
    ``"pending"``.

    Missing OR empty file ⇒ ``[]`` (cold-start / no advisor activity).
    Corrupt JSON lines are skipped silently by :func:`iter_jsonl`.
    Rows without a ``proposal_id`` (or a non-string id) are skipped —
    a malformed line cannot fold into a coherent id. Rows without a
    ``status`` key (e.g. pre-T-B-030 sprint_9 lines that did not stamp
    the field) default to ``"pending"`` so the fold round-trips the
    existing on-disk corpus without a migration.

    Returns the pending ids in INSERTION order (first-emit-wins for
    position; latest-update-wins for status). This is what the
    dashboard pending-proposals panel renders top-down — oldest pending
    at the top, newest at the bottom — matching the T-D-010 design.
    """
    # Python dict preserves insertion order (PEP 468 / 3.7+), so
    # ``latest_status`` already records the first-seen order — no
    # parallel list needed. The dict-only fold cuts memory + matches
    # the open-bets pattern (which is also order-preserving).
    latest_status: dict[str, str] = {}
    for row in iter_jsonl(path):
        pid_raw = row.get("proposal_id")
        if not isinstance(pid_raw, str) or not pid_raw:
            continue
        status_raw = row.get("status")
        status = (
            status_raw
            if isinstance(status_raw, str) and status_raw
            else PROPOSAL_STATUS_PENDING
        )
        latest_status[pid_raw] = status
    return [
        pid for pid, status in latest_status.items()
        if status == PROPOSAL_STATUS_PENDING
    ]


def _sha256_hex_prefixed(payload: str) -> str:
    """``0x``-prefixed SHA-256 hex digest of ``payload``.

    Used by :meth:`SandboxPhase2Loop._die` to produce the
    ``final_weights_hash`` Tombstone metadata field. The ``0x`` prefix
    is the **Python ↔ web3 boundary** convention (``web3.py``,
    ``eth_abi``, ``ethers.js`` accept and prefer ``0x``-prefixed hex
    for ``bytes32`` args; a bare 64-char hex string is sometimes
    misread as UTF-8). The EVM ``bytes32`` slot itself is 32 raw
    bytes — the chain adapter strips the prefix before encoding.
    """
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return "0x" + digest


__all__ = [
    "DEFAULT_AGENT_ID",
    "DEFAULT_DECISION_CADENCE",
    "DEFAULT_MEMORY_BANK_CID_PLACEHOLDER",
    "DEFAULT_REFLECTION_TICK_INTERVAL",
    "DEFAULT_REFLECTION_WEIGHT_DELTA_THRESHOLD",
    "DEFAULT_STRATEGY_ADVISOR_STABILITY_THRESHOLD",
    "DEFAULT_STRATEGY_ADVISOR_STABILITY_WINDOW",
    "DEFAULT_STRATEGY_ADVISOR_TICK_INTERVAL",
    "REAL_REFLECTION_ENV_VAR",
    "REFLECTION_PER_CALL_USD_EST",
    "REFLECTION_PNL_WINDOW",
    "REFLECTION_TICK_INTERVAL_ENV_VAR",
    "REFLECTION_WEIGHT_DELTA_THRESHOLD_ENV_VAR",
    "SANDBOX_FORCE_TERMINAL_ENV_VAR",
    "SETTLEMENT_POLL_CADENCE",
    "DeathReceipt",
    "ReconstructedState",
    "RunSummary",
    "SandboxLoopChainAdapter",
    "SandboxPhase2Loop",
    "TickInputSource",
    "TickInputs",
    "TickResult",
    "WeightUpdaterPhase",
]
