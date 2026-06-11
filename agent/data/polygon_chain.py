# §-references mirror PRD / TECHNICAL_PLAN notation.
"""Polygon chain realtime adapter — CTF Exchange event subscriber.

Wires the α₃ Smart Money engine to live Polygon Amoy chain events per
PRD §7 + TECHNICAL_PLAN §4.8 / §8 Day 8. Subscribes to Polymarket
CTF Exchange log events via an injected ``eth_subscribe``-style
provider, intersects fill / position-open events with the smart-money
whitelist sourced from ``data/fixtures/smart_money_wallets.json``
(produced by T-E-002), and emits :class:`SmartMoneyPosition` frames.

The adapter exposes an **async context manager**::

    async with PolygonChainLive(subscription_provider=sub_factory) as feed:
        async for evt in feed.smart_money_positions("0xmarket"):
            if isinstance(evt, DegradedFeedWarning):
                ...
            # evt is a SmartMoneyPosition.

Hard rules enforced:

* ``available_at`` is the **wire-arrival** timestamp set when the
  decoded log was handed off from the subscription provider — NOT a
  block timestamp. Block timestamps would be a look-ahead violation
  if the chain reorgs and a finalised log later disappears.

* Gap-recovery: if the subscription emits a log whose ``block_number``
  is non-contiguous (a gap on the WS stream), the adapter yields a
  :class:`DegradedFeedWarning` so the agent loop can fall back to an
  ``eth_getLogs`` pull (in T-B-007's main loop body).

* Read-only by design — no signer, no wallet, no ``eth_sendTransaction``.
  The cross-chain auditor greps this module for write-side leakage.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.data._realtime_buffer import (
    Clock,
    DegradedFeedWarning,
    RealtimeBuffer,
    UtcClock,
)

# ----- Wire types -----


class SmartMoneyPosition(BaseModel):
    """One whitelisted-wallet fill on a Polymarket CTF Exchange market.

    Wire-stable. Emitted by :meth:`PolygonChainLive.smart_money_positions`
    AFTER intersecting the raw chain log with the smart-money whitelist
    (so non-whitelisted wallets never leak into the engine's input).

    ``available_at`` is the **wire-arrival** ISO-8601 UTC timestamp at
    which the decoded log was handed off — NOT the block timestamp.
    The look-ahead auditor enforces this convention.
    """

    model_config = ConfigDict(extra="forbid")

    market_id: str
    wallet: str
    side: str  # "YES" or "NO"
    size_usd: float = Field(ge=0.0)
    block_number: int = Field(ge=0)
    tx_hash: str
    log_index: int = Field(ge=0)
    available_at: str


# ----- Transport Protocols -----


class _ChainSubscription(Protocol):
    """Async iterator of decoded chain logs.

    The producer (production: ``web3.py`` ``eth_subscribe`` over
    websocket; tests: a fake that yields predetermined dict rows) is
    responsible for decoding the raw log into a plain ``dict[str, Any]``
    with keys ``address``, ``block_number``, ``tx_hash``, ``log_index``,
    ``topic0`` (event signature), ``decoded`` (event args dict).

    The adapter ITSELF stays decoder-agnostic — that's the price of
    keeping the Protocol thin so `eth-account` / `web3.py` are NOT
    imports of this module (read-only invariant).
    """

    def __aiter__(self) -> _ChainSubscription: ...
    async def __anext__(self) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...


class _SubscriptionProvider(Protocol):
    """Factory yielding a fresh :class:`_ChainSubscription`."""

    async def __call__(
        self, *, contract_address: str, topics: list[str]
    ) -> _ChainSubscription: ...


PolygonChainEvent = SmartMoneyPosition | DegradedFeedWarning


# ----- Defaults -----


# Polymarket CTF Exchange (Polygon mainnet). Amoy testnet uses a
# different address; production wires this from env in T-B-007.
DEFAULT_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
# Per the Polymarket CTF Exchange ABI: keccak("OrderFilled(...)")
# — we treat any matching topic0 as a fill event; the test fake feeds
# the topic verbatim.
DEFAULT_FILL_TOPIC = (
    "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
)

DEFAULT_WALLETS_PATH = Path("data/fixtures/smart_money_wallets.json")
_DEFAULT_MAX_RECONNECT = 5
_DEFAULT_BUFFER_MAXSIZE = 1024
_DEFAULT_WINDOW = 256
_DEFAULT_BACKOFF_BASE = 1.0
_DEFAULT_BACKOFF_CAP = 30.0


class PolygonChainLive:
    """Async-context-manager Polygon chain realtime feed for Smart Money.

    Production wiring (lands T-B-007) hooks an ``eth_subscribe`` WS
    provider. Tests inject a fake provider whose iterator yields
    predetermined decoded-log dicts.
    """

    def __init__(
        self,
        *,
        subscription_provider: _SubscriptionProvider,
        wallets_path: Path | str = DEFAULT_WALLETS_PATH,
        contract_address: str = DEFAULT_CTF_EXCHANGE,
        fill_topic: str = DEFAULT_FILL_TOPIC,
        clock: Clock | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_reconnect_attempts: int = _DEFAULT_MAX_RECONNECT,
        buffer_maxsize: int = _DEFAULT_BUFFER_MAXSIZE,
        window: int = _DEFAULT_WINDOW,
    ) -> None:
        if max_reconnect_attempts < 1:
            raise ValueError(
                f"max_reconnect_attempts must be ≥ 1 (got {max_reconnect_attempts})"
            )
        self._provider = subscription_provider
        self._wallets_path = Path(wallets_path)
        self._contract_address = contract_address
        self._fill_topic = fill_topic
        self._clock: Clock = clock if clock is not None else UtcClock()
        self._sleep = sleep
        self._max_attempts = max_reconnect_attempts
        self._buffer: RealtimeBuffer[SmartMoneyPosition] = RealtimeBuffer(
            maxsize=buffer_maxsize, window=window,
        )
        self._whitelist: frozenset[str] | None = None
        self._subscription: _ChainSubscription | None = None
        self._closed = False
        # Last seen block; used by the gap detector.
        self._last_block: int | None = None

    @property
    def buffer(self) -> RealtimeBuffer[SmartMoneyPosition]:
        """Exposed for windowed analytics (e.g. running net Σ_yes - Σ_no)."""
        return self._buffer

    async def __aenter__(self) -> PolygonChainLive:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Mark feed closed + best-effort drop the subscription."""
        self._closed = True
        sub = self._subscription
        self._subscription = None
        if sub is not None:
            try:
                await sub.aclose()
            except Exception:  # pragma: no cover — best-effort
                pass

    def _load_whitelist(self) -> frozenset[str]:
        """Lazy-load the smart-money wallet whitelist from disk."""
        if self._whitelist is None:
            raw_text = self._wallets_path.read_text(encoding="utf-8")
            raw = json.loads(raw_text)
            wallets_list: list[dict[str, Any]] = raw.get("wallets", [])
            # Canonicalise to lower-case for comparison.
            self._whitelist = frozenset(
                str(w["address"]).lower() for w in wallets_list if "address" in w
            )
        return self._whitelist

    async def smart_money_positions(
        self, market_id: str,
    ) -> AsyncIterator[PolygonChainEvent]:
        """Subscribe + yield Smart Money fills for ``market_id``.

        Yields :class:`SmartMoneyPosition` per whitelisted fill, and
        :class:`DegradedFeedWarning` on disconnect / block gap. The
        iterator terminates after ``max_reconnect_attempts`` consecutive
        failures or when :meth:`aclose` is called.
        """
        whitelist = self._load_whitelist()
        attempt = 0
        backoff = _DEFAULT_BACKOFF_BASE

        while not self._closed:
            sub: _ChainSubscription | None = None
            try:
                sub = await self._provider(
                    contract_address=self._contract_address,
                    topics=[self._fill_topic],
                )
                self._subscription = sub
                attempt = 0
                backoff = _DEFAULT_BACKOFF_BASE

                while not self._closed:
                    try:
                        log = await sub.__anext__()
                    except StopAsyncIteration:
                        # Subscription drained — reconnect.
                        attempt += 1
                        yield DegradedFeedWarning(
                            feed="polygon_chain",
                            reason="subscription_ended",
                            attempt=attempt,
                            available_at=self._clock.now().isoformat(),
                        )
                        break
                    except (ConnectionError, OSError, EOFError) as exc:
                        attempt += 1
                        yield DegradedFeedWarning(
                            feed="polygon_chain",
                            reason=f"recv_failed:{type(exc).__name__}",
                            attempt=attempt,
                            available_at=self._clock.now().isoformat(),
                        )
                        break

                    # ---- Wire-arrival timestamp captured HERE, BEFORE
                    # log parsing. Per the module-level look-ahead contract.
                    arrival = self._clock.now()

                    # Gap detection — emit a DegradedFeedWarning so the
                    # main loop can launch a backfill via eth_getLogs.
                    block_num = _coerce_int(log.get("block_number"), 0)
                    gap = self._detect_gap(block_num)
                    if gap is not None:
                        yield DegradedFeedWarning(
                            feed="polygon_chain",
                            reason=f"block_gap:{gap}",
                            attempt=1,
                            available_at=arrival.isoformat(),
                        )
                    self._last_block = block_num

                    position = self._project_log(
                        log=log,
                        market_id=market_id,
                        whitelist=whitelist,
                        arrival=arrival,
                    )
                    if position is None:
                        continue
                    await self._buffer.put(position)
                    yield position
                else:
                    return

                if self._closed:
                    return
                if attempt >= self._max_attempts:
                    return
                await self._sleep(backoff)
                backoff = min(backoff * 2.0, _DEFAULT_BACKOFF_CAP)

            except Exception as exc:
                attempt += 1
                yield DegradedFeedWarning(
                    feed="polygon_chain",
                    reason=f"subscribe_failed:{type(exc).__name__}",
                    attempt=attempt,
                    available_at=self._clock.now().isoformat(),
                )
                if self._closed or attempt >= self._max_attempts:
                    return
                await self._sleep(backoff)
                backoff = min(backoff * 2.0, _DEFAULT_BACKOFF_CAP)
            finally:
                if sub is not None:
                    try:
                        await sub.aclose()
                    except Exception:  # pragma: no cover — best-effort
                        pass
                self._subscription = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _detect_gap(self, block_num: int) -> int | None:
        """Return the gap size if ``block_num`` is non-contiguous, else None.

        First log on a fresh subscription has no predecessor and is
        accepted unconditionally. After that, a strictly-larger jump
        of size > 1 between consecutive logs is reported. Equal or
        descending blocks are NOT gaps (multiple events can land in
        one block; descending implies an out-of-order WS push the
        consumer handles separately).
        """
        if self._last_block is None or block_num == 0:
            return None
        delta = block_num - self._last_block
        if delta > 1:
            return delta
        return None

    def _project_log(
        self,
        *,
        log: dict[str, Any],
        market_id: str,
        whitelist: frozenset[str],
        arrival: datetime,
    ) -> SmartMoneyPosition | None:
        """Project one decoded chain log onto :class:`SmartMoneyPosition`.

        Returns ``None`` if:
        * the log's market_id doesn't match (other markets share the
          same exchange contract);
        * the trader wallet is not on the smart-money whitelist;
        * the log shape is malformed (missing required fields).

        The wallet is canonicalised to lower-case before whitelist
        lookup.
        """
        decoded = log.get("decoded")
        if not isinstance(decoded, dict):
            return None

        # The CTF Exchange OrderFilled event surfaces (taker, maker,
        # marketId, side, makerAmountFilled, takerAmountFilled, …).
        # Tests stub a minimal subset; we project defensively.
        log_market = str(decoded.get("market_id") or decoded.get("marketId") or "")
        if log_market != market_id:
            return None

        trader = str(decoded.get("trader") or decoded.get("taker") or "").lower()
        if not trader or trader not in whitelist:
            return None

        side_raw = str(decoded.get("side") or "").upper()
        if side_raw not in ("YES", "NO"):
            return None

        size_usd = _coerce_float(decoded.get("size_usd") or decoded.get("sizeUsd"))
        if size_usd is None or size_usd < 0.0:
            return None

        tx_hash = str(log.get("tx_hash") or log.get("transactionHash") or "")
        log_index = _coerce_int(log.get("log_index") or log.get("logIndex"), 0)
        block_number = _coerce_int(log.get("block_number") or log.get("blockNumber"), 0)

        return SmartMoneyPosition(
            market_id=market_id,
            wallet=trader,
            side=side_raw,
            size_usd=size_usd,
            block_number=block_number,
            tx_hash=tx_hash,
            log_index=log_index,
            available_at=arrival.isoformat(),
        )


# ----- Pure helpers -----


def _coerce_int(raw: Any, default: int = 0) -> int:
    """Defensive int decode — handles None, str, float."""
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _coerce_float(raw: Any) -> float | None:
    """Defensive float decode — returns None on malformed."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_CTF_EXCHANGE",
    "DEFAULT_FILL_TOPIC",
    "DEFAULT_WALLETS_PATH",
    "DegradedFeedWarning",
    "PolygonChainEvent",
    "PolygonChainLive",
    "SmartMoneyPosition",
]
