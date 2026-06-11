"""Bayesian Optimization refiner over the BREATH parameter space.

Sprint_3 (T-C-003) extends the sprint_2 LHS warm-start with a GP-based
refinement loop. Per PRD §14.3 the calibration must combine LHS + BO:
LHS covers the cube uniformly so the GP has decent global coverage on
trial 1; BO then exploits the high-signal regions to drive aggregate
loss down.

We use :func:`skopt.gp_minimize` with the LHS frontier supplied as
``x0``/``y0`` (warm-start data). The objective passed to ``gp_minimize``
maps a parameter vector to the aggregate calibration loss per
:func:`sim.objectives.aggregate_loss` — minimising it maximises the
``passed_count`` over the 14 GOOD_CALIBRATION objectives.

Determinism
-----------

``skopt`` accepts a ``random_state`` int; we plumb the sweep seed in
unchanged so two BO runs at the same seed produce byte-identical trial
traces. Internally ``gp_minimize`` calls the acquisition function's
optimiser, which is itself seeded — see scikit-optimize's
``_gp_minimize`` source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sim.params import LHS_BOUNDS, ParamSpace

# Default number of BO trials. Mirrors the T-C-003 task brief's
# ``--bo-trials 64`` baseline; tests override to a small value.
DEFAULT_BO_TRIALS: Final[int] = 64


@dataclass(frozen=True)
class BOTrial:
    """One BO iteration's record. Persisted to ``bo_trace.json``."""

    iteration: int
    params: ParamSpace
    loss: float
    best_loss_so_far: float


@dataclass(frozen=True)
class BOResult:
    """Output of :func:`run_bayesian_optimization`.

    ``best_params`` is the lowest-loss point seen across the warm-start
    + BO iterations combined; ``trials`` lists only the BO iterations
    (the LHS warm-start lives in the parent :class:`SweepReport`).
    """

    best_params: ParamSpace
    best_loss: float
    trials: tuple[BOTrial, ...]


def _params_to_vector(p: ParamSpace, dims: tuple[str, ...]) -> list[float]:
    """Project a :class:`ParamSpace` onto the BO search vector."""
    return [float(getattr(p, name)) for name in dims]


def _vector_to_params(
    *, base: ParamSpace, dims: tuple[str, ...], vector: list[float]
) -> ParamSpace:
    overrides = {name: float(vector[i]) for i, name in enumerate(dims)}
    return base.with_overrides(overrides)


def run_bayesian_optimization(
    *,
    objective: Callable[[ParamSpace], float],
    base_params: ParamSpace,
    dims: tuple[str, ...],
    lhs_points: tuple[ParamSpace, ...],
    lhs_losses: tuple[float, ...],
    n_trials: int = DEFAULT_BO_TRIALS,
    seed: int = 0,
) -> BOResult:
    """Run scikit-optimize ``gp_minimize`` over the LHS warm start.

    Parameters
    ----------
    objective:
        Callable mapping a :class:`ParamSpace` → aggregate loss (lower
        is better). The sweeper passes a closure that builds + scores
        the per-archetype lifetimes for the candidate.
    base_params:
        ParamSpace defaults for fields outside ``dims``. The vectoriser
        only mutates the ``dims`` columns; other fields stay at
        ``base_params``.
    dims:
        Ordered names of the BO search dimensions. Each must appear in
        :data:`sim.params.LHS_BOUNDS`.
    lhs_points:
        LHS sample tuple (each is a full :class:`ParamSpace`) — used
        as ``x0`` warm-start for the GP.
    lhs_losses:
        Aggregate loss for each LHS point, same length / ordering.
    n_trials:
        Number of new ``gp_minimize`` calls beyond the warm start.
    seed:
        Forwarded to ``gp_minimize(random_state=)``.

    Returns
    -------
    BOResult
        ``best_params`` / ``best_loss`` aggregated across warm start +
        BO iterations; ``trials`` records each BO iteration in order.
    """
    if len(lhs_points) != len(lhs_losses):
        raise ValueError(
            f"lhs_points/lhs_losses length mismatch: {len(lhs_points)} vs {len(lhs_losses)}"
        )
    if not lhs_points:
        raise ValueError("LHS warm-start must contain ≥1 point")
    if n_trials < 1:
        raise ValueError(f"n_trials must be ≥1, got {n_trials}")
    missing_dims = [d for d in dims if d not in LHS_BOUNDS]
    if missing_dims:
        raise ValueError(
            f"BO dims missing from LHS_BOUNDS: {missing_dims}"
        )

    # Imported here so the module can still typecheck on environments
    # without scikit-optimize (the BO entrypoint is the only consumer).
    from skopt import gp_minimize
    from skopt.space import Real

    space = [
        Real(low=LHS_BOUNDS[name][0], high=LHS_BOUNDS[name][1], name=name)
        for name in dims
    ]
    x0 = [_params_to_vector(p, dims) for p in lhs_points]
    y0 = list(lhs_losses)

    # Track each BO call's params + loss. gp_minimize doesn't expose a
    # per-iteration callback that carries the candidate AND the score,
    # so we wrap the objective in a closure that records both.
    trial_records: list[BOTrial] = []

    def _wrapped(vector: list[float]) -> float:
        ps = _vector_to_params(base=base_params, dims=dims, vector=vector)
        loss = float(objective(ps))
        iter_idx = len(trial_records) + 1
        running_min = min(
            [loss]
            + [t.loss for t in trial_records]
            + list(lhs_losses)
        )
        trial_records.append(
            BOTrial(
                iteration=iter_idx,
                params=ps,
                loss=loss,
                best_loss_so_far=running_min,
            )
        )
        return loss

    # n_initial_points=0 — we already supplied LHS warm-start; we don't
    # want gp_minimize to throw away budget on additional random points.
    result = gp_minimize(
        _wrapped,
        space,
        x0=x0,
        y0=y0,
        n_calls=n_trials,
        n_initial_points=0,
        random_state=seed,
        # ``noise='gaussian'`` keeps the GP well-conditioned on noisy
        # sim outputs (each (combo, archetype, lifetimes) eval is a
        # noisy Monte Carlo estimate).
        noise="gaussian",
    )
    # ``result.x`` is the best vector across x0 + BO iterations combined.
    best_vector = [float(v) for v in result.x]
    best_params = _vector_to_params(base=base_params, dims=dims, vector=best_vector)
    best_loss = float(result.fun)
    return BOResult(
        best_params=best_params,
        best_loss=best_loss,
        trials=tuple(trial_records),
    )
