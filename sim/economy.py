"""BREATH economy engine — sprint_1 skeleton.

This is the offline twin of ``contracts/EnergyController.sol`` — same
state-machine, same constants, but pure Python with no chain dependency.
A calibration sweep instantiates one :class:`BreathEconomy` per
parameter combination, drives it through N synthetic ticks per archetype,
then reads back the resulting trajectories for objective scoring.

Sprint_1 ships the CONSTRUCTOR ONLY. Burn / settle / lung-expansion
mechanics land in sprint_2 (T-C-002 onwards), per PRD §14.1 + §6
(BREATH lifecycle) and TECHNICAL_PLAN.md §4 (Day-1 Track C deliverable).
"""

from __future__ import annotations

from sim.params import ParamSpace


class BreathEconomy:
    """Pure-Python mirror of the BREATH state machine.

    The :class:`ParamSpace` passed at construction is **frozen** for the
    lifetime of the instance — calibration treats each combination as
    independent. The sprint_2 implementation will add ``tick()``,
    ``burn()``, ``settle()``, and ``lung_expand()`` methods that mutate
    a private :class:`_BreathState` dataclass; this round we expose only
    enough surface for mypy to typecheck the constructor + the
    ``params`` accessor.
    """

    def __init__(self, params: ParamSpace) -> None:
        if not isinstance(params, ParamSpace):
            raise TypeError(
                f"BreathEconomy expects ParamSpace, got {type(params).__name__}"
            )
        self._params: ParamSpace = params
        # ``breath`` is initialised to the param-defined starting value;
        # the actual mutation path (tick → burn → settle) lands in sprint_2.
        self._breath: float = params.initial_breath

    @property
    def params(self) -> ParamSpace:
        """Return the frozen :class:`ParamSpace` driving this economy."""
        return self._params

    @property
    def breath(self) -> float:
        """Current BREATH balance. Sprint_1: always equals
        ``params.initial_breath``. Sprint_2: mutates per tick."""
        return self._breath

    def tick(self) -> None:
        """Advance the economy by one tick. Real implementation in
        sprint_2 — sprint_1 raises so accidental calls fail loud.
        """
        raise NotImplementedError("BreathEconomy.tick lands in sprint_2 (T-C-002)")
