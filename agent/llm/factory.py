"""``make_llm_client`` — prod ``_LLMClient`` selection (fallback default OFF).

Single construction seam for the production LLM client. The DEFAULT path
(no ``MINIMAX_API_KEY``) returns a plain :class:`agent.llm.gemini_client.GeminiClient`
— **byte-identical** to the pre-feature wiring, so nothing about the Gemini-only
deployment changes. Only when ``MINIMAX_API_KEY`` is set/non-empty does the
factory return a :class:`RetryLLMClient` wrapping a
:class:`agent.llm.fallback_client.FallbackLLMClient` with a Gemini primary +
MiniMax fallback (the user-approved "MiniMax as fallback for Gemini" feature;
MiniMax is NOT Anthropic / OpenAI). The retry wrapper re-issues the whole chain
on an intermittent connection STALL (a ``TimeoutError`` from the clients' hard
``asyncio.wait_for``); real errors propagate so the engine fail-soft still applies.

The fallback is purely about WHICH client backs the already-flag-gated prod
paths — it does NOT change any feature flag. Callers swap their
``GeminiClient()`` construction for ``make_llm_client()`` and inherit the
fallback automatically once the operator supplies a MiniMax key.

Both clients construct lazily (no key read / no I/O at ``__init__``), so this
factory never touches the network and never raises on a missing key.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _LLMClient(Protocol):
    """Narrow SDK-agnostic protocol — same shape as the engine Protocols."""

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class RetryLLMClient:
    """Retry wrapper — re-issues the call on an intermittent network STALL.

    Some hosts intermittently STALL the TCP/TLS connection to the LLM providers
    (a fresh connection hangs with ~0 CPU). The inner clients' hard
    ``asyncio.wait_for`` turns that infinite block into a ``TimeoutError``
    instead of hanging forever; this wrapper then simply RETRIES — a fresh
    connection almost always succeeds, since the stall is intermittent. Only
    timeouts are retried; real errors (4xx / bad JSON / missing key) propagate
    unchanged so the engine's fail-soft still applies. It wraps the WHOLE inner
    chain, so a retry re-tries Gemini→MiniMax afresh.
    """

    def __init__(self, *, inner: _LLMClient, max_attempts: int = 4) -> None:
        self.inner = inner
        self._max_attempts = max(1, max_attempts)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        last_exc: BaseException | None = None
        for attempt in range(self._max_attempts):
            try:
                return await self.inner.structured_call(
                    model=model, prompt=prompt, schema=schema
                )
            except TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "LLM call stalled (attempt %d/%d), retrying with a fresh "
                    "connection",
                    attempt + 1,
                    self._max_attempts,
                )
        assert last_exc is not None  # loop ran >=1 time
        raise last_exc


def make_llm_client() -> _LLMClient:
    """Return the prod LLM client, wiring the MiniMax fallback iff configured.

    * ``MINIMAX_API_KEY`` unset / empty → a bare
      :class:`agent.llm.gemini_client.GeminiClient` (default path, byte-unchanged).
    * ``MINIMAX_API_KEY`` set → a :class:`RetryLLMClient` wrapping a
      :class:`agent.llm.fallback_client.FallbackLLMClient` with
      ``primary=GeminiClient()`` + ``fallback=MiniMaxClient()``.

    Imports are deferred inside the branch so the default path pays no import
    cost for the fallback / MiniMax modules.
    """
    # Lazy import keeps the default path's import surface unchanged.
    from agent.llm.gemini_client import GeminiClient

    if os.environ.get("MINIMAX_API_KEY"):
        from agent.llm.fallback_client import FallbackLLMClient
        from agent.llm.minimax_client import MiniMaxClient

        chain = FallbackLLMClient(primary=GeminiClient(), fallback=MiniMaxClient())
        # Wrap the chain so an intermittent connection STALL (turned into a
        # TimeoutError by the clients' hard asyncio.wait_for) is retried with a
        # fresh connection rather than fail-softing. Only active on the
        # fallback (MiniMax-configured) path; the default Gemini-only prod path
        # stays byte-identical.
        return RetryLLMClient(inner=chain, max_attempts=4)

    return GeminiClient()


__all__ = ["make_llm_client"]
