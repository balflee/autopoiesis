"""Shared fixtures for the decision-engine test modules.

The exploration-floor suite (``test_decision_exploration.py``) needs a
``decide()`` call that lands EXACTLY on the value-mode ``no-edge`` abstain
(``edge_abs`` just below ``min_edge``) so it can prove the exploration
branch turns that abstain into a flat-stake probe. The fixtures here build
that knife-edge call so the test body reads as behaviour, not arithmetic.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent.engines.base import EngineSignal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
    DecisionEngine,
)

# Value-mode knobs the no-edge fixture is calibrated against. With
# uniform signals at (score, confidence) every channel collapses so
# ``fused == score * confidence`` and (no clamp / no kappa_xm)
# ``edge_abs == kappa * |fused|``. score=0.18, conf=1.0, kappa=0.25 ⇒
# edge_abs = 0.045, which sits JUST below min_edge=0.05 ⇒ NO_BET_NO_EDGE.
_NO_EDGE_KAPPA = 0.25
_NO_EDGE_MIN_EDGE = 0.05
_NO_EDGE_MIN_CONFIDENCE = 0.05
_NO_EDGE_SCORE = 0.18
_NO_EDGE_CONFIDENCE = 1.0


def _sig(score: float, confidence: float = _NO_EDGE_CONFIDENCE) -> EngineSignal:
    return EngineSignal(
        score=score,
        confidence=confidence,
        available_at="2026-06-11T20:00:00+00:00",
        rationale="exploration-fixture",
        raw_features={},
    )


def _uniform_signals(
    score: float, confidence: float = _NO_EDGE_CONFIDENCE
) -> Mapping[str, EngineSignal]:
    return {
        TENNIS_TECHNICAL: _sig(score, confidence),
        MARKET_MOMENTUM: _sig(score, confidence),
        SURFACE_ADVANTAGE: _sig(score, confidence),
        HEAD_TO_HEAD: _sig(score, confidence),
        REST_RECENCY: _sig(score, confidence),
    }


@pytest.fixture
def no_edge_engine_kwargs() -> dict[str, float]:
    """Ctor kwargs that arm the value-mode no-edge gate at the knife edge."""
    return {
        "kappa": _NO_EDGE_KAPPA,
        "min_edge": _NO_EDGE_MIN_EDGE,
        "min_confidence": _NO_EDGE_MIN_CONFIDENCE,
    }


@pytest.fixture
def no_edge_engine(no_edge_engine_kwargs: dict[str, float]) -> DecisionEngine:
    """A value-mode engine whose default decide() lands on NO_BET_NO_EDGE."""
    return DecisionEngine(min_bet_size_usd=5.0, **no_edge_engine_kwargs)


@pytest.fixture
def no_edge_kwargs() -> dict[str, object]:
    """The 10 required ``decide()`` kwargs + ``price`` (value mode).

    Signals are tuned so the value-mode ``edge_abs`` (0.045) sits just
    below ``min_edge`` (0.05) ⇒ the engine abstains with NO_BET_NO_EDGE.
    Breath / bankroll are generous and ``liquidity_cap_usd=1000`` so the
    ONLY thing keeping the engine off a bet is the edge gate — that lets
    the exploration probe clamp to a clean flat stake.
    """
    return {
        "signals": dict(_uniform_signals(_NO_EDGE_SCORE)),
        "weights_alpha": (1 / 3, 1 / 3, 1 / 3),
        "weights_beta": (0.5, 0.5),
        "w_r": 0.5,
        "w_s": 0.5,
        "rho": 1.0,
        "bankroll_usd": 10_000.0,
        "breath": 10_000.0,
        "liquidity_cap_usd": 1_000.0,
        "market_id": "m-explore",
        "price": 0.5,
    }


@pytest.fixture
def missing_signal_kwargs(no_edge_kwargs: dict[str, object]) -> dict[str, object]:
    """Same call, but one engine signal is missing (pre-fusion abstain)."""
    signals = dict(no_edge_kwargs["signals"])  # type: ignore[arg-type]
    signals.pop(REST_RECENCY)
    return {**no_edge_kwargs, "signals": signals}
