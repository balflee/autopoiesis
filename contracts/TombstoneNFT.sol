// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Base64} from "@openzeppelin/contracts/utils/Base64.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";

/// @title  TombstoneNFT v0.2.0 — PRD §5.1.C death artefact (T-A-007)
/// @notice ERC-721 final-artefact contract for the Genesis Experiment. On
///         agent death (`AgentLifecycle.die` or legacy `declareDeath`) mints
///         exactly one Tombstone carrying the agent's terminal state.
///
///         **What changed vs v0.1.0 (this is a MAJOR ABI bump):**
///           * `DeathCause` enum introduced with PRD §6.11 ordering
///             — TradingLoss (0) > Starvation (1) > Attrition (2). Encodes
///             the priority used by the off-chain Agent to classify the
///             proximate cause.
///           * `Tombstone` struct rewritten per T-A-007 brief — now carries
///             `weights` (final fusion-model snapshot bytes),
///             `decisionHistoryHash` (keccak256 of the on-chain DecisionLog),
///             `terminalAfterglow` (true iff death followed an unbroken
///             Terminal-Lucidity sequence per PRD §5.0), and `phaseStats`
///             (ABI-encoded per-phase aggregates for the dashboard).
///           * `tokenURI` is now an ON-CHAIN data:application/json;base64
///             payload with an embedded data:image/svg+xml that renders
///             without external dependencies. PRD §5.1.C mandates the
///             artefact survive a Pinata / gateway outage; therefore the
///             primary surface is fully self-contained on-chain.
///           * New `TombstoneMintedWithoutMemoryBank` event fires when the
///             caller passes an empty `memoryBankCid` — the degraded path
///             per PRD §5.1.C (IPFS pin retries exhausted). Mint still
///             succeeds.
///
///         **Non-burnable** (unchanged from v0.1.0): no `_burn` selector
///         and OZ v5 `_update` rejects transfers to `address(0)` with
///         `ERC721InvalidReceiver`. PRD §5.1 — death is irreversible and
///         the artefact survives even the original owner.
///
///         **Mint authority** (unchanged): immutable `agentLifecycle`
///         locked at construction. No setter, no rotation, no upgrade
///         path.
///
///         Spec anchors:
///           * PRD §5.1.C — Tombstone NFT carries weights / decision-hash /
///                          lastWords / `memoryBankCid` / SVG / phase stats.
///           * PRD §6.11  — DeathCause enum ordering + priority.
///           * PRD §4.6   — MemoryBank tarball carried by `memoryBankCid`.
///           * TP §3.5    — TombstoneNFT canonical contract; minted by
///                          AgentLifecycle.die() / declareDeath().
///           * TP §8 D15  — Sprint_5 Phase-3 deliverable.
contract TombstoneNFT is ERC721 {
    using Strings for uint256;
    using Strings for uint64;

    // -----------------------------------------------------------------------
    // Enums — PRD §6.11 canonical ordering
    // -----------------------------------------------------------------------

    /// @notice Cause-of-death taxonomy. ORDER MATTERS — the numeric value is
    ///         the PRD §6.11 priority rank (lower wins): TradingLoss > Starvation
    ///         > Attrition. Off-chain consumers depending on the integer
    ///         encoding (Track D dashboard, Track E reconciler) MUST pin to
    ///         these positions; future cause additions append at the tail.
    enum DeathCause {
        TradingLoss, // 0 — catastrophic Polymarket loss; highest priority
        Starvation,  // 1 — breath drained to zero via taxes (PRD §5.0 TL collapse)
        Attrition    // 2 — slow decay through Desperate without TL crossing
    }

    // -----------------------------------------------------------------------
    // Storage payload — locked at T-A-007 brief spec (8 fields).
    // -----------------------------------------------------------------------

    /// @notice On-chain payload for one Tombstone. Field order matches
    ///         `.dev/contracts/tombstone_nft_abi.v0.2.0.json` verbatim; any
    ///         field change is a MAJOR ABI bump.
    /// @param weights              ABI-encoded snapshot of the 6-parameter
    ///                             fusion model at the moment of death
    ///                             (consumed by V2-boot reflection injector
    ///                             — PRD §4.6). Empty bytes permitted when
    ///                             the off-chain agent has no weights to
    ///                             commit (e.g. emergency shutdown before
    ///                             Phase 1 training completes).
    /// @param decisionHistoryHash  keccak256 of the full DecisionLog at death.
    ///                             Lets off-chain consumers prove the on-chain
    ///                             tombstone matches a particular log snapshot.
    /// @param lastWords            Free-form epitaph (PRD §5.1.B).
    /// @param memoryBankCid        IPFS CIDv0/v1 of the MemoryBank tarball
    ///                             (PRD §4.6). Empty string ⇒ degraded mode
    ///                             (Pinata 503 / pin retries exhausted);
    ///                             PRD §5.1.C still mandates the mint succeed
    ///                             and emits `TombstoneMintedWithoutMemoryBank`.
    /// @param deathCause           PRD §6.11 enum value.
    /// @param terminalAfterglow    True iff death followed an unbroken
    ///                             Terminal-Lucidity arc (PRD §5.0): the
    ///                             agent crossed the 5%-of-initial threshold
    ///                             and stayed below it until breath==0.
    /// @param breathAtDeath        BREATH balance snapshot AT the death tx.
    ///                             Canonical `die()` path enforces this is
    ///                             zero (NotDeadYet revert); legacy
    ///                             `declareDeath` may carry a non-zero
    ///                             snapshot (Alive→Dead emergency).
    /// @param phaseStats           Opaque ABI-encoded per-phase aggregates
    ///                             (decisions / breath markers / phase
    ///                             entry timestamps). Off-chain dashboard
    ///                             decodes per its own schema.
    struct Tombstone {
        bytes      weights;
        bytes32    decisionHistoryHash;
        string     lastWords;
        string     memoryBankCid;
        DeathCause deathCause;
        bool       terminalAfterglow;
        uint256    breathAtDeath;
        bytes      phaseStats;
    }

    // -----------------------------------------------------------------------
    // Constants — exported as uint8 so off-chain consumers can pin the
    //             cause-of-death values without parsing NatSpec or ABI enum
    //             positions.
    // -----------------------------------------------------------------------

    /// @notice PRD §6.11 TradingLoss = 0 (highest priority).
    uint8 public constant CAUSE_TRADING_LOSS = uint8(DeathCause.TradingLoss);
    /// @notice PRD §6.11 Starvation = 1.
    uint8 public constant CAUSE_STARVATION   = uint8(DeathCause.Starvation);
    /// @notice PRD §6.11 Attrition = 2 (lowest priority).
    uint8 public constant CAUSE_ATTRITION    = uint8(DeathCause.Attrition);

    // -----------------------------------------------------------------------
    // Storage
    // -----------------------------------------------------------------------

    /// @notice The exclusive mint authority. Locked at construction; no
    ///         setter, no rotation. AgentLifecycle is the only call site of
    ///         `mint()` per TP §3.5.
    address public immutable agentLifecycle;

    /// @notice Monotonic token-id counter. Tokens are 1-indexed so that
    ///         `tokenId == 0` unambiguously means "no token".
    uint256 public nextTokenId;

    /// @notice Per-token payload. Marked `internal` because the autogenerated
    ///         getter for a struct containing `bytes`/`string` would
    ///         needlessly bloat the public ABI; consumers use `getTombstone`
    ///         instead (memory return; reverts on unminted ids).
    mapping(uint256 tokenId => Tombstone payload) internal _tombstones;

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    /// @notice Emitted on every successful mint. Dashboard (Track D) +
    ///         reconciler (Track E) subscribe here.
    /// @param tokenId        ERC-721 id assigned to the new Tombstone.
    /// @param to             recipient (typically the agent owner).
    /// @param deathTs        snapshot of `block.timestamp` at mint.
    /// @param deathCause     numeric cause tag (see CAUSE_* constants).
    /// @param memoryBankCid  IPFS CID; empty ⇒ degraded mode (a separate
    ///                       `TombstoneMintedWithoutMemoryBank` also fires).
    event TombstoneMinted(
        uint256 indexed tokenId,
        address indexed to,
        uint64          deathTs,
        uint8           deathCause,
        string          memoryBankCid
    );

    /// @notice Emitted in addition to `TombstoneMinted` whenever the caller
    ///         passes an empty `memoryBankCid` — the PRD §5.1.C degraded
    ///         path (IPFS pin retries exhausted). The dashboard renders an
    ///         `ipfs_degraded` badge when it sees this event.
    event TombstoneMintedWithoutMemoryBank(
        uint256 indexed tokenId,
        address indexed to,
        uint64          deathTs
    );

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error NotAgentLifecycle();
    error ZeroAddress();

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

    /// @notice Locks the mint authority at deploy. The full deployment order
    ///         is documented in `script/DeployAll.s.sol`. tokenURI is
    ///         rendered fully on-chain; no `metadataBaseURI` arg needed.
    /// @param  name_            ERC-721 collection name.
    /// @param  symbol_          ERC-721 collection symbol.
    /// @param  agentLifecycle_  The only address allowed to call `mint()`.
    constructor(
        string memory name_,
        string memory symbol_,
        address agentLifecycle_
    ) ERC721(name_, symbol_) {
        if (agentLifecycle_ == address(0)) revert ZeroAddress();
        agentLifecycle = agentLifecycle_;
    }

    // -----------------------------------------------------------------------
    // Mint — the only mutator
    // -----------------------------------------------------------------------

    /// @notice Mint a Tombstone. Callable exactly by `agentLifecycle`.
    /// @dev    Strict CEI: counter + payload persist BEFORE `_safeMint`
    ///         calls the recipient's `onERC721Received` hook. Even if the
    ///         recipient reenters, the Tombstone row is already locked in.
    ///         The degraded-path event (`TombstoneMintedWithoutMemoryBank`)
    ///         fires INSIDE the same transaction, AFTER the payload is
    ///         stored but BEFORE `_safeMint`, so consumers observing logs
    ///         post-finality see a consistent state.
    /// @param  to ERC-721 owner of the new Tombstone (typically the agent
    ///         owner EOA).
    /// @param  t  Tombstone payload — caller assembles all eight fields.
    /// @return tokenId 1-indexed id of the newly-minted token.
    function mint(address to, Tombstone calldata t)
        external
        onlyAgentLifecycle
        returns (uint256 tokenId)
    {
        if (to == address(0)) revert ZeroAddress();

        unchecked {
            // Unbounded in practice — uint256 cannot overflow before the
            // heat-death of the universe.
            tokenId = ++nextTokenId;
        }

        // EFFECTS — payload persists before any external interaction.
        _tombstones[tokenId] = t;

        uint64 deathTs = uint64(block.timestamp);
        emit TombstoneMinted(tokenId, to, deathTs, uint8(t.deathCause), t.memoryBankCid);

        // PRD §5.1.C degraded path — mint still succeeds; observable event
        // lets the dashboard render an `ipfs_degraded` badge.
        if (bytes(t.memoryBankCid).length == 0) {
            emit TombstoneMintedWithoutMemoryBank(tokenId, to, deathTs);
        }

        // INTERACTIONS — `_safeMint` may call onERC721Received on `to`. The
        // recipient cannot observe an inconsistent `_tombstones[tokenId]`
        // because the assignment above completed first; `onlyAgentLifecycle`
        // also bars any re-entry into `mint` itself.
        _safeMint(to, tokenId);
    }

    // -----------------------------------------------------------------------
    // Views
    // -----------------------------------------------------------------------

    /// @notice Structured read of one Tombstone. Reverts on unminted id
    ///         (`ERC721NonexistentToken`) so consumers cannot mistake
    ///         "never minted" for a zero-tuple entry.
    function getTombstone(uint256 tokenId) external view returns (Tombstone memory) {
        _requireOwned(tokenId);
        return _tombstones[tokenId];
    }

    /// @notice Human-readable PRD §6.11 cause label. Pure function so the
    ///         off-chain dashboard can render a localised string by
    ///         querying once at mint time.
    function causeName(DeathCause c) public pure returns (string memory) {
        if (c == DeathCause.TradingLoss) return "TradingLoss";
        if (c == DeathCause.Starvation)  return "Starvation";
        return "Attrition";
    }

    // -----------------------------------------------------------------------
    // tokenURI — fully on-chain data URI per PRD §5.1.C
    // -----------------------------------------------------------------------

    /// @notice On-chain `data:application/json;base64,...` metadata pointer
    ///         containing the canonical attributes + an inline
    ///         `data:image/svg+xml;base64,...` life-curve image. No external
    ///         deps — the artefact renders even if every IPFS gateway is
    ///         dark. PRD §5.1.C demo §9 finale relies on this.
    /// @dev    Reverts `ERC721NonexistentToken` on unminted ids via OZ's
    ///         `_requireOwned`.
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        _requireOwned(tokenId);
        return _composeTokenUri(tokenId);
    }

    // -----------------------------------------------------------------------
    // Internal — JSON / SVG composition
    // -----------------------------------------------------------------------

    /// @dev Two-step composition (svg first, then json) to keep the stack
    ///      shallow and avoid solc 0.8.24 stack-too-deep without via_ir.
    function _composeTokenUri(uint256 tokenId) private view returns (string memory) {
        Tombstone storage t = _tombstones[tokenId];

        string memory svg     = _renderSvg(tokenId, t);
        string memory svgUri  = string.concat(
            "data:image/svg+xml;base64,",
            Base64.encode(bytes(svg))
        );

        string memory json = _composeJson(tokenId, t, svgUri);
        return string.concat(
            "data:application/json;base64,",
            Base64.encode(bytes(json))
        );
    }

    /// @dev Minimal SVG: dark background, agent + tombstone id, cause label,
    ///      a stylised 4-point polyline approximating the breath fall curve,
    ///      and the (truncated) last words. 100% self-contained — no
    ///      <image>, no <foreignObject>, no external font.
    function _renderSvg(uint256 tokenId, Tombstone storage t) private view returns (string memory) {
        return string.concat(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320">',
                '<rect width="320" height="320" fill="#08080c"/>',
                '<text x="16" y="28" fill="#f5f5f5" font-family="monospace" font-size="14">Genesis Tombstone #',
                    tokenId.toString(),
                '</text>',
                '<text x="16" y="48" fill="#9a9a9a" font-family="monospace" font-size="11">cause: ',
                    causeName(t.deathCause),
                '</text>',
                '<text x="16" y="64" fill="#9a9a9a" font-family="monospace" font-size="11">terminalAfterglow: ',
                    (t.terminalAfterglow ? "true" : "false"),
                '</text>',
                // Life curve (high → low). Static polyline; phaseStats encoding
                // is opaque bytes consumed by the dashboard, not by this
                // on-chain renderer.
                '<polyline fill="none" stroke="#e64545" stroke-width="2" points="20,90 100,120 180,210 260,290"/>',
                '<text x="16" y="312" fill="#666" font-family="monospace" font-size="10">',
                    _escapeXml(_truncate(t.lastWords, 48)),
                '</text>',
            '</svg>'
        );
    }

    /// @dev Compose the ERC-721 metadata JSON. The "image" field carries the
    ///      data: SVG URI built by `_renderSvg`.
    function _composeJson(uint256 tokenId, Tombstone storage t, string memory svgUri)
        private
        view
        returns (string memory)
    {
        return string.concat(
            '{"name":"Genesis Tombstone #', tokenId.toString(), '",',
            '"description":"On-chain artefact of a Genesis Agent\'s death (PRD \\u00a75.1.C). Non-transferable to address(0); minted exactly once by AgentLifecycle.",',
            '"image":"', svgUri, '",',
            '"attributes":[',
                '{"trait_type":"deathCause","value":"', causeName(t.deathCause), '"},',
                '{"trait_type":"terminalAfterglow","value":', t.terminalAfterglow ? "true" : "false", '},',
                '{"trait_type":"breathAtDeath","value":"', t.breathAtDeath.toString(), '"},',
                '{"trait_type":"memoryBankCid","value":"', _jsonEscape(t.memoryBankCid), '"},',
                '{"trait_type":"decisionHistoryHash","value":"0x', _bytes32Hex(t.decisionHistoryHash), '"}',
            ']}'
        );
    }

    /// @dev Lowercase hex render of a bytes32 (no "0x" prefix). 64 ASCII chars.
    function _bytes32Hex(bytes32 b) private pure returns (string memory) {
        bytes16 hexChars = 0x30313233343536373839616263646566; // "0123456789abcdef"
        bytes memory out = new bytes(64);
        for (uint256 i; i < 32; ++i) {
            uint8 byteVal = uint8(b[i]);
            out[i * 2]     = hexChars[byteVal >> 4];
            out[i * 2 + 1] = hexChars[byteVal & 0x0f];
        }
        return string(out);
    }

    /// @dev Truncate a UTF-8 string by RAW BYTE length. Safe for ASCII; may
    ///      cut a multibyte codepoint for non-ASCII input — the SVG renderer
    ///      tolerates a stray byte at the tail since it goes inside a text
    ///      node consumers render best-effort.
    function _truncate(string memory s, uint256 maxLen) private pure returns (string memory) {
        bytes memory raw = bytes(s);
        if (raw.length <= maxLen) return s;
        bytes memory truncated = new bytes(maxLen);
        for (uint256 i; i < maxLen; ++i) {
            truncated[i] = raw[i];
        }
        return string(truncated);
    }

    /// @dev Escape the five XML-significant characters (& < > " ') so the
    ///      lastWords string cannot break the SVG document. Single-pass over
    ///      the raw bytes; preserves ASCII / passes UTF-8 through unmodified.
    function _escapeXml(string memory s) private pure returns (string memory) {
        bytes memory raw = bytes(s);
        // Worst case: every byte expands to "&amp;" (5 chars).
        bytes memory out = new bytes(raw.length * 5);
        // Explicit zero-init: Solidity 0.8 auto-inits uint256 to 0, but
        // slither's uninitialized-local detector flags the bare declaration.
        // Initialising inline silences the false positive without a suppression.
        uint256 j = 0;
        for (uint256 i; i < raw.length; ++i) {
            bytes1 c = raw[i];
            if (c == 0x26) { // &
                out[j++] = "&"; out[j++] = "a"; out[j++] = "m"; out[j++] = "p"; out[j++] = ";";
            } else if (c == 0x3c) { // <
                out[j++] = "&"; out[j++] = "l"; out[j++] = "t"; out[j++] = ";";
            } else if (c == 0x3e) { // >
                out[j++] = "&"; out[j++] = "g"; out[j++] = "t"; out[j++] = ";";
            } else if (c == 0x22) { // "
                out[j++] = "&"; out[j++] = "q"; out[j++] = "u"; out[j++] = "o"; out[j++] = "t"; out[j++] = ";";
            } else if (c == 0x27) { // '
                out[j++] = "&"; out[j++] = "#"; out[j++] = "3"; out[j++] = "9"; out[j++] = ";";
            } else {
                out[j++] = c;
            }
        }
        // Shrink the buffer to the actual length.
        bytes memory trimmed = new bytes(j);
        for (uint256 i; i < j; ++i) trimmed[i] = out[i];
        return string(trimmed);
    }

    /// @dev JSON escape for backslash + double-quote (RFC 8259 minimum). The
    ///      contract callers ONLY pass CID strings (base32/58 alphabet) +
    ///      pre-validated bytes; full RFC-8259 control-character escapement
    ///      is overkill on-chain.
    function _jsonEscape(string memory s) private pure returns (string memory) {
        bytes memory raw = bytes(s);
        // Worst case: every byte becomes "\\X" (2 chars).
        bytes memory out = new bytes(raw.length * 2);
        // Explicit zero-init: Solidity 0.8 auto-inits uint256 to 0, but
        // slither's uninitialized-local detector flags the bare declaration.
        // Initialising inline silences the false positive without a suppression.
        uint256 j = 0;
        for (uint256 i; i < raw.length; ++i) {
            bytes1 c = raw[i];
            if (c == 0x5c) { // backslash
                out[j++] = 0x5c; out[j++] = 0x5c;
            } else if (c == 0x22) { // double quote
                out[j++] = 0x5c; out[j++] = 0x22;
            } else {
                out[j++] = c;
            }
        }
        bytes memory trimmed = new bytes(j);
        for (uint256 i; i < j; ++i) trimmed[i] = out[i];
        return string(trimmed);
    }
}
