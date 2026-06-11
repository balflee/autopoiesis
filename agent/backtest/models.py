"""Public Pydantic models for the backtest replay pipeline.

This module is the canonical owner of cross-module shapes the replay
emits — today :class:`BetSettlement` (T-B-035 ledger entry consumed by
T-B-036's analytic metrics + Track D's workshop drilldown). Living in a
dedicated file (not buried inside :mod:`agent.backtest.replay_runner`)
keeps a single import surface for downstream consumers:
:mod:`agent.backtest.sweep_runner`, :mod:`agent.backtest.metrics`, and
the dashboard's results.json loader.

Decimal-typed money fields
--------------------------

``stake_usd``, ``payout_usd``, and ``pnl_usd`` are :class:`decimal.Decimal`
so post-run settlement accumulation stays exact to 6 decimal places
across long bet sequences (no float drift). Pydantic v2 serialises
:class:`Decimal` as a JSON string by default — exactly what the
determinism contract for ``results.json`` round-trips wants.

Spec anchors
------------

* T-B-035 task brief: BetSettlement Pydantic model with fields
  ``{bet_id, market_id, settled_ts, stake_usd, payout_usd, pnl_usd,
  outcome}``.
* T-B-036 task brief: :func:`agent.backtest.metrics.compute_win_rate_pct`
  consumes a ``list[BetSettlement]``; voids excluded from the
  denominator.
* PRD.md §6 (BREATH Economy): PnL realised at market resolution.
* PRD.md §8 (Dashboard): workshop dogfood loop requires non-zero PnL
  signal for the operator to compare configs.
* TECHNICAL_PLAN.md §5.4 (Agent → Dashboard data contract): ReplayMetrics
  carries per-bet settlement detail for the workshop drilldown.
* CEO direction D-S11-001 §scope-decisions §2: cached MarketSnapshot
  outcomePrices are the authoritative resolution truth in replay.

Interface registry
------------------

Mirrored by ``.dev/contracts/bet_settlement.v1.0.0.json`` (producer-
owns-schema convention). Bumping the Pydantic shape requires a
coordinated registry version bump.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Outcome literal mirrors the schema's enum. Distinct from
# :class:`agent.data.polymarket_settlement.SettlementResult.outcome`
# (yes / no / void) — here we collapse the "which side won" axis into
# win / loss from the BET-placer's perspective so the dashboard can
# render WLT badges without re-deriving the bet's side at read time.
BetOutcomeLiteral = Literal["win", "loss", "void"]


class BetSettlement(BaseModel):
    """One settled bet — replay-side ledger entry.

    Fields mirror ``.dev/contracts/bet_settlement.v1.0.0.json``
    exactly; refer to that schema for field-level prose. Producer:
    :mod:`agent.backtest.replay_runner` post-loop settlement scan.
    Consumers: :mod:`agent.backtest.metrics` (win_rate denominator) +
    :mod:`agent.backtest.sweep_runner` ledger projection.

    ``frozen=True`` keeps the model hashable + safe to share across
    aggregation passes; ``extra='forbid'`` enforces the schema's
    ``additionalProperties: false`` invariant on the Python side.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    bet_id: str
    market_id: str
    settled_ts: datetime
    stake_usd: Decimal
    payout_usd: Decimal
    pnl_usd: Decimal
    outcome: BetOutcomeLiteral


__all__ = ["BetOutcomeLiteral", "BetSettlement"]
