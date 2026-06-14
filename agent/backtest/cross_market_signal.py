"""cross_market — the B' 6th signal as a price-independent LEVEL (plan step 1).

A18 ([[project_a18_setprob_signal]]) showed the match-winner CONSENSUS
(de-vigged AvgW/AvgL) inverted to an implied first-set probability is a more
accurate read of the first-set than Polymarket's own thin first-set price. This
module turns that into a single, price-independent level signal that the value
model tilts by (``p_model = price + kappa*fused + kappa_xm*cross_market_signal``).

Pure functions only — no network, no fetchers. The augment script
(``scripts/setprob_augment.py``) supplies the slug-first surname (from
``tennis_match_resolver.parse_slug``) and the matched tennis-data row.

Orientation is recovered OFFLINE from the slug alone: the first surname in a
first-set market's ``-<A>-vs-<B>`` slug suffix is the YES player (Polymarket
``outcomes[0]`` convention — empirical, not contractual; see the design plan).
We name-match that surname to the tennis-data Winner/Loser to pick AvgW vs AvgL.
The match RESULT is never read here (``y`` is for backtest scoring only), so the
signal is constructed purely from pre-match consensus odds — no look-ahead.

Fail-closed: ambiguous orientation (the slug surname matches both players or
neither) or missing/!numeric consensus odds -> neutral 0.0.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent.backtest.sharp_line import (
    implied_prob_two_way,
    match_to_set_prob,
    tennis_data_surname,
)

#: [-0.5, +0.5] implied-prob deviation -> [-1, 1] level (sweepable upstream).
DEFAULT_K_SCALE = 2.0


def _coerce_best_of(raw: object) -> int:
    """``Best of`` -> int, defaulting to 3 (handles int / float / numeric str)."""
    if isinstance(raw, bool):  # guard: bool is an int subclass
        return 3
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(float(raw))
        except ValueError:
            return 3
    return 3


def _surname_eq(a: str, b: str) -> bool:
    """True iff two normalised surnames are the same player.

    Bidirectional suffix match so a single slug token (``potro``) matches a
    compound tennis-data surname (``delpotro``) and vice-versa. Both must be
    non-empty.
    """
    if not a or not b:
        return False
    return a == b or a.endswith(b) or b.endswith(a)


def implied_first_set_prob(
    *, slug_first_surname: str, td_row: Mapping[str, object]
) -> float | None:
    """P(slug-first player wins the FIRST set), or ``None`` (fail-closed).

    De-vigs the matched tennis-data row's two-way consensus (AvgW/AvgL) on the
    reference (slug-first/YES) side, then inverts the best-of-N match model to
    the implied per-set probability. Returns ``None`` when orientation is
    ambiguous (the slug surname matches both players or neither) or the
    consensus odds are missing / non-numeric.
    """
    ref = slug_first_surname or ""
    w_sn = tennis_data_surname(str(td_row.get("Winner", ""))) or ""
    l_sn = tennis_data_surname(str(td_row.get("Loser", ""))) or ""
    matches_w = _surname_eq(ref, w_sn)
    matches_l = _surname_eq(ref, l_sn)
    if matches_w == matches_l:  # both, or neither -> ambiguous
        return None
    if matches_w:
        p_match = implied_prob_two_way(td_row.get("AvgW"), td_row.get("AvgL"))
    else:
        p_match = implied_prob_two_way(td_row.get("AvgL"), td_row.get("AvgW"))
    if p_match is None:
        return None
    return match_to_set_prob(p_match, best_of=_coerce_best_of(td_row.get("Best of")))


def cross_market_signal(
    *,
    slug_first_surname: str,
    td_row: Mapping[str, object],
    k_scale: float = DEFAULT_K_SCALE,
) -> float:
    """Price-independent level in ``[-1, 1]``; neutral ``0.0`` when fail-closed.

    ``clamp((p_set_implied - 0.5) * k_scale, -1, 1)`` — positive = the consensus
    implies the slug-first (YES) player is favoured to take the first set.
    """
    p_set = implied_first_set_prob(
        slug_first_surname=slug_first_surname, td_row=td_row
    )
    if p_set is None:
        return 0.0
    return max(-1.0, min(1.0, (p_set - 0.5) * k_scale))
