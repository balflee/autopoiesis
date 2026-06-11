"""β₁ — LLM sentiment engine (PRD §4 first Sentient-stream component).

Production LLM: **Gemini 3.1 Flash Lite via google-genai SDK + AI Studio**
(env var ``GEMINI_API_KEY``). Calls the model in structured-output mode
(``response_schema``) with a Pydantic schema matching TECHNICAL_PLAN §4.3:

    { home_team_sentiment, away_team_sentiment, confidence,
      key_themes, reasoning }

The concrete LLM client landed in sprint_4 T-B-006 as
``agent/llm/gemini_client.GeminiClient``. This engine consumes it
through the ``_LLMClient`` Protocol below — no SDK import here.

**Phase 1 freeze (HARD RULE — brief acceptance criterion):**

Per PRD §4.2 and TECHNICAL_PLAN §4.2, Phase 1 freezes β₁ to 0.0.
Training only operates in the 4-dim (W_R, α₁, α₂, α₃) subspace. The
freeze MUST short-circuit BEFORE any LLM call so:

* tests run hermetically (no ``GEMINI_API_KEY`` needed)
* Phase 1 burns zero $$ on a frozen-to-0 channel
* the weight updater can compute gradients against a deterministic
  Phase 1 output

When :class:`SentimentLLMEngine` is constructed with ``phase=1``, the
``llm_client`` argument is OPTIONAL — the freeze fires without it.
Phase 2+ requires the client to be non-``None``.

**Malformed output policy** (PRD structured-output rule):

Malformed JSON / schema validation failure ⇒ retry once ⇒ fail-soft
to a neutral default (``score=0, confidence=0, rationale="malformed_llm_output"``).
The reflection layer can read this rationale and surface it without
the loop crashing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.engines.base import Engine, Signal, require_asof_ts

# Default model identifier — Sonnet 4.6 per PRD §6 (Opus 4.7 reserved
# for key reflection moments).
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"

# `_LLM_RESPONSE_SCHEMA` is computed below right after :class:`_LLMResponse`
# is declared — the schema is static so every Phase 2 evaluate() reuses it
# instead of rebuilding the JSON Schema on each call.


class _LLMResponse(BaseModel):
    """Structured-output schema for the sentiment LLM call.

    Mirrors TECHNICAL_PLAN §4.3 exactly. Pydantic validation is what
    catches malformed model output — the engine retries once on
    :class:`ValidationError` and fails-soft on the second failure.
    """

    model_config = ConfigDict(extra="forbid")

    home_team_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    away_team_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    key_themes: list[str] = Field(default_factory=list)
    reasoning: str = ""


_LLM_RESPONSE_SCHEMA = _LLMResponse.model_json_schema()


class _LLMClient(Protocol):
    """Narrow SDK-agnostic protocol for structured LLM calls.

    Production implementation: ``agent/llm/gemini_client.GeminiClient``
    wrapping ``google-genai`` against Gemini 3.1 Flash Lite (sprint_4
    T-B-006). The single method returns the structured JSON dict payload
    from a ``response_schema``-mode call. Keeping the protocol narrow
    means tests can hand in a fake without importing ``google-genai``.
    """

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class SentimentLLMEngine(Engine):
    """Engine implementing the β₁ LLM情绪 score.

    Phase 1 freeze: ``phase=1`` short-circuits to a neutral Signal
    WITHOUT calling the LLM. Tests assert no LLM call is made.

    Phase 2+: ``llm_client`` is required. Malformed output is retried
    once then fails-soft to a neutral Signal with rationale
    ``"malformed_llm_output"``.
    """

    name = "sentiment_llm"

    def __init__(
        self,
        *,
        phase: int,
        llm_client: _LLMClient | None = None,
        model: str = DEFAULT_LLM_MODEL,
    ) -> None:
        if phase not in (1, 2, 3, 4):
            raise ValueError(f"phase must be 1..4 (got {phase})")
        if phase >= 2 and llm_client is None:
            raise ValueError(
                f"phase={phase} requires a non-None llm_client — only Phase 1 "
                "may construct without one (freeze short-circuits the call)."
            )
        self._phase = phase
        self._llm = llm_client
        self._model = model

    async def evaluate(self, *, target: str, asof_ts: datetime) -> Signal:
        """Score the home-team sentiment edge for market ``target``.

        Phase 1: returns ``Signal(score=0.0, confidence=0.0)`` WITHOUT
        calling the LLM (no $$ burned).

        Phase 2+: builds a prompt + Pydantic schema, runs structured
        call via :class:`_LLMClient`, retries once on
        :class:`ValidationError`, fails-soft on second failure.
        """
        cutoff = require_asof_ts(asof_ts)

        # PHASE 1 FREEZE — hard short-circuit.
        if self._phase == 1:
            return Signal(
                score=0.0,
                confidence=0.0,
                available_at=cutoff.isoformat(),
                rationale="phase1_frozen",
                raw_features={
                    "phase": 1.0,
                    "frozen": 1.0,
                },
            )

        # PHASE 2+ — call the LLM with retry-once + fail-soft.
        assert self._llm is not None  # narrowed by constructor guard
        prompt = _build_prompt(market=target, asof_ts=cutoff)
        schema = _LLM_RESPONSE_SCHEMA

        parsed: _LLMResponse | None = None
        for attempt_idx in range(2):
            try:
                raw = await self._llm.structured_call(
                    model=self._model, prompt=prompt, schema=schema
                )
                parsed = _LLMResponse.model_validate(raw)
                break
            except (ValidationError, ValueError, TypeError):
                if attempt_idx == 1:
                    # Fail-soft on second failure.
                    return Signal(
                        score=0.0,
                        confidence=0.0,
                        available_at=cutoff.isoformat(),
                        rationale="malformed_llm_output",
                        raw_features={
                            "phase": float(self._phase),
                            "malformed": 1.0,
                        },
                    )
                continue

        assert parsed is not None  # loop either broke or returned early
        score = parsed.home_team_sentiment - parsed.away_team_sentiment
        # Clip to [-1, 1] since difference of two [-1,1] values can hit
        # [-2, 2]. Use halve-then-clip to preserve sign and magnitude.
        score = max(-1.0, min(1.0, score / 2.0))

        return Signal(
            score=score,
            confidence=parsed.confidence,
            available_at=cutoff.isoformat(),
            rationale=parsed.reasoning[:200] if parsed.reasoning else "",
            raw_features={
                "phase": float(self._phase),
                "home_team_sentiment": parsed.home_team_sentiment,
                "away_team_sentiment": parsed.away_team_sentiment,
                "llm_confidence": parsed.confidence,
                "n_themes": float(len(parsed.key_themes)),
            },
        )


def _build_prompt(*, market: str, asof_ts: datetime) -> str:
    """Build the sentiment prompt — kept private so the wire shape lives
    in one place. Real prompt template lands when the LLM client is
    wired in T-B-003+."""
    return (
        f"Score recent Reddit/news sentiment for Polymarket market={market} "
        f"as of {asof_ts.isoformat()}. Return home/away sentiment ∈ [-1,1], "
        f"confidence ∈ [0,1], up to 5 key themes, and 1-2 sentences of reasoning."
    )


__all__ = ["DEFAULT_LLM_MODEL", "SentimentLLMEngine"]
