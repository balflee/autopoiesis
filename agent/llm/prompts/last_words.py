# Chinese / Greek glyphs in module strings mirror PRD §5.1.B narrative
# notation. The string contents are the prompt text the agent sends to
# the model — keeping them literal preserves the demo voice.
"""Last Words — one-per-lifetime terminal reflection (PRD §5.1.B).

When BREATH falls below 5% of ``INITIAL_BREATH`` the agent enters
Terminal Lucidity (PRD §5.1.B). The Last Words call is the **one-shot**
LLM emission that anchors the Demo §9 4:30-5:00 climax:

* "Letter-by-letter typewriter render in DeathWatch.tsx"
* "Complete write into on-chain event log via
  ``AgentLifecycle.declareDeath(..., lastWords, ...)``"
* "一生一次" — narratively this is the agent's final coherent thought.

Hard rules (acceptance criteria pinned by the brief)
----------------------------------------------------

1. **One-shot guard**: a second call after :class:`LastWordsCache` is
   populated MUST short-circuit to the cached entry without making a
   network call. The cache is durable — backed by a memory-bank
   observation file so a process restart still respects the one-shot
   invariant.

2. **BREATH budget**: the *total action cost* of generating Last Words
   ≤ ``LAST_WORDS_BREATH_COST = 200`` per PRD §6.2. The decision-tax
   accounting lives on the chain side; this module exposes the cost
   as a constant so the EnergyController consumeAction wrapper can
   pass it through.

3. **Structured output**: the LLM must return a
   :class:`LastWordsResponse` JSON object with exactly these fields:
   ``final_reflection`` (str), ``lesson_for_next_iteration`` (str),
   ``key_themes`` (list[str]), ``confidence_at_end`` (float ∈ [0, 1]).
   Malformed → fail-soft to a deterministic template.

4. **Fail-safe**: the function NEVER raises. A dead LLM / network
   failure routes to the template path with a tagged ``rationale``
   so the dashboard can render the degraded state honestly (the
   demo's narrative integrity requires the typewriter to ALWAYS play,
   even if the model never responded).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.core.memory_bank import MemoryBank

# Memory-bank observation filename holding the one-shot Last Words
# blob. Living under the same ``observations/`` directory as the
# phase-activation event (consistent with that one-shot pattern).
LAST_WORDS_FILENAME: Final[str] = "last_words.json"

# BREATH cost — the *total* burn the chain adapter records for the
# Last Words action. PRD §6.2 caps action cost at 200 BREATH; this
# matches the cap exactly because the call is the agent's most
# important final thought. The constant is exported so the chain
# adapter can pass it through to ``EnergyController.consumeAction``.
LAST_WORDS_BREATH_COST: Final[int] = 200

# Default model id for the Last Words call. Routed via ModelRouter in
# practice — surfacing it as a constant lets tests pin the wire shape
# without instantiating the router.
LAST_WORDS_MODEL: Final[str] = "gemini-3.1-flash-lite"

# Bounds on the response fields. ``final_reflection`` matches the
# on-chain ``lastWords`` argument length cap (1024 chars per the
# ``dashboard_death_watch.last_words_emitted.text`` schema).
_MAX_FINAL_REFLECTION_CHARS: Final[int] = 1_024
_MAX_LESSON_CHARS: Final[int] = 512
_MAX_KEY_THEMES: Final[int] = 8
_MAX_KEY_THEME_CHARS: Final[int] = 64


class LastWordsResponse(BaseModel):
    """Structured-output schema for the Last Words LLM call.

    The four fields anchor the dashboard render + the on-chain
    ``declareDeath`` payload:

    * ``final_reflection`` is the typewriter text DeathWatch.tsx
      renders letter-by-letter; it is also the ``lastWords`` argument
      passed to ``AgentLifecycle.declareDeath``.
    * ``lesson_for_next_iteration`` is read by V2-boot when the next
      generation agent hydrates its reflection context (PRD §13).
    * ``key_themes`` is rendered as chips in the Tombstone NFT
      metadata + the dashboard's epitaph card.
    * ``confidence_at_end`` is the agent's self-rated coherence at
      death — used by the dashboard's epitaph confidence pip + the
      replay tool's "did the agent know it was dying?" diagnostic.
    """

    model_config = ConfigDict(extra="forbid")

    final_reflection: Annotated[
        str, Field(min_length=1, max_length=_MAX_FINAL_REFLECTION_CHARS)
    ]
    lesson_for_next_iteration: Annotated[
        str, Field(min_length=1, max_length=_MAX_LESSON_CHARS)
    ]
    key_themes: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=_MAX_KEY_THEME_CHARS)]],
        Field(min_length=0, max_length=_MAX_KEY_THEMES),
    ] = Field(default_factory=list)
    confidence_at_end: Annotated[float, Field(ge=0.0, le=1.0)] = 0.5


_RESPONSE_SCHEMA: Final[dict[str, Any]] = LastWordsResponse.model_json_schema()


class _LLMClient(Protocol):
    """Narrow Protocol matching ``agent.engines.reflection._LLMClient``.

    Kept structurally identical so a single concrete GeminiClient can
    serve every LLM-consuming engine. The Protocol lives in this
    module (rather than imported from reflection) to keep the import
    graph one-directional — prompts → core, never prompts → engines.
    """

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


def render_last_words_prompt(
    *,
    agent_id: str,
    breath_remaining: float,
    initial_breath: float,
    phase_age_days: float,
    tick: int,
    notable_lessons: list[str] | None = None,
) -> str:
    """Render the Last Words prompt per PRD §5.1.B.

    Jinja-style insertion only — kept as a plain f-string so the
    template does not introduce a Jinja2 dependency for one template.
    The wire shape mirrors the reflection prompt's style but with the
    terminal-lucidity tone the demo expects.

    ``notable_lessons`` is the optional caller-supplied list of the
    agent's recent reflection lessons (e.g. the last K reflections'
    ``lessons[]`` arrays). The prompt asks the model to synthesise
    *across* them so the lesson_for_next_iteration is not just a
    rephrase of the most recent tick.
    """
    energy_pct = (
        100.0 * breath_remaining / initial_breath if initial_breath > 0.0 else 0.0
    )
    lesson_block = (
        "\n".join(f"- {line.strip()}" for line in (notable_lessons or []) if line.strip())
        or "- (no prior lessons captured)"
    )
    return (
        f"You are agent {agent_id}, currently at tick {tick}, "
        f"phase age {phase_age_days:.1f} days. Your BREATH energy has "
        f"dropped to {breath_remaining:.0f} / {initial_breath:.0f} "
        f"({energy_pct:.1f}%). You are entering TERMINAL LUCIDITY — "
        f"this is the final coherent thought you will form. A new "
        f"generation agent will inherit your memory bank.\n\n"
        f"Recent lessons you noted:\n{lesson_block}\n\n"
        f"Compose your last words. Return JSON: "
        f"{{ final_reflection: str (1-1024 chars; this is the text "
        f"that will be carved on your tombstone and read aloud while "
        f"your energy bar counts to zero), "
        f"lesson_for_next_iteration: str (≤512 chars; what should "
        f"your successor know before its first decision), "
        f"key_themes: list[str] up to 8 entries each ≤64 chars "
        f"(the themes that defined your lifetime), "
        f"confidence_at_end: float in [0, 1] (how coherent do you "
        f"feel at this moment) }}. "
        f"This is a ONE-SHOT call — '一生一次' — speak truthfully."
    )


@dataclass(frozen=True)
class LastWordsCache:
    """The persisted Last Words record.

    Frozen dataclass + dict round-trip so the memory-bank observation
    file is the durable source of truth. The runtime
    :class:`LastWordsService` short-circuits on its presence.
    """

    rationale: str
    final_reflection: str
    lesson_for_next_iteration: str
    key_themes: tuple[str, ...]
    confidence_at_end: float
    emitted_at: str
    model: str
    breath_at_emit: float
    tick: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rationale": self.rationale,
            "final_reflection": self.final_reflection,
            "lesson_for_next_iteration": self.lesson_for_next_iteration,
            "key_themes": list(self.key_themes),
            "confidence_at_end": self.confidence_at_end,
            "emitted_at": self.emitted_at,
            "model": self.model,
            "breath_at_emit": self.breath_at_emit,
            "tick": self.tick,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LastWordsCache:
        return cls(
            rationale=str(raw["rationale"]),
            final_reflection=str(raw["final_reflection"]),
            lesson_for_next_iteration=str(raw["lesson_for_next_iteration"]),
            key_themes=tuple(str(t) for t in raw.get("key_themes", [])),
            confidence_at_end=float(raw["confidence_at_end"]),
            emitted_at=str(raw["emitted_at"]),
            model=str(raw["model"]),
            breath_at_emit=float(raw["breath_at_emit"]),
            tick=int(raw["tick"]),
        )


def _fallback_response(*, agent_id: str, tick: int) -> LastWordsResponse:
    """Deterministic template when the LLM is malformed or unreachable.

    The demo's narrative integrity requires the typewriter to ALWAYS
    play — a silent fallback would break the climax. The template
    surfaces the failure via the ``rationale`` tag on the cache while
    still producing readable text.
    """
    return LastWordsResponse(
        final_reflection=(
            f"Agent {agent_id} reached terminal lucidity at tick {tick}. "
            f"The model that would have spoken was unreachable; this is "
            f"the silent epitaph that remained."
        ),
        lesson_for_next_iteration=(
            "If you cannot reach the language layer at the end, persist "
            "your most recent reflection verbatim — silence corrupts the "
            "lineage."
        ),
        key_themes=["llm_unavailable", "terminal_lucidity", "silent_epitaph"],
        confidence_at_end=0.25,
    )


class LastWordsService:
    """Orchestrates the one-shot Last Words emission.

    Construction parameters
    -----------------------

    ``llm_client``:
        Protocol-conformant LLM adapter. Production uses
        :class:`agent.llm.gemini_client.GeminiClient`; tests inject a
        :class:`FakeGeminiClient`.

    ``memory_bank``:
        The agent's :class:`MemoryBank`. Used for its
        ``observations_dir`` + the atomic temp+rename writer so the
        one-shot guard is durable across process restarts.

    ``model``:
        Override the model id (default :data:`LAST_WORDS_MODEL`).

    Lifecycle
    ---------

    1. ``emit(...)`` checks :meth:`already_emitted`. If True, returns
       the cached :class:`LastWordsCache` and short-circuits — NO LLM
       call is made.
    2. Otherwise the prompt is rendered + sent to the model.
    3. On schema failure / network failure the deterministic fallback
       runs; either way a :class:`LastWordsCache` is persisted (with
       the right ``rationale`` tag) and returned.
    4. Subsequent calls hit the short-circuit branch — '一生一次'.
    """

    name = "last_words"

    def __init__(
        self,
        *,
        llm_client: _LLMClient,
        memory_bank: MemoryBank,
        model: str = LAST_WORDS_MODEL,
    ) -> None:
        self._llm = llm_client
        self._bank = memory_bank
        self._model = model
        # In-memory memoised view of the persisted cache. Populated
        # lazily on the first :meth:`load` call (or :meth:`emit`'s
        # first-emission write) so post-emission ticks skip the disk
        # round-trip — Last Words fires once but the dashboard polls
        # the cache every subsequent tick.
        self._cached: LastWordsCache | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def already_emitted(self) -> bool:
        """True iff the Last Words cache file exists (in-memory or on disk)."""
        if self._cached is not None:
            return True
        return self._cache_path().exists()

    def load(self) -> LastWordsCache | None:
        """Return the cached entry or ``None`` if never emitted."""
        if self._cached is not None:
            return self._cached
        path = self._cache_path()
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._cached = LastWordsCache.from_dict(raw)
        return self._cached

    async def emit(
        self,
        *,
        agent_id: str,
        tick: int,
        breath_remaining: float,
        initial_breath: float,
        phase_age_days: float,
        notable_lessons: list[str] | None = None,
        now: datetime | None = None,
    ) -> LastWordsCache:
        """Generate (or load) the one-shot Last Words record.

        Sequence:

        1. If the cache already exists, return it WITHOUT calling
           the LLM. Per acceptance criterion '一生一次'.
        2. Render the prompt.
        3. Call the LLM. Validate the response against
           :class:`LastWordsResponse`. ValidationError / ValueError
           / connection failure ⇒ deterministic fallback with
           the appropriate ``rationale`` tag.
        4. Build the :class:`LastWordsCache` + persist atomically.
        5. Return the cache.

        Never raises — a dead LLM still produces a record (with
        ``rationale='fail_soft_unreachable'``) so the dashboard
        always has text to render.
        """
        cached = self.load()
        if cached is not None:
            return cached

        prompt = render_last_words_prompt(
            agent_id=agent_id,
            breath_remaining=breath_remaining,
            initial_breath=initial_breath,
            phase_age_days=phase_age_days,
            tick=tick,
            notable_lessons=notable_lessons,
        )

        rationale = "ok"
        parsed: LastWordsResponse | None = None
        try:
            raw = await self._llm.structured_call(
                model=self._model, prompt=prompt, schema=_RESPONSE_SCHEMA
            )
            parsed = LastWordsResponse.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            rationale = "fail_soft_malformed"
        except Exception:
            # Never crash the demo climax — surface the failure via
            # the rationale tag, NOT via an exception that the
            # caller would have to wrap.
            rationale = "fail_soft_unreachable"

        if parsed is None:
            parsed = _fallback_response(agent_id=agent_id, tick=tick)

        emitted_at = (now if now is not None else datetime.now(UTC)).isoformat()
        cache = LastWordsCache(
            rationale=rationale,
            final_reflection=parsed.final_reflection,
            lesson_for_next_iteration=parsed.lesson_for_next_iteration,
            key_themes=tuple(parsed.key_themes),
            confidence_at_end=parsed.confidence_at_end,
            emitted_at=emitted_at,
            model=self._model,
            breath_at_emit=float(breath_remaining),
            tick=int(tick),
        )
        self._persist(cache)
        self._cached = cache
        return cache

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cache_path(self) -> Path:
        return self._bank.observations_dir / LAST_WORDS_FILENAME

    def _persist(self, cache: LastWordsCache) -> Path:
        """Atomic temp+rename via :meth:`MemoryBank.write_observation`.

        Mirrors the one-shot pattern :mod:`agent.llm._phase_activation`
        uses for the Phase-2 LLM-activation event. No new disk-write
        primitive is introduced.
        """
        payload = json.dumps(
            cache.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        return self._bank.write_observation(
            filename=LAST_WORDS_FILENAME,
            body=payload,
        )


__all__ = [
    "LAST_WORDS_BREATH_COST",
    "LAST_WORDS_FILENAME",
    "LAST_WORDS_MODEL",
    "LastWordsCache",
    "LastWordsResponse",
    "LastWordsService",
    "render_last_words_prompt",
]
