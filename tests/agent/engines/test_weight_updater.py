# ruff: noqa: RUF003
"""WeightUpdater tests — softmax + EMA + Phase 1 freeze.

Brief acceptance criteria covered here:

* Constraint invariants hold after every update (α/β/W sum to 1.0 ± 1e-9, ρ
  in [0, 1]).
* Phase 1 mode: byte-identical β₁ across 1000 updates.
* Phase 1 mode: W_R / W_S byte-identical across 1000 updates.
* Phase 1: α + ρ DO train (positive control — freeze tests are vacuous
  if nothing else moves either).
* Phase 3 / 4: full freeze.
* Desperate mode: η × 2.
* Look-ahead refusal: settled_at / resolved_at / outcome / payout keys
  rejected upfront.
"""

from __future__ import annotations

import asyncio
import math

import numpy as np
import pytest

from agent.core.state import Phase, Weights
from agent.engines.weight_updater import (
    _LOOKAHEAD_FORBIDDEN_PREFIXES,  # type: ignore[attr-defined]
    DEFAULT_LEARNING_RATE,
    DESPERATE_LR_MULTIPLIER,
    WeightUpdater,
    _logit_from_simplex,  # type: ignore[attr-defined]
    _softmax,  # type: ignore[attr-defined]
)


def _phase1_weights() -> Weights:
    """Phase 1 starting point: β₁=0 + balanced α + middle ρ."""
    return Weights(
        w_r=0.6,
        w_s=0.4,
        alpha=[1 / 3, 1 / 3, 1 / 3],
        beta=[0.0, 1.0],
        rho=0.5,
    )


def _strong_alpha_gradient() -> dict[str, float]:
    """Per-engine gradient that pushes weight toward tennis_technical."""
    return {
        "tennis_technical_quality": 1.0,
        "market_momentum_quality": -1.0,
        "surface_advantage_quality": -1.0,
        "rho_quality": 0.5,
    }


# ── Invariants + softmax round-trip ──────────────────────────────────


def test_softmax_round_trip_preserves_simplex() -> None:
    """softmax(_logit_from_simplex(p)) == p (within fp tolerance)."""
    p = np.array([0.34, 0.33, 0.33])
    back = _softmax(_logit_from_simplex(p))
    assert float(back.sum()) == pytest.approx(1.0, abs=1e-12)
    assert float(np.abs(back - p).max()) < 1e-9


def test_invariants_hold_after_single_update() -> None:
    """α₁+α₂+α₃=1, β₁+β₂=1, w_r+w_s=1, ρ∈[0,1] after one update."""
    updater = WeightUpdater()
    w = asyncio.run(
        updater.update(
            current=_phase1_weights(),
            phase=Phase.PHASE_1_INFANCY,
            features=_strong_alpha_gradient(),
        )
    )
    assert math.isclose(sum(w.alpha), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(w.beta), 1.0, abs_tol=1e-9)
    assert math.isclose(w.w_r + w.w_s, 1.0, abs_tol=1e-9)
    assert 0.0 <= w.rho <= 1.0


def test_invariants_hold_across_1000_updates() -> None:
    """Repeated updates MUST NOT drift the simplex sums beyond 1e-9.

    Direct test of the brief acceptance criterion: 'Constraint invariants
    hold after every weight_updater step'.
    """
    updater = WeightUpdater()
    w = _phase1_weights()
    for _ in range(1000):
        w = asyncio.run(
            updater.update(
                current=w,
                phase=Phase.PHASE_1_INFANCY,
                features=_strong_alpha_gradient(),
            )
        )
        assert math.isclose(sum(w.alpha), 1.0, abs_tol=1e-9)
        assert math.isclose(sum(w.beta), 1.0, abs_tol=1e-9)
        assert math.isclose(w.w_r + w.w_s, 1.0, abs_tol=1e-9)
        assert 0.0 <= w.rho <= 1.0
        assert all(a >= 0 for a in w.alpha)
        assert all(b >= 0 for b in w.beta)


# ── Phase 1 freeze ───────────────────────────────────────────────────


def test_phase1_freezes_beta1_byte_identical_across_1000_updates() -> None:
    """Brief HARD RULE: β₁ MUST be byte-identical across 1000 updates."""
    updater = WeightUpdater()
    initial = _phase1_weights()
    w = initial
    for _ in range(1000):
        w = asyncio.run(
            updater.update(
                current=w,
                phase=Phase.PHASE_1_INFANCY,
                features=_strong_alpha_gradient(),
            )
        )
    # Bit-exact equality (no tolerance) — the freeze MUST be unconditional.
    assert w.beta[0] == initial.beta[0] == 0.0
    assert w.beta[1] == initial.beta[1] == 1.0


def test_phase1_freezes_w_r_w_s_byte_identical_across_1000_updates() -> None:
    """Brief: 'Phase 1 mode: weight_updater never modifies W_R/W_S'.

    Sentient layer is degenerate when β₁=0 (only rest_recency contributes),
    so training W_R/W_S would optimise against a one-dim signal — see
    delivery_report.md.
    """
    updater = WeightUpdater()
    initial = _phase1_weights()
    w = initial
    # Feed a strong stream-level gradient that SHOULD move W_R/W_S in
    # Phase 2 — Phase 1 must ignore it.
    features = _strong_alpha_gradient() | {
        "rational_stream_quality": 1.0,
        "sentient_stream_quality": -1.0,
    }
    for _ in range(1000):
        w = asyncio.run(
            updater.update(
                current=w,
                phase=Phase.PHASE_1_INFANCY,
                features=features,
            )
        )
    assert w.w_r == initial.w_r == 0.6
    assert w.w_s == initial.w_s == 0.4


def test_phase1_alpha_and_rho_actually_train() -> None:
    """Positive control: α + ρ MUST move under a non-zero gradient.

    Without this assertion, the freeze tests above are vacuous (an
    accidental "freeze everything" would still pass them).
    """
    updater = WeightUpdater()
    initial = _phase1_weights()
    w = initial
    for _ in range(50):
        w = asyncio.run(
            updater.update(
                current=w,
                phase=Phase.PHASE_1_INFANCY,
                features=_strong_alpha_gradient(),
            )
        )
    # α[0] should have grown (tennis_technical_quality=1.0 push)
    assert w.alpha[0] > initial.alpha[0] + 1e-3
    # α[1] + α[2] should have shrunk
    assert w.alpha[1] < initial.alpha[1] - 1e-3
    assert w.alpha[2] < initial.alpha[2] - 1e-3
    # ρ should have grown (rho_quality=0.5 > 0)
    assert w.rho > initial.rho


# ── Phase 2 — all 6 train ────────────────────────────────────────────


def test_phase2_trains_all_six_parameters() -> None:
    """Phase 2: β + W_R/W_S no longer frozen."""
    updater = WeightUpdater()
    initial = Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[1 / 3, 1 / 3, 1 / 3],
        beta=[0.5, 0.5],
        rho=0.5,
    )
    w = initial
    features = {
        "tennis_technical_quality": 1.0,
        "head_to_head_quality": 1.0,
        "rational_stream_quality": 1.0,
        "sentient_stream_quality": -1.0,
        "rho_quality": 0.5,
    }
    for _ in range(50):
        w = asyncio.run(
            updater.update(
                current=w,
                phase=Phase.PHASE_2_APPRENTICE,
                features=features,
            )
        )
    # Both α[0] and β[0] should have moved up
    assert w.alpha[0] > initial.alpha[0]
    assert w.beta[0] > initial.beta[0]
    # W_R should have moved up vs W_S
    assert w.w_r > initial.w_r


# ── Phase 3 / 4 full freeze ──────────────────────────────────────────


def test_phase3_master_returns_input_unchanged() -> None:
    """Phase 3 (Master) freezes ALL 6 parameters per PRD §4.5."""
    updater = WeightUpdater()
    initial = _phase1_weights()
    w = asyncio.run(
        updater.update(
            current=initial,
            phase=Phase.PHASE_3_MASTER,
            features=_strong_alpha_gradient(),
        )
    )
    assert w.alpha == initial.alpha
    assert w.beta == initial.beta
    assert w.w_r == initial.w_r
    assert w.w_s == initial.w_s
    assert w.rho == initial.rho


def test_phase4_terminal_returns_input_unchanged() -> None:
    """Phase 4 (Terminal) same freeze as Phase 3."""
    updater = WeightUpdater()
    initial = _phase1_weights()
    w = asyncio.run(
        updater.update(
            current=initial,
            phase=Phase.PHASE_4_TERMINAL,
            features=_strong_alpha_gradient(),
        )
    )
    assert w.alpha == initial.alpha
    assert w.beta == initial.beta
    assert w.rho == initial.rho


# ── Desperate mode doubles η ─────────────────────────────────────────


def test_desperate_mode_doubles_learning_rate() -> None:
    """TP §4.7: η × 2 in desperate mode.

    Compare alpha[0] movement after 1 update with/without desperate.
    The desperate step should move alpha[0] roughly 2× as far.
    """
    base_features = _strong_alpha_gradient()
    initial = _phase1_weights()

    updater_normal = WeightUpdater(learning_rate=0.05)
    w_normal = asyncio.run(
        updater_normal.update(
            current=initial,
            phase=Phase.PHASE_1_INFANCY,
            features=base_features,
            desperate=False,
        )
    )
    delta_normal = w_normal.alpha[0] - initial.alpha[0]

    updater_desperate = WeightUpdater(learning_rate=0.05)
    w_desperate = asyncio.run(
        updater_desperate.update(
            current=initial,
            phase=Phase.PHASE_1_INFANCY,
            features=base_features,
            desperate=True,
        )
    )
    delta_desperate = w_desperate.alpha[0] - initial.alpha[0]

    # Doubled rate ⇒ ~2× movement (softmax is nonlinear so allow slack).
    assert delta_desperate > delta_normal * 1.5
    assert DESPERATE_LR_MULTIPLIER == 2.0


# ── Look-ahead refusal ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_key",
    [
        "settled_at",
        "settled_at_block",
        "resolved_at",
        "resolved_at_timestamp",
        "outcome",
        "outcome_yes",
        "payout",
        "payout_usd",
    ],
)
def test_lookahead_keys_rejected(bad_key: str) -> None:
    """PRD §6.8: weight_updater MUST refuse post-settlement features.

    The brief explicitly warns: 'the look-ahead auditor is paying
    special attention to this module' and flags settled_at /
    resolved_at / outcome / payout columns.
    """
    updater = WeightUpdater()
    features = {
        "tennis_technical_quality": 1.0,
        bad_key: 1.0,
    }
    with pytest.raises(ValueError, match=r"PRD §6.8"):
        asyncio.run(
            updater.update(
                current=_phase1_weights(),
                phase=Phase.PHASE_1_INFANCY,
                features=features,
            )
        )


def test_all_four_forbidden_prefixes_are_checked() -> None:
    """Belt-and-braces: every prefix in _LOOKAHEAD_FORBIDDEN_PREFIXES
    is exercised by the parameterised test above."""
    expected = {"settled_at", "resolved_at", "outcome", "payout"}
    assert set(_LOOKAHEAD_FORBIDDEN_PREFIXES) == expected


# ── EMA smoothing ────────────────────────────────────────────────────


def test_ema_smoothing_carries_signal_across_zero_feature_ticks() -> None:
    """EMA's role: after a non-zero gradient tick, the next tick with
    feature=0 should STILL move the weights — because the EMA buffer
    still carries (1-τ)·prev_value as the gradient.

    Compare with τ=1.0 (no smoothing): the zero-feature tick produces
    zero gradient and weights freeze.
    """
    initial = _phase1_weights()

    # EMA on (τ=0.1): buffer persists.
    updater_smooth = WeightUpdater(learning_rate=0.05, ema_tau=0.1)
    w1_smooth = asyncio.run(
        updater_smooth.update(
            current=initial,
            phase=Phase.PHASE_1_INFANCY,
            features={"tennis_technical_quality": 1.0},
        )
    )
    w2_smooth = asyncio.run(
        updater_smooth.update(
            current=w1_smooth,
            phase=Phase.PHASE_1_INFANCY,
            features={"tennis_technical_quality": 0.0},
        )
    )
    # τ=0.1 ⇒ EMA after step 2 = 0.1·0 + 0.9·1 = 0.9 ⇒ α still moves.
    assert w2_smooth.alpha[0] > w1_smooth.alpha[0]

    # EMA off (τ=1.0): no buffer, zero feature ⇒ zero gradient.
    updater_raw = WeightUpdater(learning_rate=0.05, ema_tau=1.0)
    w1_raw = asyncio.run(
        updater_raw.update(
            current=initial,
            phase=Phase.PHASE_1_INFANCY,
            features={"tennis_technical_quality": 1.0},
        )
    )
    w2_raw = asyncio.run(
        updater_raw.update(
            current=w1_raw,
            phase=Phase.PHASE_1_INFANCY,
            features={"tennis_technical_quality": 0.0},
        )
    )
    # τ=1.0 ⇒ EMA after step 2 = 1·0 + 0·1 = 0 ⇒ α freezes.
    assert w2_raw.alpha[0] == pytest.approx(w1_raw.alpha[0], abs=1e-12)


# ── Constructor input validation ─────────────────────────────────────


def test_weight_updater_constructor_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="learning_rate"):
        WeightUpdater(learning_rate=0.0)
    with pytest.raises(ValueError, match="learning_rate"):
        WeightUpdater(learning_rate=-0.1)
    with pytest.raises(ValueError, match="ema_tau"):
        WeightUpdater(ema_tau=0.0)
    with pytest.raises(ValueError, match="ema_tau"):
        WeightUpdater(ema_tau=1.1)


def test_default_learning_rate_is_conservative() -> None:
    """Defaults match the docstring rationale (η ≥ 0.1 caused flapping)."""
    assert DEFAULT_LEARNING_RATE == 0.05
