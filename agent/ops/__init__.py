"""Operational tooling for Phase 3 LIVE — monitors + reconcilers.

Sprint_5 (T-B-010) introduces two ops modules that the operator runs
alongside the live agent during the Demo §9 4:00-5:00 climax:

* :mod:`agent.ops.live_monitor` — observe-only async daemon that
  watches heartbeat, energy drain, RPC latency, Polymarket WS health,
  and Gemini cost. Emits structured CRITICAL alerts via the dashboard
  event bus when any indicator crosses its threshold. NEVER writes a
  file (enforced by AST scan test).
* :mod:`agent.ops.settlement_reconciler` — per Polymarket settle event
  finds the matching signed BankrollUpdate attestation on L3 (TP §3.7
  EIP-712 schema). Three-factor identity ``(nonce, marketId, outcome)``
  + per-signer monotonic nonce replay protection mirror the on-chain
  ``usedNonces[signer][nonce]`` mapping the EnergyController enforces.
"""

from __future__ import annotations

__all__: list[str] = []
