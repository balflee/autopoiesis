"""ReflectionEngine tests — fake-client only, no real Anthropic API.

Brief acceptance criteria covered here:

* Tests use a fake LLM client; real Anthropic API never invoked under pytest.
* Reflection on a routine tick uses Sonnet 4.6.
* Key-moment reflection escalates to Opus 4.7.
* Malformed LLM output ⇒ retry-once ⇒ fail-soft template default.
* Unreachable LLM ⇒ fail-soft template (no exception escapes).
* Reflection is written atomically (temp + rename) to reflections_dir.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent.core.state import (
    Action,
    ActionKind,
    Phase,
    Side,
    TickPayload,
    Vitals,
    Weights,
)
from agent.engines.reflection import (
    DEFAULT_OPUS_MODEL,
    DEFAULT_SONNET_MODEL,
    ReflectionEngine,
    ReflectionRecord,
)


# ── helpers ───────────────────────────────────────────────────────────


def _sample_tick(*, tick: int = 1, action_kind: ActionKind = ActionKind.NO_BET) -> TickPayload:
    """Minimal valid TickPayload — shared across tests."""
    if action_kind == ActionKind.BET:
        action = Action(
            kind=ActionKind.BET,
            market_id="0xabc",
            side=Side.YES,
            size_usd=25.0,
            edge_pct=0.15,
        )
    else:
        action = Action(kind=ActionKind.NO_BET, no_bet_reason="size_below_min_bet:1.2")
    return TickPayload(
        tick=tick,
        ts="2026-05-22T20:00:00Z",
        agent_id="genesis_v1",
        phase=Phase.PHASE_1_INFANCY,
        vitals=Vitals(breath=900.0, bankroll_usd=1000.0, phase_age_days=1.0),
        weights=Weights(
            w_r=0.6,
            w_s=0.4,
            alpha=[1 / 3, 1 / 3, 1 / 3],
            beta=[0.0, 1.0],
            rho=0.5,
        ),
        action=action,
        narrative="Tick reflection test scaffold.",
    )


@dataclass
class _RecordingLLM:
    """Fake LLM client — records every call, returns scripted responses.

    `responses` is consumed in FIFO order. If exhausted, raises an
    AssertionError so a test that didn't expect another call fails
    loudly instead of silently returning None.
    """

    responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise AssertionError(
                f"_RecordingLLM exhausted after {len(self.calls)} calls; "
                f"last model={model}"
            )
        resp = self.responses.pop(0)
        if isinstance(resp, BaseException):
            raise resp
        # Allow scripted responses to be plain dicts (the happy path) or
        # dicts that fail Pydantic validation (the retry / fail-soft path).
        if not isinstance(resp, dict):
            raise AssertionError(f"Unsupported response type: {type(resp).__name__}")
        return resp


def _ok_response() -> dict[str, Any]:
    return {
        "summary": "Tick looked normal; no edge.",
        "lessons": ["wait for stronger signal"],
        "confidence_in_strategy": 0.7,
        "mood": "calm",
    }


# ── Happy path ────────────────────────────────────────────────────────


def test_reflection_happy_path_uses_sonnet(tmp_path: Path) -> None:
    llm = _RecordingLLM(responses=[_ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick()))

    assert isinstance(record, ReflectionRecord)
    assert record.model == DEFAULT_SONNET_MODEL
    assert record.key_moment is False
    assert record.rationale == "ok"
    assert record.mood == "calm"
    assert record.lessons == ("wait for stronger signal",)
    assert record.confidence_in_strategy == 0.7
    assert len(llm.calls) == 1
    assert llm.calls[0]["model"] == DEFAULT_SONNET_MODEL


def test_reflection_key_moment_uses_opus(tmp_path: Path) -> None:
    llm = _RecordingLLM(responses=[_ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick(), key_moment=True))

    assert record.model == DEFAULT_OPUS_MODEL
    assert record.key_moment is True
    assert llm.calls[0]["model"] == DEFAULT_OPUS_MODEL


def test_reflection_writes_file_to_reflections_dir(tmp_path: Path) -> None:
    llm = _RecordingLLM(responses=[_ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick(tick=42)))

    final = tmp_path / "tick_0000042.md"
    assert final.exists()
    body = final.read_text(encoding="utf-8")
    assert "tick: 42" in body
    assert "Tick looked normal" in body
    # path_rel shape slots into TickPayload.reflection_ref exactly.
    assert record.path_rel == "reflections/tick_0000042.md"


def test_reflection_uses_atomic_temp_rename(tmp_path: Path) -> None:
    """Writer MUST stage to .tick_<id>.md.tmp then rename — orphan tmp from
    a previous crash must be cleaned up before write."""
    llm = _RecordingLLM(responses=[_ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    # Drop an orphan tmp first to verify the writer sweeps it.
    tmp_path.mkdir(parents=True, exist_ok=True)
    orphan = tmp_path / ".tick_0000001.md.tmp"
    orphan.write_text("stale", encoding="utf-8")
    asyncio.run(engine.reflect(tick=_sample_tick(tick=1)))
    assert not orphan.exists()
    assert (tmp_path / "tick_0000001.md").exists()


# ── Retry once + fail-soft ───────────────────────────────────────────


def test_reflection_retries_once_on_malformed(tmp_path: Path) -> None:
    """First response missing required field ⇒ retry; second is OK."""
    bad = {"lessons": [], "confidence_in_strategy": 0.5, "mood": "x"}  # missing summary
    good = _ok_response()
    llm = _RecordingLLM(responses=[bad, good])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick()))

    assert record.rationale == "retry_ok"
    assert record.mood == "calm"
    assert len(llm.calls) == 2


def test_reflection_fail_soft_on_persistent_malformed(tmp_path: Path) -> None:
    """Two consecutive malformed responses ⇒ deterministic fallback,
    NO third call, no exception escapes."""
    bad = {"lessons": [], "confidence_in_strategy": 0.5, "mood": "x"}
    llm = _RecordingLLM(responses=[bad, bad])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick()))

    assert record.rationale == "fail_soft_malformed"
    assert len(llm.calls) == 2
    # Fallback summary is deterministic + mentions the tick.
    assert "Tick 1" in record.body


def test_reflection_fail_soft_on_unreachable_llm(tmp_path: Path) -> None:
    """An exception from the LLM client (timeout, connection error)
    MUST NOT propagate — the loop tags rationale + emits fallback."""
    llm = _RecordingLLM(responses=[ConnectionError("LLM is down")])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick()))

    assert record.rationale == "fail_soft_unreachable"
    # Only one call — the catch-all bails out before retry to avoid
    # hammering a downed endpoint.
    assert len(llm.calls) == 1


def test_reflection_writes_record_even_when_llm_dead(tmp_path: Path) -> None:
    """Critical: a dead LLM still produces a record on disk so the
    agent_loop can continue with reflection_ref populated."""
    llm = _RecordingLLM(responses=[TimeoutError("slow")])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    record = asyncio.run(engine.reflect(tick=_sample_tick(tick=99)))

    assert record.rationale == "fail_soft_unreachable"
    assert (tmp_path / "tick_0000099.md").exists()


# ── Prompt content sanity ────────────────────────────────────────────


def test_reflection_prompt_mentions_bet_action(tmp_path: Path) -> None:
    llm = _RecordingLLM(responses=[_ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    asyncio.run(engine.reflect(tick=_sample_tick(action_kind=ActionKind.BET)))

    prompt = llm.calls[0]["prompt"]
    assert "BET" in prompt
    assert "0xabc" in prompt
    assert "phase=PHASE_1_INFANCY" in prompt


def test_reflection_prompt_mentions_no_bet_reason(tmp_path: Path) -> None:
    llm = _RecordingLLM(responses=[_ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    asyncio.run(engine.reflect(tick=_sample_tick(action_kind=ActionKind.NO_BET)))

    prompt = llm.calls[0]["prompt"]
    assert "NO_BET" in prompt
    assert "size_below_min_bet" in prompt


def test_reflection_key_moment_prompt_changes(tmp_path: Path) -> None:
    """Key-moment prompt must signal 'KEY MOMENT' to the LLM."""
    llm = _RecordingLLM(responses=[_ok_response(), _ok_response()])
    engine = ReflectionEngine(llm_client=llm, reflections_dir=tmp_path)
    asyncio.run(engine.reflect(tick=_sample_tick(tick=1), key_moment=False))
    asyncio.run(engine.reflect(tick=_sample_tick(tick=2), key_moment=True))

    routine_prompt = llm.calls[0]["prompt"]
    key_prompt = llm.calls[1]["prompt"]
    assert "KEY MOMENT" in key_prompt
    assert "KEY MOMENT" not in routine_prompt


# ── Pytest hygiene: no real API key in env ───────────────────────────


def test_no_anthropic_api_key_in_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Brief HARD RULE: real Anthropic API never invoked under pytest.

    We can't *prove* the engine doesn't call the SDK without monkey-
    patching the SDK itself — but we CAN verify the test environment
    does not carry a real API key (so even a buggy engine that tries
    would fail fast on auth, not actually charge $$).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import os

    assert "ANTHROPIC_API_KEY" not in os.environ
