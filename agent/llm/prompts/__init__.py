"""LLM prompt templates + structured-output schemas.

This subpackage owns the *prompt* surface — each module exports a
Jinja-style ``render(...)`` template, a Pydantic schema for the
structured-output response, and helpers for caching one-shot outputs.

Sprint_5 (T-B-009) ships :mod:`agent.llm.prompts.last_words` — the
one-per-lifetime terminal reflection.
"""

from __future__ import annotations

from agent.llm.prompts.last_words import (
    LAST_WORDS_BREATH_COST,
    LAST_WORDS_FILENAME,
    LAST_WORDS_MODEL,
    LastWordsCache,
    LastWordsResponse,
    LastWordsService,
    render_last_words_prompt,
)

__all__ = [
    "LAST_WORDS_BREATH_COST",
    "LAST_WORDS_FILENAME",
    "LAST_WORDS_MODEL",
    "LastWordsCache",
    "LastWordsResponse",
    "LastWordsService",
    "render_last_words_prompt",
]
