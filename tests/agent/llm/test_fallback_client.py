"""Tests for :mod:`agent.llm.fallback_client` — Gemini→MiniMax fallback chain.

The :class:`FallbackLLMClient` wraps two ``_LLMClient``s (primary + fallback)
with a consecutive-failure circuit breaker. All tests use two recording fakes
— NO real network, no real LLM call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.llm.fallback_client import FallbackLLMClient
from tests.agent.llm.conftest import run_async


@dataclass
class _RecordingLLM:
    """Records ``structured_call`` invocations; scripts dict / exception."""

    responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    # When non-empty, returned cyclically once ``responses`` is drained.
    default: Any = None

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if self.responses:
            item = self.responses.pop(0)
        else:
            item = self.default
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, dict), f"bad scripted response: {item!r}"
        return item


def _call(client: FallbackLLMClient) -> dict[str, Any]:
    return run_async(client.structured_call(model="m", prompt="p", schema={}))


def test_primary_success_returns_primary_no_fallback() -> None:
    """Primary succeeds → its result returned, fallback never called."""
    primary = _RecordingLLM(responses=[{"ok": "primary"}])
    fallback = _RecordingLLM(responses=[{"ok": "fallback"}])
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    assert _call(client) == {"ok": "primary"}
    assert len(primary.calls) == 1
    assert fallback.calls == []


def test_primary_raises_falls_back() -> None:
    """Primary raises → fallback called, its result returned."""
    primary = _RecordingLLM(responses=[RuntimeError("dead")])
    fallback = _RecordingLLM(responses=[{"ok": "fallback"}])
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    assert _call(client) == {"ok": "fallback"}
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_breaker_opens_after_max_failures(caplog: pytest.LogCaptureFixture) -> None:
    """After ``max_primary_failures`` consecutive primary errors, the breaker
    opens and the primary is no longer called."""
    primary = _RecordingLLM(default=RuntimeError("dead"))
    fallback = _RecordingLLM(default={"ok": "fallback"})
    client = FallbackLLMClient(primary=primary, fallback=fallback, max_primary_failures=3)

    for _ in range(3):
        assert _call(client) == {"ok": "fallback"}
    assert len(primary.calls) == 3  # breaker now OPEN

    # Further calls go straight to fallback — primary not touched again.
    for _ in range(5):
        assert _call(client) == {"ok": "fallback"}
    assert len(primary.calls) == 3
    assert len(fallback.calls) == 8


def test_success_resets_failure_counter() -> None:
    """A primary success between failures resets the consecutive counter so
    the breaker does NOT open on non-consecutive errors."""
    primary = _RecordingLLM(
        responses=[
            RuntimeError("e1"),
            RuntimeError("e2"),
            {"ok": "primary"},  # success resets
            RuntimeError("e3"),
            RuntimeError("e4"),
        ]
    )
    fallback = _RecordingLLM(default={"ok": "fallback"})
    client = FallbackLLMClient(primary=primary, fallback=fallback, max_primary_failures=3)

    assert _call(client) == {"ok": "fallback"}  # fail 1
    assert _call(client) == {"ok": "fallback"}  # fail 2
    assert _call(client) == {"ok": "primary"}  # success → reset
    assert _call(client) == {"ok": "fallback"}  # fail 1 (post-reset)
    assert _call(client) == {"ok": "fallback"}  # fail 2 (post-reset)
    # Breaker never opened: primary was called all 5 times.
    assert len(primary.calls) == 5


def test_fallback_raising_propagates() -> None:
    """If the fallback ALSO raises, the exception propagates (engine catches)."""
    primary = _RecordingLLM(responses=[RuntimeError("primary dead")])
    fallback = _RecordingLLM(responses=[ValueError("fallback dead too")])
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    with pytest.raises(ValueError, match="fallback dead too"):
        _call(client)


def test_open_breaker_calls_fallback_directly() -> None:
    """Once latched open, the primary is bypassed entirely."""
    primary = _RecordingLLM(default=RuntimeError("dead"))
    fallback = _RecordingLLM(default={"ok": "fallback"})
    client = FallbackLLMClient(primary=primary, fallback=fallback, max_primary_failures=1)
    assert _call(client) == {"ok": "fallback"}  # 1 failure → breaker open
    assert len(primary.calls) == 1
    assert _call(client) == {"ok": "fallback"}
    assert len(primary.calls) == 1  # primary NOT called again


def test_warning_logged_on_fallback_without_key_or_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The WARNING names the exception type but NOT the prompt body."""
    primary = _RecordingLLM(responses=[RuntimeError("boom")])
    fallback = _RecordingLLM(responses=[{"ok": "fallback"}])
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    with caplog.at_level(logging.WARNING):
        run_async(
            client.structured_call(model="m", prompt="SECRET_PROMPT_BODY", schema={})
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING on fallback"
    joined = " ".join(r.getMessage() for r in warnings)
    assert "RuntimeError" in joined
    assert "SECRET_PROMPT_BODY" not in joined


# ---------------------------------------------------------------------------
# BUG 1 — the fallback must resolve its OWN model, not inherit the primary's.
#
# The engines pass the GEMINI model id (e.g. "gemini-3.1-flash-lite") as the
# ``model`` arg. Forwarding that id to MiniMax → HTTP 400 "unknown model". The
# fallback must therefore be called with ``model=""`` so MiniMaxClient resolves
# its own default (MiniMax-M3). The PRIMARY call keeps the original model.
# ---------------------------------------------------------------------------

_GEMINI_MODEL = "gemini-3.1-flash-lite"


def test_except_path_fallback_receives_empty_model() -> None:
    """On the primary-raises path, the fallback is called with ``model=""`` so
    it resolves its OWN model (not the primary's Gemini id)."""
    primary = _RecordingLLM(responses=[RuntimeError("dead")])
    fallback = _RecordingLLM(responses=[{"ok": "fallback"}])
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    run_async(client.structured_call(model=_GEMINI_MODEL, prompt="p", schema={}))
    assert len(fallback.calls) == 1
    assert fallback.calls[0]["model"] == ""
    # The primary still received the original (Gemini) model unchanged.
    assert primary.calls[0]["model"] == _GEMINI_MODEL


def test_breaker_open_path_fallback_receives_empty_model() -> None:
    """On the breaker-open early-return path, the fallback is likewise called
    with ``model=""`` (no primary involved)."""
    primary = _RecordingLLM(default=RuntimeError("dead"))
    fallback = _RecordingLLM(default={"ok": "fallback"})
    client = FallbackLLMClient(primary=primary, fallback=fallback, max_primary_failures=1)
    # First call trips the breaker (also via the except-path).
    run_async(client.structured_call(model=_GEMINI_MODEL, prompt="p", schema={}))
    # Second call goes straight to the fallback (breaker open).
    run_async(client.structured_call(model=_GEMINI_MODEL, prompt="p", schema={}))
    assert len(primary.calls) == 1  # breaker latched open after the first
    assert len(fallback.calls) == 2
    assert all(c["model"] == "" for c in fallback.calls)


def test_primary_still_receives_original_model_on_success() -> None:
    """A succeeding primary receives the ORIGINAL model id unchanged (only the
    fallback gets ``model=""``)."""
    primary = _RecordingLLM(responses=[{"ok": "primary"}])
    fallback = _RecordingLLM(responses=[{"ok": "fallback"}])
    client = FallbackLLMClient(primary=primary, fallback=fallback)
    run_async(client.structured_call(model=_GEMINI_MODEL, prompt="p", schema={}))
    assert primary.calls[0]["model"] == _GEMINI_MODEL
    assert fallback.calls == []
