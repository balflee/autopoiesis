"""Agent runtime — boot orchestration, Phase 2 launch convergence.

Sprint_4 T-B-008 ships the :class:`Phase2LaunchOrchestrator` here as
the Phase 1 → Phase 2 hard-deadline convergence wiring (TP §8 D11 + §9).
The orchestrator threads together:

* on-chain phase read (via injectable :class:`_PhaseManagerReader`)
* one-shot ``PhaseActivationEmitter`` write to MemoryBank
* dashboard WS frame fanout via :class:`WsEventEmitter`
* a single decision tick using :class:`DecisionEngine` + the engine
  signal fanout (mocked via injectable :class:`_EngineSignalSource`
  in tests / dry-run mode)

The class is designed so a single :meth:`Phase2LaunchOrchestrator.boot`
call can drive both:

* the unit smoke test (``tests/agent/integration/test_phase2_launch_smoke``)
  — all dependencies are injected via Protocols + the boot returns a
  structured result with the frames the test can assert on.
* the live Phase 2 launch operator runbook
  (``agent/runbooks/phase2_launch.md``) — the operator wires real
  chain / Polymarket / Gemini adapters via the same constructor.
"""

from agent.runtime.phase2_launch import (
    Phase2BootResult,
    Phase2LaunchOrchestrator,
    Phase2LaunchPlan,
)
from agent.runtime.sprint7_dryrun import (
    DryRunExecutor,
    DryRunResult,
    TennisMarket,
    discover_tennis_markets,
    run_dryrun,
)

__all__ = [
    "DryRunExecutor",
    "DryRunResult",
    "Phase2BootResult",
    "Phase2LaunchOrchestrator",
    "Phase2LaunchPlan",
    "TennisMarket",
    "discover_tennis_markets",
    "run_dryrun",
]
