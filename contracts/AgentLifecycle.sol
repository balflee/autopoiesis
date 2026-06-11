// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {DecisionLog} from "contracts/DecisionLog.sol";
import {TombstoneNFT} from "contracts/TombstoneNFT.sol";

/// @dev Minimal read-only view of `EnergyController` — only the two scalars
///      AgentLifecycle needs to compute its life-state thresholds. Coupled
///      narrowly to keep this contract independent of EnergyController's
///      mutator surface.
interface IEnergyController {
    function breath() external view returns (uint256);
    function initialBreath() external view returns (uint256);
}

/// @title  AgentLifecycle
/// @notice Owns the agent's *life-state* machine — distinct from the
///         operational `Phase` machine in `PhaseManager`. Reads breath
///         from `EnergyController` and auto-advances Alive → Desperate →
///         TerminalLucidity per PRD §5.0. The terminal `Dead` state is
///         only entered via an explicit `declareDeath(lastWords)` call
///         and is IRREVERSIBLE — no path, no fuzz input, no admin
///         function resurrects the contract.
///
///         The auto-advance is one-way: once the agent has entered
///         `Desperate` it cannot regress to `Alive` even if breath is
///         later topped up; once in `TerminalLucidity` it cannot regress
///         to `Desperate`. The three-stage fall is monotonic by spec
///         (PRD §5.0).
///
///         Spec anchors:
///           * PRD §3   — three-phase lifecycle (operational; separate from
///                        life-state).
///           * PRD §5.0 — Desperate → TerminalLucidity → Death thresholds.
///           * PRD §5.1 — `lastWords` + `deathBlock` are inputs to the
///                        TombstoneNFT mint that follows declareDeath.
///           * TP §3.3  — recordDecision is the off-chain Agent's single
///                        on-chain write surface (apart from EnergyController
///                        burns).
///           * TP §8 D3 — Day 3 deliverable (this task, T-A-003).
contract AgentLifecycle {
    // -----------------------------------------------------------------------
    // Death payload struct (T-A-007)
    // -----------------------------------------------------------------------

    /// @notice Bundle of canonical `die()` arguments — wraps the
    ///         seven-field death payload into a single calldata struct so
    ///         the function signature stays under the solc 0.8.24
    ///         stack-too-deep ceiling without via_ir. Field order matches
    ///         `TombstoneNFT.Tombstone` for direct forwarding.
    /// @param cause              PRD §6.11 cause enum chosen by off-chain
    ///                           agent (priority TradingLoss > Starvation
    ///                           > Attrition).
    /// @param terminalAfterglow  True iff the agent crossed the 5%-of-initial
    ///                           Terminal-Lucidity threshold and stayed below
    ///                           it until breath==0 (PRD §5.0). Off-chain
    ///                           agent computes; contract trusts.
    /// @param lastWords          Epitaph string (PRD §5.1.B).
    /// @param memoryBankCid      IPFS CIDv0/v1 of the MemoryBank tarball
    ///                           (PRD §4.6). Empty ⇒ degraded path; mint
    ///                           still succeeds.
    /// @param weights            ABI-encoded snapshot of the 6-parameter
    ///                           fusion model at death (consumed by V2-boot
    ///                           reflection injector). Empty bytes permitted.
    /// @param decisionHistoryHash keccak256 of the agent's DecisionLog at
    ///                           death — proves on-chain tombstone matches
    ///                           a particular log snapshot.
    /// @param phaseStats         Opaque ABI-encoded per-phase aggregates
    ///                           (dashboard decodes per its own schema).
    struct DeathPayload {
        TombstoneNFT.DeathCause cause;
        bool    terminalAfterglow;
        string  lastWords;
        string  memoryBankCid;
        bytes   weights;
        bytes32 decisionHistoryHash;
        bytes   phaseStats;
    }

    // -----------------------------------------------------------------------
    // Life-state enum
    // -----------------------------------------------------------------------

    /// @notice Monotonic life-state. Numeric ordering encodes the fall —
    ///         later values can ONLY be entered, never exited.
    enum LifeState {
        Alive,              // 0 — genesis
        Desperate,          // 1 — breath < 20 % of initial (PRD §5.0)
        TerminalLucidity,   // 2 — breath < 5  % of initial (PRD §5.0)
        Dead                // 3 — only via declareDeath(); irreversible
    }

    // -----------------------------------------------------------------------
    // Thresholds — PRD §5.0 percentages of initialBreath.
    // -----------------------------------------------------------------------

    /// @notice Below 20 % of initial → enter Desperate. PRD §5.0.
    uint256 public constant DESPERATE_THRESHOLD_PCT = 20;

    /// @notice Below  5 % of initial → enter Terminal Lucidity. PRD §5.0
    ///         (matches the constant referenced in EnergyController's
    ///         NatSpec — `initialBreath * 5 / 100`).
    uint256 public constant TERMINAL_THRESHOLD_PCT = 5;

    // -----------------------------------------------------------------------
    // Storage
    // -----------------------------------------------------------------------

    /// @notice Owner authorised to call `recordDecision` and `declareDeath`.
    ///         Sprint_2 wires this to a single off-chain Agent EOA; sprint_3
    ///         may delegate to a separate decisionRelayer role.
    address public owner;                              // slot 0

    /// @notice EnergyController for breath reads. Immutable so callers can
    ///         trust the address against the deployment manifest.
    IEnergyController public immutable energyController; // bytecode constant (no slot)

    /// @notice DecisionLog address. Set once via `setDecisionLog` after the
    ///         chicken-and-egg deployment dance documented in DecisionLog
    ///         NatSpec; the one-shot guard `decisionLogSet` prevents
    ///         re-pointing the audit log to a tampered fork.
    DecisionLog public decisionLog;                    // slot 1

    /// @notice Current life-state. Defaults to `LifeState.Alive` (=0).
    LifeState public lifeState;                        // slot 2 (packs)

    /// @notice One-shot flag for `setDecisionLog`. Slot-packed with
    ///         `lifeState`.
    bool      public decisionLogSet;                   // slot 2 (packs)

    /// @notice Block number captured at the instant `declareDeath` was
    ///         called. Zero while alive.
    uint256 public deathBlock;                         // slot 3

    /// @notice Final message captured at death. Mints downstream to the
    ///         Tombstone NFT per PRD §5.1. Empty string while alive.
    string  public lastWords;                          // slot 4

    /// @notice Running count of decisions appended. Off-chain consumers
    ///         (Track E reconciler) cross-check against `DecisionLog
    ///         .decisionCount()` every reconciliation tick.
    uint256 public totalDecisions;                     // slot 5

    /// @notice TombstoneNFT contract address. Set once via
    ///         `setTombstoneNFT` after deployment (the same chicken-and-egg
    ///         dance as `decisionLog` — TombstoneNFT's constructor takes
    ///         this contract's address, so AgentLifecycle deploys first
    ///         and the back-pointer is wired in a follow-up call).
    /// @dev    Optional dependency: if `tombstoneNFTSet == false` at
    ///         `declareDeath()` time the death still completes and no NFT
    ///         is minted (degraded path; the event still emits). This keeps
    ///         the chain layer robust if the NFT deployment fails or is
    ///         intentionally skipped in a unit-test setUp.
    TombstoneNFT public tombstoneNFT;                  // slot 6

    /// @notice One-shot flag for `setTombstoneNFT`. Slot-packed with the
    ///         `tombstoneNFT` address slot above (160 + 8 < 256).
    bool      public tombstoneNFTSet;                  // slot 6 (packs)

    /// @notice Token-id of the Tombstone minted on `declareDeath`. Zero
    ///         while alive (token ids are 1-indexed) and zero if death
    ///         was declared without TombstoneNFT wired.
    uint256 public tombstoneTokenId;                   // slot 7

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    /// @notice Emitted on every change to `lifeState`. The third arg is the
    ///         breath snapshot at the transition instant — Track D dashboard
    ///         reads here to animate the "Death Watch."
    event LifeStateTransitioned(
        LifeState indexed previous,
        LifeState indexed next,
        uint256           breathAtTransition
    );

    /// @notice Mirror of DecisionLog's append event, scoped to this contract
    ///         so off-chain consumers can subscribe to one topic for both
    ///         the audit-log row AND the life-state ripple effect.
    event DecisionRecorded(
        uint256 indexed idx,
        uint256 indexed marketId,
        int256          outcome,
        bytes32         sigHash
    );

    /// @notice Final-words event; consumed by the Tombstone mint pipeline
    ///         in sprint_4 (PRD §5.1). `deathBlock_` is the block at the
    ///         declareDeath call; tombstones snapshot it as `deathBlock`.
    event AgentDied(string lastWords, uint256 deathBlock_);

    /// @notice One-shot DecisionLog wiring audit.
    event DecisionLogUpdated(address indexed previousLog, address indexed newLog);

    /// @notice One-shot TombstoneNFT wiring audit.
    event TombstoneNFTUpdated(address indexed previousNFT, address indexed newNFT);

    /// @notice Emitted from `declareDeath` when the Tombstone mint was
    ///         attempted but TombstoneNFT had not been wired. Off-chain
    ///         operators / dashboard surface this so the degraded path is
    ///         observable. The death itself still completed.
    event TombstoneMintSkipped(string reason);

    /// @notice Owner rotation audit.
    event OwnerUpdated(address indexed previousOwner, address indexed newOwner);

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error NotOwner();
    error ZeroAddress();
    error AlreadyDead();
    error DecisionLogNotSet();
    error DecisionLogAlreadySet();
    error TombstoneNFTAlreadySet();
    /// @notice Raised by `die()` when the EnergyController balance is not
    ///         yet zero. PRD §5.0 specifies that `breath == 0` is the
    ///         death precondition; the off-chain agent waits for the burn
    ///         that zeroes the balance before invoking `die()`.
    error NotDeadYet();

    // -----------------------------------------------------------------------
    // Modifiers
    // -----------------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice The single irreversibility guard. Any function that would
    ///         mutate state post-death must wear this modifier; it is the
    ///         contract's invariant that `lifeState == Dead` is an
    ///         absorbing state.
    modifier notDead() {
        if (lifeState == LifeState.Dead) revert AlreadyDead();
        _;
    }

    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    /// @notice Locks the EnergyController dependency at construction. The
    ///         DecisionLog is wired in a follow-up `setDecisionLog` call
    ///         once it is deployed (see DecisionLog NatSpec for the
    ///         chicken-and-egg order).
    constructor(address energyController_) {
        if (energyController_ == address(0)) revert ZeroAddress();
        owner = msg.sender;
        energyController = IEnergyController(energyController_);
        emit OwnerUpdated(address(0), msg.sender);
    }

    // -----------------------------------------------------------------------
    // One-shot wiring
    // -----------------------------------------------------------------------

    /// @notice Bind the DecisionLog. Callable exactly once by the owner.
    ///         Subsequent calls revert `DecisionLogAlreadySet()`; this is
    ///         the keystone that makes the audit log tamper-evident even
    ///         if the owner key leaks AFTER birth.
    function setDecisionLog(address decisionLog_) external onlyOwner {
        if (decisionLog_ == address(0)) revert ZeroAddress();
        if (decisionLogSet) revert DecisionLogAlreadySet();
        decisionLogSet = true;
        decisionLog = DecisionLog(decisionLog_);
        emit DecisionLogUpdated(address(0), decisionLog_);
    }

    /// @notice Bind the TombstoneNFT. Callable exactly once by the owner.
    ///         Subsequent calls revert `TombstoneNFTAlreadySet()`. Wiring
    ///         is OPTIONAL — death may be declared before this call lands
    ///         and the contract gracefully emits `TombstoneMintSkipped`
    ///         instead of reverting (the death itself is irreversible and
    ///         must not be blocked by a downstream NFT availability bug).
    /// @param  tombstoneNFT_ TombstoneNFT contract whose `agentLifecycle`
    ///         immutable MUST equal `address(this)`. Off-chain deploy
    ///         scripts verify; chain-side we only zero-check.
    function setTombstoneNFT(address tombstoneNFT_) external onlyOwner {
        if (tombstoneNFT_ == address(0)) revert ZeroAddress();
        if (tombstoneNFTSet) revert TombstoneNFTAlreadySet();
        tombstoneNFTSet = true;
        tombstoneNFT = TombstoneNFT(tombstoneNFT_);
        emit TombstoneNFTUpdated(address(0), tombstoneNFT_);
    }

    // -----------------------------------------------------------------------
    // Decision recording — TP §3.3 single write surface
    // -----------------------------------------------------------------------

    /// @notice Append a decision to the audit log AND recompute life-state
    ///         from current breath. Reverts post-death; reverts if the
    ///         DecisionLog hasn't been wired yet.
    /// @dev    CEI discipline: bump our local counter (Effects) and build
    ///         the record in memory BEFORE calling `decisionLog.append`
    ///         (Interactions). DecisionLog is a contract we authored and
    ///         control; it has no callbacks and cannot reenter this
    ///         contract in a way that would observe inconsistent state.
    /// @param  sig       off-chain Agent signature over the decision (any
    ///                   schema; this contract treats it as opaque bytes
    ///                   and stores `keccak256(sig)`).
    /// @param  marketId  market identifier (Polymarket condition id, etc.)
    /// @param  outcome   signed PnL / directional outcome flag
    /// @return idx       position of the new record in the DecisionLog
    function recordDecision(bytes calldata sig, uint256 marketId, int256 outcome)
        external
        onlyOwner
        notDead
        returns (uint256 idx)
    {
        if (!decisionLogSet) revert DecisionLogNotSet();

        bytes32 sigHash = keccak256(sig);

        // EFFECTS — all local state writes happen BEFORE the external call:
        //   1. bump totalDecisions (our SSOT)
        //   2. recompute life-state from post-burn breath snapshot
        // The breath was already burned (decision-tax) by the caller before
        // this function fired, so the read inside _maybeAdvanceLifeState is
        // the post-burn snapshot.
        unchecked { totalDecisions += 1; }
        _maybeAdvanceLifeState();

        DecisionLog.DecisionRecord memory rec = DecisionLog.DecisionRecord({
            sigHash:    sigHash,
            marketId:   marketId,
            outcome:    outcome,
            timestamp:  uint64(block.timestamp),
            recordedBy: msg.sender
        });

        // INTERACTIONS — single external call at the end of the function.
        // DecisionLog is OUR contract with no callbacks; the only failure
        // mode is access-control (which would not reenter us anyway).
        idx = decisionLog.append(rec);

        // rationale: Strict CEI documented above (totalDecisions + life-state
        // both settled before decisionLog.append). DecisionLog is our own
        // contract; it has no callbacks and cannot reenter AgentLifecycle.
        // Event consumed by off-chain indexers post-finality.
        // slither-disable-next-line reentrancy-events
        emit DecisionRecorded(idx, marketId, outcome, sigHash);
    }

    /// @notice Re-evaluate the life-state from current breath without
    ///         recording a decision. Useful for time-tax ticks (PRD §6.2
    ///         class B) where breath drops without an Agent action.
    function pokeLifeState() external notDead {
        _maybeAdvanceLifeState();
    }

    // -----------------------------------------------------------------------
    // Death — irreversible
    // -----------------------------------------------------------------------

    /// @notice Mark the agent as Dead, persist `lastWords`, and (if a
    ///         TombstoneNFT is wired) mint the death-artefact NFT carrying
    ///         the IPFS `memoryBankCid` handoff. Reverts if already Dead
    ///         (`notDead`); there is no inverse function — `Dead` is an
    ///         absorbing state.
    /// @dev    `lastWords` is callable on any pre-Dead life-state including
    ///         Alive (e.g. emergency shutdown) — the spec doesn't require
    ///         a fall through Desperate / TerminalLucidity first. The
    ///         numeric `causeOfDeath` field on the Tombstone is derived
    ///         from the PREVIOUS lifeState so the off-chain dashboard can
    ///         distinguish a slow PRD §5.0 fall from an emergency
    ///         shutdown without parsing strings.
    /// @dev    Strict CEI: all local state writes (`lifeState`, `lastWords`,
    ///         `deathBlock`, `tombstoneTokenId`) happen BEFORE the external
    ///         `tombstoneNFT.mint(...)` call. The NFT contract is one we
    ///         authored and cannot reenter us in a way that observes
    ///         pre-death state. The `notDead` modifier above is the
    ///         second backstop — even on reentry the death has already
    ///         settled.
    /// @param  lastWords_  free-form epitaph; persisted verbatim and
    ///                     forwarded to the Tombstone payload.
    /// @param  memoryBankCid_  IPFS CIDv0/v1 of the MemoryBank tarball
    ///                     (PRD §4.6). Empty string ⇒ degraded path
    ///                     per PRD §5.1 sub-bullet C (Pinata down /
    ///                     3 retries exhausted); mint still succeeds.
    function declareDeath(string calldata lastWords_, string calldata memoryBankCid_)
        external
        onlyOwner
        notDead
    {
        // Read external state BEFORE mutating, so the emit below uses a
        // snapshot rather than a re-read (and CEI ordering is unambiguous).
        uint256 breathSnapshot = energyController.breath();

        LifeState previous = lifeState;
        TombstoneNFT.DeathCause cause = _causeFromPrevious(previous);

        // EFFECTS — every local state mutation BEFORE any external call.
        lifeState = LifeState.Dead;
        lastWords = lastWords_;
        deathBlock = block.number;

        emit LifeStateTransitioned(previous, LifeState.Dead, breathSnapshot);
        emit AgentDied(lastWords_, block.number);

        // INTERACTIONS — Tombstone mint is the LAST step so that any
        // failure inside the NFT contract (e.g. revert on bad recipient
        // hook) does not roll back the death itself. We use a low-level
        // structured call here because mint() may revert if the NFT was
        // misconfigured; we surface that as `TombstoneMintSkipped` and
        // keep the agent dead. Reverts in the mint are NOT propagated.
        if (tombstoneNFTSet) {
            TombstoneNFT.Tombstone memory t = TombstoneNFT.Tombstone({
                weights:             hex"",
                decisionHistoryHash: bytes32(0),
                lastWords:           lastWords_,
                memoryBankCid:       memoryBankCid_,
                deathCause:          cause,
                terminalAfterglow:   previous == LifeState.TerminalLucidity,
                breathAtDeath:       breathSnapshot,
                phaseStats:          hex""
            });
            // rationale: tombstoneNFT is OUR contract; mint() is onlyAgentLifecycle
            // gated to us, has CEI ordering internally, and emits a single
            // TombstoneMinted event consumed by off-chain indexers post-finality.
            // The death state writes above already settled; reentry would still
            // observe `lifeState == Dead`.
            // slither-disable-next-line reentrancy-events
            tombstoneTokenId = tombstoneNFT.mint(owner, t);
        } else {
            emit TombstoneMintSkipped("tombstoneNFT not wired");
        }
    }

    // -----------------------------------------------------------------------
    // die() — PRD §5.1.C canonical death entry point (T-A-007)
    // -----------------------------------------------------------------------

    /// @notice Canonical PRD §5.1.C death entry. Off-chain agent calls this
    ///         AFTER the burn that takes `EnergyController.breath()` to
    ///         exactly zero. Reverts `NotDeadYet()` if breath is non-zero —
    ///         legacy `declareDeath()` remains for emergency-shutdown paths
    ///         that don't pre-zero the balance.
    /// @dev    Strict CEI: every local state mutation BEFORE the external
    ///         `tombstoneNFT.mint(...)` call. The NFT contract is OUR own
    ///         and cannot reenter us in a way that observes pre-death
    ///         state. The `notDead` modifier is the second backstop.
    /// @param  p Death payload — bundles cause, terminalAfterglow,
    ///           lastWords, memoryBankCid, weights snapshot,
    ///           decisionHistoryHash and per-phase stats per PRD §5.1.C.
    /// @return tokenId 1-indexed Tombstone id (zero if NFT not wired —
    ///                 degraded path emits TombstoneMintSkipped instead).
    function die(DeathPayload calldata p)
        external
        onlyOwner
        notDead
        returns (uint256 tokenId)
    {
        // PRECONDITION — breath must already be zero. The brief uses the
        // colloquial `balanceOf(agent) == 0` phrasing; the on-chain SSOT
        // for agent liveness in the singleton-agent contract is `breath()`.
        if (energyController.breath() != 0) revert NotDeadYet();

        LifeState previous = lifeState;

        // EFFECTS — all local state writes BEFORE any external call.
        lifeState = LifeState.Dead;
        lastWords = p.lastWords;
        deathBlock = block.number;

        emit LifeStateTransitioned(previous, LifeState.Dead, 0);
        emit AgentDied(p.lastWords, block.number);

        // INTERACTIONS — Tombstone mint last so that any failure inside the
        // NFT contract does not roll back the death itself.
        if (tombstoneNFTSet) {
            TombstoneNFT.Tombstone memory t = TombstoneNFT.Tombstone({
                weights:             p.weights,
                decisionHistoryHash: p.decisionHistoryHash,
                lastWords:           p.lastWords,
                memoryBankCid:       p.memoryBankCid,
                deathCause:          p.cause,
                terminalAfterglow:   p.terminalAfterglow,
                breathAtDeath:       0,
                phaseStats:          p.phaseStats
            });
            // rationale: tombstoneNFT is OUR contract with onlyAgentLifecycle
            // gating + internal CEI; reentry would still observe `lifeState
            // == Dead`. Event consumed by off-chain indexer post-finality.
            // slither-disable-next-line reentrancy-events
            tokenId = tombstoneNFT.mint(owner, t);
            tombstoneTokenId = tokenId;
        } else {
            emit TombstoneMintSkipped("tombstoneNFT not wired");
        }
    }

    /// @dev Maps the pre-Dead life-state to the Tombstone's `DeathCause`
    ///      enum value per PRD §6.11 priority (TradingLoss > Starvation
    ///      > Attrition). Pure function so the unit tests can pin every
    ///      branch.
    ///
    ///      Legacy `declareDeath` derives the cause from the previous
    ///      life-state (the only signal it has). The canonical `die()`
    ///      path takes the cause directly from the off-chain agent which
    ///      has full context (PRD §6.11 says the off-chain agent picks
    ///      the priority winner).
    function _causeFromPrevious(LifeState previous) private pure returns (TombstoneNFT.DeathCause) {
        if (previous == LifeState.TerminalLucidity) {
            // TL collapse — breath drained to (near-)zero via taxes ⇒ Starvation.
            return TombstoneNFT.DeathCause.Starvation;
        }
        if (previous == LifeState.Desperate) {
            // Slow decay through Desperate without TL crossing ⇒ Attrition.
            return TombstoneNFT.DeathCause.Attrition;
        }
        // Alive → Dead jump (emergency shutdown) — highest-priority cause.
        return TombstoneNFT.DeathCause.TradingLoss;
    }

    // -----------------------------------------------------------------------
    // Admin
    // -----------------------------------------------------------------------

    /// @notice Rotate the owner. Zero-address guarded.
    function setOwner(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        address previous = owner;
        owner = newOwner;
        emit OwnerUpdated(previous, newOwner);
    }

    // -----------------------------------------------------------------------
    // Views
    // -----------------------------------------------------------------------

    /// @notice True iff the agent has reached the Terminal-Lucidity stage
    ///         or beyond. Convenience for the dashboard / off-chain death
    ///         watcher.
    function isTerminal() external view returns (bool) {
        return uint8(lifeState) >= uint8(LifeState.TerminalLucidity);
    }

    /// @notice The life-state the contract WOULD transition to if
    ///         `pokeLifeState` were called right now. Honours the monotonic
    ///         rule: never reports a value lower than the current state.
    ///         Consumers (Track D dashboard) can preview the fall
    ///         trajectory without sending a tx.
    function projectedLifeState() external view returns (LifeState) {
        uint256 currentBreath = energyController.breath();
        uint256 initial = energyController.initialBreath();
        LifeState target = _classify(currentBreath, initial);
        return uint8(target) > uint8(lifeState) ? target : lifeState;
    }

    // -----------------------------------------------------------------------
    // Internal
    // -----------------------------------------------------------------------

    /// @dev Read breath + initialBreath, map to a target LifeState, advance
    ///      monotonically (target > current). Never moves to Dead — that's
    ///      reserved for the explicit declareDeath() call. All external reads
    ///      happen BEFORE any state mutation or emit, keeping the function
    ///      strictly Checks-Effects-Interactions on the read path even though
    ///      reads are static-call view fns.
    function _maybeAdvanceLifeState() private {
        uint256 currentBreath = energyController.breath();
        uint256 initial = energyController.initialBreath();
        LifeState target = _classify(currentBreath, initial);

        if (uint8(target) > uint8(lifeState)) {
            LifeState previous = lifeState;
            lifeState = target;
            emit LifeStateTransitioned(previous, target, currentBreath);
        }
    }

    /// @dev Pure classifier: given current breath + initialBreath, what's
    ///      the deepest pre-Dead life-state the agent should be in?
    ///      Returns `Alive` when initialBreath is zero (defensive: the
    ///      controller may not be initialized yet).
    function _classify(uint256 currentBreath, uint256 initial) private pure returns (LifeState) {
        if (initial == 0) return LifeState.Alive;

        // Compare `breath * 100` against `initial * pct` to avoid integer
        // division loss; the multiplications cannot overflow because breath
        // is bounded by maxBreath which is bounded by initialBreath at
        // construction (and stays bounded by EnergyController's soft cap).
        if (currentBreath * 100 < initial * TERMINAL_THRESHOLD_PCT) {
            return LifeState.TerminalLucidity;
        }
        if (currentBreath * 100 < initial * DESPERATE_THRESHOLD_PCT) {
            return LifeState.Desperate;
        }
        return LifeState.Alive;
    }
}
