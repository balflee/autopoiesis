# tests/agent/backtest/test_real_signal_source.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

# NB: import the engine module before ``data.sources.tennis_sackmann`` to avoid a
# circular import in ``data.sources`` package init (engines pull it in transitively).
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.real_signal_source import (
    RealSignalSource,
    elo_signal,
    h2h_signal,
    momentum_signal,
    rest_signal,
    surface_signal,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from data.sources.tennis_sackmann import SackmannLoader

_TINY = Path(__file__).parent / "fixtures" / "sackmann_tiny"


def _tiny_loader() -> SackmannLoader:
    return SackmannLoader(snapshot_dir=_TINY)


def _pts(*pairs):
    return [(datetime.fromisoformat(ts), p) for ts, p in pairs]


def test_momentum_rising_price_gives_positive_score() -> None:
    snaps = _pts(
        ("2026-01-01T00:00:00+00:00", 0.40),
        ("2026-01-01T06:00:00+00:00", 0.55),
        ("2026-01-01T12:00:00+00:00", 0.70),
    )
    sig = momentum_signal(snaps, asof_ts=datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert sig.score > 0.0
    assert 0.0 <= sig.confidence <= 1.0
    assert -1.0 <= sig.score <= 1.0


def test_momentum_empty_history_is_neutral() -> None:
    sig = momentum_signal([], asof_ts=datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert sig.score == 0.0 and sig.confidence == 0.0


class _FakeProvider:
    def __init__(self, snap):
        self._snap = snap
    def get(self, market_id):
        return self._snap


def _snap():
    return MarketSnapshot(
        market_id="m1",
        slug="will-rain-stop-play",  # NOT a -vs- slug -> resolver returns None
        end_date_iso="2026-01-01T20:00:00+00:00",
        resolution_ts_iso="2026-01-01T19:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[
            PricePoint(ts="2026-01-01T00:00:00+00:00", mid_price=0.4),
            PricePoint(ts="2026-01-01T06:00:00+00:00", mid_price=0.6),
        ],
    )


def test_signals_for_returns_all_five_slots() -> None:
    src = RealSignalSource(
        provider=_FakeProvider(_snap()),
        resolver=TennisMatchResolver(name_index={}),
    )
    out = src.signals_for(
        market_id="m1", tick=0, asof_ts=datetime(2026, 1, 1, 6, tzinfo=UTC)
    )
    assert set(out) == {
        "tennis_technical", "market_momentum", "smart_money",
        "sentiment_llm", "crowd_volume",
    }
    # momentum is real (price rose 0.4 -> 0.6)
    assert out["market_momentum"].score > 0.0
    # unresolved slug -> tennis facets neutral
    assert out["tennis_technical"].score == 0.0
    assert out["smart_money"].confidence == 0.0


# --- C1: the 4 Sackmann facet normalizers ----------------------------------
#
# Fixture ground truth (sackmann_tiny, asof 2025-06-01, year_range=(2025, 2025)):
#   * rankings: Sinner 200001=11000 pts, Shelton 200002=2500, Tiafoe 200003=2100
#   * matches : Sinner beat Shelton (Hard), Shelton beat Tiafoe (Hard)
# so elo(Sinner-Shelton)=8500, surface(Sinner-Shelton,Hard)=+0.5,
# h2h(Sinner-Shelton).p1_win_rate=1.0, rest: Sinner=142d Shelton=142d Tiafoe=143d.

_ASOF = datetime(2025, 6, 1, tzinfo=UTC)


def test_elo_signal_favours_higher_ranked_player() -> None:
    # Sinner(200001, 11000 pts) vs Shelton(200002, 2500) -> p1 strong positive.
    sig = elo_signal("200001", "200002", asof_ts=_ASOF, loader=_tiny_loader())
    assert sig.score > 0.5
    assert sig.confidence == 0.7
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 <= sig.confidence <= 1.0


def test_elo_signal_neutral_when_unranked() -> None:
    sig = elo_signal("999998", "999999", asof_ts=_ASOF, loader=_tiny_loader())
    assert sig.score == 0.0 and sig.confidence == 0.0


def test_surface_signal_favours_better_surface_record() -> None:
    # Sinner is perfect on Hard vs Shelton's 0.5 -> positive favours p1 (Sinner).
    sig = surface_signal(
        "200001", "200002", "Hard", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert sig.score > 0.0
    assert sig.confidence == 0.6
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 <= sig.confidence <= 1.0
    # Reversing the players flips the sign (smart_money is symmetric).
    rev = surface_signal(
        "200002", "200001", "Hard", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert rev.score < 0.0


def test_surface_signal_neutral_when_no_edge() -> None:
    # Tiafoe vs Shelton: no surface history for Tiafoe as winner -> delta 0.0.
    sig = surface_signal(
        "999998", "999999", "Hard", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert sig.score == 0.0 and sig.confidence == 0.0


def test_h2h_signal_favours_h2h_winner() -> None:
    # Sinner beat Shelton in their only meeting -> p1_win_rate=1.0 -> score=+1.0.
    sig = h2h_signal(
        "200001", "200002", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert sig.score > 0.0
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 < sig.confidence <= 1.0
    # Reversed players: Shelton lost their only H2H -> negative.
    rev = h2h_signal(
        "200002", "200001", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert rev.score < 0.0


def test_h2h_signal_neutral_when_never_met() -> None:
    sig = h2h_signal(
        "999998", "999999", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert sig.score == 0.0 and sig.confidence == 0.0


def test_rest_signal_positive_when_opponent_more_rested() -> None:
    # Sinner(p1, 142d) vs Tiafoe(p2, 143d): d2-d1 = +1 -> score>0 per the
    # documented formula tanh((d2-d1)/14).
    sig = rest_signal(
        "200001", "200003", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert sig.score > 0.0
    assert sig.confidence == 0.4
    assert -1.0 <= sig.score <= 1.0
    assert 0.0 <= sig.confidence <= 1.0
    # Reversed: Tiafoe(p1, 143d) vs Sinner(p2, 142d) -> d2-d1 = -1 -> negative.
    rev = rest_signal(
        "200003", "200001", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert rev.score < 0.0


def test_rest_signal_neutral_when_player_missing() -> None:
    sig = rest_signal(
        "999998", "999999", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    )
    assert sig.score == 0.0 and sig.confidence == 0.0


# --- C2: wire the 4 facets into RealSignalSource via the resolver -----------
#
# Build a resolver from the tiny fixture name index (Sinner 200001 /
# Shelton 200002 / Tiafoe 200003) and feed it a snap whose slug resolves
# (`...-Sinner-vs-Shelton`). The 4 tennis slots must become non-neutral and
# match the standalone facet normalizers; an unresolved slug stays neutral.


def _resolved_snap() -> MarketSnapshot:
    return MarketSnapshot(
        market_id="m2",
        slug="test-open-2025-06-01-Sinner-vs-Shelton",  # resolves via tiny index
        end_date_iso="2025-06-01T20:00:00+00:00",
        resolution_ts_iso="2025-06-01T19:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[
            PricePoint(ts="2025-05-31T00:00:00+00:00", mid_price=0.4),
            PricePoint(ts="2025-05-31T06:00:00+00:00", mid_price=0.6),
        ],
    )


def _tiny_resolver() -> TennisMatchResolver:
    import pandas as pd

    from agent.backtest.tennis_match_resolver import build_name_index

    df = pd.read_csv(_TINY / "atp_matches_2025.csv", dtype=str).fillna("")
    return TennisMatchResolver(name_index=build_name_index([df]))


def test_signals_for_resolved_slug_fills_tennis_facets() -> None:
    src = RealSignalSource(
        provider=_FakeProvider(_resolved_snap()),
        resolver=_tiny_resolver(),
        loader=_tiny_loader(),
        year_range=(2025, 2025),
    )
    out = src.signals_for(
        market_id="m2", tick=0, asof_ts=_ASOF,
    )
    assert set(out) == {
        "tennis_technical", "market_momentum", "smart_money",
        "sentiment_llm", "crowd_volume",
    }
    # momentum is real (price rose 0.4 -> 0.6 before asof)
    assert out["market_momentum"].score > 0.0
    # the 4 tennis facets are now REAL (Sinner strongly favoured over Shelton).
    assert out["tennis_technical"].score > 0.5      # elo: 11000 vs 2500 -> +
    assert out["tennis_technical"].confidence == 0.7
    assert out["smart_money"].score > 0.0           # surface: Sinner perfect on Hard
    assert out["smart_money"].confidence == 0.6
    assert out["sentiment_llm"].score > 0.0         # h2h: Sinner beat Shelton
    assert out["sentiment_llm"].confidence > 0.0
    # rest: both 142d apart in fixture -> score 0.0 but signal still emitted
    assert out["crowd_volume"].confidence == 0.4
    # the wired facets MUST equal the standalone normalizer outputs.
    assert out["tennis_technical"].score == elo_signal(
        "200001", "200002", asof_ts=_ASOF, loader=_tiny_loader()
    ).score
    assert out["sentiment_llm"].score == h2h_signal(
        "200001", "200002", asof_ts=_ASOF,
        loader=_tiny_loader(), year_range=(2025, 2025),
    ).score


def test_signals_for_unresolved_slug_stays_neutral() -> None:
    # A non -vs- slug does not resolve -> all 4 tennis facets neutral, momentum real.
    src = RealSignalSource(
        provider=_FakeProvider(_snap()),
        resolver=_tiny_resolver(),
        loader=_tiny_loader(),
        year_range=(2025, 2025),
    )
    out = src.signals_for(
        market_id="m1", tick=0, asof_ts=datetime(2026, 1, 1, 6, tzinfo=UTC)
    )
    assert out["market_momentum"].score > 0.0
    for slot in ("tennis_technical", "smart_money", "sentiment_llm", "crowd_volume"):
        assert out[slot].score == 0.0
        assert out[slot].confidence == 0.0
