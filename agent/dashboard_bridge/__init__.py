"""Dashboard WS bridge — Track B → Track D event publishers.

This package owns the Pydantic models + emitters that produce frames
conforming to:

* ``.dev/contracts/dashboard_ws_message.v0.4.0.json`` — the 12-kind
  main wire schema (vitals, thought, decision, reflection, weights, etc.).
  v0.4.0 (F1) is a BREAKING bump: renames 3 of the 5 ``signals`` enum keys
  (smart_money->surface_advantage, sentiment_llm->head_to_head,
  crowd_volume->rest_recency). Producer: :class:`WsEventEmitter` (T-B-008).
* ``.dev/contracts/dashboard_death_watch.v0.1.0.json`` — the four Demo
  §9 climax death events (energy_threshold_crossed,
  terminal_lucidity_entered, last_words_emitted, tombstone_minted).
  Producer: :mod:`agent.dashboard_bridge.death_watch_emitter` (T-B-010).

Both schemas are the source of truth — Track D's TypeScript types in
``dashboard/lib/wsContract.ts`` mirror them byte-for-byte.

The general emitter pattern:

* builds a Pydantic model that mirrors the wire schema's ``$defs``;
* sets ``kind`` + ``ts`` + monotonic ``seq``;
* appends the serialised dict to an in-memory queue;
* returns the dict so callers can assert / persist directly.

The ``seq`` counter is per-instance, monotonically increasing from 0.
The dashboard de-duplicates by ``seq``, so replaying a captured tape
through a fresh emitter yields the same render.
"""

from __future__ import annotations

from agent.dashboard_bridge.event_emitter import (
    WS_CONTRACT_VERSION,
    WsEventEmitter,
)

__all__ = ["WS_CONTRACT_VERSION", "WsEventEmitter"]
