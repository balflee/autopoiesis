"""Tests for :mod:`agent.ops.settlement_reconciler`.

The Tier 1 critical invariant per the brief is the **3-factor identity
pin**: a Polymarket settle event must pair against an attestation iff
``(nonce, marketId, outcome)`` all match, AND the nonce has never been
seen before for that signer.

Coverage:

* Happy path — single settlement pairs cleanly with a matching attestation.
* Replay protection — same (signer, nonce) seen twice → REPLAY_REJECTED.
* Identity mismatch on marketId — attestation for wrong market is unmatched.
* Identity mismatch on outcome — settlement says YES won, attestation
  claims NO won → IDENTITY_MISMATCH.
* Drift detection — attestation amount differs from settlement payout
  beyond the tolerance → DRIFT_DETECTED.
* Orphan attestation — BREATH burn on L3 with no matching Polygon
  settlement → UNMATCHED.
* Per-signer nonce isolation — two different signers with the same
  nonce values are independent (mirrors on-chain
  ``usedNonces[signer][nonce]``).
* Duplicate (market, outcome) attestations from two signers — second
  rejected.
"""

from __future__ import annotations

import pytest

from agent.core.state import Side
from agent.ops.settlement_reconciler import (
    BankrollUpdateAttestation,
    PolymarketSettlement,
    ReconciliationStatus,
    SettlementReconciler,
)

# Canonical 42-char addresses used across tests. Two distinct signers
# so the per-signer nonce isolation test has something to assert.
SIGNER_A = "0x" + "a" * 40
SIGNER_B = "0x" + "b" * 40


def _att(
    *,
    signer: str = SIGNER_A,
    market_id: int = 1,
    outcome: Side = Side.YES,
    nonce: int = 1,
    amount_usd: float = 100.0,
    deadline: int = 9_999_999_999,
) -> BankrollUpdateAttestation:
    """Test-helper for a well-formed attestation."""
    return BankrollUpdateAttestation(
        signer=signer,
        market_id=market_id,
        outcome=outcome,
        nonce=nonce,
        amount_usd=amount_usd,
        deadline=deadline,
    )


def _stl(
    *,
    market_id: int = 1,
    outcome: Side = Side.YES,
    payout_usd: float = 100.0,
    settled_at: str = "2026-05-23T12:00:00+00:00",
) -> PolymarketSettlement:
    """Test-helper for a well-formed settlement."""
    return PolymarketSettlement(
        market_id=market_id,
        outcome=outcome,
        payout_usd=payout_usd,
        settled_at=settled_at,
    )


def test_happy_path_pairs_three_factors_cleanly() -> None:
    """The default fixture matches in all three factors + amounts agree."""
    reconciler = SettlementReconciler()
    report = reconciler.reconcile(
        settlements=[_stl()],
        attestations=[_att()],
    )
    assert report.is_clean
    assert len(report.matched) == 1
    assert report.matched[0].status == ReconciliationStatus.MATCHED
    assert report.matched[0].drift_usd == 0.0


def test_replay_rejected_on_same_signer_nonce_reuse() -> None:
    """The exact same (signer, nonce) pair fed twice → second rejected."""
    reconciler = SettlementReconciler()
    a1 = _att(nonce=42)
    a2 = _att(nonce=42, market_id=2, outcome=Side.NO)  # different market+outcome
    # Run them through the SAME reconciler call so both appear in the
    # attestations stream.
    report = reconciler.reconcile(
        settlements=[_stl()],  # pairs with a1
        attestations=[a1, a2],
    )
    # a1 paired with the settlement
    assert len(report.matched) == 1
    # a2 should be REPLAY_REJECTED (same nonce as a1)
    assert len(report.rejected) == 1
    assert report.rejected[0].status == ReconciliationStatus.REPLAY_REJECTED
    assert "nonce 42" in report.rejected[0].reason
    # Cache reflects the seen nonce
    assert reconciler.has_seen_nonce(signer=SIGNER_A, nonce=42)


def test_replay_protection_persists_across_reconciler_calls() -> None:
    """A nonce seen in tick N MUST still be rejected in tick N+1."""
    reconciler = SettlementReconciler()
    reconciler.reconcile(
        settlements=[_stl()],
        attestations=[_att(nonce=7)],
    )
    # Second call with the same nonce — must be rejected.
    report = reconciler.reconcile(
        settlements=[],
        attestations=[_att(nonce=7, market_id=99)],
    )
    assert len(report.rejected) == 1
    assert report.rejected[0].status == ReconciliationStatus.REPLAY_REJECTED


def test_identity_mismatch_on_wrong_outcome() -> None:
    """Settlement says YES won; attestation claims NO won → mismatch."""
    reconciler = SettlementReconciler()
    report = reconciler.reconcile(
        settlements=[_stl(market_id=5, outcome=Side.YES)],
        attestations=[_att(market_id=5, outcome=Side.NO)],
    )
    assert len(report.rejected) == 1
    finding = report.rejected[0]
    assert finding.status == ReconciliationStatus.IDENTITY_MISMATCH
    assert "outcome" in finding.reason


def test_identity_mismatch_on_wrong_market_id() -> None:
    """Settlement on market 5; attestation on market 99 → unmatched."""
    reconciler = SettlementReconciler()
    report = reconciler.reconcile(
        settlements=[_stl(market_id=5, outcome=Side.YES)],
        attestations=[_att(market_id=99, outcome=Side.YES)],
    )
    assert len(report.unmatched) == 2  # 1 settlement orphan + 1 attestation orphan
    assert all(
        f.status == ReconciliationStatus.UNMATCHED for f in report.unmatched
    )


def test_drift_detected_when_amount_exceeds_tolerance() -> None:
    """|attested 100 - settled 105| = 5 > 1 USD tolerance → DRIFT."""
    reconciler = SettlementReconciler(drift_tolerance_usd=1.0)
    report = reconciler.reconcile(
        settlements=[_stl(payout_usd=105.0)],
        attestations=[_att(amount_usd=100.0)],
    )
    assert len(report.drift) == 1
    assert report.drift[0].status == ReconciliationStatus.DRIFT_DETECTED
    assert report.drift[0].drift_usd == pytest.approx(5.0)


def test_drift_within_tolerance_is_matched() -> None:
    """|attested 100 - settled 100.5| = 0.5 ≤ 1 USD tolerance → MATCHED."""
    reconciler = SettlementReconciler(drift_tolerance_usd=1.0)
    report = reconciler.reconcile(
        settlements=[_stl(payout_usd=100.5)],
        attestations=[_att(amount_usd=100.0)],
    )
    assert report.is_clean
    assert len(report.matched) == 1
    assert report.matched[0].drift_usd == pytest.approx(0.5)


def test_orphan_attestation_with_no_polygon_settlement() -> None:
    """A BREATH burn on L3 with no matching Polygon event → UNMATCHED."""
    reconciler = SettlementReconciler()
    report = reconciler.reconcile(
        settlements=[],
        attestations=[_att(market_id=5)],
    )
    assert len(report.unmatched) == 1
    assert report.unmatched[0].attestation is not None
    assert report.unmatched[0].settlement is None


def test_per_signer_nonce_isolation() -> None:
    """Two distinct signers with the same nonce values are INDEPENDENT.

    Mirrors the on-chain ``usedNonces[signer][nonce]`` mapping — each
    EnergyController.attestationSigner has its own monotonic counter.
    """
    reconciler = SettlementReconciler()
    report = reconciler.reconcile(
        settlements=[
            _stl(market_id=1, payout_usd=100.0),
            _stl(market_id=2, payout_usd=200.0),
        ],
        attestations=[
            _att(signer=SIGNER_A, market_id=1, nonce=1, amount_usd=100.0),
            _att(signer=SIGNER_B, market_id=2, nonce=1, amount_usd=200.0),
        ],
    )
    assert len(report.matched) == 2
    assert len(report.rejected) == 0
    assert reconciler.has_seen_nonce(signer=SIGNER_A, nonce=1)
    assert reconciler.has_seen_nonce(signer=SIGNER_B, nonce=1)


def test_case_insensitive_signer_comparison() -> None:
    """Mixed-case checksum addresses MUST NOT mask a replay."""
    reconciler = SettlementReconciler()
    upper = "0x" + "A" * 40
    lower = "0x" + "a" * 40
    reconciler.reconcile(
        settlements=[],
        attestations=[_att(signer=upper, nonce=11, market_id=88)],
    )
    # Same signer, same nonce, different case → still a replay.
    report = reconciler.reconcile(
        settlements=[],
        attestations=[_att(signer=lower, nonce=11, market_id=89)],
    )
    assert len(report.rejected) == 1
    assert report.rejected[0].status == ReconciliationStatus.REPLAY_REJECTED


def test_duplicate_market_outcome_from_two_signers_rejected() -> None:
    """Two different signers attest the same (market, outcome) — second
    must be IDENTITY_MISMATCH (duplicate-credit attempt)."""
    reconciler = SettlementReconciler()
    report = reconciler.reconcile(
        settlements=[_stl(market_id=7, outcome=Side.YES)],
        attestations=[
            _att(signer=SIGNER_A, market_id=7, outcome=Side.YES, nonce=1),
            _att(signer=SIGNER_B, market_id=7, outcome=Side.YES, nonce=1),
        ],
    )
    # First paired; second was a duplicate (market, outcome)
    assert len(report.matched) == 1
    assert len(report.rejected) == 1
    assert report.rejected[0].status == ReconciliationStatus.IDENTITY_MISMATCH


def test_report_is_clean_property_consistency() -> None:
    """is_clean ⇔ no rejected + no unmatched + no drift rows."""
    reconciler = SettlementReconciler()
    # Pure happy path
    report = reconciler.reconcile(
        settlements=[_stl()], attestations=[_att()],
    )
    assert report.is_clean

    # Drift path
    reconciler2 = SettlementReconciler(drift_tolerance_usd=0.10)
    report2 = reconciler2.reconcile(
        settlements=[_stl(payout_usd=200.0)],
        attestations=[_att(amount_usd=100.0)],
    )
    assert not report2.is_clean
    assert report2.total_pairings == 1


def test_invalid_signer_address_rejected_at_model_construction() -> None:
    """Pydantic enforces the 42-char address constraint at boundary."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BankrollUpdateAttestation(
            signer="0xshort",
            market_id=1,
            outcome=Side.YES,
            nonce=1,
            amount_usd=100.0,
            deadline=9_999_999_999,
        )
