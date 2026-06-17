"""V1.2 — LIVE Polymarket tennis :class:`TickInputSource` (the paper-trading spine).

Swapped in (behind the V1.3 ``SANDBOX_LIVE`` mode switch) for the replay/idle tick
source so the agent paper-trades LIVE open Polymarket tennis markets on the 5
Sackmann/CLOB baseline signals (hold-to-resolution). It REUSES
:meth:`agent.backtest.real_signal_source.RealSignalSource.signals_for` verbatim with
``asof_ts=now`` — the backtest signal math is not reimplemented.

Design (grounded in ``docs/superpowers/plans/2026-06-17-stage2-mock-bet.md`` §V1.2):

* **The price-ledger rabbit hole.** ``signals_for``'s momentum slot reads a frozen
  retrospective ``price_ledger`` filtered ``ts <= asof_ts``. LIVE has no ledger at
  decision time, so this source maintains a mutable per-market rolling buffer
  (:class:`LiveLedgerProvider`) and appends ``(now, mid)`` each tick; the SAME provider
  backs the injected ``RealSignalSource`` so ``asof_ts=now`` includes the just-appended
  tick. Momentum is neutral until ≥2 ticks accrue (fine for the V1 baseline).

* **Side-orientation (Codex-7, MANDATORY).** Within ``RealSignalSource`` the 4 tennis
  facets + momentum are p1-oriented (positive favors the slug's p1), EXCEPT
  ``rest_recency`` (p2-oriented). ``DecisionEngine`` maps positive fused → the market YES
  token. resolver.p1 comes from the slug; there is NO guarantee p1 == the Gamma YES
  token. So this source feeds the ledger the **p1-side** mid (a single consistent frame),
  then applies ONE uniform flip to the YES frame at the boundary: if p1 is the NO side,
  negate ALL 5 scores. ``p1_is_yes`` is decided by matching the YES outcome LABEL to the
  slug's p1 surname (NOT slug order — Codex-r2-M2b). If the slug resolves to real Sackmann
  players but the YES label matches NEITHER, the market is **dropped fail-closed** (a real
  tennis signal that cannot be oriented must never silently invert). If the slug does not
  resolve at all (tennis facets neutral, momentum-only), it orients to the YES frame.

* **Identifier contract (Codex-r4-1).** Gamma ``id`` (== settlement key == ``market_id``),
  ``conditionId``, and ``clobTokenIds`` (YES/NO tokens) all come from ONE discovered market
  object, so the tick prices the YES TOKEN while the BetRecord/settlement key off the SAME
  ``market_id`` — a tick can never price one id and settle another.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from agent.backtest.historical_fetcher import PricePoint
from agent.backtest.tennis_match_resolver import (
    TennisMatchResolver,
    _norm_surname,
    parse_slug,
)
from agent.data.polymarket_sandbox_executor import MarketInfo
from agent.engines.base import Signal
from agent.runtime.sandbox_phase2_loop import TickInputs

logger = logging.getLogger(__name__)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_BASE_URL = "https://clob.polymarket.com"
TENNIS_TAG_SLUG = "tennis"
# Polymarket charges no explicit per-trade fee today; the half-spread crossing cost is
# the real haircut and is carried separately (``half_spread_frac``). Kept configurable so
# a future fee regime is a one-line change, not a code change.
DEFAULT_FEE_BPS = 0.0


# --------------------------------------------------------------------------- #
# Identifier bundle + price quote
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LiveTennisMarket:
    """One OPEN Polymarket tennis market + the full identifier bundle the LIVE tick
    and settlement need. ``market_id`` (Gamma ``id``) IS the settlement key."""

    market_id: str
    condition_id: str
    slug: str
    yes_token_id: str
    no_token_id: str
    yes_outcome_label: str
    no_outcome_label: str
    end_date_iso: str | None


@dataclass(frozen=True)
class LivePriceQuote:
    """A live CLOB quote for one token. ``half_spread_frac`` is half the bid/ask spread
    as a FRACTION of the mid (size-independent — ``_tick`` scales it by the staked size)."""

    mid: float
    half_spread_frac: float
    liquidity_usd: float


class LiveMarketDiscovery(Protocol):
    def discover(self) -> list[LiveTennisMarket]: ...


class LivePriceSource(Protocol):
    def quote(self, token_id: str) -> LivePriceQuote | None: ...


class _SignalSourceLike(Protocol):
    def signals_for(
        self, *, market_id: str, tick: int, asof_ts: datetime
    ) -> dict[str, Signal]: ...


# --------------------------------------------------------------------------- #
# gamma /events discovery (carries the token/outcome labels)
# --------------------------------------------------------------------------- #


def _parse_json_list(raw: object) -> list[str]:
    """Gamma encodes ``clobTokenIds`` / ``outcomes`` as JSON-string arrays; tolerate a
    native list too. Returns [] on anything malformed (caller drops the market)."""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    return []


def _parse_market(m: dict[str, object]) -> LiveTennisMarket | None:
    if m.get("closed") is True:
        return None
    market_id = m.get("id")
    condition_id = m.get("conditionId")
    slug = m.get("slug")
    tokens = _parse_json_list(m.get("clobTokenIds"))
    outcomes = _parse_json_list(m.get("outcomes"))
    if not (isinstance(market_id, (str, int)) and isinstance(condition_id, str)):
        return None
    if not isinstance(slug, str) or len(tokens) < 2 or len(outcomes) < 2:
        return None
    end_date = m.get("endDate")
    return LiveTennisMarket(
        market_id=str(market_id),
        condition_id=condition_id,
        slug=slug,
        yes_token_id=tokens[0],
        no_token_id=tokens[1],
        yes_outcome_label=outcomes[0],
        no_outcome_label=outcomes[1],
        end_date_iso=end_date if isinstance(end_date, str) else None,
    )


def parse_open_tennis_markets(payload: object) -> list[LiveTennisMarket]:
    """Project a gamma ``/events`` payload → open tennis markets WITH the identifier
    bundle. Robust to both ``[event, ...]`` and ``{"data": [event, ...]}`` shapes."""
    if isinstance(payload, list):
        events = [e for e in payload if isinstance(e, dict)]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        events = [e for e in payload["data"] if isinstance(e, dict)]
    else:
        events = []

    out: list[LiveTennisMarket] = []
    for ev in events:
        markets = ev.get("markets")
        if not isinstance(markets, list):
            continue
        for m in markets:
            if not isinstance(m, dict):
                continue
            lm = _parse_market(m)
            if lm is not None:
                out.append(lm)
    return out


@dataclass
class GammaLiveDiscovery:
    """Dedicated LIVE discovery via gamma ``/events?tag_slug=tennis&closed=false`` — the
    open-market endpoint (Codex-r2-M2a: does NOT mutate the PIT-historical
    ``list_tennis_markets``, which uses the wrong ``/markets`` endpoint). ``fetcher`` is
    injected (a fake in tests; a real urllib/httpx GET in prod) so this stays hermetic."""

    fetcher: Callable[[str], object]
    limit: int = 20
    tag_slug: str = TENNIS_TAG_SLUG

    def discover(self) -> list[LiveTennisMarket]:
        url = (
            f"{GAMMA_EVENTS_URL}?tag_slug={self.tag_slug}"
            f"&limit={self.limit}&active=true&closed=false"
        )
        try:
            payload = self.fetcher(url)
        except Exception as exc:  # network/parse failure → idle (no eligible market)
            logger.warning("live tennis discovery failed: %s", exc)
            return []
        return parse_open_tennis_markets(payload)


@dataclass
class ClobBookPriceSource:
    """LIVE CLOB price/liquidity from the order book (``/book?token_id=``). Best
    bid/ask → mid + half-spread fraction; top-of-book notional → a liquidity cap.
    ``fetcher`` injected (hermetic). Returns None when the book is empty/one-sided."""

    fetcher: Callable[[str], object]
    base_url: str = CLOB_BASE_URL

    def quote(self, token_id: str) -> LivePriceQuote | None:
        url = f"{self.base_url}/book?token_id={token_id}"
        try:
            payload = self.fetcher(url)
        except Exception as exc:
            logger.warning("clob book fetch failed for %s: %s", token_id, exc)
            return None
        if not isinstance(payload, dict):
            return None
        best_bid = _best_level(payload.get("bids"), want_max=True)
        best_ask = _best_level(payload.get("asks"), want_max=False)
        if best_bid is None or best_ask is None:
            return None
        bid_px, bid_sz = best_bid
        ask_px, ask_sz = best_ask
        mid = (bid_px + ask_px) / 2.0
        if mid <= 0.0:
            return None
        half_spread_frac = max(0.0, (ask_px - bid_px) / 2.0) / mid
        liquidity_usd = bid_px * bid_sz + ask_px * ask_sz
        return LivePriceQuote(
            mid=mid, half_spread_frac=half_spread_frac, liquidity_usd=liquidity_usd
        )


def _best_level(levels: object, *, want_max: bool) -> tuple[float, float] | None:
    """Best (price, size) from a CLOB book side. ``want_max`` → highest bid; else lowest
    ask. Tolerates string-encoded prices/sizes; skips malformed levels."""
    if not isinstance(levels, list):
        return None
    parsed: list[tuple[float, float]] = []
    for lv in levels:
        if not isinstance(lv, dict):
            continue
        try:
            px = float(lv["price"])
            sz = float(lv["size"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((px, sz))
    if not parsed:
        return None
    return max(parsed, key=lambda t: t[0]) if want_max else min(parsed, key=lambda t: t[0])


# --------------------------------------------------------------------------- #
# Rolling per-market price ledger (backs RealSignalSource's momentum slot)
# --------------------------------------------------------------------------- #


@dataclass
class _LiveSnap:
    """The minimal snapshot shape ``RealSignalSource`` reads: a slug (for the resolver)
    and a price_ledger (for momentum)."""

    slug: str
    price_ledger: list[PricePoint] = field(default_factory=list)


@dataclass
class LiveLedgerProvider:
    """A ``MarketSnapshotProvider``-shaped (``.get(market_id)``) live ledger. The LIVE
    tick appends each tick's (p1-side) mid; the injected ``RealSignalSource`` reads it
    via ``asof_ts <= now`` filtering. Single-writer (the loop is single-threaded)."""

    _snaps: dict[str, _LiveSnap] = field(default_factory=dict)

    def get(self, market_id: str) -> _LiveSnap | None:
        return self._snaps.get(market_id)

    def append(self, market_id: str, *, slug: str, ts: datetime, mid: float) -> None:
        snap = self._snaps.get(market_id)
        if snap is None:
            snap = _LiveSnap(slug=slug)
            self._snaps[market_id] = snap
        snap.price_ledger.append(PricePoint(ts=ts.isoformat(), mid_price=mid))


# --------------------------------------------------------------------------- #
# The LIVE TickInputSource
# --------------------------------------------------------------------------- #


@dataclass
class LiveTickInputSource:
    """LIVE :class:`agent.runtime.sandbox_phase2_loop.TickInputSource`."""

    discovery: LiveMarketDiscovery
    price_source: LivePriceSource
    signal_source: _SignalSourceLike
    resolver: TennisMatchResolver
    ledger: LiveLedgerProvider
    fee_bps: float = DEFAULT_FEE_BPS
    # Cache of every market seen across discoveries, so :meth:`market_resolver`
    # (the Executor's end-date lookup) can answer for any market the tick bet on.
    _known: dict[str, LiveTennisMarket] = field(default_factory=dict)

    def inputs_for(self, *, asof_ts: datetime, tick: int) -> TickInputs | None:
        # Filter to ORIENTABLE markets first (cheap, no network): a resolved-but-
        # unorientable market is dropped fail-closed here (Codex-7), never silently
        # inverted. ``orient_yes`` True ⇒ feed the YES mid + no flip; False ⇒ feed the
        # NO mid (the p1 side) + a uniform YES-frame flip.
        usable: list[tuple[LiveTennisMarket, bool]] = []
        for m in self.discovery.discover():
            self._known[m.market_id] = m  # cache for market_resolver (end-date lookup)
            orient_yes = self._orient(m)
            if orient_yes is not None:
                usable.append((m, orient_yes))
        if not usable:
            return None

        m, orient_yes = usable[tick % len(usable)]
        quote = self.price_source.quote(m.yes_token_id)
        if quote is None:
            return None
        yes_mid = quote.mid

        # Feed the rolling ledger the p1-side mid so the WHOLE signal vector is
        # p1-oriented (matches backtest sign-for-sign), then flip to the YES frame.
        ledger_mid = yes_mid if orient_yes else 1.0 - yes_mid
        self.ledger.append(m.market_id, slug=m.slug, ts=asof_ts, mid=ledger_mid)

        signals = self.signal_source.signals_for(
            market_id=m.market_id, tick=tick, asof_ts=asof_ts
        )
        if not orient_yes:
            # Uniform p1→YES frame swap: negate every slot's score (a frame change
            # preserves each slot's internal convention, incl. rest_recency).
            signals = {
                slot: sig.model_copy(update={"score": -sig.score})
                for slot, sig in signals.items()
            }

        return TickInputs(
            market_id=m.market_id,
            signals=signals,
            price=yes_mid,                       # the loop bets the YES token at this mid
            liquidity_cap_usd=quote.liquidity_usd,
            fill_price=yes_mid,                  # paper taker entry at the mid
            fee_bps=self.fee_bps,
            half_spread_frac=quote.half_spread_frac,
        )

    def market_resolver(self, market_id: str) -> MarketInfo | None:
        """The Executor's end-date lookup for the LIVE path: return the discovered
        market's ``end_date_iso`` (so ``place_order`` can derive
        ``expected_settle_ts``). ``None`` for a market this source never discovered —
        the executor then refuses the order (``UnknownMarketError``), which is correct
        fail-closed behaviour for a market we cannot date."""
        m = self._known.get(market_id)
        if m is None:
            return None
        return MarketInfo(end_date_iso=m.end_date_iso)

    def _orient(self, m: LiveTennisMarket) -> bool | None:
        """Resolve the p1↔YES orientation. Returns:

        * ``True``  — feed the YES mid, NO flip (p1 == YES, OR the slug does not resolve
          to Sackmann players so only momentum is real and is oriented to the YES token);
        * ``False`` — feed the NO mid (the p1 side), then flip ALL scores (p1 == NO);
        * ``None``  — the slug DOES resolve but the YES label matches neither player →
          fail-closed: drop the market (never silently invert a real tennis signal).
        """
        parsed = parse_slug(m.slug)
        if parsed is None or self.resolver.resolve(m.slug) is None:
            return True  # momentum-only (tennis facets neutral) → YES frame, no flip
        if not m.yes_outcome_label:
            return None
        yes_surname = _norm_surname(m.yes_outcome_label.split()[-1])
        if yes_surname == parsed.p1_surname:
            return True
        if yes_surname == parsed.p2_surname:
            return False
        return None


__all__ = [
    "CLOB_BASE_URL",
    "DEFAULT_FEE_BPS",
    "GAMMA_EVENTS_URL",
    "ClobBookPriceSource",
    "GammaLiveDiscovery",
    "LiveLedgerProvider",
    "LiveMarketDiscovery",
    "LivePriceQuote",
    "LivePriceSource",
    "LiveTennisMarket",
    "LiveTickInputSource",
    "parse_open_tennis_markets",
]
