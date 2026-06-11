// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2}  from "forge-std/Script.sol";
import {PhaseManager}      from "contracts/PhaseManager.sol";
import {EnergyController}  from "contracts/EnergyController.sol";

/// @title  RenouncePhase3MutableRoles — D17 / D18 trust-finalisation script
/// @notice Operator-run Foundry script that fires the **one-way valve**
///         on the Genesis Experiment's admin keys for Phase 3
///         (TECHNICAL_PLAN §8 D17 dress rehearsal + §9 D18 cutover).
///         After this script lands, the deployer / operator key NO LONGER
///         controls protocol state — PRD §5.1.A trustlessness.
///
///         Renunciation target list (per task brief):
///           * EnergyController:
///               - DEPLOYER_ADMIN  → renounced (setOwner / pause / unpause
///                                              all revert Phase3IsLocked)
///               - PARAM_TUNER     → renounced (setAttestationSigner /
///                                              setPhaseManager / setPhase
///                                              all revert Phase3IsLocked)
///           * PhaseManager:
///               - DEPLOYER_ADMIN  → renounced (setOwner reverts
///                                              Phase3IsLocked)
///               - PARAM_TUNER     → renounced (transitionTo* reverts
///                                              Phase3IsLocked, on top of
///                                              the existing onlyInPhase)
///         Preserved roles (intentional — death + decision flow must
///         continue post-renounce):
///           * TombstoneNFT.agentLifecycle   — IMMUTABLE, locked at
///                                              construction (no setter).
///           * DecisionLog.agentLifecycle    — IMMUTABLE, locked at
///                                              construction (no setter).
///           * AgentLifecycle.owner          — Agent EOA, NOT touched here.
///           * PhaseManager.enterDesperateMode operational path — left
///             callable by the owner per PRD §6.9.
///           * EnergyController operational paths (burns, top-up, bankroll
///             moves, lung expansion, settlement) — left callable by the
///             owner so Phase-3 mechanics can continue.
///
///         **Dry-run is the default.** A real transaction is broadcast
///         ONLY when env var `WRITE_BROADCAST=true` is set.
///
///         **This script is irreversible.** It MUST NOT be re-run after
///         success — the underlying `lockPhase3` functions are set-once
///         and will revert `Phase3IsLocked` on the second attempt.
///
///         Address resolution (same priority as AdvanceToAdulthood):
///           1. `PHASE_MANAGER` / `ENERGY_CONTROLLER` env vars.
///           2. `script/deployments/sprint_4/${CHAIN_NAME}.json`.
///           3. Revert `DeploymentNotResolved` otherwise.
///
///         Spec anchors:
///           * PRD §5.1.A    — 永久死亡（不可逆）规则写进合约 — trustless.
///           * PRD §6.13     — Phase 3 row continues to operate after
///                              renounce (burns, lung expansion, desperate
///                              gating).
///           * PRD §10       — Pause/Upgrade roles auto-renounced on Phase 3.
///           * TP §8 D17     — dress rehearsal in testnet.
///           * TP §9         — D17→D19 critical path.
///
///         Usage:
///           # Dry-run on a populated sprint_4 manifest.
///           forge script script/RenouncePhase3MutableRoles.s.sol \
///               --rpc-url $L3_RPC --sig "run()" -vvv
///
///           # Real broadcast (the D17 dress-rehearsal tx).
///           WRITE_BROADCAST=true \
///           CHAIN_NAME=orbit_testnet \
///           forge script script/RenouncePhase3MutableRoles.s.sol \
///               --rpc-url $L3_RPC --account deployer --sig "run()" \
///               --broadcast -vvv
contract RenouncePhase3MutableRoles is Script {
    /// @notice Bubbles up when neither env var nor JSON manifest yields a
    ///         contract address.
    error DeploymentNotResolved(string chainName);

    /// @notice Operator pointed the script at a deployment whose
    ///         PhaseManager phase is not Adulthood. The renounce ritual
    ///         can only be run AFTER AdvanceToAdulthood succeeds.
    error PhaseManagerNotInAdulthood(uint8 actual);

    /// @notice EnergyController mirror is not in Adulthood. The operator
    ///         must run AdvanceToAdulthood first (which flips both
    ///         contracts atomically).
    error EnergyControllerNotInAdulthood(uint8 actual);

    /// @notice One (or both) contracts already report `phase3Locked == true`.
    ///         The script is a one-shot; refuse to re-run.
    error AlreadyLocked();

    /// @notice Post-script invariant failed; either the broadcast was
    ///         reverted off-script or the contract returned an
    ///         inconsistent view.
    error PhaseLockNotPersisted(string contractName);

    /// @notice Returned by `run()` so the D17 dress-rehearsal log captures
    ///         the audit-anchor timestamps. Both should be within the
    ///         same broadcast block.
    struct RenounceResult {
        address phaseManager;
        address energyController;
        bool    broadcast;
        bool    pmLockedAfter;
        bool    ecLockedAfter;
        uint64  ranAt;
    }

    function run() external returns (RenounceResult memory result) {
        // 1. Resolve addresses.
        address phaseManagerAddr     = _resolveAddress("PHASE_MANAGER",     ".contracts.PhaseManager");
        address energyControllerAddr = _resolveAddress("ENERGY_CONTROLLER", ".contracts.EnergyController");
        PhaseManager     pm = PhaseManager(phaseManagerAddr);
        EnergyController ec = EnergyController(energyControllerAddr);

        // 2. Validate pre-state: BOTH contracts in Adulthood, neither
        //    already locked.
        if (pm.currentPhase() != PhaseManager.Phase.Adulthood) {
            revert PhaseManagerNotInAdulthood(uint8(pm.currentPhase()));
        }
        if (ec.currentPhase() != EnergyController.Phase.Adulthood) {
            revert EnergyControllerNotInAdulthood(uint8(ec.currentPhase()));
        }
        if (pm.isPhase3Locked() || ec.isPhase3Locked()) {
            revert AlreadyLocked();
        }

        // 3. Decide broadcast vs dry-run.
        bool broadcast = vm.envOr("WRITE_BROADCAST", false);
        console2.log("RenouncePhase3MutableRoles.run() -- TP D17 trust finalisation");
        console2.log("  PhaseManager    :", phaseManagerAddr);
        console2.log("  EnergyController:", energyControllerAddr);
        console2.log("  pm.owner()      :", pm.owner());
        console2.log("  ec.owner()      :", ec.owner());
        console2.log("  broadcast       :", broadcast);
        console2.log("  >>> ONE-WAY VALVE: after this tx, no admin / param-tuner key can mutate protocol.");

        if (broadcast) {
            vm.startBroadcast();
            // Order is deliberate: lock the EC param-tuner channel FIRST
            // so attestation signer / phase manager pointer cannot be
            // re-pointed between the two txs. Then the PhaseManager.
            ec.lockPhase3();
            pm.lockPhase3();
            vm.stopBroadcast();
        } else {
            vm.startPrank(ec.owner());
            ec.lockPhase3();
            vm.stopPrank();
            vm.startPrank(pm.owner());
            pm.lockPhase3();
            vm.stopPrank();
            console2.log("  [DRY-RUN] locks simulated; no tx broadcast.");
        }

        // 4. Post-condition: both contracts report `phase3Locked == true`.
        bool pmLocked = pm.isPhase3Locked();
        bool ecLocked = ec.isPhase3Locked();
        if (!pmLocked) revert PhaseLockNotPersisted("PhaseManager");
        if (!ecLocked) revert PhaseLockNotPersisted("EnergyController");

        // 5. Final smoke — pin that an admin path now reverts. Use
        //    `vm.startPrank(owner)` so the simulation runs from the
        //    legitimate caller; the revert is the proof that the lock is
        //    binding even for the original key.
        //    Static-call style: we attempt a setOwner call and catch the
        //    revert reason. Test suite owns the strict pinning; the script
        //    treats this as a smoke check that fails loudly.
        _smokeAdminLocked(pm, ec);

        console2.log("  PhaseManager     locked:", pmLocked);
        console2.log("  EnergyController locked:", ecLocked);
        console2.log("RenouncePhase3MutableRoles.run() OK -- protocol is now trustless.");

        result = RenounceResult({
            phaseManager:     phaseManagerAddr,
            energyController: energyControllerAddr,
            broadcast:        broadcast,
            pmLockedAfter:    pmLocked,
            ecLockedAfter:    ecLocked,
            // forge-lint: disable-next-line(block-timestamp)
            ranAt:            uint64(block.timestamp)
        });
    }

    /// @dev Smoke check that confirms post-lock admin paths revert. We
    ///      use `vm.startPrank` + `try/catch` rather than `vm.expectRevert`
    ///      because forge-std scripts don't ship the cheatcode-test
    ///      vocabulary; the script only needs to KNOW the call reverted,
    ///      not assert on the exact selector (the test suite does that).
    function _smokeAdminLocked(PhaseManager pm, EnergyController ec) internal {
        // PhaseManager setOwner — should revert Phase3IsLocked.
        address pmOwner = pm.owner();
        vm.startPrank(pmOwner);
        try pm.setOwner(pmOwner) {
            revert PhaseLockNotPersisted("PhaseManager.setOwner did not revert");
        } catch {
            // Expected — admin path is locked.
        }
        vm.stopPrank();

        // EnergyController setAttestationSigner — should revert
        // Phase3IsLocked.
        address ecOwner = ec.owner();
        vm.startPrank(ecOwner);
        try ec.setAttestationSigner(ecOwner) {
            revert PhaseLockNotPersisted("EnergyController.setAttestationSigner did not revert");
        } catch {
            // Expected — param tuner is locked.
        }
        vm.stopPrank();
    }

    /// @dev Address resolution shared by both contracts. `envVarName` is
    ///      the optional override; `jsonKey` is the dot-path inside the
    ///      sprint_4 JSON manifest.
    function _resolveAddress(string memory envVarName, string memory jsonKey)
        internal
        view
        returns (address)
    {
        address envOverride = vm.envOr(envVarName, address(0));
        if (envOverride != address(0)) {
            return envOverride;
        }
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
