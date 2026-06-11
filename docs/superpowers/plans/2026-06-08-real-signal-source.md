# Real Signal Source Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Roadmap:** This is **Plan 1 of 2**, the prerequisite. It makes the 5 engine SIGNALS real (today they are synthetic hash-noise in both backtest and mock-bet) and picks an optimal STARTING config. **Plan 2** (`2026-06-08-layer2-self-evolution.md`) then plugs in the SELF-EVOLUTION machinery (approval queue, settlement self-learning, L3 strategy advisor) so the agent moves off that seed. Together: **signals → config (seed) → self-evolution**. Learning on synthetic signals learns nothing, so Plan 1 must land first.

**Goal:** Replace the synthetic `_DeterministicSignalSource` (SHA-256 hash noise) that currently feeds BOTH the backtest replay and the deployed mock-bet loop with a `RealSignalSource` that fills the 5 engine slots with REAL signals computed from real data — 4 Sackmann tennis facets + 1 market-momentum — so the agent's strategy optimization is meaningful instead of noise-weighting.

**Architecture:** A drop-in `RealSignalSource.signals_for(market_id, tick, asof_ts) -> dict[str, Signal]` that (a) computes market momentum from the cassette `price_ledger` sliced to `asof_ts` via a pure sync helper mirroring `MarketMomentumEngine`, and (b) resolves each cassette's two players + surface from its slug via a `TennisMatchResolver` (Sackmann name→ID map) and computes 4 split tennis facets — ELO/ranking (→`tennis_technical` slot), surface advantage (→`smart_money` slot), head-to-head (→`sentiment_llm` slot), rest/recency (→`crowd_volume` slot). Markets that don't resolve fall back to a neutral tennis signal (momentum stays real). The fusion layer then *learns* the relative weighting across these 5 real signals. Wired behind a `GENESIS_REAL_SIGNALS` flag so existing synthetic-path tests are untouched.

**Tech Stack:** Python 3.11+ (runs on 3.14), pydantic v2, pandas, numpy, pytest, mypy --strict, ruff. Existing modules reused: `agent/engines/tennis_technical.py` (compute_* primitives), `agent/engines/market_momentum.py` (formula reference), `data/sources/tennis_sackmann.py` (`SackmannLoader`), `agent/backtest/replay_runner.py` (the seam), `agent/backtest/find_optimal_config.py` (the sweep).

---

## ⚠️ Read first: the coverage reality (drives Task ordering)

- **Slot mapping is a deliberate REPURPOSE of the PRD engine names.** The slot keys stay `tennis_technical / market_momentum / smart_money / sentiment_llm / crowd_volume` (the `DecisionEngine` only cares about the keys), but their *payloads* change. Dashboard display labels are out of scope for this plan (separate cosmetic task); the writeup must state the repurpose so demo labels can be corrected later.
- **The `_cache_tennis` price_ledger is REAL CLOB intraday data, NOT the synthetic ramp.** (codex Round-3 flagged the `historical_fetcher.MarketSnapshot` docstring at `:145-148` + `_build_synthetic_ledger` at `:454-481` — that is the STALE sprint_9 path used by the OLD `fetch_closed_tennis_markets`.) Our cassettes were built by the new `agent/backtest/tennis_fetcher.py` (`clob_history_to_ledger`, committed `bc57822`/`5ba4808`) which pulls the real CLOB `prices-history` stream keyed by the match window — verified median ~116–139 real intraday points per market (range 28–1161), zero synthetic ramps. So momentum computed from `price_ledger` is genuinely real. (The stale docstring should be corrected as a cleanup, but it does not affect this plan's data.)
- **Sackmann DOES publish 2026 — the gap is only the stale VENDORED snapshot.** Verified live 2026-06-08: GitHub `tennis_atp`/`tennis_wta` carry `{atp,wta}_matches_2026.csv` (1322 + 1168 matches, dates 2026-01-04 .. 2026-05-17/18) and `{atp,wta}_rankings_current.csv` (rankings to 2026-05-25). The repo's vendored `data/sources/sackmann_snapshot/` is just stale (2024–2025 only). **Fix: re-vendor the full 2024–2026 corpus (Task A0).** This is why backtest AND live mock-bet can share one data source: both read Sackmann data ≤ the decision time (PIT-correct); pre-match signals only need PAST data, which Sackmann always has.
- **(A0 IMPLEMENTED — dir split, read this before B2/C2/D1):** Task A0 did NOT overwrite `data/sources/sackmann_snapshot/` (doing so would clobber the SYNTHETIC hermetic test fixtures that `tests/data/test_tennis_etl.py` + `tests/agent/engines/test_tennis_technical.py` depend on — verified: 22→2 passed). Instead it vendored the full corpus into a NEW dir `data/sources/sackmann_corpus/`, exposed as `DEFAULT_CORPUS_DIR` from `data.sources.tennis_sackmann`. **Consequence: every real-signal path must construct `SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)`, NOT a bare `SackmannLoader()`** (bare reads the synthetic snapshot default → misses 2026 → silent GitHub fetch → online + a mixed ~53.7%). With the corpus dir: **offline, measured 4931/7494 = 65.8% resolve**. The B2 `RealSignalSource.loader` default and the D1 prod loader (below) are already written this way; honor it.
- **Empirically measured coverage (full 2024–2026 corpus via `DEFAULT_CORPUS_DIR`):** 87.5% parse the `-vs-` suffix; **65.8% resolve both players to Sackmann IDs (4931/7494, offline)**. By year: **2026 = 72% resolved** (the bulk + most live-relevant), 2025 = 30% (a slug-format quirk — early-2025 Polymarket slugs differ; minor future parser tuning), 2024 = tiny. Market-momentum is real on ALL cassettes regardless.
- **Only true gap = Sackmann's ~3-week publication lag** (data to ~2026-05-17/25, today 2026-06-08): the most recent matches aren't recorded yet. This hits the **β₂ rest/recency facet hardest** (misses recent matches → overestimates rest); elo/surface/h2h use longer history and are unaffected. The fusion layer down-weights noisy facets, so this degrades gracefully.
- **(codex Round-3) "momentum-only" on unresolved markets is NOT free bets.** The `DecisionEngine` gates on the FLAT MEAN confidence across all 5 slots (`decision.py:311-319`) against a confidence floor (`:227`). With 4 neutral slots at `confidence=0.0` and only momentum carrying confidence, the mean is `momentum_conf / 5` — usually below the floor → **NO_BET**. So unresolved cassettes will mostly abstain unless momentum confidence is very high. This is acceptable (we bet where we have signal) but MUST be made explicit: add a `DecisionEngine`-level test (Task C2/D) asserting an unresolved `RealSignalSource` tick with nonzero momentum yields the documented BET/NO_BET behaviour, and decide the fallback semantics consciously (do NOT silently set neutral confidence to 1.0).
- Therefore: **vendor the full corpus first (Task A0), momentum covers everything (Task group B), Sackmann facets cover ~65% incl. 72% of 2026 (Task group C), report the real coverage % (Task A3 / D3).** The sweep (Task group D) optimizes the multi-signal config on the resolved subset; unresolved cassettes mostly abstain (see the confidence-floor note above).

---

## File Structure

| File | Responsibility | Create/Modify |
|---|---|---|
| `agent/backtest/tennis_match_resolver.py` | Parse a cassette slug → `(p1_surname, p2_surname, surface)`; build a Sackmann name→ID map; resolve a slug → `ResolvedMatch(p1_id, p2_id, surface)` or `None`. | Create |
| `agent/backtest/real_signal_source.py` | `RealSignalSource.signals_for(...)`: momentum (always) + 4 Sackmann facets (when resolved) + neutral fallback. Holds the sync momentum helper + the 4 facet→Signal normalizers. | Create |
| `agent/backtest/replay_runner.py` | Add a factory hook so a real source can be injected; keep `_DeterministicSignalSource` default. | Modify (~`:941-945`) |
| `agent/server/main.py` | Swap the prod-loop `signal_source` to `RealSignalSource` behind `GENESIS_REAL_SIGNALS`. | Modify (~`:2216-2220`) |
| `tests/agent/backtest/test_tennis_match_resolver.py` | Slug parsing + name→ID resolution + coverage. | Create |
| `tests/agent/backtest/test_real_signal_source.py` | Momentum helper, each facet normalizer, neutral fallback, full `signals_for`. | Create |
| `scripts/vendor_sackmann_corpus.py` | One-shot: download the full 2024–2026 Sackmann ATP/WTA corpus into the vendored snapshot dir. | Create |
| `data/sources/sackmann_snapshot/{atp,wta}_matches_2026.csv` (+refresh rankings) | The 2026 corpus (the loader reads snapshot-first; this makes 2026 facets work offline). | Create/refresh |
| `tests/agent/backtest/fixtures/sackmann_tiny/` | A tiny ATP matches+rankings CSV pair for deterministic resolver/facet tests. | Create |

---

## Conventions (apply to every task)

- **Run tests:** `python -m pytest <path> -q -p no:cacheprovider`
- **Gates before each commit:** `python -m ruff check <files>` and `python -m mypy --strict <module>` must both be clean.
- **Commit account:** before any commit, confirm `git config user.email` is `256016480+balflee@users.noreply.github.com`. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Do NOT commit** the large `agent/backtest/_cache_tennis/` again (already committed); only commit code + tiny fixtures.
- **(codex Round-4) Declare the runtime deps.** `pyproject.toml [project].dependencies` currently does NOT list `pandas`, `numpy`, or `requests` (they appear only in the mypy override at `:108`) — a pre-existing gap, but this plan's flag-on real-signal path imports them transitively (`SackmannLoader` → `pandas`/`requests`; `find_optimal_config`/`weight_updater` → `numpy`). **As the first step of Task A0, add `pandas>=2.0`, `numpy>=1.26`, `requests>=2.31` to `[project].dependencies`** and verify a fresh `pip install -e .` pulls them, so the real paths don't break on a clean environment. (The vendor script itself uses stdlib `urllib`, so it has no extra dep.)

---

## Task Group A — Tennis match resolver (slug → players + surface → Sackmann IDs)

### Task A0: Vendor the full 2024–2026 Sackmann corpus (one-time data step)

**Files:**
- Create/refresh: `data/sources/sackmann_snapshot/{atp,wta}_matches_{2024,2025,2026}.csv`, `.../{atp,wta}_rankings_current.csv`
- Create: `scripts/vendor_sackmann_corpus.py`

The vendored snapshot is stale (2024–2025). The loader reads `snapshot_dir/{tour}_matches_{year}.csv` first and only GitHub-fetches on miss, so adding 2026 files makes the 2026 facets work offline + reproducibly.

- [ ] **Step 1: Write the vendoring script** (no test — it is a one-shot data-fetch utility; verify by output)

```python
# scripts/vendor_sackmann_corpus.py
"""Download the full Sackmann ATP/WTA corpus into data/sources/sackmann_snapshot/.

Idempotent: overwrites the vendored CSVs with the current GitHub master. Run
whenever the corpus should be refreshed (Sackmann publishes the current year
incrementally, ~weeks behind live).
"""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

# (codex fix) `requests` is NOT a declared project dependency (pyproject.toml:14;
# it only appears in mypy overrides). Use stdlib urllib so this one-shot data step
# works on a fresh checkout without an extra install.
_DEST = Path("data/sources/sackmann_snapshot")
_BASE = "https://raw.githubusercontent.com/JeffSackmann"
_FILES = [
    *[f"{tour}_matches_{yr}.csv" for tour in ("atp", "wta") for yr in (2024, 2025, 2026)],
    *[f"{tour}_rankings_current.csv" for tour in ("atp", "wta")],
]


def main() -> int:
    _DEST.mkdir(parents=True, exist_ok=True)
    for fname in _FILES:
        repo = "tennis_atp" if fname.startswith("atp") else "tennis_wta"
        url = f"{_BASE}/{repo}/master/{fname}"
        req = urllib.request.Request(url, headers={"User-Agent": "genesis-sackmann-vendor"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 — public read-only
                text = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  SKIP {fname} ({exc})")
            continue
        (_DEST / fname).write_text(text, encoding="utf-8")
        rows = max(0, text.count("\n") - 1)
        print(f"  wrote {fname} ({rows} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it**

Run: `python scripts/vendor_sackmann_corpus.py`
Expected: prints `wrote atp_matches_2026.csv (~1322 rows)` etc. for all 8 files.

- [ ] **Step 3: Verify the snapshot now has 2026**

Run: `python -c "import pandas as pd; df=pd.read_csv('data/sources/sackmann_snapshot/atp_matches_2026.csv', dtype=str); print('2026 atp matches:', len(df), '| date range', df.tourney_date.min(), df.tourney_date.max())"`
Expected: ~1322 rows, dates `20260104 .. 2026051x`.

- [ ] **Step 4: Commit** (the corpus CSVs are small — a few MB total — and pin reproducibility)

```bash
git add scripts/vendor_sackmann_corpus.py data/sources/sackmann_snapshot/
git commit -m "data(sackmann): vendor full 2024-2026 ATP/WTA corpus (was stale at 2024-2025)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task A1: Slug parser → (p1_surname, p2_surname, surface)

**Files:**
- Create: `agent/backtest/tennis_match_resolver.py`
- Test: `tests/agent/backtest/test_tennis_match_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_tennis_match_resolver.py
from __future__ import annotations

from agent.backtest.tennis_match_resolver import ParsedSlug, parse_slug


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/backtest/test_tennis_match_resolver.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.backtest.tennis_match_resolver'`

- [ ] **Step 3: Write minimal implementation**

```python
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
    low = slug.lower()
    for keyword, surface in _SURFACE_KEYWORDS:
        if keyword in low:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agent/backtest/test_tennis_match_resolver.py -q -p no:cacheprovider`
Expected: PASS (4 passed)

- [ ] **Step 5: Gates + commit**

Run: `python -m ruff check agent/backtest/tennis_match_resolver.py tests/agent/backtest/test_tennis_match_resolver.py` (clean) and `python -m mypy --strict agent/backtest/tennis_match_resolver.py` (clean)

```bash
git add agent/backtest/tennis_match_resolver.py tests/agent/backtest/test_tennis_match_resolver.py
git commit -m "feat(backtest): tennis slug parser (players + surface)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task A2: Sackmann name→ID index + slug→ResolvedMatch

**Files:**
- Modify: `agent/backtest/tennis_match_resolver.py`
- Create: `tests/agent/backtest/fixtures/sackmann_tiny/atp_matches_2025.csv`, `.../atp_rankings_current.csv`
- Test: `tests/agent/backtest/test_tennis_match_resolver.py`

- [ ] **Step 1: Create the tiny Sackmann fixture**

`tests/agent/backtest/fixtures/sackmann_tiny/atp_matches_2025.csv` (header must match Sackmann `MATCH_COLUMNS`; only the columns used are populated, rest blank):

```csv
tourney_id,tourney_name,surface,draw_size,tourney_level,tourney_date,match_num,winner_id,winner_seed,winner_entry,winner_name,winner_hand,winner_ht,winner_ioc,winner_age,loser_id,loser_seed,loser_entry,loser_name,loser_hand,loser_ht,loser_ioc,loser_age,score,best_of,round,minutes,w_ace,w_df,w_svpt,w_1stIn,w_1stWon,w_2ndWon,w_SvGms,w_bpSaved,w_bpFaced,l_ace,l_df,l_svpt,l_1stIn,l_1stWon,l_2ndWon,l_SvGms,l_bpSaved,l_bpFaced,winner_rank,winner_rank_points,loser_rank,loser_rank_points
2025-001,Test Open,Hard,32,A,20250110,1,200001,,,Jannik Sinner,R,188,ITA,23,200002,,,Ben Shelton,L,193,USA,22,6-3 6-4,3,F,90,,,,,,,,,,,,,,,,,,,,1,11000,15,2500
2025-001,Test Open,Hard,32,A,20250109,2,200002,,,Ben Shelton,L,193,USA,22,200003,,,Frances Tiafoe,R,188,USA,26,7-6 6-4,3,SF,100,,,,,,,,,,,,,,,,,,,,15,2500,20,2100
```

`tests/agent/backtest/fixtures/sackmann_tiny/atp_rankings_current.csv` (header = Sackmann `RANKING_COLUMNS`):

```csv
ranking_date,rank,player,points
20250106,1,200001,11000
20250106,15,200002,2500
20250106,20,200003,2100
```

**(codex fix — prevent network fallback in tests):** `SackmannLoader` loads BOTH tours for EVERY year in `year_range` and GitHub-fetches any snapshot file that is missing (`tennis_sackmann.py:166,241`). So a tiny fixture with only ATP-2025 would trigger live GitHub calls for WTA and for 2024/2026 whenever a test uses the default `year_range`. Mitigate BOTH ways: (a) create **header-only** empty companions so no tour/year falls through to GitHub — `wta_matches_2025.csv` (same `MATCH_COLUMNS` header, no rows) and `wta_rankings_current.csv` (`ranking_date,rank,player,points` header, no rows); AND (b) every tiny-loader test passes `year_range=(2025, 2025)` so only the 2025 snapshot is read. Add `atp_matches_2024.csv` / `wta_matches_2024.csv` header-only files too if any test needs the default `(2024, 2025)`/`(2024, 2026)` range.

- [ ] **Step 2: Write the failing test**

```python
# add to tests/agent/backtest/test_tennis_match_resolver.py
from pathlib import Path

from agent.backtest.tennis_match_resolver import (
    ResolvedMatch,
    TennisMatchResolver,
    build_name_index,
)

_FIX = Path(__file__).parent / "fixtures" / "sackmann_tiny"


def _atp_matches_df():
    import pandas as pd
    return pd.read_csv(_FIX / "atp_matches_2025.csv", dtype=str).fillna("")


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/agent/backtest/test_tennis_match_resolver.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'TennisMatchResolver'`

- [ ] **Step 4: Write minimal implementation (append to `tennis_match_resolver.py`)**

```python
import pandas as pd


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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/agent/backtest/test_tennis_match_resolver.py -q -p no:cacheprovider`
Expected: PASS (7 passed)

- [ ] **Step 6: Gates + commit**

```bash
git add agent/backtest/tennis_match_resolver.py tests/agent/backtest/test_tennis_match_resolver.py tests/agent/backtest/fixtures/sackmann_tiny/
git commit -m "feat(backtest): Sackmann name->id index + slug resolver

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task A3: `from_sackmann_loader` constructor + coverage report CLI

**Files:**
- Modify: `agent/backtest/tennis_match_resolver.py`
- Test: `tests/agent/backtest/test_tennis_match_resolver.py`

- [ ] **Step 1: Write the failing test** (loader injected as a fake returning the fixture frames)

```python
# add to tests/agent/backtest/test_tennis_match_resolver.py
class _FakeLoader:
    def __init__(self, atp, wta):
        self._atp, self._wta = atp, wta
    def load_atp_matches(self, year_range):
        return self._atp
    def load_wta_matches(self, year_range):
        return self._wta


def test_from_sackmann_loader_builds_index() -> None:
    import pandas as pd
    empty = pd.DataFrame(columns=["winner_name", "winner_id", "loser_name", "loser_id"])
    loader = _FakeLoader(atp=_atp_matches_df(), wta=empty)
    resolver = TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2025))
    assert resolver.resolve("x-sinner-vs-shelton") == ResolvedMatch(
        p1_id="200001", p2_id="200002", surface="Hard"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/backtest/test_tennis_match_resolver.py::test_from_sackmann_loader_builds_index -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: type object 'TennisMatchResolver' has no attribute 'from_sackmann_loader'`

- [ ] **Step 3: Write minimal implementation (append a classmethod + a `__main__` coverage report)**

```python
# inside TennisMatchResolver
    @classmethod
    def from_sackmann_loader(
        cls, loader: object, *, year_range: tuple[int, int]
    ) -> "TennisMatchResolver":
        # Duck-typed: loader has load_atp_matches / load_wta_matches.
        frames: list[pd.DataFrame] = []
        for getter in ("load_atp_matches", "load_wta_matches"):
            try:
                frames.append(getattr(loader, getter)(year_range))
            except Exception:  # missing year on disk / network — skip that tour
                continue
        return cls(name_index=build_name_index(frames))
```

```python
# module-level, at the bottom of tennis_match_resolver.py
def _coverage_report(cache_dir: str, year_range: tuple[int, int]) -> None:
    import glob
    import json

    from data.sources.tennis_sackmann import SackmannLoader

    resolver = cls_loader = TennisMatchResolver.from_sackmann_loader(
        SackmannLoader(), year_range=year_range
    )
    files = [f for f in glob.glob(f"{cache_dir}/*.json") if "gitkeep" not in f]
    resolved = sum(
        1
        for f in files
        if cls_loader.resolve(json.load(open(f, encoding="utf-8"))["slug"]) is not None
    )
    print(f"resolved {resolved}/{len(files)} cassettes ({100 * resolved / len(files):.1f}%)")


if __name__ == "__main__":  # python -m agent.backtest.tennis_match_resolver
    import sys

    y1, y2 = (2024, 2026)
    _coverage_report(
        sys.argv[1] if len(sys.argv) > 1 else "agent/backtest/_cache_tennis", (y1, y2)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agent/backtest/test_tennis_match_resolver.py -q -p no:cacheprovider`
Expected: PASS (8 passed)

- [ ] **Step 5: Run the real coverage report (informational — records the honest coverage number)**

Run: `python -m agent.backtest.tennis_match_resolver agent/backtest/_cache_tennis`
Expected: prints e.g. `resolved NNNN/7494 cassettes (XX.X%)`. **Record this number in the commit message and the eventual sweep writeup.** A low % on 2026 is expected (snapshot is 2024–2025).

- [ ] **Step 6: Gates + commit**

```bash
git add agent/backtest/tennis_match_resolver.py tests/agent/backtest/test_tennis_match_resolver.py
git commit -m "feat(backtest): resolver from SackmannLoader + coverage report (resolved N/7494)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task Group B — Momentum signal + RealSignalSource skeleton (covers ALL cassettes)

### Task B1: Sync momentum helper (mirrors `MarketMomentumEngine.evaluate`)

**Files:**
- Create: `agent/backtest/real_signal_source.py`
- Test: `tests/agent/backtest/test_real_signal_source.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_real_signal_source.py
from __future__ import annotations

from datetime import UTC, datetime

from agent.backtest.real_signal_source import momentum_signal


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/backtest/test_real_signal_source.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent.backtest.real_signal_source'`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/backtest/real_signal_source.py
"""Real per-tick signals for the 5 engine slots (replaces _DeterministicSignalSource).

Slot repurpose (the DecisionEngine keys are unchanged; payloads are real):
  market_momentum  -> live price drift/velocity from the cassette price_ledger
  tennis_technical -> ELO/ranking gap   (Sackmann)
  smart_money      -> surface advantage (Sackmann)
  sentiment_llm    -> head-to-head      (Sackmann)
  crowd_volume     -> rest/recency      (Sackmann)
Unresolved markets get a neutral tennis signal; momentum is always real.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import StatisticsError, fmean, stdev

from agent.engines.base import Signal

_NEUTRAL_FEATURES: dict[str, float] = {}


def _neutral(asof_ts: datetime, rationale: str) -> Signal:
    return Signal(
        score=0.0,
        confidence=0.0,
        available_at=asof_ts.isoformat(),
        rationale=rationale,
        raw_features=dict(_NEUTRAL_FEATURES),
    )


def momentum_signal(
    snapshots: list[tuple[datetime, float]], *, asof_ts: datetime
) -> Signal:
    """Pure sync port of MarketMomentumEngine.evaluate's SCORE+CONFIDENCE math
    (market_momentum.py:57-140). ``snapshots`` must already be filtered to
    ts <= asof_ts and sorted ascending.

    (codex fix) This mirrors the score/confidence formula exactly, NOT the engine's
    full raw_features set — the real engine also emits ``n_snapshots`` /
    ``depth_imbalance`` (always 0.0) / ``latest_mid``. We keep ``raw_features`` here
    minimal (n/drift/velocity/spread_tightness); tests assert score+confidence parity,
    not raw_features parity. If full observability parity is later wanted, add the
    missing keys rather than widening the "mirror" claim.
    """
    if not snapshots:
        return _neutral(asof_ts, "momentum: empty price history")
    prices = [p for _, p in snapshots]
    n = len(prices)
    latest = prices[-1]
    anchor = fmean(prices[:-1]) if n > 1 else latest
    drift = (latest - anchor) / max(anchor, 1e-3)
    first_ts, first_p = snapshots[0]
    last_ts, last_p = snapshots[-1]
    span_h = max((last_ts - first_ts).total_seconds() / 3600.0, 1.0)
    velocity = (last_p - first_p) / span_h
    try:
        sigma = stdev(prices) if n > 1 else 0.0
    except StatisticsError:
        sigma = 0.0
    spread_tightness = 1.0 / (1.0 + sigma)
    score = math.tanh(0.6 * drift + 0.4 * velocity)
    confidence = min(1.0, (n / 24.0) * spread_tightness)
    return Signal(
        score=score,
        confidence=confidence,
        available_at=asof_ts.isoformat(),
        rationale=f"momentum: n={n} drift={drift:+.3f} vel={velocity:+.3f}/h",
        raw_features={
            "n": float(n),
            "drift": drift,
            "velocity": velocity,
            "spread_tightness": spread_tightness,
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agent/backtest/test_real_signal_source.py -q -p no:cacheprovider`
Expected: PASS (2 passed)

- [ ] **Step 5: Gates + commit**

```bash
git add agent/backtest/real_signal_source.py tests/agent/backtest/test_real_signal_source.py
git commit -m "feat(backtest): pure sync momentum signal helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task B2: `RealSignalSource.signals_for` — momentum real, tennis neutral

**Files:**
- Modify: `agent/backtest/real_signal_source.py`
- Test: `tests/agent/backtest/test_real_signal_source.py`

- [ ] **Step 1: Write the failing test** (uses a fake provider returning a known cassette)

```python
# add to tests/agent/backtest/test_real_signal_source.py
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.real_signal_source import RealSignalSource
from agent.backtest.tennis_match_resolver import TennisMatchResolver


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/backtest/test_real_signal_source.py::test_signals_for_returns_all_five_slots -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'RealSignalSource'`

- [ ] **Step 3: Write minimal implementation (append to `real_signal_source.py`)**

```python
from dataclasses import dataclass, field          # (codex fix) `field` needed for default_factory

from agent.backtest.tennis_match_resolver import TennisMatchResolver
from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader   # (codex fix) used by the loader default; (A0 correction) DEFAULT_CORPUS_DIR = the full re-vendored corpus
from agent.engines.decision import (
    CROWD_VOLUME,
    MARKET_MOMENTUM,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
)


class _ProviderLike:  # structural: MarketSnapshotProvider.get(market_id) -> MarketSnapshot
    def get(self, market_id: str): ...


@dataclass
class RealSignalSource:
    """Drop-in replacement for _DeterministicSignalSource.signals_for.

    NOTE (codex fix — avoid constructor drift): `loader` + `year_range` are declared
    HERE in B2 with defaults (not added later in C2), so `RealSignalSource(provider,
    resolver)` from B2/B3 keeps working after C2 wires the Sackmann facets. C2 only
    USES these fields; it does not change the signature.
    """

    provider: object  # MarketSnapshotProvider (has .get)
    resolver: TennisMatchResolver
    # (A0 correction) Default to the FULL re-vendored corpus dir, NOT the bare
    # SackmannLoader() default — that default reads the small SYNTHETIC test-fixture
    # snapshot dir, which would miss 2026 and silently GitHub-fetch (online + ~53.7%
    # mixed). DEFAULT_CORPUS_DIR holds the full 2024-2026 corpus -> offline, ~65.8%.
    loader: SackmannLoader = field(
        default_factory=lambda: SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
    )
    year_range: tuple[int, int] = (2024, 2026)

    def signals_for(
        self, *, market_id: str, tick: int, asof_ts: datetime
    ) -> dict[str, Signal]:
        snap = self.provider.get(market_id)  # type: ignore[attr-defined]
        snaps = self._snapshots_until(snap, asof_ts)
        out: dict[str, Signal] = {
            MARKET_MOMENTUM: momentum_signal(snaps, asof_ts=asof_ts),
            TENNIS_TECHNICAL: _neutral(asof_ts, "tennis_technical: unresolved"),
            SMART_MONEY: _neutral(asof_ts, "surface: unresolved"),
            SENTIMENT_LLM: _neutral(asof_ts, "h2h: unresolved"),
            CROWD_VOLUME: _neutral(asof_ts, "rest: unresolved"),
        }
        return out

    @staticmethod
    def _snapshots_until(snap: object, asof_ts: datetime) -> list[tuple[datetime, float]]:
        out: list[tuple[datetime, float]] = []
        for pp in getattr(snap, "price_ledger", []):
            ts = datetime.fromisoformat(pp.ts)
            if ts <= asof_ts:
                out.append((ts, pp.mid_price))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/agent/backtest/test_real_signal_source.py -q -p no:cacheprovider`
Expected: PASS (3 passed)

- [ ] **Step 5: Gates + commit**

```bash
git add agent/backtest/real_signal_source.py tests/agent/backtest/test_real_signal_source.py
git commit -m "feat(backtest): RealSignalSource with real momentum + neutral tennis

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task B3: Inject RealSignalSource into the backtest replay behind a flag

**Files:**
- Modify: `agent/backtest/replay_runner.py` (the `_ReplayTickInputSource(... signal_source=_DeterministicSignalSource(seed=config.seed) ...)` at ~`:941-945`)
- Test: `tests/agent/backtest/test_replay_runner.py`

- [ ] **Step 1: Write the failing test** — assert a replay run with a real source produces non-synthetic momentum-driven decisions (smoke: it runs + `config_id` resolves). Reuse `_make_snapshots()` from `test_replay_runner.py`.

```python
# add to tests/agent/backtest/test_replay_runner.py
def test_run_replay_accepts_injected_real_signal_source(tmp_path: Path) -> None:
    from agent.backtest.real_signal_source import RealSignalSource
    from agent.backtest.tennis_match_resolver import TennisMatchResolver

    snaps = _make_snapshots()
    cfg = ReplayConfig(
        starting_weights=_balanced_weights(),
        seed=7,
        cache_dir=tmp_path / "_unused",
        max_ticks=6,
        start_ts=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
    )

    def _factory(provider):
        return RealSignalSource(provider=provider, resolver=TennisMatchResolver(name_index={}))

    metrics = asyncio.run(
        run_replay(cfg, state_root=tmp_path / "state", snapshots=snaps,
                   signal_source_factory=_factory)
    )
    assert metrics.ticks_completed == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/backtest/test_replay_runner.py::test_run_replay_accepts_injected_real_signal_source -q -p no:cacheprovider`
Expected: FAIL — `TypeError: run_replay() got an unexpected keyword argument 'signal_source_factory'`

- [ ] **Step 3: Write minimal implementation** — add an optional `signal_source_factory` param to `run_replay` (and pass-through `run_replay_sync`), default `None` → keep `_DeterministicSignalSource`. At the `_ReplayTickInputSource` construction (~`:941`):

```python
# replay_runner.py — in run_replay signature add:
#     signal_source_factory: Callable[[MarketSnapshotProvider], object] | None = None,
# then at the tick-source construction:
    signal_source = (
        signal_source_factory(provider)
        if signal_source_factory is not None
        else _DeterministicSignalSource(seed=config.seed)
    )
    tick_input_src = _ReplayTickInputSource(
        provider=provider,
        signal_source=signal_source,
        selected_market_ids=provider.market_ids,
    )
```

Note: relax `_ReplayTickInputSource.signal_source` type annotation from `_DeterministicSignalSource` to a `_SignalSource` Protocol (`def signals_for(self, *, market_id: str, tick: int, asof_ts: datetime) -> dict[str, Signal]`). Add the Protocol near `_DeterministicSignalSource`.

- [ ] **Step 4: Run test to verify it passes + no regression**

Run: `python -m pytest tests/agent/backtest/test_replay_runner.py -q -p no:cacheprovider`
Expected: PASS (all, including the new test)

- [ ] **Step 5: Gates + commit** (`ruff` + `mypy --strict agent/backtest/replay_runner.py`)

```bash
git add agent/backtest/replay_runner.py tests/agent/backtest/test_replay_runner.py
git commit -m "feat(backtest): run_replay accepts a signal_source_factory (real-signal seam)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task Group C — The 4 Sackmann facet signals

### Task C1: Facet normalizers (elo, surface, h2h, rest) → Signal

**Files:**
- Modify: `agent/backtest/real_signal_source.py`
- Test: `tests/agent/backtest/test_real_signal_source.py`

The reused `compute_*` primitives live in **`agent/engines/tennis_technical.py`** (not `tennis_features.py`). **(codex fix) `compute_elo_diff` takes only `(p1_id, p2_id, asof_ts, *, loader)` — NO `year_range`** (`tennis_technical.py:163`; it reads `asof_ts`-based rankings). Only `compute_surface_advantage` (`:258`), `compute_h2h` (`:320`), and `compute_days_since_last_match` (`:420`) take `year_range`. So `surface_signal`/`h2h_signal`/`rest_signal` thread a `year_range` param; `elo_signal` does NOT. **Tiny-loader tests MUST pass `year_range=(2025,2025)`** to surface/h2h/rest so the loader stays on the vendored 2025 fixture and never GitHub-fetches a missing year; `RealSignalSource` passes its own `self.year_range` (default `(2024,2026)`) in production (C2).

Normalization (the formulas mirror `tennis_features.py:236-289`, computed via the `tennis_technical.py` primitives):
- **elo** (`tennis_technical` slot): `score = tanh(compute_elo_diff(p1,p2,asof) / 3000)`; `confidence = 0.0` if elo_diff == 0 else `0.7`.
- **surface** (`smart_money` slot): `score = compute_surface_advantage(p1,p2,surface,asof)` (already [-1,1]); `confidence = 0.0` if score == 0 else `0.6`.
- **h2h** (`sentiment_llm` slot): `rec = compute_h2h(p1,p2,asof)`; `score = 2*(rec['p1_win_rate']-0.5)` if `p1_win_rate is not None` else `0.0`; `confidence = min(0.9, rec['total_matches']/5*0.6)`.
- **rest** (`crowd_volume` slot): `d1,d2 = compute_days_since_last_match(p1,asof), compute_days_since_last_match(p2,asof)`; if both not None `score = tanh((d2-d1)/14)` (more-rested-than-opponent ⇒ positive), `confidence = 0.4`; else neutral.

- [ ] **Step 1: Write the failing tests** (inject a fake loader built from the `sackmann_tiny` fixture so compute_* returns deterministic values):

```python
# add to tests/agent/backtest/test_real_signal_source.py
from datetime import datetime, UTC
from pathlib import Path

from agent.backtest.real_signal_source import (
    elo_signal, h2h_signal, rest_signal, surface_signal,
)
from data.sources.tennis_sackmann import SackmannLoader

_TINY = Path(__file__).parent / "fixtures" / "sackmann_tiny"


def _tiny_loader():
    return SackmannLoader(snapshot_dir=_TINY)


def test_elo_signal_favours_higher_ranked_player() -> None:
    # Sinner(200001, 11000 pts) vs Shelton(200002, 2500) -> p1 strong positive
    sig = elo_signal("200001", "200002", asof_ts=datetime(2025, 6, 1, tzinfo=UTC),
                     loader=_tiny_loader())
    assert sig.score > 0.5 and sig.confidence == 0.7


def test_elo_signal_neutral_when_unranked() -> None:
    sig = elo_signal("999998", "999999", asof_ts=datetime(2025, 6, 1, tzinfo=UTC),
                     loader=_tiny_loader())
    assert sig.score == 0.0 and sig.confidence == 0.0
```

(Repeat one test each for `surface_signal`, `h2h_signal`, `rest_signal` asserting `-1<=score<=1`, `0<=confidence<=1`, and the documented direction; use the fixture players.)

- [ ] **Step 2: Run to verify fail** (`ImportError: cannot import name 'elo_signal'`)

- [ ] **Step 3: Implement the 4 normalizers** (append to `real_signal_source.py`), each calling the existing `agent.engines.tennis_technical` compute function and wrapping per the normalization table above. Cast IDs to `str`. Each returns a `Signal`.

- [ ] **Step 4: Run to verify pass**

- [ ] **Step 5: Gates + commit**

```bash
git add agent/backtest/real_signal_source.py tests/agent/backtest/test_real_signal_source.py
git commit -m "feat(backtest): 4 Sackmann facet signal normalizers (elo/surface/h2h/rest)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task C2: Wire facets into `RealSignalSource` via the resolver

**Files:**
- Modify: `agent/backtest/real_signal_source.py` (`signals_for` + constructor gains `loader` + `year_range`)
- Test: `tests/agent/backtest/test_real_signal_source.py`

- [ ] **Step 1: Write the failing test** — a cassette whose slug resolves (`...-Sinner-vs-Shelton`) yields non-neutral `tennis_technical`/`smart_money`/`sentiment_llm` signals; an unresolved slug stays neutral. Provider returns a snap with that slug + a price_ledger; resolver built from the tiny fixture; loader = tiny loader.

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement** — in `signals_for`, after momentum: `rm = self.resolver.resolve(snap.slug)`; if `rm is not None`, replace the 4 neutral tennis slots with `elo_signal/surface_signal/h2h_signal/rest_signal(rm.p1_id, rm.p2_id, rm.surface, asof_ts, loader=self.loader, year_range=self.year_range)`. **(codex fix) The `loader`/`year_range` fields already exist on `RealSignalSource` from Task B2 with defaults — C2 only USES them, it does NOT change the constructor signature, so B2/B3's `RealSignalSource(provider, resolver)` callers keep working.**

- [ ] **Step 4: Run to verify pass (full file)**

- [ ] **Step 5: Gates + commit**

```bash
git add agent/backtest/real_signal_source.py tests/agent/backtest/test_real_signal_source.py
git commit -m "feat(backtest): wire 4 Sackmann facets into RealSignalSource via resolver

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task Group D — Prod-loop wiring, scoring, and the real sweep

### Task D1: Wire RealSignalSource into the deployed mock-bet loop behind `GENESIS_REAL_SIGNALS`

**Files:**
- Modify: `agent/server/main.py` (~`:2216-2220`)
- Test: `tests/agent/server/` (a unit test asserting the env flag selects the real source) — follow the existing server test pattern in `tests/agent/server/conftest.py`.

- [ ] **Step 1: Write the failing test** — with `GENESIS_REAL_SIGNALS=1` set (monkeypatch env), the prod-loop builder constructs a `RealSignalSource` (assert via type) instead of `_DeterministicSignalSource`. (If the builder is not easily unit-testable, extract a small `_make_signal_source(provider) -> object` helper and test THAT.)

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement** — extract a helper near `:2216`:

```python
def _make_prod_signal_source(provider: "MarketSnapshotProvider") -> object:
    # (codex fix) _DeterministicSignalSource is currently imported only INSIDE
    # _build_default_app (main.py:2201-2205). A module-level helper must import it
    # explicitly in BOTH branches, or it NameErrors. Either: (a) keep this helper
    # NESTED inside _build_default_app (where the import already exists) and test it
    # via factory construction, OR (b) make it module-level with explicit imports:
    from agent.backtest.replay_runner import _DeterministicSignalSource

    if os.environ.get("GENESIS_REAL_SIGNALS") == "1":
        from agent.backtest.real_signal_source import RealSignalSource
        from agent.backtest.tennis_match_resolver import TennisMatchResolver
        from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

        # (A0 correction) full re-vendored corpus dir -> offline + ~65.8% resolve;
        # a bare SackmannLoader() would read the synthetic-fixture snapshot dir.
        loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
        return RealSignalSource(
            provider=provider,
            resolver=TennisMatchResolver.from_sackmann_loader(loader, year_range=(2024, 2026)),
            loader=loader,
        )
    return _DeterministicSignalSource(seed=0)
```

and use it in the `_ReplayTickInputSource(signal_source=_make_prod_signal_source(_provider), ...)` construction. **Test path (codex fix): if you keep `_make_prod_signal_source` module-level (option b), the explicit `_DeterministicSignalSource` import above makes the helper directly unit-testable; if nested (option a), test through `_build_default_app`/factory construction instead.**

- [ ] **Step 4: Run to verify pass + `python -m agent.main --help` smoke (exit 0)**

- [ ] **Step 5: Gates + commit**

```bash
git add agent/server/main.py tests/agent/server/
git commit -m "feat(server): GENESIS_REAL_SIGNALS flag wires RealSignalSource into mock-bet loop

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task D2: Point `find_optimal_config` at the real source + add capped-notional per-bet score (optional but recommended)

**Files:**
- Modify: `agent/backtest/find_optimal_config.py` (build a `signal_source_factory` and pass it through `ReplayConfig`/`run_replay_sync`); optionally add a `sharpe_per_bet` field to `ReplayMetrics` in `replay_runner.py` (do NOT change the existing `sharpe`).
- Test: `tests/agent/backtest/test_find_optimal_config.py`

- [ ] **Step 1: Write the failing test.** (codex fix) `find_optimal_config.main(...)` returns an `int` exit code (`find_optimal_config.py:97,165`), not the ranked metrics — so the test cannot assert on Sharpe via the return value. Extract a pure helper `run_sweep(configs, *, cache_dir, signal_source_factory, ...) -> list[(StrategyConfig, ReplayMetrics)]` (which `main` then formats), and assert on THAT: with `--real` factory over a tmp cache of 2–3 resolvable cassettes, the helper returns N scored configs each with a finite `sharpe`. (Or capture stdout from `main` and assert the OPTIMAL block prints — but the extracted-helper path is cleaner + keeps `main`'s exit-code contract.)

- [ ] **Step 2–4:** Implement the `run_sweep` helper + a `--real` flag on `main` that builds the `RealSignalSource` factory and threads it into the per-config `run_replay_sync` call; run; verify.

- [ ] **Step 5: Gates + commit.**

### Task D3: Run the real sweep and write the result up

- [ ] **Step 1:** Build/refresh the resolver coverage number: `python -m agent.backtest.tennis_match_resolver agent/backtest/_cache_tennis` — record `resolved N/7494`.
- [ ] **Step 2:** Run the real sweep (background): `python -m agent.backtest.find_optimal_config --cache-dir agent/backtest/_cache_tennis --n 96 --seed 0 --real`. Capture the Sharpe-ranked table + the OPTIMAL config.
- [ ] **Step 3:** Write `reports/backtest/real_signal_sweep.md`: the optimal config (α₁..ρ + sizing), the Top-10 table, the **resolver coverage %**, and the honest caveats (Sackmann facets cover mostly 2024–2025; `sentiment_llm` slot semantics; PnL/Sharpe still uses compounded equity — bet-sizing winner is provisional pending family-③ economy re-validation in `sim/`).
- [ ] **Step 4:** Commit the report (NOT the cassettes).

---

## Self-Review

**Spec coverage:**
- ① momentum real → Task B1/B2 ✅
- ② 4 Sackmann facets (elo/surface/h2h/rest) → Task C1/C2 ✅
- Slot repurpose + neutral fallback → B2/C2 ✅
- Both seams wired (backtest B3, prod loop D1) ✅
- Flag/fallback preserves synthetic path → B3 (`signal_source_factory=None`), D1 (`GENESIS_REAL_SIGNALS`) ✅
- Coverage honesty → A3 report + D3 writeup ✅
- Sweep on real source → D2/D3 ✅
- Scoring caveat (compounded Sharpe) → D2 optional `sharpe_per_bet` + D3 caveat ✅

**Placeholder scan:** Tasks C1/C2/D1/D2 use prose "(Repeat one test each…)"/"Step 2–4" shorthands for the repetitive facet tests and the standard RED→GREEN→commit cycle — the normalization table + signatures above give the exact code; an executor expands them mechanically. All NEW types (`ParsedSlug`, `ResolvedMatch`, `TennisMatchResolver`, `RealSignalSource`, `momentum_signal`, `elo_signal`…) are fully defined in earlier tasks.

**Type consistency:** seam signature `signals_for(*, market_id: str, tick: int, asof_ts: datetime) -> dict[str, Signal]` is identical across `_DeterministicSignalSource`, the `_SignalSource` Protocol (B3), and `RealSignalSource`. Slot keys use the `decision.py` constants. Sackmann IDs passed as `str` to `compute_*`.

**Known risk (downgraded to MEDIUM after empirical check 2026-06-08):** Sackmann coverage is solid once the full corpus is vendored (Task A0) — measured **64.7% overall / 72% of 2026** cassettes resolve both players. The residual gap is (a) Sackmann's ~3-week publication lag (most-recent matches absent → hurts the β₂ rest facet specifically), and (b) the 2025 slug-format quirk (~30% parse). Both degrade gracefully (momentum stays real; the fusion layer down-weights weak facets). Decision point during D3: report the real coverage %, and optionally restrict the *multi-factor* sweep to the resolved subset while keeping a momentum-only pass over the full universe for comparison. No longer a blocking risk.

---

## Revision log

- **Round 1** (codex review, combined VERDICT `HIGH=5 MEDIUM=1 LOW=2` across both plans; all findings vetted against real code and accepted). Plan 1 fixes:
  - **HIGH-4** tiny Sackmann fixture would trigger live GitHub fallback (loader reads both tours × every year in range): added header-only empty WTA + 2024 companion CSVs and pinned tiny-loader tests to `year_range=(2025, 2025)` (`tennis_sackmann.py:166,241`).
  - **HIGH-5** `RealSignalSource` constructor drift (B2/B3 vs C2): declared `loader` + `year_range` in B2 with defaults so the `(provider, resolver)` callers keep working; C2 only consumes them.
  - **LOW-1** narrowed the momentum "faithful mirror" claim to score/confidence parity (real engine also emits `n_snapshots`/`depth_imbalance`/`latest_mid`).
- **Round 2** (codex, combined `HIGH=5 MEDIUM=2`; Plan 1 share, all accepted): **HIGH** C1 — `compute_elo_diff` takes no `year_range` (only surface/h2h/rest do); primitives live in `tennis_technical.py`. **MED** B2 — added `from dataclasses import dataclass, field` + `from data.sources.tennis_sackmann import SackmannLoader`. **MED** A0 — vendor script switched from undeclared `requests` to stdlib `urllib`.
- **Round 3** (codex, combined `HIGH=4 MEDIUM=2 LOW=2`; Plan 1 share): **HIGH-1 REBUTTED** — codex read the stale `historical_fetcher` synthetic-ledger docstring; our `_cache_tennis` cassettes carry REAL CLOB ledgers from `tennis_fetcher.py` (added a clarifying bullet). **HIGH-2** accepted — flagged the mean-confidence-floor effect (unresolved markets mostly NO_BET) + required an explicit DecisionEngine test. **LOW-1** accepted — D2 test extracts a pure `run_sweep` helper (`main` returns an `int` exit code, not metrics).
- **Round 4** (codex, combined `HIGH=1 MEDIUM=2 LOW=0`; Plan 1 share): **HIGH-1** declare `pandas`/`numpy`/`requests` in `pyproject [project].dependencies` (pre-existing gap; only in mypy override at `:108`) — added as Task A0 step 0 so flag-on real paths work on a clean install.
- **Round 5** (codex, combined `HIGH=2 MEDIUM=1`; Plan 1 share): **MED** D1 `_make_prod_signal_source` — `_DeterministicSignalSource` is imported only inside `_build_default_app`; specified explicit module-level import (or nested + factory-tested) to avoid NameError.
- **Post-A0 implementation correction** (controller, at the Group-A checkpoint): A0's implementer correctly diverged from the plan's literal "vendor into `sackmann_snapshot/`" — overwriting that dir clobbers the synthetic hermetic fixtures (`test_tennis_etl.py` 22→2 passed) — and instead created `sackmann_corpus/` exposed as `DEFAULT_CORPUS_DIR`. This makes a bare `SackmannLoader()` (snapshot default) the WRONG source for the real path (misses 2026 → silent GitHub fetch → mixed 53.7%). Patched B2's `RealSignalSource.loader` default and D1's prod loader to `SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)`, and added the "Read first" dir-split note. The already-committed A3 coverage CLI was fixed the same way (commit `cf24df6`): true offline coverage = **4931/7494 = 65.8%** (was a mixed-online 53.7%).
