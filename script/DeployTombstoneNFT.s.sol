// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TombstoneNFT} from "contracts/TombstoneNFT.sol";

/// @title  DeployTombstoneNFT v0.2.0 — sprint_5 D15 (T-A-007)
/// @notice Deploys the TombstoneNFT alone against a pre-deployed
///         AgentLifecycle. Useful when re-deploying only the NFT against
///         a long-lived L3 lifecycle (the immutable `agentLifecycle`
///         binding precludes upgrading in place).
///
///         **Safety**: defaults to DRY-RUN. Pass `WRITE_BROADCAST=true`
///         to actually broadcast the deployment tx. Without that env var
///         the script computes the deployment but never calls
///         `vm.startBroadcast`, so an accidental `forge script ... --rpc-url
///         $LIVE` invocation cannot land an unwanted tx.
///
///         Env vars:
///           AGENT_LIFECYCLE       address (REQUIRED) — the mint authority
///           TOMBSTONE_NAME        string  — ERC-721 collection name
///                                          (default "Genesis Tombstone")
///           TOMBSTONE_SYMBOL      string  — ERC-721 symbol
///                                          (default "GTOMB")
///           WRITE_BROADCAST       string  — "true" to broadcast, anything
///                                          else (or unset) ⇒ dry-run only.
///
///         Note: v0.2.0 dropped TOMBSTONE_BASE_URI — `tokenURI` is now
///         rendered fully on-chain (PRD §5.1.C). The arg is silently
///         ignored if set.
contract DeployTombstoneNFT is Script {
    /// @notice Sentinel returned in dry-run mode when the contract is NOT
    ///         actually deployed. Off-chain wrappers MUST check for zero
    ///         before treating the return value as live.
    address public constant DRY_RUN_SENTINEL = address(0);

    function run() external returns (TombstoneNFT tnft) {
        address agentLifecycle = vm.envAddress("AGENT_LIFECYCLE");
        string memory name_   = vm.envOr("TOMBSTONE_NAME",   string("Genesis Tombstone"));
        string memory symbol_ = vm.envOr("TOMBSTONE_SYMBOL", string("GTOMB"));
        bool broadcast = _shouldBroadcast();

        if (!broadcast) {
            console2.log("[DeployTombstoneNFT] DRY-RUN (set WRITE_BROADCAST=true to broadcast)");
            console2.log("  agentLifecycle =", agentLifecycle);
            console2.log("  name           =", name_);
            console2.log("  symbol         =", symbol_);
            // Return the zero address sentinel — DO NOT deploy.
            return TombstoneNFT(DRY_RUN_SENTINEL);
        }

        vm.startBroadcast();
        tnft = new TombstoneNFT(name_, symbol_, agentLifecycle);
        vm.stopBroadcast();

        console2.log("[DeployTombstoneNFT] deployed at", address(tnft));
    }

    /// @dev `WRITE_BROADCAST=true` (case-insensitive) ⇒ broadcast. Anything
    ///      else (including unset, "false", "0", "no") ⇒ dry-run.
    function _shouldBroadcast() private view returns (bool) {
        string memory raw = vm.envOr("WRITE_BROADCAST", string(""));
        bytes memory b = bytes(raw);
        if (b.length == 4) {
            // ASCII "true" / "True" / "TRUE"
            bool isTrue = (b[0] == 0x74 || b[0] == 0x54)
                       && (b[1] == 0x72 || b[1] == 0x52)
                       && (b[2] == 0x75 || b[2] == 0x55)
                       && (b[3] == 0x65 || b[3] == 0x45);
            return isTrue;
        }
        return false;
    }
}
