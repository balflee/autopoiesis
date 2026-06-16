# ruff: noqa: RUF002, RUF003
"""WeightUpdater Desperate-mode branch tests (sprint_5 T-B-009).

Brief acceptance criteria covered here:

* In Phase 3 Desperate, β/ρ unfreeze + η×2 (PRD §6.9). Normal Phase 3
  stays frozen.
* The :meth:`update_with_delta` return surface carries a
  :class:`WeightDelta` with the right :class:`DegradedMode` tag +
  effective learning rate + L1 step sizes.
* Phase 4 Desperate is still frozen (Terminal Lucidity > Desperate).
* DegradedMode enum sanity: only {none, desperate} are valid.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.state import Phase, Weights
from agent.engines.weight_updater import (
    DESPERATE_LR_MULTIPLIER,
    DegradedMode,
    WeightDelta,
    WeightUpdater,
)


def _phase3_weights() -> Weights:
    """Committed Phase-3 starting point — non-degenerate β so the
    desperate-branch β-unfreeze has somewhere to move."""
    return Weights(
        w_r=0.55,
        w_s=0.45,
        alpha=[0.4, 0.35, 0.25],
        beta=[0.5, 0.5],
        rho=0.55,
    )


def _strong_gradient() -> dict[str, float]:
    return {
        "tennis_technical_quality": 1.0,
        "market_momentum_quality": -1.0,
        "surface_advantage_quality": -1.0,
        "head_to_head_quality": 1.0,
        "rest_recency_quality": -1.0,
        "rational_stream_quality": 1.0,
        "sentient_stream_quality": -1.0,
        "rho_quality": 0.8,
    }


# ── Phase 3 normal vs Desperate ──────────────────────────────────────


def test_phase3_normal_returns_input_unchanged() -> None:
    """Sprint_4 invariant — Phase 3 normal posture is frozen."""
    updater = WeightUpdater()
    initial = _phase3_weights()
    new, delta = asyncio.run(
        updater.update_with_delta(
            current=initial,
            phase=Phase.PHASE_3_MASTER,
            features=_strong_gradient(),
            desperate=False,
        )
    )
    assert new.alpha == initial.alpha
    assert new.beta == initial.beta
    assert new.w_r == initial.w_r
    assert new.rho == initial.rho
    assert isinstance(delta, WeightDelta)
    assert delta.mode == DegradedMode.NONE
    assert delta.alpha_l1 == 0.0
    assert delta.beta_l1 == 0.0
    assert delta.w_l1 == 0.0
    assert delta.rho_delta == 0.0


def test_phase3_desperate_unlocks_beta_rho_and_alpha() -> None:
    """PRD §6.9: in Desperate Mode β/ρ unlock + α keeps training."""
    updater = WeightUpdater(learning_rate=0.05)
    initial = _phase3_weights()
    new, delta = asyncio.run(
        updater.update_with_delta(
            current=initial,
            phase=Phase.PHASE_3_MASTER,
            features=_strong_gradient(),
            desperate=True,
        )
    )
    # All four channels moved.
    assert new.alpha != initial.alpha
    assert new.beta != initial.beta
    assert new.w_r != initial.w_r
    assert new.rho != initial.rho
    # Delta tagged correctly.
    assert delta.mode == DegradedMode.DESPERATE
    assert delta.phase == Phase.PHASE_3_MASTER
    assert delta.effective_learning_rate == pytest.approx(
        0.05 * DESPERATE_LR_MULTIPLIER
    )
    # L1 deltas are non-zero (the channels actually moved).
    assert delta.alpha_l1 > 0.0
    assert delta.beta_l1 > 0.0
    assert delta.w_l1 > 0.0
    assert delta.rho_delta > 0.0


def test_phase3_desperate_doubles_learning_rate_vs_phase2_normal() -> None:
    """η×2 in Desperate vs phase-2 normal — same gradient + same start."""
    initial = _phase3_weights()
    gradient = _strong_gradient()

    updater_normal = WeightUpdater(learning_rate=0.05)
    w_normal = asyncio.run(
        updater_normal.update(
            current=initial,
            phase=Phase.PHASE_2_APPRENTICE,
            features=gradient,
            desperate=False,
        )
    )

    updater_desperate = WeightUpdater(learning_rate=0.05)
    w_desperate = asyncio.run(
        updater_desperate.update(
            current=initial,
            phase=Phase.PHASE_3_MASTER,
            features=gradient,
            desperate=True,
        )
    )

    # Desperate should move α[0] further than normal Phase 2 (η×2).
    delta_normal = w_normal.alpha[0] - initial.alpha[0]
    delta_desperate = w_desperate.alpha[0] - initial.alpha[0]
    assert delta_desperate > delta_normal * 1.5


def test_phase4_terminal_stays_frozen_even_in_desperate() -> None:
    """Terminal Lucidity > Desperate — no learning after Phase 4."""
    updater = WeightUpdater()
    initial = _phase3_weights()
    new, delta = asyncio.run(
        updater.update_with_delta(
            current=initial,
            phase=Phase.PHASE_4_TERMINAL,
            features=_strong_gradient(),
            desperate=True,
        )
    )
    assert new.alpha == initial.alpha
    assert new.beta == initial.beta
    assert new.rho == initial.rho
    # Even on a no-op, the delta carries the desperate tag — the
    # reflection layer + dashboard want to know the agent ASKED for
    # desperate even when the freeze policy denied it.
    assert delta.mode == DegradedMode.DESPERATE


def test_update_and_update_with_delta_agree_on_new_weights() -> None:
    """Back-compat: legacy callers using :meth:`update` get the same
    Weights as :meth:`update_with_delta` would on identical input."""
    updater_a = WeightUpdater(learning_rate=0.05)
    updater_b = WeightUpdater(learning_rate=0.05)
    initial = _phase3_weights()
    features = _strong_gradient()
    new_a = asyncio.run(
        updater_a.update(
            current=initial,
            phase=Phase.PHASE_3_MASTER,
            features=features,
            desperate=True,
        )
    )
    new_b, _delta = asyncio.run(
        updater_b.update_with_delta(
            current=initial,
            phase=Phase.PHASE_3_MASTER,
            features=features,
            desperate=True,
        )
    )
    assert new_a.alpha == new_b.alpha
    assert new_a.beta == new_b.beta
    assert new_a.w_r == new_b.w_r
    assert new_a.rho == new_b.rho


def test_degraded_mode_enum_membership() -> None:
    """Pinning the on-the-wire vocabulary — decision_record v0.2.0
    pins these exact strings."""
    assert {m.value for m in DegradedMode} == {"none", "desperate"}
    assert DegradedMode.NONE.value == "none"
    assert DegradedMode.DESPERATE.value == "desperate"
