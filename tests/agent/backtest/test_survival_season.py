# tests/agent/backtest/test_survival_season.py
"""A0 — SurvivalRow joined-schema + builder.

A ``SurvivalRow`` JOINs three sources WITHOUT mutating the cached
:class:`~agent.backtest.cached_sweep.SignalRow` (mutating it would break the
already-written ``reports/backtest/_signal_rows.json``):

* the cached signal scores/confidences + ``entry_price``/settlement copy
  (the ``SignalRow`` itself, embedded);
* the :class:`~agent.backtest.historical_fetcher.MarketSnapshot` settlement
  fields (``resolution_ts_iso``/``end_date_iso``/``outcome``/``winning_price``/
  ``liquidity_cap_usd``);
* ``entry_asof_ts_iso`` RECOMPUTED deterministically from the cassette
  ``price_ledger`` (mirroring ``cached_sweep``'s mid-market entry logic) and
  asserted consistent with the row's ``entry_price``;
* display ``players`` (surnames via ``parse_slug``) + ``surface`` (via
  :class:`TennisMatchResolver`, nullable UI fallback).

TDD on a tiny 3-market fixture (Sinner / Shelton / Tiafoe), mirroring the
existing ``test_cached_sweep`` fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.survival_season import (
    SurvivalRow,
    build_survival_rows,
)
from agent.backtest.tennis_match_resolver import (
    TennisMatchResolver,
    build_name_index,
)

_TINY = Path(__file__).parent / "fixtures" / "sackmann_tiny"


def _tiny_resolver() -> TennisMatchResolver:
    df = pd.read_csv(_TINY / "atp_matches_2025.csv", dtype=str).fillna("")
    return TennisMatchResolver(name_index=build_name_index([df]))


# --------------------------------------------------------------------------- #
# Fixture: a tiny 3-market universe + matching cached SignalRows.
# --------------------------------------------------------------------------- #
#
# m_a / m_b resolve to two players (Sinner-vs-Shelton, Shelton-vs-Tiafoe) and
# carry multi-point ledgers whose mid-market (entry_fraction=0.5) entry price is
# KNOWN. m_c resolves to players via the slug but is given a Wimbledon (Grass)
# tournament keyword to exercise the surface mapping. The cached rows mirror
# what ``precompute_rows`` would have written for these snaps (only entry_price +
# scores/settlement are load-bearing for A0).


def _snap_a() -> MarketSnapshot:
    # 00:00 -> 18:00 span; mid (09:00) -> last point at-or-before is 06:00 @ 0.50.
    return MarketSnapshot(
        market_id="m_a",
        slug="test-open-2025-06-01-Sinner-vs-Shelton",
        end_date_iso="2025-06-02T00:00:00+00:00",
        resolution_ts_iso="2025-06-01T23:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[
            PricePoint(ts="2025-06-01T00:00:00+00:00", mid_price=0.40),
            PricePoint(ts="2025-06-01T06:00:00+00:00", mid_price=0.50),
            PricePoint(ts="2025-06-01T12:00:00+00:00", mid_price=0.55),
            PricePoint(ts="2025-06-01T18:00:00+00:00", mid_price=0.70),
        ],
    )


def _snap_b() -> MarketSnapshot:
    # 00:00 -> 10:00 span; mid (05:00) -> last point at-or-before is 04:00 @ 0.62.
    return MarketSnapshot(
        market_id="m_b",
        slug="test-open-2025-06-03-Shelton-vs-Tiafoe",
        end_date_iso="2025-06-04T00:00:00+00:00",
        resolution_ts_iso="2025-06-03T20:00:00+00:00",
        outcome="no",
        winning_price=1.0,
        liquidity_cap_usd=12.0,
        price_ledger=[
            PricePoint(ts="2025-06-03T00:00:00+00:00", mid_price=0.55),
            PricePoint(ts="2025-06-03T04:00:00+00:00", mid_price=0.62),
            PricePoint(ts="2025-06-03T10:00:00+00:00", mid_price=0.66),
        ],
    )


def _snap_c_grass() -> MarketSnapshot:
    # Single-point ledger -> entry is that point. Wimbledon -> Grass surface.
    return MarketSnapshot(
        market_id="m_c",
        slug="wimbledon-2025-07-01-Sinner-vs-Tiafoe",
        end_date_iso="2025-07-02T00:00:00+00:00",
        resolution_ts_iso="2025-07-01T21:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=8.0,
        price_ledger=[
            PricePoint(ts="2025-07-01T12:00:00+00:00", mid_price=0.48),
        ],
    )


def _row_for(snap: MarketSnapshot, *, entry_price: float) -> SignalRow:
    """A cached SignalRow as ``precompute_rows`` would have written for ``snap``.

    Only ``entry_price`` (for the consistency assertion) + the settlement copy
    matter for A0; scores/confidences are populated with arbitrary-but-present
    5-slot maps so the join carries them through.
    """
    slots = (
        "tennis_technical",
        "market_momentum",
        "smart_money",
        "sentiment_llm",
        "crowd_volume",
    )
    return SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: 0.1 for k in slots},
        confidences={k: 0.5 for k in slots},
        entry_price=entry_price,
        outcome=snap.outcome or "",
        winning_price=snap.winning_price or 0.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
    )


def test_build_joins_signal_settlement_entry_players_surface() -> None:
    snaps = [_snap_a(), _snap_b(), _snap_c_grass()]
    rows = [
        _row_for(_snap_a(), entry_price=0.50),
        _row_for(_snap_b(), entry_price=0.62),
        _row_for(_snap_c_grass(), entry_price=0.48),
    ]
    out = build_survival_rows(rows, snaps, _tiny_resolver())

    assert [r.market_id for r in out] == ["m_a", "m_b", "m_c"]
    assert all(isinstance(r, SurvivalRow) for r in out)

    a = out[0]
    # (a) cached signal carried through WITHOUT mutation.
    assert isinstance(a.signal, SignalRow)
    assert a.signal is rows[0]
    assert a.scores == rows[0].scores
    assert a.confidences == rows[0].confidences
    assert a.entry_price == 0.50
    # (b) settlement fields from the MarketSnapshot.
    assert a.resolution_ts_iso == "2025-06-01T23:00:00+00:00"
    assert a.end_date_iso == "2025-06-02T00:00:00+00:00"
    assert a.outcome == "yes"
    assert a.winning_price == 1.0
    assert a.liquidity_cap == 20.0
    # (c) entry_asof recomputed from the ledger, consistent with entry_price.
    #     mid (09:00) -> last point at-or-before is the 06:00 point.
    assert a.entry_asof_ts_iso == "2025-06-01T06:00:00+00:00"
    # (d) display players (surnames) + surface (Hard for "test-open").
    assert a.players == ("sinner", "shelton")
    assert a.surface == "Hard"

    b = out[1]
    assert b.entry_asof_ts_iso == "2025-06-03T04:00:00+00:00"
    assert b.entry_price == 0.62
    assert b.outcome == "no"
    assert b.players == ("shelton", "tiafoe")

    c = out[2]
    # Single-point ledger -> entry asof is that single point.
    assert c.entry_asof_ts_iso == "2025-07-01T12:00:00+00:00"
    assert c.entry_price == 0.48
    # Wimbledon keyword -> Grass surface.
    assert c.surface == "Grass"
    assert c.players == ("sinner", "tiafoe")


def _snap_longshot(market_id: str, mid_price: float) -> MarketSnapshot:
    """Single-point-ledger snapshot at an arbitrary (possibly sub-floor) price."""
    return MarketSnapshot(
        market_id=market_id,
        slug="test-open-2025-06-05-Alpha-vs-Bravo",
        end_date_iso="2025-06-06T00:00:00+00:00",
        resolution_ts_iso="2025-06-05T21:00:00+00:00",
        outcome="no",
        winning_price=1.0,
        liquidity_cap_usd=5.0,
        price_ledger=[PricePoint(ts="2025-06-05T12:00:00+00:00", mid_price=mid_price)],
    )


def test_entry_price_floor_drops_sub_floor_rows_inclusive_boundary() -> None:
    """Realism floor: < 0.05 dropped; == 0.05 KEPT (inclusive); normal prices kept."""
    snaps = [
        _snap_a(),
        _snap_longshot("m_lot", 0.0005),
        _snap_longshot("m_edge", 0.05),
    ]
    rows = [
        _row_for(snaps[0], entry_price=0.50),
        _row_for(snaps[1], entry_price=0.0005),
        _row_for(snaps[2], entry_price=0.05),
    ]
    out = build_survival_rows(rows, snaps, _tiny_resolver())  # default floor 0.05
    assert [r.market_id for r in out] == ["m_a", "m_edge"]
    assert all(r.entry_price >= 0.05 for r in out)


def test_entry_price_floor_none_keeps_legacy_universe() -> None:
    """``entry_price_floor=None`` disables the filter (legacy physics)."""
    snaps = [_snap_a(), _snap_longshot("m_lot", 0.0005)]
    rows = [
        _row_for(snaps[0], entry_price=0.50),
        _row_for(snaps[1], entry_price=0.0005),
    ]
    out = build_survival_rows(rows, snaps, _tiny_resolver(), entry_price_floor=None)
    assert [r.market_id for r in out] == ["m_a", "m_lot"]


def test_surface_nullable_when_slug_unresolvable() -> None:
    # A non "-vs-" slug does not resolve to a ResolvedMatch -> surface is None
    # (UI fallback) and players is None, but the row is still built (settlement
    # facts + entry are valid).
    snap = MarketSnapshot(
        market_id="m_nores",
        slug="will-rain-stop-play",
        end_date_iso="2025-06-02T00:00:00+00:00",
        resolution_ts_iso="2025-06-01T23:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=5.0,
        price_ledger=[PricePoint(ts="2025-06-01T00:00:00+00:00", mid_price=0.40)],
    )
    row = _row_for(snap, entry_price=0.40)
    out = build_survival_rows([row], [snap], _tiny_resolver())
    assert len(out) == 1
    assert out[0].surface is None
    assert out[0].players is None
    assert out[0].entry_asof_ts_iso == "2025-06-01T00:00:00+00:00"


def test_entry_price_inconsistency_raises() -> None:
    # If a row's entry_price disagrees with the recomputed mid-market entry off
    # the snapshot ledger, the join MUST fail loudly (it would silently desync
    # the schedule from the cached PnL otherwise).
    snap = _snap_a()  # recomputed mid entry_price = 0.50
    bad_row = _row_for(snap, entry_price=0.40)  # wrong
    with pytest.raises(ValueError, match="entry_price"):
        build_survival_rows([bad_row], [snap], _tiny_resolver())


def test_missing_snapshot_for_row_raises() -> None:
    snap = _snap_a()
    row = _row_for(_snap_b(), entry_price=0.62)  # row m_b, but only snap m_a given
    with pytest.raises(KeyError, match="m_b"):
        build_survival_rows([row], [snap], _tiny_resolver())
