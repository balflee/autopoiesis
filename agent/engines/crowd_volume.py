"""β₂ — Crowd volume engine (PRD §4 second Sentient-stream component).

Consumes Track E's :class:`data.sources.reddit.RedditSentimentClient`
and surfaces the per-window post count + comment velocity as a
contrarian-ish attention signal: a sudden retail attention spike
is weakly predictive of mean reversion (PRD §4 "Crowd volume" rule
of thumb), so the engine returns NEGATIVE score on the home-team
bias direction when post velocity spikes above a baseline.

**Phase 1 contract**: this engine is NOT frozen — β₂=1.0 in Phase 1
per PRD §4.2 ("training only in (W_R, α₁, α₂, α₃) 4-dim space"; β₁
is the frozen channel, β₂ stays live so the Sentient stream has a
non-zero output during Phase 1).

**Look-ahead rules**: the Reddit client filters posts to ``created_utc
<= asof_ts`` before aggregation. We pipe the resulting snapshot
through :func:`assert_no_lookahead` for defence-in-depth.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from agent.engines.base import Engine, Signal, assert_no_lookahead, require_asof_ts
from data.sources.reddit import SentimentSnapshot


class _RedditClientLike(Protocol):
    """Subset of :class:`RedditSentimentClient` the engine consumes."""

    def fetch_subreddit(
        self,
        name: str,
        since: datetime,
        *,
        asof_ts: datetime,
        limit: int = 100,
    ) -> SentimentSnapshot: ...


# Baseline post count over a 24h window. Anything above this is read
# as an attention spike. Per the sprint 7 sport pivot (PRD §15 已决
# #8) the canonical subreddit feeding β₂ is r/tennis — typical baseline
# ≈ 30-40 posts/day on an off-Slam tour day, spiking to 200+ during
# Grand Slam finals. The ≈ 2 posts/hour default is tuned against
# r/tennis off-Slam weekdays; Phase 2+ migrates this constant into the
# calibration layer.
_DEFAULT_BASELINE_POSTS_PER_HOUR = 2.0


class CrowdVolumeEngine(Engine):
    """Engine implementing the β₂ Reddit关注度 score."""

    name = "crowd_volume"

    def __init__(
        self,
        *,
        reddit_client: _RedditClientLike,
        window_hours: int = 24,
        baseline_posts_per_hour: float = _DEFAULT_BASELINE_POSTS_PER_HOUR,
    ) -> None:
        if window_hours <= 0:
            raise ValueError(f"window_hours must be positive (got {window_hours})")
        self._reddit = reddit_client
        self._window_hours = window_hours
        self._baseline_pph = baseline_posts_per_hour

    async def evaluate(self, *, target: str, asof_ts: datetime) -> Signal:
        """Score crowd attention for subreddit ``target`` (e.g. ``"tennis"``).

        Sprint 7 sport pivot: ``target`` is the tennis subreddit slug
        (``"tennis"`` for the canonical r/tennis sub, ``"ATPTour"`` /
        ``"WTAtour"`` for the tour-specific subs). The PRD §4 contrarian
        rule (fade the crowd on spikes) is sport-agnostic — the only
        thing the pivot moves is *which* subreddit feeds the engine.
        """
        cutoff = require_asof_ts(asof_ts)
        since = cutoff - timedelta(hours=self._window_hours)
        snap = self._reddit.fetch_subreddit(target, since, asof_ts=cutoff)

        # Chokepoint defence-in-depth.
        feat_df = pd.DataFrame(
            [
                {
                    "feature": "post_count",
                    "value": float(snap.post_count),
                    "available_at": snap.available_at,
                },
                {
                    "feature": "comment_count",
                    "value": float(snap.comment_count),
                    "available_at": snap.available_at,
                },
            ]
        )
        assert_no_lookahead(feat_df, cutoff)

        pph = snap.post_count / self._window_hours
        baseline = max(self._baseline_pph, 1e-6)
        ratio = pph / baseline  # > 1 = attention spike
        # Comment velocity: comments per post, proxy for engagement.
        cpp = (snap.comment_count / snap.post_count) if snap.post_count > 0 else 0.0

        # Spike score in [-1, 1] via tanh on log-ratio (centred at 0
        # when pph == baseline). Sign is NEGATIVE per the contrarian
        # rule above: high crowd volume biases toward the "fade the
        # public" prior.
        log_ratio = math.log(max(ratio, 1e-6))
        score = -math.tanh(log_ratio)

        # Confidence rises with sample size — sparse windows are noisy.
        confidence = min(1.0, snap.post_count / 50.0)

        raw_features: dict[str, float] = {
            "post_count": float(snap.post_count),
            "comment_count": float(snap.comment_count),
            "posts_per_hour": pph,
            "baseline_pph": baseline,
            "spike_ratio": ratio,
            "comments_per_post": cpp,
            "window_hours": float(self._window_hours),
        }

        return Signal(
            score=score,
            confidence=confidence,
            available_at=snap.available_at.isoformat(),
            rationale=(
                f"r/{target} {snap.post_count}p/{self._window_hours}h "
                f"({pph:.1f}/h vs {baseline:.1f} baseline; cpp={cpp:.1f})"
            ),
            raw_features=raw_features,
        )


__all__ = ["CrowdVolumeEngine"]
