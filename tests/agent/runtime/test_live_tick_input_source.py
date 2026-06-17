"""V1.2 — LiveTickInputSource (live Polymarket tennis paper-trading spine).

Hermetic tests for the LIVE TickInputSource: gamma /events discovery parsing (the
full identifier bundle), the rolling per-market price buffer feeding RealSignalSource
with ``asof_ts=now``, the MANDATORY p1↔YES side-orientation guard (Codex-7, both
orientations + fail-closed), the 3-id identifier contract (Codex-r4-1), and a
golden-vector parity check (live signals == backtest ``signals_for`` sign-for-sign).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from agent.backtest.real_signal_source import RealSignalSource
from agent.backtest.tennis_match_resolver import TennisMatchResolver, build_name_index
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.runtime.live_tick_input_source import (
    ClobBookPriceSource,
    GammaLiveDiscovery,
    LiveLedgerProvider,
    LivePriceQuote,
    LiveTennisMarket,
    LiveTickInputSource,
    parse_open_tennis_markets,
)
from data.sources.tennis_sackmann import SackmannLoader

_ASOF = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
_TINY = Path(__file__).parents[2] / "agent" / "backtest" / "fixtures" / "sackmann_tiny"

# A resolvable -vs- slug: "Sinner" (p1) vs "Shelton" (p2), via the tiny index.
_RESOLVABLE_SLUG = "test-open-2025-06-01-Sinner-vs-Shelton"


# --------------------------------------------------------------------------- #
# gamma /events payload fixtures + fakes
# --------------------------------------------------------------------------- #


def _gamma_market(
    *,
    market_id: str = "501",
    condition_id: str = "0xcond501",
    slug: str = _RESOLVABLE_SLUG,
    yes_token: str = "tok-yes-1",
    no_token: str = "tok-no-1",
    yes_label: str = "Sinner",
    no_label: str = "Shelton",
    closed: bool = False,
) -> dict[str, object]:
    """One gamma /events market dict (clobTokenIds/outcomes are JSON STRINGS)."""
    return {
        "id": market_id,
        "conditionId": condition_id,
        "slug": slug,
        "question": "Will Sinner win?",
        "clobTokenIds": json.dumps([yes_token, no_token]),
        "outcomes": json.dumps([yes_label, no_label]),
        "endDate": "2025-06-01T20:00:00+00:00",
        "closed": closed,
    }


def _gamma_payload(*markets: dict[str, object]) -> list[dict[str, object]]:
    return [{"title": "Tennis", "slug": "tennis-event", "markets": list(markets)}]


class _FakeDiscovery:
    def __init__(self, markets: list[LiveTennisMarket]) -> None:
        self._markets = markets

    def discover(self) -> list[LiveTennisMarket]:
        return list(self._markets)


class _FakePriceSource:
    """Records the token_id queried; returns a scripted quote."""

    def __init__(self, quote: LivePriceQuote | None) -> None:
        self._quote = quote
        self.queried: list[str] = []

    def quote(self, token_id: str) -> LivePriceQuote | None:
        self.queried.append(token_id)
        return self._quote


class _ScriptedSignalSource:
    """Returns a fixed, per-slot-distinct p1-oriented vector (ignores the ledger).

    Distinct scores per slot so a uniform flip is provable slot-by-slot."""

    SCORES: ClassVar[dict[str, float]] = {
        MARKET_MOMENTUM: 0.10,
        TENNIS_TECHNICAL: 0.20,
        SURFACE_ADVANTAGE: 0.30,
        HEAD_TO_HEAD: 0.40,
        REST_RECENCY: 0.50,
    }

    def signals_for(self, *, market_id: str, tick: int, asof_ts: datetime) -> dict[str, Signal]:
        iso = asof_ts.isoformat()
        return {
            slot: Signal(score=score, confidence=0.8, available_at=iso, rationale=slot)
            for slot, score in self.SCORES.items()
        }


def _market(**over: object) -> LiveTennisMarket:
    base = dict(
        market_id="501", condition_id="0xcond501", slug=_RESOLVABLE_SLUG,
        yes_token_id="tok-yes-1", no_token_id="tok-no-1",
        yes_outcome_label="Sinner", no_outcome_label="Shelton",
        end_date_iso="2025-06-01T20:00:00+00:00",
    )
    base.update(over)
    return LiveTennisMarket(**base)  # type: ignore[arg-type]


def _tiny_resolver() -> TennisMatchResolver:
    import pandas as pd

    df = pd.read_csv(_TINY / "atp_matches_2025.csv", dtype=str).fillna("")
    return TennisMatchResolver(name_index=build_name_index([df]))


def _live_source(
    *,
    markets: list[LiveTennisMarket],
    quote: LivePriceQuote | None,
    signal_source: object | None = None,
    ledger: LiveLedgerProvider | None = None,
) -> tuple[LiveTickInputSource, _FakePriceSource]:
    price = _FakePriceSource(quote)
    src = LiveTickInputSource(
        discovery=_FakeDiscovery(markets),
        price_source=price,
        signal_source=signal_source if signal_source is not None else _ScriptedSignalSource(),
        resolver=_tiny_resolver(),
        ledger=ledger if ledger is not None else LiveLedgerProvider(),
        fee_bps=0.0,
    )
    return src, price


# --------------------------------------------------------------------------- #
# Discovery parsing — the identifier bundle (Codex-r2-M2b / r4-1)
# --------------------------------------------------------------------------- #


def test_parse_open_tennis_markets_carries_identifier_bundle() -> None:
    out = parse_open_tennis_markets(_gamma_payload(_gamma_market()))
    assert len(out) == 1
    m = out[0]
    assert m.market_id == "501"        # gamma id == settlement key
    assert m.condition_id == "0xcond501"
    assert m.yes_token_id == "tok-yes-1"
    assert m.no_token_id == "tok-no-1"
    assert m.yes_outcome_label == "Sinner"
    assert m.no_outcome_label == "Shelton"
    assert m.slug == _RESOLVABLE_SLUG


def test_parse_skips_closed_and_malformed_markets() -> None:
    payload = _gamma_payload(
        _gamma_market(market_id="1", closed=True),                 # closed → skip
        {"id": "2", "slug": "x", "conditionId": "0xc"},            # no tokens → skip
        _gamma_market(market_id="3"),                              # good
    )
    out = parse_open_tennis_markets(payload)
    assert [m.market_id for m in out] == ["3"]


def test_gamma_live_discovery_uses_open_endpoint_and_parses() -> None:
    seen: list[str] = []

    def fetcher(url: str) -> object:
        seen.append(url)
        return _gamma_payload(_gamma_market())

    disc = GammaLiveDiscovery(fetcher=fetcher)
    out = disc.discover()
    assert len(out) == 1 and out[0].market_id == "501"
    assert "closed=false" in seen[0] and "tag_slug=tennis" in seen[0]


def test_gamma_live_discovery_degrades_to_empty_on_fetch_error() -> None:
    def fetcher(url: str) -> object:
        raise OSError("network down")

    assert GammaLiveDiscovery(fetcher=fetcher).discover() == []


def test_gamma_discovery_head_to_head_only_filters_futures() -> None:
    # The live tennis tag returns a MIX: a head-to-head match (-vs- slug, player YES
    # label) + a tournament-outright future (will-X-win, YES="Yes"). Default
    # head_to_head_only=True keeps only the resolvable match; False keeps both.
    payload = _gamma_payload(
        _gamma_market(market_id="match-1", slug=_RESOLVABLE_SLUG, yes_label="Sinner"),
        _gamma_market(
            market_id="future-1",
            slug="will-sinner-win-the-2026-mens-wimbledon",
            yes_token="tok-fy", no_token="tok-fn",
            yes_label="Yes", no_label="No",
        ),
    )
    def fetcher(url: str) -> object:
        return payload

    h2h = GammaLiveDiscovery(fetcher=fetcher)  # default head_to_head_only=True
    assert [m.market_id for m in h2h.discover()] == ["match-1"]

    allm = GammaLiveDiscovery(fetcher=fetcher, head_to_head_only=False)
    assert {m.market_id for m in allm.discover()} == {"match-1", "future-1"}


# --------------------------------------------------------------------------- #
# ClobBookPriceSource — best bid/ask → mid + half-spread + top-of-book liquidity
# --------------------------------------------------------------------------- #


def test_clob_book_price_source_parses_mid_spread_liquidity() -> None:
    book = {
        "bids": [{"price": "0.48", "size": "100"}, {"price": "0.49", "size": "50"}],
        "asks": [{"price": "0.53", "size": "80"}, {"price": "0.51", "size": "40"}],
    }
    seen: list[str] = []

    def fetcher(url: str) -> object:
        seen.append(url)
        return book

    q = ClobBookPriceSource(fetcher=fetcher).quote("tok-1")
    assert q is not None
    # best bid = 0.49 (max), best ask = 0.51 (min) → mid 0.50, half-spread 0.01/0.50 = 0.02
    assert q.mid == pytest.approx(0.50)
    assert q.half_spread_frac == pytest.approx(0.02)
    # top-of-book notional: 0.49*50 + 0.51*40 = 24.5 + 20.4 = 44.9
    assert q.liquidity_usd == pytest.approx(44.9)
    assert "token_id=tok-1" in seen[0]


def test_clob_book_price_source_none_on_one_sided_or_error() -> None:
    assert ClobBookPriceSource(fetcher=lambda u: {"bids": [], "asks": []}).quote("t") is None

    def boom(url: str) -> object:
        raise OSError("down")

    assert ClobBookPriceSource(fetcher=boom).quote("t") is None


# --------------------------------------------------------------------------- #
# inputs_for — shape, live price, liquidity, cost stamps
# --------------------------------------------------------------------------- #


def test_inputs_for_returns_five_baseline_keys_price_liquidity_cost() -> None:
    src, price = _live_source(
        markets=[_market()],
        quote=LivePriceQuote(mid=0.62, half_spread_frac=0.01, liquidity_usd=25.0),
    )
    ti = src.inputs_for(asof_ts=_ASOF, tick=0)
    assert ti is not None
    assert set(ti.signals) == {
        MARKET_MOMENTUM, TENNIS_TECHNICAL, SURFACE_ADVANTAGE, HEAD_TO_HEAD, REST_RECENCY,
    }
    assert ti.market_id == "501"
    assert ti.price == pytest.approx(0.62)         # the YES-token mid the loop bets at
    assert ti.liquidity_cap_usd == pytest.approx(25.0)
    # LIVE cost stamps present (fail-closed-ready): fill_price=mid, fee rate, half-spread.
    assert ti.fill_price == pytest.approx(0.62)
    assert ti.fee_bps == pytest.approx(0.0)
    assert ti.half_spread_frac == pytest.approx(0.01)
    # the YES token was the one priced (identifier contract).
    assert price.queried == ["tok-yes-1"]


def test_inputs_for_none_when_no_markets_or_no_quote() -> None:
    src, _ = _live_source(markets=[], quote=LivePriceQuote(0.5, 0.0, 1.0))
    assert src.inputs_for(asof_ts=_ASOF, tick=0) is None
    src2, _ = _live_source(markets=[_market()], quote=None)
    assert src2.inputs_for(asof_ts=_ASOF, tick=0) is None


# --------------------------------------------------------------------------- #
# Side-orientation (Codex-7) — p1==YES (no flip) vs p1==NO (uniform flip)
# --------------------------------------------------------------------------- #


def test_orientation_p1_is_yes_passes_signals_through() -> None:
    # YES label "Sinner" == slug p1 → no flip; ledger fed the YES mid.
    src, _ = _live_source(
        markets=[_market(yes_outcome_label="Sinner", no_outcome_label="Shelton")],
        quote=LivePriceQuote(mid=0.62, half_spread_frac=0.0, liquidity_usd=10.0),
    )
    ti = src.inputs_for(asof_ts=_ASOF, tick=0)
    assert ti is not None
    for slot, score in _ScriptedSignalSource.SCORES.items():
        assert ti.signals[slot].score == pytest.approx(score)  # unflipped


def test_orientation_p1_is_no_negates_every_slot() -> None:
    # YES label "Shelton" == slug p2 → p1 is the NO side → uniform sign flip.
    src, _ = _live_source(
        markets=[_market(yes_outcome_label="Shelton", no_outcome_label="Sinner")],
        quote=LivePriceQuote(mid=0.62, half_spread_frac=0.0, liquidity_usd=10.0),
    )
    ti = src.inputs_for(asof_ts=_ASOF, tick=0)
    assert ti is not None
    for slot, score in _ScriptedSignalSource.SCORES.items():
        assert ti.signals[slot].score == pytest.approx(-score)  # uniformly flipped


def test_orientation_fail_closed_skips_unorientable_resolved_market() -> None:
    # Slug resolves (Sinner-vs-Shelton) but the YES label matches NEITHER player →
    # cannot establish p1↔YES → fail-closed: market is dropped, no silent inversion.
    src, _ = _live_source(
        markets=[_market(yes_outcome_label="Djokovic", no_outcome_label="Alcaraz")],
        quote=LivePriceQuote(mid=0.62, half_spread_frac=0.0, liquidity_usd=10.0),
    )
    assert src.inputs_for(asof_ts=_ASOF, tick=0) is None


def test_unresolved_slug_orients_to_yes_frame_no_flip() -> None:
    # Slug does NOT resolve → tennis facets neutral, momentum YES-oriented, no flip.
    src, _ = _live_source(
        markets=[_market(slug="will-rain-stop-play", yes_outcome_label="Yes",
                         no_outcome_label="No")],
        quote=LivePriceQuote(mid=0.62, half_spread_frac=0.0, liquidity_usd=10.0),
    )
    ti = src.inputs_for(asof_ts=_ASOF, tick=0)
    assert ti is not None
    for slot, score in _ScriptedSignalSource.SCORES.items():
        assert ti.signals[slot].score == pytest.approx(score)  # no flip


# --------------------------------------------------------------------------- #
# Identifier contract (Codex-r4-1) — price the token, settle the market
# --------------------------------------------------------------------------- #


def test_market_resolver_returns_end_date_for_discovered_market() -> None:
    src, _ = _live_source(
        markets=[_market(market_id="901", end_date_iso="2025-06-01T20:00:00+00:00")],
        quote=LivePriceQuote(mid=0.5, half_spread_frac=0.0, liquidity_usd=5.0),
    )
    # Before any tick the cache is cold → unknown markets resolve to None
    # (Executor then refuses the order — correct fail-closed).
    assert src.market_resolver("901") is None
    src.inputs_for(asof_ts=_ASOF, tick=0)  # populates the discovery cache
    info = src.market_resolver("901")
    assert info is not None
    assert info.end_date_iso == "2025-06-01T20:00:00+00:00"
    assert src.market_resolver("does-not-exist") is None


def test_identifier_contract_prices_yes_token_and_settles_market_id() -> None:
    src, price = _live_source(
        markets=[_market(market_id="777", yes_token_id="tok-yes-777")],
        quote=LivePriceQuote(mid=0.5, half_spread_frac=0.0, liquidity_usd=5.0),
    )
    ti = src.inputs_for(asof_ts=_ASOF, tick=0)
    assert ti is not None
    # The token priced and the market_id the loop will SETTLE on come from the SAME
    # discovered market — a tick can never price one id and settle another.
    assert price.queried == ["tok-yes-777"]
    assert ti.market_id == "777"


# --------------------------------------------------------------------------- #
# Golden-vector parity — LIVE signals == backtest signals_for, sign-for-sign
# --------------------------------------------------------------------------- #


def test_golden_vector_parity_live_equals_backtest_signals() -> None:
    """With a REAL RealSignalSource sharing the live ledger, the p1==YES LIVE
    vector equals a direct backtest signals_for call sign-for-sign on the SAME
    fixture match — proving the integration (ledger feed + market_id) reuses the
    backtest signal math verbatim, catching any per-slot inversion."""
    ledger = LiveLedgerProvider()
    real_src = RealSignalSource(
        provider=ledger,
        resolver=_tiny_resolver(),
        loader=SackmannLoader(snapshot_dir=_TINY),
        year_range=(2025, 2025),
    )
    src, _ = _live_source(
        markets=[_market(market_id="m2")],   # YES==Sinner==p1 → no flip
        quote=LivePriceQuote(mid=0.6, half_spread_frac=0.0, liquidity_usd=20.0),
        signal_source=real_src,
        ledger=ledger,
    )
    ti = src.inputs_for(asof_ts=_ASOF, tick=0)
    assert ti is not None
    # Re-run signals_for directly against the SAME shared ledger at the same asof.
    expected = real_src.signals_for(market_id="m2", tick=0, asof_ts=_ASOF)
    for slot in (MARKET_MOMENTUM, TENNIS_TECHNICAL, SURFACE_ADVANTAGE, HEAD_TO_HEAD, REST_RECENCY):
        assert ti.signals[slot].score == pytest.approx(expected[slot].score)
    # The tennis facets actually fired (Sinner favoured) — not all-neutral.
    assert ti.signals[TENNIS_TECHNICAL].score > 0.0
