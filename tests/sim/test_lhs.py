"""Sprint_2 (T-C-002) tests for :mod:`sim.sampling.lhs`.

Acceptance criteria covered:

* LHS sampler distributes ``n=256`` samples evenly across each LHS dim
  (per-dim max-min within ``1/n + epsilon`` of full range).
* The sampler is deterministic — same seed → byte-identical sample
  tuple.
* Two different seeds give different samples.
* Sampling rejects non-positive ``n``.
* The four T-C-002 mandatory PRD §14.1 dims appear in
  :data:`sim.params.LHS_DIMS`.
"""

from __future__ import annotations

import pytest

from sim.params import LHS_BOUNDS, LHS_DIMS, ParamSpace
from sim.sampling import LHSSampler


def test_mandatory_paramspace_dims_present_in_lhs() -> None:
    """Calibration validator greps the sweep output for these names."""
    mandatory = {
        "e_decision_tax",
        "e_time_tax_per_tick",
        "soft_cap_threshold",
        "desperate_threshold",
    }
    assert mandatory <= set(LHS_DIMS), (
        f"LHS_DIMS missing T-C-002 mandatory four: {mandatory - set(LHS_DIMS)}"
    )
    # And each must have a published bound — defensive.
    assert mandatory <= set(LHS_BOUNDS.keys())


def test_lhs_sampler_is_deterministic_for_same_seed() -> None:
    base = ParamSpace()
    s1 = LHSSampler(base=base, seed=42).sample(16)
    s2 = LHSSampler(base=base, seed=42).sample(16)
    # Frozen dataclasses compare by value — tuple equality is sufficient.
    assert s1 == s2


def test_lhs_sampler_differs_for_different_seeds() -> None:
    base = ParamSpace()
    s1 = LHSSampler(base=base, seed=1).sample(16)
    s2 = LHSSampler(base=base, seed=2).sample(16)
    assert s1 != s2


def test_lhs_n256_coverage_per_dim() -> None:
    """For n=256 LHS, each dim's normalised (max-min)/width should be
    ≥ (n-1)/n - epsilon. This is the T-C-002 acceptance criterion
    'max-min per dim within 1/n + epsilon'."""
    n = 256
    sampler = LHSSampler(base=ParamSpace(), seed=7)
    samples = sampler.sample(n)
    coverage = sampler.coverage(samples)
    expected_min = (n - 1) / n - 1e-6
    for dim, frac in coverage.items():
        assert frac >= expected_min, (
            f"LHS coverage shortfall on {dim!r}: {frac:.6f} < {expected_min:.6f}"
        )


def test_lhs_sampler_rejects_zero_and_negative_n() -> None:
    sampler = LHSSampler(base=ParamSpace(), seed=0)
    with pytest.raises(ValueError, match="n >= 1"):
        sampler.sample(0)
    with pytest.raises(ValueError, match="n >= 1"):
        sampler.sample(-3)


def test_lhs_samples_only_override_lhs_dims() -> None:
    """Fields NOT in LHS_DIMS must equal base.* across every sample."""
    base = ParamSpace(min_bet_size=7.25)  # not in LHS_DIMS
    samples = LHSSampler(base=base, seed=0).sample(8)
    for p in samples:
        assert p.min_bet_size == pytest.approx(7.25)
        # And the LHS dims must vary across samples (sanity).
    for dim in LHS_DIMS:
        values = {getattr(p, dim) for p in samples}
        assert len(values) == len(samples), (
            f"LHS dim {dim!r} produced duplicate values across 8 samples"
        )
