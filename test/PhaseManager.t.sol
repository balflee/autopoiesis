// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {PhaseManager} from "contracts/PhaseManager.sol";

/// @title PhaseManagerTest — T-A-003 unit coverage
/// @notice Verifies the unidirectional Childhood → Apprenticeship → Adulthood
///         transition graph (PRD §3) plus the access-control surface.
contract PhaseManagerTest is Test {
    PhaseManager internal pm;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);

    event PhaseTransitioned(PhaseManager.Phase indexed oldPhase, PhaseManager.Phase indexed newPhase, address indexed by);
    event OwnerUpdated(address indexed previousOwner, address indexed newOwner);

    function setUp() public {
        vm.prank(DEPLOYER);
        pm = new PhaseManager();
    }

    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    function test_ConstructorSetsOwnerAndStartsInChildhood() public view {
        assertEq(pm.owner(), DEPLOYER, "deployer is owner");
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Childhood), "phase=Childhood");
    }

    // -----------------------------------------------------------------------
    // Forward transitions — happy path
    // -----------------------------------------------------------------------

    function test_TransitionChildhoodToApprenticeshipEmitsAndUpdates() public {
        vm.expectEmit(true, true, true, true);
        emit PhaseTransitioned(PhaseManager.Phase.Childhood, PhaseManager.Phase.Apprenticeship, DEPLOYER);

        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship));
    }

    function test_TransitionApprenticeshipToAdulthoodEmitsAndUpdates() public {
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();

        vm.expectEmit(true, true, true, true);
        emit PhaseTransitioned(PhaseManager.Phase.Apprenticeship, PhaseManager.Phase.Adulthood, DEPLOYER);
        pm.transitionToAdulthood();
        vm.stopPrank();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood));
    }

    // -----------------------------------------------------------------------
    // Invalid transitions — skip / rewind / replay
    // -----------------------------------------------------------------------

    function test_RevertWhen_SkipChildhoodStraightToAdulthood() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToAdulthood();
    }

    function test_RevertWhen_ReplayApprenticeshipTransition() public {
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();

        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToApprenticeship();
        vm.stopPrank();
    }

    function test_RevertWhen_ReplayAdulthoodTransition() public {
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();
        pm.transitionToAdulthood();

        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToAdulthood();
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // Access control
    // -----------------------------------------------------------------------

    function test_RevertWhen_IntruderTransitionsApprenticeship() public {
        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToApprenticeship();
    }

    function test_RevertWhen_IntruderTransitionsAdulthood() public {
        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();

        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToAdulthood();
    }

    function test_SetOwnerRotates() public {
        address newOwner = address(0xBEEF);
        vm.expectEmit(true, true, true, true);
        emit OwnerUpdated(DEPLOYER, newOwner);
        vm.prank(DEPLOYER);
        pm.setOwner(newOwner);
        assertEq(pm.owner(), newOwner);
    }

    function test_RevertWhen_SetOwnerZero() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.ZeroAddress.selector);
        pm.setOwner(address(0));
    }

    function test_RevertWhen_IntruderSetsOwner() public {
        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.setOwner(INTRUDER);
    }

    // -----------------------------------------------------------------------
    // Invariant — phase is monotonic (never strictly decreases)
    // -----------------------------------------------------------------------

    function testFuzz_PhaseIsMonotonic(uint8 step1, uint8 step2) public {
        // Encode any sequence of attempted transitions; verify currentPhase
        // never strictly decreases.
        uint8 before = uint8(pm.currentPhase());

        if (step1 % 2 == 0) {
            vm.prank(DEPLOYER);
            try pm.transitionToApprenticeship() {} catch {}
        } else {
            vm.prank(DEPLOYER);
            try pm.transitionToAdulthood() {} catch {}
        }
        uint8 mid = uint8(pm.currentPhase());
        assertGe(mid, before, "phase never decreases after first attempt");

        if (step2 % 2 == 0) {
            vm.prank(DEPLOYER);
            try pm.transitionToApprenticeship() {} catch {}
        } else {
            vm.prank(DEPLOYER);
            try pm.transitionToAdulthood() {} catch {}
        }
        assertGe(uint8(pm.currentPhase()), mid, "phase never decreases after second attempt");
    }
}
