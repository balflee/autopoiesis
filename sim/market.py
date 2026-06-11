"""Market replay with a strict no-look-ahead contract.

The calibration sim never hits the live Polymarket API. Instead it
replays either (a) Track E historical parquet (future sprint), or
(b) a deterministic **synthetic** order-book stream — the
sprint_2 (T-C-002) path. Both expose the same :class:`MarketReplay`
facade so the runner is agnostic to which source it's consuming.

No-look-ahead contract (PRD §14 + DEV_FRAMEWORK §26 T2.7)
---------------------------------------------------------

The sim's calibration validity depends on the agent only seeing market
state up to its decision tick — the same Hard Rule #3 from the Track C
role doc and the T-C-002 task brief ("sim/market.py NEVER reads any
data > current tick — assert at every .step() call").

Enforced two ways:

1. :meth:`MarketReplay.step` advances a private cursor by exactly one
   tick and emits the now-current ``(price, depth)``. The runner has no
   API to peek at future ticks.
2. :meth:`MarketReplay.tick_at` is a **historical** lookup; passing an
   index ``> current_index`` raises :class:`LookaheadError`. The runner
   never calls this, but the calibration validator does — it scans the
   trace for any forward read and fails the run if one slipped in.

Determinism
-----------

:meth:`MarketReplay.from_synthetic` builds the price + depth arrays
eagerly from a seeded :class:`numpy.random.Generator`. Same seed → same
arrays, bit-for-bit. The runner's own RNG is separately seeded; we
intentionally **decouple** the market seed (via ``seed ^ 0xA0BE7517`` —
an arbitrary fixed constant) so the runner's decision randomness does
not perfectly correlate with the market shocks, which would degrade the
sweep's signal-to-noise on archetype skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

# Constant mixed into ``seed`` when building the synthetic market so the
# market RNG stream is decoupled from the runner's policy RNG stream.
# Picked once (T-C-002 ship) and frozen; changing it invalidates every
# saved sweep_<ts>.json determinism receipt.
_MARKET_SEED_SALT: Final[int] = 0xA0BE7517

# Default synthetic series length. 20K ticks is comfortably more than the
# 10K max_ticks cap on a single lifetime, so the runner never reaches the
# end of the stream during normal calibration. If a lifetime DOES exhaust
# it (e.g. an unusually long-lived Pessimist), the runner falls back to
# an "Attrition" terminal phase per the lifetime contract.
_DEFAULT_N_TICKS: Final[int] = 20_000


class LookaheadError(RuntimeError):
    """Raised when a caller attempts to read market state ahead of the
    current cursor. Track C Hard Rule #3 — any occurrence in a saved
    sweep trace is an automatic calibration FAIL.
    """


@dataclass(frozen=True)
class MarketTick:
    """One row of replay data — sprint_2 minimal shape.

    Carries the price + depth pair plus the tick index that emitted it,
    so the runner's trace can later be diffed against the source
    deterministically (the validator re-runs the synthetic factory with
    the same seed and confirms the recorded tick indices match).
    """

    tick: int
    price: float
    depth: float


class MarketReplay:
    """Replay-only market facade. **Never** hits any live API.

    The constructor is intentionally not a public API — call
    :meth:`from_synthetic` or :meth:`from_arrays` instead so the
    no-look-ahead invariants attach to a known data source.
    """

    __slots__ = ("_cursor", "_depths", "_n", "_prices")

    def __init__(
        self,
        *,
        prices: np.ndarray,
        depths: np.ndarray,
    ) -> None:
        if prices.shape != depths.shape:
            raise ValueError(
                f"prices/depths shape mismatch: {prices.shape} vs {depths.shape}"
            )
        if prices.ndim != 1:
            raise ValueError(
                f"prices must be 1-D, got shape {prices.shape}"
            )
        if prices.size == 0:
            raise ValueError("MarketReplay requires at least one tick")

        # Defensive copy + dtype lock — protects against external array
        # mutation invalidating the determinism receipt mid-run.
        self._prices: np.ndarray = np.ascontiguousarray(prices, dtype=np.float64).copy()
        self._depths: np.ndarray = np.ascontiguousarray(depths, dtype=np.float64).copy()
        self._prices.setflags(write=False)
        self._depths.setflags(write=False)
        self._n: int = int(prices.size)
        # Cursor starts at -1 — "before the first tick". First step()
        # advances to 0.
        self._cursor: int = -1

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_synthetic(
        cls,
        *,
        seed: int,
        n_ticks: int = _DEFAULT_N_TICKS,
    ) -> MarketReplay:
        """Build a synthetic order-book stream from a seeded RNG.

        The price follows a mean-reverting random walk around 0.5, clipped
        to ``[0.05, 0.95]`` so YES/NO probabilities never degenerate.
        Depth is uniform on ``[50, 500]`` USD per side. Both choices are
        documented in PRD §14.1; the LHS sweep's archetype distinction
        depends on the price walk being noisy enough to differentiate
        policies but bounded enough to keep lifetimes < 7 days.

        Parameters
        ----------
        seed:
            Deterministic seed. Same value → same arrays, bit-for-bit.
            Mixed with :data:`_MARKET_SEED_SALT` so the market RNG stream
            is decoupled from the runner's policy stream.
        n_ticks:
            Length of the synthetic series. Must exceed the runner's
            ``max_ticks`` cap, else lifetimes can be artificially
            truncated by market exhaustion.
        """
        if n_ticks < 1:
            raise ValueError(f"n_ticks must be ≥ 1, got {n_ticks}")
        rng = np.random.default_rng(seed ^ _MARKET_SEED_SALT)

        # Mean-reverting walk: small i.i.d. shocks, weak pull to 0.5.
        shocks = rng.normal(loc=0.0, scale=0.02, size=n_ticks)
        prices = np.empty(n_ticks, dtype=np.float64)
        p = 0.5
        for i in range(n_ticks):
            # Vectorised would be marginally faster but obscures the
            # mean-reversion intent. n_ticks is small (20K) — cost is
            # ~2ms per replay construction.
            p = 0.95 * p + 0.05 * 0.5 + shocks[i]
            p = float(np.clip(p, 0.05, 0.95))
            prices[i] = p

        depths = rng.uniform(low=50.0, high=500.0, size=n_ticks)
        return cls(prices=prices, depths=depths)

    @classmethod
    def from_arrays(
        cls,
        *,
        prices: np.ndarray,
        depths: np.ndarray,
    ) -> MarketReplay:
        """Construct directly from pre-computed arrays.

        Used by tests + by the future Track E parquet loader. The arrays
        are copied internally; mutating the originals does NOT affect
        the replay.
        """
        return cls(prices=prices, depths=depths)

    # ------------------------------------------------------------------
    # Iteration API — the runner only ever calls `step()`
    # ------------------------------------------------------------------

    @property
    def current_index(self) -> int:
        """Index of the most recently returned tick. ``-1`` before any
        :meth:`step` call."""
        return self._cursor

    @property
    def n_ticks(self) -> int:
        """Total length of the underlying stream."""
        return self._n

    @property
    def exhausted(self) -> bool:
        """True once the cursor has consumed the last tick."""
        return self._cursor >= self._n - 1

    def step(self) -> MarketTick:
        """Advance the cursor by one and return the now-current tick.

        Enforces the no-look-ahead invariant explicitly: ``_cursor`` is
        incremented BEFORE the array read, so any attempt to read
        beyond the published cursor would itself be a bug in this
        method — the post-condition ``self._cursor < self._n`` is
        asserted on every call.

        Raises
        ------
        LookaheadError
            If the stream is already exhausted. Distinguishing
            exhaustion from look-ahead is intentional: the runner
            catches this and ends the lifetime as Attrition, but a saved
            trace replay that hits exhaustion at a different cursor
            position would be a determinism violation.
        """
        if self._cursor >= self._n - 1:
            raise LookaheadError(
                f"MarketReplay exhausted at cursor={self._cursor} "
                f"(n_ticks={self._n}); refusing to peek past end"
            )
        self._cursor += 1
        # Defensive invariant — this assertion is the "assert at every
        # .step() call" required by the T-C-002 brief's no_lookahead gate.
        # If it ever fails, we have a logic bug; the trace is invalid.
        assert 0 <= self._cursor < self._n, (
            f"cursor {self._cursor} out of bounds [0, {self._n})"
        )
        return MarketTick(
            tick=self._cursor,
            price=float(self._prices[self._cursor]),
            depth=float(self._depths[self._cursor]),
        )

    def tick_at(self, index: int) -> MarketTick:
        """Historical lookup — index MUST be ``<= current_index``.

        The runner does not call this; the calibration validator does,
        when scanning saved traces for any forward read. Raises
        :class:`LookaheadError` on ``index > current_index`` so the
        validator can catch the violation deterministically.
        """
        if not isinstance(index, int):
            raise TypeError(f"index must be int, got {type(index).__name__}")
        if index < 0:
            raise IndexError(f"index must be non-negative, got {index}")
        if index > self._cursor:
            raise LookaheadError(
                f"tick_at({index}) would peek past current_index={self._cursor}"
            )
        return MarketTick(
            tick=index,
            price=float(self._prices[index]),
            depth=float(self._depths[index]),
        )

    # ------------------------------------------------------------------
    # Back-compat — preserves the sprint_1 HistoricalMarket name so any
    # downstream module still importing it does not break.
    # ------------------------------------------------------------------


# Sprint_1 alias kept for back-compat; HistoricalMarket WILL be deprecated
# once Track E's parquet loader ships and consolidates around MarketReplay.
HistoricalMarket = MarketReplay
