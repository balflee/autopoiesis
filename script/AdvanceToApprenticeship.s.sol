// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {PhaseManager}    from "contracts/PhaseManager.sol";

/// @title  AdvanceToApprenticeship — D11 Phase-2 launch script (T-A-006)
/// @notice Operator-run Foundry script that flips the PhaseManager from
///         `Childhood` → `Apprenticeship` for the sprint_4 D11 launch
///         (TECHNICAL_PLAN §8 + §9 hard deadline). Reads the deployment
///         manifest produced by sprint_3's calibrated deploy, validates
///         pre-state, fires the transition, and asserts post-state.
///
///         **Dry-run is the default.** A real transaction is broadcast
///         ONLY when env var `WRITE_BROADCAST=true` is set. This prevents
///         the operator's first invocation (e.g. CI smoke, dashboard
///         demo, "is the file path right?") from accidentally moving the
///         agent's life-stage clock.
///
///         Address resolution priority (highest first):
///           1. `PHASE_MANAGER` env var (raw 20-byte address) — explicit
///              override for ad-hoc / replay / fork-test invocations.
///           2. `script/deployments/sprint_3/${CHAIN_NAME}.json` where
///              `CHAIN_NAME` defaults to "anvil". The JSON shape is the
///              `Deployment` struct in `DeployAll.s.sol` exported via
///              `vm.serializeAddress`; see `script/deployments/README.md`.
///           3. Revert `DeploymentNotResolved()` if neither is provided.
///
///         Spec anchors:
///           * PRD §3        — unidirectional Childhood → Apprenticeship
///                             → Adulthood; this script owns the first edge.
///           * PRD §6.13     — Phase 2 activation table (Passive Metabolism
///                             half-speed, ActionCost ON, Idle Decay OFF,
///                             强制决策周期 60min, Lung Expansion ON,
///                             Apprenticeship Failure reset ON, USDC
///                             bankroll shadow).
///           * TP §8 D11     — Phase 2 launch hard deadline.
///           * TP §3.2       — PhaseManager.transitionToApprenticeship
///                             is the canonical entry-point; ABI v0.1.0
///                             (`.dev/contracts/phase_manager_abi.v0.1.0.json`).
///
///         Usage:
///           # Dry-run against a populated deployment file.
///           forge script script/AdvanceToApprenticeship.s.sol \
///               --rpc-url $L3_RPC --sig "run()" -vvv
///
///           # Real broadcast (the hard deadline tx).
///           WRITE_BROADCAST=true \
///           CHAIN_NAME=orbit_mainnet \
///           forge script script/AdvanceToApprenticeship.s.sol \
///               --rpc-url $L3_RPC --account deployer --sig "run()" \
///               --broadcast -vvv
contract AdvanceToApprenticeship is Script {
    /// @notice Bubbles up to the operator when neither env var nor JSON
    ///         manifest yields a PhaseManager address.
    error DeploymentNotResolved(string chainName);

    /// @notice Operator pointed the script at a deployment whose phase
    ///         is not exactly `Childhood`. We refuse to advance from any
    ///         other state — operator must inspect first.
    error UnexpectedPreState(uint8 actual);

    /// @notice Post-transition self-check failed; either the broadcast
    ///         was reverted off-script or the contract returned an
    ///         inconsistent view.
    error PostStateMismatch(uint8 actual);

    /// @notice Result struct returned by `run()` so downstream tooling
    ///         (e.g. demo dashboard backend) can read the script outcome
    ///         via `forge script --json`.
    struct AdvanceResult {
        address phaseManager;
        uint8   preState;
        uint8   postState;
        bool    broadcast;
    }

    function run() external returns (AdvanceResult memory result) {
        // 1. Resolve the PhaseManager address.
        address phaseManagerAddr = _resolvePhaseManager();
        PhaseManager pm = PhaseManager(phaseManagerAddr);

        // 2. Validate pre-state.
        PhaseManager.Phase preState = pm.currentPhase();
        if (preState != PhaseManager.Phase.Childhood) {
            revert UnexpectedPreState(uint8(preState));
        }

        // 3. Decide broadcast vs dry-run.
        bool broadcast = vm.envOr("WRITE_BROADCAST", false);
        console2.log("AdvanceToApprenticeship.run()");
        console2.log("  PhaseManager:", phaseManagerAddr);
        console2.log("  owner       :", pm.owner());
        console2.log("  pre-state   :", uint256(uint8(preState)), "(Childhood)");
        console2.log("  broadcast   :", broadcast);

        if (broadcast) {
            // Real transaction. Whoever signs via `--account` / `--private-key`
            // MUST be `pm.owner()` — the PhaseManager's `onlyOwner` modifier
            // enforces it on-chain and will revert any other caller.
            vm.startBroadcast();
            pm.transitionToApprenticeship();
            vm.stopBroadcast();
        } else {
            // Simulated transition; prank as owner so the call clears the
            // `onlyOwner` modifier and the post-state assertion below sees
            // a real (in-memory) state change. The simulation runs in
            // forge's fork context and never hits a remote node.
            vm.prank(pm.owner());
            pm.transitionToApprenticeship();
            console2.log("  [DRY-RUN] transition simulated; no tx broadcast.");
        }

        // 4. Post-condition: PhaseManager.currentPhase == Apprenticeship.
        PhaseManager.Phase postState = pm.currentPhase();
        if (postState != PhaseManager.Phase.Apprenticeship) {
            revert PostStateMismatch(uint8(postState));
        }

        console2.log("  post-state  :", uint256(uint8(postState)), "(Apprenticeship)");
        console2.log("AdvanceToApprenticeship.run() OK");

        result = AdvanceResult({
            phaseManager: phaseManagerAddr,
            preState:     uint8(preState),
            postState:    uint8(postState),
            broadcast:    broadcast
        });
    }

    /// @dev Address resolution order: PHASE_MANAGER env var first, then
    ///      the sprint_3 deployment JSON. The JSON path is parameterised
    ///      by `CHAIN_NAME` (default "anvil") so the same script works for
    ///      anvil / sepolia / orbit_testnet / orbit_mainnet without code
    ///      changes — operator just sets the env var.
    function _resolvePhaseManager() internal view returns (address) {
        // 1. Env-var override has top priority.
        address envOverride = vm.envOr("PHASE_MANAGER", address(0));
        if (envOverride != address(0)) {
            return envOverride;
        }

        // 2. Fall back to the deployment manifest.
        string memory chainName = vm.envOr("CHAIN_NAME", string("anvil"));
        string memory path = string.concat(
            "script/deployments/sprint_3/",
            chainName,
            ".json"
        );

        // `vm.readFile` reverts with a forge-std error if the path is
        // missing; we surface a domain-specific error instead so the
        // operator sees a meaningful message.
        try vm.readFile(path) returns (string memory raw) {
            // `vm.parseJsonAddress` reverts on malformed input; that's
            // an operator error we want to bubble up unchanged.
            address fromJson = vm.parseJsonAddress(raw, ".contracts.PhaseManager");
            if (fromJson == address(0)) {
                revert DeploymentNotResolved(chainName);
            }
            return fromJson;
        } catch {
            revert DeploymentNotResolved(chainName);
        }
    }
}
