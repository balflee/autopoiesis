# Phase 3 Launch — D18 Operator Runbook

Genesis Experiment — sprint_5 / T-B-010.

This is the on-stream runbook for flipping the agent from Phase 2
(Apprentice) to Phase 3 (Master / LIVE). The Demo §9 window opens at
D18 and the operator MUST be able to execute these steps without
hesitation while the broadcast is rolling.

Every step has a **verify** command (proves the step landed) and a
**rollback** command (reverses the step if the verify fails).

References:

- PRD §6.13 Phase 3 LIVE definition
- PRD §5.0 three-stage descent
- TECHNICAL_PLAN §8 D18 plan; §9 D18 hard deadline; §12 demo insurance
- T-A-009 deploys `AdvanceToAdulthood.s.sol` + `RenouncePhase3MutableRoles.s.sol`
- T-B-010 (this task) ships `agent.ops.live_monitor` + `agent.ops.settlement_reconciler`

## Pre-flight (do these BEFORE going on camera)

### Step 0a — Confirm contracts deployed + verified

**Action**

```bash
# Polygon (settlement chain) deployment
forge script script/Phase3VerifyDeployments.s.sol \
    --rpc-url $POLYGON_RPC --sig "run()" -vv
# L3 (BREATH ledger) deployment
forge script script/Phase3VerifyDeployments.s.sol \
    --rpc-url $L3_RPC --sig "run()" -vv
```

**Verify**

Both scripts exit 0 and print `OK: all 4 contracts at expected addresses`.

**Rollback**

There is none — if a contract is missing or at the wrong address, ABORT
the launch and re-run the relevant T-A-* deployment task. Do not proceed.

### Step 0b — Confirm bankroll wallet posture

**Action**

```bash
cast call $USDC_POLYGON "balanceOf(address)(uint256)" $BANKROLL_WALLET \
    --rpc-url $POLYGON_RPC
```

**Verify**

Returned balance equals the funded amount on the launch ledger row
(`reports/phase3/bankroll_funding_<DATE>.json` is the canonical row).

**Rollback**

If the balance is wrong, do NOT advance — re-fund or surface the bug.
Phase 3 with the wrong bankroll is worse than not launching today.

### Step 0c — Confirm `GEMINI_API_KEY` + `PINATA_API_KEY` set

**Action**

```bash
test -n "$GEMINI_API_KEY" && echo "GEMINI: ok" || echo "GEMINI: MISSING"
test -n "$PINATA_API_KEY" && echo "PINATA: ok" || echo "PINATA: MISSING"
test -n "$PINATA_SECRET_KEY" && echo "PINATA_SECRET: ok" || echo "MISSING"
```

**Verify**

All three print `ok`. If any prints `MISSING`, set the env vars per
`SETUP_CHECKLIST.md` §P1 + §P2 BEFORE proceeding.

**Rollback**

`unset GEMINI_API_KEY` to drop back to the deterministic fail-soft
template path (the agent will still run; sentiment + reflection will
use templates instead of LLM output). Note this WILL show on the
dashboard's `gemini_cost=0` chip — communicate the degraded posture if
you go this route.

### Step 0d — Dry-run `polymarket_smoke` (no money moved)

**Action**

```bash
py -m harness.cli run-gate polymarket_smoke \
    --task T-B-010 --mode dry --timeout 30
```

**Verify**

Exit code 0, summary contains `clob_endpoint=reachable`. Do NOT run in
`--mode live` here — that requires a separate reviewer-approval flow
(see `.dev/policy/gate_matrix.yaml` `pre_polymarket_live`).

**Rollback**

If unreachable, switch to the staging CLOB:
`--clob-base https://clob-staging.polymarket.com`. If staging also fails,
the network is partitioned — postpone the launch.

## Phase 3 advance (THIS IS ON CAMERA — read deliberately)

### Step 1 — AdvanceToAdulthood

**Action**

```bash
forge script script/AdvanceToAdulthood.s.sol \
    --rpc-url $L3_RPC --broadcast --sender $OPERATOR_WALLET \
    --private-key $OPERATOR_PRIVATE_KEY -vv
```

**Verify**

```bash
cast call $PHASE_MANAGER_L3 "currentPhase()(uint8)" --rpc-url $L3_RPC
```

Returns `2` (Phase 3 ordinal — `PHASE_3_MASTER` is index 2 in the
StrEnum; the on-chain enum starts at 0 = Phase 1 Infancy).

**Rollback**

There is NO rollback — Phase 3 is irreversible by design (PRD §5.1.A
trustless: 死亡瞬间智能合约自动执行 `kill()`). Once the tx confirms, the
only paths out are natural death or `forceKill` (operator emergency). If
the tx reverted, re-run with `-vvvv` to capture the revert reason.

### Step 2 — RenouncePhase3MutableRoles

**Action**

```bash
forge script script/RenouncePhase3MutableRoles.s.sol \
    --rpc-url $L3_RPC --broadcast --sender $OPERATOR_WALLET \
    --private-key $OPERATOR_PRIVATE_KEY -vv
```

This burns the operator's `MUTABLE_ROLE` on `EnergyController`,
`PhaseManager`, `AgentLifecycle`, `TombstoneNFT`. After this point,
the contracts are immutable and the operator cannot patch.

**Verify**

```bash
cast call $ENERGY_CONTROLLER "hasRole(bytes32,address)(bool)" \
    $(cast keccak "MUTABLE_ROLE") $OPERATOR_WALLET --rpc-url $L3_RPC
```

Returns `false` for all four contracts.

**Rollback**

There is NO rollback. If the script reverted partway, you have a
partially-renounced posture — re-run the script (it is idempotent on
the contracts that already renounced; reverts only on the ones still
mutable). If the script reverted on Tx#1, the operator still has all
roles and can either retry or abort the launch.

### Step 3 — Flip agent config to phase=3

**Action**

```bash
# The agent reads phase from on-chain (Step 1 already covers this).
# The local config flip is the SOFT switch that enables Phase-3 only
# behaviour (Idle Decay on, Survival Horizon ρ on, donate() ✅ per
# PRD §6.13). Edit .env or systemd drop-in:
echo "AGENT_PHASE_OVERRIDE=PHASE_3_MASTER" >> .env
systemctl restart genesis-agent  # or your process manager
```

**Verify**

```bash
journalctl -u genesis-agent --since "1 minute ago" | grep "phase=PHASE_3_MASTER"
```

Returns at least one line.

**Rollback**

```bash
sed -i '/AGENT_PHASE_OVERRIDE/d' .env
systemctl restart genesis-agent
```

This drops the override; the agent re-reads on-chain phase (which is
already Phase 3 from Step 1) so the rollback is a no-op semantically —
included for symmetry.

## Live ops (run continuously through the Demo window)

### Step 4 — Start `live_monitor`

**Action**

```bash
py -m agent.ops.live_monitor &
echo $! > /run/genesis/live_monitor.pid
```

**Verify**

```bash
# Dashboard should show the five indicator chips
# (heartbeat, energy_drain, rpc_latency, ws_disconnects, gemini_cost)
# all green within 60s of startup.
curl -s http://localhost:8787/api/health/live_monitor | jq .ok
```

Returns `true`.

**Rollback**

```bash
kill -TERM $(cat /run/genesis/live_monitor.pid)
rm /run/genesis/live_monitor.pid
```

The monitor is OBSERVE-ONLY (see `agent/ops/live_monitor.py` module
docstring + the AST-scan test) so killing it does not affect the
agent's behaviour — only the dashboard chips go silent.

### Step 5 — Start `settlement_reconciler`

**Action**

```bash
py -m agent.ops.settlement_reconciler --interval-s 60 &
echo $! > /run/genesis/reconciler.pid
```

**Verify**

```bash
# The reconciler logs its first reconciliation report within 60s.
journalctl -u genesis-reconciler --since "2 minutes ago" \
    | grep "ReconciliationReport: matched="
```

Returns at least one line.

**Rollback**

```bash
kill -TERM $(cat /run/genesis/reconciler.pid)
rm /run/genesis/reconciler.pid
```

Killing the reconciler does NOT halt the chain — the on-chain
`usedNonces[signer][nonce]` mapping is the authoritative replay record
(per TP §3.7); the off-chain reconciler is a defence-in-depth observer.

## Failure mode index

| Symptom | Step that catches it | Action |
|---------|----------------------|--------|
| `heartbeat_stale` red chip | live_monitor → Step 4 | Check agent process; restart if PID is dead. |
| `energy_drain` red chip | live_monitor → Step 4 | Check Polymarket loss cluster; this may be expected during Desperate Mode. |
| `rpc_latency` red chip on Polygon | live_monitor → Step 4 | Switch to backup RPC: `cast wallet set-rpc $POLYGON_RPC_BACKUP`. |
| `ws_disconnects` red chip | live_monitor → Step 4 | Polymarket WS feed is degraded; degraded posture for ≤5 min is acceptable. |
| `gemini_cost` red chip (>100%) | live_monitor → Step 4 | CostGuard already short-circuited to template path; degraded posture noted. |
| `ReconciliationReport: rejected=N>0` | settlement_reconciler → Step 5 | Tier 1 critical — STOP THE SHOW, escalate per `.dev/workflow/escalation_protocol.md`. |
| `ReconciliationReport: drift=N>0` with `drift_usd > $1` | settlement_reconciler → Step 5 | Tier 1 critical — verify L3 + Polygon indexer; if a real $1+ drift, abort. |

## After-action

When the Demo window closes:

1. Leave `live_monitor` + `reconciler` running until the agent dies.
2. Capture `journalctl -u genesis-agent --since "<launch-time>"` to
   `reports/phase3/post_launch_<DATE>.log`.
3. Run `forge script script/Phase3PostMortem.s.sol` to snapshot
   final contract state.

End of runbook.
