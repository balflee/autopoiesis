"""PIT-strict feature row builder for Phase 1 historical training.

The Phase 1 training loop consumes a parquet of historical NBA games
joined with the 4 *Phase-1-active* engine signals (β₁ = LLM frozen at
0 per PRD §4.2). This module is the I/O boundary that:

1. Reads the parquet from disk.
2. Validates the schema (required columns / dtypes).
3. Enforces point-in-time correctness via
   :func:`data.etl.pit_correct.assert_no_lookahead`. The auditor
   spec says: every feature row must have
   ``available_at <= game_start_time - 1 second``. We check that
   property on the join.
4. Projects each row into a typed :class:`Phase1FeatureRow`.

The ML look-ahead reviewer greps this module for ``assert_no_lookahead``
and the PRD §6.8 forbidden-prefix columns (``outcome``, ``payout``,
``resolved_at``, ``settled_at``). Outcome is required for training (it
IS the label) but the runner is careful to ONLY consume it AFTER
prediction — the feature_engineering layer surfaces it as a separate
field so the runner can't accidentally fold it into the gradient
features.

The training set generator (:func:`data.etl.build_training_set.build_training_set_v1`)
emits a parquet with the columns this module expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

from data.etl.pit_correct import LookaheadError, assert_no_lookahead, require_asof_ts

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    import pandas as pd


# The canonical column set the Phase 1 training set must carry. Mirrors
# the schema emitted by
# :func:`data.etl.build_training_set.build_training_set_v1`.
PHASE1_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "game_id",
    "tipoff_at",
    "available_at",
    "home_team",
    "away_team",
    "nba_technical_score",
    "market_momentum_score",
    "smart_money_score",
    "crowd_volume_score",
    "nba_technical_conf",
    "market_momentum_conf",
    "smart_money_conf",
    "crowd_volume_conf",
    "outcome",
)


@dataclass(frozen=True)
class Phase1FeatureRow:
    """One game's PIT-correct feature vector + label.

    Fields are partitioned into three groups:

    * Identity: ``game_id``, ``tipoff_at``, ``available_at`` —
      the PIT key + sorting key.
    * Features: 4 engine scores + 4 confidences — these flow into
      the fused-score computation. (Sentiment LLM is omitted: β₁ is
      frozen at 0 in Phase 1, so its row contribution is identically
      0 and we save the column space.)
    * Label: ``outcome`` ∈ {0, 1} — 1 if home won. The runner
      consumes this AFTER computing the prediction.

    Frozen so a downstream stage can't accidentally swap the outcome
    into a feature slot (look-ahead-discipline defence).
    """

    game_id: str
    tipoff_at: datetime
    available_at: datetime
    home_team: str
    away_team: str

    nba_technical_score: float
    market_momentum_score: float
    smart_money_score: float
    crowd_volume_score: float

    nba_technical_conf: float
    market_momentum_conf: float
    smart_money_conf: float
    crowd_volume_conf: float

    outcome: int

    def feature_vector(self) -> tuple[float, float, float, float]:
        """Return (nba, market_momentum, smart_money, crowd_volume) scores."""
        return (
            self.nba_technical_score,
            self.market_momentum_score,
            self.smart_money_score,
            self.crowd_volume_score,
        )

    def confidence_vector(self) -> tuple[float, float, float, float]:
        """Return (nba, market_momentum, smart_money, crowd_volume) confs."""
        return (
            self.nba_technical_conf,
            self.market_momentum_conf,
            self.smart_money_conf,
            self.crowd_volume_conf,
        )


def load_training_set(path: Path) -> pd.DataFrame:
    """Read the Phase 1 training parquet from disk.

    Performs minimum-viable schema validation: every required column
    is present, available_at + tipoff_at coerce to tz-aware UTC
    datetimes, outcome is 0/1.

    Returns the loaded :class:`pandas.DataFrame` sorted by
    ``tipoff_at`` ascending — chronological order is the only valid
    walk direction for Phase 1 training (later games must NOT
    influence earlier weights — that would be the canonical
    look-ahead bias the auditor flags).
    """
    import pandas as pd

    path = Path(path)
    try:
        df = pd.read_parquet(path)
    except (FileNotFoundError, OSError) as exc:
        raise FileNotFoundError(
            f"training set parquet missing: {path} — run "
            "`python -m data.etl.build_training_set --output <path>` to generate."
        ) from exc

    missing = [c for c in PHASE1_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"training set missing required columns: {sorted(missing)}. "
            f"got {sorted(df.columns)}."
        )

    # Coerce datetime columns — parquet readers may surface naive or
    # localised datetimes depending on writer version; canonical PIT
    # contract requires tz-aware UTC.
    for col in ("tipoff_at", "available_at"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="raise")

    # Outcome integrity.
    if df["outcome"].isin([0, 1]).sum() != len(df):
        raise ValueError(
            "outcome column must be strictly 0 / 1 — got values outside {0, 1}"
        )

    # Sort by tipoff (ascending = chronological). Later steps walk in
    # this order so the gradient at step t can only see games 0..t-1.
    df = df.sort_values("tipoff_at", kind="stable").reset_index(drop=True)
    return df


def build_phase1_feature_rows(
    df: pd.DataFrame,
    *,
    pit_safety_margin: timedelta = timedelta(seconds=1),
) -> list[Phase1FeatureRow]:
    """Project ``df`` into typed :class:`Phase1FeatureRow` objects.

    PIT enforcement runs on the WHOLE frame against
    ``asof_ts = max(tipoff_at) - pit_safety_margin``. This proves no
    row leaks past the last training-set game's tipoff; the per-row
    check (``available_at <= tipoff_at - 1s``) is verified in the loop
    so a single offending row surfaces with the offender's game_id.

    Parameters
    ----------
    df:
        :class:`pandas.DataFrame` loaded by :func:`load_training_set`.
    pit_safety_margin:
        Subtracted from tipoff_at when forming the per-row PIT cutoff.
        Default 1 second matches the brief's acceptance criterion:
        ``every feature row passes assert_no_lookahead(
        asof_ts=game_start_time - 1s)``.

    Returns
    -------
    A list of :class:`Phase1FeatureRow` in chronological order.
    """
    import pandas as pd

    if df.empty:
        return []

    # Frame-wide PIT cutoff — the latest tipoff minus the safety margin.
    # The chokepoint refuses naive datetimes, so we normalise to UTC.
    last_tipoff_raw = df["tipoff_at"].max()
    if not isinstance(last_tipoff_raw, pd.Timestamp):  # pragma: no cover — defence
        raise LookaheadError(
            f"tipoff_at column must be Timestamp-typed; got {type(last_tipoff_raw)!r}"
        )
    last_tipoff: datetime = last_tipoff_raw.to_pydatetime()
    if last_tipoff.tzinfo is None:  # pragma: no cover — load_training_set coerces tz
        last_tipoff = last_tipoff.replace(tzinfo=UTC)
    frame_cutoff = require_asof_ts(last_tipoff - pit_safety_margin)

    assert_no_lookahead(df, frame_cutoff)

    rows: list[Phase1FeatureRow] = []
    for record in df.to_dict(orient="records"):
        tipoff = _to_aware(record["tipoff_at"])
        available_at = _to_aware(record["available_at"])
        row_cutoff = tipoff - pit_safety_margin
        if available_at > row_cutoff:
            raise LookaheadError(
                f"game_id={record['game_id']}: available_at={available_at.isoformat()} "
                f"exceeds tipoff_at - {pit_safety_margin.total_seconds():.0f}s = "
                f"{row_cutoff.isoformat()} (PRD §14.1)."
            )
        rows.append(
            Phase1FeatureRow(
                game_id=str(record["game_id"]),
                tipoff_at=tipoff,
                available_at=available_at,
                home_team=str(record["home_team"]),
                away_team=str(record["away_team"]),
                nba_technical_score=float(record["nba_technical_score"]),
                market_momentum_score=float(record["market_momentum_score"]),
                smart_money_score=float(record["smart_money_score"]),
                crowd_volume_score=float(record["crowd_volume_score"]),
                nba_technical_conf=float(record["nba_technical_conf"]),
                market_momentum_conf=float(record["market_momentum_conf"]),
                smart_money_conf=float(record["smart_money_conf"]),
                crowd_volume_conf=float(record["crowd_volume_conf"]),
                outcome=int(record["outcome"]),
            )
        )
    return rows


def _to_aware(value: object) -> datetime:
    """Coerce a pandas / python datetime-ish value to tz-aware UTC.

    Centralised so the per-row PIT comparison + the projection layer
    share one timezone discipline.
    """
    import pandas as pd

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, pd.Timestamp):
        # pandas Timestamp.to_pydatetime() returns a stdlib datetime — mypy
        # sees this through the type: ignore on the pandas import override.
        py_raw = value.to_pydatetime()
        assert isinstance(py_raw, datetime)  # narrow for --strict
        if py_raw.tzinfo is None:
            return py_raw.replace(tzinfo=UTC)
        return py_raw
    raise LookaheadError(
        f"datetime field must be Timestamp/datetime; got {type(value)!r}"
    )


__all__ = [
    "PHASE1_REQUIRED_COLUMNS",
    "Phase1FeatureRow",
    "build_phase1_feature_rows",
    "load_training_set",
]
