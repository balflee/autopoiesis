# tests/agent/backtest/test_cached_sweep.py
from __future__ import annotations

from pathlib import Path

from agent.backtest.cached_sweep import (
    SignalRow,
    SweepMetrics,
    compute_bet_pnl,
    load_rows,
    main,
    precompute_rows,
    row_to_signals,
    run_cached_sweep,
    save_rows,
    score_config,
    score_config_sync,
)
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.real_signal_source import RealSignalSource
from agent.backtest.tennis_match_resolver import TennisMatchResolver, build_name_index
from agent.core.state import Weights
from agent.engines.base import Signal
from agent.engines.decision import (
    CROWD_VOLUME,
    MARKET_MOMENTUM,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
)

_TINY = Path(__file__).parent / "fixtures" / "sackmann_tiny"


def test_pnl_winner_yes() -> None:
    # entered YES at 0.40, YES wins at 1.0, $10 stake -> 10*(1/0.4 - 1) = 15.0
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="yes", winning_price=1.0) == 15.0


def test_pnl_loser_is_minus_stake() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="no", winning_price=1.0) == -10.0


def test_pnl_no_side_wins_when_outcome_no() -> None:
    assert compute_bet_pnl(side="NO", entry_price=0.25, size_usd=8.0,
                           outcome="no", winning_price=1.0) == 8.0 * (1.0 / 0.25 - 1.0)


def test_pnl_void_is_zero() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.4, size_usd=10.0,
                           outcome="void", winning_price=1.0) == 0.0


def test_pnl_zero_entry_winner_clips() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.0, size_usd=10.0,
                           outcome="yes", winning_price=1.0) == 10.0 * 1.0


# --- Optional per-bet PROFIT cap (survival-backtest realism rule) ------------


def test_pnl_cap_clamps_extreme_longshot_win() -> None:
    # $5 at 0.0005 -> $9,995 uncapped lottery payout; cap clamps it.
    assert compute_bet_pnl(side="NO", entry_price=0.0005, size_usd=5.0,
                           outcome="no", winning_price=1.0) == 9995.0
    assert compute_bet_pnl(side="NO", entry_price=0.0005, size_usd=5.0,
                           outcome="no", winning_price=1.0,
                           max_pnl_usd=100.0) == 100.0


def test_pnl_cap_default_none_unchanged() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="yes", winning_price=1.0,
                           max_pnl_usd=None) == 15.0


def test_pnl_cap_never_clamps_loss_or_void() -> None:
    # Profit-only cap: losses and voids pass through untouched.
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="no", winning_price=1.0,
                           max_pnl_usd=0.01) == -10.0
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="void", winning_price=1.0,
                           max_pnl_usd=0.01) == 0.0


def test_pnl_cap_clamps_degenerate_zero_entry_branch() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.0, size_usd=500.0,
                           outcome="yes", winning_price=1.0,
                           max_pnl_usd=100.0) == 100.0


# --- Task 2: SignalRow + precompute_rows (mid-market entry, real signals) ----
#
# Reuse the sackmann_tiny fixture (Sinner 200001 / Shelton 200002 / Tiafoe
# 200003). A `...-Sinner-vs-Shelton` slug resolves via the tiny name index, so
# precompute_rows must populate REAL tennis facets and the real momentum slot.


class _FakeProvider:
    """Structural MarketSnapshotProvider: .get(market_id) -> MarketSnapshot."""

    def __init__(self, snap: MarketSnapshot) -> None:
        self._snap = snap

    def get(self, market_id: str) -> MarketSnapshot:
        return self._snap


def _tiny_resolver() -> TennisMatchResolver:
    import pandas as pd

    df = pd.read_csv(_TINY / "atp_matches_2025.csv", dtype=str).fillna("")
    return TennisMatchResolver(name_index=build_name_index([df]))


def _tiny_loader():
    from data.sources.tennis_sackmann import SackmannLoader

    return SackmannLoader(snapshot_dir=_TINY)


def _resolvable_snap() -> MarketSnapshot:
    # Multi-point ledger spanning 00:00 -> 18:00. Mid-market (entry_fraction=0.5)
    # lands at 09:00; the last point at-or-before 09:00 is the 06:00 point with
    # mid_price=0.50 (the 12:00 / 18:00 points are later).
    return MarketSnapshot(
        market_id="m_resolvable",
        slug="test-open-2025-06-01-Sinner-vs-Shelton",
        end_date_iso="2025-06-02T00:00:00+00:00",
        resolution_ts_iso="2025-06-01T23:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[
            PricePoint(ts="2025-06-01T00:00:00+00:00", mid_price=0.40),
            PricePoint(ts="2025-06-01T06:00:00+00:00", mid_price=0.50),
            PricePoint(ts="2025-06-01T12:00:00+00:00", mid_price=0.55),
            PricePoint(ts="2025-06-01T18:00:00+00:00", mid_price=0.70),
        ],
    )


def _unresolved_outcome_snap() -> MarketSnapshot:
    # Resolves to players but outcome is None -> cannot score PnL -> excluded.
    return MarketSnapshot(
        market_id="m_no_outcome",
        slug="test-open-2025-06-01-Sinner-vs-Shelton",
        end_date_iso="2025-06-02T00:00:00+00:00",
        resolution_ts_iso=None,
        outcome=None,
        winning_price=None,
        liquidity_cap_usd=20.0,
        price_ledger=[
            PricePoint(ts="2025-06-01T00:00:00+00:00", mid_price=0.40),
        ],
    )


def _unresolvable_slug_snap() -> MarketSnapshot:
    # Outcome present but slug does not resolve (not a -vs- match) -> excluded.
    return MarketSnapshot(
        market_id="m_bad_slug",
        slug="will-rain-stop-play",
        end_date_iso="2025-06-02T00:00:00+00:00",
        resolution_ts_iso="2025-06-01T23:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[
            PricePoint(ts="2025-06-01T00:00:00+00:00", mid_price=0.40),
        ],
    )


def _src(snap: MarketSnapshot) -> RealSignalSource:
    return RealSignalSource(
        provider=_FakeProvider(snap),
        resolver=_tiny_resolver(),
        loader=_tiny_loader(),
        year_range=(2025, 2025),
    )


def test_precompute_rows_builds_one_resolvable_row() -> None:
    snap = _resolvable_snap()
    resolver = _tiny_resolver()
    rows = precompute_rows([snap], resolver, _src(snap), entry_fraction=0.5)

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, SignalRow)
    assert row.market_id == "m_resolvable"
    assert row.slug == snap.slug
    # All 5 slots populated in both score + confidence maps.
    expected_slots = {
        "tennis_technical", "market_momentum", "smart_money",
        "sentiment_llm", "crowd_volume",
    }
    assert set(row.scores) == expected_slots
    assert set(row.confidences) == expected_slots
    # Tennis facets are REAL (Sinner strongly favoured over Shelton).
    assert row.scores["tennis_technical"] > 0.5
    assert row.confidences["tennis_technical"] == 0.7
    # Mid-market entry: 50% of a 00:00 -> 18:00 span lands at 09:00; the last
    # point at-or-before 09:00 is the 06:00 point (mid_price=0.50).
    assert row.entry_price == 0.50
    # Settlement facts copied straight off the snap.
    assert row.outcome == "yes"
    assert row.winning_price == 1.0
    assert row.liquidity_cap_usd == 20.0


def test_precompute_rows_excludes_none_outcome() -> None:
    snap = _unresolved_outcome_snap()
    resolver = _tiny_resolver()
    rows = precompute_rows([snap], resolver, _src(snap), entry_fraction=0.5)
    assert rows == []


def test_precompute_rows_excludes_unresolvable_slug() -> None:
    snap = _unresolvable_slug_snap()
    resolver = _tiny_resolver()
    rows = precompute_rows([snap], resolver, _src(snap), entry_fraction=0.5)
    assert rows == []


def test_precompute_rows_mixed_batch_keeps_only_scorable() -> None:
    good = _resolvable_snap()
    bad_outcome = _unresolved_outcome_snap()
    bad_slug = _unresolvable_slug_snap()
    snaps = [good, bad_outcome, bad_slug]
    resolver = _tiny_resolver()

    # Provider must return the right snap per market_id for the mixed batch.
    class _MultiProvider:
        def __init__(self, items: list[MarketSnapshot]) -> None:
            self._by_id = {s.market_id: s for s in items}

        def get(self, market_id: str) -> MarketSnapshot:
            return self._by_id[market_id]

    src = RealSignalSource(
        provider=_MultiProvider(snaps),
        resolver=resolver,
        loader=_tiny_loader(),
        year_range=(2025, 2025),
    )
    rows = precompute_rows(snaps, resolver, src, entry_fraction=0.5)
    assert [r.market_id for r in rows] == ["m_resolvable"]


# --- Task 3: row_to_signals + score_config (real decide + PnL) ---------------
#
# These tests use the REAL DecisionEngine.decide (real fusion + 4-constraint
# sizing) and the REAL compute_bet_pnl — no mocks. The SignalRows are
# hand-built so the fused sign + confidence floor are known.

_ALL_SLOTS = (
    TENNIS_TECHNICAL,
    MARKET_MOMENTUM,
    SMART_MONEY,
    SENTIMENT_LLM,
    CROWD_VOLUME,
)


def _flat_weights() -> Weights:
    """Equal-weight fusion: every slot contributes; w_r=w_s=0.5, rho=1.0."""
    return Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
        beta=[0.5, 0.5],
        rho=1.0,
    )


def _cfg(*, min_confidence: float = 0.05, min_bet_size_usd: float = 1.0) -> StrategyConfig:
    return StrategyConfig(
        weights=_flat_weights(),
        max_breath_risk_pct=0.30,
        min_confidence=min_confidence,
        min_bet_size_usd=min_bet_size_usd,
    )


def _row(*, score: float, confidence: float, entry_price: float = 0.40,
         outcome: str = "yes", winning_price: float = 1.0) -> SignalRow:
    return SignalRow(
        market_id="m1",
        slug="test-open-2025-06-01-Sinner-vs-Shelton",
        scores={k: score for k in _ALL_SLOTS},
        confidences={k: confidence for k in _ALL_SLOTS},
        entry_price=entry_price,
        outcome=outcome,
        winning_price=winning_price,
        liquidity_cap_usd=20.0,
    )


def test_row_to_signals_rebuilds_all_five_slots() -> None:
    row = _row(score=0.6, confidence=0.8)
    sigs = row_to_signals(row)
    assert set(sigs) == set(_ALL_SLOTS)
    for k in _ALL_SLOTS:
        assert isinstance(sigs[k], Signal)
        assert sigs[k].score == 0.6
        assert sigs[k].confidence == 0.8


def test_score_config_bets_and_matches_pnl() -> None:
    # Strong positive scores + high confidence -> fused > 0 -> BET YES.
    row = _row(score=0.6, confidence=0.8, entry_price=0.40,
               outcome="yes", winning_price=1.0)
    metrics = score_config_sync([row], _cfg())

    assert isinstance(metrics, SweepMetrics)
    assert metrics.bets == 1
    # YES wins (outcome == "yes"); net_pnl must equal the faithful per-bet PnL
    # for the size the real DecisionEngine sized. avg_size is that same size.
    expected = compute_bet_pnl(
        side="YES", entry_price=0.40, size_usd=metrics.avg_size,
        outcome="yes", winning_price=1.0,
    )
    assert metrics.net_pnl == expected
    assert metrics.net_pnl > 0.0  # a winning YES bet
    assert metrics.win_rate == 1.0
    assert metrics.avg_size > 0.0
    # A single bet has no spread -> sharpe is 0.0 (needs >= 2 bets).
    assert metrics.sharpe == 0.0


def test_score_config_low_confidence_no_bet() -> None:
    # mean_confidence below the engine's min_confidence floor -> NO_BET.
    row = _row(score=0.6, confidence=0.02)
    metrics = score_config_sync([row], _cfg(min_confidence=0.10))
    assert metrics.bets == 0
    assert metrics.net_pnl == 0.0
    assert metrics.win_rate == 0.0
    assert metrics.avg_size == 0.0
    assert metrics.sharpe == 0.0


def test_score_config_async_matches_sync() -> None:
    import asyncio

    row = _row(score=0.6, confidence=0.8)
    cfg = _cfg()
    async_metrics = asyncio.run(score_config([row], cfg))
    sync_metrics = score_config_sync([row], cfg)
    assert async_metrics == sync_metrics


def test_score_config_sharpe_two_bets() -> None:
    # Two betting rows with different outcomes -> a winner and a loser -> a
    # non-zero pnl spread -> a finite, non-zero per-bet sharpe.
    win = _row(score=0.6, confidence=0.8, entry_price=0.40,
               outcome="yes", winning_price=1.0)
    lose = _row(score=0.6, confidence=0.8, entry_price=0.40,
                outcome="no", winning_price=1.0)
    metrics = score_config_sync([win, lose], _cfg())
    assert metrics.bets == 2
    assert metrics.win_rate == 0.5
    assert metrics.sharpe != 0.0


# --- Task 4: run_cached_sweep + save/load + CLI ------------------------------
#
# save_rows/load_rows round-trip equal rows through JSON; run_cached_sweep
# scores a list of configs in input order over the cached rows; the CLI
# precompute/sweep subcommands build + score end-to-end.


def test_save_load_rows_round_trips(tmp_path: Path) -> None:
    rows = [
        _row(score=0.6, confidence=0.8, entry_price=0.40,
             outcome="yes", winning_price=1.0),
        SignalRow(
            market_id="m2",
            slug="test-open-2025-06-01-Tiafoe-vs-Shelton",
            scores={k: 0.1 for k in _ALL_SLOTS},
            confidences={k: 0.3 for k in _ALL_SLOTS},
            entry_price=0.55,
            outcome="no",
            winning_price=1.0,
            liquidity_cap_usd=42.0,
        ),
    ]
    path = tmp_path / "rows.json"
    save_rows(rows, path)
    loaded = load_rows(path)
    assert loaded == rows
    assert all(isinstance(r, SignalRow) for r in loaded)


def test_run_cached_sweep_preserves_input_order() -> None:
    row = _row(score=0.6, confidence=0.8, entry_price=0.40,
               outcome="yes", winning_price=1.0)
    cfg_a = _cfg(min_confidence=0.05)
    cfg_b = _cfg(min_confidence=0.99)  # floor so high nothing bets
    results = run_cached_sweep([row], [cfg_a, cfg_b])

    assert [c for c, _ in results] == [cfg_a, cfg_b]
    for _cfg_out, metrics in results:
        assert isinstance(metrics, SweepMetrics)
        import math

        assert math.isfinite(metrics.sharpe)
    # cfg_a bets (low floor); cfg_b abstains (floor above confidence).
    assert results[0][1].bets == 1
    assert results[1][1].bets == 0


def test_cli_precompute_then_sweep(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    # End-to-end CLI: write a resolvable market cassette into a cache dir, run
    # `precompute` (real load_all_cached_markets + real DEFAULT_CORPUS_DIR
    # resolver + real RealSignalSource), then `sweep` the written rows. The
    # Sinner-vs-Shelton slug resolves against the real re-vendored corpus.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    snap = _resolvable_snap()
    (cache_dir / f"{snap.market_id}.json").write_text(
        snap.model_dump_json(), encoding="utf-8"
    )
    rows_path = tmp_path / "rows.json"

    rc = main([
        "precompute",
        "--cache-dir", str(cache_dir),
        "--out", str(rows_path),
        "--entry-fraction", "0.5",
    ])
    assert rc == 0
    assert rows_path.exists()
    out = capsys.readouterr().out
    assert "wrote" in out and "rows" in out
    rows = load_rows(rows_path)
    assert len(rows) == 1

    rc2 = main(["sweep", "--rows", str(rows_path), "--n", "4", "--seed", "0"])
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "OPTIMAL" in out2


def test_rank_configs_min_bets_filters_low_sample_then_sorts() -> None:
    from agent.backtest.cached_sweep import rank_configs
    from agent.backtest.find_optimal_config import generate_lhs_strategy_configs

    cfgs = generate_lhs_strategy_configs(4, seed=0)

    def m(bets: int, sharpe: float) -> SweepMetrics:
        return SweepMetrics(bets=bets, net_pnl=0.0, win_rate=0.0, sharpe=sharpe, avg_size=0.0)

    # high-sharpe but tiny sample (3 bets) must be excluded by min_bets=50;
    # among the >=50 eligible, sort by sharpe desc.
    scored = [
        (cfgs[0], m(3, 9.9)),    # tiny sample, top sharpe -> filtered out
        (cfgs[1], m(120, 0.40)),
        (cfgs[2], m(80, 0.55)),
        (cfgs[3], m(49, 5.0)),   # just under the gate -> filtered out
    ]
    ranked = rank_configs(scored, min_bets=50)
    assert [kv[1].bets for kv in ranked] == [80, 120]  # eligible only, sharpe desc


def test_rank_configs_falls_back_when_filter_empties() -> None:
    from agent.backtest.cached_sweep import rank_configs
    from agent.backtest.find_optimal_config import generate_lhs_strategy_configs

    cfgs = generate_lhs_strategy_configs(2, seed=0)

    def m(bets: int, sharpe: float) -> SweepMetrics:
        return SweepMetrics(bets=bets, net_pnl=0.0, win_rate=0.0, sharpe=sharpe, avg_size=0.0)

    scored = [(cfgs[0], m(2, 0.1)), (cfgs[1], m(5, 0.9))]
    # no config clears min_bets=50 -> fall back to the full pool, sorted by sharpe
    ranked = rank_configs(scored, min_bets=50)
    assert [kv[1].sharpe for kv in ranked] == [0.9, 0.1]


def test_rank_configs_default_no_filter() -> None:
    from agent.backtest.cached_sweep import rank_configs
    from agent.backtest.find_optimal_config import generate_lhs_strategy_configs

    cfgs = generate_lhs_strategy_configs(2, seed=0)

    def m(bets: int, sharpe: float) -> SweepMetrics:
        return SweepMetrics(bets=bets, net_pnl=0.0, win_rate=0.0, sharpe=sharpe, avg_size=0.0)

    scored = [(cfgs[0], m(1, 0.2)), (cfgs[1], m(0, 0.8))]
    ranked = rank_configs(scored)  # default min_bets=0 keeps all
    assert [kv[1].sharpe for kv in ranked] == [0.8, 0.2]
