// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {PhaseManager} from "contracts/PhaseManager.sol";

/// @title  Desperate_ApprenticeshipFailureBoundary — T-A-008 boundary coverage
/// @notice Per PRD §6.13, Desperate Mode is Phase-3-only. This file pins
///         the phase boundary: enterDesperateMode MUST revert WrongPhase
///         from Childhood and Apprenticeship, AND no pre-Phase-3 path
///         (including the brief's mention of an "Apprenticeship Failure
///         reset") can touch the sticky flag. There is no in-codebase
///         Apprenticeship-failure-reset path today; the test design here
///         pins that future regressions which add such a path do not
///         silently bypass the WrongPhase guard — the flag stays false
///         until the contract reaches Phase 3 AND `enterDesperateMode` is
///         explicitly called.
contract Desperate_ApprenticeshipFailureBoundaryTest is Test {
    PhaseManager internal pm;

    address internal constant DEPLOYER = address(0xA11CE);
    uint256 internal constant PRESSURE_HALF = 500_000;
    uint256 internal constant CYCLES_HELD = 2;

    function setUp() public {
        vm.prank(DEPLOYER);
        pm = new PhaseManager();
        // NOTE: deliberately DOES NOT climb — per-test setup will advance
        // to Childhood or Apprenticeship explicitly.
    }

    // -----------------------------------------------------------------------
    // Acceptance #1 — Phase 2 enterDesperateMode reverts WrongPhase
    // -----------------------------------------------------------------------

    /// PRD §6.13: Phase 2 (Apprenticeship) does NOT activate Desperate
    /// Mode. The call must revert WrongPhase (NOT NotEnoughPressureCycles,
    /// NOT AlreadyDesperate) so the off-chain caller can distinguish
    /// "wrong phase" from "wrong cycle count" in its retry logic.
    function test_RevertWhen_EnterDesperateInApprenticeship() public {
        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship));

        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        // Phase MUST be unchanged by the failed attempt.
        assertEq(
            uint8(pm.currentPhase()),
            uint8(PhaseManager.Phase.Apprenticeship),
            "currentPhase unchanged after WrongPhase revert"
        );
        // Flag stays false.
        assertFalse(pm.isDesperate(), "desperate stays false post-revert");
        // Cap stays normal.
        assertEq(pm.maxBreathRiskPct(), 3000, "cap stays 30% in Phase 2");
    }

    // -----------------------------------------------------------------------
    // Acceptance #2 — Childhood enterDesperateMode reverts WrongPhase
    // -----------------------------------------------------------------------

    function test_RevertWhen_EnterDesperateInChildhood() public {
        // setUp leaves us in Childhood; no transition needed.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Childhood));

        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        assertFalse(pm.isDesperate(), "desperate stays false in Childhood");
        assertEq(pm.maxBreathRiskPct(), 3000, "cap stays 30% in Childhood");
    }

    // -----------------------------------------------------------------------
    // Acceptance #3 — desperate flag is Phase 3 only across the full climb
    // -----------------------------------------------------------------------

    /// Pinning the boundary as a sequence: a failed Childhood call, then a
    /// successful Apprenticeship transition, then a failed Apprenticeship
    /// call, then a successful Adulthood transition, then the successful
    /// Adulthood call — the flag transitions exactly once, on the last step.
    /// Mirrors the "Apprenticeship Failure reset does not touch desperate
    /// flag" semantics: every Phase-2 attempt (and the implicit Childhood
    /// rejection) leaves the flag false; only an explicit Phase-3 call
    /// flips it.
    function test_DesperateFlagFlipsExactlyOnceAcrossFullClimb() public {
        // Childhood attempt — fails.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);
        assertFalse(pm.isDesperate(), "still false in Childhood");

        // Climb to Apprenticeship.
        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();
        // Apprenticeship attempt — fails.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);
        assertFalse(pm.isDesperate(), "still false in Apprenticeship");

        // Climb to Adulthood. The transition itself MUST NOT touch the
        // desperate flag (no implicit set/clear during phase advance).
        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();
        assertFalse(pm.isDesperate(), "Adulthood transition does not flip flag");
        assertEq(pm.maxBreathRiskPct(), 3000, "cap still 30% on Adulthood entry");

        // Now call enterDesperateMode — succeeds, flag flips.
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);
        assertTrue(pm.isDesperate(), "flag flips ONLY on Phase-3 call");
        assertEq(pm.maxBreathRiskPct(), 5000, "cap moves to 50% post-flip");
    }

    // -----------------------------------------------------------------------
    // Acceptance #4 — failed pre-Phase-3 attempts do not consume the
    //                 sticky guard. The agent can still flip the bit
    //                 later when the contract reaches Phase 3.
    // -----------------------------------------------------------------------

    function test_FailedPrePhase3AttemptsDoNotConsumeTheStickyGuard() public {
        // Childhood failure.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        // Apprenticeship failure.
        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);

        // Reach Adulthood.
        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();

        // The legitimate call must still succeed — the sticky guard was
        // never consumed by the failed attempts.
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_HALF, CYCLES_HELD);
        assertTrue(pm.isDesperate(), "legitimate Phase-3 call succeeded");
    }
}
