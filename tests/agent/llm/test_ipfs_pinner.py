"""IPFSPinner tests — Pinata REST wrapper survival semantics.

Brief acceptance criterion (T-B-006):

    ``ipfs_pinner.pin_reflection()`` returns ``None`` on Pinata
    503-after-3-retries; caller continues tick (TP §4.6 'agent must
    survive' invariant).

No real HTTP under pytest. Tests inject :class:`_RecordingHttp` which
records every POST + returns scripted :class:`HttpResponse` values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.llm.ipfs_pinner import (
    PINATA_PIN_FILE_URL,
    HttpResponse,
    IPFSPinner,
    _build_multipart,
    _extract_cid,
    _HttpClient,
)


def _no_sleep(_seconds: float) -> None:
    """Sleep fake injected into IPFSPinner to keep test runtime tight."""


def _make_pinner(
    http: _HttpClient,
    *,
    api_key: str | None = "k",
    api_secret: str | None = "s",
    max_retries: int = 3,
) -> IPFSPinner:
    """Factory wiring :class:`IPFSPinner` with deterministic deps.

    Centralises the ``sleep_fn=_no_sleep`` + ``backoff_base_s=0.0``
    injection so the per-test signal stays focused on the HTTP fixture.
    """
    return IPFSPinner(
        api_key=api_key,
        api_secret=api_secret,
        http_client=http,
        max_retries=max_retries,
        backoff_base_s=0.0,
        sleep_fn=_no_sleep,
    )


@dataclass
class _RecordingHttp:
    """:class:`_HttpClient` Protocol fake — scripts responses + records."""

    responses: list[HttpResponse | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_s: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "url": url,
                "body": body,
                "headers": dict(headers),
                "timeout_s": timeout_s,
            }
        )
        if not self.responses:
            raise AssertionError("Recording HTTP exhausted — test wired too few")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok_response(cid: str = "bafy-test-cid") -> HttpResponse:
    return HttpResponse(
        status=200,
        body=json.dumps({"IpfsHash": cid, "PinSize": 1, "Timestamp": "now"}).encode(),
    )


def _503() -> HttpResponse:
    return HttpResponse(status=503, body=b"server overloaded")


def test_pin_reflection_happy_path_returns_cid() -> None:
    """Pinata 200 → extracted CIDv1 returned."""
    http = _RecordingHttp(responses=[_ok_response("bafyhappy")])
    pinner = _make_pinner(http)
    cid = pinner.pin_reflection(body="# tick reflection")
    assert cid == "bafyhappy"
    assert len(http.calls) == 1
    # Auth headers forwarded; URL is the canonical Pinata endpoint.
    call = http.calls[0]
    assert call["url"] == PINATA_PIN_FILE_URL
    assert call["headers"]["pinata_api_key"] == "k"
    assert call["headers"]["pinata_secret_api_key"] == "s"
    # Body is the multipart blob, contains our markdown payload.
    assert b"# tick reflection" in call["body"]


def test_pin_reflection_retries_then_succeeds() -> None:
    """503 ⇒ retry; eventual 200 ⇒ CID returned. The agent_loop should
    see a successful pin after a transient outage."""
    http = _RecordingHttp(
        responses=[_503(), _503(), _ok_response("bafylater")],
    )
    pinner = _make_pinner(http)
    cid = pinner.pin_reflection(body="content")
    assert cid == "bafylater"
    assert len(http.calls) == 3


def test_pin_reflection_returns_none_on_503_after_max_retries() -> None:
    """Brief invariant: 3 consecutive 503s ⇒ ``None`` ⇒ caller continues."""
    http = _RecordingHttp(responses=[_503(), _503(), _503()])
    pinner = _make_pinner(http)
    cid = pinner.pin_reflection(body="content")
    assert cid is None
    assert len(http.calls) == 3


def test_pin_reflection_returns_none_on_4xx_non_retryable() -> None:
    """A non-retryable 4xx (e.g. 401 bad auth) ⇒ ``None`` ⇒ caller
    continues. No retries fire on 4xx so the loop exits early."""
    http = _RecordingHttp(responses=[HttpResponse(status=401, body=b"bad auth")])
    pinner = _make_pinner(http, api_key="bad", api_secret="bad")
    cid = pinner.pin_reflection(body="content")
    assert cid is None
    assert len(http.calls) == 1  # no retries on 4xx


def test_pin_reflection_returns_none_on_connection_errors() -> None:
    """An OSError / URLError on every attempt ⇒ ``None`` — caller continues."""
    http = _RecordingHttp(
        responses=[OSError("net dead"), OSError("net dead"), OSError("net dead")],
    )
    pinner = _make_pinner(http)
    cid = pinner.pin_reflection(body="content")
    assert cid is None
    assert len(http.calls) == 3


def test_pin_reflection_returns_none_when_keys_missing() -> None:
    """Operator misconfiguration (no PINATA_API_KEY) ⇒ ``None`` — same
    survival semantics as a 503, agent_loop continues."""
    # autouse_no_provider_keys already cleared PINATA env vars; the
    # constructor receives ``None`` for both keys → lazy env read also
    # returns ``None`` → pin returns None without an HTTP call.
    http = _RecordingHttp(responses=[])
    pinner = _make_pinner(http, api_key=None, api_secret=None)
    cid = pinner.pin_reflection(body="content")
    assert cid is None
    assert len(http.calls) == 0


def test_extract_cid_handles_malformed_body() -> None:
    """Pinata returning 200 but a malformed JSON body ⇒ ``None`` CID."""
    assert _extract_cid(b"not json") is None
    assert _extract_cid(json.dumps([1, 2, 3]).encode()) is None
    assert _extract_cid(json.dumps({"other": "field"}).encode()) is None
    assert _extract_cid(json.dumps({"IpfsHash": ""}).encode()) is None
    assert _extract_cid(json.dumps({"IpfsHash": "real-cid"}).encode()) == "real-cid"


def test_build_multipart_includes_filename_and_body() -> None:
    """Multipart builder must include the filename + payload + boundary."""
    blob = _build_multipart(
        body=b"hello world",
        filename="my.md",
        boundary="b1",
    )
    text = blob.decode()
    assert "--b1\r\n" in text
    assert 'filename="my.md"' in text
    assert "hello world" in text
    assert text.endswith("--b1--\r\n")


def test_pin_reflection_uses_lazy_env_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Brief: 'lazily from os.environ inside pin_reflection'.

    Construct with no keys, then set env, then call. The lazy read
    means the call should succeed instead of failing on the missing
    keys.
    """
    http = _RecordingHttp(responses=[_ok_response("bafy-env")])
    pinner = _make_pinner(http, api_key=None, api_secret=None)
    monkeypatch.setenv("PINATA_API_KEY", "from_env")
    monkeypatch.setenv("PINATA_SECRET_KEY", "from_env_secret")
    cid = pinner.pin_reflection(body="x")
    assert cid == "bafy-env"
    assert http.calls[0]["headers"]["pinata_api_key"] == "from_env"


def test_pin_reflection_backs_off_between_retries() -> None:
    """503 ⇒ backoff sleep ⇒ retry. Verifies the agent_loop does not
    hammer a struggling Pinata with back-to-back POSTs.

    Exponential backoff: attempt 0 sleeps base * 2^0, attempt 1 sleeps
    base * 2^1, last attempt does NOT sleep (no fourth attempt
    follows). With ``base=2.0`` and ``max_retries=3`` we expect sleeps
    ``[2.0, 4.0]``.
    """
    http = _RecordingHttp(responses=[_503(), _503(), _503()])
    sleeps: list[float] = []
    pinner = IPFSPinner(
        api_key="k",
        api_secret="s",
        http_client=http,
        max_retries=3,
        backoff_base_s=2.0,
        sleep_fn=sleeps.append,
    )
    cid = pinner.pin_reflection(body="content")
    assert cid is None
    assert sleeps == [2.0, 4.0]


def test_urllib_http_exposes_post_method() -> None:
    """The production ``_UrllibHttp`` must expose a ``post`` method
    matching the ``_HttpClient`` Protocol shape — caught at type-check
    time but doubled here so a future refactor that breaks the
    Protocol surface fails a unit test instead of a downstream
    typecheck."""
    from agent.llm.ipfs_pinner import _UrllibHttp

    instance = _UrllibHttp()
    assert callable(instance.post)
    # Smoke-check the signature shape — _HttpClient.post is kw-only
    # with url / body / headers / timeout_s.
    import inspect

    sig = inspect.signature(_UrllibHttp.post)
    assert set(sig.parameters) >= {"self", "url", "body", "headers", "timeout_s"}
