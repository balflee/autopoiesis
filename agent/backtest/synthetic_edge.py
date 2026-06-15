"""Deterministic synthetic-world generator (Active Survival Hand 1, Task 1).

Builds REAL :class:`SignalRow` + :class:`MarketSnapshot` objects the existing
groundhog sim consumes, joined into :class:`SurvivalRow` via
:func:`build_survival_rows`. Two regimes, both numerical-only (NO LLM, NO API):

* :func:`build_synthetic_world` — all 5 engine scores ``= C = 0.30`` ⇒ the
  v3 value-mode fused edge clears ``min_edge`` so the agent BETS. ``edge`` sets
  the true YES probability, so :func:`agent_ev` recovers it (within Monte-Carlo
  noise). This is the calibration/validation universe.
* :func:`build_subgate_world` — identical but ``C = 0.05`` ⇒ the fused edge is
  BELOW ``min_edge`` so the agent ABSTAINS (the exploration-floor probe).

Settlement clock (the load-bearing timing trick):
    entry_i        = base_ts + i·1day
    end_date_iso_i = resolution_ts_i = entry_i + 1min
    due_i          = max(end_date + 2h lag, resolution) ≈ entry_i + 2h
so each bet settles ~22 h BEFORE the next entry — mid-schedule — letting a
fragile dead world die repeatedly across a single pass.

All randomness flows through a single seeded ``random.Random(seed)``; there is
NO use of the unseeded global ``random`` / ``numpy.random``. ``base_ts`` is a
fixed constant (never ``datetime.now()``) so worlds are byte-deterministic.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.survival_season import (
    SurvivalRow,
    build_survival_rows,
    run_survival_over_rows,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.core.state import Weights
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES

# The 5 engine slot-keys the fusion consumes; ``decide()`` short-circuits to a
# missing-signal abstain if any is absent, so every synthetic row carries all 5.
ENGINES: tuple[str, ...] = (*RATIONAL_ENGINES, *SENTIENT_ENGINES)
assert len(ENGINES) == 5, "expected exactly 5 decision engine slots"

# Above-gate / below-gate confidence-anchor scores (see module docstring). With
# the v3 seed (kappa≈0.492, all conf=0.8) the fused edge ≈ 0.394·C, so C=0.30
# clears min_edge≈0.0349 (above gate) and C=0.05 does not (below gate).
_ABOVE_GATE_C = 0.30
_BELOW_GATE_C = 0.05
_CONFIDENCE = 0.8
_ENTRY_PRICE = 0.5
_LIQUIDITY_CAP_USD = 1000.0

# Fixed deterministic base wall-clock — NEVER datetime.now().
_BASE_TS = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _v3_seed() -> StrategyConfig:
    """The committed v3 value-mode seed (``docs/backtest/value_seed_v3.json``).

    Inlined verbatim (not file-read) so the synthetic season is self-contained
    and deterministic. This is the SAME seed the Task-1 gate math is calibrated
    against (kappa≈0.492, min_edge≈0.0349): with it the C=0.30 above-gate edge
    sizes a bet ABOVE the $4 min-bet floor so settlements — and deaths — occur.
    The platform DEFAULT_OPTIMUM_SEED (kappa=0.25) would size every above-gate
    bet at ~$1.79 < $4 ⇒ no settlements ⇒ no deaths, defeating the
    mid-schedule-settlement assertion.
    """
    return StrategyConfig(
        weights=Weights(
            alpha=[0.177734375, 0.0703125, 0.751953125],
            beta=[0.767578125, 0.232421875],
            rho=0.849609375,
            w_r=0.583984375,
            w_s=0.416015625,
        ),
        max_breath_risk_pct=0.38134765625,
        min_confidence=0.07558593749999999,
        min_bet_size_usd=4.0,
        min_edge=0.034863281249999996,
        kappa=0.49208984375,
    )


def _iso(ts: datetime) -> str:
    """ISO-8601 UTC string (verbatim round-trippable by the ledger)."""
    return ts.isoformat()


def _make_pair(
    i: int,
    *,
    price: float,
    C: float,
    won: bool,
    base_ts: datetime,
) -> tuple[SignalRow, MarketSnapshot]:
    """Build the matching ``(SignalRow, MarketSnapshot)`` for market ``i``.

    The slug ``alpha{i}-vs-beta{i}`` deliberately does NOT parse as a tennis
    match (digit suffix) ⇒ players/surface resolve to ``None`` (benign). The
    flat 2-point ledger at ``price`` makes the recomputed mid == ``price``
    exactly, so the entry-price consistency check passes. ``winning_price=1.0``
    is the WINNING side's price (≈1.0), NOT a YES flag.
    """
    market_id = f"syn-{i:06d}"
    slug = f"alpha{i}-vs-beta{i}"
    outcome = "yes" if won else "no"

    entry_ts = base_ts + i * timedelta(days=1)
    settle_ts = entry_ts + timedelta(minutes=1)

    signal_row = SignalRow(
        market_id=market_id,
        slug=slug,
        scores={k: C for k in ENGINES},
        confidences={k: _CONFIDENCE for k in ENGINES},
        entry_price=price,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=_LIQUIDITY_CAP_USD,
    )
    snapshot = MarketSnapshot(
        market_id=market_id,
        slug=slug,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=_LIQUIDITY_CAP_USD,
        end_date_iso=_iso(settle_ts),
        resolution_ts_iso=_iso(settle_ts),
        price_ledger=[
            PricePoint(ts=_iso(entry_ts), mid_price=price),
            PricePoint(ts=_iso(entry_ts + timedelta(seconds=1)), mid_price=price),
        ],
    )
    return signal_row, snapshot


def _build_world(
    n: int, edge: float, seed: int, *, C: float
) -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    """Shared world builder for the above/below-gate regimes.

    ``true_prob = clip(0.5 + edge, 0, 1)``; each row's YES outcome is drawn as
    ``won = rng.random() < true_prob`` from a single seeded RNG, so
    :func:`agent_ev` recovers ``edge`` (within Monte-Carlo noise) and same-seed
    worlds are byte-identical.
    """
    rng = random.Random(seed)
    true_prob = min(1.0, max(0.0, 0.5 + edge))
    signal_rows: list[SignalRow] = []
    snaps: list[MarketSnapshot] = []
    for i in range(n):
        won = rng.random() < true_prob
        sig, snap = _make_pair(i, price=_ENTRY_PRICE, C=C, won=won, base_ts=_BASE_TS)
        signal_rows.append(sig)
        snaps.append(snap)
    rows = build_survival_rows(
        signal_rows, snaps, TennisMatchResolver(name_index={})
    )
    return rows, snaps


def build_synthetic_world(
    n: int, edge: float, seed: int
) -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    """``n`` ABOVE-gate (C=0.30) synthetic markets with true edge ``edge``."""
    return _build_world(n, edge, seed, C=_ABOVE_GATE_C)


def build_subgate_world(
    n: int, edge: float, seed: int
) -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    """``n`` BELOW-gate (C=0.05) synthetic markets — the agent abstains."""
    return _build_world(n, edge, seed, C=_BELOW_GATE_C)


def agent_ev(rows: list[SurvivalRow]) -> float:
    """Mean per-row YES payoff minus entry price — the realized edge estimate.

    ``(1 if outcome=="yes" else 0) - entry_price`` averaged over rows. With
    ``entry_price=0.5`` and ``true_prob=0.5+edge`` this converges to ``edge``.
    """
    if not rows:
        return 0.0
    return sum(
        (1.0 if r.outcome == "yes" else 0.0) - r.entry_price for r in rows
    ) / len(rows)


def quick_numerical_deaths(
    rows: list[SurvivalRow],
    snaps: list[MarketSnapshot],
    *,
    loss_multiplier: float,
    initial_breath: float,
    max_lives: int,
    fragile_max_breath_risk_pct: float = 0.95,
) -> int:
    """Run the NUMERICAL groundhog season over ``rows`` and return death count.

    Numerical-only (``with_ai=False``, ``preflight=False``) — no LLM, no API.
    Returns ``summary.deaths``; a value ``> 1`` over a 1-day-spaced world proves
    mid-schedule settlement (bets settle between entries, so a fragile world can
    die more than once across a single pass).
    """
    journey = run_survival_over_rows(
        rows,
        snaps,
        base_seed=_v3_seed(),
        loss_multiplier=loss_multiplier,
        initial_breath=initial_breath,
        max_lives=max_lives,
        fragile_max_breath_risk_pct=fragile_max_breath_risk_pct,
        with_ai=False,
        preflight=False,
    )
    return journey["summary"]["deaths"]
