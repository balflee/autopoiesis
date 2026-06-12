"""Phase-2 reincarnation experiment: time split, weight-delta application,
rebirth window, note sanitization, and the multi-pass export."""

from __future__ import annotations

import dataclasses

import pytest

from agent.backtest.reincarnation import split_rows_by_time
from agent.backtest.survival_season import SurvivalRow

# Reuse the survival-season test fixture for SurvivalRow construction (the
# established cross-file private-helper idiom in this suite).
from tests.agent.backtest.test_survival_season import _survival_row


def _row_at(ts: str, market_id: str) -> SurvivalRow:
    row = _survival_row(market_id=market_id, entry_price=0.5, outcome="yes")
    # _survival_row pins a fixed ts; rebuild with the desired one.
    return dataclasses.replace(row, entry_asof_ts_iso=ts)


def test_split_rows_by_time_orders_then_splits() -> None:
    rows = [
        _row_at("2025-06-01T00:00:00+00:00", "m_b"),
        _row_at("2024-01-01T00:00:00+00:00", "m_a"),
        _row_at("2026-01-01T00:00:00+00:00", "m_c"),
        _row_at("2025-12-01T00:00:00+00:00", "m_d"),
    ]
    train, holdout = split_rows_by_time(rows, train_fraction=0.5)
    assert [r.market_id for r in train] == ["m_a", "m_b"]
    assert [r.market_id for r in holdout] == ["m_d", "m_c"]
    # No leakage: every train entry STRICTLY precedes every holdout entry.
    assert max(r.entry_asof_ts_iso for r in train) < min(
        r.entry_asof_ts_iso for r in holdout
    )


def test_split_keeps_tied_timestamps_on_the_train_side() -> None:
    """r1 M-3: equal-time markets must never straddle the boundary — ties at
    the cut are pulled INTO train so holdout starts strictly later."""
    tie = "2025-06-01T00:00:00+00:00"
    rows = [
        _row_at("2024-01-01T00:00:00+00:00", "m_a"),
        _row_at(tie, "m_b"),
        _row_at(tie, "m_c"),
        _row_at("2026-01-01T00:00:00+00:00", "m_d"),
    ]
    train, holdout = split_rows_by_time(rows, train_fraction=0.5)
    assert [r.market_id for r in train] == ["m_a", "m_b", "m_c"]
    assert [r.market_id for r in holdout] == ["m_d"]
    assert max(r.entry_asof_ts_iso for r in train) < min(
        r.entry_asof_ts_iso for r in holdout
    )


def test_split_rejects_degenerate_fractions_and_all_tied_rows() -> None:
    rows = [
        _row_at("2024-01-01T00:00:00+00:00", "m_a"),
        _row_at("2025-01-01T00:00:00+00:00", "m_b"),
    ]
    with pytest.raises(ValueError):
        split_rows_by_time(rows, train_fraction=0.0)
    with pytest.raises(ValueError):
        split_rows_by_time(rows, train_fraction=1.0)
    with pytest.raises(ValueError):
        split_rows_by_time([rows[0]], train_fraction=0.5)
    # Tie-absorption exhausting the holdout is degenerate, not silent.
    tied = [_row_at("2025-01-01T00:00:00+00:00", f"m_{i}") for i in range(4)]
    with pytest.raises(ValueError):
        split_rows_by_time(tied, train_fraction=0.5)
