"""Point-in-time-correctness chokepoint.

This module exposes :func:`assert_no_lookahead`, the single cross-track
contract Track B (live decision loop) and Track C (calibration replay)
both call before feeding a feature row to a model. Per PRD §14.1:

    When training/calibrating against a game on date D, only features
    available before D may be used.

The function accepts either a :class:`pandas.DataFrame` or a
:class:`polars.DataFrame` exposing an ``available_at`` column of
timezone-aware datetimes, plus an ``asof_ts`` cutoff, and raises
:class:`LookaheadError` if any row has ``available_at > asof_ts``.

Sprint_2 lands the real body. The mypy surface here is the contract
Tracks B and C lock against; the ML look-ahead reviewer wires its grep
against THIS function so calibration sweeps + the live agent loop both
fail fast on the same chokepoint.

Hard rules covered:

* Both ``pandas.DataFrame`` and ``polars.DataFrame`` branches are
  typed for ``mypy --strict`` — see the ``_AvailableAtSeries``
  Protocol below; we never reach for ``typing.Any`` at the public
  surface.
* ``asof_ts`` must be timezone-aware. A naive datetime is itself a
  look-ahead-discipline violation (the auditor cannot reason about
  it) and raises :class:`LookaheadError`.
* Missing ``available_at`` column raises :class:`LookaheadError`
  (the auditor flags absent PIT metadata identically to a leak —
  both end with the model seeing bad data).
* Empty DataFrames are a no-op: vacuously PIT-correct.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type-check-only imports
    import pandas as pd
    import polars as pl

    DataFrameLike = pd.DataFrame | pl.DataFrame


class LookaheadError(ValueError):
    """Raised when a feature row exceeds the as-of cutoff.

    Per PRD §14.1 + the framework's ML look-ahead auditor, hitting this
    error in production is a Tier-1 escalation. Catching it inside an
    ETL stage is the normal path — surface the offending rows in the
    error message so the operator can drop or repair them.
    """


# Back-compat alias for T-E-001 sprint_1 callers (smoke tests + any
# Track B import that pinned the old name). New code should prefer
# :class:`LookaheadError`; this alias stays until the framework's
# interface_diff drops the old symbol.
LookaheadBiasError = LookaheadError


def require_asof_ts(asof_ts: datetime | None) -> datetime:
    """Validate and normalise an ``asof_ts`` cutoff.

    Raises :class:`LookaheadError` if ``asof_ts`` is ``None`` or naive
    (no ``tzinfo``). A naive cutoff is itself a look-ahead-discipline
    violation: the auditor cannot reason about timezone-implicit
    timestamps.

    Shared by :func:`assert_no_lookahead` (the join-time chokepoint)
    AND by every ``.fetch_*`` entrypoint in :mod:`data.sources` (so a
    leak fails BEFORE the network call burns quota). One function, two
    callsites — keep it that way.
    """
    if asof_ts is None:
        raise LookaheadError(
            "asof_ts is required — point-in-time chokepoint cannot be bypassed "
            "(PRD §14.1). Pass a timezone-aware datetime."
        )
    if asof_ts.tzinfo is None:
        raise LookaheadError(
            f"asof_ts must be timezone-aware (got naive {asof_ts!r}). "
            "Use datetime(..., tzinfo=timezone.utc) — naive timestamps are a "
            "PIT-discipline violation."
        )
    return asof_ts


# Back-compat alias for the private name the first sprint_2 draft
# exported. Kept so any in-flight callsites don't break before the
# next ruff pass; remove when no callers reference the leading-underscore
# form.
_require_asof = require_asof_ts


def _is_pandas_frame(obj: object) -> bool:
    """Cheap duck-type check that avoids importing pandas at module load.

    Matches both ``pandas`` (the top-level re-export) and any
    ``pandas.*`` submodule (``pandas.core.frame.DataFrame``).
    """
    mod = type(obj).__module__
    return mod == "pandas" or mod.startswith("pandas.")


def _is_polars_frame(obj: object) -> bool:
    """Cheap duck-type check that avoids importing polars at module load."""
    mod = type(obj).__module__
    return mod == "polars" or mod.startswith("polars.")


def assert_no_lookahead(df: object, asof_ts: datetime) -> None:
    """Raise :class:`LookaheadError` if ``df`` leaks future rows.

    Parameters
    ----------
    df:
        A :class:`pandas.DataFrame` or :class:`polars.DataFrame` with
        an ``available_at`` column. The concrete engine type is kept
        as :class:`object` at the signature boundary so cross-track
        callers do not need to import a specific DataFrame library to
        typecheck against this function.
    asof_ts:
        The as-of cutoff. Any row with ``available_at > asof_ts`` is a
        look-ahead violation. Must be timezone-aware.

    Raises
    ------
    LookaheadError:
        * If ``asof_ts`` is naive or ``None``.
        * If ``df`` lacks an ``available_at`` column.
        * If any row has ``available_at > asof_ts``.

    Notes
    -----
    Empty frames pass vacuously. Both DataFrame engines are supported
    so Track B (pandas-dominant decision loop) and Track C (polars in
    the sim) share the same chokepoint without engine conversion.
    """
    cutoff = require_asof_ts(asof_ts)

    if _is_pandas_frame(df):
        _assert_no_lookahead_pandas(df, cutoff)
        return
    if _is_polars_frame(df):
        _assert_no_lookahead_polars(df, cutoff)
        return

    raise TypeError(
        f"assert_no_lookahead supports pandas.DataFrame or polars.DataFrame "
        f"(got {type(df).__module__}.{type(df).__name__}). If you need a new "
        "engine, extend this chokepoint — do not bypass it."
    )


def _assert_no_lookahead_pandas(df: object, cutoff: datetime) -> None:
    """Pandas branch — kept private; the public chokepoint dispatches."""
    import pandas as pd

    assert isinstance(df, pd.DataFrame)  # narrows for mypy

    if "available_at" not in df.columns:
        raise LookaheadError(
            "DataFrame missing 'available_at' column — PIT metadata is required "
            "for every feature row (PRD §14.1)."
        )

    if len(df) == 0:
        return

    series = df["available_at"]
    # Coerce to pandas datetime; pandas accepts python datetimes natively
    # but be defensive when callers pass strings.
    try:
        coerced = pd.to_datetime(series, utc=True, errors="raise")
    except (ValueError, TypeError) as exc:
        raise LookaheadError(
            f"available_at column could not be parsed as datetimes: {exc}"
        ) from exc

    cutoff_ts = pd.Timestamp(cutoff).tz_convert("UTC")
    offenders = coerced[coerced > cutoff_ts]
    if len(offenders) > 0:
        earliest = offenders.min()
        raise LookaheadError(
            f"{len(offenders)} row(s) leak past asof_ts={cutoff.isoformat()} "
            f"(earliest offender available_at={earliest.isoformat()}). "
            "PRD §14.1 — drop or repair these rows before model ingestion."
        )


def _assert_no_lookahead_polars(df: object, cutoff: datetime) -> None:
    """Polars branch — kept private; the public chokepoint dispatches."""
    import polars as pl

    assert isinstance(df, pl.DataFrame)  # narrows for mypy

    if "available_at" not in df.columns:
        raise LookaheadError(
            "DataFrame missing 'available_at' column — PIT metadata is required "
            "for every feature row (PRD §14.1)."
        )

    if df.height == 0:
        return

    series = df.get_column("available_at")
    dtype = series.dtype

    if not (dtype == pl.Datetime or str(dtype).startswith("Datetime")):
        raise LookaheadError(
            f"available_at column must be Datetime dtype (got {dtype}). "
            "Cast at ETL boundaries — do not let stringly-typed timestamps "
            "reach the chokepoint."
        )

    offenders_count = int(series.filter(series > cutoff).len())
    if offenders_count > 0:
        earliest = series.filter(series > cutoff).min()
        raise LookaheadError(
            f"{offenders_count} row(s) leak past asof_ts={cutoff.isoformat()} "
            f"(earliest offender available_at={earliest!r}). "
            "PRD §14.1 — drop or repair these rows before model ingestion."
        )


__all__ = ["LookaheadBiasError", "LookaheadError", "assert_no_lookahead", "require_asof_ts"]
