"""Tests for the deterministic synthetic-world generator (Active Survival Hand 1,
Task 1).

The generator must emit REAL ``SurvivalRow`` + ``MarketSnapshot`` objects the
existing groundhog sim accepts, with two regimes:

* ``build_synthetic_world`` — scores ``C=0.30`` ⇒ ABOVE the v3 value-mode edge
  gate (the agent bets); ``edge`` sets the true YES probability so ``agent_ev``
  recovers it within Monte-Carlo tolerance.
* ``build_subgate_world`` — scores ``C=0.05`` ⇒ BELOW the gate (the agent
  abstains with a ``NO_BET_NO_EDGE`` reason).

All randomness flows through a seeded ``random.Random(seed)`` so same-seed runs
are byte-identical.
"""

from __future__ import annotations

import asyncio
import math

from agent.backtest.cached_sweep import row_to_signals
from agent.backtest.synthetic_edge import (
    ENGINES,
    agent_ev,
    build_subgate_world,
    build_synthetic_world,
    quick_numerical_deaths,
)
from agent.core.state import ActionKind
from agent.engines.decision import NO_BET_NO_EDGE, DecisionEngine


def _v3_engine(price: float) -> DecisionEngine:
    """A DecisionEngine carrying the committed v3 value-mode knobs."""
    return DecisionEngine(
        kappa=0.49208984375,
        min_edge=0.034863281249999996,
        min_confidence=0.07558593749999999,
    )


def _decide(engine: DecisionEngine, row):
    """Run the engine's async decide over one row, value-mode (price=entry)."""
    return asyncio.run(
        engine.decide(
            signals=row_to_signals(row.signal),
            weights_alpha=(1 / 3, 1 / 3, 1 / 3),
            weights_beta=(0.5, 0.5),
            w_r=0.5,
            w_s=0.5,
            rho=0.85,
            bankroll_usd=100.0,
            breath=20.0,
            liquidity_cap_usd=row.liquidity_cap,
            market_id=row.market_id,
            price=row.entry_price,
        )
    )


def test_engines_are_the_five_decision_slots() -> None:
    assert len(ENGINES) == 5


def test_agent_ev_recovers_zero_edge_within_mc_tolerance() -> None:
    n = 4000
    rows, _ = build_synthetic_world(n, 0.0, 7)
    tol = 3 * 0.5 / math.sqrt(n)
    assert abs(agent_ev(rows) - 0.0) < tol


def test_agent_ev_recovers_positive_edge_within_mc_tolerance() -> None:
    n = 4000
    edge = 0.10
    rows, _ = build_synthetic_world(n, edge, 11)
    tol = 3 * 0.5 / math.sqrt(n)
    assert abs(agent_ev(rows) - edge) < tol


def test_build_synthetic_world_does_not_drop_rows_and_winning_price_is_one() -> None:
    rows, _ = build_synthetic_world(50, 0.08, 3)
    assert len(rows) == 50
    for r in rows:
        assert r.winning_price == 1.0


def test_subgate_rows_abstain_with_no_edge_reason() -> None:
    rows, _ = build_subgate_world(20, 0.0, 5)
    engine = _v3_engine(rows[0].entry_price)
    action = _decide(engine, rows[0])
    assert action.kind is ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert action.no_bet_reason.startswith(NO_BET_NO_EDGE)


def test_above_gate_rows_have_no_missing_signal_abstain() -> None:
    rows, _ = build_synthetic_world(20, 0.0, 9)
    engine = _v3_engine(rows[0].entry_price)
    action = _decide(engine, rows[0])
    # The 5 engine keys are all present ⇒ never a missing-signal abstain.
    reason = action.no_bet_reason or ""
    assert not reason.startswith("missing_engine_signal")


def test_mid_schedule_settlement_produces_deaths() -> None:
    rows, snaps = build_synthetic_world(400, 0.0, 1)
    deaths = quick_numerical_deaths(
        rows,
        snaps,
        loss_multiplier=3.0,
        initial_breath=20.0,
        max_lives=6,
        fragile_max_breath_risk_pct=0.95,
    )
    assert deaths > 1


def test_same_seed_is_deterministic() -> None:
    rows_a, _ = build_synthetic_world(60, 0.05, 42)
    rows_b, _ = build_synthetic_world(60, 0.05, 42)
    assert [r.market_id for r in rows_a] == [r.market_id for r in rows_b]
    assert [r.outcome for r in rows_a] == [r.outcome for r in rows_b]
