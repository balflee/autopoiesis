# ruff: noqa: RUF002
"""Tick wiring tests for the Desperate-Mode + Terminal-Lucidity helpers.

Covers :func:`agent.core.agent.run_pressure_check` (chain dispatch +
retry + critical_op_failed wrap) and :func:`run_terminal_lucidity`
(energy_pct gate + one-shot guard + 200-BREATH cost cap).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from agent.core.agent import (
    LAST_WORDS_ENERGY_PCT_THRESHOLD,
    PressureCheckResult,
    TerminalLucidityResult,
    run_pressure_check,
    run_terminal_lucidity,
)
from agent.core.memory_bank import MemoryBank
from agent.core.pressure_monitor import PressureMonitor
from agent.core.state import Phase
from agent.llm.prompts.last_words import LAST_WORDS_BREATH_COST, LastWordsService

# Reuse the canonical fake LLM client from the llm-package conftest.
# Same dataclass that test_last_words_prompt.py imports — keeps the
# Protocol-conformant fake declared once.
from tests.agent.llm.conftest import FakeGeminiClient


@pytest.fixture
def fake_llm_client() -> FakeGeminiClient:
    return FakeGeminiClient()


class _RecordingDispatcher:
    """Async-callable Protocol stand-in for the chain adapter."""

    def __init__(
        self,
        *,
        raises: list[Exception | None] | None = None,
        tx: str = "0xfeedbeef",
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raises = list(raises or [None])
        self.tx = tx

    async def __call__(
        self,
        *,
        pressure_at_entry: float,
        cycles_held: int,
    ) -> str:
        self.calls.append(
            {"pressure_at_entry": pressure_at_entry, "cycles_held": cycles_held}
        )
        exc = self.raises.pop(0) if self.raises else None
        if exc is not None:
            raise exc
        return self.tx


def _drive_to_intent(monitor: PressureMonitor) -> None:
    """Move the monitor's internal counter to N-1 so the next observe
    fires the intent. Lets the wiring tests exercise the trigger
    branch without re-running two ticks."""
    monitor.observe(
        breath=1500.0,
        effective_burn_rate_per_hour=100.0,
        phase=Phase.PHASE_3_MASTER,
    )


# ── Pressure check wiring ────────────────────────────────────────────


def test_run_pressure_check_returns_sample_without_intent_below_threshold() -> None:
    monitor = PressureMonitor()
    result = asyncio.run(
        run_pressure_check(
            monitor=monitor,
            breath=10_000.0,
            effective_burn_rate_per_hour=100.0,
            phase=Phase.PHASE_3_MASTER,
        )
    )
    assert isinstance(result, PressureCheckResult)
    assert result.intent_dispatched is False
    assert result.dispatch_tx is None
    assert result.critical_op_failed is False
    assert result.sample.pressure < 0.5


def test_run_pressure_check_dispatches_on_intent_via_chain_adapter() -> None:
    monitor = PressureMonitor()
    _drive_to_intent(monitor)
    dispatcher = _RecordingDispatcher(tx="0xdeadbeef")
    result = asyncio.run(
        run_pressure_check(
            monitor=monitor,
            breath=1400.0,  # pressure ≈ 0.6111 > 0.5
            effective_burn_rate_per_hour=100.0,
            phase=Phase.PHASE_3_MASTER,
            chain_dispatcher=dispatcher,
        )
    )
    assert result.intent_dispatched is True
    assert result.dispatch_tx == "0xdeadbeef"
    assert result.dispatch_attempts == 1
    assert result.critical_op_failed is False
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]["cycles_held"] == 2


def test_run_pressure_check_retries_then_surfaces_critical_op_failed() -> None:
    """RPC error 3× in a row → critical_op_failed=True but no crash."""
    monitor = PressureMonitor()
    _drive_to_intent(monitor)
    dispatcher = _RecordingDispatcher(
        raises=[
            RuntimeError("rpc timeout"),
            RuntimeError("rpc timeout"),
            RuntimeError("rpc timeout"),
        ]
    )
    result = asyncio.run(
        run_pressure_check(
            monitor=monitor,
            breath=1400.0,
            effective_burn_rate_per_hour=100.0,
            phase=Phase.PHASE_3_MASTER,
            chain_dispatcher=dispatcher,
            retries=3,
            backoff_base_s=0.0,  # fast test
            backoff_jitter_s=0.0,
        )
    )
    assert result.intent_dispatched is False
    assert result.critical_op_failed is True
    assert result.dispatch_attempts == 3
    assert result.dispatch_error is not None
    assert "RuntimeError" in result.dispatch_error
    assert len(dispatcher.calls) == 3


def test_run_pressure_check_succeeds_on_retry() -> None:
    """First two attempts fail, third succeeds — recovered."""
    monitor = PressureMonitor()
    _drive_to_intent(monitor)
    dispatcher = _RecordingDispatcher(
        raises=[
            RuntimeError("rpc timeout"),
            RuntimeError("rpc timeout"),
            None,
        ],
        tx="0xabc123",
    )
    result = asyncio.run(
        run_pressure_check(
            monitor=monitor,
            breath=1400.0,
            effective_burn_rate_per_hour=100.0,
            phase=Phase.PHASE_3_MASTER,
            chain_dispatcher=dispatcher,
            retries=3,
            backoff_base_s=0.0,
            backoff_jitter_s=0.0,
        )
    )
    assert result.intent_dispatched is True
    assert result.dispatch_tx == "0xabc123"
    assert result.dispatch_attempts == 3
    assert result.critical_op_failed is False


def test_run_pressure_check_without_dispatcher_records_intent() -> None:
    """No chain_dispatcher → result still carries the sample + a
    'no_chain_dispatcher_wired' note (the intent fired off-chain)."""
    monitor = PressureMonitor()
    _drive_to_intent(monitor)
    result = asyncio.run(
        run_pressure_check(
            monitor=monitor,
            breath=1400.0,
            effective_burn_rate_per_hour=100.0,
            phase=Phase.PHASE_3_MASTER,
            chain_dispatcher=None,
        )
    )
    assert result.intent_dispatched is False
    assert result.dispatch_error == "no_chain_dispatcher_wired"
    assert result.critical_op_failed is False
    # The monitor latched even though chain dispatch was skipped — the
    # intent was emitted off-chain and Desperate is irreversible.
    assert monitor.latched is True


# ── Terminal lucidity wiring ─────────────────────────────────────────


def test_run_terminal_lucidity_no_op_when_energy_above_threshold(
    tmp_path: Path,
    fake_llm_client: FakeGeminiClient,
) -> None:
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    service = LastWordsService(llm_client=fake_llm_client, memory_bank=bank)
    result = asyncio.run(
        run_terminal_lucidity(
            service=service,
            agent_id="genesis_v1",
            tick=10,
            breath=200.0,  # 20% > 5%
            initial_breath=1000.0,
            phase_age_days=4.0,
        )
    )
    assert isinstance(result, TerminalLucidityResult)
    assert result.fired is False
    assert result.cache is None
    assert result.breath_cost == 0
    assert len(fake_llm_client.calls) == 0


def test_run_terminal_lucidity_fires_on_threshold_cross_and_caches(
    tmp_path: Path,
    fake_llm_client: FakeGeminiClient,
) -> None:
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    service = LastWordsService(llm_client=fake_llm_client, memory_bank=bank)
    fake_llm_client.responses.append(
        {
            "final_reflection": "...",
            "lesson_for_next_iteration": "...",
            "key_themes": ["humility"],
            "confidence_at_end": 0.4,
        }
    )
    # 4% < 5% threshold.
    first = asyncio.run(
        run_terminal_lucidity(
            service=service,
            agent_id="genesis_v1",
            tick=400,
            breath=40.0,
            initial_breath=1000.0,
            phase_age_days=12.0,
        )
    )
    assert first.fired is True
    assert first.was_cached is False
    assert first.cache is not None
    assert first.breath_cost == LAST_WORDS_BREATH_COST
    assert len(fake_llm_client.calls) == 1

    # Second call — energy still <5% but cache exists → no LLM call,
    # cost 0.
    second = asyncio.run(
        run_terminal_lucidity(
            service=service,
            agent_id="genesis_v1",
            tick=405,
            breath=30.0,
            initial_breath=1000.0,
            phase_age_days=12.1,
        )
    )
    assert second.fired is True
    assert second.was_cached is True
    assert second.breath_cost == 0
    # No additional LLM call.
    assert len(fake_llm_client.calls) == 1


def test_run_terminal_lucidity_zero_initial_breath_is_no_op(
    tmp_path: Path,
    fake_llm_client: FakeGeminiClient,
) -> None:
    """Defensive — initial_breath=0 must not divide by zero."""
    bank = MemoryBank(root=tmp_path)
    bank.ensure_layout()
    service = LastWordsService(llm_client=fake_llm_client, memory_bank=bank)
    result = asyncio.run(
        run_terminal_lucidity(
            service=service,
            agent_id="genesis_v1",
            tick=0,
            breath=0.0,
            initial_breath=0.0,
            phase_age_days=0.0,
        )
    )
    assert result.fired is False


def test_last_words_threshold_is_five_percent_per_prd() -> None:
    assert LAST_WORDS_ENERGY_PCT_THRESHOLD == 5.0
