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


# ── A9 storm percept: γ gate wire + diagnostics (plan 2026-06-13) ────


def test_storm_gate_tightens_min_edge() -> None:
    """γ=+0.1, storm=1.0: a bet whose edge clears min_edge at storm 0 is
    BLOCKED at storm 1 (eff_min_edge = 0.05 + 0.1·1 = 0.15 > 0.08)."""
    eng = _engine(min_edge=0.05, kappa=0.25, gate_storm_sensitivity=0.1)
    calm = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.0, **_CALL
        )
    )
    assert calm.kind is ActionKind.BET
    assert calm.edge_pct == pytest.approx(0.08)
    stormy = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=1.0, **_CALL
        )
    )
    assert stormy.kind is ActionKind.NO_BET
    assert stormy.no_bet_reason is not None
    assert stormy.no_bet_reason.startswith(NO_BET_NO_EDGE)


def test_storm_gate_negative_gamma_loosens() -> None:
    """Sign-symmetric: γ=−0.1, storm=1.0 ⇒ eff_min_edge = max(0, 0.1−0.1)
    = 0 — a bet blocked at storm 0 passes in the storm."""
    eng = _engine(min_edge=0.1, kappa=0.25, gate_storm_sensitivity=-0.1)
    calm = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.0, **_CALL
        )
    )
    assert calm.kind is ActionKind.NO_BET
    stormy = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=1.0, **_CALL
        )
    )
    assert stormy.kind is ActionKind.BET


def test_storm_scales_rho() -> None:
    """γ2=0.5, storm=1.0 halves rho_eff ⇒ desired-bound size halves."""
    call = dict(_CALL, liquidity_cap_usd=1000.0, bankroll_usd=100.0)
    eng = _engine(min_edge=0.0, kappa=0.25, risk_storm_sensitivity=0.5)
    calm = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.0, **call
        )
    )
    stormy = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=1.0, **call
        )
    )
    assert calm.kind is ActionKind.BET and stormy.kind is ActionKind.BET
    assert calm.size_usd is not None and stormy.size_usd is not None
    assert stormy.size_usd == pytest.approx(calm.size_usd / 2)


def test_storm_zero_gamma_is_identity() -> None:
    """γ=γ2=0 (defaults): storm=1.0 is arithmetically byte-identical to
    storm=0.0 (x + 0·storm == x; rho·(1−0·storm) == rho)."""
    eng = _engine(min_edge=0.05, kappa=0.25)
    a = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.0, **_CALL
        )
    )
    b = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=1.0, **_CALL
        )
    )
    assert a == b


def test_storm_non_finite_normalized_to_zero() -> None:
    """NaN/inf storm is normalized to 0.0 — a NaN can never poison the
    γ=0 identity nor a γ>0 gate."""
    eng = _engine(min_edge=0.05, kappa=0.25, gate_storm_sensitivity=0.1)
    base = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.0, **_CALL
        )
    )
    for bad in (float("nan"), float("inf"), float("-inf")):
        got = asyncio.run(
            eng.decide(
                signals=dict(_uniform_signals(0.4)), price=0.5, storm=bad, **_CALL
            )
        )
        assert got == base


def test_storm_clamped_to_unit_interval() -> None:
    """storm is clamped to [0, 1]: storm=5.0 behaves as storm=1.0."""
    eng = _engine(min_edge=0.05, kappa=0.25, gate_storm_sensitivity=0.1)
    big = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=5.0, **_CALL
        )
    )
    one = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=1.0, **_CALL
        )
    )
    assert big == one


def test_storm_ctor_validation() -> None:
    with pytest.raises(ValueError):
        DecisionEngine(gate_storm_sensitivity=1.5)
    with pytest.raises(ValueError):
        DecisionEngine(gate_storm_sensitivity=float("nan"))
    with pytest.raises(ValueError):
        DecisionEngine(risk_storm_sensitivity=-1.5)
    with pytest.raises(ValueError):
        DecisionEngine(risk_storm_sensitivity=float("inf"))


def test_gate_diagnostics_populated_on_value_bet() -> None:
    # storm 0.25: eff_min_edge = 0.05 + 0.1·0.25 = 0.075 < edge 0.08 ⇒ BET.
    eng = _engine(min_edge=0.05, kappa=0.25, gate_storm_sensitivity=0.1)
    action = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.25, **_CALL
        )
    )
    assert action.kind is ActionKind.BET
    d = eng.last_gate_diagnostics
    assert d is not None
    assert d.storm == pytest.approx(0.25)
    assert d.edge_abs == pytest.approx(0.08)
    assert d.min_edge_base == pytest.approx(0.05)
    assert d.gamma == pytest.approx(0.1)
    assert d.eff_min_edge == pytest.approx(0.075)


def test_gate_diagnostics_populated_on_gated_no_bet() -> None:
    """The min_edge gate fires AFTER diagnostics exist — gated abstains
    still record what the gate saw."""
    eng = _engine(min_edge=0.05, kappa=0.25, gate_storm_sensitivity=0.1)
    action = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=1.0, **_CALL
        )
    )
    assert action.kind is ActionKind.NO_BET
    d = eng.last_gate_diagnostics
    assert d is not None and d.eff_min_edge == pytest.approx(0.15)


def test_gate_diagnostics_cleared_on_pre_edge_abstains() -> None:
    """Missing-signal and low-confidence paths return BEFORE the edge gate
    and must never leave STALE diagnostics behind (r7 L-4)."""
    eng = _engine(min_edge=0.05, kappa=0.25, gate_storm_sensitivity=0.1)
    # Seed stale diagnostics with a successful value BET.
    asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.5, **_CALL
        )
    )
    assert eng.last_gate_diagnostics is not None
    # Missing-signal path.
    asyncio.run(eng.decide(signals={}, price=0.5, storm=0.5, **_CALL))
    assert eng.last_gate_diagnostics is None
    # Re-seed, then low-confidence path.
    asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.5, **_CALL
        )
    )
    assert eng.last_gate_diagnostics is not None
    strict = DecisionEngine(
        min_bet_size_usd=1.0,
        min_confidence=0.9,
        min_edge=0.05,
        kappa=0.25,
        gate_storm_sensitivity=0.1,
    )
    asyncio.run(
        strict.decide(
            signals=dict(_uniform_signals(0.4, confidence=0.1)),
            price=0.5,
            storm=0.5,
            **_CALL,
        )
    )
    assert strict.last_gate_diagnostics is None


def test_gate_diagnostics_none_in_legacy_mode() -> None:
    """price=None (legacy) has no min-edge gate — diagnostics stay None
    even after a prior value call populated them."""
    eng = _engine(min_edge=0.05, kappa=0.25)
    asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)), price=0.5, storm=0.0, **_CALL
        )
    )
    assert eng.last_gate_diagnostics is not None
    asyncio.run(eng.decide(signals=dict(_uniform_signals(0.4)), **_CALL))
    assert eng.last_gate_diagnostics is None


# ── kappa_xm: additive cross-market scalar (B′, plan 2026-06-14) ──────


def test_kappa_xm_zero_is_valid_ctor() -> None:
    """kappa_xm=0.0 MUST construct without raising (unlike kappa whose guard
    rejects 0 — copying kappa's guard would break every baseline/control
    engine that explicitly passes kappa_xm=0)."""
    eng = _engine(kappa=0.25, kappa_xm=0.0)
    assert eng is not None


def test_kappa_xm_below_zero_raises() -> None:
    with pytest.raises(ValueError, match="kappa_xm"):
        DecisionEngine(kappa_xm=-0.01)


def test_kappa_xm_above_one_raises() -> None:
    with pytest.raises(ValueError, match="kappa_xm"):
        DecisionEngine(kappa_xm=1.01)


def test_byte_identity_kappa_xm_zero_any_cross_signal() -> None:
    """Engine with kappa_xm=0.0 + decide(..., cross_market_signal=0.7)
    must return the SAME Action as the same engine with cross_market_signal
    omitted (0.0 default). The added term is 0*0.7 = 0."""
    eng = _engine(kappa=0.25, kappa_xm=0.0)
    baseline = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)),
            price=0.5,
            cross_market_signal=0.0,
            **_CALL,
        )
    )
    with_signal = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.4)),
            price=0.5,
            cross_market_signal=0.7,
            **_CALL,
        )
    )
    assert baseline == with_signal


def test_byte_identity_kappa_xm_nonzero_signal_zero() -> None:
    """kappa_xm > 0 but cross_market_signal=0.0: added term = kappa_xm*0 = 0.
    Action must equal kappa_xm=0 engine with the same signal=0."""
    eng_zero = _engine(kappa=0.25, kappa_xm=0.0)
    eng_nonzero = _engine(kappa=0.25, kappa_xm=0.5)
    a = asyncio.run(
        eng_zero.decide(
            signals=dict(_uniform_signals(0.4)),
            price=0.5,
            cross_market_signal=0.0,
            **_CALL,
        )
    )
    b = asyncio.run(
        eng_nonzero.decide(
            signals=dict(_uniform_signals(0.4)),
            price=0.5,
            cross_market_signal=0.0,
            **_CALL,
        )
    )
    assert a == b


def test_byte_identity_no_kappa_xm_kwarg_vs_zero() -> None:
    """An engine built WITHOUT the kappa_xm kwarg (default=0.0) must produce
    the same Action as one built WITH kappa_xm=0.0 for any cross_market_signal.
    This is the pre-change / post-change byte-identity contract."""
    eng_old = _engine(kappa=0.25)          # no kappa_xm kwarg
    eng_new = _engine(kappa=0.25, kappa_xm=0.0)
    sigs = dict(_uniform_signals(0.5))
    a = asyncio.run(
        eng_old.decide(signals=sigs, price=0.5, **_CALL)
    )
    b = asyncio.run(
        eng_new.decide(signals=sigs, price=0.5, cross_market_signal=0.9, **_CALL)
    )
    assert a == b


def test_active_positive_cross_market_signal_bets_yes() -> None:
    """With neutral fused (score=0 → p_model=price with no kappa_xm), adding
    kappa_xm=0.5 * cross_market_signal=+1.0 shifts p_model from 0.5 to 1.0
    (clamped) → YES bet; without kappa_xm the zero-fused would be NO_BET."""
    eng = _engine(kappa=0.25, kappa_xm=0.5, min_edge=0.0)
    action = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.0)),  # fused = 0
            price=0.5,
            cross_market_signal=1.0,
            **_CALL,
        )
    )
    assert action.kind is ActionKind.BET
    assert action.side is Side.YES
    # edge_yes = clamp(0.5 + 0.5*1.0, 0, 1) - 0.5 = 0.5
    assert action.edge_pct == pytest.approx(0.5)


def test_active_negative_cross_market_signal_bets_no() -> None:
    """Negative cross_market_signal with neutral fused shifts p_model below
    price → NO side."""
    eng = _engine(kappa=0.25, kappa_xm=0.5, min_edge=0.0)
    action = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.0)),
            price=0.5,
            cross_market_signal=-1.0,
            **_CALL,
        )
    )
    assert action.kind is ActionKind.BET
    assert action.side is Side.NO


def test_active_kappa_xm_full_signal_saturates_p_model() -> None:
    """price=0.5, kappa_xm=0.5, cross_market_signal=+1, fused=0:
    un-clamped p_model = 0.5 + 0*kappa + 0.5*1 = 1.0 → clamped 1.0,
    edge_yes = 1.0 - 0.5 = 0.5, YES side."""
    eng = _engine(kappa=0.25, kappa_xm=0.5, min_edge=0.0)
    action = asyncio.run(
        eng.decide(
            signals=dict(_uniform_signals(0.0)),
            price=0.5,
            cross_market_signal=1.0,
            **_CALL,
        )
    )
    assert action.kind is ActionKind.BET
    assert action.side is Side.YES
    assert action.edge_pct == pytest.approx(0.5)


def test_cross_market_signal_clamped_above_one() -> None:
    """cross_market_signal > 1.0 is clamped to 1.0: the result must match
    passing 1.0 directly."""
    eng = _engine(kappa=0.25, kappa_xm=0.5, min_edge=0.0)
    sigs = dict(_uniform_signals(0.0))
    clamped = asyncio.run(
        eng.decide(signals=sigs, price=0.5, cross_market_signal=1.0, **_CALL)
    )
    above = asyncio.run(
        eng.decide(signals=sigs, price=0.5, cross_market_signal=5.0, **_CALL)
    )
    assert clamped == above


def test_cross_market_signal_clamped_below_minus_one() -> None:
    """cross_market_signal < -1.0 is clamped to -1.0."""
    eng = _engine(kappa=0.25, kappa_xm=0.5, min_edge=0.0)
    sigs = dict(_uniform_signals(0.0))
    clamped = asyncio.run(
        eng.decide(signals=sigs, price=0.5, cross_market_signal=-1.0, **_CALL)
    )
    below = asyncio.run(
        eng.decide(signals=sigs, price=0.5, cross_market_signal=-5.0, **_CALL)
    )
    assert clamped == below


def test_cross_market_signal_nan_inf_treated_as_zero() -> None:
    """NaN and inf in cross_market_signal must be sanitized to 0.0 — a
    non-finite value must never poison the p_model arithmetic."""
    eng = _engine(kappa=0.25, kappa_xm=0.5, min_edge=0.0)
    sigs = dict(_uniform_signals(0.0))
    base = asyncio.run(
        eng.decide(signals=sigs, price=0.5, cross_market_signal=0.0, **_CALL)
    )
    for bad in (float("nan"), float("inf"), float("-inf")):
        got = asyncio.run(
            eng.decide(signals=sigs, price=0.5, cross_market_signal=bad, **_CALL)
        )
        assert got == base, f"cross_market_signal={bad} was not treated as 0"


def test_legacy_mode_ignores_cross_market_signal() -> None:
    """price=None (legacy mode) must be byte-identical regardless of
    cross_market_signal — the additive term must NOT appear in the legacy path."""
    eng = _engine(kappa=0.25, kappa_xm=0.5)
    sigs = dict(_uniform_signals(0.5))
    base = asyncio.run(eng.decide(signals=sigs, **_CALL))  # no price, no signal
    with_signal = asyncio.run(
        eng.decide(signals=sigs, cross_market_signal=0.9, **_CALL)
    )
    assert base == with_signal
