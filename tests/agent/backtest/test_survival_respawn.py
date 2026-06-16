# tests/agent/backtest/test_survival_respawn.py
"""A2 — multi-life FRESH-loop respawn driver.

A *life* is ONE ``SandboxPhase2Loop.run()`` (with settlement learning ON) that
runs until ``breath<=0`` death. The SAME loop can NOT resurrect (``run()``
returns immediately once ``_alive`` is False). So :func:`run_survival_season`
drives the season as a SEQUENCE of lives: on each death it captures the dead
loop's evolved weights + death facts, then constructs a BRAND-NEW loop with a
fresh state dir + fresh chain adapter / breath / bankroll, ``initial_weights``
carried from the previous life, the schedule cursor advanced PAST the markets the
dead life already consumed, and any unsettled open bets voided.

Cross-death learning persistence (codex H3 + R2-MED): ONE inner
:class:`~agent.engines.weight_updater.WeightUpdater` (the EMA owner) is SHARED
across every life, but each life gets a FRESH
:class:`~agent.backtest.settlement_learner._SettlementLearningWeightUpdater`
bound to its own loop (the adapter is loop-bound via ``weights_holder``; reusing
it would mutate the DEAD loop, and a fresh inner updater per life would drop the
EMA).

Every constructed loop MUST pass ``strategy_advisor=NoOpStrategyAdvisor()`` (else
the loop defaults to the live Gemini advisor) AND
``decision_engine=DecisionEngine(...)`` threaded from the seed ``StrategyConfig``
(else the loop falls back to ``DecisionEngine()`` defaults, changing bet/no-bet
behavior).

TDD on a TINY 3-market fixture engineered to DIE: the seed is strongly bullish,
so it bets YES; the fixture markets resolve "no", so each bet LOSES its full
stake; with a low ``initial_breath`` the first loss already drives breath to 0,
forcing a death + respawn. The proof asserts >=1 death + respawn + that the
shared EMA carried (weights moved off the seed across the death).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.survival_season import (
    SurvivalRow,
    run_survival_season,
)
from agent.core.state import Weights

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "surface_advantage",
    "head_to_head",
    "rest_recency",
)


def _bullish_weights() -> Weights:
    # Strongly weight the (positive) signal scores so the fusion clears the
    # confidence/edge gate and the loop BETS YES.
    return Weights(
        w_r=0.5, w_s=0.5, alpha=[0.34, 0.33, 0.33], beta=[1.0, 0.0], rho=0.6
    )


def _fragile_seed() -> StrategyConfig:
    # Carries weights AND sizing/abstention knobs (codex R8). Sizing is fixed
    # per seed; learning evolves only the fusion Weights. Loose gates so the
    # bullish signals always BET on the tiny fixture.
    #
    # ``max_breath_risk_pct=1.0`` is the DELIBERATELY FRAGILE calibration the
    # A3b survival story needs on this fixture: the bet is sized at the full
    # breath cap (``breath_cap = breath * 1.0 / conversion_rate`` binds below the
    # liquidity/bankroll caps), so a single full-stake LOSS (a YES bet on a "no"
    # market) drains breath to exactly 0 -> a death. A risk_pct < 1.0 leaves a
    # geometric residual and the asymptote never reaches the ``breath <= 0`` line.
    return StrategyConfig(
        weights=_bullish_weights(),
        max_breath_risk_pct=1.0,
        min_confidence=0.05,
        min_bet_size_usd=1.0,
    )


def _snap(
    market_id: str,
    *,
    entry_ts: str,
    end_date: str,
    resolution: str,
    entry_price: float = 0.50,
    outcome: Literal["yes", "no", "void"] = "no",
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        slug=f"atp-{market_id}",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _row(snap: MarketSnapshot, *, score: float = 0.8) -> SurvivalRow:
    entry_ts = snap.price_ledger[0].ts
    entry_price = snap.price_ledger[0].mid_price
    signal = SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: score for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=entry_price,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
    )
    return SurvivalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        signal=signal,
        entry_asof_ts_iso=entry_ts,
        resolution_ts_iso=snap.resolution_ts_iso,
        end_date_iso=snap.end_date_iso,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap=snap.liquidity_cap_usd,
        players=("alpha", "bravo"),
        surface="Hard",
    )


def _fixture() -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    # Three markets, each entering days apart so each life's single bet settles
    # IN FLIGHT (or at the final drain) and drives breath. All resolve "no" so a
    # YES bet loses its full stake.
    snaps = [
        _snap(
            "m1",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
        _snap(
            "m2",
            entry_ts="2025-06-05T00:00:00+00:00",
            end_date="2025-06-05T12:00:00+00:00",
            resolution="2025-06-05T20:00:00+00:00",
        ),
        _snap(
            "m3",
            entry_ts="2025-06-10T00:00:00+00:00",
            end_date="2025-06-10T12:00:00+00:00",
            resolution="2025-06-10T20:00:00+00:00",
        ),
    ]
    rows = [_row(s) for s in snaps]
    return rows, snaps


def test_season_dies_and_respawns_with_persisted_weights(tmp_path: Path) -> None:
    rows, snaps = _fixture()
    seed = _fragile_seed()

    # initial_breath = one losing $5-ish stake worth: a single full-stake loss
    # drives breath to 0. Sizing is bounded by liquidity_cap (20) * risk; the
    # bet is a few dollars, so a low initial_breath guarantees a death on the
    # first settled loss.
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "season",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
    )

    # >=1 death occurred (the headline survival drama the master plan requires).
    assert result.deaths >= 1, f"expected >=1 death, got {result.deaths}"
    # >=2 lives means a death produced a respawn (a NEW loop after the first
    # died), not a single life that ran to exhaustion.
    assert len(result.lives) >= 2, "a death must produce a respawn (a new life)"

    # The first life died.
    first = result.lives[0]
    assert first.died is True
    assert first.death is not None
    assert first.death.kill_tx_hash  # a real DeathReceipt was captured

    # Cross-death weight PERSISTENCE: the second life STARTED from the first
    # life's evolved (post-settlement) weights, NOT the raw seed — settlement
    # learning moved the weights off the seed and the respawn carried them.
    assert result.lives[1].initial_weights == first.terminal_weights
    assert first.terminal_weights != seed.weights, (
        "settlement learning must have moved weights off the seed during life 0"
    )

    # The schedule cursor advanced: across all lives every fixture market is
    # consumed at most once (no market is re-decided after a respawn).
    consumed = [mid for life in result.lives for mid in life.consumed_market_ids]
    assert len(consumed) == len(set(consumed)), "a market was decided twice"
    assert set(consumed) <= {"m1", "m2", "m3"}


def test_shared_inner_weight_updater_carries_ema_across_lives(
    tmp_path: Path,
) -> None:
    # The SAME inner WeightUpdater instance is shared across every life (its EMA
    # is the cross-death state); each life gets a FRESH adapter bound to its own
    # loop. Proven structurally: the season exposes the single shared inner
    # updater, and >=2 lives ran against it.
    rows, snaps = _fixture()
    seed = _fragile_seed()
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "season",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
    )
    # One shared inner updater object across the whole season.
    assert result.shared_weight_updater is not None
    # Its EMA buffer was populated by settlements (the lazily-allocated keyset is
    # non-empty once at least one settlement-driven update ran).
    assert result.shared_weight_updater._ema, (
        "the shared inner WeightUpdater EMA must have been fed across lives"
    )
    assert result.deaths >= 1


def test_no_death_returns_single_life(tmp_path: Path) -> None:
    # Falsification control: with a HIGH initial_breath and markets that all WIN
    # (resolve "yes" for a YES bet), no death occurs -> exactly one life that
    # ran to schedule exhaustion. The season must NOT fabricate a respawn.
    snaps = [
        _snap(
            "w1",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
            outcome="yes",
        ),
        _snap(
            "w2",
            entry_ts="2025-06-05T00:00:00+00:00",
            end_date="2025-06-05T12:00:00+00:00",
            resolution="2025-06-05T20:00:00+00:00",
            outcome="yes",
        ),
    ]
    rows = [_row(s) for s in snaps]
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "season",
        initial_breath=100.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
    )
    assert result.deaths == 0
    assert len(result.lives) == 1
    assert result.lives[0].died is False
    # All markets consumed in the single life.
    assert set(result.lives[0].consumed_market_ids) == {"w1", "w2"}


def test_each_life_uses_a_fresh_state_dir(tmp_path: Path) -> None:
    # Each constructed loop gets its OWN state dir under the season root so the
    # dead life's persisted snapshot can't leak into the respawn's
    # reconstruction (a respawn is a COLD start with carried weights).
    rows, snaps = _fixture()
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "season",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
    )
    dirs = [life.state_dir for life in result.lives]
    assert len(dirs) == len(set(dirs)), "lives must not share a state dir"
    for d in dirs:
        assert d.exists()


def test_max_lives_caps_the_season(tmp_path: Path) -> None:
    # If the agent keeps dying, the season stops at max_lives rather than
    # looping forever (every life dies on its first losing bet here).
    rows, snaps = _fixture()
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "season",
        initial_breath=1.0,  # dies on the very first settled loss
        initial_bankroll_usd=100.0,
        max_lives=2,
    )
    assert len(result.lives) <= 2


def test_season_start_clock_is_first_entry(tmp_path: Path) -> None:
    # Sanity: the season schedules the first life from the earliest entry time
    # so the first decision lands at the right wall-clock (not "now").
    rows, snaps = _fixture()
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "season",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
    )
    # The first life's first consumed market is m1 (earliest entry 06-01).
    assert result.lives[0].consumed_market_ids[0] == "m1"
    assert result.lives[0].start_ts == datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
