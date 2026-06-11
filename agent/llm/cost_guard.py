"""CostGuard — running USD budget tracker for the LLM spend.

TECHNICAL_PLAN §15 Gap 5 caps the deployed agent's LLM spend at $25 USD
across the full hackathon run. The cost guard is the in-process
enforcer of that cap:

* Every successful ``_LLMClient.structured_call`` is paired with a
  :meth:`CostGuard.record` invocation that adds the call's estimated
  cost to the running total.
* :meth:`CostGuard.is_warning` returns ``True`` once the running total
  crosses 80% of the hard cap. The agent_loop emits a structured
  warning event so the dashboard / operator can see the runway shrinking.
* :meth:`CostGuard.is_exhausted` returns ``True`` once the total meets
  or exceeds the hard cap. The engine layer treats this as a
  hard-short-circuit to the deterministic template (i.e. no further
  LLM calls fire) — the agent_loop never crashes, it just stops
  paying for sentiment / reflection inference.

Look-ahead bias documentation (brief acceptance criterion):

    The cost guard's ``record`` / ``is_warning`` / ``is_exhausted``
    inspect ONLY the running total accumulated by prior ticks +
    in-flight calls in the **current** tick. There is no
    cross-tick lookahead — the state is monotonically increasing per
    `record()` invocation and never reads a future tick's spend. The
    look-ahead auditor only scans modules under ``features/`` or named
    ``*_features.py`` (see ``.dev/harness/tools/lookahead_auditor.py``);
    this module is excluded by name. The behavioural test
    :func:`tests.agent.llm.test_cost_guard.test_cost_guard_tick_window_is_monotonic`
    asserts the tick-window-only invariant as a defence-in-depth check.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

# TECHNICAL_PLAN §15 Gap 5 — explicit USD figures.
DEFAULT_HARD_CAP_USD: Final[float] = 25.0
DEFAULT_WARNING_FRACTION: Final[float] = 0.80

# T-B-029 sprint_10 — separate L3 budget per the brief. L3 advisor lives
# in a slow loop (fires every ~100 ticks) and burns a NON-trivial Gemini
# call per fire. We track it on a SEPARATE budget so a hot L1/L2 quarter
# does not silently starve the L3 slow loop, and vice-versa.
#
# Env var override (``L3_MONTHLY_BUDGET_USD``) lets ops dial the cap
# without a code change; default $10 matches the brief.
DEFAULT_L3_MONTHLY_BUDGET_USD: Final[float] = 10.0
L3_BUDGET_ENV: Final[str] = "L3_MONTHLY_BUDGET_USD"


class CostExhaustedError(RuntimeError):
    """Raised when a caller insists on recording past the hard cap.

    The canonical agent_loop path checks :meth:`CostGuard.is_exhausted`
    BEFORE calling the LLM, so this exception is a defence-in-depth
    guard against a buggy caller that forgets the precheck. Engines
    treat the exhausted state as a hard short-circuit; this exception
    is reserved for the misuse path.
    """


@dataclass(frozen=True)
class CostEvent:
    """One USD spend event — emitted for the dashboard / audit log.

    Frozen + dataclass-pure so the agent_loop can append events to its
    journal without worrying about mutation. The ``label`` is a short
    string for the engine that paid (e.g. ``"sentiment"`` /
    ``"reflection"``); the ``kind`` distinguishes the budgetary state
    transitions consumers care about.

    ``kind`` is intentionally not a :class:`StrEnum` — keeping it a
    plain string lets the dashboard render new tags without a Pydantic
    schema bump.
    """

    label: str
    usd: float
    total_after: float
    kind: str  # one of: "ok", "warning", "exhausted"


@dataclass
class CostGuard:
    """In-memory running USD budget tracker.

    Construction is the single source of truth for the policy
    parameters; copy semantics keep tests trivial. ``record()`` is
    additive; there is no decrement path (refunds are out of scope —
    the LLM provider does not refund failed calls).

    Parameters
    ----------
    hard_cap_usd:
        Maximum USD spend before :meth:`is_exhausted` returns True.
        Defaults to :data:`DEFAULT_HARD_CAP_USD` ($25).

    warning_fraction:
        Fraction of the hard cap at which :meth:`is_warning` flips on.
        Defaults to 0.80 — the agent_loop emits a structured warning
        so the operator can see the runway shrinking before the cap
        actually hits.
    """

    hard_cap_usd: float = DEFAULT_HARD_CAP_USD
    warning_fraction: float = DEFAULT_WARNING_FRACTION
    total_usd: float = 0.0
    events: list[CostEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.hard_cap_usd <= 0.0:
            raise ValueError(
                f"hard_cap_usd must be > 0 (got {self.hard_cap_usd})"
            )
        if not (0.0 < self.warning_fraction < 1.0):
            raise ValueError(
                "warning_fraction must lie in (0.0, 1.0) "
                f"(got {self.warning_fraction})"
            )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def record(self, *, label: str, usd: float) -> CostEvent:
        """Add ``usd`` to the running total and return the resulting event.

        Raises :class:`CostExhaustedError` if the caller is recording
        a NEW cost after the cap is already met. (Concretely: the
        precheck path of the engine MUST call :meth:`is_exhausted`
        first; this exception is defence-in-depth against a buggy
        caller.) ``usd < 0`` is rejected — no refunds.
        """
        if usd < 0.0:
            raise ValueError(f"usd must be non-negative (got {usd})")
        if self.is_exhausted():
            raise CostExhaustedError(
                f"refusing to record ${usd:.4f} for {label}: "
                f"total ${self.total_usd:.4f} already at cap ${self.hard_cap_usd:.4f}"
            )

        self.total_usd += usd
        kind = "ok"
        if self.is_exhausted():
            kind = "exhausted"
        elif self.is_warning():
            kind = "warning"

        event = CostEvent(
            label=label,
            usd=usd,
            total_after=self.total_usd,
            kind=kind,
        )
        self.events.append(event)
        return event

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def remaining_usd(self) -> float:
        """Return ``hard_cap_usd - total_usd`` clamped to ``[0, hard_cap]``.

        Never negative — once the cap is met the remaining budget is
        zero, even if a buggy caller pushed the total over via the
        misuse path. The dashboard renders this directly.
        """
        return max(0.0, self.hard_cap_usd - self.total_usd)

    def is_warning(self) -> bool:
        """True once ``total_usd`` crosses ``warning_fraction * hard_cap``."""
        return self.total_usd >= self.warning_fraction * self.hard_cap_usd

    def is_exhausted(self) -> bool:
        """True once ``total_usd`` meets or exceeds ``hard_cap_usd``."""
        return self.total_usd >= self.hard_cap_usd


# --------------------------------------------------------------------------- #
# L3 budget tracker — T-B-029 sprint_10.
# --------------------------------------------------------------------------- #


class L3CostGuard(CostGuard):
    """Separate budget tracker for the L3 meta-optimizer (sprint_10 T-B-029).

    Same arithmetic as :class:`CostGuard` (the parent) but constructed
    with a DIFFERENT default cap so L1 sentiment / L2 reflection spend
    cannot starve L3 advice (and vice-versa). The
    :class:`StrategyAdvisorImpl` checks :meth:`is_exhausted` BEFORE
    calling the LLM and returns ``[]`` + WARNING log on exhaustion —
    the same fail-soft posture the engine layer uses with the
    shared-budget :class:`CostGuard`.

    Construct via :meth:`from_env` in production so the cap honours
    the operator override (``L3_MONTHLY_BUDGET_USD`` env var). Tests
    construct directly with an explicit ``hard_cap_usd`` so they
    don't have to fight pytest's env isolation.
    """

    @classmethod
    def from_env(cls) -> L3CostGuard:
        """Build an L3CostGuard from the ``L3_MONTHLY_BUDGET_USD`` env var.

        Falls back to :data:`DEFAULT_L3_MONTHLY_BUDGET_USD` ($10) when
        the env var is absent or unparseable. Logging the fallback is
        the caller's responsibility — we keep this constructor pure
        so it's trivially testable.
        """
        raw = os.environ.get(L3_BUDGET_ENV)
        if raw is None or not raw.strip():
            cap = DEFAULT_L3_MONTHLY_BUDGET_USD
        else:
            try:
                cap = float(raw)
            except (TypeError, ValueError):
                cap = DEFAULT_L3_MONTHLY_BUDGET_USD
            if cap <= 0.0:
                cap = DEFAULT_L3_MONTHLY_BUDGET_USD
        return cls(hard_cap_usd=cap)


__all__ = [
    "DEFAULT_HARD_CAP_USD",
    "DEFAULT_L3_MONTHLY_BUDGET_USD",
    "DEFAULT_WARNING_FRACTION",
    "L3_BUDGET_ENV",
    "CostEvent",
    "CostExhaustedError",
    "CostGuard",
    "L3CostGuard",
]
