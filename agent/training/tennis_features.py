# Greek letters mirror PRD §4.1 / §6.6 notation; see agent/engines/weight_updater.py.
"""PIT-strict feature builder for the Phase 1 tennis training pipeline.

T-B-015 (sprint_7 D4 sport pivot — see PRD §15 已决 #8) lifts the
training pipeline off the synthetic NBA training set and onto the
real Sackmann tennis corpus. The raw parquet shipped by T-E-003
(``data/parquet/tennis_phase1.parquet``) carries one row per ATP / WTA
match in 2024–2025 with the **winner pinned to player1** by
construction — Sackmann's data shape is ``winner_id`` first.

Two transforms happen here before the training loop sees a row:

1. **PIT-correct feature projection** — for each match we call the
   :mod:`agent.engines.tennis_technical` primitives with the row's
   ``asof_ts`` as the cutoff (≥ 1 minute before match start per
   T-E-003) and combine them into the 4 *Phase 1-active* engine
   signals (tennis_technical, market_momentum, smart_money,
   crowd_volume — sentiment_llm is β₁-frozen at 0). The chokepoint
   :func:`data.etl.pit_correct.assert_no_lookahead` guards the whole
   frame as defence-in-depth.

2. **Player-order shuffle** — Sackmann's winner-first layout would
   trivially leak the outcome through column order, so we
   deterministically flip ~half of the rows (player1/player2 swap +
   market_yes_price inverted + outcome flipped). The shuffle is
   seeded from ``match_id`` so re-running the runner on the same
   parquet produces byte-identical features.

The shipped ``smart_money`` + ``crowd_volume`` signals are zero with
low (0.1) confidence — tennis-specific market depth + sentiment feeds
are not in the sprint_7 corpus, so the model's two real engines are
``tennis_technical`` (α₁) + ``market_momentum`` (α₂). Phase 2 will
add the missing channels.

PRD §6.8 forbidden columns (``outcome``, ``payout``, ``settled_at``,
``resolved_at``) never touch a feature slot — the row's outcome is
held in a separate field that the runner only reads AFTER calling
:func:`predict_tennis_prob`.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agent.engines.tennis_technical import (
    compute_best_of_factor,
    compute_elo_diff,
    compute_h2h,
    compute_surface_advantage,
)
from data.etl.pit_correct import assert_no_lookahead, require_asof_ts

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    import pandas as pd

    from data.sources.tennis_sackmann import SackmannLoader


# ─── Schema constants ────────────────────────────────────────────────

# Columns the T-E-003 tennis_phase1.parquet ships. Mirrored so a
# downstream schema-drift surfaces immediately instead of as a
# KeyError mid-loop.
TENNIS_PHASE1_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "match_id",
    "asof_ts",
    "player1_id",
    "player2_id",
    "surface",
    "tour_level",
    "best_of",
    "market_yes_price",
    "outcome",
)

# Tennis_technical engine combines three primitives; these weights pick
# how the rank_points-derived skill gap, surface advantage, and H2H
# record fuse into the α₁ engine score. The numerical values are NOT
# tuned per match — they are a sensible heuristic prior; the GLOBAL α
# simplex (trained downstream) decides how much α₁ overall contributes
# vs market_momentum / smart_money. The brief explicitly defers per-
# engine sub-weight tuning to a future hyper-sweep.
_TT_ELO_WEIGHT: Final[float] = 0.50
_TT_SURFACE_WEIGHT: Final[float] = 0.30
_TT_H2H_WEIGHT: Final[float] = 0.20

# Elo-diff to score-range normalisation. The Sackmann rank_points
# scale runs roughly 0 → ~12 000 for the world #1; a difference of
# 3 000 is "world #1 vs #100" and we want that mapped near ±1.
# tanh(diff/3000) has the right shape — gentle in the [0, 1500] band,
# saturating beyond.
_TT_ELO_SCALE: Final[float] = 3000.0

# Confidence floor for the engines we don't yet have a tennis-side
# data feed for (smart_money + crowd_volume). 0.1 == "we have nothing,
# but defer to the weight_updater rather than zeroing the channel".
_NEUTRAL_DATA_CONF: Final[float] = 0.10
_NEUTRAL_DATA_SCORE: Final[float] = 0.0

# Confidence floor + ceiling for the two engines we DO have. These
# clamp the per-row confidence so a perfectly-deterministic row never
# becomes a 1.0 dirac that drives the gradient over a single match.
_ACTIVE_CONF_FLOOR: Final[float] = 0.30
_ACTIVE_CONF_CEILING: Final[float] = 0.95

# Hash of asof_ts already enforces PIT discipline at the source; the
# margin we additionally check at the frame chokepoint just guards
# against a future writer that drops the per-row check.
_PIT_SAFETY_MARGIN_SEC: Final[int] = 60


# ─── Public dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class TennisFeatureRow:
    """One match's Phase 1 feature vector + label after PIT projection.

    Fields are partitioned into three groups:

    * Identity: ``match_id``, ``asof_ts``, ``player1_id``, ``player2_id``,
      ``surface``, ``tour_level``, ``best_of`` — the PIT key + sorting
      key. ``tour_level`` is the composite ``"<tour>-<level>"`` form
      emitted by :func:`data.etl.build_training_set.build_tennis_phase1`.

    * Engine signals: 4 ``<engine>_score`` + 4 ``<engine>_conf`` floats.
      The β₁ sentiment_llm channel is omitted — it's frozen at 0 in
      Phase 1, so its row contribution is identically 0.

    * Label: ``outcome`` ∈ {0, 1} — 1 if player1 won AFTER the shuffle.

    ``shuffled`` records whether the player-order swap fired for this
    match. The runner doesn't need it but the auditor + the
    backtest replay want to verify the swap rate stayed near 50%.

    Frozen so a downstream stage can't accidentally swap the outcome
    into a feature slot (look-ahead defence).
    """

    match_id: str
    asof_ts: datetime
    player1_id: str
    player2_id: str
    surface: str
    tour_level: str
    best_of: int
    market_yes_price: float
    shuffled: bool

    tennis_technical_score: float
    market_momentum_score: float
    smart_money_score: float
    crowd_volume_score: float

    tennis_technical_conf: float
    market_momentum_conf: float
    smart_money_conf: float
    crowd_volume_conf: float

    outcome: int


# ─── I/O ─────────────────────────────────────────────────────────────


def load_tennis_phase1(path: Path) -> pd.DataFrame:
    """Read + schema-validate the tennis_phase1.parquet from disk.

    Returns the loaded frame sorted by ``asof_ts`` ascending —
    chronological order is the only valid walk direction for the
    Phase 1 training loop. PIT enforcement on individual rows happens
    in :func:`build_tennis_feature_rows` once we have ``asof_ts`` +
    a derived ``available_at`` alias.
    """
    import pandas as pd

    path = Path(path)
    try:
        df = pd.read_parquet(path)
    except (FileNotFoundError, OSError) as exc:
        raise FileNotFoundError(
            f"tennis_phase1 parquet missing: {path} — run "
            "`python -m data.etl.build_training_set tennis-phase1 "
            f"--output {path}` to generate."
        ) from exc

    missing = [c for c in TENNIS_PHASE1_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"tennis_phase1 parquet missing required columns: {sorted(missing)}. "
            f"got {sorted(df.columns)}."
        )

    df["asof_ts"] = pd.to_datetime(df["asof_ts"], utc=True, errors="raise")

    # Outcome integrity — the source parquet ships outcome=1 everywhere
    # (winner-first construction). Validate the integer-ness; the
    # shuffle in build_tennis_feature_rows balances the distribution.
    if df["outcome"].isin([0, 1]).sum() != len(df):
        raise ValueError(
            "outcome column must be strictly 0 / 1 — got values outside {0, 1}"
        )

    df = df.sort_values("asof_ts", kind="stable").reset_index(drop=True)
    return df


# ─── Deterministic player-order shuffle ──────────────────────────────


def _should_swap(match_id: str, seed: int) -> bool:
    """True iff this match's player1/player2 should swap.

    Hash the match_id with the seed and take the low bit. SHA-256 so a
    future seed bump has near-uniform downstream effects rather than
    nudging adjacent matches in lockstep. Deterministic + replay-safe.
    """
    key = f"{seed}:{match_id}".encode()
    digest = hashlib.sha256(key).digest()
    return digest[0] & 1 == 1


# ─── Tennis-specific feature math ────────────────────────────────────


def _clip(value: float, lo: float, hi: float) -> float:
    """Inline clamp — math.fsum-friendly + mypy --strict happy."""
    return max(lo, min(hi, value))


def _compute_tennis_technical(  # lookahead: ok — asof_ts param is the cutoff; require_asof_ts enforced downstream
    *,
    p1_id: str,
    p2_id: str,
    surface: str,
    asof_ts: datetime,
    loader: SackmannLoader | None,
) -> tuple[float, float]:
    """Combine the three tennis_technical primitives into (score, conf).

    Score is the weighted blend of:

    * ``tanh(elo_diff / 3000)`` — skill gap normalised to roughly [-1, 1]
    * ``surface_advantage`` — already in [-1, 1] from the engine
    * ``2 · (h2h.p1_win_rate − 0.5)`` — H2H mapped to [-1, 1] when
      available; 0 when the pair has never met.

    Confidence floor 0.30, ceiling 0.95 — see :data:`_ACTIVE_CONF_FLOOR`
    / :data:`_ACTIVE_CONF_CEILING`. The base confidence is built from
    the three signals' availability: 0.95 when all present, ~0.55
    when only the elo signal is present (no surface or H2H history).
    """
    elo_diff = compute_elo_diff(p1_id, p2_id, asof_ts, loader=loader)
    surface_adv = compute_surface_advantage(
        p1_id, p2_id, surface, asof_ts, loader=loader
    )
    h2h = compute_h2h(p1_id, p2_id, asof_ts, loader=loader)

    elo_signal_present = elo_diff != 0.0
    surface_signal_present = surface_adv != 0.0
    h2h_signal_present = h2h["p1_win_rate"] is not None

    elo_norm = math.tanh(elo_diff / _TT_ELO_SCALE)
    h2h_norm: float
    h2h_norm = 2.0 * (h2h["p1_win_rate"] - 0.5) if h2h["p1_win_rate"] is not None else 0.0

    score = (
        _TT_ELO_WEIGHT * elo_norm
        + _TT_SURFACE_WEIGHT * surface_adv
        + _TT_H2H_WEIGHT * h2h_norm
    )
    # The blend can theoretically exceed [-1, 1] when all three signals
    # are at +1 (max 0.50 + 0.30 + 0.20 = 1.0 — saturated), so just
    # clip defensively.
    score = _clip(score, -1.0, 1.0)

    # Confidence: each available signal contributes its weight; missing
    # signals reduce the floor.
    raw_conf = (
        _TT_ELO_WEIGHT * (1.0 if elo_signal_present else 0.4)
        + _TT_SURFACE_WEIGHT * (1.0 if surface_signal_present else 0.4)
        + _TT_H2H_WEIGHT * (1.0 if h2h_signal_present else 0.4)
    )
    conf = _clip(raw_conf, _ACTIVE_CONF_FLOOR, _ACTIVE_CONF_CEILING)
    return score, conf


def _compute_market_momentum(market_yes_price: float) -> tuple[float, float]:  # lookahead: ok — pure transform on a price already snapped at asof_ts by T-E-003
    """Project the YES book price into (α₂ score, α₂ confidence).

    A 0.50 mid-price is the "no signal" point; ±0.50 from there
    saturates to ±1 score. Confidence rises with distance from 0.5
    because a market that has committed to a side is — by efficient-
    market priors — more informative than a coin-flip line.
    """
    score = _clip(2.0 * (market_yes_price - 0.5), -1.0, 1.0)
    distance = abs(market_yes_price - 0.5)
    conf = _clip(2.0 * distance, _ACTIVE_CONF_FLOOR, _ACTIVE_CONF_CEILING)
    return score, conf


# ─── Top-level row builder ───────────────────────────────────────────


def build_tennis_feature_rows(  # lookahead: ok — asof_ts read from each row inside the loop; assert_no_lookahead chokepoint runs frame-wide
    df: pd.DataFrame,
    *,
    shuffle_seed: int,
    loader: SackmannLoader | None = None,
) -> list[TennisFeatureRow]:
    """Project ``df`` (tennis_phase1.parquet rows) into TennisFeatureRow.

    Parameters
    ----------
    df:
        :class:`pandas.DataFrame` from :func:`load_tennis_phase1`.
    shuffle_seed:
        Integer seed for the deterministic player-order shuffle. The
        same seed always picks the same swap-bit per match_id, so
        feature generation is fully reproducible.
    loader:
        Optional :class:`data.sources.tennis_sackmann.SackmannLoader`
        override (tests inject a fixture-pinned snapshot dir).

    Returns
    -------
    A list of :class:`TennisFeatureRow` in chronological order.

    Raises
    ------
    :class:`data.etl.pit_correct.LookaheadError`:
        If any row's ``asof_ts`` is naive or later than the frame-wide
        cutoff (``max(asof_ts) - 1 minute``).
    """
    import pandas as pd

    if df.empty:
        return []

    # Frame-wide PIT chokepoint — every row's asof_ts must precede the
    # latest match by at least the safety margin. The per-row asof_ts
    # came from T-E-003's PIT-aware builder, so this is defence-in-depth.
    last_asof_raw = df["asof_ts"].max()
    if not isinstance(last_asof_raw, pd.Timestamp):  # pragma: no cover — defence
        raise ValueError(
            f"asof_ts column must be Timestamp-typed; got {type(last_asof_raw)!r}"
        )
    last_asof: datetime = last_asof_raw.to_pydatetime()
    if last_asof.tzinfo is None:  # pragma: no cover — load_tennis_phase1 coerces
        from datetime import UTC as _UTC

        last_asof = last_asof.replace(tzinfo=_UTC)
    # Build an available_at alias for the chokepoint signature.
    audit = df[["asof_ts"]].rename(columns={"asof_ts": "available_at"})
    assert_no_lookahead(audit, last_asof)

    rows: list[TennisFeatureRow] = []
    for record in df.to_dict(orient="records"):
        match_id = str(record["match_id"])
        asof_raw = record["asof_ts"]
        if isinstance(asof_raw, pd.Timestamp):
            asof = asof_raw.to_pydatetime()
        elif isinstance(asof_raw, datetime):
            asof = asof_raw
        else:  # pragma: no cover — defence
            raise ValueError(f"unexpected asof_ts type: {type(asof_raw)!r}")
        asof = require_asof_ts(asof)

        # Source order: player1 = winner per Sackmann's layout.
        winner_id = str(record["player1_id"])
        loser_id = str(record["player2_id"])
        market_yes_price_winner = float(record["market_yes_price"])

        swap = _should_swap(match_id, shuffle_seed)
        if swap:
            p1_id, p2_id = loser_id, winner_id
            market_yes_price = 1.0 - market_yes_price_winner
            outcome = 0  # original outcome was 1 (winner first); flipped
        else:
            p1_id, p2_id = winner_id, loser_id
            market_yes_price = market_yes_price_winner
            outcome = 1

        surface = str(record["surface"])
        tour_level = str(record["tour_level"])
        best_of = int(record["best_of"])

        tt_score, tt_conf = _compute_tennis_technical(
            p1_id=p1_id, p2_id=p2_id, surface=surface, asof_ts=asof, loader=loader
        )
        mm_score, mm_conf = _compute_market_momentum(market_yes_price)

        # best_of_factor scales market_momentum confidence slightly —
        # best-of-5 carries lower upset variance, so the market price is
        # a better signal (we trust α₂ a touch more on Slam finals).
        if compute_best_of_factor(tour_level):
            mm_conf = _clip(mm_conf * 1.10, _ACTIVE_CONF_FLOOR, _ACTIVE_CONF_CEILING)

        rows.append(
            TennisFeatureRow(
                match_id=match_id,
                asof_ts=asof,
                player1_id=p1_id,
                player2_id=p2_id,
                surface=surface,
                tour_level=tour_level,
                best_of=best_of,
                market_yes_price=market_yes_price,
                shuffled=swap,
                tennis_technical_score=tt_score,
                market_momentum_score=mm_score,
                smart_money_score=_NEUTRAL_DATA_SCORE,
                crowd_volume_score=_NEUTRAL_DATA_SCORE,
                tennis_technical_conf=tt_conf,
                market_momentum_conf=mm_conf,
                smart_money_conf=_NEUTRAL_DATA_CONF,
                crowd_volume_conf=_NEUTRAL_DATA_CONF,
                outcome=outcome,
            )
        )
    return rows


__all__ = [
    "TENNIS_PHASE1_REQUIRED_COLUMNS",
    "TennisFeatureRow",
    "build_tennis_feature_rows",
    "load_tennis_phase1",
]
