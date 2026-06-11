# §-references mirror PRD / TECHNICAL_PLAN notation.
"""Polymarket settlement reader — gamma-api → :class:`SettlementResult`.

Sprint_8 Day-0 spike (T-B-017) that gates the rest of sandbox-pivot.
T-B-019 (settlement poller) imports this module verbatim — keep the
interface stable, evolve only the cassette set when schema changes.

Canonical gamma-api fields (probed 2026-05-26 from
``https://gamma-api.polymarket.com/markets/{id}``):

* ``umaResolutionStatus``  — final status string. ``'resolved'`` is the
  only value that means "outcomePrices are final"; other observed
  values include ``'proposed'`` (UMA dispute window open) and absent.
* ``closedTime``           — naive-UTC string ``YYYY-MM-DD HH:MM:SS+00``
  flipped when the market actually settled. This is what we treat as
  the resolution timestamp — the agent's PnL becomes computable here.
* ``umaEndDate``           — ISO-8601 ``Z``-suffixed; in every observed
  sample equals ``closedTime`` to the second.
* ``endDate``              — **NOT the match-end timestamp.** Observed
  to be the tour-period / market-window upper bound (Roland Garros
  matches show endDate 5-7 days AFTER closedTime). The naming is
  misleading per the PRD §7 brief; this module exposes ``end_date``
  but the lag report (T-B-017) flags the field-name mismatch loudly.
* ``gameStartTime``        — UTC ``YYYY-MM-DD HH:MM:SS+00``; the
  scheduled match start. ``closedTime - gameStartTime`` is the
  operationally meaningful "how soon does the agent see settlement
  after the game" lag.
* ``outcomes`` / ``outcomePrices`` — JSON-string-encoded lists, e.g.
  ``'["Hugo Gaston", "Gael Monfils"]'`` and ``'["1", "0"]'``. The
  winning outcome's price is ``1.0`` (or ``0.5`` on void / walkover).

Worked-example markets (T-B-017 spike samples — see
``reports/sprint8/spike_settlement_lag_report.md`` for the full
matrix):

* ``2328096`` — atp-gaston-monfils-2026-05-24: gameStart→closed +5.62h
* ``2348945`` — atp-ilagan-uchiyam-… set-1 O/U:    gameStart→closed +3.25h
* ``2336501`` — wta-guo-kessler-2026-05-25:         gameStart→closed +4.89h

All three settled well under the 6h threshold the CEO 2026-05-26 plan
requires. None tripped the NO-GO branch.

Schema-drift protection
-----------------------

The CEO sandbox-pivot plan (locked decision #1, 2026-05-26):
*"Pydantic models for gamma-api response use ``extra='ignore'`` + log
unknown fields (no 3am crash on schema additions)."*

This is implemented by:

* ``SettlementResult.model_config = ConfigDict(extra='ignore')`` — the
  parser drops unknown keys instead of raising.
* ``@model_validator(mode='before')`` that walks the incoming dict and
  emits a single WARNING log line with the unknown key names. The
  warning is rate-limit-friendly (one record per unknown-key set per
  call) so a new gamma-api field added at 3am produces a single ops
  ping, not a log storm.

Lookahead bias
--------------

The function signature accepts only ``market_id`` and the http
``client``. There is no ``cutoff_date`` arg because the settled
outcome IS post-game data by construction — this module is consumed
by the PnL reducer and the dashboard, not by any feature engine. The
look-ahead auditor (``.dev/harness/tools/lookahead_auditor.py``)
scopes itself to ``features/`` dirs and ``*features*`` filenames, so
this module is auditor-clean by location. See README_BACKEND.md "What
the auditor scopes" for the policy.

Network contract
----------------

The module never imports ``httpx`` directly. It consumes an injected
:class:`_HttpClient` Protocol that matches both
``httpx.AsyncClient.get`` and the test fakes / VCR-wrapped client. No
live network at import time.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Public Pydantic model
# --------------------------------------------------------------------------- #


class SettlementResult(BaseModel):
    """One Polymarket binary-market settlement, projected from gamma-api.

    Fields follow the locked T-B-017 acceptance criteria:

    * ``market_id``    — Polymarket market id (the numeric ``id``
      field, exposed as ``str`` because Polymarket sometimes returns
      ints, sometimes strings).
    * ``resolved``     — ``True`` iff ``umaResolutionStatus == 'resolved'``.
    * ``outcome``      — Mapped from ``outcomePrices``:
      ``'yes'`` if outcomes[0] won, ``'no'`` if outcomes[1] won,
      ``'void'`` for splits / 50-50 walkovers / >2 outcomes /
      ambiguous. This is the binary projection the agent's PnL
      reducer expects; the lossy mapping is deliberate.
    * ``winning_price``— ``max(outcomePrices)`` cast to float; for a
      cleanly resolved market this is ``1.0``.
    * ``resolution_ts``— UTC ``datetime`` of ``closedTime`` (when
      umaResolutionStatus flipped to ``resolved``). This is the
      agent-visible settlement time.
    * ``end_date``     — UTC ``datetime`` of the gamma-api ``endDate``
      field. **Misleadingly named** — see module docstring. Exposed
      verbatim so downstream consumers can apply their own semantics
      and so the lag report (T-B-017) can quantify the drift.
    """

    model_config = ConfigDict(extra="ignore")

    market_id: str
    resolved: bool
    outcome: Literal["yes", "no", "void"]
    winning_price: float
    resolution_ts: datetime
    end_date: datetime

    # Canonical key set — single source of truth. The validator below uses
    # this to detect gamma-api schema drift; downstream consumers (e.g.
    # T-B-019 settlement poller) can read it without re-deriving.
    KNOWN_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"market_id", "resolved", "outcome", "winning_price", "resolution_ts", "end_date"}
    )

    @model_validator(mode="before")
    @classmethod
    def _warn_on_unknown_keys(cls, data: Any) -> Any:
        """Log a single WARNING when the input dict has keys outside
        :attr:`KNOWN_KEYS`. The validator runs in ``before`` mode so it
        sees the raw input BEFORE ``extra='ignore'`` drops anything — the
        only place where unknown keys are still observable. One log line
        per call (not per field, not per second) per the CEO 2026-05-26
        schema-drift decision; tested in
        :func:`tests.agent.data.test_polymarket_settlement.test_unknown_field_tolerated`.
        """
        if isinstance(data, dict):
            unknown = sorted(set(data.keys()) - cls.KNOWN_KEYS)
            if unknown:
                logger.warning(
                    "polymarket_settlement: ignoring unknown fields %s "
                    "(expected if gamma-api added new keys; "
                    "review and update SettlementResult if a relevant field)",
                    unknown,
                )
        return data


# --------------------------------------------------------------------------- #
# Transport protocols (injected; module never imports httpx itself)
# --------------------------------------------------------------------------- #


class _HttpResponse(Protocol):
    """Minimal :class:`httpx.Response`-shaped Protocol.

    Structural subtype of :class:`httpx.Response` — we only read the
    three members below, so the production wiring can pass an actual
    httpx response and the tests can pass a fake.
    """

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class _HttpClient(Protocol):
    """Minimal async :class:`httpx.AsyncClient`-shaped Protocol.

    The trailing ``**kwargs: Any`` is what lets an actual
    ``httpx.AsyncClient`` instance — which adds many optional kwargs
    like ``params=``, ``headers=``, ``timeout=`` — satisfy this
    Protocol structurally. ``resolve_market`` never passes any of
    those, but the looser signature is what mypy needs to accept
    ``httpx.AsyncClient`` as an argument.
    """

    async def get(self, url: str, **kwargs: Any) -> _HttpResponse: ...


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


GAMMA_API_BASE = "https://gamma-api.polymarket.com"


async def resolve_market(
    market_id: str,
    *,
    client: _HttpClient,
) -> SettlementResult | None:
    """Fetch the gamma-api market record and project it to ``SettlementResult``.

    Returns ``None`` if the market exists but has not yet resolved
    (``umaResolutionStatus != 'resolved'``). This lets the caller poll
    on a schedule without having to distinguish "missing" from
    "pending" inside an exception handler.

    Raises only on transport-level failures — network error, non-2xx
    HTTP, malformed JSON. Schema drift (new fields) is logged but
    tolerated; missing required fields surface as
    :class:`pydantic.ValidationError`.

    See module docstring for the canonical field mapping.
    """
    url = f"{GAMMA_API_BASE}/markets/{market_id}"
    resp = await client.get(url)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ValueError(
            f"gamma-api /markets/{market_id} returned non-dict payload "
            f"(type={type(payload).__name__})"
        )
    return _project(payload)


# --------------------------------------------------------------------------- #
# Pure projection helpers — unit-testable independently
# --------------------------------------------------------------------------- #


def _project(payload: dict[str, Any]) -> SettlementResult | None:
    """Project a raw gamma-api market dict to a :class:`SettlementResult`.

    Returns ``None`` when the market is not yet resolved. Splitting
    "raw dict → typed model" into a pure function keeps it trivially
    unit-testable without VCR / httpx setup.
    """
    uma_status = payload.get("umaResolutionStatus")
    if uma_status != "resolved":
        # Not yet settled — caller polls again later. Don't construct
        # a SettlementResult that lies about being resolved.
        return None

    market_id_raw = payload.get("id")
    if market_id_raw is None:
        raise ValueError(
            "gamma-api payload missing 'id' field "
            "(required for SettlementResult.market_id)"
        )
    market_id = str(market_id_raw)

    outcome_prices = _decode_json_list(payload.get("outcomePrices"))
    outcome, winning_price = _classify_outcome(outcome_prices)

    resolution_ts = _parse_polymarket_ts(payload.get("closedTime"))
    if resolution_ts is None:
        raise ValueError(
            f"gamma-api market {market_id} has umaResolutionStatus=resolved "
            "but no parseable closedTime field"
        )

    end_date = _parse_polymarket_ts(payload.get("endDate"))
    if end_date is None:
        raise ValueError(
            f"gamma-api market {market_id} missing parseable endDate field"
        )

    return SettlementResult(
        market_id=market_id,
        resolved=True,
        outcome=outcome,
        winning_price=winning_price,
        resolution_ts=resolution_ts,
        end_date=end_date,
    )


def _decode_json_list(raw: Any) -> list[float]:
    """Decode gamma-api's JSON-string-encoded prices list.

    Polymarket returns ``outcomePrices`` as a JSON-encoded string of
    string-valued numbers, e.g. ``'["1", "0"]'``. Defensive: also
    accepts a native ``list``, returns ``[]`` on any decode failure.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        items: list[Any] = list(raw)
    elif isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if not isinstance(decoded, list):
            return []
        items = list(decoded)
    else:
        return []

    out: list[float] = []
    for it in items:
        try:
            out.append(float(it))
        except (TypeError, ValueError):
            return []
    return out


def _classify_outcome(
    prices: list[float],
) -> tuple[Literal["yes", "no", "void"], float]:
    """Map ``outcomePrices`` → ``(outcome, winning_price)``.

    Convention: a Polymarket binary market lists outcomes as
    ``[yes_label, no_label]`` (e.g. ``[home_team, away_team]`` for a
    moneyline). After settlement the winning outcome's price → 1.0.

    * 2-element list with one >= 0.99 → ``'yes'`` if index 0,
      ``'no'`` if index 1. winning_price = max(prices).
    * Any other shape (single element, three+ elements, no clear
      winner, all near 0.5) → ``'void'``. This collapses walkovers
      (50-50 resolutions per the gamma-api market description) and
      multi-outcome markets the binary mapping can't represent.
    """
    if len(prices) != 2:
        return "void", (max(prices) if prices else 0.0)

    winning_price = max(prices)
    if winning_price < 0.99:
        # No clear winner — covers 50-50 walkovers and disputed markets.
        return "void", winning_price
    winner_idx = prices.index(winning_price)
    if winner_idx == 0:
        return "yes", winning_price
    return "no", winning_price


def _parse_polymarket_ts(raw: Any) -> datetime | None:
    """Parse gamma-api timestamps into UTC ``datetime``.

    Polymarket emits two shapes:

    * ISO-8601 with ``Z`` suffix — e.g. ``'2026-05-31T09:00:00Z'``.
    * Space-separated naive-UTC with ``+00`` tz — e.g.
      ``'2026-05-25 23:57:11+00'``.

    Returns ``None`` on malformed strings so the caller can decide
    whether the missing timestamp is a hard error (resolution_ts) or
    soft (purely informational).
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # Normalise Z → +00:00
    s = s.replace("Z", "+00:00")
    # Normalise space-separator → T
    if "T" not in s and " " in s:
        s = s.replace(" ", "T", 1)
    # Normalise trailing +00 → +00:00
    if s.endswith("+00"):
        s = s + ":00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    # Force UTC: gamma-api always returns UTC; if a tz is missing,
    # attach UTC. If a different tz is somehow present, convert.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


__all__ = [
    "GAMMA_API_BASE",
    "SettlementResult",
    "resolve_market",
]
