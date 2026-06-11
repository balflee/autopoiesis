// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script}              from "forge-std/Script.sol";
import {stdJson}             from "forge-std/StdJson.sol";
import {EnergyController}    from "contracts/EnergyController.sol";
import {PhaseManager}        from "contracts/PhaseManager.sol";
import {AgentLifecycle}      from "contracts/AgentLifecycle.sol";
import {DecisionLog}         from "contracts/DecisionLog.sol";
import {TombstoneNFT}        from "contracts/TombstoneNFT.sol";
import {CalibratedConstants} from "contracts/lib/CalibratedConstants.sol";

/// @title  DeployCalibrated — T-A-005 redeploy with Track C-calibrated parameters
/// @notice Reads the BayesOpt-validated economic parameters from
///         `reports/calibration/selected_params.json` (produced by T-C-003),
///         deploys all five Genesis L3 contracts in the canonical
///         chicken-and-egg order from `DeployAll.s.sol`, wires the
///         AgentLifecycle back-pointers, and (when broadcasting) emits a
///         post-deploy fixture under `script/deployments/sprint_3/<chain>.json`
///         carrying contract addresses + `selected_params_hash` so Track B's
///         Phase 1 training can target a known-good bundle.
///
///         The script is structured so the same `run()` body works for
///         dry-run simulation (no `--broadcast` flag — see brief
///         "READ-ONLY constraint") AND for the eventual live deployment
///         (sprint_4 Gate C). The `_emitDeployFixture` step is gated on
///         a `WRITE_FIXTURE=true` env var so the simulation path stays
///         filesystem-side-effect-free by default. The same env flag is
///         what the live-deploy operator will set per chain.
///
///         Environment variables (all optional unless flagged REQUIRED):
///           SELECTED_PARAMS_PATH   string  default
///                                          "reports/calibration/selected_params.json"
///                                          (override for tests)
///           ATTESTATION_SIGNER     address REQUIRED — off-chain Polymarket
///                                          settlement signer (PRD §3.7)
///           CHAIN_LABEL            string  default "rh_chain" — used as the
///                                          deployment-fixture filename
///                                          ("rh_chain" | "sepolia" |
///                                          "polygon_amoy" per TP §7)
///           TOMBSTONE_NAME         string  default "Genesis Tombstone"
///           TOMBSTONE_SYMBOL       string  default "GTOMB"
///           TOMBSTONE_BASE_URI     string  default
///                                          "https://api.genesis.experiment/tombstone/"
///           WRITE_FIXTURE          bool    default false — when true, the
///                                          script writes
///                                          "script/deployments/sprint_3/
///                                          <CHAIN_LABEL>.json" with the
///                                          deployed addresses + the
///                                          selected_params_hash. Pure dry
///                                          runs leave this off.
///
///         Spec anchors:
///           * PRD §14         — calibration framework precedes deploy.
///           * PRD §15         — locked decision 5 + pragma 0.8.24.
///           * TP §3.1–§3.5    — five contracts.
///           * TP §7           — 3-chain parallel deploy (RH / Sepolia /
///                               Polygon Amoy) sharing identical bytecode.
///           * TP §8 D5        — Day 5 redeploy deliverable.
contract DeployCalibrated is Script {
    using stdJson for string;

    // -----------------------------------------------------------------------
    // Deployment manifest — returned by `run()` so off-chain operators can
    // capture the addresses without parsing forge stdout.
    // -----------------------------------------------------------------------

    /// @notice Bundle of the five deployed contracts plus the calibrated
    ///         parameters used at construction. The off-chain `state_sync`
    ///         + reconciler reads this back via the JSON fixture.
    struct Deployment {
        EnergyController         energyController;
        PhaseManager             phaseManager;
        AgentLifecycle           agentLifecycle;
        DecisionLog              decisionLog;
        TombstoneNFT             tombstoneNFT;
        CalibratedConstants.Params params;
        bytes32                  selectedParamsHash;
    }

    /// @notice TombstoneNFT constructor arg bundle — shared across the
    ///         env-driven and direct-call entry points.
    ///         v33 backfill: TombstoneNFT v0.2.0 dropped `baseURI` from the
    ///         constructor (tokenURI is now fully on-chain data: URI per
    ///         PRD §5.1.C). `TOMBSTONE_BASE_URI` env var is no longer read.
    struct TombstoneConfig {
        string name;
        string symbol;
    }

    /// @notice Production entry point — reads every knob from env vars
    ///         (SELECTED_PARAMS_PATH, ATTESTATION_SIGNER, TOMBSTONE_*,
    ///         WRITE_FIXTURE, CHAIN_LABEL) and runs the full pipeline.
    ///         This is what `forge script script/DeployCalibrated.s.sol`
    ///         invokes by default.
    function run() external returns (Deployment memory d) {
        string memory paramsPath = vm.envOr(
            "SELECTED_PARAMS_PATH",
            string("reports/calibration/selected_params.json")
        );
        address signer  = vm.envAddress("ATTESTATION_SIGNER");
        TombstoneConfig memory tCfg = TombstoneConfig({
            name:    vm.envOr("TOMBSTONE_NAME",   string("Genesis Tombstone")),
            symbol:  vm.envOr("TOMBSTONE_SYMBOL", string("GTOMB"))
        });
        bool writeFixture = vm.envOr("WRITE_FIXTURE", false);
        string memory chainLabel = vm.envOr("CHAIN_LABEL", string("rh_chain"));

        return _runImpl(paramsPath, signer, tCfg, writeFixture, chainLabel);
    }

    /// @notice Direct-arg entry point — lets the caller pass every knob
    ///         in a single tx so tests + sprint_4 multi-chain operators
    ///         can sidestep the process-wide env map. Forge runs test
    ///         suites in parallel; relying on env vars makes deterministic
    ///         multi-suite runs flap. The PRODUCTION path still flows
    ///         through `run()` and the env vars documented in this file's
    ///         top-level NatSpec; this overload is the cleaner internal
    ///         seam.
    /// @param paramsPath    path on disk to selected_params.json
    /// @param signer        address authorised for EIP-712 settlement
    /// @param tCfg          TombstoneNFT constructor args
    /// @param writeFixture  whether to emit script/deployments/sprint_3/
    ///                      <chainLabel>.json (live deploy = true; dry run
    ///                      = false per brief READ-ONLY constraint)
    /// @param chainLabel    fixture filename label
    function runWithArgs(
        string memory paramsPath,
        address signer,
        TombstoneConfig memory tCfg,
        bool writeFixture,
        string memory chainLabel
    ) external returns (Deployment memory d) {
        return _runImpl(paramsPath, signer, tCfg, writeFixture, chainLabel);
    }

    /// @notice Convenience overload — uses the canonical Tombstone config
    ///         + skips fixture emission. Equivalent to the test path.
    function runWithParamsPath(string memory paramsPath, address signer)
        external
        returns (Deployment memory d)
    {
        return _runImpl(
            paramsPath,
            signer,
            _defaultTombstoneConfig(),
            false,
            "rh_chain"
        );
    }

    // -----------------------------------------------------------------------
    // Internal pipeline
    // -----------------------------------------------------------------------

    function _runImpl(
        string memory paramsPath,
        address signer,
        TombstoneConfig memory tCfg,
        bool writeFixture,
        string memory chainLabel
    ) internal returns (Deployment memory d) {
        require(signer != address(0), "DeployCalibrated: ATTESTATION_SIGNER must be non-zero");

        // 1. Read + parse + validate the calibration payload BEFORE any
        //    broadcast so a malformed selected_params.json fails the
        //    dry-run cleanly instead of leaving a half-deployed bundle.
        string memory rawJson = vm.readFile(paramsPath);
        d.params = readSelectedParams(rawJson);
        CalibratedConstants.validate(d.params);
        // sha256 over the EXACT bytes-on-disk gives a stable
        // selected_params_hash that survives whitespace-stable replays. The
        // fixture emission step writes this hash verbatim so off-chain
        // consumers can prove the deploy targeted a specific calibration run.
        d.selectedParamsHash = sha256(bytes(rawJson));

        // 2. Scale BREATH-unit params to on-chain 1e6 fixed-point.
        uint256 initialBreathOnChain    = CalibratedConstants.toOnChainBreath(d.params.initialBreath);
        uint256 softCapThresholdOnChain = CalibratedConstants.toOnChainBreath(d.params.softCapThreshold);

        // 3. Broadcast — same ordering as DeployAll.s.sol (TP §3.1–§3.5).
        vm.startBroadcast();

        d.energyController = new EnergyController();
        d.energyController.initialize(initialBreathOnChain, softCapThresholdOnChain, signer);

        d.phaseManager   = new PhaseManager();
        d.agentLifecycle = new AgentLifecycle(address(d.energyController));
        d.decisionLog    = new DecisionLog(address(d.agentLifecycle));
        d.tombstoneNFT   = new TombstoneNFT(tCfg.name, tCfg.symbol, address(d.agentLifecycle));

        d.agentLifecycle.setDecisionLog(address(d.decisionLog));
        d.agentLifecycle.setTombstoneNFT(address(d.tombstoneNFT));

        vm.stopBroadcast();

        // 4. Optional fixture emission — false for dry-run (brief READ-ONLY).
        if (writeFixture) {
            _emitDeployFixture(d, chainLabel);
        }
    }

    /// @notice Canonical TombstoneNFT constructor args used by every
    ///         non-customised deploy path (tests + the default `run()`
    ///         env-driven flow). Hard-coding the strings here keeps the
    ///         multi-chain deploys (TP §7) byte-identical without
    ///         operators having to remember to set TOMBSTONE_*.
    function _defaultTombstoneConfig() internal pure returns (TombstoneConfig memory) {
        return TombstoneConfig({
            name:    "Genesis Tombstone",
            symbol:  "GTOMB"
        });
    }

    // -----------------------------------------------------------------------
    // selected_params.json parser
    //
    // Schema (per .dev/policy/calibration_playbook.md §`selected_params.json`):
    //   flat JSON object, UPPERCASE PRD §14.1 keys, integer values in
    //   BREATH-unit space. ParamSpace currently emits 9 keys; we read all
    //   9 unconditionally and rely on stdJson.readUint reverting on
    //   missing required fields. The two ON-CHAIN-relevant keys
    //   (INITIAL_BREATH + SOFT_CAP_THRESHOLD) MUST be present; the other 7
    //   are reserved for off-chain consumers (Track B engines) but
    //   mirrored here so the artifact-vs-struct mapping stays explicit.
    // -----------------------------------------------------------------------

    /// @notice Parse a flat `selected_params.json` payload into a Params
    ///         struct. Reverts via stdJson if any required key is missing
    ///         OR the value is not a non-negative integer.
    /// @dev    Pure — `stdJson.readUint` resolves to `vm.parseJsonUint`
    ///         which is itself a pure cheatcode (decodes the input string
    ///         without touching chain or storage). Keeping this `pure`
    ///         lets test harnesses call it from any context, including
    ///         constant-folding situations.
    function readSelectedParams(string memory rawJson)
        public
        pure
        returns (CalibratedConstants.Params memory p)
    {
        p.initialBreath       = rawJson.readUint(CalibratedConstants.JSON_KEY_INITIAL_BREATH);
        p.softCapThreshold    = rawJson.readUint(CalibratedConstants.JSON_KEY_SOFT_CAP_THRESHOLD);
        p.desperateThreshold  = rawJson.readUint(CalibratedConstants.JSON_KEY_DESPERATE_THRESHOLD);
        p.passiveBurnRate     = rawJson.readUint(CalibratedConstants.JSON_KEY_PASSIVE_BURN_RATE);
        p.eDecisionTax        = rawJson.readUint(CalibratedConstants.JSON_KEY_E_DECISION_TAX);
        p.eTimeTaxPerTick     = rawJson.readUint(CalibratedConstants.JSON_KEY_E_TIME_TAX_PER_TICK);
        p.conversionRate      = rawJson.readUint(CalibratedConstants.JSON_KEY_CONVERSION_RATE);
        p.targetHorizon       = rawJson.readUint(CalibratedConstants.JSON_KEY_TARGET_HORIZON);
        p.minBetSize          = rawJson.readUint(CalibratedConstants.JSON_KEY_MIN_BET_SIZE);
    }

    // -----------------------------------------------------------------------
    // Fixture emission
    // -----------------------------------------------------------------------

    /// @notice Serialize the deployment manifest to
    ///         `script/deployments/sprint_3/<chainLabel>.json`. Schema:
    ///
    ///         {
    ///           "chain":              "<chainLabel>",
    ///           "deployedAtBlock":    "<block.number>",
    ///           "selectedParamsHash": "0x<sha256 of selected_params.json>",
    ///           "contracts": {
    ///             "energyController": "0x..",
    ///             "phaseManager":     "0x..",
    ///             "agentLifecycle":   "0x..",
    ///             "decisionLog":      "0x..",
    ///             "tombstoneNFT":     "0x.."
    ///           },
    ///           "params": { ... 9 calibrated BREATH-unit fields ... },
    ///           "onChainBreath": {
    ///             "initialBreath":    "<initialBreath * 1e6>",
    ///             "softCapThreshold": "<softCapThreshold * 1e6>"
    ///           }
    ///         }
    ///
    ///         Off-chain reconcilers (state_sync.py, weight_updater
    ///         persistence) read this directly. Skipped when
    ///         WRITE_FIXTURE=false (the brief's READ-ONLY default).
    function _emitDeployFixture(Deployment memory d, string memory chainLabel) internal {
        // Build the nested "contracts" object first; serialize sets keys
        // incrementally + returns the rolling JSON string for each scope.
        string memory contractsKey = "contracts";
        contractsKey.serialize("energyController", address(d.energyController));
        contractsKey.serialize("phaseManager",     address(d.phaseManager));
        contractsKey.serialize("agentLifecycle",   address(d.agentLifecycle));
        contractsKey.serialize("decisionLog",      address(d.decisionLog));
        string memory contractsJson = contractsKey.serialize("tombstoneNFT", address(d.tombstoneNFT));

        // Build the "params" object mirroring the calibration JSON.
        string memory paramsKey = "params";
        paramsKey.serialize("INITIAL_BREATH",      d.params.initialBreath);
        paramsKey.serialize("SOFT_CAP_THRESHOLD",  d.params.softCapThreshold);
        paramsKey.serialize("DESPERATE_THRESHOLD", d.params.desperateThreshold);
        paramsKey.serialize("PASSIVE_BURN_RATE",   d.params.passiveBurnRate);
        paramsKey.serialize("E_DECISION_TAX",      d.params.eDecisionTax);
        paramsKey.serialize("E_TIME_TAX_PER_TICK", d.params.eTimeTaxPerTick);
        paramsKey.serialize("CONVERSION_RATE",     d.params.conversionRate);
        paramsKey.serialize("TARGET_HORIZON",      d.params.targetHorizon);
        string memory paramsJson = paramsKey.serialize("MIN_BET_SIZE", d.params.minBetSize);

        // On-chain projected values — what the contracts actually store.
        string memory onChainKey = "onChainBreath";
        onChainKey.serialize("initialBreath",
            CalibratedConstants.toOnChainBreath(d.params.initialBreath));
        string memory onChainJson = onChainKey.serialize("softCapThreshold",
            CalibratedConstants.toOnChainBreath(d.params.softCapThreshold));

        // Compose the top-level object.
        string memory root = "deployment";
        root.serialize("chain",              chainLabel);
        root.serialize("deployedAtBlock",    block.number);
        root.serialize("selectedParamsHash", d.selectedParamsHash);
        root.serialize("contracts",          contractsJson);
        root.serialize("params",             paramsJson);
        string memory finalJson = root.serialize("onChainBreath",     onChainJson);

        string memory outPath = string.concat("script/deployments/sprint_3/", chainLabel, ".json");
        finalJson.write(outPath);
    }
}
