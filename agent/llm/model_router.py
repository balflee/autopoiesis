"""ModelRouter — pick the Gemini model id for a given engine call.

T-B-006 ships a *single-model* router: every engine task routes to
``gemini-3.1-flash-lite``. The class is intentionally tier-aware so
later sprints can promote key-moment reflections to a heavier model
without rewriting the engine call sites.

Historical note (TECHNICAL_PLAN §15 Gap 5 superseded by v29 commit
``bbd1944``):

    Earlier drafts of TECHNICAL_PLAN §15 named ``claude-sonnet-4-6`` and
    ``claude-opus-4-7`` as the routine and key-moment models. That
    section was retired in v29 — the User decision was to consolidate
    on a single provider (Gemini via AI Studio) to (1) avoid juggling
    two API keys for a hackathon, (2) stay under the cost cap with the
    cheaper Flash Lite tier, and (3) keep the SDK surface single-vendor
    so the ``test_no_forbidden_imports`` AST scan can enforce the
    policy with a one-rule check. The retry-once + fail-soft + $25
    USD budget cap policy carry over unchanged.

Design
------

The router exposes :meth:`ModelRouter.model_for` which takes a *task*
descriptor and a *key_moment* flag and returns a model id string. The
return value is consumed by the engine's ``model=`` argument when it
calls :meth:`_LLMClient.structured_call`. Keeping the routing inside
this class (rather than spreading literal strings across the engines)
means a later calibration sprint can swap the dispatch table without
touching the engines.
"""

from __future__ import annotations

from typing import Final, Literal

from agent.llm.gemini_client import DEFAULT_GEMINI_MODEL

# Canonical task surfaces — kept as a Literal so mypy --strict catches
# a typo in either the caller or the router's dispatch table. The v1
# router does NOT actually branch on ``task`` (single-model dispatch);
# the axis exists so a later calibration sprint can diverge per task
# (e.g. reflection escalates but sentiment never does) without
# changing the public surface.
TaskKind = Literal["sentiment", "reflection"]

# Single source of truth for the *escalated* (key-moment) model id. The
# current dispatch table maps both routine + escalated to the same model id
# (the default ``gemini-3.5-flash``); the constant exists so future
# calibration sprints can bump it (e.g. to a heavier tier) without touching
# engine code. Kept aliased to :data:`DEFAULT_GEMINI_MODEL` so the v1
# single-model invariant holds even when the default is retuned.
ESCALATED_GEMINI_MODEL: Final[str] = DEFAULT_GEMINI_MODEL


class ModelRouter:
    """Resolves a Gemini model id for an engine call.

    Parameters
    ----------
    routine_model:
        Model id used for non-key-moment calls (every routine tick of
        the sentiment + reflection engines). Defaults to
        :data:`agent.llm.gemini_client.DEFAULT_GEMINI_MODEL`.

    escalated_model:
        Model id used when ``key_moment=True`` (phase transition, big
        loss, win streak). Defaults to :data:`ESCALATED_GEMINI_MODEL`
        which currently aliases the routine model; a calibration sprint
        can swap it to a heavier tier.

    Examples
    --------
    >>> router = ModelRouter()
    >>> router.model_for(task="sentiment", key_moment=False)
    'gemini-3.1-flash-lite'
    >>> router.model_for(task="reflection", key_moment=True)
    'gemini-3.1-flash-lite'

    A future calibration override:

    >>> override = ModelRouter(escalated_model="gemini-3.1-pro")
    >>> override.model_for(task="reflection", key_moment=True)
    'gemini-3.1-pro'
    """

    def __init__(
        self,
        *,
        routine_model: str = DEFAULT_GEMINI_MODEL,
        escalated_model: str = ESCALATED_GEMINI_MODEL,
    ) -> None:
        self._routine_model = routine_model
        self._escalated_model = escalated_model

    def model_for(self, *, task: TaskKind, key_moment: bool = False) -> str:
        """Return the model id to use for ``task``.

        ``task`` and ``key_moment`` are kept as separate axes (rather
        than collapsed to a single tier) so the dispatch table can
        diverge per task in a later sprint (e.g. reflection escalates
        but sentiment never does). v1 ignores ``task`` and branches
        only on ``key_moment``.
        """
        del task  # captured for the API contract; v1 dispatch is task-agnostic
        return self._escalated_model if key_moment else self._routine_model


__all__ = [
    "ESCALATED_GEMINI_MODEL",
    "ModelRouter",
    "TaskKind",
]
