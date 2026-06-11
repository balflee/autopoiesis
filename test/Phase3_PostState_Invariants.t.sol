// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test}            from "forge-std/Test.sol";
import {PhaseManager}    from "contracts/PhaseManager.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {AgentLifecycle}  from "contracts/AgentLifecycle.sol";
import {DecisionLog}     from "contracts/DecisionLog.sol";
import {TombstoneNFT}    from "contracts/TombstoneNFT.sol";

/// @title Phase3_PostState_Invariants -- T-A-009 PRD §6.13 Phase 3 pin
/// @notice After `transitionToAdulthood()` lands, every row of the PRD §6.13
///         Phase-3 activation table is anchored to a concrete on-chain
///         invariant -- so the Track B main loop can rely on the chain as
///         the SSOT for which Phase-3 mechanics are live.
///
///         PRD §6.13 Phase 3 row (the cells this file pins):
///
///         | mechanism              | Phase 3 value           | invariant pinned here                       |
///         |------------------------|-------------------------|----------------------------------------------|
///         | Passive Metabolism     | 1.4 / min (full speed)  | burnTimeTax(1.4e6) succeeds + debits          |
///         | Action Cost            | ✅                      | burnDecisionTax succeeds + debits             |
///         | Idle Decay             | ✅                      | burnTimeTax succeeds across time warps        |
///         | 强制决策周期           | 45 min                  | currentPhase()==2 ⇒ off-chain reads 45min     |
///         | Survival Horizon → ρ   | ✅                      | off-chain; chain signal is the phase          |
///         | Lung Expansion         | ✅                      | deepenBreath succeeds when not Desperate      |
///         | Desperate Mode         | ✅                      | enterDesperateMode flips the sticky bit       |
///         | Terminal / Starvation  | ✅                      | AgentLifecycle.declareDeath callable          |
///         | USDC bankroll          | 真金 (real money)       | bankroll moves DO NOT affect breath           |
///         | Apprenticeship Failure | path no longer here     | InvalidTransition on rewind                   |
///
///         Spec anchors:
///           * PRD §5.1.A  -- Phase 3 is the trustless phase.
///           * PRD §5.1.C  -- death flow (Terminal / Starvation).
///           * PRD §6.1    -- BREATH ≠ bankroll; bankroll on Polygon ($50).
///           * PRD §6.7    -- soft cap + lung-expansion.
///           * PRD §6.9    -- Desperate Mode trigger + cap raise.
///           * PRD §6.13   -- Phase 3 activation table.
contract Phase3_PostState_Invariants_Test is Test {
    PhaseManager     internal pm;
    EnergyController internal ec;
    AgentLifecycle   internal al;
    DecisionLog      internal dl;
    TombstoneNFT     internal tn;

    address internal constant DEPLOYER = address(0xA11CE);
    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;
    uint256 internal constant SIGNER_PK      = 0xA11CE2D5;

    /// @dev PRD §6.13 Phase-3 passive-metabolism rate (1.4 / min, full speed).
    uint256 internal constant PASSIVE_METABOLISM_P3_PER_MIN = 14e5; // 1.4 * 1e6

    /// @notice Sets up a deployment fully transitioned to Adulthood -- the
    ///         operational Phase-3 state. The lock is NOT applied here;
    ///         this file pins PRD §6.13 rows independent of the renounce.
    function setUp() public {
        vm.startPrank(DEPLOYER);

        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, vm.addr(SIGNER_PK));
        pm = new PhaseManager();
        al = new AgentLifecycle(address(ec));
        dl = new DecisionLog(address(al));
        tn = new TombstoneNFT("Genesis", "GEN", address(al));
        al.setDecisionLog(address(dl));
        al.setTombstoneNFT(address(tn));

        // Wire the Desperate Mode lockout view on EnergyController.
        ec.setPhaseManager(address(pm));

        // Climb to Adulthood. Operator script flips both phase clocks in
        // the same broadcast.
        pm.transitionToApprenticeship();
        pm.transitionToAdulthood();
        ec.setPhase(EnergyController.Phase.Adulthood);

        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // T1 -- Passive Metabolism 1.4 / min (PRD §6.13)
    // -----------------------------------------------------------------------

    function test_PassiveMetabolism_FullSpeedBurnAccepted() public {
        uint256 before = ec.breath();
        uint256 minutesElapsed = 60;
        uint256 amount = PASSIVE_METABOLISM_P3_PER_MIN * minutesElapsed;

        vm.prank(DEPLOYER);
        ec.burnTimeTax(amount, "passive_metabolism_phase3");

        assertEq(ec.breath(), before - amount, "P3 passive burn debited at 1.4/min rate");
    }

    function testFuzz_PassiveMetabolismIsDebitedExactly(uint16 minutesElapsed) public {
        uint256 m = bound(uint256(minutesElapsed), 1, 1_000);
        uint256 amount = PASSIVE_METABOLISM_P3_PER_MIN * m;
        vm.assume(amount < ec.breath());

        uint256 before = ec.breath();
        vm.prank(DEPLOYER);
        ec.burnTimeTax(amount, "p3_passive_fuzz");
        assertEq(ec.breath(), before - amount, "exact debit at full-speed rate");
    }

    // -----------------------------------------------------------------------
    // T2 -- Action Cost ✅ (PRD §6.13)
    // -----------------------------------------------------------------------

    function test_ActionCost_BurnDecisionTaxActive() public {
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood), "phase pinned");

        uint256 before = ec.breath();
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(3e6, "p3_action_cost_no_bet");
        assertEq(ec.breath(), before - 3e6, "ActionCost debit applied in Phase 3");
    }

    // -----------------------------------------------------------------------
    // T3 -- Idle Decay ✅ + decisionCycle 45min signal (PRD §6.4 + §6.13)
    //
    //      Idle Decay is the off-chain agent firing burnTimeTax per tick
    //      whether or not a decision happened. Pin the on-chain
    //      accept-path stays clean and the cycle signal is the right
    //      phase value.
    // -----------------------------------------------------------------------

    function test_IdleDecayAndCycleSignal() public {
        // Cycle signal: phase index 2 => 45min window (Track B reads).
        assertEq(uint8(pm.currentPhase()), 2, "Phase=Adulthood => 45min cycle");

        // Idle-decay burn fires successfully even with no preceding action.
        uint256 before = ec.breath();
        vm.warp(block.timestamp + 1 hours);
        vm.prank(DEPLOYER);
        ec.burnTimeTax(PASSIVE_METABOLISM_P3_PER_MIN * 60, "idle_decay_after_1h");
        assertEq(ec.breath(), before - (PASSIVE_METABOLISM_P3_PER_MIN * 60), "idle decay applied");
    }

    // -----------------------------------------------------------------------
    // T4 -- Lung Expansion ✅ (PRD §6.7 + §6.13)
    //
    //      `deepenBreath` succeeds in Phase 3 as long as Desperate Mode
    //      has not been triggered. The cap-raise is one-way.
    // -----------------------------------------------------------------------

    function test_LungExpansion_DeepenBreathSucceeds() public {
        uint256 oldCap = ec.maxBreath();
        uint256 newCap = oldCap + 5_000e6;

        vm.prank(DEPLOYER);
        ec.deepenBreath(newCap);

        assertEq(ec.maxBreath(), newCap, "cap raised in Phase 3");
    }

    function test_LungExpansion_BlockedWhenDesperate() public {
        // Flip Desperate Mode first (Phase-3 only path).
        vm.startPrank(DEPLOYER);
        pm.enterDesperateMode(500_000, 2);
        assertTrue(pm.isDesperate(), "Desperate flipped");

        // Lung expansion now blocks per PRD §6.7. Pre-compute the argument
        // BEFORE expectRevert -- ec.maxBreath() is an external view and
        // would otherwise be the "next call" expectRevert observes.
        uint256 newCap = ec.maxBreath() + 1;
        vm.expectRevert(EnergyController.LungExpansionBlockedDesperate.selector);
        ec.deepenBreath(newCap);
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // T5 -- Desperate Mode ✅ (PRD §6.9 + §6.13)
    // -----------------------------------------------------------------------

    function test_DesperateMode_FlipsBitAndLiftsCap() public {
        assertFalse(pm.isDesperate(), "starts non-desperate");
        assertEq(pm.maxBreathRiskPct(), 3000, "normal cap 30%");

        vm.prank(DEPLOYER);
        pm.enterDesperateMode(600_000, 3);

        assertTrue(pm.isDesperate(), "desperate after flip");
        assertEq(pm.maxBreathRiskPct(), 5000, "desperate cap 50%");
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood), "still Adulthood");
    }

    // -----------------------------------------------------------------------
    // T6 -- Terminal Lucidity / Starvation ✅ (PRD §5.0 + §6.13)
    //
    //      The Phase-3 row turns Terminal Lucidity + Starvation triggers
    //      ON. The chain anchor is AgentLifecycle.declareDeath -- pin
    //      it remains callable from the legitimate owner, transitions
    //      lifeState to Dead, and mints the Tombstone.
    // -----------------------------------------------------------------------

    function test_TerminalAndStarvation_DeathFlowCompletes() public {
        // Drain breath toward starvation (full burn).
        vm.startPrank(DEPLOYER);
        ec.burnTimeTax(ec.breath(), "starvation_drain");
        vm.stopPrank();

        assertEq(ec.breath(), 0, "starved");

        // declareDeath captures last words + tombstone.
        vm.prank(DEPLOYER);
        al.declareDeath("phase3 terminal", "ipfs://memory");

        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead), "Dead state");
        assertEq(tn.nextTokenId(), 1, "tombstone minted");
        TombstoneNFT.Tombstone memory stored = tn.getTombstone(1);
        assertEq(stored.breathAtDeath, 0, "death snapshot recorded");
    }

    // -----------------------------------------------------------------------
    // T7 -- USDC bankroll 真金 (PRD §6.1 + §6.13)
    //
    //      Bankroll becomes real money in Phase 3 -- but the BREATH
    //      account stays independent on-chain. Pin via cross-state:
    //      bankroll moves do NOT alter breath.
    // -----------------------------------------------------------------------

    function test_USDCBankroll_RealMoneyIndependentOfBreath() public {
        uint256 breathBefore   = ec.breath();
        uint256 bankrollBefore = ec.bankroll();

        vm.startPrank(DEPLOYER);
        ec.bankrollCredit(50e6, "p3_real_win");
        ec.bankrollDebit(10e6, "p3_real_loss");
        vm.stopPrank();

        assertEq(ec.breath(), breathBefore, "breath untouched by Phase-3 bankroll moves");
        assertEq(ec.bankroll(), bankrollBefore + 40e6, "bankroll net of credit/debit");
    }

    // -----------------------------------------------------------------------
    // T8 -- Apprenticeship Failure path no longer available (PRD §6.13)
    //
    //      Phase 3 row turns the Apprenticeship Failure reset OFF: the
    //      chain unidirectional state machine forbids the rewind.
    // -----------------------------------------------------------------------

    function test_ApprenticeshipFailure_PathNoLongerAvailable() public {
        // transitionToApprenticeship from Adulthood: source-state guard fails.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToApprenticeship();

        // Re-transitionToAdulthood: same revert (source must be
        // Apprenticeship, but we're already in Adulthood).
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.InvalidTransition.selector);
        pm.transitionToAdulthood();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood), "phase pinned");
    }
}
