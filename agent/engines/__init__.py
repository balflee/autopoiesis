# Greek letters (α, β) mirror PRD §4 / §6.6 notation.
"""Decision engines wired into the agent loop.

Eight canonical modules per TECHNICAL_PLAN §4 file tree:

* :mod:`agent.engines.tennis_technical` — α₁ Tennis (sprint_7 sport
  pivot per PRD §15 已决 #8; the previous ``nba_technical`` is
  removed in lockstep so α₁'s identity is unambiguous). T-B-014
  ships the 5 analytical primitives (compute_elo_diff,
  compute_surface_advantage, compute_h2h, compute_best_of_factor,
  compute_days_since_last_match); the Engine-subclass wrapper that
  fuses them into a Signal lands in T-B-015 Phase 1 training.
* :mod:`agent.engines.market_momentum` — α₂ 盘口动量
* :mod:`agent.engines.smart_money` — α₃ Smart Money
* :mod:`agent.engines.sentiment_llm` — β₁ LLM情绪 (Phase 1 frozen to 0)
* :mod:`agent.engines.crowd_volume` — β₂ Reddit关注度 / volume
* :mod:`agent.engines.decision` — 2-layer fusion + bet-size clamping
* :mod:`agent.engines.reflection` — Claude reflection pipeline
* :mod:`agent.engines.weight_updater` — softmax-reparam SGD
"""

from __future__ import annotations

from agent.engines import tennis_technical
from agent.engines.base import (
    Engine,
    EngineProtocol,
    EngineSignal,
    LookaheadError,
    Signal,
)
from agent.engines.crowd_volume import CrowdVolumeEngine
from agent.engines.decision import DecisionEngine, FusionResult
from agent.engines.market_momentum import MarketMomentumEngine
from agent.engines.reflection import (
    REFLECTION_WEIGHT_KEYS,
    ReflectionEngine,
    ReflectionRecord,
    SandboxReflectionRecord,
)
from agent.engines.sentiment_llm import SentimentLLMEngine
from agent.engines.smart_money import SmartMoneyEngine
from agent.engines.weight_updater import WeightUpdater

__all__ = [
    "REFLECTION_WEIGHT_KEYS",
    "CrowdVolumeEngine",
    "DecisionEngine",
    "Engine",
    "EngineProtocol",
    "EngineSignal",
    "FusionResult",
    "LookaheadError",
    "MarketMomentumEngine",
    "ReflectionEngine",
    "ReflectionRecord",
    "SandboxReflectionRecord",
    "SentimentLLMEngine",
    "Signal",
    "SmartMoneyEngine",
    "WeightUpdater",
    "tennis_technical",
]
