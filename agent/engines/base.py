"""Shared engine plumbing — ABC + Signal payload + PIT chokepoint re-export.

PRD §4 defines five canonical signal engines (NBA技术, 盘口动量, Smart Money,
LLM情绪, Reddit关注度). They all produce the same normalised payload —
:class:`Signal` (alias :class:`EngineSignal`) — so the downstream fusion
layer (:mod:`agent.engines.decision`, lands T-B-003) consumes one shape
regardless of which stream produced it.

Sprint_3 (T-B-002) promotes :class:`Engine` from a structural ``Protocol``
to a real :class:`abc.ABC`. The brief's hard rule "All 5 engines inherit
from ``agent/engines/base.Engine``" requires nominal inheritance so the
look-ahead auditor + the fusion layer can use ``isinstance(eng, Engine)``
as a runtime gate. The Protocol is retained as :class:`EngineProtocol`
for callers that want structural typing.

Re-exports :class:`LookaheadError` so engines have a single import path
for the PIT chokepoint — the look-ahead auditor greps ``LookaheadError``
in this module to confirm engines surface the canonical exception.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Re-export so every engine has a single import surface for the
# point-in-time chokepoint. Per the brief: "Each engine accepts an
# ``asof_ts`` parameter and raises :class:`LookaheadError` ... if
# downstream data has future leakage."
from data.etl.pit_correct import LookaheadError, assert_no_lookahead, require_asof_ts


class Signal(BaseModel):
    """Normalised engine output payload — wire schema is
    ``.dev/contracts/engine_signal.v0.1.0.json``.

    * ``score`` ∈ [-1, 1] — positive supports BET on the prior side,
      negative supports BET against. Magnitude is the engine's edge.
    * ``confidence`` ∈ [0, 1] — engine self-rated reliability. The
      decision layer weights each engine's score by its confidence
      before fusion.
    * ``available_at`` — UTC ISO-8601 stamp at which every feature this
      score depends on was available. The look-ahead auditor reads this
      to enforce ``available_at <= asof_ts`` per PRD §14.1.
    * ``rationale`` — short text the reflection step can quote.
    * ``raw_features`` — flat name→float mapping of the inputs that
      produced the score. Sprint_3 brief acceptance criterion: every
      engine must surface its feature inputs so the reflection layer
      + the weight updater + reviewers can audit what each engine saw.
    """

    model_config = ConfigDict(extra="forbid")

    score: Annotated[float, Field(ge=-1.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    available_at: str
    rationale: str = ""
    raw_features: dict[str, float] = Field(default_factory=dict)


# Back-compat alias — sprint_1 callers import :class:`EngineSignal`.
# Keep both names live so the rename does not bump the schema major.
EngineSignal = Signal


class Engine(ABC):
    """ABC every signal engine inherits from.

    Subclasses MUST:

    * Set ``name: str`` — used as the dict key in
      :func:`asyncio.gather` parallel fanout and as the
      ``engine_signal.engine_name`` field in the wire schema.
    * Implement :meth:`evaluate` — the single canonical entrypoint.
      ``target`` is a string identifier (market slug, game id,
      subreddit name — engine-specific). ``asof_ts`` is the
      timezone-aware PIT cutoff; a naive or missing cutoff raises
      :class:`LookaheadError` upstream in
      :func:`data.etl.pit_correct.require_asof_ts`.
    """

    name: str = "base"

    @abstractmethod
    async def evaluate(self, *, target: str, asof_ts: datetime) -> Signal:
        """Produce a :class:`Signal` for ``target`` as of ``asof_ts``.

        Implementations MUST refuse to surface features whose
        ``available_at > asof_ts``. The conventional path is to build a
        per-feature DataFrame (or analogue) and pipe it through
        :func:`data.etl.pit_correct.assert_no_lookahead` BEFORE
        computing the score — defence-in-depth on top of the per-row
        filtering the data sources already do.
        """


class EngineProtocol(Protocol):
    """Structural alternative to :class:`Engine` for callers that want
    duck typing (e.g. test doubles that don't inherit from the ABC)."""

    name: str

    async def evaluate(self, *, target: str, asof_ts: datetime) -> Signal:
        ...


__all__ = [
    "Engine",
    "EngineProtocol",
    "EngineSignal",
    "LookaheadError",
    "Signal",
    "assert_no_lookahead",
    "require_asof_ts",
]
