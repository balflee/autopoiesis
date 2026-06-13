"""Gemini prompt templates + structured-output schema for the L3 advisor.

Spec anchors
------------

* PRD §4.6: "Reflection 是 L2; 上方还有 L3 — agent 看自己一段时间表现,
  提议策略修改 (L3, sprint 10 实施). L3 提议必须人审."
* TECHNICAL_PLAN §4.4 (Reflection Engine, slow loop): "L3 advisor 跨
  100 ticks 读 PerformanceWindow -> 提议 weight_delta / new_signal_idea /
  prompt_tweak."

This module owns three things, all module-level and pure:

1. :data:`SYSTEM_PROMPT` — locked role / output contract the LLM sees
   before any per-call user message. Lists the 3 allowed proposal
   ``kind`` categories, the per-proposal field requirements, the cap
   of :data:`MAX_PROPOSALS_PER_CALL` proposals, and the rendering
   conventions for ``proposed_change``.

2. :func:`render_user_prompt` — renders the user-turn body for one
   :class:`PerformanceWindow`. Pure: no globals, no clock, no
   network. The advisor injects this output into
   :meth:`_LLMClient.structured_call`.

3. :data:`RESPONSE_SCHEMA` — the Gemini structured-JSON schema (plain
   :class:`dict`) the SDK passes via ``response_json_schema``.
   The shape is a wrapper object ``{ "proposals": [...] }`` rather
   than a bare list because Gemini structured-output mode prefers a
   top-level object — the same shape :class:`agent.engines.sentiment_llm._LLMResponse`
   ships with. The wrapper also gives the advisor a clean affordance
   for the ``MAX_PROPOSALS_PER_CALL`` cap (an ``items`` constraint on
   the inner list); even if the model overshoots, the impl tail-
   trims defensively.

Rendering conventions
---------------------

* ``recent_pnl`` is rendered as a comma-separated list with each value
  in dollars rounded to 2 dp. Keeps the prompt token count small for
  the worst case (20 floats ≈ 120 chars).
* ``weight_trajectory`` is rendered as the FIRST + LAST weights only
  (with an ellipsis between them) — the full 100-tick trajectory would
  blow the prompt size out, and the advisor doesn't need every step:
  it needs to see WHERE the agent started and WHERE it's ended up.
* ``recent_reflections`` is rendered verbatim, one bullet per
  reflection. Each is already capped at the
  :data:`agent.engines.reflection._MAX_REFLECTION_CHARS` budget by the
  reflection engine, so we don't re-truncate here.
* All weights are rendered with the canonical 6-key naming
  (``w_r``, ``alpha_0``, ``alpha_1``, ``alpha_2``, ``beta_0``,
  ``rho``) so the advisor's ``weight_delta`` proposals can carry
  the same key in ``proposed_change`` and the dashboard renders
  consistently.

Look-ahead bias documentation
-----------------------------

The prompt renderer reads ONLY :class:`PerformanceWindow` fields plus
its own ``asof`` argument; no clock read, no JSONL tail, no future
tick data. The look-ahead auditor scans
``agent/engines/features*`` / ``agent/training/**``; this module is
excluded by directory shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from agent.core.state import Weights
from agent.engines._performance_window import PerformanceWindow
from agent.engines.reflection import REFLECTION_WEIGHT_KEYS

# --------------------------------------------------------------------------- #
# Constants — locked by T-B-029 brief.
# --------------------------------------------------------------------------- #

#: Maximum proposals per single :meth:`StrategyAdvisorImpl.review_window`
#: call. Cap is enforced both via the structured-output schema
#: ``maxItems`` AND a defensive tail-trim in the impl (Gemini does NOT
#: always honour schema constraints to the letter).
MAX_PROPOSALS_PER_CALL: Final[int] = 3

#: The 3 locked proposal categories per PRD §4.6.
PROPOSAL_KINDS: Final[tuple[str, ...]] = (
    "weight_delta",
    "new_signal_idea",
    "prompt_tweak",
)

#: Per-weight projector — pulls the canonical scalar out of a
#: :class:`Weights` model for each :data:`REFLECTION_WEIGHT_KEYS` entry.
#: Keyed projection avoids the if/elif cascade and pairs structurally
#: with the canonical key tuple (a future key addition in reflection.py
#: surfaces as a mypy / KeyError here rather than silently dropping).
_WEIGHT_PROJECTORS: Final[dict[str, Callable[[Weights], float]]] = {
    "w_r": lambda w: float(w.w_r),
    "alpha_0": lambda w: float(w.alpha[0]),
    "alpha_1": lambda w: float(w.alpha[1]),
    "alpha_2": lambda w: float(w.alpha[2]),
    "beta_0": lambda w: float(w.beta[0]),
    "rho": lambda w: float(w.rho),
}


# --------------------------------------------------------------------------- #
# System prompt — locked role + output contract.
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT: Final[str] = """\
You are the L3 meta-optimizer for an autonomous Polymarket betting agent. \
You read the agent's own recent performance (P&L, weight trajectory, and \
self-reflection narratives) and propose STRUCTURAL changes that a human \
operator will review before applying.

Your job is NOT to predict markets. Your job is to spot patterns in the \
agent's own learning trajectory and propose at most 3 high-leverage \
adjustments per review window.

You may propose changes in EXACTLY these 3 categories ("kind" field):

  * "weight_delta"      — bump one of the 6 fusion weights by a small \
delta. The proposed_change dict MUST be {"key": <weight_name>, "delta": <float>} \
where <weight_name> is one of: w_r, alpha_0, alpha_1, alpha_2, beta_0, rho.
  * "new_signal_idea"   — propose adding a brand-new engine / signal source. \
The proposed_change dict carries a free-form description (e.g. \
{"description": "...", "data_source": "..."}).
  * "prompt_tweak"      — propose a specific edit to the L1 sentiment or \
L2 reflection prompt. The proposed_change dict carries the patch \
(e.g. {"target": "L1_sentiment", "before": "...", "after": "..."}).

Per-proposal output contract:

  * rationale         — non-empty human-readable WHY (1-3 sentences).
  * proposed_change   — dict with kind-specific keys per above.
  * expected_impact   — 1-2 sentence projection (e.g. "+3% Sharpe over \
next 100 ticks"). Required, non-empty.
  * confidence_pct    — integer 0-100 (your confidence in the proposal).
  * kind              — one of the 3 categories above.

HARD RULES:

  1. Return AT MOST 3 proposals. Fewer is fine. If the agent looks \
healthy and you have no high-conviction suggestions, return an empty \
"proposals" list.
  2. Every weight_delta MUST keep the proposed change small (|delta| < 0.1) — \
the operator applies these incrementally.
  3. Do NOT propose changes that would violate normalisation (w_r+w_s=1, \
alpha_0+alpha_1+alpha_2=1, beta_0+beta_1=1) — the runtime renormalises but \
your proposal should respect the invariant where reasonable.
  4. Every proposal MUST quote a specific observation from the \
PerformanceWindow you were given. No generic advice.
"""


# --------------------------------------------------------------------------- #
# Response schema — Gemini structured-output mode contract.
# --------------------------------------------------------------------------- #

#: Strict JSON Schema for Gemini's ``response_json_schema`` mode.
#: Top-level object with a single ``proposals`` list constrained to
#: :data:`MAX_PROPOSALS_PER_CALL` items maximum. The inner item shape
#: mirrors the L3 advisor's per-proposal contract (kind + rationale +
#: proposed_change + expected_impact + confidence_pct). The advisor's
#: impl layer wraps each item into a full
#: :class:`agent.engines._strategy_proposal_schema.StrategyProposal`
#: by injecting ``proposal_id`` (UUID4) + ``ts`` (now) +
#: ``requires_human_approval`` (True locked per PRD §4.6).
RESPONSE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "maxItems": MAX_PROPOSALS_PER_CALL,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": list(PROPOSAL_KINDS),
                    },
                    "rationale": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "proposed_change": {
                        "type": "object",
                    },
                    "expected_impact": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "confidence_pct": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": [
                    "kind",
                    "rationale",
                    "proposed_change",
                    "expected_impact",
                    "confidence_pct",
                ],
            },
        }
    },
    "required": ["proposals"],
}


# --------------------------------------------------------------------------- #
# Strict "weight_delta only" variant — survival-simulation opt-in (T-D-018).
#
# The prod SYSTEM_PROMPT / RESPONSE_SCHEMA above stay byte-unchanged for the
# human-review path (all 3 kinds, loose proposed_change). The survival season's
# auto-approve loop needs the LLM to ALWAYS emit a structured, applicable
# {"key","delta"} so a real Gemini/MiniMax call genuinely moves weights — a
# loose proposed_change={} silently fail-softs and the run is not AI-driven.
#
# IMPORTANT: the provider schema is only a HINT (esp. MiniMax, which injects the
# schema as prompt text and does not honour response_format). The schema + prompt
# below only raise the ODDS the model complies; the binding guarantee is the
# LOCAL parse enforcement in StrategyAdvisorImpl._build_proposal (strict mode) +
# the run-level hard invariant in survival_season.
# --------------------------------------------------------------------------- #

#: Canonical 6 fusion-weight keys a ``weight_delta`` proposal may target.
#: Derived from :data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS` so the
#: L2 reflection set, this strict schema, and the runtime apply surface
#: (:data:`agent.runtime.sandbox_phase2_loop._WEIGHT_DELTA_KEYS`) share ONE
#: vocabulary — a future key addition widens all three together.
WEIGHT_DELTA_KEYS: Final[tuple[str, ...]] = tuple(REFLECTION_WEIGHT_KEYS)

#: Inclusive per-proposal magnitude bound for a strict-mode ``delta``. This is
#: an ADDITIONAL strict-advisor constraint (the runtime apply layer does NOT
#: bound magnitude — it only checks key/type/finite); kept inclusive so the
#: schema (``minimum``/``maximum``), the prompt wording (``|delta| <= 0.1``),
#: and the parser enforcement all agree.
WEIGHT_DELTA_MAX_ABS: Final[float] = 0.1

#: Strict JSON Schema: same wrapper as :data:`RESPONSE_SCHEMA` but every item is
#: locked to ``kind="weight_delta"`` and ``proposed_change`` is a STRICT object
#: requiring ``{"key": <one of the 6>, "delta": <number in [-0.1, 0.1]>}``.
WEIGHT_DELTA_RESPONSE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "maxItems": MAX_PROPOSALS_PER_CALL,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["weight_delta"],
                    },
                    "rationale": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "proposed_change": {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                                "enum": list(WEIGHT_DELTA_KEYS),
                            },
                            "delta": {
                                "type": "number",
                                "minimum": -WEIGHT_DELTA_MAX_ABS,
                                "maximum": WEIGHT_DELTA_MAX_ABS,
                            },
                        },
                        "required": ["key", "delta"],
                    },
                    "expected_impact": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "confidence_pct": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": [
                    "kind",
                    "rationale",
                    "proposed_change",
                    "expected_impact",
                    "confidence_pct",
                ],
            },
        }
    },
    "required": ["proposals"],
}

#: Strict system prompt: ONLY ``weight_delta``, ALWAYS a filled
#: ``proposed_change``, and a bias toward >=1 concrete proposal when the agent
#: is losing / its weights are stale (an empty list is the fail-soft outcome we
#: are trying to avoid in the auto-approve sim).
WEIGHT_DELTA_SYSTEM_PROMPT: Final[str] = """\
You are the L3 meta-optimizer for an autonomous Polymarket betting agent in a \
SELF-EVOLUTION simulation where your proposals are applied AUTOMATICALLY (no \
human in the loop). You read the agent's own recent performance (P&L, weight \
trajectory, and self-reflection narratives) and propose small, concrete \
adjustments to the agent's 6 fusion weights.

You may propose changes in EXACTLY ONE category ("kind" field):

  * "weight_delta" — bump one of the 6 fusion weights by a small delta. The \
proposed_change dict MUST ALWAYS be {"key": <weight_name>, "delta": <float>} \
where <weight_name> is one of: w_r, alpha_0, alpha_1, alpha_2, beta_0, rho, and \
<float> satisfies |delta| <= 0.1. NEVER return an empty proposed_change, and \
NEVER propose any other kind.

Per-proposal output contract:

  * kind              — MUST be "weight_delta".
  * rationale         — non-empty human-readable WHY (1-3 sentences).
  * proposed_change   — {"key": <one of the 6>, "delta": <float, |delta| <= 0.1>}. REQUIRED, never empty.
  * expected_impact   — 1-2 sentence projection. Required, non-empty.
  * confidence_pct    — integer 0-100 (your confidence in the proposal).

HARD RULES:

  1. Return AT MOST 3 proposals. When the agent is LOSING money or its weights \
look STALE (current ≈ baseline despite poor P&L), you SHOULD return at least 1 \
concrete weight_delta rather than an empty list — a small nudge is better than \
no learning. Only return an empty "proposals" list if the agent is clearly \
healthy and well-tuned.
  2. Every weight_delta MUST keep the change small (|delta| <= 0.1) — the \
runtime renormalises and applies incrementally.
  3. Do NOT propose changes that would violate normalisation (w_r+w_s=1, \
alpha_0+alpha_1+alpha_2=1, beta_0+beta_1=1) where reasonable — the runtime \
renormalises but respect the invariant.
  4. Every proposal MUST quote a specific observation from the \
PerformanceWindow you were given. No generic advice.
"""


# --------------------------------------------------------------------------- #
# A9 genome vocabulary (plan 2026-06-13) — BOUNDARY-ONLY extension.
#
# The rebirth-boundary advisor may receive an EXTENDED key vocabulary
# (fusion keys + StrategyConfig genome knobs). This NEVER widens
# REFLECTION_WEIGHT_KEYS / WEIGHT_DELTA_KEYS themselves — those feed the
# LIVE drain (sandbox_phase2_loop._WEIGHT_DELTA_KEYS raises on unknown
# keys), so the extension exists only as a render-time parameter.
# Descriptions are NEUTRAL and sign-symmetric: the falsification leg
# (shuffled-control season), not wording, is the prior defense.
# --------------------------------------------------------------------------- #

#: Neutral, sign-symmetric descriptions for the genome knobs the rebirth
#: boundary may expose. Keys absent here render without a description.
GENOME_KEY_DESCRIPTIONS: Final[dict[str, str]] = {
    "min_edge": (
        "the minimum |p_model - price| edge required to bet; higher bets "
        "less often, lower bets more often"
    ),
    "max_breath_risk_pct": (
        "the fraction of breath risked per bet; higher sizes larger, "
        "lower sizes smaller"
    ),
    "min_confidence": (
        "the fused-confidence floor below which the agent abstains"
    ),
    "kappa": (
        "the market-prior tilt scale (p_model = price + kappa*fused); "
        "higher trusts the signals more, lower trusts the market more"
    ),
    "gate_storm_sensitivity": (
        "how strongly the edge gate responds to the storm signal; "
        "positive tightens in storms, negative loosens; 0 ignores it"
    ),
    "risk_storm_sensitivity": (
        "how strongly bet sizing responds to the storm signal; positive "
        "shrinks stakes in storms, negative grows them; 0 ignores it"
    ),
}


def render_weight_delta_schema(keys: tuple[str, ...]) -> dict[str, Any]:
    """A strict weight-delta response schema over an explicit key enum.

    ``render_weight_delta_schema(WEIGHT_DELTA_KEYS)`` is STRUCTURALLY
    equal to :data:`WEIGHT_DELTA_RESPONSE_SCHEMA` but is a NEW object —
    callers that need the exact module constant (identity-compared in
    tests) must keep passing the constant itself.
    """
    if not keys:
        raise ValueError("render_weight_delta_schema requires >= 1 key")
    return {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "maxItems": MAX_PROPOSALS_PER_CALL,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["weight_delta"],
                        },
                        "rationale": {
                            "type": "string",
                            "minLength": 1,
                        },
                        "proposed_change": {
                            "type": "object",
                            "properties": {
                                "key": {
                                    "type": "string",
                                    "enum": list(keys),
                                },
                                "delta": {
                                    "type": "number",
                                    "minimum": -WEIGHT_DELTA_MAX_ABS,
                                    "maximum": WEIGHT_DELTA_MAX_ABS,
                                },
                            },
                            "required": ["key", "delta"],
                        },
                        "expected_impact": {
                            "type": "string",
                            "minLength": 1,
                        },
                        "confidence_pct": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                        },
                    },
                    "required": [
                        "kind",
                        "rationale",
                        "proposed_change",
                        "expected_impact",
                        "confidence_pct",
                    ],
                },
            }
        },
        "required": ["proposals"],
    }


def render_weight_delta_system_prompt(keys: tuple[str, ...]) -> str:
    """The strict system prompt over an explicit key vocabulary.

    ``render_weight_delta_system_prompt(WEIGHT_DELTA_KEYS)`` returns the
    module constant :data:`WEIGHT_DELTA_SYSTEM_PROMPT` verbatim (the
    byte-identity contract for default-vocabulary callers); any other
    key set renders the extended wording with the neutral genome
    descriptions appended.
    """
    if not keys:
        raise ValueError("render_weight_delta_system_prompt requires >= 1 key")
    if tuple(keys) == WEIGHT_DELTA_KEYS:
        return WEIGHT_DELTA_SYSTEM_PROMPT
    described = [
        f"  * {k} — {GENOME_KEY_DESCRIPTIONS[k]}"
        for k in keys
        if k in GENOME_KEY_DESCRIPTIONS
    ]
    key_list = ", ".join(keys)
    description_block = (
        "\n\nParameter meanings (knobs beyond the 6 fusion weights):\n\n"
        + "\n".join(described)
        + "\n"
        if described
        else "\n"
    )
    return f"""\
You are the L3 meta-optimizer for an autonomous Polymarket betting agent in a \
SELF-EVOLUTION simulation where your proposals are applied AUTOMATICALLY (no \
human in the loop). You read the agent's own recent performance (P&L, weight \
trajectory, and self-reflection narratives) and propose small, concrete \
adjustments to the agent's strategy parameters.

You may propose changes in EXACTLY ONE category ("kind" field):

  * "weight_delta" — bump ONE parameter by a small delta. The \
proposed_change dict MUST ALWAYS be {{"key": <parameter_name>, "delta": <float>}} \
where <parameter_name> is one of: {key_list}, and \
<float> satisfies |delta| <= 0.1. NEVER return an empty proposed_change, and \
NEVER propose any other kind.{description_block}
Per-proposal output contract:

  * kind              — MUST be "weight_delta".
  * rationale         — non-empty human-readable WHY (1-3 sentences).
  * proposed_change   — {{"key": <one of the allowed>, "delta": <float, |delta| <= 0.1>}}. REQUIRED, never empty.
  * expected_impact   — 1-2 sentence projection. Required, non-empty.
  * confidence_pct    — integer 0-100 (your confidence in the proposal).

HARD RULES:

  1. Return AT MOST 3 proposals. When the agent is LOSING money or its \
parameters look STALE (current ≈ baseline despite poor P&L), you SHOULD return \
at least 1 concrete weight_delta rather than an empty list — a small nudge is \
better than no learning. Only return an empty "proposals" list if the agent is \
clearly healthy and well-tuned.
  2. Every weight_delta MUST keep the change small (|delta| <= 0.1) — the \
runtime clamps and applies incrementally.
  3. Do NOT propose changes that would violate normalisation (w_r+w_s=1, \
alpha_0+alpha_1+alpha_2=1, beta_0+beta_1=1) where reasonable — the runtime \
renormalises but respect the invariant.
  4. Every proposal MUST quote a specific observation from the \
PerformanceWindow you were given. No generic advice.
"""


# --------------------------------------------------------------------------- #
# User-turn prompt renderer — pure function.
# --------------------------------------------------------------------------- #


def render_user_prompt(window: PerformanceWindow) -> str:
    """Render the user-turn prompt body for a :class:`PerformanceWindow`.

    Pure: same input -> same output, no globals, no clock, no JSONL read.
    The advisor's :meth:`StrategyAdvisorImpl.review_window` builds the
    window first (using the folder helpers), then calls this once, then
    hands the resulting string to :meth:`_LLMClient.structured_call`.
    """
    parts: list[str] = []
    parts.append(
        f"L3 review trigger: {window.trigger} at tick "
        f"{window.tick_count_or_tick} (asof {window.ts.isoformat()})."
    )
    parts.append(f"Agent id: {window.agent_id}; phase: {window.phase.value}.")
    parts.append("")
    parts.append("=== Current vs baseline weights ===")
    parts.append(f"  current:  {_render_weights(window.current_weights)}")
    parts.append(f"  baseline: {_render_weights(window.baseline_weights)}")
    parts.append("")
    parts.append("=== Recent PnL (last settled bets, $USD) ===")
    if window.recent_pnl:
        rendered = ", ".join(f"{v:+.2f}" for v in window.recent_pnl)
        net = sum(window.recent_pnl)
        parts.append(f"  values: [{rendered}]  (net ${net:+.2f})")
    else:
        parts.append("  (no settled bets in window)")
    parts.append(
        f"  loop-side recent_pnl_window_usd: ${window.recent_pnl_window_usd:+.2f}"
    )
    parts.append("")
    parts.append("=== Weight trajectory (first -> last) ===")
    if window.weight_trajectory:
        first = window.weight_trajectory[0]
        last = window.weight_trajectory[-1]
        parts.append(f"  first:  {_render_weights(first)}")
        if len(window.weight_trajectory) > 1:
            parts.append(f"  ... ({len(window.weight_trajectory)} ticks)")
        parts.append(f"  last:   {_render_weights(last)}")
    else:
        parts.append("  (no trajectory recorded yet)")
    parts.append("")
    parts.append("=== Recent reflections (oldest -> newest) ===")
    if window.recent_reflections:
        for idx, narrative in enumerate(window.recent_reflections):
            parts.append(f"  [{idx}] {narrative}")
    else:
        parts.append("  (no reflections in window)")
    parts.append("")
    parts.append(
        "Based on the above, return at most "
        f"{MAX_PROPOSALS_PER_CALL} STRUCTURAL proposals in the locked "
        "JSON schema. Fewer is fine; an empty list is valid."
    )
    return "\n".join(parts)


def _render_weights(weights: Weights) -> str:
    """Compact one-line render — keys in canonical order, fixed 4 dp.

    Iterates :data:`REFLECTION_WEIGHT_KEYS` so the prompt's key set is
    locked-step with the reflection engine's snapshot dict. A future
    key addition in reflection.py raises :class:`KeyError` here at
    import time rather than silently dropping from the prompt.
    """
    return ", ".join(
        f"{key}={_WEIGHT_PROJECTORS[key](weights):.4f}"
        for key in REFLECTION_WEIGHT_KEYS
    )


__all__ = [
    "GENOME_KEY_DESCRIPTIONS",
    "MAX_PROPOSALS_PER_CALL",
    "PROPOSAL_KINDS",
    "RESPONSE_SCHEMA",
    "SYSTEM_PROMPT",
    "WEIGHT_DELTA_KEYS",
    "WEIGHT_DELTA_MAX_ABS",
    "WEIGHT_DELTA_RESPONSE_SCHEMA",
    "WEIGHT_DELTA_SYSTEM_PROMPT",
    "render_user_prompt",
    "render_weight_delta_schema",
    "render_weight_delta_system_prompt",
]
