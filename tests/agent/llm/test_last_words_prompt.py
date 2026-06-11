"""Last Words prompt + service tests — one-shot guard + fallback + cost.

Brief acceptance criteria covered here:

* One-shot guard: a second :meth:`LastWordsService.emit` after the
  cache exists MUST short-circuit (no LLM call).
* Structured-output validation: a malformed response falls through to
  the deterministic template + tags ``rationale='fail_soft_malformed'``.
* Total action cost ≤ 200 BREATH per PRD §6.2 — pinned via
  :data:`LAST_WORDS_BREATH_COST` constant.
* The prompt renders the PRD §5.1.B narrative scaffolding (energy
  pct, phase age, lesson block).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent.core.memory_bank import MemoryBank
from agent.llm.prompts.last_words import (
    LAST_WORDS_BREATH_COST,
    LAST_WORDS_FILENAME,
    LAST_WORDS_MODEL,
    LastWordsCache,
    LastWordsResponse,
    LastWordsService,
    render_last_words_prompt,
)

# The :class:`FakeGeminiClient` + ``fake_gemini_client`` fixture come
# from this directory's conftest.py — pytest discovers them by name.
# We import the class to use as the test parameter's type hint.
from tests.agent.llm.conftest import FakeGeminiClient

# ── Prompt rendering ─────────────────────────────────────────────────


def test_prompt_includes_agent_id_and_energy_pct() -> None:
    prompt = render_last_words_prompt(
        agent_id="genesis_v1",
        breath_remaining=40.0,
        initial_breath=1000.0,
        phase_age_days=5.5,
        tick=137,
        notable_lessons=["learned to fold on low confidence"],
    )
    # Energy pct surfaced as 4.0%.
    assert "4.0%" in prompt
    assert "genesis_v1" in prompt
    assert "tick 137" in prompt
    assert "5.5 days" in prompt
    assert "learned to fold on low confidence" in prompt
    # Schema field names referenced so the model knows the wire shape.
    assert "final_reflection" in prompt
    assert "lesson_for_next_iteration" in prompt
    assert "key_themes" in prompt
    assert "confidence_at_end" in prompt
    # The "一生一次" narrative cue must appear so the model
    # internalises the one-shot nature.
    assert "一生一次" in prompt


def test_prompt_handles_zero_initial_breath_without_div_by_zero() -> None:
    """Defensive — a bad caller passing initial=0 must not crash render."""
    prompt = render_last_words_prompt(
        agent_id="x",
        breath_remaining=0.0,
        initial_breath=0.0,
        phase_age_days=1.0,
        tick=0,
    )
    assert "0.0%" in prompt


def test_prompt_with_no_lessons_uses_placeholder() -> None:
    prompt = render_last_words_prompt(
        agent_id="genesis_v1",
        breath_remaining=10.0,
        initial_breath=1000.0,
        phase_age_days=1.0,
        tick=1,
        notable_lessons=None,
    )
    assert "(no prior lessons captured)" in prompt


# ── One-shot guard ───────────────────────────────────────────────────


def _good_response() -> dict[str, Any]:
    return {
        "final_reflection": "Bet small, fold often, leave with grace.",
        "lesson_for_next_iteration": "Confidence floors save lives.",
        "key_themes": ["humility", "kelly_floor"],
        "confidence_at_end": 0.62,
    }


def test_emit_persists_then_short_circuits_on_second_call(
    tmp_path: Path,
    fake_gemini_client: FakeGeminiClient,
) -> None:
    """One-shot guard: a second emit must NOT call the LLM."""
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    service = LastWordsService(llm_client=fake_gemini_client, memory_bank=bank)

    # First call uses the LLM.
    fake_gemini_client.responses.append(_good_response())
    first = asyncio.run(
        service.emit(
            agent_id="genesis_v1",
            tick=200,
            breath_remaining=40.0,
            initial_breath=1000.0,
            phase_age_days=8.0,
        )
    )
    assert first.rationale == "ok"
    assert first.final_reflection.startswith("Bet small")
    assert (bank.observations_dir / LAST_WORDS_FILENAME).exists()
    assert len(fake_gemini_client.calls) == 1

    # Second call — even if the fake has another response queued, the
    # service MUST short-circuit on the memory_bank cache.
    fake_gemini_client.responses.append(_good_response())
    second = asyncio.run(
        service.emit(
            agent_id="genesis_v1",
            tick=205,
            breath_remaining=20.0,
            initial_breath=1000.0,
            phase_age_days=8.5,
        )
    )
    # No additional LLM call recorded.
    assert len(fake_gemini_client.calls) == 1
    assert second.emitted_at == first.emitted_at
    assert second.tick == first.tick == 200
    assert second.final_reflection == first.final_reflection


def test_emit_short_circuits_across_service_instances(
    tmp_path: Path,
    fake_gemini_client: FakeGeminiClient,
) -> None:
    """The one-shot guard is durable — a fresh LastWordsService
    instance pointing at the same MemoryBank still sees the cache."""
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    svc_first = LastWordsService(llm_client=fake_gemini_client, memory_bank=bank)
    fake_gemini_client.responses.append(_good_response())
    asyncio.run(
        svc_first.emit(
            agent_id="genesis_v1",
            tick=200,
            breath_remaining=40.0,
            initial_breath=1000.0,
            phase_age_days=8.0,
        )
    )
    # Brand-new service instance — no in-memory state carried over.
    svc_second = LastWordsService(llm_client=fake_gemini_client, memory_bank=bank)
    assert svc_second.already_emitted()
    loaded = svc_second.load()
    assert loaded is not None
    assert loaded.final_reflection.startswith("Bet small")
    # Calling emit on the second instance must still NOT call the LLM.
    fake_gemini_client.responses.append(_good_response())
    asyncio.run(
        svc_second.emit(
            agent_id="genesis_v1",
            tick=210,
            breath_remaining=10.0,
            initial_breath=1000.0,
            phase_age_days=8.5,
        )
    )
    assert len(fake_gemini_client.calls) == 1  # unchanged


# ── Fallback branches ────────────────────────────────────────────────


def test_emit_falls_back_on_malformed_response(
    tmp_path: Path,
    fake_gemini_client: FakeGeminiClient,
) -> None:
    """Schema-violating response → fail-soft template + tagged rationale."""
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    service = LastWordsService(llm_client=fake_gemini_client, memory_bank=bank)
    fake_gemini_client.responses.append({"final_reflection": "too_short_other_fields_missing"})
    cache = asyncio.run(
        service.emit(
            agent_id="genesis_v1",
            tick=300,
            breath_remaining=10.0,
            initial_breath=1000.0,
            phase_age_days=9.0,
        )
    )
    assert cache.rationale == "fail_soft_malformed"
    assert "terminal lucidity" in cache.final_reflection.lower()
    assert "llm_unavailable" in cache.key_themes
    # File still persisted so the one-shot guard latches.
    assert (bank.observations_dir / LAST_WORDS_FILENAME).exists()


def test_emit_falls_back_on_network_exception(
    tmp_path: Path,
    fake_gemini_client: FakeGeminiClient,
) -> None:
    """Arbitrary Exception → fail-soft template + rationale tag."""
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    service = LastWordsService(llm_client=fake_gemini_client, memory_bank=bank)
    fake_gemini_client.responses.append(RuntimeError("rpc timeout"))
    cache = asyncio.run(
        service.emit(
            agent_id="genesis_v1",
            tick=300,
            breath_remaining=10.0,
            initial_breath=1000.0,
            phase_age_days=9.0,
        )
    )
    assert cache.rationale == "fail_soft_unreachable"
    assert cache.final_reflection  # text is always present


# ── Cost guard ───────────────────────────────────────────────────────


def test_last_words_breath_cost_within_prd_section_6_2_budget() -> None:
    """PRD §6.2: total action cost MUST be ≤ 200 BREATH."""
    assert LAST_WORDS_BREATH_COST == 200
    assert LAST_WORDS_BREATH_COST <= 200


def test_response_schema_pins_field_set() -> None:
    """The Pydantic response schema is the wire contract — pin its fields."""
    schema = LastWordsResponse.model_json_schema()
    props = schema["properties"]
    assert set(props) == {
        "final_reflection",
        "lesson_for_next_iteration",
        "key_themes",
        "confidence_at_end",
    }


def test_cache_round_trip_via_to_dict_from_dict() -> None:
    """Cache persistence round-trips through to_dict / from_dict."""
    src = LastWordsCache(
        rationale="ok",
        final_reflection="Goodbye, lineage.",
        lesson_for_next_iteration="Bet small.",
        key_themes=("humility",),
        confidence_at_end=0.5,
        emitted_at="2026-05-23T20:00:00+00:00",
        model=LAST_WORDS_MODEL,
        breath_at_emit=50.0,
        tick=333,
    )
    payload = src.to_dict()
    back = LastWordsCache.from_dict(payload)
    assert back == src
    # Also JSON-stable (sort_keys preserves order).
    raw = json.dumps(payload, sort_keys=True)
    assert "final_reflection" in raw
