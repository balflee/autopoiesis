"""Test fixtures for the ``agent/llm`` package.

Brief acceptance criterion (T-B-006):

    "conftest.py leaves ``GEMINI_API_KEY`` unset under pytest — fixture
    assertion enforces it; ``FakeGeminiClient`` stand-in is the only
    path tests touch."

The :func:`autouse_no_gemini_key` autouse fixture deletes
``GEMINI_API_KEY`` + ``PINATA_API_KEY`` + ``PINATA_SECRET_KEY`` for the
duration of every test in this package, then asserts they are absent.
A buggy test that constructs the real :class:`GeminiClient` /
:class:`IPFSPinner` and calls the hot path will then raise on missing
auth — fail-fast, no $$ leaked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

# Names of every env var the production adapters honour. The autouse
# fixture deletes all of them so a leaked dev-box env cannot influence
# pytest behaviour.
_SECRET_ENV_VARS = (
    "GEMINI_API_KEY",
    "PINATA_API_KEY",
    "PINATA_SECRET_KEY",
    # MiniMax is the user-approved FALLBACK provider for the Gemini seam
    # (sprint follow-up). Strip its key + the per-call config knobs so a
    # leaked dev-box env cannot influence the factory-selection / client
    # tests or trigger a live MiniMax round-trip.
    "MINIMAX_API_KEY",
    "MINIMAX_BASE_URL",
    "MINIMAX_MODEL",
    "MINIMAX_GROUP_ID",
    # Defensive: also strip the forbidden-provider keys so a leaked
    # env on a dev box does NOT mask a bug that would have hit them.
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def autouse_no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete provider API keys for the duration of every test.

    Asserts post-deletion that none survived (defensive against a CI
    environment that exports them via a runner secret). The assertion
    is what makes the brief's "fixture assertion enforces it" criterion
    real — a future contributor cannot silently re-leak the key by
    forgetting to delete it.
    """
    import os

    for name in _SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in _SECRET_ENV_VARS:
        assert name not in os.environ, (
            f"{name} survived monkeypatch.delenv — a real key in the "
            f"test environment risks $$ leak. Unset it before running pytest."
        )


# ---------------------------------------------------------------------------
# Fake LLM client — Protocol-conformant stand-in for tests.
# ---------------------------------------------------------------------------


@dataclass
class FakeGeminiClient:
    """In-memory ``_LLMClient`` Protocol implementation used by every test.

    Same shape as the :class:`_RecordingLLM` already used in the
    sentiment + reflection engine tests, but lives here so the
    LLM-package tests can import a single canonical fake.

    Attributes
    ----------
    responses:
        FIFO queue of scripted responses. Plain dicts are returned
        directly; :class:`BaseException` instances are raised.
    calls:
        Records every ``structured_call`` invocation — model id,
        prompt, schema dict — for post-hoc assertions.
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
                f"FakeGeminiClient exhausted after {len(self.calls)} calls — "
                f"last model={model}. Test wired fewer responses than calls."
            )
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, dict):
            raise AssertionError(
                f"FakeGeminiClient unsupported response type: {type(item).__name__}"
            )
        return item


@pytest.fixture
def fake_gemini_client() -> FakeGeminiClient:
    """Convenience fixture returning a fresh :class:`FakeGeminiClient`."""
    return FakeGeminiClient()


# Tiny helper for tests that need to run an async coroutine without
# pulling in pytest-asyncio (the repo has not adopted it as of v29).
def run_async(coro: Any) -> Any:
    """Run ``coro`` on a fresh event loop and return the result."""
    return asyncio.run(coro)
