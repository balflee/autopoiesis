"""Tests for :mod:`agent.llm.factory` — ``make_llm_client`` selection.

The factory returns a plain :class:`GeminiClient` on the default path (no
``MINIMAX_API_KEY``) — byte-unchanged from before this feature — and a
:class:`RetryLLMClient` wrapping a :class:`FallbackLLMClient` (Gemini primary,
MiniMax fallback) only when ``MINIMAX_API_KEY`` is set/non-empty.

No real LLM call: the factory only CONSTRUCTS clients (both are lazy — no key
read at __init__), so these tests never hit a network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.llm.factory import RetryLLMClient, make_llm_client
from agent.llm.fallback_client import FallbackLLMClient
from agent.llm.gemini_client import GeminiClient
from agent.llm.minimax_client import MiniMaxClient


def test_default_path_returns_plain_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``MINIMAX_API_KEY`` → a bare :class:`GeminiClient` (default path)."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    client = make_llm_client()
    assert type(client) is GeminiClient


def test_empty_minimax_key_returns_plain_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty-string key is treated as unset → plain Gemini."""
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    client = make_llm_client()
    assert type(client) is GeminiClient


def test_minimax_key_set_returns_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MINIMAX_API_KEY`` set → a :class:`RetryLLMClient` WRAPPING a
    :class:`FallbackLLMClient` (Gemini primary + MiniMax fallback).

    The retry wrapper re-issues the whole Gemini→MiniMax chain on an intermittent
    connection STALL (turned into a ``TimeoutError`` by the clients' hard
    ``asyncio.wait_for``); the inner chain is unchanged.
    """
    monkeypatch.setenv("MINIMAX_API_KEY", "some-key")
    client = make_llm_client()
    assert isinstance(client, RetryLLMClient)
    inner = client.inner
    assert isinstance(inner, FallbackLLMClient)
    assert isinstance(inner.primary, GeminiClient)
    assert isinstance(inner.fallback, MiniMaxClient)


# --------------------------------------------------------------------------- #
# RetryLLMClient — retry ONLY on an intermittent connection STALL (TimeoutError).
# --------------------------------------------------------------------------- #


class _ScriptedLLM:
    """A fake inner ``_LLMClient`` that yields a scripted sequence of behaviours.

    Each entry is either an exception instance (raised) or a dict (returned). The
    next call pops the next behaviour; records how many times it was called.
    """

    def __init__(self, behaviours: list[object]) -> None:
        self._behaviours = list(behaviours)
        self.calls = 0

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls += 1
        behaviour = self._behaviours.pop(0)
        if isinstance(behaviour, BaseException):
            raise behaviour
        assert isinstance(behaviour, dict)
        return behaviour


def _call(client: RetryLLMClient) -> dict[str, Any]:
    return asyncio.run(
        client.structured_call(model="m", prompt="p", schema={"type": "object"})
    )


def test_retry_recovers_after_one_timeout() -> None:
    """A TimeoutError on attempt 1 then a dict on attempt 2 → the dict (retried)."""
    inner = _ScriptedLLM([TimeoutError("stall"), {"ok": True}])
    client = RetryLLMClient(inner=inner, max_attempts=4)
    assert _call(client) == {"ok": True}
    assert inner.calls == 2, "must have retried exactly once after the stall"


def test_retry_exhausts_and_reraises_last_timeout() -> None:
    """All attempts time out → the last ``TimeoutError`` is re-raised at the cap."""
    inner = _ScriptedLLM([TimeoutError("s1"), TimeoutError("s2"), TimeoutError("s3")])
    client = RetryLLMClient(inner=inner, max_attempts=3)
    with pytest.raises(TimeoutError):
        _call(client)
    assert inner.calls == 3, "must have tried exactly max_attempts times"


def test_retry_does_not_retry_non_timeout() -> None:
    """A non-timeout error (e.g. ValueError) propagates immediately — NO retry."""
    inner = _ScriptedLLM([ValueError("bad json"), {"ok": True}])
    client = RetryLLMClient(inner=inner, max_attempts=4)
    with pytest.raises(ValueError):
        _call(client)
    assert inner.calls == 1, "a real error must NOT be retried"
