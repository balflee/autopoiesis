"""L3 meta-optimizer interface scaffold — T-B-025 sprint_9.

Spec anchors
------------

* PRD §4.6: "Reflection 是 L2; 上方还有 L3 — agent 看自己一段时间表现,
  提议策略修改 (L3, sprint 10 实施). L3 提议必须人审."
* TECHNICAL_PLAN §4.2 (权重学习): "weight_updater 是 L0 (在线 SGD). 之上
  L1 sentiment, L2 reflection, L3 meta-optimizer 计划在 sprint 10. L3 接口
  预留: StrategyAdvisor Protocol + StrategyProposal Pydantic."
* TECHNICAL_PLAN §4.4 (Reflection Engine): "Reflection 输出消耗者:
  dashboard (人看), L3 meta-optimizer (机器看 — 从 reflections.jsonl +
  decisions.jsonl 拼出 PerformanceWindow 输入)."

What this module ships (sprint_9 scaffold)
------------------------------------------

* :class:`StrategyAdvisor` — :class:`typing.Protocol` with the single
  :meth:`review_window` method. Sprint_10 swaps in the real LLM-backed
  advisor by writing a concrete class that satisfies this Protocol;
  nothing else needs to change.

* :class:`NoOpStrategyAdvisor` — the production default for sprint_9.
  Always returns ``[]`` (no proposals). The sandbox loop's L3 trigger
  pathway still fires (so the dashboard sees the cadence), but no
  rows land in ``proposals.jsonl`` until sprint_10 wires the real
  advisor. The "swap test" in
  :mod:`tests.agent.engines.test_strategy_advisor_scaffold` proves a
  drop-in replacement WORKS without touching loop code.

* :class:`PerformanceWindow` — the input bundle the
  :class:`SandboxPhase2Loop` hands to :meth:`review_window`. Carries
  enough scalar context that a future LLM advisor can render a
  prompt without reaching back into the loop's private state.

Architectural posture
---------------------

The advisor is a **synchronous** Protocol, not ``async``. Sprint_10's
real implementation will be LLM-backed and therefore want async; the
trade-off is locked in favour of the scaffold being trivially swappable
under sandbox harness tests (no event-loop plumbing). When sprint_10
needs async, the swap is a Protocol bump (NOT a wire bump — the
:class:`StrategyProposal` schema stays v0.1.0). The
:class:`SandboxPhase2Loop` calls :meth:`review_window` from inside an
``async def _tick`` body so a future async Protocol version slots in
with ``await`` at one line.
"""

from __future__ import annotations

from typing import Protocol

from agent.engines._performance_window import PerformanceWindow
from agent.engines._strategy_proposal_schema import StrategyProposal

# ``PerformanceWindow`` moved to :mod:`agent.engines._performance_window` in
# sprint_10 T-B-029 so the L3 advisor can extend it with slow-loop history
# fields (recent_pnl / weight_trajectory / recent_reflections / tick_count)
# WITHOUT a Protocol or wire-schema bump. Sprint_9 call sites (the sandbox
# loop, the scaffold tests) keep importing ``PerformanceWindow`` from THIS
# module via the re-export below; the dataclass is identical at the old
# field set (additive enrichment with defaults). See the T-B-029 module
# docstring for the migration rationale.


class StrategyAdvisor(Protocol):
    """L3 meta-optimizer interface — sprint_10 swaps in the real LLM.

    Single method :meth:`review_window`. The loop calls it on the L3
    trigger boundary (M=100 ticks OR 20 consecutive stable ticks,
    whichever first — see :class:`SandboxPhase2Loop._strategy_advisor_trigger`)
    and appends every returned :class:`StrategyProposal` to
    ``state/sandbox/proposals.jsonl``.

    Implementations
    ---------------

    Sprint_9 ships only :class:`NoOpStrategyAdvisor`; sprint_10 will
    add a concrete LLM-backed implementation that:

    1. Tail-reads ``state/sandbox/reflections.jsonl`` +
       ``decisions.jsonl`` for the window content.
    2. Renders the input as a prompt + calls Gemini with
       :class:`StrategyProposal` as the ``response_schema``.
    3. Returns the parsed proposals.

    The Protocol is **synchronous** for the scaffold (test ergonomics);
    a sprint_10 async swap is a one-line ``await`` change in the loop
    plus a Protocol bump (the :class:`StrategyProposal` wire schema
    stays v0.1.0).
    """

    def review_window(
        self,
        window: PerformanceWindow,
    ) -> list[StrategyProposal]:
        """Review a performance window; return 0..N proposals.

        Implementations MUST be idempotent w.r.t. the input — calling
        :meth:`review_window` twice with the same window MUST return
        equivalent output (the loop relies on this to retry the advice
        cycle if the JSONL append fails). The Phase-1
        :class:`NoOpStrategyAdvisor` is trivially idempotent (always
        returns ``[]``).

        Sprint_10's LLM-backed advisor will achieve idempotency via
        provider-side caching keyed on ``window.agent_id`` +
        ``window.tick``.
        """
        ...


class NoOpStrategyAdvisor:
    """Production-default Phase-1 advisor — always returns ``[]``.

    Sprint_9 scaffold posture: the L3 trigger pathway fires on the
    locked cadence (M=100 ticks OR 20-consecutive-stable, whichever
    first), the loop calls :meth:`review_window`, and gets an empty
    list back. No proposals land in ``proposals.jsonl``,
    ``pending_proposals`` stays empty, the dashboard renders the
    "L3 inactive" badge.

    Sprint_10 will replace this default with a concrete LLM-backed
    advisor by passing ``strategy_advisor=GeminiStrategyAdvisor(...)``
    to the :class:`SandboxPhase2Loop` constructor. The
    :class:`StrategyAdvisor` Protocol guarantees the swap is one-line.

    Test fixture role
    -----------------

    The class doubles as the baseline for the "swap test" in
    :mod:`tests.agent.engines.test_strategy_advisor_scaffold`: the test
    replaces this with a stub that returns 1 proposal and asserts
    JSONL grows + ``pending_proposals`` carries the id — proving the
    sprint_10 wiring will work without further loop changes.
    """

    def review_window(
        self,
        window: PerformanceWindow,
    ) -> list[StrategyProposal]:
        """Always return ``[]`` — no proposals from the no-op advisor.

        ``window`` is accepted but intentionally unused; the parameter
        is on the signature so :class:`NoOpStrategyAdvisor` structurally
        satisfies the :class:`StrategyAdvisor` Protocol (which mypy
        verifies at the call site, not at the class-definition site).
        """
        del window  # explicitly unused — Protocol conformance only
        return []


__all__ = [
    "NoOpStrategyAdvisor",
    "PerformanceWindow",
    "StrategyAdvisor",
    "StrategyProposal",
]
