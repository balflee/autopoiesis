// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {PhaseManager} from "contracts/PhaseManager.sol";
import {EnergyController} from "contracts/EnergyController.sol";

/// @title  Desperate_Invariants — T-A-008 cross-contract invariant coverage
/// @notice Verifies the on-chain blast radius of `PhaseManager.enterDesperateMode`:
///         (a) EnergyController.deepenBreath is BLOCKED with the exact
///             `LungExpansionBlockedDesperate` selector (PRD §6.7);
///         (b) PhaseManager.maxBreathRiskPct steps 30% → 50% basis points
///             (PRD §6.9 cap-raise);
///         (c) donations (`topUpBreath`) keep working — Desperate doesn't
///             freeze the soft-cap inflow path;
///         (d) PhaseManager.enterDesperateMode does NOT touch maxBreath in
///             EnergyController — the cap itself is independent of the bit.
contract Desperate_InvariantsTest is Test {
    PhaseManager      internal pm;
    EnergyController  internal ec;

    address internal constant DEPLOYER = address(0xA11CE);
    uint256 internal constant SIGNER_PK = 0xA11CE2D5;
    address internal signer;

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;
    uint256 internal constant CYCLES_HELD    = 2;
    uint256 internal constant PRESSURE_HALF  = 500_000;

    // Mirror events.
    event MaxBreathDeepened(uint256 oldMaxBreath, uint256 newMaxBreath);
    event EnergyChanged(uint256 oldBreath, uint256 newBreath, string reason);

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.startPrank(DEPLOYER);
        pm = new PhaseManager();
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);
        ec.setPhaseManager(address(pm));
        pm.transitionToApprenticeship();
        pm.transitionToAdulthood();
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // (a) deepenBreath revert path
    // -----------------------------------------------------------------------

    /// Pre-Desperate sanity — deepenBreath SUCCEEDS in Phase 3, lifting the
    /// soft cap. This is the control case for the desperate-blocks-it test.
    function test_DeepenBreathSucceedsBeforeDesperate() public {
        uint256 newCap = MAX_BREATH + 1_000e6;
        vm.expectEmit(false, false, false, true);
        emit MaxBreathDeepened(MAX_BREATH, newCap);

        vm.prank(DEPLOYER);
        ec.deepenBreath(newCap);

        assertEq(ec.maxBreath(), newCap, "cap raised");
    }

    /// Acceptance — once `PhaseManager.isDesperate()` is true, deepenBreath
    /// MUST revert. Pins the exact selector (the brief calls this out
    /// explicitly so the test selector match is the wire contract).
    function test_DeepenBreathRevertsWhenDesperate_ExactSelector() public {
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);
        assertTrue(pm.isDesperate(), "preconditions: desperate flipped");

        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.LungExpansionBlockedDesperate.selector);
        ec.deepenBreath(MAX_BREATH + 1_000e6);

        // Sanity: cap unchanged by the failed call.
        assertEq(ec.maxBreath(), MAX_BREATH, "cap untouched by reverted call");
    }

    // -----------------------------------------------------------------------
    // (b) maxBreathRiskPct read jump
    // -----------------------------------------------------------------------

    /// PRD §6.9 cap step: 30% (3000 bps) → 50% (5000 bps). The off-chain
    /// Kelly sizer (T-B-009) reads this view every cycle; pin the exact bps
    /// values so a unit-mismatch regression in either consumer fails here.
    function test_MaxBreathRiskPctJumpsFromNormalToDesperate() public {
        assertEq(pm.maxBreathRiskPct(), 3000, "normal cap 30% bps");

        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        assertEq(pm.maxBreathRiskPct(), 5000, "desperate cap 50% bps");
        // And it stays at 5000 (sticky) — the bit is set-once so the view
        // never re-derives to a lower value.
        assertEq(pm.maxBreathRiskPct(), 5000, "still 50% bps on re-read");
    }

    // -----------------------------------------------------------------------
    // (c) donate still pre-Terminal
    // -----------------------------------------------------------------------

    /// PRD §6.9 lists what Desperate Mode disables (lung expansion); it does
    /// NOT disable donations / top-ups. The bankroll → BREATH path must
    /// remain open so a late-game milestone reward can still credit BREATH
    /// to the dying agent (PRD §6.7 soft cap still clamps as before).
    function test_TopUpBreathStillWorksUnderDesperate() public {
        // Burn some BREATH so there is headroom for a top-up.
        uint256 burn = 2_000e6;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(burn, "setup-headroom");
        uint256 breathBeforeFlip = ec.breath();
        assertEq(breathBeforeFlip, GENESIS_BREATH - burn, "burned");

        // Flip Desperate.
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);
        assertTrue(pm.isDesperate());

        // topUpBreath SUCCEEDS even though Desperate.
        uint256 donate = 500e6;
        vm.expectEmit(false, false, false, true);
        emit EnergyChanged(breathBeforeFlip, breathBeforeFlip + donate, "donation");
        vm.prank(DEPLOYER);
        ec.topUpBreath(donate, "donation");

        assertEq(ec.breath(), breathBeforeFlip + donate, "donation credited");
    }

    // -----------------------------------------------------------------------
    // (d) lung-expansion-attempt-during-desperate revert selector — explicit
    //     selector pinning (separate from (a) for the brief's bullet list)
    // -----------------------------------------------------------------------

    /// The brief acceptance says "lung-expansion-attempt-during-desperate
    /// emits LungExpansionBlockedDesperate revert selector". Selector pin:
    function test_LungExpansionAttemptDuringDesperateEmitsExactSelector() public {
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        // Compute selector at runtime so the test self-documents the wire bytes.
        bytes4 expected = bytes4(keccak256("LungExpansionBlockedDesperate()"));
        assertEq(
            EnergyController.LungExpansionBlockedDesperate.selector,
            expected,
            "selector matches typed-error keccak"
        );

        vm.prank(DEPLOYER);
        vm.expectRevert(expected);
        ec.deepenBreath(MAX_BREATH + 5_000e6);
    }

    // -----------------------------------------------------------------------
    // (e) donate cap unchanged after Desperate
    // -----------------------------------------------------------------------

    /// PRD §6.9 explicitly says Desperate Mode lifts the RISK cap (30%→50%)
    /// — NOT the BREATH cap (maxBreath). The brief's "donate cap unchanged"
    /// invariant pins that the EnergyController.maxBreath storage is
    /// untouched by the PhaseManager flip; top-ups continue to clamp to the
    /// pre-flip cap (PRD §6.7).
    function test_MaxBreathStorageUnchangedByEnterDesperate() public {
        uint256 capBefore = ec.maxBreath();
        assertEq(capBefore, MAX_BREATH);

        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        assertEq(ec.maxBreath(), capBefore, "maxBreath storage untouched");

        // And the soft cap still clamps — top-up beyond cap is deflected.
        uint256 huge = MAX_BREATH * 10;
        vm.prank(DEPLOYER);
        ec.topUpBreath(huge, "deflect-test");
        assertEq(ec.breath(), ec.maxBreath(), "breath clamped at cap");
    }

    // -----------------------------------------------------------------------
    // Bonus: legacy deploy without PhaseManager wiring keeps deepenBreath
    // open (forwards-compatibility for the sprint_2-4 deploy script that
    // does not set phaseManager).
    // -----------------------------------------------------------------------

    function test_DeepenBreathOpenWhenPhaseManagerUnwired() public {
        vm.prank(DEPLOYER);
        EnergyController bare = new EnergyController();
        vm.prank(DEPLOYER);
        bare.initialize(GENESIS_BREATH, MAX_BREATH, signer);

        // PhaseManager not set; deepenBreath should succeed.
        vm.prank(DEPLOYER);
        bare.deepenBreath(MAX_BREATH + 100);
        assertEq(bare.maxBreath(), MAX_BREATH + 100);
    }
}
