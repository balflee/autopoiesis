// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {EIP712Settlement} from "contracts/lib/EIP712Settlement.sol";

/// @dev Minimal read-only view of `PhaseManager` — only the single bit
///      `EnergyController.deepenBreath` needs to enforce the PRD §6.7
///      lung-expansion lockout. Coupling narrowly keeps this contract
///      independent of PhaseManager's mutator surface.
interface IPhaseManagerDesperateView {
    function isDesperate() external view returns (bool);
}

/// @title  EnergyController v3.1
/// @notice On-chain anchor for the Genesis Experiment BREATH life-balance.
///         This sprint ships the full v3.1 economic surface from PRD §6 and
///         TECHNICAL_PLAN §3.1: dual-account state (`breath` vs `bankroll`),
///         the three burn classes (PRD §6.2: decision-tax, time-tax,
///         market-loss), donation top-up, soft-cap math (PRD §6.7), and the
///         EIP-712 settlement attestation surface (TP §3.7).
///
///         Phase-3 irreversibility (renounce pause/admin roles on phase
///         entry) is OUT OF SCOPE this round — see sprint_4 D17 / PRD §10.
///
///         Spec anchors:
///           * PRD §6.0      — single survival scalar; `breath == 0 ⇒ Dead`.
///           * PRD §6.1      — BREATH ≠ bankroll; bankroll losses may FUND
///                             a BREATH burn but BREATH may never fund the
///                             bankroll.
///           * PRD §6.2      — three burn classes (decision-tax / time-tax /
///                             market-loss); each MUST be a distinct entry
///                             point so analytics can attribute attrition.
///           * PRD §6.3      — `effective_burn_rate` is the sum of all three
///                             paths over a tick; this contract emits one
///                             `EnergyChanged` per path so callers can sum
///                             off-chain rather than encoding the formula
///                             on-chain.
///           * PRD §6.7      — soft cap + lung-expansion: top-ups clamp at
///                             `maxBreath`; the truncated delta is reported
///                             via `SoftCapDeflected`.
///           * PRD §10       — Phase 3 will renounce the pause role
///                             (sprint_4 scope; not implemented here).
///           * PRD §15       — pragma 0.8.24 LOCKED.
///           * TP §3.1       — canonical contract; every other contract
///                             reads through `totalBreath()` / `bankroll()`.
///           * TP §3.7       — EIP-712 attestation gates every off-chain →
///                             on-chain settlement; replay-protected via
///                             per-signer nonce + chainId in domain.
contract EnergyController {
    // -----------------------------------------------------------------------
    // Lifecycle enum (locked at sprint_1)
    // -----------------------------------------------------------------------

    /// @notice Phase order is canonical — `PhaseManager` + `AgentLifecycle`
    ///         depend on it. See `.dev/contracts/energy_controller_abi.v0.2.0.json`.
    enum Phase {
        Childhood,
        Apprenticeship,
        Adulthood,
        Dead
    }

    // -----------------------------------------------------------------------
    // Storage — slot order LOCKED to remain compatible with v0.1.0 skeleton.
    // -----------------------------------------------------------------------

    /// @notice Sprint_2 owner is the deployment script; sprint_4 will replace
    ///         this with role-based auth and renounce admin on Phase-3 entry.
    address public owner;                       // slot 0

    /// @notice BREATH balance — the single life scalar (1e6 fixed point).
    ///         PRD §6.0: `breath == 0` ⇒ permadeath.
    uint256 public breath;                      // slot 1

    /// @notice Genesis-time injection — locked once by `initialize`.
    ///         Terminal-Lucidity threshold is `initialBreath * 5 / 100`
    ///         (TP §3.1, sprint_4).
    uint256 public initialBreath;               // slot 2

    /// @notice Soft cap (PRD §6.7). Top-ups clamp here; deepening the cap
    ///         is a sprint_4 ritual (`lungExpansion`).
    uint256 public maxBreath;                   // slot 3

    /// @notice Current lifecycle phase. Sprint_2 keeps this owner-writable
    ///         via `setPhase`; sprint_3 hands the mutator to `PhaseManager`.
    Phase   public currentPhase;                // slot 4 (packs with `initialized`)

    /// @notice Re-init guard; set true on first `initialize` call.
    bool    public initialized;                 // slot 4

    /// @notice USDC-denominated risk capital (1e6 precision). PRD §6.1 forbids
    ///         crediting `breath` from `bankroll` directly — both accounts
    ///         move independently and the off-chain reconciler reads both.
    uint256 public bankroll;                    // slot 5

    /// @notice Off-chain signer authorised to mint `SettlementAttestation`s.
    ///         Owner may rotate via `setAttestationSigner`. TP §3.7.
    address public attestationSigner;           // slot 6 (packs with `paused`)

    /// @notice Pausable for Phase 1/2 emergency stop. PRD §10 / TP §8 D17
    ///         require the pause role to auto-renounce on Phase 3 entry —
    ///         enforced here by `whenNotPhase3Locked` on `pause()` and
    ///         `unpause()`. The flag itself is preserved (the contract may
    ///         still be queried `paused()`) but no future toggle is
    ///         possible after `lockPhase3()` fires.
    bool    public paused;                      // slot 6

    /// @notice STICKY Phase-3 admin lock (T-A-009 / TP §8 D17). Once
    ///         `lockPhase3()` fires every admin / param-tuner entry point
    ///         reverts `Phase3IsLocked`. Operational paths (burns, top-up,
    ///         bankroll moves, lung expansion, settlement) remain callable
    ///         by the owner (Agent EOA in Phase 3). Slot-packed with
    ///         `attestationSigner` + `paused` (20 + 1 + 1 = 22 bytes of
    ///         slot 6 used; 10 bytes free).
    bool    public phase3Locked;                // slot 6

    /// @notice `usedNonces[signer][nonce]` — true once consumed.
    ///         Per-signer monotonic counter is OFF-CHAIN (TP §3.7); the
    ///         on-chain check is the cheap "have-we-seen-this" bit.
    mapping(address signer => mapping(uint256 nonce => bool used)) public usedNonces;

    /// @notice PhaseManager reference for the Desperate-Mode read used by
    ///         `deepenBreath` (PRD §6.7 lung-expansion lockout). Sprint_5
    ///         owner-settable via `setPhaseManager`; if unset, `deepenBreath`
    ///         falls back to the legacy "no Desperate gate" behaviour so the
    ///         existing sprint_2-4 deploy script does not need to wire the
    ///         link before raising `maxBreath`. Once set, Desperate Mode
    ///         (PRD §6.9) BLOCKS further lung expansion.
    IPhaseManagerDesperateView public phaseManager; // slot 7+ (after mapping seed)

    // -----------------------------------------------------------------------
    // Immutables — EIP-712 domain components are cached in code, not storage.
    // -----------------------------------------------------------------------

    /// @notice keccak256("Genesis Experiment EnergyController")
    bytes32 private immutable _DOMAIN_NAME_HASH;
    /// @notice keccak256("1") — domain version. Bump if the typehash changes.
    bytes32 private immutable _DOMAIN_VERSION_HASH;
    /// @notice chainId at deployment; if the chain forks we re-derive via
    ///         `block.chainid` at verify time and tolerate either separator.
    uint256 private immutable _DEPLOY_CHAIN_ID;
    /// @notice Pre-computed separator for the common case.
    bytes32 private immutable _CACHED_DOMAIN_SEPARATOR;

    // -----------------------------------------------------------------------
    // Events — TP §3.1 BREATH event family.
    // -----------------------------------------------------------------------

    /// @notice Emitted on every mutation to `breath`. `reason` is a short
    ///         ASCII tag — analytics groups burns by reason to recompute
    ///         PRD §6.3 effective_burn_rate off-chain.
    event EnergyChanged(uint256 oldBreath, uint256 newBreath, string reason);

    /// @notice Emitted on every mutation to `bankroll`. Bankroll moves
    ///         independently of breath per PRD §6.1.
    event BankrollMutated(uint256 oldBankroll, uint256 newBankroll, string reason);

    /// @notice Owner rotation audit trail.
    event OwnerUpdated(address indexed previousOwner, address indexed newOwner);

    /// @notice One-shot init audit; emitted exactly once per deployment.
    event Initialized(uint256 initialBreath, uint256 maxBreath, address owner, address attestationSigner);

    /// @notice Signer rotation audit; consumed by the off-chain attester.
    event AttestationSignerUpdated(address indexed previousSigner, address indexed newSigner);

    /// @notice Pause / unpause audit; sprint_4 Phase-3 entry permanently
    ///         renounces the mutator.
    event Paused(address indexed by);
    event Unpaused(address indexed by);

    /// @notice Emitted when a `topUpBreath` would have raised `breath` above
    ///         `maxBreath`; `applied` is the actual delta credited and
    ///         `dropped = attempted - applied` is the soft-cap deflection
    ///         (PRD §6.7).
    event SoftCapDeflected(uint256 attempted, uint256 cap, uint256 applied);

    /// @notice Emitted when `settleMarketLoss` consumes an EIP-712 attestation.
    event MarketLossSettled(address indexed signer, uint256 indexed marketId, uint256 lossAmount, uint256 nonce);

    /// @notice Sprint_2 keeps `setPhase` owner-gated; emit so subscribers
    ///         (dashboard, PhaseManager once it lands) can react.
    event PhaseChanged(Phase indexed previous, Phase indexed next);

    /// @notice Emitted when the owner rotates the PhaseManager pointer used
    ///         by `deepenBreath` for the Desperate-Mode guard (PRD §6.7).
    event PhaseManagerUpdated(address indexed previousPhaseManager, address indexed newPhaseManager);

    /// @notice Emitted when `deepenBreath` raises the soft cap (PRD §6.7
    ///         Lung Expansion). `oldMaxBreath` is the cap before the call;
    ///         `newMaxBreath` is the new cap. Burn-rate analytics may use
    ///         this to recompute `projected_hours` post-expansion.
    event MaxBreathDeepened(uint256 oldMaxBreath, uint256 newMaxBreath);

    /// @notice Emitted exactly once on the successful Phase-3 admin lock
    ///         (T-A-009 / TP §8 D17). The `lockedAt` block-timestamp is the
    ///         audit anchor — the renounce-ritual smoke test cross-checks
    ///         this against the sibling event on `PhaseManager`.
    event Phase3RolesRenounced(uint64 lockedAt);

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error NotOwner();
    error AlreadyInitialized();
    error NotInitialized();
    error ZeroAddress();
    error ZeroBreath();
    error ZeroAmount();
    error WhilePaused();
    error InsufficientBreath();
    error InsufficientBankroll();
    error AlreadyDead();
    error InvalidSignature();
    error InvalidSigner();
    error NonceUsed();
    error DeadlineExpired();
    error AttestationMismatch();
    error InvalidPhase();

    /// @notice `deepenBreath` called while `phaseManager.isDesperate()`
    ///         returned true. PRD §6.7: lung expansion is one of the
    ///         actions Desperate Mode disables.
    error LungExpansionBlockedDesperate();

    /// @notice `deepenBreath` called with `newMaxBreath <= maxBreath`. The
    ///         function is one-way (cap may only rise); calls that would
    ///         leave the cap unchanged or LOWER it are a logic bug.
    error InvalidLungExpansion();

    /// @notice An admin / param-tuner path was called after `lockPhase3`
    ///         had already fired. PRD §5.1.A trustlessness — no recovery
    ///         path; the only remedy is redeploy.
    error Phase3IsLocked();

    /// @notice `lockPhase3()` was called from a phase other than Adulthood
    ///         — locking from Childhood/Apprenticeship would brick the
    ///         contract before the unidirectional climb completes.
    error WrongPhase();

    // -----------------------------------------------------------------------
    // Modifiers
    // -----------------------------------------------------------------------

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier whenInitialized() {
        if (!initialized) revert NotInitialized();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert WhilePaused();
        _;
    }

    modifier notDead() {
        if (currentPhase == Phase.Dead) revert AlreadyDead();
        _;
    }

    /// @notice Reverts when the Phase-3 admin lock has fired. Gates every
    ///         admin / param-tuner entry point (`setOwner`,
    ///         `setAttestationSigner`, `setPhaseManager`, `setPhase`,
    ///         `pause`, `unpause`) but NOT the operational paths (burns,
    ///         top-up, bankroll moves, lung expansion, settlement) — those
    ///         remain callable by the Agent EOA owner in Phase 3.
    modifier whenNotPhase3Locked() {
        if (phase3Locked) revert Phase3IsLocked();
        _;
    }

    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    /// @notice Records the deployer as owner and caches the EIP-712 domain
    ///         separator. Real genesis injection still goes through
    ///         `initialize` so deploy scripts can set seed values atomically
    ///         without trusting constructor args for a re-deploy.
    constructor() {
        owner = msg.sender;
        emit OwnerUpdated(address(0), msg.sender);

        _DOMAIN_NAME_HASH = keccak256(bytes("Genesis Experiment EnergyController"));
        _DOMAIN_VERSION_HASH = keccak256(bytes("1"));
        _DEPLOY_CHAIN_ID = block.chainid;
        _CACHED_DOMAIN_SEPARATOR = EIP712Settlement.domainSeparator(
            _DOMAIN_NAME_HASH, _DOMAIN_VERSION_HASH, block.chainid, address(this)
        );
    }

    /// @notice One-shot initializer. MUST be called by the deployment script
    ///         immediately after construction.
    /// @param  initialBreath_      genesis BREATH amount (1e6 precision)
    /// @param  maxBreath_          soft cap (≥ initialBreath_)
    /// @param  attestationSigner_  off-chain signer for settleMarketLoss
    function initialize(
        uint256 initialBreath_,
        uint256 maxBreath_,
        address attestationSigner_
    ) external onlyOwner {
        if (initialized) revert AlreadyInitialized();
        if (initialBreath_ == 0) revert ZeroBreath();
        if (attestationSigner_ == address(0)) revert ZeroAddress();

        initialized = true;
        initialBreath = initialBreath_;
        maxBreath = maxBreath_ >= initialBreath_ ? maxBreath_ : initialBreath_;
        breath = initialBreath_;
        currentPhase = Phase.Childhood;
        attestationSigner = attestationSigner_;

        emit Initialized(initialBreath_, maxBreath, owner, attestationSigner_);
        emit EnergyChanged(0, initialBreath_, "initialize");
        emit AttestationSignerUpdated(address(0), attestationSigner_);
    }

    // -----------------------------------------------------------------------
    // BREATH burn paths — PRD §6.2 (three classes; one entry point per class)
    // -----------------------------------------------------------------------

    /// @notice PRD §6.2 class A — decision-tax. Burned on every Agent
    ///         decision regardless of outcome. Caller MUST be `owner`
    ///         (sprint_3 hands this to PhaseManager).
    /// @dev    Reverts with `InsufficientBreath` rather than clamping to
    ///         zero; the death path is owned by `AgentLifecycle` and MUST
    ///         observe an explicit zero-balance transition.
    function burnDecisionTax(uint256 amount, string calldata reason)
        external
        onlyOwner
        whenInitialized
        whenNotPaused
        notDead
    {
        _burn(amount, reason);
    }

    /// @notice PRD §6.2 class B — time-tax. Burned per simulation tick
    ///         regardless of activity. Sprint_3 hands this to PhaseManager.
    function burnTimeTax(uint256 amount, string calldata reason)
        external
        onlyOwner
        whenInitialized
        whenNotPaused
        notDead
    {
        _burn(amount, reason);
    }

    /// @notice PRD §6.2 class C — market-loss. Burned on Polymarket settlement
    ///         against a signed EIP-712 attestation (TP §3.7).
    /// @dev    The off-chain signer mints a `SettlementAttestation` with the
    ///         loss in BREATH units; this function consumes the nonce + bumps
    ///         storage. Reentrancy: there are no external calls; reentrancy
    ///         is structurally impossible.
    /// @param  att signed attestation
    /// @param  sig 65-byte `r ‖ s ‖ v`
    function settleMarketLoss(
        EIP712Settlement.SettlementAttestation calldata att,
        bytes calldata sig
    ) external whenInitialized whenNotPaused notDead {
        if (att.lossAmount == 0) revert ZeroAmount();
        // Deadlines on EIP-712 attestations are minute-scale; the
        // ~12s validator clock-skew on Orbit L3 is well below threshold.
        // forge-lint: disable-next-line(block-timestamp)
        if (att.deadline < block.timestamp) revert DeadlineExpired();

        bytes32 d = EIP712Settlement.digest(_domainSeparator(), _toMemory(att));
        address signer = EIP712Settlement.recover(d, sig);
        if (signer == address(0)) revert InvalidSignature();
        if (signer != attestationSigner) revert InvalidSigner();
        if (usedNonces[signer][att.nonce]) revert NonceUsed();

        usedNonces[signer][att.nonce] = true;

        _burn(att.lossAmount, "settleMarketLoss");
        emit MarketLossSettled(signer, att.marketId, att.lossAmount, att.nonce);
    }

    /// @dev Internal burn primitive. Centralises underflow + zero checks +
    ///      EnergyChanged emission so the three public burn entry points stay
    ///      one-liners and analytics sees identical event shapes.
    function _burn(uint256 amount, string memory reason) private {
        if (amount == 0) revert ZeroAmount();

        uint256 old = breath;
        if (amount > old) revert InsufficientBreath();
        unchecked { breath = old - amount; }
        emit EnergyChanged(old, breath, reason);
    }

    // -----------------------------------------------------------------------
    // BREATH donations — soft-cap math (PRD §6.7)
    // -----------------------------------------------------------------------

    /// @notice Credit BREATH from a donation / reward event. Clamps at
    ///         `maxBreath` per PRD §6.7 (no eager mint above the cap); the
    ///         truncated portion is reported via `SoftCapDeflected`.
    /// @dev    Owner-gated for sprint_2; sprint_3 will permission this to
    ///         `PhaseManager` (for milestone rewards) and a separate
    ///         `donationVault`.
    function topUpBreath(uint256 amount, string calldata reason)
        external
        onlyOwner
        whenInitialized
        whenNotPaused
        notDead
    {
        if (amount == 0) revert ZeroAmount();

        uint256 old = breath;
        uint256 headroom = maxBreath - old; // safe: invariant `breath ≤ maxBreath`
        uint256 applied = amount > headroom ? headroom : amount;

        if (applied < amount) {
            emit SoftCapDeflected(amount, maxBreath, applied);
        }

        if (applied == 0) {
            // Soft-cap is fully saturated; no state mutation but the deflection
            // event above is enough for the dashboard.
            return;
        }

        unchecked { breath = old + applied; }
        emit EnergyChanged(old, breath, reason);
    }

    // -----------------------------------------------------------------------
    // Lung Expansion — PRD §6.7 (cap-raising; blocked in Desperate Mode)
    // -----------------------------------------------------------------------

    /// @notice Raise the BREATH soft cap (`maxBreath`) per PRD §6.7 Lung
    ///         Expansion. The full §6.7 trigger condition (`BREATH 充裕 +
    ///         projected_hours ≥ TARGET+24h + 非饥饿/终幕 + Phase ≥ 2`) is
    ///         enforced OFF-CHAIN by the agent main loop (T-B-009 weight
    ///         updater + decision loop); the on-chain check here is the
    ///         single PRD §6.9 invariant the contract owns: lung expansion
    ///         is BLOCKED once Desperate Mode is sticky.
    /// @dev    Reentrancy: the external call is a `view` on PhaseManager
    ///         (which we own) BEFORE any state mutation; even if a
    ///         malicious PhaseManager were swapped in via `setPhaseManager`,
    ///         a reentry would re-enter `deepenBreath` and either be
    ///         blocked by the same gate or by `notDead` /
    ///         `InvalidLungExpansion`. No value flow. Owner-gated.
    /// @param  newMaxBreath  the new soft cap (MUST be strictly greater
    ///                       than the current `maxBreath`)
    function deepenBreath(uint256 newMaxBreath)
        external
        onlyOwner
        whenInitialized
        whenNotPaused
        notDead
    {
        // PRD §6.9: Desperate Mode disables lung expansion. We tolerate an
        // unset phaseManager (legacy deploys) — only enforce when the link
        // has been wired.
        IPhaseManagerDesperateView pm = phaseManager;
        if (address(pm) != address(0) && pm.isDesperate()) {
            revert LungExpansionBlockedDesperate();
        }

        uint256 old = maxBreath;
        if (newMaxBreath <= old) revert InvalidLungExpansion();

        maxBreath = newMaxBreath;
        emit MaxBreathDeepened(old, newMaxBreath);
    }

    // -----------------------------------------------------------------------
    // Bankroll accounting — PRD §6.1
    // -----------------------------------------------------------------------

    /// @notice Credit the bankroll (e.g. winning Polymarket settlement).
    function bankrollCredit(uint256 amount, string calldata reason)
        external
        onlyOwner
        whenInitialized
        whenNotPaused
    {
        if (amount == 0) revert ZeroAmount();
        uint256 old = bankroll;
        bankroll = old + amount; // overflow-safe via 0.8.x checked math
        emit BankrollMutated(old, bankroll, reason);
    }

    /// @notice Debit the bankroll (e.g. losing settlement, fee, withdrawal).
    function bankrollDebit(uint256 amount, string calldata reason)
        external
        onlyOwner
        whenInitialized
        whenNotPaused
    {
        if (amount == 0) revert ZeroAmount();
        uint256 old = bankroll;
        if (amount > old) revert InsufficientBankroll();
        unchecked { bankroll = old - amount; }
        emit BankrollMutated(old, bankroll, reason);
    }

    // -----------------------------------------------------------------------
    // Admin
    // -----------------------------------------------------------------------

    function setOwner(address newOwner) external onlyOwner whenNotPhase3Locked {
        if (newOwner == address(0)) revert ZeroAddress();
        address previous = owner;
        owner = newOwner;
        emit OwnerUpdated(previous, newOwner);
    }

    function setAttestationSigner(address newSigner) external onlyOwner whenNotPhase3Locked {
        if (newSigner == address(0)) revert ZeroAddress();
        address previous = attestationSigner;
        attestationSigner = newSigner;
        emit AttestationSignerUpdated(previous, newSigner);
    }

    /// @notice Wire the PhaseManager reference used by `deepenBreath` for
    ///         the Desperate-Mode lockout (PRD §6.7 / §6.9). Sprint_5 deploy
    ///         script calls this once after both contracts are constructed.
    /// @dev    Zero-address is rejected; to disconnect, deploy a fresh
    ///         controller. Sprint_4 Phase-3 renounce will burn `owner` and
    ///         thus this setter (matching `setAttestationSigner` discipline).
    function setPhaseManager(address newPhaseManager) external onlyOwner whenNotPhase3Locked {
        if (newPhaseManager == address(0)) revert ZeroAddress();
        address previous = address(phaseManager);
        phaseManager = IPhaseManagerDesperateView(newPhaseManager);
        emit PhaseManagerUpdated(previous, newPhaseManager);
    }

    function pause() external onlyOwner whenNotPhase3Locked {
        if (paused) return;
        paused = true;
        emit Paused(msg.sender);
    }

    function unpause() external onlyOwner whenNotPhase3Locked {
        if (!paused) return;
        paused = false;
        emit Unpaused(msg.sender);
    }

    /// @notice Sprint_2 owner-gated phase mutator. Sprint_5 T-A-009 added
    ///         the `whenNotPhase3Locked` guard — after the renounce ritual
    ///         fires, no further phase override is possible (PRD §5.1.A).
    ///         Emits `PhaseChanged` so subscribers can react.
    function setPhase(Phase next) external onlyOwner whenInitialized whenNotPhase3Locked {
        // Forbid leaving Dead; PhaseManager owns death by sprint_4.
        if (currentPhase == Phase.Dead) revert AlreadyDead();
        if (uint8(next) > uint8(Phase.Dead)) revert InvalidPhase();

        Phase previous = currentPhase;
        currentPhase = next;
        emit PhaseChanged(previous, next);
    }

    // -----------------------------------------------------------------------
    // Phase-3 admin lock — TP §8 D17 / T-A-009
    // -----------------------------------------------------------------------

    /// @notice Permanently renounce the admin / param-tuner channel on this
    ///         contract. Set-once; no `unlockPhase3` exists by design (PRD
    ///         §5.1.A trustlessness). Emits `Phase3RolesRenounced` with
    ///         the block timestamp as audit anchor; reconcilers cross-
    ///         check against the sibling event on `PhaseManager`.
    /// @dev    Preconditions: caller MUST be `owner`, `currentPhase` MUST
    ///         be `Adulthood` (locking from earlier phases would brick the
    ///         contract before the climb completes), `phase3Locked` MUST
    ///         be false (`whenNotPhase3Locked` enforces set-once).
    ///         Post-condition: every admin path reverts `Phase3IsLocked`;
    ///         operational burns / top-ups / bankroll moves / lung
    ///         expansion / settlement remain callable.
    function lockPhase3() external onlyOwner whenNotPhase3Locked {
        if (currentPhase != Phase.Adulthood) revert WrongPhase();
        phase3Locked = true;
        emit Phase3RolesRenounced(uint64(block.timestamp));
    }

    /// @notice Convenience view mirroring `phase3Locked` storage. Lets
    ///         off-chain consumers poll a stable selector matching the
    ///         style of `paused()` / `isDesperate()` on PhaseManager.
    function isPhase3Locked() external view returns (bool) {
        return phase3Locked;
    }

    // -----------------------------------------------------------------------
    // Views
    // -----------------------------------------------------------------------

    /// @notice Canonical BREATH view; downstream contracts MUST read here
    ///         (not direct storage) so future re-orgs stay ABI-compatible.
    function totalBreath() external view returns (uint256) {
        return breath;
    }

    /// @notice Current EIP-712 domain separator (re-derived on fork).
    function DOMAIN_SEPARATOR() external view returns (bytes32) {
        return _domainSeparator();
    }

    // -----------------------------------------------------------------------
    // Internal helpers
    // -----------------------------------------------------------------------

    function _domainSeparator() private view returns (bytes32) {
        if (block.chainid == _DEPLOY_CHAIN_ID) {
            return _CACHED_DOMAIN_SEPARATOR;
        }
        return EIP712Settlement.domainSeparator(
            _DOMAIN_NAME_HASH, _DOMAIN_VERSION_HASH, block.chainid, address(this)
        );
    }

    /// @dev Coerce a calldata attestation into memory for the library hash.
    ///      Tiny memcpy — Solidity inserts the conversion implicitly when
    ///      we assign field-by-field; keeping it explicit documents the
    ///      cost path for reviewers.
    function _toMemory(EIP712Settlement.SettlementAttestation calldata att)
        private
        pure
        returns (EIP712Settlement.SettlementAttestation memory m)
    {
        m.marketId = att.marketId;
        m.lossAmount = att.lossAmount;
        m.nonce = att.nonce;
        m.deadline = att.deadline;
    }
}
