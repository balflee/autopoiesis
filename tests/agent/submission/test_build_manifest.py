"""Tests for :mod:`agent.submission.build_manifest`.

Covers:

* Happy path — placeholder build using the real ABI registry under
  ``.dev/contracts/`` produces a manifest that round-trips through
  Pydantic + JSON.
* Fail mode 1: missing rehearsal report → exit code 2.
* Fail mode 2: ABI hash mismatch vs expected → exit code 3.
* CLI dispatch + end-to-end markdown rendering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.submission import build_manifest as bm_module
from agent.submission.build_manifest import (
    AbiHashMismatchError,
    DEFAULT_CONTRACTS,
    PROJECT_TITLE,
    RehearsalReportMissingError,
    SCHEMA_VERSION,
    SubmissionManifest,
    build_manifest,
    compute_abi_hashes,
    load_rehearsal_summary,
    main,
    manifest_to_json,
    verify_abi_hashes,
)


# Repo root — the worktree's CWD has .dev/contracts under it.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REAL_REGISTRY = _REPO_ROOT / ".dev" / "contracts"


# ── Test helpers ─────────────────────────────────────────────────────


def _write_rehearsal_report(
    path: Path,
    *,
    passed: bool = True,
    fail_reason: str | None = None,
) -> None:
    """Write a minimal but field-complete RehearsalReport JSON dump."""
    payload: dict[str, Any] = {
        # Dumped via model_dump(by_alias=True) ⇒ "pass" key.
        "pass": passed,
        "fail_reason": fail_reason,
        "desperate_mode_count": 2,
        "lung_expansion_count": 1,
        "settlement_count": 3,
        "ws_disconnect_count": 0,
        "pause_role_renounced_tx": (
            "0x" + "ab" * 32 if passed else None
        ),
        "upgrade_role_renounced_tx": (
            "0x" + "cd" * 32 if passed else None
        ),
        "timeline_events": [],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture
def rehearsal_report(tmp_path: Path) -> Path:
    """A passing rehearsal report on disk."""
    p = tmp_path / "rehearsal_report.json"
    _write_rehearsal_report(p, passed=True)
    return p


@pytest.fixture
def fixed_now() -> str:
    """Deterministic generated_at value so tests assert byte-stable output."""
    return "2026-05-24T12:00:00Z"


# ── Happy path ───────────────────────────────────────────────────────


def test_build_manifest_happy_path(
    rehearsal_report: Path, fixed_now: str
) -> None:
    """End-to-end: real ABI dir + passing rehearsal report ⇒ valid manifest."""
    manifest = build_manifest(
        commit_hash="abcdef1234567890",
        rehearsal_report_path=rehearsal_report,
        abi_registry_dir=_REAL_REGISTRY,
        now_iso=fixed_now,
    )

    # Top-level invariants.
    assert isinstance(manifest, SubmissionManifest)
    assert manifest.project_title == PROJECT_TITLE
    assert manifest.schema_version == SCHEMA_VERSION
    assert manifest.commit_hash == "abcdef1234567890"
    assert manifest.generated_at == fixed_now

    # Three chains per PRD §10.
    assert len(manifest.deploys) == 3
    chain_keys = [d.chain for d in manifest.deploys]
    assert chain_keys == [
        "robinhood_chain_testnet",
        "arbitrum_sepolia",
        "polygon_amoy",
    ]
    for deploy in manifest.deploys:
        assert deploy.placeholder is True
        assert len(deploy.contracts) == len(DEFAULT_CONTRACTS)

    # Five ABI hash entries — one per core contract.
    assert {entry.contract for entry in manifest.abi_hashes} == {
        name for name, _ in DEFAULT_CONTRACTS
    }
    for entry in manifest.abi_hashes:
        assert len(entry.sha256) == 64
        assert all(c in "0123456789abcdef" for c in entry.sha256)

    # Three Phase-3 launch records aligned with deploys.
    assert [r.chain for r in manifest.phase3_launch] == chain_keys

    # Rehearsal summary echoes the fixture payload.
    s = manifest.rehearsal_summary
    assert s.passed is True
    assert s.fail_reason is None
    assert s.desperate_mode_count == 2
    assert s.lung_expansion_count == 1
    assert s.settlement_count == 3
    assert s.ws_disconnect_count == 0
    assert s.pause_role_renounced_tx is not None
    assert s.upgrade_role_renounced_tx is not None


def test_build_manifest_round_trips_through_json(
    rehearsal_report: Path, fixed_now: str
) -> None:
    """Serialise → parse → re-validate yields an equal manifest."""
    manifest = build_manifest(
        commit_hash="abcdef1",  # min length 7
        rehearsal_report_path=rehearsal_report,
        abi_registry_dir=_REAL_REGISTRY,
        now_iso=fixed_now,
    )
    text = manifest_to_json(manifest)
    parsed = json.loads(text)
    reloaded = SubmissionManifest.model_validate(parsed)
    assert reloaded == manifest


def test_compute_abi_hashes_picks_highest_version() -> None:
    """``compute_abi_hashes`` resolves to the highest semver-sorted ABI file."""
    entries = compute_abi_hashes(_REAL_REGISTRY)
    by_contract = {e.contract: e for e in entries}
    # EnergyController is at v0.4.0 in the registry (sprint_5).
    assert by_contract["EnergyController"].abi_version == "0.4.0"
    # PhaseManager is at v0.3.0 (sprint_5).
    assert by_contract["PhaseManager"].abi_version == "0.3.0"


# ── Fail mode 1: missing rehearsal report ────────────────────────────


def test_missing_rehearsal_report_raises(tmp_path: Path) -> None:
    """A non-existent rehearsal report path ⇒ RehearsalReportMissingError."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(RehearsalReportMissingError):
        build_manifest(
            commit_hash="abcdef1234567890",
            rehearsal_report_path=missing,
            abi_registry_dir=_REAL_REGISTRY,
        )


def test_missing_rehearsal_report_cli_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI surfaces RehearsalReportMissingError as exit code 2."""
    missing = tmp_path / "does_not_exist.json"
    out_path = tmp_path / "SUBMISSION.json"
    rc = main(
        [
            "--commit", "abcdef1234567890",
            "--rehearsal-report", str(missing),
            "--out", str(out_path),
            "--abi-registry-dir", str(_REAL_REGISTRY),
            "--now", "2026-05-24T12:00:00Z",
        ]
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "Rehearsal report unavailable" in captured.err
    assert not out_path.exists()


def test_malformed_rehearsal_report_raises(tmp_path: Path) -> None:
    """Non-JSON content ⇒ RehearsalReportMissingError (with clear message)."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(RehearsalReportMissingError):
        load_rehearsal_summary(bad)


# ── Fail mode 2: ABI hash mismatch ───────────────────────────────────


def test_verify_abi_hashes_detects_mismatch() -> None:
    """``verify_abi_hashes`` raises with a per-contract diff list."""
    entries = compute_abi_hashes(_REAL_REGISTRY)
    expected_bad: dict[str, str] = {
        "EnergyController": "0" * 64,  # deliberately wrong
    }
    with pytest.raises(AbiHashMismatchError) as exc_info:
        verify_abi_hashes(entries, expected_bad)
    # The exception payload carries (name, expected, actual) tuples.
    mismatches = exc_info.value.args[0]
    assert any(m[0] == "EnergyController" for m in mismatches)


def test_verify_abi_hashes_passes_when_all_match() -> None:
    """When every expected hash matches computed, returns silently."""
    entries = compute_abi_hashes(_REAL_REGISTRY)
    # Build expected from computed itself ⇒ trivially correct.
    expected = {e.contract: e.sha256 for e in entries}
    verify_abi_hashes(entries, expected)  # must not raise


def test_abi_hash_mismatch_cli_exit_code_3(
    tmp_path: Path,
    rehearsal_report: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI surfaces AbiHashMismatchError as exit code 3 + no manifest written."""
    expected_bad = tmp_path / "expected.json"
    expected_bad.write_text(
        json.dumps({"EnergyController": "0" * 64}),
        encoding="utf-8",
    )
    out_path = tmp_path / "SUBMISSION.json"
    rc = main(
        [
            "--commit", "abcdef1234567890",
            "--rehearsal-report", str(rehearsal_report),
            "--out", str(out_path),
            "--abi-registry-dir", str(_REAL_REGISTRY),
            "--expected-abi-hashes", str(expected_bad),
            "--now", "2026-05-24T12:00:00Z",
        ]
    )
    assert rc == 3
    captured = capsys.readouterr()
    assert "ABI hash mismatch" in captured.err
    assert not out_path.exists()


def test_abi_hash_match_cli_exit_code_0(
    tmp_path: Path,
    rehearsal_report: Path,
) -> None:
    """When expected hashes match computed, CLI exits 0 + writes manifest."""
    entries = compute_abi_hashes(_REAL_REGISTRY)
    expected_good = tmp_path / "expected.json"
    expected_good.write_text(
        json.dumps({e.contract: e.sha256 for e in entries}),
        encoding="utf-8",
    )
    out_path = tmp_path / "SUBMISSION.json"
    rc = main(
        [
            "--commit", "abcdef1234567890",
            "--rehearsal-report", str(rehearsal_report),
            "--out", str(out_path),
            "--abi-registry-dir", str(_REAL_REGISTRY),
            "--expected-abi-hashes", str(expected_good),
            "--now", "2026-05-24T12:00:00Z",
        ]
    )
    assert rc == 0
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["commit_hash"] == "abcdef1234567890"


# ── CLI end-to-end + markdown rendering ──────────────────────────────


def test_cli_renders_markdown_when_requested(
    tmp_path: Path,
    rehearsal_report: Path,
) -> None:
    """``--render-markdown <path>`` writes a SUBMISSION.md alongside JSON."""
    out_json = tmp_path / "SUBMISSION.json"
    out_md = tmp_path / "SUBMISSION.md"
    rc = main(
        [
            "--commit", "abcdef1234567890",
            "--rehearsal-report", str(rehearsal_report),
            "--out", str(out_json),
            "--abi-registry-dir", str(_REAL_REGISTRY),
            "--render-markdown", str(out_md),
            "--now", "2026-05-24T12:00:00Z",
        ]
    )
    assert rc == 0
    md = out_md.read_text(encoding="utf-8")

    # Required sections per task brief acceptance criteria.
    assert PROJECT_TITLE in md
    assert "Deployed Contracts" in md
    assert "ABI Hashes" in md
    assert "Phase 3 Launch" in md
    assert "Demo Video" in md
    assert "Pre-Demo Staging Rehearsal" in md
    # Generation timestamp surfaces in the header.
    assert "2026-05-24T12:00:00Z" in md
    # Renderer marks placeholder rows so reviewers spot them.
    assert "placeholder" in md.lower()
    # All five contract names show up in the contract table.
    for contract_name, _ in DEFAULT_CONTRACTS:
        assert contract_name in md


def test_cli_writes_canonical_json(
    tmp_path: Path,
    rehearsal_report: Path,
) -> None:
    """Output JSON is human-readable + ends with newline (POSIX-clean)."""
    out_json = tmp_path / "SUBMISSION.json"
    rc = main(
        [
            "--commit", "abcdef1234567890",
            "--rehearsal-report", str(rehearsal_report),
            "--out", str(out_json),
            "--abi-registry-dir", str(_REAL_REGISTRY),
            "--now", "2026-05-24T12:00:00Z",
        ]
    )
    assert rc == 0
    text = out_json.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # 2-space indented ⇒ first key line is "  \"project_title\": ..."
    assert "  \"project_title\":" in text


# ── Sanity: module-level constants are coherent ──────────────────────


def test_default_chains_and_contracts_are_stable() -> None:
    """Module constants — the manifest contract depends on these."""
    assert len(bm_module.DEFAULT_CHAINS) == 3
    assert len(bm_module.DEFAULT_CONTRACTS) == 5
    chain_keys = {c[0] for c in bm_module.DEFAULT_CHAINS}
    assert chain_keys == {
        "robinhood_chain_testnet",
        "arbitrum_sepolia",
        "polygon_amoy",
    }
    contract_names = {c[0] for c in bm_module.DEFAULT_CONTRACTS}
    assert contract_names == {
        "EnergyController",
        "PhaseManager",
        "AgentLifecycle",
        "DecisionLog",
        "TombstoneNFT",
    }
