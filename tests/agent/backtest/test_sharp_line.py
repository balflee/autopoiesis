"""Unit tests for the A17 sharp-line probe pure helpers + injectable fetch.

Sync pytest, no network: 2b fetch is exercised via inline fake Gamma/CLOB
clients. See agent/backtest/sharp_line.py and the plan-loop record.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from agent.backtest.cached_sweep import compute_bet_pnl
from agent.backtest.sharp_line import (
    BootstrapCI,
    DropBuckets,
    MatchSample,
    brier_edge,
    build_2a_row,
    build_2b_sample,
    cluster_bootstrap_ci,
    implied_prob_two_way,
    iso_week_key,
    join_one_to_one,
    normalize_name,
    resolve_2b_close,
    roi_cell,
    simulate_bet,
    soft_consensus_prob,
    surname_matches,
    taker_fee_usd,
    tennis_data_surname,
    three_state_verdict,
)


class TestImpliedProb:
    def test_known_devig(self) -> None:
        # 1.5 / 2.75: inv 0.6667 / 0.3636 -> 0.6470588...
        p = implied_prob_two_way(1.5, 2.75)
        assert p == pytest.approx(0.6470588, abs=1e-6)

    def test_complement_sums_to_one(self) -> None:
        p = implied_prob_two_way(1.5, 2.75)
        q = implied_prob_two_way(2.75, 1.5)
        assert p is not None and q is not None
        assert p + q == pytest.approx(1.0, abs=1e-9)

    def test_overround_stripped(self) -> None:
        # Two even 2.10 odds (overround) de-vig to 0.5 each.
        assert implied_prob_two_way(2.10, 2.10) == pytest.approx(0.5, abs=1e-9)

    @pytest.mark.parametrize(
        "a,b",
        [(None, 2.0), (2.0, None), ("", 2.0), ("x", 2.0), (1.0, 2.0), (2.0, 0.9)],
    )
    def test_invalid_returns_none(self, a: object, b: object) -> None:
        assert implied_prob_two_way(a, b) is None

    def test_nan_returns_none(self) -> None:
        assert implied_prob_two_way(float("nan"), 2.0) is None


class TestSurname:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Sinner J.", "sinner"),
            ("De Minaur A.", "deminaur"),
            ("Del Potro J.M.", "delpotro"),
            ("Auger Aliassime F.", "augeraliassime"),
            ("Ramos-Vinolas A.", "ramosvinolas"),
            ("Collins D.R.", "collins"),
        ],
    )
    def test_tennis_data_surname(self, raw: str, expected: str) -> None:
        assert tennis_data_surname(raw) == expected

    def test_empty(self) -> None:
        assert tennis_data_surname("") is None
        assert tennis_data_surname("J.") is None

    @pytest.mark.parametrize(
        "td,display",
        [
            ("delpotro", "Juan Martin Del Potro"),
            ("shelton", "Ben Shelton"),
            ("deminaur", "Alex de Minaur"),
            ("augeraliassime", "Felix Auger Aliassime"),
            ("sinner", "Jannik Sinner"),
        ],
    )
    def test_surname_matches_display(self, td: str, display: str) -> None:
        assert surname_matches(td, display)

    def test_surname_mismatch(self) -> None:
        assert not surname_matches("alcaraz", "Jannik Sinner")

    def test_normalize_name(self) -> None:
        assert normalize_name("Nicolás Jarry") == "nicolasjarry"


class TestSoftConsensus:
    def test_averages_probabilities_excluding_pinnacle(self) -> None:
        # Two individual books; reference is the Winner.
        row = {"B365W": 1.5, "B365L": 2.75, "EXW": 1.6, "EXL": 2.5, "PSW": 1.4, "PSL": 3.0}
        p, used_avg = soft_consensus_prob(row, ref_is_winner=True)
        assert used_avg is False
        b365 = implied_prob_two_way(1.5, 2.75)
        ex = implied_prob_two_way(1.6, 2.5)
        assert p == pytest.approx((b365 + ex) / 2.0, abs=1e-9)

    def test_avg_fallback_flagged(self) -> None:
        row = {"AvgW": 1.5, "AvgL": 2.75}
        p, used_avg = soft_consensus_prob(row, ref_is_winner=True)
        assert used_avg is True
        assert p == pytest.approx(implied_prob_two_way(1.5, 2.75), abs=1e-9)

    def test_reference_loser_inverts(self) -> None:
        row = {"B365W": 1.5, "B365L": 2.75}
        p_win, _ = soft_consensus_prob(row, ref_is_winner=True)
        p_lose, _ = soft_consensus_prob(row, ref_is_winner=False)
        assert p_win is not None and p_lose is not None
        assert p_win + p_lose == pytest.approx(1.0, abs=1e-9)


class TestBrierCluster:
    def test_edge_sign_positive_when_pinnacle_better(self) -> None:
        # winner (y=1): pin says 0.8, soft says 0.6 -> pin closer -> positive edge
        assert brier_edge(p_pin=0.8, p_soft=0.6, y=1) > 0

    def test_edge_sign_negative_when_pinnacle_worse(self) -> None:
        assert brier_edge(p_pin=0.6, p_soft=0.8, y=1) < 0

    def test_cluster_ci_deterministic(self) -> None:
        vals = [0.01, 0.02, -0.005, 0.03, 0.0, 0.015] * 40
        clusters = [f"t{i % 12}" for i in range(len(vals))]
        a = cluster_bootstrap_ci(
            vals, clusters, rng=np.random.default_rng(0), n_boot=200
        )
        b = cluster_bootstrap_ci(
            vals, clusters, rng=np.random.default_rng(0), n_boot=200
        )
        assert (a.lo, a.hi, a.point) == (b.lo, b.hi, b.point)
        assert a.point == pytest.approx(sum(vals) / len(vals), abs=1e-12)
        assert a.lo <= a.point <= a.hi

    def test_three_state_edge(self) -> None:
        ci = BootstrapCI(n=300, n_clusters=20, point=0.01, lo=0.004, hi=0.016, iid_lo=0.005, iid_hi=0.015)
        assert three_state_verdict(ci, sesoi=0.002) == "EDGE"

    def test_three_state_refuted(self) -> None:
        ci = BootstrapCI(n=300, n_clusters=20, point=-0.001, lo=-0.004, hi=0.0015, iid_lo=-0.003, iid_hi=0.001)
        assert three_state_verdict(ci, sesoi=0.002) == "REFUTED"

    def test_three_state_inconclusive_wide(self) -> None:
        # CI compatible with both 0 and SESOI -> inconclusive (not no-go)
        ci = BootstrapCI(n=300, n_clusters=20, point=0.001, lo=-0.003, hi=0.01, iid_lo=-0.002, iid_hi=0.009)
        assert three_state_verdict(ci, sesoi=0.002) == "INCONCLUSIVE"

    def test_three_state_low_n_inconclusive(self) -> None:
        ci = BootstrapCI(n=50, n_clusters=20, point=0.01, lo=0.004, hi=0.016, iid_lo=0.005, iid_hi=0.015)
        assert three_state_verdict(ci, sesoi=0.002) == "INCONCLUSIVE"


class TestSimulateBet:
    def test_below_threshold_not_placed(self) -> None:
        r = simulate_bet(
            p_pin_ref=0.51, p_soft_ref=0.50, p_yes_close=0.5,
            y_ref=1, threshold=0.05, half_spread=0.0,
        )
        assert not r.placed and r.reason == "below_threshold"

    def test_ex_ante_side_independent_of_outcome(self) -> None:
        # Same ex-ante edge, opposite outcomes -> SAME side chosen.
        kw = dict(
            p_pin_ref=0.70, p_soft_ref=0.50, p_yes_close=0.60,
            threshold=0.05, half_spread=0.0,
        )
        win = simulate_bet(y_ref=1, **kw)  # type: ignore[arg-type]
        lose = simulate_bet(y_ref=0, **kw)  # type: ignore[arg-type]
        assert win.side == lose.side == "YES"
        # winning bet pays positive; losing bet pays -size minus the taker fee
        # (the fee is paid at entry regardless of outcome).
        assert win.net_pnl is not None and win.net_pnl > 0
        assert lose.net_pnl == pytest.approx(-1.0 - taker_fee_usd(1.0, 0.60), abs=1e-9)

    def test_no_side_uses_yes_mid_contract_no_double_complement(self) -> None:
        # ex-ante edge negative -> bet against the YES reference -> NO side.
        # entry_yes = p_yes - hs ; compute_bet_pnl complements internally.
        r = simulate_bet(
            p_pin_ref=0.30, p_soft_ref=0.55, p_yes_close=0.60,
            y_ref=0, threshold=0.05, half_spread=0.02,
        )
        assert r.placed and r.side == "NO"
        assert r.entry_yes == pytest.approx(0.58, abs=1e-12)
        # NO bet won (ref lost). Expected gross via the SAME contract:
        gross = compute_bet_pnl(
            side="NO", entry_price=0.58, size_usd=1.0, outcome="no",
            winning_price=1.0, side_correct_pricing=True,
        )
        fee = taker_fee_usd(1.0, 1.0 - 0.58)
        assert r.net_pnl == pytest.approx(gross - fee, abs=1e-12)
        # sanity: NOT the legacy (YES-priced) payout
        legacy = compute_bet_pnl(
            side="NO", entry_price=0.58, size_usd=1.0, outcome="no",
            winning_price=1.0, side_correct_pricing=False,
        )
        assert gross != pytest.approx(legacy, abs=1e-6)

    def test_invalid_spread_price_excluded(self) -> None:
        # p_yes + hs > 1 on a YES bet -> invalid_spread_price
        r = simulate_bet(
            p_pin_ref=0.99, p_soft_ref=0.50, p_yes_close=0.99,
            y_ref=1, threshold=0.05, half_spread=0.05,
        )
        assert not r.placed and r.reason == "invalid_spread_price"


# --------------------------------------------------------------------------- #
# 2b fakes
# --------------------------------------------------------------------------- #

_GS = "2025-06-10T13:00:00+00:00"


def _raw_market(
    *,
    outcomes: list[str],
    prices: list[float],
    tokens: list[dict[str, object]] | None,
    game_start: str | None = _GS,
    clob_token_ids: list[str] | None = None,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "outcomes": outcomes,
        "outcomePrices": prices,
        "clobTokenIds": clob_token_ids if clob_token_ids is not None else ["tok0", "tok1"],
    }
    if tokens is not None:
        raw["tokens"] = tokens
    if game_start is not None:
        raw["gameStartTime"] = game_start
    return raw


class _FakeGamma:
    def __init__(self, mapping: dict[str, list[dict[str, object]]]) -> None:
        self._m = mapping

    def get(self, market_id: str) -> list[dict[str, object]] | None:
        return self._m.get(market_id)


class _FakeClob:
    def __init__(self, ticks_by_token: dict[str, list[dict[str, object]]]) -> None:
        self._t = ticks_by_token

    def prices_history(
        self, token_id: str, *, start_ts: int | None, end_ts: int | None
    ) -> list[dict[str, object]]:
        return list(self._t.get(token_id, []))


def _unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


class TestResolve2B:
    def _good_market(self) -> dict[str, object]:
        return _raw_market(
            outcomes=["Ben Shelton", "Frances Tiafoe"],
            prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tokA", "outcome": "Ben Shelton"},
                {"token_id": "tokB", "outcome": "Frances Tiafoe"},
            ],
            clob_token_ids=["tokA", "tokB"],
        )

    def test_happy_path(self) -> None:
        gamma = _FakeGamma({"m1": [self._good_market()]})
        clob = _FakeClob(
            {
                "tokA": [
                    {"t": _unix("2025-06-10T10:00:00+00:00"), "p": 0.62},
                    {"t": _unix("2025-06-10T12:30:00+00:00"), "p": 0.66},
                    {"t": _unix("2025-06-10T14:00:00+00:00"), "p": 0.99},
                ]
            }
        )
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=clob
        )
        assert res.ok
        assert res.reference_display == "Ben Shelton"
        assert res.p_polymarket == pytest.approx(0.66, abs=1e-9)  # last < gameStart

    def test_gamma_missing(self) -> None:
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes",
            gamma_index=_FakeGamma({}), clob=_FakeClob({}),
        )
        assert not res.ok and res.reason == "gamma_missing"

    def test_gamma_duplicate(self) -> None:
        gamma = _FakeGamma({"m1": [self._good_market(), self._good_market()]})
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=_FakeClob({})
        )
        assert not res.ok and res.reason == "gamma_duplicate"

    def test_outcome_drift(self) -> None:
        gamma = _FakeGamma({"m1": [self._good_market()]})  # refetched outcome = yes
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="no", gamma_index=gamma, clob=_FakeClob({})
        )
        assert not res.ok and res.reason == "outcome_drift"

    def test_token_orientation_unverified_when_no_label_map(self) -> None:
        # No `tokens` label map -> cannot verify clobTokenIds<->outcomes ordering.
        raw = _raw_market(
            outcomes=["Ben Shelton", "Frances Tiafoe"], prices=[1.0, 0.0], tokens=None
        )
        gamma = _FakeGamma({"m1": [raw]})
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=_FakeClob({})
        )
        assert not res.ok and res.reason == "token_orientation_unverified"

    def test_missing_game_start(self) -> None:
        raw = _raw_market(
            outcomes=["Ben Shelton", "Frances Tiafoe"],
            prices=[1.0, 0.0],
            tokens=[{"token_id": "tokA", "outcome": "Ben Shelton"}],
            game_start=None,
            clob_token_ids=["tokA", "tokB"],
        )
        gamma = _FakeGamma({"m1": [raw]})
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=_FakeClob({})
        )
        assert not res.ok and res.reason == "missing_gameStartTime"

    def test_no_prematch_tick(self) -> None:
        gamma = _FakeGamma({"m1": [self._good_market()]})
        clob = _FakeClob(
            {"tokA": [{"t": _unix("2025-06-10T15:00:00+00:00"), "p": 0.99}]}  # all post-start
        )
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=clob
        )
        assert not res.ok and res.reason == "no_prematch_tick"

    def test_stale_premarket_tick(self) -> None:
        gamma = _FakeGamma({"m1": [self._good_market()]})
        clob = _FakeClob(
            {"tokA": [{"t": _unix("2025-06-05T10:00:00+00:00"), "p": 0.6}]}  # 5 days old
        )
        res = resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=clob,
            max_tick_age_h=24.0,
        )
        assert not res.ok and res.reason == "stale_premarket_tick"


class TestJoin:
    def _td_row(self, winner: str, loser: str) -> dict[str, object]:
        return {"Winner": winner, "Loser": loser}

    def test_one_to_one_match(self) -> None:
        gs = datetime(2025, 6, 10, 13, 0, tzinfo=UTC)
        td = {"2025-06-10": [self._td_row("Shelton B.", "Tiafoe F.")]}
        row, ref_is_winner, reason = join_one_to_one(
            ref_display="Ben Shelton", other_display="Frances Tiafoe",
            game_start=gs, td_by_date=td,
        )
        assert reason is None and row is not None and ref_is_winner is True

    def test_date_tolerance(self) -> None:
        gs = datetime(2025, 6, 10, 13, 0, tzinfo=UTC)
        td = {"2025-06-11": [self._td_row("Tiafoe F.", "Shelton B.")]}  # +1 day, ref lost
        row, ref_is_winner, reason = join_one_to_one(
            ref_display="Ben Shelton", other_display="Frances Tiafoe",
            game_start=gs, td_by_date=td,
        )
        assert reason is None and row is not None and ref_is_winner is False

    def test_date_miss(self) -> None:
        gs = datetime(2025, 6, 10, 13, 0, tzinfo=UTC)
        td = {"2025-06-20": [self._td_row("Shelton B.", "Tiafoe F.")]}
        _, _, reason = join_one_to_one(
            ref_display="Ben Shelton", other_display="Frances Tiafoe",
            game_start=gs, td_by_date=td,
        )
        assert reason == "date_miss"

    def test_ambiguous_join(self) -> None:
        gs = datetime(2025, 6, 10, 13, 0, tzinfo=UTC)
        td = {
            "2025-06-10": [self._td_row("Shelton B.", "Tiafoe F.")],
            "2025-06-11": [self._td_row("Tiafoe F.", "Shelton B.")],
        }
        _, _, reason = join_one_to_one(
            ref_display="Ben Shelton", other_display="Frances Tiafoe",
            game_start=gs, td_by_date=td,
        )
        assert reason == "ambiguous_join"


class TestBuild2A:
    def _row(self, **kw: object) -> dict[str, object]:
        base: dict[str, object] = {
            "Winner": "Sinner J.", "Loser": "Alcaraz C.",
            "PSW": 1.5, "PSL": 2.75, "B365W": 1.5, "B365L": 2.75,
            "Comment": "Completed", "Tournament": "Wimbledon", "Date": "2025-07-10",
        }
        base.update(kw)
        return base

    def test_completed_sample(self) -> None:
        sample, reason = build_2a_row(self._row())
        assert reason is None and sample is not None
        # reference = alphabetically-first surname = alcaraz (loser) -> y=0
        assert sample.y == 0
        # p_pin(reference=loser) = devig(PSL, PSW)
        assert sample.p_pin == pytest.approx(implied_prob_two_way(2.75, 1.5), abs=1e-9)
        assert "wimbledon" in sample.cluster_key

    def test_winner_is_reference(self) -> None:
        # Winner surname alphabetically first -> reference is winner -> y=1
        sample, reason = build_2a_row(self._row(Winner="Aaron A.", Loser="Zverev A."))
        assert reason is None and sample is not None and sample.y == 1

    def test_retired_dropped(self) -> None:
        _, reason = build_2a_row(self._row(Comment="Retired"))
        assert reason == "incomplete_ret_wo"

    def test_no_sharp_dropped(self) -> None:
        _, reason = build_2a_row(self._row(PSW=None, PSL=None))
        assert reason == "no_sharp"

    def test_no_soft_dropped(self) -> None:
        _, reason = build_2a_row(
            self._row(B365W=None, B365L=None, AvgW=None, AvgL=None)
        )
        assert reason == "no_soft"


class TestBuild2B:
    def _good_close(self) -> object:
        gamma = _FakeGamma(
            {
                "m1": [
                    _raw_market(
                        outcomes=["Ben Shelton", "Frances Tiafoe"],
                        prices=[1.0, 0.0],
                        tokens=[{"token_id": "tokA", "outcome": "Ben Shelton"}],
                        clob_token_ids=["tokA", "tokB"],
                    )
                ]
            }
        )
        clob = _FakeClob(
            {"tokA": [{"t": _unix("2025-06-10T12:30:00+00:00"), "p": 0.66}]}
        )
        return resolve_2b_close(
            market_id="m1", cassette_outcome="yes", gamma_index=gamma, clob=clob
        )

    def test_build_2b_sample(self) -> None:
        close = self._good_close()
        td = {"2025-06-10": [{"Winner": "Shelton B.", "Loser": "Tiafoe F.",
                              "PSW": 1.4, "PSL": 3.0, "Tournament": "Stuttgart",
                              "Date": "2025-06-10"}]}
        sample, reason = build_2b_sample(close, td_by_date=td)  # type: ignore[arg-type]
        assert reason is None and sample is not None
        # reference = Shelton (outcomes[0]) = Winner -> y=1, p_pin from PSW
        assert sample.y == 1
        assert sample.p_pin == pytest.approx(implied_prob_two_way(1.4, 3.0), abs=1e-9)
        assert sample.p_soft == pytest.approx(0.66, abs=1e-9)  # polymarket close

    def test_build_2b_date_miss(self) -> None:
        close = self._good_close()
        td = {"2025-07-01": [{"Winner": "Shelton B.", "Loser": "Tiafoe F.",
                              "PSW": 1.4, "PSL": 3.0}]}
        _, reason = build_2b_sample(close, td_by_date=td)  # type: ignore[arg-type]
        assert reason == "date_miss"


class TestRoi:
    def _samples(self, n: int) -> list[MatchSample]:
        # sharp says reference more likely (0.7) than market (0.5); reference
        # wins -> ex-ante YES bets win. Distinct clusters for the bootstrap.
        return [
            MatchSample(edge=0.0, p_pin=0.70, p_soft=0.50, y=1, cluster_key=f"t{i}")
            for i in range(n)
        ]

    def test_roi_cell_counts_and_positive(self) -> None:
        cell = roi_cell(
            self._samples(40),
            [s.cluster_key for s in self._samples(40)],
            threshold=0.05, half_spread=0.0, fee_rate=0.0,
            rng=np.random.default_rng(0), n_boot=200,
        )
        assert cell.bets == 40  # all clear |d_edge|=0.20 > 0.05
        assert cell.roi > 0  # winning YES bets
        assert cell.ci.lo <= cell.ci.point <= cell.ci.hi

    def test_roi_cell_below_threshold_no_bets(self) -> None:
        small = [
            MatchSample(edge=0.0, p_pin=0.51, p_soft=0.50, y=1, cluster_key=f"t{i}")
            for i in range(10)
        ]
        cell = roi_cell(
            small, [s.cluster_key for s in small],
            threshold=0.05, half_spread=0.0, fee_rate=0.0,
            rng=np.random.default_rng(0), n_boot=50,
        )
        assert cell.bets == 0 and cell.roi == 0.0


class TestIsoWeek:
    def test_same_week_same_key(self) -> None:
        a = iso_week_key("Wimbledon", "2025-07-08")
        b = iso_week_key("Wimbledon", "2025-07-10")  # same ISO week
        assert a == b and "wimbledon" in a

    def test_ddmmyyyy_format(self) -> None:
        a = iso_week_key("US Open", "10/07/2025")
        b = iso_week_key("US Open", "2025-07-10")
        assert a == b


class TestDropBuckets:
    def test_counts(self) -> None:
        b = DropBuckets()
        b.add("no_clob_history")
        b.add("no_clob_history")
        b.add("ambiguous_join")
        assert b.counts == {"no_clob_history": 2, "ambiguous_join": 1}
        assert b.total() == 3
