# The Genesis Experiment — Submission Manifest

- **Schema version:** `1.1.0`
- **Commit:** `3c350830ffbf041eddc305ea3a84167f8cb52f9a`
- **Generated at:** `2026-05-26T03:00:00Z` (UTC)

## Deployed Contracts (3-Chain Parallel)

| Contract | robinhood_chain_testnet | arbitrum_sepolia | polygon_amoy |
| --- | --- | --- | --- |
| `EnergyController` | [`0xeb504449195b0491F52b455650056f0763A54525`](https://explorer.testnet.chain.robinhood.com/address/0xeb504449195b0491F52b455650056f0763A54525) | [`0xeb504449195b0491F52b455650056f0763A54525`](https://sepolia.arbiscan.io/address/0xeb504449195b0491F52b455650056f0763A54525) | [`0x0000000000000000000000000000000000000000`](https://amoy.polygonscan.com/address/0x0000000000000000000000000000000000000000) ⚠ placeholder |
| `PhaseManager` | [`0x20e07db0169E35553a66608736161f433d8E44E0`](https://explorer.testnet.chain.robinhood.com/address/0x20e07db0169E35553a66608736161f433d8E44E0) | [`0x20e07db0169E35553a66608736161f433d8E44E0`](https://sepolia.arbiscan.io/address/0x20e07db0169E35553a66608736161f433d8E44E0) | [`0x0000000000000000000000000000000000000000`](https://amoy.polygonscan.com/address/0x0000000000000000000000000000000000000000) ⚠ placeholder |
| `AgentLifecycle` | [`0x125929f6451e5e5Fa9C64b498646793CaF5b4128`](https://explorer.testnet.chain.robinhood.com/address/0x125929f6451e5e5Fa9C64b498646793CaF5b4128) | [`0x125929f6451e5e5Fa9C64b498646793CaF5b4128`](https://sepolia.arbiscan.io/address/0x125929f6451e5e5Fa9C64b498646793CaF5b4128) | [`0x0000000000000000000000000000000000000000`](https://amoy.polygonscan.com/address/0x0000000000000000000000000000000000000000) ⚠ placeholder |
| `DecisionLog` | [`0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818`](https://explorer.testnet.chain.robinhood.com/address/0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818) | [`0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818`](https://sepolia.arbiscan.io/address/0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818) | [`0x0000000000000000000000000000000000000000`](https://amoy.polygonscan.com/address/0x0000000000000000000000000000000000000000) ⚠ placeholder |
| `TombstoneNFT` | [`0xDE6178D892AA9F80f748a399f07B588b08Faec2f`](https://explorer.testnet.chain.robinhood.com/address/0xDE6178D892AA9F80f748a399f07B588b08Faec2f) | [`0xDE6178D892AA9F80f748a399f07B588b08Faec2f`](https://sepolia.arbiscan.io/address/0xDE6178D892AA9F80f748a399f07B588b08Faec2f) | [`0x0000000000000000000000000000000000000000`](https://amoy.polygonscan.com/address/0x0000000000000000000000000000000000000000) ⚠ placeholder |

### Chain metadata

| Chain | Chain ID | Deploy block | Status |
| --- | --- | --- | --- |
| `robinhood_chain_testnet` | `46630` | `60897767` | live |
| `arbitrum_sepolia` | `421614` | `10917212` | live |
| `polygon_amoy` | `80002` | `0` | ⚠ placeholder |

## ABI Hashes (Canonical sha256)

Canonicalisation: `json.dumps(abi, sort_keys=True, separators=(',', ':'), ensure_ascii=False)` then `sha256`. Reproducible across runs; whitespace-insensitive (see `agent/submission/abi_hasher.py`).

| Contract | ABI version | ABI file | sha256 |
| --- | --- | --- | --- |
| `EnergyController` | `0.4.0` | `energy_controller_abi.v0.4.0.json` | `7803cff04cb2092ebde2b4bf358244f07058163ab1214a8e2e5c1abf94c0eb9a` |
| `PhaseManager` | `0.3.0` | `phase_manager_abi.v0.3.0.json` | `f71d12c9afe7f6bbe62781996f4ba6879bd38e90e1d8849d263c4802968e9cec` |
| `AgentLifecycle` | `0.3.0` | `agent_lifecycle_abi.v0.3.0.json` | `2d7fcf7f0111af534c8b281eba4610076ba2576e048c3d9be85082530a5afcc9` |
| `DecisionLog` | `0.1.0` | `decision_log_abi.v0.1.0.json` | `9abb46dd00c0beea51551599c6db38543412fe1c8bbce6e866c11e5e1c976912` |
| `TombstoneNFT` | `0.2.0` | `tombstone_nft_abi.v0.2.0.json` | `64874cf596344cbf9d47f6a0aaf77746642539a1ad080f7502efcf34f507ff39` |

## Phase 1 Training Headline (Sprint 7 — Tennis Pivot)

- **Sport:** `tennis`
- **Dataset:** `data/parquet/tennis_phase1.parquet`
- **Training matches / Test matches / Epochs:** `88` / `22` / `12`

| Metric | Uniform baseline | Trained | Improvement |
| --- | --- | --- | --- |
| Log-loss | `0.6498` | `0.5973` | `8.07%` |

### Final weights (`weights_v0.json`)

```json
{
  "alpha": [
    0.7869408587530673,
    0.10700322092982174,
    0.10605592031711109
  ],
  "beta": [
    0.0,
    1.0
  ],
  "rho": 0.5,
  "w_r": 0.5,
  "w_s": 0.5
}
```

- Backtest report: `reports/phase1/backtest_report.json`
- Weights snapshot: `reports/phase1/weights_v0.json`

## Phase 2 Dry-Run Verdict (Sprint 7 — Day 6 closer)

- **Log:** `logs/phase2_dryrun/sprint7_dryrun.jsonl`
- **Summary:** `logs/phase2_dryrun/sprint7_dryrun_summary.md`

| Metric | Value |
| --- | --- |
| Decisions emitted | `5` (BET=`5` / NO_BET=`0`) |
| Idle heartbeats | `0` |
| Tennis markets discovered (gamma-api) | `5` |
| Broadcasts (✅ no signed orders) | `0` |
| Real-market reference (✅ real tennis market referenced) | `True` |

## Phase 3 Launch + Role Renunciation

Per PRD §5.1 + TECHNICAL_PLAN §15 Gap 7, the Phase-3 launch tx emits BOTH `EnergyController.Phase3RolesRenounced` (= `PauseRoleRenounced`) and `PhaseManager.Phase3RolesRenounced` (= `UpgradeRoleRenounced`) — the agent EOA permanently loses pause + upgrade authority in the same atomic action.

| Chain | Launch tx | Pause role renounced tx | Upgrade role renounced tx | Block | Status |
| --- | --- | --- | --- | --- | --- |
| `robinhood_chain_testnet` | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://explorer.testnet.chain.robinhood.com/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://explorer.testnet.chain.robinhood.com/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://explorer.testnet.chain.robinhood.com/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | `0` | ⚠ placeholder |
| `arbitrum_sepolia` | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://sepolia.arbiscan.io/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://sepolia.arbiscan.io/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://sepolia.arbiscan.io/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | `0` | ⚠ placeholder |
| `polygon_amoy` | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://amoy.polygonscan.com/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://amoy.polygonscan.com/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | [`0x0000000000000000000000000000000000000000000000000000000000000000`](https://amoy.polygonscan.com/tx/0x0000000000000000000000000000000000000000000000000000000000000000) | `0` | ⚠ placeholder |

## IPFS Anchors

| Name | CID | Status |
| --- | --- | --- |
| `memory_bank_v1` | `bafyplaceholder0000000000000000000000000000000000000000000` | ⚠ placeholder |
| `tombstone_metadata` | `bafyplaceholder0000000000000000000000000000000000000000000` | ⚠ placeholder |
| `demo_assets` | `bafyplaceholder0000000000000000000000000000000000000000000` | ⚠ placeholder |

## Demo Video

- **URL:** [https://placeholder.invalid/demo.mp4](https://placeholder.invalid/demo.mp4)
- **sha256:** `0000000000000000000000000000000000000000000000000000000000000000`
- **Duration:** `0` seconds
- **Status:** ⚠ placeholder

## Pre-Demo Staging Rehearsal (TP §15 Gap 7)

- **Verdict:** ❌ FAILED
- **Report path:** `submission\rehearsal_report.placeholder.json`
- **Failure reason:** `MISSING_RENUNCIATION_EVENT`

### Diagnostic counts

- Desperate Mode entries: `0` (pass: ≥ 1)
- Lung Expansion events: `0` (pass: ≥ 1)
- Market loss settlements: `0` (informational)
- WS disconnects: `0` (pass: == 0)
- **Pause role renounced tx:** _not observed_
- **Upgrade role renounced tx:** _not observed_
