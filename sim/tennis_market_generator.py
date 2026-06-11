"""Synthetic tennis-cadence Polymarket order-book generator.

Sprint_7 T-C-004 (NBA → Tennis pivot per PRD §15 v0.4) re-tunes the
Layer 2 calibration sim against tennis-cadence price dynamics. The
synthetic market generator that shipped in sprint_2 / sprint_3 modeled
basketball-style markets — one wide-cadence game every 2-3 hours, a
single mean-reverting price walk per stream. Tennis markets differ:

* **Match cadence is short.** Polymarket tennis maintains ~90+ active
  per-match markets across the ATP + WTA tours; on the demo cadence a
  fresh match begins roughly every 30-90 minutes (3-9 sim ticks at
  PRD's 10-min/tick anchor). The NBA market had one regime per
  ~2-3 hours (12-18 ticks).
* **Within-match volatility is higher.** Each point in a tennis match
  is its own price-moving event; the ML (match-line) probability swings
  on serve breaks + tiebreakers. The basketball stream uses
  ``σ=0.02`` Gaussian shocks; tennis bumps that to ``σ=0.05`` plus
  occasional "point-shock" spikes.
* **Order-book depth ramps within a match.** Volume builds as the
  match nears its decisive set; we model depth as roughly linear in
  the fraction of the match's lifetime, then resets when the next
  match's market opens.

Calibration objectives (PRD §14.2) are sport-agnostic, but the market
dynamics drive every objective that depends on the price walk —
desperate-mode trigger rate, lung-expansion count, bankroll volatility,
satisficer vs optimist ordering. Re-tuning the calibration on the
tennis generator is therefore necessary even if the headline parameter
set ends up close to sprint_3's basketball-calibrated values.

Determinism
-----------

Every output array is built from a seeded
:class:`numpy.random.Generator`. Same ``(seed, n_ticks)`` →
byte-identical ``(prices, depths)``. The seed is mixed with
:data:`_TENNIS_MARKET_SALT` so the tennis market RNG stream is decoupled
from both (a) the basketball synthetic stream (different salt) and
(b) the runner's policy + outcome streams (see ``sim/runner.py``).

On-chain immutables (sprint_3 redeploy)
---------------------------------------

The contracts deployed in sprint 3 (rh_chain.json
``selectedParamsHash=0x1edd...d60d``) bake the sprint_3 calibrated
constants — INITIAL_BREATH=1132, DESPERATE_THRESHOLD=201,
PASSIVE_BURN_RATE=1, SOFT_CAP_THRESHOLD=3175, etc. The tennis sim is
free to explore the full LHS_BOUNDS; any selected parameter set that
diverges from the deployed constants is logged as a **v2 redeploy
candidate** in ``CALIBRATION_REPORT.md`` (sprint_3 deploys are
immutable per task brief "Cannot propose changes to deployed
contracts").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from sim.market import MarketReplay

# Salt mixed into ``seed`` when building the tennis market so the
# tennis-cadence RNG stream is decoupled from both the basketball
# synthetic stream (which uses _MARKET_SEED_SALT in sim.market) and
# the runner's policy / outcome streams (which use their own salts).
# Picked once on ship — changing it invalidates every saved
# sprint7_tennis determinism receipt.
_TENNIS_MARKET_SALT: Final[int] = 0xC4A5_5E11  # "tennis"-ish hex

# Default length of the synthetic stream. 20K ticks > the runner's
# 2000-tick max_ticks cap so a single lifetime never runs out of market.
_DEFAULT_N_TICKS: Final[int] = 20_000

# Match cadence — number of sim ticks per ATP/WTA match. PRD §14.3
# tennis cadence: matches every 30-90 min on the demo cadence; at the
# PRD §6 10-min/tick anchor that is 3-9 ticks per match. We sample
# uniformly inside that range each match so the stream alternates
# between fast-cadence days and slow-cadence days organically.
_MATCH_TICKS_LOW: Final[int] = 3
_MATCH_TICKS_HIGH: Final[int] = 9

# Per-tick within-match price volatility. Tuned 2.5× the basketball
# (sprint_2) ``σ=0.02`` baseline to reflect the higher per-point ML
# swing observed in tennis order books (sprint_7 prep notes).
_WITHIN_MATCH_SIGMA: Final[float] = 0.05

# Probability per tick of a "point-shock" — a single-tick discrete
# Bernoulli probability jump emulating a break-of-serve or tiebreak
# turning point. Magnitude is small (~0.07) so the shock matters but
# doesn't dominate.
_POINT_SHOCK_RATE: Final[float] = 0.08
_POINT_SHOCK_MAGNITUDE: Final[float] = 0.07

# Order-book depth ranges. Basketball used a flat U[50, 500] per tick;
# tennis depth ramps linearly from a thinner opening to a thicker
# decisive-set level inside each match. The ramp is reset at each
# match boundary, replicating the empirical pattern that volume builds
# as the result becomes more imminent.
_DEPTH_OPEN_LOW: Final[float] = 30.0
_DEPTH_OPEN_HIGH: Final[float] = 120.0
_DEPTH_CLOSE_LOW: Final[float] = 200.0
_DEPTH_CLOSE_HIGH: Final[float] = 700.0

# Price clipping bounds — identical to basketball stream so the
# downstream LookaheadError invariant is unchanged.
_PRICE_FLOOR: Final[float] = 0.05
_PRICE_CEIL: Final[float] = 0.95


@dataclass(frozen=True)
class TennisMarketArrays:
    """Container returned by :func:`build_arrays` — paired ``(prices,
    depths)`` numpy arrays plus the match-boundary indices for
    diagnostics.

    The ``match_boundaries`` array is exposed so reviewers + tests can
    confirm the stream actually emitted multiple matches at the
    expected cadence; the runner itself ignores this field.
    """

    prices: np.ndarray
    depths: np.ndarray
    match_boundaries: np.ndarray


def build_arrays(
    seed: int,
    n_ticks: int = _DEFAULT_N_TICKS,
) -> TennisMarketArrays:
    """Build the synthetic tennis-cadence price + depth arrays.

    Parameters
    ----------
    seed:
        Deterministic seed. Same value → identical arrays.
    n_ticks:
        Stream length in ticks. Default 20_000 (~139 days at 10-min
        per tick, anchor per PRD §6). Must exceed the runner's
        ``max_ticks`` cap so a lifetime never exhausts the stream.

    Returns
    -------
    TennisMarketArrays
        ``prices``: float64 array of length ``n_ticks``, each in
        ``[_PRICE_FLOOR, _PRICE_CEIL]``.
        ``depths``: float64 array of length ``n_ticks``, monotonically
        ramping inside each match.
        ``match_boundaries``: int64 array of tick indices at which a
        new match begins.
    """
    if n_ticks < 1:
        raise ValueError(f"n_ticks must be ≥ 1, got {n_ticks}")

    rng = np.random.default_rng(seed ^ _TENNIS_MARKET_SALT)

    prices = np.empty(n_ticks, dtype=np.float64)
    depths = np.empty(n_ticks, dtype=np.float64)
    boundaries: list[int] = []

    tick = 0
    while tick < n_ticks:
        # Start a new match: pick its tick-length + freshly sample a
        # baseline ML probability + open/close depth anchors.
        match_len = int(rng.integers(_MATCH_TICKS_LOW, _MATCH_TICKS_HIGH + 1))
        match_end = min(tick + match_len, n_ticks)
        boundaries.append(tick)

        # Match baseline drawn from Beta(2, 2) → centered around 0.5
        # with mild concentration; clipped to keep the price walk away
        # from the degenerate extremes.
        baseline = float(
            np.clip(rng.beta(2.0, 2.0), _PRICE_FLOOR, _PRICE_CEIL)
        )
        # Per-match depth anchors. Tennis volume scales with prestige,
        # so we let depth land anywhere in the open/close bands.
        depth_open = float(rng.uniform(_DEPTH_OPEN_LOW, _DEPTH_OPEN_HIGH))
        depth_close = float(rng.uniform(_DEPTH_CLOSE_LOW, _DEPTH_CLOSE_HIGH))

        p = baseline
        for j in range(tick, match_end):
            # Within-match shock + mean reversion to the match baseline.
            # Coefficient 0.85 on baseline preserves the basketball
            # "weak pull" behavior; the larger σ + occasional point
            # shocks give tennis its higher within-match variance.
            shock = float(rng.normal(loc=0.0, scale=_WITHIN_MATCH_SIGMA))
            if rng.random() < _POINT_SHOCK_RATE:
                shock += float(
                    _POINT_SHOCK_MAGNITUDE * (1.0 if rng.random() < 0.5 else -1.0)
                )
            p = 0.85 * p + 0.15 * baseline + shock
            p = float(np.clip(p, _PRICE_FLOOR, _PRICE_CEIL))
            prices[j] = p

            # Depth ramps linearly from open → close across the match.
            denom = max(match_end - tick - 1, 1)
            frac = (j - tick) / denom
            depths[j] = depth_open + (depth_close - depth_open) * frac

        tick = match_end

    return TennisMarketArrays(
        prices=prices,
        depths=depths,
        match_boundaries=np.asarray(boundaries, dtype=np.int64),
    )


def build_replay(
    seed: int,
    n_ticks: int = _DEFAULT_N_TICKS,
) -> MarketReplay:
    """Build a :class:`MarketReplay` over the tennis-cadence arrays.

    Convenience wrapper used as ``market_factory`` by
    :class:`sim.runner.Runner`. The Runner calls ``factory(seed)``
    positionally so ``seed`` is positional here; ``n_ticks`` stays
    keyword-overridable for ``functools.partial`` plumbing in tests.
    The Runner already enforces the no-look-ahead contract on top of
    any :class:`MarketReplay` instance, so the tennis factory inherits
    the same guarantees without re-implementing them.
    """
    arrays = build_arrays(seed, n_ticks=n_ticks)
    return MarketReplay.from_arrays(prices=arrays.prices, depths=arrays.depths)


__all__ = [
    "TennisMarketArrays",
    "build_arrays",
    "build_replay",
]
