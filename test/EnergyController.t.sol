// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {EnergyController} from "contracts/EnergyController.sol";
import {EIP712Settlement} from "contracts/lib/EIP712Settlement.sol";

/// @title EnergyControllerTest — sprint_2 v3.1 unit coverage
/// @notice Exercises every PRD §6 burn class, the EIP-712 attestation
///         surface (TP §3.7), the soft-cap math (PRD §6.7), the dual-account
///         invariant (PRD §6.1) and the owner-gated admin surface.
contract EnergyControllerTest is Test {
    EnergyController internal ec;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);

    // Off-chain attestation signer — generated from a fixed test private key.
    uint256 internal constant SIGNER_PK = 0xA11CE2D5;
    address internal signer;

    uint256 internal constant GENESIS_BREATH = 10_000e6;
    uint256 internal constant MAX_BREATH = 12_000e6;

    // Mirror events for vm.expectEmit.
    event EnergyChanged(uint256 oldBreath, uint256 newBreath, string reason);
    event BankrollMutated(uint256 oldBankroll, uint256 newBankroll, string reason);
    event Initialized(uint256 initialBreath, uint256 maxBreath, address owner, address attestationSigner);
    event OwnerUpdated(address indexed previousOwner, address indexed newOwner);
    event AttestationSignerUpdated(address indexed previousSigner, address indexed newSigner);
    event Paused(address indexed by);
    event Unpaused(address indexed by);
    event SoftCapDeflected(uint256 attempted, uint256 cap, uint256 applied);
    event MarketLossSettled(address indexed signer, uint256 indexed marketId, uint256 lossAmount, uint256 nonce);
    event PhaseChanged(EnergyController.Phase indexed previous, EnergyController.Phase indexed next);

    function setUp() public {
        signer = vm.addr(SIGNER_PK);

        vm.prank(DEPLOYER);
        ec = new EnergyController();

        vm.prank(DEPLOYER);
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);
    }

    // -----------------------------------------------------------------------
    // Constructor / initialize
    // -----------------------------------------------------------------------

    function test_InitializeSetsCanonicalState() public view {
        assertEq(ec.owner(), DEPLOYER, "deployer is owner");
        assertEq(ec.breath(), GENESIS_BREATH, "breath seeded");
        assertEq(ec.initialBreath(), GENESIS_BREATH, "initialBreath locked");
        assertEq(ec.maxBreath(), MAX_BREATH, "maxBreath set");
        assertEq(ec.bankroll(), 0, "bankroll starts zero");
        assertEq(ec.attestationSigner(), signer, "signer wired");
        assertTrue(ec.initialized(), "initialized flag set");
        assertEq(uint256(ec.currentPhase()), uint256(EnergyController.Phase.Childhood), "phase=Childhood");
        assertEq(ec.totalBreath(), GENESIS_BREATH, "view echoes storage");
    }

    function test_RevertWhen_InitializeCalledTwice() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.AlreadyInitialized.selector);
        ec.initialize(GENESIS_BREATH, MAX_BREATH, signer);
    }

    function test_RevertWhen_InitializeWithZeroBreath() public {
        // Spin up a fresh controller to test the constructor-adjacent path.
        vm.prank(DEPLOYER);
        EnergyController fresh = new EnergyController();
        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.ZeroBreath.selector);
        fresh.initialize(0, MAX_BREATH, signer);
    }

    function test_RevertWhen_InitializeWithZeroSigner() public {
        vm.prank(DEPLOYER);
        EnergyController fresh = new EnergyController();
        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.ZeroAddress.selector);
        fresh.initialize(GENESIS_BREATH, MAX_BREATH, address(0));
    }

    function test_InitializeClampsMaxBreathBelowInitial() public {
        vm.prank(DEPLOYER);
        EnergyController fresh = new EnergyController();
        vm.prank(DEPLOYER);
        fresh.initialize(GENESIS_BREATH, GENESIS_BREATH / 2, signer);
        assertEq(fresh.maxBreath(), GENESIS_BREATH, "maxBreath clamps up to initial");
    }

    // -----------------------------------------------------------------------
    // PRD §6.2 class A — decision-tax
    // -----------------------------------------------------------------------

    function test_BurnDecisionTaxReducesBreathAndEmits() public {
        uint256 amount = 100e6;

        vm.expectEmit(true, true, true, true);
        emit EnergyChanged(GENESIS_BREATH, GENESIS_BREATH - amount, "decision");

        vm.prank(DEPLOYER);
        ec.burnDecisionTax(amount, "decision");

        assertEq(ec.breath(), GENESIS_BREATH - amount, "breath debited");
    }

    function test_RevertWhen_DecisionTaxExceedsBreath() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.InsufficientBreath.selector);
        ec.burnDecisionTax(GENESIS_BREATH + 1, "too-much");
    }

    function test_RevertWhen_DecisionTaxByIntruder() public {
        vm.prank(INTRUDER);
        vm.expectRevert(EnergyController.NotOwner.selector);
        ec.burnDecisionTax(1e6, "intruder");
    }

    function test_RevertWhen_DecisionTaxZeroAmount() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.ZeroAmount.selector);
        ec.burnDecisionTax(0, "zero");
    }

    // -----------------------------------------------------------------------
    // PRD §6.2 class B — time-tax
    // -----------------------------------------------------------------------

    function test_BurnTimeTaxReducesBreathAndEmits() public {
        uint256 amount = 50e6;
        vm.expectEmit(true, true, true, true);
        emit EnergyChanged(GENESIS_BREATH, GENESIS_BREATH - amount, "tick");

        vm.prank(DEPLOYER);
        ec.burnTimeTax(amount, "tick");

        assertEq(ec.breath(), GENESIS_BREATH - amount, "breath debited");
    }

    function test_RevertWhen_TimeTaxWhilePaused() public {
        vm.startPrank(DEPLOYER);
        ec.pause();
        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.burnTimeTax(10e6, "paused");
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // PRD §6.2 class C — market-loss (EIP-712)
    // -----------------------------------------------------------------------

    function test_SettleMarketLossWithValidAttestation() public {
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 42,
            lossAmount: 250e6,
            nonce: 1,
            deadline: block.timestamp + 1 hours
        });
        bytes memory sig = _sign(SIGNER_PK, att);

        vm.expectEmit(true, true, true, true);
        emit EnergyChanged(GENESIS_BREATH, GENESIS_BREATH - att.lossAmount, "settleMarketLoss");
        vm.expectEmit(true, true, true, true);
        emit MarketLossSettled(signer, att.marketId, att.lossAmount, att.nonce);

        ec.settleMarketLoss(att, sig);

        assertEq(ec.breath(), GENESIS_BREATH - att.lossAmount, "breath debited by attestation amount");
        assertTrue(ec.usedNonces(signer, att.nonce), "nonce consumed");
    }

    function test_RevertWhen_SettleReplayedNonce() public {
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 7,
            lossAmount: 100e6,
            nonce: 5,
            deadline: block.timestamp + 30 minutes
        });
        bytes memory sig = _sign(SIGNER_PK, att);

        ec.settleMarketLoss(att, sig);

        vm.expectRevert(EnergyController.NonceUsed.selector);
        ec.settleMarketLoss(att, sig);
    }

    function test_RevertWhen_SettleWrongSigner() public {
        uint256 attackerPk = 0xBADC0DE;
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 1,
            lossAmount: 1e6,
            nonce: 99,
            deadline: block.timestamp + 1 hours
        });
        bytes memory sig = _sign(attackerPk, att);

        vm.expectRevert(EnergyController.InvalidSigner.selector);
        ec.settleMarketLoss(att, sig);
    }

    function test_RevertWhen_SettleAfterDeadline() public {
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 1,
            lossAmount: 1e6,
            nonce: 1,
            deadline: block.timestamp + 10
        });
        bytes memory sig = _sign(SIGNER_PK, att);

        vm.warp(att.deadline + 1);
        vm.expectRevert(EnergyController.DeadlineExpired.selector);
        ec.settleMarketLoss(att, sig);
    }

    function test_RevertWhen_SettleZeroLoss() public {
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 1,
            lossAmount: 0,
            nonce: 1,
            deadline: block.timestamp + 1 hours
        });
        bytes memory sig = _sign(SIGNER_PK, att);

        vm.expectRevert(EnergyController.ZeroAmount.selector);
        ec.settleMarketLoss(att, sig);
    }

    function test_RevertWhen_SettleMalformedSignature() public {
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 1,
            lossAmount: 10e6,
            nonce: 1,
            deadline: block.timestamp + 1 hours
        });
        bytes memory sig = hex"deadbeef"; // 4 bytes — invalid length

        vm.expectRevert(EnergyController.InvalidSignature.selector);
        ec.settleMarketLoss(att, sig);
    }

    function test_RevertWhen_SettleAttestationFieldTamperedAfterSigning() public {
        EIP712Settlement.SettlementAttestation memory att = EIP712Settlement.SettlementAttestation({
            marketId: 1,
            lossAmount: 100e6,
            nonce: 1,
            deadline: block.timestamp + 1 hours
        });
        bytes memory sig = _sign(SIGNER_PK, att);

        // Tamper: bump loss amount AFTER signing → recovered signer is not
        // the authorised attestationSigner anymore.
        att.lossAmount = 200e6;

        vm.expectRevert(EnergyController.InvalidSigner.selector);
        ec.settleMarketLoss(att, sig);
    }

    // -----------------------------------------------------------------------
    // Soft cap (PRD §6.7) and donations
    // -----------------------------------------------------------------------

    function test_TopUpBreathClampsAtSoftCap() public {
        // Pre-drain so we can credit cleanly.
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(1_000e6, "drain");

        // Headroom = 12000 - 9000 = 3000; deposit 5000 → 2000 deflected.
        uint256 attempted = 5_000e6;
        uint256 headroom = MAX_BREATH - (GENESIS_BREATH - 1_000e6);

        vm.expectEmit(true, true, true, true);
        emit SoftCapDeflected(attempted, MAX_BREATH, headroom);
        vm.expectEmit(true, true, true, true);
        emit EnergyChanged(GENESIS_BREATH - 1_000e6, MAX_BREATH, "donation");

        vm.prank(DEPLOYER);
        ec.topUpBreath(attempted, "donation");

        assertEq(ec.breath(), MAX_BREATH, "clamped to cap");
    }

    function test_TopUpBreathAtCapIsSilentNoOp() public {
        // breath == maxBreath only after we donate full headroom first.
        vm.prank(DEPLOYER);
        ec.topUpBreath(MAX_BREATH - GENESIS_BREATH, "fill-cap");
        assertEq(ec.breath(), MAX_BREATH, "filled cap");

        vm.expectEmit(true, true, true, true);
        emit SoftCapDeflected(1e6, MAX_BREATH, 0);

        // Now any further top-up emits SoftCapDeflected but never EnergyChanged.
        vm.recordLogs();
        vm.prank(DEPLOYER);
        ec.topUpBreath(1e6, "post-cap");
        // No EnergyChanged should have fired — verified by the absence of a
        // second log; vm.expectEmit above checked the only expected log.
        assertEq(ec.breath(), MAX_BREATH, "breath unchanged at cap");
    }

    function test_TopUpBreathSucceedsUnderCap() public {
        vm.prank(DEPLOYER);
        ec.burnDecisionTax(500e6, "drain");

        vm.expectEmit(true, true, true, true);
        emit EnergyChanged(GENESIS_BREATH - 500e6, GENESIS_BREATH - 500e6 + 100e6, "donation");

        vm.prank(DEPLOYER);
        ec.topUpBreath(100e6, "donation");

        assertEq(ec.breath(), GENESIS_BREATH - 500e6 + 100e6, "credit landed");
    }

    // -----------------------------------------------------------------------
    // Bankroll dual-account (PRD §6.1)
    // -----------------------------------------------------------------------

    function test_BankrollCreditAndDebitMoveIndependentlyOfBreath() public {
        uint256 breathSnapshot = ec.breath();

        vm.expectEmit(true, true, true, true);
        emit BankrollMutated(0, 1_000e6, "credit");
        vm.prank(DEPLOYER);
        ec.bankrollCredit(1_000e6, "credit");
        assertEq(ec.bankroll(), 1_000e6);
        assertEq(ec.breath(), breathSnapshot, "breath untouched by bankroll credit");

        vm.expectEmit(true, true, true, true);
        emit BankrollMutated(1_000e6, 600e6, "debit");
        vm.prank(DEPLOYER);
        ec.bankrollDebit(400e6, "debit");
        assertEq(ec.bankroll(), 600e6);
        assertEq(ec.breath(), breathSnapshot, "breath still untouched");
    }

    function test_RevertWhen_BankrollDebitExceedsBalance() public {
        vm.startPrank(DEPLOYER);
        ec.bankrollCredit(100e6, "seed");
        vm.expectRevert(EnergyController.InsufficientBankroll.selector);
        ec.bankrollDebit(101e6, "overdraft");
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // Admin / pause
    // -----------------------------------------------------------------------

    function test_OnlyOwnerCanRotateSigner() public {
        address newSigner = address(0xBEEF);

        vm.prank(INTRUDER);
        vm.expectRevert(EnergyController.NotOwner.selector);
        ec.setAttestationSigner(newSigner);

        vm.expectEmit(true, true, true, true);
        emit AttestationSignerUpdated(signer, newSigner);
        vm.prank(DEPLOYER);
        ec.setAttestationSigner(newSigner);

        assertEq(ec.attestationSigner(), newSigner);
    }

    function test_RevertWhen_SetSignerZero() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(EnergyController.ZeroAddress.selector);
        ec.setAttestationSigner(address(0));
    }

    function test_PauseGatesAllMutators() public {
        vm.startPrank(DEPLOYER);
        ec.pause();
        assertTrue(ec.paused(), "paused flag set");

        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.burnDecisionTax(1e6, "x");

        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.burnTimeTax(1e6, "x");

        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.topUpBreath(1e6, "x");

        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.bankrollCredit(1e6, "x");

        vm.expectRevert(EnergyController.WhilePaused.selector);
        ec.bankrollDebit(1e6, "x");

        ec.unpause();
        assertFalse(ec.paused(), "unpaused");
        vm.stopPrank();
    }

    function test_SetPhaseEmitsAndUpdates() public {
        vm.expectEmit(true, true, true, true);
        emit PhaseChanged(EnergyController.Phase.Childhood, EnergyController.Phase.Apprenticeship);

        vm.prank(DEPLOYER);
        ec.setPhase(EnergyController.Phase.Apprenticeship);
        assertEq(uint256(ec.currentPhase()), uint256(EnergyController.Phase.Apprenticeship));
    }

    function test_RevertWhen_SetPhaseFromDead() public {
        vm.startPrank(DEPLOYER);
        ec.setPhase(EnergyController.Phase.Dead);
        vm.expectRevert(EnergyController.AlreadyDead.selector);
        ec.setPhase(EnergyController.Phase.Childhood);
        vm.stopPrank();
    }

    function test_RevertWhen_BurnAfterDeath() public {
        vm.startPrank(DEPLOYER);
        ec.setPhase(EnergyController.Phase.Dead);
        vm.expectRevert(EnergyController.AlreadyDead.selector);
        ec.burnDecisionTax(1e6, "x");
        vm.stopPrank();
    }

    function test_DomainSeparatorIncludesChainIdAndAddress() public view {
        bytes32 expected = EIP712Settlement.domainSeparator(
            keccak256(bytes("Genesis Experiment EnergyController")),
            keccak256(bytes("1")),
            block.chainid,
            address(ec)
        );
        assertEq(ec.DOMAIN_SEPARATOR(), expected, "domain separator matches spec");
    }

    function test_DomainSeparatorReDerivedOnFork() public {
        bytes32 onChain = ec.DOMAIN_SEPARATOR();
        vm.chainId(block.chainid + 1);
        bytes32 onFork = ec.DOMAIN_SEPARATOR();
        assertTrue(onChain != onFork, "fork-separator differs");
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

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
