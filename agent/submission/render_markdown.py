"""SUBMISSION.json → SUBMISSION.md renderer.

Spec
----

The judges read ``SUBMISSION.md`` first; ``SUBMISSION.json`` is the
machine-readable anchor. This module is a thin, dependency-free
renderer that turns the validated :class:`SubmissionManifest` into a
clean markdown document with these mandatory sections (per task brief
acceptance criteria):

1. Project title.
2. Generation timestamp.
3. Three-chain contract table — every chain × every deployed contract.
4. ABI hash table — one row per contract, with file + canonical
   sha256 hex.
5. Phase 3 launch tx + role-renunciation tx hashes — per chain.
6. Demo video URL + sha256.
7. Rehearsal report summary — pass/fail + diagnostic counts.

Design notes
------------

* The renderer is **pure**: ``render(manifest) -> str``. It does NOT
  touch the filesystem (the CLI writes the file). This keeps it
  trivial to test and to inline-call from :func:`build_manifest.main`.
* Placeholder rows are marked with a ``⚠ placeholder`` cell so a
  reviewer scanning the rendered document immediately spots
  unfilled-in sections without cross-referencing the JSON.
* Etherscan-style links are built from each chain's ``explorer_url``;
  the renderer does NOT hard-code chain explorers — adding a fourth
  chain to ``DEFAULT_CHAINS`` (e.g. Sepolia main, Optimism) flows
  through unchanged.
* No external dependencies — pure string concatenation. Pydantic is
  already a project dep but we only USE the model for typed reads
  here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

from agent.submission.build_manifest import (
    ChainDeploy,
    Phase3LaunchRecord,
    SubmissionManifest,
)

# Sentinel string we render inside placeholder cells. Anchored as a
# constant so test assertions can grep for it.
_PLACEHOLDER_MARK: Final[str] = "⚠ placeholder"


def _placeholder_badge(is_placeholder: bool) -> str:
    """Return the placeholder badge or an empty string."""
    return _PLACEHOLDER_MARK if is_placeholder else ""


def _explorer_link(explorer_url: str, segment: str, value: str) -> str:
    """Render *value* as a markdown link to its explorer page.

    *segment* is the explorer URL path (``"tx"`` or ``"address"``).
    Falls back to a bare-backtick code span when *explorer_url* is
    empty so the renderer never crashes on a manifest that drops the
    explorer base URL.
    """
    if not explorer_url:
        return f"`{value}`"
    base = explorer_url if explorer_url.endswith("/") else explorer_url + "/"
    return f"[`{value}`]({base}{segment}/{value})"


def _render_header(manifest: SubmissionManifest) -> str:
    """Top-of-document title + generation metadata block."""
    lines = [
        f"# {manifest.project_title} — Submission Manifest",
        "",
        f"- **Schema version:** `{manifest.schema_version}`",
        f"- **Commit:** `{manifest.commit_hash}`",
        f"- **Generated at:** `{manifest.generated_at}` (UTC)",
        "",
    ]
    return "\n".join(lines)


def _render_contract_table(deploys: list[ChainDeploy]) -> str:
    """Render the cross-chain contract address table.

    Layout: contracts as rows, chains as columns, so the reader
    visually compares "is the same contract deployed at the same
    role on every chain?" in one glance.
    """
    if not deploys:
        return "## Deployed Contracts\n\n_No deploys recorded._\n"

    # Stable contract roster from the first chain (every chain has the
    # same five contracts in the same order per DEFAULT_CONTRACTS).
    contract_names = [c.name for c in deploys[0].contracts]

    header_cells = ["Contract"] + [d.chain for d in deploys]
    separator = ["---"] * len(header_cells)
    rows: list[list[str]] = []
    for contract_name in contract_names:
        row = [f"`{contract_name}`"]
        for chain in deploys:
            entry = next(
                (c for c in chain.contracts if c.name == contract_name),
                None,
            )
            if entry is None:
                row.append("_missing_")
            else:
                cell = _explorer_link(chain.explorer_url, "address", entry.address)
                if chain.placeholder:
                    cell = f"{cell} {_PLACEHOLDER_MARK}"
                row.append(cell)
        rows.append(row)

    lines = [
        "## Deployed Contracts (3-Chain Parallel)",
        "",
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Per-chain block metadata footer.
    lines.append("### Chain metadata")
    lines.append("")
    lines.append("| Chain | Chain ID | Deploy block | Status |")
    lines.append("| --- | --- | --- | --- |")
    for d in deploys:
        lines.append(
            f"| `{d.chain}` | `{d.chain_id}` | `{d.deploy_block}` | "
            f"{_placeholder_badge(d.placeholder) or 'live'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_abi_hash_table(manifest: SubmissionManifest) -> str:
    """Render the ABI hash anchor table."""
    lines = [
        "## ABI Hashes (Canonical sha256)",
        "",
        "Canonicalisation: `json.dumps(abi, sort_keys=True, separators=(',', ':'),"
        " ensure_ascii=False)` then `sha256`. Reproducible across runs; whitespace-"
        "insensitive (see `agent/submission/abi_hasher.py`).",
        "",
        "| Contract | ABI version | ABI file | sha256 |",
        "| --- | --- | --- | --- |",
    ]
    for entry in manifest.abi_hashes:
        lines.append(
            f"| `{entry.contract}` | `{entry.abi_version}` | "
            f"`{entry.abi_file}` | `{entry.sha256}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_phase3_launch(
    phase3_launch: list[Phase3LaunchRecord],
    deploys: list[ChainDeploy],
) -> str:
    """Render the per-chain Phase-3 launch + role-renunciation tx table.

    PRD §5.1 + TP §15 Gap 7: the launch tx atomically renounces both
    pause + upgrade roles. The table surfaces all three tx hashes so a
    judge can verify on the explorer that each tx (a) landed and (b)
    emitted the right ``Phase3RolesRenounced`` event.
    """
    explorer_by_chain = {d.chain: d.explorer_url for d in deploys}

    lines = [
        "## Phase 3 Launch + Role Renunciation",
        "",
        "Per PRD §5.1 + TECHNICAL_PLAN §15 Gap 7, the Phase-3 launch tx"
        " emits BOTH `EnergyController.Phase3RolesRenounced` (= "
        "`PauseRoleRenounced`) and `PhaseManager.Phase3RolesRenounced`"
        " (= `UpgradeRoleRenounced`) — the agent EOA permanently loses"
        " pause + upgrade authority in the same atomic action.",
        "",
        "| Chain | Launch tx | Pause role renounced tx |"
        " Upgrade role renounced tx | Block | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in phase3_launch:
        explorer = explorer_by_chain.get(record.chain, "")
        launch = _explorer_link(explorer, "tx", record.launch_tx)
        pause = _explorer_link(explorer, "tx", record.pause_role_renounced_tx)
        upgrade = _explorer_link(explorer, "tx", record.upgrade_role_renounced_tx)
        status = _placeholder_badge(record.placeholder) or "live"
        lines.append(
            f"| `{record.chain}` | {launch} | {pause} | {upgrade} |"
            f" `{record.block_number}` | {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_demo_video(manifest: SubmissionManifest) -> str:
    """Render the demo video URL + integrity hash."""
    dv = manifest.demo_video
    lines = [
        "## Demo Video",
        "",
        f"- **URL:** [{dv.url}]({dv.url})",
        f"- **sha256:** `{dv.sha256}`",
        f"- **Duration:** `{dv.duration_seconds}` seconds",
    ]
    if dv.placeholder:
        lines.append(f"- **Status:** {_PLACEHOLDER_MARK}")
    lines.append("")
    return "\n".join(lines)


def _render_ipfs_roots(manifest: SubmissionManifest) -> str:
    """Render the IPFS root anchors."""
    if not manifest.ipfs_roots:
        return ""
    lines = [
        "## IPFS Anchors",
        "",
        "| Name | CID | Status |",
        "| --- | --- | --- |",
    ]
    for root in manifest.ipfs_roots:
        status = _placeholder_badge(root.placeholder) or "pinned"
        lines.append(f"| `{root.name}` | `{root.cid}` | {status} |")
    lines.append("")
    return "\n".join(lines)


def _render_phase1_training_summary(manifest: SubmissionManifest) -> str:
    """Render the Sprint-7 tennis-pivot Phase-1 backtest block.

    Empty string if the manifest does not carry a phase1 summary —
    keeps v1.0.0 manifests visually identical to before the bump.
    """
    s = manifest.phase1_training_summary
    if s is None:
        return ""
    lines = [
        "## Phase 1 Training Headline (Sprint 7 — Tennis Pivot)",
        "",
        f"- **Sport:** `{s.sport}`",
        f"- **Dataset:** `{s.dataset_path}`",
        f"- **Training matches / Test matches / Epochs:** "
        f"`{s.training_matches}` / `{s.test_matches}` / `{s.epochs}`",
        "",
        "| Metric | Uniform baseline | Trained | Improvement |",
        "| --- | --- | --- | --- |",
        f"| Log-loss | `{s.uniform_baseline_log_loss:.4f}` | "
        f"`{s.trained_log_loss:.4f}` | "
        f"`{s.improvement_pct:.2f}%` |",
        "",
        "### Final weights (`weights_v0.json`)",
        "",
        "```json",
        json.dumps(s.weights_v0, indent=2),
        "```",
        "",
        f"- Backtest report: `{s.backtest_report_path}`",
        f"- Weights snapshot: `{s.weights_path}`",
        "",
    ]
    return "\n".join(lines)


def _render_phase2_dryrun_summary(manifest: SubmissionManifest) -> str:
    """Render the Sprint-7 closer Phase-2 dry-run verdict block."""
    s = manifest.phase2_dryrun_summary
    if s is None:
        return ""
    broadcast_status = "✅ no signed orders" if s.broadcast_count == 0 else (
        "❌ broadcast detected"
    )
    real_market_status = "✅ real tennis market referenced" if s.real_market_referenced else (
        "⚠ no real-market reference"
    )
    lines = [
        "## Phase 2 Dry-Run Verdict (Sprint 7 — Day 6 closer)",
        "",
        f"- **Log:** `{s.log_path}`",
        f"- **Summary:** `{s.summary_path}`",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Decisions emitted | `{s.decisions_count}` (BET=`{s.bets_count}` / NO_BET=`{s.no_bets_count}`) |",
        f"| Idle heartbeats | `{s.heartbeat_count}` |",
        f"| Tennis markets discovered (gamma-api) | `{s.markets_used}` |",
        f"| Broadcasts ({broadcast_status}) | `{s.broadcast_count}` |",
        f"| Real-market reference ({real_market_status}) | `{s.real_market_referenced}` |",
        "",
    ]
    return "\n".join(lines)


def _render_rehearsal_summary(manifest: SubmissionManifest) -> str:
    """Render the §15 Gap 7 staging-rehearsal verdict."""
    s = manifest.rehearsal_summary
    verdict = "✅ PASSED" if s.passed else "❌ FAILED"
    fail_line = (
        f"- **Failure reason:** `{s.fail_reason}`" if s.fail_reason else ""
    )
    pause_line = (
        f"- **Pause role renounced tx:** `{s.pause_role_renounced_tx}`"
        if s.pause_role_renounced_tx
        else "- **Pause role renounced tx:** _not observed_"
    )
    upgrade_line = (
        f"- **Upgrade role renounced tx:** `{s.upgrade_role_renounced_tx}`"
        if s.upgrade_role_renounced_tx
        else "- **Upgrade role renounced tx:** _not observed_"
    )
    lines = [
        "## Pre-Demo Staging Rehearsal (TP §15 Gap 7)",
        "",
        f"- **Verdict:** {verdict}",
        f"- **Report path:** `{s.report_path}`",
    ]
    if fail_line:
        lines.append(fail_line)
    lines.extend(
        [
            "",
            "### Diagnostic counts",
            "",
            f"- Desperate Mode entries: `{s.desperate_mode_count}` (pass: ≥ 1)",
            f"- Lung Expansion events: `{s.lung_expansion_count}` (pass: ≥ 1)",
            f"- Market loss settlements: `{s.settlement_count}` (informational)",
            f"- WS disconnects: `{s.ws_disconnect_count}` (pass: == 0)",
            pause_line,
            upgrade_line,
            "",
        ]
    )
    return "\n".join(lines)


def render(manifest: SubmissionManifest) -> str:
    """Render *manifest* to the full SUBMISSION.md document body.

    Section order is deliberate: title → contracts (the headline
    artefact) → ABI hashes (the cryptographic anchor) → Phase-3
    renunciation (the trust-loss proof) → IPFS pins → demo video →
    rehearsal verdict. A judge reading top-to-bottom sees the layered
    evidence narrative.
    """
    sections = [
        _render_header(manifest),
        _render_contract_table(manifest.deploys),
        _render_abi_hash_table(manifest),
        _render_phase1_training_summary(manifest),
        _render_phase2_dryrun_summary(manifest),
        _render_phase3_launch(manifest.phase3_launch, manifest.deploys),
        _render_ipfs_roots(manifest),
        _render_demo_video(manifest),
        _render_rehearsal_summary(manifest),
    ]
    # Filter empty sections (defensive — _render_ipfs_roots returns ""
    # if the list is empty) and join with a blank line between blocks.
    return "\n".join(section for section in sections if section).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Render an existing SUBMISSION.json to a SUBMISSION.md file.

    Returns the process exit code (does NOT call sys.exit).
    """
    parser = argparse.ArgumentParser(
        prog="agent.submission.render_markdown",
        description="Render SUBMISSION.json → SUBMISSION.md.",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to SUBMISSION.json.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output path for SUBMISSION.md.",
    )
    args = parser.parse_args(argv)

    if not args.manifest.is_file():
        sys.stderr.write(f"Manifest not found: {args.manifest}\n")
        return 2
    raw = args.manifest.read_text(encoding="utf-8")
    payload = json.loads(raw)
    manifest = SubmissionManifest.model_validate(payload)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(manifest), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI dispatch
    raise SystemExit(main())


__all__ = ["main", "render"]
