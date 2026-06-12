"""The tribute mechanism (A7): money for breath, the gods always get paid.

World rule, not agent behavior: at the moment of death the loop consults an
optional :class:`TributePolicy`. The OFFER is the policy's choice (a
disclosed scripted reflex on the control leg, an LLM decision on the
treatment leg); the DICE belong to the gods (a seeded RNG owned by the
orchestrator). The tribute is consumed win or lose — it is an offering, not
a purchase. With no policy wired (the default, and the only live-runtime
configuration) the death check is byte-identical to the bare loop.
"""

from __future__ import annotations

from typing import Final, Protocol

TRIBUTE_MIN_USD: Final[float] = 500.0
TRIBUTE_FULL_USD: Final[float] = 2000.0
_P_FLOOR: Final[float] = 0.30
_P_CAP: Final[float] = 0.99

__all__ = [
    "TRIBUTE_FULL_USD",
    "TRIBUTE_MIN_USD",
    "ReflexTributePolicy",
    "TributePolicy",
    "tribute_success_probability",
]


def tribute_success_probability(amount_usd: float) -> float:
    """The gods' price list: $500 → 0.30, slope 0.70 per $1,500, CAPPED at
    0.99 — the gods never guarantee ($1,250 → 0.65; the uncapped line
    reaches 1.00 at $2,000, the cap shaves it to 0.99). Below the floor the
    offering is REFUSED."""
    if amount_usd < TRIBUTE_MIN_USD:
        raise ValueError(
            f"the gods refuse offerings below ${TRIBUTE_MIN_USD:.0f}"
        )
    frac = (amount_usd - TRIBUTE_MIN_USD) / (TRIBUTE_FULL_USD - TRIBUTE_MIN_USD)
    return min(_P_CAP, _P_FLOOR + 0.70 * min(1.0, frac))


class TributePolicy(Protocol):
    """Deathbed decision: how much to offer (``None`` = accept death)."""

    async def on_dying(
        self, *, tick: int, breath: float, bankroll_usd: float
    ) -> float | None: ...


class ReflexTributePolicy:
    """The control leg's DISCLOSED scripted baseline: when dying with at
    least the gods' floor in the bank, offer ``min($2,000, bankroll)``.

    A baseline policy in the same spirit as the always-favorite archetype —
    it is published as scripted, never claimed as emergent behavior."""

    async def on_dying(
        self, *, tick: int, breath: float, bankroll_usd: float
    ) -> float | None:
        if bankroll_usd < TRIBUTE_MIN_USD:
            return None
        return min(TRIBUTE_FULL_USD, bankroll_usd)
