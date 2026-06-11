"""Real L3 advisor — sprint_10 T-B-029.

Replaces sprint_9's :class:`agent.engines.strategy_advisor.NoOpStrategyAdvisor`
with a concrete Gemini-backed implementation. Reads a
:class:`PerformanceWindow`, renders the locked
:data:`agent.engines._strategy_prompts.SYSTEM_PROMPT` +
:func:`agent.engines._strategy_prompts.render_user_prompt`, calls Gemini in
structured-JSON mode via the SDK-agnostic ``_LLMClient`` Protocol, and
returns 0..:data:`MAX_PROPOSALS_PER_CALL` :class:`StrategyProposal`
records. Everything that can fail fails-soft: malformed JSON, missing
keys, exhausted budget, or an outright SDK exception all collapse to
an empty list + a structured WARNING log so the L3 trigger pathway
never crashes the agent.

Spec anchors
------------

* PRD §4.6 (L3 meta-optimizer + per-tick narrative).
* TECHNICAL_PLAN §4.4 (Reflection Engine slow loop).
* track-b-backend.md Rule 7 (production LLM = Gemini 3.5 Flash
  via google-genai SDK; **no ``anthropic`` / ``openai`` import**).

Sync-from-async bridge
----------------------

The :class:`agent.engines.strategy_advisor.StrategyAdvisor` Protocol is
**synchronous** (sprint_9 ergonomic choice — the loop calls
``review_window`` from inside ``async def _tick`` so the sync call
imposes zero plumbing). The Gemini client's ``structured_call`` is
**async** (sprint_4 T-B-006 wired ``google-genai`` aio). This impl
bridges the two via :meth:`_run_async`:

* Tests (pytest, no event loop) → :func:`asyncio.run` directly.
* Production (inside :class:`SandboxPhase2Loop._tick`, event loop
  running) → submit the coroutine to a single-shot
  :class:`concurrent.futures.ThreadPoolExecutor` that owns its own
  loop. Returns the result on completion or raises whatever the
  coroutine raised.

The thread bridge is deliberate: ``asyncio.run`` raises
:class:`RuntimeError` when called from inside a running loop, so we
detect that case via :func:`asyncio.get_running_loop` and route
through a worker thread. The worker thread's loop is independent of
the loop that called us; it holds no shared aio resources. The
:class:`agent.llm.gemini_client.GeminiClient` builds its
``google.genai.Client`` lazily on first call, so a fresh
``GeminiClient`` instance per advisor is NOT required for correctness
— the SDK's underlying transport is thread-safe per its docs.

Cost accounting
---------------

Each successful Gemini call is recorded against the injected
:class:`L3CostGuard` (separate budget from the L1/L2 shared
:class:`CostGuard`, per the T-B-029 brief). Per-call cost estimate is
:data:`_L3_PER_CALL_USD` — slightly higher than the L1/L2 per-call
cost because the L3 prompt is larger (carries the recent reflections,
which themselves can be paragraphs). Exhaustion BEFORE the call ⇒
short-circuit to ``[]`` + WARNING log. Exhaustion DURING the call (the
record path) is impossible by construction — the pre-check fires first.

Look-ahead bias documentation
-----------------------------

The advisor reads ONLY :class:`PerformanceWindow` fields (which are
themselves built from JSONL streams that contain historical lines
only) plus its own ``datetime.now(UTC)`` clock read for the
:class:`StrategyProposal.ts` stamp. No future-tick data is fetched.
The look-ahead auditor scans ``agent/engines/features*`` /
``agent/training/**``; this module is excluded by directory shape.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from agent.engines._performance_window import PerformanceWindow
from agent.engines._strategy_prompts import (
    MAX_PROPOSALS_PER_CALL,
    PROPOSAL_KINDS,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    WEIGHT_DELTA_KEYS,
    WEIGHT_DELTA_MAX_ABS,
    WEIGHT_DELTA_RESPONSE_SCHEMA,
    WEIGHT_DELTA_SYSTEM_PROMPT,
    render_user_prompt,
)
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.llm.cost_guard import L3CostGuard
from agent.llm.gemini_client import DEFAULT_GEMINI_MODEL

logger = logging.getLogger(__name__)

# Per-call USD estimate for the L3 advisor's Gemini call. Slightly
# higher than the L1/L2 per-call cost because the prompt embeds the
# recent reflections (paragraph-sized). Calibrated against the T-B-022
# smoke run (~$0.0015 / L1 call at ~600 input tokens); L3 averages
# ~3-4x that on input tokens (~2-3k including reflections).
_L3_PER_CALL_USD: Final[float] = 0.006


class _LLMClient(Protocol):
    """Narrow SDK-agnostic protocol — same shape as
    :class:`agent.engines.sentiment_llm._LLMClient` and
    :class:`agent.engines.reflection._LLMClient`. Production:
    :class:`agent.llm.gemini_client.GeminiClient`. Tests inject a
    Protocol-conformant fake (e.g.
    :class:`tests.agent.llm.conftest.FakeGeminiClient`).
    """

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class StrategyAdvisorImpl:
    """Real Gemini-backed L3 meta-optimizer (sprint_10 T-B-029).

    Drop-in replacement for
    :class:`agent.engines.strategy_advisor.NoOpStrategyAdvisor`. Plug
    via ``SandboxPhase2Loop(strategy_advisor=StrategyAdvisorImpl(...))``;
    the L3 trigger pathway, JSONL append, and pending-proposals tracking
    already exist in the loop from sprint_9 T-B-025.

    Parameters
    ----------
    llm_client:
        Protocol-conformant ``_LLMClient``. Production:
        :class:`agent.llm.gemini_client.GeminiClient`. Tests inject a
        fake; no real Gemini call under pytest.

    cost_guard:
        :class:`L3CostGuard` enforcing the separate L3 budget cap (env
        ``L3_MONTHLY_BUDGET_USD``, default $10). When tripped (i.e.
        :meth:`CostGuard.is_exhausted` returns True), :meth:`review_window`
        SHORT-CIRCUITS to ``[]`` + WARNING log BEFORE the LLM call.

    model:
        Gemini model id; defaults to :data:`DEFAULT_GEMINI_MODEL`
        (``gemini-3.5-flash``). The L3 advisor's slow cadence
        (~every 100 ticks) means its latency is acceptable;
        sprint_11+ may calibrate to a heavier model for big-window
        reviews.

    per_call_usd_estimate:
        USD cost to record against ``cost_guard`` per successful call.
        Defaults to :data:`_L3_PER_CALL_USD`; tests can pass a smaller
        figure to fit a tighter budget cap.

    weight_delta_only:
        Opt-in STRICT mode for the survival self-evolution simulation
        (T-D-018). When True, :meth:`_review_window_async` renders
        :data:`WEIGHT_DELTA_SYSTEM_PROMPT` + passes
        :data:`WEIGHT_DELTA_RESPONSE_SCHEMA` (single ``weight_delta``
        kind, strict ``proposed_change``), and :meth:`_build_proposal`
        LOCALLY ENFORCES that every item is a ``weight_delta`` carrying
        ``{"key": <one of the 6>, "delta": <float, |delta| <= 0.1>}`` —
        any non-conforming item is dropped, never auto-approved. This is
        the binding guarantee (the provider schema is only a hint, esp.
        MiniMax). Default False = the byte-unchanged prod path (all 3
        kinds, loose ``proposed_change`` for the human-review queue).

    Notes
    -----
    The class deliberately exposes :meth:`review_window` as the only
    public method to satisfy the
    :class:`agent.engines.strategy_advisor.StrategyAdvisor` Protocol
    contract. The async helper :meth:`_review_window_async` is the unit
    of work the sync entrypoint bridges to; it stays underscored so
    callers can't bypass the sync interface (and the swap test in
    :mod:`tests.agent.engines.test_strategy_advisor_scaffold` keeps
    working).
    """

    def __init__(
        self,
        *,
        llm_client: _LLMClient,
        cost_guard: L3CostGuard,
        model: str = DEFAULT_GEMINI_MODEL,
        per_call_usd_estimate: float = _L3_PER_CALL_USD,
        weight_delta_only: bool = False,
    ) -> None:
        self._llm = llm_client
        self._cost_guard = cost_guard
        self._model = model
        if per_call_usd_estimate < 0.0:
            raise ValueError(
                "per_call_usd_estimate must be non-negative "
                f"(got {per_call_usd_estimate})"
            )
        self._per_call_usd = per_call_usd_estimate
        self._weight_delta_only = weight_delta_only

    # ------------------------------------------------------------------
    # Public Protocol method — sync entrypoint.
    # ------------------------------------------------------------------

    def review_window(
        self,
        window: PerformanceWindow,
    ) -> list[StrategyProposal]:
        """Review a performance window; return 0..N proposals.

        Sync entrypoint to satisfy the
        :class:`agent.engines.strategy_advisor.StrategyAdvisor` Protocol.
        Bridges to the async LLM via :meth:`_run_async`.

        Hardened return contract (all roads lead to ``list[StrategyProposal]``):

        * Cost guard exhausted at entry      → ``[]`` + WARNING log.
        * LLM raises any exception           → ``[]`` + WARNING log.
        * LLM returns malformed JSON / wrong shape → ``[]`` + WARNING log.
        * LLM returns more than
          :data:`MAX_PROPOSALS_PER_CALL`     → tail-trim to the cap.
        * Per-proposal Pydantic validation fails → that proposal dropped,
          remaining valid ones returned (defence-in-depth against
          partial schema drift).

        The fail-soft posture mirrors :class:`ReflectionEngine.reflect`
        and the loop's own exception handler in
        :meth:`SandboxPhase2Loop._run_strategy_advice` (which ALSO
        catches any exception we let escape — double-defence).
        """
        # ── 1. Cost-guard precheck (cheapest path). ────────────────────
        if self._cost_guard.is_exhausted():
            logger.warning(
                "strategy_advisor_impl: L3 cost guard exhausted "
                "(total=$%.4f cap=$%.4f) — returning [] without LLM call",
                self._cost_guard.total_usd,
                self._cost_guard.hard_cap_usd,
            )
            return []

        # ── 2. Bridge sync → async; capture LLM result OR exception. ───
        try:
            raw = self._run_async(self._review_window_async(window))
        except Exception as exc:
            logger.warning(
                "strategy_advisor_impl: LLM call raised %s: %s — returning []",
                type(exc).__name__,
                exc,
            )
            return []

        try:
            proposals = self._parse_response(raw)
        except Exception as exc:
            logger.warning(
                "strategy_advisor_impl: response parse failed (%s: %s) "
                "— returning [] (raw=%r)",
                type(exc).__name__,
                exc,
                _safe_repr(raw),
            )
            return []

        # Cap at MAX_PROPOSALS_PER_CALL — defensive against models that
        # overshoot the schema's maxItems constraint.
        if len(proposals) > MAX_PROPOSALS_PER_CALL:
            logger.warning(
                "strategy_advisor_impl: model returned %d proposals "
                "(cap=%d) — trimming",
                len(proposals),
                MAX_PROPOSALS_PER_CALL,
            )
            proposals = proposals[:MAX_PROPOSALS_PER_CALL]

        # Record AFTER successful parse so a malformed response does NOT
        # charge the budget — keeps a bug-and-retry loop from draining
        # the cap. The precheck above guarantees the guard is not
        # exhausted at entry; ``record()`` therefore cannot raise.
        self._cost_guard.record(label="l3_advisor", usd=self._per_call_usd)
        return proposals

    # ------------------------------------------------------------------
    # Async core — single LLM call, no parsing.
    # ------------------------------------------------------------------

    async def _review_window_async(
        self,
        window: PerformanceWindow,
    ) -> dict[str, Any]:
        """Render the prompt and call Gemini once.

        Returns the raw response dict. Caller (:meth:`review_window`)
        owns parsing + validation + cost recording. Splitting the call
        from parsing keeps the test-injected fake LLM trivial — fakes
        return a dict, the impl handles everything else.
        """
        # Strict survival-sim mode swaps in the weight_delta-only prompt +
        # schema so a real LLM is steered to ALWAYS emit a filled, applicable
        # proposed_change; default mode keeps the prod 3-kind contract.
        if self._weight_delta_only:
            system_prompt = WEIGHT_DELTA_SYSTEM_PROMPT
            schema = WEIGHT_DELTA_RESPONSE_SCHEMA
        else:
            system_prompt = SYSTEM_PROMPT
            schema = RESPONSE_SCHEMA
        user_prompt = render_user_prompt(window)
        full_prompt = system_prompt + "\n\n" + user_prompt
        return await self._llm.structured_call(
            model=self._model,
            prompt=full_prompt,
            schema=schema,
        )

    # ------------------------------------------------------------------
    # Parse — dict → list[StrategyProposal] (validates per-item).
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        raw: dict[str, Any],
    ) -> list[StrategyProposal]:
        """Walk the wrapper dict's ``proposals`` list -> :class:`StrategyProposal`.

        Per-item failure (bad kind, missing field, wrong type) drops
        that item but keeps the rest. The wrapper-level failure modes
        (``proposals`` not a list / not present) raise — caller's
        try/except collapses to ``[]`` + WARNING.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"response root not a dict (got {type(raw).__name__})")
        items = raw.get("proposals")
        if not isinstance(items, list):
            raise ValueError(
                f"response missing 'proposals' list "
                f"(got {type(items).__name__ if items is not None else 'None'})"
            )

        now = datetime.now(UTC)
        out: list[StrategyProposal] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                logger.warning(
                    "strategy_advisor_impl: proposal[%d] not a dict (got %s) — skipping",
                    idx,
                    type(item).__name__,
                )
                continue
            try:
                proposal = self._build_proposal(
                    item, now=now, weight_delta_only=self._weight_delta_only
                )
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning(
                    "strategy_advisor_impl: proposal[%d] failed validation "
                    "(%s: %s) — skipping (item=%r)",
                    idx,
                    type(exc).__name__,
                    exc,
                    _safe_repr(item),
                )
                continue
            out.append(proposal)
        return out

    @staticmethod
    def _build_proposal(
        item: dict[str, Any],
        *,
        now: datetime,
        weight_delta_only: bool = False,
    ) -> StrategyProposal:
        """Project one wrapper item into a full :class:`StrategyProposal`.

        Injects the runtime-side fields that the LLM does NOT produce:

        * ``proposal_id`` — UUID4 hex (idempotency-friendly enough for
          dashboard React keys; cross-call idempotency is the loop's
          job).
        * ``ts``                       — ``datetime.now(UTC)`` snapshot
          at call time (passed in so all proposals from one window
          share a timestamp).
        * ``requires_human_approval``  — locked True per PRD §4.6.

        When ``weight_delta_only`` is set (the survival-sim strict mode),
        the item is additionally enforced to be a ``weight_delta`` whose
        ``proposed_change`` is exactly ``{"key": <one of the 6>, "delta":
        <finite float, |delta| <= 0.1>}`` — any other kind, a missing /
        unknown key, or a non-numeric / out-of-range delta raises
        :class:`ValueError`, so the caller's per-item ``try/except`` DROPS
        it. This is the binding guarantee that a loose / empty
        ``proposed_change`` from a real LLM (the provider schema is only a
        hint) can never reach the auto-approve queue.
        """
        kind = item.get("kind")
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"unknown kind {kind!r} (expected one of {PROPOSAL_KINDS})")
        if weight_delta_only and kind != "weight_delta":
            # Strict-mode kind allow-list: a real model can still emit a
            # non-weight_delta item (the provider schema is only a hint), so
            # reject it here → the caller skips it. Scoped to strict mode only;
            # the prod 3-kind contract is untouched.
            raise ValueError(
                "strict weight_delta_only mode: kind must be 'weight_delta' "
                f"(got {kind!r})"
            )
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("rationale must be a non-empty string")
        proposed_change_raw = item.get("proposed_change", {})
        if not isinstance(proposed_change_raw, dict):
            raise ValueError(
                f"proposed_change must be a dict (got {type(proposed_change_raw).__name__})"
            )
        # Stash a typed copy so Pydantic's ``dict[str, Any]`` field
        # accepts it without further coercion.
        proposed_change: dict[str, Any] = dict(proposed_change_raw)
        if weight_delta_only:
            # kind is guaranteed "weight_delta" here. Enforce the structured
            # {key, delta} payload locally — the binding guarantee, independent
            # of provider. Mirror the runtime's key/type/finite checks
            # (sandbox_phase2_loop._apply_weight_delta) and ADD the inclusive
            # magnitude bound (the runtime does not bound magnitude).
            key = proposed_change.get("key")
            if not isinstance(key, str) or key not in WEIGHT_DELTA_KEYS:
                raise ValueError(
                    "weight_delta proposed_change.key must be one of "
                    f"{list(WEIGHT_DELTA_KEYS)} (got {key!r})"
                )
            raw_delta = proposed_change.get("delta")
            # Reject bool (a bool IS an int in Python); accept int | float.
            if isinstance(raw_delta, bool) or not isinstance(raw_delta, (int, float)):
                raise ValueError(
                    "weight_delta proposed_change.delta must be numeric "
                    f"(got {type(raw_delta).__name__})"
                )
            delta = float(raw_delta)
            if not math.isfinite(delta):
                raise ValueError("weight_delta proposed_change.delta must be finite")
            if abs(delta) > WEIGHT_DELTA_MAX_ABS:
                raise ValueError(
                    "weight_delta proposed_change.delta out of range "
                    f"(|{delta}| > {WEIGHT_DELTA_MAX_ABS})"
                )
            # Persist the coerced float so a schema-valid integer literal (JSON
            # "type":"number" permits it) reaches the runtime as a float.
            proposed_change["key"] = key
            proposed_change["delta"] = delta
        expected_impact = item.get("expected_impact")
        if expected_impact is not None and not isinstance(expected_impact, str):
            raise ValueError(
                f"expected_impact must be str or None "
                f"(got {type(expected_impact).__name__})"
            )
        confidence_raw = item.get("confidence_pct")
        if not isinstance(confidence_raw, (int, float)) or isinstance(
            confidence_raw, bool
        ):
            raise ValueError(
                f"confidence_pct must be a number (got "
                f"{type(confidence_raw).__name__})"
            )
        confidence_pct = round(float(confidence_raw))
        # Pydantic enforces 0..100; we coerce-and-defer.
        return StrategyProposal(
            proposal_id=uuid.uuid4().hex,
            ts=now,
            kind=kind,
            rationale=rationale,
            proposed_change=proposed_change,
            expected_impact=expected_impact if isinstance(expected_impact, str) else None,
            confidence_pct=confidence_pct,
            requires_human_approval=True,
        )

    # ------------------------------------------------------------------
    # Sync → async bridge.
    # ------------------------------------------------------------------

    @staticmethod
    def _run_async(coro: Any) -> Any:
        """Run ``coro`` to completion, regardless of caller's loop state.

        * No event loop running (pytest, repl)         → :func:`asyncio.run`.
        * Event loop running (production async loop)  → submit to a
          single-shot :class:`concurrent.futures.ThreadPoolExecutor`
          so we don't clash with the parent loop.

        The thread bridge is the standard sync-from-async pattern when
        the caller's loop is the wrong place to ``await`` (e.g. we're
        inside a sync method called from inside an async function).
        The worker thread owns its own event loop via
        :func:`asyncio.run` so we don't have to manage one explicitly.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop — synchronous caller, can run directly.
            return asyncio.run(coro)

        # We're inside a running loop — submit to a worker thread that
        # creates its own loop via asyncio.run. The worker is single-
        # shot (with statement) so the executor cleans up on return.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


# --------------------------------------------------------------------------- #
# Tiny helpers.
# --------------------------------------------------------------------------- #


def _safe_repr(obj: Any) -> str:
    """Best-effort short repr for log messages — guards against giant payloads."""
    try:
        text = json.dumps(obj, default=repr)
    except (TypeError, ValueError):
        text = repr(obj)
    if len(text) > 400:
        return text[:200] + "...[truncated]..." + text[-100:]
    return text


__all__ = [
    "StrategyAdvisorImpl",
]
