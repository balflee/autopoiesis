// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, stdJson}       from "forge-std/Test.sol";
import {DeployCalibrated}    from "script/DeployCalibrated.s.sol";
import {EnergyController}    from "contracts/EnergyController.sol";
import {PhaseManager}        from "contracts/PhaseManager.sol";
import {AgentLifecycle}      from "contracts/AgentLifecycle.sol";
import {DecisionLog}         from "contracts/DecisionLog.sol";
import {TombstoneNFT}        from "contracts/TombstoneNFT.sol";
import {CalibratedConstants} from "contracts/lib/CalibratedConstants.sol";

/// @title  DeployCalibratedTest — T-A-005 round 1
/// @notice Validates that `script/DeployCalibrated.s.sol` correctly
///         reads a `selected_params.json` payload and produces an
///         on-chain bundle whose state matches the calibration row
///         bit-for-bit (after the documented 1e6 BREATH_SCALE
///         transform). The brief enumerates four required tests; this
///         file covers them with the same `runWithParamsPath`
///         overload the production deploy uses.
///
///         All tests use the local `test/fixtures/selected_params.test.json`
///         fixture so this suite is independent of Track C's actual
///         `reports/calibration/selected_params.json` deliverable (which
///         lands via T-C-003 and may not be present in every CI run).
contract DeployCalibratedTest is Test {
    using stdJson for string;

    DeployCalibrated internal deployer;

    string internal constant TEST_PARAMS_PATH = "test/fixtures/selected_params.test.json";

    address internal constant ATTESTATION_SIGNER = address(0xBEEF);

    function setUp() public {
        deployer = new DeployCalibrated();
    }

    // -----------------------------------------------------------------------
    // Test 1 — happy path: deploy succeeds + every contract is wired
    // -----------------------------------------------------------------------

    /// @notice End-to-end deploy: read selected_params, deploy all 5
    ///         contracts, verify the AgentLifecycle back-pointers landed.
    function test_DeployCalibratedHappyPath() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        // The 5 addresses are all non-zero.
        assertTrue(address(d.energyController) != address(0), "EnergyController unset");
        assertTrue(address(d.phaseManager)     != address(0), "PhaseManager unset");
        assertTrue(address(d.agentLifecycle)   != address(0), "AgentLifecycle unset");
        assertTrue(address(d.decisionLog)      != address(0), "DecisionLog unset");
        assertTrue(address(d.tombstoneNFT)     != address(0), "TombstoneNFT unset");

        // EnergyController is initialised.
        assertTrue(d.energyController.initialized(), "EC not initialised");
        assertEq(d.energyController.attestationSigner(), ATTESTATION_SIGNER, "signer mismatch");

        // AgentLifecycle has both back-pointers wired (one-shot).
        assertTrue(d.agentLifecycle.decisionLogSet(), "DecisionLog not wired");
        assertTrue(d.agentLifecycle.tombstoneNFTSet(), "TombstoneNFT not wired");
        assertEq(address(d.agentLifecycle.decisionLog()), address(d.decisionLog), "DL pointer wrong");
        assertEq(address(d.agentLifecycle.tombstoneNFT()), address(d.tombstoneNFT), "TS pointer wrong");

        // Immutable AL pointer on the two child contracts matches.
        assertEq(d.decisionLog.agentLifecycle(),  address(d.agentLifecycle), "DL.AL wrong");
        assertEq(d.tombstoneNFT.agentLifecycle(), address(d.agentLifecycle), "TS.AL wrong");

        // selectedParamsHash is non-zero — sha256 of a non-empty file.
        assertTrue(d.selectedParamsHash != bytes32(0), "params hash zero");
    }

    // -----------------------------------------------------------------------
    // Test 2 — bit-for-bit BREATH-unit → on-chain BREATH_SCALE projection
    // -----------------------------------------------------------------------

    /// @notice After deploy, EnergyController.initialBreath() and
    ///         .maxBreath() MUST equal the JSON's INITIAL_BREATH /
    ///         SOFT_CAP_THRESHOLD scaled by BREATH_SCALE (1e6). This is
    ///         the "all deployed constants on-chain match
    ///         selected_params.json bit-for-bit" gate from the brief —
    ///         BREATH_SCALE is the deterministic conversion factor
    ///         documented in CalibratedConstants.sol.
    function test_OnChainConstantsMatchSelectedParamsAfterBreathScale() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        // Parse the same JSON the script just read and compare the
        // post-scale values against EnergyController storage.
        string memory raw = vm.readFile(TEST_PARAMS_PATH);
        uint256 jsonInitial = raw.readUint(CalibratedConstants.JSON_KEY_INITIAL_BREATH);
        uint256 jsonSoftCap = raw.readUint(CalibratedConstants.JSON_KEY_SOFT_CAP_THRESHOLD);

        assertEq(
            d.energyController.initialBreath(),
            CalibratedConstants.toOnChainBreath(jsonInitial),
            "EnergyController.initialBreath != INITIAL_BREATH * BREATH_SCALE"
        );
        assertEq(
            d.energyController.maxBreath(),
            CalibratedConstants.toOnChainBreath(jsonSoftCap),
            "EnergyController.maxBreath != SOFT_CAP_THRESHOLD * BREATH_SCALE"
        );

        // Round-trip via fromOnChainBreath is lossless.
        assertEq(
            CalibratedConstants.fromOnChainBreath(d.energyController.initialBreath()),
            jsonInitial,
            "fromOnChainBreath round-trip broken (initialBreath)"
        );
        assertEq(
            CalibratedConstants.fromOnChainBreath(d.energyController.maxBreath()),
            jsonSoftCap,
            "fromOnChainBreath round-trip broken (softCapThreshold)"
        );
    }

    // -----------------------------------------------------------------------
    // Test 3 — JSON schema validation: missing required key reverts
    // -----------------------------------------------------------------------

    /// @notice If `selected_params.json` is missing a required PRD §14.1
    ///         key, the script MUST fail at the read step (NOT silently
    ///         default to a sentinel). The stdJson cheatcode reverts on
    ///         missing keys; we just need to make sure DeployCalibrated
    ///         doesn't swallow that error.
    function test_RevertWhen_RequiredKeyMissing() public {
        // Write a tampered JSON (no INITIAL_BREATH).
        string memory tamperedPath = "test/fixtures/selected_params.broken.json";
        vm.writeFile(
            tamperedPath,
            "{\"SOFT_CAP_THRESHOLD\": 2500, \"DESPERATE_THRESHOLD\": 200, \"PASSIVE_BURN_RATE\": 1, "
            "\"E_DECISION_TAX\": 2, \"E_TIME_TAX_PER_TICK\": 1, \"CONVERSION_RATE\": 1, "
            "\"TARGET_HORIZON\": 5, \"MIN_BET_SIZE\": 5}"
        );

        // stdJson surfaces missing keys via revert; we don't pin the
        // exact selector because it lives in the cheatcode runtime, but
        // `vm.expectRevert()` with no payload catches any revert reason.
        vm.expectRevert();
        deployer.runWithParamsPath(tamperedPath, ATTESTATION_SIGNER);

        // Cleanup so subsequent tests don't see the stale fixture.
        vm.removeFile(tamperedPath);
    }

    // -----------------------------------------------------------------------
    // Test 4 — validate() catches mis-ordered cap vs initial breath
    // -----------------------------------------------------------------------

    /// @notice `CalibratedConstants.validate()` enforces
    ///         `softCapThreshold >= initialBreath`. The script invokes
    ///         this BEFORE broadcast so misconfigured calibration can't
    ///         even reach the broadcast block. We trip it by feeding a
    ///         payload where INITIAL_BREATH > SOFT_CAP_THRESHOLD.
    function test_RevertWhen_SoftCapBelowInitialBreath() public {
        string memory misorderedPath = "test/fixtures/selected_params.misordered.json";
        vm.writeFile(
            misorderedPath,
            "{\"INITIAL_BREATH\": 5000, \"SOFT_CAP_THRESHOLD\": 1000, \"DESPERATE_THRESHOLD\": 200, "
            "\"PASSIVE_BURN_RATE\": 1, \"E_DECISION_TAX\": 2, \"E_TIME_TAX_PER_TICK\": 1, "
            "\"CONVERSION_RATE\": 1, \"TARGET_HORIZON\": 5, \"MIN_BET_SIZE\": 5}"
        );

        vm.expectRevert(
            bytes("CalibratedConstants: softCapThreshold must be >= initialBreath")
        );
        deployer.runWithParamsPath(misorderedPath, ATTESTATION_SIGNER);

        vm.removeFile(misorderedPath);
    }

    // -----------------------------------------------------------------------
    // Test 5 — fuzz: selectedParamsHash is deterministic across reruns
    // -----------------------------------------------------------------------

    /// @notice The selectedParamsHash is sha256 over the JSON bytes — for
    ///         any fixed JSON file, the hash MUST be identical across
    ///         repeated `run()` calls. This is the lever off-chain
    ///         consumers use to prove a deploy targeted a specific
    ///         calibration run.
    function testFuzz_SelectedParamsHashIsDeterministic(uint8 reruns) public {
        // Bound between 2 and 6 — enough to prove determinism without
        // pessimising the fuzz budget; fuzz already runs 10000 entrypoints.
        reruns = uint8(bound(uint256(reruns), 2, 6));

        DeployCalibrated.Deployment memory first =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);
        bytes32 firstHash = first.selectedParamsHash;
        assertTrue(firstHash != bytes32(0), "first hash zero");

        for (uint8 i = 0; i < reruns; ++i) {
            DeployCalibrated d2 = new DeployCalibrated();
            DeployCalibrated.Deployment memory subsequent =
                d2.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);
            assertEq(subsequent.selectedParamsHash, firstHash, "hash drifted on re-read");
        }
    }
}
