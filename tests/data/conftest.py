"""Shared test fixtures for tests/data.

The pattern here is "session-injection cassette": tests construct a
:class:`FakeSession` from a dict of URL → JSON-payload tuples (loaded
from ``tests/data/fixtures/``), inject it via the client's
``HttpClient.set_session(...)`` hook, and the rest of the fetcher
runs against deterministic offline data.

This is the same hermetic-replay shape vcrpy provides but without the
YAML-cassette indirection — every fixture is a plain JSON file the
reviewer can read at a glance.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import pytest
import requests


FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Read ``tests/data/fixtures/<name>`` and decode JSON."""
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeResponse:
    """A duck-typed minimal :class:`requests.Response`.

    We only need ``status_code``, ``raise_for_status``, ``json``, and
    ``text`` for the Track E fetchers.
    """

    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Any | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._text = text or (json.dumps(payload) if payload is not None else "")

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("No JSON payload in fake response")
        return self._payload

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(f"Fake {self.status_code}", response=self)  # type: ignore[arg-type]


# A response factory: per-call dynamic decision (used by the retry test).
ResponseFactory = Callable[[str, dict[str, Any] | None], _FakeResponse]


class FakeSession:
    """A :class:`requests.Session`-shaped object for hermetic tests.

    Two routing modes:

    * ``routes``: ``{path: (status_code, payload)}`` — exact path match.
    * ``factory``: a callable invoked on every ``.get()`` for full
      control (used by the retry/backoff test).
    """

    def __init__(
        self,
        *,
        routes: dict[str, tuple[int, Any]] | None = None,
        factory: ResponseFactory | None = None,
    ) -> None:
        self.routes = routes or {}
        self.factory = factory
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, params))
        if self.factory is not None:
            return self.factory(url, params)
        path = urlparse(url).path
        if path in self.routes:
            status, payload = self.routes[path]
            return _FakeResponse(status_code=status, payload=payload)
        # Fall back to root path-only lookup for endpoints with no path.
        if url in self.routes:
            status, payload = self.routes[url]
            return _FakeResponse(status_code=status, payload=payload)
        raise AssertionError(f"FakeSession: no route registered for {url} (path={path})")


@pytest.fixture
def fake_session_cls() -> type[FakeSession]:
    """Expose the :class:`FakeSession` class to tests as a fixture."""
    return FakeSession


@pytest.fixture
def fake_response_cls() -> type[_FakeResponse]:
    """Expose the :class:`_FakeResponse` class to tests as a fixture."""
    return _FakeResponse


@pytest.fixture
def balldontlie_payload() -> dict[str, Any]:
    return load_fixture("balldontlie_game_15908525.json")


@pytest.fixture
def polymarket_meta_payload() -> dict[str, Any]:
    return load_fixture("polymarket_market_metadata.json")


@pytest.fixture
def polymarket_history_payload() -> dict[str, Any]:
    return load_fixture("polymarket_prices_history.json")


@pytest.fixture
def reddit_payload() -> dict[str, Any]:
    return load_fixture("reddit_nba_new.json")


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``live`` marker so :func:`pytest.mark.live` is recognised."""
    config.addinivalue_line(
        "markers",
        "live: hits real upstream APIs; skipped unless RUN_LIVE_DATA_TESTS=1 (see README_DATA.md)",
    )
