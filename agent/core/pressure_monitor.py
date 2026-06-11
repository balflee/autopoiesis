# Greek letters in the module docstring mirror PRD §6.8 notation; the
# look-ahead auditor reads `pressure_monitor` as a tick-window consumer
# (no future fixtures sneak in).
"""Pressure monitor — tracks survival pressure for the Desperate-Mode trigger.

PRD §6.8 defines per-tick **survival pressure**::

    projected_hours = breath / effective_burn_rate / 60
    pressure        = clamp((TARGET_HORIZON - projected_hours)/TARGET_HORIZON,
                            0, 1)

``TARGET_HORIZON`` is **36 hours** (the placeholder per PRD §6.8). The
calculation is *forward-looking but local* — it consumes only the current
tick's BREATH balance + the running per-minute burn rate. By construction
no future fixture leaks in (the look-ahead auditor's PRD §6.8 check
covers this module by data-flow: every input has ``available_at`` ≤ tick
clock).

PRD §6.9 names the trigger condition::

    pressure ≥ 0.5  AND  held for ≥ MIN_PRESSURE_CYCLES (=2)  AND
    phase == PHASE_3_MASTER

When all three hold, the monitor emits :class:`EnterDesperateModeIntent`.
The intent is consumed by the agent main loop (T-B-009 wiring) which is
the *only* layer authorised to dispatch
``PhaseManager.enterDesperateMode()`` on chain. The monitor itself is
chain-free — keeps the surface unit-testable + the look-ahead auditor
happy.

State reset rules (per acceptance criterion)
--------------------------------------------

* Phase transition Phase 2 (Apprenticeship) → Phase 3 (Adulthood) RESETS
  the counter. The 2-cycle hold window must accrue *within* Phase 3.
* "Apprenticeship failure" (a Phase 2 termination scenario where the
  agent never reaches Phase 3) does NOT reset — it never sees Phase 3
  in the first place, so the counter never started accruing. Only the
  Phase 2 → Phase 3 transition is the reset event.
* A Phase-3 tick with ``pressure < MIN_PRESSURE`` decrements the
  counter to 0 (the hold must be *consecutive*).
* Once the intent fires, the monitor latches; subsequent ticks do NOT
  re-emit. Desperate Mode is "不可逆" per PRD §6.9.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from agent.core.state import Phase

# Per-tick threshold above which the cycle counts toward the trigger
# window. PRD §6.9: "pressure >= 0.5 持续 2 cycles".
MIN_PRESSURE: Final[float] = 0.5

# Consecutive-cycles window. Mirrors the on-chain PhaseManager
# `MIN_PRESSURE_CYCLES` view constant (sprint_5 T-A-008) — kept aligned
# so the off-chain monitor + the on-chain re-check gate agree.
MIN_PRESSURE_CYCLES: Final[int] = 2

# PRD §6.8 placeholder for the survival horizon target. 36h reflects the
# "agent should survive a long weekend" demo posture; calibration
# (Track C sweeps) may later tune this — surfacing as a constant lets
# a future selected_params.json read flow through.
TARGET_HORIZON_HOURS: Final[float] = 36.0

# Smallest burn rate the projected-hours formula is willing to divide
# by. Without this floor a temporarily-zero burn rate (no decisions
# this tick + idle passive burn) would compute projected_hours = ∞ and
# stamp pressure = 0 — masking a legitimate low-breath crisis. Anchored
# at 1 BREATH/hour so the agent is "safe" only if it could survive
# TARGET_HORIZON hours at the *minimum* sane burn rate.
MIN_BURN_RATE_PER_HOUR: Final[float] = 1.0


@dataclass(frozen=True)
class PressureSample:
    """One tick's pressure observation.

    Bundled here (rather than spread across loose floats) so callers
    can journal the entire sample to MemoryBank without re-running the
    computation. The dashboard's vitals panel also consumes this dict
    directly.
    """

    breath: float
    effective_burn_rate_per_hour: float
    projected_hours: float
    pressure: float
    phase: Phase

    def to_dict(self) -> dict[str, float | str]:
        """Render for JSON persistence — pressure_golden.json shape."""
        return {
            "breath": float(self.breath),
            "effective_burn_rate_per_hour": float(self.effective_burn_rate_per_hour),
            "projected_hours": float(self.projected_hours),
            "pressure": float(self.pressure),
            "phase": self.phase.value,
        }


@dataclass(frozen=True)
class EnterDesperateModeIntent:
    """Off-chain signal that the agent main loop SHOULD dispatch
    ``PhaseManager.enterDesperateMode(pressureAtEntry, cyclesHeld)``.

    Carries the diagnostics the on-chain re-check gate needs
    (pressure, cycles_held) so the agent's chain adapter can pass them
    through verbatim. The intent does NOT itself perform any chain I/O
    — the main loop owns the dispatch + retry + degraded-mode warning
    handling per PRD's TP §4.1 "cannot crash tick" invariant.
    """

    pressure_at_entry: float
    cycles_held: int
    phase: Phase


def compute_pressure(
    *,
    breath: float,
    effective_burn_rate_per_hour: float,
) -> tuple[float, float]:
    """Return ``(projected_hours, pressure)`` per PRD §6.8.

    ``effective_burn_rate_per_hour`` is the rolling per-hour BREATH
    consumption — the agent main loop computes it from recent ticks'
    burn (passive + decision tax). The monitor accepts the rate as
    an input so unit tests can pin it deterministically.

    Edge cases:

    * ``effective_burn_rate_per_hour`` floored at :data:`MIN_BURN_RATE_PER_HOUR`
      to avoid divide-by-zero + spurious zero-pressure ticks when the
      agent is briefly idle.
    * ``breath`` clamped to ``[0, ∞)`` — a negative balance is a chain
      adapter bug; the safer surface here is pressure = 1 (the agent
      is in crisis), which the clamp naturally produces.
    """
    safe_breath = max(0.0, float(breath))
    safe_rate = max(MIN_BURN_RATE_PER_HOUR, float(effective_burn_rate_per_hour))
    projected_hours = safe_breath / safe_rate
    raw_pressure = (TARGET_HORIZON_HOURS - projected_hours) / TARGET_HORIZON_HOURS
    pressure = max(0.0, min(1.0, raw_pressure))
    return projected_hours, pressure


class PressureMonitor:
    """Tracks the rolling consecutive-cycles counter for Desperate Mode.

    Single instance owned by the agent main loop. Lifecycle:

    1. ``observe(...)`` once per tick. Returns a :class:`PressureSample`
       + an optional :class:`EnterDesperateModeIntent`.
    2. ``handle_phase_transition(...)`` called when the on-chain phase
       changes. Only the Phase 2 → Phase 3 transition resets the
       counter; all other transitions are no-ops.
    3. After ``intent`` has fired the monitor LATCHES; subsequent
       ``observe`` calls still return the live :class:`PressureSample`
       (the dashboard wants the pressure even after entry) but
       ``intent`` is ``None`` forever (Desperate is irreversible).

    Parameters
    ----------
    min_pressure:
        Override the threshold (default :data:`MIN_PRESSURE`). The
        on-chain gate is fixed at 0.5; the override is for tests +
        calibration sweeps.

    min_cycles:
        Override the consecutive-cycles requirement (default
        :data:`MIN_PRESSURE_CYCLES`). Tests use ``1`` to exercise the
        single-cycle path.
    """

    name = "pressure_monitor"

    def __init__(
        self,
        *,
        min_pressure: float = MIN_PRESSURE,
        min_cycles: int = MIN_PRESSURE_CYCLES,
    ) -> None:
        if not 0.0 < min_pressure <= 1.0:
            raise ValueError(
                f"min_pressure must be in (0, 1] (got {min_pressure})"
            )
        if min_cycles < 1:
            raise ValueError(f"min_cycles must be >= 1 (got {min_cycles})")
        self._min_pressure = float(min_pressure)
        self._min_cycles = int(min_cycles)
        self._cycles_held: int = 0
        self._latched: bool = False

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def cycles_held(self) -> int:
        """Number of consecutive Phase-3 ticks with pressure ≥ threshold.

        Exposed for tests + the dashboard vitals frame (the bar can
        render '1/2 cycles toward Desperate'). The counter is bounded
        below by 0 and never exceeds :attr:`min_cycles` (it latches
        when it reaches the trigger window — subsequent ticks keep it
        pinned).
        """
        return self._cycles_held

    @property
    def latched(self) -> bool:
        """True once an :class:`EnterDesperateModeIntent` has fired."""
        return self._latched

    def observe(
        self,
        *,
        breath: float,
        effective_burn_rate_per_hour: float,
        phase: Phase,
    ) -> tuple[PressureSample, EnterDesperateModeIntent | None]:
        """Run one tick's pressure observation.

        Returns the per-tick :class:`PressureSample` + an optional
        :class:`EnterDesperateModeIntent` when the trigger conditions
        fire (Phase 3 AND pressure ≥ threshold for min_cycles in a
        row, first-time only — latches afterward).

        The function is pure with respect to chain / network — it only
        mutates ``self._cycles_held`` + ``self._latched``. The agent
        main loop is the *only* layer authorised to dispatch the
        on-chain ``enterDesperateMode`` call.
        """
        projected_hours, pressure = compute_pressure(
            breath=breath,
            effective_burn_rate_per_hour=effective_burn_rate_per_hour,
        )
        sample = PressureSample(
            breath=float(breath),
            effective_burn_rate_per_hour=float(effective_burn_rate_per_hour),
            projected_hours=projected_hours,
            pressure=pressure,
            phase=phase,
        )

        # Counter update — only Phase 3 ticks contribute. Phase 1/2/4
        # ticks always reset to 0 (the trigger window must accrue
        # entirely within Phase 3).
        if phase != Phase.PHASE_3_MASTER:
            self._cycles_held = 0
            return sample, None

        if pressure >= self._min_pressure:
            # Bound the counter at min_cycles so it stays pinned after
            # entry rather than growing unboundedly (helps the dashboard
            # render a stable '2/2' display once latched).
            self._cycles_held = min(self._cycles_held + 1, self._min_cycles)
        else:
            # Hold must be CONSECUTIVE — any sub-threshold tick resets.
            self._cycles_held = 0

        intent: EnterDesperateModeIntent | None = None
        if (
            not self._latched
            and self._cycles_held >= self._min_cycles
            and phase == Phase.PHASE_3_MASTER
        ):
            intent = EnterDesperateModeIntent(
                pressure_at_entry=pressure,
                cycles_held=self._cycles_held,
                phase=phase,
            )
            self._latched = True
        return sample, intent

    def handle_phase_transition(
        self,
        *,
        from_phase: Phase,
        to_phase: Phase,
    ) -> None:
        """Reset the counter on the Phase 2 → Phase 3 transition only.

        The acceptance criterion pins this exact boundary:

            "pressure_monitor cycles counter reset by Phase transition
            (Apprenticeship → Adulthood) but never by
            ApprenticeshipFailure (Phase 2-only reset)"

        Apprenticeship failure is a Phase-2-only terminal — the agent
        dies in Phase 2 and never reaches Phase 3, so the counter never
        accrued (it only counts Phase-3 ticks). The function is still a
        no-op on every transition other than 2→3 so a future calibration
        change cannot quietly add resets through the wrong boundary.
        """
        if from_phase == Phase.PHASE_2_APPRENTICE and to_phase == Phase.PHASE_3_MASTER:
            self._cycles_held = 0


__all__ = [
    "MIN_BURN_RATE_PER_HOUR",
    "MIN_PRESSURE",
    "MIN_PRESSURE_CYCLES",
    "TARGET_HORIZON_HOURS",
    "EnterDesperateModeIntent",
    "PressureMonitor",
    "PressureSample",
    "compute_pressure",
]
