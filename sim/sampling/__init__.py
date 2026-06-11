"""Sampling strategies for the calibration sweeper.

Sprint_2 (T-C-002) shipped :class:`LHSSampler`; sprint_3 (T-C-003) adds
the Bayesian Optimization refiner :func:`run_bayesian_optimization` —
a thin wrapper around ``skopt.gp_minimize`` that consumes the LHS
frontier as warm-start. A future ``GridSampler`` for ablation studies
will land alongside.
"""

from __future__ import annotations

from sim.sampling.bo import (
    DEFAULT_BO_TRIALS,
    BOResult,
    BOTrial,
    run_bayesian_optimization,
)
from sim.sampling.lhs import LHSSampler

__all__ = [
    "DEFAULT_BO_TRIALS",
    "BOResult",
    "BOTrial",
    "LHSSampler",
    "run_bayesian_optimization",
]
