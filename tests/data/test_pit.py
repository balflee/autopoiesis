"""Tests for the point-in-time chokepoint :func:`assert_no_lookahead`.

Two engines, one chokepoint — every test covers the pandas branch
AND the polars branch to guarantee they're behaviourally identical.

Per PRD §14.1: any row with ``available_at > asof_ts`` is a leak.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import polars as pl
import pytest

from data.etl.pit_correct import LookaheadError, assert_no_lookahead

T_GAME = datetime(2026, 4, 12, 19, 30, tzinfo=timezone.utc)


def _pandas_frame(rows: list[datetime]) -> pd.DataFrame:
    return pd.DataFrame({"available_at": rows, "value": list(range(len(rows)))})


def _polars_frame(rows: list[datetime]) -> pl.DataFrame:
    return pl.DataFrame({"available_at": rows, "value": list(range(len(rows)))})


# ----------------------------------------------------------------------
# asof_ts validation — naive / None timestamps are themselves leaks.
# ----------------------------------------------------------------------


def test_assert_no_lookahead_rejects_naive_asof() -> None:
    naive = datetime(2026, 4, 12, 19, 30)  # no tzinfo
    df = _pandas_frame([T_GAME - timedelta(hours=1)])
    with pytest.raises(LookaheadError, match="timezone-aware"):
        assert_no_lookahead(df, naive)


def test_assert_no_lookahead_rejects_none_asof() -> None:
    df = _pandas_frame([T_GAME - timedelta(hours=1)])
    with pytest.raises(LookaheadError, match="required"):
        assert_no_lookahead(df, None)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Happy paths — all rows safely before asof_ts.
# ----------------------------------------------------------------------


def test_assert_no_lookahead_pandas_clean_passes() -> None:
    df = _pandas_frame([T_GAME - timedelta(hours=2), T_GAME - timedelta(hours=1)])
    assert_no_lookahead(df, T_GAME)  # no raise


def test_assert_no_lookahead_polars_clean_passes() -> None:
    df = _polars_frame([T_GAME - timedelta(hours=2), T_GAME - timedelta(hours=1)])
    assert_no_lookahead(df, T_GAME)  # no raise


def test_assert_no_lookahead_pandas_empty_passes() -> None:
    df = pd.DataFrame({"available_at": pd.to_datetime([], utc=True), "value": []})
    assert_no_lookahead(df, T_GAME)  # vacuously PIT-correct


def test_assert_no_lookahead_polars_empty_passes() -> None:
    df = pl.DataFrame(
        schema={"available_at": pl.Datetime(time_zone="UTC"), "value": pl.Int64}
    )
    assert_no_lookahead(df, T_GAME)


# ----------------------------------------------------------------------
# Leak detection — single row past asof_ts must raise.
# ----------------------------------------------------------------------


def test_assert_no_lookahead_pandas_single_leak_raises() -> None:
    df = _pandas_frame([T_GAME - timedelta(hours=1), T_GAME + timedelta(minutes=5)])
    with pytest.raises(LookaheadError) as excinfo:
        assert_no_lookahead(df, T_GAME)
    assert "1 row" in str(excinfo.value)
    assert "earliest offender" in str(excinfo.value)


def test_assert_no_lookahead_polars_single_leak_raises() -> None:
    df = _polars_frame([T_GAME - timedelta(hours=1), T_GAME + timedelta(minutes=5)])
    with pytest.raises(LookaheadError) as excinfo:
        assert_no_lookahead(df, T_GAME)
    assert "1 row" in str(excinfo.value)


def test_assert_no_lookahead_pandas_multi_leak_counts() -> None:
    df = _pandas_frame(
        [
            T_GAME - timedelta(hours=1),
            T_GAME + timedelta(minutes=5),
            T_GAME + timedelta(hours=2),
        ]
    )
    with pytest.raises(LookaheadError) as excinfo:
        assert_no_lookahead(df, T_GAME)
    assert "2 row" in str(excinfo.value)


# ----------------------------------------------------------------------
# Schema validation — missing column must raise.
# ----------------------------------------------------------------------


def test_assert_no_lookahead_pandas_missing_column_raises() -> None:
    df = pd.DataFrame({"some_other_col": [1, 2]})
    with pytest.raises(LookaheadError, match="available_at"):
        assert_no_lookahead(df, T_GAME)


def test_assert_no_lookahead_polars_missing_column_raises() -> None:
    df = pl.DataFrame({"some_other_col": [1, 2]})
    with pytest.raises(LookaheadError, match="available_at"):
        assert_no_lookahead(df, T_GAME)


def test_assert_no_lookahead_unknown_engine_raises_type_error() -> None:
    """A non-pandas, non-polars object is a programming error — raise TypeError."""
    with pytest.raises(TypeError, match="pandas.DataFrame or polars.DataFrame"):
        assert_no_lookahead({"available_at": [T_GAME]}, T_GAME)


def test_assert_no_lookahead_polars_string_column_raises() -> None:
    """Stringly-typed timestamps are a discipline violation — caller must cast."""
    df = pl.DataFrame({"available_at": ["2026-04-12T19:30:00+00:00"]})
    with pytest.raises(LookaheadError, match="Datetime dtype"):
        assert_no_lookahead(df, T_GAME)
