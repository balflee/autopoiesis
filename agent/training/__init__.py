"""Phase 1 historical training — sprint_3 D6 critical-path deliverable.

This package is the offline counterpart of the live ``agent_loop``:

* The runtime in :mod:`agent.core.agent` consumes ENGINE → DECISION →
  REFLECTION → WEIGHT_UPDATE at one decision per ~45 minutes against
  the real Polymarket book + on-chain BREATH economy.

* :mod:`agent.training` walks a *recorded* parquet of historical NBA
  games + 4 engine signals (LLM frozen at 0 per PRD §4.2 Phase 1) +
  binary outcomes, computes per-game log-loss against the 2-layer
  fusion, derives a quality gradient per engine, and reuses the same
  :class:`agent.engines.weight_updater.WeightUpdater` to drive the
  6-parameter softmax-reparameterised SGD that the live loop would.

The output ``weights_v0.json`` is the seed Phase 2 real-time training
inherits — it is the D5+ critical-path artefact that closes the
calibration→training→live agent handshake.

Submodules
----------

``feature_engineering``
    PIT-strict row builder. Reads the parquet, validates every row
    via :func:`data.etl.pit_correct.assert_no_lookahead`, projects
    into a :class:`Phase1FeatureRow` for the runner. The reviewer's
    look-ahead auditor greps this module for ``assert_no_lookahead`` /
    ``available_at`` / ``LookaheadError`` chokepoint references.

``phase1_runner``
    The training loop. Walks rows in tipoff order, computes BCE
    log-loss against the fused score, derives per-engine quality
    signals, calls :meth:`WeightUpdater.update`, and persists
    ``weights_v0.json`` + ``evolution_curve.csv`` +
    ``PHASE1_TRAINING_REPORT.md`` under ``reports/phase1/``.

``__main__``
    The CLI: ``python -m agent.training --training-set <path>
    --output reports/phase1/``.
"""

from __future__ import annotations

from agent.training.feature_engineering import (
    PHASE1_REQUIRED_COLUMNS,
    Phase1FeatureRow,
    build_phase1_feature_rows,
    load_training_set,
)
from agent.training.phase1_runner import (
    Phase1Config,
    Phase1Result,
    run_phase1_training,
)
from agent.training.tennis_features import (
    TENNIS_PHASE1_REQUIRED_COLUMNS,
    TennisFeatureRow,
    build_tennis_feature_rows,
    load_tennis_phase1,
)
from agent.training.tennis_runner import (
    ArchetypeBacktest,
    BoundarySaturationViolation,
    TennisTrainingConfig,
    TennisTrainingResult,
    predict_tennis_prob,
    run_tennis_training,
)

__all__ = [
    "PHASE1_REQUIRED_COLUMNS",
    "TENNIS_PHASE1_REQUIRED_COLUMNS",
    "ArchetypeBacktest",
    "BoundarySaturationViolation",
    "Phase1Config",
    "Phase1FeatureRow",
    "Phase1Result",
    "TennisFeatureRow",
    "TennisTrainingConfig",
    "TennisTrainingResult",
    "build_phase1_feature_rows",
    "build_tennis_feature_rows",
    "load_tennis_phase1",
    "load_training_set",
    "predict_tennis_prob",
    "run_phase1_training",
    "run_tennis_training",
]
