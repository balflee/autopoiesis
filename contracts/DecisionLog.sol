// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title  DecisionLog
/// @notice Append-only on-chain audit trail of every Agent decision per
///         TECHNICAL_PLAN §3.4. The single privileged writer is the
///         `AgentLifecycle` contract whose address is locked at
///         construction — no setter, no rotation, no upgrade. Any other
///         caller reverts `NotAgentLifecycle()`.
///
///         Storage philosophy: the full off-chain `sig` is hashed to
///         `bytes32 sigHash` before being persisted; the on-chain log
///         records the *commitment* to the decision attestation, not the
///         signature blob itself (gas + privacy). Off-chain consumers
///         (Track B journal, Track E reconciler, Track D dashboard) keep
///         the raw `sig` indexed by `(marketId, sigHash)`.
///
///         Spec anchors:
///           * PRD §6   — every decision burns BREATH; this log lets the
///                        reconciler reconstruct effective_burn_rate.
///           * TP §3.4  — append-only, AgentLifecycle-only writer.
///           * TP §8 D3 — Day 3 deliverable (this task, T-A-003).
contract DecisionLog {
    // -----------------------------------------------------------------------
    // Types
    // -----------------------------------------------------------------------

    /// @notice One row of the audit trail. Tightly packed — `timestamp`
    ///         compressed to uint64 (good until year ~5.85e11) and
    ///         `recordedBy` is whichever EOA initiated the call into
    ///         AgentLifecycle.recordDecision (typically the agent owner /
    ///         relayer key — useful forensic breadcrumb).
    /// @dev    Field order MUST match `.dev/contracts/decision_log_abi.v0.1.0.json`.
    struct DecisionRecord {
        bytes32 sigHash;       // keccak256(off-chain sig blob)
        uint256 marketId;      // Polymarket condition id (or local market index)
        int256  outcome;       // signed PnL or directional outcome flag
        uint64  timestamp;     // uint64(block.timestamp) at append
        address recordedBy;    // tx caller into AgentLifecycle.recordDecision (forensics)
    }

    // -----------------------------------------------------------------------
    // Storage
    // -----------------------------------------------------------------------

    /// @notice The privileged writer. Locked at construction; no setter.
    address public immutable agentLifecycle;

    /// @notice Append-only public log. `log(idx)` is the auto-generated
    ///         tuple getter; structured access is via `getRecord`.
    DecisionRecord[] public log;

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    /// @notice Emitted on every append. Off-chain indexers (Track E)
    ///         subscribe here for ordering rather than polling `log.length`.
    event DecisionAppended(
        uint256 indexed idx,
        uint256 indexed marketId,
        int256          outcome,
        bytes32         sigHash
    );

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error NotAgentLifecycle();
    error ZeroAddress();
    error IndexOutOfRange();

    // -----------------------------------------------------------------------
    // Modifiers
    // -----------------------------------------------------------------------

    modifier onlyAgentLifecycle() {
        if (msg.sender != agentLifecycle) revert NotAgentLifecycle();
        _;
    }

    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    /// @notice Locks `agentLifecycle` at construction; no rotation.
    /// @dev    Deployment order: AgentLifecycle first → DecisionLog second
    ///         with AgentLifecycle's address → AgentLifecycle.setDecisionLog
    ///         (one-shot) to close the cycle.
    constructor(address agentLifecycle_) {
        if (agentLifecycle_ == address(0)) revert ZeroAddress();
        agentLifecycle = agentLifecycle_;
    }

    // -----------------------------------------------------------------------
    // Append — the only mutator.
    // -----------------------------------------------------------------------

    /// @notice Append a decision. Reverts unless caller is the canonical
    ///         `agentLifecycle`. Returns the index at which the record was
    ///         written.
    /// @param  rec the decision record (caller MUST set `recordedBy` to
    ///         `msg.sender` for forensic correctness; this contract trusts
    ///         the caller field-for-field because the access check above
    ///         already restricts the caller set to one address).
    /// @return idx zero-based position of the new record in `log`.
    function append(DecisionRecord calldata rec)
        external
        onlyAgentLifecycle
        returns (uint256 idx)
    {
        idx = log.length;
        log.push(rec);
        emit DecisionAppended(idx, rec.marketId, rec.outcome, rec.sigHash);
    }

    // -----------------------------------------------------------------------
    // Views
    // -----------------------------------------------------------------------

    /// @notice Structured read of one record. Reverts on out-of-range
    ///         `idx` rather than returning a zero-value tuple, so callers
    ///         cannot mistake "no record" for a real entry.
    function getRecord(uint256 idx) external view returns (DecisionRecord memory) {
        if (idx >= log.length) revert IndexOutOfRange();
        return log[idx];
    }

    /// @notice Length of the log. Equivalent to `log.length` but ABI-stable
    ///         (the array's auto-generated getter does NOT expose length).
    function decisionCount() external view returns (uint256) {
        return log.length;
    }
}
