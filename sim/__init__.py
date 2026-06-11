"""Genesis Track C — Layer 2 calibration sim.

The ``sim`` package is the **offline** half of the Genesis Experiment: a
Monte Carlo + Latin Hypercube + Bayesian-Optimization framework that
selects numerical values for the BREATH economic parameter space *before*
any contracts deploy. It is fully decoupled from the live chain, live
Polymarket, and the agent's LLM — see PRD.md §14 (Calibration Framework)
and TECHNICAL_PLAN.md §4 (sim/ module tree).

Sprint_1 publishes the **package skeleton only** (per task brief T-C-001).
Every engine module exposes its public surface as a stub raising
``NotImplementedError``; the real Monte Carlo + LHS + BO logic lands in
sprint_2+. The ParamSpace dataclass in :mod:`sim.params` is the only
piece of behavior carried this round, because Track A (Solidity
constants) and Track B (agent runtime defaults) consume the parameter
schema directly via JSON round-trip.

Consumers
---------

* Track A reads ``selected_params.json`` to populate
  ``contracts/EnergyController.sol`` constants.
* Track B reads the same file for ``agent/engines/`` priors + defaults.
* Track D's "Evolution Curve" view replays the LHS sweep visually.

This module SHOULD remain import-light; downstream tooling imports
``sim`` for side-effect-free type discovery (e.g. ``mypy --strict``).
"""

from __future__ import annotations

__version__ = "0.3.0-sprint3"
__all__ = ["__version__"]
