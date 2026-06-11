"""Tests for :mod:`agent.staging.event_assertions`.

Covers:

* PASS: receipt contains both ``Phase3RolesRenounced`` topics, one
  per contract address.
* FAIL: receipt missing the EnergyController emission.
* FAIL: receipt missing the PhaseManager emission.
* FAIL: receipt has the topic but emitted from a wrong (third)
  address — spoofing-via-same-topic should NOT pass.
* Topic count helper across mixed log list.
* Canonical signature constant matches the on-chain ABIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent.staging.event_assertions import (
    PHASE3_RENUNCIATION_SIGNATURE,
    count_topic0,
    verify_phase3_renunciation,
)

# ── Test fixtures: deterministic addresses + a synthetic topic0 ──────


_EC_ADDR = "0x1111111111111111111111111111111111111111"
_PM_ADDR = "0x2222222222222222222222222222222222222222"
_OTHER_ADDR = "0x3333333333333333333333333333333333333333"
# Synthetic Keccak digest of "Phase3RolesRenounced(uint64)" — the
# verifier compares topic strings byte-stably, so any 32-byte hex
# works as long as both producer + verifier use the same value.
_TOPIC0_RENOUNCE = (
    "0xabcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
)
_TOPIC0_NOISE = (
    "0xdeadbeefcafebabe000000000000000000000000000000000000000000000000"
)


# ── Receipt fixture types — minimal Protocol-conformant fakes ────────


@dataclass(frozen=True)
class _FakeLog:
    address: str
    topics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _FakeReceipt:
    transactionHash: str
    logs: list[_FakeLog] = field(default_factory=list)


# ── PASS path ────────────────────────────────────────────────────────


def test_both_events_emitted_passes() -> None:
    """Happy path: receipt has Phase3RolesRenounced from BOTH addrs."""
    receipt = _FakeReceipt(
        transactionHash="0xphase3advancehappypath",
        logs=[
            _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
            _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is True
    assert check.pause_role_emitted is True
    assert check.upgrade_role_emitted is True
    assert check.missing == []
    assert check.transaction_hash == "0xphase3advancehappypath"


def test_pass_allows_extra_noise_logs() -> None:
    """Receipt with the canonical pair PLUS unrelated logs still passes."""
    receipt = _FakeReceipt(
        transactionHash="0xphase3withnoise",
        logs=[
            _FakeLog(address=_OTHER_ADDR, topics=[_TOPIC0_NOISE]),
            _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
            _FakeLog(address=_OTHER_ADDR, topics=[_TOPIC0_NOISE]),
            _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
            _FakeLog(address=_OTHER_ADDR, topics=[_TOPIC0_NOISE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is True


def test_pass_normalises_address_casing() -> None:
    """Checksummed mixed-case addresses must equal lower-case fixtures."""
    receipt = _FakeReceipt(
        transactionHash="0xphase3mixedcase",
        logs=[
            _FakeLog(address=_EC_ADDR.upper(), topics=[_TOPIC0_RENOUNCE.upper()]),
            _FakeLog(address=_PM_ADDR.upper(), topics=[_TOPIC0_RENOUNCE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is True


# ── FAIL: missing EC emission ────────────────────────────────────────


def test_missing_energy_controller_event_fails() -> None:
    """Only PhaseManager emitted — EC must be flagged missing."""
    receipt = _FakeReceipt(
        transactionHash="0xphase3missingec",
        logs=[
            _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is False
    assert check.pause_role_emitted is False
    assert check.upgrade_role_emitted is True
    assert any("PauseRoleRenounced" in m for m in check.missing)


# ── FAIL: missing PM emission ────────────────────────────────────────


def test_missing_phase_manager_event_fails() -> None:
    """Only EnergyController emitted — PM must be flagged missing."""
    receipt = _FakeReceipt(
        transactionHash="0xphase3missingpm",
        logs=[
            _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is False
    assert check.pause_role_emitted is True
    assert check.upgrade_role_emitted is False
    assert any("UpgradeRoleRenounced" in m for m in check.missing)


# ── FAIL: spoof from a third address (same topic, wrong source) ──────


def test_wrong_address_with_correct_topic_fails() -> None:
    """An attacker contract emitting the same topic must NOT satisfy
    the check — both contracts must be the real EC and PM addresses."""
    receipt = _FakeReceipt(
        transactionHash="0xphase3spoofed",
        logs=[
            _FakeLog(address=_OTHER_ADDR, topics=[_TOPIC0_RENOUNCE]),
            _FakeLog(address=_OTHER_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is False
    assert check.pause_role_emitted is False
    assert check.upgrade_role_emitted is False
    assert len(check.missing) == 2


def test_empty_receipt_fails() -> None:
    """A receipt with zero logs reports both addresses missing."""
    receipt = _FakeReceipt(transactionHash="0xempty", logs=[])
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0=_TOPIC0_RENOUNCE,
    )
    assert check.both_emitted is False


def test_empty_topic0_argument_fails_closed() -> None:
    """An empty/blank topic0 argument must not silently pass."""
    receipt = _FakeReceipt(
        transactionHash="0xempty_topic_call",
        logs=[
            _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
            _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
        ],
    )
    check = verify_phase3_renunciation(
        receipt=receipt,
        energy_controller_address=_EC_ADDR,
        phase_manager_address=_PM_ADDR,
        renunciation_topic0="",
    )
    assert check.both_emitted is False


# ── count_topic0 helper ──────────────────────────────────────────────


def test_count_topic0_basic() -> None:
    """Mixed-topic log list counts each matching topic once."""
    logs = [
        _FakeLog(address=_EC_ADDR, topics=[_TOPIC0_RENOUNCE]),
        _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
        _FakeLog(address=_OTHER_ADDR, topics=[_TOPIC0_NOISE]),
        _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
    ]
    assert count_topic0(logs, _TOPIC0_RENOUNCE) == 3
    assert count_topic0(logs, _TOPIC0_NOISE) == 1
    assert count_topic0(logs, "0x" + "0" * 64) == 0


def test_count_topic0_handles_empty_topics_list() -> None:
    """A log with no topics is silently skipped (not a count)."""
    logs = [
        _FakeLog(address=_EC_ADDR, topics=[]),
        _FakeLog(address=_PM_ADDR, topics=[_TOPIC0_RENOUNCE]),
    ]
    assert count_topic0(logs, _TOPIC0_RENOUNCE) == 1


# ── Canonical signature anchor — proves the constant matches ABI ─────


def test_signature_constant_matches_abi() -> None:
    """``PHASE3_RENUNCIATION_SIGNATURE`` must match the canonical event
    string the on-chain ABI declares.

    A future ABI bump that changes the parameter list would surface
    as either an unmatching signature here OR a missing field on
    the ABI — both fail this test, forcing the consumer to update
    the constant + bump the consuming task's brief.
    """
    # Build the canonical signature string from the v0.4.0
    # EnergyController ABI's Phase3RolesRenounced event entry.
    ec_abi = json.loads(
        Path(".dev/contracts/energy_controller_abi.v0.4.0.json").read_text(
            encoding="utf-8",
        )
    )
    event = next(
        e for e in ec_abi["abi"]
        if e.get("type") == "event" and e.get("name") == "Phase3RolesRenounced"
    )
    param_types = ",".join(i["type"] for i in event["inputs"])
    canonical = f"Phase3RolesRenounced({param_types})"
    assert canonical == PHASE3_RENUNCIATION_SIGNATURE

    # Same anchor on the PhaseManager ABI — both contracts must
    # declare the SAME signature for the renunciation invariant to
    # hold.
    pm_abi = json.loads(
        Path(".dev/contracts/phase_manager_abi.v0.3.0.json").read_text(
            encoding="utf-8",
        )
    )
    pm_event = next(
        e for e in pm_abi["abi"]
        if e.get("type") == "event" and e.get("name") == "Phase3RolesRenounced"
    )
    pm_canonical = "Phase3RolesRenounced(" + ",".join(
        i["type"] for i in pm_event["inputs"]
    ) + ")"
    assert pm_canonical == PHASE3_RENUNCIATION_SIGNATURE
