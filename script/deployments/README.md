# `script/deployments/` — chain-side deployment manifests

This directory holds per-sprint, per-chain JSON manifests recording the five
canonical Genesis Experiment contract addresses produced by the corresponding
sprint's deploy script. Manifests are consumed by operator scripts (e.g.
`AdvanceToApprenticeship.s.sol`, future Phase-3 renounce ritual) and by Track
B's chain adapter for address resolution.

## Layout

```
script/deployments/
  sprint_3/
    anvil.json         — local-anvil deployment (CI / dev / smoke)
    sepolia.json       — public testnet deployment (optional)
    orbit_testnet.json — Arbitrum Orbit L3 testnet deployment
    orbit_mainnet.json — Arbitrum Orbit L3 mainnet deployment (D11 target)
  sprint_4/            — future sprints add their own subdir
```

## Schema (sprint_3)

```json
{
  "$schema":   "https://genesis.experiment/schemas/deployment_manifest.v1.json",
  "sprint":    "sprint_3",
  "chain":     "anvil",
  "chainId":   31337,
  "deployedAt": "2026-05-22T00:00:00Z",
  "deployer":  "0x...",
  "contracts": {
    "EnergyController": "0x...",
    "PhaseManager":     "0x...",
    "AgentLifecycle":   "0x...",
    "DecisionLog":      "0x...",
    "TombstoneNFT":     "0x..."
  }
}
```

`AdvanceToApprenticeship.s.sol` reads exactly the `.contracts.PhaseManager`
JSON path via `vm.parseJsonAddress`; other fields are advisory metadata
for human operators.

## Address-resolution priority

Operator scripts in this repo resolve addresses in this order, highest first:

1. **Env-var override** — e.g. `PHASE_MANAGER=0x...`. Used for ad-hoc / replay
   / fork-test invocations.
2. **Deployment manifest** — `script/deployments/sprint_3/${CHAIN_NAME}.json`,
   where `CHAIN_NAME` defaults to `anvil`. This is the canonical path the D11
   launch operator uses.
3. **Revert with a domain-specific error** if neither resolves.

## Populating a new manifest

Run the sprint's deploy script with `--broadcast` and copy the broadcast log
addresses into a fresh manifest. There is no automated writer today — the
sprint_3 deploy script's broadcast artefact under `broadcast/` is the source
of truth; this directory is a curated mirror.

## Gitignore note

Manifests for mainnet deployments are committed (they are public addresses).
Manifests for ad-hoc local anvil sessions should be created locally and not
committed unless they pin a reproducible CI fixture.
