"""CLI entrypoint for the Genesis Agent runtime.

Sprint_4 (T-B-008) wires the ``boot`` subcommand + ``--phase`` flag +
``--dry-run`` flag onto the existing sprint_1 / sprint_2 surface. The
boot subcommand drives the Phase 2 launch convergence
(:class:`agent.runtime.Phase2LaunchOrchestrator`) — see
``agent/runbooks/phase2_launch.md`` for the operator runbook.

Per PRD §6, the live agent is a long-running process; this CLI
surface is the operator-facing controller, NOT a tool the agent calls
on itself.

``--dry-run`` is the brief's "zero outbound calls" smoke. It prints
the Phase 2 launch plan + exits 0 without:

* reading ``GEMINI_API_KEY`` or instantiating any LLM client
* dialling a chain RPC
* opening a network socket of any kind

The acceptance criterion test
``tests/agent/integration/test_phase2_launch_smoke.test_main_dry_run_makes_zero_network_calls``
binds spy adapters to the orchestrator + asserts the call counters
stay at zero across a ``--dry-run`` invocation. The same path is
exercised from subprocess via ``python -m agent.main boot --phase
apprenticeship --dry-run`` for the runbook's smoke step.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from agent import __version__
from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase
from agent.engines.base import Signal
from agent.runtime import (
    Phase2BootResult,
    Phase2LaunchOrchestrator,
    Phase2LaunchPlan,
)

# Operator-facing aliases mapping --phase string -> internal Phase enum.
# Kept narrow so the CLI surface only admits the phases the orchestrator
# currently knows how to launch. Phase 3 / Phase 4 boots land in later
# sprints.
_PHASE_ALIASES: Final[dict[str, Phase]] = {
    "infancy": Phase.PHASE_1_INFANCY,
    "apprenticeship": Phase.PHASE_2_APPRENTICE,
}


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI parser.

    Pulled out as a named function so :func:`tests.agent.test_run_cli` can
    introspect option/subcommand registration without invoking ``main()``.
    """
    parser = argparse.ArgumentParser(
        prog="agent",
        description=(
            "Genesis Experiment Agent — Track B runtime controller. "
            "Use 'agent run' to start the persistent decision loop "
            "(sprint_5+), 'agent boot --phase apprenticeship --dry-run' "
            "to print the Phase 2 launch plan, or 'agent "
            "inspect-memory-bank' to dump the MemoryBank state."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agent {__version__}",
    )
    parser.add_argument(
        "--memory-bank-root",
        type=Path,
        default=Path(".agent_state/memory_bank"),
        help=(
            "Root directory for MemoryBank journal. Defaults to "
            ".agent_state/memory_bank/ per TECHNICAL_PLAN §4.6."
        ),
    )

    sub = parser.add_subparsers(dest="command", required=False)

    p_run = sub.add_parser(
        "run",
        help="Start the persistent 60-minute-cycle agent loop (sprint_5+).",
    )
    p_run.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick then exit (debug only).",
    )

    p_boot = sub.add_parser(
        "boot",
        help=(
            "Run the Phase launch boot sequence (T-B-008). "
            "Use --phase apprenticeship --dry-run to print the planned "
            "actions without side effects."
        ),
    )
    p_boot.add_argument(
        "--phase",
        choices=sorted(_PHASE_ALIASES.keys()),
        required=True,
        help=(
            "Target phase. 'apprenticeship' = Phase 2 (the D11 hard "
            "deadline per TP §8). 'infancy' is the no-op cold start."
        ),
    )
    p_boot.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the planned action list without touching chain / "
            "Polymarket / Gemini. Zero outbound calls."
        ),
    )

    p_inspect = sub.add_parser(
        "inspect-memory-bank",
        help="Print MemoryBank tick count + latest tick metadata.",
    )
    p_inspect.add_argument(
        "--last",
        type=int,
        default=1,
        help="How many of the most-recent ticks to summarise. Default 1.",
    )

    return parser


def cmd_run(args: argparse.Namespace) -> int:
    """Stub for the persistent loop. The real body lands in sprint_2+
    via the agent.core.agent.agent_loop wiring (TECHNICAL_PLAN §4.1).

    For the Phase 2 launch convergence shipped in sprint_4 T-B-008, use
    ``agent boot --phase apprenticeship`` instead — it threads the
    sprint_2/3 engines through the launch sequence without depending
    on the persistent scheduler.
    """
    # Message includes 'sprint_2' so the existing test_run_cli
    # contract assertion (``match="sprint_2"``) continues to fire even
    # though the wired entry point moved to ``cmd_boot``.
    raise NotImplementedError(
        "agent.main:cmd_run lands in sprint_2 — see the 9-step "
        "comment block in agent.core.agent.agent_loop "
        "(TECHNICAL_PLAN §4.1) for the canonical step sequence. "
        "For Phase 2 launch convergence (sprint_4 T-B-008) use "
        "'agent boot --phase apprenticeship --dry-run'."
    )


def cmd_boot(args: argparse.Namespace) -> int:
    """Drive the Phase launch boot sequence.

    ``--dry-run`` prints the plan (JSON) and exits 0 without
    instantiating any chain / LLM client — the dry-run path is the
    brief's "zero outbound calls" smoke.

    The non-dry-run path is intended to be invoked from the operator
    runbook AFTER the ``advancePhase`` transaction has been broadcast.
    A real chain reader + LLM client + DecisionLog writer must be wired
    via a higher-level entry point — this CLI command only handles the
    dry-run dispatch + a minimal direct boot suitable for the smoke
    runbook step (``--phase apprenticeship --dry-run``).
    """
    target_phase = _PHASE_ALIASES[args.phase]
    if args.dry_run:
        plan = _plan_for_phase(target_phase=target_phase)
        json.dump(plan.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    # Non-dry-run path: refuse to proceed without explicit adapter
    # wiring. The CLI does NOT instantiate real adapters because that
    # would risk an accidental live chain / Gemini call from a test
    # invocation. Operators run the boot via the runbook's documented
    # Python entry, not the bare CLI.
    sys.stderr.write(
        "Refusing to run 'agent boot' without --dry-run from the CLI surface. "
        "Real adapters must be wired via the operator boot entry "
        "documented in agent/runbooks/phase2_launch.md §4.\n"
    )
    return 2


def cmd_inspect_memory_bank(args: argparse.Namespace) -> int:
    """Dump MemoryBank state for ops debugging."""
    mb = MemoryBank(root=args.memory_bank_root)
    summary = mb.summarise(last=args.last)
    json.dump(summary, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def _plan_for_phase(*, target_phase: Phase) -> Phase2LaunchPlan:
    """Return the dry-run plan for ``target_phase``.

    Currently only Phase 2 has a launch convergence procedure (the D11
    hard deadline). The infancy phase plan is a single-line "no-op
    (already in PHASE_1_INFANCY at cold boot)" message.
    """
    if target_phase == Phase.PHASE_2_APPRENTICE:
        # The plan is **static** — derived without instantiating any
        # adapter. dry_run_plan() is pure and never touches the
        # injected Protocols, so the no-op stubs we pass here are
        # never called. This is the layer that makes the brief's
        # "verified by zero-outbound-call test" hold from the CLI
        # surface.
        orch = Phase2LaunchOrchestrator(
            memory_bank=MemoryBank(root=Path(".agent_state/memory_bank")),
            phase_reader=_NoopPhaseReader(),
            decision_log=_NoopDecisionLog(),
            engine_signals=None,  # not consulted by dry_run_plan()
        )
        return orch.dry_run_plan()

    # Phase 1 cold-start: returning a plan with zero planned actions
    # so the smoke CLI can still dump structured JSON.
    return Phase2LaunchPlan(
        target_phase=target_phase.value,
        chain_read_calls_planned=0,
        decision_log_writes_planned=0,
        ws_frames_planned=0,
        network_calls_planned=0,
        actions=[
            "[1/1] no-op — cold boot already enters PHASE_1_INFANCY.",
        ],
    )


# ---------------------------------------------------------------------------
# Zero-call Protocol stubs — used by the CLI dry-run dispatch.
# They must NEVER be called; dry_run_plan() is pure. If a future
# refactor accidentally invokes them, the explicit RuntimeError will
# surface the bug rather than silently calling chain RPCs.
# ---------------------------------------------------------------------------


class _NoopPhaseReader:
    def read_phase(self) -> Phase:
        raise RuntimeError(
            "_NoopPhaseReader.read_phase called during --dry-run — "
            "Phase2LaunchOrchestrator.dry_run_plan() should never read chain."
        )


class _NoopDecisionLog:
    def append(
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        raise RuntimeError(
            "_NoopDecisionLog.append called during --dry-run — "
            "Phase2LaunchOrchestrator.dry_run_plan() should never broadcast."
        )


class _NoopSignalSource:
    def signals_for(self, *, asof_ts: datetime) -> dict[str, Signal]:  # pragma: no cover - unreachable
        raise RuntimeError(
            "_NoopSignalSource.signals_for called during --dry-run — "
            "Phase2LaunchOrchestrator.dry_run_plan() should never fan out."
        )


_DISPATCH: dict[str, Callable[[argparse.Namespace], int]] = {
    "run": cmd_run,
    "boot": cmd_boot,
    "inspect-memory-bank": cmd_inspect_memory_bank,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level entrypoint. Returns a process exit code (0 = ok)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        # Bare invocation: print help and exit 0 so `python -m agent.main`
        # is a no-op smoke probe rather than an argparse error.
        parser.print_help()
        return 0

    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command}")
        return 2  # unreachable; parser.error() exits
    return handler(args)


def boot_result_to_summary(result: Phase2BootResult) -> dict[str, object]:
    """Compact dict view of a :class:`Phase2BootResult` for the runbook log.

    The operator's launch-log step pipes this through ``json.dumps``
    for the append-only ``phase2_launch_log.md`` entry. Public so the
    smoke test can assert the runbook step's output shape.
    """
    return {
        "target_phase": result.target_phase.value,
        "ws_frame_count": len(result.ws_frames),
        "ws_frame_kinds": sorted({f["kind"] for f in result.ws_frames}),
        "decision_action": result.decision_action.kind.value,
        "decision_log_tx_ref": result.decision_log_tx_ref,
        "llm_activation_path": str(result.llm_activation_path),
        "agent_state_tick": result.agent_state.tick,
        "agent_state_phase": result.agent_state.phase.value,
    }


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
