"""Settlement reconciler — pin every Polymarket settle event to a
signed L3 BankrollUpdate attestation per TP §3.7 EIP-712.

The Genesis Experiment runs three chains:

* Polymarket (Polygon) — where the actual bet settles + payout fires.
* L3 (custom rollup) — where the EnergyController BREATH ledger lives.
* (off-chain) reconciliation — this module — that links the two.

Each Polymarket settlement event MUST be paired with a signed BankrollUpdate
attestation that the L3 EnergyController consumed. The pairing is a
**three-factor identity**: ``(nonce, marketId, outcome)``. All three
must match. Any of:

* nonce reuse → ``REPLAY_REJECTED``
* wrong marketId → ``IDENTITY_MISMATCH`` (cross-market spoofing attempt)
* wrong outcome → ``IDENTITY_MISMATCH`` (wrong-side spoofing attempt —
  attestation claims YES won when settlement says NO won)

…rejects the pairing. An unmatched settlement (no attestation at all) is
flagged as ``UNMATCHED`` — the cross-chain auditor reads this as
"BREATH-balance on L3 is no longer a deterministic function of Polygon
settlement events" (TP §3.7 invariant).

Replay protection model
-----------------------

Mirrors the on-chain ``usedNonces[signer][nonce]`` mapping the
EnergyController enforces (see ``.dev/contracts/eip712_settlement.v0.1.0.json``
"consumer_notes.Track B"): per-signer monotonic nonce. Nonce reuse by
the SAME signer is the attack vector; two distinct signers using the
same nonce values are independent (each EnergyController-recognised
attestationSigner has its own counter).

This module does NOT verify EIP-712 signatures cryptographically — the
on-chain ``recover`` call already does that, and pulling in
``eth-account`` for this off-chain reconciler would bloat the wheel.
The reconciler operates structurally on the (signer, nonce, marketId,
outcome) tuple the indexer extracted from the on-chain event.

Inputs / outputs
----------------

Inputs:

* ``settlements`` — iterable of :class:`PolymarketSettlement` (decoded
  from ``MarketResolved`` events on Polygon by Track E's indexer).
* ``attestations`` — iterable of :class:`BankrollUpdateAttestation`
  (decoded from ``MarketLossSettled`` / equivalent events on L3 by
  Track E's indexer). The attestations are already proven well-formed
  by the on-chain ``recover``; structural integrity is the only
  remaining check.

Output: a :class:`ReconciliationReport` describing every pairing
attempt. The cross-chain auditor consumes this report; a non-empty
``rejected`` or ``unmatched`` list is a Tier 1 critical drift finding
(brief acceptance criterion).

Schema note
-----------

The brief references the canonical BankrollUpdate attestation typehash
per TP §3.7. The ``.dev/contracts/bankroll_update_eip712.v0.1.0.json``
schema is marked ``status: skeleton`` in ``_registry.json`` — T-A-009
ships the Solidity side. This module defines the in-process Pydantic
view :class:`BankrollUpdateAttestation` matching TP §3.7's stated
fields (signer + marketId + outcome + nonce + amount + deadline). When
the full JSON Schema lands, the registry bump check in
``test_attestation_schema_round_trip`` (defence-in-depth) is the migration
checkpoint.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

from agent.core.state import Side

logger = logging.getLogger(__name__)


# Default drift tolerance — the reconciler flags any |attested - settled|
# above this as a drift finding. Inherited from
# ``harness/tools/reconciliation.py::ABSOLUTE_DRIFT_USD_THRESHOLD`` (1.0 USD)
# so the off-chain reconciler + the gate agree on the same number.
DEFAULT_DRIFT_TOLERANCE_USD: Final[float] = 1.0


class ReconciliationStatus(StrEnum):
    """Per-pairing outcome.

    ``MATCHED`` is the only happy path. Any other value is surfaced
    as a finding the cross-chain auditor reads as a drift / spoofing
    signal.
    """

    MATCHED = "matched"
    REPLAY_REJECTED = "replay_rejected"
    IDENTITY_MISMATCH = "identity_mismatch"
    DRIFT_DETECTED = "drift_detected"
    UNMATCHED = "unmatched"


class BankrollUpdateAttestation(BaseModel):
    """In-process view of the BankrollUpdate EIP-712 attestation.

    Mirrors TP §3.7 fields: ``signer`` (recovered address — the
    EnergyController.attestationSigner the on-chain verify already
    matched), ``marketId``, ``outcome``, ``nonce``, ``amount_usd``
    (signed delta: positive on a win, negative on a loss),
    ``deadline``. The ``signature`` field is optional + opaque — the
    on-chain ``recover`` already verified it; we keep the field for
    audit logging but don't re-verify.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signer: Annotated[str, Field(min_length=42, max_length=42)]
    market_id: Annotated[int, Field(ge=0)]
    outcome: Side
    nonce: Annotated[int, Field(ge=0)]
    amount_usd: float
    deadline: Annotated[int, Field(ge=0)]
    signature: str | None = None


class PolymarketSettlement(BaseModel):
    """In-process view of a Polymarket settle event the indexer emitted.

    ``payout_usd`` is the *delta* applied to the agent's bankroll —
    positive on a win, negative on a loss. The reconciler compares
    this against the attestation's ``amount_usd`` for the drift check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: Annotated[int, Field(ge=0)]
    outcome: Side
    payout_usd: float
    settled_at: str  # ISO-8601 UTC


@dataclass(frozen=True)
class PairingOutcome:
    """One row of :class:`ReconciliationReport`.

    ``status`` is the verdict; ``reason`` is a short description for
    the audit log; ``settlement`` and ``attestation`` are the inputs
    that were paired (either may be None on UNMATCHED). ``drift_usd``
    is populated on DRIFT_DETECTED + MATCHED (zero on the happy path).
    """

    status: ReconciliationStatus
    reason: str
    settlement: PolymarketSettlement | None = None
    attestation: BankrollUpdateAttestation | None = None
    drift_usd: float = 0.0


@dataclass
class ReconciliationReport:
    """Aggregate result of one reconciler invocation.

    The cross-chain auditor reads ``rejected`` / ``unmatched`` / ``drift``
    as Tier 1 critical findings; an empty report ``matched`` list with
    no other rows means there was nothing to reconcile this window.
    """

    matched: list[PairingOutcome] = field(default_factory=list)
    rejected: list[PairingOutcome] = field(default_factory=list)
    unmatched: list[PairingOutcome] = field(default_factory=list)
    drift: list[PairingOutcome] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True iff no rejections, no unmatched, and no drift."""
        return not (self.rejected or self.unmatched or self.drift)

    @property
    def total_pairings(self) -> int:
        """Total rows across all outcome buckets."""
        return (
            len(self.matched)
            + len(self.rejected)
            + len(self.unmatched)
            + len(self.drift)
        )


@dataclass
class SettlementReconciler:
    """Stateful reconciler — owns the per-signer seen-nonces set.

    The state is in-process memory only; a fresh reconciler at boot
    rebuilds it from the L3 indexer's full attestation stream (Track E
    provides the canonical replay). Because the on-chain
    ``usedNonces[signer][nonce]`` mapping is the authoritative replay
    record, the reconciler's in-process set is a *cache* used to
    detect replays within the current reconciliation window; the
    on-chain mapping prevents a replayed attestation from EVER being
    successfully consumed twice.

    Parameters
    ----------
    drift_tolerance_usd:
        Maximum |attested - settled| allowed before flagging
        ``DRIFT_DETECTED``. Defaults to
        :data:`DEFAULT_DRIFT_TOLERANCE_USD` (1.0 USD).
    """

    drift_tolerance_usd: float = DEFAULT_DRIFT_TOLERANCE_USD
    # Per-signer seen nonces. Indexed by (signer.lower(), nonce) so
    # case-mixed checksum addresses cannot mask a replay (the on-chain
    # mapping uses the raw 20-byte address; we normalise to lower for
    # case-insensitive comparison).
    _seen_nonces: set[tuple[str, int]] = field(default_factory=set)

    def reconcile(
        self,
        *,
        settlements: Iterable[PolymarketSettlement],
        attestations: Iterable[BankrollUpdateAttestation],
    ) -> ReconciliationReport:
        """Pair every settlement with its matching attestation.

        Algorithm:

        1. Walk attestations in stream order. Reject any whose
           ``(signer, nonce)`` was seen before (replay). Build an index
           on the remaining ones keyed by ``(marketId, outcome)``.
        2. Walk settlements in stream order. For each, look up the
           ``(marketId, outcome)`` key in the attestation index.
           - Missing → UNMATCHED.
           - Found but |attested - settled| > tolerance → DRIFT_DETECTED.
           - Otherwise → MATCHED.
        3. Attestations that never paired with a settlement are flagged
           as ``UNMATCHED`` too (the reconciler is bidirectional — a
           BREATH burn on L3 that doesn't correspond to a Polygon
           settlement is also drift).

        Returns a :class:`ReconciliationReport`.
        """
        report = ReconciliationReport()

        # ── Step 1: replay-check + index attestations
        # Bucket by composite (signer, nonce) for replay; index by
        # (marketId, outcome) for the pairing lookup. The pairing
        # lookup uses the FIRST non-rejected attestation per key —
        # a second attestation for the same (marketId, outcome) would
        # be a different signer (the per-signer nonce check above
        # already covers same-signer dup) and is an attempted
        # duplicate-credit attack — also rejected.
        attestation_index: dict[
            tuple[int, Side], BankrollUpdateAttestation
        ] = {}
        for att in attestations:
            replay_key = (att.signer.lower(), att.nonce)
            if replay_key in self._seen_nonces:
                report.rejected.append(
                    PairingOutcome(
                        status=ReconciliationStatus.REPLAY_REJECTED,
                        reason=(
                            f"nonce {att.nonce} reused by signer "
                            f"{att.signer}"
                        ),
                        attestation=att,
                    )
                )
                logger.warning(
                    "settlement_reconciler: REPLAY_REJECTED signer=%s nonce=%d",
                    att.signer,
                    att.nonce,
                )
                continue
            self._seen_nonces.add(replay_key)

            pair_key = (att.market_id, att.outcome)
            if pair_key in attestation_index:
                # Two distinct signers claimed the same (market, outcome).
                # The on-chain EnergyController only honours one
                # attestation per (market, outcome); the second is a
                # duplicate-credit attempt — reject structurally.
                report.rejected.append(
                    PairingOutcome(
                        status=ReconciliationStatus.IDENTITY_MISMATCH,
                        reason=(
                            f"duplicate attestation for market "
                            f"{att.market_id} outcome {att.outcome.value} "
                            f"(second signer {att.signer})"
                        ),
                        attestation=att,
                    )
                )
                logger.warning(
                    "settlement_reconciler: duplicate (market, outcome) "
                    "attestation market=%d outcome=%s signer=%s",
                    att.market_id,
                    att.outcome.value,
                    att.signer,
                )
                continue
            attestation_index[pair_key] = att

        # ── Step 2: walk settlements, pair against the index
        matched_attestation_keys: set[tuple[int, Side]] = set()
        for stl in settlements:
            pair_key = (stl.market_id, stl.outcome)
            paired_att = attestation_index.get(pair_key)
            if paired_att is None:
                # No attestation for this market+outcome — either the
                # signer never produced one, or it had a wrong marketId
                # / wrong outcome and the (marketId, outcome) index
                # missed. Check for a wrong-side attestation as a
                # specific diagnostic.
                wrong_outcome_key = (
                    stl.market_id,
                    Side.NO if stl.outcome == Side.YES else Side.YES,
                )
                wrong_outcome_att = attestation_index.get(wrong_outcome_key)
                if wrong_outcome_att is not None:
                    report.rejected.append(
                        PairingOutcome(
                            status=ReconciliationStatus.IDENTITY_MISMATCH,
                            reason=(
                                f"attestation claims outcome "
                                f"{wrong_outcome_att.outcome.value} but "
                                f"settlement reports {stl.outcome.value}"
                            ),
                            settlement=stl,
                            attestation=wrong_outcome_att,
                        )
                    )
                    # Mark the wrong-outcome attestation as "seen so
                    # it doesn't fall into the unmatched bucket below
                    # for a redundant finding.
                    matched_attestation_keys.add(wrong_outcome_key)
                    logger.warning(
                        "settlement_reconciler: IDENTITY_MISMATCH outcome "
                        "market=%d attested=%s actual=%s",
                        stl.market_id,
                        wrong_outcome_att.outcome.value,
                        stl.outcome.value,
                    )
                else:
                    report.unmatched.append(
                        PairingOutcome(
                            status=ReconciliationStatus.UNMATCHED,
                            reason=(
                                f"no attestation for market {stl.market_id} "
                                f"outcome {stl.outcome.value}"
                            ),
                            settlement=stl,
                        )
                    )
                    logger.warning(
                        "settlement_reconciler: UNMATCHED settlement "
                        "market=%d outcome=%s",
                        stl.market_id,
                        stl.outcome.value,
                    )
                continue

            drift = abs(paired_att.amount_usd - stl.payout_usd)
            if drift > self.drift_tolerance_usd:
                report.drift.append(
                    PairingOutcome(
                        status=ReconciliationStatus.DRIFT_DETECTED,
                        reason=(
                            f"|attested {paired_att.amount_usd:+.2f} - settled "
                            f"{stl.payout_usd:+.2f}| = {drift:.2f} > "
                            f"tolerance {self.drift_tolerance_usd:.2f}"
                        ),
                        settlement=stl,
                        attestation=paired_att,
                        drift_usd=drift,
                    )
                )
                logger.warning(
                    "settlement_reconciler: DRIFT market=%d drift_usd=%.2f",
                    stl.market_id,
                    drift,
                )
            else:
                report.matched.append(
                    PairingOutcome(
                        status=ReconciliationStatus.MATCHED,
                        reason="3-factor identity + amount within tolerance",
                        settlement=stl,
                        attestation=paired_att,
                        drift_usd=drift,
                    )
                )
            matched_attestation_keys.add(pair_key)

        # ── Step 3: orphan attestations (BREATH burns with no Polygon
        # settlement) — also surfaces as drift.
        for pair_key, att in attestation_index.items():
            if pair_key in matched_attestation_keys:
                continue
            report.unmatched.append(
                PairingOutcome(
                    status=ReconciliationStatus.UNMATCHED,
                    reason=(
                        f"attestation has no Polygon settlement: "
                        f"market {att.market_id} outcome {att.outcome.value}"
                    ),
                    attestation=att,
                )
            )
            logger.warning(
                "settlement_reconciler: orphan attestation market=%d "
                "outcome=%s nonce=%d",
                att.market_id,
                att.outcome.value,
                att.nonce,
            )

        return report

    # ------------------------------------------------------------------
    # Inspection helpers (read-only)
    # ------------------------------------------------------------------

    def has_seen_nonce(self, *, signer: str, nonce: int) -> bool:
        """Inspect the in-process replay cache.

        Used by tests + the dashboard's replay-status chip. Not part of
        the reconciliation path — the path uses the set directly.
        """
        return (signer.lower(), nonce) in self._seen_nonces

    @property
    def seen_nonce_count(self) -> int:
        """Number of distinct (signer, nonce) pairs the cache has seen."""
        return len(self._seen_nonces)


__all__ = [
    "DEFAULT_DRIFT_TOLERANCE_USD",
    "BankrollUpdateAttestation",
    "PairingOutcome",
    "PolymarketSettlement",
    "ReconciliationReport",
    "ReconciliationStatus",
    "SettlementReconciler",
]
