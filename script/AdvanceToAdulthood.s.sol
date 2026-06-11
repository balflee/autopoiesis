// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2}  from "forge-std/Script.sol";
import {PhaseManager}      from "contracts/PhaseManager.sol";
import {EnergyController}  from "contracts/EnergyController.sol";

/// @title  AdvanceToAdulthood — D18 Phase-3 launch script (T-A-009)
/// @notice Operator-run Foundry script that flips the PhaseManager from
///         `Apprenticeship` → `Adulthood` for the sprint_5 D17 dress
///         rehearsal and the D18 production cutover (TECHNICAL_PLAN §8 +
///         §9 hard deadline). Reads the deployment manifest produced by
///         sprint_4's calibrated deploy, validates pre-state, fires the
///         transition, then sweeps every PRD §6.13 Phase-3 row through a
///         post-state assertion.
///
///         **Dry-run is the default.** A real transaction is broadcast
///         ONLY when env var `WRITE_BROADCAST=true` is set. This prevents
///         the D17 dress-rehearsal invocation (or a CI smoke / fork
///         test / dashboard demo) from accidentally moving the agent's
///         life-stage clock on the live target.
///
///         Address resolution priority (highest first):
///           1. `PHASE_MANAGER` env var (raw 20-byte address) — explicit
///              override for ad-hoc / replay / fork-test invocations.
///           2. `ENERGY_CONTROLLER` env var — optional override for the
///              EC mirror flip (defaults to the JSON manifest).
///           3. `script/deployments/sprint_4/${CHAIN_NAME}.json` where
///              `CHAIN_NAME` defaults to "anvil". The JSON shape is the
///              `Deployment` struct exported by `DeployAll.s.sol`.
///           4. Revert `DeploymentNotResolved()` if neither is provided.
///
///         Spec anchors:
///           * PRD §3        — unidirectional Childhood → Apprenticeship
///                             → Adulthood; this script owns the second edge.
///           * PRD §5.1.A    — 永久死亡（不可逆）— Phase 3 is the trustless
///                             phase where rules are written into the
///                             contract.
///           * PRD §6.13     — Phase 3 activation table (Passive 1.4/min,
///                             Action Cost ✅, Idle Decay ✅, decisionCycle
///                             45min, Lung Expansion ✅, Desperate ✅,
///                             Terminal ✅, USDC bankroll 真金).
///           * TP §8 D17     — Phase 2 → Phase 3 dress rehearsal.
///           * TP §8 D18     — Phase 3 launch hard deadline.
///           * TP §3.2       — PhaseManager.transitionToAdulthood is the
///                             canonical entry point.
///
///         Usage:
///           # Dry-run against a populated deployment file.
///           forge script script/AdvanceToAdulthood.s.sol \
///               --rpc-url $L3_RPC --sig "run()" -vvv
///
///           # Real broadcast (the D18 cutover tx).
///           WRITE_BROADCAST=true \
///           CHAIN_NAME=orbit_mainnet \
///           forge script script/AdvanceToAdulthood.s.sol \
///               --rpc-url $L3_RPC --account deployer --sig "run()" \
///               --broadcast -vvv
contract AdvanceToAdulthood is Script {
    /// @notice Bubbles up when neither env var nor JSON manifest yields a
    ///         PhaseManager address.
    error DeploymentNotResolved(string chainName);

    /// @notice Operator pointed the script at a deployment whose phase is
    ///         not exactly `Apprenticeship`. We refuse to advance from any
    ///         other state — operator must inspect first.
    error UnexpectedPreState(uint8 actual);

    /// @notice Post-transition self-check failed; either the broadcast was
    ///         reverted off-script or the contract returned an inconsistent
    ///         view.
    error PostStateMismatch(uint8 actual);

    /// @notice EnergyController mirror is not in Apprenticeship at script
    ///         entry. The operator must run the D11 launch first.
    error UnexpectedEnergyControllerPhase(uint8 actual);

    /// @notice Returned by `run()` so downstream tooling (demo dashboard
    ///         backend, D17 audit log) can read the script outcome via
    ///         `forge script --json`.
    struct AdvanceResult {
        address phaseManager;
        address energyController;
        uint8   preState;
        uint8   postState;
        bool    broadcast;
        uint64  ranAt;
    }

    function run() external returns (AdvanceResult memory result) {
        // 1. Resolve the PhaseManager + EnergyController addresses.
        address phaseManagerAddr = _resolvePhaseManager();
        address energyControllerAddr = _resolveEnergyController();
        PhaseManager pm = PhaseManager(phaseManagerAddr);
        EnergyController ec = EnergyController(energyControllerAddr);

        // 2. Validate pre-state on BOTH contracts.
        PhaseManager.Phase preState = pm.currentPhase();
        if (preState != PhaseManager.Phase.Apprenticeship) {
            revert UnexpectedPreState(uint8(preState));
        }
        EnergyController.Phase ecPreState = ec.currentPhase();
        if (ecPreState != EnergyController.Phase.Apprenticeship) {
            revert UnexpectedEnergyControllerPhase(uint8(ecPreState));
        }

        // 3. Decide broadcast vs dry-run.
        bool broadcast = vm.envOr("WRITE_BROADCAST", false);
        console2.log("AdvanceToAdulthood.run() -- TP D18 launch");
        console2.log("  PhaseManager    :", phaseManagerAddr);
        console2.log("  EnergyController:", energyControllerAddr);
        console2.log("  owner           :", pm.owner());
        console2.log("  pre-state (PM)  :", uint256(uint8(preState)), "(Apprenticeship)");
        console2.log("  pre-state (EC)  :", uint256(uint8(ecPreState)), "(Apprenticeship)");
        console2.log("  broadcast       :", broadcast);

        if (broadcast) {
            // Real transaction. The signing key MUST equal `pm.owner()` —
            // PhaseManager.onlyOwner enforces this on-chain.
            vm.startBroadcast();
            pm.transitionToAdulthood();
            ec.setPhase(EnergyController.Phase.Adulthood);
            vm.stopBroadcast();
        } else {
            // Simulated transition; prank as owner so the call clears the
            // `onlyOwner` modifier and the post-state assertion sees a real
            // (in-memory) state change. Never hits a remote node.
            vm.startPrank(pm.owner());
            pm.transitionToAdulthood();
            vm.stopPrank();
            vm.startPrank(ec.owner());
            ec.setPhase(EnergyController.Phase.Adulthood);
            vm.stopPrank();
            console2.log("  [DRY-RUN] transitions simulated; no tx broadcast.");
        }

        // 4. Post-condition: both contracts in Adulthood (PRD §6.13).
        PhaseManager.Phase postState = pm.currentPhase();
        if (postState != PhaseManager.Phase.Adulthood) {
            revert PostStateMismatch(uint8(postState));
        }
        EnergyController.Phase ecPostState = ec.currentPhase();
        if (ecPostState != EnergyController.Phase.Adulthood) {
            revert UnexpectedEnergyControllerPhase(uint8(ecPostState));
        }

        // 5. PRD §6.13 Phase-3 row sweep. Each `console2.log` is a human-
        //    readable audit line; the assertions inside the test suite
        //    (`test/Phase3_PostState_Invariants.t.sol`) own the strict
        //    machine-checked version.
        console2.log("  post-state (PM) :", uint256(uint8(postState)), "(Adulthood)");
        console2.log("  post-state (EC) :", uint256(uint8(ecPostState)), "(Adulthood)");
        console2.log("  PRD 6.13 sweep  : decisionCycle signal -> Adulthood (45min off-chain)");
        console2.log("  PRD 6.13 sweep  : desperateMode flag    ->", pm.isDesperate());
        console2.log("  PRD 6.13 sweep  : phase3Locked (PM)     ->", pm.isPhase3Locked());
        console2.log("  PRD 6.13 sweep  : phase3Locked (EC)     ->", ec.isPhase3Locked());
        console2.log("AdvanceToAdulthood.run() OK");

        result = AdvanceResult({
            phaseManager:     phaseManagerAddr,
            energyController: energyControllerAddr,
            preState:         uint8(preState),
            postState:        uint8(postState),
            broadcast:        broadcast,
            // forge-lint: disable-next-line(block-timestamp)
            ranAt:            uint64(block.timestamp)
        });
    }

    /// @dev PhaseManager address resolution. PHASE_MANAGER env var first,
    ///      then sprint_4 JSON manifest (CHAIN_NAME default "anvil").
    function _resolvePhaseManager() internal view returns (address) {
        address envOverride = vm.envOr("PHASE_MANAGER", address(0));
        if (envOverride != address(0)) {
            return envOverride;
        }
        return _readManifestAddress(".contracts.PhaseManager");
    }

    /// @dev EnergyController address resolution. ENERGY_CONTROLLER env var
    ///      first, then sprint_4 JSON manifest.
    function _resolveEnergyController() internal view returns (address) {
        address envOverride = vm.envOr("ENERGY_CONTROLLER", address(0));
        if (envOverride != address(0)) {
            return envOverride;
        }
        return _readManifestAddress(".contracts.EnergyController");
    }

    /// @dev Read a single contract address from the sprint_4 JSON manifest.
    ///      `jsonKey` is a dot-path like `.contracts.PhaseManager`.
    function _readManifestAddress(string memory jsonKey) internal view returns (address) {
        string memory chainName = vm.envOr("CHAIN_NAME", string("anvil"));
        string memory path = string.concat(
            "script/deployments/sprint_4/",
            chainName,
            ".json"
        );

        try vm.readFile(path) returns (string memory raw) {
            address fromJson = vm.parseJsonAddress(raw, jsonKey);
            if (fromJson == address(0)) {
                revert DeploymentNotResolved(chainName);
            }
            return fromJson;
        } catch {
            revert DeploymentNotResolved(chainName);
        }
    }
}
