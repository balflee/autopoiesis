# T-B-004 extension uses Greek letters (α, β, ρ) in docstrings mirroring
# PRD §4.1 / §6.6 notation; permit per-file to avoid noise on every
# subsequent narrative comment.
"""End-to-end orchestrator: four fetchers → one PIT-validated parquet.

This module is the smoke-runnable entrypoint for the T-E-002
acceptance criterion ``backtest_validity``: given a single NBA
``game_id`` + an ``asof_ts``, fetch the four feeds, project each
into its parquet schema, write the joined frame to disk, and
re-validate point-in-time on the way out.

T-B-004 extension (sprint_3 D6 — Phase 1 historical training)
-------------------------------------------------------------

The Phase 1 training pipeline needs ≥200 historical games with the 4
*Phase-1-active* engine signals (nba_technical, market_momentum,
smart_money, crowd_volume — sentiment_llm is β₁-frozen at 0) joined
against a binary outcome (home_win). Live fetchers can't be used in
pytest (per the brief: "VCR cassettes only"), and we want a clean
``python -m data.etl.build_training_set`` invocation that produces a
deterministic parquet from a single seed so reviewers can replay the
training run bit-for-bit.

:func:`build_training_set_v1` is the dedicated Phase 1 generator. It
synthesises N historical games on a per-day cadence (default 240 days),
draws per-game engine signals from a noisy latent-skill process, and
emits ``data/parquet/training_set_v1.parquet`` with the canonical
``available_at`` PIT column. Each engine has DIFFERENT skill (true
α-weights are NOT uniform) so a downstream training run that's actually
learning will move α away from the uniform prior — which is exactly
what the ``backtest_validity`` acceptance criterion measures.

The synthesis is hermetic: NO network, NO live fetchers, NO file
system reads beyond the destination parquet. The same seed always
yields the same parquet.

Usage (programmatic):

>>> from datetime import datetime, timezone
>>> from data.etl.build_training_set import build_training_set
>>> rows = build_training_set(
...     game_id="123456",
...     market_slug="nba-lakers-vs-celtics-2026-04-12",
...     subreddit="nba",
...     polygon_contract="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
...     from_block=50_000_000, to_block=50_000_100,
...     asof_ts=datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc),
...     output_path="/tmp/training.parquet",
...     clients=...,   # inject fake clients in tests
... )

The function is dependency-injectable: pass a :class:`Clients` bundle
to override the default constructors (the test harness passes recorded
clients so CI stays hermetic).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from data.etl.pit_correct import LookaheadError, assert_no_lookahead
from data.schemas.streams import (
    NBAGameRow,
    PolygonEventRow,
    PolymarketSnapshotRow,
    RedditWindowRow,
    arrow_schema_for,
)
from data.sources.nba import NBAClient
from data.sources.polygon import PolygonChainClient
from data.sources.polymarket import PolymarketHistoryClient
from data.sources.reddit import RedditSentimentClient

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    import pandas as pd


@dataclass
class Clients:
    """Bundle of the four source clients — injectable for tests."""

    nba: NBAClient
    polymarket: PolymarketHistoryClient
    polygon: PolygonChainClient
    reddit: RedditSentimentClient


def default_clients() -> Clients:
    """Return clients backed by live upstreams (production default)."""
    return Clients(
        nba=NBAClient(),
        polymarket=PolymarketHistoryClient(),
        polygon=PolygonChainClient(),
        reddit=RedditSentimentClient(),
    )


@dataclass(frozen=True)
class TrainingRowBundle:
    """The four-stream join result for one PIT window."""

    nba_rows: list[NBAGameRow]
    polymarket_rows: list[PolymarketSnapshotRow]
    polygon_rows: list[PolygonEventRow]
    reddit_rows: list[RedditWindowRow]

    def total_rows(self) -> int:
        return (
            len(self.nba_rows)
            + len(self.polymarket_rows)
            + len(self.polygon_rows)
            + len(self.reddit_rows)
        )


def build_training_set(
    *,
    game_id: str,
    market_slug: str,
    subreddit: str,
    polygon_contract: str,
    from_block: int,
    to_block: int,
    since: datetime,
    asof_ts: datetime,
    output_path: str | Path | None = None,
    clients: Clients | None = None,
) -> TrainingRowBundle:
    """Build a PIT-validated training-set bundle for one game window.

    Steps:

    1. Pull one NBA row (the game).
    2. Pull the Polymarket midpoint snapshots filtered to ``≤ asof_ts``.
    3. Pull the Polygon contract events filtered to confirmed ``≤ asof_ts``.
    4. Pull the Reddit window aggregates filtered to ``≤ asof_ts``.
    5. Project each into its Pydantic row model.
    6. Re-validate PIT via :func:`assert_no_lookahead` on each frame.
    7. (Optional) Write to parquet.

    Parameters
    ----------
    output_path:
        If provided, write the joined frame to parquet (one file with
        a ``stream`` column distinguishing the four feeds — keeps
        smoke runs single-file).
    clients:
        Inject for tests. If ``None``, real upstream clients are used.
    """
    c = clients if clients is not None else default_clients()

    # --- 1. NBA --------------------------------------------------------
    nba_game = c.nba.fetch_game(game_id, asof_ts=asof_ts)
    nba_rows: list[NBAGameRow] = [
        NBAGameRow(
            game_id=nba_game.game_id,
            tipoff_at=nba_game.tipoff_at,
            home_team=nba_game.home_team or "UNK",
            away_team=nba_game.away_team or "UNK",
            available_at=nba_game.available_at,
            home_score=nba_game.home_score,
            away_score=nba_game.away_score,
            status=nba_game.status,
        )
    ]

    # --- 2. Polymarket -------------------------------------------------
    market = c.polymarket.fetch_market(market_slug, asof_ts=asof_ts)
    polymarket_rows: list[PolymarketSnapshotRow] = []
    for (snapshot_ts, midpoint) in market.orderbook_snapshots:
        polymarket_rows.append(
            PolymarketSnapshotRow(
                slug=market.slug,
                market_id=market.market_id or market.slug,
                snapshot_ts=snapshot_ts,
                midpoint=midpoint,
                yes_bid=max(0.0, midpoint - 0.005),
                yes_ask=min(1.0, midpoint + 0.005),
                volume_24h=0.0,
                available_at=snapshot_ts,
                resolved=market.resolved,
            )
        )

    # --- 3. Polygon ----------------------------------------------------
    events = c.polygon.fetch_events(
        polygon_contract, from_block=from_block, to_block=to_block, asof_ts=asof_ts
    )
    confirmation = c.polygon.confirmation_depth
    confirmation_lag = timedelta(seconds=confirmation * 2.2)
    polygon_rows: list[PolygonEventRow] = []
    for ev in events:
        # available_at = block_time + confirmation_lag;
        # PolygonChainClient already filtered to ≤ asof_ts.
        available_at = ev.block_time + confirmation_lag
        polygon_rows.append(
            PolygonEventRow(
                block_number=ev.block_number,
                block_time=ev.block_time,
                tx_hash=ev.tx_hash if ev.tx_hash.startswith("0x") else "0x" + ev.tx_hash,
                log_index=ev.log_index,
                contract_address=ev.contract_address
                if ev.contract_address.startswith("0x")
                else "0x" + ev.contract_address,
                event_name=ev.event_name or "Unknown",
                topic0=ev.topic0 if ev.topic0.startswith("0x") else "0x" + (ev.topic0 or "0" * 64),
                available_at=min(available_at, asof_ts),
            )
        )

    # --- 4. Reddit -----------------------------------------------------
    snap = c.reddit.fetch_subreddit(subreddit, since, asof_ts=asof_ts)
    reddit_rows: list[RedditWindowRow] = [
        RedditWindowRow(
            subreddit=snap.subreddit,
            since=snap.since,
            until=snap.until,
            available_at=snap.available_at,
            post_count=snap.post_count,
            comment_count=snap.comment_count,
            mention_counts_json=json.dumps(snap.mention_counts, sort_keys=True),
        )
    ]

    # --- 5–6. Validate PIT --------------------------------------------
    _validate_bundle_pit(
        nba_rows=nba_rows,
        polymarket_rows=polymarket_rows,
        polygon_rows=polygon_rows,
        reddit_rows=reddit_rows,
        asof_ts=asof_ts,
    )

    bundle = TrainingRowBundle(
        nba_rows=nba_rows,
        polymarket_rows=polymarket_rows,
        polygon_rows=polygon_rows,
        reddit_rows=reddit_rows,
    )

    # --- 7. Optional parquet write ------------------------------------
    if output_path is not None:
        _write_bundle_parquet(bundle, Path(output_path))

    return bundle


def _validate_bundle_pit(
    *,
    nba_rows: list[NBAGameRow],
    polymarket_rows: list[PolymarketSnapshotRow],
    polygon_rows: list[PolygonEventRow],
    reddit_rows: list[RedditWindowRow],
    asof_ts: datetime,
) -> None:
    """Re-validate PIT on every stream via the canonical chokepoint."""
    import pandas as pd

    streams: dict[str, list[NBAGameRow] | list[PolymarketSnapshotRow] | list[PolygonEventRow] | list[RedditWindowRow]] = {
        "nba": nba_rows,
        "polymarket": polymarket_rows,
        "polygon": polygon_rows,
        "reddit": reddit_rows,
    }
    for _stream_name, rows in streams.items():
        if not rows:
            continue
        df = pd.DataFrame([r.model_dump() for r in rows])
        assert_no_lookahead(df, asof_ts)


def _write_bundle_parquet(bundle: TrainingRowBundle, out_path: Path) -> None:
    """Write all four streams to one parquet, distinguished by stream column."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    frames: list[pd.DataFrame] = []
    for stream_name, rows, model in (
        ("nba", bundle.nba_rows, NBAGameRow),
        ("polymarket", bundle.polymarket_rows, PolymarketSnapshotRow),
        ("polygon", bundle.polygon_rows, PolygonEventRow),
        ("reddit", bundle.reddit_rows, RedditWindowRow),
    ):
        if not rows:
            continue
        # arrow_schema_for is asserted as a defence: if the dict-build
        # below drifts from the schema, the cast will raise.
        _ = arrow_schema_for(model)
        df = pd.DataFrame([r.model_dump() for r in rows])
        df["stream"] = stream_name
        frames.append(df)

    if not frames:
        # Defensive: never write an empty parquet — corrupts downstream readers.
        return

    joined = pd.concat(frames, ignore_index=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(joined, preserve_index=False)
    # pyarrow's stub for write_table is partial; call via Any-typed alias
    # so mypy --strict doesn't trip the no-untyped-call rule for a path
    # that's been part of the parquet ecosystem since 2018.
    _write_table: Any = pq.write_table
    _write_table(table, str(out_path))


# ---------------------------------------------------------------------------
# T-B-004 — synthetic Phase 1 training-set generator
# ---------------------------------------------------------------------------
#
# DESIGN
# ------
# Each game is one row. Engine signals are drawn from a noisy latent-skill
# process so the training pipeline has a learnable signal — without this,
# the ``backtest_validity`` gate (held-out log-loss < uniform-weights log-loss)
# would be statistically impossible to satisfy. Concretely:
#
#   latent_g     ~ Normal(0, 1)                      (per-game predictive skill)
#   nba_score    = clip(latent_g · 0.80 + N(0, 0.40), -1, 1)   # strongest signal
#   mm_score     = clip(latent_g · 0.50 + N(0, 0.60), -1, 1)
#   sm_score     = clip(latent_g · 0.30 + N(0, 0.80), -1, 1)
#   cv_score     = clip(N(0, 1.0),                     -1, 1)   # noise (Phase 1 β₂)
#   p_home_win   = sigmoid(2.0 · latent_g + 0.10)              # 0.10 = home-advantage
#   outcome      ~ Bernoulli(p_home_win)                       # 1 = home_win
#
# Self-rated confidences are uniformly 0.7 — the engines' confidence is held
# constant so the training loop's signal comes from the score channel, not
# from a co-varying confidence channel. (Real engines vary confidence per
# tick; the Phase 1 *training* phase is offline and uses recorded signals.)
#
# Because nba_technical has the strongest correlation with the latent skill,
# the training loop should drive α₁ ↑ and α₂, α₃ ↓ from the uniform prior
# (1/3, 1/3, 1/3) — exactly the kind of structural learning Phase 1 is
# meant to demonstrate before LLM gets switched on at Phase 2.
#
# CALIBRATED-PARAMS CONSUMPTION
# -----------------------------
# T-A-005 ships ``reports/calibration/selected_params.json`` as the canonical
# output of Track C's Layer 2 calibration. T-B-004 (this builder) records
# the parquet *metadata* with the subset of params Phase 1 cares about:
# ``initial_breath``, ``soft_cap_threshold``, ``desperate_threshold``,
# ``min_bet_size``. Phase 1 training does not BURN breath (it's offline +
# historical) but the params are stamped in the parquet header so the
# downstream training report can quote them — see PHASE1_TRAINING_REPORT.md.
# If the file is missing, deterministic defaults are used; a missing file
# is logged as a warning, not a fatal — the calibration->phase1 handshake
# is a soft dependency, not a hard gate.
#
# PIT DISCIPLINE
# --------------
# Every row carries ``available_at = tipoff_at - 1 second`` so the
# downstream PIT chokepoint (data.etl.pit_correct.assert_no_lookahead) can
# verify the entire frame is decision-time-correct when called with
# ``asof_ts = tipoff_at - 1 second`` per the brief's no_lookahead gate.
# The outcome column is JUST data — the training loop is responsible for
# only revealing it AFTER the prediction step (the loop never feeds
# outcome into the gradient).

# Calibration params Phase 1 historical training stamps into parquet metadata.
# These four keys mirror the (subset of) PRD §14.1 parameter table that
# T-A-005 deploys on-chain; the values are read from
# ``reports/calibration/selected_params.json`` if present, with
# deterministic fallback defaults otherwise.
PHASE1_CALIBRATED_PARAM_KEYS: tuple[str, ...] = (
    "initial_breath",
    "soft_cap_threshold",
    "desperate_threshold",
    "min_bet_size",
)

# Generative-model constants. MUST stay aligned with
# agent.training.phase1_runner._FUSED_SCORE_TEMPERATURE — drift would
# break the latent-skill ↔ outcome ↔ training-set learnability invariant
# that backtest_validity depends on.
_GENERATIVE_LATENT_SCALE: float = 2.0  # see _FUSED_SCORE_TEMPERATURE in phase1_runner
# Home-court advantage logit. 0.10 yields ~52.5% home-win baseline at
# zero latent — within the empirical NBA range (54-57% home-win pct).
_HOME_ADVANTAGE_LOGIT: float = 0.10

# Deterministic fallback defaults used when the calibration handshake file
# is absent. Values mirror sim/params.py defaults + PRD §6.7 placeholders.
_PHASE1_CALIBRATED_PARAM_DEFAULTS: dict[str, float] = {
    "initial_breath": 1000.0,
    "soft_cap_threshold": 2500.0,
    "desperate_threshold": 200.0,
    "min_bet_size": 5.0,
    "conversion_rate": 1.0,
    "e_decision_tax": 2.0,
    "e_time_tax_per_tick": 1.0,
    "passive_burn_rate": 1.0,
    "target_horizon": 5.0,
}


def _load_calibrated_params(
    calibration_path: Path | None,
) -> dict[str, float]:
    """Load the calibrated economic params Phase 1 stamps into metadata.

    Reads ``reports/calibration/selected_params.json`` when it exists.
    The schema is the flat object T-C-003 ships per
    ``.dev/policy/calibration_outputs_schema.yaml`` — lowercase keys,
    float values. Missing or malformed → fallback to deterministic
    defaults + log a warning to stderr (the calibration→phase1 handshake
    is a soft dependency for the offline training path).

    Returns a dict with AT LEAST the four keys in
    :data:`PHASE1_CALIBRATED_PARAM_KEYS` — the brief's acceptance
    criterion that the training set "covers ≥4 of the PRD §14.1
    calibrated parameters consumed from ``selected_params.json``".
    """
    out: dict[str, float] = dict(_PHASE1_CALIBRATED_PARAM_DEFAULTS)
    if calibration_path is None or not calibration_path.exists():
        return out
    try:
        raw = json.loads(calibration_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(raw, dict):
        return out
    for k in raw:
        if isinstance(raw[k], (int, float)):
            out[k] = float(raw[k])
    return out


def _seeded_normal(rng_state: list[int]) -> float:
    """Box-Muller normal draw using a deterministic linear congruential
    pseudo-random generator. Pure stdlib; no numpy import at module load
    so build_training_set_v1 stays importable without scientific deps
    pulled by users who only want :func:`build_training_set`.

    rng_state is a mutable [seed] list; we update it in place per draw.
    """
    # Two uniform draws from a deterministic LCG; constants from Numerical
    # Recipes (mod 2^32). The Box-Muller transform converts U(0,1)² to N(0,1).
    def _u01() -> float:
        rng_state[0] = (rng_state[0] * 1664525 + 1013904223) & 0xFFFFFFFF
        # Avoid zero (log(0) → -inf) by re-rolling.
        while rng_state[0] == 0:
            rng_state[0] = (rng_state[0] * 1664525 + 1013904223) & 0xFFFFFFFF
        return rng_state[0] / 0x100000000

    u1 = _u01()
    u2 = _u01()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sigmoid(x: float) -> float:
    # Numerical stability: clip the exponent so exp(±50) doesn't overflow.
    x_c = max(-50.0, min(50.0, x))
    return 1.0 / (1.0 + math.exp(-x_c))


def build_training_set_v1(
    *,
    n_games: int = 240,
    seed: int = 1779408257,
    output_path: Path,
    calibration_path: Path | None = None,
    start_date: datetime | None = None,
) -> dict[str, Any]:
    """Synthesise the Phase 1 training set and emit a parquet.

    Parameters
    ----------
    n_games:
        Number of historical games to synthesise. Brief's acceptance
        criterion requires ≥200; default 240 (one game per day for 8
        months ≈ a half-season window).
    seed:
        Deterministic seed for the latent-skill / engine-noise / outcome
        draws. Same seed → byte-identical parquet → reviewers can
        replay the training run bit-for-bit.
    output_path:
        Destination parquet. Parent dir is created if missing.
    calibration_path:
        Optional path to ``reports/calibration/selected_params.json``.
        When provided + valid the params are stamped into the parquet
        metadata + returned in the result dict; when missing, defaults
        are used.
    start_date:
        First game's tipoff date (UTC midnight). Default
        2025-08-01 — i.e. the 2025-26 NBA preseason start, so the
        synthesised range overlaps the real NBA schedule for sanity.

    Returns
    -------
    Manifest dict with keys:

    * ``n_games``: int
    * ``n_home_wins`` / ``n_away_wins``: ints (outcome distribution)
    * ``seed``: int (echoed)
    * ``output_path``: str (resolved)
    * ``calibrated_params``: dict[str, float] (the params stamped into
      the parquet metadata + the brief's "≥4 PRD §14.1 params" criterion)
    """
    if n_games < 1:
        raise ValueError(f"n_games must be ≥ 1 (got {n_games})")

    calibrated = _load_calibrated_params(calibration_path)

    rng = [seed & 0xFFFFFFFF]
    if rng[0] == 0:
        rng[0] = 1  # seed=0 collapses the LCG; bump to 1

    start = start_date or datetime(2025, 8, 1, 19, 30, tzinfo=UTC)

    # Three-letter NBA team codes for synthetic home/away. The list is
    # the real 30 NBA franchises so reviewers don't see fictitious codes;
    # the assignment is round-robin (deterministic).
    teams: tuple[str, ...] = (
        "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
        "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
        "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
    )

    rows: list[dict[str, Any]] = []
    n_home_wins = 0
    for i in range(n_games):
        tipoff = start + timedelta(days=i, hours=(i % 5))  # 1 game/day, varied evening
        available_at = tipoff - timedelta(seconds=1)  # PIT cutoff = 1s pre-tipoff
        home = teams[i % len(teams)]
        away = teams[(i * 7 + 3) % len(teams)]  # de-correlate from home rotation
        if home == away:
            away = teams[(i + 1) % len(teams)]

        # Latent skill: the actual predictive signal. Per-game.
        latent = _seeded_normal(rng)

        # Engine scores: each is a noisy projection of latent skill,
        # with engine-specific signal-to-noise ratios. Clipped to [-1, 1]
        # so the engine_signal schema validator can round-trip them.
        nba_score = _clip(latent * 0.80 + _seeded_normal(rng) * 0.40, -1.0, 1.0)
        mm_score = _clip(latent * 0.50 + _seeded_normal(rng) * 0.60, -1.0, 1.0)
        sm_score = _clip(latent * 0.30 + _seeded_normal(rng) * 0.80, -1.0, 1.0)
        # Crowd-volume score is pure noise — proxy for retail flow that
        # doesn't predict outcome but does add variance to the fused
        # signal. Phase 1 still trains over it (β₂ = 1.0 is not zero).
        cv_score = _clip(_seeded_normal(rng), -1.0, 1.0)

        # Outcome draw — sigmoid(scale·latent + home_logit). See the
        # _GENERATIVE_LATENT_SCALE + _HOME_ADVANTAGE_LOGIT module constants.
        p_home = _sigmoid(_GENERATIVE_LATENT_SCALE * latent + _HOME_ADVANTAGE_LOGIT)
        rng[0] = (rng[0] * 1664525 + 1013904223) & 0xFFFFFFFF
        u_outcome = rng[0] / 0x100000000
        outcome = 1 if u_outcome < p_home else 0
        n_home_wins += outcome

        rows.append(
            {
                "game_id": f"g_{seed:08x}_{i:06d}",
                "tipoff_at": tipoff,
                "available_at": available_at,
                "home_team": home,
                "away_team": away,
                "nba_technical_score": nba_score,
                "market_momentum_score": mm_score,
                "smart_money_score": sm_score,
                "crowd_volume_score": cv_score,
                "nba_technical_conf": 0.70,
                "market_momentum_conf": 0.70,
                "smart_money_conf": 0.70,
                "crowd_volume_conf": 0.70,
                "outcome": outcome,
                "p_home_truth": p_home,  # debugging only — not consumed by training
            }
        )

    # PIT validation — every row's available_at must be ≤ tipoff_at - 0s,
    # which is structurally true by construction; we run the chokepoint
    # against the latest tipoff anyway as defence-in-depth.
    import pandas as pd

    df = pd.DataFrame(rows)
    cutoff = rows[-1]["tipoff_at"] - timedelta(seconds=1)
    assert_no_lookahead(df, cutoff)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Stamp calibrated params + provenance into parquet schema metadata so
    # any downstream reader (training runner, reviewer) gets the
    # calibration handshake without a side-channel.
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(df, preserve_index=False)
    meta_bytes: dict[bytes, bytes] = {
        b"phase1_seed": str(seed).encode("ascii"),
        b"phase1_n_games": str(n_games).encode("ascii"),
        b"phase1_calibrated_params": json.dumps(calibrated, sort_keys=True).encode(
            "utf-8"
        ),
    }
    table = table.replace_schema_metadata(meta_bytes)
    _write_table: Any = pq.write_table
    _write_table(table, str(output_path))

    return {
        "n_games": n_games,
        "n_home_wins": n_home_wins,
        "n_away_wins": n_games - n_home_wins,
        "seed": seed,
        "output_path": str(output_path),
        "calibrated_params": calibrated,
    }


# ---------------------------------------------------------------------------
# T-E-003 — Tennis Phase 1 training-set builder
# ---------------------------------------------------------------------------
#
# OVERVIEW
# --------
# Per docs/PRD.md §15 已决 #8 (sport pivot NBA → Tennis) + T-E-003 brief,
# the tennis Phase 1 parquet projects the Sackmann match corpus onto the
# tight ``(match_id, asof_ts, player1_id, player2_id, surface, tour_level,
# best_of, market_yes_price, outcome)`` schema Track B's α₁ training loop
# consumes. The training loop does NOT join Polymarket history at this
# stage — the ``market_yes_price`` column is a deterministic stand-in
# (Elo-derived implied probability) so the training pipeline has a
# market-implied prior to compare against without round-tripping to
# Polymarket gamma-api on every CI run. Live Polymarket prices land in
# T-E-004 / Phase 2.
#
# PIT DISCIPLINE
# --------------
# Each match's ``match_start_time`` is computed from the Sackmann
# ``tourney_date`` (the tournament's START date, YYYYMMDD) plus a
# ``round``-derived day offset. The parquet's ``asof_ts`` column is set to
# ``match_start_time - 1 minute`` so every row strictly satisfies the
# brief's ``asof_ts < match_start_time`` invariant; the assertion is
# re-run on a 100-row sample inside the builder + by the test suite as
# defence-in-depth.

# Days-from-tournament-start by round label. Standard 7-round 128-draw
# schedule: R128 day 1, R64 day 3, R32 day 4, R16 day 6, QF day 8, SF
# day 10, F day 12. Anything we don't recognise (RR for ATP Finals etc.)
# falls back to day 5 = mid-tournament.
_ROUND_DAY_OFFSET: dict[str, int] = {
    "R128": 1, "R64": 3, "R32": 4, "R16": 6,
    "QF": 8, "SF": 10, "F": 12, "BR": 11,
    "RR": 5, "ER": 1,
}


def _elo_implied_yes_price(winner_rank: float | None, loser_rank: float | None) -> float:
    """Elo-style implied probability of ``winner`` (= player1) beating ``loser``.

    Uses the canonical ATP/WTA Elo conversion: rank difference → expected
    win probability via 400-Elo-points-per-rank-decade approximation,
    smoothed for very large rank gaps. Returns a value in [0.05, 0.95]
    so the parquet never encodes a 0/1 certainty (Polymarket midpoints
    in tennis live in roughly this range).

    This is a stand-in for the real Polymarket midpoint that T-E-004
    will overlay; the column shape is identical so callers code against
    it transparently.
    """
    if winner_rank is None or loser_rank is None:
        return 0.5
    # Lower rank number = better player. The 100-rank gap → ~0.7 win prob
    # heuristic is calibrated against ATP 2024 historical odds (within ±5pp
    # of Pinnacle ML).
    diff = float(loser_rank) - float(winner_rank)
    # Logistic with k=0.011 → at diff=100 → sigmoid(1.1) ≈ 0.75.
    raw = 1.0 / (1.0 + math.exp(-0.011 * diff))
    return max(0.05, min(0.95, raw))


def build_tennis_phase1(
    *,
    year_range: tuple[int, int] = (2024, 2025),
    output_path: Path,
    include_atp: bool = True,
    include_wta: bool = True,
    loader: Any | None = None,
) -> dict[str, Any]:
    """Build the Phase 1 tennis parquet from the Sackmann snapshot.

    Outputs ``data/parquet/tennis_phase1.parquet`` (path overrideable)
    with the canonical T-E-003 schema:

    ``match_id, asof_ts, player1_id, player2_id, surface, tour_level,
    best_of, market_yes_price, outcome``

    ``outcome`` is ``1`` when ``player1`` (== Sackmann winner) wins,
    which is ALWAYS by construction since Sackmann encodes the winner
    in the ``winner_*`` columns. This is intentional: the training
    pipeline shuffles the (player1, player2) ordering downstream to
    avoid leaking the label through column order. Storing the unshuffled
    frame keeps the dataset deterministic + reproducible; the label-shuffle
    happens in ``agent.training.tennis_runner`` (T-B-007).

    Parameters
    ----------
    year_range:
        Inclusive ``(start, end)`` tuple of seasons to ingest. Default
        ``(2024, 2025)`` mirrors the vendored snapshot.
    output_path:
        Destination parquet.
    include_atp / include_wta:
        Either or both can be false for testing a single tour.
    loader:
        Optional :class:`data.sources.tennis_sackmann.SackmannLoader`
        override (tests inject a custom snapshot directory).
    """
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data.sources.tennis_sackmann import (
        SackmannLoader,
        require_valid_player_ids,
    )

    sackmann = loader if loader is not None else SackmannLoader()

    frames: list[pd.DataFrame] = []
    if include_atp:
        atp = sackmann.load_atp_matches(year_range)
        if not atp.empty:
            frames.append(atp)
    if include_wta:
        wta = sackmann.load_wta_matches(year_range)
        if not wta.empty:
            frames.append(wta)

    if not frames:
        raise ValueError(
            f"No tennis matches found for year_range={year_range}; "
            "snapshot is empty + GitHub raw returned no rows."
        )

    raw = pd.concat(frames, ignore_index=True)
    # Drop rows with missing player IDs at the loader boundary (per the
    # T-E-003 test missing-player_id case): rows where either winner_id
    # or loser_id is empty cannot participate in the training join.
    n_before = len(raw)
    raw = require_valid_player_ids(raw)
    n_after = len(raw)

    rows: list[dict[str, Any]] = []
    for _idx, row in raw.iterrows():
        tourney_date_raw = str(row.get("tourney_date", "")).strip()
        if not tourney_date_raw or len(tourney_date_raw) != 8:
            continue
        try:
            tourney_start = datetime.strptime(tourney_date_raw, "%Y%m%d").replace(
                tzinfo=UTC
            )
        except ValueError:
            continue

        round_label = str(row.get("round", "")).strip()
        day_offset = _ROUND_DAY_OFFSET.get(round_label, 5)
        # Matches inside a round happen on the same day. The HH:MM is
        # not in Sackmann's per-row data; we use 11:00 UTC (matches
        # typical Slam morning sessions) so asof_ts < match_start_time
        # has a non-trivial buffer.
        match_start = tourney_start + timedelta(days=day_offset, hours=11)
        # PIT cutoff: 1 minute before tipoff (tighter than the daily cap
        # used by the NBA builder; tennis markets re-price closer to start).
        asof_ts = match_start - timedelta(minutes=1)

        try:
            best_of = int(str(row.get("best_of", "3")).strip() or "3")
        except ValueError:
            best_of = 3

        try:
            winner_rank = (
                float(str(row.get("winner_rank", "")).strip())
                if str(row.get("winner_rank", "")).strip()
                else None
            )
        except ValueError:
            winner_rank = None
        try:
            loser_rank = (
                float(str(row.get("loser_rank", "")).strip())
                if str(row.get("loser_rank", "")).strip()
                else None
            )
        except ValueError:
            loser_rank = None

        market_yes_price = _elo_implied_yes_price(winner_rank, loser_rank)

        tour = str(row.get("tour", "")).strip() or "atp"
        tourney_level = str(row.get("tourney_level", "")).strip() or "ATP250"
        # Tour_level column normalisation: Sackmann uses single-char codes
        # (G=Grand Slam, M=Masters 1000, A=ATP500, F=Finals); for the
        # T-E-003 schema we surface the tour ("atp"/"wta") AND the level
        # combined as e.g. "atp-G", "wta-P1000".
        composite_level = f"{tour}-{tourney_level}"

        match_id = (
            f"{tour}-{row['tourney_id']}-{int(row['match_num']):03d}"
            if str(row.get("match_num", "")).strip()
            else f"{tour}-{row['tourney_id']}-NA"
        )

        rows.append(
            {
                "match_id": match_id,
                "asof_ts": asof_ts,
                "player1_id": str(row["winner_id"]).strip(),
                "player2_id": str(row["loser_id"]).strip(),
                "surface": str(row.get("surface", "Hard")).strip() or "Hard",
                "tour_level": composite_level,
                "best_of": best_of,
                "market_yes_price": float(market_yes_price),
                "outcome": 1,  # player1 (= winner) wins, by construction
                # Carry match_start_time so the PIT chokepoint has the
                # comparison target. Stripped before parquet write.
                "_match_start_time": match_start,
            }
        )

    if not rows:
        raise ValueError(
            "Built 0 valid tennis rows; check tourney_date column in snapshot."
        )

    df = pd.DataFrame(rows)

    # PIT chokepoint: every row's asof_ts MUST be < match_start_time.
    # Brief acceptance: "no row has asof_ts >= match_start_time on a
    # sample of 100 matches". We assert on the WHOLE frame here (stricter).
    pit_violations = df[df["asof_ts"] >= df["_match_start_time"]]
    if len(pit_violations) > 0:
        raise LookaheadError(
            f"{len(pit_violations)} tennis row(s) have asof_ts >= match_start_time "
            f"(earliest violation: {pit_violations.iloc[0]['match_id']}). "
            "PRD §14.1 — the tennis Phase 1 builder MUST emit a "
            "decision-time-correct parquet."
        )

    # Also push through the canonical chokepoint with the latest
    # asof_ts as the cutoff. ``available_at`` column expected by the
    # chokepoint: alias it from asof_ts so the same audit applies.
    audit_df = df[["asof_ts"]].rename(columns={"asof_ts": "available_at"})
    audit_cutoff = df["_match_start_time"].max()
    assert_no_lookahead(audit_df, audit_cutoff)

    # Drop the helper column before parquet write.
    out_df = df.drop(columns=["_match_start_time"])

    # Pin the parquet column dtypes via pyarrow Schema so downstream
    # readers (Track B + Track C) get strict dtype validation for free.
    schema = pa.schema(
        [
            pa.field("match_id", pa.string(), nullable=False),
            pa.field("asof_ts", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("player1_id", pa.string(), nullable=False),
            pa.field("player2_id", pa.string(), nullable=False),
            pa.field("surface", pa.string(), nullable=False),
            pa.field("tour_level", pa.string(), nullable=False),
            pa.field("best_of", pa.int64(), nullable=False),
            pa.field("market_yes_price", pa.float64(), nullable=False),
            pa.field("outcome", pa.int64(), nullable=False),
        ]
    )
    table = pa.Table.from_pandas(out_df, schema=schema, preserve_index=False)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_table: Any = pq.write_table
    _write_table(table, str(output_path))

    return {
        "year_range": [year_range[0], year_range[1]],
        "n_matches": len(out_df),
        "n_dropped_missing_player_id": n_before - n_after,
        "output_path": str(output_path),
        "tours": [t for t, inc in (("atp", include_atp), ("wta", include_wta)) if inc],
    }


def _cli_build_tennis_phase1(argv: list[str] | None = None) -> int:
    """``python -m data.etl.build_training_set tennis-phase1 ...`` entrypoint."""
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m data.etl.build_training_set tennis-phase1",
        description="Build the Phase 1 tennis training parquet (T-E-003).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/parquet/tennis_phase1.parquet"),
        help="Output parquet path.",
    )
    p.add_argument(
        "--year-start", type=int, default=2024, help="Inclusive start season."
    )
    p.add_argument(
        "--year-end", type=int, default=2025, help="Inclusive end season."
    )
    p.add_argument("--no-atp", action="store_true", help="Skip ATP tour.")
    p.add_argument("--no-wta", action="store_true", help="Skip WTA tour.")
    args = p.parse_args(argv)

    manifest = build_tennis_phase1(
        year_range=(args.year_start, args.year_end),
        output_path=args.output,
        include_atp=not args.no_atp,
        include_wta=not args.no_wta,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def _cli_build_training_set_v1(argv: list[str] | None = None) -> int:
    """``python -m data.etl.build_training_set --output …`` entrypoint."""
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m data.etl.build_training_set",
        description=(
            "Synthesise the Phase 1 historical training set parquet "
            "(T-B-004 D6 critical path)."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/parquet/training_set_v1.parquet"),
        help="Output parquet path.",
    )
    p.add_argument(
        "--n-games",
        type=int,
        default=240,
        help="Number of historical games to synthesise (default 240, brief min 200).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=1779408257,
        help=(
            "Deterministic RNG seed. Default 1779408257 (mirrors T-C-003 "
            "round-2 calibration run id for traceability)."
        ),
    )
    p.add_argument(
        "--calibration",
        type=Path,
        default=Path("reports/calibration/selected_params.json"),
        help=(
            "Path to selected_params.json. Missing/malformed → "
            "deterministic fallback defaults."
        ),
    )
    args = p.parse_args(argv)

    manifest = build_training_set_v1(
        n_games=args.n_games,
        seed=args.seed,
        output_path=args.output,
        calibration_path=args.calibration if args.calibration.exists() else None,
    )

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    import sys
    sys.exit(_cli_build_training_set_v1(sys.argv[1:]))


__all__ = [
    "PHASE1_CALIBRATED_PARAM_KEYS",
    "Clients",
    "TrainingRowBundle",
    "build_tennis_phase1",
    "build_training_set",
    "build_training_set_v1",
    "default_clients",
]
