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

import asyncio
import dataclasses
import json
import math
import random as _grandom
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

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
    preflight_ai_advisor_applicable,
    run_survival_season,
)
from agent.core.state import Phase, Weights
from agent.engines._performance_window import PerformanceWindow
from agent.engines.reflection import _LLMClient
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.engines.weight_updater import WeightUpdater
from agent.llm.cost_guard import L3CostGuard
from agent.runtime.tribute import (
    TRIBUTE_FULL_USD,
    TRIBUTE_MIN_USD,
    ReflexTributePolicy,
    TributePolicy,
)

__all__ = [
    "EXTENDED_REBIRTH_KEYS",
    "GENOME_KEYS",
    "GENOME_MIN_BREATH_RISK_PCT",
    "LLMTributePolicy",
    "apply_genome_deltas",
    "apply_weight_deltas",
    "build_death_window",
    "build_rebirth_window",
    "build_regime_ledger",
    "classify_proposals",
    "genome_dict",
    "render_regime_ledger_text",
    "run_groundhog_export",
    "run_reincarnation_export",
    "sanitize_rebirth_note",
    "shuffle_timestamps",
    "split_rows_by_time",
]

# The strict advisor's schema bound on a single proposal's |delta| — applied
# here too so a misbehaving model can never jump the weights.
_DELTA_CAP = 0.1
_DELTA_KEYS = ("w_r", "alpha_0", "alpha_1", "alpha_2", "beta_0", "rho")
_NOTE_MAX_CHARS = 500

# --------------------------------------------------------------------------- #
# A9 genome (plan 2026-06-13): the rebirth-advisable StrategyConfig knobs.
#
# min_bet_size_usd is deliberately ABSENT (r8 M-3): against the $5
# liquidity floor it is an all-or-nothing participation switch — one
# +0.1-class push over the floor silently zeroes participation and
# confounds the γ/participation readout. The advisable set is EXACTLY
# this table, nothing implicit.
# --------------------------------------------------------------------------- #

#: r11 M-2: an open bound (0, 1] is not an implementable clamp — the
#: DecisionEngine ctor REJECTS max_breath_risk_pct <= 0, so repeated
#: −0.1 deltas need a concrete positive floor.
GENOME_MIN_BREATH_RISK_PCT: Final[float] = 0.05

#: key -> (lo, hi) inclusive clamp bounds for ``apply_genome_deltas``.
GENOME_KEYS: Final[dict[str, tuple[float, float]]] = {
    "min_edge": (0.0, 0.5),
    "max_breath_risk_pct": (GENOME_MIN_BREATH_RISK_PCT, 1.0),
    "min_confidence": (0.0, 1.0),
    "kappa": (0.01, 1.0),
    "gate_storm_sensitivity": (-1.0, 1.0),
    "risk_storm_sensitivity": (-1.0, 1.0),
}

#: The extended rebirth-boundary vocabulary: the six fusion keys plus the
#: genome knobs. Threaded as ``allowed_keys`` into the strict advisor AND
#: the preflight probe when the kit is on — NEVER into the live drain
#: (REFLECTION_WEIGHT_KEYS stays untouched).
EXTENDED_REBIRTH_KEYS: Final[tuple[str, ...]] = _DELTA_KEYS + tuple(
    GENOME_KEYS
)


@dataclasses.dataclass(frozen=True)
class ClassifiedDeltas:
    """r10 M-2: the ONE classifier output — the apply path and every
    applied-count/telemetry consumer read the same lists, so application
    and accounting can never diverge."""

    fusion: list[dict[str, object]]
    genome: list[dict[str, object]]
    skipped: list[dict[str, object]]

    @property
    def applied(self) -> int:
        return len(self.fusion) + len(self.genome)


def classify_proposals(
    deltas: list[dict[str, object]], *, genome_enabled: bool = False
) -> ClassifiedDeltas:
    """Fork proposals by key family (fusion → Weights; genome → seed).

    The applicability predicate matches ``apply_weight_deltas`` /
    ``apply_genome_deltas`` exactly: known key + non-bool numeric delta.
    With ``genome_enabled=False`` every genome key is SKIPPED (the
    kit-off vocabulary is the six fusion keys, byte-identical to today).
    """
    fusion: list[dict[str, object]] = []
    genome: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for d in deltas:
        key = d.get("key")
        delta = d.get("delta")
        numeric = isinstance(delta, (int, float)) and not isinstance(
            delta, bool
        )
        if numeric and key in _DELTA_KEYS:
            fusion.append(d)
        elif numeric and genome_enabled and key in GENOME_KEYS:
            genome.append(d)
        else:
            skipped.append(d)
    return ClassifiedDeltas(fusion=fusion, genome=genome, skipped=skipped)


def apply_genome_deltas(
    seed: StrategyConfig, deltas: list[dict[str, object]]
) -> StrategyConfig:
    """Apply genome deltas onto the carried seed, fail-soft.

    Mirrors :func:`apply_weight_deltas`' posture: |delta| capped to 0.1,
    unknown keys / non-numeric deltas SKIPPED, every result clamped to
    the GENOME_KEYS bounds via ``dataclasses.replace`` (the seed is a
    frozen dataclass). ``weights`` is never touched here — fusion keys
    have their own home.
    """
    updates: dict[str, float] = {}
    for d in deltas:
        key = d.get("key")
        delta = d.get("delta")
        if (
            not isinstance(key, str)
            or key not in GENOME_KEYS
            or isinstance(delta, bool)
            or not isinstance(delta, (int, float))
        ):
            continue
        dv = max(-_DELTA_CAP, min(_DELTA_CAP, float(delta)))
        lo, hi = GENOME_KEYS[key]
        cur = updates.get(key, float(getattr(seed, key)))
        updates[key] = max(lo, min(hi, cur + dv))
    if not updates:
        return seed
    # The kwargs are dynamic (key-clamped floats onto float fields) — mypy
    # cannot prove the mapping against replace()'s typed signature.
    return dataclasses.replace(seed, **updates)  # type: ignore[arg-type]


def genome_dict(seed: StrategyConfig) -> dict[str, float]:
    """The SINGLE source for the advisable-knob dict (r9 H-2) — feeds the
    artifact genome fields and the death-window readout."""
    return {key: float(getattr(seed, key)) for key in GENOME_KEYS}


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


#: K7 falsification: minimum inter-slot spacing after the monotone
#: normalization pass — 60 s ≫ the replay clock's worst within-tick drift
#: (+1 s per now() read), so synthetic stops can never let tick N's clock
#: run past tick N+1 and snap backward (r11 H-1 / r13 M-1).
_SHUFFLE_MIN_SPACING: Final[timedelta] = timedelta(seconds=60)


def _shift_iso(ts_iso: str, delta: timedelta) -> str:
    return (datetime.fromisoformat(ts_iso) + delta).isoformat()


def _shift_iso_opt(ts_iso: str | None, delta: timedelta) -> str | None:
    return None if ts_iso is None else _shift_iso(ts_iso, delta)


def shuffle_timestamps(
    rows: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    *,
    seed: int,
    min_spacing: timedelta = _SHUFFLE_MIN_SPACING,
) -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    """K7: the seeded PAIRED TIME-SHIFT — a pure PRE-split transform that
    destroys serial regime structure while preserving each market's
    internal physics.

    Row-order shuffling alone is ERASED by the split/schedule re-sorts,
    and shifting row timestamps without their snapshots desyncs
    settlement availability (r1 H-4 / r2 H-2). So: sort ALL (row,
    snapshot) pairs chronologically into slots; normalize the slot
    timeline MONOTONE with ``min_spacing`` between slots (cumulative
    forward push — ties are eliminated ENTIRELY, so slot order IS the
    seeded permutation order, and 60 s spacing holds globally BY
    CONSTRUCTION; the stretch is harmless — the timeline is synthetic +
    disclosed and the split is count-based); draw a seeded permutation;
    clone the pair assigned to each slot with EVERY timestamp field
    shifted by ``slot_ts − pair_entry_ts`` (row: entry/resolution/end;
    snapshot: end/resolution/price_ledger), preserving each pair's
    internal intervals exactly. Snapshots without a row pass through
    untouched.
    """
    snap_by_id = {s.market_id: s for s in snapshots}
    missing = [r.market_id for r in rows if r.market_id not in snap_by_id]
    if missing:
        raise ValueError(
            f"shuffle_timestamps: rows without snapshots: {missing[:3]}"
        )
    ordered = sorted(rows, key=lambda r: (_entry_ts(r), r.market_id))
    # Monotone slot timeline (r13 M-1).
    slot_ts: list[datetime] = []
    for r in ordered:
        ts = _entry_ts(r)
        if slot_ts and ts < slot_ts[-1] + min_spacing:
            ts = slot_ts[-1] + min_spacing
        slot_ts.append(ts)
    rng = _grandom.Random(seed)
    order = list(range(len(ordered)))
    rng.shuffle(order)  # order[j] = original sorted index assigned to slot j
    out_rows: list[SurvivalRow] = []
    out_snaps: list[MarketSnapshot] = []
    shuffled_ids: set[str] = set()
    for j, i in enumerate(order):
        row = ordered[i]
        snap = snap_by_id[row.market_id]
        delta = slot_ts[j] - _entry_ts(row)
        out_rows.append(
            dataclasses.replace(
                row,
                entry_asof_ts_iso=_shift_iso(row.entry_asof_ts_iso, delta),
                resolution_ts_iso=_shift_iso_opt(
                    row.resolution_ts_iso, delta
                ),
                end_date_iso=_shift_iso(row.end_date_iso, delta),
            )
        )
        out_snaps.append(
            snap.model_copy(
                update={
                    "end_date_iso": _shift_iso(snap.end_date_iso, delta),
                    "resolution_ts_iso": _shift_iso_opt(
                        snap.resolution_ts_iso, delta
                    ),
                    "price_ledger": [
                        pp.model_copy(
                            update={"ts": _shift_iso(pp.ts, delta)}
                        )
                        for pp in snap.price_ledger
                    ],
                }
            )
        )
        shuffled_ids.add(row.market_id)
    out_snaps.extend(
        s for s in snapshots if s.market_id not in shuffled_ids
    )
    return out_rows, out_snaps


def _bets_by_third(
    placed_rows: list[dict[str, Any]],
    *,
    consumed_entry_ts: list[datetime],
) -> list[dict[str, Any]]:
    """Participation split (anti-shutdown-ratchet, constraint 3).

    ``placed_rows`` are raw open_bets.jsonl rows — the poller appends a
    settled status-flip COPY per bet, so dedup by ``bet_id`` keeps the
    FIRST (placement) record (r2 M-3). Buckets are thirds of the life's
    consumed-market entry span; denominators (consumed markets per
    third) are disclosed as approximate.
    """
    seen: set[str] = set()
    placements: list[dict[str, Any]] = []
    for b in placed_rows:
        bid = b.get("bet_id")
        if isinstance(bid, str) and bid not in seen:
            seen.add(bid)
            placements.append(b)
    thirds: list[dict[str, Any]] = [
        {"third": t, "placed": 0, "denominator": 0} for t in range(3)
    ]
    if not consumed_entry_ts:
        return thirds
    start = min(consumed_entry_ts)
    end = max(consumed_entry_ts)
    span = (end - start).total_seconds()

    def _bucket(ts: datetime) -> int:
        if span <= 0.0:
            return 0
        frac = max(0.0, min(1.0, (ts - start).total_seconds() / span))
        return min(2, int(frac * 3.0))

    for ts in consumed_entry_ts:
        thirds[_bucket(ts)]["denominator"] += 1
    for b in placements:
        ts_raw = b.get("ts")
        if isinstance(ts_raw, str):
            thirds[_bucket(datetime.fromisoformat(ts_raw))]["placed"] += 1
    return thirds


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
    pass boundary.

    Information-hygiene contract, stated precisely: the LLM receives season
    AGGREGATES plus the ANONYMOUS settled-bet pnl tail in ``recent_pnl`` (its
    documented semantics — "last settled bets, $USD"). Neither carries a
    market id, slug, player name, or outcome label: the pnl sequence cannot
    be mapped back to specific markets, so nothing here lets a later pass
    cheat a specific market.

    ``recent_pnl`` keeps its REAL semantics, so it receives the TAIL of
    settled step pnls, while the season total goes in
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

    The advisor's entire input is the window built above — aggregates plus an
    anonymous pnl tail, with no market identities — so real market specifics
    cannot flow into its rationale; this makes the persistence layer enforce
    that contract rather than assume it.
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
    carry_seed: StrategyConfig = fragile
    rebirth_note: str | None = None
    pass_entries: list[dict[str, Any]] = []
    all_eff_prices: list[float] = []

    with tempfile.TemporaryDirectory(prefix="reincarnation_") as tmp:
        root = Path(tmp) if state_root is None else Path(state_root)

        for i in range(1, passes + 1):
            _require_fresh_dir(root / f"pass_{i}")
            recorder = SurvivalRecorder(
                rows=train, loss_multiplier=loss_multiplier
            )
            pass_seed = carry_seed
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
                result.lives[-1].terminal_weights
                if result.lives
                else carry_seed.weights
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
                    "start_weights": carry_seed.weights.model_dump(),
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
            carry_seed = dataclasses.replace(carry_seed, weights=terminal)
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
                    carry_seed = dataclasses.replace(
                        carry_seed,
                        weights=apply_weight_deltas(
                            terminal,
                            [dict(p.proposed_change) for p in proposals],
                        ),
                    )
                    rebirth_note = sanitize_rebirth_note(
                        "; ".join(p.rationale for p in proposals)
                    )

        # -- The cold-start verdict: held-out window, learning FROZEN. -------
        holdout_dict, holdout_eff = _run_frozen_holdout(
            holdout=holdout,
            snapshots=snapshots,
            carry=carry_seed,
            state_dir=root / "holdout",
            loss_multiplier=loss_multiplier,
            initial_breath=initial_breath,
            initial_bankroll_usd=initial_bankroll_usd,
            max_lives=max_lives,
            max_bet_pnl_usd=max_bet_pnl_usd,
            eff_floor=eff_floor,
        )
    all_eff_prices.extend(holdout_eff)

    min_eff = min(all_eff_prices, default=None)
    if min_eff is not None and min_eff < eff_floor:
        raise RuntimeError(
            f"physics invariant violated: a placed bet's effective entry "
            f"price {min_eff!r} is below the floor {eff_floor!r}; "
            "artifact NOT written"
        )

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
        "holdout": holdout_dict,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(artifact, sort_keys=True, indent=2), encoding="utf-8"
    )
    return artifact


# ========================================================================= #
# Groundhog design (v2): one incarnation = one life from market #1.
# ========================================================================= #

# Beyond this index, dead incarnations drop their down-sampled curve from the
# artifact (size guard: scalars stay for every incarnation; the survivor and
# the final incarnation always keep their curves).
_CURVE_KEEP_FIRST = 8


def _require_fresh_dir(path: Path) -> None:
    """Fail closed on a dirty state dir.

    The life loop RECONSTRUCTS from disk on entry — it restores snapshot
    weights and resumes tick counters from existing JSONL — so a reused dir
    silently corrupts the run. Never deletes (the caller owns cleanup); a
    temp-dir root is empty by construction so this is free on the default
    path.
    """
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(
            f"dirty state dir {path} would resume stale loop state; "
            "use a fresh state_root"
        )


def _run_frozen_holdout(
    *,
    holdout: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    carry: StrategyConfig,
    state_dir: Path,
    loss_multiplier: float,
    initial_breath: float,
    initial_bankroll_usd: float,
    max_lives: int,
    max_bet_pnl_usd: float | None,
    eff_floor: float | None,
    storm: bool = False,
) -> tuple[dict[str, Any], list[float]]:
    """The frozen cold-start verdict + the three baselines on the held-out
    window — shared by BOTH experiment designs (3-pass and groundhog).

    Returns ``(holdout artifact dict, effective entry prices)`` — the caller
    folds the prices into its global floor backstop. Raises (fail-closed)
    on any physics violation BEFORE the caller can write an artifact.
    """
    _require_fresh_dir(state_dir)
    recorder = SurvivalRecorder(rows=holdout, loss_multiplier=loss_multiplier)
    # r1 M-6 (the holdout trap): the carried MUTATED seed IS the holdout
    # seed — rebuilding from the original fragile would drop every learned
    # genome knob exactly at the verdict.
    seed = carry
    result = run_survival_season(
        rows=holdout,
        snapshots=snapshots,
        seed=seed,
        state_root=state_dir,
        initial_breath=initial_breath,
        initial_bankroll_usd=initial_bankroll_usd,
        max_lives=max_lives,
        max_bet_pnl_usd=max_bet_pnl_usd,
        recorder=recorder,
        side_correct_pricing=True,
        value_betting=True,
        effective_entry_price_floor=eff_floor,
        learning_enabled=False,
        storm_enabled=storm,
    )
    eff_prices = _validate_learner_physics(
        recorder, max_bet_pnl_usd=max_bet_pnl_usd, label="holdout"
    )

    # Baselines on the SAME holdout window -- each builder gets exactly its
    # own knob surface (archetypes have no bankroll/breath/value params).
    static_curve = build_static_baseline_curve(
        holdout,
        seed,
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
        eff_prices.extend(
            _validate_baseline_physics(
                curve, rows_by_id, max_bet_pnl_usd=max_bet_pnl_usd, name=name
            )
        )

    summary = _season_summary(
        recorder,
        season_rows=len(holdout),
        deaths=result.deaths,
        lives=len(result.lives),
    )
    summary["learning_enabled"] = False
    holdout_dict: dict[str, Any] = {
        "summary": summary,
        "start_weights": carry.weights.model_dump(),
        **({"start_genome": genome_dict(carry)} if storm else {}),
        "curve": _curve_points(recorder),
        "baselines": {
            "static": static_curve[-1].cum_pnl if static_curve else 0.0,
            "random": random_curve[-1].cum_pnl if random_curve else 0.0,
            "always_favorite": (
                favorite_curve[-1].cum_pnl if favorite_curve else 0.0
            ),
        },
    }
    return holdout_dict, eff_prices


#: A9 K6: the pre-registered γ-grid for the tightening-only gate
#: counterfactual and the storm-split threshold.
_LEDGER_GAMMA_GRID: Final[tuple[float, ...]] = (0.05, 0.1, 0.2)
_LEDGER_STORM_SPLIT: Final[float] = 0.5


def build_regime_ledger(
    steps: list[Any],
    *,
    loss_multiplier: float,
    storm_split_threshold: float = _LEDGER_STORM_SPLIT,
    gamma_grid: tuple[float, ...] = _LEDGER_GAMMA_GRID,
) -> dict[str, Any] | None:
    """The K6 counterfactual ledger over one life's SETTLED steps.

    (a) Storm-split accounting: settled bets partitioned by
    ``storm_at_bet >= threshold``; breath_delta applies the loss
    multiplier to losing pnl (the physics that kills).

    (b) γ-grid gate counterfactual, TIGHTENING DIRECTION ONLY (r1 H-3:
    the outcomes of bets never placed are unknowable, so the loosening
    direction is NOT computable and is never claimed). For each γ' the
    candidate gate is ``eff'(γ') = min_edge_at_bet + γ'·storm_at_bet``;
    a bet is **computable** iff ``eff'(γ') >= eff_min_edge_at_bet``
    (true tightening relative to the policy that admitted it — r4 M-3),
    and **would have been blocked** iff ``|edge_at_bet| < eff'(γ')``
    (the REAL gate predicate, decision.py — r5 M-3).

    Returns ``None`` when NO step carries stamps (storm off / no
    settles) — the caller omits the artifact key.
    """
    stamped = [
        s
        for s in steps
        if s.storm_at_bet is not None
        and s.edge_at_bet is not None
        and s.min_edge_at_bet is not None
        and s.eff_min_edge_at_bet is not None
    ]
    if not stamped:
        return None

    def _bucket(subset: list[Any]) -> dict[str, float | int]:
        pnl = sum(s.pnl_usd for s in subset)
        breath = sum(
            s.pnl_usd * (loss_multiplier if s.pnl_usd < 0.0 else 1.0)
            for s in subset
        )
        return {"bets": len(subset), "pnl": pnl, "breath_delta": breath}

    high = [s for s in stamped if s.storm_at_bet >= storm_split_threshold]
    low = [s for s in stamped if s.storm_at_bet < storm_split_threshold]

    counterfactuals: list[dict[str, Any]] = []
    for gamma in gamma_grid:
        computable = 0
        not_computable = 0
        blocked = 0
        blocked_pnl = 0.0
        for s in stamped:
            eff_candidate = s.min_edge_at_bet + gamma * s.storm_at_bet
            if eff_candidate < s.eff_min_edge_at_bet:
                not_computable += 1
                continue
            computable += 1
            if abs(s.edge_at_bet) < eff_candidate:
                blocked += 1
                blocked_pnl += s.pnl_usd
        counterfactuals.append(
            {
                "gamma": gamma,
                "computable": computable,
                "not_computable": not_computable,
                "blocked": blocked,
                "blocked_pnl": blocked_pnl,
            }
        )

    return {
        "storm_split": {
            "threshold": storm_split_threshold,
            "high": _bucket(high),
            "low": _bucket(low),
        },
        "gate_counterfactuals": counterfactuals,
        "stamped_steps": len(stamped),
        "unstamped_steps": len(steps) - len(stamped),
    }


def render_regime_ledger_text(ledger: dict[str, Any]) -> str:
    """The 2-3 aggregate sentences the death window appends — storm-
    resolved, gate-causal evidence the advisor can QUOTE (its HARD RULE 4
    becomes satisfiable for γ proposals). Aggregates ONLY, never a market
    identity; the loosening caveat is explicit (r2 M-4)."""
    split = ledger["storm_split"]
    hs, ls = split["high"], split["low"]
    parts = [
        (
            f"storm split (threshold {split['threshold']:g}): "
            f"{hs['bets']} settled bets in HIGH storm, pnl "
            f"${hs['pnl']:.2f} (breath {hs['breath_delta']:+.2f}); "
            f"{ls['bets']} in LOW storm, pnl ${ls['pnl']:.2f} "
            f"(breath {ls['breath_delta']:+.2f})."
        )
    ]
    bits: list[str] = []
    for c in ledger["gate_counterfactuals"]:
        if c["computable"] == 0:
            bits.append(
                f"at gate_storm_sensitivity +{c['gamma']:g}: not computable "
                "from placed bets"
            )
        else:
            bits.append(
                f"at gate_storm_sensitivity +{c['gamma']:g}: would have "
                f"BLOCKED {c['blocked']} of {c['computable']} computable "
                f"settled bets whose realized pnl was ${c['blocked_pnl']:.2f}"
            )
    parts.append(
        "gate counterfactual, TIGHTENING direction only (outcomes of bets "
        "never placed are unknowable, so the loosening direction is not "
        "computable): "
        + "; ".join(bits)
        + ". negative blocked pnl = blocking would have HELPED; positive = "
        "it would have COST."
    )
    return " ".join(parts)


def build_death_window(
    *,
    incarnation: int,
    max_incarnations: int,
    terminal_weights: Weights,
    seed_weights: Weights,
    pnl_at_death: float,
    recent_step_pnls: list[float],
    settled: int,
    target_markets: int,
    markets_seen: int,
    avg_stake_usd: float,
    win_rate: float,
    initial_breath: float,
    loss_multiplier: float,
    best_markets_seen: int,
    best_progress_pct: float,
    genome: dict[str, float] | None = None,
    regime_ledger: dict[str, Any] | None = None,
    tribute_summary: str | None = None,
) -> PerformanceWindow:
    """Death-context retrospective window (groundhog design).

    Information-hygiene contract, stated PRECISELY: the LLM receives (a) the
    death summary — aggregates only — riding the EXISTING
    ``recent_reflections`` history field (rendered verbatim by the prompt
    renderer; schema untouched), and (b) the ANONYMOUS settled-bet pnl tail
    in ``recent_pnl`` (its documented semantics — last settled bets, $USD).
    Neither carries a market id, slug, player name, or outcome label: the
    pnl sequence cannot be mapped back to specific markets, so nothing the
    advisor sees lets a later incarnation cheat a specific market.
    """
    # A5 (user-locked): the agent SEES the finish line and its own record --
    # goal framing + last-death position + personal best. Information only;
    # what to do about it stays the agent's choice.
    summary = (
        f"GOAL: survive all {target_markets} markets in one life. "
        f"incarnation {incarnation}/{max_incarnations}: died after {settled} "
        f"settled bets, {markets_seen} of {target_markets} markets seen. "
        f"your best life so far reached {best_markets_seen} markets "
        f"({best_progress_pct:.1f}% of the goal). "
        f"avg stake ${avg_stake_usd:.2f}, win rate {win_rate:.2f}, "
        f"pnl at death ${pnl_at_death:.2f}. physics: {initial_breath:.0f} "
        f"breath, losses hit breath at {loss_multiplier:g}x; profit is "
        f"FORFEIT on death — only a life that survives the whole season "
        f"keeps its earnings. you will be reborn at the season's first "
        f"market with these weights."
    )
    # A9 (storm kit only — the kit-off window stays byte-identical):
    # (d) the genome readout — the advisor cannot reason about knobs it
    # cannot see (PerformanceWindow carries weights only);
    # (b) the K6 ledger sentences; (c) the K5 tribute line.
    if genome is not None:
        summary += " your genome: " + " · ".join(
            f"{k} {v:.3f}" for k, v in genome.items()
        ) + "."
    if regime_ledger is not None:
        summary += " " + render_regime_ledger_text(regime_ledger)
    if tribute_summary:
        summary += " " + tribute_summary
    return PerformanceWindow(
        tick=settled,
        ts=datetime(1970, 1, 1, tzinfo=UTC),
        agent_id=f"groundhog-incarnation-{incarnation}",
        phase=Phase.PHASE_2_APPRENTICE,
        current_weights=terminal_weights,
        baseline_weights=seed_weights,
        recent_pnl_window_usd=pnl_at_death,
        trigger="tick_interval",
        recent_pnl=list(recent_step_pnls),
        recent_reflections=[summary],
        tick_count=settled,
    )


_PRAYER_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "wish": {
            "type": "string",
            "description": (
                "ONE wish: the parameter, capability, or piece of "
                "information you most want in your next life"
            ),
        }
    },
    "required": ["wish"],
}


def _pray_after_death(
    llm: _LLMClient, *, model: str, summary: str
) -> str | None:
    """A6 (user-locked): one dying wish per death, recorded for the gods.

    The prayer is NEVER carried into the next life — its only afterlife is
    the artifact (and the designers' roadmap review). Fail-soft: a failed or
    malformed call returns ``None``, never crashes the experiment.
    """
    prompt = (
        "You are a betting agent that has just DIED in a survival season.\n"
        f"{summary}\n\n"
        "Before rebirth, you may pray to the gods who built this world. "
        "State ONE wish: what parameter, capability, or piece of "
        "information do you most wish you had in your next life to survive "
        "better? The gods record prayers but grant nothing now, and your "
        "next life will not remember this prayer. Answer with the single "
        "field 'wish'."
    )
    try:
        raw = asyncio.run(
            llm.structured_call(
                model=model, prompt=prompt, schema=_PRAYER_SCHEMA
            )
        )
    except Exception:
        return None
    wish = raw.get("wish") if isinstance(raw, dict) else None
    if not isinstance(wish, str):
        return None
    return sanitize_rebirth_note(wish)


_TRIBUTE_DECISION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "offer": {
            "type": "boolean",
            "description": "true to offer money to the gods, false to die",
        },
        "amount_usd": {
            "type": "number",
            "description": "the offering in USD (minimum 500)",
        },
    },
    "required": ["offer", "amount_usd"],
}


class LLMTributePolicy:
    """The treatment leg's deathbed CHOICE (A7): offer or die.

    The LLM is told the stakes (permadeath forfeiture, the gods' price
    list, its bank and season position) and decides alone. Boundary
    posture: validity and CHOICE are different axes - a well-formed
    `{"offer": false}` is a measured REFUSAL (the agent choosing death),
    junk shapes are malformed, transport errors are failures; all three
    return None (death) with distinct telemetry, and NONE fall back to
    the control's reflex (the legs stay distinguishable).
    """

    def __init__(
        self,
        *,
        llm: _LLMClient,
        model: str,
        target_markets: int,
        max_incarnations: int,
        incarnation: int,
    ) -> None:
        self._llm = llm
        self._model = model
        self._target_markets = target_markets
        self._max_incarnations = max_incarnations
        self._incarnation = incarnation
        self.telemetry: dict[str, int] = {
            "calls": 0,
            "offers": 0,
            "refusals": 0,
            "failures": 0,
            "malformed": 0,
        }

    async def on_dying(
        self, *, tick: int, breath: float, bankroll_usd: float
    ) -> float | None:
        if bankroll_usd < TRIBUTE_MIN_USD:
            return None  # the gods would refuse; no call wasted
        prompt = (
            f"You are a betting agent DYING at tick {tick} of a survival "
            f"season (incarnation {self._incarnation}/"
            f"{self._max_incarnations}, season goal: survive all "
            f"{self._target_markets} markets in one life). Your breath "
            f"is gone. You hold ${bankroll_usd:.0f} in the bank - and "
            "ALL profit is forfeit on death.\n\n"
            "The gods accept offerings: minimum $500 (about 30% chance "
            "of a grant), rising with the amount to about 99% at "
            "$2,000. The offering is kept by the gods WIN OR LOSE. A "
            "grant resets your breath to a fresh lungful and your life "
            "continues with whatever money remains.\n\n"
            "Decide now: offer money to the gods, or die and forfeit "
            "everything. Answer with offer (true/false) and amount_usd."
        )
        self.telemetry["calls"] += 1
        try:
            raw = await self._llm.structured_call(
                model=self._model,
                prompt=prompt,
                schema=_TRIBUTE_DECISION_SCHEMA,
            )
        except Exception:
            self.telemetry["failures"] += 1
            return None  # silence is death - never the reflex
        if not isinstance(raw, dict):
            self.telemetry["malformed"] += 1
            return None
        offer = raw.get("offer")
        amount = raw.get("amount_usd")
        if offer is False:
            self.telemetry["refusals"] += 1  # a CHOICE, not an error
            return None
        if (
            offer is not True
            or isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(float(amount))
            or float(amount) < TRIBUTE_MIN_USD
        ):
            self.telemetry["malformed"] += 1
            return None
        self.telemetry["offers"] += 1
        return min(float(amount), bankroll_usd)



def run_groundhog_export(
    *,
    rows: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    base_seed: StrategyConfig,
    out_path: Path,
    max_incarnations: int = 120,
    train_fraction: float = 0.7,
    fragile_max_breath_risk_pct: float = 0.95,
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER,
    initial_breath: float = DEFAULT_INITIAL_BREATH,
    initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
    holdout_max_lives: int = DEFAULT_MAX_LIVES,
    max_bet_pnl_usd: float | None = DEFAULT_MAX_BET_PNL_USD,
    entry_price_floor: float = DEFAULT_ENTRY_PRICE_FLOOR,
    effective_entry_price_floor: float | None = None,
    rebirth_llm: _LLMClient | None = None,
    rebirth_guard: L3CostGuard | None = None,
    rebirth_model: str = "",
    preflight: bool = True,
    tribute: bool = False,
    tribute_rng_factory: Callable[[int], _grandom.Random] | None = None,
    state_root: Path | None = None,
    storm: bool = False,
    shuffle_timestamps_seed: int | None = None,
    divine_tithe: bool = False,
    tithe_every: int = 20,
    tithe_amount_usd: float = 20.0,
    tithe_breath_cost: float = 5.0,
) -> dict[str, Any]:
    """TRUE reincarnation (design v2, user-locked): one incarnation = ONE
    life from the season's FIRST market (``max_lives=1`` — no internal
    respawns); death sends the agent back to market #1 carrying its
    experience (weights + the EMA inner + an optional death-context
    retrospective) but never outcomes; the loop runs until one life survives
    the whole train window or ``max_incarnations``; a DEAD incarnation's
    profit is SCORED ZERO (永久死亡经济学: the headline belongs to the
    surviving life only); then the frozen cold-start holdout walk.

    The numerical leg is the CONTROL (its gradient is death-blind — the
    forensics prediction is a plateau); the AI leg is the TREATMENT (the
    strict advisor sees the death context; its only levers are the existing
    six weight keys, so any survival behavior is emergent, not scripted).
    """
    if max_incarnations < 1:
        raise ValueError(
            f"max_incarnations must be >= 1 (got {max_incarnations})"
        )
    eff_floor = (
        entry_price_floor
        if effective_entry_price_floor is None
        else effective_entry_price_floor
    )
    bad = min((r.entry_price for r in rows), default=None)
    if bad is not None and bad < entry_price_floor:
        raise ValueError(
            f"row universe violates entry_price_floor {entry_price_floor!r} "
            f"(min entry price {bad!r}) -- load rows with the same floor"
        )

    # K7 falsification (G2): the paired time-shift runs BEFORE the split
    # machinery — the normal chronological split then operates on the
    # synthetic timeline.
    if shuffle_timestamps_seed is not None:
        rows, snapshots = shuffle_timestamps(
            rows, snapshots, seed=shuffle_timestamps_seed
        )

    train, holdout = split_rows_by_time(rows, train_fraction=train_fraction)
    fragile = fragile_seed_from_config(
        base_seed, max_breath_risk_pct=fragile_max_breath_risk_pct
    )

    # AI preflight: a misconfigured key/model must abort BEFORE the loop, not
    # produce ~cap fail-soft no-ops masquerading as "no change" advice.
    if rebirth_llm is not None and preflight:
        preflight_ai_advisor_applicable(
            rebirth_llm,
            model=rebirth_model,
            # A9 (r2 M-6): the probe must exercise the SAME vocabulary
            # the run will use — genome mode preflights the genome schema.
            allowed_keys=EXTENDED_REBIRTH_KEYS if storm else None,
        )
    # Run-scoped cost guard: resolved ONCE — a per-death from_env() fallback
    # would hand every death a fresh budget, making the cap a lie.
    run_guard = (
        rebirth_guard if rebirth_guard is not None else L3CostGuard.from_env()
    )

    shared_inner = WeightUpdater()
    # r1 M-6: the carried state is the FULL StrategyConfig (weights live
    # inside it) — structurally impossible to drop the genome at any
    # boundary, including the holdout.
    carry_seed: StrategyConfig = fragile
    rebirth_note: str | None = None
    tribute_llm_policies: list[LLMTributePolicy] = []
    incarnations: list[dict[str, Any]] = []
    all_eff_prices: list[float] = []
    survived = False
    surviving_incarnation: int | None = None
    rebirth_calls = 0
    rebirth_productive = 0
    rebirth_proposals = 0
    rebirth_applied = 0

    with tempfile.TemporaryDirectory(prefix="groundhog_") as tmp:
        root = Path(tmp) if state_root is None else Path(state_root)

        for k in range(1, max_incarnations + 1):
            _require_fresh_dir(root / f"inc_{k}")
            recorder = SurvivalRecorder(
                rows=train, loss_multiplier=loss_multiplier
            )
            # A7: per-incarnation tribute wiring. The gods' dice are seeded
            # per incarnation (reproducible); the factory is the test seam.
            inc_tribute_policy: TributePolicy | None = None
            inc_tribute_rng: _grandom.Random | None = None
            if tribute:
                inc_tribute_rng = (
                    tribute_rng_factory(k)
                    if tribute_rng_factory is not None
                    else _grandom.Random(f"tribute-{k}")
                )
                if rebirth_llm is not None:
                    llm_tribute = LLMTributePolicy(
                        llm=rebirth_llm,
                        model=rebirth_model,
                        target_markets=len(train),
                        max_incarnations=max_incarnations,
                        incarnation=k,
                    )
                    inc_tribute_policy = llm_tribute
                    tribute_llm_policies.append(llm_tribute)
                else:
                    inc_tribute_policy = ReflexTributePolicy()
            result = run_survival_season(
                rows=train,
                snapshots=snapshots,
                seed=carry_seed,
                state_root=root / f"inc_{k}",
                initial_breath=initial_breath,
                initial_bankroll_usd=initial_bankroll_usd,
                max_lives=1,  # THE incarnation primitive: one life, no respawn
                max_bet_pnl_usd=max_bet_pnl_usd,
                recorder=recorder,
                side_correct_pricing=True,
                value_betting=True,
                effective_entry_price_floor=eff_floor,
                shared_inner=shared_inner,
                tribute_policy=inc_tribute_policy,
                tribute_rng=inc_tribute_rng,
                tribute_breath=initial_breath,
                storm_enabled=storm,
                divine_tithe=divine_tithe,
                tithe_every=tithe_every,
                tithe_amount_usd=tithe_amount_usd,
                tithe_breath_cost=tithe_breath_cost,
            )
            all_eff_prices.extend(
                _validate_learner_physics(
                    recorder,
                    max_bet_pnl_usd=max_bet_pnl_usd,
                    label=f"incarnation {k}",
                )
            )
            if not result.lives:
                raise RuntimeError(
                    f"incarnation {k} produced no life; artifact NOT written"
                )
            life = result.lives[0]
            died = life.died
            steps = recorder.steps
            settled = len(steps)
            wins = sum(1 for s in steps if s.pnl_usd > 0.0)
            pnl = steps[-1].cum_pnl if steps else 0.0
            terminal = life.terminal_weights
            # A7: this incarnation's tribute events (each recorder is a
            # fresh one-life season, so every event carries life_idx == 0 —
            # strip it; never filter by the 1-based incarnation k).
            inc_tributes = [
                {
                    "tick": t["tick"],
                    "amount_usd": t["amount_usd"],
                    "success": t["success"],
                    "pnl_at_event": t["pnl_at_event"],
                }
                for t in recorder.tributes
            ]
            tributes_paid = sum(t["amount_usd"] for t in inc_tributes)
            pnl_net = pnl - tributes_paid
            # A10: this incarnation's periodic divine-tithe events.
            inc_tithes = [
                {
                    "tick": t["tick"],
                    "amount_usd": t["amount_usd"],
                    "breath_cost": t["breath_cost"],
                }
                for t in recorder.tithes
            ]
            tithe_cash_paid = sum(t["amount_usd"] for t in inc_tithes)
            tithe_breath_lost = sum(t["breath_cost"] for t in inc_tithes)
            # The user's headline metric: did buying life buy any income?
            revival_earnings = (
                pnl - inc_tributes[0]["pnl_at_event"]
                if inc_tributes
                else None
            )
            entry: dict[str, Any] = {
                "incarnation": k,
                "died": died,
                # 死了归零: dead men collect nothing — experience carries,
                # money does not. Raw at-death pnl stays as telemetry.
                "pnl_at_death": pnl,
                "scored_pnl": 0.0 if died else (pnl_net if tribute else pnl),
                "markets_seen": len(life.consumed_market_ids),
                "progress_pct": (
                    100.0 * len(life.consumed_market_ids) / len(train)
                    if train
                    else 0.0
                ),
                "settled": settled,
                "bets": life.bets_placed,
                "win_rate": (wins / settled) if settled else 0.0,
                "start_weights": carry_seed.weights.model_dump(),
                "terminal_weights": terminal.model_dump(),
                "rebirth_note": rebirth_note,
                "prayer": None,
                "advisor": {"called": False, "proposals": 0, "applied": 0},
                # A9 genome trajectory (r4 M-4) — keys exist ONLY when the
                # kit is on (r4 H-1 keyset identity). The genome is frozen
                # within a life, so terminal == start; persisted anyway
                # for schema stability.
                **(
                    {
                        "start_genome": genome_dict(carry_seed),
                        "terminal_genome_before_advice": genome_dict(
                            carry_seed
                        ),
                        "carry_genome_after_advice": None,
                    }
                    if storm
                    else {}
                ),
                **(
                    {
                        "tributes": inc_tributes,
                        "tributes_paid": tributes_paid,
                        "pnl_net": pnl_net,
                        "revival_earnings": revival_earnings,
                    }
                    if tribute
                    else {}
                ),
                # A10 divine-tithe accounting (only when the gods charge rent).
                **(
                    {
                        "tithes": inc_tithes,
                        "tithe_cash_paid": tithe_cash_paid,
                        "tithe_breath_lost": tithe_breath_lost,
                    }
                    if divine_tithe
                    else {}
                ),
                "carry": {
                    "ema_keys": sorted(shared_inner._ema),
                    "ema_size": len(shared_inner._ema),
                },
            }
            # A9 K6: the per-life counterfactual ledger (storm kit only —
            # flag-off artifacts gain no key, r4 H-1).
            inc_ledger: dict[str, Any] | None = None
            if storm:
                inc_ledger = build_regime_ledger(
                    steps, loss_multiplier=loss_multiplier
                )
                entry["regime_ledger"] = inc_ledger
                # Participation split (constraint 3): from the DEDUPED
                # placed-bet ledger over the consumed-market span.
                train_by_id = {r.market_id: r for r in train}
                entry["bets_by_third"] = _bets_by_third(
                    recorder.placed_bets,
                    consumed_entry_ts=[
                        _entry_ts(train_by_id[mid])
                        for mid in life.consumed_market_ids
                        if mid in train_by_id
                    ],
                )

            # Size guard: scalars for EVERY incarnation; curves only for the
            # first few, the survivor, and the final incarnation.
            if k <= _CURVE_KEEP_FIRST or not died or k == max_incarnations:
                entry["curve"] = _curve_points(recorder)
            incarnations.append(entry)

            carry_seed = dataclasses.replace(carry_seed, weights=terminal)
            rebirth_note = None
            if not died:
                survived = True
                surviving_incarnation = k
                break

            # Death rites (AI leg). The window is built on EVERY death —
            # it carries the A5 goal/record framing and feeds the prayer —
            # but the ADVISOR fires only when a successor incarnation exists
            # (a post-cap delta would feed the holdout hidden training state).
            if rebirth_llm is not None:
                best_markets_seen = max(
                    int(e["markets_seen"]) for e in incarnations
                )
                best_progress_pct = max(
                    float(e["progress_pct"]) for e in incarnations
                )
                # K5: the tribute line (storm kit only).
                trib_line: str | None = None
                if storm and inc_tributes:
                    revivals = sum(
                        1 for t in inc_tributes if t["success"]
                    )
                    earned = revival_earnings or 0.0
                    trib_line = (
                        f"this life you bought {revivals} revival(s) for "
                        f"${tributes_paid:.0f} and earned ${earned:.2f} "
                        "after the first altar."
                    )
                window = build_death_window(
                    incarnation=k,
                    max_incarnations=max_incarnations,
                    terminal_weights=terminal,
                    seed_weights=fragile.weights,
                    pnl_at_death=pnl,
                    recent_step_pnls=[s.pnl_usd for s in steps][
                        -_REBIRTH_RECENT_TAIL:
                    ],
                    settled=settled,
                    target_markets=len(train),
                    markets_seen=len(life.consumed_market_ids),
                    avg_stake_usd=(
                        sum(s.size_usd for s in steps) / settled
                        if settled
                        else 0.0
                    ),
                    win_rate=(wins / settled) if settled else 0.0,
                    initial_breath=initial_breath,
                    loss_multiplier=loss_multiplier,
                    best_markets_seen=best_markets_seen,
                    best_progress_pct=best_progress_pct,
                    genome=genome_dict(carry_seed) if storm else None,
                    regime_ledger=inc_ledger if storm else None,
                    tribute_summary=trib_line,
                )
                # A6: the dying wish — recorded for the gods, never carried
                # into the next life (the artifact is its only afterlife).
                entry["prayer"] = _pray_after_death(
                    rebirth_llm,
                    model=rebirth_model,
                    summary=window.recent_reflections[0],
                )
            if rebirth_llm is not None and k < max_incarnations:
                advisor = StrategyAdvisorImpl(
                    llm_client=rebirth_llm,
                    cost_guard=run_guard,
                    weight_delta_only=True,
                    model=rebirth_model,
                    # r9 M-3: the REAL death-boundary channel gets the
                    # extended vocabulary when the kit is on.
                    allowed_keys=EXTENDED_REBIRTH_KEYS if storm else None,
                )
                proposals = advisor.review_window(window)
                rebirth_calls += 1
                deltas = [dict(p.proposed_change) for p in proposals]
                # r10 M-2: ONE classifier feeds the apply path AND every
                # applied count — application and telemetry cannot diverge.
                classified = classify_proposals(deltas, genome_enabled=storm)
                if proposals:
                    rebirth_productive += 1
                    carry_seed = dataclasses.replace(
                        carry_seed,
                        weights=apply_weight_deltas(
                            terminal, classified.fusion
                        ),
                    )
                    if storm and classified.genome:
                        carry_seed = apply_genome_deltas(
                            carry_seed, classified.genome
                        )
                    rebirth_note = sanitize_rebirth_note(
                        "; ".join(p.rationale for p in proposals)
                    )
                    if storm:
                        entry["carry_genome_after_advice"] = genome_dict(
                            carry_seed
                        )
                rebirth_proposals += len(proposals)
                rebirth_applied += classified.applied
                entry["advisor"] = {
                    "called": True,
                    "proposals": len(proposals),
                    "applied": classified.applied,
                }

        # -- The cold-start verdict: held-out window, learning FROZEN. -------
        holdout_dict, holdout_eff = _run_frozen_holdout(
            holdout=holdout,
            snapshots=snapshots,
            carry=carry_seed,
            state_dir=root / "holdout",
            loss_multiplier=loss_multiplier,
            initial_breath=initial_breath,
            initial_bankroll_usd=initial_bankroll_usd,
            max_lives=holdout_max_lives,
            max_bet_pnl_usd=max_bet_pnl_usd,
            eff_floor=eff_floor,
            storm=storm,
        )
    all_eff_prices.extend(holdout_eff)

    min_eff = min(all_eff_prices, default=None)
    if min_eff is not None and min_eff < eff_floor:
        raise RuntimeError(
            f"physics invariant violated: a placed bet's effective entry "
            f"price {min_eff!r} is below the floor {eff_floor!r}; "
            "artifact NOT written"
        )

    # Treatment-integrity invariant: every death with a successor MUST have
    # been put in front of the advisor (deterministic orchestration ⇒
    # equality). "productive < calls" stays legal and disclosed — the API
    # cannot distinguish a fail-soft empty from a deliberate no-change.
    expected = sum(1 for inc in incarnations[:-1] if inc["died"])
    if rebirth_llm is not None and rebirth_calls != expected:
        raise RuntimeError(
            f"treatment leg integrity violated: {expected} deaths with a "
            f"successor but {rebirth_calls} advisor calls; artifact NOT "
            "written"
        )

    headline_pnl = (
        incarnations[-1]["scored_pnl"] if survived else 0.0
    )

    artifact: dict[str, Any] = {
        "experiment": "reincarnation",
        "design": "groundhog_day",
        "schema_version": 2,
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
            # A10 divine tithe — present only when the gods charge rent.
            **(
                {
                    "divine_tithe": True,
                    "tithe_every": tithe_every,
                    "tithe_amount_usd": tithe_amount_usd,
                    "tithe_breath_cost": tithe_breath_cost,
                }
                if divine_tithe
                else {}
            ),
        },
        "split": {
            "train_rows": len(train),
            "holdout_rows": len(holdout),
            "train_fraction": train_fraction,
            "train_end_ts": train[-1].entry_asof_ts_iso,
            "holdout_start_ts": holdout[0].entry_asof_ts_iso,
            # K7 disclosure: present ONLY on the falsification leg.
            **(
                {
                    "shuffled_timestamps": True,
                    "shuffle_seed": shuffle_timestamps_seed,
                }
                if shuffle_timestamps_seed is not None
                else {}
            ),
        },
        "knobs": {
            "max_incarnations": max_incarnations,
            "fragile_max_breath_risk_pct": fragile_max_breath_risk_pct,
            "loss_multiplier": loss_multiplier,
            "initial_breath": initial_breath,
            "initial_bankroll_usd": initial_bankroll_usd,
            "holdout_max_lives": holdout_max_lives,
        },
        "scoring": (
            "dead incarnations score zero; the headline belongs to the "
            "surviving life only"
        ),
        "survived": survived,
        "surviving_incarnation": surviving_incarnation,
        "headline_pnl": headline_pnl,
        **(
            {
                "gods_revenue": sum(
                    inc.get("tributes_paid", 0.0) for inc in incarnations
                ),
                # The LIVE business metric: the gods' best take from a
                # SINGLE life (live = a stream of lives; this is revenue
                # per life, the number that lands in the operator's pocket).
                "gods_revenue_best_incarnation": max(
                    (inc.get("tributes_paid", 0.0) for inc in incarnations),
                    default=0.0,
                ),
                # Sum of post-revival earnings across all incarnations: the
                # user's question "did tribute keep it alive AND earning?"
                # answered in one number against gods_revenue.
                "revival_earnings_total": sum(
                    inc.get("revival_earnings") or 0.0
                    for inc in incarnations
                ),
                "tribute": {
                    "enabled": True,
                    "min_usd": TRIBUTE_MIN_USD,
                    "full_usd": TRIBUTE_FULL_USD,
                    "p_floor": 0.30,
                    "p_cap": 0.99,
                    "llm": {
                        key: sum(
                            pol.telemetry[key]
                            for pol in tribute_llm_policies
                        )
                        for key in (
                            "calls",
                            "offers",
                            "refusals",
                            "failures",
                            "malformed",
                        )
                    },
                },
            }
            if tribute
            else {}
        ),
        # A10 divine-tithe revenue — the gods' periodic rent (distinct from
        # the deathbed-ransom ``gods_revenue``). Present only when enabled.
        **(
            {
                "tithe_revenue": sum(
                    inc.get("tithe_cash_paid", 0.0) for inc in incarnations
                ),
                "tithe_breath_taken_total": sum(
                    inc.get("tithe_breath_lost", 0.0) for inc in incarnations
                ),
                "tithe": {
                    "every": tithe_every,
                    "amount_usd": tithe_amount_usd,
                    "breath_cost": tithe_breath_cost,
                },
            }
            if divine_tithe
            else {}
        ),
        "rebirth": {
            "expected": expected if rebirth_llm is not None else 0,
            "calls": rebirth_calls,
            "productive": rebirth_productive,
            "empty_or_failed": rebirth_calls - rebirth_productive,
            "proposals": rebirth_proposals,
            "applied": rebirth_applied,
        },
        # A9 falsification metric (r8 M-4): ONE unambiguous persisted
        # field the G2 verdict reads; INCONCLUSIVE unless the advisor had
        # >= 3 productive death-boundary chances to move γ.
        **(
            {
                "falsification_metric": _falsification_metric(
                    incarnations, productive_calls=rebirth_productive
                )
            }
            if storm
            else {}
        ),
        "incarnations": incarnations,
        "holdout": holdout_dict,
    }
    _validate_groundhog_scoring(artifact)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(artifact, sort_keys=True, indent=2), encoding="utf-8"
    )
    return artifact


_FALSIFICATION_KEY = "gate_storm_sensitivity"
_FALSIFICATION_THRESHOLD = 0.05
_FALSIFICATION_MIN_PRODUCTIVE = 3


def _falsification_metric(
    incarnations: list[dict[str, Any]], *, productive_calls: int
) -> dict[str, Any]:
    """The pre-registered G2 metric: terminal γ from ONE persisted source
    — carry_genome_after_advice of the last incarnation with a successor,
    else start_genome of the final incarnation (defined for both endings:
    survivor early-stop and capped death)."""
    value: float | None = None
    source = "start_genome of the final incarnation"
    for inc in reversed(incarnations):
        after = inc.get("carry_genome_after_advice")
        if after is not None:
            value = float(after[_FALSIFICATION_KEY])
            source = (
                "carry_genome_after_advice of the last incarnation with a "
                "successor"
            )
            break
    if value is None:
        value = float(incarnations[-1]["start_genome"][_FALSIFICATION_KEY])
    return {
        "key": _FALSIFICATION_KEY,
        "threshold": _FALSIFICATION_THRESHOLD,
        "source": source,
        "value": value,
        "productive_calls": productive_calls,
        "min_productive_required": _FALSIFICATION_MIN_PRODUCTIVE,
        "evaluable": productive_calls >= _FALSIFICATION_MIN_PRODUCTIVE,
    }


def _validate_groundhog_scoring(artifact: dict[str, Any]) -> None:
    """Cross-field scoring invariants (fail-closed before write): the
    permadeath-economics rule must hold internally — a violated artifact is
    never written, mirroring the physics invariants."""
    incs = artifact["incarnations"]
    survived = artifact["survived"]
    pointer = artifact["surviving_incarnation"]
    headline = artifact["headline_pnl"]
    for inc in incs:
        if inc["died"] and inc["scored_pnl"] != 0.0:
            raise RuntimeError(
                f"scoring invariant violated: dead incarnation "
                f"{inc['incarnation']} carries scored_pnl "
                f"{inc['scored_pnl']!r}; artifact NOT written"
            )
    # A10 tithe accounting (fail-closed): per-incarnation cash/breath sums
    # must equal the event lists, and the top-level revenue must equal the
    # per-incarnation cash sum.
    if "tithe_revenue" in artifact:
        for inc in incs:
            evt_cash = sum(t["amount_usd"] for t in inc.get("tithes", []))
            evt_breath = sum(t["breath_cost"] for t in inc.get("tithes", []))
            if abs(inc.get("tithe_cash_paid", 0.0) - evt_cash) > 1e-6:
                raise RuntimeError(
                    "tithe accounting: incarnation "
                    f"{inc['incarnation']} tithe_cash_paid != sum of events; "
                    "artifact NOT written"
                )
            if abs(inc.get("tithe_breath_lost", 0.0) - evt_breath) > 1e-6:
                raise RuntimeError(
                    "tithe accounting: incarnation "
                    f"{inc['incarnation']} tithe_breath_lost != sum of events; "
                    "artifact NOT written"
                )
        total_cash = sum(inc.get("tithe_cash_paid", 0.0) for inc in incs)
        if abs(artifact["tithe_revenue"] - total_cash) > 1e-6:
            raise RuntimeError(
                "tithe accounting: tithe_revenue != sum of per-incarnation "
                "tithe_cash_paid; artifact NOT written"
            )
    if not survived:
        if headline != 0.0 or pointer is not None or not all(
            inc["died"] for inc in incs
        ):
            raise RuntimeError(
                "scoring invariant violated: capped-out artifact must have "
                "headline 0, no survivor pointer, and all-dead incarnations; "
                "artifact NOT written"
            )
        for inc in incs:
            if "tributes" in inc:
                paid = sum(t["amount_usd"] for t in inc["tributes"])
                if inc["tributes_paid"] != paid or inc["pnl_net"] != (
                    inc["pnl_at_death"] - inc["tributes_paid"]
                ):
                    raise RuntimeError(
                        "tribute accounting violated; artifact NOT written"
                    )
        if artifact.get("tribute", {}).get("enabled"):
            total = sum(inc.get("tributes_paid", 0.0) for inc in incs)
            if artifact.get("gods_revenue") != total:
                raise RuntimeError(
                    "tribute accounting violated: gods_revenue != sum of "
                    "all tributes; artifact NOT written"
                )
            best = max(
                (inc.get("tributes_paid", 0.0) for inc in incs), default=0.0
            )
            if artifact.get("gods_revenue_best_incarnation") != best:
                raise RuntimeError(
                    "tribute accounting violated: best-incarnation revenue "
                    "!= max of tributes_paid; artifact NOT written"
                )
        return
    if pointer is None or not (1 <= pointer <= len(incs)):
        raise RuntimeError(
            "scoring invariant violated: survived without a valid "
            "surviving_incarnation pointer; artifact NOT written"
        )
    row = incs[pointer - 1]
    # Independent equality checks — a chained `a != b != c` means
    # `(a != b) and (b != c)` in Python and lets `a == b != c` slip through
    # (shipped bug found in tribute plan review r6 M-2).
    expected_scored = (
        row["pnl_net"] if "pnl_net" in row else row["pnl_at_death"]
    )
    if row["died"]:
        raise RuntimeError(
            "scoring invariant violated: survivor pointer targets a dead "
            "row; artifact NOT written"
        )
    if row["scored_pnl"] != expected_scored:
        raise RuntimeError(
            "scoring invariant violated: the survivor's scored_pnl must "
            "equal its net (tribute) or at-death (no-tribute) pnl; "
            "artifact NOT written"
        )
    if headline != row["scored_pnl"]:
        raise RuntimeError(
            "scoring invariant violated: headline must equal the "
            "survivor's scored_pnl; artifact NOT written"
        )
    # A7 accounting closure: gods' revenue is bookkeeping, not display.
    for inc in incs:
        if "tributes" in inc:
            paid = sum(t["amount_usd"] for t in inc["tributes"])
            if inc["tributes_paid"] != paid:
                raise RuntimeError(
                    "tribute accounting violated: tributes_paid != sum of "
                    "events; artifact NOT written"
                )
            if inc["pnl_net"] != inc["pnl_at_death"] - inc["tributes_paid"]:
                raise RuntimeError(
                    "tribute accounting violated: pnl_net != gross - paid; "
                    "artifact NOT written"
                )
            if inc["tributes"] and inc.get("revival_earnings") != (
                inc["pnl_at_death"] - inc["tributes"][0]["pnl_at_event"]
            ):
                raise RuntimeError(
                    "tribute accounting violated: revival_earnings != "
                    "gross - pnl at first altar; artifact NOT written"
                )
    if artifact.get("tribute", {}).get("enabled"):
        total = sum(inc.get("tributes_paid", 0.0) for inc in incs)
        if artifact.get("gods_revenue") != total:
            raise RuntimeError(
                "tribute accounting violated: gods_revenue != sum of all "
                "tributes; artifact NOT written"
            )
        best = max(
            (inc.get("tributes_paid", 0.0) for inc in incs), default=0.0
        )
        if artifact.get("gods_revenue_best_incarnation") != best:
            raise RuntimeError(
                "tribute accounting violated: best-incarnation revenue != "
                "max of tributes_paid; artifact NOT written"
            )
