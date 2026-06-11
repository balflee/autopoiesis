"""_SettlementLearningWeightUpdater adapter — bridges the settlement
poller's flat-float `update(...)` Protocol to the real WeightUpdater.

Task L3 (Plan 2). The adapter:
  * flattens the poller's `score_<engine>` float keys back into a
    `signal_scores` dict + reads `pnl_usd` / `size_usd` / `bet_direction`,
  * maps the `WeightUpdaterPhase` string (e.g. "PHASE_2_EXTENDED") to the
    real `Phase` enum (which has no PHASE_2_EXTENDED member),
  * calls `WeightUpdater.update_from_settlement` and RE-ASSIGNS the loop's
    mutable `_weights`,
  * honors the loop's `_desperate` flag,
  * SKIPS + warns when `bet_direction` is absent (never silent YES-default).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent.core.state import Phase, Weights
from agent.engines.weight_updater import WeightUpdater


@dataclass
class _FakeHolder:
    """Stand-in for the loop: mutable _weights + _desperate."""

    _weights: Weights = field(
        default_factory=lambda: Weights(
            w_r=0.6,
            w_s=0.4,
            alpha=[1 / 3, 1 / 3, 1 / 3],
            beta=[0.5, 0.5],
            rho=0.5,
        )
    )
    _desperate: bool = False


def test_adapter_reassigns_holder_weights_on_yes_win() -> None:
    from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater

    holder = _FakeHolder()
    before = holder._weights
    adapter = _SettlementLearningWeightUpdater(
        inner=WeightUpdater(), weights_holder=holder
    )
    asyncio.run(
        adapter.update(
            phase="PHASE_2_EXTENDED",
            signals={
                "pnl_usd": 5.0,
                "size_usd": 10.0,
                "bet_direction": 1.0,
                "score_tennis_technical": 1.0,
                "score_market_momentum": 0.0,
            },
            outcome=object(),
        )
    )
    # weights object was re-assigned (not the same instance) and α[0] rose.
    assert holder._weights is not before
    assert holder._weights.alpha[0] > before.alpha[0]


def test_adapter_does_not_raise_on_phase_2_extended_string() -> None:
    """The 'PHASE_2_EXTENDED' string maps to Phase.PHASE_2_APPRENTICE —
    never KeyErrors via Phase[phase]."""
    from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater

    holder = _FakeHolder()
    adapter = _SettlementLearningWeightUpdater(
        inner=WeightUpdater(), weights_holder=holder
    )
    # Should complete without raising.
    asyncio.run(
        adapter.update(
            phase="PHASE_2_EXTENDED",
            signals={
                "pnl_usd": 1.0,
                "size_usd": 10.0,
                "bet_direction": 1.0,
                "score_tennis_technical": 0.5,
            },
            outcome=object(),
        )
    )


def test_adapter_skips_update_when_bet_direction_absent() -> None:
    """Missing bet_direction → SKIP + warn (no silent YES-default), so the
    holder's weights are UNCHANGED (not just inverted)."""
    from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater

    holder = _FakeHolder()
    before = holder._weights
    adapter = _SettlementLearningWeightUpdater(
        inner=WeightUpdater(), weights_holder=holder
    )
    asyncio.run(
        adapter.update(
            phase="PHASE_2_EXTENDED",
            signals={
                "pnl_usd": 5.0,
                "size_usd": 10.0,
                "score_tennis_technical": 1.0,
            },
            outcome=object(),
        )
    )
    # No bet_direction → no change at all.
    assert holder._weights is before


def test_adapter_accepts_phase_enum_directly() -> None:
    """A real Phase enum passes through unchanged (not only the string form)."""
    from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater

    holder = _FakeHolder()
    adapter = _SettlementLearningWeightUpdater(
        inner=WeightUpdater(), weights_holder=holder
    )
    asyncio.run(
        adapter.update(
            phase=Phase.PHASE_2_APPRENTICE,
            signals={
                "pnl_usd": 5.0,
                "size_usd": 10.0,
                "bet_direction": 1.0,
                "score_tennis_technical": 1.0,
            },
            outcome=object(),
        )
    )
    assert holder._weights.alpha[0] > 1 / 3
