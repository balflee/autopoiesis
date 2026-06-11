// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {AgentLifecycle}   from "contracts/AgentLifecycle.sol";
import {DecisionLog}      from "contracts/DecisionLog.sol";
import {TombstoneNFT}     from "contracts/TombstoneNFT.sol";

/// @title  E2EDeathTest — T-A-004 cross-contract death chain
/// @notice Walks the canonical death sequence end-to-end and asserts the
///         event-order invariant from the T-A-004 acceptance criteria:
///
///             BurnExecuted        (EnergyController.EnergyChanged)
///         →   AgentLifecycle      (LifeStateTransitioned)
///         →   Dead                (AgentLifecycle.AgentDied)
///         →   TombstoneMinted     (TombstoneNFT.TombstoneMinted)
///
///         The brief uses "BurnExecuted" colloquially — the canonical
///         emission for a burn on EnergyController is `EnergyChanged`.
///         We assert that as the leading event in the sequence. See
///         delivery_report.md for the naming reconciliation note.
contract E2EDeathTest is Test {
    EnergyController internal ec;
    AgentLifecycle   internal al;
    DecisionLog      internal dlog;
    TombstoneNFT     internal tnft;

    address internal constant DEPLOYER = address(0xA11CE);
    uint256 internal constant SIGNER_PK = 0xA11CE2D5;
    address internal signer;

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;

    string internal constant NAME    = "Genesis Tombstone";
    string internal constant SYMBOL  = "GTOMB";

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.startPrank(DEPLOYER);

        // Deployment order locked by chicken-and-egg of immutable refs:
        //   1. EnergyController (no peers)
        //   2. AgentLifecycle  (needs EC)
        //   3. DecisionLog     (needs AL)
        //   4. TombstoneNFT    (needs AL)
        //   5. wire AL → DL, AL → TNFT
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
    // Acceptance criterion: end-to-end death chain emits the expected
    // events IN ORDER. We record logs across the entire transaction
    // boundary so the assertion is robust to extra interstitial events.
    // -----------------------------------------------------------------------
    function test_DeathChainEventOrderMatchesAcceptance() public {
        // ----- Phase A: drain BREATH to <5% of initial (TerminalLucidity).
        uint256 toBurn = GENESIS_BREATH - (GENESIS_BREATH * 4) / 100;

        vm.recordLogs();
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(toBurn, "drain-to-terminal");

        Vm.Log[] memory burnLogs = vm.getRecordedLogs();
        // EnergyChanged is the canonical "BurnExecuted" event (brief naming).
        bytes32 ENERGY_CHANGED = keccak256("EnergyChanged(uint256,uint256,string)");
        bool sawBurn;
        for (uint256 i; i < burnLogs.length; i++) {
            if (burnLogs[i].topics[0] == ENERGY_CHANGED) { sawBurn = true; break; }
        }
        assertTrue(sawBurn, "step 1: BurnExecuted (EnergyChanged) emitted by EC");

        // ----- Phase B: poke lifecycle to ratchet to TerminalLucidity.
        vm.recordLogs();
        vm.prank(DEPLOYER);
        al.pokeLifeState();
        Vm.Log[] memory pokeLogs = vm.getRecordedLogs();

        bytes32 LIFE_STATE_TRANSITIONED =
            keccak256("LifeStateTransitioned(uint8,uint8,uint256)");
        bool sawTransition;
        for (uint256 i; i < pokeLogs.length; i++) {
            if (pokeLogs[i].topics[0] == LIFE_STATE_TRANSITIONED) {
                sawTransition = true;
                // topic[2] is the indexed `next` LifeState; should equal 2 (TerminalLucidity).
                assertEq(uint256(pokeLogs[i].topics[2]), uint256(uint8(AgentLifecycle.LifeState.TerminalLucidity)));
                break;
            }
        }
        assertTrue(sawTransition, "step 2: AgentLifecycle state advance emitted");
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.TerminalLucidity));

        // ----- Phase C: declareDeath — the SAME tx emits both AgentDied
        //                and TombstoneMinted; we capture and verify
        //                strict ordering between those two events.
        string memory words = "the dataset is enough";
        string memory cid   = "bafyreigenesisexamplecid";

        vm.recordLogs();
        vm.prank(DEPLOYER);
        al.declareDeath(words, cid);
        Vm.Log[] memory deathLogs = vm.getRecordedLogs();

        bytes32 AGENT_DIED       = keccak256("AgentDied(string,uint256)");
        bytes32 TOMBSTONE_MINTED = keccak256("TombstoneMinted(uint256,address,uint64,uint8,string)");
        // v0.2.0 — degraded path emits an extra `TombstoneMintedWithoutMemoryBank` when
        // the CID is empty. This test passes a non-empty CID so the event should NOT fire.
        bytes32 TOMBSTONE_NO_CID = keccak256("TombstoneMintedWithoutMemoryBank(uint256,address,uint64)");

        // Use uint sentinels (max-uint = "not found"). All log indices are < 2^256-1 by construction.
        uint256 idxAgentDied       = type(uint256).max;
        uint256 idxTombstoneMinted = type(uint256).max;
        uint256 idxDeadTransition  = type(uint256).max;
        for (uint256 i; i < deathLogs.length; i++) {
            bytes32 sig = deathLogs[i].topics[0];
            if (sig == LIFE_STATE_TRANSITIONED) {
                // The Dead-bound transition has topics[2] == 3 (LifeState.Dead).
                if (uint256(deathLogs[i].topics[2]) == uint256(uint8(AgentLifecycle.LifeState.Dead))) {
                    idxDeadTransition = i;
                }
            } else if (sig == AGENT_DIED) {
                idxAgentDied = i;
            } else if (sig == TOMBSTONE_MINTED) {
                idxTombstoneMinted = i;
            }
        }
        assertLt(idxDeadTransition,  type(uint256).max, "step 3a: LifeStateTransitioned to Dead present");
        assertLt(idxAgentDied,       type(uint256).max, "step 3b: AgentDied present");
        assertLt(idxTombstoneMinted, type(uint256).max, "step 4: TombstoneMinted present");

        // Canonical ordering: Dead transition  <  AgentDied  <  TombstoneMinted
        assertLt(idxDeadTransition,   idxAgentDied,       "Dead transition precedes AgentDied");
        assertLt(idxAgentDied,        idxTombstoneMinted, "AgentDied precedes TombstoneMinted");

        // Happy-path CID supplied ⇒ NO degraded event.
        for (uint256 i; i < deathLogs.length; i++) {
            assertTrue(deathLogs[i].topics[0] != TOMBSTONE_NO_CID, "no degraded event when CID present");
        }
    }

    // -----------------------------------------------------------------------
    // The Tombstone NFT carries the correct payload: deathTs, breathAtDeath,
    // lastWords, memoryBankCid, and the derived causeOfDeath (TerminalLucidity
    // here because we ratcheted before declaring death).
    // -----------------------------------------------------------------------
    function test_TombstonePayloadMirrorsAgentStateAtDeath() public {
        uint256 toBurn = GENESIS_BREATH - (GENESIS_BREATH * 4) / 100;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(toBurn, "drain");
        vm.prank(DEPLOYER);
        al.pokeLifeState();

        string memory words = "i tried";
        string memory cid   = "bafyreilineagev1";
        uint256 breathBefore = ec.breath();

        vm.prank(DEPLOYER);
        al.declareDeath(words, cid);

        uint256 tokenId = al.tombstoneTokenId();
        assertEq(tokenId, 1, "first mint -> id=1");
        assertEq(tnft.ownerOf(tokenId), DEPLOYER, "mint to agent owner");

        TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
        // v0.2.0 — `deathTs` is no longer a struct field; carried only in
        // the `TombstoneMinted` event payload. We assert event presence
        // separately above; here we only check the persistent struct.
        assertEq(t.breathAtDeath,  breathBefore, "breathAtDeath snapshot");
        assertEq(t.lastWords,      words);
        assertEq(t.memoryBankCid,  cid);
        // Prior life-state was TerminalLucidity => PRD 6.11 Starvation.
        assertEq(uint8(t.deathCause), uint8(TombstoneNFT.DeathCause.Starvation));
        assertTrue(t.terminalAfterglow, "TL-Dead carries terminalAfterglow=true");
    }

    // -----------------------------------------------------------------------
    // Cause-of-death derivation matches the pre-Dead lifeState. We do not
    // need to walk all three branches end-to-end; the Desperate fall-through
    // is the most realistic intermediate path.
    // -----------------------------------------------------------------------
    function test_CauseOfDeathIsDesperateWhenPreviouslyDesperate() public {
        // Drain to <20% but >=5% (Desperate).
        uint256 toBurn = GENESIS_BREATH - (GENESIS_BREATH * 19) / 100;
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(toBurn, "drain-to-desperate");
        vm.prank(DEPLOYER);
        al.pokeLifeState();
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Desperate));

        vm.prank(DEPLOYER);
        al.declareDeath("desperate end", "");

        uint256 tokenId = al.tombstoneTokenId();
        TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
        // PRD 6.11 - Desperate->Dead without TL crossing => Attrition.
        assertEq(uint8(t.deathCause), uint8(TombstoneNFT.DeathCause.Attrition));
        assertFalse(t.terminalAfterglow, "Desperate path: no afterglow");
    }

    function test_CauseOfDeathIsEmergencyWhenAliveAtDeath() public {
        // No drain — declare death from Alive (emergency shutdown).
        vm.prank(DEPLOYER);
        al.declareDeath("emergency shutdown", "");

        uint256 tokenId = al.tombstoneTokenId();
        TombstoneNFT.Tombstone memory t = tnft.getTombstone(tokenId);
        // PRD 6.11 - Alive->Dead emergency => TradingLoss (highest priority).
        assertEq(uint8(t.deathCause), uint8(TombstoneNFT.DeathCause.TradingLoss));
    }

    // -----------------------------------------------------------------------
    // Degraded path: declaring death without the NFT wired emits
    // TombstoneMintSkipped and leaves the agent dead with tombstoneTokenId
    // == 0. PRD §5.1 mandates death cannot be blocked by NFT availability.
    // -----------------------------------------------------------------------
    function test_DegradedPath_TombstoneNotWired_DeathStillSucceeds() public {
        // Fresh stack WITHOUT calling setTombstoneNFT.
        vm.startPrank(DEPLOYER);
        EnergyController ec2 = new EnergyController();
        ec2.initialize(GENESIS_BREATH, MAX_BREATH, signer);
        AgentLifecycle al2 = new AgentLifecycle(address(ec2));
        DecisionLog dlog2 = new DecisionLog(address(al2));
        al2.setDecisionLog(address(dlog2));

        vm.recordLogs();
        al2.declareDeath("no nft", "");
        Vm.Log[] memory logs = vm.getRecordedLogs();
        vm.stopPrank();

        bytes32 SKIPPED = keccak256("TombstoneMintSkipped(string)");
        bool sawSkip;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == SKIPPED) { sawSkip = true; break; }
        }
        assertTrue(sawSkip, "degraded path emits TombstoneMintSkipped");
        assertEq(uint8(al2.lifeState()), uint8(AgentLifecycle.LifeState.Dead), "death still settled");
        assertEq(al2.tombstoneTokenId(), 0, "no token minted");
    }

    // -----------------------------------------------------------------------
    // Cross-contract access control: a non-AgentLifecycle caller cannot
    // mint a Tombstone even by directly invoking TombstoneNFT.
    // -----------------------------------------------------------------------
    function test_TombstoneMintRejectsDirectCallerEvenAfterDeath() public {
        vm.prank(DEPLOYER);
        al.declareDeath("legitimate", "cid-legit");

        // Even DEPLOYER (the agent owner) cannot mint a second Tombstone
        // bypassing the lifecycle.
        TombstoneNFT.Tombstone memory ghost = TombstoneNFT.Tombstone({
            weights:             hex"",
            decisionHistoryHash: bytes32(0),
            lastWords:           "ghost",
            memoryBankCid:       "ghost-cid",
            deathCause:          TombstoneNFT.DeathCause.TradingLoss,
            terminalAfterglow:   false,
            breathAtDeath:       0,
            phaseStats:          hex""
        });
        vm.prank(DEPLOYER);
        vm.expectRevert(TombstoneNFT.NotAgentLifecycle.selector);
        tnft.mint(DEPLOYER, ghost);
    }

    // -----------------------------------------------------------------------
    // Once death is declared the AgentLifecycle locks: any subsequent
    // declareDeath reverts AlreadyDead — no double-mint, even with a
    // different memoryBankCid.
    // -----------------------------------------------------------------------
    function test_RevertWhen_DeclareDeathTwiceWithDifferentCid() public {
        vm.startPrank(DEPLOYER);
        al.declareDeath("first", "cid-1");
        vm.expectRevert(AgentLifecycle.AlreadyDead.selector);
        al.declareDeath("second", "cid-2");
        vm.stopPrank();

        assertEq(al.tombstoneTokenId(), 1, "only one mint");
        assertEq(tnft.nextTokenId(), 1);
    }
}
