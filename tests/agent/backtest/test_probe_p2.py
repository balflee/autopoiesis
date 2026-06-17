"""V1.7 — P2 favorite-longshot calibration probe.

Backtest-only. Asserts the honesty gates: FAIL-CLOSED skip of markets without a real
``actual_trade`` entry, a reusable per-decile calibration curve, and the V1.6 graduation
gate on the strongest-favorite decile — NO_GO on a zero-edge input, GO only when
favorites beat their implied rate net of cost.
"""

from __future__ import annotations

from datetime import UTC, datetime

from data.sources.polymarket_trades import PROVENANCE_ACTUAL_TRADE, TradePrice
from scripts.probe_p2_favorite_longshot import ResolvedMarket, run_p2_probe

_TS = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


class _FakeTradesClient:
    """Maps market_id → a scripted entry TradePrice (or None = no real trade)."""

    def __init__(self, prices: dict[str, TradePrice | None]) -> None:
        self._prices = prices

    def entry_price(self, market_id: str, *, asof_ts=None) -> TradePrice | None:
        return self._prices.get(market_id)


def _tp(market_id: str, price: float, provenance: str = PROVENANCE_ACTUAL_TRADE) -> TradePrice:
    return TradePrice(market_id=market_id, price=price, ts=_TS, provenance=provenance)


def test_fail_closed_skips_markets_without_real_trade() -> None:
    prices = {
        "has-trade": _tp("has-trade", 0.70),
        "no-trade": None,                                   # no real fill → skip
        "midpoint": _tp("midpoint", 0.70, provenance="midpoint"),  # wrong provenance → skip
    }
    markets = [
        ResolvedMarket("has-trade", favorite_won=True),
        ResolvedMarket("no-trade", favorite_won=True),
        ResolvedMarket("midpoint", favorite_won=True),
    ]
    res = run_p2_probe(markets, trades_client=_FakeTradesClient(prices), n_deciles=1)
    assert res.n_scored == 1          # only the real-trade market was scored
    assert res.n_skipped == 2         # the missing + the non-actual_trade one


class _RecordingTradesClient:
    """Records the (market_id, asof_ts) it was queried with."""

    def __init__(self, prices: dict[str, TradePrice | None]) -> None:
        self._prices = prices
        self.asof_calls: list[tuple[str, object]] = []

    def entry_price(self, market_id: str, *, asof_ts=None) -> TradePrice | None:
        self.asof_calls.append((market_id, asof_ts))
        return self._prices.get(market_id)


def test_per_market_asof_is_parsed_and_passed_to_entry_price() -> None:
    # MED-1: a per-market PIT cutoff is parsed to a tz-aware datetime and forwarded
    # to entry_price, so the entry is pinned at/before the decision time.
    client = _RecordingTradesClient({"m": _tp("m", 0.70)})
    run_p2_probe(
        [ResolvedMarket("m", favorite_won=True, asof_ts="2025-06-01T12:00:00+00:00")],
        trades_client=client,
        n_deciles=1,
    )
    assert client.asof_calls == [("m", datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC))]


def test_naive_per_market_asof_is_coerced_to_utc_not_crash() -> None:
    # Codex r2 MED-1: a NAIVE ISO asof must not crash (the trades client requires
    # tz-aware) — it is coerced to UTC before being forwarded.
    client = _RecordingTradesClient({"m": _tp("m", 0.70)})
    run_p2_probe(
        [ResolvedMarket("m", favorite_won=True, asof_ts="2025-06-01T12:00:00")],  # naive
        trades_client=client,
        n_deciles=1,
    )
    assert client.asof_calls == [("m", datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC))]


def test_favorite_price_is_max_of_both_sides() -> None:
    # entry trade at 0.30 → the FAVORITE side's implied prob is 0.70.
    prices = {"m": _tp("m", 0.30)}
    res = run_p2_probe(
        [ResolvedMarket("m", favorite_won=True)],
        trades_client=_FakeTradesClient(prices),
        n_deciles=1,
    )
    assert res.deciles[0].implied == 0.70


def test_calibration_curve_reports_implied_and_empirical() -> None:
    # Two price tiers → two deciles; favorites at 0.6 win 50%, at 0.8 win 100%.
    prices = {f"lo{i}": _tp(f"lo{i}", 0.60) for i in range(4)}
    prices.update({f"hi{i}": _tp(f"hi{i}", 0.80) for i in range(4)})
    markets = (
        [ResolvedMarket(f"lo{i}", favorite_won=(i < 2)) for i in range(4)]
        + [ResolvedMarket(f"hi{i}", favorite_won=True) for i in range(4)]
    )
    res = run_p2_probe(markets, trades_client=_FakeTradesClient(prices), n_deciles=2)
    assert len(res.deciles) == 2
    lo, hi = res.deciles[0], res.deciles[1]
    assert lo.implied == 0.60 and lo.empirical == 0.50
    assert hi.implied == 0.80 and hi.empirical == 1.00


def test_zero_edge_favorites_return_no_go() -> None:
    # 40 favorites priced 0.70 winning ~70% (== implied) → no edge net of cost → NO_GO.
    wins = [True] * 28 + [False] * 12  # 70% win-rate, exactly the implied prob
    prices = {f"m{i}": _tp(f"m{i}", 0.70) for i in range(40)}
    markets = [ResolvedMarket(f"m{i}", favorite_won=wins[i]) for i in range(40)]
    res = run_p2_probe(markets, trades_client=_FakeTradesClient(prices), n_deciles=1, seed=1)
    assert res.favorite_bin.verdict == "NO_GO"


def test_strong_edge_favorites_clear_the_gate() -> None:
    # 40 favorites priced 0.70 but winning 95% → big cost-net edge → GO.
    wins = [True] * 38 + [False] * 2
    prices = {f"m{i}": _tp(f"m{i}", 0.70) for i in range(40)}
    markets = [ResolvedMarket(f"m{i}", favorite_won=wins[i]) for i in range(40)]
    res = run_p2_probe(markets, trades_client=_FakeTradesClient(prices), n_deciles=1, seed=1)
    assert res.favorite_bin.verdict == "GO"
    assert res.favorite_bin.gain > res.favorite_bin.threshold
