"""F0 — sandbox loop decision_telemetry wiring (dashboard_ws_message v0.3.0).

Asserts the per-tick ``decision_telemetry`` state-hook surfaces the three
v0.3.0 correlation fields — ``market_id``, ``bet_id`` (== executor
order_id), and ``signals`` (a {engine_name: score} map keyed by the 5
lowercase persisted engine names). The wiring is READ-ONLY telemetry: it
must not change the BET / NO_BET decision, so the existing loop suites
stay green.

Reuses the L3 loop harness builders so the fakes (chain adapter,
executor, scripted bullish inputs) match the rest of the runtime suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.runtime.sandbox_phase2_loop import TickInputs
from tests.agent.runtime.test_sandbox_phase2_loop_l3 import (
    _build_loop,
    _drive,
)

_ENGINE_KEYS = {
    TENNIS_TECHNICAL,
    MARKET_MOMENTUM,
    SURFACE_ADVANTAGE,
    HEAD_TO_HEAD,
    REST_RECENCY,
}


def test_decision_telemetry_hook_carries_v0_3_0_fields_on_bet(
    tmp_path: pytest.TempPathFactory,
) -> None:
    loop, state_hook, _writer, clock = _build_loop(tmp_path=tmp_path)
    _drive(loop, n=1, clock=clock)

    events = state_hook.by_kind("decision_telemetry")
    assert events, "expected a decision_telemetry hook on every tick"
    ev = events[0]

    # BET tick (bullish scripted signals) — market_id + bet_id present.
    assert ev["action"] == "BET"
    assert ev["market_id"] == "m-l3-001"
    assert isinstance(ev["bet_id"], str) and ev["bet_id"]

    # signals is a {engine_name: score} map over exactly the 5 lowercase
    # persisted engine keys.
    signals = ev["signals"]
    assert set(signals.keys()) == _ENGINE_KEYS
    assert all(isinstance(v, float) for v in signals.values())


@dataclass
class _NoMarketInputs:
    """TickInputSource that yields NO eligible market every tick."""

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        return None


def test_decision_telemetry_hook_on_no_bet_omits_bet_id(
    tmp_path: pytest.TempPathFactory,
) -> None:
    loop, state_hook, _writer, clock = _build_loop(tmp_path=tmp_path)
    # Swap in a no-eligible-market source so the tick routes NO_BET.
    loop._tick_inputs = _NoMarketInputs()  # type: ignore[attr-defined]
    _drive(loop, n=1, clock=clock)

    ev = state_hook.by_kind("decision_telemetry")[0]
    assert ev["action"] == "NO_BET"
    # No market eligible -> market_id + signals + bet_id all absent (None).
    assert ev["market_id"] is None
    assert ev["bet_id"] is None
    assert ev["signals"] is None


@dataclass
class _NeutralFusedInputs:
    """TickInputSource that yields an ELIGIBLE market whose 5 signals fuse
    to a neutral edge.

    Every engine reports ``score=0.0`` with high confidence, so the
    fusion's ``fused`` collapses to exactly 0.0 → :data:`DecisionEngine`
    routes ``NO_BET`` with ``no_bet_reason="fused_score_neutral"`` while
    still PASSING the confidence floor. This exercises the OTHER NO_BET
    path: ``inputs`` present + ``signals`` populated + ``market_id``
    present, but ``decide()`` returns NO_BET so NO order is placed (the
    BET branch is skipped and ``bet_id`` stays None).
    """

    market_id: str = "m-l3-001"
    price: float = 0.4
    liquidity_cap_usd: float = 50.0

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        iso = asof_ts.isoformat()
        # score=0.0 on every engine → raw_rational == raw_sentient == 0
        # → fused == 0.0 → NO_BET (fused_score_neutral). Confidence stays
        # high so the confidence floor does NOT short-circuit first.
        signals: dict[str, Signal] = {
            name: Signal(
                score=0.0,
                confidence=0.9,
                available_at=iso,
                rationale="neutral read",
                raw_features={"tick": float(tick)},
            )
            for name in _ENGINE_KEYS
        }
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
        )


def test_decision_telemetry_hook_on_eligible_no_bet_has_market_and_signals(
    tmp_path: Path,
) -> None:
    """Eligible market + populated signals but decide() → NO_BET.

    The decision_telemetry hook must carry ``market_id`` + the full
    5-key ``signals`` map (the read-only telemetry is built BEFORE
    decide() runs, so it survives a NO_BET outcome) while ``bet_id``
    is absent/None (no order placed on a NO_BET).
    """
    loop, state_hook, _writer, clock = _build_loop(tmp_path=tmp_path)
    # Swap in an eligible-but-neutral source so the tick routes NO_BET
    # WITHOUT being a no-eligible-market NO_BET.
    loop._tick_inputs = _NeutralFusedInputs()
    _drive(loop, n=1, clock=clock)

    ev = state_hook.by_kind("decision_telemetry")[0]
    assert ev["action"] == "NO_BET"
    # Eligible market → market_id present.
    assert ev["market_id"] == "m-l3-001"
    # signals built pre-decide → the full 5-key map survives the NO_BET.
    signals = ev["signals"]
    assert set(signals.keys()) == _ENGINE_KEYS
    assert all(v == 0.0 for v in signals.values())
    # No order placed on a NO_BET → bet_id absent/None.
    assert ev["bet_id"] is None
