"""Analytic metrics for the backtest replay sweep — T-B-036.

Three pure functions, no I/O, no global state, dependency-free beyond
the standard-library :mod:`statistics` + :mod:`math`. They feed the
analytic fields the dashboard's workshop config-comparison view needs
(PRD §8) — sharpe, max drawdown percentage, win rate. The workshop
loop's "operator chooses a config to PROMOTE" decision rests on these
numbers being meaningful, not nan / not crashing on edge inputs.

Spec anchors
------------

* PRD.md §8 (Dashboard): "Workshop config comparison requires
  meaningful analytic metrics — sharpe + MDD + win_rate — for the
  operator to choose a config to PROMOTE."
* TECHNICAL_PLAN.md §5.4 (Data contract): ``ReplayMetrics`` exposed
  to dashboard MUST carry ``sharpe`` / ``max_drawdown_pct`` /
  ``win_rate_pct`` / ``n_decisions`` / ``n_bets``.
* CEO direction D-S11-001 §scope-decisions §3: pure-Python from the
  per-config bet ledger (no scipy / numpy / pandas — keep deps tight).
  Sharpe assumes ~10-day return horizon since ``lifetime_days=10`` is
  the sweep cadence (annualisation factor 365 / 10 = 36.5).

Design notes
------------

Every helper is **fail-soft**: empty inputs and degenerate inputs
(constant returns → zero stdev, monotonic-up equity curve, no settled
bets) return ``0.0`` rather than NaN or raising. The metrics are read
by the dashboard renderer + sweep results.json serialiser; both
prefer "no signal" over "schema-poisoning NaN". This matches PRD §8
acceptance "non-zero sharpe for configs that traded actively" — the
contrapositive is that configs that didn't trade get a clean 0.0
rather than a misleading null.

The functions are deliberately **not** tied to ``BetSettlement`` for
sharpe + MDD — they accept generic ``list[float]`` so the caller (the
:func:`agent.backtest.replay_runner.run_replay` aggregation step) can
build the return series + equity curve from whatever source it has.
:func:`compute_win_rate_pct` IS bound to the settlement model because
the void-exclusion rule is the model's invariant, not the caller's.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from agent.backtest.models import BetSettlement


def compute_sharpe(
    returns: Sequence[float],
    periods_per_year: float,
) -> float:
    """Annualised Sharpe ratio over a return series.

    ``sharpe = mean(returns) / stdev(returns) * sqrt(periods_per_year)``

    Risk-free rate is assumed zero (the BREATH agent's reference
    asset is the bankroll itself; there is no separate risk-free
    instrument in the replay world). Annualisation uses the supplied
    ``periods_per_year`` — for the sprint-9 sweep the caller passes
    ``36.5`` (365 / 10-day lifetime).

    Fail-soft on:

    * empty ``returns`` → ``0.0`` (no series to score)
    * single-element ``returns`` → ``0.0`` (stdev undefined on n=1)
    * zero stdev → ``0.0`` (degenerate "all returns identical" case;
      sharpe would be ±∞ which the JSON serialiser refuses).

    Parameters
    ----------
    returns
        Per-period returns (decimal fractions, e.g. ``0.02`` = +2 %).
    periods_per_year
        Annualisation factor; ``periods_per_year > 0`` is required.

    Returns
    -------
    float
        Annualised Sharpe ratio, or ``0.0`` on any of the fail-soft
        edge cases above. Never NaN, never raises ZeroDivisionError.
    """
    if len(returns) < 2:
        return 0.0
    stdev = statistics.stdev(returns)
    if stdev == 0.0:
        return 0.0
    mean = statistics.fmean(returns)
    return (mean / stdev) * math.sqrt(periods_per_year)


def compute_max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    """Peak-to-trough drawdown, expressed as a percentage of the peak.

    Single-pass over ``equity_curve`` tracking the running peak; for
    each subsequent value the drawdown is ``(peak - value) / peak``
    when ``peak > 0``. The returned percentage is the MAXIMUM such
    drawdown across the whole curve, in [0.0, 100.0].

    Fail-soft on:

    * empty or single-element curve → ``0.0`` (no drawdown possible)
    * monotonic-up curve → ``0.0`` (peak never falls)
    * non-positive peak — skipped in the running max so a curve that
      touches zero won't divide by zero.

    Parameters
    ----------
    equity_curve
        Bankroll values sampled at successive checkpoints. Order is
        load-bearing (chronological); units are arbitrary as long as
        they are consistent (the function reports a ratio).

    Returns
    -------
    float
        Maximum peak-to-trough drawdown as a percentage of peak. ``0.0``
        if the curve never declines.
    """
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve[1:]:
        if value > peak:
            peak = value
            continue
        if peak <= 0.0:
            # Guard against divide-by-zero on a curve that touched zero.
            # No meaningful drawdown can be reported off a zero peak.
            continue
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100.0


def compute_win_rate_pct(settlements: Sequence[BetSettlement]) -> float:
    """Win rate over decided settlements, percent.

    ``win_rate = wins / (wins + losses) * 100``

    Voids are excluded from the denominator per the brief — a voided
    market is a wash (stake refunded), not a loss, so counting it
    against the win rate would mis-represent the strategy's signal.

    Fail-soft on:

    * empty list → ``0.0``
    * all-void list → ``0.0`` (denominator collapses to zero).

    Parameters
    ----------
    settlements
        Sequence of :class:`BetSettlement` rows from the replay's
        post-loop scan; ``outcome`` field carries the win/loss/void
        triage tag set by the settlement classifier.

    Returns
    -------
    float
        Percentage of decided bets that won, in [0.0, 100.0].
    """
    wins = 0
    losses = 0
    for s in settlements:
        if s.outcome == "win":
            wins += 1
        elif s.outcome == "loss":
            losses += 1
        # voids excluded from denominator per CEO direction D-S11-001 §3
    decided = wins + losses
    if decided == 0:
        return 0.0
    return (wins / decided) * 100.0


__all__ = [
    "compute_max_drawdown_pct",
    "compute_sharpe",
    "compute_win_rate_pct",
]
