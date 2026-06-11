// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {AgentLifecycle} from "contracts/AgentLifecycle.sol";
import {DecisionLog} from "contracts/DecisionLog.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {PhaseManager} from "contracts/PhaseManager.sol";

/// @title IntegrationTest — T-A-003 cross-contract coverage
/// @notice Wires PhaseManager + EnergyController + AgentLifecycle +
///         DecisionLog into the full Day-3 surface and walks the canonical
///         lifecycle Childhood → Apprenticeship → Adulthood while burning
///         BREATH down through Desperate → TerminalLucidity → declared
///         Death. Asserts the cross-contract invariants the brief
///         identifies for D4/D5 dependencies.
contract IntegrationTest is Test {
    PhaseManager     internal pm;
    EnergyController internal ec;
    AgentLifecycle   internal al;
    DecisionLog      internal dlog;

    address internal constant DEPLOYER = address(0xA11CE);

    uint256 internal constant SIGNER_PK = 0xA11CE2D5;
    address internal signer;

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH = 12_000e6;

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.startPrank(DEPLOYER);

        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);

        pm = new PhaseManager();

        al = new AgentLifecycle(address(ec));
        dlog = new DecisionLog(address(al));
        al.setDecisionLog(address(dlog));

        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // End-to-end lifecycle walk
    // -----------------------------------------------------------------------

    function test_FullLifecycleWalkChildhoodThroughDeath() public {
        // -- Childhood ----------------------------------------------------
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Childhood));
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));

        vm.prank(DEPLOYER);
        al.recordDecision(hex"01", 1, int256(10));
        assertEq(dlog.decisionCount(), 1);
        assertEq(al.totalDecisions(), 1);
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));

        // Burn enough breath to drop to 19% → enter Desperate.
        uint256 toBurnForDesperate = GENESIS_BREATH - (GENESIS_BREATH * 19) / 100;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(toBurnForDesperate, "drain-1");

        // -- Apprenticeship (operational phase advance) -------------------
        vm.prank(DEPLOYER);
        pm.transitionToApprenticeship();
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Apprenticeship));

        // recordDecision picks up the life-state advance.
        vm.prank(DEPLOYER);
        al.recordDecision(hex"02", 2, int256(-5));
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Desperate));

        // -- Adulthood + Terminal Lucidity --------------------------------
        vm.prank(DEPLOYER);
        pm.transitionToAdulthood();
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood));

        // Drain further to <5% of initial.
        uint256 currentBreath = ec.breath();
        uint256 toBurnForTerminal = currentBreath - (GENESIS_BREATH * 4) / 100;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(toBurnForTerminal, "drain-2");

        vm.prank(DEPLOYER);
        al.pokeLifeState();
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.TerminalLucidity));
        assertTrue(al.isTerminal());

        // -- Death --------------------------------------------------------
        string memory words = "i tried";
        vm.prank(DEPLOYER);
        al.declareDeath(words, "");
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead));
        assertEq(al.lastWords(), words);
        assertEq(al.deathBlock(), block.number);

        // Post-death: every mutator must revert.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.recordDecision(hex"03", 3, int256(0));
    }

    // -----------------------------------------------------------------------
    // PhaseManager + AgentLifecycle are independent state machines
    // -----------------------------------------------------------------------

    function test_PhaseManagerAndAgentLifecycleAreOrthogonal() public {
        // Advance Phase without touching breath; LifeState stays Alive.
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();
        pm.transitionToAdulthood();
        vm.stopPrank();

        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood));
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));

        // Conversely, declaring death must NOT regress Phase.
        vm.prank(DEPLOYER);
        al.declareDeath("dead in Adulthood", "");
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood));
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead));
    }

    // -----------------------------------------------------------------------
    // DecisionLog access control across contracts
    // -----------------------------------------------------------------------

    function test_DecisionLogRejectsDirectCallsFromOwner() public {
        DecisionLog.DecisionRecord memory rec = DecisionLog.DecisionRecord({
            sigHash:    keccak256("bogus"),
            marketId:   99,
            outcome:    int256(0),
            timestamp:  uint64(block.timestamp),
            recordedBy: DEPLOYER
        });
        // Even the deployer / owner cannot bypass AgentLifecycle.
        vm.prank(DEPLOYER);
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);
    }

    function test_DecisionLogCountTracksAgentLifecycleTotals() public {
        vm.startPrank(DEPLOYER);
        al.recordDecision(hex"aa", 1, int256(1));
        al.recordDecision(hex"bb", 2, int256(2));
        al.recordDecision(hex"cc", 3, int256(-3));
        vm.stopPrank();

        assertEq(al.totalDecisions(), 3, "AgentLifecycle SSOT");
        assertEq(dlog.decisionCount(), 3, "DecisionLog mirrors");

        DecisionLog.DecisionRecord memory mid = dlog.getRecord(1);
        assertEq(mid.marketId, 2);
        assertEq(mid.outcome, int256(2));
    }

    // -----------------------------------------------------------------------
    // recordDecision burns BREATH off-chain (handled by caller); the
    // life-state recompute correctly tracks the resulting drop.
    // -----------------------------------------------------------------------

    function testFuzz_AdvanceMatchesBreathThresholds(uint256 burn) public {
        burn = bound(burn, 1, GENESIS_BREATH - 1);
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(burn, "fuzz");

        vm.prank(DEPLOYER);
        al.pokeLifeState();

        uint256 remaining = ec.breath();
        if (remaining * 100 < GENESIS_BREATH * 5) {
            assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.TerminalLucidity));
        } else if (remaining * 100 < GENESIS_BREATH * 20) {
            assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Desperate));
        } else {
            assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));
        }
    }
}
