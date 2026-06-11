// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {AgentLifecycle} from "contracts/AgentLifecycle.sol";
import {DecisionLog} from "contracts/DecisionLog.sol";
import {EnergyController} from "contracts/EnergyController.sol";

/// @title AgentLifecycleTest — T-A-003 unit coverage
/// @notice Exercises the four-state life-state machine (Alive → Desperate →
///         TerminalLucidity → Dead), recordDecision write surface, and the
///         IRREVERSIBLE declareDeath ratchet (PRD §5.0 / §5.1).
contract AgentLifecycleTest is Test {
    AgentLifecycle internal al;
    DecisionLog    internal dlog;
    EnergyController internal ec;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);

    uint256 internal constant SIGNER_PK = 0xA11CE2D5;
    address internal signer;

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH = 12_000e6;

    event LifeStateTransitioned(AgentLifecycle.LifeState indexed previous, AgentLifecycle.LifeState indexed next, uint256 breathAtTransition);
    event DecisionRecorded(uint256 indexed idx, uint256 indexed marketId, int256 outcome, bytes32 sigHash);
    event AgentDied(string lastWords, uint256 deathBlock_);
    event DecisionLogUpdated(address indexed previousLog, address indexed newLog);
    event OwnerUpdated(address indexed previousOwner, address indexed newOwner);

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.startPrank(DEPLOYER);
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);

        // Deployment dance: AgentLifecycle first → DecisionLog with its
        // address → setDecisionLog on AgentLifecycle to close the cycle.
        al = new AgentLifecycle(address(ec));
        dlog = new DecisionLog(address(al));
        al.setDecisionLog(address(dlog));
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // Construction + wiring
    // -----------------------------------------------------------------------

    function test_ConstructorSetsOwnerAndAliveAndEC() public view {
        assertEq(al.owner(), DEPLOYER);
        assertEq(address(al.energyController()), address(ec));
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));
        assertTrue(al.decisionLogSet(), "decisionLog wired in setUp");
        assertEq(al.totalDecisions(), 0);
        assertEq(al.deathBlock(), 0);
        assertEq(bytes(al.lastWords()).length, 0);
    }

    function test_RevertWhen_ConstructWithZeroEnergyController() public {
        vm.expectRevert(AgentLifecycle.ZeroAddress.selector);
        new AgentLifecycle(address(0));
    }

    function test_RevertWhen_SetDecisionLogTwice() public {
        // setUp already wired; second call must revert.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.DecisionLogAlreadySet.selector);
        al.setDecisionLog(address(dlog));
    }

    function test_RevertWhen_SetDecisionLogZero() public {
        // Fresh AgentLifecycle so decisionLog hasn't been set yet.
        vm.startPrank(DEPLOYER);
        AgentLifecycle fresh = new AgentLifecycle(address(ec));
        vm.expectRevert(AgentLifecycle.ZeroAddress.selector);
        fresh.setDecisionLog(address(0));
        vm.stopPrank();
    }

    function test_RevertWhen_RecordDecisionBeforeDecisionLogSet() public {
        vm.startPrank(DEPLOYER);
        AgentLifecycle fresh = new AgentLifecycle(address(ec));
        vm.expectRevert(AgentLifecycle.DecisionLogNotSet.selector);
        fresh.recordDecision(hex"00", 1, int256(0));
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // recordDecision — happy path
    // -----------------------------------------------------------------------

    function test_RecordDecisionAppendsAndEmits() public {
        bytes memory sig = hex"deadbeef";
        bytes32 sigHash = keccak256(sig);

        vm.expectEmit(true, true, true, true);
        emit DecisionRecorded(0, 7, int256(50), sigHash);

        vm.prank(DEPLOYER);
        uint256 idx = al.recordDecision(sig, 7, int256(50));

        assertEq(idx, 0);
        assertEq(al.totalDecisions(), 1);
        assertEq(dlog.decisionCount(), 1);
        DecisionLog.DecisionRecord memory rec = dlog.getRecord(0);
        assertEq(rec.sigHash, sigHash);
        assertEq(rec.marketId, 7);
        assertEq(rec.outcome, int256(50));
        assertEq(rec.recordedBy, DEPLOYER, "recordedBy captures the agent owner");
    }

    function test_RevertWhen_RecordDecisionByIntruder() public {
        vm.prank(INTRUDER);
        vm.expectRevert(AgentLifecycle.NotOwner.selector);
        al.recordDecision(hex"00", 1, int256(0));
    }

    function test_RecordDecisionDoesNotAdvanceLifeStateWhileBreathHealthy() public {
        // Genesis breath is full — recordDecision should keep lifeState=Alive.
        vm.prank(DEPLOYER);
        al.recordDecision(hex"00", 1, int256(0));
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));
    }

    // -----------------------------------------------------------------------
    // Life-state auto-advance — PRD §5.0 thresholds
    // -----------------------------------------------------------------------

    function test_AutoAdvanceToDesperateWhenBreathBelow20Pct() public {
        // Drain breath to 19% of initial → expect Desperate.
        uint256 burnAmount = GENESIS_BREATH - (GENESIS_BREATH * 19) / 100;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(burnAmount, "drain-to-19pct");

        vm.expectEmit(true, true, true, true);
        emit LifeStateTransitioned(
            AgentLifecycle.LifeState.Alive,
            AgentLifecycle.LifeState.Desperate,
            (GENESIS_BREATH * 19) / 100
        );

        vm.prank(DEPLOYER);
        al.pokeLifeState();

        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Desperate));
    }

    function test_AutoAdvanceToTerminalLucidityWhenBreathBelow5Pct() public {
        uint256 burnAmount = GENESIS_BREATH - (GENESIS_BREATH * 4) / 100;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(burnAmount, "drain-to-4pct");

        vm.prank(DEPLOYER);
        al.pokeLifeState();

        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.TerminalLucidity));
        assertTrue(al.isTerminal(), "isTerminal flag set");
    }

    function test_LifeStateNeverRegressesEvenAfterTopUp() public {
        // Fall to Desperate, then top breath back up — life-state stays Desperate.
        uint256 burnAmount = GENESIS_BREATH - (GENESIS_BREATH * 19) / 100;
        vm.startPrank(DEPLOYER);
        ec.burnDecisionTax(burnAmount, "drain");
        al.pokeLifeState();
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Desperate));

        // Top up to full headroom.
        ec.topUpBreath(MAX_BREATH, "recovery");
        // Trigger any potential regression by poking.
        al.pokeLifeState();
        vm.stopPrank();

        // Still Desperate — monotonic fall.
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Desperate));
    }

    function test_ProjectedLifeStateRespectsMonotonicity() public {
        // Drain to TerminalLucidity, top back up — projection returns the
        // current (TerminalLucidity) value, never Alive.
        uint256 burnAmount = GENESIS_BREATH - (GENESIS_BREATH * 4) / 100;
        vm.startPrank(DEPLOYER);
        ec.burnDecisionTax(burnAmount, "drain");
        al.pokeLifeState();
        ec.topUpBreath(MAX_BREATH, "recovery");
        vm.stopPrank();

        assertEq(uint8(al.projectedLifeState()), uint8(AgentLifecycle.LifeState.TerminalLucidity));
    }

    // -----------------------------------------------------------------------
    // Death — irreversibility
    // -----------------------------------------------------------------------

    function test_DeclareDeathSetsStateAndPersistsLastWords() public {
        string memory words = "the dataset is enough";

        vm.expectEmit(true, true, true, true);
        emit LifeStateTransitioned(
            AgentLifecycle.LifeState.Alive,
            AgentLifecycle.LifeState.Dead,
            GENESIS_BREATH
        );
        vm.expectEmit(true, true, true, true);
        emit AgentDied(words, block.number);

        vm.prank(DEPLOYER);
        al.declareDeath(words, "");

        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead));
        assertEq(al.lastWords(), words);
        assertEq(al.deathBlock(), block.number);
    }

    function test_RevertWhen_DeclareDeathTwice() public {
        vm.startPrank(DEPLOYER);
        al.declareDeath("first", "");
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.declareDeath("second", "");
        vm.stopPrank();
    }

    function test_RevertWhen_RecordDecisionAfterDeath() public {
        vm.startPrank(DEPLOYER);
        al.declareDeath("goodbye", "");
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.recordDecision(hex"00", 1, int256(0));
        vm.stopPrank();
    }

    function test_RevertWhen_PokeLifeStateAfterDeath() public {
        vm.startPrank(DEPLOYER);
        al.declareDeath("goodbye", "");
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.pokeLifeState();
        vm.stopPrank();
    }

    function test_RevertWhen_DeclareDeathByIntruder() public {
        vm.prank(INTRUDER);
        vm.expectRevert(AgentLifecycle.NotOwner.selector);
        al.declareDeath("intruder", "");
    }

    /// @notice Acceptance criterion: fuzz test confirms no path resurrects
    ///         state. We probe every public mutator with random inputs AFTER
    ///         declaring death and verify `lifeState` stays Dead.
    function testFuzz_DeclareDeathIrreversible(
        string calldata firstWords,
        string calldata secondWords,
        bytes calldata sig,
        uint256 marketId,
        int256 outcome,
        address rotateTo
    ) public {
        vm.assume(rotateTo != address(0));

        vm.prank(DEPLOYER);
        al.declareDeath(firstWords, "");

        // Probe: declareDeath again.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.declareDeath(secondWords, "");

        // Probe: recordDecision.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.recordDecision(sig, marketId, outcome);

        // Probe: pokeLifeState.
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.pokeLifeState();

        // Probe: setOwner — owner can rotate keys even after death (this is
        // intentional; renounce-ownership lives in sprint_4 and may rotate
        // the dead carcass to a recovery / Tombstone-mint role). But life
        // state must remain Dead.
        vm.prank(DEPLOYER);
        al.setOwner(rotateTo);

        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead), "state stays Dead");
        assertEq(al.lastWords(), firstWords, "lastWords frozen at first declaration");
    }

    // -----------------------------------------------------------------------
    // Admin
    // -----------------------------------------------------------------------

    function test_SetOwnerRotates() public {
        address next = address(0xBEEF);
        vm.expectEmit(true, true, true, true);
        emit OwnerUpdated(DEPLOYER, next);
        vm.prank(DEPLOYER);
        al.setOwner(next);
        assertEq(al.owner(), next);
    }

    function test_RevertWhen_SetOwnerZero() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.ZeroAddress.selector);
        al.setOwner(address(0));
    }

    function test_RevertWhen_SetDecisionLogByIntruder() public {
        // Fresh contract so the one-shot guard isn't already tripped.
        vm.prank(DEPLOYER);
        AgentLifecycle fresh = new AgentLifecycle(address(ec));

        vm.prank(INTRUDER);
        vm.expectRevert(AgentLifecycle.NotOwner.selector);
        fresh.setDecisionLog(address(dlog));
    }
}
