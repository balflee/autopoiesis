// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title  CalibratedConstants
/// @notice Solidity mirror of Track C's `reports/calibration/selected_params.json`
///         output schema. T-A-005 (this task) is the consumer; T-C-003 the
///         producer. The struct + helpers here are the SINGLE source of
///         truth for translating calibrated economic parameters into the
///         constructor / initializer arguments of the five Genesis L3
///         contracts (PRD §14, TP §3.1).
///
///         Two coordinate systems coexist in this codebase:
///
///         1. **Calibration / BREATH-unit space** — what Track C's
///            `sim/params.py` ParamSpace dataclass emits. Values are
///            human-readable integers (e.g. `INITIAL_BREATH = 8000`,
///            meaning eight thousand BREATH units). PRD §14.1's parameter
///            table uses this space; `selected_params.json` keys are the
///            UPPERCASE PRD-canonical names per
///            `.dev/policy/calibration_playbook.md §selected_params.json`.
///
///         2. **On-chain fixed-point space** — what `EnergyController`
///            stores. All BREATH amounts are scaled by `BREATH_SCALE = 1e6`
///            so the existing 1e6-precision math (see TP §3.1) works
///            without floats. The deploy script multiplies BREATH-space
///            values by `BREATH_SCALE` before passing them to
///            `EnergyController.initialize`.
///
///         The struct fields below mirror the 9 ParamSpace dimensions from
///         `sim/params.py` using camelCase Solidity naming. The
///         `JSON_KEY_*` string constants document the UPPERCASE JSON keys
///         that the script reads from `selected_params.json` — this avoids
///         every reader inventing their own constants and lets a future
///         lookahead-style audit grep one location to find every key.
///
///         Spec anchors:
///           * PRD §14         — mechanism calibration framework + locked
///                               decision 5 (Track C precedes deploy).
///           * PRD §14.1       — parameter table; UPPERCASE PRD names.
///           * PRD §14.2       — GOOD_CALIBRATION 14 objectives.
///           * TP §3.1         — EnergyController canonical constants.
///           * TP §7           — 3-chain parallel deploy (RH / Sepolia /
///                               Polygon Amoy) sharing identical bytecode.
///           * TP §8 D5        — Day 5 redeploy with calibrated params
///                               (this task, T-A-005).
///           * .dev/policy/calibration_playbook.md §`selected_params.json missing`
///                             — canonical JSON shape (flat object,
///                               UPPERCASE PRD keys, integer values in
///                               BREATH-unit space).
library CalibratedConstants {
    // -----------------------------------------------------------------------
    // Coordinate-system constants
    // -----------------------------------------------------------------------

    /// @notice BREATH-unit → on-chain fixed-point scale. Multiplying a
    ///         `selected_params.json` BREATH-space value by `BREATH_SCALE`
    ///         yields the EnergyController storage representation. Locked
    ///         at 1e6 to match TP §3.1 ("BREATH balance — the single life
    ///         scalar (1e6 fixed point)").
    uint256 internal constant BREATH_SCALE = 1e6;

    // -----------------------------------------------------------------------
    // selected_params.json JSON-key constants (PRD §14.1 UPPERCASE names)
    //
    // Per `.dev/policy/calibration_playbook.md` the canonical JSON shape is
    // a flat object with the 14 PRD §14.1 parameter names as keys. T-A-005
    // round 1 reads the 9 dimensions ParamSpace currently emits (sim/params
    // .py); the rest are reserved for future T-C-NNN expansion (PRD §14.1
    // hints at MAX_BREATH_RISK_PCT etc. but those have not been added to
    // ParamSpace yet — adding them is a non-breaking change for the script,
    // see `readSelectedParams`'s `keyExists` guards in DeployCalibrated.s.sol).
    // -----------------------------------------------------------------------

    string internal constant JSON_KEY_INITIAL_BREATH        = ".INITIAL_BREATH";
    string internal constant JSON_KEY_PASSIVE_BURN_RATE     = ".PASSIVE_BURN_RATE";
    string internal constant JSON_KEY_CONVERSION_RATE       = ".CONVERSION_RATE";
    string internal constant JSON_KEY_TARGET_HORIZON        = ".TARGET_HORIZON";
    string internal constant JSON_KEY_MIN_BET_SIZE          = ".MIN_BET_SIZE";
    string internal constant JSON_KEY_E_DECISION_TAX        = ".E_DECISION_TAX";
    string internal constant JSON_KEY_E_TIME_TAX_PER_TICK   = ".E_TIME_TAX_PER_TICK";
    string internal constant JSON_KEY_SOFT_CAP_THRESHOLD    = ".SOFT_CAP_THRESHOLD";
    string internal constant JSON_KEY_DESPERATE_THRESHOLD   = ".DESPERATE_THRESHOLD";

    // -----------------------------------------------------------------------
    // Struct — Solidity mirror of sim/params.py ParamSpace dataclass.
    //
    // Field order matches the alphabetised dataclass order (which is
    // ParamSpace's canonical JSON-output order via `sort_keys=True` in
    // `ParamSpace.to_json()`). Reviewers diffing artifact-vs-struct can
    // grep this struct top-down and the JSON top-down and they line up.
    // -----------------------------------------------------------------------

    /// @notice Calibrated economic parameters mirrored from
    ///         `selected_params.json`. Values are BREATH-unit integers
    ///         (NOT 1e6 fixed-point) — convert with `BREATH_SCALE` before
    ///         passing to EnergyController.
    /// @param initialBreath       PRD §14.1 INITIAL_BREATH; genesis BREATH
    ///                            balance. Multiplied by BREATH_SCALE for
    ///                            `EnergyController.initialize`.
    /// @param softCapThreshold    PRD §14.1 SOFT_CAP_THRESHOLD; soft cap
    ///                            for top-ups. Multiplied by BREATH_SCALE
    ///                            for `EnergyController.initialize.maxBreath_`.
    /// @param desperateThreshold  PRD §14.1 DESPERATE_THRESHOLD; not on-chain
    ///                            today (AgentLifecycle uses a fixed 20%
    ///                            constant per PRD §5.0) but mirrored for
    ///                            forward-compat with sprint_4 work that
    ///                            may parameterise it.
    /// @param passiveBurnRate     PRD §14.1 PASSIVE_BURN_RATE; runtime
    ///                            burn unit (consumed by Track B engines,
    ///                            not deployed on-chain in sprint_3).
    /// @param eDecisionTax        PRD §14.1 E_DECISION_TAX; runtime burn
    ///                            (Track B engines).
    /// @param eTimeTaxPerTick     PRD §14.1 E_TIME_TAX_PER_TICK; runtime
    ///                            burn (Track B engines).
    /// @param conversionRate      PRD §14.1 CONVERSION_RATE; off-chain
    ///                            decision modulator.
    /// @param targetHorizon       PRD §14.1 TARGET_HORIZON; off-chain
    ///                            decision modulator.
    /// @param minBetSize          PRD §14.1 MIN_BET_SIZE; off-chain
    ///                            Polymarket sizing.
    struct Params {
        uint256 initialBreath;
        uint256 softCapThreshold;
        uint256 desperateThreshold;
        uint256 passiveBurnRate;
        uint256 eDecisionTax;
        uint256 eTimeTaxPerTick;
        uint256 conversionRate;
        uint256 targetHorizon;
        uint256 minBetSize;
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    /// @notice Scale a BREATH-unit value into on-chain 1e6 fixed-point.
    /// @dev    Pure + checked math; reverts on overflow (uint256 ceiling
    ///         is 2^256 - 1 — `BREATH_SCALE = 1e6` only overflows for
    ///         input ≥ ~1.16e71, which `selected_params.json` cannot
    ///         realistically reach).
    function toOnChainBreath(uint256 breathUnitValue) internal pure returns (uint256) {
        return breathUnitValue * BREATH_SCALE;
    }

    /// @notice Inverse of `toOnChainBreath`. Used by PostDeployInvariants
    ///         to assert chain state matches `selected_params.json`
    ///         bit-for-bit after the BREATH_SCALE round-trip.
    /// @dev    Integer division; assumes the on-chain value was produced
    ///         by `toOnChainBreath` (multiple of BREATH_SCALE). The
    ///         invariant test asserts the round-trip is lossless.
    function fromOnChainBreath(uint256 onChainValue) internal pure returns (uint256) {
        return onChainValue / BREATH_SCALE;
    }

    /// @notice Validate that a Params struct has the two mandatory chain
    ///         constraints satisfied:
    ///           1. `initialBreath > 0`             (EnergyController.ZeroBreath)
    ///           2. `softCapThreshold >= initialBreath` (TP §3.1; the
    ///              EnergyController initialize() will clamp upward but
    ///              we surface the misconfiguration at script time so
    ///              calibration vs. on-chain semantics stay aligned).
    /// @dev    Pure function so the deploy script + PostDeployInvariants
    ///         + future Track B consumers can share one validator. Reverts
    ///         on failure with a descriptive string (script-level error,
    ///         not a contract revert — gas not material in deployment).
    function validate(Params memory p) internal pure {
        require(p.initialBreath > 0, "CalibratedConstants: initialBreath must be > 0");
        require(
            p.softCapThreshold >= p.initialBreath,
            "CalibratedConstants: softCapThreshold must be >= initialBreath"
        );
    }
}
