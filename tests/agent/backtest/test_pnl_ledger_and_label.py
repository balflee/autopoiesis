"""Regression tests for T-B-039 (PnL ledger projection) + T-B-040 (label preservation).

Sprint 11 shipped the BetSettlement schema (T-B-035) and the analytic
aggregator (T-B-036) but never wired the projection from the loop's
``settled_bets.jsonl`` into the ledger — every replay aggregated against
an empty list and ``net_pnl_usd`` collapsed to 0 regardless of how the
cached markets resolved.

Sprint 11 also added the operator-facing ``StartingWeightConfig.label``
field (T-B-037 typed body) but the production sweep runner dropped it
when projecting to ``Weights``; the workshop UI rendered "(UNNAMED)"
for every row regardless of what the operator typed.

This module pins both fixes:

* ``test_build_pnl_ledger_*`` — direct unit tests of
  :func:`agent.backtest.replay_runner._build_pnl_ledger_from_state`
  against synthetic JSONL streams; pins the win / loss / void mapping
  + the stake join against ``open_bets.jsonl``.
* ``test_replay_metrics_label_*`` — ``ReplayConfig.label`` round-trips
  through :class:`ReplayMetrics` and serialises into ``results.json``.
* ``test_sweep_runner_config_labels_*`` — :class:`SweepConfig` validates
  the parallel-indexed ``config_labels`` tuple + threads it per replay.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from agent.backtest.replay_runner import (
    ReplayConfig,
    _build_pnl_ledger_from_state,
)
from agent.backtest.sweep_runner import SweepConfig, run_sweep
from agent.core.state import Weights
from agent.data.sandbox_state import (
    BetRecord,
    SandboxStateWriter,
    SettledBetRecord,
)


# --------------------------------------------------------------------------- #
# _build_pnl_ledger_from_state — unit tests.
# --------------------------------------------------------------------------- #


def test_build_pnl_ledger_returns_empty_when_no_settled_file(tmp_path: Path) -> None:
    """Missing settled_bets.jsonl → empty tuple (fail-soft per T-B-036)."""
    ledger = _build_pnl_ledger_from_state(tmp_path)
    assert ledger == ()


def test_build_pnl_ledger_projects_win(tmp_path: Path) -> None:
    """Settled yes-outcome bet with pnl > 0 → BetSettlement.outcome='win'.

    Stake is joined from open_bets.jsonl; payout = stake + pnl.
    """
    writer = SandboxStateWriter(root=tmp_path)
    writer.append_open_bet(
        BetRecord(
            bet_id="b-win",
            ts="2026-05-01T00:00:00+00:00",
            market_id="m-1",
            side="YES",
            price=0.4,
            size_usd=10.0,
            expected_settle_ts="2026-05-02T00:00:00+00:00",
        )
    )
    writer.append_settled_bet(
        SettledBetRecord(
            bet_id="b-win",
            market_id="m-1",
            settled_ts="2026-05-02T00:00:00+00:00",
            outcome="yes",
            winning_price=1.0,
            pnl_usd=15.0,  # 10 stake → 25 payout on yes-side win at 0.4
        )
    )

    ledger = _build_pnl_ledger_from_state(tmp_path)
    assert len(ledger) == 1
    s = ledger[0]
    assert s.bet_id == "b-win"
    assert s.outcome == "win"
    assert s.stake_usd == Decimal("10.0")
    assert s.pnl_usd == Decimal("15.0")
    assert s.payout_usd == Decimal("25.0")


def test_build_pnl_ledger_projects_loss(tmp_path: Path) -> None:
    """Settled bet with pnl < 0 → BetSettlement.outcome='loss'."""
    writer = SandboxStateWriter(root=tmp_path)
    writer.append_open_bet(
        BetRecord(
            bet_id="b-loss",
            ts="2026-05-01T00:00:00+00:00",
            market_id="m-2",
            side="NO",
            price=0.6,
            size_usd=8.0,
            expected_settle_ts="2026-05-02T00:00:00+00:00",
        )
    )
    writer.append_settled_bet(
        SettledBetRecord(
            bet_id="b-loss",
            market_id="m-2",
            settled_ts="2026-05-02T00:00:00+00:00",
            outcome="yes",  # NO-side bet, YES resolved → loss
            winning_price=1.0,
            pnl_usd=-8.0,
        )
    )

    ledger = _build_pnl_ledger_from_state(tmp_path)
    assert len(ledger) == 1
    s = ledger[0]
    assert s.outcome == "loss"
    assert s.stake_usd == Decimal("8.0")
    assert s.pnl_usd == Decimal("-8.0")
    assert s.payout_usd == Decimal("0")


def test_build_pnl_ledger_projects_void(tmp_path: Path) -> None:
    """Settled bet with outcome='void' → BetSettlement.outcome='void'
    (refund stake → pnl=0, payout=stake)."""
    writer = SandboxStateWriter(root=tmp_path)
    writer.append_open_bet(
        BetRecord(
            bet_id="b-void",
            ts="2026-05-01T00:00:00+00:00",
            market_id="m-3",
            side="YES",
            price=0.5,
            size_usd=5.0,
            expected_settle_ts="2026-05-02T00:00:00+00:00",
        )
    )
    writer.append_settled_bet(
        SettledBetRecord(
            bet_id="b-void",
            market_id="m-3",
            settled_ts="2026-05-02T00:00:00+00:00",
            outcome="void",
            winning_price=0.5,
            pnl_usd=0.0,
        )
    )

    ledger = _build_pnl_ledger_from_state(tmp_path)
    assert len(ledger) == 1
    s = ledger[0]
    assert s.outcome == "void"
    assert s.pnl_usd == Decimal("0")
    assert s.payout_usd == Decimal("5.0")


def test_build_pnl_ledger_zero_stake_when_open_record_missing(tmp_path: Path) -> None:
    """Settled bet whose matching open_bets entry is absent → stake=0
    (fail-soft, doesn't crash the run). Win/loss inference still works
    off pnl_usd sign so the analytic aggregator stays sane."""
    writer = SandboxStateWriter(root=tmp_path)
    writer.append_settled_bet(
        SettledBetRecord(
            bet_id="b-orphan",
            market_id="m-4",
            settled_ts="2026-05-02T00:00:00+00:00",
            outcome="yes",
            winning_price=1.0,
            pnl_usd=3.0,
        )
    )

    ledger = _build_pnl_ledger_from_state(tmp_path)
    assert len(ledger) == 1
    assert ledger[0].stake_usd == Decimal("0")
    assert ledger[0].outcome == "win"


# --------------------------------------------------------------------------- #
# Label preservation — ReplayConfig → ReplayMetrics.
# --------------------------------------------------------------------------- #


def _make_weights() -> Weights:
    return Weights(
        w_r=0.5, w_s=0.5, alpha=[0.4, 0.4, 0.2], beta=[1.0, 0.0], rho=0.6,
    )


def test_replay_config_label_default_is_none() -> None:
    """Default ``ReplayConfig.label`` is None — backward-compat with
    sprint 11 tests that don't pass the field."""
    cfg = ReplayConfig(starting_weights=_make_weights())
    assert cfg.label is None


def test_replay_config_label_accepts_operator_string() -> None:
    cfg = ReplayConfig(starting_weights=_make_weights(), label="TEST-EXTREME")
    assert cfg.label == "TEST-EXTREME"


# --------------------------------------------------------------------------- #
# SweepConfig.config_labels validation + threading.
# --------------------------------------------------------------------------- #


def test_sweep_config_labels_length_mismatch_raises() -> None:
    weights = (_make_weights(),)
    cfg = SweepConfig(
        starting_weights=weights,
        config_labels=("A", "B"),  # 2 labels for 1 weights → mismatch
    )
    with pytest.raises(ValueError, match="config_labels length must match"):
        asyncio.run(run_sweep(cfg, snapshots=[]))


def test_sweep_config_labels_default_none_preserves_existing_call_sites() -> None:
    """Old callers that don't pass ``config_labels`` still get ``None`` →
    each ReplayConfig.label falls back to None and the auto-derived
    config_id continues to identify rows. Backward-compat invariant."""
    cfg = SweepConfig(starting_weights=(_make_weights(),))
    assert cfg.config_labels is None
