// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {TombstoneNFT} from "contracts/TombstoneNFT.sol";
import {IERC721Receiver} from "@openzeppelin/contracts/token/ERC721/IERC721Receiver.sol";
import {IERC721Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";

/// @dev Minimal ERC-721 receiver that accepts every incoming transfer.
contract TombstoneReceiver is IERC721Receiver {
    function onERC721Received(address, address, uint256, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        return IERC721Receiver.onERC721Received.selector;
    }
}

/// @title TombstoneNFTTest — T-A-007 v0.2.0 coverage
/// @notice Verifies the ERC-721 surface, the `onlyAgentLifecycle` access
///         control on `mint`, the new T-A-007 8-field struct layout, the
///         on-chain `tokenURI` data: URI with embedded SVG, the degraded-CID
///         event path (PRD §5.1.C), the PRD §6.11 DeathCause enum
///         ordering, and the explicit non-burnability of minted Tombstones
///         (PRD §5.1 — death is irreversible, the NFT survives).
contract TombstoneNFTTest is Test {
    TombstoneNFT internal tnft;

    address internal constant LIFECYCLE = address(0xA11CE);
    address internal constant INTRUDER  = address(0xBADBAD);
    address internal constant RECIPIENT = address(0xCAFE);

    string internal constant NAME    = "Genesis Tombstone";
    string internal constant SYMBOL  = "GTOMB";

    event TombstoneMinted(
        uint256 indexed tokenId,
        address indexed to,
        uint64          deathTs,
        uint8           deathCause,
        string          memoryBankCid
    );
    event TombstoneMintedWithoutMemoryBank(
        uint256 indexed tokenId,
        address indexed to,
        uint64          deathTs
    );

    function setUp() public {
        tnft = new TombstoneNFT(NAME, SYMBOL, LIFECYCLE);
    }

    function _samplePayload() internal pure returns (TombstoneNFT.Tombstone memory) {
        return TombstoneNFT.Tombstone({
            weights:             hex"01020304",
            decisionHistoryHash: keccak256("decision-history-seed"),
            lastWords:           "the dataset is enough",
            memoryBankCid:       "bafyreigenesisexamplecid",
            deathCause:          TombstoneNFT.DeathCause.Starvation,
            terminalAfterglow:   true,
            breathAtDeath:       uint256(123_456e6),
            phaseStats:          hex"0102"
        });
    }

    // -----------------------------------------------------------------------
    // Test 1 — Construction wires the immutable mint authority and exposes
    //          the brief-locked ERC-721 metadata + PRD §6.11 cause constants.
    // -----------------------------------------------------------------------
    function test_ConstructorLocksAgentLifecycleAndMetadata() public view {
        assertEq(tnft.agentLifecycle(), LIFECYCLE, "agentLifecycle locked at deploy");
        assertEq(tnft.name(),   NAME,    "ERC-721 name");
        assertEq(tnft.symbol(), SYMBOL,  "ERC-721 symbol");
        assertEq(tnft.nextTokenId(), 0,  "no tokens yet");

        // PRD §6.11 enum ordering is locked: TradingLoss=0, Starvation=1, Attrition=2.
        assertEq(uint8(TombstoneNFT.DeathCause.TradingLoss), 0);
        assertEq(uint8(TombstoneNFT.DeathCause.Starvation),  1);
        assertEq(uint8(TombstoneNFT.DeathCause.Attrition),   2);
        assertEq(tnft.CAUSE_TRADING_LOSS(), 0);
        assertEq(tnft.CAUSE_STARVATION(),   1);
        assertEq(tnft.CAUSE_ATTRITION(),    2);
    }

    function test_RevertWhen_ConstructWithZeroAgentLifecycle() public {
        vm.expectRevert(TombstoneNFT.ZeroAddress.selector);
        new TombstoneNFT(NAME, SYMBOL, address(0));
    }

    // -----------------------------------------------------------------------
    // Test 2 — Happy-path mint: only AgentLifecycle may call, payload
    //          round-trips byte-for-byte, the event fires with expected
    //          topics, ERC-721 ownership updates, and the degraded-CID
    //          event does NOT fire (CID present).
    // -----------------------------------------------------------------------
    function test_MintHappyPathPersistsPayloadAndAssignsOwnership() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();

        vm.expectEmit(true, true, true, true);
        emit TombstoneMinted(1, RECIPIENT, uint64(block.timestamp), uint8(t.deathCause), t.memoryBankCid);

        vm.prank(LIFECYCLE);
        uint256 tokenId = tnft.mint(RECIPIENT, t);

        assertEq(tokenId, 1, "token ids start at 1");
        assertEq(tnft.nextTokenId(), 1);
        assertEq(tnft.ownerOf(tokenId), RECIPIENT);
        assertEq(tnft.balanceOf(RECIPIENT), 1);

        TombstoneNFT.Tombstone memory got = tnft.getTombstone(tokenId);
        assertEq(got.weights,             t.weights,             "weights round-trip");
        assertEq(got.decisionHistoryHash, t.decisionHistoryHash, "decisionHistoryHash round-trip");
        assertEq(got.lastWords,           t.lastWords,           "lastWords round-trip");
        assertEq(got.memoryBankCid,       t.memoryBankCid,       "memoryBankCid round-trip");
        assertEq(uint8(got.deathCause),   uint8(t.deathCause),   "deathCause round-trip");
        assertEq(got.terminalAfterglow,   t.terminalAfterglow,   "terminalAfterglow round-trip");
        assertEq(got.breathAtDeath,       t.breathAtDeath,       "breathAtDeath round-trip");
        assertEq(got.phaseStats,          t.phaseStats,          "phaseStats round-trip");
    }

    // -----------------------------------------------------------------------
    // Test 3 — Access control: every non-AgentLifecycle caller MUST revert
    //          NotAgentLifecycle. Fuzzed over the address space.
    // -----------------------------------------------------------------------
    function testFuzz_MintRevertsForNonAgentLifecycle(address caller) public {
        vm.assume(caller != LIFECYCLE);
        vm.assume(caller != address(0));

        TombstoneNFT.Tombstone memory t = _samplePayload();

        vm.prank(caller);
        vm.expectRevert(TombstoneNFT.NotAgentLifecycle.selector);
        tnft.mint(RECIPIENT, t);

        assertEq(tnft.nextTokenId(), 0);
    }

    // -----------------------------------------------------------------------
    // Test 4 — Mint to zero address is explicitly rejected.
    // -----------------------------------------------------------------------
    function test_RevertWhen_MintToZeroAddress() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();

        vm.prank(LIFECYCLE);
        vm.expectRevert(TombstoneNFT.ZeroAddress.selector);
        tnft.mint(address(0), t);
    }

    // -----------------------------------------------------------------------
    // Test 5 — Tombstone is non-burnable: transferring to address(0) MUST
    //          revert (OZ v5 enforces this in `_update`).
    // -----------------------------------------------------------------------
    function test_RevertWhen_TransferToZeroAttemptingBurn() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();

        vm.prank(LIFECYCLE);
        tnft.mint(RECIPIENT, t);

        vm.prank(RECIPIENT);
        vm.expectRevert(
            abi.encodeWithSelector(IERC721Errors.ERC721InvalidReceiver.selector, address(0))
        );
        tnft.transferFrom(RECIPIENT, address(0), 1);

        assertEq(tnft.ownerOf(1), RECIPIENT);
    }

    // -----------------------------------------------------------------------
    // Test 6 — tokenURI() is a data:application/json;base64 URI with an
    //          embedded data:image/svg+xml;base64 image. Decode + assert
    //          structure (PRD §5.1.C: artefact must render without external
    //          deps).
    // -----------------------------------------------------------------------
    function test_TokenURIReturnsOnChainDataUriWithEmbeddedSvg() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();

        vm.prank(LIFECYCLE);
        tnft.mint(RECIPIENT, t);

        string memory uri = tnft.tokenURI(1);
        bytes memory raw = bytes(uri);

        // Prefix assertion.
        string memory prefix = "data:application/json;base64,";
        assertGt(raw.length, bytes(prefix).length, "uri longer than prefix");
        for (uint256 i; i < bytes(prefix).length; ++i) {
            assertEq(raw[i], bytes(prefix)[i], "json data: prefix mismatch");
        }

        // Decode the base64 body and verify JSON shape (must contain
        // "image" with a nested SVG data URI + "attributes" array + the
        // PRD §6.11 cause label).
        bytes memory body = new bytes(raw.length - bytes(prefix).length);
        for (uint256 i; i < body.length; ++i) {
            body[i] = raw[bytes(prefix).length + i];
        }
        bytes memory json = _base64Decode(body);

        assertTrue(_contains(json, bytes("\"name\":\"Genesis Tombstone #1\"")), "json carries name");
        assertTrue(_contains(json, bytes("\"image\":\"data:image/svg+xml;base64,")), "image inlined");
        assertTrue(_contains(json, bytes("\"attributes\":")), "attributes array present");
        // Cause label matches PRD §6.11 string for Starvation.
        assertTrue(_contains(json, bytes("\"value\":\"Starvation\"")), "deathCause label");

        // Pull out the embedded SVG and confirm it parses as <svg...>...</svg>
        bytes memory svgBytes = _extractEmbeddedSvg(json);
        assertGt(svgBytes.length, 0, "svg extracted");
        assertTrue(_startsWith(svgBytes, bytes("<svg")), "svg starts with <svg");
        assertTrue(_endsWith(svgBytes, bytes("</svg>")), "svg ends with </svg>");
        // 100% self-contained: no fetched / referenced assets. We allow the
        // standard SVG namespace declaration (`xmlns="http://www.w3.org/...`)
        // because browsers DO NOT dereference that URI — it's a URN — but
        // every other content-fetch surface is forbidden.
        assertFalse(_contains(svgBytes, bytes("xlink:href")), "no xlink");
        assertFalse(_contains(svgBytes, bytes("<image"  )),   "no <image>");
        assertFalse(_contains(svgBytes, bytes("<link"   )),   "no <link>");
        assertFalse(_contains(svgBytes, bytes("<script" )),   "no <script>");
        assertFalse(_contains(svgBytes, bytes("<foreignObject")), "no <foreignObject>");
        assertFalse(_contains(svgBytes, bytes("<use"    )),   "no <use href> (could pull in external)");

        // Unminted ids revert.
        vm.expectRevert();
        tnft.tokenURI(999);
    }

    // -----------------------------------------------------------------------
    // Test 7 — Degraded path: empty memoryBankCid emits the
    //          `TombstoneMintedWithoutMemoryBank` event (PRD §5.1.C) AND
    //          the mint still succeeds.
    // -----------------------------------------------------------------------
    function test_DegradedPath_EmptyCidEmitsExtraEventAndMintSucceeds() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();
        t.memoryBankCid = "";

        // Both events expected (TombstoneMinted then TombstoneMintedWithoutMemoryBank).
        vm.expectEmit(true, true, true, true);
        emit TombstoneMinted(1, RECIPIENT, uint64(block.timestamp), uint8(t.deathCause), "");
        vm.expectEmit(true, true, true, true);
        emit TombstoneMintedWithoutMemoryBank(1, RECIPIENT, uint64(block.timestamp));

        vm.prank(LIFECYCLE);
        uint256 tokenId = tnft.mint(RECIPIENT, t);

        assertEq(tokenId, 1, "mint still succeeds with empty CID");
        assertEq(tnft.ownerOf(tokenId), RECIPIENT);

        // Round-trip carries the empty CID.
        TombstoneNFT.Tombstone memory got = tnft.getTombstone(tokenId);
        assertEq(got.memoryBankCid, "", "empty CID persisted");
    }

    function test_HappyPath_NonEmptyCidDoesNotEmitDegradedEvent() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();
        // memoryBankCid already non-empty in _samplePayload.

        vm.recordLogs();
        vm.prank(LIFECYCLE);
        tnft.mint(RECIPIENT, t);
        Vm.Log[] memory logs = vm.getRecordedLogs();

        bytes32 SIG = keccak256("TombstoneMintedWithoutMemoryBank(uint256,address,uint64)");
        for (uint256 i; i < logs.length; ++i) {
            assertTrue(logs[i].topics[0] != SIG, "degraded event must NOT fire when CID present");
        }
    }

    // -----------------------------------------------------------------------
    // Test 8 — All three PRD §6.11 DeathCause values round-trip through
    //          mint → getTombstone with the cause label visible in tokenURI.
    // -----------------------------------------------------------------------
    function test_AllThreeDeathCausesRoundTripWithLabelsInTokenURI() public {
        string[3] memory labels = ["TradingLoss", "Starvation", "Attrition"];
        TombstoneNFT.DeathCause[3] memory causes = [
            TombstoneNFT.DeathCause.TradingLoss,
            TombstoneNFT.DeathCause.Starvation,
            TombstoneNFT.DeathCause.Attrition
        ];

        for (uint256 i; i < 3; ++i) {
            TombstoneNFT.Tombstone memory t = _samplePayload();
            t.deathCause = causes[i];
            t.lastWords = string.concat("epitaph-", labels[i]);

            vm.prank(LIFECYCLE);
            uint256 tokenId = tnft.mint(RECIPIENT, t);

            TombstoneNFT.Tombstone memory got = tnft.getTombstone(tokenId);
            assertEq(uint8(got.deathCause), uint8(causes[i]), "cause round-trip");
            assertEq(tnft.causeName(causes[i]), labels[i], "label match");

            // Decode tokenURI JSON and confirm cause label is visible.
            string memory uri = tnft.tokenURI(tokenId);
            bytes memory json = _decodeJsonBody(uri);
            assertTrue(_contains(json, bytes(string.concat("\"value\":\"", labels[i], "\""))), "cause label in json");
        }
    }

    // -----------------------------------------------------------------------
    // Test 9 — Multiple mints assign monotonically increasing 1-indexed ids
    //          and per-token payload remains independent.
    // -----------------------------------------------------------------------
    function test_MultipleMintsAssignMonotonicIds() public {
        TombstoneNFT.Tombstone memory a = _samplePayload();
        TombstoneNFT.Tombstone memory b = _samplePayload();
        b.lastWords         = "later death";
        b.deathCause        = TombstoneNFT.DeathCause.Attrition;
        b.terminalAfterglow = false;
        b.memoryBankCid     = "";

        vm.startPrank(LIFECYCLE);
        uint256 id1 = tnft.mint(RECIPIENT, a);
        uint256 id2 = tnft.mint(address(0xCA11), b);
        vm.stopPrank();

        assertEq(id1, 1);
        assertEq(id2, 2);
        assertEq(tnft.nextTokenId(), 2);

        TombstoneNFT.Tombstone memory got2 = tnft.getTombstone(id2);
        assertEq(got2.lastWords, "later death");
        assertEq(uint8(got2.deathCause), uint8(TombstoneNFT.DeathCause.Attrition));
        assertEq(got2.memoryBankCid, "", "degraded-path CID empty");
        assertFalse(got2.terminalAfterglow);
    }

    // -----------------------------------------------------------------------
    // Test 10 — Mint to a contract recipient invokes ERC721Receiver hook.
    // -----------------------------------------------------------------------
    function test_MintToContractRecipientInvokesReceiverHook() public {
        TombstoneReceiver receiver = new TombstoneReceiver();
        TombstoneNFT.Tombstone memory t = _samplePayload();

        vm.prank(LIFECYCLE);
        uint256 tokenId = tnft.mint(address(receiver), t);

        assertEq(tnft.ownerOf(tokenId), address(receiver));
    }

    // -----------------------------------------------------------------------
    // Test 11 — Fuzz the payload to confirm storage round-trips arbitrary
    //           string/bytes lengths + extreme uint values.
    // -----------------------------------------------------------------------
    /// @dev Calldata fixture struct keeps the fuzz arity below the solc
    ///      0.8.24 stack-too-deep limit (8 fields would otherwise blow the
    ///      stack without via_ir).
    struct FuzzFixture {
        bytes weights;
        bytes32 historyHash;
        string lastWords;
        string memoryBankCid;
        uint8 causeRaw;
        bool terminalAfterglow;
        uint256 breathAtDeath;
        bytes phaseStats;
    }

    function testFuzz_PayloadRoundTrip(FuzzFixture calldata f) public {
        // Clamp the fuzzed cause to a valid enum value (0..2).
        TombstoneNFT.DeathCause cause = TombstoneNFT.DeathCause(f.causeRaw % 3);

        TombstoneNFT.Tombstone memory t = TombstoneNFT.Tombstone({
            weights:             f.weights,
            decisionHistoryHash: f.historyHash,
            lastWords:           f.lastWords,
            memoryBankCid:       f.memoryBankCid,
            deathCause:          cause,
            terminalAfterglow:   f.terminalAfterglow,
            breathAtDeath:       f.breathAtDeath,
            phaseStats:          f.phaseStats
        });

        vm.prank(LIFECYCLE);
        uint256 tokenId = tnft.mint(RECIPIENT, t);

        TombstoneNFT.Tombstone memory got = tnft.getTombstone(tokenId);
        assertEq(got.weights,             f.weights);
        assertEq(got.decisionHistoryHash, f.historyHash);
        assertEq(got.lastWords,           f.lastWords);
        assertEq(got.memoryBankCid,       f.memoryBankCid);
        assertEq(uint8(got.deathCause),   uint8(cause));
        assertEq(got.terminalAfterglow,   f.terminalAfterglow);
        assertEq(got.breathAtDeath,       f.breathAtDeath);
        assertEq(got.phaseStats,          f.phaseStats);
    }

    // -----------------------------------------------------------------------
    // Test 12 — XML escaping protects the SVG from being broken by hostile
    //           lastWords containing `<`/`>`/`&`/`"`. Important because
    //           lastWords is user-controlled by the off-chain agent.
    // -----------------------------------------------------------------------
    function test_TokenURIEscapesXmlInLastWords() public {
        TombstoneNFT.Tombstone memory t = _samplePayload();
        t.lastWords = "</svg><script>alert(1)</script>";

        vm.prank(LIFECYCLE);
        tnft.mint(RECIPIENT, t);

        // Decode JSON, then the embedded SVG, and confirm the literal
        // `</svg>` does NOT appear inside the SVG body before the closing
        // tag (i.e. the injection was escaped).
        bytes memory json = _decodeJsonBody(tnft.tokenURI(1));
        bytes memory svg  = _extractEmbeddedSvg(json);

        // The escaped form `&lt;/svg&gt;` SHOULD appear; the raw form must NOT
        // appear except as the LAST closing tag.
        assertTrue(_contains(svg, bytes("&lt;/svg&gt;")), "raw </svg> got escaped");
        // Count raw `</svg>` occurrences — exactly one (the legitimate
        // closing tag).
        uint256 occurrences = _countOccurrences(svg, bytes("</svg>"));
        assertEq(occurrences, 1, "exactly one </svg> closing tag");
    }

    // =======================================================================
    //                          base64 / string helpers
    // =======================================================================

    /// @dev Pull the data:application/json;base64,... body out of the full
    ///      tokenURI and base64-decode it.
    function _decodeJsonBody(string memory uri) internal pure returns (bytes memory) {
        bytes memory raw = bytes(uri);
        string memory prefix = "data:application/json;base64,";
        bytes memory body = new bytes(raw.length - bytes(prefix).length);
        for (uint256 i; i < body.length; ++i) {
            body[i] = raw[bytes(prefix).length + i];
        }
        return _base64Decode(body);
    }

    /// @dev Pull the embedded image SVG out of the JSON body. Returns the
    ///      decoded SVG bytes.
    function _extractEmbeddedSvg(bytes memory json) internal pure returns (bytes memory) {
        bytes memory marker = bytes("\"image\":\"data:image/svg+xml;base64,");
        uint256 start = _find(json, marker);
        require(start != type(uint256).max, "image marker not found");
        start += marker.length;

        // Read until the closing quote.
        uint256 end = start;
        while (end < json.length && json[end] != 0x22) {
            ++end;
        }
        bytes memory b64 = new bytes(end - start);
        for (uint256 i; i < b64.length; ++i) {
            b64[i] = json[start + i];
        }
        return _base64Decode(b64);
    }

    /// @dev Minimal RFC-4648 base64 decoder (no padding-stripping required;
    ///      we tolerate trailing `=`). Returns the decoded bytes.
    function _base64Decode(bytes memory data) internal pure returns (bytes memory) {
        // Compute trailing padding.
        uint256 inputLen = data.length;
        if (inputLen == 0) return new bytes(0);

        uint256 padCount = 0;
        if (data[inputLen - 1] == 0x3d) padCount++;
        if (inputLen >= 2 && data[inputLen - 2] == 0x3d) padCount++;

        // rationale: integer-division loss is intentional here — base64 input
        // length is always a multiple of 4 (the encoder pads to align). So
        // (inputLen / 4) * 3 is the exact decoded length before padCount
        // subtraction; reordering would not change precision.
        // forge-lint: disable-next-line(divide-before-multiply)
        uint256 outputLen = (inputLen / 4) * 3 - padCount;
        bytes memory out = new bytes(outputLen);

        uint256 outIdx;
        for (uint256 i; i < inputLen; i += 4) {
            uint24 packed = (uint24(_b64char(data[i])) << 18)
                          | (uint24(_b64char(data[i + 1])) << 12)
                          | (uint24(_b64char(data[i + 2])) << 6)
                          |  uint24(_b64char(data[i + 3]));

            // rationale: each shift selects 8 bits of a uint24; the uint8
            // narrowing is by design (we deliberately discard the upper bits).
            // forge-lint: disable-next-line(unsafe-typecast)
            if (outIdx < outputLen) out[outIdx++] = bytes1(uint8(packed >> 16));
            // forge-lint: disable-next-line(unsafe-typecast)
            if (outIdx < outputLen) out[outIdx++] = bytes1(uint8(packed >> 8));
            // forge-lint: disable-next-line(unsafe-typecast)
            if (outIdx < outputLen) out[outIdx++] = bytes1(uint8(packed));
        }
        return out;
    }

    /// @dev RFC-4648 alphabet position. Returns 0 for the `=` pad char so the
    ///      decoder produces zero bits for padded slots (sliced off via
    ///      outputLen).
    function _b64char(bytes1 c) private pure returns (uint8) {
        uint8 v = uint8(c);
        if (v >= 0x41 && v <= 0x5a) return v - 0x41;       // A-Z  → 0..25
        if (v >= 0x61 && v <= 0x7a) return v - 0x61 + 26;  // a-z  → 26..51
        if (v >= 0x30 && v <= 0x39) return v - 0x30 + 52;  // 0-9  → 52..61
        if (v == 0x2b) return 62;                          // +    → 62
        if (v == 0x2f) return 63;                          // /    → 63
        return 0;                                          // = / unknown
    }

    function _contains(bytes memory haystack, bytes memory needle) internal pure returns (bool) {
        return _find(haystack, needle) != type(uint256).max;
    }

    function _find(bytes memory haystack, bytes memory needle) internal pure returns (uint256) {
        if (needle.length == 0 || needle.length > haystack.length) return type(uint256).max;
        for (uint256 i; i <= haystack.length - needle.length; ++i) {
            bool ok = true;
            for (uint256 j; j < needle.length; ++j) {
                if (haystack[i + j] != needle[j]) { ok = false; break; }
            }
            if (ok) return i;
        }
        return type(uint256).max;
    }

    function _startsWith(bytes memory haystack, bytes memory prefix) internal pure returns (bool) {
        if (prefix.length > haystack.length) return false;
        for (uint256 i; i < prefix.length; ++i) {
            if (haystack[i] != prefix[i]) return false;
        }
        return true;
    }

    function _endsWith(bytes memory haystack, bytes memory suffix) internal pure returns (bool) {
        if (suffix.length > haystack.length) return false;
        uint256 offset = haystack.length - suffix.length;
        for (uint256 i; i < suffix.length; ++i) {
            if (haystack[offset + i] != suffix[i]) return false;
        }
        return true;
    }

    function _countOccurrences(bytes memory haystack, bytes memory needle) internal pure returns (uint256 count) {
        if (needle.length == 0 || needle.length > haystack.length) return 0;
        uint256 i;
        while (i + needle.length <= haystack.length) {
            bool ok = true;
            for (uint256 j; j < needle.length; ++j) {
                if (haystack[i + j] != needle[j]) { ok = false; break; }
            }
            if (ok) { ++count; i += needle.length; }
            else { ++i; }
        }
    }

}
