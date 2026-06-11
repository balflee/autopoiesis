"""IPFSPinner — Pinata REST wrapper for reflection markdown pinning.

The Tombstone NFT's ``memoryBankCid`` and per-tick on-chain reflection
hash both need an IPFS CID. The free-tier Pinata account configured in
``SETUP_CHECKLIST.md §P1`` (1 GB storage / 1000 pins) is plenty for the
hackathon run; this wrapper is the single import surface the agent_loop
uses.

Brief invariant (TECHNICAL_PLAN §4.6 'agent must survive'):

    :meth:`IPFSPinner.pin_reflection` MUST return ``None`` (not raise)
    when Pinata returns HTTP 503 three times in a row. The caller —
    the agent_loop — interprets ``None`` as a transient outage, logs
    a warning, persists the reflection markdown to disk anyway, and
    continues the tick. A Pinata outage MUST NOT crash the agent.

Test discipline:

* No real HTTP call under pytest. Tests inject a Protocol-conformant
  :class:`_HttpClient` fake that returns scripted responses.
* The ``api_key`` / ``api_secret`` are read **lazily** from
  ``os.environ['PINATA_API_KEY']`` / ``os.environ['PINATA_SECRET_KEY']``
  inside the first :meth:`pin_reflection` call — so importing the
  module on a dev box without the keys does not raise.
* The HTTP layer is abstracted behind :class:`_HttpClient` (Protocol
  with a single ``post`` method). Production uses :class:`_RequestsHttp`
  which delegates to ``urllib`` — keeping the dependency at zero so
  the hackathon wheel stays small. Tests inject :class:`_RecordingHttp`
  from the test module.

Authentication
--------------

Pinata's REST API accepts the legacy ``pinata_api_key`` /
``pinata_secret_api_key`` headers OR a JWT. We use the legacy headers
to match ``.env.example``'s ``PINATA_API_KEY`` / ``PINATA_SECRET_KEY``
fields. Switching to JWT later is a one-line change.
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol

PINATA_PIN_FILE_URL: Final[str] = "https://api.pinata.cloud/pinning/pinFileToIPFS"
_PINATA_API_KEY_ENV: Final[str] = "PINATA_API_KEY"
_PINATA_SECRET_KEY_ENV: Final[str] = "PINATA_SECRET_KEY"

# Brief acceptance criterion: "ipfs_pinner.pin_reflection() returns None
# on Pinata 503-after-3-retries". Cap is exposed so tests can shrink
# it to keep test runtime tight without touching production behaviour.
DEFAULT_MAX_RETRIES: Final[int] = 3


@dataclass(frozen=True)
class HttpResponse:
    """Structured HTTP response — what :class:`_HttpClient` returns.

    Frozen so tests can assert against it without worrying about
    mutation. The Pinata "pinFileToIPFS" endpoint returns a JSON body
    with an ``IpfsHash`` field on success; the wrapper parses that
    field into the CIDv1 string returned by :meth:`IPFSPinner.pin_reflection`.
    """

    status: int
    body: bytes


class _HttpClient(Protocol):
    """Narrow HTTP interface — kept SDK-agnostic so the test fake does
    not depend on ``urllib`` semantics.

    The single method posts a multipart-encoded body to a URL with the
    supplied headers and returns a :class:`HttpResponse`. The header
    dict supplies the Pinata auth + content-type. The wrapper does NOT
    follow redirects — a 503 is a 503 is a 503.
    """

    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_s: float,
    ) -> HttpResponse: ...


@dataclass
class _UrllibHttp:
    """Production :class:`_HttpClient` — delegates to ``urllib.request``.

    Wrapped in this thin class so the test fake has a clean Protocol
    target. No retry / backoff here — that policy lives in
    :class:`IPFSPinner` so it is tested in one place.
    """

    def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_s: float,
    ) -> HttpResponse:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status = int(resp.status)
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            # Pinata's 503 lands here; we surface it as a structured
            # response rather than re-raising so the retry loop in
            # :class:`IPFSPinner` can branch deterministically.
            status = int(exc.code)
            payload = exc.read() if hasattr(exc, "read") else b""

        return HttpResponse(status=status, body=payload)


@dataclass
class IPFSPinner:
    """Pinata REST wrapper for the per-tick reflection markdown blob.

    Parameters
    ----------
    api_key:
        Pinata REST API key. Defaults to ``None``; in that case the
        env var ``PINATA_API_KEY`` is read **lazily** on the first
        :meth:`pin_reflection` call. Tests inject explicit non-empty
        strings to keep the env clean.

    api_secret:
        Companion secret. Same lazy-env-read behaviour as ``api_key``.

    http_client:
        Protocol-conformant HTTP client. Defaults to :class:`_UrllibHttp`
        — production. Tests inject a recording fake.

    max_retries:
        How many times to retry on Pinata 5xx before giving up and
        returning ``None``. Defaults to :data:`DEFAULT_MAX_RETRIES` (3).
        Connection errors count as a 5xx for the retry loop.

    timeout_s:
        Per-request timeout in seconds. Defaults to 30 — Pinata's free
        tier is reliable but occasionally slow under load.

    backoff_base_s:
        Base for exponential backoff between retries: attempt N sleeps
        ``backoff_base_s * 2**N`` seconds before retrying. Defaults to
        1.0 so a transient 503 / 429 doesn't hammer a struggling
        server. Tests inject ``0.0`` (and a fake sleeper) to keep the
        test suite fast.

    sleep_fn:
        Sleep callback. Defaults to :func:`time.sleep` so production
        behaves the obvious way; tests inject a no-op or a recording
        fake to keep ``test_pin_reflection_retries_then_succeeds``
        deterministic.
    """

    api_key: str | None = None
    api_secret: str | None = None
    http_client: _HttpClient = field(default_factory=_UrllibHttp)
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_s: float = 30.0
    backoff_base_s: float = 1.0
    sleep_fn: Callable[[float], None] = time.sleep

    def pin_reflection(
        self,
        *,
        body: str,
        filename: str = "reflection.md",
    ) -> str | None:
        """Pin ``body`` to Pinata IPFS and return the CIDv1 string.

        Returns ``None`` if Pinata returned 503 (or a connection error)
        for :attr:`max_retries` consecutive attempts. The caller — the
        agent_loop — interprets ``None`` as 'pin failed but tick
        continues' per the TECHNICAL_PLAN §4.6 survival invariant.

        Parameters
        ----------
        body:
            UTF-8 markdown payload (typically the reflection
            :class:`agent.engines.reflection.ReflectionRecord.body`).

        filename:
            Filename Pinata stores in the pin record. Defaults to
            ``reflection.md``. Pinata uses this for its dashboard
            display only; the IPFS CID is computed from ``body``
            content alone.
        """
        api_key = self.api_key or os.environ.get(_PINATA_API_KEY_ENV)
        api_secret = self.api_secret or os.environ.get(_PINATA_SECRET_KEY_ENV)
        if not api_key or not api_secret:
            # An operator misconfiguration; surface clearly. The
            # agent_loop treats this as a non-fatal warning and skips
            # the pin (None) — same survival semantics as a 503.
            return None

        boundary = "----autopoiesis-pin-boundary"
        multipart_body = _build_multipart(
            body=body.encode("utf-8"),
            filename=filename,
            boundary=boundary,
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "pinata_api_key": api_key,
            "pinata_secret_api_key": api_secret,
        }

        for attempt in range(self.max_retries):
            try:
                resp = self.http_client.post(
                    url=PINATA_PIN_FILE_URL,
                    body=multipart_body,
                    headers=headers,
                    timeout_s=self.timeout_s,
                )
            except (OSError, urllib.error.URLError):
                # Connection failure — count as a retryable failure so
                # transient network blips do not silently fail the pin.
                # If this is the last attempt the loop exits and we
                # return None below.
                self._backoff(attempt)
                continue

            if 200 <= resp.status < 300:
                return _extract_cid(resp.body)

            # 5xx (server error) and 429 (rate-limited) share the retry
            # budget. Pinata's docs treat both as transient; the only
            # behavioural difference would be honouring a
            # ``Retry-After`` header on 429, which Pinata does not
            # currently emit on the free tier.
            if resp.status >= 500 or resp.status == 429:
                self._backoff(attempt)
                continue

            # 4xx (other than 429) is non-retryable — typically a
            # bad-auth or invalid-payload error from the operator's
            # side. Surface as None so the tick continues; the
            # agent_loop emits a structured event with the status code
            # so the operator can fix the misconfiguration.
            return None

        return None

    def _backoff(self, attempt: int) -> None:
        """Sleep before the next retry. No-op on the last attempt so
        the loop exits without an unnecessary wall-clock delay."""
        if attempt + 1 >= self.max_retries:
            return
        self.sleep_fn(self.backoff_base_s * (2**attempt))


# ---------------------------------------------------------------------------
# Helpers — module-level so tests can exercise the multipart + CID-extract
# paths without spinning up an :class:`IPFSPinner`.
# ---------------------------------------------------------------------------


def _build_multipart(*, body: bytes, filename: str, boundary: str) -> bytes:
    """Build a minimal multipart/form-data payload for Pinata's
    ``pinFileToIPFS`` endpoint.

    The function is module-level (not a method) so tests can call it
    directly. Only the ``file`` field is set — Pinata accepts a bare
    file with no ``pinataMetadata`` / ``pinataOptions`` fields.
    """
    buf = io.BytesIO()
    buf.write(f"--{boundary}\r\n".encode())
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    buf.write(b"Content-Type: text/markdown\r\n\r\n")
    buf.write(body)
    buf.write(f"\r\n--{boundary}--\r\n".encode())
    return buf.getvalue()


def _extract_cid(payload: bytes) -> str | None:
    """Parse Pinata's JSON response and return the ``IpfsHash`` field.

    Returns ``None`` if the response is not valid JSON or is missing
    the ``IpfsHash`` field — surfaces as a soft-fail pin from
    :meth:`IPFSPinner.pin_reflection`.
    """
    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    cid = body.get("IpfsHash")
    if not isinstance(cid, str) or not cid:
        return None
    return cid


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "PINATA_PIN_FILE_URL",
    "HttpResponse",
    "IPFSPinner",
]
