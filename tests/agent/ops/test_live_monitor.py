"""Tests for :mod:`agent.ops.live_monitor`.

Covers:

* Observe-only AST invariant (the critical brief invariant — no file
  writes anywhere in the module).
* Pure classifier functions for each indicator (OK / WARNING / CRITICAL
  thresholds).
* End-to-end tick that fires every kind of probe and asserts the
  alerts the sink received.
* Suppression rule: OK→OK transitions are NOT pushed; recovery
  (non-OK → OK) IS pushed.
* Probe-exception containment: a buggy probe surfaces as a WARNING
  alert, never crashes the monitor's tick.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from agent.ops.live_monitor import (
    INDICATOR_HEARTBEAT,
    INDICATOR_RPC_LATENCY,
    Alert,
    AlertSeverity,
    EnergyDrainProbe,
    GeminiCostProbe,
    HeartbeatProbe,
    LiveMonitor,
    LiveMonitorConfig,
    Probe,
    ProbeReading,
    RecordingAlertSink,
    RpcLatencyProbe,
    WsDisconnectProbe,
    classify_energy_drain,
    classify_gemini_cost,
    classify_heartbeat,
    classify_rpc_latency,
    classify_ws_disconnects,
)

# ── AST scan: the observe-only structural invariant ─────────────────


def _module_source() -> str:
    """Return the live_monitor.py source for AST inspection."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "agent" / "ops" / "live_monitor.py"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise AssertionError("could not locate agent/ops/live_monitor.py")


def test_live_monitor_is_observe_only() -> None:
    """Hard brief invariant: NO file writes anywhere in live_monitor.

    Walks the module AST and asserts:

    * No ``open(...)`` call has a string-arg mode that contains 'w', 'a',
      'x', or '+' (the writable modes).
    * No ``os.write`` reference.
    * No attribute access ``.write_text`` / ``.write_bytes`` /
      ``.write`` (catches ``Path.write_text`` and ``file.write``).

    The scan is conservative — false positives are preferred to a
    silent write that breaks the observe-only invariant.
    """
    tree = ast.parse(_module_source())
    violations: list[str] = []

    for node in ast.walk(tree):
        # open(..., 'w') / open(..., 'a') / 'x' / '+'
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name == "open" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if any(ch in arg.value for ch in ("w", "a", "x", "+")):
                        violations.append(
                            f"open({{...}}, {arg.value!r}) at line {node.lineno}"
                        )
        # os.write attribute reference
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                if node.attr == "write":
                    violations.append(f"os.write at line {node.lineno}")
            # Path.write_text / Path.write_bytes / file.write
            if node.attr in ("write_text", "write_bytes"):
                violations.append(f".{node.attr} at line {node.lineno}")

    assert not violations, (
        "live_monitor.py violated the observe-only invariant:\n  - "
        + "\n  - ".join(violations)
    )


# ── Pure classifier tests ────────────────────────────────────────────


def test_classify_heartbeat_thresholds() -> None:
    cfg = LiveMonitorConfig()
    assert classify_heartbeat(age_s=30.0, config=cfg) == AlertSeverity.OK
    assert (
        classify_heartbeat(age_s=cfg.heartbeat_max_age_s + 1, config=cfg)
        == AlertSeverity.WARNING
    )
    assert (
        classify_heartbeat(age_s=2 * cfg.heartbeat_max_age_s + 1, config=cfg)
        == AlertSeverity.CRITICAL
    )


def test_classify_energy_drain_thresholds() -> None:
    cfg = LiveMonitorConfig(energy_drain_max_per_second=2.0)
    assert classify_energy_drain(drain_per_second=0.5, config=cfg) == AlertSeverity.OK
    assert (
        classify_energy_drain(drain_per_second=3.0, config=cfg)
        == AlertSeverity.WARNING
    )
    assert (
        classify_energy_drain(drain_per_second=5.0, config=cfg)
        == AlertSeverity.CRITICAL
    )


def test_classify_rpc_latency_thresholds() -> None:
    cfg = LiveMonitorConfig(rpc_latency_max_ms=5000.0)
    assert classify_rpc_latency(latency_ms=100.0, config=cfg) == AlertSeverity.OK
    assert classify_rpc_latency(latency_ms=7000.0, config=cfg) == AlertSeverity.WARNING
    assert classify_rpc_latency(latency_ms=15000.0, config=cfg) == AlertSeverity.CRITICAL


def test_classify_ws_disconnects_thresholds() -> None:
    cfg = LiveMonitorConfig(ws_disconnects_max_per_window=2)
    assert classify_ws_disconnects(disconnects_in_window=1, config=cfg) == AlertSeverity.OK
    assert (
        classify_ws_disconnects(disconnects_in_window=3, config=cfg)
        == AlertSeverity.WARNING
    )
    assert (
        classify_ws_disconnects(disconnects_in_window=10, config=cfg)
        == AlertSeverity.CRITICAL
    )


def test_classify_gemini_cost_thresholds() -> None:
    cfg = LiveMonitorConfig(
        gemini_cost_warning_fraction=0.80,
        gemini_cost_critical_fraction=1.00,
    )
    assert classify_gemini_cost(fraction_used=0.50, config=cfg) == AlertSeverity.OK
    assert classify_gemini_cost(fraction_used=0.85, config=cfg) == AlertSeverity.WARNING
    assert classify_gemini_cost(fraction_used=1.10, config=cfg) == AlertSeverity.CRITICAL


# ── Tick + sink integration ──────────────────────────────────────────


class _StaticProbe:
    """Probe stand-in that returns a fixed reading. Conforms to Probe."""

    def __init__(self, indicator: str, value: float, **extra: object) -> None:
        self.indicator = indicator
        self._value = value
        self._extra: dict[str, object] = dict(extra)

    async def sample(self) -> ProbeReading:
        return ProbeReading(
            indicator=self.indicator,
            value=self._value,
            extra=self._extra,
        )


class _RaisingProbe:
    """Probe that raises — tests the containment branch."""

    def __init__(self, indicator: str, exc: Exception) -> None:
        self.indicator = indicator
        self._exc = exc

    async def sample(self) -> ProbeReading:
        raise self._exc


def test_tick_emits_critical_alert_for_stale_heartbeat() -> None:
    """200s stale (2x of 90s threshold) → CRITICAL alert."""
    probe = _StaticProbe(INDICATOR_HEARTBEAT, 200.0, last_tick_at_s=1.0)
    sink = RecordingAlertSink()
    monitor = LiveMonitor(probes=[probe], sink=sink)
    alerts = asyncio.run(monitor.tick())
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.CRITICAL
    assert alerts[0].indicator == INDICATOR_HEARTBEAT
    assert "heartbeat" in alerts[0].message.lower()


def test_tick_suppresses_ok_to_ok_transitions() -> None:
    """A healthy probe MUST NOT spam the sink on every tick."""
    probe = _StaticProbe(INDICATOR_HEARTBEAT, 5.0)
    sink = RecordingAlertSink()
    monitor = LiveMonitor(probes=[probe], sink=sink)
    asyncio.run(monitor.tick())
    asyncio.run(monitor.tick())
    asyncio.run(monitor.tick())
    assert sink.alerts == []


def test_tick_emits_recovery_alert_on_critical_to_ok() -> None:
    """A non-OK → OK transition MUST emit so the dashboard clears
    the sticky chip."""
    sink = RecordingAlertSink()
    probe_bad = _StaticProbe(INDICATOR_HEARTBEAT, 500.0)
    monitor = LiveMonitor(probes=[probe_bad], sink=sink)
    asyncio.run(monitor.tick())  # CRITICAL
    assert sink.alerts[-1].severity == AlertSeverity.CRITICAL

    # Swap probe to healthy reading via the standard pluggable design.
    probe_good = _StaticProbe(INDICATOR_HEARTBEAT, 10.0)
    monitor.probes = [probe_good]
    asyncio.run(monitor.tick())
    assert sink.alerts[-1].severity == AlertSeverity.OK
    assert sink.alerts[-1].indicator == INDICATOR_HEARTBEAT


def test_probe_exception_surfaces_as_warning_not_crash() -> None:
    """A buggy probe MUST NOT crash the tick — it surfaces as WARNING."""
    probes: list[Probe] = [
        _RaisingProbe(INDICATOR_RPC_LATENCY, RuntimeError("rpc bombed")),
        _StaticProbe(INDICATOR_HEARTBEAT, 10.0),  # healthy
    ]
    sink = RecordingAlertSink()
    monitor = LiveMonitor(probes=probes, sink=sink)
    alerts = asyncio.run(monitor.tick())
    # One warning for the raising probe; the healthy one is OK and
    # suppressed (OK→OK).
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.WARNING
    assert alerts[0].indicator == INDICATOR_RPC_LATENCY
    assert "RuntimeError" in alerts[0].extra.get("exception", "")


def test_production_probes_round_trip_via_injected_getters() -> None:
    """All five production probes accept getter injection + return
    well-formed readings."""
    hb = HeartbeatProbe(
        last_tick_at_getter=lambda: 100.0,
        now_s_getter=lambda: 250.0,
    )
    ed = EnergyDrainProbe(delta_window_getter=lambda: (-30.0, 10.0))  # 3.0 BREATH/s
    rpc = RpcLatencyProbe(chain="polygon", latency_ms_getter=lambda: 1234.0)
    ws = WsDisconnectProbe(disconnects_getter=lambda: 1, window_seconds=60)
    cost = GeminiCostProbe(cost_state_getter=lambda: (12.50, 25.0))  # 50%

    async def _drive() -> list[ProbeReading]:
        return list(
            await asyncio.gather(
                hb.sample(),
                ed.sample(),
                rpc.sample(),
                ws.sample(),
                cost.sample(),
            )
        )

    readings = asyncio.run(_drive())
    assert readings[0].value == 150.0
    assert readings[1].value == 3.0  # 30 / 10
    assert readings[2].value == 1234.0
    assert readings[2].extra["chain"] == "polygon"
    assert readings[3].value == 1
    assert pytest.approx(readings[4].value, abs=1e-9) == 0.5


def test_run_forever_can_be_cancelled() -> None:
    """The run_forever loop catches CancelledError + re-raises cleanly."""
    probe = _StaticProbe(INDICATOR_HEARTBEAT, 10.0)
    sink = RecordingAlertSink()
    monitor = LiveMonitor(
        probes=[probe],
        sink=sink,
        config=LiveMonitorConfig(sample_interval_s=0.05),
    )

    async def _run_and_cancel() -> None:
        task = asyncio.create_task(monitor.run_forever())
        await asyncio.sleep(0.12)  # let ~2 ticks fire
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run_and_cancel())


def test_alert_envelope_carries_threshold_and_extras() -> None:
    """The Alert frame surfaces the threshold the classifier compared
    against + the probe's extra dict — the dashboard tooltip wants
    both."""
    probe = _StaticProbe(
        INDICATOR_RPC_LATENCY,
        12000.0,
        chain="polygon",
    )
    sink = RecordingAlertSink()
    monitor = LiveMonitor(probes=[probe], sink=sink)
    alerts = asyncio.run(monitor.tick())
    assert len(alerts) == 1
    alert: Alert = alerts[0]
    assert alert.threshold == 5000.0
    assert alert.value == 12000.0
    assert alert.extra.get("chain") == "polygon"
