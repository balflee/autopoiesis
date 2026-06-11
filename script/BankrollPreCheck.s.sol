// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2}  from "forge-std/Script.sol";
import {PhaseManager}      from "contracts/PhaseManager.sol";

/// @dev Minimal IERC20 view interface — only the single read the script
///      needs (balanceOf). Coupling narrowly keeps the script independent
///      of any production token wrapper.
interface IERC20BalanceView {
    function balanceOf(address account) external view returns (uint256);
}

/// @title  BankrollPreCheck — D17 dress-rehearsal Polygon-side gate
/// @notice Polygon-side gate that the operator runs BEFORE
///         `AdvanceToAdulthood` on the D17 dress rehearsal and the D18
///         cutover. The Agent operates BREATH on the L3 (placeholder
///         8,000) and USDC `bankroll` on Polygon (placeholder $50 per
///         PRD §6.1). Phase 3 turns the bankroll into 真金 — actual
///         risk capital. Launching Phase 3 with an underfunded bankroll
///         would invalidate the demo invariant; this script refuses to
///         proceed in that case.
///
///         The script:
///           1. Forks to Polygon (`POLYGON_RPC`) and reads
///              `USDC.balanceOf(AGENT_EOA)`.
///           2. Reverts `BankrollUnderfunded` if balance < $50 (50e6 USDC).
///           3. Forks to the L3 (`L3_RPC`) and reads `PhaseManager.phase()`.
///           4. Reverts `PhaseNotApprenticeship` if the L3 has already
///              advanced — this is a PRE-launch check.
///
///         **Dry-run is the default.** No tx is broadcast under any
///         circumstance — this script is read-only by design — but the
///         WRITE_BROADCAST flag is still honoured so the operator's
///         muscle memory across D17/D18 is consistent.
///
///         Env vars:
///           POLYGON_RPC      string  REQUIRED — Polygon Amoy / mainnet RPC.
///           L3_RPC           string  REQUIRED — Genesis L3 RPC.
///           POLYGON_USDC     address REQUIRED — USDC token address on
///                                   Polygon Amoy / mainnet.
///           AGENT_EOA        address REQUIRED — Agent EOA holding the
///                                   bankroll.
///           PHASE_MANAGER    address optional — overrides sprint_4 JSON
///                                   manifest for the L3-side address.
///           BANKROLL_MIN     uint256 optional — overrides the $50 default
///                                   (50e6 in USDC 6-decimals).
///
///         Spec anchors:
///           * PRD §6.1      — dual-account architecture: BREATH on L3,
///                              USDC bankroll on Polygon ($50 placeholder).
///           * PRD §6.13     — Phase 3 row "USDC bankroll 真金" — bankroll
///                              moves from shadow to real money.
///           * TP §7         — three-chain parallel: RH Chain + Sepolia +
///                              Polygon Amoy; USDC on Polygon.
///           * TP §8 D17     — Phase 3 dress rehearsal includes the
///                              bankroll pre-check.
contract BankrollPreCheck is Script {
    /// @notice Default minimum USDC bankroll for Phase 3 — $50 per PRD §6.1.
    uint256 public constant DEFAULT_BANKROLL_MIN_USDC = 50e6; // 50.000000 USDC

    /// @notice Bankroll is below the configured minimum. Phase 3 launch is
    ///         REFUSED — top up the bankroll first.
    error BankrollUnderfunded(uint256 actual, uint256 required);

    /// @notice The L3 PhaseManager is not in `Apprenticeship`. Either the
    ///         launch already happened (advance to a NEW deployment) or
    ///         the operator wired up an inconsistent manifest.
    error PhaseNotApprenticeship(uint8 actual);

    /// @notice One of the REQUIRED env vars is unset.
    error MissingEnv(string name);

    /// @notice Manifest lookup failed for the L3 contract address.
    error DeploymentNotResolved(string chainName);

    /// @notice Returned by `run()` so D17 audit log captures the pre-launch
    ///         numbers. Always non-broadcasting.
    struct PreCheckResult {
        address polygonUsdc;
        address agentEoa;
        uint256 bankrollObserved;
        uint256 bankrollRequired;
        uint8   l3Phase;
        uint64  ranAt;
    }

    function run() external returns (PreCheckResult memory result) {
        // 1. Resolve env vars (all REQUIRED except PHASE_MANAGER override).
        string memory polygonRpc = _envStringRequired("POLYGON_RPC");
        string memory l3Rpc      = _envStringRequired("L3_RPC");
        address polygonUsdc      = _envAddressRequired("POLYGON_USDC");
        address agentEoa         = _envAddressRequired("AGENT_EOA");
        uint256 bankrollMin      = vm.envOr("BANKROLL_MIN", DEFAULT_BANKROLL_MIN_USDC);

        console2.log("BankrollPreCheck.run() -- TP D17 Polygon-side gate");
        console2.log("  POLYGON_USDC:", polygonUsdc);
        console2.log("  AGENT_EOA   :", agentEoa);
        console2.log("  required    :", bankrollMin);

        // 2. Fork to Polygon, read USDC balance.
        vm.createSelectFork(polygonRpc);
        uint256 bankroll = IERC20BalanceView(polygonUsdc).balanceOf(agentEoa);
        console2.log("  observed    :", bankroll);
        if (bankroll < bankrollMin) {
            revert BankrollUnderfunded(bankroll, bankrollMin);
        }

        // 3. Fork to L3, read PhaseManager.currentPhase().
        vm.createSelectFork(l3Rpc);
        address phaseManagerAddr = _resolvePhaseManager();
        PhaseManager pm = PhaseManager(phaseManagerAddr);
        PhaseManager.Phase l3Phase = pm.currentPhase();
        console2.log("  L3 phase    :", uint256(uint8(l3Phase)), "(expect 1 = Apprenticeship)");
        if (l3Phase != PhaseManager.Phase.Apprenticeship) {
            revert PhaseNotApprenticeship(uint8(l3Phase));
        }

        // 4. Smoke log + return.
        bool broadcast = vm.envOr("WRITE_BROADCAST", false);
        console2.log("  broadcast   :", broadcast, "(IGNORED -- read-only script)");
        console2.log("BankrollPreCheck.run() OK -- Phase 3 launch may proceed.");

        result = PreCheckResult({
            polygonUsdc:      polygonUsdc,
            agentEoa:         agentEoa,
            bankrollObserved: bankroll,
            bankrollRequired: bankrollMin,
            l3Phase:          uint8(l3Phase),
            // forge-lint: disable-next-line(block-timestamp)
            ranAt:            uint64(block.timestamp)
        });
    }

    /// @dev PhaseManager resolution. PHASE_MANAGER env var first, then
    ///      sprint_4 JSON manifest. Same pattern as AdvanceToAdulthood.
    function _resolvePhaseManager() internal view returns (address) {
        address envOverride = vm.envOr("PHASE_MANAGER", address(0));
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
            address fromJson = vm.parseJsonAddress(raw, ".contracts.PhaseManager");
            if (fromJson == address(0)) {
                revert DeploymentNotResolved(chainName);
            }
            return fromJson;
        } catch {
            revert DeploymentNotResolved(chainName);
        }
    }

    /// @dev REQUIRED string env-var loader. Empty string ⇒ MissingEnv revert.
    function _envStringRequired(string memory name) internal view returns (string memory) {
        string memory v = vm.envOr(name, string(""));
        if (bytes(v).length == 0) revert MissingEnv(name);
        return v;
    }

    /// @dev REQUIRED address env-var loader. Zero ⇒ MissingEnv revert.
    function _envAddressRequired(string memory name) internal view returns (address) {
        address v = vm.envOr(name, address(0));
        if (v == address(0)) revert MissingEnv(name);
        return v;
    }
}
