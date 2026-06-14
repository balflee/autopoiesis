"""Unit tests for the cross_market level signal (B' approach, plan step 1).

Pure logic: (slug-first surname, matched tennis-data row, best_of, k_scale)
-> a price-independent level signal in [-1, 1] from the de-vigged match-winner
consensus inverted to an implied first-set probability. Neutral 0.0 when the
orientation is ambiguous or the consensus odds are missing (fail-closed).

No network: every input is an in-memory dict, so this never touches Gamma /
tennis-data fetchers.
"""

from __future__ import annotations

from agent.backtest.cross_market_signal import (
    DEFAULT_K_SCALE,
    cross_market_signal,
    implied_first_set_prob,
)
from agent.backtest.sharp_line import implied_prob_two_way, match_to_set_prob


def _expected_signal(p_match_ref: float, best_of: int, k: float) -> float:
    p_set = match_to_set_prob(p_match_ref, best_of=best_of)
    return max(-1.0, min(1.0, (p_set - 0.5) * k))


def test_reference_is_winner_uses_avgw_as_ref_odds() -> None:
    # slug-first surname == winner -> de-vig with ref=AvgW, other=AvgL.
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
          "AvgW": 1.5, "AvgL": 2.5, "Best of": 3}
    p_match = implied_prob_two_way(1.5, 2.5)
    assert p_match is not None
    got = cross_market_signal(slug_first_surname="alcaraz", td_row=td)
    assert got == _expected_signal(p_match, 3, DEFAULT_K_SCALE)
    assert got > 0.0  # favourite -> positive level


def test_reference_is_loser_uses_avgl_as_ref_odds() -> None:
    # slug-first surname == loser -> de-vig with ref=AvgL, other=AvgW (underdog).
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
          "AvgW": 1.5, "AvgL": 2.5, "Best of": 3}
    p_match = implied_prob_two_way(2.5, 1.5)
    assert p_match is not None
    got = cross_market_signal(slug_first_surname="sinner", td_row=td)
    assert got == _expected_signal(p_match, 3, DEFAULT_K_SCALE)
    assert got < 0.0  # underdog -> negative level


def test_orientation_winner_and_loser_are_mirror_signed() -> None:
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
          "AvgW": 1.5, "AvgL": 2.5, "Best of": 3}
    w = cross_market_signal(slug_first_surname="alcaraz", td_row=td)
    loser = cross_market_signal(slug_first_surname="sinner", td_row=td)
    assert w > 0.0 > loser


def test_best_of_five_path_differs_from_three() -> None:
    td3 = {"Winner": "Djokovic N.", "Loser": "Nadal R.",
           "AvgW": 1.4, "AvgL": 3.0, "Best of": 3}
    td5 = {**td3, "Best of": 5}
    s3 = cross_market_signal(slug_first_surname="djokovic", td_row=td3)
    s5 = cross_market_signal(slug_first_surname="djokovic", td_row=td5)
    # A favourite's per-set edge is SMALLER in bo5 (more sets to win), so the
    # inverted first-set prob is closer to 0.5 -> a smaller positive signal.
    assert 0.0 < s5 < s3


def test_compound_td_surname_matched_by_single_slug_token() -> None:
    # tennis-data "Del Potro J." -> surname "delpotro"; slug captured "potro".
    td = {"Winner": "Del Potro J.", "Loser": "Federer R.",
          "AvgW": 1.8, "AvgL": 2.0, "Best of": 3}
    got = cross_market_signal(slug_first_surname="potro", td_row=td)
    p_match = implied_prob_two_way(1.8, 2.0)
    assert p_match is not None
    assert got == _expected_signal(p_match, 3, DEFAULT_K_SCALE)


def test_ambiguous_orientation_matches_neither_is_neutral() -> None:
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
          "AvgW": 1.5, "AvgL": 2.5, "Best of": 3}
    assert cross_market_signal(slug_first_surname="medvedev", td_row=td) == 0.0
    assert implied_first_set_prob(slug_first_surname="medvedev", td_row=td) is None


def test_ambiguous_orientation_matches_both_is_neutral() -> None:
    # Contrived collision: same surname both sides -> ambiguous -> fail-closed.
    td = {"Winner": "Williams S.", "Loser": "Williams V.",
          "AvgW": 1.6, "AvgL": 2.3, "Best of": 3}
    assert cross_market_signal(slug_first_surname="williams", td_row=td) == 0.0


def test_missing_consensus_odds_is_neutral() -> None:
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
          "AvgW": None, "AvgL": 2.5, "Best of": 3}
    assert cross_market_signal(slug_first_surname="alcaraz", td_row=td) == 0.0
    td_nan = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
              "AvgW": float("nan"), "AvgL": 2.5, "Best of": 3}
    assert cross_market_signal(slug_first_surname="alcaraz", td_row=td_nan) == 0.0


def test_missing_best_of_defaults_to_three() -> None:
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.", "AvgW": 1.5, "AvgL": 2.5}
    td_bad = {**td, "Best of": "oops"}
    p_match = implied_prob_two_way(1.5, 2.5)
    assert p_match is not None
    exp = _expected_signal(p_match, 3, DEFAULT_K_SCALE)
    assert cross_market_signal(slug_first_surname="alcaraz", td_row=td) == exp
    assert cross_market_signal(slug_first_surname="alcaraz", td_row=td_bad) == exp


def test_extreme_odds_clamp_to_unit_interval() -> None:
    # A massive favourite still clamps the level to <= 1.0.
    td = {"Winner": "Alcaraz C.", "Loser": "Qualifier",
          "AvgW": 1.01, "AvgL": 30.0, "Best of": 3}
    got = cross_market_signal(slug_first_surname="alcaraz", td_row=td, k_scale=5.0)
    assert got == 1.0


def test_blank_slug_surname_is_neutral() -> None:
    td = {"Winner": "Alcaraz C.", "Loser": "Sinner J.",
          "AvgW": 1.5, "AvgL": 2.5, "Best of": 3}
    assert cross_market_signal(slug_first_surname="", td_row=td) == 0.0
