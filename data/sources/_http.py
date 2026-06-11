"""Shared HTTP helpers for Track E source clients.

Centralised because every fetcher needs the same three pieces:

* A ``requests.Session`` with a polite User-Agent so upstream feeds
  don't bucket us with anonymous scraper traffic.
* Exponential-backoff retry on 429 + 5xx — at least 3 attempts per
  the T-E-002 acceptance criterion for the Polymarket fetcher; the
  other fetchers reuse the same wrapper for consistency.
* A point-in-time gate (:func:`require_asof_ts`) that wraps the bare
  ``asof_ts`` parameter check, so every ``.fetch_*`` entrypoint
  enforces PRD §14.1 the same way.

NB: no module-level network I/O. Sessions are constructed lazily on
first :meth:`HttpClient.get` call.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests

# Re-export the canonical asof guard so source-clients pick up the
# same chokepoint Track B + Track C use at join time. ONE function,
# imported in two places — see PRD §14.1.
from data.etl.pit_correct import require_asof_ts

DEFAULT_USER_AGENT = (
    "genesis-experiment/0.2 (+https://genesis.experiment; track-e-data-fetcher)"
)

# Acceptance criterion: ≥3-retry exponential schedule. Schedule is 1s, 2s, 4s
# back-off between attempts 1→2, 2→3, 3→4, so 4 total attempts and ≥3 retries.
DEFAULT_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 4.0)

# 429 = Too Many Requests, 5xx = upstream transient. 4xx other than 429 are
# caller errors and should NOT retry — they propagate immediately.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class HttpClient:
    """Thin wrapper around :class:`requests.Session` with retry + UA.

    The constructor is cheap (no network) — the session is built on
    demand. Tests that mock the session can inject one via
    :meth:`set_session`.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        backoff_schedule: tuple[float, ...] = DEFAULT_BACKOFF_SCHEDULE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._user_agent = user_agent
        self._backoff_schedule = backoff_schedule
        self._sleep = sleep
        self._session: requests.Session | None = None

    def set_session(self, session: requests.Session) -> None:
        """Inject a pre-built session — used by tests."""
        self._session = session

    def _session_lazy(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update({"User-Agent": self._user_agent})
            self._session = s
        return self._session

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> requests.Response:
        """GET ``url`` with exponential-backoff retry on 429/5xx.

        Raises :class:`requests.HTTPError` on the final attempt if the
        response is still retryable; non-retryable errors propagate
        immediately on the first attempt.
        """
        session = self._session_lazy()
        last_exc: Exception | None = None
        last_resp: requests.Response | None = None
        # +1 to backoff schedule = total attempt count (the schedule is
        # the *gap* between attempts, not the attempt count).
        total_attempts = len(self._backoff_schedule) + 1

        for attempt_idx in range(total_attempts):
            try:
                resp = session.get(url, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                last_exc = exc
                last_resp = None
                # Network errors retry on the same schedule.
                if attempt_idx < total_attempts - 1:
                    self._sleep(self._backoff_schedule[attempt_idx])
                    continue
                raise

            if resp.status_code in RETRYABLE_STATUS_CODES:
                last_resp = resp
                if attempt_idx < total_attempts - 1:
                    self._sleep(self._backoff_schedule[attempt_idx])
                    continue
                resp.raise_for_status()  # fall through if 2xx, raise if 4xx/5xx
                return resp

            # Non-retryable: either success (2xx/3xx) or hard failure (4xx).
            if 400 <= resp.status_code < 600:
                resp.raise_for_status()
            return resp

        # Defensive: should be unreachable given the loop structure.
        if last_resp is not None:
            last_resp.raise_for_status()
            return last_resp
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("HttpClient.get exhausted attempts without result")


__all__ = [
    "DEFAULT_BACKOFF_SCHEDULE",
    "DEFAULT_USER_AGENT",
    "RETRYABLE_STATUS_CODES",
    "HttpClient",
    "require_asof_ts",
]
