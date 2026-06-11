"""Submission manifest builder — Day-20 Demo handoff package.

Spec anchors
------------

* TECHNICAL_PLAN §8 Day 20 — "Demo 视频剪辑 + Pitch deck 制作 + 提交材料
  → 提交包" (Demo video edit + pitch deck + submission materials →
  submission package).
* TECHNICAL_PLAN §12 Demo Insurance — "Demo 视频应该明确演示「按下
  Phase 3 启动按钮 → 同时看到 pause/upgrade role 被 burn 的交易上链」"
  (the video must explicitly show the Phase-3 launch button press
  alongside both role-renunciation txes landing on chain).
* TECHNICAL_PLAN §15 Gap 7 — Pre-Demo Staging rehearsal produces the
  RehearsalReport this builder ingests.
* PRD §10 — three-chain parallel deploy: Robinhood Chain testnet
  (primary), Arbitrum Sepolia (hot fallback), Polygon Amoy.
* PRD §5.1 — Phase 3 launch tx emits BOTH ``PauseRoleRenounced`` and
  ``UpgradeRoleRenounced``; the launch tx and the renunciation event
  source are the same transaction.

What this builds
----------------

A single ``SUBMISSION.json`` payload that anchors:

* The git ``commit_hash`` of the deployed agent + contracts.
* Per-chain ``deploys`` (3 entries, one each for Robinhood / Arbitrum
  / Polygon) — each holds the deployed contract addresses.
* ``abi_hashes`` — canonical sha256 of every contract's ABI (see
  :mod:`agent.submission.abi_hasher` for the canonicalisation rules).
* ``ipfs_roots`` — IPFS CIDs of memory-bank snapshots / tombstone NFT
  metadata.
* ``demo_video`` — URL + sha256 of the recorded demo clip.
* ``phase3_launch`` — per-chain Phase-3 advance tx hash plus the
  EnergyController + PhaseManager ``Phase3RolesRenounced`` event tx
  hashes (in the single-tx-renunciation design these all match, but
  the schema lets a future fork emit them separately).
* ``rehearsal_summary`` — the staging-runner verdict, summarised from
  the JSON ``RehearsalReport`` the caller passes via
  ``--rehearsal-report``.
* ``generated_at`` — ISO-8601 UTC timestamp the manifest was built.

Placeholder discipline
----------------------

Day-20 may run BEFORE all the live deploys / IPFS pins / demo video
exists. Every chain-specific section carries a boolean ``placeholder``
flag; tests that the builder produces a fully populated structure use
placeholder values for fields they cannot fill yet. The reviewer's
"no falsified evidence" rule means: when ``placeholder`` is ``True``,
the address / cid / hash fields are obviously synthetic
(``"0x" + "0"*40``, ``"placeholder"`` sentinel CIDs). Production runs
flip ``placeholder`` to ``False`` per-section as real data lands; an
all-placeholder=False manifest is the gate for the Day-21 submission.

CLI surface
-----------

.. code-block:: console

   python -m agent.submission.build_manifest \\
       --commit <gitsha> \\
       --rehearsal-report .dev/integration_tests/.../rehearsal_report.json \\
       --out submission/SUBMISSION.json \\
       [--abi-registry-dir .dev/contracts] \\
       [--expected-abi-hashes <file>] \\
       [--render-markdown <path>] \\
       [--now <iso8601>]

Exit codes:

* ``0`` — manifest written successfully.
* ``2`` — input I/O error (missing rehearsal report, missing ABI dir).
* ``3`` — ABI hash mismatch vs ``--expected-abi-hashes`` file.
* ``4`` — manifest validation error (schema or invariants).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.submission.abi_hasher import hash_abi_file

SCHEMA_VERSION: Final[str] = "1.1.0"
PROJECT_TITLE: Final[str] = "The Genesis Experiment"

# Per PRD §10 三链平行. Order is intentionally stable — Robinhood
# first because that's the primary deploy; Arbitrum second (hot
# fallback); Polygon third. The downstream markdown renderer reads
# this same order so the contract table is layout-stable across runs.
DEFAULT_CHAINS: Final[tuple[tuple[str, int, str], ...]] = (
    ("robinhood_chain_testnet", 46630, "https://explorer.testnet.chain.robinhood.com/"),
    ("arbitrum_sepolia", 421614, "https://sepolia.arbiscan.io/"),
    ("polygon_amoy", 80002, "https://amoy.polygonscan.com/"),
)

# Per PRD §5.1 + §10. Every chain deploys the same five .sol files via
# the single ``Deploy.s.sol`` script. The list is locked here so the
# manifest's abi_hashes section has a known, stable contract roster.
DEFAULT_CONTRACTS: Final[tuple[tuple[str, str], ...]] = (
    ("EnergyController", "energy_controller_abi"),
    ("PhaseManager", "phase_manager_abi"),
    ("AgentLifecycle", "agent_lifecycle_abi"),
    ("DecisionLog", "decision_log_abi"),
    ("TombstoneNFT", "tombstone_nft_abi"),
)

# Synthetic-but-recognisable placeholder values. We picked these
# shapes deliberately so a reviewer scanning a Day-20 placeholder
# manifest immediately spots the placeholder=true sections without
# decoding hashes manually.
_PLACEHOLDER_ADDRESS: Final[str] = "0x" + "0" * 40
_PLACEHOLDER_TX_HASH: Final[str] = "0x" + "0" * 64
_PLACEHOLDER_SHA256: Final[str] = "0" * 64
_PLACEHOLDER_CID: Final[str] = "bafyplaceholder0000000000000000000000000000000000000000000"
_PLACEHOLDER_URL: Final[str] = "https://placeholder.invalid/demo.mp4"


# ---------------------------------------------------------------------------
# Pydantic wire schema
# ---------------------------------------------------------------------------


class ContractDeploy(BaseModel):
    """One ``contract → address`` mapping inside a chain's deploy block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1)]
    address: Annotated[str, Field(min_length=1)]


class ChainDeploy(BaseModel):
    """One chain's deploy snapshot.

    Fields
    ------
    chain : human-readable chain key (e.g. ``"polygon_amoy"``).
    chain_id : EIP-155 chain id.
    explorer_url : block-explorer base URL the renderer uses to build
        per-tx links (always ends in ``/``).
    contracts : ordered list of the five core contract → address pairs.
    deploy_block : block height of the deploy tx (``0`` when placeholder).
    placeholder : ``True`` iff this section is a Day-20 placeholder.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chain: Annotated[str, Field(min_length=1)]
    chain_id: Annotated[int, Field(ge=1)]
    explorer_url: Annotated[str, Field(min_length=1)]
    contracts: list[ContractDeploy]
    deploy_block: Annotated[int, Field(ge=0)]
    placeholder: bool


class AbiHashEntry(BaseModel):
    """One contract → canonical-ABI-sha256 mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Annotated[str, Field(min_length=1)]
    abi_file: Annotated[str, Field(min_length=1)]
    abi_version: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class IpfsRoot(BaseModel):
    """One IPFS root we anchor in the manifest.

    Examples: ``"memory_bank_v1"`` (the final memory-bank export the
    Tombstone NFT references) or ``"demo_assets"`` (graphics bundle
    pinned for the demo dashboard).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1)]
    cid: Annotated[str, Field(min_length=1)]
    placeholder: bool


class DemoVideo(BaseModel):
    """The recorded demo clip the judges watch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    duration_seconds: Annotated[int, Field(ge=0)]
    placeholder: bool


class Phase3LaunchRecord(BaseModel):
    """Per-chain Phase-3 advance tx + role-renunciation event txes.

    Fields
    ------
    chain : matches a ``ChainDeploy.chain`` entry.
    launch_tx : the Phase-3 advance tx that called ``lockPhase3()``.
    pause_role_renounced_tx : tx hash that emitted
        ``EnergyController.Phase3RolesRenounced`` (= PauseRoleRenounced).
    upgrade_role_renounced_tx : tx hash that emitted
        ``PhaseManager.Phase3RolesRenounced`` (= UpgradeRoleRenounced).
    block_number : block the launch tx landed in.
    placeholder : ``True`` iff this is a Day-20 placeholder.

    In the canonical design (PRD §5.1 + §10), the launch tx atomically
    triggers BOTH role-renunciation events, so the three tx-hash fields
    are typically equal. The schema lets them differ to leave room for
    a future fork that splits the operations.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chain: Annotated[str, Field(min_length=1)]
    launch_tx: Annotated[str, Field(min_length=1)]
    pause_role_renounced_tx: Annotated[str, Field(min_length=1)]
    upgrade_role_renounced_tx: Annotated[str, Field(min_length=1)]
    block_number: Annotated[int, Field(ge=0)]
    placeholder: bool


class Phase1TrainingSummary(BaseModel):
    """Compact view of the Phase-1 backtest verdict (Sprint 7 tennis pivot).

    Sprint 7 trained the 6-parameter fusion model (W_R, α₁/α₂/α₃, β₁=0
    frozen, β₂, ρ) on the Sackmann tennis corpus per PRD §3 / §15 已决 #8.
    This block anchors the trained weights + headline backtest metrics
    into the submission manifest so a judge sees the sprint-7 result
    without opening a separate report.

    Source: ``reports/phase1/weights_v0.json`` +
    ``reports/phase1/backtest_report.json`` (produced by
    ``agent.training.tennis_runner`` in T-B-015 round 1).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sport: Annotated[str, Field(min_length=1)]
    dataset_path: Annotated[str, Field(min_length=1)]
    weights_path: Annotated[str, Field(min_length=1)]
    backtest_report_path: Annotated[str, Field(min_length=1)]
    training_matches: Annotated[int, Field(ge=0)]
    test_matches: Annotated[int, Field(ge=0)]
    epochs: Annotated[int, Field(ge=0)]
    uniform_baseline_log_loss: float
    trained_log_loss: float
    improvement_pct: float
    weights_v0: dict[str, Any]


class Phase2DryRunSummary(BaseModel):
    """Compact view of the Sprint-7 Phase-2 dry-run verdict (T-B-016).

    Anchors the four CEO acceptance criteria + the safety invariant
    into the manifest so a judge sees the sprint closer at a glance.

    Source: ``logs/phase2_dryrun/sprint7_dryrun.jsonl`` +
    ``logs/phase2_dryrun/sprint7_dryrun_summary.md`` (produced by
    :func:`agent.runtime.sprint7_dryrun.run_dryrun`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    log_path: Annotated[str, Field(min_length=1)]
    summary_path: Annotated[str, Field(min_length=1)]
    decisions_count: Annotated[int, Field(ge=0)]
    bets_count: Annotated[int, Field(ge=0)]
    no_bets_count: Annotated[int, Field(ge=0)]
    heartbeat_count: Annotated[int, Field(ge=0)]
    markets_used: Annotated[int, Field(ge=0)]
    broadcast_count: Annotated[int, Field(ge=0)]
    real_market_referenced: bool


class RehearsalSummary(BaseModel):
    """Compact view of the Gap-7 staging rehearsal verdict.

    Mirrors the subset of :class:`agent.staging.rehearsal_runner.RehearsalReport`
    a judge cares about. We do NOT inline the full ``timeline_events``
    list — those can be enormous; the manifest carries the relative
    path so the judge can open the underlying report if needed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_path: Annotated[str, Field(min_length=1)]
    passed: bool
    fail_reason: str | None = None
    desperate_mode_count: Annotated[int, Field(ge=0)]
    lung_expansion_count: Annotated[int, Field(ge=0)]
    settlement_count: Annotated[int, Field(ge=0)]
    ws_disconnect_count: Annotated[int, Field(ge=0)]
    pause_role_renounced_tx: str | None = None
    upgrade_role_renounced_tx: str | None = None


class SubmissionManifest(BaseModel):
    """The full SUBMISSION.json payload.

    The schema is intentionally flat — every section is a discrete,
    independently-fillable block so Day-20 can ship a partially
    populated manifest and Day-21 can flip ``placeholder`` flags
    section by section.
    """

    model_config = ConfigDict(extra="forbid")

    project_title: Annotated[str, Field(min_length=1)]
    schema_version: Annotated[str, Field(min_length=1)]
    commit_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]
    generated_at: Annotated[str, Field(min_length=1)]
    deploys: list[ChainDeploy]
    abi_hashes: list[AbiHashEntry]
    ipfs_roots: list[IpfsRoot]
    demo_video: DemoVideo
    phase3_launch: list[Phase3LaunchRecord]
    rehearsal_summary: RehearsalSummary
    # Schema v1.1.0 (sprint_7 tennis pivot): optional Phase-1 backtest
    # + Phase-2 dry-run summary blocks. Both default to None so v1.0.0
    # consumers continue to validate against the new schema (additive
    # change; v1.0.0 manifests are a strict subset of v1.1.0).
    phase1_training_summary: Phase1TrainingSummary | None = None
    phase2_dryrun_summary: Phase2DryRunSummary | None = None


# ---------------------------------------------------------------------------
# Builder errors — narrow exit-code carriers.
# ---------------------------------------------------------------------------


class BuildError(Exception):
    """Base class for build-manifest errors.

    ``code`` is the CLI exit code; ``label`` is the stderr prefix.
    The CLI ``main`` catches :class:`BuildError` once and reads both
    fields, so adding a new subclass only requires setting these two
    class attributes.
    """

    code: int = 1
    label: str = "Build error"


class RehearsalReportMissingError(BuildError):
    """The path passed via ``--rehearsal-report`` does not exist or is unreadable."""

    code = 2
    label = "Rehearsal report unavailable"


class AbiRegistryMissingError(BuildError):
    """The ``--abi-registry-dir`` is missing or does not contain expected files."""

    code = 2
    label = "ABI registry unavailable"


class AbiHashMismatchError(BuildError):
    """At least one contract's computed ABI hash does not match the expected map.

    The exception's ``args`` carries a list of ``(contract, expected,
    actual)`` tuples so callers (CLI + tests) can render a precise
    diff.
    """

    code = 3
    label = "ABI hash mismatch"


class ManifestValidationError(BuildError):
    """The assembled manifest failed Pydantic validation. Wraps the underlying error."""

    code = 4
    label = "Manifest validation failed"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """Return a Z-suffixed second-precision UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _resolve_abi_file(registry_dir: Path, abi_basename: str) -> tuple[Path, str]:
    """Find the highest-versioned ``<abi_basename>.v<X.Y.Z>.json`` file.

    The ``.dev/contracts/`` registry holds multiple historical versions
    (e.g. ``energy_controller_abi.v0.1.0.json``, ``...v0.4.0.json``).
    The submission manifest anchors the CURRENT version — i.e. the
    highest semver-sorted file matching the basename. We compare
    tuples of ``(major, minor, patch)`` parsed from the ``vX.Y.Z``
    segment; non-conforming filenames are skipped.
    """
    pattern = f"{abi_basename}.v*.json"
    candidates = sorted(registry_dir.glob(pattern))
    if not candidates:
        raise AbiRegistryMissingError(
            f"No ABI files matching {pattern!r} in {registry_dir}"
        )
    best: tuple[tuple[int, int, int], Path, str] | None = None
    for path in candidates:
        # filename: e.g. "energy_controller_abi.v0.4.0.json"
        stem = path.name.removesuffix(".json")
        # find the ".v" anchor before the version triple
        idx = stem.rfind(".v")
        if idx < 0:
            continue
        version_str = stem[idx + 2 :]
        parts = version_str.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        triple = (int(parts[0]), int(parts[1]), int(parts[2]))
        if best is None or triple > best[0]:
            best = (triple, path, version_str)
    if best is None:
        raise AbiRegistryMissingError(
            f"No semver-conforming filenames among {pattern!r} in {registry_dir}"
        )
    return best[1], best[2]


def compute_abi_hashes(
    registry_dir: Path,
    contracts: tuple[tuple[str, str], ...] = DEFAULT_CONTRACTS,
) -> list[AbiHashEntry]:
    """Compute the canonical-form ABI hash for every contract in *contracts*.

    Returns a list in the same order as *contracts* (stable for the
    markdown renderer). Raises :class:`AbiRegistryMissingError` if any
    contract's ABI file cannot be located.
    """
    if not registry_dir.is_dir():
        raise AbiRegistryMissingError(
            f"ABI registry dir not found: {registry_dir}"
        )
    entries: list[AbiHashEntry] = []
    for contract_name, abi_basename in contracts:
        abi_path, abi_version = _resolve_abi_file(registry_dir, abi_basename)
        digest = hash_abi_file(abi_path)
        entries.append(
            AbiHashEntry(
                contract=contract_name,
                abi_file=abi_path.name,
                abi_version=abi_version,
                sha256=digest,
            )
        )
    return entries


def verify_abi_hashes(
    computed: list[AbiHashEntry],
    expected: dict[str, str],
) -> None:
    """Raise :class:`AbiHashMismatchError` if any computed hash differs.

    *expected* is a flat ``{contract_name: sha256_hex}`` map. Contracts
    that appear in *expected* but NOT in *computed* are reported as
    missing-with-expected-hash (a registry shrink); the converse
    (extra computed contracts) is allowed silently so adding a new
    contract does not retroactively fail older expected maps.
    """
    mismatches: list[tuple[str, str, str]] = []
    by_name = {entry.contract: entry.sha256 for entry in computed}
    for name, expected_sha in expected.items():
        actual_sha = by_name.get(name)
        if actual_sha is None:
            mismatches.append((name, expected_sha, "<not-computed>"))
        elif actual_sha != expected_sha:
            mismatches.append((name, expected_sha, actual_sha))
    if mismatches:
        raise AbiHashMismatchError(mismatches)


def _placeholder_deploys() -> list[ChainDeploy]:
    """Build the placeholder deploy section — one entry per default chain."""
    out: list[ChainDeploy] = []
    for chain_key, chain_id, explorer_url in DEFAULT_CHAINS:
        contracts = [
            ContractDeploy(name=name, address=_PLACEHOLDER_ADDRESS)
            for name, _ in DEFAULT_CONTRACTS
        ]
        out.append(
            ChainDeploy(
                chain=chain_key,
                chain_id=chain_id,
                explorer_url=explorer_url,
                contracts=contracts,
                deploy_block=0,
                placeholder=True,
            )
        )
    return out


# DeployCalibrated.s.sol writes fixtures under a CHAIN_LABEL name that
# differs from the manifest's canonical chain key. Map fixture-name →
# manifest-key here. Add new chains by appending an entry — the loader
# silently ignores fixtures that aren't in this map.
_FIXTURE_LABEL_TO_MANIFEST_CHAIN: Final[dict[str, str]] = {
    "rh_chain": "robinhood_chain_testnet",
    "sepolia": "arbitrum_sepolia",
    "polygon_amoy": "polygon_amoy",
}

# DeployCalibrated.s.sol emits camelCase contract keys; the manifest uses
# PascalCase. Keep this mirror of DEFAULT_CONTRACTS so the loader can
# translate without a second source of truth drifting out of sync.
_FIXTURE_CONTRACT_KEY_TO_MANIFEST: Final[dict[str, str]] = {
    "energyController": "EnergyController",
    "phaseManager":     "PhaseManager",
    "agentLifecycle":   "AgentLifecycle",
    "decisionLog":      "DecisionLog",
    "tombstoneNFT":     "TombstoneNFT",
}


def load_deploys_from_fixtures(deployments_dir: Path) -> list[ChainDeploy]:
    """Read DeployCalibrated.s.sol fixtures and assemble real ChainDeploy entries.

    For every chain in DEFAULT_CHAINS: if a matching fixture file exists
    AND its contract addresses are non-zero, populate that chain with
    real data and set ``placeholder=False``. Chains without a fixture
    (or with all-zero placeholders) fall back to the synthetic
    ``_placeholder_deploys()`` entry so the manifest shape stays stable.
    """
    # Build reverse map: manifest_chain_key → fixture_label.
    manifest_to_label = {v: k for k, v in _FIXTURE_LABEL_TO_MANIFEST_CHAIN.items()}

    out: list[ChainDeploy] = []
    for chain_key, chain_id, explorer_url in DEFAULT_CHAINS:
        fixture_label = manifest_to_label.get(chain_key)
        fixture_path = (
            deployments_dir / f"{fixture_label}.json" if fixture_label else None
        )
        if fixture_path is None or not fixture_path.is_file():
            # No fixture → keep placeholder entry for this chain.
            out.append(_placeholder_chain(chain_key, chain_id, explorer_url))
            continue

        try:
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out.append(_placeholder_chain(chain_key, chain_id, explorer_url))
            continue

        raw_contracts = fixture.get("contracts") or {}
        # Require EVERY contract in DEFAULT_CONTRACTS to have a non-zero
        # address in the fixture. This protects against three failure modes:
        #   (1) all-zero template (deploy script never ran),
        #   (2) partial deploy (e.g. gas spike mid-script left some addresses
        #       as Foundry-predicted-but-never-broadcast),
        #   (3) missing key in fixture (schema drift).
        # In all three the chain entry stays placeholder=True so SUBMISSION
        # never claims a chain is live when one or more contracts aren't on it.
        all_real = True
        for manifest_name, _abi in DEFAULT_CONTRACTS:
            fk = next(
                (k for k, v in _FIXTURE_CONTRACT_KEY_TO_MANIFEST.items()
                 if v == manifest_name),
                None,
            )
            addr = raw_contracts.get(fk) if fk else None
            if not (isinstance(addr, str) and addr.startswith("0x")
                    and int(addr, 16) != 0):
                all_real = False
                break
        if not all_real:
            out.append(_placeholder_chain(chain_key, chain_id, explorer_url))
            continue

        contracts: list[ContractDeploy] = []
        for manifest_name, _abi_name in DEFAULT_CONTRACTS:
            # Find the fixture key whose value maps to this manifest name.
            fixture_key = next(
                (k for k, v in _FIXTURE_CONTRACT_KEY_TO_MANIFEST.items()
                 if v == manifest_name),
                None,
            )
            addr = (raw_contracts.get(fixture_key) if fixture_key else None) \
                   or _PLACEHOLDER_ADDRESS
            contracts.append(ContractDeploy(name=manifest_name, address=addr))

        deploy_block = int(fixture.get("deployedAtBlock") or 0)
        out.append(ChainDeploy(
            chain=chain_key,
            chain_id=chain_id,
            explorer_url=explorer_url,
            contracts=contracts,
            deploy_block=deploy_block,
            placeholder=False,
        ))
    return out


def _placeholder_chain(chain_key: str, chain_id: int, explorer_url: str) -> ChainDeploy:
    """One placeholder ChainDeploy — shared between full-placeholder builds
    and the per-chain fallback inside load_deploys_from_fixtures."""
    contracts = [
        ContractDeploy(name=name, address=_PLACEHOLDER_ADDRESS)
        for name, _ in DEFAULT_CONTRACTS
    ]
    return ChainDeploy(
        chain=chain_key,
        chain_id=chain_id,
        explorer_url=explorer_url,
        contracts=contracts,
        deploy_block=0,
        placeholder=True,
    )


def _placeholder_phase3_launch() -> list[Phase3LaunchRecord]:
    """Build the placeholder phase3_launch section — one entry per default chain."""
    return [
        Phase3LaunchRecord(
            chain=chain_key,
            launch_tx=_PLACEHOLDER_TX_HASH,
            pause_role_renounced_tx=_PLACEHOLDER_TX_HASH,
            upgrade_role_renounced_tx=_PLACEHOLDER_TX_HASH,
            block_number=0,
            placeholder=True,
        )
        for chain_key, _, _ in DEFAULT_CHAINS
    ]


def _placeholder_ipfs_roots() -> list[IpfsRoot]:
    """Build the placeholder ipfs_roots section."""
    return [
        IpfsRoot(name="memory_bank_v1", cid=_PLACEHOLDER_CID, placeholder=True),
        IpfsRoot(name="tombstone_metadata", cid=_PLACEHOLDER_CID, placeholder=True),
        IpfsRoot(name="demo_assets", cid=_PLACEHOLDER_CID, placeholder=True),
    ]


def _placeholder_demo_video() -> DemoVideo:
    """Build the placeholder demo_video section."""
    return DemoVideo(
        url=_PLACEHOLDER_URL,
        sha256=_PLACEHOLDER_SHA256,
        duration_seconds=0,
        placeholder=True,
    )


def load_rehearsal_summary(report_path: Path) -> RehearsalSummary:
    """Parse a :class:`agent.staging.rehearsal_runner.RehearsalReport` JSON dump.

    Only the fields the manifest summarises are extracted; extra
    fields (like ``timeline_events``) are ignored on purpose.
    Raises :class:`RehearsalReportMissingError` if the file is
    missing or malformed.
    """
    if not report_path.is_file():
        raise RehearsalReportMissingError(
            f"Rehearsal report not found: {report_path}"
        )
    try:
        raw = report_path.read_text(encoding="utf-8")
        data: Any = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RehearsalReportMissingError(
            f"Could not read rehearsal report {report_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RehearsalReportMissingError(
            f"Rehearsal report {report_path} is not a JSON object"
        )

    # RehearsalReport's pass flag is aliased on the wire as "pass"
    # (Python keyword). Accept either spelling so this builder
    # tolerates both alias-dumped and field-dumped reports.
    passed: bool = bool(data.get("pass", data.get("passed", False)))

    def _get_int(key: str) -> int:
        val = data.get(key, 0)
        if not isinstance(val, int):
            raise RehearsalReportMissingError(
                f"Rehearsal report field {key!r} must be int, got {type(val).__name__}"
            )
        return val

    def _get_opt_str(key: str) -> str | None:
        val = data.get(key)
        if val is None:
            return None
        if not isinstance(val, str):
            raise RehearsalReportMissingError(
                f"Rehearsal report field {key!r} must be str|None, got {type(val).__name__}"
            )
        return val

    return RehearsalSummary(
        report_path=str(report_path),
        passed=passed,
        fail_reason=_get_opt_str("fail_reason"),
        desperate_mode_count=_get_int("desperate_mode_count"),
        lung_expansion_count=_get_int("lung_expansion_count"),
        settlement_count=_get_int("settlement_count"),
        ws_disconnect_count=_get_int("ws_disconnect_count"),
        pause_role_renounced_tx=_get_opt_str("pause_role_renounced_tx"),
        upgrade_role_renounced_tx=_get_opt_str("upgrade_role_renounced_tx"),
    )


def load_phase1_training_summary(
    *,
    backtest_report_path: Path,
    weights_path: Path,
) -> Phase1TrainingSummary:
    """Project ``reports/phase1/backtest_report.json`` + ``weights_v0.json``
    into a manifest-shaped :class:`Phase1TrainingSummary`.

    Tolerant to either ``backtest_report.json`` shape or the lighter
    JSON the runner emits in dev mode — required headline fields are
    `training_matches`, `test_matches`, `epochs`, plus the trained vs
    uniform log-losses. Missing fields default to 0; the manifest is
    still useful as a pointer even when a metric is absent.
    """
    if not backtest_report_path.is_file():
        raise BuildError(
            f"Phase 1 backtest report not found: {backtest_report_path}"
        )
    if not weights_path.is_file():
        raise BuildError(f"Phase 1 weights snapshot not found: {weights_path}")

    raw = json.loads(backtest_report_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BuildError(
            f"Phase 1 backtest report {backtest_report_path} is not a JSON object"
        )

    dataset = raw.get("dataset") or {}
    # The tennis_runner emits a nested ``log_loss`` block carrying both
    # the uniform-baseline and trained test log-losses + an explicit
    # improvement_pct. Fall back to flat keys if a future runner ships
    # a flatter schema.
    log_loss_block = raw.get("log_loss") or {}

    weights = json.loads(weights_path.read_text(encoding="utf-8"))
    if not isinstance(weights, dict):
        raise BuildError(f"weights_v0 not a JSON object: {weights_path}")

    uniform_ll = float(
        log_loss_block.get("uniform_test")
        or raw.get("uniform_log_loss")
        or 0.0
    )
    trained_ll = float(
        log_loss_block.get("trained_test")
        or raw.get("trained_log_loss")
        or 0.0
    )
    improvement = float(
        log_loss_block.get("improvement_pct")
        or ((uniform_ll - trained_ll) / uniform_ll * 100.0 if uniform_ll > 0 else 0.0)
    )

    return Phase1TrainingSummary(
        sport=str(raw.get("sport") or dataset.get("sport") or "tennis"),
        dataset_path=str(
            dataset.get("path") or raw.get("dataset_path")
            or "data/parquet/tennis_phase1.parquet"
        ),
        weights_path=weights_path.as_posix(),
        backtest_report_path=backtest_report_path.as_posix(),
        training_matches=int(
            dataset.get("n_training_matches")
            or dataset.get("training_matches")
            or raw.get("training_matches")
            or 0
        ),
        test_matches=int(
            dataset.get("n_test_matches")
            or dataset.get("test_matches")
            or raw.get("test_matches")
            or 0
        ),
        epochs=int(
            dataset.get("epochs_run") or dataset.get("epochs")
            or raw.get("epochs") or 0
        ),
        uniform_baseline_log_loss=uniform_ll,
        trained_log_loss=trained_ll,
        improvement_pct=improvement,
        weights_v0=weights,
    )


def load_phase2_dryrun_summary(report_path: Path) -> Phase2DryRunSummary:
    """Parse a sprint_7 dry-run summary JSON dump.

    Source: written by an external CLI step that captures the
    :class:`agent.runtime.sprint7_dryrun.DryRunResult` to disk so the
    submission builder can ingest it without re-running the dry-run.
    """
    if not report_path.is_file():
        raise BuildError(f"Phase 2 dryrun summary not found: {report_path}")
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BuildError(f"Phase 2 dryrun summary not a JSON object: {report_path}")
    try:
        return Phase2DryRunSummary.model_validate(raw)
    except ValidationError as exc:
        raise BuildError(f"Phase 2 dryrun summary validation failed: {exc}") from exc


def build_manifest(
    *,
    commit_hash: str,
    rehearsal_report_path: Path,
    abi_registry_dir: Path,
    expected_abi_hashes: dict[str, str] | None = None,
    now_iso: str | None = None,
    deploys: list[ChainDeploy] | None = None,
    ipfs_roots: list[IpfsRoot] | None = None,
    demo_video: DemoVideo | None = None,
    phase3_launch: list[Phase3LaunchRecord] | None = None,
    phase1_training_summary: Phase1TrainingSummary | None = None,
    phase2_dryrun_summary: Phase2DryRunSummary | None = None,
) -> SubmissionManifest:
    """Assemble the SUBMISSION manifest from its component parts.

    Sections not passed explicitly are filled with the Day-20
    placeholder values (``placeholder=True`` on every chain-bound
    record). Production runs pass real values per-section as deploy /
    pin / record evidence becomes available.
    """
    abi_hashes = compute_abi_hashes(abi_registry_dir)
    if expected_abi_hashes is not None:
        verify_abi_hashes(abi_hashes, expected_abi_hashes)

    rehearsal_summary = load_rehearsal_summary(rehearsal_report_path)

    manifest_kwargs: dict[str, Any] = {
        "project_title": PROJECT_TITLE,
        "schema_version": SCHEMA_VERSION,
        "commit_hash": commit_hash.lower(),
        "generated_at": now_iso or _utcnow_iso(),
        "deploys": deploys or _placeholder_deploys(),
        "abi_hashes": abi_hashes,
        "ipfs_roots": ipfs_roots or _placeholder_ipfs_roots(),
        "demo_video": demo_video or _placeholder_demo_video(),
        "phase3_launch": phase3_launch or _placeholder_phase3_launch(),
        "rehearsal_summary": rehearsal_summary,
        "phase1_training_summary": phase1_training_summary,
        "phase2_dryrun_summary": phase2_dryrun_summary,
    }
    try:
        return SubmissionManifest(**manifest_kwargs)
    except ValidationError as exc:
        raise ManifestValidationError(str(exc)) from exc


def manifest_to_json(manifest: SubmissionManifest) -> str:
    """Serialise *manifest* to a stable, human-readable JSON string.

    We round-trip through ``model_dump`` then ``json.dumps`` so the
    indentation + key ordering are bytes-stable across runs (Pydantic
    preserves declared field order). ``sort_keys=False`` because the
    Pydantic field order IS the intentional reading order.
    """
    payload = manifest.model_dump(mode="json")
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.submission.build_manifest",
        description="Build the Genesis Experiment SUBMISSION.json package.",
    )
    parser.add_argument(
        "--commit",
        required=True,
        help="Git commit hash of the deployed agent + contracts (7-40 hex).",
    )
    parser.add_argument(
        "--rehearsal-report",
        required=True,
        type=Path,
        help="Path to the §15 Gap 7 staging RehearsalReport JSON dump.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output path for SUBMISSION.json.",
    )
    parser.add_argument(
        "--abi-registry-dir",
        type=Path,
        default=Path(".dev/contracts"),
        help="Directory holding *_abi.v*.json registry files (default: .dev/contracts).",
    )
    parser.add_argument(
        "--expected-abi-hashes",
        type=Path,
        default=None,
        help=(
            "Optional JSON file of {contract_name: sha256_hex}. When provided, "
            "the builder verifies every computed hash matches and exits with "
            "code 3 on the first mismatch."
        ),
    )
    parser.add_argument(
        "--render-markdown",
        type=Path,
        default=None,
        help="Optional output path for SUBMISSION.md (renders alongside the JSON).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Override the generated_at ISO-8601 timestamp (test determinism).",
    )
    parser.add_argument(
        "--deployments-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory holding DeployCalibrated.s.sol fixtures "
            "(e.g. script/deployments/sprint_3/). When passed, the loader "
            "reads <chain_label>.json files and flips placeholder=False on "
            "any chain whose fixture carries non-zero contract addresses."
        ),
    )
    parser.add_argument(
        "--phase1-backtest-report",
        type=Path,
        default=None,
        help=(
            "Optional path to reports/phase1/backtest_report.json — when "
            "passed alongside --phase1-weights, the manifest carries the "
            "Sprint-7 tennis-pivot training headline."
        ),
    )
    parser.add_argument(
        "--phase1-weights",
        type=Path,
        default=None,
        help=(
            "Optional path to reports/phase1/weights_v0.json — required "
            "with --phase1-backtest-report."
        ),
    )
    parser.add_argument(
        "--phase2-dryrun-summary",
        type=Path,
        default=None,
        help=(
            "Optional path to a Phase-2 dryrun summary JSON dump (produced "
            "by `agent.runtime.sprint7_dryrun.run_dryrun()`). Adds the "
            "Sprint-7 closer block to the manifest."
        ),
    )
    return parser


def _load_expected_hashes(path: Path) -> dict[str, str]:
    """Load a {contract: sha256} JSON map. Raises on shape error."""
    try:
        raw = path.read_text(encoding="utf-8")
        data: Any = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise AbiHashMismatchError(
            f"Could not read expected-abi-hashes file {path}: {exc}"
        ) from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise AbiHashMismatchError(
            f"Expected-abi-hashes file {path} must be a JSON object of str→str"
        )
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code (does NOT call sys.exit)."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        expected: dict[str, str] | None = None
        if args.expected_abi_hashes is not None:
            expected = _load_expected_hashes(args.expected_abi_hashes)

        deploys: list[ChainDeploy] | None = None
        if args.deployments_dir is not None:
            deploys = load_deploys_from_fixtures(args.deployments_dir)

        phase1_summary: Phase1TrainingSummary | None = None
        if args.phase1_backtest_report is not None or args.phase1_weights is not None:
            if (
                args.phase1_backtest_report is None
                or args.phase1_weights is None
            ):
                raise BuildError(
                    "--phase1-backtest-report and --phase1-weights must be "
                    "passed together (or neither)."
                )
            phase1_summary = load_phase1_training_summary(
                backtest_report_path=args.phase1_backtest_report,
                weights_path=args.phase1_weights,
            )

        phase2_summary: Phase2DryRunSummary | None = None
        if args.phase2_dryrun_summary is not None:
            phase2_summary = load_phase2_dryrun_summary(args.phase2_dryrun_summary)

        manifest = build_manifest(
            commit_hash=args.commit,
            rehearsal_report_path=args.rehearsal_report,
            abi_registry_dir=args.abi_registry_dir,
            expected_abi_hashes=expected,
            now_iso=args.now,
            deploys=deploys,
            phase1_training_summary=phase1_summary,
            phase2_dryrun_summary=phase2_summary,
        )
    except BuildError as exc:
        # exc.args[0] is either a free-text str (most subclasses) or
        # the list[(name, expected, actual)] payload from
        # verify_abi_hashes. Both render cleanly via f-string.
        payload = exc.args[0] if exc.args else "<unknown>"
        sys.stderr.write(f"{exc.label}: {payload}\n")
        return exc.code

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(manifest_to_json(manifest), encoding="utf-8")

    if args.render_markdown is not None:
        # Lazy import keeps the JSON-only build path lightweight + cycle-free.
        from agent.submission.render_markdown import render

        md = render(manifest)
        args.render_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.render_markdown.write_text(md, encoding="utf-8")

    return 0


if __name__ == "__main__":  # pragma: no cover — CLI dispatch
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CHAINS",
    "DEFAULT_CONTRACTS",
    "PROJECT_TITLE",
    "SCHEMA_VERSION",
    "AbiHashEntry",
    "AbiHashMismatchError",
    "AbiRegistryMissingError",
    "BuildError",
    "ChainDeploy",
    "ContractDeploy",
    "DemoVideo",
    "IpfsRoot",
    "ManifestValidationError",
    "Phase1TrainingSummary",
    "Phase2DryRunSummary",
    "Phase3LaunchRecord",
    "RehearsalReportMissingError",
    "RehearsalSummary",
    "SubmissionManifest",
    "build_manifest",
    "compute_abi_hashes",
    "load_phase1_training_summary",
    "load_phase2_dryrun_summary",
    "load_rehearsal_summary",
    "main",
    "manifest_to_json",
    "verify_abi_hashes",
]
