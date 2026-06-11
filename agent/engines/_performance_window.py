"""L3 advisor input bundle + pure folder helpers (T-B-029 sprint_10).

Spec anchors
------------

* PRD §4.6 (Per-tick narrative): "Agent 维护每一 tick 的 narrative
  trail (内心独白 + 决策理由) 作为 reflection 输入. L3 advisor 在慢回路
  里读这个 trail (连同数值历史) 来提议结构性变更."
* TECHNICAL_PLAN §4.4 (Reflection Engine): "L1 reflection 是 per-tick:
  每一次 decision 后写一条短 reflection. L3 advisor 是 slow loop: 跨
  100 ticks 读 PerformanceWindow (含 recent_reflections 最后 5 条) +
  数值历史 → 提议 weight_delta / new_signal_idea / prompt_tweak."

Module role
-----------

This module owns the :class:`PerformanceWindow` dataclass — the input
the loop hands to :meth:`StrategyAdvisor.review_window`. Sprint_9
T-B-025 introduced the type inline in :mod:`agent.engines.strategy_advisor`;
sprint_10 T-B-029 enriches it with the slow-loop history fields the
real LLM-backed advisor needs to render its prompt:

* ``recent_pnl`` — last 20 settled-bet realised PnL values (USD).
* ``weight_trajectory`` — last 100 ticks of fusion weights (so the
  advisor can spot drift trajectories rather than just current/baseline).
* ``recent_reflections`` — last 5 L2 reflection narratives (so the
  advisor sees the agent's own recent self-talk).
* ``tick_count`` — total tick count at trigger time. Distinct from the
  sprint_9 ``tick`` field which carries the SAME number but under the
  older name (kept for back-compat with the sprint_9 NoOp scaffold tests).

All NEW fields default to empty / zero so existing call sites
(:class:`SandboxPhase2Loop._run_strategy_advice`, the sprint_9 scaffold
tests, replay tooling) keep building :class:`PerformanceWindow` without
breakage. The L3 advisor implementation reads the new fields when
present and falls back gracefully when they're absent (e.g. when the
loop hasn't been upgraded to populate them yet).

Folder helpers
--------------

Pure, side-effect-free functions that fold the on-disk JSONL streams
(``state/sandbox/settled_bets.jsonl``, ``state/sandbox/reflections.jsonl``,
``state/sandbox/decisions.jsonl``) into the python primitives the
advisor consumes:

* :func:`fold_pnl_from_settled` — extract the last N ``pnl_usd`` floats.
* :func:`fold_recent_reflections_from_jsonl` — extract the last N
  ``narrative`` strings.
* :func:`fold_weight_trajectory_from_jsonl` — extract the last N
  ``weight_snapshot`` dicts as :class:`Weights` objects.

These are PURE: they read a :class:`Path` and return a list. They
NEVER write, NEVER mutate global state, NEVER make a network call.
The advisor wires them in :meth:`StrategyAdvisorImpl._build_window` to
turn the loop's scalar :class:`PerformanceWindow` into a richer
prompt context without forcing the loop to know the JSONL layout.

Look-ahead bias documentation
-----------------------------

The folder helpers tail-read JSONL files; the data they return is
strictly historical (lines already written by prior ticks). There is
NO cross-tick look-ahead — the file pointer never seeks beyond the
last appended line, and the dataclass carries no future-tick data.
The look-ahead auditor (``.dev/harness/tools/lookahead_auditor.py``)
scans ``agent/engines/features*`` / ``agent/training/**``; this module
is excluded by directory shape.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

from agent.core.state import Phase, Weights
from agent.data.sandbox_state import iter_jsonl

# --------------------------------------------------------------------------- #
# Window sizes — locked by T-B-029 brief acceptance criteria.
# --------------------------------------------------------------------------- #

#: Tail-size for the PnL fold (last N settled bets). Matches the T-B-029
#: brief's "last 20 settled bets".
RECENT_PNL_TAIL: Final[int] = 20

#: Tail-size for the weight trajectory (last N ticks). Matches the brief's
#: "last 100 ticks".
WEIGHT_TRAJECTORY_TAIL: Final[int] = 100

#: Tail-size for the recent reflections (last N narratives). Matches the
#: brief's "last 5".
RECENT_REFLECTIONS_TAIL: Final[int] = 5


# --------------------------------------------------------------------------- #
# Default placeholders — let dataclass declarations keep working with
# the sprint_9 PerformanceWindow construction shape (no new required args).
# --------------------------------------------------------------------------- #


def _default_weights() -> Weights:
    """Cold-start neutral weights used when the loop didn't supply one.

    Mirrors the Phase 2 default from
    :mod:`agent.runtime.phase2_launch` numerically without importing
    that module (avoid a circular dep at the engine layer). The
    advisor only cares about the SHAPE of the weights for the diff
    prompt; absolute values are loop-side state.
    """
    return Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
        beta=[1.0, 0.0],
        rho=0.05,
    )


def _default_ts() -> datetime:
    """Epoch sentinel — clearly NOT a real trigger ts.

    The sprint_9 scaffold required a non-default ``ts`` because the
    field was required. The sprint_10 enrichment keeps the field
    required IF caller doesn't pass it the new way; the default is
    only ever reached through the dataclass's ``default_factory`` — in
    practice every caller (loop + tests) constructs with an explicit ts.
    """
    return datetime(1970, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Dataclass — backwards-compatible enrichment of the sprint_9 shape.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PerformanceWindow:
    """L3 advisor input bundle — sprint_9 scalars + sprint_10 history.

    Sprint_9 scalar fields (carried over from
    :class:`agent.engines.strategy_advisor.PerformanceWindow` v0.1):

    Parameters
    ----------
    tick:
        The tick number at which the trigger fired. Useful for the
        advisor's narrative ("over the last 100 ticks I've observed...").
        Kept for sprint_9 NoOp-scaffold back-compat; sprint_10 prefers
        the ``tick_count`` field which carries the same number.

    ts:
        ISO-8601 / UTC :class:`datetime` of the trigger event. The
        sprint_10 advisor wiring will pass this through to the LLM
        call as the ``asof`` timestamp.

    agent_id:
        The loop's :attr:`SandboxPhase2Loop.agent_id`. Sprint_10's LLM
        keys provider-side caching off this so multi-agent runs don't
        cross-pollinate advice.

    phase:
        Current lifecycle phase.

    current_weights:
        Snapshot of the 6-parameter fusion model RIGHT NOW.

    baseline_weights:
        Snapshot at the time of the LAST advice call (or cold-start
        weights if no advice has fired yet).

    recent_pnl_window_usd:
        Net USD P&L over the last 50 settled bets (same window the L2
        reflection uses). Sprint_10's advisor compares this against a
        running mean to detect regime change.

    trigger:
        Which condition fired this advice cycle (``"tick_interval"`` /
        ``"weight_stability"``).

    Sprint_10 history fields (NEW in T-B-029):

    recent_pnl:
        Last :data:`RECENT_PNL_TAIL` settled-bet realised PnL values
        (USD floats). Empty list on cold start or when the JSONL stream
        is unreadable. Populated by :func:`fold_pnl_from_settled`.

    weight_trajectory:
        Last :data:`WEIGHT_TRAJECTORY_TAIL` ticks of fusion
        :class:`Weights` (oldest first). The advisor uses this to spot
        drift TRAJECTORIES rather than just current-vs-baseline. Empty
        list on cold start. Populated by
        :func:`fold_weight_trajectory_from_jsonl`.

    recent_reflections:
        Last :data:`RECENT_REFLECTIONS_TAIL` L2 reflection narrative
        strings (oldest first). The advisor uses these to see the
        agent's own recent self-talk and propose prompt tweaks /
        structural changes. Populated by
        :func:`fold_recent_reflections_from_jsonl`.

    tick_count:
        Total tick count at trigger time (same number as ``tick``,
        renamed per T-B-029 brief for prompt-rendering clarity).
        Defaults to 0 so older callers that only set ``tick`` keep
        working; the L3 advisor reads :attr:`tick_count_or_tick` which
        falls back to ``tick`` when ``tick_count`` is zero.

    Notes
    -----
    The dataclass is ``frozen=True`` so the loop can hand the same
    instance to multiple consumers (advisor + state hook event) without
    worrying about mid-flight mutation. New fields are appended at the
    END with default values so the sprint_9 positional + keyword
    constructions stay valid — pydantic-like additive evolution.
    """

    tick: int
    ts: datetime
    agent_id: str
    phase: Phase
    current_weights: Weights
    baseline_weights: Weights
    recent_pnl_window_usd: float
    trigger: str = field(default="tick_interval")
    # ── Sprint_10 enrichment ──────────────────────────────────────────────
    recent_pnl: list[float] = field(default_factory=list)
    weight_trajectory: list[Weights] = field(default_factory=list)
    recent_reflections: list[str] = field(default_factory=list)
    tick_count: int = 0

    @property
    def tick_count_or_tick(self) -> int:
        """Return ``tick_count`` if set, else fall back to ``tick``.

        Sprint_9 callers only set the older ``tick`` field; sprint_10
        callers set ``tick_count`` (or both). The advisor reads this
        property so it gets the right number without caring about the
        caller generation.
        """
        return self.tick_count if self.tick_count > 0 else self.tick


# --------------------------------------------------------------------------- #
# Pure folder helpers — read JSONL → return list[primitive].
# --------------------------------------------------------------------------- #


def fold_pnl_from_settled(
    path: Path,
    *,
    tail: int = RECENT_PNL_TAIL,
) -> list[float]:
    """Read ``settled_bets.jsonl`` and return the last ``tail`` PnL floats.

    The settlement poller (T-B-019) writes one
    :class:`agent.data.sandbox_state.SettledBetRecord` per settled bet.
    The ``pnl_usd`` column carries the realised USD PnL (negative on
    loss, zero on void). Order on disk is FIFO (oldest → newest); this
    helper returns the LAST ``tail`` values, also FIFO.

    Edge cases (covered by the impl tests):

    * Missing file → return ``[]``.
    * Empty file → return ``[]``.
    * File present but no settled rows → return ``[]``.
    * Corrupt JSON lines → :func:`iter_jsonl` skips them silently per
      the runtime invariant; this helper inherits that posture.
    * Non-numeric / missing ``pnl_usd`` field → row is skipped (we
      can't fold a string into a float and silently coerce-or-skip is
      the safer choice for advisor input).
    * ``NaN`` / ``inf`` in ``pnl_usd`` → row is skipped (advisor
      prompts can't render NaN sensibly).
    """
    if tail <= 0:
        return []
    rows = iter_jsonl(path)
    pnls: list[float] = []
    for row in rows:
        raw = row.get("pnl_usd")
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            # ``bool`` is a subclass of ``int`` — explicitly reject it
            # because a stray ``true`` in the JSON would otherwise fold
            # into the advisor's PnL window.
            continue
        value = float(raw)
        if math.isnan(value) or math.isinf(value):
            continue
        pnls.append(value)
    return pnls[-tail:]


def fold_recent_reflections_from_jsonl(
    path: Path,
    *,
    tail: int = RECENT_REFLECTIONS_TAIL,
) -> list[str]:
    """Read ``reflections.jsonl`` and return the last ``tail`` narratives.

    The reflection writer (T-B-024) writes one
    :class:`agent.engines.reflection.SandboxReflectionRecord` per trigger
    fire. The ``narrative`` column carries the LLM-produced summary.

    Same edge-case posture as :func:`fold_pnl_from_settled`: missing /
    empty / non-string narratives → skipped silently.
    """
    if tail <= 0:
        return []
    rows = iter_jsonl(path)
    narratives: list[str] = []
    for row in rows:
        raw = row.get("narrative")
        if not isinstance(raw, str) or not raw.strip():
            continue
        narratives.append(raw)
    return narratives[-tail:]


def fold_weight_trajectory_from_jsonl(
    path: Path,
    *,
    tail: int = WEIGHT_TRAJECTORY_TAIL,
) -> list[Weights]:
    """Read ``reflections.jsonl`` and return the last ``tail`` Weights.

    Each reflection record carries a ``weight_snapshot`` dict keyed by
    the canonical :data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS`
    (``"w_r"``, ``"alpha_0"`` .. ``"alpha_2"``, ``"beta_0"``, ``"rho"``).
    We project those scalars back into a :class:`Weights` model so the
    advisor sees a homogeneous list.

    A row whose snapshot fails Pydantic validation (e.g. weights don't
    normalise — surprising but possible if a future writer bug leaks)
    is skipped silently. The advisor cannot fold a malformed weight
    record into a sensible prompt; better to drop than to lie.
    """
    if tail <= 0:
        return []
    rows = iter_jsonl(path)
    trajectory: list[Weights] = []
    for row in rows:
        snapshot = row.get("weight_snapshot")
        if not isinstance(snapshot, dict):
            continue
        weights = _try_project_weight_snapshot(cast(dict[str, Any], snapshot))
        if weights is None:
            continue
        trajectory.append(weights)
    return trajectory[-tail:]


def _try_project_weight_snapshot(snapshot: dict[str, Any]) -> Weights | None:
    """Project the dashboard's flat snapshot shape into a :class:`Weights`.

    Mirrors :data:`agent.engines.reflection.REFLECTION_WEIGHT_KEYS`. The
    sprint_9 reflection snapshot shape is::

        {"w_r": float, "alpha_0": float, "alpha_1": float, "alpha_2": float,
         "beta_0": float, "rho": float}

    We derive ``w_s = 1 - w_r`` and ``beta_1 = 1 - beta_0`` because the
    snapshot stores only the independent components (the constraint
    ``w_r+w_s=1`` etc. is implicit on disk). Validation is delegated to
    :class:`Weights.__init__` so any divergence raises and we drop the
    row.
    """
    try:
        w_r = float(snapshot["w_r"])
        alpha = [
            float(snapshot["alpha_0"]),
            float(snapshot["alpha_1"]),
            float(snapshot["alpha_2"]),
        ]
        beta_0 = float(snapshot["beta_0"])
        rho = float(snapshot["rho"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        return Weights(
            w_r=w_r,
            w_s=max(0.0, min(1.0, 1.0 - w_r)),
            alpha=alpha,
            beta=[beta_0, max(0.0, min(1.0, 1.0 - beta_0))],
            rho=rho,
        )
    except Exception:
        return None


__all__ = [
    "RECENT_PNL_TAIL",
    "RECENT_REFLECTIONS_TAIL",
    "WEIGHT_TRAJECTORY_TAIL",
    "PerformanceWindow",
    "fold_pnl_from_settled",
    "fold_recent_reflections_from_jsonl",
    "fold_weight_trajectory_from_jsonl",
]
