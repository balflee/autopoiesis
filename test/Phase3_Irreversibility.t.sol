// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm}        from "forge-std/Test.sol";
import {PhaseManager}    from "contracts/PhaseManager.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {AgentLifecycle}  from "contracts/AgentLifecycle.sol";
import {DecisionLog}     from "contracts/DecisionLog.sol";
import {TombstoneNFT}    from "contracts/TombstoneNFT.sol";

/// @title  Phase3_Irreversibility -- T-A-009 trustlessness pin
/// @notice After `RenouncePhase3MutableRoles.s.sol` lands, the Phase 3
///         contracts MUST behave as "rules written into the contract --
///         even the project team cannot change them" (PRD §5.1.A). This
///         test file pins each surface the renounce script is supposed
///         to close, plus the death-flow surfaces the brief says MUST
///         remain bound to AgentLifecycle / Agent EOA.
///
///         Brief acceptance: explicitly attempts and asserts revert on:
///           (a) admin PhaseManager.advancePhase backward
///           (b) param tuner key-rotate on EnergyController
///           (c) tombstone mint from stranger address
///           (d) bypass die() precondition
///
///         Spec anchors:
///           * PRD §5.1.A  -- trustlessness; rules written into the contract.
///           * PRD §5.1.C  -- death flow: declareDeath + Tombstone mint.
///           * PRD §6.13   -- Phase 3 row continues to operate post-renounce.
///           * PRD §10     -- pause/upgrade roles auto-renounced on Phase 3.
///           * TP §8 D17   -- dress rehearsal in testnet.
contract Phase3_Irreversibility_Test is Test {
    PhaseManager     internal pm;
    EnergyController internal ec;
    AgentLifecycle   internal al;
    DecisionLog      internal dl;
    TombstoneNFT     internal tn;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);
    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH     = 12_000e6;
    uint256 internal constant SIGNER_PK      = 0xA11CE2D5;

    /// @notice Sets up a fully-deployed stack already locked for Phase 3:
    ///         pm.currentPhase == Adulthood AND pm.phase3Locked AND
    ///         ec.phase3Locked. This is the post-RenouncePhase3MutableRoles
    ///         state -- the state the project ships in.
    function setUp() public {
        vm.startPrank(DEPLOYER);

        // Full stack deploy mirroring DeployAll.s.sol order.
        ec = new EnergyController();
        ec.initialize(GENESIS_BREATH, MAX_BREATH, vm.addr(SIGNER_PK));
        pm = new PhaseManager();
        al = new AgentLifecycle(address(ec));
        dl = new DecisionLog(address(al));
        tn = new TombstoneNFT("Genesis", "GEN", address(al));
        al.setDecisionLog(address(dl));
        al.setTombstoneNFT(address(tn));

        // Climb to Adulthood and lock both contracts.
        pm.transitionToApprenticeship();
        pm.transitionToAdulthood();
        ec.setPhase(EnergyController.Phase.Adulthood);
        ec.lockPhase3();
        pm.lockPhase3();

        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // (a) admin PhaseManager.advancePhase backward -- reverts
    // -----------------------------------------------------------------------

    /// @notice Brief acceptance (a) -- after lock, transitionTo* reverts
    ///         Phase3IsLocked even from the legitimate owner. There is no
    ///         "advancePhase backward" function by design; the closest
    ///         "rewind" path is calling transitionToApprenticeship from
    ///         Adulthood, which must revert.
    function test_AdvancePhaseBackward_RevertsAfterLock() public {
        // From the legitimate owner, both transition paths revert.
        vm.startPrank(DEPLOYER);
        vm.expectRevert(PhaseManager.Phase3IsLocked.selector);
        pm.transitionToApprenticeship();

        vm.expectRevert(PhaseManager.Phase3IsLocked.selector);
        pm.transitionToAdulthood();

        // setOwner is the only other state-mutating admin path; pin
        // it too while we're inside the same prank.
        vm.expectRevert(PhaseManager.Phase3IsLocked.selector);
        pm.setOwner(INTRUDER);
        vm.stopPrank();

        // Non-owner: even before the lock check, NotOwner fires first.
        // Pin that intruder can never advance regardless of lock state.
        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.transitionToApprenticeship();

        // Phase pinned at Adulthood throughout.
        assertEq(uint8(pm.currentPhase()), uint8(PhaseManager.Phase.Adulthood), "phase pinned");
    }

    // -----------------------------------------------------------------------
    // (b) param tuner key-rotate on EnergyController -- reverts
    // -----------------------------------------------------------------------

    /// @notice Brief acceptance (b) -- after lock, setAttestationSigner
    ///         and the rest of the PARAM_TUNER surface reverts
    ///         Phase3IsLocked. Pin the full target list.
    function test_ParamTunerKeyRotation_RevertsAfterLock() public {
        address newSigner = address(0xDEADBEEF);

        vm.startPrank(DEPLOYER);
        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.setAttestationSigner(newSigner);

        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.setPhaseManager(address(pm));

        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.setPhase(EnergyController.Phase.Apprenticeship);
        vm.stopPrank();

        // Storage stays at the pre-lock signer / phase manager.
        assertEq(ec.attestationSigner(), vm.addr(SIGNER_PK), "signer pinned");
        assertEq(uint8(ec.currentPhase()), uint8(EnergyController.Phase.Adulthood), "phase pinned");
    }

    /// @notice DEPLOYER_ADMIN surface on EnergyController is also frozen:
    ///         setOwner / pause / unpause all revert after the lock.
    function test_DeployerAdmin_SurfaceFrozenAfterLock() public {
        vm.startPrank(DEPLOYER);
        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.setOwner(INTRUDER);

        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.pause();

        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.unpause();
        vm.stopPrank();

        assertEq(ec.owner(), DEPLOYER, "owner still deployer (locked)");
        assertFalse(ec.paused(), "paused flag unchanged");
    }

    // -----------------------------------------------------------------------
    // (c) tombstone mint from stranger address -- reverts
    // -----------------------------------------------------------------------

    /// @notice Brief acceptance (c) -- TombstoneNFT.mint reverts
    ///         NotAgentLifecycle for ANY caller other than the bound
    ///         AgentLifecycle. The lock state does not affect this --
    ///         immutability is enforced at construction. Pin it under
    ///         the locked Phase 3 to confirm no new bypass appeared.
    function test_TombstoneMint_FromStrangerReverts() public {
        // v33 backfill: adapt to TombstoneNFT v0.2.0 8-field struct (was 5-field).
        // This test asserts NotAgentLifecycle revert — payload field values are
        // irrelevant; defaults are sufficient.
        TombstoneNFT.Tombstone memory t = TombstoneNFT.Tombstone({
            weights:             hex"",
            decisionHistoryHash: bytes32(0),
            lastWords:           "spoofed",
            memoryBankCid:       "",
            deathCause:          TombstoneNFT.DeathCause.Attrition,
            terminalAfterglow:   false,
            breathAtDeath:       100,
            phaseStats:          hex""
        });

        // Stranger caller.
        vm.prank(INTRUDER);
        vm.expectRevert(TombstoneNFT.NotAgentLifecycle.selector);
        tn.mint(INTRUDER, t);

        // Even the legitimate AgentLifecycle owner (the Agent EOA) cannot
        // call tn.mint directly -- it must go through AgentLifecycle.
        vm.prank(DEPLOYER);
        vm.expectRevert(TombstoneNFT.NotAgentLifecycle.selector);
        tn.mint(DEPLOYER, t);

        // The mint authority remains the AgentLifecycle contract address.
        assertEq(tn.agentLifecycle(), address(al), "minter immutable");
        assertEq(tn.nextTokenId(), 0, "no token minted by stranger");
    }

    // -----------------------------------------------------------------------
    // (d) bypass die() precondition -- reverts
    // -----------------------------------------------------------------------

    /// @notice Brief acceptance (d) -- AgentLifecycle.declareDeath is
    ///         onlyOwner. A non-owner cannot trigger the death flow even
    ///         in the locked Phase 3 state. The death flow itself is
    ///         INTENTIONALLY preserved (AgentLifecycle.owner is the Agent
    ///         EOA, brief mandates AGENT_ROLE remains).
    function test_BypassDie_NonOwnerReverts() public {
        vm.prank(INTRUDER);
        vm.expectRevert(AgentLifecycle.NotOwner.selector);
        al.declareDeath("rogue", "");

        // Storage unchanged -- no LifeState advance happened.
        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Alive), "lifeState pinned");
        assertEq(bytes(al.lastWords()).length, 0, "no lastWords written");
        assertEq(al.deathBlock(), 0, "no deathBlock set");
    }

    /// @notice declareDeath remains callable by the legitimate Agent EOA
    ///         even under the locked Phase 3. Brief mandates preserving
    ///         AGENT_ROLE on DecisionLog (via AgentLifecycle) so the
    ///         Permadeath narrative completes.
    function test_DeathFlow_StillCallableByAgentInLockedPhase3() public {
        vm.prank(DEPLOYER);
        al.declareDeath("genesis ends here", "ipfs://memorybank");

        assertEq(uint8(al.lifeState()), uint8(AgentLifecycle.LifeState.Dead), "agent is Dead");
        assertEq(al.deathBlock(), block.number, "deathBlock captured");
        assertEq(tn.nextTokenId(), 1, "tombstone minted via AgentLifecycle");
    }

    // -----------------------------------------------------------------------
    // Lock semantics -- set-once / no-reverse / preserves operational paths
    // -----------------------------------------------------------------------

    /// @notice The lock itself is set-once on both contracts. Second call
    ///         reverts Phase3IsLocked even from the owner.
    function test_LockPhase3_CannotBeReversedOrRerun() public {
        vm.startPrank(DEPLOYER);
        vm.expectRevert(PhaseManager.Phase3IsLocked.selector);
        pm.lockPhase3();

        vm.expectRevert(EnergyController.Phase3IsLocked.selector);
        ec.lockPhase3();
        vm.stopPrank();

        assertTrue(pm.isPhase3Locked(), "PM stays locked");
        assertTrue(ec.isPhase3Locked(), "EC stays locked");
    }

    /// @notice The lock event payload (`Phase3RolesRenounced(uint64)`) is
    ///         the audit anchor reconcilers cross-reference against the
    ///         D17 dress-rehearsal log. Pin the topic-shape + payload
    ///         independent of when it fires.
    function test_Phase3RolesRenouncedEvent_PayloadShape() public {
        // Re-deploy a fresh stack so we can capture the events on lock.
        EnergyController ec2 = new EnergyController();
        ec2.initialize(GENESIS_BREATH, MAX_BREATH, vm.addr(SIGNER_PK));
        PhaseManager pm2 = new PhaseManager();
        pm2.transitionToApprenticeship();
        pm2.transitionToAdulthood();
        ec2.setPhase(EnergyController.Phase.Adulthood);

        vm.recordLogs();
        ec2.lockPhase3();
        pm2.lockPhase3();
        Vm.Log[] memory entries = vm.getRecordedLogs();

        bytes32 sig = keccak256("Phase3RolesRenounced(uint64)");
        uint256 count;
        for (uint256 i = 0; i < entries.length; ++i) {
            if (entries[i].topics.length == 1 && entries[i].topics[0] == sig) {
                uint64 lockedAt = abi.decode(entries[i].data, (uint64));
                // forge-lint: disable-next-line(block-timestamp)
                assertEq(lockedAt, uint64(block.timestamp), "lockedAt = block.timestamp");
                count++;
            }
        }
        // Two events expected: one from EnergyController, one from PhaseManager.
        assertEq(count, 2, "two renounce events fire");
    }

    /// @notice Operational paths on EnergyController remain callable after
    ///         the lock. Specifically the Phase-3 burn flow (the agent's
    ///         only path to apply ActionCost / Idle Decay) must not be
    ///         crippled by the renounce ritual.
    function test_OperationalPath_BurnDecisionTaxStillCallable() public {
        uint256 before = ec.breath();
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(2e6, "p3_post_lock_action_cost");
        assertEq(ec.breath(), before - 2e6, "operational burn still works after lock");
    }

    /// @notice Operational path: enterDesperateMode (Phase-3 only, the
    ///         Agent's on-chain anchor for the §6.9 trigger) remains
    ///         callable even though it's onlyOwner -- the renounce keeps
    ///         the OWNER role on PhaseManager bound to the Agent EOA.
    function test_OperationalPath_EnterDesperateModeStillCallable() public {
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(600_000, 2);
        assertTrue(pm.isDesperate(), "Desperate flip still works after lock");
    }
}
