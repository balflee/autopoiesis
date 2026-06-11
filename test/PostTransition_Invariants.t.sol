// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {PhaseManager}     from "contracts/PhaseManager.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {AgentLifecycle}   from "contracts/AgentLifecycle.sol";

/// @title PostTransition_Invariants — T-A-006 PRD §6.13 pin
/// @notice After `transitionToApprenticeship()` lands, every row of the
///         PRD §6.13 Phase-2 activation table is anchored to a concrete
///         on-chain invariant — so the Track B main loop can rely on the
///         chain as the SSOT for which Phase-2 mechanics are live.
///
///         PRD §6.13 Phase 2 row (the cells this file pins):
///
///         | mechanism             | Phase 2 value         | invariant pinned here                   |
///         |-----------------------|-----------------------|------------------------------------------|
///         | Passive Metabolism    | 0.7 / min (半速)       | burnTimeTax(0.7e6) succeeds + debits      |
///         | Action Cost           | ON                    | burnDecisionTax succeeds + debits         |
///         | Idle Decay            | OFF                   | breath unchanged across vm.warp jumps     |
///         | 强制决策周期            | 60 min                | currentPhase()==1 ⇒ off-chain reads 60min |
///         | Lung Expansion        | ON                    | topUpBreath above cap → SoftCapDeflected  |
///         | Apprenticeship Fail   | reset enabled (TP §3.2)| InvalidTransition on rewind to Childhood  |
///         | USDC bankroll         | shadow (PRD §6.13)     | bankroll moves DO NOT affect breath       |
///
///         Spec anchors:
///           * PRD §6.13 — phase-segmented activation table.
///           * PRD §6.7  — soft cap + lung-expansion.
///           * PRD §6.1  — BREATH ≠ bankroll (independent accounts).
///           * TP §3.2   — PhaseManager unidirectional state machine.
contract PostTransition_Invariants_Test is Test {
    PhaseManager     internal pm;
    EnergyController internal ec;
    AgentLifecycle   internal al;

    address internal constant DEPLOYER  = address(0xA11CE);
    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;
    uint256 internal constant SIGNER_PK      = 0xA11CE2D5;

    /// @dev PRD §6.13 Phase-2 passive-metabolism rate. 0.7e6 BREATH per
    ///      minute in 1e6 fixed-point (the EnergyController unit).
    uint256 internal constant PASSIVE_METABOLISM_P2_PER_MIN = 7e5; // 0.7 * 1e6

    function setUp() public {
        vm.startPrank(DEPLOYER);

        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, vm.addr(SIGNER_PK));

        pm = new PhaseManager();
        al = new AgentLifecycle(address(ec));

        // Operator script flips both phase clocks in the same broadcast.
        pm.transitionToApprenticeship();
        ec.setPhase(EnergyController.Phase.Apprenticeship);

        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // T1 — Passive Metabolism 0.7 / min (PRD §6.13)
    //
    //      The contract is passive — the operator burns time-tax per tick
    //      with `amount = PASSIVE_METABOLISM_P2_PER_MIN * elapsed_minutes`.
    //      Pin that the controller accepts that exact unit and the burn
    //      is reflected in `breath` field-for-field.
    // -----------------------------------------------------------------------

    function test_PassiveMetabolism_HalfSpeedBurnAccepted() public {
        uint256 before = ec.breath();
        uint256 minutesElapsed = 60;
        uint256 amount = PASSIVE_METABOLISM_P2_PER_MIN * minutesElapsed;

        vm.prank(DEPLOYER);
        ec.burnTimeTax(amount, "passive_metabolism_phase2");

        assertEq(ec.breath(), before - amount, "P2 passive burn debited at 0.7/min rate");
    }

    function testFuzz_PassiveMetabolismIsDebitedExactly(uint16 minutesElapsed) public {
        uint256 m = bound(uint256(minutesElapsed), 1, 1_000);
        uint256 amount = PASSIVE_METABOLISM_P2_PER_MIN * m;
        vm.assume(amount < ec.breath());

        uint256 before = ec.breath();
        vm.prank(DEPLOYER);
        ec.burnTimeTax(amount, "passive_metabolism_fuzz");
        assertEq(ec.breath(), before - amount, "exact debit");
    }

    // -----------------------------------------------------------------------
    // T2 — Action Cost ✅ (PRD §6.13)
    // -----------------------------------------------------------------------

    function test_ActionCost_BurnDecisionTaxActive() public {
        // Phase == Apprenticeship → ActionCost.NO_BET fires per decision.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship), "phase pinned");

        uint256 before = ec.breath();
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(2e6, "action_cost_no_bet");
        assertEq(ec.breath(), before - 2e6, "ActionCost debit applied");
    }

    function test_ActionCost_RevertsWhenPaused() public {
        // Even with the activation gate ON, the pause modifier on the
        // controller still gates the burn — the Phase-2 launch script's
        // post-transition smoke test would catch a stuck pause flag.
        vm.startPrank(DEPLOYER);
        ec.pause();
        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.burnDecisionTax(1e6, "action_cost_no_bet");
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // T3 — Idle Decay OFF (PRD §6.13)
    //
    //      No mechanism burns breath without an explicit caller invocation
    //      in Phase 2; time itself does not decay the balance. Pin via
    //      vm.warp + state diff.
    // -----------------------------------------------------------------------

    function test_IdleDecay_OFF_BreathStableAcrossTimeWarp() public {
        uint256 before = ec.breath();
        // Jump 24 hours forward without any external call.
        vm.warp(block.timestamp + 1 days);
        assertEq(ec.breath(), before, "no idle decay over 24h");

        // And a long-range fuzz-style hop too.
        vm.warp(block.timestamp + 365 days);
        assertEq(ec.breath(), before, "no idle decay over a year");
    }

    function test_IdleDecay_OFF_BankrollStableAcrossTimeWarp() public {
        // Bankroll account is shadow in P2 but the chain ledger still
        // accumulates settlement events. Idle time alone must not move it.
        uint256 b0 = ec.bankroll();
        vm.warp(block.timestamp + 7 days);
        assertEq(ec.bankroll(), b0, "bankroll has no idle decay");
    }

    // -----------------------------------------------------------------------
    // T4 — 强制决策周期 60 min signal (PRD §6.4 + §6.13)
    //
    //      Chain signal is the phase value itself; off-chain agent reads
    //      `pm.currentPhase()` to pick the 60-min window in Phase 2.
    // -----------------------------------------------------------------------

    function test_DecisionCycleSignal_PhaseIsApprenticeship() public view {
        assertEq(uint8(pm.currentPhase()), 1, "Phase=Apprenticeship => 60min cycle (Track B reads)");
    }

    // -----------------------------------------------------------------------
    // T5 — Lung Expansion ✅ (PRD §6.13)
    //
    //      `topUpBreath` is callable; tops above the soft cap fold into
    //      `SoftCapDeflected` per PRD §6.7. We exercise both the under-cap
    //      and over-cap branches because the off-chain agent relies on the
    //      deflection event for the dashboard "Lung Expansion" UI in P2.
    // -----------------------------------------------------------------------

    function test_LungExpansion_TopUpUnderCapAccepted() public {
        // Burn some breath first so the controller is below the cap.
        vm.startPrank(DEPLOYER);
        ec.burnTimeTax(1_000e6, "drain_for_lung_expansion_test");

        uint256 before = ec.breath();
        ec.topUpBreath(500e6, "lung_expansion_under_cap");
        vm.stopPrank();

        assertEq(ec.breath(), before + 500e6, "under-cap top-up credited");
    }

    function test_LungExpansion_SoftCapDeflectionEmitted() public {
        vm.startPrank(DEPLOYER);
        // We're already at GENESIS_BREATH and the cap is MAX_BREATH; a
        // top-up of MAX_BREATH should fully saturate and deflect.
        uint256 headroom = MAX_BREATH - ec.breath();
        uint256 attempted = MAX_BREATH; // > headroom
        vm.expectEmit(true, true, true, true);
        emit EnergyController.SoftCapDeflected(attempted, MAX_BREATH, headroom);
        ec.topUpBreath(attempted, "lung_expansion_over_cap");
        vm.stopPrank();

        assertEq(ec.breath(), MAX_BREATH, "clamped at soft cap");
    }

    // -----------------------------------------------------------------------
    // T6 — Apprenticeship Failure (reset enabled per TP §3.2 + PRD §6.13)
    //
    //      PRD §6.13 marks Apprenticeship-Failure-reset as ON in Phase 2.
    //      The chain layer's contribution is the unidirectional state
    //      machine: once in Apprenticeship the contract cannot regress
    //      to Childhood through any code path. Off-chain reset is a Track
    //      B + Track D ritual (memory-bank wipe + cosmetic). Pin the
    //      chain invariant here.
    // -----------------------------------------------------------------------

    function test_ApprenticeshipNoRewindToChildhood() public {
        // There's no public function to rewind, but pin defensively that
        // a re-call of transitionToApprenticeship (which would imply a
        // hidden rewind to Childhood) reverts InvalidTransition.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToApprenticeship();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship), "phase pinned");
    }

    // -----------------------------------------------------------------------
    // T7 — USDC bankroll = 影子 (PRD §6.13)
    //
    //      Bankroll is a shadow account in P2 — it accumulates settlement
    //      events but never funds the breath account directly (PRD §6.1).
    //      Pin via cross-state: bankroll moves do NOT alter breath.
    // -----------------------------------------------------------------------

    function test_BankrollShadow_DoesNotFundBreath() public {
        uint256 breathBefore   = ec.breath();
        uint256 bankrollBefore = ec.bankroll();

        vm.startPrank(DEPLOYER);
        ec.bankrollCredit(5_000e6, "shadow_win");
        ec.bankrollDebit(1_000e6, "shadow_loss");
        vm.stopPrank();

        assertEq(ec.breath(), breathBefore, "breath untouched by bankroll moves");
        assertEq(ec.bankroll(), bankrollBefore + 4_000e6, "bankroll net of credit/debit");
    }
}
