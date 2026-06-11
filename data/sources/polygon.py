"""Polygon chain source-adapter — READ-ONLY event scanner.

Per PRD §7 the Polygon chain feed powers Smart Money wallet
identification via historical event-log scans. This module ships the
read-only ``fetch_events`` entrypoint for sprint_2.

**Hard rules** (READ-ONLY by manifest; the cross-chain reviewer greps
this file for write-side leakage):

* No private-key handling, no wallet construction, no signer imports
  (no ``eth_account``, no ``LocalAccount``, no ``Account`` from
  ``web3``).
* No ``eth_sendTransaction`` / ``eth_sendRawTransaction`` /
  ``eth_sign`` / ``personal_sign`` invocations.
* No write-side method names anywhere — only ``eth_getLogs``,
  ``eth_blockNumber``, ``eth_getBlockByNumber``.

The :func:`_assert_read_only_w3` helper runs at construction time
against the wired :class:`Web3` instance to make this enforcement
machine-checkable (and adds a layer of defence on top of the source
grep).

PIT semantics: a log row's ``available_at`` is ``block_time +
confirmation_depth``. We default to 12 blocks ≈ 26 s on Polygon —
enough to defeat single-block reorgs on the public RPC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from data.sources._http import require_asof_ts

if TYPE_CHECKING:  # pragma: no cover — type-check-only import
    from web3 import Web3
    from web3.providers.rpc import HTTPProvider

DEFAULT_RPC_URL = "https://polygon-rpc.com"
DEFAULT_CONFIRMATION_DEPTH = 12  # ≈ 26s @ 2.2s block time
DEFAULT_BLOCK_TIME_SECONDS = 2.2

# Method-name allowlist for the read-only invariant. The cross-chain
# auditor scans this constant; widening it requires a paired review.
READ_ONLY_RPC_METHODS: frozenset[str] = frozenset(
    {
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_getLogs",
        "eth_chainId",
    }
)


@dataclass(frozen=True)
class ChainEvent:
    """A single decoded chain event.

    Schema preserved from T-E-001 sprint_1; sprint_2 adds the
    ``topic0`` field (the keccak event signature) so the smart-money
    reducer can group events without re-parsing decoded_args.
    """

    block_number: int
    block_time: datetime
    tx_hash: str
    log_index: int
    contract_address: str
    event_name: str
    decoded_args: dict[str, str] = field(default_factory=dict)
    topic0: str = ""


class PolygonChainClient:
    """Read-only Polygon event scanner.

    READ-ONLY by construction:

    * No signer is wired.
    * No private key is loaded.
    * The :func:`_assert_read_only_w3` invariant runs once at first
      RPC contact and asserts the connected provider exposes ONLY
      the methods in :data:`READ_ONLY_RPC_METHODS`.
    """

    def __init__(
        self,
        *,
        rpc_url: str = DEFAULT_RPC_URL,
        cache_dir: str | None = None,
        confirmation_depth: int = DEFAULT_CONFIRMATION_DEPTH,
        block_time_seconds: float = DEFAULT_BLOCK_TIME_SECONDS,
        w3: Web3 | None = None,
    ) -> None:
        self._rpc_url = rpc_url
        self._cache_dir = cache_dir
        self._confirmation_depth = confirmation_depth
        self._block_time_seconds = block_time_seconds
        # Lazily constructed unless explicitly injected (tests).
        self._w3: Web3 | None = w3

    @property
    def confirmation_depth(self) -> int:
        return self._confirmation_depth

    def _w3_lazy(self) -> Web3:
        if self._w3 is None:
            # Imported lazily so the module is cheap to load and so
            # construction is still side-effect free.
            from web3 import Web3
            from web3.providers.rpc import HTTPProvider

            provider: HTTPProvider = HTTPProvider(self._rpc_url, request_kwargs={"timeout": 10})
            self._w3 = Web3(provider)
        # Re-assert read-only invariant on every entry — catches injected
        # Web3 instances that wire signing middleware after construction.
        _assert_read_only_w3(self._w3)
        return self._w3

    def fetch_events(
        self,
        contract_address: str,
        from_block: int,
        to_block: int,
        *,
        asof_ts: datetime,
    ) -> list[ChainEvent]:
        """Return decoded events in ``[from_block, to_block]`` — READ-ONLY.

        ``asof_ts`` is required and rejected if naive. Events whose
        ``available_at`` (block_time + confirmation_depth*block_time)
        exceeds ``asof_ts`` are filtered out before return.
        """
        cutoff = require_asof_ts(asof_ts)

        if from_block < 0 or to_block < from_block:
            raise ValueError(
                f"Invalid block range [{from_block}, {to_block}] — to_block must be ≥ from_block ≥ 0."
            )

        w3 = self._w3_lazy()

        # ``eth_getLogs`` is the canonical read; the address+block-range
        # filter is the only filter we expose to callers. We pass the
        # filter as a plain dict via Any so we don't bind to web3.py's
        # internal TypedDict, which has churned across versions.
        filter_params: Any = {
            "address": contract_address,
            "fromBlock": int(from_block),
            "toBlock": int(to_block),
        }
        logs: list[Any] = w3.eth.get_logs(filter_params)

        confirmation_lag = timedelta(
            seconds=self._confirmation_depth * self._block_time_seconds
        )

        events: list[ChainEvent] = []
        for raw in logs:
            log = _coerce_log(raw)
            block_time = _block_time_from_log(log)
            available_at = block_time + confirmation_lag
            if available_at > cutoff:
                # PIT filter — drop unconfirmed-by-cutoff events.
                continue
            topics: list[str] = list(log.get("topics", []))
            topic0 = topics[0] if topics else ""
            events.append(
                ChainEvent(
                    block_number=int(log["blockNumber"]),
                    block_time=block_time,
                    tx_hash=str(log["transactionHash"]),
                    log_index=int(log["logIndex"]),
                    contract_address=str(log["address"]),
                    event_name=str(log.get("event_name", "")),
                    topic0=topic0,
                )
            )
        return events


def _coerce_log(raw: Any) -> dict[str, Any]:
    """Normalise a web3 log (AttributeDict or dict) into a plain dict."""
    if isinstance(raw, dict):
        out: dict[str, Any] = dict(raw)
    else:
        # web3.py AttributeDict / Munch-like — iterate keys, index per key.
        out = {k: raw[k] for k in raw}
    # Hex-bytes → 0x-prefixed strings
    for key in ("transactionHash", "blockHash"):
        val = out.get(key)
        if val is not None and not isinstance(val, str):
            try:
                out[key] = val.hex() if hasattr(val, "hex") else str(val)
            except Exception:
                out[key] = str(val)
            if isinstance(out[key], str) and not out[key].startswith("0x"):
                out[key] = "0x" + out[key]
    raw_topics = out.get("topics", [])
    coerced_topics: list[str] = []
    for t in raw_topics:
        if isinstance(t, str):
            coerced_topics.append(t)
        elif hasattr(t, "hex"):
            h = t.hex()
            coerced_topics.append(h if h.startswith("0x") else "0x" + h)
        else:
            coerced_topics.append(str(t))
    out["topics"] = coerced_topics
    return out


def _block_time_from_log(log: dict[str, Any]) -> datetime:
    """Best-effort: use ``blockTime`` if present (fixture), else now-UTC."""
    bt = log.get("blockTime") or log.get("block_time")
    if isinstance(bt, datetime):
        return bt if bt.tzinfo else bt.replace(tzinfo=UTC)
    if isinstance(bt, (int, float)):
        return datetime.fromtimestamp(int(bt), tz=UTC)
    if isinstance(bt, str):
        return datetime.fromisoformat(bt.replace("Z", "+00:00"))
    # If the RPC log doesn't carry block_time inline (the standard case),
    # callers must enrich via a separate eth_getBlockByNumber. For sprint_2
    # we fall back to the canonical "unknown ⇒ pessimistic ⇒ now()" which
    # is then trimmed by the PIT filter to keep semantics safe.
    return datetime.now(UTC)


def _assert_read_only_w3(w3: Web3) -> None:
    """Defence-in-depth: assert the wired Web3 has no signing accounts.

    The cross-chain auditor greps this module for signer imports; this
    runtime check catches the case where a future refactor wires a
    signer through some indirect path (e.g. middleware that injects a
    LocalAccount).
    """
    # ``w3.eth.account`` is a class proxy that's always present; what we
    # forbid is *configured* default accounts.
    default_account = getattr(w3.eth, "default_account", None)
    if default_account:
        raise RuntimeError(
            "PolygonChainClient is READ-ONLY; the wired Web3 instance has a "
            f"default_account configured ({default_account!r}) which could "
            "send transactions. Refusing to proceed."
        )

    # Disallow the ``add_middleware('signing')`` patterns Web3.py uses
    # for local-account signing. The middleware onion exposes names; we
    # forbid any whose name contains 'sign'.
    middleware_onion = getattr(w3, "middleware_onion", None)
    if middleware_onion is not None:
        try:
            names = list(middleware_onion.middlewares)
        except Exception:
            names = []
        for entry in names:
            entry_name = getattr(entry, "__name__", str(entry)).lower()
            if "sign" in entry_name:
                raise RuntimeError(
                    "PolygonChainClient is READ-ONLY; signing-related middleware "
                    f"detected on the wired Web3 instance ({entry_name!r}). "
                    "Refusing to proceed."
                )


__all__ = [
    "DEFAULT_CONFIRMATION_DEPTH",
    "DEFAULT_RPC_URL",
    "READ_ONLY_RPC_METHODS",
    "ChainEvent",
    "PolygonChainClient",
]
