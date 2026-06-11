// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm}        from "forge-std/Test.sol";
import {PhaseManager}    from "contracts/PhaseManager.sol";
import {EnergyController} from "contracts/EnergyController.sol";

/// @title PhaseTransition_P2ToP3 -- T-A-009 D18 launch verification
/// @notice Pins the contract-level behaviour the D18 Phase-3 launch script
///         depends on: admin gating, single-fire transition, event shape,
///         and the cross-contract signal the off-chain agent uses to flip
///         decisionCycle from 60min to 45min + activate Idle Decay. The
///         operational script `script/AdvanceToAdulthood.s.sol` is
///         exercised at the contract-call layer here.
///
///         Spec anchors:
///           * PRD §3      -- Apprenticeship → Adulthood unidirectional edge.
///           * PRD §5.1.A  -- Phase 3 is the trustless phase (renounce
///                            ritual lives in Phase3_Irreversibility.t.sol).
///           * PRD §6.4    -- 强制决策周期 transition (60min Phase 2 →
///                            45min Phase 3); off-chain reads
///                            pm.currentPhase() to pick the window.
///           * PRD §6.13   -- Phase 3 activation table: Passive 1.4/min,
///                            Action Cost ON, Idle Decay ON,
///                            decisionCycle 45min, Apprenticeship Failure
///                            path no longer available.
///           * TP §8 D17/D18 -- dress rehearsal + launch.
contract PhaseTransition_P2ToP3_Test is Test {
    PhaseManager     internal pm;
    EnergyController internal ec;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;
    uint256 internal constant SIGNER_PK      = 0xA11CE2D5;

    /// @dev PRD §6.13 Phase-3 passive-metabolism rate. 1.4e6 BREATH per
    ///      minute in 1e6 fixed-point (double the Phase-2 rate of 0.7).
    uint256 internal constant PASSIVE_METABOLISM_P3_PER_MIN = 14e5; // 1.4 * 1e6

    // Mirror events for vm.expectEmit / recordLogs decoding.
    event PhaseTransitioned(
        PhaseManager.Phase indexed oldPhase,
        PhaseManager.Phase indexed newPhase,
        address           indexed by
    );
    event PhaseChanged(EnergyController.Phase indexed previous, EnergyController.Phase indexed next);

    /// @notice Sets up a deployment where PhaseManager + EnergyController
    ///         are already in `Apprenticeship` -- the operational state at
    ///         the D17 dress rehearsal entry (post-D11 launch).
    function setUp() public {
        vm.startPrank(DEPLOYER);
        pm = new PhaseManager();
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, vm.addr(SIGNER_PK));

        // Climb to Phase 2 -- T-A-006 D11 entry state.
        pm.transitionToApprenticeship();
        ec.setPhase(EnergyController.Phase.Apprenticeship);
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // T1 -- admin gating: only owner can advance (PRD §3 + TP §3.2)
    // -----------------------------------------------------------------------

    function test_AdminGating_IntruderCannotAdvance() public {
        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToAdulthood();

        // State unchanged after the failed attempt.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship), "phase unchanged");
    }

    function testFuzz_AdminGating_AnyNonOwnerReverts(address caller) public {
        vm.assume(caller != DEPLOYER);
        vm.assume(caller != address(0));
        vm.prank(caller);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToAdulthood();
    }

    // -----------------------------------------------------------------------
    // T2 -- idempotent revert: second transition reverts InvalidTransition
    // -----------------------------------------------------------------------

    function test_Idempotent_SecondCallReverts() public {
        vm.startPrank(DEPLOYER);
        pm.transitionToAdulthood();
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood));

        // Re-call must revert -- the source-state guard (onlyInPhase
        // Apprenticeship) no longer matches.
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToAdulthood();
        vm.stopPrank();

        // Phase still Adulthood -- the revert did not regress it.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood), "phase pinned");
    }

    // -----------------------------------------------------------------------
    // T3 -- event emitted exactly once (PRD §3 + dashboard subscriber contract)
    // -----------------------------------------------------------------------

    function test_EventEmittedExactlyOnce() public {
        vm.recordLogs();
        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();
        Vm.Log[] memory entries = vm.getRecordedLogs();

        // Signature topic[0] is the event sighash, topics[1] = oldPhase,
        // topics[2] = newPhase, topics[3] = by.
        bytes32 sig = keccak256("PhaseTransitioned(uint8,uint8,address)");
        uint256 count;
        for (uint256 i = 0; i < entries.length; ++i) {
            if (entries[i].topics.length == 4 && entries[i].topics[0] == sig) {
                count++;
                // oldPhase = Apprenticeship (1), newPhase = Adulthood (2)
                assertEq(uint256(entries[i].topics[1]), 1, "oldPhase=Apprenticeship");
                assertEq(uint256(entries[i].topics[2]), 2, "newPhase=Adulthood");
                assertEq(address(uint160(uint256(entries[i].topics[3]))), DEPLOYER, "by=DEPLOYER");
            }
        }
        assertEq(count, 1, "PhaseTransitioned emitted exactly once");
    }

    function test_EventTopicShapeMatchesABI() public {
        vm.expectEmit(true, true, true, true);
        emit PhaseTransitioned(PhaseManager.Phase.Apprenticeship, PhaseManager.Phase.Adulthood, DEPLOYER);

        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();
    }

    // -----------------------------------------------------------------------
    // T4 -- Action Cost still active in Phase 3 (PRD §6.13 row "Action Cost ✅")
    //
    //      The off-chain Agent burns ActionCost.NO_BET on every decision.
    //      Phase 3 KEEPS this burn (vs. Phase 1 where it's off). Pin the
    //      controller still accepts the corresponding decision-tax burn
    //      after the transition.
    // -----------------------------------------------------------------------

    function test_ActionCostGateStillActive() public {
        // Mirror the operational sequence: PhaseManager flips first, then
        // EnergyController's local Phase mirror.
        vm.startPrank(DEPLOYER);
        pm.transitionToAdulthood();
        ec.setPhase(EnergyController.Phase.Adulthood);
        vm.stopPrank();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood), "phase pinned");
        assertEq(uint8(ec.currentPhase()), uint8(EnergyController.Phase.Adulthood), "ec mirror");

        uint256 beforeBreath = ec.breath();
        uint256 actionCost   = 1e6;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(actionCost, "p3_action_cost_no_bet");
        assertEq(ec.breath(), beforeBreath - actionCost, "ActionCost debit applied in Phase 3");
    }

    // -----------------------------------------------------------------------
    // T5 -- Idle Decay enabled (PRD §6.13 row "Idle Decay ✅")
    //
    //      Off-chain layer fires `burnTimeTax` per simulated tick when
    //      Phase 3 is active. The chain layer's contribution is that the
    //      burn keeps clearing the modifier stack (whenInitialized,
    //      whenNotPaused, notDead). Pin via a non-zero accept.
    // -----------------------------------------------------------------------

    function test_IdleDecayEnabled_TimeTaxAccepted() public {
        vm.startPrank(DEPLOYER);
        pm.transitionToAdulthood();
        ec.setPhase(EnergyController.Phase.Adulthood);
        vm.stopPrank();

        uint256 minutesElapsed = 30;
        uint256 amount = PASSIVE_METABOLISM_P3_PER_MIN * minutesElapsed;

        uint256 before = ec.breath();
        vm.prank(DEPLOYER);
        ec.burnTimeTax(amount, "p3_idle_decay");
        assertEq(ec.breath(), before - amount, "Idle Decay debited at Phase-3 1.4/min rate");
    }

    // -----------------------------------------------------------------------
    // T6 -- decisionCycle 60min → 45min signal (PRD §6.4 + §6.13)
    //
    //      Chain signal is the phase value itself; off-chain agent flips
    //      window from 60min (Phase 2) to 45min (Phase 3) by reading
    //      pm.currentPhase(). Pin the value transitions cleanly.
    // -----------------------------------------------------------------------

    function test_DecisionCycleSignal_TransitionsFrom60To45() public {
        // Pre-transition: phase index 1 => 60min cycle (Track B reads).
        assertEq(uint8(pm.currentPhase()), 1, "pre: Phase index 1 => 60min");

        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();

        // Post-transition: phase index 2 => 45min cycle.
        assertEq(uint8(pm.currentPhase()), 2, "post: Phase index 2 => 45min");

        // No intermediate value exists; consecutive reads stay pinned.
        for (uint256 i = 0; i < 4; ++i) {
            assertEq(uint8(pm.currentPhase()), 2, "phase pinned across rereads");
        }
    }

    // -----------------------------------------------------------------------
    // T7 -- Apprenticeship Failure path NO LONGER AVAILABLE (PRD §6.13)
    //
    //      Phase 3 row turns the "Apprenticeship Failure" reset OFF: there
    //      is no rewind from Adulthood, the agent has graduated. The chain
    //      invariant is the unidirectional state machine -- once Adulthood,
    //      every attempt to advance "back" to Apprenticeship reverts.
    // -----------------------------------------------------------------------

    function test_NoApprenticeshipFailureRewind() public {
        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();
        assertEq(uint8(pm.currentPhase()), 2, "in Adulthood");

        // Attempt the rewind: transitionToApprenticeship requires
        // currentPhase == Childhood -- from Adulthood, the modifier
        // reverts InvalidTransition.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToApprenticeship();

        // Likewise, transitionToAdulthood again reverts: source must be
        // Apprenticeship, but we're already in Adulthood.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToAdulthood();

        // Phase still pinned at Adulthood -- no rewind path exists.
        assertEq(uint8(pm.currentPhase()), 2, "phase still Adulthood");
    }

    // -----------------------------------------------------------------------
    // T8 -- EnergyController mirror flips cleanly to Adulthood (PRD §6.13)
    //
    //      `ec.setPhase(Adulthood)` is the operator's sync step in the
    //      AdvanceToAdulthood broadcast. Pin event payload + storage.
    // -----------------------------------------------------------------------

    function test_EnergyControllerMirror_FlipsToAdulthood() public {
        vm.expectEmit(true, true, true, true);
        emit PhaseChanged(EnergyController.Phase.Apprenticeship, EnergyController.Phase.Adulthood);

        vm.prank(DEPLOYER);
        ec.setPhase(EnergyController.Phase.Adulthood);

        assertEq(uint8(ec.currentPhase()), uint8(EnergyController.Phase.Adulthood), "ec mirror");
    }
}
