"""Production LLM plumbing for the Genesis Autopoiesis agent.

This package wires the *runtime* (deployed-agent) LLM dependencies that
sit behind the ``_LLMClient`` Protocol declared in
:mod:`agent.engines.sentiment_llm` and :mod:`agent.engines.reflection`.
The dev tooling (orchestrator subagents) does NOT touch this code —
that layer goes through Claude Code. See ``.env.example`` for the
two-layer split rationale.

Provider rule (track-b-backend.md Rule 7, authoritative):

* Production LLM is **Gemini 3.1 Flash Lite** via the ``google-genai``
  SDK + AI Studio (env var ``GEMINI_API_KEY``).
* The engines consume an SDK-agnostic ``_LLMClient`` Protocol whose
  single method is ``structured_call(*, model, prompt, schema) -> dict``.
* The concrete adapter is :class:`GeminiClient` wrapping the
  ``response_schema`` (structured JSON) mode against the model id
  ``gemini-3.1-flash-lite``.
* Malformed output ⇒ retry once ⇒ fail-soft to a deterministic default.
  Retry-once is the engine's job; this layer surfaces a plain dict (or
  raises ``ValueError`` / ``json.JSONDecodeError``) and lets the engine
  decide how to recover.
* **NEVER** import ``anthropic`` or ``openai`` anywhere under
  ``agent/``. The test
  :mod:`tests.agent.llm.test_no_forbidden_imports` is the AST scan
  that enforces this hard policy.
* Tests inject a Protocol-conformant fake; no real Gemini call under
  pytest. ``conftest.py`` leaves ``GEMINI_API_KEY`` unset so a buggy
  test that DID hit the real SDK would fail-fast on auth — not silently
  charge $$.

Modules
-------

:mod:`agent.llm.gemini_client`
    Concrete ``google-genai`` adapter implementing the engine
    ``_LLMClient`` Protocol.

:mod:`agent.llm.model_router`
    Dispatch table for picking the model id. Defaults to
    ``gemini-3.1-flash-lite`` and is designed extensible for tier
    escalation (Pro / Ultra) but ships single-model for v1.

:mod:`agent.llm.cost_guard`
    Running USD budget tracker for the reflection / sentiment LLM
    spend. Hard cap $25 per TECHNICAL_PLAN §15 Gap 5; warning event at
    80% of cap; hard short-circuit to template at 100%.

:mod:`agent.llm.ipfs_pinner`
    Wraps the Pinata REST API (free tier — 1 GB / 1000 pins per
    SETUP_CHECKLIST.md §P1) to pin the per-tick reflection markdown
    blob and return a CIDv1 string. Returns ``None`` on a Pinata 503
    after 3 retries so the caller can continue the tick — TECHNICAL_PLAN
    §4.6 'Agent must survive' invariant.

:mod:`agent.llm._phase_activation`
    One-shot emitter for the D11 'LLM activated' event (PRD §4.4).
    Writes to the memory_bank via the existing :class:`MemoryBank`
    atomic temp+rename pattern (``os.replace``) — no new disk-write
    primitive is introduced.
"""

from __future__ import annotations

from agent.llm._phase_activation import PhaseActivationEmitter, PhaseActivationEvent
from agent.llm.cost_guard import CostExhaustedError, CostGuard
from agent.llm.gemini_client import DEFAULT_GEMINI_MODEL, GeminiClient
from agent.llm.ipfs_pinner import IPFSPinner
from agent.llm.model_router import ModelRouter
from agent.llm.prompts import (
    LAST_WORDS_BREATH_COST,
    LastWordsCache,
    LastWordsResponse,
    LastWordsService,
)

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "LAST_WORDS_BREATH_COST",
    "CostExhaustedError",
    "CostGuard",
    "GeminiClient",
    "IPFSPinner",
    "LastWordsCache",
    "LastWordsResponse",
    "LastWordsService",
    "ModelRouter",
    "PhaseActivationEmitter",
    "PhaseActivationEvent",
]
