# Greek letters mirror PRD §4.1 notation.
"""Reflection engine — LLM-driven per-tick / per-event post-mortem.

T-B-003 ships the LLM-emitter half of PRD §4.4. Production LLM is
**Gemini 3.1 Flash Lite via google-genai SDK + AI Studio** (env var
``GEMINI_API_KEY``); the concrete client lands in sprint_4 T-B-006 as
``agent/llm/gemini_client.GeminiClient`` and is consumed via the
``_LLMClient`` Protocol below (no SDK import in this engine).

The reflection layer:

1. **Default model**: ``gemini-3.1-flash-lite`` per PRD §6 narrative
   cost budget. Cheap, good-enough for routine ticks.
2. **Key-moment escalation**: phase transitions, big losses, win streaks
   route to a heavier model (e.g. ``gemini-3.1-pro``) — wired via
   constructor input (``escalated_model``) and per-call ``key_moment``
   flag. No hard-coded mapping so calibration can tune thresholds.
3. **Structured output (HARD RULE)**: every call uses the LLM's
   structured-output mode (Gemini's ``response_schema``) with a
   Pydantic schema (PRD §6.4). Malformed → retry once → fail-soft
   to a deterministic template default. The reflection loop NEVER
   crashes the agent; a dead LLM is rendered as the
   ``rationale="llm_unreachable"`` reflection and the tick continues.
4. **Persistence**: ``ReflectionRecord`` is written to
   ``reflections_dir/<reflection_id>.md`` (atomic temp+rename). The
   returned record carries the relative path that
   :class:`TickPayload.reflection_ref` consumes.

Test discipline: the brief mandates **no real Gemini API call** under
pytest. The engine accepts a fake :class:`_LLMClient` (Protocol). Tests
inject a deterministic fake; ``conftest.py`` enforces it by leaving
``GEMINI_API_KEY`` (and ``ANTHROPIC_API_KEY``) unset.

Sprint follow-ups (NOT this task):
* IPFS pin of the reflection blob + on-chain hash anchor — lands when
  the chain adapter promotes from L3-stub to real L3 (sprint_4 T-B-006
  ships the LLM client; on-chain anchor is sprint_4 T-B-007).
* Reflection cadence selector (every Nth tick, post-loss, post-phase)
  — currently the agent_loop fires reflect() every tick.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.core.state import ActionKind, TickPayload

# Default model identifiers — match PRD §6 narrative cost budget.
DEFAULT_SONNET_MODEL: Final[str] = "claude-sonnet-4-6"
DEFAULT_OPUS_MODEL: Final[str] = "claude-opus-4-7"

# Maximum length of the reflection body written to disk — guards
# against a runaway LLM that emits an essay. PRD §6 says reflections
# are 1-3 paragraphs.
_MAX_REFLECTION_CHARS: Final[int] = 4_000


class _ReflectionResponse(BaseModel):
    """Structured-output schema for the reflection LLM call.

    Mirrors PRD §4.4 reflection blob shape. The reflection layer
    validates every LLM response against this schema; a
    :class:`ValidationError` triggers retry-once → fail-soft per the
    PRD §6.4 structured-output rule.
    """

    model_config = ConfigDict(extra="forbid")

    summary: Annotated[str, Field(min_length=1, max_length=_MAX_REFLECTION_CHARS)]
    lessons: list[str] = Field(default_factory=list)
    confidence_in_strategy: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5
    mood: str = "neutral"


_RESPONSE_SCHEMA: Final[dict[str, Any]] = _ReflectionResponse.model_json_schema()


@dataclass(frozen=True)
class ReflectionRecord:
    """Per-tick reflection bundle.

    Returned by :meth:`ReflectionEngine.reflect`. Carries:

    * ``tick`` — the source tick number.
    * ``model`` — which Claude model was actually used (Sonnet/Opus).
    * ``key_moment`` — caller's flag, persisted for replay/audit.
    * ``rationale`` — short tag describing the outcome path
      (``"ok"``, ``"retry_ok"``, ``"fail_soft_malformed"``,
      ``"fail_soft_unreachable"``).
    * ``body`` — full reflection text written to disk.
    * ``path_rel`` — relative path ``"reflections/<id>.md"`` shaped to
      slot directly into :attr:`TickPayload.reflection_ref`.
    * ``written_at`` — ISO-8601 UTC creation time.
    * ``lessons`` / ``confidence_in_strategy`` / ``mood`` — pass-through
      structured fields the dashboard renders.
    """

    tick: int
    model: str
    key_moment: bool
    rationale: str
    body: str
    path_rel: str
    written_at: str
    lessons: tuple[str, ...] = field(default_factory=tuple)
    confidence_in_strategy: float = 0.5
    mood: str = "neutral"


class _LLMClient(Protocol):
    """Narrow SDK-agnostic protocol for structured LLM calls.

    Identical shape to the sentiment_llm protocol — kept structural so
    a single concrete LLM client wrapper can serve both engines.
    Production implementation: ``agent/llm/gemini_client.GeminiClient``
    wrapping ``google-genai`` against Gemini 3.1 Flash Lite (sprint_4
    T-B-006). The single method returns the validated JSON payload from
    a structured-output (``response_schema``) call.
    """

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class ReflectionEngine:
    """Generates the per-tick reflection text.

    Construction parameters
    -----------------------

    ``llm_client``:
        The Claude wrapper. Required in non-test environments. Tests
        inject a deterministic fake (see ``tests/agent/engines/
        test_reflection.py``).

    ``reflections_dir``:
        Filesystem directory where the reflection MD files are
        written. Default ``.agent_state/memory_bank/reflections/``.

    ``sonnet_model`` / ``opus_model``:
        Override the default model identifiers — useful for canary
        rollouts.
    """

    name = "reflection"

    def __init__(
        self,
        *,
        llm_client: _LLMClient,
        reflections_dir: Path,
        sonnet_model: str = DEFAULT_SONNET_MODEL,
        opus_model: str = DEFAULT_OPUS_MODEL,
    ) -> None:
        self._llm = llm_client
        self._reflections_dir = Path(reflections_dir)
        self._sonnet_model = sonnet_model
        self._opus_model = opus_model

    async def reflect(
        self,
        *,
        tick: TickPayload,
        key_moment: bool = False,
    ) -> ReflectionRecord:
        """Generate + persist a reflection for ``tick``.

        Sequence:

        1. Pick model (Sonnet default, Opus when ``key_moment``).
        2. Build prompt from the TickPayload.
        3. Call the LLM; validate against :class:`_ReflectionResponse`.
        4. On ValidationError / ValueError / TypeError: retry ONCE.
        5. On second failure: fail-soft to deterministic template.
        6. Atomic temp+rename write to disk.
        7. Return :class:`ReflectionRecord`.

        The method NEVER raises — a dead LLM still produces a record
        (with ``rationale="fail_soft_unreachable"``) so the agent_loop
        can continue uninterrupted.
        """
        model = self._opus_model if key_moment else self._sonnet_model
        prompt = _build_prompt(tick=tick, key_moment=key_moment)

        parsed: _ReflectionResponse | None = None
        rationale = "ok"

        for attempt_idx in range(2):
            try:
                raw = await self._llm.structured_call(
                    model=model, prompt=prompt, schema=_RESPONSE_SCHEMA
                )
                parsed = _ReflectionResponse.model_validate(raw)
                if attempt_idx == 1:
                    rationale = "retry_ok"
                break
            except (ValidationError, ValueError, TypeError):
                if attempt_idx == 1:
                    parsed = None
                    rationale = "fail_soft_malformed"
            except Exception:
                # Catch-all for connection / timeout / client errors.
                # The agent_loop MUST NOT crash on LLM downtime — we
                # explicitly absorb everything here, tag the rationale,
                # and emit the deterministic fallback below.
                parsed = None
                rationale = "fail_soft_unreachable"
                break

        if parsed is None:
            parsed = _fallback_response(tick=tick, rationale=rationale)

        body = _format_body(tick=tick, response=parsed, model=model, rationale=rationale)
        reflection_id = _reflection_id(tick=tick.tick)
        path_rel = f"reflections/{reflection_id}.md"
        written_at = datetime.now(UTC).isoformat()

        try:
            _atomic_write(self._reflections_dir, f"{reflection_id}.md", body)
        except OSError:
            # Even disk failure shouldn't crash the tick; the in-memory
            # ReflectionRecord stays the source of truth.
            rationale = f"{rationale}+disk_failure"

        return ReflectionRecord(
            tick=tick.tick,
            model=model,
            key_moment=key_moment,
            rationale=rationale,
            body=body,
            path_rel=path_rel,
            written_at=written_at,
            lessons=tuple(parsed.lessons),
            confidence_in_strategy=parsed.confidence_in_strategy,
            mood=parsed.mood,
        )


# ---------------------------------------------------------------------------
# Helpers — module-level so tests can exercise the formatting / fallback
# behaviour without instantiating the engine + the LLM fake.
# ---------------------------------------------------------------------------


def _build_prompt(*, tick: TickPayload, key_moment: bool) -> str:
    """Build the reflection prompt. Bound here so the wire shape lives
    in one place. The prompt is a thin template — the agent's identity
    + memory_bank context will be injected by the lifecycle layer when
    the V2-boot reflection context lands (PRD §13)."""
    action = tick.action
    action_str = (
        f"BET {action.side} {action.size_usd} USD on {action.market_id}"
        if action.kind == ActionKind.BET
        else f"NO_BET ({action.no_bet_reason or 'unspecified'})"
    )
    cadence_hint = (
        "This is a KEY MOMENT — phase transition, big swing, or "
        "ritual event. Reflect carefully; this reflection will anchor "
        "future ticks."
        if key_moment
        else "Routine tick. Keep the reflection short — 1-2 sentences."
    )
    return (
        f"Tick {tick.tick} (phase={tick.phase.value}). "
        f"Vitals: breath={tick.vitals.breath:.0f}, "
        f"bankroll=${tick.vitals.bankroll_usd:,.2f}, "
        f"phase_age={tick.vitals.phase_age_days:.1f}d. "
        f"Action: {action_str}. {cadence_hint} "
        f"Return JSON {{summary, lessons[], confidence_in_strategy, mood}}."
    )


def _fallback_response(*, tick: TickPayload, rationale: str) -> _ReflectionResponse:
    """Deterministic template-based reflection used when the LLM is
    malformed or unreachable.

    PRD §6.4 fail-soft policy: keep the agent alive, surface the failure
    via the ``rationale`` tag the reflection record carries so the
    reviewer / dashboard can flag it without parsing free-form text.
    """
    action = tick.action
    summary = (
        f"Tick {tick.tick}: {action.kind.value} "
        + (
            f"({action.no_bet_reason or 'unspecified reason'})"
            if action.kind == ActionKind.NO_BET
            else f"on {action.market_id}"
        )
        + f". LLM unavailable ({rationale}); routine fallback emitted."
    )
    return _ReflectionResponse(
        summary=summary,
        lessons=["llm_unavailable"],
        confidence_in_strategy=0.5,
        mood="neutral",
    )


def _format_body(
    *,
    tick: TickPayload,
    response: _ReflectionResponse,
    model: str,
    rationale: str,
) -> str:
    """Render the reflection MD body the dashboard renders.

    Layout is intentionally human-readable so an operator can ``less``
    the file directly. The dashboard's PLAYBACK loader parses the
    YAML-ish frontmatter for the structured fields.
    """
    lines = [
        "---",
        f"tick: {tick.tick}",
        f"ts: {tick.ts}",
        f"phase: {tick.phase.value}",
        f"model: {model}",
        f"rationale: {rationale}",
        f"confidence_in_strategy: {response.confidence_in_strategy}",
        f"mood: {response.mood}",
        "---",
        "",
        response.summary.strip(),
    ]
    if response.lessons:
        lines.append("")
        lines.append("## Lessons")
        lines.extend(f"- {lesson}" for lesson in response.lessons)
    return "\n".join(lines) + "\n"


def _reflection_id(*, tick: int) -> str:
    """Stable, sortable reflection identifier."""
    return f"tick_{tick:07d}"


def _atomic_write(directory: Path, filename: str, body: str) -> Path:
    """Atomic temp+rename write — matches MemoryBank.write_tick semantics.

    Crash mid-write leaves the previous file intact + an orphan
    ``.tmp`` that the next boot can sweep.
    """
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / filename
    tmp = directory / f".{filename}.tmp"
    if tmp.exists():
        tmp.unlink()
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, final)
    return final


# ---------------------------------------------------------------------------
# Sandbox JSONL projection — T-B-024 L2 wire (sprint_9 Day 1).
#
# The :class:`ReflectionRecord` dataclass above is the engine-output bundle
# the :class:`ReflectionEngine` returns to its caller (carries the rich MD
# body + structured fields). ``SandboxReflectionRecord`` below is a thinner
# Pydantic projection — the wire shape the
# :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop` appends to
# ``state/sandbox/reflections.jsonl`` once per trigger fire.
#
# Naming rationale — T-B-024 brief asks for a Pydantic ``ReflectionRecord``
# but the existing dataclass with that exact name already serves the engine
# layer (and is re-exported through :mod:`agent.engines.__init__`).
# Renaming the dataclass would break sprint_3 T-B-003's lifecycle layer,
# the IPFS pinner docstring, and the engine test suite for zero functional
# gain. The Pydantic projection lives alongside as
# ``SandboxReflectionRecord`` — distinct concern, distinct name. The
# dashboard tail-consumer (T-D-010) reads JSONL lines that round-trip
# through THIS Pydantic model; the engine layer keeps consuming the
# dataclass. Per the brief's "interface contracts touched: None in
# cross-track sense" note, the naming divergence is runtime-private and
# does not affect ``.dev/contracts/`` schema registry.
# ---------------------------------------------------------------------------


# 6 canonical fusion weight identifiers — PRD §4.1 names the parameters as
# (W_R, α₁, α₂, α₃, β₁, ρ). The other components (w_s, β₂) are derived
# (w_s = 1 - W_R, β₂ = 1 - β₁) and so are NOT independently tracked for
# trigger purposes.
REFLECTION_WEIGHT_KEYS: Final[tuple[str, ...]] = (
    "w_r",
    "alpha_0",
    "alpha_1",
    "alpha_2",
    "beta_0",
    "rho",
)


class SandboxReflectionRecord(BaseModel):
    """Append-only JSONL wire shape for sandbox reflection emissions.

    Written by :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`
    to ``state/sandbox/reflections.jsonl`` once per trigger fire
    (tick_interval N=10 OR weight_delta > 0.05 across the 6 fusion
    weights, whichever first; see T-B-024 brief acceptance criteria).

    The dashboard (T-D-010 follow-up) tails this stream to render the
    consciousness feed. Field shapes:

    * ``reflection_id`` — UUID4 hex string. Stable identifier the dashboard
      uses as the React-list key + the link target if the operator wants
      to view the rich MD body the engine separately writes via
      :meth:`ReflectionEngine.reflect` to ``reflections_dir/``.
    * ``ts`` — ISO-8601 UTC timestamp at trigger time.
    * ``trigger`` — which condition fired. ``"tick_interval"`` =
      every N ticks; ``"weight_delta"`` = max |Δw| > threshold.
    * ``narrative`` — the LLM-produced 1-3 paragraph summary (body of
      the rich :class:`ReflectionRecord`). Bounded above by
      :data:`_MAX_REFLECTION_CHARS` so a runaway LLM cannot blow up the
      JSONL stream.
    * ``weight_snapshot`` — the 6 fusion weights at trigger time. Keyed
      by the canonical :data:`REFLECTION_WEIGHT_KEYS`; the dashboard can
      diff two consecutive snapshots to render the per-engine delta.
    * ``recent_pnl_window`` — net USD P&L over the last 50 settled bets
      (sum of ``settled_bets.jsonl``'s ``pnl_usd`` column, tail-50).
      Capped at last-50 for narrative window stability — a 24-hour run
      can produce hundreds of settlements but the dashboard's "what
      happened recently" panel only fits ~50.
    * ``llm_cost_usd`` — incremental USD cost attributable to THIS
      reflection's LLM call. Tracked via the shared
      :class:`agent.llm.cost_guard.CostGuard` so a single dashboard
      surface aggregates L1 sentiment + L2 reflection spending against
      the locked $25 monthly cap.
    """

    model_config = ConfigDict(extra="forbid")

    reflection_id: Annotated[str, Field(min_length=1)]
    ts: Annotated[str, Field(min_length=1)]
    trigger: Literal["tick_interval", "weight_delta"]
    narrative: Annotated[str, Field(min_length=1, max_length=_MAX_REFLECTION_CHARS)]
    weight_snapshot: dict[str, float]
    recent_pnl_window: float
    llm_cost_usd: Annotated[float, Field(ge=0.0)]


__all__ = [
    "DEFAULT_OPUS_MODEL",
    "DEFAULT_SONNET_MODEL",
    "REFLECTION_WEIGHT_KEYS",
    "ReflectionEngine",
    "ReflectionRecord",
    "SandboxReflectionRecord",
]
