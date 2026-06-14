# tests/agent/backtest/test_setprob_augment.py
"""TDD tests for scripts/setprob_augment.py — Task 7a.

All tests are OFFLINE: no network, no Excel files. We inject fake tennis-data
indices and fake SignalRows so the core pure functions can be verified without
any I/O. The end-to-end test writes a tiny JSON to a tmp_path and round-trips
it through save_rows / load_rows.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ importable (scripts/ is outside the agent package).
# ---------------------------------------------------------------------------
_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT / "scripts"))

# ---------------------------------------------------------------------------
# Imports from the module under test (after sys.path manipulation — noqa OK)
# ---------------------------------------------------------------------------
from setprob_augment import (  # type: ignore[import-not-found]  # noqa: E402
    BUCKET_DATE_NO_UNIQUE,
    BUCKET_NO_DATE,
    BUCKET_NO_SLUG_PARSE,
    BUCKET_PAIR_NOT_IN_TD,
    augment_rows,
    make_neutral_rows,
    make_placebo_rows,
)

# ---------------------------------------------------------------------------
# Imports from the main package
# ---------------------------------------------------------------------------
from agent.backtest.cached_sweep import SignalRow, load_rows, save_rows  # noqa: E402
from agent.backtest.cross_market_signal import cross_market_signal  # noqa: E402
from agent.backtest.sharp_line import iso_week_key, tennis_data_surname  # noqa: E402

# ---------------------------------------------------------------------------
# Fake-data helpers
# ---------------------------------------------------------------------------

TdRow = dict[str, Any]
TdIndex = dict[frozenset[str], list[TdRow]]


def _td_row(
    winner: str = "Djokovic N.",
    loser: str = "Federer R.",
    avg_w: float = 1.5,
    avg_l: float = 2.5,
    best_of: int = 3,
    tournament: str = "Roland Garros",
    date: str = "2024-05-10",
) -> TdRow:
    return {
        "Winner": winner,
        "Loser": loser,
        "AvgW": avg_w,
        "AvgL": avg_l,
        "Best of": best_of,
        "Tournament": tournament,
        "Date": date,
    }


def _signal_row(
    market_id: str,
    slug: str,
    **kwargs: Any,
) -> SignalRow:
    return SignalRow(market_id=market_id, slug=slug, **kwargs)


def _build_td_index(*rows: TdRow) -> TdIndex:
    """Build a fake TdIndex from one or more td rows."""
    idx: TdIndex = {}
    for r in rows:
        w = tennis_data_surname(str(r["Winner"]))
        loser = tennis_data_surname(str(r["Loser"]))
        assert w and loser, f"unparseable surnames in fake td row: {r}"
        key: frozenset[str] = frozenset({w, loser})
        idx.setdefault(key, []).append(r)
    return idx


# Canonical fake slug: date at start, -vs- at end (parse_slug needs $ anchor).
_SLUG_DJ_FED = "2024-05-10-tennis-first-set-winner-djokovic-vs-federer"
_SLUG_MUR_WAW = "2024-05-11-tennis-first-set-winner-murray-vs-wawrinka"
_SLUG_FED_DJ = "2024-05-10-tennis-first-set-winner-federer-vs-djokovic"
_SLUG_WAW_MUR = "2024-05-11-tennis-first-set-winner-wawrinka-vs-murray"
# Slug with no "-<word>-vs-<word>$" suffix → parse_slug returns None.
# Must NOT contain the pattern "-<alpha>-vs-<alpha>$"; avoid any "vs" as a
# hyphen-delimited token at the end.
_SLUG_NO_VS = "tennis-match-only-2024"
_SLUG_NO_DATE = "tennis-first-set-winner-djokovic-vs-federer"


_TD_DJ_FED = _td_row()
_TD_MUR_WAW = _td_row(
    winner="Murray A.",
    loser="Wawrinka S.",
    avg_w=1.8,
    avg_l=2.0,
    tournament="Wimbledon",
    date="2024-05-11",
)


def _full_index() -> TdIndex:
    return _build_td_index(_TD_DJ_FED, _TD_MUR_WAW)


# ===========================================================================
# TestAugmentRows
# ===========================================================================


class TestAugmentRows:
    """Test ``augment_rows`` pure function."""

    # --- Matched row ---

    def test_matched_row_gets_expected_signal(self) -> None:
        rows = [_signal_row("m1", _SLUG_DJ_FED)]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        expected = cross_market_signal(
            slug_first_surname="djokovic",
            td_row=_TD_DJ_FED,
        )
        assert active[0].cross_market_signal == pytest.approx(expected)
        assert expected != 0.0, "sanity: Djokovic favourite should yield nonzero signal"

    def test_matched_row_gets_expected_cluster_key(self) -> None:
        rows = [_signal_row("m1", _SLUG_DJ_FED)]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        expected_ck = iso_week_key(_TD_DJ_FED["Tournament"], _TD_DJ_FED["Date"])
        assert active[0].cluster_key == expected_ck
        assert active[0].cluster_key != ""

    def test_reversed_orientation_gives_opposite_sign(self) -> None:
        """slug-first=federer (the underdog) → signal should be negative."""
        rows = [_signal_row("m1", _SLUG_FED_DJ)]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        dj_sig = cross_market_signal(slug_first_surname="djokovic", td_row=_TD_DJ_FED)
        fed_sig = active[0].cross_market_signal
        # Both should be nonzero and have opposite signs
        assert dj_sig > 0.0
        assert fed_sig < 0.0
        assert abs(fed_sig) == pytest.approx(abs(dj_sig))

    # --- Unmatched: no slug parse ---

    def test_no_slug_parse_zero_signal_empty_key(self) -> None:
        rows = [_signal_row("m1", _SLUG_NO_VS)]
        active, buckets = augment_rows(rows, {})
        assert active[0].cross_market_signal == 0.0
        assert active[0].cluster_key == ""
        assert buckets.get(BUCKET_NO_SLUG_PARSE, 0) == 1

    # --- Unmatched: no date ---

    def test_no_date_in_slug_zero_signal_empty_key(self) -> None:
        rows = [_signal_row("m1", _SLUG_NO_DATE)]
        active, buckets = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        assert active[0].cross_market_signal == 0.0
        assert active[0].cluster_key == ""
        assert buckets.get(BUCKET_NO_DATE, 0) == 1

    # --- Unmatched: surname pair not in index ---

    def test_pair_not_in_td_zero_signal_empty_key(self) -> None:
        slug = "2024-05-10-tennis-first-set-winner-unknown-vs-nobody"
        rows = [_signal_row("m1", slug)]
        active, buckets = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        assert active[0].cross_market_signal == 0.0
        assert active[0].cluster_key == ""
        assert buckets.get(BUCKET_PAIR_NOT_IN_TD, 0) == 1

    # --- Unmatched: date out of ±1 day window ---

    def test_date_out_of_range_unmatched(self) -> None:
        slug = "2023-01-01-tennis-first-set-winner-djokovic-vs-federer"
        rows = [_signal_row("m1", slug)]
        active, buckets = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        assert active[0].cross_market_signal == 0.0
        assert active[0].cluster_key == ""
        assert buckets.get(BUCKET_DATE_NO_UNIQUE, 0) == 1

    # --- Date ±1 day is accepted ---

    def test_date_within_one_day_matches(self) -> None:
        # slug date = 2024-05-11 (td date = 2024-05-10, diff=1 day)
        slug = "2024-05-11-tennis-first-set-winner-djokovic-vs-federer"
        rows = [_signal_row("m1", slug)]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        assert active[0].cluster_key != ""

    # --- Output length / order preserved ---

    def test_output_same_length_as_input(self) -> None:
        rows = [
            _signal_row("m1", _SLUG_DJ_FED),
            _signal_row("m2", _SLUG_NO_VS),
            _signal_row("m3", _SLUG_MUR_WAW),
        ]
        active, _ = augment_rows(rows, _full_index())
        assert len(active) == 3

    def test_market_id_order_preserved(self) -> None:
        rows = [
            _signal_row("m1", _SLUG_NO_VS),
            _signal_row("m2", _SLUG_DJ_FED),
        ]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        assert [r.market_id for r in active] == ["m1", "m2"]

    def test_outcome_field_passed_through_unchanged(self) -> None:
        """Augment must NOT read or modify the outcome field — no look-ahead."""
        rows = [_signal_row("m1", _SLUG_DJ_FED, outcome="yes")]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        assert active[0].outcome == "yes"


# ===========================================================================
# TestNeutralRows
# ===========================================================================


class TestNeutralRows:
    """Test ``make_neutral_rows``."""

    def _base(self) -> list[SignalRow]:
        rows = [
            _signal_row("m1", _SLUG_DJ_FED),
            _signal_row("m2", _SLUG_NO_VS),
        ]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        return active

    def test_all_signals_zero(self) -> None:
        neutral = make_neutral_rows(self._base())
        assert all(r.cross_market_signal == 0.0 for r in neutral)

    def test_cluster_key_preserved(self) -> None:
        active = self._base()
        neutral = make_neutral_rows(active)
        for a, n in zip(active, neutral, strict=True):
            assert n.cluster_key == a.cluster_key

    def test_same_length_and_market_id_order(self) -> None:
        active = self._base()
        neutral = make_neutral_rows(active)
        assert len(neutral) == len(active)
        for a, n in zip(active, neutral, strict=True):
            assert n.market_id == a.market_id

    def test_input_rows_not_mutated(self) -> None:
        active = self._base()
        orig_sig = active[0].cross_market_signal
        make_neutral_rows(active)
        assert active[0].cross_market_signal == orig_sig


# ===========================================================================
# TestPlaceboRows
# ===========================================================================


class TestPlaceboRows:
    """Test ``make_placebo_rows``."""

    def _active(self, n_each: int = 2) -> list[SignalRow]:
        """Build matched rows from two matchups + one unmatched row.

        Each matchup contributes n_each rows to give distinct signal values
        (Djokovic-vs-Federer has a different signal than Murray-vs-Wawrinka
        because of different AvgW/AvgL ratios).
        """
        rows: list[SignalRow] = []
        for i in range(n_each):
            rows.append(_signal_row(f"dj{i}", _SLUG_DJ_FED))
            rows.append(_signal_row(f"mu{i}", _SLUG_MUR_WAW))
        rows.append(_signal_row("unmatched", _SLUG_NO_VS))  # unmatched sentinel
        active, _ = augment_rows(rows, _full_index())
        return active

    # --- (a) Deterministic per seed ---

    def test_deterministic(self) -> None:
        active = self._active()
        p1 = make_placebo_rows(active, seed=42)
        p2 = make_placebo_rows(active, seed=42)
        for r1, r2 in zip(p1, p2, strict=True):
            assert r1.cross_market_signal == pytest.approx(r2.cross_market_signal)

    # --- (b) Permutation invariant: multiset of matched signal values preserved ---

    def test_multiset_of_matched_signals_preserved(self) -> None:
        active = self._active()
        placebo = make_placebo_rows(active, seed=42)
        matched_active = sorted(
            r.cross_market_signal for r in active if r.cluster_key != ""
        )
        matched_placebo = sorted(
            r.cross_market_signal for r in placebo if r.cluster_key != ""
        )
        assert matched_active == pytest.approx(matched_placebo)

    # --- (c) Matched / unmatched partition preserved ---

    def test_partition_preserved(self) -> None:
        active = self._active()
        placebo = make_placebo_rows(active, seed=42)
        for a, p in zip(active, placebo, strict=True):
            assert (a.cluster_key != "") == (p.cluster_key != "")

    def test_unmatched_rows_keep_zero(self) -> None:
        active = self._active()
        placebo = make_placebo_rows(active, seed=42)
        for r in placebo:
            if r.cluster_key == "":
                assert r.cross_market_signal == 0.0

    # --- (d) cluster_key unchanged ---

    def test_cluster_key_unchanged(self) -> None:
        active = self._active()
        placebo = make_placebo_rows(active, seed=42)
        for a, p in zip(active, placebo, strict=True):
            assert a.cluster_key == p.cluster_key

    # --- (e) Different seeds generally give different assignments ---

    def test_different_seeds_generally_differ(self) -> None:
        # 8 matched rows with 2 distinct signal values (4 each) → 8!/(4!4!)=70
        # possible permutations; seeds 0 and 1 almost certainly produce
        # different permutations.
        active = self._active(n_each=4)  # 8 matched + 1 unmatched
        p_s0 = make_placebo_rows(active, seed=0)
        p_s1 = make_placebo_rows(active, seed=1)
        sigs0 = [r.cross_market_signal for r in p_s0]
        sigs1 = [r.cross_market_signal for r in p_s1]
        assert sigs0 != sigs1

    # --- (f) Active vs placebo differ for non-degenerate input ---

    def test_active_and_placebo_differ(self) -> None:
        """Signal assignments should differ for at least some matched rows.

        We use 8 matched rows so even with bad luck the identity permutation
        probability for numpy default_rng(42) on this specific input is
        astronomically low.
        """
        active = self._active(n_each=4)  # 8 matched rows
        placebo = make_placebo_rows(active, seed=42)
        active_sigs = [r.cross_market_signal for r in active]
        placebo_sigs = [r.cross_market_signal for r in placebo]
        assert active_sigs != placebo_sigs

    # --- Edge case: all rows unmatched ---

    def test_all_unmatched_returns_copy(self) -> None:
        rows = [_signal_row("m1", _SLUG_NO_VS), _signal_row("m2", _SLUG_NO_VS)]
        active, _ = augment_rows(rows, {})
        placebo = make_placebo_rows(active, seed=0)
        for a, p in zip(active, placebo, strict=True):
            assert a.cross_market_signal == p.cross_market_signal

    # --- Input rows not mutated ---

    def test_input_not_mutated(self) -> None:
        active = self._active()
        orig = [r.cross_market_signal for r in active]
        make_placebo_rows(active, seed=42)
        after = [r.cross_market_signal for r in active]
        assert orig == after


# ===========================================================================
# TestAlignment — active / neutral / placebo have same rows in same order
# ===========================================================================


class TestAlignment:
    def test_same_length_all_three(self) -> None:
        rows = [
            _signal_row("m1", _SLUG_DJ_FED),
            _signal_row("m2", _SLUG_NO_VS),
            _signal_row("m3", _SLUG_MUR_WAW),
        ]
        active, _ = augment_rows(rows, _full_index())
        neutral = make_neutral_rows(active)
        placebo = make_placebo_rows(active, seed=0)
        assert len(active) == len(rows) == len(neutral) == len(placebo)

    def test_market_id_order_identical(self) -> None:
        rows = [
            _signal_row("m1", _SLUG_DJ_FED),
            _signal_row("m2", _SLUG_NO_VS),
            _signal_row("m3", _SLUG_MUR_WAW),
        ]
        active, _ = augment_rows(rows, _full_index())
        neutral = make_neutral_rows(active)
        placebo = make_placebo_rows(active, seed=0)
        for a, n, p, orig in zip(active, neutral, placebo, rows, strict=True):
            assert a.market_id == orig.market_id
            assert n.market_id == orig.market_id
            assert p.market_id == orig.market_id

    def test_only_cross_market_signal_differs(self) -> None:
        """All other fields (slug, scores, outcome, …) must be identical."""
        rows = [_signal_row("m1", _SLUG_DJ_FED, outcome="yes", entry_price=0.6)]
        active, _ = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        neutral = make_neutral_rows(active)
        placebo = make_placebo_rows(active, seed=0)

        assert active[0].outcome == neutral[0].outcome == placebo[0].outcome == "yes"
        assert (
            active[0].entry_price
            == neutral[0].entry_price
            == placebo[0].entry_price
            == 0.6
        )
        # cluster_key is identical across all three
        assert active[0].cluster_key == neutral[0].cluster_key == placebo[0].cluster_key


# ===========================================================================
# TestEndToEnd — tiny JSON round-trip
# ===========================================================================


class TestEndToEnd:
    def test_file_roundtrip(self, tmp_path: Path) -> None:
        """Write a tiny fake _signal_rows.json, run augment core, read back 3 files."""
        rows = [
            _signal_row("m1", _SLUG_DJ_FED, outcome="yes"),
            _signal_row("m2", _SLUG_NO_VS, outcome="no"),
            _signal_row("m3", _SLUG_MUR_WAW),
        ]
        inp = tmp_path / "_signal_rows.json"
        save_rows(rows, inp)

        # Run core (no real file I/O for tennis-data — inject index)
        loaded = load_rows(inp)
        active, _ = augment_rows(loaded, _full_index())
        neutral = make_neutral_rows(active)
        placebo = make_placebo_rows(active, seed=7)

        out_active = tmp_path / "_signal_rows_v4.json"
        out_neutral = tmp_path / "_signal_rows_v4_neutral.json"
        out_placebo = tmp_path / "_signal_rows_v4_placebo.json"

        save_rows(active, out_active)
        save_rows(neutral, out_neutral)
        save_rows(placebo, out_placebo)

        back_active = load_rows(out_active)
        back_neutral = load_rows(out_neutral)
        back_placebo = load_rows(out_placebo)

        # All three have the same length and market_id order
        assert len(back_active) == len(back_neutral) == len(back_placebo) == 3
        for a, n, p, orig in zip(back_active, back_neutral, back_placebo, rows, strict=True):
            assert a.market_id == n.market_id == p.market_id == orig.market_id

        # Active: matched rows have nonzero signal + cluster_key
        assert back_active[0].cross_market_signal != 0.0
        assert back_active[0].cluster_key != ""
        assert back_active[2].cross_market_signal != 0.0
        assert back_active[2].cluster_key != ""

        # Active: unmatched row has zero signal + empty cluster_key
        assert back_active[1].cross_market_signal == 0.0
        assert back_active[1].cluster_key == ""

        # Neutral: ALL signals are zero, cluster_keys preserved
        assert all(r.cross_market_signal == 0.0 for r in back_neutral)
        for a, n in zip(back_active, back_neutral, strict=True):
            assert a.cluster_key == n.cluster_key

        # Placebo: unmatched row still zero, cluster_keys unchanged
        assert back_placebo[1].cross_market_signal == 0.0
        for a, p in zip(back_active, back_placebo, strict=True):
            assert a.cluster_key == p.cluster_key

        # Outcome field is carried through untouched (no look-ahead)
        assert back_active[0].outcome == "yes"
        assert back_active[1].outcome == "no"

    def test_bucket_counts(self) -> None:
        rows = [
            _signal_row("m1", _SLUG_DJ_FED),          # matched
            _signal_row("m2", _SLUG_NO_VS),            # no_slug_parse
            _signal_row("m3", "2024-05-10-djokovic-vs-federer-extra"),  # pair NOT in empty idx
        ]
        _, buckets = augment_rows(rows, _build_td_index(_TD_DJ_FED))
        # m2 → no_slug_parse bucket (no -vs- at end)
        assert buckets.get(BUCKET_NO_SLUG_PARSE, 0) >= 1
