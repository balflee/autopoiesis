"""Value-betting mode (T-V3): market-prior+tilt p_model, EV gate, side-aware
floor, odds-aware Kelly. Legacy mode (``price=None``) must stay byte-identical.

Design (plan 2026-06-11-value-betting-physics):

* ``p_model = clamp(price + kappa * fused, 0, 1)`` — the model anchors on the
  MARKET price and tilts by the signal. Zero signal ⇒ zero edge ⇒ abstain
  (a ``(1+fused)/2`` anchor would systematically fade favorites on no
  information — the anti-fade test below locks this in).
* side = sign of ``edge_yes = p_model - price`` (≡ sign(fused) except
  clamp-to-zero at the price boundaries).
* odds-aware Kelly ``f* = edge / (1 - q)`` with ``q`` = the chosen side's
  effective price.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agent.core.state import ActionKind, Side
from agent.engines.base import EngineSignal
from agent.engines.decision import (
    CROWD_VOLUME,
    MARKET_MOMENTUM,
    NO_BET_NO_EDGE,
    NO_BET_PRICE_FLOOR,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
    DecisionEngine,
    _value_kelly_fraction,
)


def _sig(score: float, confidence: float = 0.8) -> EngineSignal:
    return EngineSignal(
        score=score,
        confidence=confidence,
        available_at="2026-06-11T20:00:00+00:00",
        rationale="test",
        raw_features={},
    )


def _uniform_signals(score: float, confidence: float = 0.8) -> Mapping[str, EngineSignal]:
    return {
        TENNIS_TECHNICAL: _sig(score, confidence),
        MARKET_MOMENTUM: _sig(score, confidence),
        SMART_MONEY: _sig(score, confidence),
        SENTIMENT_LLM: _sig(score, confidence),
        CROWD_VOLUME: _sig(score, confidence),
    }


_CALL = dict(
    weights_alpha=(1 / 3, 1 / 3, 1 / 3),
    weights_beta=(0.5, 0.5),
    w_r=0.5,
    w_s=0.5,
    rho=1.0,
    bankroll_usd=100.0,
    breath=100.0,
    liquidity_cap_usd=5.0,
    market_id="m1",
)


def _engine(**kw: object) -> DecisionEngine:
    return DecisionEngine(min_bet_size_usd=1.0, min_confidence=0.0, **kw)  # type: ignore[arg-type]


# ── _value_kelly_fraction unit cases ─────────────────────────────────


def test_value_kelly_formula_edge_over_one_minus_cost() -> None:
    assert _value_kelly_fraction(edge=0.10, effective_price=0.60) == pytest.approx(
        0.10 / 0.40
    )


def test_value_kelly_zero_or_negative_edge_is_zero() -> None:
    assert _value_kelly_fraction(edge=0.0, effective_price=0.5) == 0.0
    assert _value_kelly_fraction(edge=-0.1, effective_price=0.5) == 0.0


def test_value_kelly_cost_at_one_saturates() -> None:
    assert _value_kelly_fraction(edge=0.1, effective_price=1.0) == 1.0


# ── value mode behavior ──────────────────────────────────────────────


def test_neutral_signal_abstains_even_at_extreme_price() -> None:
    """ANTI-FADE: p_model anchors on price, so zero fused ⇒ zero edge ⇒
    NO_BET — never a fade-the-favorite bet on no information."""
    action = asyncio.run(
        _engine(min_edge=0.01, kappa=0.25).decide(
            signals=dict(_uniform_signals(0.0)), price=0.90, **_CALL
        )
    )
    assert action.kind is ActionKind.NO_BET
    assert action.no_bet_reason is not None


def test_positive_signal_bets_yes_with_true_edge() -> None:
    action = asyncio.run(
        _engine(min_edge=0.0, kappa=0.25).decide(
            signals=dict(_uniform_signals(0.5)), price=0.50, **_CALL
        )
    )
    assert action.kind is ActionKind.BET and action.side is Side.YES
    assert action.edge_pct is not None and 0.0 < action.edge_pct <= 0.25


def test_negative_signal_bets_no() -> None:
    action = asyncio.run(
        _engine(min_edge=0.0, kappa=0.25).decide(
            signals=dict(_uniform_signals(-0.5)), price=0.50, **_CALL
        )
    )
    assert action.kind is ActionKind.BET and action.side is Side.NO


def test_min_edge_gates() -> None:
    # kappa=0.25 and |fused| <= 1 means edge can never reach 0.30.
    action = asyncio.run(
        _engine(min_edge=0.30, kappa=0.25).decide(
            signals=dict(_uniform_signals(0.5)), price=0.50, **_CALL
        )
    )
    assert action.kind is ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert action.no_bet_reason.startswith(NO_BET_NO_EDGE)


def test_effective_floor_blocks_cheap_no_side() -> None:
    # NO at yes-price 0.97 ⇒ effective price 0.03 < floor 0.05 ⇒ NO_BET.
    action = asyncio.run(
        _engine(min_edge=0.0, kappa=0.25, entry_price_floor=0.05).decide(
            signals=dict(_uniform_signals(-0.5)), price=0.97, **_CALL
        )
    )
    assert action.kind is ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert action.no_bet_reason.startswith(NO_BET_PRICE_FLOOR)


def test_p_model_clamped_at_one() -> None:
    # price 0.95 + 0.5*fused(≈0.8) clamps p_model at 1.0 ⇒ edge = 0.05, YES.
    action = asyncio.run(
        _engine(min_edge=0.0, kappa=0.5).decide(
            signals=dict(_uniform_signals(1.0)), price=0.95, **_CALL
        )
    )
    assert action.kind is ActionKind.BET and action.side is Side.YES
    assert action.edge_pct == pytest.approx(0.05)


def test_legacy_mode_untouched_without_price() -> None:
    """price=None ⇒ identical to today's behavior: side=sign(fused),
    edge_pct=|fused| (legacy semantics)."""
    action = asyncio.run(
        _engine().decide(signals=dict(_uniform_signals(0.5)), **_CALL)
    )
    assert action.kind is ActionKind.BET and action.side is Side.YES
    # |fused| for uniform score 0.5 / conf 0.8 with these weights = 0.4.
    assert action.edge_pct == pytest.approx(0.4)


def test_ctor_validation() -> None:
    with pytest.raises(ValueError):
        DecisionEngine(min_edge=-0.1)
    with pytest.raises(ValueError):
        DecisionEngine(kappa=0.0)
    with pytest.raises(ValueError):
        DecisionEngine(kappa=1.5)
    with pytest.raises(ValueError):
        DecisionEngine(entry_price_floor=1.0)
