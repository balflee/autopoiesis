# Greek letters (α, β, σ) mirror PRD §4 / §6.6 notation. Disambiguating
# them to Latin fallbacks would silently desync the code from the spec.
"""α₂ — Market momentum engine (PRD §4 second Rational-stream component).

Pulls a point-in-time Polymarket price+volume trace via Track E's
:class:`data.sources.polymarket.PolymarketHistoryClient` and computes
the four features TECHNICAL_PLAN §4.5 specifies:

* ``drift`` — recent EWMA-weighted mid change.
* ``velocity`` — 1-hour normalised mid change (proxy for the 1h / 4h
  TP §4.5 features; we collapse to one velocity until intraday tick
  data is wired).
* ``depth_imbalance`` — placeholder set to 0.0 since the public CLOB
  ``prices-history`` endpoint does not return bid/ask depth. Wired
  through the schema so the wire shape is stable; populated when
  Track E adds the L2 orderbook fetch.
* ``spread_tightness`` — 1 / (1 + σ) of the mid (high tightness =
  low variance = high confidence).

Scores are clipped to [-1, 1] via a tanh squash so a runaway drift
on a thin market cannot escape the fusion layer's normalisation
invariant.

**Look-ahead rules**: ``PolymarketHistoryClient.fetch_market`` already
filters snapshots to ``ts <= asof_ts`` and returns ``available_at =
asof_ts``. The engine's feature DataFrame still gets piped through
:func:`assert_no_lookahead` for defence-in-depth.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import StatisticsError, fmean, stdev
from typing import Protocol

import pandas as pd

from agent.engines.base import Engine, Signal, assert_no_lookahead, require_asof_ts
from data.sources.polymarket import MarketHistory


class _PolymarketHistoryLike(Protocol):
    """Subset of :class:`PolymarketHistoryClient` the engine consumes."""

    def fetch_market(self, slug: str, *, asof_ts: datetime) -> MarketHistory: ...


class MarketMomentumEngine(Engine):
    """Engine implementing the α₂ 盘口动量 score."""

    name = "market_momentum"

    def __init__(self, *, polymarket_client: _PolymarketHistoryLike) -> None:
        self._pm = polymarket_client

    async def evaluate(self, *, target: str, asof_ts: datetime) -> Signal:
        """Score the recent-price momentum for market ``target`` (slug)."""
        cutoff = require_asof_ts(asof_ts)
        history = self._pm.fetch_market(target, asof_ts=cutoff)
        snapshots = history.orderbook_snapshots

        if not snapshots:
            # Empty history → vacuously safe, no signal.
            return Signal(
                score=0.0,
                confidence=0.0,
                available_at=cutoff.isoformat(),
                rationale="empty price history",
                raw_features={
                    "n_snapshots": 0.0,
                    "drift": 0.0,
                    "velocity": 0.0,
                    "depth_imbalance": 0.0,
                    "spread_tightness": 0.0,
                },
            )

        # Build feature DataFrame for the chokepoint.
        feat_df = pd.DataFrame(
            [{"feature": "mid", "value": p, "available_at": ts} for ts, p in snapshots]
        )
        assert_no_lookahead(feat_df, cutoff)

        prices = [p for _, p in snapshots]
        n = len(prices)

        # Drift: latest minus mean of the prior window. Normalised by
        # the anchor so a +0.05 move on a 0.50 market reads stronger
        # than the same move on a 0.95 market.
        latest = prices[-1]
        anchor = fmean(prices[:-1]) if n > 1 else latest
        denom = max(anchor, 1e-3)
        drift = (latest - anchor) / denom

        # Velocity: latest minus first / time span — proxy for the
        # TP §4.5 1h/4h velocities until intraday ticks land.
        first_ts, first_p = snapshots[0]
        last_ts, last_p = snapshots[-1]
        span_h = max((last_ts - first_ts).total_seconds() / 3600.0, 1.0)
        velocity = (last_p - first_p) / span_h

        # Depth imbalance: placeholder until L2 orderbook lands. The
        # field is wired through the schema so the wire shape is stable.
        depth_imbalance = 0.0

        # Spread tightness: high when variance is low. Use 1 / (1 + σ)
        # so the range is naturally (0, 1].
        try:
            sigma = stdev(prices) if n > 1 else 0.0
        except StatisticsError:
            sigma = 0.0
        spread_tightness = 1.0 / (1.0 + sigma)

        raw_features: dict[str, float] = {
            "n_snapshots": float(n),
            "drift": drift,
            "velocity": velocity,
            "depth_imbalance": depth_imbalance,
            "spread_tightness": spread_tightness,
            "latest_mid": latest,
        }

        # Fuse into a single score in [-1, 1]. Drift dominates;
        # velocity adds short-horizon kick; spread_tightness ↦ confidence.
        raw_score = 0.6 * drift + 0.4 * velocity
        score = math.tanh(raw_score)
        # Confidence rises with sample size and spread tightness.
        confidence = min(1.0, (n / 24.0) * spread_tightness)

        return Signal(
            score=score,
            confidence=confidence,
            available_at=cutoff.isoformat(),
            rationale=(
                f"n={n} drift={drift:+.3f} velocity={velocity:+.3f}/h "
                f"tightness={spread_tightness:.2f}"
            ),
            raw_features=raw_features,
        )


__all__ = ["MarketMomentumEngine"]
