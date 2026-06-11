// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {DecisionLog} from "contracts/DecisionLog.sol";

/// @title DecisionLogTest — T-A-003 unit coverage
/// @notice Confirms the append-only audit log accepts writes only from the
///         canonical AgentLifecycle address (set at construction, no setter).
contract DecisionLogTest is Test {
    DecisionLog internal dlog;

    // The fixture uses a plain EOA as the "agentLifecycle" so tests can
    // prank from it. Integration coverage exercises the real AgentLifecycle.
    address internal constant AGENT_LIFECYCLE = address(0xDEC15);
    address internal constant INTRUDER = address(0xBADBAD);

    event DecisionAppended(uint256 indexed idx, uint256 indexed marketId, int256 outcome, bytes32 sigHash);

    function setUp() public {
        dlog = new DecisionLog(AGENT_LIFECYCLE);
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
            recordedBy: AGENT_LIFECYCLE
        });
    }

    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    function test_ConstructorLocksAgentLifecycle() public view {
        assertEq(dlog.agentLifecycle(), AGENT_LIFECYCLE);
        assertEq(dlog.decisionCount(), 0);
    }

    function test_RevertWhen_ConstructWithZeroAddress() public {
        vm.expectRevert(DecisionLog.ZeroAddress.selector);
        new DecisionLog(address(0));
    }

    // -----------------------------------------------------------------------
    // Append — privileged write surface
    // -----------------------------------------------------------------------

    function test_AppendIncrementsLogAndEmits() public {
        DecisionLog.DecisionRecord memory rec = _record(keccak256("sig-1"), 42, int256(100));

        vm.expectEmit(true, true, true, true);
        emit DecisionAppended(0, 42, int256(100), keccak256("sig-1"));

        vm.prank(AGENT_LIFECYCLE);
        uint256 idx = dlog.append(rec);

        assertEq(idx, 0, "first idx is zero");
        assertEq(dlog.decisionCount(), 1, "count bumped");

        DecisionLog.DecisionRecord memory read = dlog.getRecord(0);
        assertEq(read.sigHash, keccak256("sig-1"));
        assertEq(read.marketId, 42);
        assertEq(read.outcome, int256(100));
        assertEq(read.recordedBy, AGENT_LIFECYCLE);
    }

    function test_AppendMultipleRecordsPreservesOrder() public {
        DecisionLog.DecisionRecord memory a = _record(keccak256("a"), 1, int256(10));
        DecisionLog.DecisionRecord memory b = _record(keccak256("b"), 2, int256(-5));
        DecisionLog.DecisionRecord memory c = _record(keccak256("c"), 3, int256(0));

        vm.startPrank(AGENT_LIFECYCLE);
        uint256 i0 = dlog.append(a);
        uint256 i1 = dlog.append(b);
        uint256 i2 = dlog.append(c);
        vm.stopPrank();

        assertEq(i0, 0);
        assertEq(i1, 1);
        assertEq(i2, 2);
        assertEq(dlog.decisionCount(), 3);

        assertEq(dlog.getRecord(0).sigHash, keccak256("a"));
        assertEq(dlog.getRecord(1).sigHash, keccak256("b"));
        assertEq(dlog.getRecord(2).sigHash, keccak256("c"));
    }

    function test_RevertWhen_AppendedByIntruder() public {
        DecisionLog.DecisionRecord memory rec = _record(bytes32(0), 1, int256(0));
        vm.prank(INTRUDER);
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);
    }

    function test_RevertWhen_GetRecordOutOfRange() public {
        vm.expectRevert(DecisionLog.IndexOutOfRange.selector);
        dlog.getRecord(0);

        DecisionLog.DecisionRecord memory rec = _record(bytes32(0), 1, int256(0));
        vm.prank(AGENT_LIFECYCLE);
        dlog.append(rec);

        vm.expectRevert(DecisionLog.IndexOutOfRange.selector);
        dlog.getRecord(1);
    }

    // -----------------------------------------------------------------------
    // Invariant — log can only grow
    // -----------------------------------------------------------------------

    function testFuzz_LogLengthMonotonic(uint8 appends) public {
        appends = uint8(bound(appends, 0, 32));
        uint256 before = dlog.decisionCount();
        for (uint256 i = 0; i < appends; ++i) {
            // casting to 'int256' is safe because i is bounded above by 32.
            // forge-lint: disable-next-line(unsafe-typecast)
            DecisionLog.DecisionRecord memory rec = _record(keccak256(abi.encode(i)), i, int256(i));
            vm.prank(AGENT_LIFECYCLE);
            dlog.append(rec);
        }
        assertEq(dlog.decisionCount(), before + appends);
        assertGe(dlog.decisionCount(), before, "log length monotonic");
    }

    function testFuzz_RevertWhen_IntruderAppends(address caller, uint256 marketId, int256 outcome) public {
        vm.assume(caller != AGENT_LIFECYCLE);
        DecisionLog.DecisionRecord memory rec = _record(keccak256(abi.encode(caller)), marketId, outcome);
        vm.prank(caller);
        vm.expectRevert(DecisionLog.NotAgentLifecycle.selector);
        dlog.append(rec);
    }
}
