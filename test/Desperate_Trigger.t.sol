// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {PhaseManager} from "contracts/PhaseManager.sol";

/// @title  Desperate_Trigger — T-A-008 unit coverage for PhaseManager
///         enterDesperateMode preconditions + sticky-flip semantics.
/// @notice Pinned to PRD §6.9 (Desperate Mode trigger) + §6.13 (Phase
///         3 activation table). Sibling test files cover the cross-
///         contract lockouts (Desperate_Invariants) and the Phase 2
///         boundary (Desperate_ApprenticeshipFailureBoundary).
contract Desperate_TriggerTest is Test {
    PhaseManager internal pm;

    address internal constant DEPLOYER = address(0xA11CE);
    address internal constant INTRUDER = address(0xBADBAD);

    // Convention: 1e6 == 1.0 pressure (matches off-chain agent scaling).
    uint256 internal constant PRESSURE_AT_ENTRY = 500_000; // 0.5
    uint256 internal constant CYCLES_HELD = 2;

    // Mirror event for vm.expectEmit.
    event DesperateModeEntered(uint256 pressureAtEntry, uint256 cyclesHeld);

    function setUp() public {
        vm.prank(DEPLOYER);
        pm = new PhaseManager();

        // Climb to Phase 3 — the only phase Desperate Mode is meaningful in.
        vm.startPrank(DEPLOYER);
        pm.transitionToApprenticeship();
        pm.transitionToAdulthood();
        vm.stopPrank();
    }

    // -----------------------------------------------------------------------
    // Acceptance #1 — Phase 3 gating (happy path)
    // -----------------------------------------------------------------------

    /// Brief acceptance: `enterDesperateMode` succeeds in Adulthood (Phase 3)
    /// with `cyclesHeld >= 2`, flips the sticky bit, and lifts the risk cap
    /// from 30% → 50% basis points per PRD §6.9.
    function test_EnterDesperateInAdulthoodFlipsBitAndLiftsRiskCap() public {
        assertFalse(pm.isDesperate(), "desperate flag starts false in Adulthood");
        assertEq(pm.maxBreathRiskPct(), 3000, "normal cap is 30% (basis points)");

        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, CYCLES_HELD);

        assertTrue(pm.isDesperate(), "desperate flag now true");
        assertEq(pm.maxBreathRiskPct(), 5000, "desperate cap is 50% (basis points)");
        // Phase 3 invariant — enterDesperateMode MUST NOT change currentPhase.
        assertEq(
            uint8(pm.currentPhase()),
            uint8(PhaseManager.Phase.Adulthood),
            "currentPhase unchanged after Desperate flip"
        );
    }

    // -----------------------------------------------------------------------
    // Acceptance #2 — NotEnoughPressureCycles revert
    // -----------------------------------------------------------------------

    /// PRD §6.9 trigger requires `pressure >= 0.5` for ≥2 consecutive cycles.
    /// The contract enforces the cycle floor (the pressure observation
    /// itself is off-chain audit-only); a `cyclesHeld < 2` call MUST revert.
    function test_RevertWhen_CyclesHeldBelowMinimum() public {
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.NotEnoughPressureCycles.selector);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, 1);

        // Even a zero-cycle attempt is bounced.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.NotEnoughPressureCycles.selector);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, 0);

        // Negative-path side-effect check — bit stayed false through both
        // failed attempts.
        assertFalse(pm.isDesperate(), "failed calls do not flip the bit");
    }

    // -----------------------------------------------------------------------
    // Acceptance #3 — Event payload
    // -----------------------------------------------------------------------

    /// Track D's DeathWatch + Track B's weight_updater (T-B-009) both
    /// subscribe to `DesperateModeEntered`. The payload (pressureAtEntry,
    /// cyclesHeld) is audit-only but its byte ordering is part of the wire
    /// contract — pin the exact emission.
    function test_DesperateModeEnteredEventPayload() public {
        uint256 pressure = 612_345; // 0.612345 (1e6 scale)
        uint256 cycles = 4;

        vm.expectEmit(false, false, false, true);
        emit DesperateModeEntered(pressure, cycles);

        vm.prank(DEPLOYER);
        pm.enterDesperateMode(pressure, cycles);
    }

    // -----------------------------------------------------------------------
    // Acceptance #4 — Sticky-after-first-call
    // -----------------------------------------------------------------------

    /// PRD §6.9 marks Desperate Mode irreversible AND set-once. There is no
    /// `clearDesperateMode` path (compile-time absence) and a second
    /// `enterDesperateMode` call MUST revert `AlreadyDesperate`. We assert
    /// both the revert AND the flag persistence so a future regression that
    /// silently no-ops would fail this test.
    function test_StickyFlagRejectsSecondEnterCall() public {
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, CYCLES_HELD);
        assertTrue(pm.isDesperate(), "first call flips the bit");

        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.AlreadyDesperate.selector);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, CYCLES_HELD);

        // Bit + cap MUST still reflect the first flip.
        assertTrue(pm.isDesperate(), "flag stays sticky after rejected second call");
        assertEq(pm.maxBreathRiskPct(), 5000, "cap stays at 50% after rejected second call");
    }

    // -----------------------------------------------------------------------
    // Acceptance #5 — NOT callable in Phase 2 (Apprenticeship)
    // -----------------------------------------------------------------------

    /// PRD §6.13: Phase 2 does NOT enable Desperate Mode. Even with a fully
    /// satisfied `cyclesHeld` argument, calling `enterDesperateMode` from
    /// Apprenticeship MUST revert WrongPhase and leave the flag false.
    /// (The companion boundary test file covers Childhood + post-call state.)
    function test_RevertWhen_EnterDesperateFromApprenticeship() public {
        // Spin up a fresh PhaseManager that we stop at Phase 2.
        vm.prank(DEPLOYER);
        PhaseManager pm2 = new PhaseManager();
        vm.prank(DEPLOYER);
        pm2.transitionToApprenticeship();
        assertEq(
            uint8(pm2.currentPhase()),
            uint8(PhaseManager.Phase.Apprenticeship),
            "pm2 stopped at Apprenticeship"
        );

        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.WrongPhase.selector);
        pm2.enterDesperateMode(PRESSURE_AT_ENTRY, CYCLES_HELD);

        assertFalse(pm2.isDesperate(), "Phase 2 attempt does not flip the bit");
        assertEq(pm2.maxBreathRiskPct(), 3000, "Phase 2 cap stays at 30%");
    }

    // -----------------------------------------------------------------------
    // Access control — intruder cannot flip the bit
    // -----------------------------------------------------------------------

    function test_RevertWhen_IntruderEntersDesperate() public {
        vm.prank(INTRUDER);
        vm.expectRevert(PhaseManager.NotOwner.selector);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, CYCLES_HELD);

        assertFalse(pm.isDesperate(), "intruder call leaves bit unset");
    }

    // -----------------------------------------------------------------------
    // No clearDesperateMode admin path — set-once must be enforced by the
    // ABI itself, not just by a runtime check. We probe by call-selector to
    // guarantee the function does not exist (low-level call against a
    // hand-rolled selector returns success=true only if the dispatcher
    // matched; we expect failure).
    // -----------------------------------------------------------------------

    function test_NoClearDesperateModeFunctionExists() public {
        vm.prank(DEPLOYER);
        pm.enterDesperateMode(PRESSURE_AT_ENTRY, CYCLES_HELD);
        assertTrue(pm.isDesperate(), "bit flipped");

        // Probe four plausible "reset" names. None must dispatch.
        // bytes4 selectors are computed off-chain so the call uses a literal.
        bytes4[4] memory selectors = [
            bytes4(keccak256("clearDesperateMode()")),
            bytes4(keccak256("resetDesperateMode()")),
            bytes4(keccak256("exitDesperateMode()")),
            bytes4(keccak256("setDesperate(bool)"))
        ];
        for (uint256 i = 0; i < selectors.length; ++i) {
            vm.prank(DEPLOYER);
            (bool ok, ) = address(pm).call(abi.encodeWithSelector(selectors[i], false));
            assertFalse(ok, "phantom desperate-clearing function dispatched");
        }
        assertTrue(pm.isDesperate(), "bit STILL sticky after every probe");
    }

    // -----------------------------------------------------------------------
    // Fuzz invariant — any (pressure, cycles) input with cycles>=2 from
    // owner in Adulthood SUCCEEDS exactly once and is sticky thereafter.
    // -----------------------------------------------------------------------

    function testFuzz_EnterDesperateIdempotentAfterFirstFlip(uint64 pressure, uint8 rawCycles) public {
        uint256 cycles = uint256(rawCycles) >= 2 ? rawCycles : uint256(2);

        vm.prank(DEPLOYER);
        pm.enterDesperateMode(pressure, cycles);
        assertTrue(pm.isDesperate());
        assertEq(pm.maxBreathRiskPct(), 5000);

        // Second call always reverts AlreadyDesperate regardless of input.
        vm.prank(DEPLOYER);
        vm.expectRevert(PhaseManager.AlreadyDesperate.selector);
        pm.enterDesperateMode(pressure, cycles);

        assertTrue(pm.isDesperate(), "still sticky after fuzz second-call");
    }
}
