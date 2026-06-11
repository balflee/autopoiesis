// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test}                from "forge-std/Test.sol";
import {PrintPhase3Evidence} from "script/print_phase3_evidence.s.sol";

/// @title  PrintPhase3EvidenceTest — T-A-010 round 1
/// @notice Exercises the receipt-inspection logic of
///         `script/print_phase3_evidence.s.sol` against committed fixtures
///         under `test/fixtures/phase3_receipt.*.json`. The script's
///         live-RPC path (`run(bytes32)`) is unit-tested implicitly via
///         the offline path because both branches converge on the same
///         private `_parseAndAssert` routine — exercising the JSON
///         decoder is what matters.
///
///         Fixtures committed alongside this test:
///           * phase3_receipt.happy.json        — three logs: one noise
///                                                event, then two valid
///                                                Phase3RolesRenounced
///                                                emissions from distinct
///                                                addresses. The script
///                                                must return cleanly.
///           * phase3_receipt.missing.json      — only ONE matching event;
///                                                MissingRenunciationEvents
///                                                must fire.
///           * phase3_receipt.same_emitter.json — two matching events but
///                                                from the SAME address;
///                                                EmittersNotDistinct
///                                                must fire.
///
///         Topic hash used in fixtures:
///           keccak256("Phase3RolesRenounced(uint64)")
///         = 0x077aa5fbb23c1e9b5421146609deca59f7c51b7fa3c342ce9ba2c14260ff9b5e
///         (cross-checked via `cast keccak`).
contract PrintPhase3EvidenceTest is Test {
    PrintPhase3Evidence internal script;

    string internal constant HAPPY_PATH =
        "test/fixtures/phase3_receipt.happy.json";
    string internal constant MISSING_PATH =
        "test/fixtures/phase3_receipt.missing.json";
    string internal constant SAME_EMITTER_PATH =
        "test/fixtures/phase3_receipt.same_emitter.json";

    bytes32 internal constant EXPECTED_TOPIC =
        0x077aa5fbb23c1e9b5421146609deca59f7c51b7fa3c342ce9ba2c14260ff9b5e;

    function setUp() public {
        script = new PrintPhase3Evidence();
    }

    // ------------------------------------------------------------------ //
    // Pin: the on-chain topic hash MUST match the constant used by the   //
    // script. If `Phase3RolesRenounced` is ever renamed or its param     //
    // type changes, this test breaks loudly — flagging the README +      //
    // delivery fixtures need a coordinated bump.                          //
    // ------------------------------------------------------------------ //

    function test_TopicConstantMatchesEventSignature() public view {
        assertEq(
            script.PHASE3_RENOUNCED_TOPIC(),
            EXPECTED_TOPIC,
            "PHASE3_RENOUNCED_TOPIC drift - re-run cast keccak"
        );
    }

    // ------------------------------------------------------------------ //
    // Happy path                                                          //
    // ------------------------------------------------------------------ //

    function test_HappyPath_DistinctEmittersAndDecodedTimestamps() public view {
        PrintPhase3Evidence.Evidence memory ev = script.runFromReceipt(HAPPY_PATH);

        assertEq(ev.matchedLogCount, 2, "should have matched exactly 2 logs");

        // Emitter addresses match fixture order (EnergyController-like
        // first, then PhaseManager-like — the script does not enforce
        // ordering by name; it just records first-seen as PM-slot,
        // second-distinct as EC-slot).
        assertEq(
            ev.phaseManagerEmitter,
            address(0xbeeF),
            "PM emitter slot should hold first-seen distinct address"
        );
        assertEq(
            ev.energyControllerEmitter,
            address(0xCAFE),
            "EC emitter slot should hold second-seen distinct address"
        );

        // lockedAt values decoded from data (0x68000001 / 0x68000002).
        assertEq(
            uint256(ev.phaseManagerLockedAt),
            uint256(uint64(0x68000001)),
            "PM lockedAt decode wrong"
        );
        assertEq(
            uint256(ev.energyControllerLockedAt),
            uint256(uint64(0x68000002)),
            "EC lockedAt decode wrong"
        );

        // transactionHash carried through from the receipt JSON.
        assertEq(
            ev.txHash,
            bytes32(0xa1a2a3a4a5a6a7a8a9a0a1a2a3a4a5a6a7a8a9a0a1a2a3a4a5a6a7a8a9a0a1a2),
            "txHash not carried through from receipt"
        );
    }

    // ------------------------------------------------------------------ //
    // Negative path 1 — only one matching event                          //
    // ------------------------------------------------------------------ //

    function test_RevertWhen_OnlyOneRenunciationEvent() public {
        vm.expectRevert(
            abi.encodeWithSelector(
                PrintPhase3Evidence.MissingRenunciationEvents.selector,
                uint256(1)
            )
        );
        script.runFromReceipt(MISSING_PATH);
    }

    // ------------------------------------------------------------------ //
    // Negative path 2 — both events from the same emitter                //
    // ------------------------------------------------------------------ //

    function test_RevertWhen_EmittersNotDistinct() public {
        vm.expectRevert(
            abi.encodeWithSelector(
                PrintPhase3Evidence.EmittersNotDistinct.selector,
                address(0xbeeF)
            )
        );
        script.runFromReceipt(SAME_EMITTER_PATH);
    }
}
