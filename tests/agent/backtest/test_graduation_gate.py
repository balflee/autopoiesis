"""V1.6 — graduation gate: real edge → GO; zero-edge / cost-eaten → NO_GO (placebo guard)."""

from __future__ import annotations

import random

from agent.backtest.graduation_gate import ProbeBet, evaluate_graduation


def test_strong_real_edge_graduates() -> None:
    # Bettor wins 90% on coin-flip-priced (0.5) markets → ~+80% ROI, far beyond
    # the market-efficiency null (which would win ~50%).
    rng = random.Random(1)
    bets = [
        ProbeBet(stake_usd=5.0, entry_price=0.5, won=(rng.random() < 0.9))
        for _ in range(400)
    ]
    res = evaluate_graduation(bets, seed=0)
    assert res.verdict == "GO"
    assert res.gain >= 0.2
    assert res.beats_placebo


def test_zero_edge_is_no_go_via_placebo() -> None:
    # No edge: win-rate == price (market-efficient). Real ROI ≈ 0 and does NOT
    # beat the placebo p95 → NO_GO even before the threshold check.
    rng = random.Random(2)
    bets = [
        ProbeBet(stake_usd=5.0, entry_price=0.6, won=(rng.random() < 0.6))
        for _ in range(500)
    ]
    res = evaluate_graduation(bets, seed=0)
    assert res.verdict == "NO_GO"


def test_cost_eats_a_thin_edge_to_no_go() -> None:
    # A small raw edge wiped out by per-bet cost → NO_GO (the A18 trap guard).
    rng = random.Random(3)
    bets = [
        ProbeBet(
            stake_usd=5.0, entry_price=0.5,
            won=(rng.random() < 0.55), cost_usd=0.6,
        )
        for _ in range(400)
    ]
    res = evaluate_graduation(bets, seed=0)
    assert res.verdict == "NO_GO"


def test_empty_is_no_go() -> None:
    assert evaluate_graduation([], seed=0).verdict == "NO_GO"


def test_reproducible_under_seed() -> None:
    bets = [ProbeBet(stake_usd=5.0, entry_price=0.5, won=(i % 2 == 0)) for i in range(50)]
    a = evaluate_graduation(bets, seed=7)
    b = evaluate_graduation(bets, seed=7)
    assert a == b
