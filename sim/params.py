"""ParamSpace — the BREATH economic parameter schema.

This module defines :class:`ParamSpace`, the dataclass that enumerates the
parameters Layer 2 calibration sweeps over. The same shape is consumed
later by Track A (Solidity constants in ``EnergyController.sol``) and
Track B (agent runtime defaults under ``agent/engines/``), so the JSON
round-trip enforced here is the **cross-track contract** for the BREATH
economy values.

Source of truth
---------------

Every field below cites the PRD section that introduced it. Sprint_1
(T-C-001) shipped the **five** core BREATH parameters listed in PRD
§14.1. Sprint_2 (T-C-002) adds the **four** additional dimensions
required for the first LHS sweep — ``e_decision_tax``,
``e_time_tax_per_tick``, ``soft_cap_threshold``, ``desperate_threshold``
— all PRD §14.1 entries per the T-C-002 task brief. The remaining slots
in PRD §14.1 land in later sprints when Bayesian-Optimization needs
them.

Round-trip semantics
--------------------

:meth:`ParamSpace.to_json` and :meth:`ParamSpace.from_json` MUST be exact
inverses: ``ParamSpace.from_json(p.to_json()) == p`` for every valid
``p``. The harness reproducibility check (Track C calibration validator,
per DEV_FRAMEWORK §26 T2.7) replays a saved JSON spec and asserts
bitwise equality against the original sweeper input — anything less and
the reviewer fails the run.

LHS dimensions
--------------

Not every field is sampled by the LHS sweeper. :data:`LHS_DIMS` lists the
exact names that participate in calibration; :data:`LHS_BOUNDS` gives
the ``(low, high)`` range per dim. Fields outside :data:`LHS_DIMS` stay
at their dataclass defaults during a sweep. The four T-C-002 mandatory
dims are guaranteed to appear in :data:`LHS_DIMS` — the Track C
calibration validator greps for them in the sweep output.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Final


@dataclass(frozen=True)
class ParamSpace:
    """The BREATH economic parameter space.

    All fields are :class:`float` so the JSON serialiser is trivially
    round-trippable. Booleans, enums, and nested structures are deferred
    until a sprint requires them.

    Attributes
    ----------
    initial_breath:
        Starting BREATH balance for a fresh agent at PHASE_1_INFANCY.
        Per PRD §14.1 the default sweep range is roughly [800, 1500];
        a single calibration run pins one value.
    passive_burn_rate:
        BREATH consumed per tick when ``action.kind == NO_BET``. Per
        PRD §6 NO_BET is NOT free; the passive burn is the floor cost
        of being alive. PRD §14.1 sweeps [0.5, 3.0] BREATH/tick.
    conversion_rate:
        USD → BREATH conversion factor used when bankroll is converted
        into agent breath via the Lung Expansion ritual (PRD §6.4).
        PRD §14.1 sweeps roughly [0.5, 2.0].
    target_horizon:
        The agent's target lifetime in days, a soft objective the
        weight updater (PRD §4.4) tries to match against the achieved
        lifetime. Calibration aims for a mean lifetime of 3-7 days
        per PRD §14.2 — TARGET_HORIZON is one of the dials.
    min_bet_size:
        Minimum USD bet the agent will place. Below this the bet is
        rejected and the agent emits NO_BET. Prevents thrashing on
        zero-EV slivers and bounds Polymarket gas wastage; PRD §14.1.
    e_decision_tax:
        BREATH consumed per tick when ``action.kind == BET`` — the
        cognitive overhead of making a decision. Added in T-C-002 per
        PRD §14.1 to give the LHS sweep direct control over Optimist /
        Satisficer death rates. Sweep range [0.5, 5.0].
    e_time_tax_per_tick:
        Universal time tax burnt every tick regardless of action. This
        is the "you're alive, that costs breath" floor distinct from
        :attr:`passive_burn_rate` (which is NO_BET-specific). PRD §14.1
        sweep range [0.1, 2.0].
    soft_cap_threshold:
        BREATH balance above which the soft cap suppresses further
        Lung Expansion gains (PRD §6.4 soft cap mechanic). Below the
        cap, USD→BREATH conversions land in full; above it the
        conversion is dampened. PRD §14.1 sweep range [1500, 4000].
    desperate_threshold:
        BREATH balance below which Desperate Mode activates (PRD §6.5).
        While desperate, the agent loosens its edge threshold and the
        weight updater amplifies high-volatility actions. PRD §14.1
        sweep range [50, 400].

    Notes
    -----
    The ordering of fields here is the canonical key ordering for JSON
    serialisation — :meth:`to_json` uses ``sort_keys=True`` so reviewers
    diffing two ``selected_params.json`` artifacts get a stable layout.
    """

    # ── Spec anchors: PRD §14.1 BREATH parameter table ────────────────
    # Five core dims shipped in sprint_1 (T-C-001) ↓
    initial_breath: float = 1000.0
    passive_burn_rate: float = 1.0
    conversion_rate: float = 1.0
    target_horizon: float = 5.0
    min_bet_size: float = 5.0
    # Four LHS-sweep dims added in sprint_2 (T-C-002) ↓
    e_decision_tax: float = 1.0
    e_time_tax_per_tick: float = 0.5
    soft_cap_threshold: float = 2500.0
    desperate_threshold: float = 200.0

    # ------------------------------------------------------------------
    # JSON round-trip
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialize to a stable, sorted-key JSON string.

        The output is deterministic byte-for-byte across runs of the
        same Python build — the harness reproducibility gate hashes it
        directly. ``ensure_ascii=True`` so the artifact survives any
        downstream consumer locale.
        """
        return json.dumps(asdict(self), indent=2, sort_keys=True, ensure_ascii=True)

    @classmethod
    def from_json(cls, text: str) -> ParamSpace:
        """Parse a JSON string produced by :meth:`to_json`.

        Raises :class:`ValueError` if a required field is missing or has
        the wrong type. Unknown keys are rejected — additive schema
        changes must bump :data:`sim.__version__` and add a migration
        step, not be tolerated silently. This mirrors the
        ``additionalProperties: false`` posture taken by every JSON
        schema under :file:`.dev/policy/schemas/`.
        """
        raw: Any = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError(
                f"ParamSpace JSON must decode to an object, got {type(raw).__name__}"
            )
        known_keys = {f.name for f in fields(cls)}
        extra = set(raw.keys()) - known_keys
        if extra:
            raise ValueError(
                f"ParamSpace JSON contains unknown keys: {sorted(extra)} "
                f"(allowed: {sorted(known_keys)})"
            )
        missing = known_keys - set(raw.keys())
        if missing:
            raise ValueError(
                f"ParamSpace JSON missing required keys: {sorted(missing)}"
            )
        coerced: dict[str, float] = {}
        for f in fields(cls):
            v = raw[f.name]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(
                    f"ParamSpace.{f.name} must be a number, got {type(v).__name__}"
                )
            coerced[f.name] = float(v)
        return cls(**coerced)

    # ------------------------------------------------------------------
    # Filesystem convenience
    # ------------------------------------------------------------------

    def write_json(self, path: Path) -> Path:
        """Write the JSON form to ``path`` (creating parents). Returns
        the path written. Used by :mod:`sim.runner` and the sweeper to
        persist ``selected_params.json``."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def read_json(cls, path: Path) -> ParamSpace:
        """Mirror of :meth:`write_json` for symmetric I/O."""
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # LHS sampling convenience
    # ------------------------------------------------------------------

    def with_overrides(self, overrides: dict[str, float]) -> ParamSpace:
        """Return a copy of ``self`` with the named fields replaced.

        Used by :class:`sim.sampling.lhs.LHSSampler` to build per-sample
        :class:`ParamSpace` instances from the four LHS dimensions while
        keeping the remaining dataclass defaults intact. Unknown keys
        raise :class:`ValueError` — silent typos would otherwise
        materialise as same-as-default samples and quietly destroy the
        LHS coverage guarantee.
        """
        unknown = set(overrides.keys()) - {f.name for f in fields(ParamSpace)}
        if unknown:
            raise ValueError(
                f"with_overrides got unknown field(s): {sorted(unknown)}"
            )
        return replace(self, **overrides)


# ---------------------------------------------------------------------------
# LHS configuration — exported as module-level constants so the sweeper can
# import them without instantiating a ParamSpace. The four T-C-002 mandatory
# dims (per task brief acceptance criteria) MUST appear in LHS_DIMS; the
# calibration validator greps the sweep output for these names.
# ---------------------------------------------------------------------------

LHS_DIMS: Final[tuple[str, ...]] = (
    # ── T-C-002 mandatory four (PRD §14.1) ──
    "e_decision_tax",
    "e_time_tax_per_tick",
    "soft_cap_threshold",
    "desperate_threshold",
    # ── Two additional PRD §14.1 dims pulled into the sweep for richer
    # coverage. Both have well-understood ranges and meaningful effect on
    # mean lifetime per the calibration heuristic. ──
    "initial_breath",
    "passive_burn_rate",
)

LHS_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    # Bounds chosen from PRD §14.1 sweep ranges cited in the field
    # docstrings above. The LHS sampler scales unit-cube samples by these.
    "e_decision_tax": (0.5, 5.0),
    "e_time_tax_per_tick": (0.1, 2.0),
    "soft_cap_threshold": (1500.0, 4000.0),
    "desperate_threshold": (50.0, 400.0),
    "initial_breath": (800.0, 1500.0),
    "passive_burn_rate": (0.5, 3.0),
}

# Defensive: any LHS_DIM not in LHS_BOUNDS would crash the sampler with a
# misleading KeyError far from this module. Catch it at import time.
assert set(LHS_DIMS) == set(LHS_BOUNDS.keys()), (
    "LHS_DIMS and LHS_BOUNDS keys must match exactly — "
    f"diff: {set(LHS_DIMS) ^ set(LHS_BOUNDS.keys())}"
)
