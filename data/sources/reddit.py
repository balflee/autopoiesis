"""Reddit sentiment source-adapter — read-only subreddit window fetch.

Per PRD §7 the Reddit feed (r/nba + r/sportsbook archives) is one of
the four canonical data sources. This module ships the read-only
``fetch_subreddit`` entrypoint for sprint_2.

Choice of upstream: Reddit's public JSON endpoints
(``https://www.reddit.com/r/<sub>/new.json``) — no auth required for
read traffic, and the JSON is stable enough that a thin projection
yields the per-window counts the agent's sentiment featurisation
needs. Pushshift archives + PRAW are still the long-term plan
(per the brief), but the public JSON path is enough for sprint_2 to
produce real rows; the Pushshift backfill plugs in behind the same
``fetch_subreddit`` signature.

Hard rules:

* No auth tokens at module load (we use the public JSON path).
* :meth:`RedditSentimentClient.fetch_subreddit` requires the
  ``asof_ts`` keyword; missing or naive → :class:`LookaheadError`.
* Posts whose ``created_utc > asof_ts`` are filtered before the
  window aggregates are computed.
* No subreddit modification, no posting — read paths only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from data.sources._http import HttpClient, require_asof_ts

REDDIT_BASE_URL = "https://www.reddit.com"

# Token mention parser — stop words we strip from the mention_counts
# tally so we don't waste parquet width on prepositions.
_STOP_WORDS: frozenset[str] = frozenset(
    {"the", "and", "for", "this", "that", "with", "from", "have", "they"}
)


@dataclass(frozen=True)
class SentimentSnapshot:
    """Point-in-time subreddit sentiment snapshot.

    Back-compat with T-E-001 sprint_1 schema: ``subreddit`` / ``since``
    / ``until`` / ``available_at`` / ``post_count`` / ``mention_counts``
    all preserved. sprint_2 adds ``comment_count`` populated when the
    upstream payload includes it (Reddit's listing JSON returns it on
    every post).
    """

    subreddit: str
    since: datetime
    until: datetime
    available_at: datetime
    post_count: int = 0
    mention_counts: dict[str, int] = field(default_factory=dict)
    comment_count: int = 0


class RedditSentimentClient:
    """Reddit read-only sentiment client.

    Constructor is cheap (no network, no auth). Tests inject a
    recorded :class:`requests.Session` via :meth:`HttpClient.set_session`.
    """

    def __init__(
        self,
        *,
        base_url: str = REDDIT_BASE_URL,
        http: HttpClient | None = None,
        cache_dir: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http if http is not None else HttpClient()
        self._cache_dir: str | None = cache_dir

    @property
    def http(self) -> HttpClient:
        return self._http

    def fetch_subreddit(
        self,
        name: str,
        since: datetime,
        *,
        asof_ts: datetime,
        limit: int = 100,
    ) -> SentimentSnapshot:
        """Return the sentiment snapshot for ``r/<name>`` since ``since``.

        ``asof_ts`` defines both the upper bound of the window AND the
        PIT cutoff: posts created after ``asof_ts`` are filtered out.
        """
        cutoff = require_asof_ts(asof_ts)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if since > cutoff:
            raise ValueError(
                f"since ({since.isoformat()}) is after asof_ts ({cutoff.isoformat()}) — "
                "window is empty by construction."
            )

        url = f"{self._base_url}/r/{name}/new.json"
        resp = self._http.get(
            url,
            params={"limit": str(limit), "raw_json": "1"},
            timeout=10.0,
        )
        payload = resp.json()

        children: list[dict[str, Any]] = payload.get("data", {}).get("children", [])
        posts_in_window: list[dict[str, Any]] = []
        for c in children:
            data: dict[str, Any] = c.get("data", {})
            created_raw = data.get("created_utc")
            if created_raw is None:
                continue
            created = datetime.fromtimestamp(int(created_raw), tz=UTC)
            if created < since or created > cutoff:
                continue
            posts_in_window.append(data)

        mention_counts: dict[str, int] = {}
        total_comments = 0
        for p in posts_in_window:
            title = str(p.get("title", "")).lower()
            for tok in _tokenize(title):
                mention_counts[tok] = mention_counts.get(tok, 0) + 1
            total_comments += int(p.get("num_comments", 0) or 0)

        return SentimentSnapshot(
            subreddit=name,
            since=since,
            until=cutoff,
            available_at=cutoff,
            post_count=len(posts_in_window),
            mention_counts=mention_counts,
            comment_count=total_comments,
        )


def _tokenize(text: str) -> list[str]:
    """Cheap whitespace tokenizer with stop-word filter."""
    out: list[str] = []
    for raw in text.split():
        tok = "".join(ch for ch in raw if ch.isalnum())
        if len(tok) < 3 or tok in _STOP_WORDS:
            continue
        out.append(tok)
    return out


__all__ = ["REDDIT_BASE_URL", "RedditSentimentClient", "SentimentSnapshot"]
