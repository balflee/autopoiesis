# ruff: noqa: RUF003
"""DecisionEngine tests — 2-layer fusion + Kelly + 4-constraint min.

Brief acceptance criteria covered here:

* `bet_size_usd = min(ρ·kelly·confidence·bankroll, breath·MAX_BREATH_RISK_PCT/
  CONVERSION_RATE, bankroll, liquidity_cap)` — all four terms exercised.
* Desperate mode: `bet_size_cap = 0.50` instead of 0.30 per TP §4.7.
* NO_BET paths: missing signal, low confidence, zero Kelly, neutral fused,
  sub-min-bet size.
* Side inference from sign(fused).
* ρ ∈ [0, 1] clamp via ρ_effective.
"""

from __future__ import annotations

import asyncio
from typing import Mapping

import pytest

from agent.core.state import ActionKind, Side
from agent.engines.base import EngineSignal
from agent.engines.decision import (
    CROWD_VOLUME,
    DEFAULT_CONVERSION_RATE,
    DEFAULT_MAX_BREATH_RISK_PCT,
    DEFAULT_MIN_BET_SIZE_USD,
    DESPERATE_BET_SIZE_CAP,
    DecisionEngine,
    MARKET_MOMENTUM,
    NO_BET_BELOW_MIN_SIZE,
    NO_BET_LOW_CONFIDENCE,
    NO_BET_MISSING_SIGNAL,
    NO_BET_NEUTRAL_FUSED,
    NORMAL_BET_SIZE_CAP,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
    _fuse_signals,
    _kelly_fraction,
)


# ── helpers ───────────────────────────────────────────────────────────


def _sig(score: float, confidence: float = 0.8) -> EngineSignal:
    """Build a minimal EngineSignal — only score / confidence matter
    for the decision math."""
    return EngineSignal(
        score=score,
        confidence=confidence,
        available_at="2026-05-22T20:00:00+00:00",
        rationale="test",
        raw_features={},
    )


def _full_signals(
    *,
    tennis: float = 0.5,
    mm: float = 0.3,
    sm: float = 0.4,
    llm: float = 0.0,
    cv: float = 0.0,
    confidence: float = 0.8,
) -> Mapping[str, EngineSignal]:
    return {
        TENNIS_TECHNICAL: _sig(tennis, confidence),
        MARKET_MOMENTUM: _sig(mm, confidence),
        SMART_MONEY: _sig(sm, confidence),
        SENTIMENT_LLM: _sig(llm, confidence),
        CROWD_VOLUME: _sig(cv, confidence),
    }


def _phase1_weights() -> dict[str, object]:
    """Phase 1 weights: β₁ = 0, β₂ = 1, plus balanced α."""
    return {
        "weights_alpha": (1 / 3, 1 / 3, 1 / 3),
        "weights_beta": (0.0, 1.0),
        "w_r": 0.6,
        "w_s": 0.4,
        "rho": 0.5,
    }


# ── core math helpers ────────────────────────────────────────────────


def test_kelly_fraction_at_boundaries() -> None:
    """Kelly: 0 → 0, 1 → 1, monotonic between."""
    assert _kelly_fraction(0.0) == 0.0
    assert _kelly_fraction(1.0) == 1.0
    assert _kelly_fraction(-0.1) == 0.0
    # Standard fractional: e=0.1 ⇒ 0.111…
    assert _kelly_fraction(0.1) == pytest.approx(0.1 / 0.9, rel=1e-9)
    # Saturation: e=0.6 → 0.6/0.4 = 1.5 → clamp to 1.0
    assert _kelly_fraction(0.6) == 1.0


def test_fuse_signals_confidence_weighted() -> None:
    """raw_R = Σᵢ αᵢ·scoreᵢ·confᵢ; raw_S analogous; fused = W_R·R + W_S·S."""
    sigs = _full_signals(tennis=0.6, mm=0.3, sm=0.0, llm=0.4, cv=-0.2, confidence=0.5)
    res = _fuse_signals(
        signals=dict(sigs),
        alpha=(0.5, 0.3, 0.2),
        beta=(0.5, 0.5),
        w_r=0.6,
        w_s=0.4,
    )
    # Recompute manually so an arithmetic typo in the engine surfaces here.
    raw_r = 0.5 * 0.6 * 0.5 + 0.3 * 0.3 * 0.5 + 0.2 * 0.0 * 0.5
    raw_s = 0.5 * 0.4 * 0.5 + 0.5 * -0.2 * 0.5
    fused = 0.6 * raw_r + 0.4 * raw_s
    assert res.raw_rational == pytest.approx(raw_r, rel=1e-9)
    assert res.raw_sentient == pytest.approx(raw_s, rel=1e-9)
    assert res.fused == pytest.approx(fused, rel=1e-9)
    assert res.mean_confidence == pytest.approx(0.5, rel=1e-9)


# ── NO_BET paths ──────────────────────────────────────────────────────


def test_no_bet_when_signal_missing() -> None:
    engine = DecisionEngine()
    sigs = dict(_full_signals())
    del sigs[TENNIS_TECHNICAL]
    action = asyncio.run(
        engine.decide(
            signals=sigs,
            market_id="m1",
            bankroll_usd=1000.0,
            breath=100.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert NO_BET_MISSING_SIGNAL in action.no_bet_reason
    assert TENNIS_TECHNICAL in action.no_bet_reason


def test_no_bet_when_mean_confidence_below_threshold() -> None:
    """Mean confidence < min_confidence ⇒ NO_BET."""
    engine = DecisionEngine(min_confidence=0.5)
    sigs = _full_signals(tennis=0.8, mm=0.8, sm=0.8, confidence=0.1)
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=100.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.NO_BET
    assert action.no_bet_reason == NO_BET_LOW_CONFIDENCE


def test_no_bet_when_fused_is_zero() -> None:
    """A perfectly balanced signal collapses to fused=0 ⇒ NO_BET."""
    engine = DecisionEngine()
    # all zeros — fused == 0 exactly
    sigs = _full_signals(tennis=0.0, mm=0.0, sm=0.0, llm=0.0, cv=0.0, confidence=0.8)
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=100.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.NO_BET
    assert action.no_bet_reason == NO_BET_NEUTRAL_FUSED


def test_no_bet_when_size_below_min() -> None:
    """A tiny edge yields a sub-MIN_BET size ⇒ NO_BET."""
    engine = DecisionEngine(min_bet_size_usd=10.0)
    # Tiny score so kelly ≈ 0 and desired tiny
    sigs = _full_signals(tennis=0.001, mm=0.001, sm=0.001, confidence=0.5)
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=1000.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert NO_BET_BELOW_MIN_SIZE in action.no_bet_reason


# ── 4-constraint min: each term exercised in isolation ───────────────


def test_bet_size_constrained_by_desired() -> None:
    """Desired (ρ·k·conf·bankroll) is the binding constraint.

    Set bankroll low + everything else huge so desired wins the min().
    """
    engine = DecisionEngine(
        max_breath_risk_pct=1.0,
        conversion_rate=1.0,
        min_bet_size_usd=0.0,
    )
    sigs = _full_signals(tennis=0.5, mm=0.5, sm=0.5, confidence=1.0)
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=100.0,
            breath=1_000_000.0,
            liquidity_cap_usd=1_000_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.BET
    assert action.size_usd is not None
    # desired = ρ·k·conf·bankroll = 0.5·k·1.0·100
    # With Phase 1 freeze (β₂=1, β₁=0) and crowd_volume score = 0,
    # the Sentient stream contributes 0; fused = 0.6·R.
    # R = (1/3)·0.5·1.0 + (1/3)·0.5·1.0 + (1/3)·0.5·1.0 = 0.5
    # fused = 0.6·0.5 = 0.3, kelly = 0.3/0.7 ≈ 0.4286
    # desired = 0.5·0.4286·1.0·100 ≈ 21.43
    # bankroll_cap = 100·0.30 = 30 (NOT binding here)
    # We expect desired to bind.
    expected_desired = 0.5 * (0.3 / 0.7) * 1.0 * 100.0
    assert action.size_usd == pytest.approx(expected_desired, rel=1e-6)


def test_bet_size_constrained_by_breath_cap() -> None:
    """Breath cap term `breath·MAX_RISK_PCT/CONVERSION` binds.

    Set BREATH very low so the breath_cap shrinks below all other terms.
    """
    engine = DecisionEngine(
        max_breath_risk_pct=0.30,
        conversion_rate=1.0,
        min_bet_size_usd=0.0,
    )
    sigs = _full_signals(tennis=0.5, mm=0.5, sm=0.5, confidence=1.0)
    # breath_cap = 10 * 0.30 / 1.0 = 3.0
    # desired (from prior test math) ≈ 21.43, bankroll_cap=30 — both bigger
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=100.0,
            breath=10.0,
            liquidity_cap_usd=1_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.BET
    assert action.size_usd == pytest.approx(3.0, rel=1e-6)


def test_bet_size_constrained_by_bankroll_cap() -> None:
    """Bankroll hard cap `bankroll·bet_size_cap_fraction` binds (normal=30%).

    Normal-mode bet_size_cap=0.30. A 100 USD bankroll caps the bet at $30
    regardless of how strong the signal / how much breath there is.
    """
    engine = DecisionEngine(
        max_breath_risk_pct=1.0,
        conversion_rate=1.0,
        min_bet_size_usd=0.0,
    )
    # Maximally strong signal so desired is large — k → 1
    sigs = _full_signals(tennis=1.0, mm=1.0, sm=1.0, confidence=1.0)
    # With perfect score, raw_r = 1.0, fused = 0.6 (with phase1 β=[0,1] +
    # crowd_volume=0). kelly = 0.6/0.4 = 1.5 → clamp to 1.0.
    # desired = 0.5·1.0·1.0·100 = 50.
    # bankroll_cap = 100·0.30 = 30 ← binding.
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=100.0,
            breath=1_000_000.0,
            liquidity_cap_usd=1_000_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.BET
    assert action.size_usd == pytest.approx(30.0, rel=1e-6)


def test_bet_size_constrained_by_liquidity_cap() -> None:
    """Liquidity cap binds when other constraints are loose."""
    engine = DecisionEngine(
        max_breath_risk_pct=1.0,
        conversion_rate=1.0,
        min_bet_size_usd=0.0,
    )
    sigs = _full_signals(tennis=1.0, mm=1.0, sm=1.0, confidence=1.0)
    # All other caps huge; liquidity = $5
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1_000_000.0,
            breath=1_000_000.0,
            liquidity_cap_usd=5.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.BET
    assert action.size_usd == pytest.approx(5.0, rel=1e-6)


# ── Desperate mode flip ───────────────────────────────────────────────


def test_desperate_mode_loosens_bankroll_cap() -> None:
    """TP §4.7: bet_size_cap 0.30 → 0.50 in desperate mode.

    Same scenario as test_bet_size_constrained_by_bankroll_cap but
    desperate=True ⇒ cap term is 50 instead of 30.
    """
    engine = DecisionEngine(
        max_breath_risk_pct=1.0,
        conversion_rate=1.0,
        min_bet_size_usd=0.0,
    )
    sigs = _full_signals(tennis=1.0, mm=1.0, sm=1.0, confidence=1.0)
    normal = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=100.0,
            breath=1_000_000.0,
            liquidity_cap_usd=1_000_000.0,
            desperate=False,
            **_phase1_weights(),
        )
    )
    desperate = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=100.0,
            breath=1_000_000.0,
            liquidity_cap_usd=1_000_000.0,
            desperate=True,
            **_phase1_weights(),
        )
    )
    assert normal.size_usd == pytest.approx(100.0 * NORMAL_BET_SIZE_CAP)
    assert desperate.size_usd == pytest.approx(100.0 * DESPERATE_BET_SIZE_CAP)
    assert desperate.size_usd is not None and normal.size_usd is not None
    assert desperate.size_usd > normal.size_usd


# ── Side inference + Phase 1 wiring ──────────────────────────────────


def test_side_inferred_from_positive_fused() -> None:
    engine = DecisionEngine(min_bet_size_usd=0.0)
    sigs = _full_signals(tennis=0.5, mm=0.5, sm=0.5, confidence=1.0)
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=1000.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.BET
    assert action.side == Side.YES


def test_side_inferred_from_negative_fused() -> None:
    engine = DecisionEngine(min_bet_size_usd=0.0)
    sigs = _full_signals(tennis=-0.5, mm=-0.5, sm=-0.5, confidence=1.0)
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=1000.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert action.kind == ActionKind.BET
    assert action.side == Side.NO


def test_phase1_beta_zeros_out_llm_channel() -> None:
    """Phase 1 weights pin β₁=0; LLM score should NOT affect the output.

    Two runs: identical α / W / ρ, identical scores EXCEPT the LLM
    score (which is multiplied by β₁=0). Outputs must be identical.
    """
    engine = DecisionEngine(min_bet_size_usd=0.0)
    sigs_a = _full_signals(tennis=0.3, mm=0.3, sm=0.3, llm=0.0, cv=0.1, confidence=0.8)
    sigs_b = _full_signals(tennis=0.3, mm=0.3, sm=0.3, llm=1.0, cv=0.1, confidence=0.8)
    a = asyncio.run(
        engine.decide(
            signals=dict(sigs_a),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=1000.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    b = asyncio.run(
        engine.decide(
            signals=dict(sigs_b),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=1000.0,
            liquidity_cap_usd=10_000.0,
            **_phase1_weights(),
        )
    )
    assert a.size_usd == b.size_usd
    assert a.edge_pct == b.edge_pct


def test_negative_rho_clamped_to_zero_via_rho_effective() -> None:
    """ρ_effective = clamp(ρ, 0, 1). A negative ρ ⇒ desired=0 ⇒ NO_BET via
    the min-bet-size floor (engine guards ``size <= 0`` so a zero-size
    BET cannot reach the Action validator)."""
    engine = DecisionEngine(min_bet_size_usd=0.0)
    sigs = _full_signals(tennis=0.5, mm=0.5, sm=0.5, confidence=1.0)
    weights = _phase1_weights() | {"rho": -0.5}
    action = asyncio.run(
        engine.decide(
            signals=dict(sigs),
            market_id="m1",
            bankroll_usd=1000.0,
            breath=1000.0,
            liquidity_cap_usd=10_000.0,
            **weights,
        )
    )
    assert action.kind == ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert NO_BET_BELOW_MIN_SIZE in action.no_bet_reason


# ── Constants + Action validator integration ─────────────────────────


def test_engine_constants_match_brief_spec() -> None:
    """Brief acceptance criteria: defaults match PRD/TP placeholders."""
    assert NORMAL_BET_SIZE_CAP == 0.30
    assert DESPERATE_BET_SIZE_CAP == 0.50
    assert DEFAULT_MAX_BREATH_RISK_PCT == 0.30
    assert DEFAULT_CONVERSION_RATE == 1.0
    assert DEFAULT_MIN_BET_SIZE_USD == 5.0


def test_constructor_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="max_breath_risk_pct"):
        DecisionEngine(max_breath_risk_pct=0.0)
    with pytest.raises(ValueError, match="max_breath_risk_pct"):
        DecisionEngine(max_breath_risk_pct=1.5)
    with pytest.raises(ValueError, match="conversion_rate"):
        DecisionEngine(conversion_rate=0.0)
    with pytest.raises(ValueError, match="min_bet_size_usd"):
        DecisionEngine(min_bet_size_usd=-1.0)
    with pytest.raises(ValueError, match="min_confidence"):
        DecisionEngine(min_confidence=1.5)
