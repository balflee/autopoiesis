// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {AgentLifecycle}   from "contracts/AgentLifecycle.sol";
import {DecisionLog}      from "contracts/DecisionLog.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {TombstoneNFT}     from "contracts/TombstoneNFT.sol";
import {IAgentLifecycle}  from "contracts/IAgentLifecycle.sol";

/// @title AgentLifecycle_Die_Test — T-A-007 canonical die() coverage
/// @notice Verifies the PRD §5.1.C `die(DeathPayload)` entry point:
///           * `NotDeadYet` precondition (EnergyController.breath() == 0)
///           * Tombstone struct round-trip (weights, decisionHistoryHash,
///             phaseStats, deathCause, terminalAfterglow)
///           * sticky Dead flag — once die() lands, every mutator reverts
///           * double-die reverts (`AlreadyDead`)
///           * memoryBankCid degraded path forwards to TombstoneNFT
///           * three DeathCause enum values forward verbatim
///           * IAgentLifecycle interface compatibility
contract AgentLifecycle_Die_Test is Test {
    EnergyController internal ec;
    AgentLifecycle   internal al;
    DecisionLog      internal dlog;
    TombstoneNFT     internal tnft;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);
    uint256 internal constant SIGNER_PK = 0xA11CE2D5;
    address internal signer;

    // Use small initialBreath so a single decision-tax burn drains it to 0.
    uint256 internal constant GENESIS_BREATH = 1_000e6;
    uint256 internal constant MAX_BREATH     = 2_000e6;

    string internal constant NAME    = "Genesis Tombstone";
    string internal constant SYMBOL  = "GTOMB";

    event LifeStateTransitioned(AgentLifecycle.LifeState indexed previous, AgentLifecycle.LifeState indexed next, uint256 breathAtTransition);
    event AgentDied(string lastWords, uint256 deathBlock_);
    event TombstoneMinted(uint256 indexed tokenId, address indexed to, uint64 deathTs, uint8 deathCause, string memoryBankCid);
    event TombstoneMintedWithoutMemoryBank(uint256 indexed tokenId, address indexed to, uint64 deathTs);

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.startPrank(DEPLOYER);
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);

        al = new AgentLifecycle(address(ec));
        dlog = new DecisionLog(address(al));
        tnft = new TombstoneNFT(NAME, SYMBOL, address(al));

        al.setDecisionLog(address(dlog));
        al.setTombstoneNFT(address(tnft));
        vm.stopPrank();
    }

    function _zeroBreath() internal {
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(GENESIS_BREATH, "drain-to-zero");
        assertEq(ec.breath(), 0, "breath driven to zero in test setup");
    }

    function _samplePayload() internal pure returns (AgentLifecycle.DeathPayload memory) {
        return AgentLifecycle.DeathPayload({
            cause:               TombstoneNFT.DeathCause.Starvation,
            terminalAfterglow:   true,
            lastWords:           "the dataset was enough",
            memoryBankCid:       "bafyreigenesisexamplecid",
            weights:             hex"deadbeef",
            decisionHistoryHash: keccak256("phase-1-decisions"),
            phaseStats:          hex"01020304"
        });
    }

    // -----------------------------------------------------------------------
    // Test 1 — die() flips lifeState to Dead, persists lastWords/deathBlock,
    //          forwards payload to TombstoneNFT, and returns the new tokenId.
    // -----------------------------------------------------------------------
    function test_DieHappyPath_PersistsStateAndMintsTombstone() public {
        _zeroBreath();
        AgentLifecycle.DeathPayload memory p = _samplePayload();

        vm.expectEmit(true, true, true, true);
        emit LifeStateTransitioned(AgentLifecycle.LifeState.Alive, AgentLifecycle.LifeState.Dead, 0);
        vm.expectEmit(true, true, true, true);
        emit AgentDied(p.lastWords, block.number);
        vm.expectEmit(true, true, true, true);
        emit TombstoneMinted(1, DEPLOYER, uint64(block.timestamp), uint8(p.cause), p.memoryBankCid);

        vm.prank(DEPLOYER);
        uint256 tokenId = al.die(p);

        assertEq(tokenId, 1, "first die() mints token id 1");
        assertEq(al.tombstoneTokenId(), tokenId, "AgentLifecycle stores tokenId");
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead));
        assertEq(al.lastWords(), p.lastWords, "lastWords persisted");
        assertEq(al.deathBlock(), block.number, "deathBlock snapshot");
        assertEq(tnft.ownerOf(tokenId), DEPLOYER, "owner of tombstone is agent owner");

        // Weights / phaseStats / decisionHistoryHash round-trip into the NFT.
        TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
        assertEq(t.weights,             p.weights,             "weights snapshot at moment of death");
        assertEq(t.decisionHistoryHash, p.decisionHistoryHash, "decisionHistoryHash snapshot");
        assertEq(t.phaseStats,          p.phaseStats,          "phaseStats snapshot");
        assertEq(t.lastWords,           p.lastWords);
        assertEq(t.memoryBankCid,       p.memoryBankCid);
        assertEq(uint8(t.deathCause),   uint8(p.cause));
        assertEq(t.terminalAfterglow,   p.terminalAfterglow);
        assertEq(t.breathAtDeath,       0, "breathAtDeath == 0 (die() precondition)");
    }

    // -----------------------------------------------------------------------
    // Test 2 — die() reverts NotDeadYet when EnergyController.breath() != 0.
    //          PRD §5.0: death precondition is breath==0.
    // -----------------------------------------------------------------------
    function test_RevertWhen_DieCalledWhileBreathNonZero() public {
        // No burn — breath is still at GENESIS_BREATH.
        assertGt(ec.breath(), 0, "precondition: breath > 0");

        AgentLifecycle.DeathPayload memory p = _samplePayload();
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.NotDeadYet.selector);
        al.die(p);

        // No mutation.
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive));
        assertEq(al.tombstoneTokenId(), 0);
        assertEq(tnft.nextTokenId(), 0);
    }

    function testFuzz_RevertWhen_DieCalledWithNonZeroBreath(uint256 burnAmount) public {
        burnAmount = bound(burnAmount, 0, GENESIS_BREATH - 1);
        if (burnAmount > 0) {
            vm.prank(DEPLOYER);
            ec.burnDecisionTax(burnAmount, "partial drain");
        }
        assertGt(ec.breath(), 0, "breath still > 0");

        AgentLifecycle.DeathPayload memory p = _samplePayload();
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.NotDeadYet.selector);
        al.die(p);
    }

    // -----------------------------------------------------------------------
    // Test 3 — sticky Dead: once die() lands, every mutator reverts
    //          AlreadyDead. Also covers double-die.
    // -----------------------------------------------------------------------
    function test_DieFlipsDeadFlagSticky() public {
        _zeroBreath();
        AgentLifecycle.DeathPayload memory p = _samplePayload();
        vm.prank(DEPLOYER);
        al.die(p);

        // Double-die reverts.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.die(p);

        // declareDeath also blocked.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.declareDeath("ghost", "");

        // recordDecision blocked.
        vm.prank(DEPLOYER);
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.recordDecision(hex"00", 1, int256(0));

        // pokeLifeState blocked.
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.pokeLifeState();

        // tombstoneTokenId stays at the first mint.
        assertEq(al.tombstoneTokenId(), 1);
        assertEq(tnft.nextTokenId(), 1);
    }

    // -----------------------------------------------------------------------
    // Test 4 — die() honours owner-only gate.
    // -----------------------------------------------------------------------
    function test_RevertWhen_DieCalledByIntruder() public {
        _zeroBreath();
        AgentLifecycle.DeathPayload memory p = _samplePayload();
        vm.prank(INTRUDER);
        vm.expectRevert(AgentLifecycle.NotOwner.selector);
        al.die(p);
    }

    // -----------------------------------------------------------------------
    // Test 5 — Weights snapshot is captured AT THE MOMENT of die(). Even if
    //          a different weights bytes appears in storage / memory after
    //          die() returns, the tombstone preserves what was passed in.
    // -----------------------------------------------------------------------
    function test_WeightsSnapshotAtMomentOfDeath() public {
        _zeroBreath();

        AgentLifecycle.DeathPayload memory p = _samplePayload();
        bytes memory immutableWeights = abi.encode(uint256(1), uint256(2), uint256(3));
        p.weights = immutableWeights;

        vm.prank(DEPLOYER);
        uint256 tokenId = al.die(p);

        // Tombstone has the EXACT bytes we passed.
        TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
        assertEq(t.weights, immutableWeights, "weights snapshot exact byte match");
        assertEq(t.weights.length, 32 * 3, "weights = abi.encode(uint256x3)");
    }

    // -----------------------------------------------------------------------
    // Test 6 — Degraded memoryBankCid (empty string) — die() still mints +
    //          emits the dedicated `TombstoneMintedWithoutMemoryBank` event.
    // -----------------------------------------------------------------------
    function test_DieWithEmptyMemoryBankCidEmitsDegradedEvent() public {
        _zeroBreath();
        AgentLifecycle.DeathPayload memory p = _samplePayload();
        p.memoryBankCid = "";

        vm.expectEmit(true, true, true, true);
        emit TombstoneMintedWithoutMemoryBank(1, DEPLOYER, uint64(block.timestamp));

        vm.prank(DEPLOYER);
        uint256 tokenId = al.die(p);

        assertEq(tokenId, 1, "mint still succeeds");
        TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
        assertEq(t.memoryBankCid, "", "empty cid persisted");
    }

    // -----------------------------------------------------------------------
    // Test 7 — All three PRD §6.11 DeathCause values forward verbatim.
    // -----------------------------------------------------------------------
    function test_DeathCauseForwardsAllThreeValues() public {
        TombstoneNFT.DeathCause[3] memory causes = [
            TombstoneNFT.DeathCause.TradingLoss,
            TombstoneNFT.DeathCause.Starvation,
            TombstoneNFT.DeathCause.Attrition
        ];

        // We need a fresh deployment per cause because each AgentLifecycle is
        // single-shot Dead.
        for (uint256 i; i < 3; ++i) {
            _resetStack();
            _zeroBreath();

            AgentLifecycle.DeathPayload memory p = _samplePayload();
            p.cause = causes[i];

            vm.prank(DEPLOYER);
            uint256 tokenId = al.die(p);
            TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
            assertEq(uint8(t.deathCause), uint8(causes[i]), "cause forwarded");
        }
    }

    function _resetStack() private {
        vm.startPrank(DEPLOYER);
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);
        al = new AgentLifecycle(address(ec));
        dlog = new DecisionLog(address(al));
        tnft = new TombstoneNFT(NAME, SYMBOL, address(al));
        al.setDecisionLog(address(dlog));
        al.setTombstoneNFT(address(tnft));
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // Test 8 — IAgentLifecycle interface points at the same die() function.
    //          Confirms the interface is wire-compatible with the contract.
    // -----------------------------------------------------------------------
    function test_IAgentLifecycleInterface_DieCompatible() public {
        _zeroBreath();
        AgentLifecycle.DeathPayload memory p = _samplePayload();

        IAgentLifecycle iface = IAgentLifecycle(address(al));
        vm.prank(DEPLOYER);
        uint256 tokenId = iface.die(p);

        assertEq(tokenId, 1);
        assertEq(uint8(iface.lifeState()), uint8(AgentLifecycle.LifeState.Dead));
        assertEq(iface.tombstoneTokenId(), tokenId);
        assertEq(address(iface.tombstoneNFT()), address(tnft));
    }

    // -----------------------------------------------------------------------
    // Test 9 — die() without TombstoneNFT wired: agent still dies (degraded
    //          path); returns tokenId=0; TombstoneMintSkipped event emitted.
    // -----------------------------------------------------------------------
    function test_DieWithoutTombstoneNFTWired_DeathSucceedsTokenIsZero() public {
        // Build a fresh stack WITHOUT calling setTombstoneNFT.
        vm.startPrank(DEPLOYER);
        EnergyController ec2 = new EnergyController();
        ec2.initialize(GENESIS_BREATH, MAX_BREATH, signer);
        AgentLifecycle al2 = new AgentLifecycle(address(ec2));
        DecisionLog dlog2 = new DecisionLog(address(al2));
        al2.setDecisionLog(address(dlog2));

        // Zero breath on the fresh stack.
        ec2.burnDecisionTax(GENESIS_BREATH, "drain");
        vm.stopPrank();

        AgentLifecycle.DeathPayload memory p = _samplePayload();
        vm.prank(DEPLOYER);
        uint256 tokenId = al2.die(p);

        assertEq(tokenId, 0, "no token minted when NFT not wired");
        assertEq(uint8(al2.lifeState()), uint8(AgentLifecycle.LifeState.Dead), "death still landed");
        assertEq(al2.tombstoneTokenId(), 0);
    }
}
