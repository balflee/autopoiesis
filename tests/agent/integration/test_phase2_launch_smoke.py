"""Phase 2 launch end-to-end smoke (T-B-008 acceptance criteria).

Hermetic — no chain, no Polymarket, no Gemini. Fakes everywhere. The
brief's hard rule "Zero outbound calls under pytest" is enforced by
spy adapters that count invocations + an assertion that the
``--dry-run`` path keeps every counter at zero.

The Gemini side is guarded structurally rather than via a fake: the
boot path uses the deterministic
:func:`agent.runtime.phase2_launch._template_reflection` and the AST
scan in ``tests/agent/llm/test_no_forbidden_imports`` enforces that no
production module imports ``anthropic`` / ``openai``. The Phase 2
launch orchestrator never receives an LLM client to inject — that
absence is what proves no Gemini call can leak.

Brief minimum: five tests. This suite ships eight (the extra three
cover the chain-phase precondition, the constructor surface, and the
subprocess CLI invocation):

1. test_dry_run_plan_makes_zero_outbound_calls
2. test_phase2_boot_emits_llm_activated_exactly_once
3. test_phase2_boot_produces_required_frame_kinds
4. test_phase2_boot_records_first_decision_on_decision_log
5. test_phase2_demo_tape_fixture_meets_demo_invariants
6. test_main_dry_run_subprocess_exits_zero_with_zero_outbound_calls
7. test_boot_refuses_when_chain_phase_is_still_phase1
8. test_orchestrator_default_signal_source_is_required_for_boot
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase
from agent.engines.base import Signal
from agent.engines.decision import (
    CROWD_VOLUME,
    MARKET_MOMENTUM,
    SENTIMENT_LLM,
    SMART_MONEY,
    TENNIS_TECHNICAL,
)
from agent.runtime import Phase2LaunchOrchestrator

FIXED_TS = datetime(2026, 5, 23, 18, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fake adapters — Protocol-conformant, counter-instrumented.
# ---------------------------------------------------------------------------


@dataclass
class FakePhaseManagerReader:
    """Returns a scripted Phase on each ``read_phase()`` call.

    Counts invocations so the dry-run zero-call assertion can verify
    the orchestrator does NOT consult the chain in ``--dry-run`` mode.
    """

    phase: Phase = Phase.PHASE_2_APPRENTICE
    calls: int = 0

    def read_phase(self) -> Phase:
        self.calls += 1
        return self.phase


@dataclass
class FakeDecisionLog:
    """Captures every ``append`` invocation without broadcasting."""

    appended: list[dict[str, Any]] = field(default_factory=list)

    def append(
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        self.appended.append(
            {
                "market_id": market_id,
                "action": action.value,
                "size_usd": size_usd,
                "side": side,
                "edge_pct": edge_pct,
            }
        )
        # Deterministic fake tx hash so the smoke log is byte-stable.
        return f"0xfake_{len(self.appended):04x}"


@dataclass
class FakeSignalSource:
    """Returns a deterministic dict of 5 :class:`Signal` objects.

    Default scores route to NO_BET (low mean confidence) so the smoke
    test exercises the "first NO_BET decision logged" success criterion
    from the brief without needing to wire bankroll / liquidity math.

    The ``override_signals`` knob lets the BET-path test inject signals
    that produce a BET so we cover both branches of the WS decision
    frame.
    """

    asof_calls: list[datetime] = field(default_factory=list)
    override_signals: dict[str, Signal] | None = None

    def signals_for(self, *, asof_ts: datetime) -> dict[str, Signal]:
        self.asof_calls.append(asof_ts)
        if self.override_signals is not None:
            return self.override_signals
        # Default: low-confidence neutral signals → NO_BET.
        return _build_signals(score=0.0, confidence=0.0, asof_ts=asof_ts)


def _build_signals(
    *, score: float, confidence: float, asof_ts: datetime
) -> dict[str, Signal]:
    """Return all 5 engine signals with the same score / confidence.

    Engine name keys come from :mod:`agent.engines.decision` constants
    so a future rename of an engine surfaces at import time rather than
    silently dropping a signal.
    """
    asof_iso = asof_ts.isoformat()
    return {
        n: Signal(
            score=score,
            confidence=confidence,
            available_at=asof_iso,
            rationale=f"{n} fake signal",
            raw_features={"smoke": 1.0},
        )
        for n in (TENNIS_TECHNICAL, MARKET_MOMENTUM, SMART_MONEY, SENTIMENT_LLM, CROWD_VOLUME)
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orchestrator(
    *,
    tmp_path: Path,
    phase_reader: FakePhaseManagerReader | None = None,
    decision_log: FakeDecisionLog | None = None,
    signal_source: FakeSignalSource | None = None,
) -> tuple[
    Phase2LaunchOrchestrator,
    MemoryBank,
    FakePhaseManagerReader,
    FakeDecisionLog,
    FakeSignalSource,
]:
    bank = MemoryBank(root=tmp_path / "mb")
    bank.ensure_layout()
    reader = phase_reader if phase_reader is not None else FakePhaseManagerReader()
    log = decision_log if decision_log is not None else FakeDecisionLog()
    src = signal_source if signal_source is not None else FakeSignalSource()
    orch = Phase2LaunchOrchestrator(
        memory_bank=bank,
        phase_reader=reader,
        decision_log=log,
        engine_signals=src,
    )
    return orch, bank, reader, log, src


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_plan_makes_zero_outbound_calls(tmp_path: Path) -> None:
    """``dry_run_plan()`` MUST NOT call any injected adapter.

    Counts on every spy stay at zero. The plan still returns the
    structured Phase 2 action list — the runbook's smoke step pipes
    this through JSON.
    """
    orch, _bank, reader, log, src = _make_orchestrator(tmp_path=tmp_path)

    plan = orch.dry_run_plan()

    assert reader.calls == 0, "dry_run_plan must NOT read chain"
    assert log.appended == [], "dry_run_plan must NOT broadcast decisions"
    assert src.asof_calls == [], "dry_run_plan must NOT fanout engines"
    assert plan.network_calls_planned == 0
    assert plan.target_phase == "PHASE_2_APPRENTICE"
    assert plan.chain_read_calls_planned == 1
    assert plan.decision_log_writes_planned == 1
    assert plan.ws_frames_planned >= 5
    assert len(plan.actions) == 8  # the documented 8-step boot sequence
    # The plan's WS frames count must align with the boot's emit count.
    # The boot emits 7 frames: phase_transition, llm_activated,
    # vitals_open, decision, reflection, weights_updated, vitals_close.
    assert plan.ws_frames_planned == 7


def test_phase2_boot_emits_llm_activated_exactly_once(tmp_path: Path) -> None:
    """One-shot ``llm_activated`` invariant + persisted on-disk file.

    Brief: "data/fixtures/phase2_demo_tape.json contains ... single
    `llm_activated` marker". The producer side of that invariant is
    enforced here.
    """
    orch, bank, _reader, _log, _src = _make_orchestrator(tmp_path=tmp_path)

    result = orch.boot(asof_ts=FIXED_TS)

    llm_frames = [f for f in result.ws_frames if f["kind"] == "llm_activated"]
    assert len(llm_frames) == 1, f"expected 1 llm_activated frame, got {len(llm_frames)}"
    assert llm_frames[0]["note"], "llm_activated frame must carry a note for the overlay copy"

    # MemoryBank-persisted activation file exists and round-trips.
    activation_path = bank.observations_dir / "llm_activated.json"
    assert activation_path.is_file()
    persisted = json.loads(activation_path.read_text(encoding="utf-8"))
    assert persisted["phase"] == 2
    assert persisted["model"] == "gemini-3.1-flash-lite"

    # Idempotency: a second boot does NOT add a second activation frame.
    orch2, bank2, _r2, _l2, _s2 = _make_orchestrator(tmp_path=tmp_path / "second")
    bank2.observations_dir.mkdir(parents=True, exist_ok=True)
    (bank2.observations_dir / "llm_activated.json").write_text(
        json.dumps(persisted), encoding="utf-8"
    )
    result2 = orch2.boot(asof_ts=FIXED_TS)
    llm_frames2 = [f for f in result2.ws_frames if f["kind"] == "llm_activated"]
    assert len(llm_frames2) == 1


def test_phase2_boot_produces_required_frame_kinds(tmp_path: Path) -> None:
    """Brief acceptance criterion: ≥3 distinct frame kinds.

    The boot must produce at minimum: vitals + decision + reflection +
    llm_activated + phase_transition + weights_updated. That's six —
    we assert all six are present + monotonic seq.
    """
    orch, _bank, _reader, _log, _src = _make_orchestrator(tmp_path=tmp_path)

    result = orch.boot(asof_ts=FIXED_TS)

    kinds = {f["kind"] for f in result.ws_frames}
    required = {
        "phase_transition",
        "llm_activated",
        "vitals",
        "decision",
        "reflection",
        "weights_updated",
    }
    assert required.issubset(kinds), (
        f"missing required kinds: {required - kinds}"
    )

    # Monotonic seq — Track D dedups by seq, so a non-monotonic
    # producer would silently drop frames on reconnect.
    seqs = [int(f["seq"]) for f in result.ws_frames]
    assert seqs == sorted(seqs), f"seq not monotonic: {seqs}"
    assert len(set(seqs)) == len(seqs), "duplicate seq numbers"

    # ts is ISO-8601 with timezone (the dashboard parses with Date()).
    for frame in result.ws_frames:
        ts = frame["ts"]
        # Parse it via datetime to confirm ISO-8601-ness; raise on parse.
        datetime.fromisoformat(ts)


def test_phase2_boot_records_first_decision_on_decision_log(
    tmp_path: Path,
) -> None:
    """Brief success criterion: 'first NO_BET decision logged on fake
    DecisionLog'. The default signal source returns low-confidence
    neutral signals which route to NO_BET via
    DecisionEngine's confidence floor."""
    orch, _bank, _reader, log, src = _make_orchestrator(tmp_path=tmp_path)

    result = orch.boot(asof_ts=FIXED_TS)

    assert len(log.appended) == 1, "exactly one DecisionLog append per boot"
    appended = log.appended[0]
    assert appended["action"] == "NO_BET"  # default signals → NO_BET
    assert appended["market_id"] == "polymarket:nba:lakers_at_celtics:2026-04-12"
    assert result.decision_log_tx_ref.startswith("0xfake_")
    assert result.decision_action.kind == ActionKind.NO_BET

    # Engines were consulted exactly once for the boot tick.
    assert len(src.asof_calls) == 1


def test_phase2_demo_tape_fixture_meets_demo_invariants() -> None:
    """The shipped tape under data/fixtures/ MUST meet brief invariants.

    * ≥30 WS messages
    * ≥3 distinct ``kind`` values (≥ vitals, decision, reflection)
    * exactly one ``llm_activated`` frame
    * every frame validates against the v0.2.0 wire schema's required
      base fields (kind, ts, seq)
    """
    repo_root = _repo_root()
    tape_path = repo_root / "data" / "fixtures" / "phase2_demo_tape.json"
    assert tape_path.is_file(), (
        f"Phase 2 demo tape missing at {tape_path} — Demo §9 1:30-2:30 "
        "depends on this fixture"
    )
    tape: list[dict[str, Any]] = json.loads(tape_path.read_text(encoding="utf-8"))

    assert isinstance(tape, list)
    assert len(tape) >= 30, f"need ≥30 frames for Demo tape, got {len(tape)}"

    kinds = {f["kind"] for f in tape}
    assert {"vitals", "decision", "reflection"}.issubset(kinds), (
        f"Demo tape must include vitals + decision + reflection; got {kinds}"
    )
    assert len(kinds) >= 3, f"need ≥3 distinct kinds, got {len(kinds)}: {kinds}"

    activations = [f for f in tape if f["kind"] == "llm_activated"]
    assert len(activations) == 1, (
        f"Demo tape must carry exactly 1 llm_activated frame, got {len(activations)}"
    )

    # Required base fields on every frame.
    for i, frame in enumerate(tape):
        assert "kind" in frame, f"frame[{i}] missing 'kind'"
        assert "ts" in frame, f"frame[{i}] missing 'ts'"
        assert "seq" in frame, f"frame[{i}] missing 'seq'"

    # seqs are unique (the dashboard dedups by seq).
    seqs = [int(f["seq"]) for f in tape]
    assert len(set(seqs)) == len(seqs), "duplicate seq in demo tape"


def test_main_dry_run_subprocess_exits_zero_with_zero_outbound_calls() -> None:
    """``python -m agent.main boot --phase apprenticeship --dry-run`` exit 0.

    Runs as a subprocess so we exercise the real argv path the operator
    runbook documents. We assert:

    * exit code 0
    * stdout is parseable JSON
    * stdout matches the Phase2LaunchPlan shape
    * stderr is empty (a network error would surface here)
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.main",
            "boot",
            "--phase",
            "apprenticeship",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, (
        f"--dry-run should exit 0, got {proc.returncode}; stderr=\n{proc.stderr}"
    )
    assert proc.stderr == "", f"--dry-run should not write to stderr: {proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["target_phase"] == "PHASE_2_APPRENTICE"
    assert payload["network_calls_planned"] == 0
    assert len(payload["actions"]) >= 5


def test_boot_refuses_when_chain_phase_is_still_phase1(tmp_path: Path) -> None:
    """Operator-runbook hard rule: ``advancePhase`` MUST land first.

    If the chain still reads PHASE_1_INFANCY when boot fires, the
    orchestrator raises RuntimeError pointing the operator back to the
    runbook. The decision log is NOT touched.
    """
    reader = FakePhaseManagerReader(phase=Phase.PHASE_1_INFANCY)
    orch, _bank, _r, log, _src = _make_orchestrator(
        tmp_path=tmp_path,
        phase_reader=reader,
    )

    with pytest.raises(RuntimeError, match="advancePhase"):
        orch.boot(asof_ts=FIXED_TS)

    assert log.appended == [], (
        "DecisionLog must NOT be touched when phase precondition fails"
    )


def test_orchestrator_default_signal_source_is_required_for_boot(
    tmp_path: Path,
) -> None:
    """Constructor permits engine_signals=None but boot() refuses.

    The CLI dry-run dispatch builds the orchestrator with
    ``engine_signals=None`` because dry_run_plan() doesn't need them.
    boot() rejects the same construction so a buggy caller cannot
    silently NO_BET via a missing fanout.
    """
    bank = MemoryBank(root=tmp_path / "mb")
    orch = Phase2LaunchOrchestrator(
        memory_bank=bank,
        phase_reader=FakePhaseManagerReader(),
        decision_log=FakeDecisionLog(),
        engine_signals=None,
    )
    # dry-run works without a signal source
    assert orch.dry_run_plan().target_phase == "PHASE_2_APPRENTICE"
    # boot() refuses
    with pytest.raises(RuntimeError, match="engine_signals"):
        orch.boot(asof_ts=FIXED_TS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Locate repo root regardless of where pytest was invoked."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "agent").is_dir():
            return parent
    raise AssertionError("could not locate repo root from test file")
