"""LiveMonitor — observe-only Phase 3 daemon.

The Demo §9 4:00-5:00 climax is the most fragile part of the run: the
agent is on LIVE bankroll, the dashboard is the user-visible surface,
three chains + Polymarket + Gemini are all in the critical path, and
ANY silent breakage spoils the broadcast. The LiveMonitor's job is to
make every failure mode VISIBLE — heartbeat misses, energy drain spikes,
RPC latency excursions, Polymarket WS disconnects, Gemini budget burn.

Hard invariant: **OBSERVE ONLY**. The monitor MUST NOT write to disk,
MUST NOT open files for writing, MUST NOT mutate any state beyond its
own in-memory rolling counters. The AST scan test
:func:`tests.agent.ops.test_live_monitor.test_live_monitor_is_observe_only`
asserts this by walking the module's AST for ``open(... 'w')`` calls
and ``os.write`` references; any violation fails the gate. Alerts are
emitted through an injected :class:`AlertSink` Protocol (the dashboard
event bus in production; a recording fake in tests).

Why observe-only matters
------------------------

A write-capable monitor could mask a bug by silently auto-correcting
(e.g. nudging the cost guard, retrying a failed pin). The Demo §9 §12
posture is that EVERY anomaly surfaces to the operator + the dashboard;
no silent self-healing during the broadcast window. The observe-only
invariant is the structural enforcement of that posture.

Probe shape
-----------

Each kind of indicator is wrapped in a :class:`Probe` Protocol — a
single async ``sample()`` call returning a :class:`ProbeReading`. The
monitor's main loop fires all probes in parallel via
:func:`asyncio.gather`, classifies each reading (OK / WARNING / CRITICAL)
against the configured thresholds, and emits :class:`Alert` events for
anything above OK. The probes themselves are SDK-thin: the production
probes wrap web3.py / py-clob-client / CostGuard, but the monitor
itself sees only the Protocol.

Threshold table (defaults)
--------------------------

================  =======================  ============================
Indicator         Threshold                 Source
================  =======================  ============================
heartbeat         >90s since last tick     TP §4.1 (45-min cadence; 1.5x
                                            cycle = 90s alarm; faster
                                            cadence Demo)
energy_drain      >2 BREATH/s spike (peak  PRD §6.5 (symmetric pnl→BREATH;
                  vs rolling 5-tick mean    conversion rate caps drain
                  for >30s)                 around 1 BREATH/s typical)
rpc_latency_ms    >5000ms on any chain      TP §3 (3 chains: Polygon,
                                            L3, Polymarket aggregator)
ws_disconnects    >2 reconnects in 60s      TP §12 (Polymarket WS feed
                                            health)
gemini_cost_usd   >80% of $25 → WARNING     TP §15 Gap 5 cost cap
                  >100% → CRITICAL
================  =======================  ============================

All thresholds are configurable on :class:`LiveMonitorConfig`; the
defaults match the table above.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Severity + Alert envelope
# ---------------------------------------------------------------------------


class AlertSeverity(StrEnum):
    """Three-level severity ladder matching the dashboard alert chips.

    The dashboard renders OK probes as quiet check-marks; WARNING as
    amber chips; CRITICAL as red chips with a sticky banner. The
    monitor never demotes a CRITICAL back to OK silently — once an
    indicator goes critical the alert sticks until the underlying
    probe reports a healthy reading on the next sample.
    """

    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    """One structured alert frame the monitor pushes to the sink.

    Fields are JSON-serialisable so the dashboard bus can forward the
    frame verbatim to any UI panel that subscribes. The ``indicator``
    field is the stable id used by the dashboard to route the alert
    (e.g. ``heartbeat`` → top-of-screen banner; ``gemini_cost`` →
    cost meter chip).
    """

    indicator: str
    severity: AlertSeverity
    message: str
    ts: str
    value: float | int | str | None = None
    threshold: float | int | str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AlertSink(Protocol):
    """Async sink the monitor pushes :class:`Alert` frames to.

    Production wires this to the dashboard event bus (the same bus
    :mod:`agent.dashboard_bridge.death_watch_emitter` pushes WS frames
    through). Tests inject :class:`RecordingAlertSink` which captures
    every alert for post-hoc assertions.
    """

    async def emit(self, alert: Alert) -> None:
        ...


@dataclass
class RecordingAlertSink:
    """Test sink that captures every alert in :attr:`alerts`.

    Module-level so the e2e test can share the same fake every unit
    test uses — one canonical recording surface, same pattern
    :class:`agent.dashboard_bridge.death_watch_emitter.RecordingTransport`
    follows.
    """

    alerts: list[Alert] = field(default_factory=list)

    async def emit(self, alert: Alert) -> None:
        self.alerts.append(alert)


# ---------------------------------------------------------------------------
# Probe Protocol + readings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeReading:
    """One indicator sample.

    ``indicator`` matches the stable id the dashboard routes on.
    ``value`` is the raw measured number (the monitor compares against
    the configured threshold). ``extra`` carries probe-specific context
    (e.g. ``{"chain": "polygon", "endpoint": "..."}``) the dashboard
    surfaces in the tooltip.
    """

    indicator: str
    value: float
    extra: dict[str, Any] = field(default_factory=dict)


class Probe(Protocol):
    """Narrow async probe interface.

    A single ``sample()`` call returns the current :class:`ProbeReading`.
    The monitor fires every registered probe in parallel via
    :func:`asyncio.gather` each tick; a probe that raises is caught and
    surfaced as a WARNING alert (the monitor's own bug containment).
    """

    indicator: str

    async def sample(self) -> ProbeReading:
        ...


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


# Default sampling cadence. The agent tick fires every 45 min (TP §4.1)
# but the monitor is INDEPENDENT of the tick — it samples on a wall-clock
# cadence so a frozen agent surfaces as a heartbeat alert rather than
# silently disappearing from the dashboard.
DEFAULT_SAMPLE_INTERVAL_S: Final[float] = 30.0

# Default thresholds — see module docstring's threshold table.
DEFAULT_HEARTBEAT_MAX_AGE_S: Final[float] = 90.0
DEFAULT_ENERGY_DRAIN_MAX_PER_SECOND: Final[float] = 2.0
DEFAULT_RPC_LATENCY_MAX_MS: Final[float] = 5000.0
DEFAULT_WS_DISCONNECTS_MAX_PER_WINDOW: Final[int] = 2
DEFAULT_GEMINI_COST_WARNING_FRACTION: Final[float] = 0.80
DEFAULT_GEMINI_COST_CRITICAL_FRACTION: Final[float] = 1.00


@dataclass(frozen=True)
class LiveMonitorConfig:
    """All tunable thresholds in one frozen record.

    Frozen so the monitor's behaviour is pinned at construction —
    reviewers can diff a single argument against PRD/TP rather than
    chasing magic numbers through method calls.
    """

    sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL_S
    heartbeat_max_age_s: float = DEFAULT_HEARTBEAT_MAX_AGE_S
    energy_drain_max_per_second: float = DEFAULT_ENERGY_DRAIN_MAX_PER_SECOND
    rpc_latency_max_ms: float = DEFAULT_RPC_LATENCY_MAX_MS
    ws_disconnects_max_per_window: int = DEFAULT_WS_DISCONNECTS_MAX_PER_WINDOW
    gemini_cost_warning_fraction: float = DEFAULT_GEMINI_COST_WARNING_FRACTION
    gemini_cost_critical_fraction: float = DEFAULT_GEMINI_COST_CRITICAL_FRACTION


# Indicator id constants — exported so tests + the dashboard can route
# alerts without re-typing string literals.
INDICATOR_HEARTBEAT: Final[str] = "heartbeat"
INDICATOR_ENERGY_DRAIN: Final[str] = "energy_drain"
INDICATOR_RPC_LATENCY: Final[str] = "rpc_latency"
INDICATOR_WS_DISCONNECTS: Final[str] = "ws_disconnects"
INDICATOR_GEMINI_COST: Final[str] = "gemini_cost"


# ---------------------------------------------------------------------------
# Classification — pure functions
# ---------------------------------------------------------------------------


def classify_heartbeat(
    *,
    age_s: float,
    config: LiveMonitorConfig,
) -> AlertSeverity:
    """Heartbeat severity ladder.

    Two-step ladder so a slightly-stale tick is a WARNING (operator
    glances at the chip) but a multi-cycle silence is CRITICAL
    (operator must intervene). The 2x ratio mirrors the TP §4.1
    cycle = sample_window invariant.
    """
    if age_s > 2 * config.heartbeat_max_age_s:
        return AlertSeverity.CRITICAL
    if age_s > config.heartbeat_max_age_s:
        return AlertSeverity.WARNING
    return AlertSeverity.OK


def classify_energy_drain(
    *,
    drain_per_second: float,
    config: LiveMonitorConfig,
) -> AlertSeverity:
    """A drain spike above 2x the threshold is CRITICAL; >1x is WARNING.

    The PRD §6.5 burn rate is symmetric (pnl→BREATH via CONVERSION_RATE);
    a sustained drain >2 BREATH/s is anomalous outside an active loss
    cluster. Tests can pin a tighter threshold.
    """
    if drain_per_second > 2 * config.energy_drain_max_per_second:
        return AlertSeverity.CRITICAL
    if drain_per_second > config.energy_drain_max_per_second:
        return AlertSeverity.WARNING
    return AlertSeverity.OK


def classify_rpc_latency(
    *,
    latency_ms: float,
    config: LiveMonitorConfig,
) -> AlertSeverity:
    """Same 2x ladder as heartbeat — minor lag = warning, 2x = critical."""
    if latency_ms > 2 * config.rpc_latency_max_ms:
        return AlertSeverity.CRITICAL
    if latency_ms > config.rpc_latency_max_ms:
        return AlertSeverity.WARNING
    return AlertSeverity.OK


def classify_ws_disconnects(
    *,
    disconnects_in_window: int,
    config: LiveMonitorConfig,
) -> AlertSeverity:
    """Polymarket WS health.

    > threshold = WARNING; > 2x threshold = CRITICAL. The threshold is
    an integer (count in window) so the 2x check is meaningful even
    at small numbers.
    """
    if disconnects_in_window > 2 * config.ws_disconnects_max_per_window:
        return AlertSeverity.CRITICAL
    if disconnects_in_window > config.ws_disconnects_max_per_window:
        return AlertSeverity.WARNING
    return AlertSeverity.OK


def classify_gemini_cost(
    *,
    fraction_used: float,
    config: LiveMonitorConfig,
) -> AlertSeverity:
    """Cost ladder mirrors :class:`agent.llm.cost_guard.CostGuard`.

    The CostGuard already short-circuits to template inference when
    exhausted (its own ``is_warning`` / ``is_exhausted`` getters); this
    classifier surfaces the same state via the monitor so the dashboard
    has a single source of alert chips even when the engine layer is
    independently fail-safed.
    """
    if fraction_used >= config.gemini_cost_critical_fraction:
        return AlertSeverity.CRITICAL
    if fraction_used >= config.gemini_cost_warning_fraction:
        return AlertSeverity.WARNING
    return AlertSeverity.OK


# ---------------------------------------------------------------------------
# Production probe implementations (Protocol-conformant; pluggable)
# ---------------------------------------------------------------------------


@dataclass
class HeartbeatProbe:
    """Reads the agent's last-tick timestamp from an injected getter.

    The getter is a sync callable returning the wall-clock seconds-
    since-epoch of the most recent ``agent_loop`` tick. Wiring up to a
    real tick clock is the lifecycle layer's job; this probe stays
    SDK-thin so unit tests inject a deterministic clock.
    """

    indicator: str = INDICATOR_HEARTBEAT
    last_tick_at_getter: GetterFloat | None = None
    now_s_getter: GetterFloat | None = None

    async def sample(self) -> ProbeReading:
        getter = self.last_tick_at_getter
        now = self.now_s_getter
        if getter is None or now is None:
            return ProbeReading(
                indicator=self.indicator,
                value=float("inf"),
                extra={"missing": "tick_getter"},
            )
        last_tick = float(getter())
        age = max(0.0, float(now()) - last_tick)
        return ProbeReading(
            indicator=self.indicator,
            value=age,
            extra={"last_tick_at_s": last_tick},
        )


@dataclass
class EnergyDrainProbe:
    """Computes drain BREATH/s from the rolling window the lifecycle
    layer maintains.

    The producer (agent_loop) hands the monitor a sync getter returning
    ``(breath_delta_5tick, window_seconds)`` so this probe stays free
    of memory_bank coupling. Drain rate = -delta / window — a positive
    number means BREATH is being consumed; a negative value indicates
    a refill (PRD §6.5 symmetric conversion on WIN) and clamps to 0
    for the drain-spike check.
    """

    indicator: str = INDICATOR_ENERGY_DRAIN
    delta_window_getter: GetterDelta | None = None

    async def sample(self) -> ProbeReading:
        getter = self.delta_window_getter
        if getter is None:
            return ProbeReading(
                indicator=self.indicator,
                value=0.0,
                extra={"missing": "delta_getter"},
            )
        delta_breath, window_s = getter()
        if window_s <= 0:
            return ProbeReading(
                indicator=self.indicator,
                value=0.0,
                extra={"window_s": window_s},
            )
        drain_per_s = max(0.0, -float(delta_breath) / float(window_s))
        return ProbeReading(
            indicator=self.indicator,
            value=drain_per_s,
            extra={"delta_breath": float(delta_breath), "window_s": float(window_s)},
        )


@dataclass
class RpcLatencyProbe:
    """Measures round-trip latency to one chain endpoint.

    Construction takes a sync getter that does the probe — production
    wires this to a thin ``await web3.eth.blockNumber`` wrapper with
    a timer; tests inject a deterministic latency. The probe owns the
    chain id so a multi-chain deployment registers three probes
    (one each for Polygon / L3 / Polymarket aggregator).
    """

    chain: str
    latency_ms_getter: GetterFloat
    indicator: str = INDICATOR_RPC_LATENCY

    async def sample(self) -> ProbeReading:
        latency = float(self.latency_ms_getter())
        return ProbeReading(
            indicator=self.indicator,
            value=latency,
            extra={"chain": self.chain},
        )


@dataclass
class WsDisconnectProbe:
    """Counts Polymarket WS reconnects observed in the rolling window.

    The producer (Polymarket executor) maintains the count; the probe
    just reads it via the injected getter. The window length is the
    producer's policy (typically 60s) — we surface it in ``extra`` so
    the dashboard tooltip is honest about the time scale.
    """

    indicator: str = INDICATOR_WS_DISCONNECTS
    disconnects_getter: GetterInt | None = None
    window_seconds: int = 60

    async def sample(self) -> ProbeReading:
        getter = self.disconnects_getter
        if getter is None:
            return ProbeReading(
                indicator=self.indicator,
                value=0,
                extra={"missing": "ws_getter"},
            )
        count = int(getter())
        return ProbeReading(
            indicator=self.indicator,
            value=count,
            extra={"window_seconds": self.window_seconds},
        )


@dataclass
class GeminiCostProbe:
    """Reads CostGuard state — fraction of $25 hard cap consumed.

    Construction takes a sync getter returning ``(total_usd, hard_cap_usd)``.
    The probe converts to a fraction so the threshold check is
    independent of the cap (a future cap change is a config edit, not
    a probe edit).
    """

    indicator: str = INDICATOR_GEMINI_COST
    cost_state_getter: GetterCostState | None = None

    async def sample(self) -> ProbeReading:
        getter = self.cost_state_getter
        if getter is None:
            return ProbeReading(
                indicator=self.indicator,
                value=0.0,
                extra={"missing": "cost_getter"},
            )
        total_usd, cap_usd = getter()
        if cap_usd <= 0:
            return ProbeReading(
                indicator=self.indicator,
                value=0.0,
                extra={"total_usd": float(total_usd), "cap_usd": float(cap_usd)},
            )
        fraction = float(total_usd) / float(cap_usd)
        return ProbeReading(
            indicator=self.indicator,
            value=fraction,
            extra={"total_usd": float(total_usd), "cap_usd": float(cap_usd)},
        )


# ---------------------------------------------------------------------------
# Getter type aliases — narrow Callables so probes type cleanly.
# ---------------------------------------------------------------------------


class GetterFloat(Protocol):
    def __call__(self) -> float:
        ...


class GetterInt(Protocol):
    def __call__(self) -> int:
        ...


class GetterDelta(Protocol):
    def __call__(self) -> tuple[float, float]:
        """Return ``(breath_delta_over_window, window_seconds)``."""
        ...


class GetterCostState(Protocol):
    def __call__(self) -> tuple[float, float]:
        """Return ``(total_usd, hard_cap_usd)``."""
        ...


# ---------------------------------------------------------------------------
# LiveMonitor itself
# ---------------------------------------------------------------------------


@dataclass
class LiveMonitor:
    """Observe-only async daemon that fires registered probes on a
    cadence + emits :class:`Alert` frames via the injected sink.

    Construction
    ------------

    * ``probes`` — sequence of :class:`Probe` instances. Production
      wires one probe per indicator (heartbeat + energy_drain + 3x
      rpc_latency + ws_disconnects + gemini_cost = 7 probes).
    * ``sink`` — :class:`AlertSink` (the dashboard event bus).
    * ``config`` — :class:`LiveMonitorConfig` of thresholds + cadence.
    * ``classifier_override`` — for tests; production uses the
      module-level ``classify_*`` functions.

    Lifecycle
    ---------

    * :meth:`tick` — run one observation cycle. Pure logic, no sleep.
      The e2e test drives this directly so the scenario is deterministic.
    * :meth:`run_forever` — async loop that fires :meth:`tick` then
      sleeps ``config.sample_interval_s``. Cancellable via the standard
      ``asyncio.CancelledError`` propagation.

    The monitor NEVER opens a file for writing. The
    :func:`tests.agent.ops.test_live_monitor.test_live_monitor_is_observe_only`
    AST scan asserts this as a structural invariant.
    """

    probes: Sequence[Probe]
    sink: AlertSink
    config: LiveMonitorConfig = field(default_factory=LiveMonitorConfig)
    # Last-emitted severity per indicator — used to suppress repeated
    # OK→OK transitions (no point spamming the bus with 'still ok').
    _last_severity: dict[str, AlertSeverity] = field(default_factory=dict)

    async def tick(self) -> list[Alert]:
        """Run one observation cycle and return the alerts emitted.

        Process:

        1. ``asyncio.gather`` every probe's ``sample()`` (parallel).
        2. Classify each reading via the matching ``classify_*`` fn.
        3. Emit an Alert when the severity is not OK, OR when an OK
           reading resolves a prior non-OK (recovery transition — the
           dashboard wants to know the indicator healed).
        4. Probe exceptions surface as a WARNING with the exception's
           type in ``extra``. The monitor never crashes its own tick.

        Returns the list of :class:`Alert` frames pushed this tick so
        the caller (e2e test, dashboard observer) can introspect.
        """
        readings = await self._gather_readings()
        alerts: list[Alert] = []
        for indicator, reading_or_err in readings:
            if isinstance(reading_or_err, BaseException):
                # Containment: a buggy probe surfaces as a WARNING,
                # never crashes the tick. The exception type identifies
                # the failure in the dashboard tooltip.
                alert = Alert(
                    indicator=indicator,
                    severity=AlertSeverity.WARNING,
                    message=f"probe raised: {type(reading_or_err).__name__}",
                    ts=_iso_now(),
                    extra={"exception": type(reading_or_err).__name__},
                )
                await self._maybe_emit(alert)
                alerts.append(alert)
                continue

            reading = reading_or_err
            severity = self._classify(reading)
            alert = self._build_alert(reading, severity)
            emitted = await self._maybe_emit(alert)
            if emitted:
                alerts.append(alert)
        return alerts

    async def run_forever(self) -> None:
        """Blocking async loop: tick + sleep, forever.

        Cancellable via ``task.cancel()`` — the loop catches the
        ``CancelledError`` and exits cleanly. The sleep happens AFTER
        the tick so a freshly-started monitor emits its baseline
        readings immediately (a fail-fast on a CRITICAL state at boot
        rather than waiting one full interval).
        """
        try:
            while True:
                await self.tick()
                await asyncio.sleep(self.config.sample_interval_s)
        except asyncio.CancelledError:
            logger.info("live_monitor cancelled — exiting cleanly")
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _gather_readings(
        self,
    ) -> list[tuple[str, ProbeReading | BaseException]]:
        """Run every probe in parallel and pair with its indicator id.

        Uses ``asyncio.gather(..., return_exceptions=True)`` so a single
        probe raising does not poison the rest. The indicator id is
        zipped back in afterwards because gather's return order matches
        input order.
        """
        coros = [p.sample() for p in self.probes]
        results = await asyncio.gather(*coros, return_exceptions=True)
        out: list[tuple[str, ProbeReading | BaseException]] = []
        for probe, res in zip(self.probes, results, strict=True):
            out.append((probe.indicator, res))
        return out

    def _classify(self, reading: ProbeReading) -> AlertSeverity:
        """Dispatch to the matching ``classify_*`` function by indicator."""
        cfg = self.config
        if reading.indicator == INDICATOR_HEARTBEAT:
            return classify_heartbeat(age_s=reading.value, config=cfg)
        if reading.indicator == INDICATOR_ENERGY_DRAIN:
            return classify_energy_drain(
                drain_per_second=reading.value, config=cfg
            )
        if reading.indicator == INDICATOR_RPC_LATENCY:
            return classify_rpc_latency(latency_ms=reading.value, config=cfg)
        if reading.indicator == INDICATOR_WS_DISCONNECTS:
            return classify_ws_disconnects(
                disconnects_in_window=int(reading.value), config=cfg
            )
        if reading.indicator == INDICATOR_GEMINI_COST:
            return classify_gemini_cost(fraction_used=reading.value, config=cfg)
        # Unknown indicator — treat as WARNING so the dashboard
        # surfaces the misconfiguration rather than swallowing it.
        return AlertSeverity.WARNING

    def _build_alert(self, reading: ProbeReading, severity: AlertSeverity) -> Alert:
        """Render the structured alert frame."""
        message = self._render_message(reading, severity)
        threshold = self._threshold_for(reading.indicator)
        return Alert(
            indicator=reading.indicator,
            severity=severity,
            message=message,
            ts=_iso_now(),
            value=reading.value,
            threshold=threshold,
            extra=dict(reading.extra),
        )

    def _render_message(
        self,
        reading: ProbeReading,
        severity: AlertSeverity,
    ) -> str:
        """One-line human-readable description for the dashboard chip."""
        if reading.indicator == INDICATOR_HEARTBEAT:
            return (
                f"agent heartbeat stale: {reading.value:.1f}s since last tick "
                f"(severity={severity.value})"
            )
        if reading.indicator == INDICATOR_ENERGY_DRAIN:
            return (
                f"energy drain spike: {reading.value:.2f} BREATH/s "
                f"(severity={severity.value})"
            )
        if reading.indicator == INDICATOR_RPC_LATENCY:
            chain = reading.extra.get("chain", "?")
            return (
                f"RPC latency on {chain}: {reading.value:.0f}ms "
                f"(severity={severity.value})"
            )
        if reading.indicator == INDICATOR_WS_DISCONNECTS:
            window = reading.extra.get("window_seconds", "?")
            return (
                f"Polymarket WS reconnects: {int(reading.value)} in "
                f"{window}s (severity={severity.value})"
            )
        if reading.indicator == INDICATOR_GEMINI_COST:
            total = reading.extra.get("total_usd", 0.0)
            cap = reading.extra.get("cap_usd", 0.0)
            return (
                f"Gemini cost ${total:.2f} / ${cap:.2f} "
                f"({100 * reading.value:.0f}%; severity={severity.value})"
            )
        return f"{reading.indicator}: value={reading.value} severity={severity.value}"

    def _threshold_for(self, indicator: str) -> float | int | None:
        """Surface the threshold the classifier compared against."""
        c = self.config
        return {
            INDICATOR_HEARTBEAT: c.heartbeat_max_age_s,
            INDICATOR_ENERGY_DRAIN: c.energy_drain_max_per_second,
            INDICATOR_RPC_LATENCY: c.rpc_latency_max_ms,
            INDICATOR_WS_DISCONNECTS: c.ws_disconnects_max_per_window,
            INDICATOR_GEMINI_COST: c.gemini_cost_warning_fraction,
        }.get(indicator)

    async def _maybe_emit(self, alert: Alert) -> bool:
        """Push the alert to the sink iff it represents a state change.

        Suppression rules:

        * OK→OK transitions: NEVER emitted (no point spamming with
          'still healthy'). Recovery (non-OK→OK) IS emitted so the
          dashboard can clear its sticky chip.
        * Same-non-OK transitions (WARNING→WARNING, CRITICAL→CRITICAL):
          ALWAYS emitted so the dashboard keeps the chip live (a
          dropped frame on a long-running incident would let the chip
          stale out + appear "resolved").
        * Severity escalation/de-escalation: always emitted.
        """
        previous = self._last_severity.get(alert.indicator, AlertSeverity.OK)
        self._last_severity[alert.indicator] = alert.severity
        if previous == AlertSeverity.OK and alert.severity == AlertSeverity.OK:
            return False
        await self.sink.emit(alert)
        return True


def _iso_now() -> str:
    """Wall-clock ISO-8601 stamp (UTC). Pulled out so tests can patch."""
    return datetime.now(UTC).isoformat()


__all__ = [
    "DEFAULT_ENERGY_DRAIN_MAX_PER_SECOND",
    "DEFAULT_GEMINI_COST_CRITICAL_FRACTION",
    "DEFAULT_GEMINI_COST_WARNING_FRACTION",
    "DEFAULT_HEARTBEAT_MAX_AGE_S",
    "DEFAULT_RPC_LATENCY_MAX_MS",
    "DEFAULT_SAMPLE_INTERVAL_S",
    "DEFAULT_WS_DISCONNECTS_MAX_PER_WINDOW",
    "INDICATOR_ENERGY_DRAIN",
    "INDICATOR_GEMINI_COST",
    "INDICATOR_HEARTBEAT",
    "INDICATOR_RPC_LATENCY",
    "INDICATOR_WS_DISCONNECTS",
    "Alert",
    "AlertSeverity",
    "AlertSink",
    "EnergyDrainProbe",
    "GeminiCostProbe",
    "GetterCostState",
    "GetterDelta",
    "GetterFloat",
    "GetterInt",
    "HeartbeatProbe",
    "LiveMonitor",
    "LiveMonitorConfig",
    "Probe",
    "ProbeReading",
    "RecordingAlertSink",
    "RpcLatencyProbe",
    "WsDisconnectProbe",
    "classify_energy_drain",
    "classify_gemini_cost",
    "classify_heartbeat",
    "classify_rpc_latency",
    "classify_ws_disconnects",
]
