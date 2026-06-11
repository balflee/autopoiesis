// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script} from "forge-std/Script.sol";
import {EnergyController} from "contracts/EnergyController.sol";

/// @title  DeployEnergyController — sprint_2 v3.1 deployment
/// @notice Deploys EnergyController, then runs `initialize` with the genesis
///         BREATH amount, the soft cap, and the off-chain attestation signer
///         address. Env-driven so CI runs against deterministic numbers
///         without hard-coding them into the script.
///
///         Env vars:
///           INITIAL_BREATH        uint256, 1e6 precision. Default 10_000e6.
///           MAX_BREATH            uint256, soft cap.       Default 12_000e6.
///           ATTESTATION_SIGNER    address (no leading 0x). REQUIRED — fails
///                                 fast if unset so we never ship with a
///                                 zero signer.
contract DeployEnergyController is Script {
    function run() external returns (EnergyController ec) {
        uint256 initialBreath   = vm.envOr("INITIAL_BREATH", uint256(10_000e6));
        uint256 maxBreath       = vm.envOr("MAX_BREATH",     uint256(12_000e6));
        address attestationSigner = vm.envAddress("ATTESTATION_SIGNER");

        vm.startBroadcast();
        ec = new EnergyController();
        ec.initialize(initialBreath, maxBreath, attestationSigner);
        vm.stopBroadcast();
    }
}
