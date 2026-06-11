"""Find the Sharpe-optimal strategy config via a backtest sweep.

A strategy config spans the TWO parameter families the agent uses to bet:

* ① fusion weights (:class:`~agent.core.state.Weights`: ``w_r`` with
  ``w_s = 1 - w_r``; the ``alpha`` 2-simplex; the ``beta`` split; ``rho``)
* ② bet sizing / abstention (``max_breath_risk_pct``, ``min_confidence``,
  ``min_bet_size_usd``)

(Family ③ — the BREATH survival economy — is NOT swept here: the backtest
replay does not model burn/tax/thresholds, and ③ is calibrated separately in
``sim/`` and baked into the deployed contracts.)

A Latin-hypercube sample over the 8 free dimensions is replayed through
:func:`agent.backtest.replay_runner.run_replay` against the cached tennis
markets, and the per-config :class:`~agent.backtest.replay_runner.ReplayMetrics`
are ranked by Sharpe.

Run::

    python -m agent.backtest.find_optimal_config \
        --cache-dir agent/backtest/_cache_tennis --n 96 --seed 0
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from agent.core.state import Weights

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent.backtest.historical_fetcher import MarketSnapshotProvider
    from agent.backtest.replay_runner import ReplayMetrics, _SignalSource

    SignalSourceFactory = Callable[[MarketSnapshotProvider], _SignalSource]

# Family ② search bounds. Defaults (0.30 / 0.05 / 5.0) sit inside each range so
# the canonical operating point is reachable by the sweep.
MAX_BREATH_RISK_BOUNDS = (0.05, 0.50)  # fraction of BREATH at risk per bet
MIN_CONFIDENCE_BOUNDS = (0.0, 0.30)  # abstain below this fused confidence
MIN_BET_SIZE_BOUNDS = (1.0, 10.0)  # USD floor per bet


@dataclass(frozen=True)
class StrategyConfig:
    """One point in the joint ①+② strategy space."""

    weights: Weights
    max_breath_risk_pct: float
    min_confidence: float
    min_bet_size_usd: float


def _scale(unit: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    return lo + unit * (hi - lo)


def generate_lhs_strategy_configs(n: int, *, seed: int) -> list[StrategyConfig]:
    """``n`` Latin-hypercube :class:`StrategyConfig`, deterministic in ``seed``.

    8 free dims: ``w_r`` (0); the ``alpha`` 2-simplex as two sorted breakpoints
    (1-2); the ``beta`` split (3); ``rho`` on [0,1] (4); and the three family-②
    knobs scaled to their bounds (5-7). Each column is an independent seeded
    permutation of the stratum centres ``(i+0.5)/n`` — the Latin-hypercube
    property — so every 1-D projection is evenly covered.
    """
    if n <= 0:
        raise ValueError(f"n must be > 0 (got {n})")
    dims = 8
    rng = np.random.default_rng(seed)
    centres = (np.arange(n, dtype=np.float64) + 0.5) / n
    cube = np.empty((n, dims), dtype=np.float64)
    for j in range(dims):
        cube[:, j] = rng.permutation(centres)

    configs: list[StrategyConfig] = []
    for row in cube:
        w_r = float(row[0])
        u1, u2 = sorted((float(row[1]), float(row[2])))
        alpha = [u1, u2 - u1, 1.0 - u2]
        b = float(row[3])
        rho = float(row[4])
        configs.append(
            StrategyConfig(
                weights=Weights(
                    w_r=w_r, w_s=1.0 - w_r, alpha=alpha, beta=[b, 1.0 - b], rho=rho
                ),
                max_breath_risk_pct=_scale(float(row[5]), MAX_BREATH_RISK_BOUNDS),
                min_confidence=_scale(float(row[6]), MIN_CONFIDENCE_BOUNDS),
                min_bet_size_usd=_scale(float(row[7]), MIN_BET_SIZE_BOUNDS),
            )
        )
    return configs


def run_sweep(
    configs: list[StrategyConfig],
    *,
    cache_dir: Path,
    signal_source_factory: SignalSourceFactory | None = None,
    seed: int = 0,
    max_ticks: int = 240,
    progress: bool = False,
) -> list[tuple[StrategyConfig, ReplayMetrics]]:
    """Replay every ``config`` against ``cache_dir``; return scored pairs.

    Pure helper extracted from :func:`main` so callers (and tests) can assert
    on the ranked metrics directly — ``main`` returns only an ``int`` exit
    code and prints, so it cannot expose the per-config Sharpe.

    ``signal_source_factory`` is threaded straight into
    :func:`~agent.backtest.replay_runner.run_replay_sync`: ``None`` (default)
    keeps the synthetic :class:`_DeterministicSignalSource`; passing the real
    ``provider -> RealSignalSource`` factory (the ``--real`` flag) runs the
    REAL momentum + Sackmann-facet signals.

    The returned list pairs each input config with its
    :class:`~agent.backtest.replay_runner.ReplayMetrics` in INPUT ORDER;
    :func:`main` sorts by Sharpe for display.
    """
    from agent.backtest.replay_runner import ReplayConfig, run_replay_sync

    scored: list[tuple[StrategyConfig, ReplayMetrics]] = []
    total = len(configs)
    for i, cfg in enumerate(configs):
        metrics = run_replay_sync(
            ReplayConfig(
                starting_weights=cfg.weights,
                seed=seed,
                cache_dir=cache_dir,
                max_ticks=max_ticks,
                max_breath_risk_pct=cfg.max_breath_risk_pct,
                min_confidence=cfg.min_confidence,
                min_bet_size_usd=cfg.min_bet_size_usd,
                config_id=f"cfg_{i:03d}",
            ),
            signal_source_factory=signal_source_factory,
        )
        scored.append((cfg, metrics))
        if progress:
            print(
                f"  [{i + 1:>3}/{total}] sharpe={metrics.sharpe:>7.3f} "
                f"pnl={metrics.net_pnl_usd:>8.2f} bets={metrics.bets_placed}",
                flush=True,
            )
    return scored


def _make_real_signal_source_factory() -> SignalSourceFactory:
    """Build the ``provider -> RealSignalSource`` factory for the ``--real`` flag.

    (A0 correction) The loader reads the full re-vendored corpus dir
    (``DEFAULT_CORPUS_DIR``) so resolution is offline + ~65.8%, NOT a bare
    ``SackmannLoader()`` (synthetic test-fixture snapshot). The resolver is
    built ONCE and shared across every per-config replay so the corpus is not
    re-parsed per config.
    """
    from agent.backtest.real_signal_source import RealSignalSource
    from agent.backtest.tennis_match_resolver import TennisMatchResolver
    from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

    loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
    resolver = TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2026))

    def _factory(provider: MarketSnapshotProvider) -> RealSignalSource:
        return RealSignalSource(provider=provider, resolver=resolver, loader=loader)

    return _factory


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent.backtest.find_optimal_config",
        description="Sharpe-rank an LHS sweep of joint ①+② strategy configs.",
    )
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--n", type=int, default=96, help="LHS sample size.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-ticks", type=int, default=240)
    parser.add_argument("--top", type=int, default=10, help="Rows to print.")
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use the REAL signal source (momentum + Sackmann facets) instead "
        "of the synthetic deterministic default.",
    )
    args = parser.parse_args(argv)

    configs = generate_lhs_strategy_configs(args.n, seed=args.seed)
    signal_source_factory = (
        _make_real_signal_source_factory() if args.real else None
    )
    source_label = "REAL signals" if args.real else "synthetic signals"
    print(
        f"running {args.n} configs against {args.cache_dir} ({source_label}) ...",
        flush=True,
    )

    scored = run_sweep(
        configs,
        cache_dir=args.cache_dir,
        signal_source_factory=signal_source_factory,
        seed=args.seed,
        max_ticks=args.max_ticks,
        progress=True,
    )

    ranked = sorted(scored, key=lambda kv: kv[1].sharpe, reverse=True)

    print(f"\n=== Sharpe-ranked strategy sweep (n={args.n}) ===")
    print(f"{'rank':>4} {'sharpe':>8} {'net_pnl':>9} {'win%':>6} {'bets':>5}  config")
    for rank, (cfg, m) in enumerate(ranked[: args.top], start=1):
        w = cfg.weights
        print(
            f"{rank:>4} {m.sharpe:>8.3f} {m.net_pnl_usd:>9.2f} "
            f"{m.win_rate_pct:>6.1f} {m.bets_placed:>5}  "
            f"w_r={w.w_r:.2f} a={[round(a, 2) for a in w.alpha]} "
            f"b1={w.beta[0]:.2f} rho={w.rho:.2f} | "
            f"risk={cfg.max_breath_risk_pct:.2f} "
            f"minconf={cfg.min_confidence:.2f} minbet={cfg.min_bet_size_usd:.1f}"
        )

    best_cfg, best_m = ranked[0]
    print("\n=== OPTIMAL (max Sharpe) ===")
    print(f"weights: {best_cfg.weights.model_dump_json()}")
    print(
        f"sizing:  max_breath_risk_pct={best_cfg.max_breath_risk_pct:.4f} "
        f"min_confidence={best_cfg.min_confidence:.4f} "
        f"min_bet_size_usd={best_cfg.min_bet_size_usd:.4f}"
    )
    print(
        f"sharpe={best_m.sharpe:.3f} net_pnl=${best_m.net_pnl_usd:.2f} "
        f"win_rate={best_m.win_rate_pct:.1f}% bets={best_m.bets_placed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
