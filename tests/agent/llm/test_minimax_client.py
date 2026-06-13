"""Tests for :mod:`agent.llm.minimax_client` — MiniMax ``_LLMClient`` adapter.

MiniMax is the **fallback** provider for the prod LLM seam (user-approved
"MiniMax as fallback for Gemini" feature). MiniMax is NOT Anthropic / OpenAI,
so it does not violate the project's no-Anthropic / no-OpenAI rule.

Test discipline (mirrors ``test_gemini_client`` / the package conftest):

* No real MiniMax call under pytest. Every HTTP round-trip is served by an
  :class:`httpx.MockTransport` injected via the ``transport`` constructor
  arg — never a live socket.
* The package ``conftest.py`` autouse fixture strips ``MINIMAX_API_KEY`` (and
  the Gemini / Pinata keys), so a buggy test that DID build the real transport
  would raise :class:`MissingApiKeyError` before any network I/O.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agent.llm.gemini_client import MissingApiKeyError
from agent.llm.minimax_client import (
    DEFAULT_MINIMAX_BASE_URL,
    DEFAULT_MINIMAX_MODEL,
    MiniMaxClient,
)
from tests.agent.llm.conftest import run_async


def _openai_body(content: str) -> dict[str, Any]:
    """Build an OpenAI-shaped chat-completion response body."""
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _make_client(
    *,
    handler: Any,
    monkeypatch: pytest.MonkeyPatch,
    api_key: str = "test-minimax-key",
    **kwargs: Any,
) -> MiniMaxClient:
    """Build a MiniMaxClient wired to a MockTransport handler + a fake key."""
    monkeypatch.setenv("MINIMAX_API_KEY", api_key)
    transport = httpx.MockTransport(handler)
    return MiniMaxClient(transport=transport, **kwargs)


def test_structured_call_returns_parsed_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: OpenAI-shaped JSON content → parsed dict."""
    payload = {"summary": "ok", "lessons": ["a", "b"]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(json.dumps(payload)))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(
        client.structured_call(model="MiniMax-M3", prompt="hi", schema={"type": "object"})
    )
    assert out == payload


def test_structured_call_strips_json_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ```json ... ``` fenced body is unwrapped before parsing."""
    payload = {"a": 1}
    fenced = "```json\n" + json.dumps(payload) + "\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(fenced))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == payload


def test_structured_call_strips_leading_prose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leading prose before the first ``{`` is discarded."""
    payload = {"x": "y"}
    noisy = "Sure, here is the JSON: " + json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(noisy))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == payload


def test_missing_api_key_raises_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``MINIMAX_API_KEY`` → :class:`MissingApiKeyError`, NO HTTP call."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_openai_body("{}"))

    client = MiniMaxClient(transport=httpx.MockTransport(handler))
    with pytest.raises(MissingApiKeyError):
        run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert calls == []


def test_empty_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty-string key is treated as unset."""
    monkeypatch.setenv("MINIMAX_API_KEY", "")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("HTTP must not be called when key is empty")

    client = MiniMaxClient(transport=httpx.MockTransport(handler))
    with pytest.raises(MissingApiKeyError):
        run_async(client.structured_call(model="", prompt="hi", schema={}))


def test_raises_on_http_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-2xx (401) → raises, and the api key is NOT in the message."""
    secret = "super-secret-key"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _make_client(handler=handler, monkeypatch=monkeypatch, api_key=secret)
    with pytest.raises(Exception) as exc_info:
        run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert secret not in str(exc_info.value)
    assert "401" in str(exc_info.value)


def test_raises_on_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 500 body raises a clear error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    with pytest.raises(Exception) as exc_info:
        run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert "500" in str(exc_info.value)


def test_unparseable_content_raises_valueerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """Content with no JSON object → ValueError (engine retry-once fires)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body("not json at all"))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    with pytest.raises(ValueError):
        run_async(client.structured_call(model="", prompt="hi", schema={}))


def test_non_object_json_raises_valueerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """A top-level JSON array (not an object) is malformed → ValueError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body("[1, 2, 3]"))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    with pytest.raises(ValueError):
        run_async(client.structured_call(model="", prompt="hi", schema={}))


def test_request_carries_auth_model_and_json_system_msg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wire request carries Bearer auth, the model, a JSON-only system
    msg, and the schema embedded in the user message."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_body("{}"))

    client = _make_client(
        handler=handler, monkeypatch=monkeypatch, api_key="abc123"
    )
    schema = {"type": "object", "properties": {"k": {"type": "string"}}}
    run_async(client.structured_call(model="MiniMax-M2", prompt="PROMPT_X", schema=schema))

    assert captured["auth"] == "Bearer abc123"
    assert captured["content_type"] == "application/json"
    assert captured["url"].endswith("/chat/completions")
    body = captured["body"]
    assert body["model"] == "MiniMax-M2"
    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "PROMPT_X" in messages[1]["content"]
    # schema is embedded in the user message
    assert "properties" in messages[1]["content"]


def test_default_model_used_when_model_arg_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty ``model`` arg falls back to the configured default."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai_body("{}"))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert captured["body"]["model"] == DEFAULT_MINIMAX_MODEL


def test_base_url_override_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MINIMAX_BASE_URL`` overrides the default endpoint host."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_openai_body("{}"))

    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert captured["url"].startswith("https://api.minimaxi.com/v1")


def test_group_id_appended_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MINIMAX_GROUP_ID`` adds a ``?GroupId=`` query param."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_openai_body("{}"))

    monkeypatch.setenv("MINIMAX_GROUP_ID", "grp-42")
    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert "GroupId=grp-42" in captured["url"]


def test_default_constants() -> None:
    """The exported defaults match the international OpenAI-compat endpoint."""
    assert DEFAULT_MINIMAX_BASE_URL == "https://api.minimax.io/v1"
    assert DEFAULT_MINIMAX_MODEL == "MiniMax-M3"


# ---------------------------------------------------------------------------
# BUG 2 — cross-loop reuse. The survival season runs EACH life via a separate
# ``asyncio.run(...)`` (a NEW event loop per life) but reuses ONE shared
# MiniMaxClient instance. A cached httpx.AsyncClient is bound to the FIRST loop
# → ``RuntimeError: Event loop is closed`` on the next life. The client must
# build a fresh AsyncClient PER call, bound to the running loop.
# ---------------------------------------------------------------------------


class _LoopBoundTransport(httpx.AsyncBaseTransport):
    """A transport that models httpx/httpcore's loop-bound connection pool.

    A real ``httpx.AsyncClient`` lazily opens its connection pool on the loop
    of its first request and keeps it until the client is **closed**. Using
    that same (still-open) pool from a later, different loop raises
    ``RuntimeError: Event loop is closed``. This transport reproduces that:

    * On a request it binds to the running loop (if not already bound) and
      raises if a DIFFERENT loop is seen while still bound.
    * On ``aclose`` (which ``async with httpx.AsyncClient(...)`` calls when the
      block exits) it RELEASES the binding — the next request may bind to a
      fresh loop.

    Therefore: the FIXED per-call ``async with`` client closes the transport
    after each call, releasing the binding → both loops succeed. The BUGGY
    cached client never closes between calls, so the binding survives into the
    second loop → raises, exactly like the live backtest failure.
    """

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self._bound_loop: asyncio.AbstractEventLoop | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        current = asyncio.get_running_loop()
        if self._bound_loop is None:
            self._bound_loop = current
        elif self._bound_loop is not current:
            # The original loop is gone (asyncio.run closed it). This is the
            # exact error httpx raises when a never-closed cached client's pool
            # crosses loops.
            raise RuntimeError("Event loop is closed")
        return httpx.Response(200, json=self._body)

    async def aclose(self) -> None:
        # httpx calls this on AsyncClient close (the per-call ``async with``).
        # Releasing the binding mirrors the pool being torn down.
        self._bound_loop = None


def test_same_instance_works_across_two_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SAME MiniMaxClient, called from two SEPARATE ``asyncio.run(...)``
    invocations (two distinct event loops), must succeed BOTH times — no
    "Event loop is closed". This is the survival-season multi-life scenario.

    Uses a loop-bound transport that raises ``RuntimeError: Event loop is
    closed`` if a cached client carries it across loops — so this test FAILS
    against the buggy cached-client code and PASSES once a fresh client is
    built per call.
    """
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax-key")
    payload = {"life": "ok"}
    transport = _LoopBoundTransport(_openai_body(json.dumps(payload)))
    client = MiniMaxClient(transport=transport)

    # Life 1 — first event loop.
    out1 = run_async(client.structured_call(model="", prompt="hi", schema={}))
    # Life 2 — a brand-new event loop on the SAME client instance. With the
    # buggy cached client this raises "Event loop is closed".
    out2 = run_async(client.structured_call(model="", prompt="hi", schema={}))

    assert out1 == payload
    assert out2 == payload


def test_injected_transport_honoured_every_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The injected MockTransport must be used on EVERY call (the per-call
    client is built with ``transport=self._transport`` each time), so no live
    socket is opened on the second loop either."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_openai_body("{}"))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    run_async(client.structured_call(model="", prompt="hi", schema={}))
    run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# BUG 3 — default per-request READ timeout for the M3 reasoning model. Raised
# to a patient 180s (a hard ``asyncio.wait_for`` budget on top so a stalled
# connection RAISES instead of hanging forever), still overridable via the
# ctor. The generous READ budget lets slow M3 reasoning return on the FIRST
# attempt instead of timing out → retrying (every retry is a billable call).
# ---------------------------------------------------------------------------


def test_default_read_timeout_is_patient_for_reasoning_model() -> None:
    """The default READ budget is a patient, finite 180s — generous enough
    that slow M3 reasoning returns on the first attempt (no retry spend)."""
    from agent.llm.minimax_client import _DEFAULT_TIMEOUT_S

    assert _DEFAULT_TIMEOUT_S == 180.0
    import math

    assert math.isfinite(_DEFAULT_TIMEOUT_S)


def test_connect_and_write_timeouts_stay_tight() -> None:
    """A dead endpoint must still fail fast — connect/write legs stay short."""
    from agent.llm.minimax_client import (
        _CONNECT_TIMEOUT_S,
        _WRITE_TIMEOUT_S,
    )

    assert _CONNECT_TIMEOUT_S <= 30.0
    assert _WRITE_TIMEOUT_S <= 60.0


def test_timeout_overridable_via_ctor() -> None:
    """The ctor ``timeout`` param still overrides the default."""
    client = MiniMaxClient(timeout=5.0)
    assert client._timeout == 5.0


# ---------------------------------------------------------------------------
# Reasoning-model (<think>) output hardening — MiniMax-M3 is a reasoning model
# whose ``content`` is PREFIXED with a chain-of-thought block that itself
# contains JSON-like ``{...}`` snippets (the echoed schema/example). The parser
# must strip the reasoning block FIRST, then extract the REAL answer object.
# ---------------------------------------------------------------------------


def test_think_block_with_json_inside_returns_real_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``<think>`` block that echoes example JSON must NOT be parsed as the
    answer; the real object AFTER the block wins."""
    content = (
        '<think>reasoning that mentions {"foo": 1, "bar": 2}</think>\n'
        '{"real": "answer", "n": 3}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == {"real": "answer", "n": 3}
    assert out != {"foo": 1, "bar": 2}


def test_thinking_tag_variant_mixed_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """``<thinking>...</thinking>`` with mixed-case tags is stripped too."""
    content = (
        '<Thinking>I will output {"schema": "echo"} per the example.</THINKING>'
        '\n{"answer": 42}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == {"answer": 42}


def test_think_block_then_json_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reasoning block followed by a ```json fenced answer parses the fence,
    not the example braces inside the reasoning."""
    payload = {"decision": "buy", "size": 10}
    content = (
        '<think>The example shows {"decision": "?", "size": 0}. '
        "I should produce the final object.</think>\n"
        "```json\n" + json.dumps(payload) + "\n```"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == payload


def test_think_block_nested_escaped_braces_in_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Braces that live inside JSON strings (or reasoning prose) must not
    derail the balanced-brace scan."""
    content = (
        '<think>the value is "{not json}" and also a stray } brace and { brace'
        "</think>\n"
        '{"note": "contains { and } and \\" escaped quote", "k": "v"}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == {"note": 'contains { and } and " escaped quote', "k": "v"}


def test_clean_json_content_still_parses_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: clean JSON content with no reasoning block still works."""
    payload = {"summary": "fine", "n": 7}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(json.dumps(payload)))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == payload


def test_think_block_only_no_answer_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degenerate response that is ONLY a reasoning block (content becomes
    empty after strip) raises ValueError so the engine retries."""
    content = '<think>just reasoning, no final object {"x": 1} here</think>   '

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    with pytest.raises(ValueError):
        run_async(client.structured_call(model="", prompt="hi", schema={}))


def test_dangling_close_think_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dangling ``</think>`` with no opening tag drops everything up to and
    including the LAST close tag, leaving the real answer."""
    content = 'leaked reasoning {"echo": 0} </think>\n{"real": 1}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == {"real": 1}


def test_empty_content_after_strip_is_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only content raises ValueError (degenerate empty answer)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body("   \n  "))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    with pytest.raises(ValueError):
        run_async(client.structured_call(model="", prompt="hi", schema={}))


def test_last_top_level_object_wins_over_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When multiple top-level objects survive the strip (e.g. think strip
    missed an inline example), the LAST balanced object is the answer."""
    content = '{"example": true}\nFinal answer:\n{"real": "last"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == {"real": "last"}


def test_first_object_fallback_when_last_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LAST balanced object is not valid JSON, fall back to the FIRST
    valid balanced object rather than raising."""
    # The trailing braces are not valid JSON (bare word, no quotes); the first
    # object is the real, parseable answer.
    content = '{"real": "first"}\nnote: {this is not json}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_body(content))

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == {"real": "first"}


def test_reasoning_details_present_with_clean_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some MiniMax configs put reasoning in a separate ``reasoning_details``
    field and leave ``content`` clean — that path must NOT crash."""
    payload = {"answer": "clean"}
    body = _openai_body(json.dumps(payload))
    body["choices"][0]["message"]["reasoning_details"] = [
        {"type": "reasoning.text", "text": "I considered the options."}
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = _make_client(handler=handler, monkeypatch=monkeypatch)
    out = run_async(client.structured_call(model="", prompt="hi", schema={}))
    assert out == payload
