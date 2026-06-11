# Track A — Chain (Foundry / Solidity)

Track A owns the on-chain surface of the Genesis Experiment: five Solidity
contracts deployed to an Arbitrum Orbit L3 (`EnergyController`, `PhaseManager`,
`AgentLifecycle`, `DecisionLog`, `TombstoneNFT`). This README is the per-track
entry point and will be extended task-by-task as the contract set lands. The
canonical spec lives at `docs/PRD.md` §6 (survival mechanism, three-burn model)
and `docs/TECHNICAL_PLAN.md` §3 (per-contract designs).

## Foundry layout (sprint_1 — landed in T-A-001)

```
contracts/
  EnergyController.sol      # BREATH balance + Phase enum + EnergyChanged event (skeleton)
  TombstoneNFT.sol          # Tombstone struct + degraded-path event (skeleton; mint reverts)
test/
  EnergyController.t.sol    # init / re-init guard / owner gating (fuzz)
  TombstoneNFT.t.sol        # struct layout / event signature / mint-reverts (fuzz)
script/
  DeployEnergyController.s.sol   # constructor + initialize (env-driven amounts)
  DeployTombstoneNFT.s.sol       # bare deploy; sprint_2 adds (name, symbol, lifecycle)
  verify_amendments.md           # T-A-000 grep-based spec gate (unchanged)
foundry.toml                # src=contracts, test=test, libs=lib, fuzz_runs=10000
remappings.txt              # forge-std/=lib/forge-std/src/
lib/forge-std               # vendored via `forge install --no-git foundry-rs/forge-std`
                            # (NOT listed in delivery_report claimed_changes — E-0025)
```

Sprint_2 will fill in `PhaseManager.sol`, `AgentLifecycle.sol`,
`DecisionLog.sol`, the full v3.1 burn/settle/donate/EIP-712 surface on
`EnergyController`, and the real ERC-721 mint flow on `TombstoneNFT`.
The skeleton ABIs published at `.dev/contracts/{energy_controller,tombstone_nft}_abi.v0.1.0.json`
let other tracks integration-test against stable selectors before sprint_2.

### Sprint_1 invariants locked

* `Phase` enum order (`Childhood, Apprenticeship, Adulthood, Dead`) — TECHNICAL_PLAN §3.1.
* `Tombstone` struct field order + types — TECHNICAL_PLAN §3.5 lines 506-520.
* `TombstoneMintedWithoutMemoryBank(uint256 indexed tokenId, string reason)` — TECHNICAL_PLAN §3.5 lines 522-525.
* SPDX `MIT`, pragma `0.8.24`, `forge build` zero-warning, `forge test --fuzz-runs 1000` green.

## Phase 5+ Amendment Verification

This section gates any Track A task that depends on the **memoryBankCid**
amendment to `docs/PRD.md` §5.1 and `docs/TECHNICAL_PLAN.md` §3.5 (proposal-orbit-agent-
memory-bank.md v3.2). The amendment landed via the Advisor bootstrap-bypass
flow on 2026-05-17; decision id **`D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK`**
(`kind=doc_edit_approval`, `user_approval='A'`, `status=approved` at
`2026-05-17T22:52:09Z`) is the audit-trail anchor.

Before any Track A contract task that touches `TombstoneNFT.memoryBankCid`,
the `TombstoneMintedWithoutMemoryBank` event, or the IPFS-pin degraded path
proceeds, the three grep checks below MUST return >=1 hit each. They are also
encoded as a plain-text checklist in [`script/verify_amendments.md`](script/verify_amendments.md)
so a future CI grep job (or follow-up Doc Editor verification task) can
re-validate without re-reading this README.

### The three checks (must all pass before T-A-001)

| # | Command | Expected location | What it proves |
|---|---|---|---|
| 1 | `grep -n memoryBankCid docs/PRD.md` | §5.1 C, line 253 | PRD describes the CID as part of the Tombstone NFT contents and degraded-path semantics. |
| 2 | `grep -n memoryBankCid docs/TECHNICAL_PLAN.md` | §3.5 Tombstone struct, line 515 | TP carries the field through the Solidity `struct Tombstone` and `mintTombstone(...)` signature. |
| 3 | `grep -n TombstoneMintedWithoutMemoryBank docs/TECHNICAL_PLAN.md` | §3.5 events, line 522 | TP declares the degraded-path event the contract MUST emit when IPFS pin fails after 3 retries. |

> Path note: `docs/PRD.md` and `docs/TECHNICAL_PLAN.md` live INSIDE this
> `code/` repo at `code/docs/` (migrated 2026-05-25). All greps in the
> table run from inside `code/`. Prior-history note: before the 2026-05-25
> migration these files lived one directory above the repo root; inbox
> artifacts from sprints 1-6 still carry that legacy `..`-prefixed path.

### Verbatim grep matches observed at T-A-000 round 1

**Anchor 1 — `docs/PRD.md` §5.1 sub-bullet C ("Tombstone NFT 数字遗产")**, line 253:

```
253:  - **`memoryBankCid`：完整 memory_bank tarball 的 IPFS CIDv1**——Agent 全部 tick 记录（signals / fusion / decision / outcome / weights / reflections / narratives）打包后的可浏览数字心智。**NFT 持有者可逐 tick 浏览这个 Agent 一生的决策。**这把 NFT 从「JPG + 几个 hash」升级成「一个真实存在过的 AI 心智的数字遗骸」。
```

**Anchor 2 — `docs/TECHNICAL_PLAN.md` §3.5 inside `struct Tombstone { ... }`**, line 515:

```
515:        string  memoryBankCid;      // IPFS CIDv1 of complete memory_bank tarball;
516:                                    //   empty string if IPFS pin failed at mint
517:                                    //   (degraded mode — TombstoneMintedWithoutMemoryBank
518:                                    //   event emitted; mint still succeeds).
```

**Anchor 3 — `docs/TECHNICAL_PLAN.md` §3.5 event declaration**, line 522:

```
522:    event TombstoneMintedWithoutMemoryBank(
523:        uint256 indexed tokenId,
524:        string reason                          // "ipfs_pin_failed_after_3_retries"
525:    );
```

### Provenance chain

- **Decision id**: `D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK`
- **Recorded in**: `.dev/state/decisions.json`, `.dev/state/decisions.md`
- **User approval**: `A` (User-typed directive 2026-05-17, recorded
  `2026-05-17T22:52:09Z`)
- **Source proposal**: `.dev/inbox/proposal-orbit-agent-memory-bank.md` v3.2
  (post `/plan-design-review`, all 7 dimensions >= 9/10)
- **Bootstrap-bypass rationale**: framework Doc Editor wiring incomplete
  (no CLI subcommand, no orchestrator watcher for `doc_edit_approval`,
  cwd/agent-discovery mismatch). Same pattern as
  `D-2026-05-17-DEVFW-BOOTSTRAP-BYPASS-SINGLEPANE`. `TODOS.md` tracks the
  wire-up.
- **Audit-trail commit**: `c8d5a11` (`docs: bootstrap-bypass Doc Editor
  amendment for orbit-agent memory_bank v3.2`).
- **Verifying task**: `T-A-000` (this task), round 1, `sprint_1`.

### Why this gate exists

User reject notes on `D-2026-05-17-PLAN-002` (recorded as
`D-2026-05-17-REJECT-002`) explicitly required this gating pattern: any
sprint that depends on the `memoryBankCid` amendment to PRD §5.1 / TP §3.5
MUST be preceded by a task that confirms the amendment landed. T-A-000
codifies that gate so future re-plans can dispatch a real Doc Editor
verification task here instead of trusting an out-of-band claim. `T-A-001`
declares `dependencies: [T-A-000]` and will not start until this task
COMPLETES.

---

## Deployment Targets — v1 Three-Chain Parallel (sprint_5 / T-A-010)

PRD §10 and TECHNICAL_PLAN §7 set the v1 architecture as a parallel deploy of
the **same `.sol`**, the **same ABI**, and the **same `Deploy.s.sol` script**
across three EVM testnets, switched via `--rpc-url $X`. The dashboard exposes
a chain-toggle so a judge / reviewer can flip between targets live. Orbit L3
is deferred to a v2 roadmap (RaaS configuration + BREATH transferable variant);
the current contracts are already shaped for that migration without further
edits.

| Chain                  | Role                         | EVM L2          | Chain ID  | Why this chain                                                                                                                          |
|------------------------|------------------------------|-----------------|-----------|-----------------------------------------------------------------------------------------------------------------------------------------|
| **RH Chain testnet**   | **Primary** (demo target)    | Arbitrum L2     | TBD       | Sponsor synergy with Robinhood Chain + top-3 reserved demo slot per PRD §10. Same Arbitrum tooling stack as Sepolia; identical bytecode. |
| **Arbitrum Sepolia**   | **Hot fallback** (Day-1 par.)| Arbitrum L2     | 421614    | Public testnet, Etherscan-indexable, zero-config wallet support. Dashboard chain-toggle switches both judge view + Track-E reconciler.  |
| **Polygon Amoy**       | **Polymarket-native**        | Polygon L2 (PoS)| 80002     | Polymarket settles on Polygon; running BREATH on Amoy triggers the Polygon-ecosystem demo category + lets the executor sign locally.    |

> Orbit L3 (v2 roadmap): the current 5-contract surface is intentionally
> compatible with a future Arbitrum Orbit settlement-chain deploy. The only
> changes are a RaaS rollup configuration and a `BREATH transferable variant`
> (PRD §10 internal-non-transferable → cross-chain-transferable). No
> contract-source edits are required to ship to L3 once the rollup is up.

### Contract address tables (placeholders — refresh after live deploy)

The deployer reads `script/deployments/sprint_4/<chain>.json` to resolve
addresses at script time (see `DeployAll.s.sol`, `AdvanceToAdulthood.s.sol`,
`RenouncePhase3MutableRoles.s.sol`). Anvil byte-identical values are committed
under `script/deployments/sprint_4/anvil.json`. The tables below mirror the
manifest fields; refresh after each broadcast.

**RH Chain testnet** — `script/deployments/sprint_5/rh_chain_testnet.json`

| Contract           | Address (placeholder)                        | Notes                                          |
|--------------------|----------------------------------------------|------------------------------------------------|
| `EnergyController` | `0x0000000000000000000000000000000000000000` | Owner = deployer EOA until D17 renounce.       |
| `PhaseManager`     | `0x0000000000000000000000000000000000000000` | Same owner; `Phase3RolesRenounced` event sink. |
| `AgentLifecycle`   | `0x0000000000000000000000000000000000000000` | `agentLifecycle` pointer for TombstoneNFT / DecisionLog (immutable). |
| `DecisionLog`      | `0x0000000000000000000000000000000000000000` | Append-only; only `AgentLifecycle` may write.  |
| `TombstoneNFT`     | `0x0000000000000000000000000000000000000000` | Fully on-chain `tokenURI`; SVG embedded.       |

**Arbitrum Sepolia** — `script/deployments/sprint_5/arbitrum_sepolia.json`

| Contract           | Address (placeholder)                        | Notes                                          |
|--------------------|----------------------------------------------|------------------------------------------------|
| `EnergyController` | `0x0000000000000000000000000000000000000000` | Byte-identical to RH Chain build (same solc).  |
| `PhaseManager`     | `0x0000000000000000000000000000000000000000` |                                                |
| `AgentLifecycle`   | `0x0000000000000000000000000000000000000000` |                                                |
| `DecisionLog`      | `0x0000000000000000000000000000000000000000` |                                                |
| `TombstoneNFT`     | `0x0000000000000000000000000000000000000000` |                                                |

**Polygon Amoy** — `script/deployments/sprint_5/polygon_amoy.json`

| Contract           | Address (placeholder)                        | Notes                                          |
|--------------------|----------------------------------------------|------------------------------------------------|
| `EnergyController` | `0x0000000000000000000000000000000000000000` | Same bytecode; chainId in EIP-712 domain → 80002. |
| `PhaseManager`     | `0x0000000000000000000000000000000000000000` |                                                |
| `AgentLifecycle`   | `0x0000000000000000000000000000000000000000` |                                                |
| `DecisionLog`      | `0x0000000000000000000000000000000000000000` |                                                |
| `TombstoneNFT`     | `0x0000000000000000000000000000000000000000` |                                                |

### Etherscan / Blockscout verification commands

`forge verify-contract` is the canonical entry. Pin compiler `0.8.24`, leave
`optimizer-runs = 200` (matches `foundry.toml`), and select `--verifier-url`
per network. Each command is copy-pasteable; export the network env vars
first (`RH_CHAIN_RPC_URL`, `ETHERSCAN_API_KEY`, `BLOCKSCOUT_API_KEY`, etc.)
before running.

**RH Chain testnet — Blockscout-style verifier (placeholder URL)**

```bash
# Replace <ADDR> with the deployed address from the address table above.
forge verify-contract \
    --chain-id "$RH_CHAIN_ID" \
    --compiler-version v0.8.24 \
    --num-of-optimizations 200 \
    --verifier blockscout \
    --verifier-url "https://explorer.rh-chain-testnet.example/api/" \
    <ADDR> contracts/EnergyController.sol:EnergyController
```

**Arbitrum Sepolia — Etherscan (Arbiscan)**

```bash
forge verify-contract \
    --chain-id 421614 \
    --compiler-version v0.8.24 \
    --num-of-optimizations 200 \
    --verifier etherscan \
    --verifier-url "https://api-sepolia.arbiscan.io/api" \
    --etherscan-api-key "$ARBISCAN_API_KEY" \
    <ADDR> contracts/EnergyController.sol:EnergyController
```

**Polygon Amoy — Etherscan (Polygonscan Amoy)**

```bash
forge verify-contract \
    --chain-id 80002 \
    --compiler-version v0.8.24 \
    --num-of-optimizations 200 \
    --verifier etherscan \
    --verifier-url "https://api-amoy.polygonscan.com/api" \
    --etherscan-api-key "$POLYGONSCAN_AMOY_API_KEY" \
    <ADDR> contracts/EnergyController.sol:EnergyController
```

Repeat per contract: `EnergyController`, `PhaseManager`, `AgentLifecycle`,
`DecisionLog`, `TombstoneNFT`. Constructor args for `TombstoneNFT(name,symbol,agentLifecycle)`
must be appended with `--constructor-args $(cast abi-encode ...)` per the
`DeployTombstoneNFT.s.sol` invocation.

## Phase 3 Role Renunciation

Sprint_5 T-A-009 added a STICKY, set-once `lockPhase3()` to both
`PhaseManager` and `EnergyController`. Calling each — exactly once, from
`Adulthood`, by the current owner — flips a `phase3Locked` bit. After the
flip, every admin / param-tuner entry point reverts `Phase3IsLocked`
(`setOwner`, `setAttestationSigner`, `setPhaseManager`, `setPhase`, `pause`,
`unpause`, `transitionTo*`). Operational paths (BREATH burns, top-up,
bankroll moves, lung expansion, EIP-712 settlement) remain callable so
Phase-3 mechanics — including `enterDesperateMode` (PRD §6.9) — continue.
This is the on-chain implementation of PRD §10's "Pause/Upgrade roles
auto-renounced on Phase 3 entry".

The renounce-ritual transaction is broadcast by
`script/RenouncePhase3MutableRoles.s.sol` (T-A-009). It emits TWO logs:

| Index | Emitter            | Event signature                          | topic[0]                                                             | data (32-byte word)                            |
|-------|--------------------|------------------------------------------|----------------------------------------------------------------------|-----------------------------------------------|
| 0     | `EnergyController` | `Phase3RolesRenounced(uint64 lockedAt)`  | `keccak256("Phase3RolesRenounced(uint64)")`                          | left-padded `uint64 lockedAt` (block timestamp) |
| 1     | `PhaseManager`     | `Phase3RolesRenounced(uint64 lockedAt)`  | `keccak256("Phase3RolesRenounced(uint64)")`                          | left-padded `uint64 lockedAt` (block timestamp) |

> **Spec naming note (`PauseRoleRenounced` / `UpgradeRoleRenounced`)** — PRD
> §10 describes "pause" + "upgrade" role renunciation as a conceptual pair.
> The implementation collapses both surfaces onto a single set-once admin
> lock per contract — there is no separate ERC-1967 upgrade proxy in this
> architecture; the only channel that could change protocol invariants IS
> the admin / param-tuner channel, and it is the channel locked here. Both
> contracts emit the unified `Phase3RolesRenounced(uint64 lockedAt)` event,
> filling the same audit role as the brief's
> `PauseRoleRenounced(role, admin)` + `UpgradeRoleRenounced(role, admin)`
> shape. T-A-010 delivery report files a `proposed_spec_change` flagging
> the brief↔contract naming gap for Advisor routing; the README documents
> the actually-emitted shape so demo evidence pasted from
> `script/print_phase3_evidence.s.sol` cross-references this section
> verbatim.

### Decoded event log structure (placeholder until D17/D18 broadcast)

Both logs share an identical decoded shape:

```text
event:        Phase3RolesRenounced
signature:    Phase3RolesRenounced(uint64)
topic[0]:     0x<keccak256("Phase3RolesRenounced(uint64)") — recompute via
              `cast sig-event "Phase3RolesRenounced(uint64)"`>
topics[1..]:  (none — single non-indexed param)
data:         0x000000000000000000000000000000000000000000000000<lockedAt_hex_u64>
              ABI: uint64 lockedAt        — uint(block.timestamp) at flip
```

After the live D17 dress rehearsal lands, the operator runs
`script/print_phase3_evidence.s.sol` against the tx hash and pastes the
emitted Markdown block under the placeholder below. The script asserts:

1. Receipt's `logs` array contains **at least 2** logs whose `topics[0]`
   equals `keccak256("Phase3RolesRenounced(uint64)")`.
2. The two matching logs were emitted by **two distinct** addresses (one
   `PhaseManager`, one `EnergyController`).
3. Each log's `data` field is exactly 32 bytes (the ABI-encoded `uint64`).

Any deviation reverts:

| Error                              | Meaning                                                                                                                  |
|------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| `MissingInput()`                   | Neither `PHASE3_TX_HASH` nor `PHASE3_RECEIPT_PATH` env vars set.                                                          |
| `ReceiptHasNoLogs()`               | Receipt JSON has no `.logs` key — wrong tx hash, or tx never mined.                                                       |
| `MissingRenunciationEvents(found)` | Fewer than 2 matching events; `found` reports how many were located so the operator can see whether 0 or 1 lockPhase3 landed. |
| `EmittersNotDistinct(emitter)`     | Both matches came from the same contract — would only happen on a replay attempt (and contracts revert anyway).            |
| `LogDataMalformed(idx, len)`       | A topic-matching log had data ≠ 32 bytes — receipt corruption or spoof.                                                   |

### Phase 3 evidence — paste-ready block (refresh after live broadcast)

```markdown
### Phase 3 Role Renunciation Evidence

| Field                       | Value |
|-----------------------------|-------|
| `transactionHash`           | `0x0000000000000000000000000000000000000000000000000000000000000000` |
| `matchedLogCount`           | `2` |
| `PhaseManager` emitter      | `0x0000000000000000000000000000000000000000` |
| `PhaseManager` lockedAt     | `<uint64 block timestamp at flip>` |
| `EnergyController` emitter  | `0x0000000000000000000000000000000000000000` |
| `EnergyController` lockedAt | `<uint64 block timestamp at flip — same block as PhaseManager>` |

**Event signature**: `Phase3RolesRenounced(uint64 lockedAt)`
**topic[0]**: `<keccak256("Phase3RolesRenounced(uint64)")>`
**Assertion**: both `PhaseManager.lockPhase3()` and
`EnergyController.lockPhase3()` emitted Phase3RolesRenounced;
emitter addresses are distinct (one per contract).
```

### Running the evidence script

```bash
# Recommended (offline, demo-evidence file): pre-fetch receipt with cast,
# then read it deterministically. Works without an active --rpc-url.
cast receipt 0xTX_HASH --rpc-url "$RPC_URL" --json \
    > script/deployments/sprint_5/phase3_receipt.json

PHASE3_RECEIPT_PATH=script/deployments/sprint_5/phase3_receipt.json \
    forge script script/print_phase3_evidence.s.sol --sig "run()"

# Alternative (live RPC): forge issues eth_getTransactionReceipt internally.
PHASE3_TX_HASH=0xTX_HASH \
    forge script script/print_phase3_evidence.s.sol \
        --rpc-url "$RPC_URL" --sig "run()"
```

The script reverts with a typed error on any verification failure; success
prints the paste-ready Markdown block to stdout. Splice that block into the
section above, commit, and the demo README is ready for the Demo Day deck.

---

*Track A README — Genesis Experiment. Extended per task.*
