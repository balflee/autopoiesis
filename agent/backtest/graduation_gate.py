"""Stage-2 graduation gate (plan-loop V1.6) — the anti-manufactured-edge guard.

`gain` is the **net ROI per $ staked** (monetary edge is primary; Brier is only a
diagnostic, computed elsewhere). A candidate graduates ONLY if it clears BOTH:

1. ``gain >= threshold`` (default 0.2), AND
2. it beats a **market-efficiency placebo** — under the null "no edge," a bet wins
   with probability equal to the price paid (`entry_price`), so the bettor has no
   information the market lacks. We resample each bet's win/loss from
   ``Bernoulli(entry_price)`` ``n_placebo`` times to build the null ROI distribution
   and require the real ROI to exceed its 95th percentile. A shuffled / zero-edge
   input therefore returns ``NO_GO`` — the guard that stops a synthetic or
   cost-blind backtest from "manufacturing a graduation" (Codex; A18 +pre-fee/
   −post-fee trap). All randomness is seeded → reproducible.

Costs are explicit: each :class:`ProbeBet` carries the ``cost_usd`` (fee + half-
spread/crossing) actually charged, so the ROI is net-of-cost by construction.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

DEFAULT_THRESHOLD = 0.2
DEFAULT_N_PLACEBO = 2000


@dataclass(frozen=True)
class ProbeBet:
    """One resolved paper bet, cost-tagged.

    ``entry_price`` is the price paid for the taken side's token in (0, 1].
    ``won`` is whether the taken side resolved as the winner. ``cost_usd`` is the
    fee + half-spread/crossing actually charged (>= 0).
    """

    stake_usd: float
    entry_price: float
    won: bool
    cost_usd: float = 0.0


@dataclass(frozen=True)
class GraduationResult:
    gain: float
    n_bets: int
    threshold: float
    placebo_p95: float
    beats_placebo: bool
    verdict: str  # "GO" | "NO_GO"


def _profit(stake: float, entry_price: float, won: bool, cost: float) -> float:
    """Realized net profit of one bet (buy ``stake/entry_price`` shares @ entry_price;
    each share pays $1 on a win, $0 on a loss), minus cost."""
    if entry_price <= 0.0:
        raise ValueError("entry_price must be > 0")
    gross = stake * (1.0 / entry_price - 1.0) if won else -stake
    return gross - cost


def _roi(bets: list[ProbeBet]) -> float:
    total_stake = sum(b.stake_usd for b in bets)
    if total_stake <= 0.0:
        return 0.0
    total_profit = sum(
        _profit(b.stake_usd, b.entry_price, b.won, b.cost_usd) for b in bets
    )
    return total_profit / total_stake


def evaluate_graduation(
    bets: list[ProbeBet],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    n_placebo: int = DEFAULT_N_PLACEBO,
    seed: int = 0,
) -> GraduationResult:
    """Return the graduation verdict. ``GO`` iff ``gain >= threshold`` AND the real
    ROI beats the market-efficiency placebo's 95th percentile."""
    if not bets:
        return GraduationResult(
            gain=0.0, n_bets=0, threshold=threshold, placebo_p95=0.0,
            beats_placebo=False, verdict="NO_GO",
        )

    gain = _roi(bets)

    rng = random.Random(seed)
    null_rois: list[float] = []
    for _ in range(n_placebo):
        shuffled = [
            ProbeBet(
                stake_usd=b.stake_usd,
                entry_price=b.entry_price,
                won=(rng.random() < b.entry_price),  # null: win-rate == price
                cost_usd=b.cost_usd,
            )
            for b in bets
        ]
        null_rois.append(_roi(shuffled))
    null_rois.sort()
    # 95th percentile (nearest-rank).
    idx = min(len(null_rois) - 1, int(0.95 * len(null_rois)))
    placebo_p95 = null_rois[idx]

    beats_placebo = gain > placebo_p95
    verdict = "GO" if (gain >= threshold and beats_placebo) else "NO_GO"
    return GraduationResult(
        gain=gain,
        n_bets=len(bets),
        threshold=threshold,
        placebo_p95=placebo_p95,
        beats_placebo=beats_placebo,
        verdict=verdict,
    )


__all__ = ["DEFAULT_THRESHOLD", "GraduationResult", "ProbeBet", "evaluate_graduation"]
