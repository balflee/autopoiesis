"""Per-tick narrative writer.

Step 9 of the agent loop (TECHNICAL_PLAN §4.1) calls
:func:`write_narrative` after :meth:`MemoryBank.write_tick`. The
narrative is the 1-2 sentence diary entry shown on the dashboard
(Track D's ConsciousnessStream component, rendered ≥28px per PRD §8).

Per PRD §4.6 the LLM call is **fail-fast → template fallback**: a single
attempt, no retry. If the LLM is unavailable, hit the deterministic
template path so the tick budget isn't blown by network back-off. This
module ships the template path in sprint_1; the LLM-backed path lands
in sprint_2 alongside the production LLM wiring (Gemini 3.1 Flash Lite
via google-genai; concrete client in agent/llm/gemini_client.py
sprint_4 T-B-006).
"""

from __future__ import annotations

from typing import Final

from agent.core.state import ActionKind, TickPayload

# Templates intentionally short (≤2 sentences) so they fit the
# dashboard's per-tick render budget. Mandarin variants land in
# sprint_2 — the dashboard's i18n layer is the right place to pivot.
_BET_TEMPLATE: Final = (
    "Tick {tick}: placed ${size:.2f} on {market} ({side}). "
    "Edge {edge:+.2%}, breath {breath:.1f} remaining."
)
_NO_BET_TEMPLATE: Final = (
    "Tick {tick}: passed this market. "
    "Reason: {reason}. Breath {breath:.1f} remaining."
)


def write_narrative(payload: TickPayload) -> str:
    """Return the narrative string for this tick.

    Sprint_1: deterministic template path only. Sprint_2 introduces an
    LLM call before the template fallback per PRD §4.6 (fail-fast — one
    attempt, no retry).
    """
    if payload.action.kind == ActionKind.BET:
        # Action.validate_shape guarantees market_id/side/size_usd are
        # set on BET; the `or` fallbacks satisfy mypy + survive a
        # malformed BET tick without crashing the dashboard.
        market = payload.action.market_id or "<unknown>"
        side = payload.action.side.value if payload.action.side else "<?>"
        size = float(payload.action.size_usd or 0.0)
        edge = float(payload.action.edge_pct or 0.0)
        return _BET_TEMPLATE.format(
            tick=payload.tick,
            size=size,
            market=market,
            side=side,
            edge=edge,
            breath=payload.vitals.breath,
        )

    reason = payload.action.no_bet_reason or "no edge above threshold"
    return _NO_BET_TEMPLATE.format(
        tick=payload.tick,
        reason=reason,
        breath=payload.vitals.breath,
    )
