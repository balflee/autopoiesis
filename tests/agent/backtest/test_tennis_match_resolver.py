# tests/agent/backtest/test_tennis_match_resolver.py
from __future__ import annotations

from pathlib import Path

from agent.backtest.tennis_match_resolver import (
    ParsedSlug,
    ResolvedMatch,
    TennisMatchResolver,
    build_name_index,
    parse_slug,
)

_FIX = Path(__file__).parent / "fixtures" / "sackmann_tiny"


def _atp_matches_df():  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.read_csv(_FIX / "atp_matches_2025.csv", dtype=str).fillna("")


def test_parse_first_set_winner_slug_uses_full_name_suffix() -> None:
    p = parse_slug("wta-putints-hon-2026-01-01-first-set-winner-Putintseva-vs-Hon")
    assert p == ParsedSlug(p1_surname="putintseva", p2_surname="hon", surface="Hard")


def test_parse_clay_tournament_maps_surface() -> None:
    p = parse_slug("french-open-alcaraz-vs-sinner")
    assert p is not None
    assert p.surface == "Clay"
    assert p.p1_surname == "alcaraz" and p.p2_surname == "sinner"


def test_parse_grass_tournament() -> None:
    p = parse_slug("wimbledon-djokovic-vs-musetti")
    assert p is not None and p.surface == "Grass"


def test_parse_returns_none_without_vs() -> None:
    assert parse_slug("will-any-tennis-upset-happen-2025") is None


def test_build_name_index_maps_surname_to_id() -> None:
    idx = build_name_index([_atp_matches_df()])
    assert idx["sinner"] == "200001"
    assert idx["shelton"] == "200002"
    assert idx["tiafoe"] == "200003"


def test_resolver_resolves_known_match() -> None:
    resolver = TennisMatchResolver(name_index=build_name_index([_atp_matches_df()]))
    rm = resolver.resolve("test-open-shelton-vs-tiafoe")
    assert rm == ResolvedMatch(p1_id="200002", p2_id="200003", surface="Hard")


def test_resolver_returns_none_on_unknown_player() -> None:
    resolver = TennisMatchResolver(name_index=build_name_index([_atp_matches_df()]))
    assert resolver.resolve("test-open-nadal-vs-federer") is None


class _FakeLoader:
    def __init__(self, atp, wta):  # type: ignore[no-untyped-def]
        self._atp, self._wta = atp, wta

    def load_atp_matches(self, year_range):  # type: ignore[no-untyped-def]
        return self._atp

    def load_wta_matches(self, year_range):  # type: ignore[no-untyped-def]
        return self._wta


def test_from_sackmann_loader_builds_index() -> None:
    import pandas as pd

    empty = pd.DataFrame(columns=["winner_name", "winner_id", "loser_name", "loser_id"])
    loader = _FakeLoader(atp=_atp_matches_df(), wta=empty)
    resolver = TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2025))
    assert resolver.resolve("x-sinner-vs-shelton") == ResolvedMatch(
        p1_id="200001", p2_id="200002", surface="Hard"
    )


def test_surface_keyword_matches_only_on_segment_boundary() -> None:
    # A surface keyword must match a whole hyphen-delimited segment, not a substring
    # buried inside a surname: 'rome' inside 'jerome' must NOT yield Clay (would feed
    # the surface-advantage facet a wrong court).
    buried = parse_slug("atp-cup-2026-medvedev-vs-jerome")
    assert buried is not None and buried.surface == "Hard"
    # But a real Rome (clay) tournament segment still maps to Clay.
    real = parse_slug("rome-masters-sinner-vs-alcaraz")
    assert real is not None and real.surface == "Clay"
    # Multi-segment keyword still works on its boundaries.
    fo = parse_slug("french-open-alcaraz-vs-sinner")
    assert fo is not None and fo.surface == "Clay"
