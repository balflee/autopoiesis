# tests/agent/backtest/test_validate_value_seed.py
"""Task 6a — the SEED PRODUCER + the opt-in walk-forward OOS selection.

``validate_value_seed`` is the REAL v3/v4 seed producer. Task 6a:

* ``_seed_payload`` carries ``kappa_xm`` so the committed v4 seed round-trips
  the new genome scalar;
* the CLI default ``--out`` is ``value_seed_v4.json`` and a new opt-in
  ``--walk-forward`` flag (DEFAULT OFF) toggles between the v3 in-sample
  select+validate path (byte-identical to the pre-flag behavior) and the
  post-floor :class:`SurvivalRow` walk-forward OOS path;
* the select+validate body is extracted into a reusable
  :func:`select_winner` seam shared by ``main`` and the future three-arm
  journey driver.

These tests stay OFFLINE: a tiny disk universe + an EMPTY slug resolver (no
Sackmann corpus parse). They do NOT run the heavy 4925-row sweep — only prove
the WIRING + byte-identity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from agent.backtest.cached_sweep import (
    SignalRow,
    load_rows,
    rank_configs_by_pnl,
    run_cached_sweep,
    save_rows,
)
from agent.backtest.find_optimal_config import (
    StrategyConfig,
    generate_lhs_strategy_configs,
)
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    PricePoint,
    load_all_cached_markets,
    save_cached_market,
)
from agent.backtest.reincarnation import split_rows_by_time
from agent.backtest.survival_season import (
    run_survival_export,
    run_survival_over_rows,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.backtest.validate_value_seed import (
    _JOURNEY_KNOBS,
    _SEED_OUT_V4,
    _build_parser,
    _clamp_min_bet,
    _seed_payload,
    select_winner,
)
from agent.core.state import Weights

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "smart_money",
    "sentiment_llm",
    "crowd_volume",
)


def _empty_resolver() -> TennisMatchResolver:
    return TennisMatchResolver(name_index={})


def _seed_cfg(*, kappa_xm: float = 0.0) -> StrategyConfig:
    return StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=1.0
        ),
        max_breath_risk_pct=0.4,
        min_confidence=0.05,
        min_bet_size_usd=4.0,
        min_edge=0.02,
        kappa=0.3,
        kappa_xm=kappa_xm,
    )


def _snap(
    market_id: str,
    *,
    entry_ts: str,
    end_date: str,
    resolution: str,
    entry_price: float,
    outcome: Literal["yes", "no"],
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        slug=f"atp-{market_id}-alpha-vs-bravo",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _row_for(snap: MarketSnapshot) -> SignalRow:
    return SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: 0.8 for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=snap.price_ledger[0].mid_price,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
    )


def _write_universe(tmp_path: Path, *, n_markets: int = 6) -> tuple[Path, Path]:
    """A small offline universe with DISTINCT entry timestamps (so the
    chronological walk-forward split is well-defined) + mixed outcomes/prices
    (so seasons differ across configs)."""
    snaps: list[MarketSnapshot] = []
    for i in range(n_markets):
        day = i + 1
        snaps.append(
            _snap(
                f"m{i}",
                entry_ts=f"2025-06-{day:02d}T00:00:00+00:00",
                end_date=f"2025-06-{day:02d}T12:00:00+00:00",
                resolution=f"2025-06-{day:02d}T20:00:00+00:00",
                entry_price=0.40 if i % 2 == 0 else 0.60,
                outcome="no" if i % 2 == 0 else "yes",
            )
        )
    cache_dir = tmp_path / "_cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for s in snaps:
        save_cached_market(snapshot=s, cache_dir=cache_dir)
    rows_path = tmp_path / "_signal_rows.json"
    save_rows([_row_for(s) for s in snaps], rows_path)
    return rows_path, cache_dir


# --------------------------------------------------------------------------- #
# _seed_payload — kappa_xm flows into the seed json.
# --------------------------------------------------------------------------- #


def test_seed_payload_includes_kappa_xm() -> None:
    cfg = _seed_cfg(kappa_xm=0.27)
    payload = _seed_payload(cfg)
    assert payload["kappa_xm"] == pytest.approx(0.27)
    # The pre-existing v3 keys must all survive.
    assert set(payload) >= {
        "weights",
        "max_breath_risk_pct",
        "min_confidence",
        "min_bet_size_usd",
        "min_edge",
        "kappa",
        "kappa_xm",
    }


def test_seed_payload_kappa_xm_defaults_zero_on_v3_config() -> None:
    cfg = _seed_cfg()  # kappa_xm defaults 0.0 -> v3-shaped config still works
    assert _seed_payload(cfg)["kappa_xm"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# CLI defaults — value_seed_v4.json out + walk-forward OFF by default.
# --------------------------------------------------------------------------- #


def test_default_out_is_value_seed_v4() -> None:
    args = _build_parser().parse_args([])
    assert args.out == _SEED_OUT_V4
    assert _SEED_OUT_V4 == Path("docs/backtest/value_seed_v4.json")


def test_walk_forward_defaults_off() -> None:
    args = _build_parser().parse_args([])
    assert args.walk_forward is False
    args_on = _build_parser().parse_args(["--walk-forward"])
    assert args_on.walk_forward is True


# --------------------------------------------------------------------------- #
# select_winner — reusable seam, importable + callable by an external caller.
# --------------------------------------------------------------------------- #


def test_select_winner_is_importable_and_callable() -> None:
    # Importable at module scope (an external driver can ``from
    # ...validate_value_seed import select_winner``) and is a real callable.
    assert callable(select_winner)


# --------------------------------------------------------------------------- #
# --walk-forward OFF == the pre-flag v3 in-sample path (byte-identity).
#
# GOLDEN = the exact pre-change algorithm (LHS -> fast sweep -> rank -> top-K
# validated via run_survival_export -> rank by (finished-alive, season pnl)).
# select_winner(walk_forward=False) must select the SAME winner / seed payload.
# --------------------------------------------------------------------------- #


def test_walk_forward_off_matches_v3_in_sample(tmp_path: Path) -> None:
    rows_path, cache_dir = _write_universe(tmp_path, n_markets=6)
    rows = load_rows(rows_path)
    snapshots = load_all_cached_markets(cache_dir=cache_dir)
    resolver = _empty_resolver()

    n, min_bets, top, seed = 8, 0, 2, 0

    # GOLDEN: replicate the pre-flag v3 in-sample selection + validation.
    configs = [
        _clamp_min_bet(c) for c in generate_lhs_strategy_configs(n, seed=seed)
    ]
    scored = run_cached_sweep(
        rows,
        configs,
        entry_price_floor=0.05,
        effective_entry_price_floor=0.05,
        max_pnl_usd=100.0,
        side_correct_pricing=True,
        value_betting=True,
    )
    ranked = rank_configs_by_pnl(scored, min_bets=min_bets)
    candidates = ranked[:top]
    results: list[dict[str, Any]] = []
    for i, (cfg, _fast_m) in enumerate(candidates):
        journey = run_survival_export(
            rows_path=rows_path,
            cache_dir=cache_dir,
            out_path=tmp_path / f"golden_{i}.json",
            base_seed=cfg,
            resolver=resolver,
            **_JOURNEY_KNOBS,
        )
        s = journey["summary"]
        results.append(
            {
                "cfg": cfg,
                "season_pnl": s["learner_final_pnl"],
                "finished_alive": s["deaths"] < s["lives"],
            }
        )
    results.sort(key=lambda r: (r["finished_alive"], r["season_pnl"]), reverse=True)
    golden_cfg = results[0]["cfg"]

    # ACTUAL via the reusable seam (walk_forward OFF).
    win_cfg, summary = select_winner(
        rows,
        snapshots,
        seed,
        walk_forward=False,
        resolver=resolver,
        n=n,
        min_bets=min_bets,
        top=top,
        verbose=False,
    )

    assert _seed_payload(win_cfg) == _seed_payload(golden_cfg)
    for k in ("learner_final_pnl", "deaths", "lives", "learning_vs_static_delta"):
        assert k in summary


# --------------------------------------------------------------------------- #
# --walk-forward ON — train-select / test-eval wiring against real signatures.
# --------------------------------------------------------------------------- #


def test_walk_forward_on_splits_train_select_test_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows_path, cache_dir = _write_universe(tmp_path, n_markets=6)
    rows = load_rows(rows_path)
    snapshots = load_all_cached_markets(cache_dir=cache_dir)
    resolver = _empty_resolver()

    import agent.backtest.validate_value_seed as vvs

    calls: dict[str, Any] = {"eval_rows_n": []}

    # The ``real_*`` originals are the canonical functions ``select_winner``
    # binds (same objects); monkeypatch swaps the names IN ``vvs`` for spies.

    def spy_split(
        survival_rows: list[Any], *, train_fraction: float = 0.7
    ) -> tuple[list[Any], list[Any]]:
        tr, te = split_rows_by_time(survival_rows, train_fraction=train_fraction)
        calls["all_n"] = len(survival_rows)
        calls["train_n"] = len(tr)
        calls["test_n"] = len(te)
        return tr, te

    def spy_sweep(sweep_rows: list[Any], cfgs: list[Any], **kw: Any) -> Any:
        calls["sweep_rows_n"] = len(sweep_rows)
        # The TRAIN sweep must run on SignalRows (mapped back from .signal).
        calls["sweep_row_types"] = {type(r).__name__ for r in sweep_rows}
        return run_cached_sweep(sweep_rows, cfgs, **kw)

    def spy_run(eval_rows: list[Any], snaps: list[Any], **kw: Any) -> Any:
        calls["eval_rows_n"].append(len(eval_rows))
        calls["eval_row_types"] = {type(r).__name__ for r in eval_rows}
        return run_survival_over_rows(eval_rows, snaps, **kw)

    monkeypatch.setattr(vvs, "split_rows_by_time", spy_split)
    monkeypatch.setattr(vvs, "run_cached_sweep", spy_sweep)
    monkeypatch.setattr(vvs, "run_survival_over_rows", spy_run)

    cfg, summary = select_winner(
        rows,
        snapshots,
        0,
        walk_forward=True,
        resolver=resolver,
        n=8,
        min_bets=0,
        top=2,
        verbose=False,
    )

    assert isinstance(cfg, StrategyConfig)
    for k in ("learner_final_pnl", "deaths", "lives", "learning_vs_static_delta"):
        assert k in summary

    # A single chronological split partitioned the post-floor SurvivalRows.
    assert calls["train_n"] + calls["test_n"] == calls["all_n"]
    assert calls["train_n"] >= 1 and calls["test_n"] >= 1
    # Selection ran the fast sweep on the TRAIN SignalRows (mapped from .signal).
    assert calls["sweep_rows_n"] == calls["train_n"]
    assert calls["sweep_row_types"] == {"SignalRow"}
    # Each candidate was evaluated on the TEST SurvivalRows via the run-half.
    assert calls["eval_rows_n"]
    assert all(n == calls["test_n"] for n in calls["eval_rows_n"])
    assert calls["eval_row_types"] == {"SurvivalRow"}
