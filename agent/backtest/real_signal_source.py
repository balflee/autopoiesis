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
from dataclasses import dataclass, field  # (codex fix) `field` needed for default_factory
from datetime import datetime
from statistics import StatisticsError, fmean, stdev

from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.engines.base import Signal
from agent.engines.decision import (
    CROWD_VOLUME,
    MARKET_MOMENTUM,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
)
from agent.engines.tennis_technical import (
    compute_days_since_last_match,
    compute_elo_diff,
    compute_h2h,
    compute_surface_advantage,
)

# (codex fix) used by the loader default; (A0 correction) DEFAULT_CORPUS_DIR = the
# full re-vendored corpus, NOT the synthetic-fixture snapshot default.
from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

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


# ---------------------------------------------------------------------------
# The 4 Sackmann facet normalizers (slot repurpose, see module docstring).
# Each wraps a tennis_technical.compute_* primitive and normalizes its output
# into a Signal per the plan's normalization table. IDs are cast to str.
# ---------------------------------------------------------------------------


def elo_signal(
    p1_id: object,
    p2_id: object,
    *,
    asof_ts: datetime,
    loader: SackmannLoader,
) -> Signal:
    """ELO/ranking gap -> tennis_technical slot.

    ``score = tanh(elo_diff / 3000)`` (rank-points delta squashed to [-1, 1]);
    ``confidence = 0.0`` when the diff is exactly 0 (either player unranked, no
    signal) else ``0.7``. ``compute_elo_diff`` takes no ``year_range`` — it reads
    ``asof_ts``-based rankings directly.
    """
    elo_diff = compute_elo_diff(str(p1_id), str(p2_id), asof_ts, loader=loader)
    score = math.tanh(elo_diff / 3000.0)
    confidence = 0.0 if elo_diff == 0.0 else 0.7
    return Signal(
        score=score,
        confidence=confidence,
        available_at=asof_ts.isoformat(),
        rationale=f"elo: diff={elo_diff:+.0f}",
        raw_features={"elo_diff": elo_diff},
    )


def surface_signal(
    p1_id: object,
    p2_id: object,
    surface: str,
    *,
    asof_ts: datetime,
    loader: SackmannLoader,
    year_range: tuple[int, int],
) -> Signal:
    """Surface advantage -> smart_money slot.

    ``score = compute_surface_advantage(...)`` (already in [-1, 1], positive
    favours p1); ``confidence = 0.0`` when the advantage is exactly 0 (no edge)
    else ``0.6``.
    """
    score = compute_surface_advantage(
        str(p1_id), str(p2_id), surface, asof_ts, loader=loader, year_range=year_range
    )
    confidence = 0.0 if score == 0.0 else 0.6
    return Signal(
        score=score,
        confidence=confidence,
        available_at=asof_ts.isoformat(),
        rationale=f"surface[{surface}]: adv={score:+.3f}",
        raw_features={"surface_advantage": score},
    )


def h2h_signal(
    p1_id: object,
    p2_id: object,
    *,
    asof_ts: datetime,
    loader: SackmannLoader,
    year_range: tuple[int, int],
) -> Signal:
    """Head-to-head -> sentiment_llm slot.

    ``score = 2 * (p1_win_rate - 0.5)`` when they've met (maps a [0, 1] win rate
    to [-1, 1], positive favours p1) else ``0.0``; ``confidence = min(0.9,
    total_matches / 5 * 0.6)`` so more meetings -> more confidence (capped).
    """
    rec = compute_h2h(
        str(p1_id), str(p2_id), asof_ts, loader=loader, year_range=year_range
    )
    win_rate = rec["p1_win_rate"]
    total = rec["total_matches"]
    score = 0.0 if win_rate is None else 2.0 * (win_rate - 0.5)
    confidence = min(0.9, total / 5.0 * 0.6)
    return Signal(
        score=score,
        confidence=confidence,
        available_at=asof_ts.isoformat(),
        rationale=f"h2h: p1_wr={win_rate} n={total}",
        raw_features={
            "p1_win_rate": -1.0 if win_rate is None else win_rate,
            "total_matches": float(total),
        },
    )


def rest_signal(
    p1_id: object,
    p2_id: object,
    *,
    asof_ts: datetime,
    loader: SackmannLoader,
    year_range: tuple[int, int],
) -> Signal:
    """Rest/recency -> crowd_volume slot.

    ``d1, d2`` = days since each player's last match. When BOTH are known,
    ``score = tanh((d2 - d1) / 14)`` (positive when the opponent is the more-
    rested side per the plan's formula) and ``confidence = 0.4``; otherwise
    neutral (a missing player means no rest signal).
    """
    d1 = compute_days_since_last_match(
        str(p1_id), asof_ts, loader=loader, year_range=year_range
    )
    d2 = compute_days_since_last_match(
        str(p2_id), asof_ts, loader=loader, year_range=year_range
    )
    if d1 is None or d2 is None:
        return _neutral(asof_ts, "rest: missing match history")
    score = math.tanh((d2 - d1) / 14.0)
    return Signal(
        score=score,
        confidence=0.4,
        available_at=asof_ts.isoformat(),
        rationale=f"rest: d1={d1} d2={d2}",
        raw_features={"days_p1": float(d1), "days_p2": float(d2)},
    )


class _ProviderLike:  # structural: MarketSnapshotProvider.get(market_id) -> MarketSnapshot
    def get(self, market_id: str) -> object: ...


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
        # When the slug resolves to two Sackmann players + surface, replace the 4
        # neutral tennis slots with the REAL facet signals. Momentum is always real.
        # Markets that do not resolve keep the neutral fallback above.
        rm = self.resolver.resolve(getattr(snap, "slug", ""))
        if rm is not None:
            out[TENNIS_TECHNICAL] = elo_signal(
                rm.p1_id, rm.p2_id, asof_ts=asof_ts, loader=self.loader
            )
            out[SMART_MONEY] = surface_signal(
                rm.p1_id, rm.p2_id, rm.surface, asof_ts=asof_ts,
                loader=self.loader, year_range=self.year_range,
            )
            out[SENTIMENT_LLM] = h2h_signal(
                rm.p1_id, rm.p2_id, asof_ts=asof_ts,
                loader=self.loader, year_range=self.year_range,
            )
            out[CROWD_VOLUME] = rest_signal(
                rm.p1_id, rm.p2_id, asof_ts=asof_ts,
                loader=self.loader, year_range=self.year_range,
            )
        return out

    @staticmethod
    def _snapshots_until(snap: object, asof_ts: datetime) -> list[tuple[datetime, float]]:
        out: list[tuple[datetime, float]] = []
        for pp in getattr(snap, "price_ledger", []):
            ts = datetime.fromisoformat(pp.ts)
            if ts <= asof_ts:
                out.append((ts, pp.mid_price))
        return out
