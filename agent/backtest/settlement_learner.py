"""Settlement-time self-learning bridge (Task L3, Plan 2).

The sandbox settlement poller speaks a flat-float Protocol
(``update(*, phase: str, signals: dict[str, float], outcome) -> None`` —
see :class:`agent.runtime.sandbox_settlement_poller.WeightUpdater`). The
real learner is :class:`agent.engines.weight_updater.WeightUpdater`, whose
``update_from_settlement`` takes a typed ``Phase`` enum + named scalars and
RETURNS new :class:`agent.core.state.Weights`.

:class:`_SettlementLearningWeightUpdater` is the adapter between the two:
it reads the per-engine ``score_<engine>`` float keys the poller flattens
onto the ``signals`` map (from the due open ``BetRecord``'s persisted
``signal_scores``) plus ``pnl_usd`` / ``size_usd`` / ``bet_direction``,
calls the real updater, and RE-ASSIGNS the loop's mutable ``_weights`` so
the agent learns from realized PnL.

Timing (Plan-2 Round-1 MED-1 / Round-7): the poller runs at the TOP of
``SandboxPhase2Loop._tick`` BEFORE the decision, so weights re-assigned at
settlement take effect on the SAME tick's decision, not the next one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.core.state import Phase
from agent.engines.slot_aliases import alias_slot
from agent.engines.weight_updater import WeightUpdater

logger = logging.getLogger(__name__)

# WeightUpdaterPhase string -> Phase enum. The poller passes a
# ``WeightUpdaterPhase`` label (e.g. "PHASE_2_EXTENDED") that the real
# :class:`Phase` enum has NO member for, so a direct ``Phase[phase]`` would
# KeyError. Phase members are INFANCY / APPRENTICE / MASTER / TERMINAL only
# (agent/core/state.py). The sandbox-extended Phase 2 maps onto the
# apprentice freeze rules (β + ρ + α + w all train).
_PHASE_MAP: dict[str, Phase] = {
    "PHASE_1_INFANCY": Phase.PHASE_1_INFANCY,
    "PHASE_2_APPRENTICE": Phase.PHASE_2_APPRENTICE,
    "PHASE_2_EXTENDED": Phase.PHASE_2_APPRENTICE,
    "PHASE_3_MASTER": Phase.PHASE_3_MASTER,
    "PHASE_4_TERMINAL": Phase.PHASE_4_TERMINAL,
}

_SCORE_PREFIX = "score_"


def _unflatten_scores(signals: dict[str, float]) -> dict[str, float]:
    """Strip the ``score_`` prefix off per-engine keys and upgrade any legacy
    slot name to its post-rename key (:func:`alias_slot`).

    In-flight ``open_bets.jsonl`` BetRecords written before the 2026-06-16 slot
    rename carry ``score_smart_money`` etc.; without the alias the renamed
    weight_updater would ``.get(new_key, 0.0)`` and silently zero that engine's
    settlement credit. ``alias_slot`` is the identity for already-new keys, so
    this is a zero-behavior-change normalization for fresh data.
    """
    return {
        alias_slot(k[len(_SCORE_PREFIX) :]): v
        for k, v in signals.items()
        if k.startswith(_SCORE_PREFIX)
    }


@dataclass
class _SettlementLearningWeightUpdater:
    """Adapter implementing the settlement poller's ``WeightUpdater`` Protocol.

    ``inner``:
        The real :class:`agent.engines.weight_updater.WeightUpdater`. Owns
        the EMA state — construct a FRESH one per replay so a sweep's
        replays stay independent.

    ``weights_holder``:
        The object whose mutable ``_weights`` we re-assign and whose
        ``_desperate`` flag we honor — in practice the
        :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`.
    """

    inner: WeightUpdater
    weights_holder: Any  # has mutable ._weights (Weights) and ._desperate (bool)

    async def update(
        self,
        *,
        phase: str | Phase,
        signals: dict[str, float],
        outcome: object,
    ) -> None:
        # signals is a FLAT dict[str, float]: pnl_usd / size_usd +
        # "score_<engine>" per-engine keys + "bet_direction" (+1 YES / -1 NO),
        # all flattened by the poller from the due open BetRecord.
        scores = _unflatten_scores(signals)
        # bet_direction is REQUIRED — do NOT silently default to YES (+1),
        # which would invert learning for NO bets. Skip + warn if absent.
        if "bet_direction" not in signals:
            logger.warning(
                "settlement learner: missing bet_direction — skipping update"
            )
            return

        resolved_phase = phase if isinstance(phase, Phase) else _PHASE_MAP[phase]
        new_weights = await self.inner.update_from_settlement(
            current=self.weights_holder._weights,
            phase=resolved_phase,
            pnl_usd=signals.get("pnl_usd", 0.0),
            size_usd=signals.get("size_usd", 0.0),
            signal_scores=scores,
            bet_direction=signals["bet_direction"],
            desperate=bool(getattr(self.weights_holder, "_desperate", False)),
        )
        self.weights_holder._weights = new_weights


__all__ = ["_SettlementLearningWeightUpdater", "_unflatten_scores"]
