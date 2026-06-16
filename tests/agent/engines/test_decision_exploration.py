"""Exploration floor (Active Survival Hand-1, Task 4).

The agent freezes (0 bets/life) in some regimes and dies doing nothing. The
exploration floor forces a minimum non-zero bet rate on the FOUR explorable
abstains (low-confidence, no-edge, zero-kelly, below-min-size) by emitting a
FLAT-stake probe instead of a NO_BET, with probability ``epsilon``.

Hard invariant (this engine is SHARED by backtest + live): with
``exploration_epsilon=0`` OR ``exploration_rng=None`` the decision is
byte-identical to the frozen baseline — the RNG is touched ONLY inside the
explore branch, so a no-explore configuration never consumes a draw.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from agent.core.state import ActionKind
from agent.engines.decision import (
    NO_BET_NO_EDGE,
    DecisionEngine,
)


class _ExplodingRandom(random.Random):
    """A Random whose ``.random()`` raises — proves the RNG is untouched."""

    def random(self) -> float:  # type: ignore[override]
        raise AssertionError("exploration RNG must not be consumed here")


def _decide(engine: DecisionEngine, kwargs: dict[str, object]) -> object:
    return asyncio.run(engine.decide(**kwargs))  # type: ignore[arg-type]


# ── fixture guard ────────────────────────────────────────────────────


def test_fixture_actually_abstains_on_no_edge(
    no_edge_engine: DecisionEngine, no_edge_kwargs: dict[str, object]
) -> None:
    """Guard: the fixture must land on the no-edge abstain, else the rest
    of the suite is vacuously green."""
    action = _decide(no_edge_engine, no_edge_kwargs)
    assert action.kind is ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert action.no_bet_reason.startswith(NO_BET_NO_EDGE)


# ── byte-identical OFF paths ─────────────────────────────────────────


def test_epsilon_zero_never_touches_rng(
    no_edge_engine_kwargs: dict[str, float], no_edge_kwargs: dict[str, object]
) -> None:
    """epsilon=0 with an exploding RNG ⇒ still NO_BET (rng never consumed)."""
    engine = DecisionEngine(
        min_bet_size_usd=5.0,
        exploration_epsilon=0.0,
        exploration_rng=_ExplodingRandom(0),
        **no_edge_engine_kwargs,
    )
    action = _decide(engine, no_edge_kwargs)
    assert action.kind is ActionKind.NO_BET
    assert action.no_bet_reason is not None
    assert action.no_bet_reason.startswith(NO_BET_NO_EDGE)


def test_rng_none_never_explores(
    no_edge_engine_kwargs: dict[str, float], no_edge_kwargs: dict[str, object]
) -> None:
    """epsilon=1.0 but rng=None ⇒ the gate is closed, always NO_BET."""
    engine = DecisionEngine(
        min_bet_size_usd=5.0,
        exploration_epsilon=1.0,
        exploration_rng=None,
        **no_edge_engine_kwargs,
    )
    for _ in range(50):
        action = _decide(engine, no_edge_kwargs)
        assert action.kind is ActionKind.NO_BET


# ── explore behaviour ────────────────────────────────────────────────


def test_epsilon_drives_bet_rate(
    no_edge_engine_kwargs: dict[str, float], no_edge_kwargs: dict[str, object]
) -> None:
    """epsilon=0.2 over ~2000 calls ⇒ BET rate inside [0.15, 0.25]."""
    engine = DecisionEngine(
        min_bet_size_usd=5.0,
        exploration_epsilon=0.2,
        exploration_rng=random.Random(0),
        **no_edge_engine_kwargs,
    )
    n = 2000
    bets = sum(
        1
        for _ in range(n)
        if _decide(engine, no_edge_kwargs).kind is ActionKind.BET
    )
    rate = bets / n
    assert 0.15 <= rate <= 0.25, f"explore rate {rate} outside band"


def test_explored_bet_is_clean(
    no_edge_engine_kwargs: dict[str, float], no_edge_kwargs: dict[str, object]
) -> None:
    """A probe that fires is a clean BET: no reason, size >= min stake."""
    engine = DecisionEngine(
        min_bet_size_usd=5.0,
        exploration_epsilon=1.0,
        exploration_rng=random.Random(1),
        **no_edge_engine_kwargs,
    )
    action = _decide(engine, no_edge_kwargs)
    assert action.kind is ActionKind.BET
    assert action.no_bet_reason is None
    assert action.side is not None
    assert action.market_id == no_edge_kwargs["market_id"]
    assert action.edge_pct == 0.0
    assert action.size_usd is not None and action.size_usd >= 5.0


def test_explore_probe_clamped_below_min_is_no_bet(
    no_edge_engine_kwargs: dict[str, float], no_edge_kwargs: dict[str, object]
) -> None:
    """With liquidity_cap_usd=0 the flat-stake clamp drops below min ⇒
    NO_BET even though the gate said explore (sub-floor probe rejected)."""
    engine = DecisionEngine(
        min_bet_size_usd=5.0,
        exploration_epsilon=1.0,
        exploration_rng=random.Random(2),
        **no_edge_engine_kwargs,
    )
    kwargs = {**no_edge_kwargs, "liquidity_cap_usd": 0.0}
    action = _decide(engine, kwargs)
    assert action.kind is ActionKind.NO_BET


def test_missing_signal_never_explores(
    no_edge_engine_kwargs: dict[str, float],
    missing_signal_kwargs: dict[str, object],
) -> None:
    """Pre-fusion missing-signal abstain has no resolvable side ⇒ the
    exploration branch must never fire, even at epsilon=1.0."""
    engine = DecisionEngine(
        min_bet_size_usd=5.0,
        exploration_epsilon=1.0,
        exploration_rng=random.Random(3),
        **no_edge_engine_kwargs,
    )
    for _ in range(50):
        action = _decide(engine, missing_signal_kwargs)
        assert action.kind is ActionKind.NO_BET
        assert action.no_bet_reason is not None


# ── ctor validation ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
def test_epsilon_out_of_range_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        DecisionEngine(exploration_epsilon=bad)
