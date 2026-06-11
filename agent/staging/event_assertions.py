"""Phase 3 renunciation receipt verifier — §15 Gap 7 audit anchor.

Spec
----

PRD §5.1 + TP §15 Gap 7 require that the Phase 3 advance transaction
emit ``PauseRoleRenounced`` AND ``UpgradeRoleRenounced`` so the Demo
panel can render Etherscan-verifiable proof of trustlessness. On-chain
both surface as ``Phase3RolesRenounced(uint64)`` — one emitted by
``EnergyController`` (the pause-role contract), the other by
``PhaseManager`` (the upgrade-role contract). The brief's logical
labels map structurally:

* ``PauseRoleRenounced`` ↔ ``EnergyController.Phase3RolesRenounced``
* ``UpgradeRoleRenounced`` ↔ ``PhaseManager.Phase3RolesRenounced``

Both events MUST appear in the SAME tx receipt's log list, each
with the canonical topic0 ``keccak256("Phase3RolesRenounced(uint64)")``
and with ``log.address`` matching the respective contract.

Why both addresses?
-------------------

A naïve check would just count "two ``Phase3RolesRenounced`` topics
in the receipt" — but that's spoofable. A malicious upgrade could
emit the topic twice from a single contract while leaving the OTHER
role un-renounced. The audit story requires both contracts'
addresses to be observed in the receipt's log list AND each to
carry the renunciation topic. That's the structural pinning this
module performs.

Dep-free design
---------------

We do NOT take a ``web3.py`` dependency here. The caller (production
``rehearsal_runner.fetch_receipt`` wrapper) computes the topic0
keccak from the canonical signature string ONCE at boot, then
passes the precomputed hex to :func:`verify_phase3_renunciation`. The
module exposes the canonical signature string as
:data:`PHASE3_RENUNCIATION_SIGNATURE` so a future ABI bump (e.g.
adding an indexed param) surfaces as a signature-string change
caught by reviewers.

The :class:`ReceiptLike` Protocol pins the minimal receipt shape the
verifier needs (``logs`` iterable with ``address``, ``topics``,
``transactionHash``) — production hands a raw web3 receipt; tests
hand a plain dataclass conforming to the Protocol.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Annotated, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Canonical Solidity signature of the renunciation event. Anchored
# verbatim in ``.dev/contracts/energy_controller_abi.v0.4.0.json``
# and ``.dev/contracts/phase_manager_abi.v0.3.0.json`` — both
# contracts emit the SAME event signature; what differs is the
# emitting ``log.address``. A future ABI bump that changes the
# parameter list MUST update this string + bump the consuming task's
# task_brief acceptance criterion.
PHASE3_RENUNCIATION_SIGNATURE: Final[str] = "Phase3RolesRenounced(uint64)"


class _LogLike(Protocol):
    """Minimal structural shape of one chain log entry.

    Production: a ``web3.AttributeDict`` decoded by ``web3.py``.
    Tests: a plain ``dataclass`` or ``dict``-like with the same
    attribute names. We use a Protocol instead of inheriting from
    ``web3.AttributeDict`` so the staging package stays import-free
    of ``web3.py``.

    Notes on attribute spelling:

    * ``address`` is the EVM address of the contract that emitted the
      log. Always 20 bytes (0x-prefixed 42-char hex by convention).
    * ``topics`` is an ordered list of 32-byte hex strings; index 0
      is the event signature hash (``topic0``).
    """

    @property
    def address(self) -> str: ...

    @property
    def topics(self) -> Sequence[str]: ...


class ReceiptLike(Protocol):
    """Minimal structural shape of an EVM transaction receipt.

    The verifier reads ``transactionHash`` (for diagnostic display)
    and ``logs`` (for the renunciation check). Block number /
    status / gas-used are deliberately NOT touched — a failed tx
    with status=0 should NOT be silently ignored; the upstream
    runner enforces ``status == 1`` BEFORE invoking the verifier so
    a confused-deputy "expected revert, got renounce" surfaces with
    a clearer error message.

    ``Sequence`` (not ``list``) is used so concrete receipt classes
    typed with a narrower element type (e.g. ``list[ConcreteLog]``)
    satisfy the Protocol via covariance.
    """

    @property
    def transactionHash(self) -> str: ...

    @property
    def logs(self) -> Sequence[_LogLike]: ...


class Phase3RenunciationCheck(BaseModel):
    """Verdict + diagnostic detail of one renunciation receipt check.

    The runner stashes this in its :class:`RehearsalReport`; the
    Demo dashboard renders both ``pause_role_renounced_tx`` and
    ``upgrade_role_renounced_tx`` as Etherscan links.

    Fields
    ------

    transaction_hash:
        The tx whose receipt was verified — verbatim from
        ``receipt.transactionHash``. Always set so a failed check
        still tells the operator WHICH tx was scrutinised.

    both_emitted:
        ``True`` iff both contracts' addresses appeared in the
        receipt's log list AND each carried the renunciation
        topic0. This is the hard pass criterion.

    pause_role_emitted:
        ``True`` iff the EnergyController address emitted the
        canonical topic0.

    upgrade_role_emitted:
        ``True`` iff the PhaseManager address emitted the canonical
        topic0.

    missing:
        Human-readable list of the contracts that DID NOT emit.
        Empty iff ``both_emitted`` is true. The Demo runbook copies
        this verbatim into the failure narrative.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    transaction_hash: Annotated[str, Field(min_length=1)]
    both_emitted: bool
    pause_role_emitted: bool
    upgrade_role_emitted: bool
    missing: list[str] = Field(default_factory=list)


def _normalize_hex(raw: str) -> str:
    """Canonicalise an EVM hex value for byte-stable comparison.

    Web3 receipts return checksummed addresses + lower-case topic
    hashes inconsistently across providers. We lower-case both
    sides of every comparison so a Polygon Amoy receipt and a
    local Anvil receipt compare equal modulo provider-specific
    casing.

    The function is conservative — non-hex input (``None``, empty
    string, missing ``0x`` prefix) returns the canonicalised empty
    string ``""`` so the caller's comparison fails cleanly without
    raising. A spoofed address that's literally empty would simply
    fail the receipt check rather than crashing the verifier.
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    if not s.startswith("0x"):
        return ""
    return s


def verify_phase3_renunciation(
    *,
    receipt: ReceiptLike,
    energy_controller_address: str,
    phase_manager_address: str,
    renunciation_topic0: str,
) -> Phase3RenunciationCheck:
    """Verify the Phase 3 advance tx renunciation invariant.

    Walks the receipt's log list. For each log whose ``topics[0]``
    matches ``renunciation_topic0``, record the emitting
    ``log.address``. Pass iff BOTH the EnergyController address and
    the PhaseManager address are observed with that topic.

    The check is structurally pinned by ``log.address`` — emitting
    the topic twice from the same contract does NOT count.

    Parameters
    ----------
    receipt
        EVM transaction receipt of the Phase 3 advance tx.
    energy_controller_address
        EVM address of the deployed ``EnergyController``. The
        runner reads this from the operator's testnet deploy
        config; tests pass a fixture address.
    phase_manager_address
        EVM address of the deployed ``PhaseManager``.
    renunciation_topic0
        Pre-computed ``keccak256(PHASE3_RENUNCIATION_SIGNATURE)`` as
        a 32-byte 0x-prefixed hex string. The runner computes this
        ONCE at boot via the injected ``keccak_fn`` (production
        wires ``web3.Web3.keccak``); tests pass a known fixture
        value.

    Returns
    -------
    Phase3RenunciationCheck
        The verdict — see field docs above.
    """
    target_topic = _normalize_hex(renunciation_topic0)
    ec_addr = _normalize_hex(energy_controller_address)
    pm_addr = _normalize_hex(phase_manager_address)

    pause_role_emitted = False
    upgrade_role_emitted = False

    if target_topic and ec_addr and pm_addr:
        for log in receipt.logs:
            topics = log.topics
            if not topics:
                continue
            topic0 = _normalize_hex(topics[0])
            if topic0 != target_topic:
                continue
            log_addr = _normalize_hex(log.address)
            if log_addr == ec_addr:
                pause_role_emitted = True
            elif log_addr == pm_addr:
                upgrade_role_emitted = True

    missing: list[str] = []
    if not pause_role_emitted:
        missing.append("EnergyController.Phase3RolesRenounced (PauseRoleRenounced)")
    if not upgrade_role_emitted:
        missing.append("PhaseManager.Phase3RolesRenounced (UpgradeRoleRenounced)")

    return Phase3RenunciationCheck(
        transaction_hash=receipt.transactionHash,
        both_emitted=(pause_role_emitted and upgrade_role_emitted),
        pause_role_emitted=pause_role_emitted,
        upgrade_role_emitted=upgrade_role_emitted,
        missing=missing,
    )


# ---------------------------------------------------------------------------
# Convenience: count occurrences of a topic across a log iterable.
# ---------------------------------------------------------------------------


def count_topic0(
    logs: Iterable[_LogLike],
    topic0_hex: str,
) -> int:
    """Count logs whose ``topics[0]`` matches the given hash.

    Used by the rehearsal runner's per-event-kind counters
    (DesperateModeEntered, MaxBreathDeepened, MarketLossSettled).
    Casing is normalised on both sides per :func:`_normalize_hex`.
    """
    target = _normalize_hex(topic0_hex)
    if not target:
        return 0
    n = 0
    for log in logs:
        topics = log.topics
        if not topics:
            continue
        if _normalize_hex(topics[0]) == target:
            n += 1
    return n


__all__ = [
    "PHASE3_RENUNCIATION_SIGNATURE",
    "Phase3RenunciationCheck",
    "ReceiptLike",
    "count_topic0",
    "verify_phase3_renunciation",
]
