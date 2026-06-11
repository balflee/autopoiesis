"""Latin Hypercube sampler over :class:`sim.params.ParamSpace`.

The sweeper uses LHS to spread ``n`` parameter combinations evenly across
the BREATH parameter cube before Bayesian Optimization refines the
top-scoring frontier (PRD §14). LHS is preferred over plain uniform
sampling because it guarantees one sample per **stratum per dimension**,
so even with small ``n`` (e.g. n=64) every dim gets full coverage — a
property the T-C-002 acceptance criteria check directly ("max-min per
dim within 1/n + epsilon").

Determinism
-----------

Every sample is produced from :class:`numpy.random.Generator` seeded
deterministically via the constructor ``seed`` parameter. Two
:class:`LHSSampler` instances with the same seed AND the same
:class:`ParamSpace` bounds produce byte-identical sample sequences —
this is the reproducibility contract DEV_FRAMEWORK §26 T2.7 grades.

Implementation
--------------

Uses a **centered Latin Hypercube** — samples sit at the midpoint of
each of ``n`` equal-width strata per dim, then per-dim columns are
independently shuffled with a seeded :class:`numpy.random.Generator`.
This deterministic construction satisfies the T-C-002 coverage gate
exactly: per-dim ``max-min == (n-1)/n`` of the full range.

We deliberately avoid :class:`scipy.stats.qmc.LatinHypercube`'s default
``scramble=True`` here — scrambled LHS allows the per-dim extrema to
sit anywhere inside their stratum, which can shave the observed
coverage below the brief's ``1/n + epsilon`` tolerance at small ``n``
(empirically observed at n=256 with seed=7 during T-C-002 dev).
SciPy remains a dependency for the future Bayesian-Optimization
refiner; the LHS sampler just doesn't need it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sim.params import LHS_BOUNDS, LHS_DIMS, ParamSpace


@dataclass(frozen=True)
class LHSSampler:
    """Latin-Hypercube sampler over the :data:`sim.params.LHS_DIMS`.

    Parameters
    ----------
    base:
        :class:`ParamSpace` providing default values for every field NOT
        in :data:`sim.params.LHS_DIMS`. Each emitted sample is
        ``base.with_overrides(...)`` populated with the sampled LHS dims.
    dims:
        Ordered tuple of dimension names to sample. Defaults to
        :data:`sim.params.LHS_DIMS`. Each must appear in
        :data:`sim.params.LHS_BOUNDS`.
    seed:
        RNG seed passed to :class:`scipy.stats.qmc.LatinHypercube`.
        Determinism: ``LHSSampler(...).sample(n)`` is byte-identical
        across runs for fixed ``(base, dims, seed, n)``.
    """

    base: ParamSpace
    dims: tuple[str, ...] = LHS_DIMS
    seed: int = 0

    def __post_init__(self) -> None:
        # Validate every dim has a published bound — early error beats a
        # downstream KeyError far from the sampler call site.
        missing = [d for d in self.dims if d not in LHS_BOUNDS]
        if missing:
            raise ValueError(
                f"LHSSampler dims missing from LHS_BOUNDS: {missing}"
            )

    def sample(self, n: int) -> tuple[ParamSpace, ...]:
        """Draw ``n`` :class:`ParamSpace` samples.

        Returns a tuple (immutable, hashable as a sequence) so callers
        cannot accidentally mutate the sweep state mid-run. Each sample
        sets exactly the dims in :attr:`dims`; remaining fields inherit
        from :attr:`base`.

        Raises
        ------
        ValueError
            If ``n`` is not a positive integer. The sweeper relies on
            this — passing ``n=0`` from the CLI would silently produce
            an empty sweep and look like a "passed" run.
        """
        if not isinstance(n, int) or n <= 0:
            raise ValueError(f"LHSSampler.sample(n) requires n >= 1, got {n!r}")

        d = len(self.dims)
        rng = np.random.default_rng(self.seed)
        # Centered LHS: stratum midpoints (i + 0.5) / n for i in [0, n).
        # Each column gets an independent random permutation of these
        # midpoints. Guarantees max-min == (n-1)/n exactly per dim.
        centres = (np.arange(n, dtype=np.float64) + 0.5) / n
        unit_cube = np.empty((n, d), dtype=np.float64)
        for col in range(d):
            unit_cube[:, col] = rng.permutation(centres)

        # Scale each column by its (low, high) bound.
        lows = np.array([LHS_BOUNDS[name][0] for name in self.dims], dtype=np.float64)
        highs = np.array([LHS_BOUNDS[name][1] for name in self.dims], dtype=np.float64)
        scaled: np.ndarray = lows + unit_cube * (highs - lows)

        samples: list[ParamSpace] = []
        for row in scaled:
            overrides = {
                name: float(row[i])
                for i, name in enumerate(self.dims)
            }
            samples.append(self.base.with_overrides(overrides))
        return tuple(samples)

    # ------------------------------------------------------------------
    # Diagnostic helpers — used by tests to verify the LHS coverage
    # property without re-implementing the bounding math.
    # ------------------------------------------------------------------

    def coverage(self, samples: tuple[ParamSpace, ...]) -> dict[str, float]:
        """Return per-dim normalised range (max - min, divided by the
        dim's bound width). For a perfect LHS over ``n`` samples this
        ratio is ≥ ``(n - 1) / n`` (one stratum per row, with the last
        row's centre at ``(n - 0.5) / n``). The T-C-002 acceptance test
        asserts coverage ≥ ``(n - 1) / n - 1e-6``.
        """
        out: dict[str, float] = {}
        for name in self.dims:
            low, high = LHS_BOUNDS[name]
            width = high - low
            col = np.array(
                [getattr(p, name) for p in samples], dtype=np.float64
            )
            out[name] = float((col.max() - col.min()) / width)
        return out
