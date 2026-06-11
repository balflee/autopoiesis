// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AgentLifecycle} from "contracts/AgentLifecycle.sol";
import {TombstoneNFT} from "contracts/TombstoneNFT.sol";

/// @title  IAgentLifecycle — minimal external view of AgentLifecycle (T-A-007)
/// @notice Lean interface used by off-chain Python (Track B) + downstream
///         contracts when they want to depend only on the death-side surface
///         (PRD §5.1.C) without pulling the full AgentLifecycle storage
///         layout.
///
///         The interface intentionally re-exports both `die()` (the
///         canonical PRD §5.1.C entry per T-A-007 brief) and the legacy
///         `declareDeath()` so consumers can pick the right surface for
///         their use case:
///           * `die(DeathPayload)`  — call AFTER the burn that takes
///                                    breath to zero. Reverts NotDeadYet
///                                    if breath != 0. Returns the minted
///                                    Tombstone tokenId.
///           * `declareDeath(...)`  — legacy emergency-shutdown path; no
///                                    breath precondition. Cause derived
///                                    from previous life-state.
///
///         Spec anchors:
///           * PRD §5.1.C — Tombstone NFT mint flow + memoryBankCid handoff.
///           * PRD §6.11  — DeathCause priority (TradingLoss > Starvation
///                          > Attrition).
///           * TP §3.3    — single off-chain Agent write surface.
interface IAgentLifecycle {
    // -----------------------------------------------------------------------
    // PRD §5.1.C canonical death entry (T-A-007)
    // -----------------------------------------------------------------------

    /// @notice Mark the agent Dead AFTER breath has been driven to zero by
    ///         the upstream burn. The off-chain Agent assembles the full
    ///         payload (cause, afterglow flag, lastWords, IPFS CID, weights
    ///         snapshot, decision-history hash, phase stats) and triggers
    ///         the Tombstone mint inside the same transaction.
    /// @param  payload See AgentLifecycle.DeathPayload NatSpec.
    /// @return tokenId 1-indexed Tombstone NFT id (zero if TombstoneNFT not
    ///                 wired; degraded path emits TombstoneMintSkipped).
    function die(AgentLifecycle.DeathPayload calldata payload) external returns (uint256 tokenId);

    // -----------------------------------------------------------------------
    // Legacy emergency-shutdown entry — pre-existing surface (T-A-004)
    // -----------------------------------------------------------------------

    /// @notice Legacy emergency-shutdown death path. Does NOT require
    ///         breath==0. Cause derived from previous life-state.
    function declareDeath(string calldata lastWords_, string calldata memoryBankCid_) external;

    // -----------------------------------------------------------------------
    // Death-side views
    // -----------------------------------------------------------------------

    function lifeState() external view returns (AgentLifecycle.LifeState);
    function lastWords() external view returns (string memory);
    function deathBlock() external view returns (uint256);
    function tombstoneTokenId() external view returns (uint256);
    function tombstoneNFT() external view returns (TombstoneNFT);
    function isTerminal() external view returns (bool);
}
