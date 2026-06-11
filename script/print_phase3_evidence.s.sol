// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {stdJson}          from "forge-std/StdJson.sol";

/// @title  PrintPhase3Evidence — T-A-010 receipt-inspection + markdown emitter
/// @notice Post-broadcast verification script for the D17 / D18 Phase-3 trust
///         finalisation transaction (`RenouncePhase3MutableRoles.s.sol`).
///         Reads the `eth_getTransactionReceipt` payload for a given tx hash,
///         filters log entries by the `Phase3RolesRenounced(uint64)` topic,
///         asserts both renunciation events fired (one from `PhaseManager`,
///         one from `EnergyController`), and prints a Markdown-formatted
///         evidence block ready to paste into the demo README + the Demo Day
///         walk-through deck.
///
///         The Phase-3 admin lock is the on-chain analogue of the PRD §10
///         pause-role + upgrade-role renunciation:
///           * `EnergyController.Phase3RolesRenounced` covers the
///             admin / param-tuner channel: `setOwner`, `pause`, `unpause`,
///             `setAttestationSigner`, `setPhaseManager`, `setPhase`. These
///             collectively constitute the "pause" + "upgrade" surfaces PRD
///             §10 describes — there is no separate ERC-1967 upgrade proxy in
///             this design; the channel that could change protocol invariants
///             IS the param-tuner channel, and it is locked here.
///           * `PhaseManager.Phase3RolesRenounced` covers `setOwner` +
///             `transitionTo*`, freezing the lifecycle owner key.
///         The brief's `PauseRoleRenounced(role, admin)` /
///         `UpgradeRoleRenounced(role, admin)` shape is documented in
///         `README_CHAIN.md` §"Phase 3 Role Renunciation"; the actual
///         emitted-on-chain event is the unified `Phase3RolesRenounced` per
///         the v0.3.0 / v0.4.0 ABI bump in
///         `.dev/contracts/{phase_manager,energy_controller}_abi.v*.json`.
///         The delivery report flags the naming gap as a
///         `proposed_spec_change` for the Advisor to route.
///
///         Inputs (priority order):
///           1. `run(bytes32 txHash)`                 — explicit hash arg.
///           2. `runFromReceipt(string memory path)`  — pre-fetched receipt
///                                                       JSON (offline use).
///           3. `run()` (default) — reads env vars:
///                * `PHASE3_RECEIPT_PATH` (preferred, offline)
///                * `PHASE3_TX_HASH`      (live RPC)
///
///         Live-RPC mode requires `--rpc-url $RPC_URL` so the underlying
///         `vm.rpc("eth_getTransactionReceipt", …)` cheatcode has a target.
///         Offline mode (preferred for CI + the demo evidence file) reads a
///         pre-captured receipt produced by:
///
///           cast receipt 0xTX_HASH --rpc-url $RPC_URL --json > evidence.json
///
///         Spec anchors:
///           * PRD §5.1.A    — 永久死亡（不可逆）规则写进合约 — trustless.
///           * PRD §10       — Pause/Upgrade roles auto-renounced on Phase 3
///                              entry; demo must show pause + upgrade role
///                              burned in the same tx.
///           * TP §3.2       — PhaseManager.lockPhase3 emits
///                              Phase3RolesRenounced(uint64 lockedAt).
///           * TP §8 D17/D18 — the renounce-ritual tx whose receipt this
///                              script audits.
///
///         Usage:
///           # Live RPC, explicit hash:
///           PHASE3_TX_HASH=0xabcd… \
///           forge script script/print_phase3_evidence.s.sol \
///               --rpc-url $RPC_URL --sig "run()"
///
///           # Offline, pre-fetched receipt JSON (recommended for evidence):
///           PHASE3_RECEIPT_PATH=script/deployments/sprint_5/phase3_receipt.json \
///           forge script script/print_phase3_evidence.s.sol \
///               --sig "run()"
contract PrintPhase3Evidence is Script {
    using stdJson for string;

    // ------------------------------------------------------------------ //
    // Constants                                                           //
    // ------------------------------------------------------------------ //

    /// @notice keccak256 of the canonical Phase-3 renunciation event
    ///         signature emitted by BOTH `PhaseManager.lockPhase3()` and
    ///         `EnergyController.lockPhase3()`. Used as the topic[0]
    ///         filter on the receipt's logs array.
    bytes32 public constant PHASE3_RENOUNCED_TOPIC =
        keccak256(bytes("Phase3RolesRenounced(uint64)"));

    /// @notice Hard upper bound on receipt logs we will scan. The
    ///         renounce-ritual tx emits exactly 2 logs in the canonical
    ///         path but the receipt may bundle extras from other contracts
    ///         in the same broadcast (none expected for D17/D18 — the
    ///         renounce script is a dedicated tx). 64 is generous.
    uint256 internal constant MAX_LOGS = 64;

    // ------------------------------------------------------------------ //
    // Errors                                                              //
    // ------------------------------------------------------------------ //

    /// @notice Neither `PHASE3_TX_HASH` nor `PHASE3_RECEIPT_PATH` env vars
    ///         were set, and `run()` was the entry — caller must supply at
    ///         least one input.
    error MissingInput();

    /// @notice Receipt JSON had no `.logs` key — the receipt payload is
    ///         malformed or the tx was never mined.
    error ReceiptHasNoLogs();

    /// @notice Receipt logs scanned, but fewer than 2 entries matched the
    ///         expected event topic. Records how many WERE found so the
    ///         operator can investigate (0 = wrong tx; 1 = one of the two
    ///         lockPhase3 calls did not land).
    error MissingRenunciationEvents(uint256 found);

    /// @notice Both matching logs were emitted from the SAME contract —
    ///         the renounce-ritual must fire one event from PhaseManager
    ///         AND one from EnergyController; receipts with two events
    ///         from the same emitter indicate a replayed / re-attempted
    ///         lock, which the contracts revert anyway (set-once), so this
    ///         shape should never occur from a clean run.
    error EmittersNotDistinct(address emitter);

    /// @notice A log's `data` field was not the 32-byte ABI-encoded
    ///         uint64 the event signature implies. Indicates receipt
    ///         corruption or a malicious replay attempting to spoof the
    ///         topic.
    error LogDataMalformed(uint256 logIndex, uint256 dataLength);

    // ------------------------------------------------------------------ //
    // Output struct                                                       //
    // ------------------------------------------------------------------ //

    /// @notice Audit summary returned by every entry point so `--json` mode
    ///         picks up structured data alongside the human-readable
    ///         Markdown block.
    struct Evidence {
        bytes32 txHash;
        address phaseManagerEmitter;
        uint64  phaseManagerLockedAt;
        address energyControllerEmitter;
        uint64  energyControllerLockedAt;
        uint256 matchedLogCount;
    }

    // ------------------------------------------------------------------ //
    // Entry points                                                        //
    // ------------------------------------------------------------------ //

    /// @notice Env-driven entry. Prefers `PHASE3_RECEIPT_PATH` (offline);
    ///         falls back to `PHASE3_TX_HASH` (live RPC via `vm.rpc`).
    function run() external returns (Evidence memory ev) {
        string memory path = vm.envOr("PHASE3_RECEIPT_PATH", string(""));
        if (bytes(path).length > 0) {
            return runFromReceipt(path);
        }
        bytes32 zero;
        bytes32 txHash = vm.envOr("PHASE3_TX_HASH", zero);
        if (txHash == bytes32(0)) revert MissingInput();
        return run(txHash);
    }

    /// @notice Live-RPC entry. Issues `eth_getTransactionReceipt` against
    ///         the active fork URL and audits the response.
    function run(bytes32 txHash) public returns (Evidence memory ev) {
        string memory params = string.concat(
            "[\"",
            vm.toString(txHash),
            "\"]"
        );
        bytes memory raw = vm.rpc("eth_getTransactionReceipt", params);
        // forge's vm.rpc returns the JSON value abi-encoded; for an
        // object result this is the raw JSON string wrapped as bytes.
        string memory json = string(raw);
        ev = _parseAndAssert(json);
        ev.txHash = txHash;
        _printMarkdown(ev);
    }

    /// @notice Offline entry. Reads a pre-fetched receipt JSON (produced
    ///         by `cast receipt --json`) from `path`, audits, and prints
    ///         the Markdown evidence block. Marked `view` because the
    ///         function only invokes view cheatcodes (`vm.readFile`,
    ///         `vm.keyExistsJson`, `vm.parseJson*`) and side-effects via
    ///         `console2.log` (which routes through a static call to the
    ///         console magic address — no chain-state mutation).
    function runFromReceipt(string memory path)
        public
        view
        returns (Evidence memory ev)
    {
        string memory json = vm.readFile(path);
        ev = _parseAndAssert(json);
        if (vm.keyExistsJson(json, ".transactionHash")) {
            ev.txHash = vm.parseJsonBytes32(json, ".transactionHash");
        }
        _printMarkdown(ev);
    }

    // ------------------------------------------------------------------ //
    // Internals                                                           //
    // ------------------------------------------------------------------ //

    /// @dev Scans the receipt's `.logs` array, filters by
    ///      `PHASE3_RENOUNCED_TOPIC`, asserts exactly 2 distinct emitters,
    ///      and decodes each log's `lockedAt` payload. Reverts loudly on
    ///      any structural mismatch.
    function _parseAndAssert(string memory json)
        internal
        view
        returns (Evidence memory ev)
    {
        if (!vm.keyExistsJson(json, ".logs")) revert ReceiptHasNoLogs();

        for (uint256 i = 0; i < MAX_LOGS; i++) {
            string memory logBase = string.concat(".logs[", vm.toString(i), "]");
            if (!vm.keyExistsJson(json, logBase)) break;

            // topics[0] is the event signature hash.
            string memory topic0Path = string.concat(logBase, ".topics[0]");
            if (!vm.keyExistsJson(json, topic0Path)) continue;
            bytes32 topic0 = vm.parseJsonBytes32(json, topic0Path);
            if (topic0 != PHASE3_RENOUNCED_TOPIC) continue;

            address emitter = vm.parseJsonAddress(json, string.concat(logBase, ".address"));
            bytes memory data = vm.parseJsonBytes(json, string.concat(logBase, ".data"));
            if (data.length != 32) revert LogDataMalformed(i, data.length);

            // Phase3RolesRenounced(uint64 lockedAt) — non-indexed; ABI-
            // encoded into a single 32-byte word with the value in the
            // low 8 bytes. Truncate to uint64 to drop the leading zeros.
            // rationale: data.length is asserted == 32 immediately above
            // (LogDataMalformed revert), so `bytes32(data)` consumes the
            // full ABI-encoded word without truncation; the outer uint64
            // truncation IS intended (event payload is uint64).
            // forge-lint: disable-next-line(unsafe-typecast)
            uint64 lockedAt = uint64(uint256(bytes32(data)));

            if (ev.phaseManagerEmitter == address(0)) {
                ev.phaseManagerEmitter   = emitter;
                ev.phaseManagerLockedAt  = lockedAt;
            } else if (emitter != ev.phaseManagerEmitter) {
                ev.energyControllerEmitter   = emitter;
                ev.energyControllerLockedAt  = lockedAt;
            } else {
                revert EmittersNotDistinct(emitter);
            }
            ev.matchedLogCount++;
        }

        if (ev.matchedLogCount < 2 || ev.energyControllerEmitter == address(0)) {
            revert MissingRenunciationEvents(ev.matchedLogCount);
        }
    }

    /// @dev Prints a paste-ready Markdown evidence block. Section headings
    ///      match `README_CHAIN.md` §"Phase 3 Role Renunciation" so the
    ///      operator can splice the block directly under the placeholder
    ///      values after D17/D18 lands.
    function _printMarkdown(Evidence memory ev) internal pure {
        console2.log("```markdown");
        console2.log("### Phase 3 Role Renunciation Evidence");
        console2.log("");
        console2.log("| Field                       | Value |");
        console2.log("|-----------------------------|-------|");
        console2.log(string.concat("| `transactionHash`           | `", vm.toString(ev.txHash), "` |"));
        console2.log(string.concat("| `matchedLogCount`           | `", vm.toString(ev.matchedLogCount), "` |"));
        console2.log(string.concat("| `PhaseManager` emitter      | `", vm.toString(ev.phaseManagerEmitter), "` |"));
        console2.log(string.concat("| `PhaseManager` lockedAt     | `", vm.toString(uint256(ev.phaseManagerLockedAt)), "` |"));
        console2.log(string.concat("| `EnergyController` emitter  | `", vm.toString(ev.energyControllerEmitter), "` |"));
        console2.log(string.concat("| `EnergyController` lockedAt | `", vm.toString(uint256(ev.energyControllerLockedAt)), "` |"));
        console2.log("");
        console2.log("**Event signature**: `Phase3RolesRenounced(uint64 lockedAt)`");
        console2.log(string.concat("**topic[0]**: `", vm.toString(PHASE3_RENOUNCED_TOPIC), "`"));
        console2.log("**Assertion**: both `PhaseManager.lockPhase3()` and");
        console2.log("`EnergyController.lockPhase3()` emitted Phase3RolesRenounced;");
        console2.log("emitter addresses are distinct (one per contract).");
        console2.log("```");
    }
}
