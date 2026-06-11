"""Bearer-token authentication for the FastAPI control plane — T-B-027.

Single-tenant, single-agent — sprint_9 deliberately avoids OAuth / JWT
complexity per TECHNICAL_PLAN §5.4 ("Auth: bearer token, 不做 OAuth").
The token is read from the ``DASHBOARD_API_TOKEN`` env var on every
request so the operator can rotate it by exporting a new value and
restarting the process; no in-memory caching to invalidate.

The dependency intentionally raises :class:`fastapi.HTTPException` with
the payload ``{"detail": "unauthorized"}`` (lowercase) per the brief —
both missing and wrong tokens collapse to the SAME response shape so an
attacker can't probe the env var presence by header omission. The 401
fires BEFORE route handler payload validation per FastAPI's dependency
resolution order; that ordering is the load-bearing reason this lives in
a dependency rather than a route-local check.
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

_TOKEN_ENV_VAR = "DASHBOARD_API_TOKEN"
"""Env var name carrying the operator's bearer token. Never hardcoded."""


def _read_configured_token() -> str | None:
    """Fetch the operator-configured token from the environment.

    Returns ``None`` when the env var is missing OR empty; that case
    short-circuits to 401 unconditionally so a forgotten env var never
    silently opens the API.
    """
    value = os.environ.get(_TOKEN_ENV_VAR)
    if value is None or not value.strip():
        return None
    return value


def _unauthorized() -> HTTPException:
    """Canonical 401 response shape per the brief.

    Centralised so future audits can grep for one call-site rather than
    chase ad-hoc dict literals.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
    )


def require_bearer_token(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """FastAPI dependency that enforces ``Authorization: Bearer <token>``.

    Behaviour
    ---------

    * ``DASHBOARD_API_TOKEN`` env var missing / empty → 401 always. A
      mis-configured deploy must FAIL CLOSED.
    * ``Authorization`` header absent → 401.
    * Header present but not ``Bearer <token>`` shape → 401.
    * Token mismatch → 401.
    * Match → return ``None`` (FastAPI treats that as "allow through").

    All four failure modes share ``{"detail": "unauthorized"}`` so the
    caller cannot distinguish "no env var" from "wrong token" from
    timing or response shape (constant-time compare on the token bytes
    via :func:`secrets.compare_digest`).
    """
    configured = _read_configured_token()
    if configured is None:
        raise _unauthorized()

    if authorization is None:
        raise _unauthorized()

    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise _unauthorized()

    # Constant-time compare so an attacker measuring response latency
    # cannot byte-by-byte recover the token.
    if not secrets.compare_digest(presented, configured):
        raise _unauthorized()


# Re-exported under a short alias so route signatures stay tidy:
#
#     async def my_route(_: AuthDep) -> ...:
AuthDep = Annotated[None, Depends(require_bearer_token)]


__all__ = [
    "AuthDep",
    "require_bearer_token",
]
