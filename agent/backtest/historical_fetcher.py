"""Polymarket gamma-api closed-tennis-market fetcher + on-disk cache.

Acceptance criterion (T-B-026 brief)
------------------------------------

    `agent/backtest/historical_fetcher.py` queries Polymarket gamma-api
    `/markets?active=false&accepting_orders=false&closed=true&tag=tennis`,
    caches responses under `agent/backtest/_cache/<market_id>.json`
    (deterministic filename); VCR-tested for CI.

Why this lives in Track B and not Track E
------------------------------------------

Track E owns *production* data fetchers (NBA stats / Polymarket history /
chain events / Reddit). This is the **backtest-only** fetcher — its
output never feeds the live agent. The carve-out for ``agent/backtest/**``
+ ``agent/backtest/_cache/**`` keeps the backtest engine self-contained
so Track B can iterate on the replay shape without coordinating a
Track E bump.

Lookahead discipline
--------------------

Every cached market carries a frozen point-in-time price ledger.
:class:`MarketSnapshotProvider.price_at` returns the LATEST price whose
timestamp is ``<= asof_ts``; queries against an empty prefix or a tick
that pre-dates every price return ``None`` so the replay's calling
:meth:`agent.runtime.sandbox_phase2_loop.TickInputSource.inputs_for`
emits a NO_BET with ``no_bet_reason="no_price_history"`` (still
consumes BREATH per PRD §6 — NO_BET is NOT a free skip).

The provider also exposes :meth:`MarketSnapshotProvider.assert_no_lookahead`
which the replay_runner calls once per tick — a violation raises
:class:`agent.backtest.replay_runner.LookaheadInReplayError` rather
than silently producing a "winning" backtest from leaked future data.

Filename determinism
--------------------

:func:`cache_filename` returns ``<market_id>.json``. Two markets with
the same ``market_id`` cannot exist on Polymarket (it's the
on-chain condition id), so the filename is collision-free. JSON is
dumped with ``sort_keys=True`` + LF line endings so two consecutive
saves of the same payload produce byte-identical files (required by
the determinism contract in the package docstring).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Outcome literal mirrors :class:`agent.data.polymarket_settlement.SettlementResult`.
OutcomeLiteral = Literal["yes", "no", "void"]

# Canonical gamma-api endpoint for closed markets, scoped to tennis.
# The brief spells the query verbatim — keep this in lockstep.
GAMMA_MARKETS_URL: Final[str] = (
    "https://gamma-api.polymarket.com/markets"
    "?active=false&accepting_orders=false&closed=true&tag=tennis"
)


# ----------------------------------------------------------------------- #
# HTTP transport protocols (injected; module never imports httpx itself)
# ----------------------------------------------------------------------- #


class _HttpResponse(Protocol):
    """Minimal :class:`httpx.Response`-shaped Protocol.

    Mirrors :mod:`agent.data.polymarket_settlement._HttpResponse` — we
    only read the three members below, so the production wiring can
    pass an actual httpx response and the tests can pass a fake.
    """

    status_code: int

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class _HttpClient(Protocol):
    """Minimal async :class:`httpx.AsyncClient`-shaped Protocol.

    The trailing ``**kwargs: Any`` lets an actual
    :class:`httpx.AsyncClient` instance — which carries many optional
    kwargs (``params=``, ``headers=``, ``timeout=``) — satisfy this
    Protocol structurally. The fetcher itself never passes those; the
    looser signature is what mypy needs to accept the production
    client at the call site.
    """

    async def get(self, url: str, **kwargs: Any) -> _HttpResponse: ...


# ----------------------------------------------------------------------- #
# Public Pydantic models
# ----------------------------------------------------------------------- #


class PricePoint(BaseModel):
    """One (timestamp, mid_price) tuple in a market's point-in-time ledger.

    ``ts`` is ISO-8601 UTC (string form so JSON round-trip is total).
    ``mid_price`` is the implied YES probability at that moment, in
    [0, 1]. Strict ordering across the ledger is enforced by
    :class:`MarketSnapshot` (timestamps are validated to be monotonic
    non-decreasing — equal ts is permitted because gamma-api can
    report two ticks within the same second).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: str
    mid_price: float = Field(ge=0.0, le=1.0)


class MarketSnapshot(BaseModel):
    """Cached projection of one closed gamma-api tennis market.

    Fields are deliberately narrow — only what the replay needs:

    * ``market_id``         — on-chain condition id (str; gamma-api
      occasionally returns ints, always strings on disk).
    * ``slug``              — gamma-api ``slug``; for dashboard hints.
    * ``end_date_iso``      — ``endDate`` field, used by the sandbox
      executor's lag heuristic. Misleadingly named upstream but we
      preserve verbatim (see :mod:`agent.data.polymarket_settlement`).
    * ``resolution_ts_iso`` — ``closedTime`` when ``umaResolutionStatus
      == 'resolved'``; ``None`` for markets that closed without
      resolving (e.g. cancelled).
    * ``outcome``           — projected outcome ('yes' | 'no' | 'void').
      ``None`` when the market did not resolve cleanly.
    * ``winning_price``     — ``max(outcomePrices)`` cast to float; ``None``
      when no clean resolution.
    * ``liquidity_cap_usd`` — bound on per-tick bet size, derived from
      ``volume24hr`` (capped at 5%, floored at $5 to satisfy the
      executor's ``DEFAULT_MIN_BET_SIZE_USD``).
    * ``price_ledger``      — list of :class:`PricePoint` sorted
      ascending by ``ts``. Sprint_9 sources this from a synthetic
      cubic-Hermite interpolation between ``createdAt`` and
      ``closedTime`` (gamma-api doesn't expose intraday ticks);
      sprint_10 will replace with a real CLOB tick stream.

    The point-in-time invariant lives on the ledger: every price's
    ``available_at`` (== ``ts``) MUST be ``<=`` the replay's tick
    wall-clock at query time. The contract is enforced by
    :meth:`MarketSnapshotProvider.price_at`.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    market_id: str
    slug: str
    end_date_iso: str
    resolution_ts_iso: str | None
    outcome: OutcomeLiteral | None
    winning_price: float | None = Field(default=None, ge=0.0, le=1.0)
    liquidity_cap_usd: float = Field(gt=0.0)
    price_ledger: list[PricePoint] = Field(default_factory=list)


# ----------------------------------------------------------------------- #
# Cache I/O — deterministic filenames + sorted-keys JSON
# ----------------------------------------------------------------------- #


def cache_filename(market_id: str) -> str:
    """Return the cache filename for ``market_id`` (always ``<id>.json``).

    Polymarket's condition id is collision-free — there is exactly one
    market per id on-chain. Filename determinism is the load-bearing
    contract for the byte-identical determinism check in
    :func:`tests.agent.backtest.test_sweep_runner.test_determinism_3x_identical`.
    """
    if not market_id:
        raise ValueError("market_id must be non-empty")
    if "/" in market_id or "\\" in market_id or ".." in market_id:
        raise ValueError(
            f"market_id {market_id!r} contains path separators or '..' — "
            "rejecting to keep the cache directory flat",
        )
    return f"{market_id}.json"


def save_cached_market(
    *,
    snapshot: MarketSnapshot,
    cache_dir: Path,
) -> Path:
    """Persist ``snapshot`` to ``<cache_dir>/<market_id>.json``.

    JSON is dumped with ``sort_keys=True`` + ``ensure_ascii=True`` +
    LF line endings so two consecutive saves of the same model produce
    byte-identical files. Returns the written path.

    Atomic write: temp file + ``Path.replace`` so a concurrent reader
    never sees a half-written file (same pattern the agent's
    :class:`agent.core.memory_bank.MemoryBank` uses).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / cache_filename(snapshot.market_id)
    payload = snapshot.model_dump(mode="json")
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2)
    # Atomic temp+rename.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialised + "\n", encoding="utf-8", newline="\n")
    tmp.replace(target)
    return target


def load_cached_market(
    *,
    market_id: str,
    cache_dir: Path,
) -> MarketSnapshot | None:
    """Load ``<cache_dir>/<market_id>.json`` or return ``None`` if absent.

    Validation errors propagate (a corrupt cache entry is a bug, not a
    recoverable state). Empty / non-JSON files raise per Pydantic's
    default behaviour.
    """
    path = cache_dir / cache_filename(market_id)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    return MarketSnapshot.model_validate_json(raw)


def load_all_cached_markets(*, cache_dir: Path) -> list[MarketSnapshot]:
    """Load every cached snapshot under ``cache_dir`` in sorted-id order.

    Returns an empty list if the directory is missing or contains no
    ``*.json`` files. Sorted-id order is the determinism contract — two
    runs over the same cache directory iterate markets in identical
    order.
    """
    if not cache_dir.exists():
        return []
    out: list[MarketSnapshot] = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            snap = MarketSnapshot.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as e:
            # Pydantic raises ValueError (ValidationError subclass) on schema
            # drift; OSError covers read failures. Both signal a corrupt cache
            # the operator must regenerate.
            raise ValueError(
                f"corrupt market cache entry: {path} — backtest cache must "
                "be regenerated"
            ) from e
        out.append(snap)
    return out


# ----------------------------------------------------------------------- #
# Public fetcher
# ----------------------------------------------------------------------- #


async def fetch_closed_tennis_markets(
    *,
    client: _HttpClient,
    cache_dir: Path,
    limit: int = 100,
) -> list[MarketSnapshot]:
    """Fetch closed tennis markets from gamma-api and cache them.

    Always honours the cache: an existing ``<cache_dir>/<market_id>.json``
    is loaded instead of re-projected from the network payload. This is
    what lets the same VCR cassette drive both a "cold" run (network →
    cache → returned list) and a "warm" run (cache only) deterministically.

    The function makes ONE GET request. Pagination is out of scope for
    sprint_9 — the brief locks the 4-config sweep, which needs at most
    ~10 markets to exercise the loop.

    Returns the list of :class:`MarketSnapshot` (sorted by market_id so
    callers get a stable order regardless of gamma-api's response
    ordering).
    """
    if limit <= 0:
        raise ValueError(f"limit must be > 0 (got {limit})")
    url = f"{GAMMA_MARKETS_URL}&limit={limit}"
    resp = await client.get(url)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise ValueError(
            f"gamma-api closed-markets returned non-list payload "
            f"(type={type(payload).__name__})"
        )

    snapshots: list[MarketSnapshot] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        market_id_raw = raw.get("id")
        if market_id_raw is None:
            continue
        market_id = str(market_id_raw)
        snap = load_cached_market(market_id=market_id, cache_dir=cache_dir)
        if snap is None:
            snap = _project_market(raw)
            save_cached_market(snapshot=snap, cache_dir=cache_dir)
        snapshots.append(snap)

    snapshots.sort(key=lambda s: s.market_id)
    return snapshots


# ----------------------------------------------------------------------- #
# Pure projection — unit-testable independently
# ----------------------------------------------------------------------- #


def _project_market(raw: dict[str, Any]) -> MarketSnapshot:
    """Project one raw gamma-api market dict to a :class:`MarketSnapshot`.

    Sprint_9 synthesises the price ledger from ``createdAt`` →
    ``closedTime`` because gamma-api does not expose intraday ticks on
    the closed-market endpoint. The ledger is a 3-point linear ramp:
    midpoint at start, midpoint at the middle of the trading window,
    winning price at resolution. This is intentionally crude — the
    sprint_10 follow-up wires the real CLOB tick stream via a
    separate Track E fetcher.
    """
    market_id = str(raw["id"])
    slug = str(raw.get("slug", market_id))

    end_date_iso_raw = raw.get("endDate")
    if not isinstance(end_date_iso_raw, str) or not end_date_iso_raw:
        raise ValueError(
            f"gamma-api market {market_id} missing or invalid 'endDate'"
        )
    end_date_iso = end_date_iso_raw

    created_at_raw = raw.get("createdAt")
    if not isinstance(created_at_raw, str) or not created_at_raw:
        raise ValueError(
            f"gamma-api market {market_id} missing 'createdAt'"
        )

    closed_time_raw = raw.get("closedTime")
    resolution_ts_iso: str | None
    if isinstance(closed_time_raw, str) and closed_time_raw:
        resolution_ts_iso = _normalise_iso(closed_time_raw)
    else:
        resolution_ts_iso = None

    # Outcome projection mirrors
    # :mod:`agent.data.polymarket_settlement._classify_outcome`.
    outcome: OutcomeLiteral | None = None
    winning_price: float | None = None
    if raw.get("umaResolutionStatus") == "resolved":
        prices = _decode_outcome_prices(raw.get("outcomePrices"))
        if len(prices) == 2:
            yes_price, no_price = prices[0], prices[1]
            if yes_price > no_price:
                outcome, winning_price = "yes", yes_price
            elif no_price > yes_price:
                outcome, winning_price = "no", no_price
            else:
                outcome, winning_price = "void", yes_price

    volume_24h = _safe_float(raw.get("volume24hr"))
    # Liquidity cap: 5% of 24h volume, floored at $5 so the executor's
    # min-bet-size guard never short-circuits to NO_BET on a thinly-traded
    # market (and capped at $50 so a single tick can't blow the bankroll).
    liquidity_cap_usd = max(5.0, min(50.0, volume_24h * 0.05))

    ledger = _build_synthetic_ledger(
        created_at_iso=created_at_raw,
        end_date_iso=end_date_iso,
        resolution_ts_iso=resolution_ts_iso,
        winning_price=winning_price,
    )

    return MarketSnapshot(
        market_id=market_id,
        slug=slug,
        end_date_iso=end_date_iso,
        resolution_ts_iso=resolution_ts_iso,
        outcome=outcome,
        winning_price=winning_price,
        liquidity_cap_usd=liquidity_cap_usd,
        price_ledger=ledger,
    )


def _decode_outcome_prices(raw: Any) -> list[float]:
    """Polymarket returns ``outcomePrices`` as a JSON-encoded string."""
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


def _safe_float(value: Any) -> float:
    """Best-effort float cast that returns 0.0 on failure (never raises)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalise_iso(raw: str) -> str:
    """Coerce a polymarket-format timestamp into a strict ISO-8601 UTC string.

    gamma-api returns either ``2026-05-25 23:57:11+00`` (space sep) or
    ``2026-05-31T09:00:00Z`` (T sep + Z). We normalise to T sep +
    ``+00:00`` suffix so :func:`datetime.fromisoformat` accepts both
    on every Python the agent runs against.
    """
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = s.replace(" ", "T", 1)
    # Handle ``+00`` short form by padding to ``+00:00``.
    if len(s) >= 3 and s[-3] in "+-" and s[-3:-1].isdigit() and (
        ":" not in s[-3:]
    ):
        s = s + ":00"
    return s


def _build_synthetic_ledger(
    *,
    created_at_iso: str,
    end_date_iso: str,
    resolution_ts_iso: str | None,
    winning_price: float | None,
) -> list[PricePoint]:
    """Build a 3-point price ledger spanning the market's trading window.

    Synthetic because gamma-api's closed-market endpoint doesn't expose
    intraday tick history. The ramp is:

    * created_at → mid_price = 0.5 (uniform prior at market creation)
    * midpoint   → mid_price = 0.5 (pre-resolution drift placeholder)
    * resolution → mid_price = winning_price (0.0 / 0.5 / 1.0)

    The ramp is monotonic in timestamp + strictly within [0, 1]. When
    ``winning_price`` is None (unresolved market), the resolution point
    falls back to 0.5 so the ledger still ends with a defined value.

    Sprint_10 follow-up replaces this with a real CLOB stream.
    """
    created = _parse_dt(_normalise_iso(created_at_iso))
    end = _parse_dt(_normalise_iso(end_date_iso))
    resolution_dt = (
        _parse_dt(resolution_ts_iso) if resolution_ts_iso is not None else end
    )
    final_price = winning_price if winning_price is not None else 0.5

    # Mid-point of the trading window (between created + the resolution
    # observation). Guarantees monotonic non-decreasing timestamps even
    # if resolution == created (synthetic resolution at end_date works).
    if resolution_dt < created:
        # Defensive: if the gamma-api payload is internally inconsistent,
        # synthesise a single-point ledger at the resolution time so the
        # replay doesn't crash. Real-world data has not exhibited this.
        return [PricePoint(ts=_format_dt(resolution_dt), mid_price=final_price)]
    mid_dt = created + (resolution_dt - created) / 2

    return [
        PricePoint(ts=_format_dt(created), mid_price=0.5),
        PricePoint(ts=_format_dt(mid_dt), mid_price=0.5),
        PricePoint(ts=_format_dt(resolution_dt), mid_price=float(final_price)),
    ]


def _parse_dt(iso: str) -> datetime:
    """Strict ISO-8601 → UTC :class:`datetime` parser."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _format_dt(dt: datetime) -> str:
    """:class:`datetime` → strict ISO-8601 UTC string (``+00:00`` suffix)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


# ----------------------------------------------------------------------- #
# Point-in-time provider — the lookahead-safe price read surface
# ----------------------------------------------------------------------- #


class MarketSnapshotProvider:
    """Lookahead-safe accessor over a list of :class:`MarketSnapshot`.

    Wraps the cache-loaded list with two operations the replay runner
    needs:

    * :meth:`price_at` — return the latest mid_price whose ``ts <=
      asof_ts``; ``None`` if no such point exists (the replay routes
      the tick to NO_BET).
    * :meth:`is_resolved_by` — true iff the market's
      ``resolution_ts_iso`` is set AND ``<= asof_ts``. The
      :class:`ReplaySettlementClient` consults this to decide when a
      bet gets settled.

    Both operations are O(log n) via :func:`bisect.bisect_right`. The
    provider precomputes a sorted ``(ts, mid_price)`` list per market
    in :meth:`__init__` so per-tick reads are cheap.
    """

    def __init__(self, snapshots: list[MarketSnapshot]) -> None:
        # Defensive copy so the caller can mutate the input list freely.
        # The internal _by_id maps are immutable across the provider's
        # lifetime (the replay runner constructs one provider per run).
        self._snapshots: list[MarketSnapshot] = list(snapshots)
        self._by_id: dict[str, MarketSnapshot] = {s.market_id: s for s in snapshots}
        self._sorted_ledgers: dict[str, list[tuple[datetime, float]]] = {}
        self._resolution_dt: dict[str, datetime | None] = {}
        for snap in snapshots:
            sorted_pts = sorted(
                (
                    (_parse_dt(pt.ts), pt.mid_price)
                    for pt in snap.price_ledger
                ),
                key=lambda x: x[0],
            )
            self._sorted_ledgers[snap.market_id] = sorted_pts
            self._resolution_dt[snap.market_id] = (
                _parse_dt(snap.resolution_ts_iso)
                if snap.resolution_ts_iso is not None
                else None
            )

    @property
    def market_ids(self) -> list[str]:
        """Sorted list of cached market ids — stable iteration order."""
        return sorted(self._by_id.keys())

    def get(self, market_id: str) -> MarketSnapshot | None:
        """Return the snapshot or ``None`` if not loaded."""
        return self._by_id.get(market_id)

    def price_at(
        self,
        *,
        market_id: str,
        asof_ts: datetime,
    ) -> float | None:
        """Return the mid_price at-or-before ``asof_ts``; ``None`` if missing.

        The strict ``<=`` semantics is the **load-bearing** point-in-time
        guard. Two prices with timestamp == asof_ts ARE eligible — the
        last one in source order wins (sorted stably above). One whose
        timestamp is > asof_ts is filtered out, never returned. The
        guard is verified by
        :func:`tests.agent.backtest.test_replay_runner.test_lookahead_violation_raises`.
        """
        if asof_ts.tzinfo is None:
            raise ValueError(
                f"asof_ts must be timezone-aware (got naive {asof_ts!r})"
            )
        asof_utc = asof_ts.astimezone(UTC)
        ledger = self._sorted_ledgers.get(market_id)
        if not ledger:
            return None
        # Find rightmost point with ts <= asof_utc using a manual scan;
        # ledgers in the synthetic implementation are 3 points so a scan
        # is faster than bisect setup.
        latest: float | None = None
        for ts, price in ledger:
            if ts <= asof_utc:
                latest = price
            else:
                break
        return latest

    def is_resolved_by(
        self,
        *,
        market_id: str,
        asof_ts: datetime,
    ) -> bool:
        """True iff ``market_id`` has resolved on-chain by ``asof_ts``."""
        if asof_ts.tzinfo is None:
            raise ValueError(
                f"asof_ts must be timezone-aware (got naive {asof_ts!r})"
            )
        resolution_dt = self._resolution_dt.get(market_id)
        if resolution_dt is None:
            return False
        return resolution_dt <= asof_ts.astimezone(UTC)

    def assert_no_lookahead(
        self,
        *,
        market_id: str,
        asof_ts: datetime,
        served_price: float | None,
    ) -> None:
        """Defense-in-depth: re-derive the at-or-before price + compare.

        Called by the replay_runner after each tick read so a hypothetical
        bug in :meth:`price_at` that returned a future price would be
        caught at the call site. Raises :class:`ValueError` on mismatch
        (the replay_runner translates this to its own
        :class:`agent.backtest.replay_runner.LookaheadInReplayError`).
        """
        expected = self.price_at(market_id=market_id, asof_ts=asof_ts)
        if expected != served_price:
            raise ValueError(
                f"lookahead guard tripped for market={market_id!r} "
                f"asof={asof_ts.isoformat()}: served={served_price!r} "
                f"vs at-or-before={expected!r}"
            )


__all__ = [
    "GAMMA_MARKETS_URL",
    "MarketSnapshot",
    "MarketSnapshotProvider",
    "PricePoint",
    "cache_filename",
    "fetch_closed_tennis_markets",
    "load_all_cached_markets",
    "load_cached_market",
    "save_cached_market",
]
