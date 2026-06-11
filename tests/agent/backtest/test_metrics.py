"""Unit tests for :mod:`agent.backtest.metrics` — T-B-036.

Six tests, one per acceptance criterion in the task brief:

1. ``compute_sharpe`` against a known-input return series; expected
   value is computed offline (``mean / stdev * sqrt(periods_per_year)``)
   and asserted to 6 decimal places.
2. ``compute_max_drawdown_pct`` on a monotonic-up curve returns ``0.0``.
3. ``compute_max_drawdown_pct`` on a curve with a real drawdown returns
   the peak-to-trough percentage of peak (``33.33`` for ``[100, 120, 80,
   110]``).
4. ``compute_win_rate_pct`` excludes voids from the denominator
   (3 wins / 2 losses / 1 void → ``60.0``).
5. Empty inputs return ``0.0`` (not NaN, not ZeroDivisionError).
6. ``compute_sharpe`` with a zero-stdev return series returns ``0.0``
   (no divide-by-zero).

All asserts use ``pytest.approx`` with an explicit tolerance to avoid
brittle float equality. The 6-dp tolerance on the sharpe test mirrors
the brief's "asserted to 6dp" wording exactly.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent.backtest.metrics import (
    compute_max_drawdown_pct,
    compute_sharpe,
    compute_win_rate_pct,
)
from agent.backtest.models import BetOutcomeLiteral, BetSettlement


# --------------------------------------------------------------------------- #
# Shared fixtures — :class:`BetSettlement` builder.
# --------------------------------------------------------------------------- #


def _settlement(
    *,
    bet_id: str,
    outcome: BetOutcomeLiteral,
    pnl: str = "0",
) -> BetSettlement:
    """Build a minimal :class:`BetSettlement` for win_rate tests.

    The model's other fields don't affect the metrics under test —
    only ``outcome`` matters for win_rate; ``pnl_usd`` only matters
    for tests that hand-build an equity curve, which the brief's
    cases do separately as plain floats.
    """
    return BetSettlement(
        bet_id=bet_id,
        market_id="0xtest",
        settled_ts=datetime(2026, 5, 1, tzinfo=UTC),
        stake_usd=Decimal("10.0"),
        payout_usd=Decimal(pnl),
        pnl_usd=Decimal(pnl),
        outcome=outcome,
    )


# --------------------------------------------------------------------------- #
# 1. Sharpe — known input
# --------------------------------------------------------------------------- #


def test_sharpe_known_input() -> None:
    """Asserts the canonical sharpe formula against an offline-computed value.

    Inputs (from the brief): ``returns=[0.01, 0.02, -0.005]``,
    ``periods_per_year=36.5`` (the sprint-9 sweep cadence: 365 days /
    10-day lifetime).

    Offline derivation::

        mean   = (0.01 + 0.02 - 0.005) / 3                = 0.008333...
        stdev  = sqrt(sum((r - mean)**2) / (n - 1))       = 0.012583...
        sharpe = (mean / stdev) * sqrt(36.5)              = 4.001096...

    Asserted to 6 decimal places per the brief.
    """
    returns = [0.01, 0.02, -0.005]
    periods_per_year = 36.5

    # Cross-check the expected value against the standard-library formula
    # so a future Python whose statistics module subtly changes is
    # caught at runtime rather than via a stale hard-coded float.
    expected = (
        statistics.fmean(returns)
        / statistics.stdev(returns)
        * math.sqrt(periods_per_year)
    )

    result = compute_sharpe(returns=returns, periods_per_year=periods_per_year)

    assert result == pytest.approx(expected, abs=1e-6)
    # And against the hand-derived literal so the test docstring stays
    # the source of truth for the canonical sprint-9 sharpe number.
    assert result == pytest.approx(4.001096, abs=1e-6)


# --------------------------------------------------------------------------- #
# 2. Max drawdown — monotonic-up curve
# --------------------------------------------------------------------------- #


def test_max_drawdown_pct_monotonic_up() -> None:
    """A strictly increasing curve has zero drawdown by definition."""
    equity_curve = [100.0, 110.0, 120.0, 130.0]
    assert compute_max_drawdown_pct(equity_curve) == 0.0


# --------------------------------------------------------------------------- #
# 3. Max drawdown — real drawdown
# --------------------------------------------------------------------------- #


def test_max_drawdown_pct_real_drawdown() -> None:
    """Peak 120 → trough 80 = drawdown of (120 - 80) / 120 = 33.33...%.

    The subsequent recovery to 110 does not erase the historic
    drawdown — the metric is the WORST observed peak-to-trough across
    the curve, not the end-to-end loss.
    """
    equity_curve = [100.0, 120.0, 80.0, 110.0]
    result = compute_max_drawdown_pct(equity_curve)
    # 40/120 * 100 = 33.33333...
    assert result == pytest.approx(33.333333, abs=1e-4)


# --------------------------------------------------------------------------- #
# 4. Win rate — voids excluded
# --------------------------------------------------------------------------- #


def test_win_rate_pct_excludes_voids() -> None:
    """3 wins / 2 losses / 1 void → 60.0% — void NOT in the denominator."""
    settlements = [
        _settlement(bet_id="w1", outcome="win"),
        _settlement(bet_id="w2", outcome="win"),
        _settlement(bet_id="w3", outcome="win"),
        _settlement(bet_id="l1", outcome="loss"),
        _settlement(bet_id="l2", outcome="loss"),
        _settlement(bet_id="v1", outcome="void"),
    ]
    result = compute_win_rate_pct(settlements)
    # 3 / (3 + 2) * 100 = 60.0 — NOT 3 / 6 * 100 = 50.0.
    assert result == pytest.approx(60.0)


# --------------------------------------------------------------------------- #
# 5. Empty inputs — fail-soft to 0.0 (not NaN)
# --------------------------------------------------------------------------- #


def test_empty_inputs_return_zero_not_nan() -> None:
    """All three metrics return a clean ``0.0`` on empty inputs.

    Critical for the dashboard renderer: a JSON-serialised NaN poisons
    the workshop config-comparison panel (browsers refuse the literal
    ``NaN``); 0.0 lets the operator see "this config didn't trade"
    rather than a render failure.
    """
    assert compute_sharpe(returns=[], periods_per_year=36.5) == 0.0
    assert compute_max_drawdown_pct(equity_curve=[]) == 0.0
    assert compute_win_rate_pct(settlements=[]) == 0.0

    # Defensive: confirm none of the results is NaN — math.isnan would
    # return False on a clean 0.0 anyway, but the explicit guard is the
    # documentation the test exists to preserve.
    assert not math.isnan(compute_sharpe(returns=[], periods_per_year=36.5))
    assert not math.isnan(compute_max_drawdown_pct(equity_curve=[]))
    assert not math.isnan(compute_win_rate_pct(settlements=[]))


# --------------------------------------------------------------------------- #
# 6. Sharpe — zero stdev guards divide-by-zero
# --------------------------------------------------------------------------- #


def test_sharpe_zero_stdev_returns_zero() -> None:
    """Constant returns ⇒ stdev=0 ⇒ degenerate sharpe ⇒ 0.0.

    A strict ``mean / stdev`` would raise ``ZeroDivisionError`` or
    return ``±inf``; either kills the JSON serialiser. The metrics
    module's contract is to fail-soft to 0.0.
    """
    constant_returns = [0.01, 0.01, 0.01]
    assert compute_sharpe(
        returns=constant_returns, periods_per_year=36.5
    ) == 0.0
