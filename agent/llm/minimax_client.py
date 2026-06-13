"""MiniMaxClient — fallback ``_LLMClient`` adapter (OpenAI-compatible).

Concrete implementation of the SDK-agnostic ``_LLMClient`` Protocol declared
in :mod:`agent.engines.reflection` / :mod:`agent.engines.strategy_advisor_impl`
(``async def structured_call(*, model, prompt, schema) -> dict``). This client
is the user-approved **fallback** for the primary :class:`GeminiClient` — wired
behind :class:`agent.llm.fallback_client.FallbackLLMClient`.

Provider note: MiniMax is NOT Anthropic / OpenAI, so this client does not
violate the project's no-``anthropic`` / no-``openai`` import rule (the AST
scan in :mod:`tests.agent.llm.test_no_forbidden_imports` enforces that). We
talk to the MiniMax **OpenAI-compatible** chat-completions endpoint over plain
HTTP via :mod:`httpx` — no MiniMax / OpenAI SDK is imported.

Construction mirrors :class:`agent.llm.gemini_client.GeminiClient` exactly:

* The API key is read **lazily** on the first ``structured_call`` from
  ``os.environ['MINIMAX_API_KEY']`` — never at ``__init__`` — so a process
  that boots but never calls the LLM burns nothing and never raises an auth
  error. A missing / empty key raises the SAME
  :class:`agent.llm.gemini_client.MissingApiKeyError` the Gemini client uses,
  so callers / tests treat both clients uniformly.
* The :class:`httpx.AsyncClient` is built **fresh per** ``structured_call``
  (inside an ``async with``) and closed on exit — NOT cached. The survival
  season runs each life on a separate ``asyncio.run(...)`` loop while reusing
  one shared client instance; a cached client bound to the first loop would
  raise ``RuntimeError: Event loop is closed`` on the next life. A per-call
  client is loop-safe and burns nothing until the path is wired.

Endpoint (international, OpenAI-compatible — confirmed live):

* ``POST {base_url}/chat/completions``
* Headers: ``Authorization: Bearer <key>``, ``Content-Type: application/json``.
* Body: ``{"model": ..., "messages": [system, user]}``.
* Response: OpenAI-shaped — assistant text at
  ``choices[0].message.content``.

``response_format`` (strict JSON) is NOT reliably supported on the
OpenAI-compatible endpoint, so we DO NOT rely on it; instead the system message
instructs JSON-only and we parse the content ourselves. Unparseable content
raises :class:`ValueError` so the engine's retry-once path fires — mirroring
:class:`GeminiClient`.

Reasoning-model hardening
-------------------------
``MiniMax-M3`` is a **reasoning model**. On the OpenAI-compatible endpoint its
``choices[0].message.content`` is frequently PREFIXED with a chain-of-thought
block, e.g. ``<think>... {"echoed": "schema"} ...</think>\\n{"real": "answer"}``.
The ``<think>`` block itself contains JSON-like ``{...}`` snippets (the echoed
schema / example), so a naive "slice from the first ``{``" parser latches onto
the example brace inside the reasoning and returns garbage. The content→dict
extraction therefore runs a three-stage pipeline (see
:func:`_extract_json_object`):

1. **Strip reasoning blocks** — remove ``<think>...</think>`` /
   ``<thinking>...</thinking>`` (regex, DOTALL, non-greedy, case-insensitive).
   A *dangling* ``</think>`` with no matching open tag drops everything up to
   and including the LAST close tag. A dangling OPEN tag with no close is left
   as-is (the balanced scan below handles it).
2. **Strip a code fence** — a ```json ... ``` (or bare ``` ... ```) wrapper.
3. **Balanced-brace extraction** — scan for top-level ``{...}`` objects while
   tracking JSON string state + backslash escapes (so braces inside strings do
   not derail the scan), then ``json.loads`` the LAST balanced object (the real
   answer follows the reasoning); if that fails, fall back to the FIRST. Raise
   :class:`ValueError` if nothing parses.

Request-side: we do NOT send any provider-specific "reasoning split" hint. The
international OpenAI-compatible endpoint rejects unknown body fields with a 400,
so an unverified ``reasoning_split`` flag would risk breaking every call; the
content-side strip is the guarantee and is sufficient on its own.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Final

import httpx

from agent.llm.gemini_client import MissingApiKeyError

# International OpenAI-compatible base URL. The domestic platform
# (``api.minimaxi.com``) differs only by host, so this is configurable
# via ``MINIMAX_BASE_URL``.
DEFAULT_MINIMAX_BASE_URL: Final[str] = "https://api.minimax.io/v1"

# Default model id when the per-call ``model`` arg is empty (mirrors
# GeminiClient's ``model or self._default_model``).
DEFAULT_MINIMAX_MODEL: Final[str] = "MiniMax-M3"

# Env var names — all read lazily inside ``structured_call``.
_MINIMAX_API_KEY_ENV: Final[str] = "MINIMAX_API_KEY"
_MINIMAX_BASE_URL_ENV: Final[str] = "MINIMAX_BASE_URL"
_MINIMAX_MODEL_ENV: Final[str] = "MINIMAX_MODEL"
_MINIMAX_GROUP_ID_ENV: Final[str] = "MINIMAX_GROUP_ID"

# System message that pins JSON-only output (response_format is unreliable
# on the OpenAI-compat endpoint, so we instruct + parse).
_JSON_SYSTEM_MSG: Final[str] = (
    "You output ONLY a single valid minified JSON object conforming to the "
    "user's JSON Schema. No markdown, no code fences, no prose."
)

# Cap on how much of an error body is echoed into an exception message — the
# api key is never part of the body, but we still bound the size.
_MAX_ERR_BODY_CHARS: Final[int] = 500

# Per-call READ timeout (seconds). ``MiniMax-M3`` is a slow REASONING model and
# the L3 advisor cadence is slow, so a generous read budget is required — a
# tight 40s budget caused repeated ``ReadTimeout`` in the multi-life backtest,
# and EVERY read timeout triggers a RetryLLMClient retry whose timed-out
# request the provider may still bill server-side. A patient read budget lets
# the slow M3 response come back on the FIRST attempt, so we pay once, not 2-4×.
# Applied as the READ leg only (see ``_request_timeout``): connect/write/pool
# stay tight so a dead endpoint still fails fast. Overridable via the ctor.
_DEFAULT_TIMEOUT_S: Final[float] = 180.0
# Tight non-read legs — a dead endpoint should fail in seconds, not minutes.
_CONNECT_TIMEOUT_S: Final[float] = 15.0
_WRITE_TIMEOUT_S: Final[float] = 30.0


class MiniMaxClient:
    """OpenAI-compatible ``_LLMClient`` adapter for MiniMax.

    Parameters
    ----------
    default_model:
        Model id used when the per-call ``model`` arg is empty AND
        ``MINIMAX_MODEL`` is unset. Defaults to :data:`DEFAULT_MINIMAX_MODEL`.

    transport:
        Optional :class:`httpx.AsyncBaseTransport` injected for tests (e.g.
        :class:`httpx.MockTransport`, which satisfies both the sync and async
        transport bases). Production leaves this ``None`` and the real network
        transport is used. Like the API key, the transport is only consulted
        on the first call — ``__init__`` performs no I/O.

    timeout:
        Per-request timeout in seconds.

    Notes
    -----
    The :class:`httpx.AsyncClient` is built **fresh per call** (inside an
    ``async with``) and closed on exit — never cached across calls. This keeps
    the single shared client instance safe across the per-life event loops the
    survival season creates (``asyncio.run`` per life): a cached client bound
    to the first loop would raise ``RuntimeError: Event loop is closed`` on the
    next life. ``__init__`` still performs no I/O, so a process that boots but
    never wires the LLM path burns nothing — mirrors
    :class:`agent.llm.gemini_client.GeminiClient`.
    """

    def __init__(
        self,
        *,
        default_model: str = DEFAULT_MINIMAX_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._default_model = default_model
        self._transport = transport
        self._timeout = timeout

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Call MiniMax in JSON-only mode and return the parsed dict.

        Sequence:

        1. Resolve ``MINIMAX_API_KEY`` from the environment (lazy).
        2. Resolve base URL / model / optional group id from the environment.
        3. Build a FRESH :class:`httpx.AsyncClient` bound to the running loop
           (``async with``); never cached across calls / loops.
        4. POST ``{base_url}/chat/completions`` with Bearer auth + the
           system + user messages.
        5. Raise on non-2xx (status + truncated body; NEVER the key).
        6. Parse ``choices[0].message.content`` via the reasoning-model
           pipeline (strip ``<think>`` blocks → strip a ```json fence →
           balanced-brace extraction of the last/first top-level object);
           ``json.loads`` the result.

        Raises
        ------
        :class:`agent.llm.gemini_client.MissingApiKeyError`
            If ``MINIMAX_API_KEY`` is unset / empty. Distinct from
            :class:`ValueError` so the engine's retry-once catch-all does NOT
            absorb operator misconfiguration.
        :class:`RuntimeError`
            On an HTTP non-2xx response (carries the status + a truncated
            body, never the api key).
        :class:`ValueError` / :class:`json.JSONDecodeError`
            If the content is missing or not valid JSON — the engine's
            retry-once path catches these (``JSONDecodeError`` is a
            ``ValueError`` subclass).
        """
        api_key = os.environ.get(_MINIMAX_API_KEY_ENV)
        if not api_key:
            raise MissingApiKeyError(
                f"{_MINIMAX_API_KEY_ENV} is not set; refusing to call MiniMax. "
                "Set the env var per .env.example § 'MiniMax fallback'."
            )

        base_url = (
            os.environ.get(_MINIMAX_BASE_URL_ENV) or DEFAULT_MINIMAX_BASE_URL
        ).rstrip("/")
        chosen_model = model or os.environ.get(_MINIMAX_MODEL_ENV) or self._default_model
        group_id = os.environ.get(_MINIMAX_GROUP_ID_ENV)

        url = f"{base_url}/chat/completions"
        if group_id:
            # Domestic native path needs ?GroupId=; OpenAI-compat ignores it.
            url = f"{url}?GroupId={group_id}"

        user_msg = (
            f"{prompt}\n\nJSON Schema:\n{json.dumps(schema)}"
            "\n\nRespond with ONLY the JSON object."
        )
        body: dict[str, Any] = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": _JSON_SYSTEM_MSG},
                {"role": "user", "content": user_msg},
            ],
        }

        # Build a FRESH AsyncClient per call, bound to the CURRENTLY running
        # event loop, and close it on exit. The survival season runs each life
        # via a separate ``asyncio.run(...)`` (a new loop per life) while
        # reusing ONE shared MiniMaxClient instance — a cached client bound to
        # the first loop raises ``RuntimeError: Event loop is closed`` on the
        # next life. A per-call client (which also fixes the cross-loop
        # ``WriteTimeout`` symptom) is loop-safe. The injected test transport
        # is honoured on every call.
        # Granular timeout: a generous READ budget (slow M3 reasoning) but a
        # tight connect/write/pool so a dead endpoint fails in seconds.
        request_timeout = httpx.Timeout(
            self._timeout,
            connect=_CONNECT_TIMEOUT_S,
            write=_WRITE_TIMEOUT_S,
            pool=_CONNECT_TIMEOUT_S,
        )
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=request_timeout,
        ) as client:
            # Hard asyncio-level timeout on top of httpx's own. An intermittent
            # connection stall where the inner timeout fails to fire would
            # otherwise block FOREVER with no exception (observed for the Gemini
            # primary), so the engine's fail-soft never runs. asyncio.wait_for
            # ALWAYS fires → raises TimeoutError. Budget slightly over the httpx
            # timeout so the inner one wins on a genuinely slow (not stalled) M3.
            response = await asyncio.wait_for(
                client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                ),
                timeout=self._timeout + 10.0,
            )

        if response.status_code < 200 or response.status_code >= 300:
            # NEVER include the api key. The response body does not carry it.
            snippet = response.text[:_MAX_ERR_BODY_CHARS]
            raise RuntimeError(
                f"MiniMax HTTP {response.status_code} from {base_url}: {snippet}"
            )

        data = response.json()
        content = _extract_content(data)
        parsed = _extract_json_object(content)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"MiniMax returned non-object JSON: {type(parsed).__name__}"
            )
        return parsed


def _extract_content(data: Any) -> str:
    """Pull ``choices[0].message.content`` from an OpenAI-shaped body.

    Raises :class:`ValueError` (engine retry-once fires) if the shape is
    missing or the content is empty.
    """
    if not isinstance(data, dict):
        raise ValueError(f"MiniMax response not a JSON object: {type(data).__name__}")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("MiniMax response missing 'choices'")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("MiniMax choices[0] not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("MiniMax choices[0].message missing")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("MiniMax choices[0].message.content empty")
    return content


# Matches a complete ``<think>...</think>`` / ``<thinking>...</thinking>``
# reasoning block: DOTALL (``.`` spans newlines), non-greedy (``.*?`` so two
# blocks are not merged), IGNORECASE (handles ``<Think>`` / ``</THINK>``).
_THINK_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"<(think|thinking)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

# Matches a *dangling* close tag (``</think>`` / ``</thinking>``) with no
# surviving open partner — used to drop a leaked reasoning prefix.
_DANGLING_CLOSE_RE: Final[re.Pattern[str]] = re.compile(
    r"</(think|thinking)\s*>",
    re.IGNORECASE,
)


def _strip_reasoning_blocks(text: str) -> str:
    """Remove reasoning chain-of-thought so the real JSON answer survives.

    Pipeline step 1 (see module docstring):

    * Drop every complete ``<think>...</think>`` / ``<thinking>...</thinking>``
      block (non-greedy, DOTALL, case-insensitive).
    * Then handle a *dangling* close tag: if a ``</think>`` / ``</thinking>``
      still remains (the model emitted reasoning then a close tag with no
      matching open), drop everything up to and including the LAST such close
      tag — the real answer follows it.
    * A dangling OPEN tag with no close is deliberately LEFT as-is; the
      downstream balanced-brace scan extracts the answer object from whatever
      remains, which is safer than nuking to end-of-string.
    """
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # Drop a leaked reasoning prefix terminated by a dangling close tag.
    last_close = None
    for match in _DANGLING_CLOSE_RE.finditer(cleaned):
        last_close = match
    if last_close is not None:
        cleaned = cleaned[last_close.end() :]
    return cleaned


def _strip_code_fence(text: str) -> str:
    """Pipeline step 2: unwrap a ```json ... ``` (or bare ``` ... ```) fence.

    Only a fence at the very start (after stripping) is unwrapped; the closing
    fence is removed if present. Non-fenced text passes through untouched.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    newline = stripped.find("\n")
    if newline != -1:
        stripped = stripped[newline + 1 :]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[: -len("```")]
    return stripped.strip()


def _balanced_objects(text: str) -> list[str]:
    """Return every top-level balanced ``{...}`` object substring, in order.

    The scan tracks JSON **string state** + backslash escapes so braces that
    live inside strings (e.g. reasoning prose ``the value is "{not json}"`` or
    a JSON value ``"contains { and }"``) do NOT open / close depth and cannot
    derail extraction. Only top-level objects (depth returns to 0) are emitted;
    nested objects are part of their enclosing object.
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    objects.append(text[start : i + 1])
                    start = -1
    return objects


def _extract_json_object(content: str) -> Any:
    """Reasoning-model-aware content→object extraction (pipeline steps 1–3).

    Strips ``<think>`` blocks, then a code fence, then performs a
    balanced-brace scan and ``json.loads`` the LAST top-level object (the real
    answer follows the reasoning); on failure it falls back to the FIRST
    parseable object. Raises :class:`ValueError` if nothing parses, so the
    engine's retry-once + fail-soft still applies.
    """
    cleaned = _strip_code_fence(_strip_reasoning_blocks(content))
    if not cleaned.strip():
        raise ValueError(
            "MiniMax content empty after stripping reasoning block(s)"
        )

    objects = _balanced_objects(cleaned)
    if not objects:
        raise ValueError("MiniMax content had no balanced JSON object")

    # The real answer follows the reasoning, so prefer the LAST object; fall
    # back to the FIRST if the last one is not valid JSON.
    last_err: Exception | None = None
    try:
        return json.loads(objects[-1])
    except json.JSONDecodeError as exc:
        last_err = exc
    for candidate in objects[:-1]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_err = exc
    raise ValueError(
        f"MiniMax content had no parseable JSON object: {last_err}"
    )


__all__ = [
    "DEFAULT_MINIMAX_BASE_URL",
    "DEFAULT_MINIMAX_MODEL",
    "MiniMaxClient",
]
