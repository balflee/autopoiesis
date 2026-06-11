// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {PhaseManager}     from "contracts/PhaseManager.sol";
import {EnergyController} from "contracts/EnergyController.sol";

/// @title PhaseTransition_P1ToP2 — T-A-006 D11 launch verification
/// @notice Pins the contract-level behaviour the D11 Phase-2 launch script
///         depends on: admin gating, single-fire transition, event shape,
///         and the cross-contract signal the off-chain agent uses to flip
///         ActionCost.NO_BET and the 60-min decision cycle. The
///         operational script `script/AdvanceToApprenticeship.s.sol` is
///         exercised at the contract-call layer here — the script itself
///         is a thin wrapper around `transitionToApprenticeship()` and
///         derives all its safety properties from the contract.
///
///         Spec anchors:
///           * PRD §3      — Childhood → Apprenticeship unidirectional edge.
///           * PRD §6.4    — 强制决策周期 transition (45min Phase 3, 60min
///                           Phase 2); the off-chain agent reads
///                           `pm.currentPhase()` to pick the window.
///           * PRD §6.13   — Phase 2 activation table (ActionCost ON, etc.).
///                           Chain-side gate is `pm.currentPhase() == 1`.
///           * TP §8 D11   — hard deadline for the transition.
contract PhaseTransition_P1ToP2_Test is Test {
    PhaseManager     internal pm;
    EnergyController internal ec;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;
    uint256 internal constant SIGNER_PK      = 0xA11CE2D5;

    // Mirror of PhaseManager's event for vm.expectEmit / recordLogs decoding.
    event PhaseTransitioned(
        PhaseManager.Phase indexed oldPhase,
        PhaseManager.Phase indexed newPhase,
        address           indexed by
    );
    event PhaseChanged(EnergyController.Phase indexed previous, EnergyController.Phase indexed next);

    function setUp() public {
        vm.startPrank(DEPLOYER);
        pm = new PhaseManager();
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, vm.addr(SIGNER_PK));
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // T1 — admin gating: only owner can transition (PRD §3 + TP §3.2)
    // -----------------------------------------------------------------------

    function test_AdminGating_IntruderCannotAdvance() public {
        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToApprenticeship();

        // State unchanged after the failed attempt.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Childhood), "phase unchanged");
    }

    function testFuzz_AdminGating_AnyNonOwnerReverts(address caller) public {
        vm.assume(caller != DEPLOYER);
        vm.assume(caller != address(0));
        vm.prank(caller);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToApprenticeship();
    }

    // -----------------------------------------------------------------------
    // T2 — idempotent revert: second transition reverts InvalidTransition
    // -----------------------------------------------------------------------

    function test_Idempotent_SecondCallReverts() public {
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship));

        // The same call again must revert because the source-state guard
        // (onlyInPhase(Childhood)) no longer matches.
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToApprenticeship();
        vm.stopPrank();

        // Phase still Apprenticeship — the revert did not regress it.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship), "phase pinned");
    }

    // -----------------------------------------------------------------------
    // T3 — event emitted exactly once (PRD §3 + dashboard subscriber contract)
    // -----------------------------------------------------------------------

    function test_EventEmittedExactlyOnce() public {
        vm.recordLogs();
        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();
        Vm.Log[] memory entries = vm.getRecordedLogs();

        // Count PhaseTransitioned emissions; signature topic[0] is the
        // event sighash, topics[1] = oldPhase, topics[2] = newPhase,
        // topics[3] = by. Three indexed args + sig = 4 topics.
        bytes32 sig = keccak256("PhaseTransitioned(uint8,uint8,address)");
        uint256 count;
        for (uint256 i = 0; i < entries.length; ++i) {
            if (entries[i].topics.length == 4 && entries[i].topics[0] == sig) {
                count++;
                // oldPhase = Childhood (0), newPhase = Apprenticeship (1)
                assertEq(uint256(entries[i].topics[1]), 0, "oldPhase=Childhood");
                assertEq(uint256(entries[i].topics[2]), 1, "newPhase=Apprenticeship");
                assertEq(address(uint160(uint256(entries[i].topics[3]))), DEPLOYER, "by=DEPLOYER");
            }
        }
        assertEq(count, 1, "PhaseTransitioned emitted exactly once");
    }

    function test_EventTopicShapeMatchesABI() public {
        // Belt-and-braces: use expectEmit with the exact field set so a
        // future ABI tweak that re-orders / re-indexes fields is caught.
        vm.expectEmit(true, true, true, true);
        emit PhaseTransitioned(PhaseManager.Phase.Childhood, PhaseManager.Phase.Apprenticeship, DEPLOYER);

        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();
    }

    // -----------------------------------------------------------------------
    // T4 — ActionCost.NO_BET activation gate (PRD §6.13).
    //
    //      The contract layer does not enforce per-phase burn permissions;
    //      the off-chain Agent reads `pm.currentPhase()` and only
    //      applies ActionCost.NO_BET when phase >= Apprenticeship. The
    //      invariant we pin on-chain is therefore the SIGNAL — that the
    //      gate flips from 0 → 1 exactly when this transition fires,
    //      and that the EnergyController accepts the corresponding
    //      decision-tax burn once that signal is set.
    // -----------------------------------------------------------------------

    function test_ActionCostGateFlipsOnTransition() public {
        // Before the transition: gate is OFF (off-chain agent must NOT
        // apply ActionCost.NO_BET burn — pin via phase==Childhood).
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Childhood), "gate OFF pre");

        // Mirror the operational sequence: PhaseManager flips first, then
        // the operator script syncs EnergyController's local Phase mirror.
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();
        ec.setPhase(EnergyController.Phase.Apprenticeship);
        vm.stopPrank();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship), "gate ON post");
        assertEq(uint8(ec.currentPhase()), uint8(EnergyController.Phase.Apprenticeship), "ec mirror");

        // Now ActionCost.NO_BET (modelled by an explicit burnDecisionTax
        // call from the off-chain Agent) clears all controller modifiers.
        uint256 beforeBreath = ec.breath();
        uint256 actionCost   = 1e6; // 1 BREATH (1e6 fixed-point unit) — typical NO_BET
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(actionCost, "action_cost_no_bet");
        assertEq(ec.breath(), beforeBreath - actionCost, "NO_BET burn debited");
    }

    // -----------------------------------------------------------------------
    // T5 — decision-cycle window setter (PRD §6.4)
    //
    //      The Phase-keyed window (60min in P2, 45min in P3) is a Track B
    //      off-chain constant; the on-chain signal is the phase value
    //      itself. This test pins that the phase value transitions
    //      cleanly so the agent's window selector observes a clean edge.
    // -----------------------------------------------------------------------

    function test_DecisionCycleSignal_TransitionsCleanly() public {
        // Phase 1 (Childhood) — 90-min window per PRD §6.4 (Track B reads).
        assertEq(uint8(pm.currentPhase()), 0, "Phase index 0 => 90min cycle");

        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();

        // Phase 2 (Apprenticeship) — 60-min window.
        assertEq(uint8(pm.currentPhase()), 1, "Phase index 1 => 60min cycle");

        // No intermediate value exists; consecutive reads stay pinned.
        for (uint256 i = 0; i < 4; ++i) {
            assertEq(uint8(pm.currentPhase()), 1, "phase pinned across rereads");
        }
    }

    function test_NoSkipFromChildhoodToAdulthood() public {
        // P1 → P3 must be forbidden — the 60min Phase-2 cycle cannot
        // be silently skipped to the 45min Phase-3 cycle.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToAdulthood();
    }
}
