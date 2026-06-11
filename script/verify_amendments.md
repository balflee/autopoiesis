# script/verify_amendments.md

Plain-text spec-anchor checklist for the **memoryBankCid amendment** to
`docs/PRD.md` §5.1 and `docs/TECHNICAL_PLAN.md` §3.5 (proposal-orbit-agent-memory-bank.md
v3.2). This file is a seed for a future automated check (CI grep job or a
follow-up Doc Editor verification task). It complements `README_CHAIN.md` →
"Phase 5+ Amendment Verification".

## Why this file exists

User reject notes on `D-2026-05-17-PLAN-002` (recorded as
`D-2026-05-17-REJECT-002`) require this gate: any sprint that depends on the
memoryBankCid amendment MUST be preceded by a verifier task that confirms the
amendment landed. The amendment was applied via Advisor bootstrap-bypass on
2026-05-17 per decision **`D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK`** (user
approval `A`, `status=approved` at `2026-05-17T22:52:09Z`).

T-A-000 (this task) verifies the three anchors below. T-A-001 declares
`dependencies: [T-A-000]` and will not start until this task COMPLETES.

## Path note (operators)

2026-05-25 migration: `docs/PRD.md`, `docs/TECHNICAL_PLAN.md`, `docs/DEV_FRAMEWORK.md`
now live INSIDE this repo at `code/docs/`. Before the migration they lived
one directory above the repo root outside any git repo; the inbox
artifacts from sprints 1-6 still carry that legacy `..`-prefixed path —
those are immutable history and not load-bearing.

The audit trail for the original orbit-agent-memory-bank amendment is in
`.dev/state/decisions.json` (per commit `c8d5a11`).

Run the commands shown below from inside `code/` (the repo root).

## Expected anchors

### Anchor 1 — `docs/PRD.md` §5.1 contains `memoryBankCid` (line 253)

- **Check**: `grep -n memoryBankCid docs/PRD.md`
- **Expected**: >=1 hit, located inside §5.1 "三件套：Permadeath + Last Words
  + Tombstone NFT", sub-bullet C ("Tombstone NFT 数字遗产").
- **Verbatim match (line 253, observed at T-A-000 round 1)**:
  ```
  253:  - **`memoryBankCid`：完整 memory_bank tarball 的 IPFS CIDv1**——Agent 全部 tick 记录（signals / fusion / decision / outcome / weights / reflections / narratives）打包后的可浏览数字心智。**NFT 持有者可逐 tick 浏览这个 Agent 一生的决策。**这把 NFT 从「JPG + 几个 hash」升级成「一个真实存在过的 AI 心智的数字遗骸」。
  ```

### Anchor 2 — `docs/TECHNICAL_PLAN.md` §3.5 contains `memoryBankCid` (line 515)

- **Check**: `grep -n memoryBankCid docs/TECHNICAL_PLAN.md`
- **Expected**: >=1 hit, located inside §3.5 `TombstoneNFT (ERC-721)`, inside
  the `struct Tombstone { ... }` declaration. (Additional hits at lines 533,
  538, 544, 546, 658, 803, 959 are expected and not part of the gate.)
- **Verbatim match (line 515, observed at T-A-000 round 1)**:
  ```
  515:        string  memoryBankCid;      // IPFS CIDv1 of complete memory_bank tarball;
  516:                                    //   empty string if IPFS pin failed at mint
  517:                                    //   (degraded mode — TombstoneMintedWithoutMemoryBank
  518:                                    //   event emitted; mint still succeeds).
  ```

### Anchor 3 — `docs/TECHNICAL_PLAN.md` §3.5 contains `TombstoneMintedWithoutMemoryBank` (line 522)

- **Check**: `grep -n TombstoneMintedWithoutMemoryBank docs/TECHNICAL_PLAN.md`
- **Expected**: >=1 hit, declared as a Solidity `event` inside §3.5
  `TombstoneNFT (ERC-721)` immediately after the `struct Tombstone`.
- **Verbatim match (line 522, observed at T-A-000 round 1)**:
  ```
  522:    event TombstoneMintedWithoutMemoryBank(
  523:        uint256 indexed tokenId,
  524:        string reason                          // "ipfs_pin_failed_after_3_retries"
  525:    );
  ```

## Pass criteria

All three `grep -n` commands MUST return at least one hit at (or near) the
quoted line numbers AND in the cited section. If any check fails:

1. STOP — do not dispatch dependent contract work (e.g. `T-A-001`).
2. Emit an escalation referencing decision id
   `D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK` and this file.
3. Spawn a Doc Editor verification task (or re-run Doc Editor flow against
   `proposal-orbit-agent-memory-bank.md` v3.2) before re-planning.

## Decision-id provenance

- `D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK` — `kind=doc_edit_approval`,
  `status=approved`, `user_approval='A'`, recorded `2026-05-17T22:52:09Z`.
- Source proposal: `.dev/inbox/proposal-orbit-agent-memory-bank.md` v3.2.
- Audit-trail commit: `c8d5a11` (`docs: bootstrap-bypass Doc Editor
  amendment for orbit-agent memory_bank v3.2`).
- Upstream User direction (`D-2026-05-17-REJECT-002`, the reject notes that
  mandated this gating pattern): User reject notes on
  `D-2026-05-17-PLAN-002`.

---

*Seed for future automated verification. Maintained by Track A.
First populated by T-A-000.*
