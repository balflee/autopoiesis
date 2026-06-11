"""``run_sweep`` helper + ``--real`` factory tests (Task D2).

``find_optimal_config.main`` returns an ``int`` exit code (not metrics), so
the Sharpe assertions live on the extracted pure helper
:func:`agent.backtest.find_optimal_config.run_sweep` instead. ``main`` calls
``run_sweep`` and only formats the result.

These tests run a SMALL sweep (2 configs) over a tmp cache of 3 resolvable
tennis cassettes copied out of the real ``_cache_tennis`` corpus, exercising
the real-signal factory path end-to-end:

* ``run_sweep`` returns one ``(StrategyConfig, ReplayMetrics)`` pair per input
  config, each with a finite ``sharpe``.
* The ``signal_source_factory`` is honoured — when it builds a
  :class:`agent.backtest.real_signal_source.RealSignalSource`, the sweep runs
  the REAL signals (momentum + Sackmann facets) rather than the synthetic
  default.

This is NOT the full 96-config sweep (Task D3) — just enough to pin the seam.
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from agent.backtest.find_optimal_config import StrategyConfig, run_sweep
from agent.backtest.historical_fetcher import (
    MarketSnapshotProvider,
    load_all_cached_markets,
)
from agent.backtest.real_signal_source import RealSignalSource
from agent.backtest.replay_runner import ReplayMetrics
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.core.state import Weights
from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

_REAL_CACHE = Path("agent/backtest/_cache_tennis")
_N_CASSETTES = 3


def _real_signal_factory() -> Callable[[MarketSnapshotProvider], RealSignalSource]:
    """Build the same provider->RealSignalSource factory the ``--real`` flag uses."""
    loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
    resolver = TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2026))

    def _factory(provider: MarketSnapshotProvider) -> RealSignalSource:
        return RealSignalSource(provider=provider, resolver=resolver, loader=loader)

    return _factory


def _tmp_cache_of_resolvable_cassettes(tmp_path: Path) -> Path:
    """Copy ``_N_CASSETTES`` resolvable cassettes into a fresh tmp cache dir.

    Resolvable = the slug maps to two Sackmann players, so the RealSignalSource
    exercises the facet path (not just neutral fallbacks).
    """
    if not _REAL_CACHE.is_dir():
        pytest.skip(f"real tennis cache not present at {_REAL_CACHE}")
    loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
    resolver = TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2026))
    snaps = load_all_cached_markets(cache_dir=_REAL_CACHE)
    picked: list[str] = []
    for snap in snaps:
        if resolver.resolve(getattr(snap, "slug", "")) is not None:
            picked.append(snap.market_id)
            if len(picked) >= _N_CASSETTES:
                break
    if len(picked) < _N_CASSETTES:
        pytest.skip("not enough resolvable cassettes in the real cache")

    cache_dir = tmp_path / "cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for market_id in picked:
        src = _REAL_CACHE / f"{market_id}.json"
        if not src.exists():
            # Fall back to scanning by payload market_id if the filename differs.
            src = next(
                (
                    p
                    for p in _REAL_CACHE.glob("*.json")
                    if json.loads(p.read_text(encoding="utf-8")).get("market_id")
                    == market_id
                ),
                src,
            )
        shutil.copy2(src, cache_dir / src.name)
    return cache_dir


def _two_configs() -> list[StrategyConfig]:
    return [
        StrategyConfig(
            weights=Weights(
                w_r=0.5,
                w_s=0.5,
                alpha=[0.34, 0.33, 0.33],
                beta=[0.5, 0.5],
                rho=0.5,
            ),
            max_breath_risk_pct=0.30,
            min_confidence=0.05,
            min_bet_size_usd=5.0,
        ),
        StrategyConfig(
            weights=Weights(
                w_r=0.7,
                w_s=0.3,
                alpha=[0.5, 0.3, 0.2],
                beta=[0.6, 0.4],
                rho=0.2,
            ),
            max_breath_risk_pct=0.20,
            min_confidence=0.0,
            min_bet_size_usd=2.0,
        ),
    ]


def test_run_sweep_real_factory_scores_every_config_with_finite_sharpe(
    tmp_path: Path,
) -> None:
    """``run_sweep`` with the real factory → N scored configs, finite Sharpe each."""
    cache_dir = _tmp_cache_of_resolvable_cassettes(tmp_path)
    configs = _two_configs()

    scored = run_sweep(
        configs,
        cache_dir=cache_dir,
        signal_source_factory=_real_signal_factory(),
        seed=0,
        max_ticks=60,
    )

    assert len(scored) == len(configs)
    for cfg, metrics in scored:
        assert isinstance(cfg, StrategyConfig)
        assert isinstance(metrics, ReplayMetrics)
        assert math.isfinite(metrics.sharpe), (
            f"sharpe must be finite, got {metrics.sharpe!r} for {metrics.config_id}"
        )
    # Pairing order is preserved: the i-th result pairs the i-th input config.
    assert [cfg for cfg, _ in scored] == configs


def test_run_sweep_default_factory_runs_synthetic(tmp_path: Path) -> None:
    """``signal_source_factory=None`` → synthetic source; still finite Sharpe."""
    cache_dir = _tmp_cache_of_resolvable_cassettes(tmp_path)
    configs = _two_configs()[:1]

    scored = run_sweep(
        configs,
        cache_dir=cache_dir,
        signal_source_factory=None,
        seed=0,
        max_ticks=60,
    )

    assert len(scored) == 1
    _, metrics = scored[0]
    assert math.isfinite(metrics.sharpe)
