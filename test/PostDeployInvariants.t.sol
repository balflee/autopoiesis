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

/// @title  PostDeployInvariantsTest — T-A-005 brief gate
/// @notice The brief's acceptance criterion "All deployed constants
///         on-chain match `selected_params.json` bit-for-bit" is enforced
///         here. After `DeployCalibrated.runWithParamsPath` lands a real
///         5-contract bundle, we walk EVERY on-chain constant that derived
///         from a calibrated parameter and assert the inverse round-trip
///         lands back on the JSON value byte-identical.
///
///         The current sprint_3 surface only pushes two BREATH-unit
///         parameters on-chain (INITIAL_BREATH → initialize.initialBreath_,
///         SOFT_CAP_THRESHOLD → initialize.maxBreath_); the remaining
///         seven mirror fields exist in CalibratedConstants.Params for
///         schema-fidelity but are consumed off-chain by Track B engines.
///         When sprint_4 work parameterises e.g. DESPERATE_THRESHOLD on
///         AgentLifecycle, an additional `test_*Matches*` entry here is
///         the canonical place to wire the invariant.
contract PostDeployInvariantsTest is Test {
    using stdJson for string;

    DeployCalibrated internal deployer;
    string internal constant TEST_PARAMS_PATH = "test/fixtures/selected_params.test.json";

    address internal constant ATTESTATION_SIGNER = address(0xC0FFEE);

    function setUp() public {
        deployer = new DeployCalibrated();
    }

    // -----------------------------------------------------------------------
    // Invariant 1 — EnergyController.initialBreath == INITIAL_BREATH * 1e6
    // -----------------------------------------------------------------------

    function test_InitialBreathMatchesSelectedParams() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        string memory raw = vm.readFile(TEST_PARAMS_PATH);
        uint256 jsonValue = raw.readUint(CalibratedConstants.JSON_KEY_INITIAL_BREATH);

        // Forward direction: JSON × 1e6 == storage.
        assertEq(
            d.energyController.initialBreath(),
            CalibratedConstants.toOnChainBreath(jsonValue),
            "initialBreath: storage != JSON * BREATH_SCALE"
        );

        // Inverse direction (the "bit-for-bit" gate): storage ÷ 1e6 == JSON.
        assertEq(
            CalibratedConstants.fromOnChainBreath(d.energyController.initialBreath()),
            jsonValue,
            "initialBreath: storage / BREATH_SCALE != JSON"
        );
    }

    // -----------------------------------------------------------------------
    // Invariant 2 — EnergyController.maxBreath == SOFT_CAP_THRESHOLD * 1e6
    // -----------------------------------------------------------------------

    function test_MaxBreathMatchesSelectedParams() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        string memory raw = vm.readFile(TEST_PARAMS_PATH);
        uint256 jsonValue = raw.readUint(CalibratedConstants.JSON_KEY_SOFT_CAP_THRESHOLD);

        assertEq(
            d.energyController.maxBreath(),
            CalibratedConstants.toOnChainBreath(jsonValue),
            "maxBreath: storage != JSON * BREATH_SCALE"
        );
        assertEq(
            CalibratedConstants.fromOnChainBreath(d.energyController.maxBreath()),
            jsonValue,
            "maxBreath: storage / BREATH_SCALE != JSON"
        );
    }

    // -----------------------------------------------------------------------
    // Invariant 3 — EnergyController.breath == initialBreath at genesis
    //                                          (PRD §6.0 invariant)
    // -----------------------------------------------------------------------

    /// @notice EnergyController.initialize() sets `breath = initialBreath_`
    ///         so the genesis state has the full calibrated reserve. If
    ///         this invariant ever drifts (e.g. someone introduces a
    ///         deploy-time burn), PRD §6.0 ("breath == 0 ⇒ Dead") is
    ///         violated for any calibration with INITIAL_BREATH > 0.
    function test_GenesisBreathEqualsInitialBreath() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        assertEq(
            d.energyController.breath(),
            d.energyController.initialBreath(),
            "genesis breath != initialBreath"
        );
        assertEq(uint8(d.energyController.currentPhase()), uint8(0), "genesis phase != Childhood");
    }

    // -----------------------------------------------------------------------
    // Invariant 4 — AgentLifecycle wiring matches the deployed bundle
    // -----------------------------------------------------------------------

    /// @notice Cross-contract pointers wired by `setDecisionLog` +
    ///         `setTombstoneNFT` land on the SAME addresses the script
    ///         deployed in step 4-5. A misordered wiring step would
    ///         either leave the back-pointers zero (covered by the
    ///         one-shot revert) OR point at a stale contract.
    function test_AgentLifecycleBackPointersMatchDeployedBundle() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        assertEq(
            address(d.agentLifecycle.decisionLog()),
            address(d.decisionLog),
            "AgentLifecycle.decisionLog mismatch"
        );
        assertEq(
            address(d.agentLifecycle.tombstoneNFT()),
            address(d.tombstoneNFT),
            "AgentLifecycle.tombstoneNFT mismatch"
        );

        // EC reference on AgentLifecycle is immutable; assert it points
        // at the deployed EnergyController, not at address(0) or a stale
        // instance from a different deploy.
        assertEq(
            address(d.agentLifecycle.energyController()),
            address(d.energyController),
            "AgentLifecycle.energyController mismatch"
        );

        // DecisionLog + TombstoneNFT immutable AL references point back
        // at AgentLifecycle (closes the cycle).
        assertEq(d.decisionLog.agentLifecycle(),  address(d.agentLifecycle), "DL.AL mismatch");
        assertEq(d.tombstoneNFT.agentLifecycle(), address(d.agentLifecycle), "TS.AL mismatch");
    }

    // -----------------------------------------------------------------------
    // Invariant 5 — attestation signer matches env-supplied value
    // -----------------------------------------------------------------------

    /// @notice The brief's chain-policy invariant: the off-chain
    ///         Polymarket settlement signer is the address operators
    ///         pass at deploy time (NOT a hardcoded constant). If the
    ///         script ever silently substitutes a default address, the
    ///         settlement attestation surface would accept signatures
    ///         from a key operators didn't intend.
    function test_AttestationSignerMatchesArg() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        assertEq(
            d.energyController.attestationSigner(),
            ATTESTATION_SIGNER,
            "EnergyController.attestationSigner != script arg"
        );
    }

    /// @notice The script's `_runImpl` requires a non-zero signer. Pass
    ///         `address(0)` and confirm the descriptive revert fires
    ///         BEFORE any contract is deployed.
    function test_RevertWhen_AttestationSignerZero() public {
        DeployCalibrated d2 = new DeployCalibrated();
        vm.expectRevert(
            bytes("DeployCalibrated: ATTESTATION_SIGNER must be non-zero")
        );
        d2.runWithParamsPath(TEST_PARAMS_PATH, address(0));
    }

    // -----------------------------------------------------------------------
    // Invariant 6 — selectedParamsHash is sha256(file bytes) exactly
    // -----------------------------------------------------------------------

    /// @notice `Deployment.selectedParamsHash` MUST equal
    ///         `sha256(vm.readFile(SELECTED_PARAMS_PATH))` so off-chain
    ///         consumers can re-derive the hash from the same artifact.
    function test_SelectedParamsHashMatchesFileBytes() public {
        DeployCalibrated.Deployment memory d =
            deployer.runWithParamsPath(TEST_PARAMS_PATH, ATTESTATION_SIGNER);

        bytes32 expected = sha256(bytes(vm.readFile(TEST_PARAMS_PATH)));
        assertEq(d.selectedParamsHash, expected, "selectedParamsHash != sha256(file)");
    }

    // -----------------------------------------------------------------------
    // Invariant 7 — committed sprint_3 deployment fixtures parse + match
    //               the canonical schema (script/deployments/sprint_3/*.json)
    // -----------------------------------------------------------------------

    /// @notice The three committed deploy fixtures
    ///         (`rh_chain.json`, `sepolia.json`, `polygon_amoy.json`)
    ///         MUST be parseable as the deployment-manifest schema so
    ///         downstream consumers (state_sync.py, reconciler) never
    ///         choke on a malformed template. We exercise each chain
    ///         label and assert the expected keys exist + the chain
    ///         string round-trips. Bit-equality with selected_params is
    ///         left to live deploy time; this test guards the SCHEMA.
    function test_CommittedDeploymentFixturesAreSchemaConformant() public view {
        _assertFixtureSchema("script/deployments/sprint_3/rh_chain.json",     "rh_chain");
        _assertFixtureSchema("script/deployments/sprint_3/sepolia.json",      "sepolia");
        _assertFixtureSchema("script/deployments/sprint_3/polygon_amoy.json", "polygon_amoy");
    }

    function _assertFixtureSchema(string memory path, string memory expectedChain) internal view {
        string memory raw = vm.readFile(path);
        // chain string round-trips.
        assertEq(raw.readString(".chain"), expectedChain, "fixture: chain string mismatch");

        // Required nested keys.
        assertTrue(raw.keyExists(".contracts.energyController"), "fixture: missing energyController");
        assertTrue(raw.keyExists(".contracts.phaseManager"),     "fixture: missing phaseManager");
        assertTrue(raw.keyExists(".contracts.agentLifecycle"),   "fixture: missing agentLifecycle");
        assertTrue(raw.keyExists(".contracts.decisionLog"),      "fixture: missing decisionLog");
        assertTrue(raw.keyExists(".contracts.tombstoneNFT"),     "fixture: missing tombstoneNFT");

        // Params block exposes the 9 PRD §14.1 keys.
        assertTrue(raw.keyExists(".params.INITIAL_BREATH"),       "fixture: missing INITIAL_BREATH");
        assertTrue(raw.keyExists(".params.SOFT_CAP_THRESHOLD"),   "fixture: missing SOFT_CAP_THRESHOLD");
        assertTrue(raw.keyExists(".params.DESPERATE_THRESHOLD"),  "fixture: missing DESPERATE_THRESHOLD");
        assertTrue(raw.keyExists(".params.PASSIVE_BURN_RATE"),    "fixture: missing PASSIVE_BURN_RATE");
        assertTrue(raw.keyExists(".params.E_DECISION_TAX"),       "fixture: missing E_DECISION_TAX");
        assertTrue(raw.keyExists(".params.E_TIME_TAX_PER_TICK"),  "fixture: missing E_TIME_TAX_PER_TICK");
        assertTrue(raw.keyExists(".params.CONVERSION_RATE"),      "fixture: missing CONVERSION_RATE");
        assertTrue(raw.keyExists(".params.TARGET_HORIZON"),       "fixture: missing TARGET_HORIZON");
        assertTrue(raw.keyExists(".params.MIN_BET_SIZE"),         "fixture: missing MIN_BET_SIZE");

        // Top-level metadata keys.
        assertTrue(raw.keyExists(".selectedParamsHash"), "fixture: missing selectedParamsHash");
        assertTrue(raw.keyExists(".deployedAtBlock"),    "fixture: missing deployedAtBlock");
        assertTrue(raw.keyExists(".foundryLockHash"),    "fixture: missing foundryLockHash");
    }
}
