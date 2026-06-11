"""Stream row schemas.

These Pydantic models lock the parquet column layout for the four
Genesis feeds. Every model is frozen and carries a timezone-aware
``available_at`` field — the cross-track PIT key consumed by
:func:`data.etl.pit_correct.assert_no_lookahead`.

The parallel ``arrow_schema_for(model)`` builder returns a
:class:`pyarrow.Schema` so writers can declare column types up front
(parquet readers in Track B + Track C then get strict dtype validation
for free on read).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    import pyarrow as pa


class _FrozenRow(BaseModel):
    """Common config for all stream row models.

    Frozen so rows are hashable and accidental in-place mutation in an
    ETL stage surfaces as an exception rather than silently corrupting
    downstream state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("available_at", mode="after", check_fields=False)
    @classmethod
    def _available_at_must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "available_at must be timezone-aware (PRD §14.1) — got naive datetime"
            )
        return v


class NBAGameRow(_FrozenRow):
    """One NBA game's point-in-time snapshot."""

    game_id: str = Field(..., description="Stable game identifier (e.g. balldontlie 'id').")
    tipoff_at: datetime = Field(..., description="Game start, UTC.")
    home_team: str = Field(..., min_length=2, max_length=4, description="Three-letter team code.")
    away_team: str = Field(..., min_length=2, max_length=4, description="Three-letter team code.")
    available_at: datetime = Field(..., description="PIT cutoff: when this row became public.")
    home_score: int = Field(0, ge=0)
    away_score: int = Field(0, ge=0)
    status: str = Field("scheduled", description="balldontlie status string.")


class PolymarketSnapshotRow(_FrozenRow):
    """One Polymarket orderbook midpoint snapshot."""

    slug: str = Field(..., min_length=1)
    market_id: str = Field(..., min_length=1)
    snapshot_ts: datetime
    midpoint: float = Field(..., ge=0.0, le=1.0)
    yes_bid: float = Field(..., ge=0.0, le=1.0)
    yes_ask: float = Field(..., ge=0.0, le=1.0)
    volume_24h: float = Field(0.0, ge=0.0)
    available_at: datetime = Field(
        ..., description="PIT cutoff (equals snapshot_ts for live orderbook reads)."
    )
    resolved: bool = False


class PolygonEventRow(_FrozenRow):
    """One decoded Polygon chain event."""

    block_number: int = Field(..., ge=0)
    block_time: datetime
    tx_hash: str = Field(..., pattern="^0x[0-9a-fA-F]{64}$")
    log_index: int = Field(..., ge=0)
    contract_address: str = Field(..., pattern="^0x[0-9a-fA-F]{40}$")
    event_name: str = Field(..., min_length=1)
    topic0: str = Field(..., pattern="^0x[0-9a-fA-F]{64}$")
    available_at: datetime = Field(
        ...,
        description=(
            "PIT cutoff — equals block_time + confirmation depth; sprint_2 "
            "fetcher sets confirmation_depth=12 blocks ≈ ~26s on Polygon."
        ),
    )


class RedditWindowRow(_FrozenRow):
    """One subreddit sentiment window."""

    subreddit: str = Field(..., min_length=1)
    since: datetime
    until: datetime
    available_at: datetime = Field(
        ..., description="PIT cutoff — equals window 'until' (snapshot is sealed)."
    )
    post_count: int = Field(0, ge=0)
    comment_count: int = Field(0, ge=0)
    mention_counts_json: str = Field(
        "{}",
        description=(
            "Token → count map serialised as a JSON string. Parquet stores "
            "this as a UTF-8 column; readers parse on demand."
        ),
    )


def available_at_columns() -> dict[str, str]:
    """Return ``{model_name: pit_column_name}`` for the auditor."""
    return {
        "NBAGameRow": "available_at",
        "PolymarketSnapshotRow": "available_at",
        "PolygonEventRow": "available_at",
        "RedditWindowRow": "available_at",
    }


def arrow_schema_for(model: type[_FrozenRow]) -> pa.Schema:
    """Return a :class:`pyarrow.Schema` matching ``model``'s field layout."""
    import pyarrow as pa

    py_to_arrow: dict[type, pa.DataType] = {
        str: pa.string(),
        int: pa.int64(),
        float: pa.float64(),
        bool: pa.bool_(),
        datetime: pa.timestamp("us", tz="UTC"),
    }

    fields: list[pa.Field] = []
    for name, info in model.model_fields.items():
        annot = info.annotation
        arrow_type = py_to_arrow.get(annot)  # type: ignore[arg-type]
        if arrow_type is None:
            raise TypeError(
                f"{model.__name__}.{name}: cannot map {annot!r} to a pyarrow dtype. "
                "Add an entry to py_to_arrow."
            )
        fields.append(pa.field(name, arrow_type, nullable=False))
    return pa.schema(fields)


__all__ = [
    "NBAGameRow",
    "PolygonEventRow",
    "PolymarketSnapshotRow",
    "RedditWindowRow",
    "arrow_schema_for",
    "available_at_columns",
]
