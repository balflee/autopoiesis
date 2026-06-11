"""L3 meta-optimizer proposal wire schema — T-B-025 scaffolding.

Spec anchors
------------

* PRD §4.6 (Per-tick narrative): "Reflection 是 L2; 上方还有 L3 — agent
  看自己一段时间表现, 提议策略修改 (L3, sprint 10 实施). L3 提议必须人审."
* TECHNICAL_PLAN §4.2 (权重学习): "L3 meta-optimizer 计划在 sprint 10. L3
  接口预留: StrategyAdvisor Protocol + StrategyProposal Pydantic."
* TECHNICAL_PLAN §4.4 (Reflection Engine): "Reflection 输出消耗者:
  dashboard (人看), L3 meta-optimizer (机器看 — 从 reflections.jsonl +
  decisions.jsonl 拼出 PerformanceWindow 输入)."

This module is the **L3 wire schema half**: a thin Pydantic shape carried
across the L2→L3 boundary and persisted to ``state/sandbox/proposals.jsonl``
as one append-only line per proposal. The Protocol that produces these
records lives in :mod:`agent.engines.strategy_advisor`.

The schema is JSON-mirrored in
``.dev/contracts/strategy_proposal_schema.v0.1.0.json`` (producer=B,
consumer=D). The dashboard (T-D-010 follow-up) tails the JSONL stream
and renders one card per pending proposal; the operator approves /
rejects via the dashboard, which writes to a separate flow Track D owns.

Schema design notes
-------------------

* ``proposal_id`` — UUID4 hex. Stable identifier the dashboard uses as a
  React-list key + the audit trail's foreign-key column.
* ``ts`` — ISO-8601 UTC trigger timestamp. Pydantic serialises
  :class:`datetime` to ISO-8601 by default; the JSONL line on disk
  carries the same wire shape the dashboard parses.
* ``kind`` — three locked categories per the T-B-025 brief:

  - ``weight_delta`` — the advisor suggests bumping one of the 6 fusion
    weights (``proposed_change`` carries ``{key: str, delta: float}``).
  - ``new_signal_idea`` — the advisor proposes adding a new engine
    (``proposed_change`` carries a free-form description).
  - ``prompt_tweak`` — the advisor proposes a tweak to the L1 sentiment
    / L2 reflection prompt (``proposed_change`` carries the patch).

  Open-ended :class:`dict` here keeps the schema stable across sprint_10
  when the real LLM-backed advisor lands; only the four wire keys
  (``proposal_id``, ``ts``, ``kind``, ``rationale``) need to remain
  stable for the dashboard renderer.

* ``rationale`` — human-readable explanation. ``min_length=1`` because
  an empty rationale is operator-hostile: the dashboard's approval
  workflow requires the operator to read WHY before approving WHAT.

* ``proposed_change`` — opaque-but-typed payload. ``dict[str, Any]`` is
  intentionally loose for sprint_9 scaffolding so sprint_10 can swap in
  a richer advisor without a schema bump. The dashboard renders this
  as a JSON-tree component.

* ``expected_impact`` — optional one-line projection of the expected
  effect (e.g. "+3% Sharpe over next 100 ticks"). Optional because the
  Phase-1 NoOp advisor never populates it; sprint_10 production wiring
  fills this in via a separate LLM call.

* ``confidence_pct`` — integer 0-100. ``int`` (not float) because the
  dashboard displays this as a percentage badge and float precision
  isn't useful at the operator decision level.

* ``requires_human_approval`` — locked to ``True`` in production per PRD
  §4.6 ("L3 提议必须人审"). The field is on the schema (not a constant)
  because the sandbox-replay tooling needs to construct historical
  records that already shipped with ``False`` post-approval, and the
  schema mirrors the truth on disk; the *producer* (advisor) is the
  layer that enforces the "always True at proposal time" invariant.

* ``model_config = ConfigDict(extra='ignore')`` — locked by the T-B-025
  brief. Unlike :mod:`agent.data.sandbox_state` which uses
  ``extra='forbid'`` for runtime JSONL streams, the L3 proposal channel
  is forward-compatible: sprint_10's richer advisor may ship extra
  diagnostic fields that older :class:`StrategyProposal` validators
  silently drop rather than raise on. ``ignore`` is the right posture
  for a producer→consumer schema that crosses a sprint boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

#: T-B-030 — locked status vocabulary for the on-disk ``proposals.jsonl``
#: stream. Every freshly-emitted proposal lands as ``"pending"``; the
#: operator-approval flow (Track D follow-up) appends a NEW line with the
#: same ``proposal_id`` carrying ``"approved"`` / ``"rejected"`` (mirrors
#: the open-bets latest-status-wins fold pattern). The runtime is the
#: source of truth at fold time — see
#: :func:`agent.runtime.sandbox_phase2_loop._fold_pending_proposals_from_jsonl`.
PROPOSAL_STATUS_PENDING: Final[Literal["pending"]] = "pending"
PROPOSAL_STATUS_APPROVED: Final[Literal["approved"]] = "approved"
PROPOSAL_STATUS_REJECTED: Final[Literal["rejected"]] = "rejected"


class StrategyProposal(BaseModel):
    """One L3 meta-optimizer proposal — appended to ``state/sandbox/proposals.jsonl``.

    Produced by any :class:`agent.engines.strategy_advisor.StrategyAdvisor`
    implementation (Phase-1 :class:`NoOpStrategyAdvisor` never produces
    one; sprint_10 LLM-backed advisor produces 0..N per trigger). Read
    by the dashboard's pending-proposals panel (T-D-010 follow-up).

    Fields locked by the T-B-025 brief — bump the version on any
    breaking change. The JSON Schema mirror lives at
    ``.dev/contracts/strategy_proposal_schema.v0.2.0.json`` (T-B-030
    sprint_10 added the optional ``status`` field; v0.1.0 superseded).
    """

    model_config = ConfigDict(extra="ignore")

    proposal_id: str = Field(
        ...,
        description=(
            "UUID4 hex string — stable identifier the dashboard uses as a "
            "React-list key and the audit trail's foreign-key column."
        ),
    )
    ts: datetime = Field(
        ...,
        description=(
            "ISO-8601 UTC trigger timestamp. Pydantic serialises "
            "datetime to ISO-8601 by default; the JSONL line on disk "
            "carries the same wire shape the dashboard parses."
        ),
    )
    kind: Literal["weight_delta", "new_signal_idea", "prompt_tweak"] = Field(
        ...,
        description=(
            "Proposal category. ``weight_delta`` bumps one of the 6 fusion "
            "weights; ``new_signal_idea`` proposes a new engine; "
            "``prompt_tweak`` patches the L1/L2 LLM prompt."
        ),
    )
    rationale: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable explanation. min_length=1 because an empty "
            "rationale is operator-hostile: the approval workflow "
            "requires the operator to read WHY before approving WHAT."
        ),
    )
    proposed_change: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Opaque-but-typed payload. Loose ``dict[str, Any]`` for "
            "sprint_9 scaffolding so sprint_10 can swap in a richer "
            "advisor without a schema bump."
        ),
    )
    expected_impact: str | None = Field(
        default=None,
        description=(
            "Optional one-line projection of the expected effect "
            "(e.g. '+3% Sharpe over next 100 ticks'). Optional because "
            "the Phase-1 NoOp advisor never populates it."
        ),
    )
    confidence_pct: int = Field(
        ...,
        ge=0,
        le=100,
        description=(
            "Integer 0-100. ``int`` (not float) because the dashboard "
            "displays this as a percentage badge and float precision "
            "isn't useful at the operator decision level."
        ),
    )
    requires_human_approval: bool = Field(
        ...,
        description=(
            "Locked to True in production per PRD §4.6 ('L3 提议必须人审'). "
            "The field is on the schema (not a constant) because "
            "sandbox-replay tooling needs to construct historical "
            "records that already shipped with False post-approval."
        ),
    )
    status: Literal["pending", "approved", "rejected"] = Field(
        default="pending",
        description=(
            "Locked status vocabulary, T-B-030 sprint_10. Every freshly "
            "emitted proposal lands as ``'pending'``; the operator-approval "
            "workflow (Track D follow-up) appends a NEW line with the "
            "same ``proposal_id`` carrying ``'approved'`` / ``'rejected'`` "
            "— mirrors the open-bets latest-status-wins fold pattern. "
            "Default ``'pending'`` so sprint_9 callers that omit the field "
            "behave correctly (the on-disk row carries ``'pending'`` "
            "explicitly because Pydantic serialises field defaults)."
        ),
    )


__all__ = [
    "PROPOSAL_STATUS_APPROVED",
    "PROPOSAL_STATUS_PENDING",
    "PROPOSAL_STATUS_REJECTED",
    "StrategyProposal",
]
