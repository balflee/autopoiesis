"""Phase-2 reincarnation experiment (plan 2026-06-12).

The agent lives the SAME training season N times. Across passes it carries
its EXPERIENCE — the 8 fusion-weight scalars, the EMA learner's ~ten derived
quality aggregates (per-engine settlement-credit EMAs + stream qualities +
``rho_quality``; built by ``WeightUpdater.update_from_settlement``), and (AI
variant) one sanitized strategy-level "rebirth retrospective" — but never the
market outcomes themselves. The defense against memorization is the parameter
bottleneck: the whole carried surface is ~20 scalars, disclosed per pass in
the artifact (``carry.ema_keys``). After the passes, ONE learning-frozen
cold-start pass on a held-out later time window measures generalization.
v3 physics (side-correct payouts, EV-gated value mode, effective floor)
apply throughout.
"""

from __future__ import annotations

import dataclasses
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.backtest.cached_sweep import compute_bet_pnl, effective_entry_price
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot
from agent.backtest.survival_season import (
    DEFAULT_ENTRY_PRICE_FLOOR,
    DEFAULT_INITIAL_BREATH,
    DEFAULT_LOSS_MULTIPLIER,
    DEFAULT_MAX_BET_PNL_USD,
    DEFAULT_MAX_LIVES,
    DEFAULT_PHASE2_BANKROLL_USD,
    BaselinePoint,
    SurvivalRecorder,
    SurvivalRow,
    _downsample,
    build_archetype_curve,
    build_static_baseline_curve,
    fragile_seed_from_config,
    run_survival_season,
)
from agent.core.state import Phase, Weights
from agent.engines._performance_window import PerformanceWindow
from agent.engines.reflection import _LLMClient
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.engines.weight_updater import WeightUpdater
from agent.llm.cost_guard import L3CostGuard

__all__ = [
    "apply_weight_deltas",
    "build_rebirth_window",
    "run_reincarnation_export",
    "sanitize_rebirth_note",
    "split_rows_by_time",
]

# The strict advisor's schema bound on a single proposal's |delta| — applied
# here too so a misbehaving model can never jump the weights.
_DELTA_CAP = 0.1
_DELTA_KEYS = ("w_r", "alpha_0", "alpha_1", "alpha_2", "beta_0", "rho")
_NOTE_MAX_CHARS = 500


def _entry_ts(row: SurvivalRow) -> datetime:
    return datetime.fromisoformat(row.entry_asof_ts_iso)


def split_rows_by_time(
    rows: list[SurvivalRow], *, train_fraction: float = 0.7
) -> tuple[list[SurvivalRow], list[SurvivalRow]]:
    """Chronological split: first ``train_fraction`` of entry-time-ordered
    rows = the reincarnation training season; the remainder = the held-out
    cold-start window.

    Ordering matches the season scheduler's canonical key
    ``(parsed timestamp, market_id)``, and rows whose entry timestamp EQUALS
    the last train timestamp are pulled into train so the holdout starts
    STRICTLY later — equal-time markets can never straddle the boundary
    (no look-ahead leakage).
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction must be in (0,1) (got {train_fraction})")
    if len(rows) < 2:
        raise ValueError("need at least 2 rows to split")
    ordered = sorted(rows, key=lambda r: (_entry_ts(r), r.market_id))
    cut = round(len(ordered) * train_fraction)
    cut = max(1, min(len(ordered) - 1, cut))
    # Absorb boundary ties into train.
    while cut < len(ordered) and _entry_ts(ordered[cut]) == _entry_ts(ordered[cut - 1]):
        cut += 1
    if cut >= len(ordered):
        raise ValueError(
            "split degenerate: tie absorption exhausted the holdout window"
        )
    return ordered[:cut], ordered[cut:]


def apply_weight_deltas(
    weights: Weights, deltas: list[dict[str, object]]
) -> Weights:
    """Apply strict-advisor weight deltas, fail-soft.

    Deliberately NOT the runtime's ``_apply_weight_delta``, which RAISES on
    unknown keys and leaves magnitude capping to the advisor parser
    (sandbox_phase2_loop / strategy_advisor_impl). Here: |delta| capped to
    0.1 (the advisor-schema bound), unknown keys and non-numeric deltas
    SKIPPED (a failed retrospective must never crash the experiment); w_r/w_s
    and beta complements; alpha shifted then renormalized to the simplex;
    rho clamped to [-1, 1].
    """
    w_r = weights.w_r
    alpha = list(weights.alpha)
    beta = list(weights.beta)
    rho = weights.rho
    for d in deltas:
        key = d.get("key")
        delta = d.get("delta")
        if (
            key not in _DELTA_KEYS
            or not isinstance(delta, (int, float))
            or isinstance(delta, bool)
        ):
            continue
        dv = max(-_DELTA_CAP, min(_DELTA_CAP, float(delta)))
        if key == "w_r":
            w_r = max(0.0, min(1.0, w_r + dv))
        elif key == "rho":
            rho = max(-1.0, min(1.0, rho + dv))
        elif key == "beta_0":
            b0 = max(0.0, min(1.0, beta[0] + dv))
            beta = [b0, 1.0 - b0]
        else:  # alpha_0 / alpha_1 / alpha_2
            i = int(str(key)[-1])
            alpha[i] = max(0.0, alpha[i] + dv)
            s = sum(alpha)
            alpha = [a / s for a in alpha] if s > 0 else [1 / 3, 1 / 3, 1 / 3]
    return Weights(w_r=w_r, w_s=1.0 - w_r, alpha=alpha, beta=beta, rho=rho)


def build_rebirth_window(
    *,
    pass_index: int,
    terminal_weights: Weights,
    seed_weights: Weights,
    season_pnl_usd: float,
    recent_step_pnls: list[float],
    total_settles: int,
    deaths: int,
) -> PerformanceWindow:
    """The season-level retrospective window the strict advisor reviews at a
    pass boundary. STRATEGY-LEVEL ONLY: aggregates, never market specifics —
    the information-hygiene contract of the reincarnation experiment.

    ``recent_pnl`` keeps its REAL semantics — "last settled bets, $USD" (the
    prompt renderer's label and the dataclass's documented meaning) — so it
    receives the TAIL of settled step pnls, while the season total goes in
    ``recent_pnl_window_usd``. Feeding life totals into recent_pnl would hand
    the advisor false semantics.
    """
    return PerformanceWindow(
        tick=total_settles,
        ts=datetime(1970, 1, 1, tzinfo=UTC),
        agent_id=f"rebirth-pass-{pass_index}-deaths-{deaths}",
        phase=Phase.PHASE_2_APPRENTICE,
        current_weights=terminal_weights,
        baseline_weights=seed_weights,
        recent_pnl_window_usd=season_pnl_usd,
        trigger="tick_interval",
        recent_pnl=list(recent_step_pnls),
        tick_count=total_settles,
    )


def sanitize_rebirth_note(text: str) -> str | None:
    """Enforced hygiene for the persisted rebirth note: collapse whitespace,
    hard-cap length, empty ⇒ None.

    The advisor's entire input is the aggregates-only window built above, so
    real market specifics cannot flow into its rationale — this makes the
    persistence layer enforce that contract rather than assume it.
    """
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return None
    return collapsed[:_NOTE_MAX_CHARS]


# Settled-step tail handed to the rebirth window's ``recent_pnl`` (its real
# "last settled bets" semantics).
_REBIRTH_RECENT_TAIL = 20
# Chart budget per pass curve (mirrors the journey exporter's step budget).
_CURVE_MAX_POINTS = 500


def _season_summary(
    recorder: SurvivalRecorder, *, season_rows: int, deaths: int, lives: int
) -> dict[str, Any]:
    """Per-pass aggregates from the recorder's SETTLED steps (LifeOutcome has
    no pnl field — the steps ledger is the source of truth)."""
    steps = recorder.steps
    settled = len(steps)
    wins = sum(1 for s in steps if s.pnl_usd > 0.0)
    return {
        "pnl": steps[-1].cum_pnl if steps else 0.0,
        "deaths": deaths,
        "lives": lives,
        "settled": settled,
        "coverage_pct": (100.0 * settled / season_rows) if season_rows else 0.0,
        "win_rate": (wins / settled) if settled else 0.0,
    }


def _curve_points(recorder: SurvivalRecorder) -> list[dict[str, float]]:
    """Down-sampled cumulative-PnL curve (settled-step index -> cum)."""
    full = [
        {"i": float(i), "cum_pnl": s.cum_pnl}
        for i, s in enumerate(recorder.steps)
    ]
    return list(_downsample(full, _CURVE_MAX_POINTS))


def _validate_learner_physics(
    recorder: SurvivalRecorder,
    *,
    max_bet_pnl_usd: float | None,
    label: str,
) -> list[float]:
    """Recompute every settled step from first principles; return the
    effective entry prices of ALL PLACED bets (settled or not) for the floor
    backstop. Raises before anything can be written."""
    for s in recorder.steps:
        expected = compute_bet_pnl(
            side=s.side,
            entry_price=s.entry_price,
            size_usd=s.size_usd,
            outcome=s.outcome,
            winning_price=s.winning_price,
            max_pnl_usd=max_bet_pnl_usd,
            side_correct_pricing=True,
        )
        if abs(expected - s.pnl_usd) > 1e-6:
            raise RuntimeError(
                f"physics invariant violated ({label}): step pnl "
                f"{s.pnl_usd!r} != recomputed {expected!r} for "
                f"{s.market_id}; artifact NOT written"
            )
    eff_prices: list[float] = []
    for b in recorder.placed_bets:
        side_v = b.get("side")
        price_v = b.get("price")
        if isinstance(side_v, str) and isinstance(price_v, (int, float)):
            eff_prices.append(
                effective_entry_price(side=side_v, yes_price=float(price_v))
            )
    return eff_prices


def _validate_baseline_physics(
    curve: list[BaselinePoint],
    rows_by_id: dict[str, SurvivalRow],
    *,
    max_bet_pnl_usd: float | None,
    name: str,
) -> list[float]:
    """Recompute every baseline bet; skipped points must be shape-clean.
    Returns the bets' effective entry prices for the floor backstop."""
    eff_prices: list[float] = []
    for p in curve:
        if not p.is_bet:
            if p.size_usd != 0.0 or p.pnl_usd != 0.0:
                raise RuntimeError(
                    f"physics invariant violated ({name}): skipped point "
                    f"{p.market_id} carries size={p.size_usd!r}/"
                    f"pnl={p.pnl_usd!r}; artifact NOT written"
                )
            continue
        assert p.side is not None
        row = rows_by_id[p.market_id]
        expected = compute_bet_pnl(
            side=p.side,
            entry_price=row.entry_price,
            size_usd=p.size_usd,
            outcome=row.outcome,
            winning_price=row.winning_price,
            max_pnl_usd=max_bet_pnl_usd,
            side_correct_pricing=True,
        )
        if abs(expected - p.pnl_usd) > 1e-6:
            raise RuntimeError(
                f"physics invariant violated ({name}): baseline pnl "
                f"{p.pnl_usd!r} != recomputed {expected!r} for "
                f"{p.market_id}; artifact NOT written"
            )
        eff_prices.append(
            effective_entry_price(side=p.side, yes_price=row.entry_price)
        )
    return eff_prices


def run_reincarnation_export(
    *,
    rows: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    base_seed: StrategyConfig,
    out_path: Path,
    passes: int = 3,
    train_fraction: float = 0.7,
    fragile_max_breath_risk_pct: float = 0.95,
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER,
    initial_breath: float = DEFAULT_INITIAL_BREATH,
    initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
    max_lives: int = DEFAULT_MAX_LIVES,
    max_bet_pnl_usd: float | None = DEFAULT_MAX_BET_PNL_USD,
    entry_price_floor: float = DEFAULT_ENTRY_PRICE_FLOOR,
    effective_entry_price_floor: float | None = None,
    rebirth_llm: _LLMClient | None = None,
    rebirth_guard: L3CostGuard | None = None,
    rebirth_model: str = "",
    state_root: Path | None = None,
) -> dict[str, Any]:
    """The Phase-2 reincarnation experiment: ``passes`` full survival seasons
    over the SAME chronological training window, carrying weights + the EMA
    learner's inner state (+ optionally a sanitized strict-advisor "rebirth
    retrospective" at each pass boundary), then ONE learning-frozen cold-start
    pass on the held-out later window + the three baselines. v3 physics
    throughout; every settled step, baseline point, and placed bet is
    re-validated from first principles before the artifact is written.

    The orchestrator's own knob defaults mirror ``run_survival_export``'s so
    tiny-fixture tests stay cheap; the REAL journey knobs (fragile 0.95 /
    loss 5.0 / breath 35 / lives 12) are passed explicitly by the runner.
    """
    # One source of truth for the physics knobs (None => mirror the row floor).
    eff_floor = (
        entry_price_floor
        if effective_entry_price_floor is None
        else effective_entry_price_floor
    )

    # Row-floor provenance: fail closed if the universe was not loaded under
    # the floor this artifact will claim.
    bad = min((r.entry_price for r in rows), default=None)
    if bad is not None and bad < entry_price_floor:
        raise ValueError(
            f"row universe violates entry_price_floor {entry_price_floor!r} "
            f"(min entry price {bad!r}) -- load rows with the same floor"
        )

    train, holdout = split_rows_by_time(rows, train_fraction=train_fraction)
    fragile = fragile_seed_from_config(
        base_seed, max_breath_risk_pct=fragile_max_breath_risk_pct
    )

    shared_inner = WeightUpdater()
    carry = fragile.weights
    rebirth_note: str | None = None
    pass_entries: list[dict[str, Any]] = []
    all_eff_prices: list[float] = []

    with tempfile.TemporaryDirectory(prefix="reincarnation_") as tmp:
        root = Path(tmp) if state_root is None else Path(state_root)

        for i in range(1, passes + 1):
            recorder = SurvivalRecorder(
                rows=train, loss_multiplier=loss_multiplier
            )
            pass_seed = dataclasses.replace(fragile, weights=carry)
            result = run_survival_season(
                rows=train,
                snapshots=snapshots,
                seed=pass_seed,
                state_root=root / f"pass_{i}",
                initial_breath=initial_breath,
                initial_bankroll_usd=initial_bankroll_usd,
                max_lives=max_lives,
                max_bet_pnl_usd=max_bet_pnl_usd,
                recorder=recorder,
                side_correct_pricing=True,
                value_betting=True,
                effective_entry_price_floor=eff_floor,
                shared_inner=shared_inner,
            )
            all_eff_prices.extend(
                _validate_learner_physics(
                    recorder,
                    max_bet_pnl_usd=max_bet_pnl_usd,
                    label=f"pass {i}",
                )
            )

            terminal = (
                result.lives[-1].terminal_weights if result.lives else carry
            )
            per_life_pnls = [
                sum(s.pnl_usd for s in recorder.steps if s.life_idx == life.idx)
                for life in result.lives
            ]
            pass_entries.append(
                {
                    "pass": i,
                    "summary": _season_summary(
                        recorder,
                        season_rows=len(train),
                        deaths=result.deaths,
                        lives=len(result.lives),
                    ),
                    "per_life_pnls": per_life_pnls,
                    "start_weights": carry.model_dump(),
                    "terminal_weights": terminal.model_dump(),
                    "curve": _curve_points(recorder),
                    "rebirth_note": rebirth_note,
                    # Carried-state disclosure: the parameter bottleneck is
                    # auditable -- the EMA buffer's keyset IS the whole carried
                    # inner state (feature-name-keyed scalar aggregates).
                    "carry": {
                        "ema_keys": sorted(shared_inner._ema),
                        "ema_size": len(shared_inner._ema),
                    },
                }
            )

            # Pass boundary: carry the experience into the next incarnation.
            carry = terminal
            rebirth_note = None
            if rebirth_llm is not None and i < passes:
                window = build_rebirth_window(
                    pass_index=i,
                    terminal_weights=terminal,
                    seed_weights=fragile.weights,
                    season_pnl_usd=(
                        recorder.steps[-1].cum_pnl if recorder.steps else 0.0
                    ),
                    recent_step_pnls=[s.pnl_usd for s in recorder.steps][
                        -_REBIRTH_RECENT_TAIL:
                    ],
                    total_settles=len(recorder.steps),
                    deaths=result.deaths,
                )
                advisor = StrategyAdvisorImpl(
                    llm_client=rebirth_llm,
                    cost_guard=(
                        rebirth_guard
                        if rebirth_guard is not None
                        else L3CostGuard.from_env()
                    ),
                    weight_delta_only=True,
                    model=rebirth_model,
                )
                proposals = advisor.review_window(window)
                if proposals:
                    carry = apply_weight_deltas(
                        terminal,
                        [dict(p.proposed_change) for p in proposals],
                    )
                    rebirth_note = sanitize_rebirth_note(
                        "; ".join(p.rationale for p in proposals)
                    )

        # -- The cold-start verdict: held-out window, learning FROZEN. -------
        holdout_recorder = SurvivalRecorder(
            rows=holdout, loss_multiplier=loss_multiplier
        )
        holdout_seed = dataclasses.replace(fragile, weights=carry)
        holdout_result = run_survival_season(
            rows=holdout,
            snapshots=snapshots,
            seed=holdout_seed,
            state_root=root / "holdout",
            initial_breath=initial_breath,
            initial_bankroll_usd=initial_bankroll_usd,
            max_lives=max_lives,
            max_bet_pnl_usd=max_bet_pnl_usd,
            recorder=holdout_recorder,
            side_correct_pricing=True,
            value_betting=True,
            effective_entry_price_floor=eff_floor,
            learning_enabled=False,
        )
    all_eff_prices.extend(
        _validate_learner_physics(
            holdout_recorder, max_bet_pnl_usd=max_bet_pnl_usd, label="holdout"
        )
    )

    # Baselines on the SAME holdout window -- each builder gets exactly its
    # own knob surface (archetypes have no bankroll/breath/value params).
    static_curve = build_static_baseline_curve(
        holdout,
        holdout_seed,
        bankroll=initial_bankroll_usd,
        breath=initial_breath,
        max_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=True,
        value_betting=True,
        effective_entry_price_floor=eff_floor,
    )
    random_curve = build_archetype_curve(
        holdout,
        archetype="random",
        seed=0,
        max_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=True,
        effective_entry_price_floor=eff_floor,
    )
    favorite_curve = build_archetype_curve(
        holdout,
        archetype="always_favorite",
        max_pnl_usd=max_bet_pnl_usd,
        side_correct_pricing=True,
        effective_entry_price_floor=eff_floor,
    )
    rows_by_id = {r.market_id: r for r in holdout}
    for name, curve in (
        ("static", static_curve),
        ("random", random_curve),
        ("always_favorite", favorite_curve),
    ):
        all_eff_prices.extend(
            _validate_baseline_physics(
                curve, rows_by_id, max_bet_pnl_usd=max_bet_pnl_usd, name=name
            )
        )

    min_eff = min(all_eff_prices, default=None)
    if min_eff is not None and min_eff < eff_floor:
        raise RuntimeError(
            f"physics invariant violated: a placed bet's effective entry "
            f"price {min_eff!r} is below the floor {eff_floor!r}; "
            "artifact NOT written"
        )

    holdout_summary = _season_summary(
        holdout_recorder,
        season_rows=len(holdout),
        deaths=holdout_result.deaths,
        lives=len(holdout_result.lives),
    )
    holdout_summary["learning_enabled"] = False

    artifact: dict[str, Any] = {
        "experiment": "reincarnation",
        "provider": "ai" if rebirth_llm is not None else "numerical",
        "physics": {
            "side_correct_pricing": True,
            "value_betting": True,
            "entry_price_floor": entry_price_floor,
            "max_bet_pnl_usd": max_bet_pnl_usd,
            "effective_entry_price_floor": eff_floor,
            "min_effective_entry_price": min_eff,
            "min_edge": fragile.min_edge,
            "kappa": fragile.kappa,
        },
        "split": {
            "train_rows": len(train),
            "holdout_rows": len(holdout),
            "train_fraction": train_fraction,
            "train_end_ts": train[-1].entry_asof_ts_iso,
            "holdout_start_ts": holdout[0].entry_asof_ts_iso,
        },
        "knobs": {
            "passes": passes,
            "fragile_max_breath_risk_pct": fragile_max_breath_risk_pct,
            "loss_multiplier": loss_multiplier,
            "initial_breath": initial_breath,
            "initial_bankroll_usd": initial_bankroll_usd,
            "max_lives": max_lives,
        },
        "passes": pass_entries,
        "holdout": {
            "summary": holdout_summary,
            "start_weights": carry.model_dump(),
            "curve": _curve_points(holdout_recorder),
            "baselines": {
                "static": static_curve[-1].cum_pnl if static_curve else 0.0,
                "random": random_curve[-1].cum_pnl if random_curve else 0.0,
                "always_favorite": (
                    favorite_curve[-1].cum_pnl if favorite_curve else 0.0
                ),
            },
        },
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(artifact, sort_keys=True, indent=2), encoding="utf-8"
    )
    return artifact
