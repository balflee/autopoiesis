"""FallbackLLMClient — primary→fallback chain with a circuit breaker.

Wraps two ``_LLMClient``s behind the same SDK-agnostic Protocol
(``async def structured_call(*, model, prompt, schema) -> dict``). In
production: ``primary = GeminiClient``, ``fallback = MiniMaxClient`` (the
user-approved "MiniMax as fallback for Gemini" feature). MiniMax is NOT
Anthropic / OpenAI, so this does not violate the no-``anthropic`` /
no-``openai`` import rule.

Behaviour of :meth:`FallbackLLMClient.structured_call`:

* If the circuit breaker is OPEN → call the fallback directly (skip the
  primary entirely).
* Otherwise try the primary. On success → reset the consecutive-failure
  counter and return the result. On ANY exception → increment the
  consecutive-failure counter (open the breaker once it reaches
  ``max_primary_failures``), log a WARNING that names the exception TYPE
  (never the api key, never the prompt body), then call the fallback and
  return its result. If the fallback ALSO raises, the exception propagates —
  the engine layer's fail-soft handler catches it.

Model routing
-------------
The ``model`` arg the engines pass is the PRIMARY provider's model id (in
production the Gemini id, e.g. ``gemini-3.1-flash-lite``). It is forwarded to
the primary unchanged, but the fallback is ALWAYS called with ``model=""`` so
it resolves its OWN model (``MiniMaxClient`` → ``MINIMAX_MODEL`` env or
``MiniMax-M3``). Forwarding the Gemini id to MiniMax would otherwise trigger an
HTTP 400 "unknown model" and silently fail-soft every fallback call.

Circuit-breaker mode: **latched-open** for the process. Once the breaker
opens it stays open for the life of the instance (a dead primary key does not
recover within a season; re-instantiating the client — e.g. a fresh loop per
``/api/agent/start`` — re-arms the primary). This deliberately avoids
re-probing a dead primary every N calls and keeps the state trivially
single-loop-safe (no locking; all calls run on one asyncio loop).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _LLMClient(Protocol):
    """Narrow SDK-agnostic protocol — same shape as the engine Protocols
    (:class:`agent.engines.reflection._LLMClient` etc.)."""

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class FallbackLLMClient:
    """``_LLMClient`` that fails over from ``primary`` to ``fallback``.

    Parameters
    ----------
    primary:
        The preferred client (production: :class:`GeminiClient`).
    fallback:
        The backup client (production: :class:`MiniMaxClient`).
    max_primary_failures:
        Consecutive primary failures that trip the breaker. Once tripped, the
        primary is bypassed for the rest of the instance's life. Must be
        ``>= 1``.
    """

    def __init__(
        self,
        *,
        primary: _LLMClient,
        fallback: _LLMClient,
        max_primary_failures: int = 3,
    ) -> None:
        if max_primary_failures < 1:
            raise ValueError(
                f"max_primary_failures must be >= 1 (got {max_primary_failures})"
            )
        self.primary = primary
        self.fallback = fallback
        self._max_primary_failures = max_primary_failures
        self._consecutive_failures = 0
        self._breaker_open = False

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Try the primary (unless the breaker is open); fall back on error.

        See the module docstring for the full contract. Never logs the api
        key or the prompt body.
        """
        if self._breaker_open:
            # Pass model="" so the fallback resolves its OWN model (e.g.
            # MiniMaxClient → MiniMax-M3). The ``model`` arg here is the
            # PRIMARY's id (Gemini); forwarding it to the fallback would
            # cause an "unknown model" 400. See the model-routing note below.
            return await self.fallback.structured_call(
                model="", prompt=prompt, schema=schema
            )

        try:
            result = await self.primary.structured_call(
                model=model, prompt=prompt, schema=schema
            )
        except Exception as exc:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_primary_failures:
                self._breaker_open = True
            logger.warning(
                "primary LLM failed (%s: %.300s), falling back to MiniMax "
                "(consecutive_failures=%d, breaker_open=%s)",
                type(exc).__name__,
                # The exception MESSAGE (truncated) — needed to tell a 429
                # rate-limit from a 400 schema rejection from a 5xx outage;
                # provider error bodies carry quota names, never the api key.
                str(exc).replace("\n", " "),
                self._consecutive_failures,
                self._breaker_open,
            )
            # Pass model="" so the fallback resolves its OWN model rather than
            # inheriting the primary's (Gemini) id — which the fallback's
            # provider does not recognise (→ "unknown model" 400).
            return await self.fallback.structured_call(
                model="", prompt=prompt, schema=schema
            )

        # Primary succeeded — reset the consecutive-failure counter so a
        # transient blip does not eventually trip the latched breaker.
        self._consecutive_failures = 0
        return result


__all__ = ["FallbackLLMClient"]
