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

from datetime import datetime

from agent.backtest.survival_season import SurvivalRow

__all__ = [
    "split_rows_by_time",
]


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
