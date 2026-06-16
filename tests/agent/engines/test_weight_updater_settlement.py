"""WeightUpdater.update_from_settlement — settlement-time credit assignment.

Task L3 (Plan 2 self-evolution). The settlement-PnL gradient entrypoint
nudges the RIGHT engine weights from a realized win/loss using the
direction-aware credit-assignment formula:

    engine_quality = sign(pnl) * bet_direction * signal_score[engine]
    rho_quality    = tanh(pnl_usd / max(size_usd, 1e-6))   (SIGNED + bounded)

bet_direction is +1 for a YES bet, -1 for a NO bet. The signedness of
rho_quality matters: the gradient ADDS it to the rho logit, so a win must
not cut risk and a loss must not raise it.
"""

from __future__ import annotations

import asyncio
import math

from agent.core.state import Phase, Weights
from agent.engines.weight_updater import WeightUpdater


def _balanced_phase2_weights() -> Weights:
    """Phase-2 starting point: balanced α, β unfrozen, middle ρ."""
    return Weights(
        w_r=0.6,
        w_s=0.4,
        alpha=[1 / 3, 1 / 3, 1 / 3],
        beta=[0.5, 0.5],
        rho=0.5,
    )


def test_yes_win_shifts_alpha_toward_agreeing_engine() -> None:
    """YES bet, positive pnl, tennis_technical drove it (+score) → α[0] rises."""
    updater = WeightUpdater()
    start = _balanced_phase2_weights()
    new = asyncio.run(
        updater.update_from_settlement(
            current=start,
            phase=Phase.PHASE_2_APPRENTICE,
            pnl_usd=5.0,
            size_usd=10.0,
            signal_scores={
                "tennis_technical": 1.0,
                "market_momentum": 0.0,
                "surface_advantage": 0.0,
            },
            bet_direction=1.0,
        )
    )
    assert new.alpha[0] > start.alpha[0]
    # simplex still normalised
    assert math.isclose(sum(new.alpha), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(new.beta), 1.0, abs_tol=1e-9)
    assert math.isclose(new.w_r + new.w_s, 1.0, abs_tol=1e-9)


def test_winning_no_bet_still_rewards_the_driving_engine() -> None:
    """Winning NO bet: driving engine had a NEGATIVE score, bet_direction=-1.

    sign(pnl)*bet_direction*score = (+)(-)(-) = + → STILL reward tennis_technical.
    The naive sign(pnl)*score would have PUNISHED the correct NO predictor.
    """
    updater = WeightUpdater()
    start = _balanced_phase2_weights()
    new = asyncio.run(
        updater.update_from_settlement(
            current=start,
            phase=Phase.PHASE_2_APPRENTICE,
            pnl_usd=5.0,
            size_usd=10.0,
            signal_scores={
                "tennis_technical": -1.0,
                "market_momentum": 0.0,
                "surface_advantage": 0.0,
            },
            bet_direction=-1.0,
        )
    )
    assert new.alpha[0] > start.alpha[0]
    assert math.isclose(sum(new.alpha), 1.0, abs_tol=1e-9)


def test_losing_no_bet_penalizes_the_driving_engine() -> None:
    """Losing NO bet: driving engine had a negative score, bet_direction=-1,
    pnl<0 → sign(pnl)*bet_direction*score = (-)(-)(-) = - → α[0] DROPS."""
    updater = WeightUpdater()
    start = _balanced_phase2_weights()
    new = asyncio.run(
        updater.update_from_settlement(
            current=start,
            phase=Phase.PHASE_2_APPRENTICE,
            pnl_usd=-10.0,
            size_usd=10.0,
            signal_scores={
                "tennis_technical": -1.0,
                "market_momentum": 0.0,
                "surface_advantage": 0.0,
            },
            bet_direction=-1.0,
        )
    )
    assert new.alpha[0] < start.alpha[0]
    assert math.isclose(sum(new.alpha), 1.0, abs_tol=1e-9)


def test_loss_reduces_rho_and_win_does_not() -> None:
    """rho_quality = tanh(pnl/size) is SIGNED: a loss must CUT risk, a win
    must NOT cut it (raise/hold)."""
    # Loss path.
    updater_loss = WeightUpdater()
    start = _balanced_phase2_weights()
    after_loss = asyncio.run(
        updater_loss.update_from_settlement(
            current=start,
            phase=Phase.PHASE_2_APPRENTICE,
            pnl_usd=-10.0,
            size_usd=10.0,
            signal_scores={"tennis_technical": 1.0},
            bet_direction=1.0,
        )
    )
    assert after_loss.rho < start.rho

    # Win path.
    updater_win = WeightUpdater()
    after_win = asyncio.run(
        updater_win.update_from_settlement(
            current=start,
            phase=Phase.PHASE_2_APPRENTICE,
            pnl_usd=10.0,
            size_usd=10.0,
            signal_scores={"tennis_technical": 1.0},
            bet_direction=1.0,
        )
    )
    assert after_win.rho >= start.rho


def test_stream_weights_train_from_rational_vs_sentient_groups() -> None:
    """A win driven by the rational group raises w_r (Phase 2 unlocks w)."""
    updater = WeightUpdater()
    start = _balanced_phase2_weights()
    new = asyncio.run(
        updater.update_from_settlement(
            current=start,
            phase=Phase.PHASE_2_APPRENTICE,
            pnl_usd=5.0,
            size_usd=10.0,
            signal_scores={
                "tennis_technical": 1.0,
                "market_momentum": 1.0,
                "surface_advantage": 1.0,
                "head_to_head": 0.0,
                "rest_recency": 0.0,
            },
            bet_direction=1.0,
        )
    )
    assert new.w_r > start.w_r
    assert math.isclose(new.w_r + new.w_s, 1.0, abs_tol=1e-9)
