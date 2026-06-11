"""Single-lifetime sim runner.

The runner drives one agent — keyed by archetype string — through a
synthetic :class:`sim.market.MarketReplay` while bookkeeping BREATH +
USD bankroll. Each call to :meth:`Runner.simulate_lifetime` produces a
:class:`LifetimeResult` summarising how the lifetime ended. The
sweeper (``sim.sweeper``) instantiates a Runner once and calls
``simulate_lifetime`` repeatedly across LHS-sampled parameter combos x
archetypes.

Design notes
------------

The public archetype tier (:class:`sim.strategies.Pessimist` /
``Optimist`` / ``Satisficer``) ships its ``decide`` method as a sprint_2
stub raising :class:`NotImplementedError` ("sprint_2"). T-C-001's
smoke tests assert that contract — we MUST not break it. So the runner
instead dispatches via a private archetype-string → policy callable
table (:data:`_POLICIES`), and the public ``Strategy`` subclasses
continue to serve as the registry of valid archetype names only.
T-C-004 will migrate the policies back onto the ``decide`` method once
the agent.core.state.Action enum is settled.

A fourth implicit archetype — ``"random_gambler"`` — is supported to
serve the calibration objective #6 sanity check ("random gambler dies
within 2 days" per PRD §14.2). It is NOT in
:data:`sim.strategies.ARCHETYPES` because the Track C Hard Rule #2 list
of mandatory three excludes it — the random_gambler is the **control**,
not a tested archetype.

Determinism
-----------

Every lifetime is deterministic given ``(params, archetype, seed,
max_ticks)``. Internally we derive **two independent** RNG streams from
``seed`` (policy stream + Bernoulli outcome stream) so that adding or
removing an archetype-specific RNG call later does not perturb the
market replay's seed mixing. The byte-identical re-run test
(``tests/sim/test_runner.py::test_simulate_lifetime_is_byte_identical``)
asserts this directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import numpy as np

from sim.market import LookaheadError, MarketReplay
from sim.params import ParamSpace
from sim.strategies import ARCHETYPES

# ----------------------------------------------------------------------
# Outcome enum (string-typed so JSON round-trip is trivial)
# ----------------------------------------------------------------------

TERMINAL_PHASES: Final[tuple[str, ...]] = (
    # PRD §6 lifecycle: three documented death paths + survival fallback.
    # Saved as string literals so the JSON sweep report is grep-able by
    # the calibration validator.
    "Attrition",       # ran out of breath from passive burn / time tax
    "Starvation",      # entered Desperate Mode and died there
    "TradingLoss",     # bankroll collapsed below the recovery floor
    "Survival",        # max_ticks hit without death (sweep cap)
)


# ----------------------------------------------------------------------
# Result dataclass
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class LifetimeResult:
    """Per-lifetime trace summary.

    Attributes
    ----------
    archetype:
        One of ``{"pessimist", "optimist", "satisficer", "random_gambler"}``.
    seed:
        The seed passed to :meth:`Runner.simulate_lifetime`. The
        market RNG is derived from this via
        :data:`sim.market._MARKET_SEED_SALT`; the policy RNG is derived
        via :data:`_POLICY_SEED_SALT`.
    ticks_survived:
        Number of ticks the agent lived. Equal to ``len(breath_curve) - 1``
        (the curve includes the initial pre-tick balance).
    terminal_phase:
        Member of :data:`TERMINAL_PHASES`.
    breath_curve:
        Per-tick BREATH balance, starting with ``params.initial_breath``
        at index 0. Length is ``ticks_survived + 1``.
    final_bankroll:
        USD bankroll at death (or at max_ticks for Survival). Used by
        the analyser to flag :class:`TradingLoss` deaths.
    desperate_mode_entered:
        True if BREATH dropped below ``params.desperate_threshold``
        during the lifetime. Calibration objective #2 keys on the
        rate-of-trigger across archetypes.
    lung_expansion_count:
        Number of times bankroll → BREATH conversion fired during the
        lifetime. Calibration objective #4 keys on the mean across
        the sweep.
    """

    archetype: str
    seed: int
    ticks_survived: int
    terminal_phase: str
    breath_curve: list[float] = field(default_factory=list)
    final_bankroll: float = 0.0
    desperate_mode_entered: bool = False
    lung_expansion_count: int = 0


# ----------------------------------------------------------------------
# Archetype policy table
# ----------------------------------------------------------------------

# Per-archetype edge threshold (absolute deviation of price from 0.5
# required before the archetype enters a BET). Chosen so that:
#
#  * Pessimist trades rarely → lifetime bounded by passive burn rate.
#  * Optimist trades often but only on positive edge → moderate variance.
#  * Satisficer accepts low edges → frequent bets, accumulated loss.
#  * Random gambler trades every tick with a coin-flip side → fastest
#    death; the validator's "random gambler dies within 2 days" sanity
#    check keys on this.
_POLICY_EDGE_THRESHOLD: Final[dict[str, float]] = {
    "pessimist": 0.20,
    "optimist": 0.05,
    "satisficer": 0.03,
    "random_gambler": 0.0,
}

# Per-archetype bet sizing multiplier on top of params.min_bet_size.
_POLICY_BET_SIZE: Final[dict[str, float]] = {
    "pessimist": 2.0,    # bets big when it does bet
    "optimist": 1.5,
    "satisficer": 1.0,
    "random_gambler": 1.0,
}


# Salt mixed into ``seed`` to decouple the policy RNG from the market
# RNG. Picked once at ship; changing it invalidates every saved sweep.
_POLICY_SEED_SALT: Final[int] = 0xD134510E


# Type alias for archetype policy callables.
# Signature: (price, edge, rng) -> ("BET", side) | ("NO_BET", None)
PolicyFn = Callable[
    [float, float, np.random.Generator],
    tuple[str, str | None],
]


def _pessimist_policy(
    price: float, edge: float, rng: np.random.Generator
) -> tuple[str, str | None]:
    """Bet only when edge is large; otherwise NO_BET."""
    if abs(edge) < _POLICY_EDGE_THRESHOLD["pessimist"]:
        return ("NO_BET", None)
    return ("BET", "YES" if edge > 0 else "NO")


def _optimist_policy(
    price: float, edge: float, rng: np.random.Generator
) -> tuple[str, str | None]:
    """Enthusiastic bettor — small positive edge is enough."""
    if abs(edge) < _POLICY_EDGE_THRESHOLD["optimist"]:
        return ("NO_BET", None)
    return ("BET", "YES" if edge > 0 else "NO")


def _satisficer_policy(
    price: float, edge: float, rng: np.random.Generator
) -> tuple[str, str | None]:
    """First-edge-good-enough — accepts very small edges. Must die
    faster than Optimist per PRD §14.2 anti-laziness invariant."""
    if abs(edge) < _POLICY_EDGE_THRESHOLD["satisficer"]:
        return ("NO_BET", None)
    return ("BET", "YES" if edge > 0 else "NO")


def _random_gambler_policy(
    price: float, edge: float, rng: np.random.Generator
) -> tuple[str, str | None]:
    """Coin-flip side, bets every tick. The calibration control."""
    return ("BET", "YES" if rng.random() < 0.5 else "NO")


_POLICIES: Final[dict[str, PolicyFn]] = {
    "pessimist": _pessimist_policy,
    "optimist": _optimist_policy,
    "satisficer": _satisficer_policy,
    "random_gambler": _random_gambler_policy,
}

# The three mandatory archetypes per Track C Hard Rule #2, plus the
# control. The runner ACCEPTS the control; the SWEEPER (sim.sweeper)
# enforces the three-mandatory rule on the archetype list it iterates.
_VALID_ARCHETYPES: Final[frozenset[str]] = frozenset(_POLICIES.keys())

# Defensive: the three Strategy subclasses' .archetype strings must
# round-trip through _POLICIES. If a future commit renames
# Pessimist.archetype = "pessimistic", this assertion catches it at
# import time rather than at sweep time.
_REGISTRY_NAMES: Final[frozenset[str]] = frozenset(
    cls.archetype for cls in ARCHETYPES
)
assert _REGISTRY_NAMES <= _VALID_ARCHETYPES, (
    f"strategies.ARCHETYPES names {_REGISTRY_NAMES} missing from runner "
    f"policy table {_VALID_ARCHETYPES}"
)


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


class Runner:
    """Drives one agent lifetime against a :class:`MarketReplay`.

    The Runner is stateless across lifetimes — every call to
    :meth:`simulate_lifetime` builds a fresh :class:`MarketReplay` and
    fresh RNGs from the seed. Two calls with the same arguments produce
    identical results.

    Parameters
    ----------
    max_ticks:
        Hard cap on lifetime length. A lifetime reaching this cap is
        classified ``Survival`` and counted against the calibration
        objective #10 ("no immortal outcomes"). Default 10_000.
    initial_bankroll:
        USD bankroll seeded into every lifetime. PRD §14.1 anchors this
        at $100 for the sweep; held outside :class:`ParamSpace` for now
        because the LHS does not vary it in T-C-002.
    """

    DEFAULT_MAX_TICKS: Final[int] = 10_000
    DEFAULT_INITIAL_BANKROLL: Final[float] = 100.0
    # Threshold below which bankroll-collapse classifies as TradingLoss
    # rather than Attrition. 50% of initial bankroll is the PRD §6.6
    # default; held here until ParamSpace claims the dial.
    BANKROLL_LOSS_FLOOR: Final[float] = 50.0

    def __init__(
        self,
        *,
        max_ticks: int = DEFAULT_MAX_TICKS,
        initial_bankroll: float = DEFAULT_INITIAL_BANKROLL,
        market_factory: Callable[[int], MarketReplay] | None = None,
    ) -> None:
        if max_ticks < 1:
            raise ValueError(f"max_ticks must be ≥ 1, got {max_ticks}")
        self.max_ticks: int = int(max_ticks)
        self.initial_bankroll: float = float(initial_bankroll)
        # The market_factory hook lets sprint_7 T-C-004 inject a tennis-
        # cadence MarketReplay without modifying the runner's per-tick
        # logic. The default reproduces the sprint_2 / sprint_3
        # basketball-style synthetic stream so existing tests + the
        # back-compat ``run_lifetime`` wrapper stay byte-identical.
        self.market_factory: Callable[[int], MarketReplay] = (
            market_factory
            if market_factory is not None
            else (lambda seed: MarketReplay.from_synthetic(seed=seed))
        )

    def simulate_lifetime(
        self,
        *,
        params: ParamSpace,
        archetype: str,
        seed: int,
    ) -> LifetimeResult:
        """Run one full lifetime; return its :class:`LifetimeResult`.

        Determinism: same ``(params, archetype, seed)`` → identical
        result, including byte-identical ``breath_curve``.

        Raises
        ------
        ValueError
            If ``archetype`` is not in :data:`_VALID_ARCHETYPES`.
        """
        if archetype not in _VALID_ARCHETYPES:
            raise ValueError(
                f"archetype {archetype!r} not in {sorted(_VALID_ARCHETYPES)}"
            )

        market = self.market_factory(seed)
        # Two independent streams — see module docstring rationale.
        policy_rng = np.random.default_rng(seed ^ _POLICY_SEED_SALT)
        outcome_rng = np.random.default_rng(
            (seed ^ _POLICY_SEED_SALT) + 1  # +1 to fully decorrelate
        )

        policy = _POLICIES[archetype]
        breath = float(params.initial_breath)
        bankroll = float(self.initial_bankroll)
        breath_curve: list[float] = [breath]
        desperate_mode_entered = False
        lung_expansion_count = 0
        terminal_phase: str = "Attrition"  # default if max_ticks hit
        bet_size = params.min_bet_size * _POLICY_BET_SIZE[archetype]

        for _tick_idx in range(self.max_ticks):
            # Death check BEFORE the next market read — this guarantees
            # the no-lookahead contract: we never read the next tick if
            # the agent is already dead.
            if breath <= 0.0:
                terminal_phase = self._classify_death(
                    bankroll=bankroll,
                    desperate_mode_entered=desperate_mode_entered,
                )
                break

            try:
                tick = market.step()
            except LookaheadError:
                # Market stream exhausted — treat as Attrition (longest
                # observed lifetime, but still terminated).
                terminal_phase = "Attrition"
                break

            # Universal time tax — every tick costs breath.
            breath -= params.e_time_tax_per_tick

            edge = tick.price - 0.5
            action, side = policy(tick.price, edge, policy_rng)

            if action == "BET":
                # Cognitive overhead of deciding to bet.
                breath -= params.e_decision_tax
                # Outcome: YES wins with probability `tick.price`; NO
                # wins with `1 - tick.price`. Payoff per bet_size:
                #   bet YES & YES wins  → +(1 - price) * bet_size
                #   bet YES & YES loses → -price * bet_size
                #   bet NO  & YES wins  → -(1 - price) * bet_size
                #   bet NO  & YES loses → +price * bet_size
                yes_outcome = outcome_rng.random() < tick.price
                if side == "YES":
                    payoff = (1.0 - tick.price) if yes_outcome else -tick.price
                else:
                    payoff = -(1.0 - tick.price) if yes_outcome else tick.price
                bankroll += payoff * bet_size
            else:
                # NO_BET — passive burn cost (PRD §6).
                breath -= params.passive_burn_rate

            # Lung Expansion ritual — bankroll → breath if above floor.
            # Soft cap dampens above params.soft_cap_threshold.
            if bankroll > self.initial_bankroll * 1.10:
                gained = (bankroll - self.initial_bankroll) * params.conversion_rate
                if breath > params.soft_cap_threshold:
                    gained *= 0.25  # soft cap suppresses
                breath += gained
                bankroll = self.initial_bankroll
                lung_expansion_count += 1

            # Desperate Mode trigger.
            if breath < params.desperate_threshold and breath > 0.0:
                desperate_mode_entered = True

            breath_curve.append(breath)
        else:
            # Loop exhausted without break → max_ticks reached.
            terminal_phase = "Survival"

        ticks_survived = len(breath_curve) - 1
        return LifetimeResult(
            archetype=archetype,
            seed=seed,
            ticks_survived=ticks_survived,
            terminal_phase=terminal_phase,
            breath_curve=breath_curve,
            final_bankroll=bankroll,
            desperate_mode_entered=desperate_mode_entered,
            lung_expansion_count=lung_expansion_count,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_death(
        self,
        *,
        bankroll: float,
        desperate_mode_entered: bool,
    ) -> str:
        """Decide which of the three death paths a death belongs to.

        PRD §6 documents three: Attrition (slow burn), Starvation
        (Desperate Mode → death), TradingLoss (bankroll collapse).
        Classification order: TradingLoss > Starvation > Attrition.
        """
        if bankroll < self.BANKROLL_LOSS_FLOOR:
            return "TradingLoss"
        if desperate_mode_entered:
            return "Starvation"
        return "Attrition"


# ----------------------------------------------------------------------
# Back-compat module-level helper
# ----------------------------------------------------------------------


def run_lifetime(
    *,
    params: ParamSpace,
    archetype: str,
    seed: int,
    max_ticks: int = Runner.DEFAULT_MAX_TICKS,
) -> LifetimeResult:
    """Functional wrapper around :class:`Runner.simulate_lifetime`.

    Sprint_1 exposed ``run_lifetime`` as a stub raising
    :class:`NotImplementedError`. Sprint_2 replaces that stub with a
    thin wrapper around the new :class:`Runner` class so any caller that
    grabbed the stub signature continues to import cleanly. The class
    form is the preferred API.
    """
    return Runner(max_ticks=max_ticks).simulate_lifetime(
        params=params,
        archetype=archetype,
        seed=seed,
    )
