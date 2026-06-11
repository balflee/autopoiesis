"""Production-loop factory wiring tests (T-B-041 — sprint_13 Day 0).

Five hardened assertions on the seam that replaces sprint_9's
``_placeholder_loop_factory`` with the real
:class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`:

1. :func:`agent.server.main._build_production_loop_factory` returns a
   :class:`LoopFactoryProto`-conformant 0-arg callable that constructs
   a :class:`SandboxPhase2Loop` per call. Two invocations return
   distinct instances (per the LoopFactoryProto's per-call cleanliness
   contract).

2. The factory closes over its ``state_dir`` argument — the constructed
   loop's :attr:`state_dir` attribute equals the value passed in.

3. :func:`agent.server.bootstrap.resolve_prod_loop_config` against an
   empty env returns the brief-locked defaults (60s tick interval,
   1.0x compression, sandbox kind).

4. Env overrides flow through: setting the three env knobs to
   non-default values changes the resolved config + the loop's
   ``decision_cadence`` accordingly.

5. ``_build_default_app`` no longer wires :class:`_PlaceholderLoop` —
   the :class:`AgentRunner`'s ``loop_factory`` constructs a
   :class:`SandboxPhase2Loop`, NOT a placeholder.

All five tests run hermetically under ``GENESIS_SERVER_AUTOBUILD=0``
(set by the conftest fixture) so the module-level ``app`` global is
``None`` and the tests build their own :class:`FastAPI` via
:func:`_build_default_app` against a ``tmp_path`` volume.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent.data._realtime_buffer import UtcClock
from agent.runtime.sandbox_phase2_loop import SandboxPhase2Loop
from agent.server import main as server_main
from agent.server.bootstrap import (
    DEFAULT_PROD_LOOP_CHAIN_ADAPTER_KIND,
    DEFAULT_PROD_LOOP_TICK_INTERVAL_SECONDS,
    DEFAULT_PROD_LOOP_TIME_COMPRESSION,
    PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR,
    PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN,
    PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX,
    PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR,
    PROD_LOOP_TIME_COMPRESSION_ENV_VAR,
    SANDBOX_STATE_DIR_ENV_VAR,
    ResolvedProdLoopConfig,
    resolve_prod_loop_config,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def volume_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every state-path env var at a per-test ``tmp_path`` subtree.

    Keeps :func:`_build_default_app` hermetic — without this fixture
    the default ``/data/...`` paths would trip
    :func:`validate_state_paths` on a developer box where ``/data``
    does not exist."""
    sandbox = tmp_path / "sandbox"
    backtest_runs = tmp_path / "backtest" / "runs"
    backtest_cache = tmp_path / "backtest" / "cache"
    monkeypatch.setenv(SANDBOX_STATE_DIR_ENV_VAR, str(sandbox))
    monkeypatch.setenv("BACKTEST_OUTPUT_ROOT", str(backtest_runs))
    monkeypatch.setenv("BACKTEST_CACHE_DIR", str(backtest_cache))
    yield tmp_path


@pytest.fixture
def clear_prod_loop_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all three PROD_LOOP_* env knobs so the helper sees defaults."""
    monkeypatch.delenv(PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR, raising=False)
    monkeypatch.delenv(PROD_LOOP_TIME_COMPRESSION_ENV_VAR, raising=False)
    monkeypatch.delenv(PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR, raising=False)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_factory_returns_sandbox_phase2_loop(tmp_path: Path) -> None:
    """T1 — :func:`_build_production_loop_factory` returns a 0-arg callable
    that constructs a fresh :class:`SandboxPhase2Loop` per call.

    Brief contract: ``LoopFactoryProto.__call__()`` returns a NEW
    instance every time (so in-memory state like the tick counter does
    NOT bleed across stop/start cycles). The test asserts (a) the
    return type, (b) two consecutive calls produce DISTINCT loop
    instances."""
    state_dir = tmp_path / "sandbox"
    chain = server_main._build_chain_adapter(
        kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX
    )
    factory = server_main._build_production_loop_factory(
        state_dir=state_dir,
        chain_adapter=chain,
        tick_input_source=server_main._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=60.0,
    )

    first = factory()
    second = factory()

    assert isinstance(first, SandboxPhase2Loop)
    assert isinstance(second, SandboxPhase2Loop)
    assert first is not second, (
        "LoopFactoryProto contract: each call must return a fresh handle "
        "so in-memory state (tick counter, breath cache) does NOT leak "
        "across stop/start cycles"
    )


def test_factory_closes_over_state_dir(tmp_path: Path) -> None:
    """T2 — the factory's ``state_dir`` argument flows to every loop instance.

    The loop's reconstruction reads JSONL streams + snapshot from THIS
    path on every ``run()`` start, so two factory invocations against
    the same closed-over path must produce loops that point at the
    same on-disk corpus.
    """
    state_dir = tmp_path / "sandbox" / "deep" / "nest"
    chain = server_main._build_chain_adapter(
        kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX
    )
    factory = server_main._build_production_loop_factory(
        state_dir=state_dir,
        chain_adapter=chain,
        tick_input_source=server_main._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=60.0,
    )

    loop = factory()
    assert loop.state_dir == state_dir
    # mkdir(parents=True, exist_ok=True) happens inside the loop ctor.
    assert state_dir.exists() and state_dir.is_dir()

    # Second factory call also points at the same path — closure capture.
    second = factory()
    assert second.state_dir == state_dir


def test_resolve_prod_loop_config_defaults(
    clear_prod_loop_env: None,
) -> None:
    """T3 — empty-env defaults match the brief: 60s, 1.0x, sandbox kind."""
    config = resolve_prod_loop_config(env={})

    assert isinstance(config, ResolvedProdLoopConfig)
    assert config.tick_interval_seconds == DEFAULT_PROD_LOOP_TICK_INTERVAL_SECONDS
    assert config.tick_interval_seconds == 60.0
    assert config.time_compression == DEFAULT_PROD_LOOP_TIME_COMPRESSION
    assert config.time_compression == 1.0
    assert config.chain_adapter_kind == DEFAULT_PROD_LOOP_CHAIN_ADAPTER_KIND
    assert config.chain_adapter_kind == PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX


def test_env_overrides_flow_to_decision_cadence(
    tmp_path: Path,
) -> None:
    """T4 — overriding the env knobs changes the resolved config AND the
    constructed loop's :attr:`_decision_cadence`.

    Setting ``PROD_LOOP_TICK_INTERVAL_SECONDS=120`` +
    ``PROD_LOOP_TIME_COMPRESSION=4.0`` should produce a cadence of
    120 / 4 = 30 seconds. ``PROD_LOOP_CHAIN_ADAPTER_KIND='sandbox'``
    stays the default; the rh_chain branch is exercised separately so
    the divisor test does not race the NotImplementedError gate.
    """
    config = resolve_prod_loop_config(
        env={
            PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR: "120",
            PROD_LOOP_TIME_COMPRESSION_ENV_VAR: "4.0",
            PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR: "sandbox",
        }
    )
    assert config.tick_interval_seconds == 120.0
    assert config.time_compression == 4.0
    assert config.chain_adapter_kind == PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX

    factory = server_main._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=server_main._build_chain_adapter(
            kind=config.chain_adapter_kind
        ),
        tick_input_source=server_main._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=config.time_compression,
        tick_interval_seconds=config.tick_interval_seconds,
    )
    loop = factory()
    # decision_cadence = 120 / 4 = 30 seconds
    assert loop._decision_cadence.total_seconds() == pytest.approx(30.0)

    # rh_chain kind requires the 5 env vars to be set (T-B-042 wiring).
    # Without them, _build_chain_adapter surfaces a typed RuntimeError
    # listing the missing keys — that's the operator-visible failure
    # mode the deploy runbook hinges on.
    with pytest.raises(RuntimeError, match="missing required env vars"):
        server_main._build_chain_adapter(
            kind=PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN,
            env={},
        )


def test_placeholder_loop_no_longer_wired(
    volume_env: Path, clear_prod_loop_env: None
) -> None:
    """T5 — :func:`_build_default_app` no longer wires :class:`_PlaceholderLoop`.

    Constructs the production app via the same entrypoint uvicorn uses
    in deployment, then introspects the :class:`AgentRunner`'s loop
    factory to confirm it constructs a :class:`SandboxPhase2Loop` —
    NOT the de-wired sprint_9 placeholder. The placeholder class
    itself is still defined (required by the rollback contract) but
    no longer reachable through the standard boot path.
    """
    # The conftest set GENESIS_SERVER_AUTOBUILD=0 before importing main,
    # so the module-level ``app`` is None; we build a fresh one here.
    app = server_main._build_default_app()
    runner = app.state.deps.agent_runner

    # The factory is the 0-arg LoopFactoryProto bound at _build_default_app
    # time — calling it should construct a SandboxPhase2Loop, NOT a
    # _PlaceholderLoop. (Sprint_13 T-B-041 contract.)
    loop = runner._loop_factory()
    assert isinstance(loop, SandboxPhase2Loop)
    assert not isinstance(loop, server_main._PlaceholderLoop)

    # The _PlaceholderLoop class IS still defined on the module — the
    # one-line rollback seam (`loop_factory=_placeholder_loop_factory(...)`)
    # depends on import safety surviving the de-wire. Asserting the symbol
    # exists keeps the rollback contract auditable.
    assert hasattr(server_main, "_PlaceholderLoop")
    assert hasattr(server_main, "_placeholder_loop_factory")


def test_loop_boot_marker_emitted_on_factory_call(tmp_path: Path) -> None:
    """T6 — T-B-043: each factory invocation appends a ``kind=='loop_boot'``
    row to ``decisions.jsonl`` for the SUBMISSION boot smoke.

    Mirrors the sprint_9 :class:`_PlaceholderLoop` marker shape MINUS the
    ``placeholder: True`` flag. The shape is asserted at the file level
    because the SUBMISSION smoke
    (``agent/scripts/sprint13_boot_smoke.py``) tails the same JSONL via
    SSE — same source-of-truth as the dashboard.

    Per-call invariants:
      * Row #1 has ``kind == 'loop_boot'``
      * No ``placeholder`` field on the row
      * ``loop == 'sandbox_phase2_real'`` (distinct from the placeholder)
      * Calling the factory twice produces TWO marker rows (one per
        /api/agent/start cycle — operator timeline reads cleanly).

    Reconstruction safety: the marker omits the ``tick`` field, so
    :meth:`SandboxPhase2Loop._reconstruct_from_disk` step 3 (tick-counter
    fold) skips it — covered by the test_sandbox_restart tick assertions.
    """
    import json

    from agent.data.sandbox_state import DECISIONS_FILENAME

    state_dir = tmp_path / "sandbox"
    chain = server_main._build_chain_adapter(
        kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX
    )
    factory = server_main._build_production_loop_factory(
        state_dir=state_dir,
        chain_adapter=chain,
        tick_input_source=server_main._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=60.0,
    )

    decisions_path = state_dir / DECISIONS_FILENAME
    assert not decisions_path.exists(), (
        "factory has not been called yet — no marker row should land"
    )

    _ = factory()

    assert decisions_path.exists()
    rows = [
        json.loads(line)
        for line in decisions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    boot = rows[0]
    assert boot["kind"] == "loop_boot"
    assert "placeholder" not in boot, (
        f"loop_boot marker must NOT carry the sprint_9 placeholder flag: {boot!r}"
    )
    assert boot["loop"] == server_main.LOOP_BOOT_MARKER_LOOP_NAME
    assert boot["loop"] == "sandbox_phase2_real"

    # Per-call: second factory invocation appends a SECOND marker so the
    # operator timeline carries one row per /api/agent/start cycle.
    _ = factory()
    rows = [
        json.loads(line)
        for line in decisions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert all(r["kind"] == "loop_boot" for r in rows)
