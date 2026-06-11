// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title  EIP712Settlement
/// @notice Typed-data verifier for off-chain settlement attestations that gate
///         every off-chain → on-chain `EnergyController` burn / bankroll
///         mutation. Mirrors TECHNICAL_PLAN §3.7 (EIP-712 attestation surface)
///         and PRD §6.2 (only signed attestations may settle a market loss).
///
/// @dev    Design choices (locked):
///           * Domain separator includes `chainId` + `verifyingContract` so a
///             signature minted for L3 / chainId X cannot be replayed on a
///             fork or sibling deployment. (TP §3.7.)
///           * Per-signer `nonce` is consumed by the verifying contract; this
///             library is pure (no storage). The caller MUST persist nonce
///             consumption — see `EnergyController.usedNonces`.
///           * `deadline` is enforced by the caller (this library does not
///             read `block.timestamp` to remain `pure`-compatible for unit
///             testing); the caller MUST reject `deadline < block.timestamp`.
///           * Signature recovery rejects `s` values in the upper half (EIP-2
///             malleability) and rejects `v ∉ {27, 28}`.
///           * Signature length MUST be exactly 65 bytes (`r ‖ s ‖ v`).
library EIP712Settlement {
    // -----------------------------------------------------------------------
    // Type definitions
    // -----------------------------------------------------------------------

    /// @notice Off-chain attestation that authorises a single market-loss
    ///         settlement on `EnergyController`.
    /// @dev    Field order MUST match the EIP-712 type string below; changing
    ///         either requires bumping `eip712_settlement` in
    ///         `.dev/contracts/_registry.json`.
    struct SettlementAttestation {
        uint256 marketId;     // Polymarket condition id (or local market index)
        uint256 lossAmount;   // BREATH units to burn (1e6 fixed point)
        uint256 nonce;        // per-signer monotonic; tracked off-chain
        uint256 deadline;     // unix seconds; caller enforces freshness
    }

    /// @notice keccak256 of the canonical EIP-712 type string.
    bytes32 internal constant ATTESTATION_TYPEHASH = keccak256(
        "SettlementAttestation(uint256 marketId,uint256 lossAmount,uint256 nonce,uint256 deadline)"
    );

    /// @notice keccak256 of the EIP-712 domain type string.
    bytes32 internal constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );

    // -----------------------------------------------------------------------
    // Pure helpers
    // -----------------------------------------------------------------------

    /// @notice Compute the EIP-712 domain separator. Pure so the verifying
    ///         contract may cache `nameHash` / `versionHash` as immutables and
    ///         re-derive the separator only when chainId is ambiguous (forks).
    /// @param  nameHash         keccak256(bytes(domain name))
    /// @param  versionHash      keccak256(bytes(domain version))
    /// @param  chainId          EIP-155 chain id; caller passes `block.chainid`
    /// @param  verifyingContract address of the contract enforcing the
    ///         attestation (typically `address(this)`)
    function domainSeparator(
        bytes32 nameHash,
        bytes32 versionHash,
        uint256 chainId,
        address verifyingContract
    ) internal pure returns (bytes32) {
        return keccak256(abi.encode(DOMAIN_TYPEHASH, nameHash, versionHash, chainId, verifyingContract));
    }

    /// @notice Compute the struct hash for a `SettlementAttestation`.
    function hashAttestation(SettlementAttestation memory a) internal pure returns (bytes32) {
        return keccak256(abi.encode(ATTESTATION_TYPEHASH, a.marketId, a.lossAmount, a.nonce, a.deadline));
    }

    /// @notice Compute the EIP-712 typed-data digest for a settlement.
    function digest(
        bytes32 domainSep,
        SettlementAttestation memory a
    ) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(hex"1901", domainSep, hashAttestation(a)));
    }

    /// @notice Recover the signer of an EIP-712 typed-data signature.
    /// @dev    Returns `address(0)` on:
    ///           * malformed length (not 65 bytes)
    ///           * out-of-range `v` (not 27 or 28 after normalisation)
    ///           * high-`s` value (EIP-2 malleability guard)
    /// @param  digest_ output of `digest()`
    /// @param  sig     `abi.encodePacked(r, s, v)` — exactly 65 bytes
    function recover(bytes32 digest_, bytes calldata sig) internal pure returns (address) {
        if (sig.length != 65) return address(0);

        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }

        // EIP-2: reject the upper half of `s`. The secp256k1 curve order is
        //   n = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141
        // any signature with s > n/2 is malleable.
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) {
            return address(0);
        }

        if (v != 27 && v != 28) return address(0);

        return ecrecover(digest_, v, r, s);
    }
}
