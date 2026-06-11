"""Production :class:`SandboxLoopChainAdapter` — T-B-042 sprint_13 D11 gate.

Spec anchors
------------

* PRD §5.0 / §5.1 — Death + Tombstone NFT carry ``memory_bank_cid`` +
  ``last_words`` + ``final_weights_hash``. The adapter encodes these
  into the on-chain ``AgentLifecycle.die`` payload so the demo's
  emotional payload survives the on-chain round-trip.

* PRD §6 — BREATH economy. Every settled bet PnL flows through the
  adapter's :meth:`RhChainAdapter.update_breath_from_pnl` to the
  on-chain ``EnergyController``. Replay protection is mandatory
  (TECHNICAL_PLAN §3.7).

* TECHNICAL_PLAN §3.1 — ``EnergyController.settleMarketLoss`` is the
  EIP-712 signed entry for off-chain → on-chain loss accounting.
  Per-signer nonce monotonicity (``usedNonces[signer][nonce]``) is the
  hard invariant; the adapter MUST honour it client-side too so a
  retry storm cannot accidentally re-broadcast the same attestation.

* TECHNICAL_PLAN §3.3 — ``AgentLifecycle.die(DeathPayload)`` mints the
  Tombstone in the same tx that flips ``LifeState`` to ``Dead``.
  ``die`` reverts ``NotDeadYet`` if breath != 0 — the adapter does
  NOT assert that precondition itself (the loop is responsible for
  driving breath to 0 first).

* TECHNICAL_PLAN §3.5 — ``TombstoneNFT.mint`` is locked to the
  ``AgentLifecycle`` address; the adapter never calls
  ``TombstoneNFT.mint`` directly — it goes through ``AgentLifecycle.die``
  and reads the resulting ``tombstoneTokenId`` for the
  :class:`DeathReceipt`.

* TECHNICAL_PLAN §7 — RH Chain is the canonical demo target. Address
  resolution is by env var so Sepolia / Polygon Amoy fall back without
  a code change. Phase 3 mainnet activation is a SEPARATE Gate C task —
  this adapter is testnet-only by construction (the loop's "no mainnet
  RPC URL" invariant is enforced by the operator runbook, not the
  adapter).

Architectural invariants enforced inline
----------------------------------------

* **Client-side replay protection**. ``_used_nonces`` is a per-process
  ``set[int]`` keyed off the signer address; every
  :meth:`update_breath_from_pnl` that signs a SettlementAttestation
  bumps a monotonic counter ``_next_nonce`` and adds it to the set.
  Re-attempting the same nonce raises
  :class:`ReplayAttemptError` BEFORE the RPC call lands — the on-chain
  ``usedNonces`` mapping is the second line of defence.

* **Sign exactly once**. ``_build_and_sign_settlement`` is the SINGLE
  call site that produces a signature; the test seam relies on this so
  ``Account.recover_message`` against the captured payload always
  returns the configured signer address.

* **No look-ahead**. The adapter exposes only ``read_breath()`` against
  the latest block; ``read_breath_at_block(historical_block)`` is
  INTENTIONALLY absent. If a future caller needs historical breath, it
  goes through the Track E reconciler — not the live adapter.

* **Death is one-way**. ``kill_and_mint_tombstone`` is idempotent at
  the adapter level only in the "happy path" sense; a second call after
  the on-chain ``LifeState`` is already ``Dead`` will surface the
  ``AgentLifecycle`` revert as :class:`ContractRevertError`. The loop's
  ``_die`` gate is the canonical idempotency anchor — the adapter is
  the broadcast surface, not the state machine.

Production wiring
-----------------

:func:`agent.server.main._build_chain_adapter` constructs this class
when ``PROD_LOOP_CHAIN_ADAPTER_KIND=rh_chain`` (sprint_13 T-B-041 env
knob). The 5 required env vars are documented in ``docs/DEPLOYMENT.md``
under "RH Chain adapter env vars". Rollback to the in-memory fake is a
single env-var flip back to ``sandbox`` — no code change required.

Test seam
---------

The constructor accepts a private ``_calls`` injection point exposing
the :class:`_ChainCalls` Protocol. Production resolves a real
:class:`_Web3ChainCalls` instance from the RPC URL; tests inject an
:class:`_InMemoryChainCalls` that records every signed attestation +
simulates the on-chain ``usedNonces`` + ``breath`` + ``die`` surfaces
without requiring anvil / eth_tester. The EIP-712 signing path is
exercised UNCHANGED by both modes — the test seam only replaces the
transport.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from eth_account import Account
from eth_account.messages import encode_typed_data

from agent.runtime.sandbox_phase2_loop import DeathReceipt

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Domain constants — match the deployed contract surface.
# --------------------------------------------------------------------------- #


EIP712_DOMAIN_NAME: Final[str] = "Genesis Experiment EnergyController"
"""EIP-712 domain ``name`` — matches ``contracts/EnergyController.sol``.

Locked by ``.dev/contracts/eip712_settlement.v0.1.0.json``. A drift here
silently breaks signature verification on-chain — the
``test_signature_recovery`` test guards the value off-chain. Production
deploy scripts read the same constant via the Track A address book."""


EIP712_DOMAIN_VERSION: Final[str] = "1"
"""EIP-712 domain ``version`` — matches the deployed
``EnergyController.DOMAIN_SEPARATOR`` cache."""


BREATH_DECIMALS: Final[int] = 6
"""1e6 fixed-point — matches ``EnergyController.breath`` storage unit.

PRD §6.4 and ``.dev/contracts/eip712_settlement.v0.1.0.json`` lock the
unit as 1e6. The adapter converts ``pnl_usd`` ↔ BREATH wei via this
constant. ``read_breath`` returns the float USD equivalent so the loop
sees a number in the same units it consumes."""


USD_TO_BREATH_WEI: Final[int] = 10**BREATH_DECIMALS


DEFAULT_DEADLINE_OFFSET_SECONDS: Final[int] = 3600
"""How far in the future the attestation deadline lives (1h).

EnergyController reverts ``DeadlineExpired`` when ``block.timestamp >
att.deadline``. 1h is comfortably longer than any plausible mempool
inclusion window even on a congested testnet. Locked at module scope so
operators can override at construction without per-tick env reads."""


# PRD §6.11 DeathCause enum values — mirrored from the contract.
DEATH_CAUSE_TRADING_LOSS: Final[int] = 0
DEATH_CAUSE_STARVATION: Final[int] = 1
DEATH_CAUSE_ATTRITION: Final[int] = 2


TOP_UP_REASON_POSITIVE_PNL_PREFIX: Final[str] = "settled_pnl_positive:"
"""Reason-string prefix written to ``EnergyController.topUpBreath``.

A structured prefix (constant + ":" + magnitude) lets log indexers
parse without magic-string matching. The full reason looks like
``settled_pnl_positive:0.123456`` (USD with 6dp). Operators searching
on-chain logs can filter on the prefix to surface every positive-PnL
top-up cleanly."""


# Env-var canonical names (also documented in docs/DEPLOYMENT.md).
RH_CHAIN_RPC_URL_ENV_VAR: Final[str] = "RH_CHAIN_RPC_URL"
RH_CHAIN_ENERGY_CONTROLLER_ADDRESS_ENV_VAR: Final[str] = (
    "RH_CHAIN_ENERGY_CONTROLLER_ADDRESS"
)
RH_CHAIN_AGENT_LIFECYCLE_ADDRESS_ENV_VAR: Final[str] = (
    "RH_CHAIN_AGENT_LIFECYCLE_ADDRESS"
)
RH_CHAIN_TOMBSTONE_NFT_ADDRESS_ENV_VAR: Final[str] = (
    "RH_CHAIN_TOMBSTONE_NFT_ADDRESS"
)
RH_CHAIN_SIGNER_PRIVATE_KEY_ENV_VAR: Final[str] = (
    "RH_CHAIN_SIGNER_PRIVATE_KEY"
)


# --------------------------------------------------------------------------- #
# Typed exceptions — distinct surfaces so callers can branch precisely.
# --------------------------------------------------------------------------- #


class RhChainAdapterError(RuntimeError):
    """Base class for every typed RH-Chain adapter failure.

    Subclassed by :class:`ReplayAttemptError` and
    :class:`ContractRevertError`. Callers (the loop's settlement-time
    retry policy) MUST NOT branch on bare ``Exception`` — a typed
    base class lets a defensive ``except RhChainAdapterError`` capture
    every adapter-level failure without swallowing unrelated bugs."""


class ReplayAttemptError(RhChainAdapterError):
    """Raised when a SettlementAttestation nonce is reused.

    Fired client-side (the adapter's :attr:`_used_nonces` cache) BEFORE
    the RPC call lands so a retry storm cannot accidentally re-broadcast
    the same attestation. The on-chain
    ``usedNonces[signer][nonce]`` mapping is the second line of defence;
    if it fires we surface the same typed error (the underlying revert
    string is in the exception ``args``).

    TECHNICAL_PLAN §3.7 makes replay protection a hard invariant. The
    typed error (NOT bare ``ValueError``) is what
    ``test_update_breath_replay_rejected`` asserts on."""


class ContractRevertError(RhChainAdapterError):
    """Raised when an on-chain call reverts.

    Distinct from :class:`ReplayAttemptError` because the loop's retry
    policy treats reverts differently — a revert often indicates a
    contract-state precondition (e.g. ``NotDeadYet`` on ``die``,
    ``DeadlineExpired`` on a stale attestation) and the caller should
    refresh state before retrying, whereas a replay attempt is
    permanent."""


class RpcTransportError(RhChainAdapterError):
    """Raised when an RPC transport (build / broadcast / receipt) fails.

    Distinct from :class:`ContractRevertError` so the loop's retry
    policy can route them differently: a transport error (connection
    refused, timeout, malformed reply) is typically transient and
    retryable; a revert is a contract-state precondition that needs a
    state refresh first. Wrapping bare ``Exception`` was conflating
    the two — the typed split surfaces the difference."""


# --------------------------------------------------------------------------- #
# Value objects.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EIP712Domain:
    """EIP-712 domain separator inputs.

    ``chain_id`` + ``verifying_contract`` are network-specific (set at
    construction from the RPC's ``eth_chainId`` + the deployed
    ``EnergyController`` address). ``name`` + ``version`` are locked
    by the contract and default to the canonical constants —
    overriding them is a footgun the constructor permits only via
    explicit keyword args."""

    name: str
    version: str
    chain_id: int
    verifying_contract: str  # checksum address

    def as_typed_data_dict(self) -> dict[str, Any]:
        """Project to the dict shape ``encode_typed_data`` expects."""
        return {
            "name": self.name,
            "version": self.version,
            "chainId": self.chain_id,
            "verifyingContract": self.verifying_contract,
        }


@dataclass(frozen=True)
class SettlementAttestation:
    """Off-chain SettlementAttestation payload.

    Mirrors the on-chain struct
    ``.dev/contracts/eip712_settlement.v0.1.0.json``:

    * ``market_id``   — uint256 Polymarket condition id / local index.
    * ``loss_amount`` — uint256 BREATH wei (1e6 fixed point).
    * ``nonce``       — uint256 monotonic per-signer counter.
    * ``deadline``    — uint256 unix seconds, ``block.timestamp <=
      deadline`` required on-chain.
    """

    market_id: int
    loss_amount: int
    nonce: int
    deadline: int

    def as_typed_data_dict(self) -> dict[str, int]:
        """Project to the dict shape ``encode_typed_data`` expects."""
        return {
            "marketId": self.market_id,
            "lossAmount": self.loss_amount,
            "nonce": self.nonce,
            "deadline": self.deadline,
        }


@dataclass(frozen=True)
class DeathPayload:
    """Off-chain DeathPayload — mirrors ``AgentLifecycle.die`` calldata.

    Mirrors ``.dev/contracts/agent_lifecycle_abi.v0.3.0.json`` ::

        struct DeathPayload {
            uint8   cause;              // TombstoneNFT.DeathCause
            bool    terminalAfterglow;
            string  lastWords;
            string  memoryBankCid;
            bytes   weights;
            bytes32 decisionHistoryHash;
            bytes   phaseStats;
        }
    """

    cause: int  # DeathCause enum value
    terminal_afterglow: bool
    last_words: str
    memory_bank_cid: str
    weights: bytes
    decision_history_hash: bytes  # 32 bytes
    phase_stats: bytes

    def as_tuple(self) -> tuple[int, bool, str, str, bytes, bytes, bytes]:
        """Project to the ABI tuple ordering the contract expects."""
        return (
            self.cause,
            self.terminal_afterglow,
            self.last_words,
            self.memory_bank_cid,
            self.weights,
            self.decision_history_hash,
            self.phase_stats,
        )


# --------------------------------------------------------------------------- #
# Test seam — _ChainCalls Protocol + InMemory implementation.
# --------------------------------------------------------------------------- #


class _ChainCalls(Protocol):
    """Adapter test seam — production wraps web3.py; tests inject a fake.

    The Protocol is intentionally narrow — the adapter does the EIP-712
    signing + nonce sequencing + dispatch, then hands the *prepared*
    payload to one of these methods. This keeps the on-chain
    boundary trivially swappable while the rich behaviour stays under
    test in the adapter itself.

    Methods are async because the production impl awaits web3 RPC calls
    (offloaded via :func:`asyncio.to_thread` to keep the loop responsive)."""

    async def breath(self) -> int:
        """Return current BREATH balance in wei (1e6 fixed-point)."""
        ...

    async def used_nonces(self, signer: str, nonce: int) -> bool:
        """``EnergyController.usedNonces[signer][nonce]`` view."""
        ...

    async def settle_market_loss(
        self,
        *,
        attestation: SettlementAttestation,
        signature: bytes,
        signer: str,
    ) -> str:
        """Broadcast ``EnergyController.settleMarketLoss``; return tx hash hex."""
        ...

    async def top_up_breath(self, *, amount_wei: int, reason: str) -> str:
        """Broadcast ``EnergyController.topUpBreath``; return tx hash hex."""
        ...

    async def die(
        self, *, payload: DeathPayload
    ) -> tuple[str, int]:
        """Broadcast ``AgentLifecycle.die``; return ``(tx_hash, token_id)``."""
        ...


@dataclass
class _InMemoryChainCalls:
    """Test-side :class:`_ChainCalls` — simulates contract state in-process.

    Captures every signed attestation + die payload so tests can assert
    on the exact bytes broadcast on-chain. Does NOT do any networking.

    Replay semantics: ``settle_market_loss`` checks
    :attr:`used_nonce_set` BEFORE applying and raises
    :class:`ContractRevertError` (mirroring an on-chain
    ``ReplayAttempt`` revert) if the nonce was already consumed. The
    adapter's client-side replay guard fires FIRST, so this path is
    only exercised by tests that bypass the adapter's cache."""

    current_breath_wei: int = 100 * USD_TO_BREATH_WEI
    used_nonce_set: set[tuple[str, int]] = field(default_factory=set)
    captured_attestations: list[
        tuple[SettlementAttestation, bytes, str]
    ] = field(default_factory=list)
    captured_topups: list[tuple[int, str]] = field(default_factory=list)
    captured_dies: list[DeathPayload] = field(default_factory=list)
    next_tombstone_token_id: int = 1
    has_died: bool = False

    async def breath(self) -> int:
        return self.current_breath_wei

    async def used_nonces(self, signer: str, nonce: int) -> bool:
        return (signer, nonce) in self.used_nonce_set

    async def settle_market_loss(
        self,
        *,
        attestation: SettlementAttestation,
        signature: bytes,
        signer: str,
    ) -> str:
        key = (signer, attestation.nonce)
        if key in self.used_nonce_set:
            raise ContractRevertError(
                f"ReplayAttempt: nonce {attestation.nonce} already used by {signer}"
            )
        self.used_nonce_set.add(key)
        self.captured_attestations.append((attestation, signature, signer))
        self.current_breath_wei = max(
            0, self.current_breath_wei - attestation.loss_amount
        )
        # Deterministic tx hash hex derived from the nonce so tests can
        # assert on equality without depending on wall-clock time.
        return "0x" + f"{attestation.nonce:064x}"

    async def top_up_breath(self, *, amount_wei: int, reason: str) -> str:
        self.captured_topups.append((amount_wei, reason))
        self.current_breath_wei += amount_wei
        # Deterministic tx hash hex derived from the amount.
        idx = len(self.captured_topups)
        return "0x" + f"{idx:064x}"

    async def die(
        self, *, payload: DeathPayload
    ) -> tuple[str, int]:
        if self.has_died:
            raise ContractRevertError(
                "NotDeadYet: AgentLifecycle.die already invoked"
            )
        self.has_died = True
        self.captured_dies.append(payload)
        token_id = self.next_tombstone_token_id
        self.next_tombstone_token_id += 1
        tx_hash = "0x" + f"{token_id:064x}"
        return tx_hash, token_id


# --------------------------------------------------------------------------- #
# RhChainAdapter — production :class:`SandboxLoopChainAdapter`.
# --------------------------------------------------------------------------- #


class RhChainAdapter:
    """Production :class:`SandboxLoopChainAdapter` for RH Chain.

    Implements the three-method
    :class:`agent.runtime.sandbox_phase2_loop.SandboxLoopChainAdapter`
    Protocol against the deployed
    :class:`EnergyController` + :class:`AgentLifecycle` + :class:`TombstoneNFT`
    triple. EIP-712 SettlementAttestation signing + per-signer nonce
    replay protection live here; the on-chain mappings are the second
    line of defence.

    Constructor seams
    -----------------

    * ``_calls`` (private kwarg) — the :class:`_ChainCalls` test seam.
      Default ``None`` → constructs an :class:`_Web3ChainCalls` lazily
      from ``rpc_url`` + the three contract addresses. Tests inject an
      :class:`_InMemoryChainCalls` to avoid requiring anvil / eth_tester
      while still exercising the EIP-712 + nonce + ABI-encoding logic.

    Rollback
    --------

    Setting ``PROD_LOOP_CHAIN_ADAPTER_KIND=sandbox`` (the T-B-041
    default) routes the prod loop factory back to
    :class:`agent.server.main._SandboxChainAdapter`. No code change is
    required to fall back — the env-var is read once at boot.

    Not handled here (by design)
    ----------------------------

    * **IPFS pinning** of the MemoryBank tarball. The loop pins
      out-of-band and hands the resulting CID into
      :meth:`kill_and_mint_tombstone` as ``memory_bank_cid``; the
      adapter passes the string through verbatim. PRD §5.1.C degraded
      path: when no CID is available the loop substitutes
      ``DEFAULT_MEMORY_BANK_CID_PLACEHOLDER`` (sandbox-only constant).

    * **Force-driving breath to 0 before die.** ``AgentLifecycle.die``
      reverts ``NotDeadYet`` when breath != 0; the loop's ``_die``
      method is responsible for the burn-to-zero call ordering. The
      adapter surfaces the revert as :class:`ContractRevertError`
      if a caller skips that step.
    """

    def __init__(
        self,
        *,
        rpc_url: str,
        energy_controller_address: str,
        agent_lifecycle_address: str,
        tombstone_nft_address: str,
        signer_private_key: str,
        eip712_domain: EIP712Domain | None = None,
        deadline_offset_seconds: int = DEFAULT_DEADLINE_OFFSET_SECONDS,
        _calls: _ChainCalls | None = None,
    ) -> None:
        if not signer_private_key:
            raise ValueError(
                "RhChainAdapter: signer_private_key MUST be non-empty"
            )
        account = Account.from_key(signer_private_key)
        self._account = account
        self._signer_address: str = str(account.address)

        self._rpc_url = rpc_url
        self._energy_controller_address = energy_controller_address
        self._agent_lifecycle_address = agent_lifecycle_address
        self._tombstone_nft_address = tombstone_nft_address
        self._deadline_offset_seconds = deadline_offset_seconds

        # Per-process replay guard — every nonce we have already broadcast.
        # The on-chain ``usedNonces`` mapping is the second line of defence
        # (a fresh process restart resets this set; the adapter consults the
        # on-chain mapping if necessary).
        self._used_nonces: set[int] = set()
        # Monotonic counter — bumped EVERY time we sign a new attestation.
        # We never re-use a nonce; even on a revert the next attempt uses
        # a fresh nonce so the on-chain mapping cannot accidentally lock
        # the signer out.
        self._next_nonce: int = 0

        # Resolve the EIP-712 domain. ``chain_id`` defaults to a sentinel
        # that callers MUST override (or supply a pre-built domain). The
        # production factory in ``agent.server.main`` reads
        # ``eth_chainId`` from the RPC at construction; tests pass an
        # explicit domain.
        if eip712_domain is None:
            raise ValueError(
                "RhChainAdapter: eip712_domain MUST be provided "
                "(production factory reads chain_id from the RPC)"
            )
        self._domain = eip712_domain

        # Test seam — production constructs an :class:`_Web3ChainCalls`
        # lazily here. The web3 import is deferred so an
        # in-memory-mode test (the dominant test path) never imports
        # web3 unnecessarily.
        if _calls is None:
            self._calls: _ChainCalls = _build_web3_chain_calls(
                rpc_url=rpc_url,
                energy_controller_address=energy_controller_address,
                agent_lifecycle_address=agent_lifecycle_address,
                signer_account=account,
            )
        else:
            self._calls = _calls

    @property
    def signer_address(self) -> str:
        """Configured signer's checksum address — for diagnostics / tests."""
        return self._signer_address

    @property
    def next_nonce(self) -> int:
        """Next nonce that will be allocated on
        :meth:`update_breath_from_pnl`. Surfaced for tests + debugging."""
        return self._next_nonce

    # ----- SandboxLoopChainAdapter Protocol --------------------------- #

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        """Mutate on-chain BREATH by ``pnl_usd`` (negative = loss).

        Routing:

        * ``pnl_usd < 0`` → build EIP-712 SettlementAttestation, sign,
          broadcast ``EnergyController.settleMarketLoss``.
        * ``pnl_usd > 0`` → broadcast ``EnergyController.topUpBreath``
          (admin path; no signature). Reason string carries a
          machine-readable provenance hint.
        * ``pnl_usd == 0`` → no-op (the loop should not call us, but
          we silently short-circuit so a defensive caller is harmless).

        Raises
        ------
        ReplayAttemptError
            If the next monotonic nonce was somehow already consumed —
            should be impossible in a single-process run but guards
            against multi-instance racing.

        ContractRevertError
            If the on-chain call reverts (``DeadlineExpired``,
            ``InvalidSignature``, ``Phase3IsLocked`` on a paused admin
            path, etc.).
        """
        if pnl_usd == 0:
            return

        if pnl_usd < 0:
            await self._settle_loss(loss_usd=-pnl_usd)
        else:
            await self._top_up(gain_usd=pnl_usd)

    async def read_breath(self) -> float:
        """Return current BREATH balance in USD (float).

        Pure ``eth_call`` against ``EnergyController.breath``. No
        signing, no nonce. Result is divided by :data:`USD_TO_BREATH_WEI`
        so the loop sees a USD-denominated float (the unit it consumes
        elsewhere)."""
        wei = await self._calls.breath()
        return float(wei) / float(USD_TO_BREATH_WEI)

    async def kill_and_mint_tombstone(
        self,
        *,
        agent_id: str,
        bankroll_usd: float,
        last_tick: int,
        final_weights_hash: str,
        memory_bank_cid: str,
        last_words: str,
    ) -> DeathReceipt:
        """Mint the on-chain Tombstone via ``AgentLifecycle.die``.

        The contract reverts ``NotDeadYet`` when breath != 0; the loop's
        :meth:`SandboxPhase2Loop._die` drives breath to 0 BEFORE calling
        us. The adapter surfaces the revert as
        :class:`ContractRevertError` so the loop's run summary records
        the precondition violation honestly.

        Parameters mirror the loop's death-path arguments. The
        ``memory_bank_cid`` empty-string fallback (PRD §5.1.C degraded
        path) is OWNED by the loop, not the adapter; we pass the string
        through verbatim.

        Returns a :class:`DeathReceipt` carrying the two tx hashes (we
        only broadcast ONE tx — kill + mint happen atomically — so
        ``kill_tx_hash`` and ``tombstone_tx_hash`` are the same value)
        plus the minted ERC-721 ``tokenId``.
        """
        # PRD §5.1.B "the typewriter ALWAYS plays" — empty last_words
        # would still mint, but the dashboard renders nothing. Surface a
        # warning so the operator sees the degraded state.
        if not last_words:
            logger.warning(
                "RhChainAdapter.kill_and_mint_tombstone: empty last_words for "
                "agent_id=%s last_tick=%d — dashboard will render blank",
                agent_id,
                last_tick,
            )

        decision_history_hash_bytes = _hex_to_bytes32(final_weights_hash)
        # The on-chain ``weights`` field is intentionally a separate
        # ABI-encoded blob; we use the agent_id + bankroll bytes as a
        # compact provenance payload for the sprint_13 wiring. A future
        # task swaps this for the actual ABI-encoded Weights snapshot;
        # the registry-tracked interface contract documents the change
        # as a v1.1.0 MINOR bump (no consumer break — the bytes field is
        # opaque to readers).
        weights_blob = agent_id.encode("utf-8")
        phase_stats_blob = _encode_phase_stats(
            bankroll_usd=bankroll_usd, last_tick=last_tick
        )

        # PRD §6.11 priority TradingLoss > Starvation > Attrition. Without
        # the per-life-state context the adapter defaults to Starvation —
        # the canonical "breath drained to zero" cause. The loop knows
        # the real cause and can override via a future kwarg if needed;
        # the sprint_13 interface keeps the surface minimal.
        cause = DEATH_CAUSE_STARVATION

        payload = DeathPayload(
            cause=cause,
            terminal_afterglow=True,
            last_words=last_words,
            memory_bank_cid=memory_bank_cid,
            weights=weights_blob,
            decision_history_hash=decision_history_hash_bytes,
            phase_stats=phase_stats_blob,
        )

        tx_hash, token_id = await self._calls.die(payload=payload)
        return DeathReceipt(
            kill_tx_hash=tx_hash,
            tombstone_token_id=str(token_id),
            tombstone_tx_hash=tx_hash,
        )

    # ----- Public test hooks ------------------------------------------ #

    def build_and_sign_settlement(
        self,
        *,
        market_id: int,
        loss_amount_wei: int,
        nonce: int | None = None,
        deadline: int | None = None,
    ) -> tuple[SettlementAttestation, bytes]:
        """Build + sign a SettlementAttestation; return (att, signature).

        Public so ``test_signature_recovery`` can recover the signer
        address from the produced signature WITHOUT routing through the
        full :meth:`update_breath_from_pnl` path. Also useful as a
        sanity check at construction time: a domain misconfiguration
        surfaces here as a recovered-address mismatch instead of an
        opaque on-chain ``InvalidSignature`` revert later.

        ``nonce`` defaults to the next monotonic counter (consuming it
        + adding it to the replay-guard set, same as the production
        path). ``deadline`` defaults to ``now + deadline_offset_seconds``.

        Raises
        ------
        ReplayAttemptError
            If the explicit ``nonce`` was already consumed.
        """
        if nonce is None:
            nonce = self._consume_next_nonce()
        else:
            if nonce in self._used_nonces:
                raise ReplayAttemptError(
                    f"nonce {nonce} already consumed by signer "
                    f"{self._signer_address}"
                )
            self._used_nonces.add(nonce)
            # Keep the monotonic counter ahead of any explicit nonce so a
            # subsequent default-nonce call cannot collide.
            self._next_nonce = max(self._next_nonce, nonce + 1)

        if deadline is None:
            deadline = int(time.time()) + self._deadline_offset_seconds

        att = SettlementAttestation(
            market_id=market_id,
            loss_amount=loss_amount_wei,
            nonce=nonce,
            deadline=deadline,
        )
        signature = self._sign_settlement_attestation(att)
        return att, signature

    # ----- Internals -------------------------------------------------- #

    def _consume_next_nonce(self) -> int:
        """Allocate the next monotonic nonce; record it; return it.

        Raises :class:`ReplayAttemptError` if the bookkeeping is
        inconsistent (should be impossible — guards against memory
        corruption / external mutation of :attr:`_used_nonces`).
        """
        nonce = self._next_nonce
        if nonce in self._used_nonces:
            # Defensive — should be unreachable in a single-threaded
            # caller because we control both _next_nonce and
            # _used_nonces. Tests bypass this path by passing explicit
            # nonces.
            raise ReplayAttemptError(
                f"monotonic nonce {nonce} already in used set; signer="
                f"{self._signer_address}"
            )
        self._used_nonces.add(nonce)
        self._next_nonce = nonce + 1
        return nonce

    def _sign_settlement_attestation(
        self, attestation: SettlementAttestation
    ) -> bytes:
        """Produce a 65-byte EIP-712 signature.

        Layered as a separate method so the test seam can swap the
        signing path (e.g. inject a fault) without touching the
        attestation construction. The signature is ``r||s||v`` bytes,
        the format ``EnergyController.settleMarketLoss`` expects.
        """
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "SettlementAttestation": [
                    {"name": "marketId", "type": "uint256"},
                    {"name": "lossAmount", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                ],
            },
            "primaryType": "SettlementAttestation",
            "domain": self._domain.as_typed_data_dict(),
            "message": attestation.as_typed_data_dict(),
        }
        encoded = encode_typed_data(full_message=typed_data)
        signed = self._account.sign_message(encoded)
        return bytes(signed.signature)

    async def _settle_loss(self, *, loss_usd: float) -> None:
        """Build + sign + broadcast a SettlementAttestation.

        ``update_breath_from_pnl`` is tick-aggregated (the loop sums
        PnL across a tick before calling us), so there is no single
        market_id to attribute the burn to. We use the nonce as the
        synthetic market_id — the (signer, nonce) pair is what the
        on-chain replay guard keys off anyway, so the market_id field
        is opaque to replay. Producers that want a real market_id call
        :meth:`build_and_sign_settlement` directly with the explicit
        value (the public hook the test_signature_recovery test uses).
        """
        loss_wei = _usd_to_wei(loss_usd)
        nonce = self._consume_next_nonce()
        deadline = int(time.time()) + self._deadline_offset_seconds
        attestation = SettlementAttestation(
            market_id=nonce,
            loss_amount=loss_wei,
            nonce=nonce,
            deadline=deadline,
        )
        signature = self._sign_settlement_attestation(attestation)

        # Belt-and-braces — even though our client-side cache already
        # bumped the nonce, double-check the on-chain mapping. A nonce
        # collision here implies state drift between this process and a
        # previously-run signer; we fail loudly rather than silently
        # broadcast a will-revert tx.
        if await self._calls.used_nonces(self._signer_address, nonce):
            raise ReplayAttemptError(
                f"on-chain usedNonces[{self._signer_address}][{nonce}] is "
                "already true; refusing to broadcast"
            )

        try:
            tx_hash = await self._calls.settle_market_loss(
                attestation=attestation,
                signature=signature,
                signer=self._signer_address,
            )
        except ContractRevertError as exc:
            # If the on-chain mapping rejected, surface as a ReplayAttempt
            # so the loop's typed handling matches.
            if "ReplayAttempt" in str(exc) or "usedNonces" in str(exc):
                raise ReplayAttemptError(str(exc)) from exc
            raise
        logger.info(
            "RhChainAdapter.settleMarketLoss: tx=%s loss_wei=%d nonce=%d",
            tx_hash,
            loss_wei,
            nonce,
        )

    async def _top_up(self, *, gain_usd: float) -> None:
        """Broadcast ``EnergyController.topUpBreath`` for positive PnL."""
        amount_wei = _usd_to_wei(gain_usd)
        reason = f"{TOP_UP_REASON_POSITIVE_PNL_PREFIX}{gain_usd:.6f}"
        tx_hash = await self._calls.top_up_breath(
            amount_wei=amount_wei, reason=reason
        )
        logger.info(
            "RhChainAdapter.topUpBreath: tx=%s amount_wei=%d",
            tx_hash,
            amount_wei,
        )


# --------------------------------------------------------------------------- #
# Production _Web3ChainCalls — wraps web3.py against the deployed contracts.
# --------------------------------------------------------------------------- #


def _build_web3_chain_calls(
    *,
    rpc_url: str,
    energy_controller_address: str,
    agent_lifecycle_address: str,
    signer_account: Any,
) -> _ChainCalls:
    """Construct the production :class:`_Web3ChainCalls`.

    Deferred web3 import so an in-memory-mode test never pays the
    import cost. The returned instance is opaque to the adapter — the
    Protocol surface is the only contract.
    """
    # Import inside the function so this module stays importable in
    # environments without web3 (test-only paths inject ``_calls``).
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    # ABIs are bundled with the harness — load lazily so a misnamed
    # entry surfaces at boot, not at first call. The deployed contracts
    # match the Track A canonical version anchors.
    energy_controller_abi = _load_abi(
        ".dev/contracts/energy_controller_abi.v0.4.0.json"
    )
    agent_lifecycle_abi = _load_abi(
        ".dev/contracts/agent_lifecycle_abi.v0.3.0.json"
    )
    return _Web3ChainCalls(
        w3=w3,
        energy_controller_address=energy_controller_address,
        energy_controller_abi=energy_controller_abi,
        agent_lifecycle_address=agent_lifecycle_address,
        agent_lifecycle_abi=agent_lifecycle_abi,
        signer_account=signer_account,
    )


def _load_abi(path: str) -> list[dict[str, Any]]:
    """Load the ``abi`` field from a Track A interface JSON file."""
    import json
    from pathlib import Path

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    abi = raw.get("abi")
    if not isinstance(abi, list):
        raise RuntimeError(
            f"_load_abi({path!r}): missing or malformed 'abi' field"
        )
    # The list contains dicts; cast for mypy --strict.
    return [item for item in abi if isinstance(item, dict)]


class _Web3ChainCalls:
    """Production :class:`_ChainCalls` — talks to deployed contracts via web3.

    Every async method offloads the synchronous web3 RPC call via
    :func:`asyncio.to_thread` so the loop's event loop remains
    responsive while the tx is in flight. Receipts are awaited inline —
    the loop's settlement-time retry policy assumes the result is
    confirmed before returning.
    """

    def __init__(
        self,
        *,
        w3: Any,
        energy_controller_address: str,
        energy_controller_abi: list[dict[str, Any]],
        agent_lifecycle_address: str,
        agent_lifecycle_abi: list[dict[str, Any]],
        signer_account: Any,
    ) -> None:
        self._w3 = w3
        self._signer = signer_account
        self._ec_address = energy_controller_address
        self._al_address = agent_lifecycle_address
        self._ec = w3.eth.contract(
            address=energy_controller_address, abi=energy_controller_abi
        )
        self._al = w3.eth.contract(
            address=agent_lifecycle_address, abi=agent_lifecycle_abi
        )

    async def breath(self) -> int:
        result = await asyncio.to_thread(self._ec.functions.breath().call)
        return int(result)

    async def used_nonces(self, signer: str, nonce: int) -> bool:
        result = await asyncio.to_thread(
            self._ec.functions.usedNonces(signer, nonce).call
        )
        return bool(result)

    async def settle_market_loss(
        self,
        *,
        attestation: SettlementAttestation,
        signature: bytes,
        signer: str,
    ) -> str:
        att_tuple = (
            attestation.market_id,
            attestation.loss_amount,
            attestation.nonce,
            attestation.deadline,
        )
        fn = self._ec.functions.settleMarketLoss(att_tuple, signature)
        return await self._send_tx(fn=fn, label="settleMarketLoss")

    async def top_up_breath(self, *, amount_wei: int, reason: str) -> str:
        fn = self._ec.functions.topUpBreath(amount_wei, reason)
        return await self._send_tx(fn=fn, label="topUpBreath")

    async def die(
        self, *, payload: DeathPayload
    ) -> tuple[str, int]:
        fn = self._al.functions.die(payload.as_tuple())
        tx_hash = await self._send_tx(fn=fn, label="die")
        # The minted tokenId is exposed via ``AgentLifecycle.tombstoneTokenId``
        # after the die tx confirms. Read it post-broadcast so we have a
        # deterministic value to surface in the DeathReceipt.
        token_id = int(
            await asyncio.to_thread(self._al.functions.tombstoneTokenId().call)
        )
        return tx_hash, token_id

    async def _send_tx(self, *, fn: Any, label: str) -> str:
        """Build → sign → broadcast → wait for receipt; return tx hash hex."""
        signer_addr = self._signer.address
        tx_count = await asyncio.to_thread(
            self._w3.eth.get_transaction_count, signer_addr
        )
        try:
            tx = await asyncio.to_thread(
                fn.build_transaction,
                {
                    "from": signer_addr,
                    "nonce": tx_count,
                    # gas / gasPrice intentionally left to the provider's
                    # estimate — RH Chain is an L3 with elastic gas.
                },
            )
        except Exception as exc:
            # Build-time failures are RPC-side (estimateGas timeout,
            # malformed reply) — typed as RpcTransportError so the
            # loop's retry policy can distinguish a transient transport
            # hiccup from a deterministic contract revert.
            raise RpcTransportError(
                f"{label}: build_transaction failed: {exc!r}"
            ) from exc

        signed = self._signer.sign_transaction(tx)
        try:
            tx_hash = await asyncio.to_thread(
                self._w3.eth.send_raw_transaction, signed.raw_transaction
            )
            receipt = await asyncio.to_thread(
                self._w3.eth.wait_for_transaction_receipt, tx_hash
            )
        except Exception as exc:
            # Broadcast / receipt failures are RPC transport issues —
            # the on-chain revert path is the ``status != 1`` branch
            # below (deterministic contract precondition).
            raise RpcTransportError(
                f"{label}: broadcast / receipt failed: {exc!r}"
            ) from exc
        status = int(getattr(receipt, "status", 0))
        if status != 1:
            raise ContractRevertError(
                f"{label}: tx reverted on-chain (status=0); tx_hash={tx_hash.hex()}"
            )
        # web3.py returns a ``HexBytes`` for tx hashes; ``.hex()`` returns
        # ``str`` but the stub is untyped (web3 mypy-ignored above). Cast
        # for ``mypy --strict``.
        return str(tx_hash.hex())


# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #


def _usd_to_wei(usd: float) -> int:
    """Convert a USD float to 1e6 fixed-point BREATH wei.

    ``round`` is intentional — the contract expects an integer, and
    truncation would silently lose precision on the typical settlement
    PnL magnitudes (which round to the cent). Negative inputs raise;
    callers MUST pass an absolute value (``_settle_loss`` does)."""
    if usd < 0:
        raise ValueError(f"_usd_to_wei: USD must be >= 0; got {usd}")
    return round(usd * USD_TO_BREATH_WEI)


def _hex_to_bytes32(hex_str: str) -> bytes:
    """Normalise a ``0x``-prefixed (or raw) 32-byte hex string.

    PRD §5.1 stores ``decisionHistoryHash`` as on-chain ``bytes32`` —
    the loop hands us a hex string (with or without ``0x`` prefix) and
    we project it to exactly 32 bytes. Short strings are LEFT-padded
    with zeros so a SHA-256 digest projects naturally. Over-long
    strings raise — silent truncation would burn an audit trail.
    """
    s = hex_str.lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) > 64:
        raise ValueError(
            f"_hex_to_bytes32: input is {len(s)} hex chars; max 64"
        )
    s = s.rjust(64, "0")
    return bytes.fromhex(s)


def _encode_phase_stats(*, bankroll_usd: float, last_tick: int) -> bytes:
    """Encode the per-phase aggregates blob for the TombstoneNFT struct.

    Compact ABI-encoded ``(uint256, uint256)`` — bankroll wei and tick
    index. The dashboard decodes per its own schema; the contract treats
    the blob as opaque. Keeping the encoding here lets a downstream
    sprint widen the payload (e.g. add per-engine win-rate aggregates)
    without changing the public adapter Protocol.
    """
    bankroll_wei = max(0, round(bankroll_usd * USD_TO_BREATH_WEI))
    # Manual ABI encoding to avoid pulling eth_abi as a new direct
    # dependency — two uint256 big-endian values, 32 bytes each.
    return (
        bankroll_wei.to_bytes(32, byteorder="big", signed=False)
        + max(0, int(last_tick)).to_bytes(32, byteorder="big", signed=False)
    )


def build_from_env(
    *,
    env: dict[str, str] | None = None,
    eip712_domain: EIP712Domain | None = None,
) -> RhChainAdapter:
    """Construct an :class:`RhChainAdapter` from the canonical env vars.

    Called by ``agent.server.main._build_chain_adapter`` when
    ``PROD_LOOP_CHAIN_ADAPTER_KIND=rh_chain``. Reads the five env vars
    documented in ``docs/DEPLOYMENT.md``:

    * ``RH_CHAIN_RPC_URL``
    * ``RH_CHAIN_ENERGY_CONTROLLER_ADDRESS``
    * ``RH_CHAIN_AGENT_LIFECYCLE_ADDRESS``
    * ``RH_CHAIN_TOMBSTONE_NFT_ADDRESS``
    * ``RH_CHAIN_SIGNER_PRIVATE_KEY``

    ``eip712_domain`` defaults to a value built from the RPC's
    ``eth_chainId`` + the EnergyController address; tests pass an
    explicit domain to keep the construction hermetic. Missing env
    vars raise ``RuntimeError`` with the offending key in the message
    so the operator runbook can fix the deploy config without grepping.

    ``env`` defaults to ``os.environ``; tests inject a dict to keep the
    helper hermetic.
    """
    e = env if env is not None else os.environ
    required = (
        RH_CHAIN_RPC_URL_ENV_VAR,
        RH_CHAIN_ENERGY_CONTROLLER_ADDRESS_ENV_VAR,
        RH_CHAIN_AGENT_LIFECYCLE_ADDRESS_ENV_VAR,
        RH_CHAIN_TOMBSTONE_NFT_ADDRESS_ENV_VAR,
        RH_CHAIN_SIGNER_PRIVATE_KEY_ENV_VAR,
    )
    missing = [k for k in required if not e.get(k, "").strip()]
    if missing:
        raise RuntimeError(
            "RhChainAdapter.build_from_env: missing required env vars: "
            + ", ".join(missing)
        )

    rpc_url = e[RH_CHAIN_RPC_URL_ENV_VAR].strip()
    ec_addr = e[RH_CHAIN_ENERGY_CONTROLLER_ADDRESS_ENV_VAR].strip()
    al_addr = e[RH_CHAIN_AGENT_LIFECYCLE_ADDRESS_ENV_VAR].strip()
    tn_addr = e[RH_CHAIN_TOMBSTONE_NFT_ADDRESS_ENV_VAR].strip()
    pk = e[RH_CHAIN_SIGNER_PRIVATE_KEY_ENV_VAR].strip()

    if eip712_domain is None:
        chain_id = _read_chain_id_from_rpc(rpc_url)
        eip712_domain = EIP712Domain(
            name=EIP712_DOMAIN_NAME,
            version=EIP712_DOMAIN_VERSION,
            chain_id=chain_id,
            verifying_contract=ec_addr,
        )

    return RhChainAdapter(
        rpc_url=rpc_url,
        energy_controller_address=ec_addr,
        agent_lifecycle_address=al_addr,
        tombstone_nft_address=tn_addr,
        signer_private_key=pk,
        eip712_domain=eip712_domain,
    )


def _read_chain_id_from_rpc(rpc_url: str) -> int:
    """Probe ``eth_chainId`` so the EIP-712 domain matches the network.

    Isolated so a test can patch the lookup without monkey-patching
    web3 globally. Production: a single synchronous RPC call at boot.
    """
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    return int(w3.eth.chain_id)


__all__ = [
    "BREATH_DECIMALS",
    "DEATH_CAUSE_ATTRITION",
    "DEATH_CAUSE_STARVATION",
    "DEATH_CAUSE_TRADING_LOSS",
    "DEFAULT_DEADLINE_OFFSET_SECONDS",
    "EIP712_DOMAIN_NAME",
    "EIP712_DOMAIN_VERSION",
    "RH_CHAIN_AGENT_LIFECYCLE_ADDRESS_ENV_VAR",
    "RH_CHAIN_ENERGY_CONTROLLER_ADDRESS_ENV_VAR",
    "RH_CHAIN_RPC_URL_ENV_VAR",
    "RH_CHAIN_SIGNER_PRIVATE_KEY_ENV_VAR",
    "RH_CHAIN_TOMBSTONE_NFT_ADDRESS_ENV_VAR",
    "TOP_UP_REASON_POSITIVE_PNL_PREFIX",
    "USD_TO_BREATH_WEI",
    "ContractRevertError",
    "DeathPayload",
    "EIP712Domain",
    "ReplayAttemptError",
    "RhChainAdapter",
    "RhChainAdapterError",
    "RpcTransportError",
    "SettlementAttestation",
    "_InMemoryChainCalls",
    "build_from_env",
]
