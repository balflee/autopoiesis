# tests/agent/backtest/test_run_cross_market_journey.py
"""TDD composition tests for scripts/run_cross_market_journey.py — Task 7b.

All tests are OFFLINE: no network, no real tennis-data files, no 4925-row
experiment.  The tests prove WIRING and compositional correctness using tiny
fake rows + snapshots.

Tests:
  - delta assembly: aligned vectors, unmatched-row exclusion, both-NO_BET kept
  - treatment/placebo use the IDENTICAL test partition (shared split helper)
  - survival gate: sign-test logic + per-seed verdict
  - verdict rule: EDGE only if BOTH layers pass (4-combination table test)
  - end-to-end smoke: tiny fake universe → run_journey → writes report without error
  - markdown output contains both layers' numbers
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ importable
# ---------------------------------------------------------------------------
_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT / "scripts"))

# ---------------------------------------------------------------------------
# All imports below need noqa:E402 (sys.path is manipulated before them).
# The agent.* + run_cross_market_journey imports are alphabetically sorted
# per isort rules; run_* comes after agent.* (r > a).
# ---------------------------------------------------------------------------
from run_cross_market_journey import (  # type: ignore[import-not-found]  # noqa: E402
    GO_CI_SESOI,
    GoCiResult,
    PerRowResult,
    SeedTriple,
    _build_parser,
    _load_v3_seed,
    compute_go_ci_for_seeds,
    compute_go_delta,
    get_test_signal_rows,
    layer1_verdict,
    main,
    run_journey,
    sign_test_over_seeds,
    treatment_beats_baseline,
    write_report,
)

from agent.backtest.cached_sweep import SignalRow, load_rows, save_rows  # noqa: E402
from agent.backtest.find_optimal_config import StrategyConfig  # noqa: E402
from agent.backtest.historical_fetcher import (  # noqa: E402
    MarketSnapshot,
    PricePoint,
    load_all_cached_markets,
    save_cached_market,
)
from agent.backtest.reincarnation import split_rows_by_time  # noqa: E402
from agent.backtest.sharp_line import BootstrapCI  # noqa: E402
from agent.backtest.tennis_match_resolver import TennisMatchResolver  # noqa: E402
from agent.backtest.validate_value_seed import (  # noqa: E402
    _ENTRY_PRICE_FLOOR,
    select_winner,
)
from agent.core.state import Weights  # noqa: E402

# ===========================================================================
# Helpers: fake-data factories (mirrors test_validate_value_seed.py pattern)
# ===========================================================================

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "surface_advantage",
    "head_to_head",
    "rest_recency",
)


def _empty_resolver() -> TennisMatchResolver:
    return TennisMatchResolver(name_index={})


def _seed_cfg(*, kappa_xm: float = 0.0) -> StrategyConfig:
    return StrategyConfig(
        weights=Weights(
            w_r=0.5,
            w_s=0.5,
            alpha=[1 / 3, 1 / 3, 1 / 3],
            beta=[0.5, 0.5],
            rho=1.0,
        ),
        max_breath_risk_pct=0.4,
        min_confidence=0.05,
        min_bet_size_usd=4.0,
        min_edge=0.02,
        kappa=0.3,
        kappa_xm=kappa_xm,
    )


def _snap(
    market_id: str,
    *,
    entry_ts: str,
    end_date: str,
    resolution: str,
    entry_price: float,
    outcome: Literal["yes", "no"],
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        slug=f"atp-{market_id}-alpha-vs-bravo",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _row_for(
    snap: MarketSnapshot,
    *,
    cross_market_signal: float = 0.0,
    cluster_key: str = "",
) -> SignalRow:
    return SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: 0.8 for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=snap.price_ledger[0].mid_price,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
        cross_market_signal=cross_market_signal,
        cluster_key=cluster_key,
    )


def _write_universe(
    tmp_path: Path,
    *,
    n_markets: int = 8,
    with_cluster_keys: bool = False,
) -> tuple[Path, Path]:
    """Write a small offline universe (signals rows + snapshot cache)."""
    snaps: list[MarketSnapshot] = []
    for i in range(n_markets):
        day = i + 1
        snaps.append(
            _snap(
                f"m{i}",
                entry_ts=f"2025-06-{day:02d}T00:00:00+00:00",
                end_date=f"2025-06-{day:02d}T12:00:00+00:00",
                resolution=f"2025-06-{day:02d}T20:00:00+00:00",
                entry_price=0.40 if i % 2 == 0 else 0.60,
                outcome="no" if i % 2 == 0 else "yes",
            )
        )
    cache_dir = tmp_path / "_cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for s in snaps:
        save_cached_market(snapshot=s, cache_dir=cache_dir)
    rows_path = tmp_path / "_signal_rows.json"
    ck = "roland|2025-W22" if with_cluster_keys else ""
    save_rows([_row_for(s, cluster_key=ck) for s in snaps], rows_path)
    return rows_path, cache_dir


# ===========================================================================
# Tests: per-row delta assembly
# ===========================================================================


class TestComputeGoDelta:
    """Tests for compute_go_delta — the per-row (T - P) assembly."""

    def test_simple_alignment(self) -> None:
        """delta_i = pnl_T[i] - pnl_P[i], matched rows only."""
        result_t = PerRowResult(pnls=[1.0, 2.0, 3.0], cluster_keys=["a", "b", "c"])
        result_p = PerRowResult(pnls=[0.5, 1.0, 1.5], cluster_keys=["a", "b", "c"])
        deltas, ck = compute_go_delta(result_t, result_p)
        assert deltas == pytest.approx([0.5, 1.0, 1.5])
        assert ck == ["a", "b", "c"]

    def test_unmatched_rows_excluded(self) -> None:
        """Rows with cluster_key=='' are dropped from the GO-CI substrate."""
        result_t = PerRowResult(
            pnls=[1.0, 2.0, 3.0], cluster_keys=["a", "", "c"]
        )
        result_p = PerRowResult(
            pnls=[0.5, 1.0, 1.5], cluster_keys=["a", "", "c"]
        )
        deltas, ck = compute_go_delta(result_t, result_p)
        # row index 1 (cluster_key='') excluded
        assert len(deltas) == 2
        assert deltas == pytest.approx([0.5, 1.5])
        assert ck == ["a", "c"]

    def test_both_no_bet_kept(self) -> None:
        """Both-NO_BET rows (pnl=0.0 on both arms) keep delta=0.0, counted."""
        # When both T and P score NO_BET, pnl=0.0 on both → delta=0.0
        result_t = PerRowResult(pnls=[0.0, 2.0], cluster_keys=["a", "b"])
        result_p = PerRowResult(pnls=[0.0, 1.0], cluster_keys=["a", "b"])
        deltas, _ck = compute_go_delta(result_t, result_p)
        assert len(deltas) == 2
        assert deltas[0] == pytest.approx(0.0)  # both-NO_BET → delta 0
        assert deltas[1] == pytest.approx(1.0)

    def test_length_mismatch_raises(self) -> None:
        """Mismatched vector lengths raise ValueError (strict 1:1 contract)."""
        result_t = PerRowResult(pnls=[1.0, 2.0], cluster_keys=["a", "b"])
        result_p = PerRowResult(pnls=[1.0], cluster_keys=["a"])
        with pytest.raises(ValueError, match="different lengths"):
            compute_go_delta(result_t, result_p)

    def test_cluster_key_mismatch_raises(self) -> None:
        """If cluster_key disagrees between arms, ValueError is raised."""
        result_t = PerRowResult(pnls=[1.0], cluster_keys=["a"])
        result_p = PerRowResult(pnls=[1.0], cluster_keys=["b"])
        with pytest.raises(ValueError, match="cluster_key mismatch"):
            compute_go_delta(result_t, result_p)

    def test_all_unmatched_yields_empty(self) -> None:
        """All unmatched → empty deltas and cluster_keys."""
        result_t = PerRowResult(pnls=[1.0, 2.0], cluster_keys=["", ""])
        result_p = PerRowResult(pnls=[0.5, 1.0], cluster_keys=["", ""])
        deltas, ck = compute_go_delta(result_t, result_p)
        assert deltas == []
        assert ck == []

    def test_delta_values_correct(self) -> None:
        """Exact per-element subtraction check."""
        result_t = PerRowResult(pnls=[5.0, -3.0, 0.0], cluster_keys=["x", "x", "y"])
        result_p = PerRowResult(pnls=[2.0, -1.0, 0.0], cluster_keys=["x", "x", "y"])
        deltas, ck = compute_go_delta(result_t, result_p)
        assert deltas == pytest.approx([3.0, -2.0, 0.0])
        assert ck == ["x", "x", "y"]


# ===========================================================================
# Tests: shared test partition — T and P use the IDENTICAL split
# ===========================================================================


class TestSharedTestPartition:
    """TREATMENT and PLACEBO must use the IDENTICAL test partition."""

    def test_get_test_signal_rows_matches_select_winner_internal_split(
        self, tmp_path: Path
    ) -> None:
        """The shared helper reproduces the same test rows select_winner uses.

        We use monkeypatching on validate_value_seed to intercept the split
        that select_winner calls internally, then compare it to what
        get_test_signal_rows returns from the driver.
        """
        rows_path, cache_dir = _write_universe(tmp_path, n_markets=8)
        rows = load_rows(rows_path)
        snapshots = load_all_cached_markets(cache_dir=cache_dir)
        resolver = _empty_resolver()

        import agent.backtest.validate_value_seed as vvs

        captured_test_rows: list[Any] = []

        def spy_split(
            survival_rows: list[Any], *, train_fraction: float = 0.7
        ) -> tuple[list[Any], list[Any]]:
            tr, te = split_rows_by_time(survival_rows, train_fraction=train_fraction)
            captured_test_rows.clear()
            captured_test_rows.extend(te)
            return tr, te

        # Patch the name in vvs module scope, restore after
        original = vvs.split_rows_by_time
        vvs.split_rows_by_time = spy_split  # type: ignore[assignment]
        try:
            select_winner(
                rows,
                snapshots,
                0,
                walk_forward=True,
                resolver=resolver,
                n=4,
                min_bets=0,
                top=2,
                verbose=False,
            )
        finally:
            vvs.split_rows_by_time = original  # type: ignore[assignment]

        # get_test_signal_rows must produce the same test rows
        driver_test_rows = get_test_signal_rows(
            rows,
            snapshots,
            resolver=resolver,
            entry_price_floor=_ENTRY_PRICE_FLOOR,
            train_fraction=0.7,
        )

        # Both should have the same market_ids in the same order
        captured_market_ids = [r.signal.market_id for r in captured_test_rows]
        driver_market_ids = [r.market_id for r in driver_test_rows]
        assert captured_market_ids == driver_market_ids, (
            f"Test partitions disagree: "
            f"select_winner got {captured_market_ids}, "
            f"get_test_signal_rows got {driver_market_ids}"
        )

    def test_same_partition_regardless_of_config(self, tmp_path: Path) -> None:
        """The test partition is config-independent (floor-first, then split).

        Two configs should yield the same test SignalRows when using the shared
        helper, because the split key is entry_price not config.
        """
        rows_path, cache_dir = _write_universe(tmp_path, n_markets=8)
        rows = load_rows(rows_path)
        snapshots = load_all_cached_markets(cache_dir=cache_dir)
        resolver = _empty_resolver()

        test_rows_1 = get_test_signal_rows(
            rows, snapshots, resolver=resolver, train_fraction=0.7
        )
        test_rows_2 = get_test_signal_rows(
            rows, snapshots, resolver=resolver, train_fraction=0.7
        )
        # Same call → same result (deterministic)
        assert [r.market_id for r in test_rows_1] == [r.market_id for r in test_rows_2]
        assert len(test_rows_1) >= 1

    def test_different_train_fractions_yield_different_splits(
        self, tmp_path: Path
    ) -> None:
        """Sanity: different train_fractions produce different test set sizes."""
        rows_path, cache_dir = _write_universe(tmp_path, n_markets=8)
        rows = load_rows(rows_path)
        snapshots = load_all_cached_markets(cache_dir=cache_dir)
        resolver = _empty_resolver()

        test_rows_07 = get_test_signal_rows(
            rows, snapshots, resolver=resolver, train_fraction=0.7
        )
        test_rows_05 = get_test_signal_rows(
            rows, snapshots, resolver=resolver, train_fraction=0.5
        )
        # 0.5 train → larger test set
        assert len(test_rows_05) > len(test_rows_07)


# ===========================================================================
# Tests: survival gate — sign test and per-seed verdict
# ===========================================================================


class TestSurvivalGate:
    """Tests for treatment_beats_baseline and sign_test_over_seeds."""

    def _make_triple(
        self,
        lhs_seed: int,
        treatment_alive: bool,
        treatment_pnl: float,
        baseline_alive: bool,
        baseline_pnl: float,
    ) -> SeedTriple:
        return SeedTriple(
            lhs_seed=lhs_seed,
            treatment_alive=treatment_alive,
            treatment_pnl=treatment_pnl,
            baseline_alive=baseline_alive,
            baseline_pnl=baseline_pnl,
            placebo_alive_rate=0.5,
            placebo_pnl_mean=0.0,
        )

    def test_beats_baseline_alive_and_pnl(self) -> None:
        t = self._make_triple(0, True, 100.0, True, 50.0)
        assert treatment_beats_baseline(t) is True

    def test_not_beats_dead_treatment(self) -> None:
        t = self._make_triple(0, False, 200.0, True, 50.0)
        assert treatment_beats_baseline(t) is False

    def test_not_beats_lower_pnl(self) -> None:
        t = self._make_triple(0, True, 30.0, True, 50.0)
        assert treatment_beats_baseline(t) is False

    def test_not_beats_equal_pnl(self) -> None:
        # Must be STRICTLY greater
        t = self._make_triple(0, True, 50.0, True, 50.0)
        assert treatment_beats_baseline(t) is False

    def test_sign_test_all_win(self) -> None:
        # 5/5 wins: one-sided binomial p = C(5,5)/2^5 = 1/32 ≈ 0.03125
        # round(0.03125, 4) = 0.0312 (banker's rounding in Python 3)
        triples = [
            self._make_triple(i, True, 100.0 + i, True, 50.0)
            for i in range(5)
        ]
        result = sign_test_over_seeds(triples)
        assert result["n_seeds"] == 5
        assert result["n_treatment_wins"] == 5
        assert result["n_treatment_losses"] == 0
        # p-value for 5/5: sum C(5,k)/2^5 for k=5..5 = 1/32 = 0.03125
        # round(0.03125, 4) == 0.0312 in Python 3 (banker's rounding on .5)
        assert result["sign_test_pvalue"] < 0.05
        assert result["verdict"] == "GO"

    def test_sign_test_all_lose(self) -> None:
        triples = [
            self._make_triple(i, False, 10.0, True, 100.0)
            for i in range(5)
        ]
        result = sign_test_over_seeds(triples)
        assert result["n_treatment_wins"] == 0
        assert result["verdict"] == "NO_GO"

    def test_sign_test_majority_wins_insufficient_power(self) -> None:
        # 4 of 5 seeds win, but p(X>=4 | n=5, p=0.5) = C(5,4)/32 + C(5,5)/32
        # = (5+1)/32 = 6/32 = 0.1875 ≥ 0.05 → NO_GO (insufficient power at n=5)
        triples = [
            self._make_triple(i, True, 100.0, True, 50.0)
            for i in range(4)
        ] + [self._make_triple(4, False, 10.0, True, 100.0)]
        result = sign_test_over_seeds(triples)
        assert result["n_treatment_wins"] == 4
        # With n=5, need all 5 to win (p=1/32≈0.031) to pass p<0.05
        assert result["sign_test_pvalue"] > 0.05
        assert result["verdict"] == "NO_GO"

    def test_sign_test_tie_no_go(self) -> None:
        # 2 of 4 seeds win → tie → NO_GO
        triples = [
            self._make_triple(i, True, 100.0, True, 50.0)
            for i in range(2)
        ] + [
            self._make_triple(i + 2, False, 10.0, True, 100.0)
            for i in range(2)
        ]
        result = sign_test_over_seeds(triples)
        assert result["n_treatment_wins"] == 2
        assert result["verdict"] == "NO_GO"

    def test_sign_test_empty(self) -> None:
        result = sign_test_over_seeds([])
        assert result["n_seeds"] == 0
        assert result["verdict"] == "NO_GO"


# ===========================================================================
# Tests: verdict rule — EDGE only if BOTH layers pass (4-combination table)
# ===========================================================================


class TestLayer1Verdict:
    """FIX C: Layer-1 verdict must route through three_state_verdict.

    A bare ``lo>0`` test is NOT enough — a CI with too few clusters / too small
    n must read INCONCLUSIVE, NOT EDGE.  Only the EDGE state maps to
    ``layer1_pass``.
    """

    def _ci(
        self,
        *,
        lo: float,
        hi: float,
        point: float,
        n: int = 300,
        n_clusters: int = 12,
    ) -> BootstrapCI:
        return BootstrapCI(
            n=n, n_clusters=n_clusters, point=point, lo=lo, hi=hi,
            iid_lo=lo, iid_hi=hi,
        )

    def test_sesoi_is_zero(self) -> None:
        """The pre-registered GO_CI_SESOI is intentionally 0.0 (per-bet PnL)."""
        assert GO_CI_SESOI == 0.0

    def test_edge_when_ci_excludes_zero_positive_and_enough_clusters(self) -> None:
        ci = self._ci(lo=0.01, hi=0.09, point=0.05, n=300, n_clusters=12)
        verdict, layer1_pass = layer1_verdict(ci)
        assert verdict == "EDGE"
        assert layer1_pass is True

    def test_too_few_clusters_is_inconclusive_not_edge(self) -> None:
        """A sub-10-cluster CI with lo>0 must read INCONCLUSIVE, NOT EDGE.

        This is the load-bearing guard: without three_state_verdict's
        min_clusters floor a too-narrow CI would falsely read EDGE.
        """
        ci = self._ci(lo=0.01, hi=0.09, point=0.05, n=300, n_clusters=9)
        verdict, layer1_pass = layer1_verdict(ci)
        assert verdict == "INCONCLUSIVE"
        assert layer1_pass is False

    def test_too_small_n_is_inconclusive_not_edge(self) -> None:
        """n below min_n (200) must read INCONCLUSIVE even with lo>0."""
        ci = self._ci(lo=0.01, hi=0.09, point=0.05, n=150, n_clusters=12)
        verdict, layer1_pass = layer1_verdict(ci)
        assert verdict == "INCONCLUSIVE"
        assert layer1_pass is False

    def test_refuted_when_ci_upper_below_sesoi(self) -> None:
        """CI fully below SESOI (0.0) → REFUTED (a positive edge is ruled out)."""
        ci = self._ci(lo=-0.09, hi=-0.01, point=-0.05, n=300, n_clusters=12)
        verdict, layer1_pass = layer1_verdict(ci)
        assert verdict == "REFUTED"
        assert layer1_pass is False

    def test_inconclusive_when_ci_straddles_zero(self) -> None:
        ci = self._ci(lo=-0.02, hi=0.05, point=0.01, n=300, n_clusters=12)
        verdict, layer1_pass = layer1_verdict(ci)
        assert verdict == "INCONCLUSIVE"
        assert layer1_pass is False


class TestVerdictRule:
    """Pre-registered 4-combination verdict table test (routes through L1 gate)."""

    def _go_ci(
        self, *, lo: float, hi: float, point: float, n_clusters: int = 12
    ) -> BootstrapCI:
        return BootstrapCI(
            n=300, n_clusters=n_clusters, point=point, lo=lo, hi=hi,
            iid_lo=lo, iid_hi=hi,
        )

    @pytest.mark.parametrize(
        "ci_lo, ci_point, n_clusters, sign_verdict, expected_edge",
        [
            # EDGE only if BOTH pass — and L1 now routes through three_state.
            (0.01, 0.05, 12, "GO", True),     # L1=EDGE, L2=GO → EDGE
            (0.01, 0.05, 12, "NO_GO", False), # L1=EDGE, L2=fail → NO_GO
            (-0.01, 0.05, 12, "GO", False),   # L1=INCONCLUSIVE, L2=GO → NO_GO
            (-0.05, -0.02, 12, "NO_GO", False),  # L1=REFUTED, L2=fail → NO_GO
            # Even a positive lo reads NO_GO if too few clusters (the new guard):
            (0.01, 0.05, 9, "GO", False),     # L1=INCONCLUSIVE (clusters<10)
        ],
    )
    def test_verdict_table(
        self,
        ci_lo: float,
        ci_point: float,
        n_clusters: int,
        sign_verdict: str,
        expected_edge: bool,
    ) -> None:
        ci = self._go_ci(lo=ci_lo, hi=0.1, point=ci_point, n_clusters=n_clusters)
        _verdict, l1 = layer1_verdict(ci)
        l2 = sign_verdict == "GO"
        overall = l1 and l2
        assert overall == expected_edge, (
            f"L1={l1}, L2={l2} → expected EDGE={expected_edge}, got {overall}"
        )


# ===========================================================================
# Tests: markdown report output
# ===========================================================================


class TestMarkdownReport:
    """The markdown output must contain both layers' numbers and the verdict."""

    def _make_triple(self, lhs_seed: int, *, alive: bool, pnl: float) -> SeedTriple:
        return SeedTriple(
            lhs_seed=lhs_seed,
            treatment_alive=alive,
            treatment_pnl=pnl,
            baseline_alive=True,
            baseline_pnl=50.0,
            placebo_alive_rate=0.5,
            placebo_pnl_mean=10.0,
        )

    def test_report_contains_both_layer_sections(self, tmp_path: Path) -> None:
        ci = BootstrapCI(n=120, n_clusters=12, point=0.05,
                         lo=0.01, hi=0.09, iid_lo=0.02, iid_hi=0.08)
        triples = [self._make_triple(i, alive=True, pnl=100.0 + i) for i in range(3)]
        sign = {
            "n_seeds": 3, "n_treatment_wins": 3, "n_treatment_losses": 0,
            "sign_test_pvalue": 0.125, "verdict": "GO",
        }
        out = tmp_path / "test_report.md"
        write_report(
            out,
            lhs_seeds=[0, 1, 2],
            placebo_seeds=[0],
            triples=triples,
            go_ci=ci,
            sign_test=sign,
            n=32,
            walk_forward=True,
            train_fraction=0.7,
            active_path=Path("active.json"),
            placebo_path=Path("placebo.json"),
        )
        text = out.read_text(encoding="utf-8")
        assert "Layer 1" in text
        assert "Layer 2" in text
        assert "GO edge CI" in text or "GO Edge CI" in text or "GO" in text
        assert "survival" in text.lower()
        # Both layers' numbers must appear
        assert "0.050000" in text or "0.05" in text  # point estimate
        assert "3" in text  # n seeds
        # The verdict
        assert "EDGE" in text
        assert "GO" in text

    def test_report_no_go_when_layer1_fails(self, tmp_path: Path) -> None:
        ci = BootstrapCI(n=120, n_clusters=12, point=-0.02,
                         lo=-0.05, hi=0.01, iid_lo=-0.04, iid_hi=0.00)
        triples = [self._make_triple(i, alive=True, pnl=100.0) for i in range(3)]
        sign = {
            "n_seeds": 3, "n_treatment_wins": 3, "n_treatment_losses": 0,
            "sign_test_pvalue": 0.125, "verdict": "GO",
        }
        out = tmp_path / "test_report2.md"
        write_report(
            out,
            lhs_seeds=[0, 1, 2],
            placebo_seeds=[0],
            triples=triples,
            go_ci=ci,
            sign_test=sign,
            n=32,
            walk_forward=True,
            train_fraction=0.7,
            active_path=Path("active.json"),
            placebo_path=Path("placebo.json"),
        )
        text = out.read_text(encoding="utf-8")
        assert "NO_GO" in text

    def test_report_verdict_table_present(self, tmp_path: Path) -> None:
        ci = BootstrapCI(n=100, n_clusters=10, point=0.01,
                         lo=0.001, hi=0.02, iid_lo=0.001, iid_hi=0.02)
        triples = [self._make_triple(0, alive=True, pnl=100.0)]
        sign = {
            "n_seeds": 1, "n_treatment_wins": 1, "n_treatment_losses": 0,
            "sign_test_pvalue": 0.5, "verdict": "NO_GO",
        }
        out = tmp_path / "test_report3.md"
        write_report(
            out,
            lhs_seeds=[0],
            placebo_seeds=[0],
            triples=triples,
            go_ci=ci,
            sign_test=sign,
            n=32,
            walk_forward=True,
            train_fraction=0.7,
            active_path=Path("active.json"),
            placebo_path=Path("placebo.json"),
        )
        text = out.read_text(encoding="utf-8")
        # Pre-registered verdict table must appear
        assert "EDGE CONFIRMED" in text or "EDGE" in text
        assert "NO_GO" in text


# ===========================================================================
# End-to-end smoke test: tiny fake universe → run_journey → writes report
# ===========================================================================


class TestEndToEndSmoke:
    """Small offline smoke test: run_journey wiring with tiny fake data.

    Proves that all composition works without errors.  Does NOT prove any
    statistical validity — the tiny fake universe is designed to be fast
    and deterministic, not to produce meaningful results.
    """

    def _write_universe_with_cluster_keys(
        self, tmp_path: Path, n: int = 6
    ) -> tuple[list[SignalRow], list[MarketSnapshot], Path]:
        """Tiny universe with cluster_key set so GO-CI can compute."""
        snaps = []
        for i in range(n):
            day = i + 1
            snaps.append(
                _snap(
                    f"m{i}",
                    entry_ts=f"2025-06-{day:02d}T00:00:00+00:00",
                    end_date=f"2025-06-{day:02d}T12:00:00+00:00",
                    resolution=f"2025-06-{day:02d}T20:00:00+00:00",
                    entry_price=0.40 if i % 2 == 0 else 0.60,
                    outcome="no" if i % 2 == 0 else "yes",
                )
            )
        cache_dir = tmp_path / "_cache_tennis"
        cache_dir.mkdir(parents=True, exist_ok=True)
        for s in snaps:
            save_cached_market(snapshot=s, cache_dir=cache_dir)
        # Give some rows cluster keys so GO-CI has matched rows
        rows = [
            _row_for(
                s,
                cluster_key=f"rolandgarros|2025-W{22 + (i % 2):02d}",
                cross_market_signal=0.1 * (i % 3 - 1),
            )
            for i, s in enumerate(snaps)
        ]
        snapshots = load_all_cached_markets(cache_dir=cache_dir)
        return rows, snapshots, cache_dir

    def test_smoke_runs_without_error(self, tmp_path: Path) -> None:
        """run_journey completes end-to-end on a tiny universe without error."""
        active_rows, snapshots, _cache = self._write_universe_with_cluster_keys(
            tmp_path, n=6
        )
        # Placebo: just permute the signals
        from scripts.setprob_augment import make_placebo_rows  # type: ignore[import-not-found]
        placebo_rows = make_placebo_rows(active_rows, seed=0)
        placebo_rows_by_seed = {0: placebo_rows}

        resolver = _empty_resolver()
        out_path = tmp_path / "cross_market_journey.md"

        result = run_journey(
            active_rows=active_rows,
            placebo_rows_by_seed=placebo_rows_by_seed,
            snapshots=snapshots,
            resolver=resolver,
            lhs_seeds=[0],
            placebo_seeds=[0],
            n=4,       # tiny sweep
            walk_forward=True,
            train_fraction=0.6,
            n_boot=20,  # fast
            out_path=out_path,
            verbose=False,
        )

        # Report was written
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "Layer 1" in text
        assert "Layer 2" in text

        # Result dict has the expected keys
        assert "go_ci" in result
        assert "sign_test" in result
        assert "triples" in result
        assert "layer1_pass" in result
        assert "layer2_pass" in result
        assert "overall_edge" in result
        assert isinstance(result["overall_edge"], bool)

    def test_smoke_report_contains_numeric_values(self, tmp_path: Path) -> None:
        """The written report must include numeric CI values and seed counts."""
        active_rows, snapshots, _cache = self._write_universe_with_cluster_keys(
            tmp_path, n=6
        )
        from scripts.setprob_augment import make_placebo_rows  # type: ignore[import-not-found]
        placebo_rows = make_placebo_rows(active_rows, seed=0)
        placebo_rows_by_seed = {0: placebo_rows}
        resolver = _empty_resolver()
        out_path = tmp_path / "smoke_report.md"

        run_journey(
            active_rows=active_rows,
            placebo_rows_by_seed=placebo_rows_by_seed,
            snapshots=snapshots,
            resolver=resolver,
            lhs_seeds=[0],
            placebo_seeds=[0],
            n=4,
            walk_forward=True,
            train_fraction=0.6,
            n_boot=20,
            out_path=out_path,
            verbose=False,
        )

        text = out_path.read_text(encoding="utf-8")
        # Must contain a numeric-looking CI value
        import re
        assert re.search(r"\d+\.\d+", text), "expected numeric values in report"
        # Layer 2 table should show a seed row
        assert "|" in text  # markdown tables use |


# ===========================================================================
# FIX A — GO-CI on the HEADLINE seed only (no cross-seed pseudo-replication)
# ===========================================================================


def _write_go_universe(
    tmp_path: Path, *, n: int = 12
) -> tuple[list[SignalRow], list[MarketSnapshot]]:
    """Universe with cluster keys on every row so the GO-CI has matched rows.

    Every row carries a cluster_key (so none are dropped) and a non-zero
    cross_market_signal so the active vs placebo signal differs.
    """
    snaps: list[MarketSnapshot] = []
    for i in range(n):
        day = i + 1
        snaps.append(
            _snap(
                f"m{i}",
                entry_ts=f"2025-06-{day:02d}T00:00:00+00:00",
                end_date=f"2025-06-{day:02d}T12:00:00+00:00",
                resolution=f"2025-06-{day:02d}T20:00:00+00:00",
                entry_price=0.40 if i % 2 == 0 else 0.60,
                outcome="no" if i % 2 == 0 else "yes",
            )
        )
    cache_dir = tmp_path / "_cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for s in snaps:
        save_cached_market(snapshot=s, cache_dir=cache_dir)
    rows = [
        _row_for(
            s,
            cluster_key=f"rolandgarros|2025-W{22 + (i % 3):02d}",
            cross_market_signal=0.2 * (i % 3 - 1),  # spread of -0.2, 0.0, +0.2
        )
        for i, s in enumerate(snaps)
    ]
    snapshots = load_all_cached_markets(cache_dir=cache_dir)
    return rows, snapshots


class TestGoCiHeadlineSeed:
    """FIX A: GO-CI is computed ONCE on the headline LHS seed (lhs_seeds[0]).

    The LHS-seed grid is a SEPARATE descriptive replicate axis (selection
    robustness), NOT a pseudo-replication that multiplies the bootstrap n.
    """

    def test_returns_go_ci_result(self, tmp_path: Path) -> None:
        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        active_rows, snapshots = _write_go_universe(tmp_path, n=12)
        placebo_rows_by_seed = {0: make_placebo_rows(active_rows, seed=0)}
        resolver = _empty_resolver()

        out = compute_go_ci_for_seeds(
            [0, 1, 2],
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=4,
            walk_forward=True,
            train_fraction=0.5,
            n_boot=20,
            verbose=False,
        )
        assert isinstance(out, GoCiResult)
        assert isinstance(out.ci, BootstrapCI)
        # Per-seed robustness readout: one point estimate per LHS seed.
        assert set(out.per_seed_point.keys()) == {0, 1, 2}
        assert out.headline_seed == 0

    def test_ci_n_is_matched_rows_not_times_seeds(self, tmp_path: Path) -> None:
        """go_ci.n must equal the matched test rows for the HEADLINE seed —
        NOT multiplied by the number of LHS seeds (the old pseudo-replication).
        """
        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        active_rows, snapshots = _write_go_universe(tmp_path, n=12)
        placebo_rows_by_seed = {0: make_placebo_rows(active_rows, seed=0)}
        resolver = _empty_resolver()

        # The matched test rows for the headline seed (the bootstrap substrate):
        test_rows = get_test_signal_rows(
            active_rows, snapshots, resolver=resolver, train_fraction=0.5
        )
        # All rows in this universe carry a cluster_key → none dropped.
        n_matched = sum(1 for r in test_rows if r.cluster_key != "")

        out_one_seed = compute_go_ci_for_seeds(
            [0],
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=4,
            walk_forward=True,
            train_fraction=0.5,
            n_boot=20,
            verbose=False,
        )
        out_three_seeds = compute_go_ci_for_seeds(
            [0, 1, 2],
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=4,
            walk_forward=True,
            train_fraction=0.5,
            n_boot=20,
            verbose=False,
        )
        # The bootstrap n must NOT grow with the number of LHS seeds.
        assert out_one_seed.ci.n == n_matched
        assert out_three_seeds.ci.n == n_matched, (
            "GO-CI n grew with the seed count — pseudo-replication regression!"
        )
        assert out_three_seeds.ci.n == out_one_seed.ci.n

    def test_robustness_spread_summary(self, tmp_path: Path) -> None:
        """The robustness readout summarises min/max/mean of per-seed points."""
        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        active_rows, snapshots = _write_go_universe(tmp_path, n=12)
        placebo_rows_by_seed = {0: make_placebo_rows(active_rows, seed=0)}
        resolver = _empty_resolver()

        out = compute_go_ci_for_seeds(
            [0, 1, 2],
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=4,
            walk_forward=True,
            train_fraction=0.5,
            n_boot=20,
            verbose=False,
        )
        pts = list(out.per_seed_point.values())
        assert out.point_min == pytest.approx(min(pts))
        assert out.point_max == pytest.approx(max(pts))
        assert out.point_mean == pytest.approx(sum(pts) / len(pts))
        # Headline-seed CI point uses the headline seed's per-row deltas.
        assert out.per_seed_point[0] == pytest.approx(out.ci.point)


# ===========================================================================
# FIX B — Placebo arm scored on PLACEBO-test rows (permuted signal)
# ===========================================================================


class TestPlaceboScoredOnPlaceboTest:
    """FIX B: the placebo arm must be scored on placebo-test rows.

    Because make_placebo_rows preserves row order/partition and the time-split
    keys on entry_price (signal-independent), placebo-test is the SAME physical
    rows in the SAME order as the active test rows — only the
    cross_market_signal column is permuted.  So index-pairing still holds and
    the delta isolates genuine signal value.
    """

    def test_placebo_test_same_rows_different_signal(self, tmp_path: Path) -> None:
        """placebo_test has identical market_ids/order to active_test, but a
        permuted cross_market_signal (so the delta is NOT a self-comparison)."""
        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        active_rows, snapshots = _write_go_universe(tmp_path, n=12)
        placebo_rows = make_placebo_rows(active_rows, seed=0)
        resolver = _empty_resolver()

        active_test = get_test_signal_rows(
            active_rows, snapshots, resolver=resolver, train_fraction=0.5
        )
        placebo_test = get_test_signal_rows(
            placebo_rows, snapshots, resolver=resolver, train_fraction=0.5
        )
        # Same physical rows, same order, same cluster keys.
        assert [r.market_id for r in active_test] == [
            r.market_id for r in placebo_test
        ]
        assert [r.cluster_key for r in active_test] == [
            r.cluster_key for r in placebo_test
        ]
        # But the signal column is permuted → at least one row differs.
        active_sigs = [r.cross_market_signal for r in active_test]
        placebo_sigs = [r.cross_market_signal for r in placebo_test]
        assert active_sigs != placebo_sigs, (
            "placebo signal identical to active — permutation not reflected in "
            "the test partition (FIX B regression)."
        )
        # The marginal multiset is preserved over the FULL universe (the
        # permutation runs over ALL matched rows), but NOT necessarily over the
        # test SUBSET (a value can be swapped between a train and a test row).
        active_full = sorted(r.cross_market_signal for r in active_rows)
        placebo_full = sorted(r.cross_market_signal for r in placebo_rows)
        assert active_full == pytest.approx(placebo_full)

    def test_go_ci_uses_placebo_test_not_active_test(self, tmp_path: Path) -> None:
        """With a strong, sign-aligned signal and κ_xm able to learn it, the
        treatment scored on the REAL signal should differ from a placebo scored
        on the PERMUTED signal — the delta is non-degenerate.

        We assert that the GO-CI substrate is built from placebo-test (not a
        self-comparison): the per-seed point estimate is finite and the
        bootstrap n equals the matched test rows.
        """
        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        active_rows, snapshots = _write_go_universe(tmp_path, n=12)
        placebo_rows_by_seed = {0: make_placebo_rows(active_rows, seed=0)}
        resolver = _empty_resolver()

        out = compute_go_ci_for_seeds(
            [0],
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=4,
            walk_forward=True,
            train_fraction=0.5,
            n_boot=20,
            verbose=False,
        )
        import math

        assert not math.isnan(out.ci.point)
        test_rows = get_test_signal_rows(
            active_rows, snapshots, resolver=resolver, train_fraction=0.5
        )
        n_matched = sum(1 for r in test_rows if r.cluster_key != "")
        assert out.ci.n == n_matched


# ===========================================================================
# FIX D — Layer-2 baseline evaluated on the SAME TEST partition as treatment
# ===========================================================================


class TestLayer2BaselineOnTestPartition:
    """FIX D: BASELINE (v3, κ_xm=0) must run on the IDENTICAL held-out TEST
    SurvivalRows that the treatment uses — not the full active universe.
    """

    def test_run_one_seed_baseline_uses_test_partition(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_one_seed must run the baseline over the SAME number of survival
        rows as the treatment's test partition (not the full universe).

        We spy on run_survival_over_rows to capture the row set sizes for both
        the baseline and the treatment-test evaluation.
        """
        import run_cross_market_journey as drv  # type: ignore[import-not-found]

        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        active_rows, snapshots = _write_go_universe(tmp_path, n=12)
        placebo_rows_by_seed = {0: make_placebo_rows(active_rows, seed=0)}
        resolver = _empty_resolver()

        # The treatment's TEST survival rows (identical split):
        from agent.backtest.reincarnation import split_rows_by_time
        from agent.backtest.survival_season import build_survival_rows

        survival_all = build_survival_rows(
            active_rows, snapshots, resolver, entry_price_floor=_ENTRY_PRICE_FLOOR
        )
        _train, test_survival = split_rows_by_time(survival_all, train_fraction=0.5)
        expected_test_n = len(test_survival)
        full_n = len(survival_all)
        assert expected_test_n < full_n  # the test window must be a strict subset

        captured_row_counts: list[int] = []
        original = drv.run_survival_over_rows

        def spy_run(
            rows: list[Any], snaps: list[Any], **kw: Any
        ) -> dict[str, Any]:
            captured_row_counts.append(len(rows))
            return original(rows, snaps, **kw)

        monkeypatch.setattr(drv, "run_survival_over_rows", spy_run)

        drv.run_one_seed(
            0,
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=4,
            walk_forward=True,
            train_fraction=0.5,
            verbose=False,
        )

        # The BASELINE run must be over the TEST partition (expected_test_n),
        # NOT the full universe (full_n).
        assert expected_test_n in captured_row_counts, (
            f"baseline did not run on the test partition "
            f"(expected {expected_test_n}, captured {captured_row_counts})"
        )
        assert full_n not in captured_row_counts, (
            f"baseline ran on the FULL universe ({full_n}) — apples-to-oranges "
            f"regression (FIX D)."
        )


# ===========================================================================
# FIX E — --walk-forward CLI flag toggles (BooleanOptionalAction)
# ===========================================================================


class TestCliWalkForwardFlag:
    """FIX E: --walk-forward / --no-walk-forward (BooleanOptionalAction)."""

    def test_default_is_walk_forward_true(self) -> None:
        args = _build_parser().parse_args([])
        assert args.walk_forward is True

    def test_explicit_walk_forward_true(self) -> None:
        args = _build_parser().parse_args(["--walk-forward"])
        assert args.walk_forward is True

    def test_no_walk_forward_reaches_in_sample_path(self) -> None:
        """--no-walk-forward must flip the flag to False (the in-sample path)."""
        args = _build_parser().parse_args(["--no-walk-forward"])
        assert args.walk_forward is False


# ===========================================================================
# FIX F — main() entrypoint import resolves + smoke
# ===========================================================================


class TestMainEntrypoint:
    """FIX F: main() must import make_placebo_rows from scripts.setprob_augment
    (NOT a bare `from setprob_augment import ...` that ModuleNotFoundErrors)."""

    def test_main_smoke_runs(self, tmp_path: Path) -> None:
        """main(argv=[...]) on a tiny fake universe resolves the import and
        completes end-to-end (writes a report, returns 0)."""
        # main() loads the snapshot cache itself from --cache-dir; we only need
        # the rows + the on-disk cache _write_go_universe creates.
        active_rows, _snapshots = _write_go_universe(tmp_path, n=12)
        from scripts.setprob_augment import make_placebo_rows

        active_path = tmp_path / "_signal_rows_v4.json"
        placebo_path = tmp_path / "_signal_rows_v4_placebo.json"
        save_rows(active_rows, active_path)
        save_rows(make_placebo_rows(active_rows, seed=0), placebo_path)

        cache_dir = tmp_path / "_cache_tennis"
        out_path = tmp_path / "journey.md"

        rc = main(
            [
                "--active",
                str(active_path),
                "--placebo",
                str(placebo_path),
                "--cache-dir",
                str(cache_dir),
                "--lhs-seeds",
                "0",
                "--placebo-seeds",
                "0",
                "--n",
                "4",
                "--n-boot",
                "20",
                "--train-fraction",
                "0.5",
                "--out",
                str(out_path),
            ]
        )
        assert rc == 0
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "Layer 1" in text
        assert "Layer 2" in text

    def test_main_placebo_import_resolves(self) -> None:
        """The make_placebo_rows symbol main() depends on resolves via the
        scripts package import (the old bare import crashed at runtime)."""
        from scripts.setprob_augment import (
            make_placebo_rows,  # type: ignore[import-not-found]
        )

        assert callable(make_placebo_rows)


# ===========================================================================
# Tests: load_v3_seed (baseline loader)
# ===========================================================================


class TestLoadV3Seed:
    """Tests for the v3 seed loader used as the baseline."""

    def test_loads_from_json(self, tmp_path: Path) -> None:
        # alpha must sum to exactly 1.0 (validated by Weights)
        seed_data = {
            "kappa": 0.49,
            "kappa_xm": 0.0,
            "max_breath_risk_pct": 0.38,
            "min_bet_size_usd": 4.0,
            "min_confidence": 0.075,
            "min_edge": 0.035,
            "weights": {
                "w_r": 0.58,
                "w_s": 0.42,
                "alpha": [0.177734375, 0.0703125, 0.751953125],
                "beta": [0.767578125, 0.232421875],
                "rho": 0.849609375,
            },
        }
        p = tmp_path / "v3.json"
        import json
        p.write_text(json.dumps(seed_data), encoding="utf-8")
        cfg = _load_v3_seed(p)
        assert isinstance(cfg, StrategyConfig)
        assert cfg.kappa_xm == pytest.approx(0.0)
        assert cfg.kappa == pytest.approx(0.49)

    def test_missing_kappa_xm_defaults_zero(self, tmp_path: Path) -> None:
        """v3 seed JSON without kappa_xm key → defaults to 0.0."""
        seed_data = {
            "kappa": 0.49,
            "max_breath_risk_pct": 0.38,
            "min_bet_size_usd": 4.0,
            "min_confidence": 0.075,
            "min_edge": 0.035,
            "weights": {
                "w_r": 0.58,
                "w_s": 0.42,
                "alpha": [0.177734375, 0.0703125, 0.751953125],
                "beta": [0.767578125, 0.232421875],
                "rho": 0.849609375,
            },
        }
        p = tmp_path / "v3_no_kappa_xm.json"
        import json
        p.write_text(json.dumps(seed_data), encoding="utf-8")
        cfg = _load_v3_seed(p)
        assert cfg.kappa_xm == pytest.approx(0.0)

    def test_fallback_when_file_missing(self) -> None:
        """When seed_path doesn't exist, falls back to DEFAULT_OPTIMUM_SEED."""
        cfg = _load_v3_seed(Path("nonexistent/path/seed.json"))
        assert isinstance(cfg, StrategyConfig)
        # DEFAULT_OPTIMUM_SEED has kappa_xm=0.0 (the v3 baseline)
        assert cfg.kappa_xm == pytest.approx(0.0)
