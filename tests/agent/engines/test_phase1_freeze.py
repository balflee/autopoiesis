"""Phase 1 LLM-sentiment freeze tests — brief acceptance criterion.

PRD §4.2 + TECHNICAL_PLAN §4.2: Phase 1 freezes β₁ = 0. The
:class:`SentimentLLMEngine` MUST short-circuit BEFORE any LLM call
when constructed with ``phase=1``:

* score == 0.0 + confidence == 0.0
* rationale == "phase1_frozen"
* zero $$ burned on the LLM API
* the freeze fires even when ``llm_client`` is None — Phase 1 must
  not require an LLM client at construction time

This module is small and dedicated because the freeze is the single
hardest invariant the agent's economic safety relies on for Phase 1:
a leak here means the agent pays for Sonnet calls during training
on a channel that's locked to 0.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from agent.engines import SentimentLLMEngine, Signal

CUTOFF = datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)


@dataclass
class _SpyLLM:
    """LLM stub that records every structured_call invocation.

    Phase 1 tests assert ``len(calls) == 0`` after evaluate(). If
    a future refactor accidentally drops the freeze short-circuit,
    this test catches it deterministically.
    """

    response: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        return self.response


def test_phase1_returns_zero_signal_without_llm_client() -> None:
    """Phase 1 must construct + evaluate WITHOUT an llm_client.

    A null client here is the canonical Phase 1 wiring (the chain
    adapter does not pass a client when phase==1). Returning a neutral
    Signal proves the freeze short-circuits BEFORE any client lookup.
    """
    engine = SentimentLLMEngine(phase=1, llm_client=None)
    sig = asyncio.run(engine.evaluate(target="market1", asof_ts=CUTOFF))

    assert isinstance(sig, Signal)
    assert sig.score == 0.0
    assert sig.confidence == 0.0
    assert sig.rationale == "phase1_frozen"
    assert sig.raw_features.get("frozen") == 1.0


def test_phase1_never_calls_llm_even_if_client_present() -> None:
    """If an llm_client is wired (e.g. shared across engines), Phase 1
    MUST still skip it. The recorder catches any accidental call."""
    spy = _SpyLLM(
        response={
            "home_team_sentiment": 0.5,
            "away_team_sentiment": -0.5,
            "confidence": 0.9,
            "key_themes": [],
            "reasoning": "should NOT be seen",
        }
    )
    engine = SentimentLLMEngine(phase=1, llm_client=spy)
    sig = asyncio.run(engine.evaluate(target="market1", asof_ts=CUTOFF))

    # Output is the frozen signal, NOT the spy's would-be response.
    assert sig.score == 0.0
    assert sig.confidence == 0.0
    # The spy recorded ZERO calls — the freeze short-circuited.
    assert spy.calls == []


def test_phase1_freeze_repeated_evaluate_stays_zero() -> None:
    """Repeated evaluations must NOT escape the freeze (the recorder
    invariant must hold across many cycles, not just the first one)."""
    spy = _SpyLLM(response={})
    engine = SentimentLLMEngine(phase=1, llm_client=spy)

    for _ in range(5):
        sig = asyncio.run(engine.evaluate(target="market1", asof_ts=CUTOFF))
        assert sig.score == 0.0
        assert sig.confidence == 0.0

    assert spy.calls == []  # zero across all 5 ticks


def test_phase_out_of_range_rejected() -> None:
    """Constructor must refuse phase values outside the PRD §3 enum."""
    with pytest.raises(ValueError, match="phase"):
        SentimentLLMEngine(phase=0, llm_client=None)
    with pytest.raises(ValueError, match="phase"):
        SentimentLLMEngine(phase=5, llm_client=None)
