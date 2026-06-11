"""GeminiClient — production ``_LLMClient`` adapter.

Concrete implementation of the ``_LLMClient`` Protocol declared in
:mod:`agent.engines.sentiment_llm` and :mod:`agent.engines.reflection`.

The single method :meth:`GeminiClient.structured_call` wraps the
``google-genai`` SDK's structured-JSON mode (``response_mime_type=
'application/json' + response_json_schema=schema``) against the model id
``gemini-3.5-flash`` (:data:`DEFAULT_GEMINI_MODEL`). The engine consumes the returned ``dict``
through Pydantic validation; malformed output ⇒ engine retries once
⇒ engine fails-soft to a deterministic default. **This adapter does NOT
implement retry / fallback** — that policy lives in the engines so a
single fake :class:`_LLMClient` can drive both surfaces in tests.

Provider rule (track-b-backend.md Rule 7, authoritative):

* Production LLM = Gemini 3.5 Flash via AI Studio (user directive 2026-06-10).
* API key read **lazily** from ``os.environ['GEMINI_API_KEY']`` — never
  from a constructor argument and never persisted to disk. The lazy
  read means importing :mod:`agent.llm.gemini_client` does NOT trigger
  an auth failure on a dev box without the key, only the first
  ``structured_call`` does.
* The ``google.genai`` import is module-level so the import error
  surfaces at module-load time, not on the first hot-path call. This
  matches the dependency declared in ``pyproject.toml``.
* **No ``anthropic`` / ``openai`` import.** The AST scan in
  :mod:`tests.agent.llm.test_no_forbidden_imports` is the policy
  enforcer.

Test discipline:

* No real Gemini call under pytest. Tests inject a Protocol-conformant
  :class:`FakeGeminiClient` defined in ``tests/agent/llm/conftest.py``.
* The ``conftest.py`` fixture leaves ``GEMINI_API_KEY`` unset so even
  a buggy test that DID instantiate :class:`GeminiClient` and call
  ``structured_call`` would raise :class:`MissingApiKeyError` before
  any network I/O.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Final

from google import genai
from google.genai import types as genai_types

# Canonical model identifier — user directive 2026-06-10: use Gemini 3.5 Flash
# (NOT Flash-Lite). Overridable per-process via the GEMINI_MODEL env var so the
# model can be retuned without a code edit; defaults to gemini-3.5-flash.
DEFAULT_GEMINI_MODEL: Final[str] = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

# Env var the API key is read from. Lazy read inside
# :meth:`GeminiClient.structured_call` keeps import-time side effects
# at zero and lets pytest run without leaking a real key.
_GEMINI_API_KEY_ENV: Final[str] = "GEMINI_API_KEY"

# Per-request timeout (SECONDS). A stalled connection with NO timeout blocks
# FOREVER — a real backtest hung 32 min at the preflight Gemini call burning
# ~0 CPU, and a hang is NOT an exception, so FallbackLLMClient (which fails
# over only on an EXCEPTION) never fell back to MiniMax. A finite timeout makes
# a non-responding endpoint RAISE within the budget → fallback fires.
# Overridable via the ctor ``timeout`` param. Mirrors MiniMaxClient.
#
# 45s, NOT 15s (2026-06-11 finding): the L3 advisor / L2 reflection calls carry
# large prompts + structured-JSON outputs and routinely need >15s of GENERATION
# time — a Gemini-only survival subset showed the only failures were our own
# 15s TimeoutError + a Google 504 DEADLINE_EXCEEDED at the same budget (the
# HttpOptions timeout is forwarded as the server-side deadline), with ZERO
# quota/429 errors. 45s keeps the stall protection (bounded, raises, fallback
# fires) while letting real generations finish.
_DEFAULT_TIMEOUT_S: Final[float] = 45.0

# Milliseconds per second — google-genai's ``HttpOptions.timeout`` is in
# MILLISECONDS (verified against the installed SDK), so we convert the
# seconds-valued ctor param at client-construction time.
_MS_PER_S: Final[int] = 1000


class MissingApiKeyError(RuntimeError):
    """Raised when :meth:`GeminiClient.structured_call` is invoked but
    ``GEMINI_API_KEY`` is not set in the environment.

    The error type is distinct from :class:`ValueError` so the engine
    catch-all (which absorbs ``ValueError`` and falls through to the
    fail-soft template) cannot accidentally swallow a misconfiguration.
    A missing key is an *operator* error and should propagate.
    """


class GeminiClient:
    """SDK-agnostic ``_LLMClient`` adapter for Gemini (default 3.5 Flash).

    Construct once at agent boot (or per process) and inject the same
    instance into both :class:`agent.engines.sentiment_llm.SentimentLLMEngine`
    and :class:`agent.engines.reflection.ReflectionEngine`. The two engines
    consume the structural :class:`_LLMClient` Protocol, so this concrete
    class never appears in their type hints — keeps the engine modules
    SDK-free.

    Parameters
    ----------
    default_model:
        Model id used when the per-call ``model`` arg is the empty
        string. Defaults to :data:`DEFAULT_GEMINI_MODEL`.

    api_key:
        Optional explicit override. **Strongly discouraged in
        production** — the canonical path is to leave this ``None`` and
        let :meth:`structured_call` read ``GEMINI_API_KEY`` from the
        environment on first call. Tests should NEVER pass a real key
        here; the AST forbidden-import scan + the conftest fixture both
        guard against a leaked key.

    timeout:
        Per-request timeout in SECONDS. Converted to MILLISECONDS for the
        SDK's ``HttpOptions.timeout``. A finite timeout is the guarantee that
        a stalled connection RAISES (→ fallback) instead of hanging forever.

    Notes
    -----
    The ``google.genai.Client`` is built **fresh per** ``structured_call``
    (bound to the running event loop) and never cached across calls. The
    survival season runs each life via a separate ``asyncio.run(...)`` (a new
    loop per life) while reusing ONE shared instance; a cached client (whose
    internal httpx async transport is bound to the first loop) would raise
    ``RuntimeError: Event loop is closed`` on the next life. ``genai.Client``
    construction does no network I/O, so per-call is cheap + loop-safe, and a
    process that boots but never wires the LLM path (e.g. Phase 1 freeze)
    still burns zero API quota and never surfaces an auth error. Mirrors
    :class:`agent.llm.minimax_client.MiniMaxClient`.
    """

    def __init__(
        self,
        *,
        default_model: str = DEFAULT_GEMINI_MODEL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._default_model = default_model
        self._api_key_override = api_key
        self._timeout = timeout

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Call Gemini in structured-JSON mode and return the parsed dict.

        Sequence:

        1. Resolve API key from ``os.environ`` (or constructor override).
        2. Build a FRESH :class:`google.genai.Client` bound to the running
           loop (never cached across calls / loops), with a finite
           ``HttpOptions.timeout`` so a stalled connection RAISES instead of
           hanging forever.
        3. Build a :class:`GenerateContentConfig` with
           ``response_mime_type='application/json'`` and
           ``response_json_schema=schema``.
        4. Call ``client.aio.models.generate_content`` (async).
        5. Parse ``response.text`` as JSON; if the body is empty, raise
           :class:`ValueError` so the engine's retry-once path fires.

        Raises
        ------
        :class:`MissingApiKeyError`
            If neither the constructor nor the environment supplies a
            key. Distinct from :class:`ValueError` so the engine's
            retry-once catch-all does NOT silently absorb operator
            misconfiguration.
        :class:`ValueError`
            If the response is missing the JSON text payload. The
            engine catches :class:`ValueError` and retries once before
            falling through to the fail-soft template.
        :class:`json.JSONDecodeError`
            If the model returned text that is not valid JSON. The
            engine's retry-once path catches this via the broader
            ``ValueError`` lineage (``JSONDecodeError`` is a
            ``ValueError`` subclass).
        """
        api_key = self._api_key_override or os.environ.get(_GEMINI_API_KEY_ENV)
        if not api_key:
            raise MissingApiKeyError(
                f"{_GEMINI_API_KEY_ENV} is not set; refusing to call Gemini. "
                "Set the env var per .env.example § 'Production LLM'."
            )

        # Build a FRESH client per call, bound to the CURRENTLY running event
        # loop, with a finite request timeout. ``HttpOptions.timeout`` is in
        # MILLISECONDS (verified against google-genai 2.6.0). Per-call (not
        # cached) keeps the same shared instance safe across the per-life
        # ``asyncio.run(...)`` loops the survival season creates — a cached
        # client's loop-bound httpx transport would raise "Event loop is
        # closed" on the next life. Construction does no network I/O.
        client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=int(self._timeout * _MS_PER_S),
            ),
        )

        chosen_model = model or self._default_model
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
        )

        # Hard asyncio-level timeout. The SDK's HttpOptions.timeout was observed
        # NOT to fire on an intermittent connection stall — the call blocked
        # FOREVER (0 CPU) with no exception, so FallbackLLMClient (which fails
        # over only on an EXCEPTION) never reached MiniMax and a real backtest
        # hung. asyncio.wait_for ALWAYS fires regardless of SDK behaviour →
        # raises TimeoutError → the fallback runs. Belt-and-suspenders over
        # HttpOptions.timeout (kept as the inner, best-effort budget).
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=chosen_model,
                contents=prompt,
                config=config,
            ),
            timeout=self._timeout,
        )

        text = response.text
        if not text:
            # Empty body — Gemini returned nothing parseable. Engine's
            # retry-once catches ValueError and tries again. If the
            # second attempt also returns empty, fail-soft template
            # fires.
            raise ValueError("Gemini returned empty response.text")

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            # The engine schema is always a top-level object; a list /
            # scalar response is malformed.
            raise ValueError(
                f"Gemini returned non-object JSON: {type(parsed).__name__}"
            )
        return parsed


__all__ = ["DEFAULT_GEMINI_MODEL", "GeminiClient", "MissingApiKeyError"]
