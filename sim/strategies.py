"""Archetype strategies — sprint_1 stubs.

Three archetypes drive the calibration sweep, per PRD §14.1 and the
Track C validator's anti-laziness rule (DEV_FRAMEWORK §26 T2.7):

* :class:`Pessimist` — refuses to bet unless edge is large; lifetime
  bounded by passive burn.
* :class:`Optimist` — bets enthusiastically when edge is positive;
  drawdowns are accepted.
* :class:`Satisficer` — accepts the first edge meeting a low
  threshold; the validator REQUIRES this archetype dies faster than
  :class:`Optimist`, otherwise the calibration is rejected as
  encouraging laziness.

Sprint_1 ships the class hierarchy + the ``decide`` signature only.
Each ``decide`` body raises ``NotImplementedError`` until the runner
in sprint_2 (T-C-004) provides the market context object.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyDecision:
    """Sprint_1 placeholder shape for a strategy's per-tick decision.

    The sprint_2 runner will replace ``payload`` with a concrete
    BET/NO_BET enum mirroring ``agent.core.state.Action``; we keep the
    type as ``Any`` here so the stub typechecks under ``mypy --strict``
    without pulling in agent.core (cross-track import).
    """

    archetype: str
    payload: Any


class Strategy(ABC):
    """Abstract base for all archetype strategies."""

    archetype: str = "abstract"

    @abstractmethod
    def decide(self, context: Any) -> StrategyDecision:
        """Produce a decision for the current tick.

        ``context`` carries the market snapshot + agent vitals; its
        concrete type is finalised in sprint_2 (T-C-004) when the
        runner lands. Until then the parameter is ``Any`` so mypy can
        still typecheck the signature without forward references.
        """


class Pessimist(Strategy):
    """High-edge-only bettor. Stub raises until T-C-004."""

    archetype = "pessimist"

    def decide(self, context: Any) -> StrategyDecision:
        raise NotImplementedError("Pessimist.decide lands in sprint_2 (T-C-004)")


class Optimist(Strategy):
    """Enthusiastic bettor. Stub raises until T-C-004."""

    archetype = "optimist"

    def decide(self, context: Any) -> StrategyDecision:
        raise NotImplementedError("Optimist.decide lands in sprint_2 (T-C-004)")


class Satisficer(Strategy):
    """First-edge-good-enough bettor. Anti-laziness invariant:
    Satisficer MUST die faster than Optimist (PRD §14.2 +
    DEV_FRAMEWORK §26 T2.7)."""

    archetype = "satisficer"

    def decide(self, context: Any) -> StrategyDecision:
        raise NotImplementedError("Satisficer.decide lands in sprint_2 (T-C-004)")


# Canonical sweep tuple. The runner imports this so dropping or
# reordering an archetype here is a single-source change. The
# calibration validator FAILS the run if any of these three is missing
# from the sweep output (PRD §14.2 + Track C Hard Rule #2).
ARCHETYPES: tuple[type[Strategy], ...] = (Pessimist, Optimist, Satisficer)
