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

import re
from datetime import UTC, datetime

from agent.backtest.survival_season import SurvivalRow
from agent.core.state import Phase, Weights
from agent.engines._performance_window import PerformanceWindow

__all__ = [
    "apply_weight_deltas",
    "build_rebirth_window",
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
