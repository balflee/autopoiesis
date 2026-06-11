"""ETL helpers — joins, point-in-time-correct slicing, schema validation.

Sprint_2 ships the real body of
:func:`data.etl.pit_correct.assert_no_lookahead` (pandas + polars
branches) and the four-stream :func:`data.etl.build_training_set.build_training_set`
orchestrator that smoke-runs all four fetchers into one PIT-validated
parquet.
"""

from __future__ import annotations

from data.etl.build_training_set import (
    PHASE1_CALIBRATED_PARAM_KEYS,
    Clients,
    TrainingRowBundle,
    build_training_set,
    build_training_set_v1,
    default_clients,
)
from data.etl.pit_correct import LookaheadBiasError, LookaheadError, assert_no_lookahead

__all__ = [
    "PHASE1_CALIBRATED_PARAM_KEYS",
    "Clients",
    "LookaheadBiasError",
    "LookaheadError",
    "TrainingRowBundle",
    "assert_no_lookahead",
    "build_training_set",
    "build_training_set_v1",
    "default_clients",
]
