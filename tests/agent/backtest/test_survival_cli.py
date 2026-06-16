# tests/agent/backtest/test_survival_cli.py
"""A4 — CLI + export to ``dashboard/public/backtest/survival_journey.json``.

A ``__main__`` CLI:

    python -m agent.backtest.survival_season run \
        --rows reports/backtest/_signal_rows.json \
        --cache-dir agent/backtest/_cache_tennis \
        --out dashboard/public/backtest/survival_journey.json \
        [--fragile-seed ... --initial-breath ... --max-lives ...]

UTF-8 safe, ``main() -> int``. The journey steps are DOWN-SAMPLED (<= the
``--max-steps`` budget) for the chart while the summary + per-life spans are
kept whole.

TDD over a TINY 2-3 market fixture cache — the test exercises the ``run_sweep``-
style helper (:func:`run_survival_export`), NOT the full 4925-row season. A tiny
``_cache_tennis``-shaped dir + a matching cached ``_signal_rows.json`` are
written to ``tmp_path`` and an EMPTY resolver is injected (no Sackmann corpus
parse) so the test is fast + offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest

from agent.backtest.cached_sweep import SignalRow, save_rows
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    PricePoint,
    save_cached_market,
)
from agent.backtest.survival_season import (
    DEFAULT_AI_OUT_PATH,
    DEFAULT_OUT_PATH,
    main,
    run_survival_export,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.core.state import Weights

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "surface_advantage",
    "head_to_head",
    "rest_recency",
)


def _empty_resolver() -> TennisMatchResolver:
    # No Sackmann corpus parse — the tiny fixture slugs never resolve to player
    # ids, so players/surface fall back to None (the documented UI fallback).
    return TennisMatchResolver(name_index={})


def _bullish_base_seed() -> StrategyConfig:
    # A bullish base (beta favours YES) so the fragile derivation BETS YES on the
    # all-"no" fixture markets -> full-stake losses -> the agent dies. Mirrors the
    # proven A2/A3 dying fixtures. The real CLI default base is the static optimum
    # (DEFAULT_OPTIMUM_SEED), whose death drama A5 calibrates; this just exercises
    # the run_sweep-style export end-to-end on a tiny universe.
    return StrategyConfig(
        weights=Weights(
            w_r=0.5, w_s=0.5, alpha=[0.34, 0.33, 0.33], beta=[1.0, 0.0], rho=0.6
        ),
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
        slug=f"atp-{market_id}-alpha-vs-bravo",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _row_for(snap: MarketSnapshot, *, score: float = 0.8) -> SignalRow:
    return SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: score for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=snap.price_ledger[0].mid_price,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
    )


def _write_tiny_universe(tmp_path: Path) -> tuple[Path, Path]:
    """Write a tiny ``_cache_tennis`` dir + a matching cached rows JSON.

    The fixture is engineered to DIE: 3 all-"no" markets bet bullishly (YES) →
    full-stake losses → breath drains. Mirrors the A2/A3 dying fixtures.
    """
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
    cache_dir = tmp_path / "_cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for s in snaps:
        save_cached_market(snapshot=s, cache_dir=cache_dir)

    rows_path = tmp_path / "_signal_rows.json"
    save_rows([_row_for(s) for s in snaps], rows_path)
    return rows_path, cache_dir


# --------------------------------------------------------------------------- #
# run_survival_export — the run_sweep-style helper (no full season).
# --------------------------------------------------------------------------- #


def test_run_survival_export_writes_downsampled_journey(tmp_path: Path) -> None:
    rows_path, cache_dir = _write_tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey.json"

    summary = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_bullish_base_seed(),
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
        max_steps=2,  # force down-sampling of the chart series
        resolver=_empty_resolver(),
    )

    # The helper returns the in-memory journey dict it wrote (run_sweep style).
    assert isinstance(summary, dict)

    # The file was written + is valid UTF-8 JSON.
    assert out_path.exists()
    journey = json.loads(out_path.read_text(encoding="utf-8"))

    # Page-2 data-contract top-level keys.
    assert set(journey) >= {"seed", "lives", "steps", "baselines", "summary"}

    # Steps are DOWN-SAMPLED to <= the budget; the full count is kept in summary.
    assert len(journey["steps"]) <= 2
    assert journey["summary"]["total_steps"] >= len(journey["steps"])

    # Per-life spans are kept whole (one entry per life, not down-sampled).
    assert journey["lives"]
    assert len(journey["lives"]) == journey["summary"]["lives"]

    # The dying fixture produced at least one death + a learning-vs-static delta.
    assert journey["summary"]["deaths"] >= 1
    assert "learning_vs_static_delta" in journey["summary"]

    # The seed is the (fragile) calibrated config.
    assert "weights" in journey["seed"]
    assert journey["seed"]["max_breath_risk_pct"] == 1.0

    # baselines carry the frozen static + archetype cum-PnL series.
    assert set(journey["baselines"]) >= {"static", "random", "always_favorite"}


def test_run_survival_export_is_deterministic(tmp_path: Path) -> None:
    rows_path, cache_dir = _write_tiny_universe(tmp_path)
    out1 = tmp_path / "a" / "j.json"
    out2 = tmp_path / "b" / "j.json"

    j1 = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out1,
        base_seed=_bullish_base_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
    )
    j2 = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out2,
        base_seed=_bullish_base_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
    )
    # Same inputs -> byte-identical journey (the export is reproducible).
    assert out1.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")
    assert j1 == j2


# --------------------------------------------------------------------------- #
# main() — argparse wiring + exit code.
# --------------------------------------------------------------------------- #


def test_main_run_subcommand_returns_zero_and_writes(tmp_path: Path) -> None:
    rows_path, cache_dir = _write_tiny_universe(tmp_path)
    out_path = tmp_path / "dash" / "survival_journey.json"

    rc = main(
        [
            "run",
            "--rows",
            str(rows_path),
            "--cache-dir",
            str(cache_dir),
            "--out",
            str(out_path),
            "--initial-breath",
            "3.0",
            "--max-lives",
            "5",
            "--max-steps",
            "2",
            "--no-resolver",  # skip the Sackmann corpus parse in the test
        ]
    )
    assert rc == 0
    assert out_path.exists()
    journey = json.loads(out_path.read_text(encoding="utf-8"))
    # main() uses the CLI-default (static-optimum) base seed, whose death drama
    # A5 calibrates — here we only assert the export ran cleanly + is well-formed
    # + down-sampled (the deliberately-dying path is covered above).
    assert set(journey) >= {"seed", "lives", "steps", "baselines", "summary"}
    assert journey["summary"]["deaths"] >= 0
    assert len(journey["steps"]) <= 2
    assert journey["summary"]["total_steps"] >= len(journey["steps"])


# --------------------------------------------------------------------------- #
# L3 — ``--with-ai`` CLI plumbing (out-path routing + with_ai forwarding).
#
# Kept OFFLINE: ``run_survival_export`` is monkeypatched to capture the kwargs
# the CLI passes, so NO season runs, NO real GeminiClient is built, NO probe
# fires, and nothing is written under dashboard/public/backtest/.
# --------------------------------------------------------------------------- #


def test_main_with_ai_routes_to_default_ai_out_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--with-ai`` (no ``--out``) → ``DEFAULT_AI_OUT_PATH`` + ``with_ai=True``.

    The CLI must route the omitted ``--out`` to the SEPARATE AI artifact when
    ``--with-ai`` is set, and plumb ``with_ai=True`` through to the export.
    """
    rows_path, cache_dir = _write_tiny_universe(tmp_path)

    captured: dict[str, Any] = {}
    import agent.backtest.survival_season as ss

    def _fake_export(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "summary": {
                "lives": 1,
                "deaths": 0,
                "total_steps": 0,
                "learning_vs_static_delta": 0.0,
            },
            "steps": [],
        }

    monkeypatch.setattr(ss, "run_survival_export", _fake_export)

    rc = main(
        [
            "run",
            "--rows",
            str(rows_path),
            "--cache-dir",
            str(cache_dir),
            "--with-ai",
            "--no-resolver",
        ]
    )
    assert rc == 0
    # Omitted --out + --with-ai → the AI artifact path, and with_ai plumbed True.
    assert captured["out_path"] == DEFAULT_AI_OUT_PATH
    assert captured["with_ai"] is True


def test_main_without_ai_routes_to_default_numerical_out_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``--with-ai`` + no ``--out`` → the NUMERICAL ``DEFAULT_OUT_PATH``."""
    rows_path, cache_dir = _write_tiny_universe(tmp_path)

    captured: dict[str, Any] = {}
    import agent.backtest.survival_season as ss

    def _fake_export(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "summary": {
                "lives": 1,
                "deaths": 0,
                "total_steps": 0,
                "learning_vs_static_delta": 0.0,
            },
            "steps": [],
        }

    monkeypatch.setattr(ss, "run_survival_export", _fake_export)

    rc = main(
        [
            "run",
            "--rows",
            str(rows_path),
            "--cache-dir",
            str(cache_dir),
            "--no-resolver",
        ]
    )
    assert rc == 0
    assert captured["out_path"] == DEFAULT_OUT_PATH
    assert captured["with_ai"] is False
