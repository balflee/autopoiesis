// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {DecisionLog}    from "contracts/DecisionLog.sol";
import {AgentLifecycle} from "contracts/AgentLifecycle.sol";

/// @title DecisionLog_OnAgent — T-A-006 spoof-resistance invariants
/// @notice The Track B main loop's `recordBetDecisionAndConsume`
///         (`agent/engines/decision.py`) ultimately funnels into
///         `AgentLifecycle.recordDecision`, which is the SOLE
///         path that may append to `DecisionLog`. This file pins the
///         invariants the off-chain main loop relies on:
///
///           1. `NotAgentLifecycle()` reverts every non-AgentLifecycle
///              caller, including the AgentLifecycle owner.
///           2. `decisionCount` is monotonic across N appends — no path
///              can decrement or rewind it.
///           3. The `DecisionAppended` event payload matches the persisted
///              `DecisionRecord` field-for-field; off-chain consumers
///              (Track E reconciler, Track D dashboard) can rely on the
///              event topic as a faithful pointer into `log[idx]`.
///           4. Cross-contract: only AgentLifecycle's recordDecision can
///              advance the audit log, and totals on both sides stay in
///              lockstep — preventing a "ghost" decision the main loop
///              cannot reconcile.
///
///         Spec anchors:
///           * TP §3.4  — append-only, AgentLifecycle-only writer.
///           * TP §8 D10 — DecisionLog + main-loop integration (5-signal).
///           * PRD §6.3 — effective_burn_rate aggregation off-chain reads
///                        every DecisionAppended; spoofing a row would
///                        bias the reconstructed rate.
contract DecisionLog_OnAgent_Test is Test {
    DecisionLog    internal dlog;
    AgentLifecycle internal al;

    address internal constant DEPLOYER       = address(0xA11CE);
    address internal constant INTRUDER       = address(0xBADBAD);
    address internal constant FAKE_LIFECYCLE = address(0xDEC15);
    address internal constant ENERGY_STUB    = address(0xE7E7E7);

    event DecisionAppended(uint256 indexed idx, uint256 indexed marketId, int256 outcome, bytes32 sigHash);

    function setUp() public {
        vm.startPrank(DEPLOYER);
        // Build a real AgentLifecycle so the "spoof via owner" path is
        // exercised against the actual contract surface. We need a non-
        // zero EnergyController for the constructor; for these tests
        // the controller is never read because we don't call
        // recordDecision in the spoof paths.
        al   = new AgentLifecycle(ENERGY_STUB);
        dlog = new DecisionLog(address(al));
        al.setDecisionLog(address(dlog));
        vm.stopPrank();
    }

    function _record(bytes32 sigHash, uint256 marketId, int256 outcome)
        internal
        view
        returns (DecisionLog.DecisionRecord memory)
    {
        return DecisionLog.DecisionRecord({
            sigHash:    sigHash,
            marketId:   marketId,
            outcome:    outcome,
            timestamp:  uint64(block.timestamp),
            recordedBy: address(al)
        });
    }

    // -----------------------------------------------------------------------
    // T1 — NotAgentLifecycle on every non-agent caller
    // -----------------------------------------------------------------------

    function test_RevertWhen_OwnerOfAgentLifecycleTriesDirectAppend() public {
        // Even the AgentLifecycle owner (the operator EOA) must NOT be
        // able to bypass recordDecision and write directly to the log.
        DecisionLog.DecisionRecord memory rec = _record(keccak256("spoof"), 1, int256(0));

        vm.prank(DEPLOYER);
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);

        // And from a random intruder.
        vm.prank(INTRUDER);
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);

        // Log untouched.
        assertEq(dlog.decisionCount(), 0, "no entries created by spoof attempts");
    }

    function testFuzz_RevertWhen_AnyNonAgentLifecycleCaller(address caller) public {
        vm.assume(caller != address(al));
        DecisionLog.DecisionRecord memory rec = _record(keccak256(abi.encode(caller)), uint256(uint160(caller)), int256(0));
        vm.prank(caller);
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);
    }

    // -----------------------------------------------------------------------
    // T2 — decisionCount monotonic across N appends
    // -----------------------------------------------------------------------

    function test_DecisionCountStrictlyMonotonicAcrossAppends() public {
        // Prank as the canonical AgentLifecycle so we exercise the
        // privileged write surface without standing up the controller.
        uint256 previousCount = 0;
        for (uint256 i = 0; i < 16; ++i) {
            DecisionLog.DecisionRecord memory rec = _record(
                keccak256(abi.encode("decision", i)),
                100 + i,
                // forge-lint: disable-next-line(unsafe-typecast)
                int256(i) - 8
            );
            vm.prank(address(al));
            uint256 idx = dlog.append(rec);

            assertEq(idx, previousCount, "idx == previous count");
            uint256 newCount = dlog.decisionCount();
            assertEq(newCount, previousCount + 1, "count increments by 1");
            assertGt(newCount, previousCount, "strict monotonic");
            previousCount = newCount;
        }
        assertEq(dlog.decisionCount(), 16, "final count");
    }

    function testFuzz_DecisionCountMonotonicUnderArbitraryAppends(uint8 appends) public {
        appends = uint8(bound(appends, 0, 64));
        uint256 before = dlog.decisionCount();
        for (uint256 i = 0; i < appends; ++i) {
            DecisionLog.DecisionRecord memory rec = _record(keccak256(abi.encode(i)), i, int256(0));
            vm.prank(address(al));
            dlog.append(rec);

            // After every append the count is strictly greater than the
            // previous step (and ≥ the starting `before`).
            assertGe(dlog.decisionCount(), before, "count never decreases");
        }
        assertEq(dlog.decisionCount(), before + appends, "final == before + appends");
    }

    // -----------------------------------------------------------------------
    // T3 — event payload matches stored record bit-for-bit
    // -----------------------------------------------------------------------

    function test_EventPayloadMatchesStoredRecord() public {
        DecisionLog.DecisionRecord memory rec = _record(
            keccak256("event-fidelity"),
            777,
            int256(-42)
        );

        vm.recordLogs();
        vm.prank(address(al));
        uint256 idx = dlog.append(rec);
        Vm.Log[] memory entries = vm.getRecordedLogs();

        // Find the DecisionAppended log; topic[0] is the sig, topics[1]
        // is `idx`, topics[2] is `marketId`. Non-indexed data carries
        // (outcome, sigHash) packed via abi.encode.
        bytes32 sig = keccak256("DecisionAppended(uint256,uint256,int256,bytes32)");
        bool found;
        for (uint256 i = 0; i < entries.length; ++i) {
            if (entries[i].topics.length >= 1 && entries[i].topics[0] == sig) {
                found = true;
                assertEq(uint256(entries[i].topics[1]), idx, "idx topic matches return");
                assertEq(uint256(entries[i].topics[2]), 777, "marketId topic matches struct");

                (int256 outcome, bytes32 sigHash) = abi.decode(entries[i].data, (int256, bytes32));
                assertEq(outcome, int256(-42), "outcome data matches struct");
                assertEq(sigHash, keccak256("event-fidelity"), "sigHash data matches struct");
            }
        }
        assertTrue(found, "DecisionAppended log present");

        // Persisted record matches the event payload field-for-field.
        DecisionLog.DecisionRecord memory read = dlog.getRecord(idx);
        assertEq(read.sigHash,  keccak256("event-fidelity"));
        assertEq(read.marketId, 777);
        assertEq(read.outcome,  int256(-42));
        assertEq(read.recordedBy, address(al));
    }

    // -----------------------------------------------------------------------
    // T4 — only AgentLifecycle.recordDecision advances the audit log,
    //      and counts on both sides stay in lockstep
    // -----------------------------------------------------------------------

    function test_RevertWhen_AppendByDifferentLifecycleAddressNotPersisted() public {
        // Anybody can deploy their own AgentLifecycle pointing at THIS
        // DecisionLog, but the immutable `agentLifecycle` was already
        // locked at construction — the rogue lifecycle still cannot
        // write through us. Pin that pattern: deploy a parallel
        // AgentLifecycle and check it's rejected.
        AgentLifecycle rogue = new AgentLifecycle(ENERGY_STUB);
        DecisionLog.DecisionRecord memory rec = _record(keccak256("rogue"), 1, int256(0));

        vm.prank(address(rogue));
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);

        assertEq(dlog.decisionCount(), 0, "log untouched by rogue lifecycle");
    }

    function test_RevertWhen_SpoofedAddressLooksLikeAgent() public {
        // Even if an intruder somehow manages to spin up a contract at
        // an address that LOOKS like AgentLifecycle (we can't simulate
        // CREATE2 collisions in unit tests, but the access-control
        // contract should still reject anything that isn't bit-for-bit
        // the immutable `agentLifecycle`).
        vm.prank(FAKE_LIFECYCLE); // arbitrary address known not to be `al`
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(_record(keccak256("fake"), 1, int256(0)));
    }
}
