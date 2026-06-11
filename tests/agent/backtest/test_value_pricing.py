"""Side-correct pricing (realism rule #3) — compute_bet_pnl + effective_entry_price.

The legacy winner formula ``size*(winning_price/entry_price - 1)`` used the
YES-leg mid for BOTH sides, paying a winning NO bet at the YES leg's odds —
81x overpaid at yes-mid 0.10, mirrored-underpaid at 0.90. The
``side_correct_pricing`` flag (default OFF = legacy byte-identical) prices the
taken leg at its effective cost: YES pays ``yes_price``, NO pays
``1 - yes_price``.
"""

from __future__ import annotations

import pytest

from agent.backtest.cached_sweep import compute_bet_pnl, effective_entry_price


class TestEffectiveEntryPrice:
    def test_yes_side_is_the_yes_mid(self) -> None:
        assert effective_entry_price(side="YES", yes_price=0.30) == 0.30

    def test_no_side_is_the_complement(self) -> None:
        assert effective_entry_price(side="NO", yes_price=0.30) == pytest.approx(0.70)


class TestSideCorrectPricing:
    def test_no_winner_at_longshot_yes_mid_pays_no_leg_odds(self) -> None:
        # THE bug: legacy paid 5*(1/0.10-1)=$45.00; correct NO cost is 0.90.
        pnl = compute_bet_pnl(
            side="NO", entry_price=0.10, size_usd=5.0,
            outcome="no", winning_price=1.0, side_correct_pricing=True,
        )
        assert pnl == pytest.approx(5.0 * (1.0 / 0.90 - 1.0))  # +$0.5556

    def test_no_winner_at_favorite_yes_mid_pays_big(self) -> None:
        pnl = compute_bet_pnl(
            side="NO", entry_price=0.90, size_usd=5.0,
            outcome="no", winning_price=1.0, side_correct_pricing=True,
        )
        assert pnl == pytest.approx(5.0 * (1.0 / 0.10 - 1.0))  # +$45.00

    def test_yes_winner_unchanged_by_flag(self) -> None:
        legacy = compute_bet_pnl(
            side="YES", entry_price=0.40, size_usd=5.0,
            outcome="yes", winning_price=1.0,
        )
        corrected = compute_bet_pnl(
            side="YES", entry_price=0.40, size_usd=5.0,
            outcome="yes", winning_price=1.0, side_correct_pricing=True,
        )
        assert corrected == pytest.approx(legacy)

    def test_loser_and_void_never_priced(self) -> None:
        assert compute_bet_pnl(
            side="NO", entry_price=0.10, size_usd=5.0,
            outcome="yes", winning_price=1.0, side_correct_pricing=True,
        ) == -5.0
        assert compute_bet_pnl(
            side="NO", entry_price=0.10, size_usd=5.0,
            outcome="void", winning_price=0.5, side_correct_pricing=True,
        ) == 0.0

    def test_cap_applies_after_side_correction(self) -> None:
        # NO at yes 0.99 → eff 0.01 → raw win $495 → capped to $100.
        pnl = compute_bet_pnl(
            side="NO", entry_price=0.99, size_usd=5.0,
            outcome="no", winning_price=1.0,
            side_correct_pricing=True, max_pnl_usd=100.0,
        )
        assert pnl == 100.0

    def test_default_flag_off_is_legacy_byte_identical(self) -> None:
        legacy = compute_bet_pnl(
            side="NO", entry_price=0.10, size_usd=5.0,
            outcome="no", winning_price=1.0,
        )
        assert legacy == pytest.approx(45.0)

    def test_degenerate_eff_zero_clips(self) -> None:
        # NO at yes 1.0 → eff 0.0 → degenerate branch: size*winning_price.
        pnl = compute_bet_pnl(
            side="NO", entry_price=1.0, size_usd=5.0,
            outcome="no", winning_price=1.0, side_correct_pricing=True,
        )
        assert pnl == 5.0
