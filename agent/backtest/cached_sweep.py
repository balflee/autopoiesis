"""Signal-cached config sweep.

Precompute the REAL 5-slot signals + settlement facts for every resolvable
cassette ONCE, then sweep all configs in-memory over the faithful
``DecisionEngine.decide`` fusion+sizing and the faithful settlement PnL
formula. Full coverage, seconds per config.

This module is built incrementally across the plan's tasks. Task 1 provides
``compute_bet_pnl`` — a byte-faithful mirror of the production settlement
poller's ``_compute_pnl``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import pstdev
from typing import Protocol

from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    MarketSnapshotProvider,
)
from agent.backtest.real_signal_source import RealSignalSource
from agent.backtest.replay_runner import (
    DEFAULT_REPLAY_INITIAL_BANKROLL_USD,
    DEFAULT_REPLAY_INITIAL_BREATH,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.core.state import ActionKind
from agent.engines.base import Signal
from agent.engines.decision import DecisionEngine

# At $100 bankroll the per-bet sizer caps at $5 (NORMAL_BET_SIZE_CAP=0.05), so a
# config whose ``min_bet_size_usd`` >= $5 can never place a bet — every BET is
# rejected by the floor. The LHS sweep samples ``min_bet_size_usd`` on [1, 10],
# so the sweep CLI post-clamps it sub-$5 to this ceiling (see ``main``).
_MIN_BET_SWEEP_CEILING_USD = 4.0


def effective_entry_price(*, side: str, yes_price: float) -> float:
    """The price the bettor actually pays for the leg they took.

    ``yes_price`` is the YES-token mid (the only price the cassette pipeline
    carries). A YES bet costs ``yes_price``; a NO bet costs the complement
    ``1 - yes_price``. Realism rule #3 (2026-06-11): the legacy formula paid
    BOTH sides at the YES price, overpaying winning NO bets on YES-longshots
    by up to 81x (and underpaying NO bets on YES-favorites symmetrically) —
    always-favorite's +$8,451 "profit" was 103.7% this artifact and becomes
    −$661 under this correction.
    """
    return yes_price if side == "YES" else 1.0 - yes_price


def compute_bet_pnl(
    *,
    side: str,
    entry_price: float,
    size_usd: float,
    outcome: str,
    winning_price: float,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
) -> float:
    """Per-bet realised P&L — a faithful mirror of the production formula.

    Mirrors ``agent.runtime.sandbox_settlement_poller._compute_pnl`` exactly:

    * ``outcome == "void"``               -> ``0.0``
    * winner (side matches outcome)       -> ``size_usd * (winning_price / entry_price - 1.0)``
      (degenerate ``entry_price <= 0`` clips to ``size_usd * winning_price``)
    * loser                               -> ``-size_usd``

    "Winner" = ``(side == "YES") == (outcome == "yes")``.

    ``max_pnl_usd`` (optional, default ``None`` = byte-unchanged): clamp a
    WINNER's profit to this ceiling. A crude liquidity-realism approximation —
    the win payout formula is otherwise unbounded at extreme-longshot entry
    prices (a $5 bet at $0.0005 "wins" $9,995 no $5-liquidity market could
    pay). Losses (``-size_usd``) and voids are NEVER clamped: the cap is on
    profit only. Mirrors the same optional cap on the production
    ``_compute_pnl``.

    ``side_correct_pricing`` (optional, default ``False`` = legacy YES-price
    payouts, byte-unchanged): price the taken leg at its effective cost via
    :func:`effective_entry_price` (realism rule #3) — a winning NO bet pays
    NO-leg odds instead of the YES leg's.
    """
    if outcome == "void":
        return 0.0
    side_is_yes = side == "YES"
    outcome_is_yes = outcome == "yes"
    is_winner = side_is_yes == outcome_is_yes
    if not is_winner:
        return -size_usd
    # Winner — symmetric formula. Defensive: a bet entered at price 0 is
    # degenerate; clip to ``size_usd * winning_price`` to avoid ZeroDivision.
    eff = (
        effective_entry_price(side=side, yes_price=entry_price)
        if side_correct_pricing
        else entry_price
    )
    if eff <= 0.0:
        pnl = size_usd * winning_price
    else:
        pnl = size_usd * (winning_price / eff - 1.0)
    if max_pnl_usd is not None and pnl > max_pnl_usd:
        return max_pnl_usd
    return pnl


# --------------------------------------------------------------------------- #
# Task 2: SignalRow + precompute_rows (mid-market entry, real signals)
# --------------------------------------------------------------------------- #


class _SignalSourceLike(Protocol):
    """Structural :class:`RealSignalSource` — only ``signals_for`` is used."""

    def signals_for(
        self, *, market_id: str, tick: int, asof_ts: datetime
    ) -> dict[str, object]: ...


@dataclass
class SignalRow:
    """Precomputed per-market signal + settlement facts for one cassette.

    Signals are config-INDEPENDENT — only fusion weights and sizing knobs vary
    per config — so they are computed ONCE here and reused across the whole
    config sweep. ``scores``/``confidences`` are keyed by the 5 ``decision.py``
    engine-slot constants; ``entry_price`` is the mid-market mid-price (see
    :func:`_entry_asof`); ``outcome``/``winning_price``/``liquidity_cap_usd`` are
    copied verbatim off the settled :class:`MarketSnapshot`.
    """

    market_id: str
    slug: str
    scores: dict[str, float] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    entry_price: float = 0.0
    outcome: str = ""
    winning_price: float = 0.0
    liquidity_cap_usd: float = 0.0


def _entry_asof(
    ledger: list[tuple[datetime, float]], entry_fraction: float
) -> tuple[datetime, float]:
    """Mid-market entry point for a price ledger.

    Computes ``first_ts + entry_fraction * (last_ts - first_ts)`` and returns the
    LAST ``(ts, mid_price)`` at-or-before that target — the point-in-time the
    backtest enters at. ``ledger`` MUST be non-empty and sorted ascending by ts.
    A single-point ledger returns that point.
    """
    if not ledger:
        raise ValueError("_entry_asof requires a non-empty ledger")
    first_ts, _ = ledger[0]
    last_ts, _ = ledger[-1]
    target = first_ts + entry_fraction * (last_ts - first_ts)
    chosen = ledger[0]
    for ts, price in ledger:
        if ts <= target:
            chosen = (ts, price)
        else:
            break
    return chosen


def precompute_rows(
    snapshots: list[MarketSnapshot],
    resolver: TennisMatchResolver,
    src: _SignalSourceLike | RealSignalSource,
    *,
    entry_fraction: float = 0.5,
) -> list[SignalRow]:
    """Build one :class:`SignalRow` per SCORABLE, RESOLVABLE cassette.

    A snapshot is skipped (excluded from the sweep universe, not silently lost —
    callers count ``len(rows)`` vs ``len(snapshots)``) when it cannot score a
    PnL or cannot resolve to two players:

    * ``outcome is None`` or ``winning_price is None`` (no clean resolution), or
    * empty ``price_ledger`` (no entry price), or
    * the slug does not resolve via ``resolver`` (not a tennis ``-vs-`` match).

    For every surviving snapshot the entry asof + price are taken at the
    mid-market point (:func:`_entry_asof`), the REAL 5-slot signals are computed
    at that asof, and a row is emitted carrying the settlement facts.
    """
    rows: list[SignalRow] = []
    for snap in snapshots:
        if snap.outcome is None or snap.winning_price is None:
            continue
        if not snap.price_ledger:
            continue
        if resolver.resolve(snap.slug) is None:
            continue
        ledger = [
            (datetime.fromisoformat(pp.ts), pp.mid_price) for pp in snap.price_ledger
        ]
        asof_ts, entry_price = _entry_asof(ledger, entry_fraction)
        sigs = src.signals_for(market_id=snap.market_id, tick=0, asof_ts=asof_ts)
        rows.append(
            SignalRow(
                market_id=snap.market_id,
                slug=snap.slug,
                scores={k: float(s.score) for k, s in sigs.items()},  # type: ignore[attr-defined]
                confidences={k: float(s.confidence) for k, s in sigs.items()},  # type: ignore[attr-defined]
                entry_price=float(entry_price),
                outcome=snap.outcome,
                winning_price=float(snap.winning_price),
                liquidity_cap_usd=float(snap.liquidity_cap_usd),
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Task 3: row_to_signals + score_config (real decide + faithful PnL)
# --------------------------------------------------------------------------- #


def row_to_signals(row: SignalRow) -> dict[str, Signal]:
    """Reconstruct the 5-slot ``dict[str, Signal]`` the engine fusion consumes.

    Only ``score``+``confidence`` feed ``DecisionEngine.decide``; the other
    :class:`~agent.engines.base.Signal` fields are inert in the fusion math, so
    they are filled with empty placeholders. Keyed by the same ``decision.py``
    engine-slot constants the row was built with.
    """
    return {
        k: Signal(
            score=row.scores[k],
            confidence=row.confidences[k],
            available_at="",
            rationale="",
            raw_features={},
        )
        for k in row.scores
    }


@dataclass
class SweepMetrics:
    """Aggregate metrics for one config over the cached signal universe.

    ``sharpe`` is the per-bet (un-compounded) ``mean(pnl)/pstdev(pnl)`` across a
    config's BETs — 0.0 when fewer than 2 bets or the spread is zero.
    ``t_stat = sharpe * sqrt(bets)`` — the statistical-significance proxy the
    earnings ranking gates on (realism v3): a high-PnL config with a weak
    t-stat is indistinguishable from luck.
    """

    bets: int
    net_pnl: float
    win_rate: float
    sharpe: float
    avg_size: float
    t_stat: float = 0.0


def _aggregate(pnls: list[float], sizes: list[float], wins: int) -> SweepMetrics:
    """Roll per-bet ``pnls``/``sizes``/``wins`` into a :class:`SweepMetrics`."""
    bets = len(pnls)
    if bets == 0:
        return SweepMetrics(bets=0, net_pnl=0.0, win_rate=0.0, sharpe=0.0, avg_size=0.0)
    net_pnl = sum(pnls)
    win_rate = wins / bets
    avg_size = sum(sizes) / bets
    # Per-bet Sharpe (un-compounded): mean/pstdev over the BET P&Ls.
    sharpe = 0.0
    if bets >= 2:
        spread = pstdev(pnls)
        if spread > 0.0:
            sharpe = (net_pnl / bets) / spread
    return SweepMetrics(
        bets=bets,
        net_pnl=net_pnl,
        win_rate=win_rate,
        sharpe=sharpe,
        avg_size=avg_size,
        t_stat=sharpe * math.sqrt(bets),
    )


async def score_config(
    rows: list[SignalRow],
    cfg: StrategyConfig,
    *,
    bankroll: float = DEFAULT_REPLAY_INITIAL_BANKROLL_USD,
    breath: float = DEFAULT_REPLAY_INITIAL_BREATH,
    entry_price_floor: float | None = None,
    effective_entry_price_floor: float | None = None,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
) -> SweepMetrics:
    """Score one ``cfg`` over ``rows`` via the REAL ``DecisionEngine.decide``.

    Each row is decided INDEPENDENTLY at a fixed ``bankroll``/``breath`` (no
    equity compounding, no breath depletion across markets — see the locked
    design decisions). On a BET, the realised per-bet P&L is the faithful
    production formula (:func:`compute_bet_pnl`); NO_BET rows contribute
    nothing. Metrics are aggregated across the config's BETs.

    Realism v3 (all default OFF = the pre-v3 sweep, byte-unchanged):
    ``entry_price_floor`` pre-filters rows by the YES mid (parity with
    ``build_survival_rows``); ``effective_entry_price_floor`` SKIPS any BET
    whose effective side price is below it — even in legacy mode, so
    ``--realism`` alone cannot harvest the mirrored NO-side lottery (r1 M-3);
    ``max_pnl_usd``/``side_correct_pricing`` thread into the payout;
    ``value_betting`` passes ``price=row.entry_price`` into decide().
    """
    engine = DecisionEngine(
        max_breath_risk_pct=cfg.max_breath_risk_pct,
        min_bet_size_usd=cfg.min_bet_size_usd,
        min_confidence=cfg.min_confidence,
        min_edge=cfg.min_edge,
        kappa=cfg.kappa,
        entry_price_floor=(
            effective_entry_price_floor if value_betting else None
        ),
    )
    w = cfg.weights
    alpha = (w.alpha[0], w.alpha[1], w.alpha[2])
    beta = (w.beta[0], w.beta[1])

    if entry_price_floor is not None:
        rows = [r for r in rows if r.entry_price >= entry_price_floor]

    pnls: list[float] = []
    sizes: list[float] = []
    wins = 0
    for row in rows:
        action = await engine.decide(
            signals=row_to_signals(row),
            weights_alpha=alpha,
            weights_beta=beta,
            w_r=w.w_r,
            w_s=w.w_s,
            rho=w.rho,
            bankroll_usd=bankroll,
            breath=breath,
            liquidity_cap_usd=row.liquidity_cap_usd,
            market_id=row.market_id,
            desperate=False,
            **({"price": row.entry_price} if value_betting else {}),
        )
        if action.kind is not ActionKind.BET:
            continue
        assert action.side is not None and action.size_usd is not None
        # Post-decision effective-floor skip (r1 M-3): holds in BOTH modes.
        if effective_entry_price_floor is not None:
            eff = effective_entry_price(
                side=action.side.value, yes_price=row.entry_price
            )
            if eff < effective_entry_price_floor:
                continue
        pnl = compute_bet_pnl(
            side=action.side.value,
            entry_price=row.entry_price,
            size_usd=action.size_usd,
            outcome=row.outcome,
            winning_price=row.winning_price,
            max_pnl_usd=max_pnl_usd,
            side_correct_pricing=side_correct_pricing,
        )
        pnls.append(pnl)
        sizes.append(action.size_usd)
        if pnl > 0.0:
            wins += 1
    return _aggregate(pnls, sizes, wins)


def score_config_sync(
    rows: list[SignalRow],
    cfg: StrategyConfig,
    *,
    bankroll: float = DEFAULT_REPLAY_INITIAL_BANKROLL_USD,
    breath: float = DEFAULT_REPLAY_INITIAL_BREATH,
    entry_price_floor: float | None = None,
    effective_entry_price_floor: float | None = None,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
) -> SweepMetrics:
    """Synchronous wrapper around :func:`score_config` for the sweep loop."""
    return asyncio.run(
        score_config(
            rows,
            cfg,
            bankroll=bankroll,
            breath=breath,
            entry_price_floor=entry_price_floor,
            effective_entry_price_floor=effective_entry_price_floor,
            max_pnl_usd=max_pnl_usd,
            side_correct_pricing=side_correct_pricing,
            value_betting=value_betting,
        )
    )


# --------------------------------------------------------------------------- #
# Task 4: run_cached_sweep + save/load + CLI
# --------------------------------------------------------------------------- #


def save_rows(rows: list[SignalRow], path: Path) -> None:
    """Persist precomputed :class:`SignalRow` to ``path`` as a JSON array.

    Each row is serialised via :func:`dataclasses.asdict` so the on-disk shape
    mirrors the dataclass field-for-field; :func:`load_rows` reverses it. The
    parent directory is created if missing. UTF-8, ``ensure_ascii=False`` so a
    unicode player slug survives the round-trip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(row) for row in rows]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def load_rows(path: Path) -> list[SignalRow]:
    """Load the JSON array written by :func:`save_rows` back into rows."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [SignalRow(**item) for item in payload]


def run_cached_sweep(
    rows: list[SignalRow],
    configs: list[StrategyConfig],
    *,
    bankroll: float = DEFAULT_REPLAY_INITIAL_BANKROLL_USD,
    breath: float = DEFAULT_REPLAY_INITIAL_BREATH,
    entry_price_floor: float | None = None,
    effective_entry_price_floor: float | None = None,
    max_pnl_usd: float | None = None,
    side_correct_pricing: bool = False,
    value_betting: bool = False,
) -> list[tuple[StrategyConfig, SweepMetrics]]:
    """Score every ``config`` over the cached ``rows``, in INPUT order.

    The signals are computed once (in ``rows``); only the fusion weights +
    sizing knobs vary per config, so each config is just a fresh in-memory pass
    of the REAL ``DecisionEngine.decide`` + faithful PnL. The returned pairs are
    in input order; the CLI sorts for display. The realism-v3 kwargs (default
    OFF) thread straight into :func:`score_config`.
    """
    return [
        (
            cfg,
            score_config_sync(
                rows,
                cfg,
                bankroll=bankroll,
                breath=breath,
                entry_price_floor=entry_price_floor,
                effective_entry_price_floor=effective_entry_price_floor,
                max_pnl_usd=max_pnl_usd,
                side_correct_pricing=side_correct_pricing,
                value_betting=value_betting,
            ),
        )
        for cfg in configs
    ]


def _cmd_precompute(args: argparse.Namespace) -> int:
    """``precompute`` subcommand — build + cache the signal rows ONCE.

    Loads every cached market, builds the resolver off the full re-vendored
    Sackmann corpus (``DEFAULT_CORPUS_DIR``) so resolution is offline, computes
    the REAL 5-slot signals at the mid-market entry, and writes the rows.
    Imports the ``data.sources`` corpus lazily here (not at module import) to
    avoid the package's import-order circular.
    """
    from agent.backtest.historical_fetcher import load_all_cached_markets
    from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

    snapshots = load_all_cached_markets(cache_dir=args.cache_dir)
    # Determinism contract: iterate markets in stable market_id order.
    snapshots.sort(key=lambda s: s.market_id)

    loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
    resolver = TennisMatchResolver.from_sackmann_loader(
        loader, year_range=(2024, 2026)
    )
    provider = MarketSnapshotProvider(snapshots)
    src = RealSignalSource(provider=provider, resolver=resolver, loader=loader)

    rows = precompute_rows(
        snapshots, resolver, src, entry_fraction=args.entry_fraction
    )
    save_rows(rows, args.out)
    print(
        f"wrote {len(rows)} rows (resolved {len(rows)} / total {len(snapshots)}) "
        f"-> {args.out}",
        flush=True,
    )
    return 0


def rank_configs(
    scored: list[tuple[StrategyConfig, SweepMetrics]], *, min_bets: int = 0
) -> list[tuple[StrategyConfig, SweepMetrics]]:
    """Rank scored configs by per-bet Sharpe, gating out low-sample configs.

    Per-bet Sharpe is meaningless on a handful of bets (3 bets at 100% win-rate
    scores an artificially high Sharpe), so ``min_bets`` excludes configs that
    bet fewer than that many times before ranking. If the gate empties the pool
    (no config bets enough), it falls back to the full pool so the caller still
    gets a ranking. Default ``min_bets=0`` keeps every config (legacy behaviour).
    """
    eligible = [kv for kv in scored if kv[1].bets >= min_bets]
    pool = eligible if eligible else scored
    return sorted(pool, key=lambda kv: kv[1].sharpe, reverse=True)


def rank_configs_by_pnl(
    scored: list[tuple[StrategyConfig, SweepMetrics]],
    *,
    min_bets: int = 0,
    min_t_stat: float = 2.0,
) -> list[tuple[StrategyConfig, SweepMetrics]]:
    """Rank by TOTAL net PnL, gated on sample size AND statistical strength.

    The earnings-aligned objective (realism v3): "earn the most, but the edge
    must be statistically real" — a config must clear BOTH ``bets >= min_bets``
    and ``t_stat >= min_t_stat`` (``sharpe*sqrt(bets)``; ~2 ≈ a 95%-confidence
    positive edge) before competing on ``net_pnl``. If the double gate empties
    the pool it falls back to the full pool (same convention as
    :func:`rank_configs`) so the caller still gets a ranking — and should
    report "no statistically significant earner" honestly.
    """
    eligible = [
        kv
        for kv in scored
        if kv[1].bets >= min_bets and kv[1].t_stat >= min_t_stat
    ]
    pool = eligible if eligible else scored
    return sorted(pool, key=lambda kv: kv[1].net_pnl, reverse=True)


def _cmd_sweep(args: argparse.Namespace) -> int:
    """``sweep`` subcommand — load rows, LHS configs, rank by Sharpe, print.

    The LHS ``min_bet_size_usd`` range is [1, 10] but at $100 bankroll the
    bankroll_cap is $5, so any ``min_bet`` >= 5 can never bet. Each config is
    post-clamped to ``_MIN_BET_SWEEP_CEILING_USD`` ($4) so the sweep can
    actually place bets (the root cause of the Plan-1 D3 zero-bets).
    """
    from agent.backtest.find_optimal_config import generate_lhs_strategy_configs

    rows = load_rows(args.rows)
    raw_configs = generate_lhs_strategy_configs(args.n, seed=args.seed)
    configs = [_clamp_min_bet(cfg) for cfg in raw_configs]

    # --realism: floor 0.05 on BOTH knobs + the $100 profit cap + side-correct
    # payouts — the journey physics. --value: EV-gated value-mode decisions.
    realism: bool = bool(getattr(args, "realism", False))
    value: bool = bool(getattr(args, "value", False))
    scored = run_cached_sweep(
        rows,
        configs,
        entry_price_floor=0.05 if realism else None,
        effective_entry_price_floor=0.05 if realism else None,
        max_pnl_usd=100.0 if realism else None,
        side_correct_pricing=realism,
        value_betting=value,
    )
    rank_mode: str = getattr(args, "rank", "sharpe")
    if rank_mode == "pnl":
        ranked = rank_configs_by_pnl(scored, min_bets=args.min_bets)
        eligible_n = sum(
            1
            for kv in scored
            if kv[1].bets >= args.min_bets and kv[1].t_stat >= 2.0
        )
    else:
        ranked = rank_configs(scored, min_bets=args.min_bets)
        eligible_n = sum(1 for kv in scored if kv[1].bets >= args.min_bets)

    print(f"\n=== signal-cached sweep (n={args.n}, rows={len(rows)}, "
          f"min_bets={args.min_bets}, eligible={eligible_n}, "
          f"rank={rank_mode}, realism={realism}, value={value}) ===")
    print(f"{'rank':>4} {'sharpe':>8} {'t':>6} {'net_pnl':>9} {'win%':>6} "
          f"{'bets':>5} {'avg$':>6}  config")
    for rank, (cfg, m) in enumerate(ranked[: args.top], start=1):
        w = cfg.weights
        print(
            f"{rank:>4} {m.sharpe:>8.3f} {m.t_stat:>6.1f} {m.net_pnl:>9.2f} "
            f"{m.win_rate * 100.0:>6.1f} {m.bets:>5} {m.avg_size:>6.2f}  "
            f"w_r={w.w_r:.2f} a={[round(a, 2) for a in w.alpha]} "
            f"b1={w.beta[0]:.2f} rho={w.rho:.2f} | "
            f"risk={cfg.max_breath_risk_pct:.2f} "
            f"minconf={cfg.min_confidence:.2f} minbet={cfg.min_bet_size_usd:.1f} "
            f"edge={cfg.min_edge:.3f} kappa={cfg.kappa:.2f}"
        )

    best_cfg, best_m = ranked[0]
    objective = (
        "max net PnL, t-stat>=2 gated"
        if rank_mode == "pnl"
        else "max per-bet Sharpe, un-compounded"
    )
    print(f"\n=== OPTIMAL ({objective}) ===")
    print(f"weights: {best_cfg.weights.model_dump_json()}")
    print(
        f"sizing:  max_breath_risk_pct={best_cfg.max_breath_risk_pct:.4f} "
        f"min_confidence={best_cfg.min_confidence:.4f} "
        f"min_bet_size_usd={best_cfg.min_bet_size_usd:.4f} "
        f"min_edge={best_cfg.min_edge:.4f} kappa={best_cfg.kappa:.4f}"
    )
    print(
        f"sharpe={best_m.sharpe:.3f} t_stat={best_m.t_stat:.1f} "
        f"net_pnl=${best_m.net_pnl:.2f} "
        f"win_rate={best_m.win_rate * 100.0:.1f}% bets={best_m.bets} "
        f"avg_size=${best_m.avg_size:.2f}"
    )
    return 0


def _clamp_min_bet(cfg: StrategyConfig) -> StrategyConfig:
    """Post-clamp a config's ``min_bet_size_usd`` to the sub-$5 sweep ceiling.

    ``StrategyConfig`` is frozen, so return a new instance. Below the ceiling the
    config is returned unchanged.
    """
    if cfg.min_bet_size_usd <= _MIN_BET_SWEEP_CEILING_USD:
        return cfg
    from dataclasses import replace

    return replace(cfg, min_bet_size_usd=_MIN_BET_SWEEP_CEILING_USD)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint — ``precompute`` builds the rows, ``sweep`` ranks configs.

    Returns a process exit code. UTF-8 safe (slugs/players may be unicode).
    """
    parser = argparse.ArgumentParser(
        prog="python -m agent.backtest.cached_sweep",
        description="Signal-cached config sweep: precompute REAL signals once, "
        "then sweep configs in-memory over the faithful decide + PnL.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser(
        "precompute", help="Build + cache the per-market signal rows ONCE."
    )
    p_pre.add_argument("--cache-dir", type=Path, required=True)
    p_pre.add_argument("--out", type=Path, required=True)
    p_pre.add_argument("--entry-fraction", type=float, default=0.5)
    p_pre.set_defaults(func=_cmd_precompute)

    p_sweep = sub.add_parser(
        "sweep", help="Load cached rows, sweep LHS configs, rank by Sharpe."
    )
    p_sweep.add_argument("--rows", type=Path, required=True)
    p_sweep.add_argument("--n", type=int, default=96, help="LHS sample size.")
    p_sweep.add_argument("--seed", type=int, default=0)
    p_sweep.add_argument("--top", type=int, default=15, help="Rows to print.")
    p_sweep.add_argument(
        "--min-bets",
        type=int,
        default=0,
        help="Exclude configs with fewer than this many bets before ranking "
        "(per-bet Sharpe is noise on a tiny sample). 0 = no gate.",
    )
    p_sweep.add_argument(
        "--realism",
        action="store_true",
        help="Apply the journey physics: entry floors 0.05 (row + effective "
        "side), $100 profit cap, side-correct payouts. Default OFF = the "
        "pre-v3 sweep, byte-unchanged.",
    )
    p_sweep.add_argument(
        "--value",
        action="store_true",
        help="Value-betting decisions: decide() sees the market price "
        "(p_model = price + kappa*fused, min_edge gate, odds-aware Kelly).",
    )
    p_sweep.add_argument(
        "--rank",
        choices=("sharpe", "pnl"),
        default="sharpe",
        help="Ranking objective: 'sharpe' (legacy) or 'pnl' (total net PnL "
        "gated on bets>=min-bets AND t_stat>=2).",
    )
    p_sweep.set_defaults(func=_cmd_sweep)

    args = parser.parse_args(argv)
    func: object = args.func
    assert callable(func)
    result = func(args)
    assert isinstance(result, int)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
