// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title  PhaseManager
/// @notice Owns the three-phase lifecycle state machine for the Genesis
///         Experiment per PRD §3 and TECHNICAL_PLAN §3.2. The agent advances
///         unidirectionally Childhood → Apprenticeship → Adulthood — no
///         skips, no rewinds, no resurrection — and every other contract
///         that gates behaviour by phase reads through this contract's
///         `currentPhase` view (or subscribes to `PhaseTransitioned`).
///
///         Phase-3 (Adulthood) irreversibility — renouncing the admin /
///         param-tuner roles on this contract — lands in sprint_5 T-A-009
///         per TECHNICAL_PLAN §8 D17. Once the operator calls `lockPhase3`
///         (only callable in Adulthood, only by the owner, set-once), the
///         admin paths (`setOwner`, `transitionToApprenticeship`,
///         `transitionToAdulthood`) revert `Phase3IsLocked` forever. The
///         operational path `enterDesperateMode` is intentionally LEFT
///         OPEN — Desperate Mode is a Phase-3 operational mechanic the
///         Agent EOA owner needs to flip on observation (PRD §6.9).
///         Locked state is purely a one-way valve; there is no
///         `unlockPhase3` by design (PRD §5.1.A trustlessness).
///
///         Sprint_5 (T-A-008) adds the **Desperate Mode** anchor (PRD §6.9):
///         a STICKY, ONE-WAY flag the off-chain agent flips via
///         `enterDesperateMode` after observing `pressure ≥ 0.5` for two
///         consecutive decision cycles in Phase 3. Flipping the bit:
///           * Lifts `maxBreathRiskPct()` from 30% → 50% (basis points).
///           * BLOCKS `EnergyController.deepenBreath()` per PRD §6.7 (the
///             off-chain reader checks `isDesperate()`).
///           * Surfaces `DesperateModeEntered` for the dashboard red-palette
///             switch (T-D-004) and the off-chain weight-updater (T-B-009).
///         The flag CANNOT be cleared — PRD §6.9 marks Desperate Mode
///         irreversible — and there is intentionally no `clearDesperateMode`
///         admin path. The flag is only meaningful in Phase 3.
///
///         Spec anchors:
///           * PRD §3   — unidirectional Childhood (Phase 1) → Apprenticeship
///                        (Phase 2) → Adulthood (Phase 3).
///           * PRD §6.7  — Lung Expansion 触发条件 require `非绝境`; once
///                        Desperate, `deepenBreath` is blocked.
///           * PRD §6.9 — Desperate Mode trigger (`pressure ≥ 0.5` × 2 cycles
///                        in Phase 3); behaviour `β/ρ 解锁, lr 2×, 下注上限
///                        30%→50%, 扩肺禁用`; explicitly irreversible.
///           * PRD §6.13 — phase-segmented activation table; Phase 3 enables
///                        Desperate/Terminal/Starvation. Phase 2 does NOT.
///           * TP §3.2  — PhaseManager is the authority on the Phase enum;
///                        EnergyController/AgentLifecycle read here.
///           * TP §4.7 / §8 D16 — Desperate Mode on-chain anchor.
// rationale: IPhaseManagerDesperateView is a one-way consumer-local
// interface defined inside EnergyController.sol for narrow coupling;
// having PhaseManager formally inherit it would back-couple the producer
// to a header that lives in the consumer's file. The function `isDesperate`
// IS implemented here and the wire shape matches by design — slither's
// detector flags the implicit conformance correctly.
// slither-disable-next-line missing-inheritance
contract PhaseManager {
    // -----------------------------------------------------------------------
    // Phase enum — canonical, three-state.
    // -----------------------------------------------------------------------

    /// @notice Phase ordering is LOCKED. Adding intermediates here is a
    ///         MAJOR ABI bump per `.dev/contracts/_registry.json`.
    enum Phase {
        Childhood,        // 0 — genesis state at construction
        Apprenticeship,   // 1 — first promotion
        Adulthood         // 2 — terminal; irreversible by spec (PRD §3)
    }

    // -----------------------------------------------------------------------
    // Constants — Desperate Mode parameters (PRD §6.9)
    // -----------------------------------------------------------------------

    /// @notice Minimum number of consecutive decision cycles `pressure ≥ 0.5`
    ///         must hold before `enterDesperateMode` accepts the flip. The
    ///         actual pressure window is computed off-chain (T-B-009); the
    ///         agent passes the cycle count it observed for audit.
    uint256 public constant MIN_PRESSURE_CYCLES = 2;

    /// @notice Normal-mode max BREATH-risk-per-bet cap (basis points). Read
    ///         by the off-chain Kelly sizer (T-B-009) BEFORE Desperate Mode.
    ///         PRD §6.9: cap moves 30% → 50% on Desperate entry.
    uint256 public constant MAX_BREATH_RISK_PCT_NORMAL = 3000;

    /// @notice Desperate-mode max BREATH-risk-per-bet cap (basis points).
    ///         Sticky for the lifetime of the run once `enterDesperateMode`
    ///         succeeds. PRD §6.9.
    uint256 public constant MAX_BREATH_RISK_PCT_DESPERATE = 5000;

    // -----------------------------------------------------------------------
    // Storage — slot order is part of the ABI.
    //
    //   slot 0 : owner            (address, 20 bytes)
    //   slot 1 : currentPhase     (Phase enum / uint8, 1 byte)
    //          + desperateMode    (bool,           1 byte)   — slot-packed
    //
    // Layout note: `desperateMode` is appended to slot 1 (same slot as
    // `currentPhase`) so the v0.1.0 storage layout for `owner` and
    // `currentPhase` remains byte-identical. Reviewers verifying upgrade
    // safety should diff `forge inspect PhaseManager storageLayout`
    // between v0.1.0 and v0.2.0.
    // -----------------------------------------------------------------------

    /// @notice Owner authorised to mutate the phase and to flip Desperate
    ///         Mode. Sprint_2 keeps a single EOA / deploy script as owner;
    ///         the same EOA is the off-chain Agent's signing key (PRD §6.9
    ///         calls the caller of `enterDesperateMode` "the Agent"). The
    ///         renounce ritual in sprint_4 D17 transfers `owner` to
    ///         `address(0)` AFTER the Adulthood transition.
    address public owner;                  // slot 0

    /// @notice Active lifecycle phase. Defaults to `Phase.Childhood` (=0)
    ///         on construction.
    Phase   public currentPhase;           // slot 1 (low byte)

    /// @notice STICKY Desperate Mode flag. PRD §6.9: once flipped, cannot
    ///         be cleared by any path — there is no `clearDesperateMode`
    ///         admin function by design. Read by the off-chain Kelly sizer
    ///         (T-B-009) and by `EnergyController.deepenBreath` (T-A-008).
    bool    public desperateMode;          // slot 1 (next byte, slot-packed)

    /// @notice STICKY Phase-3 admin lock. T-A-009 / TECHNICAL_PLAN §8 D17:
    ///         once the operator calls `lockPhase3()` in Adulthood, every
    ///         admin / param-tuner entry point reverts `Phase3IsLocked`.
    ///         There is intentionally NO `unlockPhase3` — PRD §5.1.A says
    ///         "规则写进合约，连项目方都不能改 — trustless." Slot-packed
    ///         with `currentPhase` + `desperateMode` (3 bytes used).
    bool    public phase3Locked;           // slot 1 (next byte, slot-packed)

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    /// @notice Emitted on every successful phase transition. Field order
    ///         matches the v0.1.0 ABI: (old phase, next phase, caller).
    event PhaseTransitioned(Phase indexed oldPhase, Phase indexed newPhase, address indexed by);

    /// @notice Owner rotation audit. Final renounce in sprint_4 will emit
    ///         `OwnerUpdated(prev, address(0))`.
    event OwnerUpdated(address indexed previousOwner, address indexed newOwner);

    /// @notice Emitted exactly once, on the successful flip of `desperateMode`.
    ///         `pressureAtEntry` is the scaled pressure value the agent
    ///         observed (off-chain convention: 1e6 = 1.0); `cyclesHeld` is
    ///         the number of consecutive cycles the agent saw the trigger
    ///         hold. Both fields are audit-only — the contract does not
    ///         re-derive pressure on-chain. Consumed by Track D's
    ///         DeathWatch (red palette) and Track B's weight_updater
    ///         (desperate branch).
    event DesperateModeEntered(uint256 pressureAtEntry, uint256 cyclesHeld);

    /// @notice Emitted exactly once on the successful Phase-3 admin lock.
    ///         T-A-009 / TP §8 D17. The `lockedAt` block-timestamp is the
    ///         audit anchor reconcilers cross-reference against the D17
    ///         dress-rehearsal log. After this event fires from PhaseManager
    ///         AND from EnergyController (sibling event), the operator key
    ///         no longer controls protocol state — PRD §5.1.A.
    event Phase3RolesRenounced(uint64 lockedAt);

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error NotOwner();
    error InvalidTransition();
    error ZeroAddress();

    /// @notice `enterDesperateMode` called outside Phase 3 (Adulthood). PRD
    ///         §6.13 reserves Desperate Mode for Adulthood only.
    error WrongPhase();

    /// @notice `enterDesperateMode` called with `cyclesHeld < MIN_PRESSURE_CYCLES`.
    ///         PRD §6.9 requires the trigger to hold for ≥2 cycles before
    ///         the flag flips.
    error NotEnoughPressureCycles();

    /// @notice `enterDesperateMode` called when `desperateMode == true`.
    ///         PRD §6.9 marks the flag irreversible AND set-once; the second
    ///         call is a logic bug in the off-chain caller and must revert
    ///         loudly rather than silently no-op.
    error AlreadyDesperate();

    /// @notice An admin / param-tuner path was called after `lockPhase3` had
    ///         already fired. PRD §5.1.A trustlessness — no recovery path.
    error Phase3IsLocked();

    // -----------------------------------------------------------------------
    // Modifiers
    // -----------------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice Reverts unless the contract is in exactly `expected`. Used by
    ///         transition entry points to enforce strict source-state.
    modifier onlyInPhase(Phase expected) {
        if (currentPhase != expected) revert InvalidTransition();
        _;
    }

    /// @notice Reverts when the Phase-3 admin lock has fired. Gates every
    ///         admin / param-tuner entry point (NOT operational paths such
    ///         as `enterDesperateMode`, which remain callable by the
    ///         Agent EOA owner per PRD §6.9 + §6.13 Phase-3 row).
    modifier whenNotPhase3Locked() {
        if (phase3Locked) revert Phase3IsLocked();
        _;
    }

    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    /// @notice Records deployer as owner. `currentPhase` defaults to
    ///         `Phase.Childhood` (the zero value); `desperateMode` defaults
    ///         to `false`.
    constructor() {
        owner = msg.sender;
        emit OwnerUpdated(address(0), msg.sender);
    }

    // -----------------------------------------------------------------------
    // Transitions — unidirectional per PRD §3.
    // -----------------------------------------------------------------------

    /// @notice Promote from Childhood → Apprenticeship. Reverts unless the
    ///         contract is in `Phase.Childhood`. Reverts `Phase3IsLocked`
    ///         after the admin lock — defensive belt-and-braces over the
    ///         already-unidirectional state machine. Modifier order is
    ///         deliberate: `onlyOwner` first (security), then the lock
    ///         check (Phase 3 trustlessness), then the phase guard
    ///         (normal-state shape) so a locked Phase-3 deployment reverts
    ///         `Phase3IsLocked` rather than `InvalidTransition`.
    function transitionToApprenticeship() external onlyOwner whenNotPhase3Locked onlyInPhase(Phase.Childhood) {
        currentPhase = Phase.Apprenticeship;
        emit PhaseTransitioned(Phase.Childhood, Phase.Apprenticeship, msg.sender);
    }

    /// @notice Promote from Apprenticeship → Adulthood. Reverts unless the
    ///         contract is in `Phase.Apprenticeship` — explicit two-step
    ///         climb forbids skipping the apprenticeship. Reverts
    ///         `Phase3IsLocked` after the admin lock. Same modifier order
    ///         as `transitionToApprenticeship` (see comment above).
    function transitionToAdulthood() external onlyOwner whenNotPhase3Locked onlyInPhase(Phase.Apprenticeship) {
        currentPhase = Phase.Adulthood;
        emit PhaseTransitioned(Phase.Apprenticeship, Phase.Adulthood, msg.sender);
    }

    // -----------------------------------------------------------------------
    // Desperate Mode — PRD §6.9
    // -----------------------------------------------------------------------

    /// @notice Flip the STICKY Desperate Mode bit. PRD §6.9 trigger
    ///         (`pressure ≥ 0.5` for ≥2 consecutive cycles in Phase 3) is
    ///         computed off-chain by the agent main loop (T-B-009); this
    ///         function is the on-chain anchor that:
    ///           1. Enforces the Phase-3 (Adulthood) gate.
    ///           2. Sanity-checks `cyclesHeld ≥ MIN_PRESSURE_CYCLES` so a
    ///              buggy caller cannot flip the bit on the first dip.
    ///           3. Enforces set-once semantics (no second call).
    ///           4. Lifts `maxBreathRiskPct()` from 30% → 50% (the off-chain
    ///              Kelly sizer reads this view).
    ///           5. Surfaces `DesperateModeEntered` for the dashboard +
    ///              weight-updater subscribers.
    ///
    /// @dev    There is intentionally NO companion `clearDesperateMode`
    ///         function — PRD §6.9 marks the state irreversible for the
    ///         lifetime of the run. The function does NOT touch
    ///         `currentPhase` (Adulthood stays Adulthood) or any decision-
    ///         cycle window (Phase 3 stays at the spec'd 45-min cycle per
    ///         PRD §6.13). Reentrancy: no external calls, no value flow.
    ///
    /// @param  pressureAtEntry  off-chain pressure observation at flip
    ///                          (audit-only; convention 1e6 = 1.0)
    /// @param  cyclesHeld       number of consecutive cycles the trigger
    ///                          held; MUST be ≥ MIN_PRESSURE_CYCLES (2)
    function enterDesperateMode(uint256 pressureAtEntry, uint256 cyclesHeld) external onlyOwner {
        if (currentPhase != Phase.Adulthood) revert WrongPhase();
        if (cyclesHeld < MIN_PRESSURE_CYCLES) revert NotEnoughPressureCycles();
        if (desperateMode) revert AlreadyDesperate();

        desperateMode = true;
        emit DesperateModeEntered(pressureAtEntry, cyclesHeld);
    }

    // -----------------------------------------------------------------------
    // Views — Desperate Mode read surface
    // -----------------------------------------------------------------------

    /// @notice Sticky Desperate Mode flag. `EnergyController.deepenBreath`
    ///         reads here to enforce the PRD §6.7 lung-expansion lockout;
    ///         off-chain consumers (T-B-009 weight updater, T-D-004 Death
    ///         Watch UI) subscribe to `DesperateModeEntered` and may also
    ///         poll this view on reconnect.
    function isDesperate() external view returns (bool) {
        return desperateMode;
    }

    /// @notice Max BREATH-risk-per-bet cap, expressed in basis points
    ///         (1e4 = 100%). Returns `MAX_BREATH_RISK_PCT_NORMAL` (3000 =
    ///         30%) by default and `MAX_BREATH_RISK_PCT_DESPERATE` (5000 =
    ///         50%) once `desperateMode` is set. PRD §6.9: the cap is the
    ///         only on-chain numeric the Desperate flip changes; β/ρ
    ///         coefficients and the 2× learning rate are off-chain (T-B-009).
    function maxBreathRiskPct() external view returns (uint256) {
        return desperateMode ? MAX_BREATH_RISK_PCT_DESPERATE : MAX_BREATH_RISK_PCT_NORMAL;
    }

    // -----------------------------------------------------------------------
    // Admin
    // -----------------------------------------------------------------------

    /// @notice Rotate the owner. Sprint_5 T-A-009 added the
    ///         `whenNotPhase3Locked` guard — after the operator runs
    ///         `lockPhase3()` the owner can no longer be rotated, freezing
    ///         the key-rotation channel forever (PRD §5.1.A). The
    ///         non-zero guard remains in place because the renounce
    ///         ritual is to LOCK (not zero the address), keeping
    ///         `owner` as a useful audit field after the lock fires.
    function setOwner(address newOwner) external onlyOwner whenNotPhase3Locked {
        if (newOwner == address(0)) revert ZeroAddress();
        address previous = owner;
        owner = newOwner;
        emit OwnerUpdated(previous, newOwner);
    }

    // -----------------------------------------------------------------------
    // Phase-3 admin lock — TP §8 D17 / T-A-009
    // -----------------------------------------------------------------------

    /// @notice Permanently renounce the admin / param-tuner channel on this
    ///         contract. Set-once; no `unlockPhase3` exists by design (PRD
    ///         §5.1.A trustlessness). The lock fires `Phase3RolesRenounced`
    ///         with the block timestamp as audit anchor so the D17 dress-
    ///         rehearsal log can be cross-checked against the on-chain
    ///         event.
    /// @dev    Preconditions:
    ///           * Caller MUST be the current `owner` (sprint_5 owner == the
    ///             deployer / operator EOA).
    ///           * Contract MUST already be in `Phase.Adulthood` — locking
    ///             from Childhood/Apprenticeship would brick the contract
    ///             before the unidirectional climb completes.
    ///           * `phase3Locked` MUST be false — the `whenNotPhase3Locked`
    ///             modifier enforces set-once.
    ///         Post-condition: `phase3Locked == true` and every admin path
    ///         (`setOwner`, both `transitionTo*`) reverts `Phase3IsLocked`.
    ///         `enterDesperateMode` is INTENTIONALLY unchanged — it is the
    ///         Agent EOA's operational write surface in Phase 3 per PRD
    ///         §6.9.
    function lockPhase3() external onlyOwner whenNotPhase3Locked {
        if (currentPhase != Phase.Adulthood) revert WrongPhase();
        phase3Locked = true;
        emit Phase3RolesRenounced(uint64(block.timestamp));
    }

    /// @notice Convenience view mirroring `phase3Locked` storage. Lets
    ///         off-chain consumers (renounce-ritual smoke test, dashboard)
    ///         poll a stable selector instead of the auto-generated
    ///         storage getter, matching the style of `isDesperate()`.
    function isPhase3Locked() external view returns (bool) {
        return phase3Locked;
    }
}
