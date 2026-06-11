// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script}           from "forge-std/Script.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {PhaseManager}     from "contracts/PhaseManager.sol";
import {AgentLifecycle}   from "contracts/AgentLifecycle.sol";
import {DecisionLog}      from "contracts/DecisionLog.sol";
import {TombstoneNFT}     from "contracts/TombstoneNFT.sol";

/// @title  DeployAll — T-A-004 orchestrated full-stack deployment
/// @notice Deploys all five Genesis Experiment contracts in the
///         canonical chicken-and-egg order and wires the AgentLifecycle
///         back-pointers (`setDecisionLog`, `setTombstoneNFT`) inside the
///         same broadcast so the chain layer is fully self-consistent at
///         the end of `run()`.
///
///         Deployment order (locked by immutable references):
///           1. EnergyController        (no peer dependencies)
///           2. EnergyController.initialize(initialBreath, maxBreath, signer)
///           3. PhaseManager            (no peer dependencies)
///           4. AgentLifecycle          (needs EC address)
///           5. DecisionLog             (needs AgentLifecycle address)
///           6. TombstoneNFT            (needs AgentLifecycle address)
///           7. AgentLifecycle.setDecisionLog   (one-shot)
///           8. AgentLifecycle.setTombstoneNFT  (one-shot)
///
///         Env vars (all optional except ATTESTATION_SIGNER):
///           INITIAL_BREATH       uint256 (default 10_000e6)
///           MAX_BREATH           uint256 (default 12_000e6)
///           ATTESTATION_SIGNER   address REQUIRED
///           TOMBSTONE_NAME       string  (default "Genesis Tombstone")
///           TOMBSTONE_SYMBOL     string  (default "GTOMB")
///         (TOMBSTONE_BASE_URI dropped in T-A-007 — tokenURI is now
///          rendered fully on-chain per PRD §5.1.C.)
contract DeployAll is Script {
    struct Deployment {
        EnergyController energyController;
        PhaseManager     phaseManager;
        AgentLifecycle   agentLifecycle;
        DecisionLog      decisionLog;
        TombstoneNFT     tombstoneNFT;
    }

    function run() external returns (Deployment memory d) {
        uint256 initialBreath   = vm.envOr("INITIAL_BREATH", uint256(10_000e6));
        uint256 maxBreath       = vm.envOr("MAX_BREATH",     uint256(12_000e6));
        address attestationSigner = vm.envAddress("ATTESTATION_SIGNER");

        string memory tName   = vm.envOr("TOMBSTONE_NAME",   string("Genesis Tombstone"));
        string memory tSymbol = vm.envOr("TOMBSTONE_SYMBOL", string("GTOMB"));

        vm.startBroadcast();

        // 1. EnergyController
        d.energyController = new EnergyController();
        d.energyController.initialize(initialBreath, maxBreath, attestationSigner);

        // 2. PhaseManager
        d.phaseManager = new PhaseManager();

        // 3. AgentLifecycle — captures EC reference as immutable.
        d.agentLifecycle = new AgentLifecycle(address(d.energyController));

        // 4. DecisionLog — captures AgentLifecycle reference as immutable.
        d.decisionLog = new DecisionLog(address(d.agentLifecycle));

        // 5. TombstoneNFT — captures AgentLifecycle reference as immutable.
        //    tokenURI is rendered fully on-chain (T-A-007); no baseURI arg.
        d.tombstoneNFT = new TombstoneNFT(tName, tSymbol, address(d.agentLifecycle));

        // 6. Wire AgentLifecycle back-pointers (one-shot each).
        d.agentLifecycle.setDecisionLog(address(d.decisionLog));
        d.agentLifecycle.setTombstoneNFT(address(d.tombstoneNFT));

        vm.stopBroadcast();
    }
}
