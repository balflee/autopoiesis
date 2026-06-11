# agent/backtest/tennis_match_resolver.py
"""Resolve a tennis cassette slug to its two Sackmann players + surface.

Cassette slugs carry a human ``...-<NameA>-vs-<NameB>`` suffix (full surnames,
e.g. ``...-Putintseva-vs-Hon``) plus a tournament keyword. We parse the suffix
for the two surnames and map the tournament keyword to a court surface, then
look the surnames up in a Sackmann-derived name->id map (see ``build_name_index``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import pandas as pd

# Tournament keyword -> surface. Default Hard. Order matters (first hit wins).
_SURFACE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("french-open", "Clay"),
    ("roland-garros", "Clay"),
    ("monte-carlo", "Clay"),
    ("madrid", "Clay"),
    ("rome", "Clay"),
    ("internazionali", "Clay"),
    ("hamburg", "Clay"),
    ("wimbledon", "Grass"),
    ("queens", "Grass"),
    ("halle", "Grass"),
    ("eastbourne", "Grass"),
)

# Captures the trailing "...-<NameA>-vs-<NameB>" (case preserved by group).
_VS_SUFFIX = re.compile(r"-([A-Za-z]+)-vs-([A-Za-z]+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedSlug:
    p1_surname: str
    p2_surname: str
    surface: str


def _norm_surname(raw: str) -> str:
    # Strip accents, lowercase — cassette slugs are ASCII, Sackmann may have accents.
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_only.lower()


def _surface_for(slug: str) -> str:
    # Match a keyword only as a whole hyphen-delimited segment (or run of segments),
    # not as a bare substring — otherwise short keywords false-match inside surnames
    # (e.g. "rome" inside "jerome" would wrongly yield Clay). Wrapping both sides in
    # "-" makes start/end and segment boundaries match uniformly.
    padded = f"-{slug.lower()}-"
    for keyword, surface in _SURFACE_KEYWORDS:
        if f"-{keyword}-" in padded:
            return surface
    return "Hard"


def parse_slug(slug: str) -> ParsedSlug | None:
    m = _VS_SUFFIX.search(slug)
    if m is None:
        return None
    return ParsedSlug(
        p1_surname=_norm_surname(m.group(1)),
        p2_surname=_norm_surname(m.group(2)),
        surface=_surface_for(slug),
    )


def build_name_index(match_frames: list[pd.DataFrame]) -> dict[str, str]:
    """Build ``{normalized_surname: player_id}`` from Sackmann match frames.

    Uses the ``winner_name/winner_id`` and ``loser_name/loser_id`` columns.
    Last write wins (later frames / rows override) — deterministic given input
    order. Surname = last whitespace-delimited token of the full name.
    """
    index: dict[str, str] = {}
    for df in match_frames:
        for col_name, col_id in (("winner_name", "winner_id"), ("loser_name", "loser_id")):
            for full_name, pid in zip(df[col_name], df[col_id], strict=True):
                pid_s = str(pid).strip()
                if not full_name or not pid_s or pid_s.lower() == "nan":
                    continue
                surname = _norm_surname(str(full_name).split()[-1])
                index[surname] = pid_s
    return index


@dataclass(frozen=True)
class ResolvedMatch:
    p1_id: str
    p2_id: str
    surface: str


@dataclass(frozen=True)
class TennisMatchResolver:
    """Resolve a slug -> ResolvedMatch using a prebuilt Sackmann name index."""

    name_index: dict[str, str]

    def resolve(self, slug: str) -> ResolvedMatch | None:
        parsed = parse_slug(slug)
        if parsed is None:
            return None
        p1 = self.name_index.get(parsed.p1_surname)
        p2 = self.name_index.get(parsed.p2_surname)
        if p1 is None or p2 is None:
            return None
        return ResolvedMatch(p1_id=p1, p2_id=p2, surface=parsed.surface)

    @classmethod
    def from_sackmann_loader(
        cls, loader: object, *, year_range: tuple[int, int]
    ) -> TennisMatchResolver:
        # Duck-typed: loader has load_atp_matches / load_wta_matches.
        frames: list[pd.DataFrame] = []
        for getter in ("load_atp_matches", "load_wta_matches"):
            try:
                frames.append(getattr(loader, getter)(year_range))
            except Exception:  # missing year on disk / network — skip that tour
                continue
        return cls(name_index=build_name_index(frames))


def _coverage_report(cache_dir: str, year_range: tuple[int, int]) -> None:
    import glob
    import json

    from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

    # Read the full re-vendored corpus (DEFAULT_CORPUS_DIR), NOT the default snapshot
    # dir — the latter holds only the small synthetic test fixtures, so a bare
    # SackmannLoader() would miss 2026 and silently GitHub-fetch it (online + a mixed,
    # lower coverage number). The corpus dir gives the true offline coverage (~65.8%).
    resolver = TennisMatchResolver.from_sackmann_loader(
        SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR), year_range=year_range
    )
    files = [f for f in glob.glob(f"{cache_dir}/*.json") if "gitkeep" not in f]
    resolved = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            slug = json.load(fh)["slug"]
        if resolver.resolve(slug) is not None:
            resolved += 1
    pct = 100 * resolved / len(files) if files else 0.0
    print(f"resolved {resolved}/{len(files)} cassettes ({pct:.1f}%)")


if __name__ == "__main__":  # python -m agent.backtest.tennis_match_resolver
    import sys

    _coverage_report(
        sys.argv[1] if len(sys.argv) > 1 else "agent/backtest/_cache_tennis", (2024, 2026)
    )
