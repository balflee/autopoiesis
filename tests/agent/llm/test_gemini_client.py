"""GeminiClient tests — covers acceptance criteria for T-B-006.

Brief acceptance criteria covered here:

* No real Gemini call under pytest — the autouse conftest deletes
  ``GEMINI_API_KEY`` and every test that exercises the SDK path
  monkey-patches the ``google.genai.Client`` factory.
* response_schema happy path (structured JSON mode).
* Malformed JSON / non-dict body raises ``ValueError`` so the engine
  retry-once path fires.
* ``GEMINI_API_KEY`` missing → :class:`MissingApiKeyError`, distinct
  from ``ValueError`` so the engine catch-all does NOT swallow it.
* Model id constant matches ``gemini-3.5-flash``.
* Phase 1 freeze invariant — the engine short-circuits BEFORE any
  ``structured_call`` even when the production adapter is wired.

The :class:`FakeGeminiClient` lives in ``conftest.py``; the real
:class:`GeminiClient` SDK call is exercised by monkey-patching the
:class:`google.genai.Client` factory so no network I/O happens.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from agent.engines.sentiment_llm import SentimentLLMEngine
from agent.llm.gemini_client import (
    DEFAULT_GEMINI_MODEL,
    GeminiClient,
    MissingApiKeyError,
)

# ── Adapter unit tests (monkey-patched google.genai) ────────────────────


class _FakeResponse:
    """Stand-in for :class:`google.genai.types.GenerateContentResponse`.

    Only the ``.text`` property is read by :meth:`GeminiClient.structured_call`,
    so the fake exposes that single attribute.
    """

    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeAioModels:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self,
        *,
        model: str,
        contents: Any,
        config: Any,
    ) -> _FakeResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class _FakeAio:
    def __init__(self, models: _FakeAioModels) -> None:
        self.models = models


class _FakeGenAIClient:
    """Replaces :class:`google.genai.Client` via monkeypatch.

    ``init_calls`` is per-instance state (NOT a class variable) so
    parallel / interleaved tests cannot leak constructor args across
    each other. The factory in :func:`_install_fake_client` captures
    the instance so tests can assert against ``fake.init_calls``.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.aio = _FakeAio(_FakeAioModels(response))
        self.init_calls: list[dict[str, Any]] = []


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, response_text: str | None
) -> _FakeGenAIClient:
    """Monkey-patch ``google.genai.Client`` to return our fake."""
    fake_response = _FakeResponse(response_text)
    fake_client = _FakeGenAIClient(fake_response)

    def factory(*args: Any, **kwargs: Any) -> _FakeGenAIClient:
        fake_client.init_calls.append(dict(kwargs))
        return fake_client

    monkeypatch.setattr("agent.llm.gemini_client.genai.Client", factory)
    return fake_client


def test_default_model_constant_is_gemini_3_5_flash() -> None:
    """User directive 2026-06-10: default model is gemini-3.5-flash (NOT
    Flash-Lite). Overridable via the GEMINI_MODEL env var."""
    assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash"


def test_structured_call_returns_parsed_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path — response.text is JSON → parsed dict returned."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    fake = _install_fake_client(
        monkeypatch,
        json.dumps(
            {
                "home_team_sentiment": 0.4,
                "away_team_sentiment": -0.1,
                "confidence": 0.7,
                "key_themes": ["star_player_back"],
                "reasoning": "ok",
            }
        ),
    )

    client = GeminiClient()
    result = asyncio.run(
        client.structured_call(
            model=DEFAULT_GEMINI_MODEL,
            prompt="test prompt",
            schema={"type": "object"},
        )
    )

    assert result["home_team_sentiment"] == 0.4
    assert result["confidence"] == 0.7
    # Single call was made with the right model id and our schema went
    # to the response_json_schema field of the config.
    assert len(fake.aio.models.calls) == 1
    call = fake.aio.models.calls[0]
    assert call["model"] == DEFAULT_GEMINI_MODEL
    assert call["contents"] == "test prompt"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == {"type": "object"}


def test_missing_api_key_raises_distinct_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brief: ``GEMINI_API_KEY`` missing → :class:`MissingApiKeyError`.

    The distinct exception type is critical — the engine catches
    ``ValueError`` to fail-soft on malformed model output, but operator
    misconfiguration MUST propagate so the operator sees it.
    """
    # autouse_no_provider_keys already removed GEMINI_API_KEY; just
    # double-check the call raises.
    client = GeminiClient()
    with pytest.raises(MissingApiKeyError, match="GEMINI_API_KEY"):
        asyncio.run(
            client.structured_call(
                model=DEFAULT_GEMINI_MODEL,
                prompt="x",
                schema={"type": "object"},
            )
        )
    # MissingApiKeyError is NOT a ValueError — engine fail-soft must
    # not absorb it.
    assert not issubclass(MissingApiKeyError, ValueError)


def test_empty_response_text_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty Gemini response → ValueError → engine retries once."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _install_fake_client(monkeypatch, response_text=None)

    client = GeminiClient()
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(
            client.structured_call(
                model=DEFAULT_GEMINI_MODEL,
                prompt="x",
                schema={"type": "object"},
            )
        )


def test_non_json_response_raises_json_decode_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brief: malformed JSON triggers the engine's retry-once path.

    :class:`json.JSONDecodeError` is a ``ValueError`` subclass, so the
    engine's ``except ValueError`` branch catches it. Test the raise
    here so the contract is explicit.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _install_fake_client(monkeypatch, response_text="not-valid-json")

    client = GeminiClient()
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(
            client.structured_call(
                model=DEFAULT_GEMINI_MODEL,
                prompt="x",
                schema={"type": "object"},
            )
        )


def test_non_object_json_response_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare list / scalar JSON body is malformed for our schema."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _install_fake_client(monkeypatch, response_text="[1, 2, 3]")

    client = GeminiClient()
    with pytest.raises(ValueError, match="non-object"):
        asyncio.run(
            client.structured_call(
                model=DEFAULT_GEMINI_MODEL,
                prompt="x",
                schema={"type": "object"},
            )
        )


def test_api_key_override_bypasses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructor override should override the env. Mainly used by
    integration tests on dev machines."""
    fake = _install_fake_client(monkeypatch, response_text='{"ok": true}')

    # Env stays unset (autouse fixture) — override should make the call
    # succeed regardless.
    client = GeminiClient(api_key="override-key-not-real")
    result = asyncio.run(
        client.structured_call(
            model="gemini-3.1-flash-lite",
            prompt="x",
            schema={"type": "object"},
        )
    )
    assert result == {"ok": True}
    # And the override key landed in the Client factory call.
    assert any(
        c.get("api_key") == "override-key-not-real" for c in fake.init_calls
    )


def test_phase1_freeze_holds_with_real_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brief: 'Phase 1 sentiment engine still short-circuits BEFORE any
    ``structured_call`` (sprint_3 invariant)'.

    Wire the real :class:`GeminiClient` to a fake SDK and verify that
    Phase 1 evaluate() returns the frozen Signal without invoking the
    SDK at all. The fake records every SDK call; the test asserts the
    list stays empty.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    fake_sdk = _install_fake_client(monkeypatch, response_text='{"x": 1}')
    real_client = GeminiClient()

    engine = SentimentLLMEngine(phase=1, llm_client=real_client)
    cutoff = datetime(2026, 5, 22, 20, 0, 0, tzinfo=UTC)
    signal = asyncio.run(engine.evaluate(target="market1", asof_ts=cutoff))

    assert signal.score == 0.0
    assert signal.confidence == 0.0
    assert signal.rationale == "phase1_frozen"
    # The freeze MUST short-circuit BEFORE any SDK call.
    assert fake_sdk.aio.models.calls == []


def test_lazy_api_key_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brief: 'reads API key lazily from os.environ inside structured_call'.

    Construct the client with NO env, then set the env, then call —
    the call should succeed because the read is lazy.
    """
    # autouse fixture cleared GEMINI_API_KEY; construct now (must not
    # raise) then set env, then call.
    client = GeminiClient()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _install_fake_client(monkeypatch, response_text='{"ok": true}')
    result = asyncio.run(
        client.structured_call(
            model=DEFAULT_GEMINI_MODEL,
            prompt="x",
            schema={"type": "object"},
        )
    )
    assert result == {"ok": True}


def test_default_model_used_when_empty_string_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty ``model=`` arg should fall back to the constructor default."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    fake = _install_fake_client(monkeypatch, response_text='{"ok": true}')

    client = GeminiClient(default_model="gemini-3.1-flash-lite-test-default")
    asyncio.run(
        client.structured_call(
            model="",  # empty → default
            prompt="x",
            schema={"type": "object"},
        )
    )

    assert fake.aio.models.calls[0]["model"] == "gemini-3.1-flash-lite-test-default"


# ---------------------------------------------------------------------------
# Cross-loop reuse. The survival season runs EACH life via a separate
# ``asyncio.run(...)`` (a NEW event loop per life) but reuses ONE shared
# GeminiClient instance. The OLD code cached ``self._client`` (a genai.Client
# whose internal httpx async client is bound to life-0's loop) → on the next
# life the cached client's async transport raises ``RuntimeError: Event loop
# is closed``. The fix builds a FRESH genai.Client per ``structured_call``,
# bound to the running loop. Mirrors the MiniMaxClient cross-loop fix.
# ---------------------------------------------------------------------------


class _LoopBoundAioModels:
    """``aio.models`` stand-in that models genai's loop-bound async transport.

    A real ``genai.Client``'s ``aio`` surface lazily binds its httpx
    ``AsyncClient`` to the loop of its first request and keeps it until the
    client is dropped. Calling it from a later, different loop raises
    ``RuntimeError: Event loop is closed``. This fake reproduces that: the
    FIRST ``generate_content`` binds to the running loop; a later call from a
    DIFFERENT loop raises. A fresh client built per call (the fix) binds anew
    each loop and never crosses, so both lives succeed. The buggy cached
    client carries one instance across loops → raises on the second life.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []
        self._bound_loop: asyncio.AbstractEventLoop | None = None

    async def generate_content(
        self,
        *,
        model: str,
        contents: Any,
        config: Any,
    ) -> _FakeResponse:
        current = asyncio.get_running_loop()
        if self._bound_loop is None:
            self._bound_loop = current
        elif self._bound_loop is not current:
            # The original loop is gone (asyncio.run closed it). This is the
            # exact error genai's httpx async transport raises when a cached
            # client's pool crosses loops.
            raise RuntimeError("Event loop is closed")
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class _LoopBoundGenAIClient:
    """Replaces ``genai.Client``; its ``aio.models`` is loop-bound.

    Critically, a NEW instance gets a NEW (unbound) ``_LoopBoundAioModels``.
    The factory below shares ONE instance per test-installed factory call, so
    when the production code builds a fresh client per ``structured_call`` it
    gets a fresh (unbound) aio surface → no cross-loop error; when the buggy
    code caches one client, the single bound aio surface is reused across
    loops → ``Event loop is closed``.
    """

    def __init__(self, response: _FakeResponse) -> None:
        self.aio = _FakeAio(_LoopBoundAioModels(response))
        self.init_calls: list[dict[str, Any]] = []


def test_same_instance_works_across_two_event_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SAME GeminiClient, called from two SEPARATE ``asyncio.run(...)``
    invocations (two distinct event loops), must succeed BOTH times — no
    "Event loop is closed". This is the survival-season multi-life scenario.

    ``genai.Client`` is mocked so NO network happens. Each factory call
    returns a client whose loop-bound ``aio.models`` raises ``Event loop is
    closed`` if a CACHED client carries it across loops. So this test FAILS
    against the buggy cached-client code and PASSES once a fresh client is
    built per call.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    def factory(*args: Any, **kwargs: Any) -> _LoopBoundGenAIClient:
        # A fresh client per construction — the FIX builds one per call, so
        # each call's aio surface binds to its own loop. The BUG constructs
        # once and reuses, so the single aio surface is shared across loops.
        return _LoopBoundGenAIClient(_FakeResponse('{"life": "ok"}'))

    monkeypatch.setattr("agent.llm.gemini_client.genai.Client", factory)
    client = GeminiClient()

    # Life 1 — first event loop.
    out1 = asyncio.run(
        client.structured_call(model="", prompt="hi", schema={"type": "object"})
    )
    # Life 2 — a brand-new event loop on the SAME client instance. With the
    # buggy cached client this raises "Event loop is closed".
    out2 = asyncio.run(
        client.structured_call(model="", prompt="hi", schema={"type": "object"})
    )

    assert out1 == {"life": "ok"}
    assert out2 == {"life": "ok"}


# ---------------------------------------------------------------------------
# Request TIMEOUT. A stalled connection with NO timeout blocks FOREVER (the
# live backtest hung 32 min at the preflight call) — and a hang is not an
# exception, so the FallbackLLMClient never fails over to MiniMax. A finite
# per-request timeout makes a non-responding endpoint RAISE within ~45s.
# In google-genai 2.6.0 the knob is
# ``genai.Client(http_options=HttpOptions(timeout=<MILLISECONDS>))``.
# ---------------------------------------------------------------------------


def test_default_timeout_is_finite_and_passed_to_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The genai.Client is constructed with a finite HttpOptions.timeout so a
    stalled connection raises instead of hanging forever.

    Captures the kwargs the (mocked) ``genai.Client`` factory receives and
    asserts ``http_options.timeout`` is the default 45_000 ms (45s, in
    MILLISECONDS per the SDK).
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    fake = _install_fake_client(monkeypatch, response_text='{"ok": true}')

    client = GeminiClient()
    asyncio.run(
        client.structured_call(
            model=DEFAULT_GEMINI_MODEL,
            prompt="x",
            schema={"type": "object"},
        )
    )

    assert fake.init_calls, "genai.Client factory was never called"
    http_options = fake.init_calls[0].get("http_options")
    assert http_options is not None, "http_options not passed to genai.Client"
    # HttpOptions.timeout is in MILLISECONDS; default 45s = 45_000 ms (bounded
    # stall protection, with enough headroom for the large structured advisor /
    # reflection generations that routinely exceed 15s — see _DEFAULT_TIMEOUT_S).
    assert http_options.timeout == 45_000


def test_timeout_overridable_via_ctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ctor ``timeout`` param (SECONDS) overrides the default and is
    converted to MILLISECONDS for HttpOptions.timeout."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    fake = _install_fake_client(monkeypatch, response_text='{"ok": true}')

    client = GeminiClient(timeout=5.0)
    asyncio.run(
        client.structured_call(
            model=DEFAULT_GEMINI_MODEL,
            prompt="x",
            schema={"type": "object"},
        )
    )

    http_options = fake.init_calls[0].get("http_options")
    assert http_options is not None
    assert http_options.timeout == 5_000
