"""Runtime state types shared across the agent core.

Defines the typed payloads that flow through the 9-step ``agent_loop`` in
:mod:`agent.core.agent`. Pydantic models live here so the loop, the
MemoryBank writer, and the engines can all agree on shape.

The constraints enforced by ``Weights.validate_normalised``
(α₁+α₂+α₃=1, β₁+β₂=1, w_r+w_s=1) come from PRD §4.1. The
:class:`Action` ``kind`` enum mirrors the on-chain action taxonomy used by
``EnergyController.consumeAction`` so the chain adapter has a 1-1 mapping.

T-B-003 adds :class:`AgentState` — the *in-memory* live snapshot that
the lifecycle scheduler updates between tick boundaries. ``Weights``
remains the *persisted* snapshot written to MemoryBank; ``AgentState``
holds the same Weights plus runtime breath / bankroll / phase /
desperate-mode flag so engines can read a single typed object instead
of N positional arguments. The full V2-boot memory_bank rehydration
lands in sprint_4 — :meth:`AgentState.save_json` /
:meth:`AgentState.load_json` are the persistence stubs.
"""

from __future__ import annotations

import json
import math
import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Phase(StrEnum):
    """Mirror of PRD §3 lifecycle phases. StrEnum so JSON serialisation
    matches the on-chain ``EnergyController.Phase`` uint8 by name, not by
    ordinal."""

    PHASE_1_INFANCY = "PHASE_1_INFANCY"
    PHASE_2_APPRENTICE = "PHASE_2_APPRENTICE"
    PHASE_3_MASTER = "PHASE_3_MASTER"
    PHASE_4_TERMINAL = "PHASE_4_TERMINAL"


class ActionKind(StrEnum):
    """Both BET and NO_BET consume BREATH per PRD §6 — NO_BET is NOT free."""

    BET = "BET"
    NO_BET = "NO_BET"


class Side(StrEnum):
    YES = "YES"
    NO = "NO"


class Vitals(BaseModel):
    """Bankroll / breath / phase-age snapshot at the close of a tick."""

    model_config = ConfigDict(extra="forbid")

    breath: Annotated[float, Field(ge=0.0)]
    bankroll_usd: float
    phase_age_days: Annotated[float, Field(ge=0.0)]


class Weights(BaseModel):
    """6-parameter fusion model snapshot per PRD §4.1.

    Validation enforces the three normalisation constraints. Producer
    (engines.weight_updater) MUST clip + renormalise before writing; this
    check is the second line of defence — a divergent tick MUST raise
    rather than be silently persisted, because Track C replay and Track D
    playback both rely on the invariants.
    """

    model_config = ConfigDict(extra="forbid")

    w_r: Annotated[float, Field(ge=0.0, le=1.0)]
    w_s: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[list[float], Field(min_length=3, max_length=3)]
    beta: Annotated[list[float], Field(min_length=2, max_length=2)]
    rho: Annotated[float, Field(ge=-1.0, le=1.0)]

    @model_validator(mode="after")
    def validate_normalised(self) -> Weights:
        if not math.isclose(self.w_r + self.w_s, 1.0, abs_tol=1e-6):
            raise ValueError("w_r + w_s must equal 1.0 per PRD §4.1")
        if not math.isclose(sum(self.alpha), 1.0, abs_tol=1e-6):
            raise ValueError("alpha components must sum to 1.0 per PRD §4.1")
        if not math.isclose(sum(self.beta), 1.0, abs_tol=1e-6):
            raise ValueError("beta components must sum to 1.0 per PRD §4.1")
        if any(a < 0 for a in self.alpha):
            raise ValueError("alpha components must be non-negative")
        if any(b < 0 for b in self.beta):
            raise ValueError("beta components must be non-negative")
        return self


class Action(BaseModel):
    """Decision produced by step 4 of the agent loop."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    market_id: str | None = None
    side: Side | None = None
    size_usd: Annotated[float, Field(ge=0.0)] | None = None
    edge_pct: float | None = None
    no_bet_reason: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Action:
        if self.kind == ActionKind.BET:
            if self.market_id is None or self.side is None or self.size_usd is None:
                raise ValueError("BET action requires market_id + side + size_usd")
            if self.size_usd <= 0.0:
                # The 4-constraint clamp in PRD §6.6 returns a positive
                # size whenever kind=BET — a zero-size BET would consume
                # BREATH for a no-op order, which is the failure mode
                # NO_BET exists to express explicitly.
                raise ValueError("BET action size_usd must be > 0")
            if self.no_bet_reason is not None:
                raise ValueError("BET action must NOT carry no_bet_reason")
        else:  # NO_BET
            if self.size_usd not in (None, 0.0):
                raise ValueError("NO_BET action must NOT carry positive size_usd")
        return self


class TickPayload(BaseModel):
    """The MemoryBank row written by step 9 of the agent loop.

    Mirrors ``.dev/contracts/memory_bank_schema.v1.0.0.json`` field-for-field
    so consumers (Track C replay, Track D playback, V2 boot) can validate
    against the JSON Schema without trusting the producer's Pydantic.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    tick: Annotated[int, Field(ge=0)]
    ts: str  # ISO-8601 UTC; kept as string so JSON round-trip is byte-stable
    agent_id: Annotated[str, Field(min_length=1)]
    phase: Phase
    vitals: Vitals
    weights: Weights
    action: Action
    narrative: Annotated[str, Field(min_length=1)]
    reflection_ref: str | None = None
    ancestor_memory_bank_cid: str | None = None
    lookahead_ok: bool | None = None


class AgentState(BaseModel):
    """In-memory live snapshot — owned by the lifecycle scheduler.

    Holds the cross-tick runtime state engines / decision / reflection
    layer all share-read between tick boundaries. The persisted
    counterpart is :class:`TickPayload` (written to MemoryBank as
    step 9 of the agent_loop); ``AgentState`` is the *runtime* view
    that gets mutated in place by the scheduler.

    Sprint_3 (T-B-003) ships the dataclass + JSON persistence stub.
    The full V2-boot memory_bank rehydration (PRD §13) lands in
    sprint_4 — :meth:`save_json` / :meth:`load_json` here are the
    minimum-viable persistence helpers so the lifecycle scheduler can
    snapshot state across an ordinary process restart.

    Fields
    ------

    ``tick``:
        Monotonic tick counter from genesis. Increments by 1 each loop.

    ``phase``:
        Current PRD §3 phase as read from on-chain PhaseManager.

    ``vitals``:
        Bankroll / breath / phase-age snapshot. Updated post-step-8
        (passive burn) every tick.

    ``weights``:
        Latest fusion weights. Updated post-step-7 (weight_updater)
        every tick.

    ``desperate``:
        True when ``breath < desperate_threshold`` (PRD §6.5). Latched
        on entry; the lifecycle layer clears it when breath recovers.
        Decision engine + weight updater both read this flag.

    ``agent_id``:
        V1 = ``"genesis_v1"``. V2-boot loader carries ancestor lineage
        in the form ``"genesis_v<N>"``.

    ``ancestor_memory_bank_cid``:
        IPFS CID of the ancestor agent's memory bank, present only on
        V2-boot agents per PRD §5.1. Absent on the first genesis run.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0.0"
    agent_id: Annotated[str, Field(min_length=1)] = "genesis_v1"
    tick: Annotated[int, Field(ge=0)] = 0
    phase: Phase = Phase.PHASE_1_INFANCY
    vitals: Vitals
    weights: Weights
    desperate: bool = False
    ancestor_memory_bank_cid: str | None = None

    def save_json(self, path: Path) -> Path:
        """Atomic temp+rename JSON snapshot.

        Used by the lifecycle scheduler on SIGTERM to flush in-memory
        state before exit. Mirrors :meth:`MemoryBank.write_tick`
        semantics — temp file + os.replace so a crash mid-write leaves
        the previous snapshot intact.

        The persistence here is a stub for sprint_3 — the full
        memory_bank-driven rehydration (PRD §13) lands in sprint_4. The
        on-disk shape is forward-compatible with the V2 boot path: a
        :meth:`load_json` call reads the same file back round-trip.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = self.model_dump(mode="json", exclude_none=True)
        data = json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)
        tmp = path.with_name(f".{path.name}.tmp")
        if tmp.exists():
            tmp.unlink()
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
        return path

    @classmethod
    def load_json(cls, path: Path) -> AgentState:
        """Round-trip counterpart of :meth:`save_json`.

        Raises :class:`FileNotFoundError` if the snapshot is missing
        (the lifecycle scheduler interprets that as a cold-start and
        boots from defaults).
        """
        raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)
