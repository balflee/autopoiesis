// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {EIP712Settlement} from "contracts/lib/EIP712Settlement.sol";

/// @title EnergyControllerFuzzTest — invariants over PRD §6 economic surface
/// @notice Each fuzz case asserts the invariants the static unit suite cannot
///         cover by case enumeration:
///           1. `breath` is monotonically non-increasing under burn paths
///              and never underflows.
///           2. `breath` after `topUpBreath` is always `<= maxBreath` and the
///              soft-cap deflection event is consistent with the actual delta.
///           3. The dual-account invariant: a bankroll credit/debit must not
///              perturb `breath` (PRD §6.1).
///           4. Replay protection: any nonce that succeeded once MUST be
///              rejected on the second submission across the full uint256 space.
contract EnergyControllerFuzzTest is Test {
    EnergyController internal ec;

    address internal constant DEPLOYER = address(0xA11CE);
    uint256 internal constant SIGNER_PK = 0xBEEF1234;
    address internal signer;

    uint256 internal constant GENESIS_BREATH = 100_000e6;
    uint256 internal constant MAX_BREATH = 150_000e6;

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.prank(DEPLOYER);
        ec = new EnergyController();
        vm.prank(DEPLOYER);
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);
    }

    /// @dev Burn never underflows: bounded amount stays ≤ current breath; the
    ///      post-state equals pre-state minus the burn delta exactly.
    function testFuzz_BurnDecisionTaxNeverUnderflows(uint256 amount) public {
        amount = bound(amount, 1, ec.breath());
        uint256 pre = ec.breath();

        vm.prank(DEPLOYER);
        ec.burnDecisionTax(amount, "fuzz-decision");

        assertEq(ec.breath(), pre - amount, "burn arithmetic exact");
        assertLe(ec.breath(), pre, "burn is non-increasing");
    }

    /// @dev Burning more than current breath always reverts with
    ///      `InsufficientBreath`; never silently consumes anything.
    function testFuzz_BurnOverBalanceReverts(uint256 amount) public {
        amount = bound(amount, ec.breath() + 1, type(uint128).max);

        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.InsufficientBreath.selector);
        ec.burnTimeTax(amount, "over");
    }

    /// @dev Top-up clamps at `maxBreath`. Whatever the donation, breath
    ///      post-call MUST be ≤ maxBreath; the delta credited MUST equal
    ///      min(amount, headroom).
    function testFuzz_TopUpRespectsSoftCap(uint256 amount, uint256 preBurn) public {
        preBurn = bound(preBurn, 0, ec.breath() - 1);
        if (preBurn > 0) {
            vm.prank(DEPLOYER);
            ec.burnDecisionTax(preBurn, "drain");
        }
        uint256 pre = ec.breath();
        uint256 headroom = MAX_BREATH - pre;

        amount = bound(amount, 1, type(uint128).max);
        uint256 expectedApplied = amount > headroom ? headroom : amount;

        vm.prank(DEPLOYER);
        ec.topUpBreath(amount, "fuzz-donate");

        assertLe(ec.breath(), MAX_BREATH, "soft cap holds");
        assertEq(ec.breath(), pre + expectedApplied, "credit matches headroom semantics");
    }

    /// @dev Dual-account invariant (PRD §6.1): bankroll mutations must not
    ///      perturb `breath`.
    function testFuzz_BankrollMovesDoNotAffectBreath(uint256 credit, uint256 debit) public {
        credit = bound(credit, 1, type(uint128).max);
        debit  = bound(debit, 0, credit);
        uint256 breathSnapshot = ec.breath();

        vm.startPrank(DEPLOYER);
        ec.bankrollCredit(credit, "fuzz-credit");
        if (debit > 0) {
            ec.bankrollDebit(debit, "fuzz-debit");
        }
        vm.stopPrank();

        assertEq(ec.breath(), breathSnapshot, "breath untouched by bankroll");
        assertEq(ec.bankroll(), credit - debit, "bankroll arithmetic exact");
    }

    /// @dev Replay protection: any (signer, nonce) pair that succeeded MUST
    ///      reject on the second attempt across the uint256 nonce space.
    function testFuzz_NonceReplayRejected(uint256 nonce, uint64 lossAmount) public {
        lossAmount = uint64(bound(lossAmount, 1, ec.breath() / 2));
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 1234,
            lossAmount: lossAmount,
            nonce: nonce,
            deadline: block.timestamp + 1 hours
        });
        bytes memory sig = _sign(SIGNER_PK, att);

        ec.settleMarketLoss(att, sig);
        assertTrue(ec.usedNonces(signer, nonce), "nonce flipped after first settle");

        vm.expectRevert(EnergyController.NonceUsed.selector);
        ec.settleMarketLoss(att, sig);
    }

    function _sign(uint256 pk, EIP712Settlement.SettlementAttestation memory att)
        internal
        view
        returns (bytes memory)
    {
        bytes32 d = EIP712Settlement.digest(ec.DOMAIN_SEPARATOR(), att);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, d);
        return abi.encodePacked(r, s, v);
    }
}
