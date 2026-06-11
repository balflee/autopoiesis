"""RuntimeAgentRunner queue-wiring tests (Plan 2 / Task L1).

The FastAPI approve route enqueues weight deltas on the
:class:`RuntimeAgentRunner` constructed in :func:`_build_default_app`, but
``_build_production_loop_factory`` historically never received it — so the
loop it built fell back to a FRESH queue
(``sandbox_phase2_loop.py`` ctor default). Approved deltas therefore never
reached the running agent's ``_drain_and_apply_weight_deltas`` consumer.

This test pins the fix: the loop the factory builds must share the SAME
:class:`RuntimeAgentRunner` instance passed into the factory, so a delta
enqueued via the API reaches the loop's drain-and-apply path.

Runs hermetically — the conftest sets ``GENESIS_SERVER_AUTOBUILD=0`` so the
module-level ``app`` global is ``None`` and we build our own seams against a
per-test ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# `UtcClock` is imported from its canonical module (not via main) because
# main does not re-export it in __all__ — mypy --strict's
# no-implicit-reexport rule rejects `m.UtcClock`. Mirrors the convention in
# test_main_prod_loop_factory.py.
from agent.data._realtime_buffer import UtcClock

# NOTE: the class is `AgentRunner` (agent/runtime/agent_runner.py:91);
# `RuntimeAgentRunner` is only a local alias in main.py. Import + alias to
# mirror the production naming.
from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner


def test_factory_threads_shared_runtime_agent(tmp_path: Path) -> None:
    """The loop built by ``_build_production_loop_factory`` must use the SAME
    ``RuntimeAgentRunner`` we pass in (not a fresh fallback instance), so
    deltas enqueued via the API reach the loop's drain-and-apply consumer."""
    from agent.server import main as m

    shared = RuntimeAgentRunner()
    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),  # real in-memory adapter
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
        runtime_agent=shared,
    )
    loop = factory()
    assert loop._runtime_agent is shared


def test_factory_falls_back_to_fresh_runtime_agent_when_omitted(
    tmp_path: Path,
) -> None:
    """Default-OFF behaviour is byte-unchanged: omitting ``runtime_agent``
    still yields a loop with its OWN fresh ``RuntimeAgentRunner`` (the
    pre-fix construction path), so the existing factory contract holds."""
    from agent.server import main as m

    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    assert isinstance(loop._runtime_agent, RuntimeAgentRunner)


# ---------------------------------------------------------------------------
# Task L2 — un-stub the StrategyAdvisor (prod-only, behind a flag).
# ---------------------------------------------------------------------------


def test_prod_strategy_advisor_is_real_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``GENESIS_REAL_STRATEGY_ADVISOR=1`` the prod loop's advisor is the
    real Gemini-backed :class:`StrategyAdvisorImpl`; with the flag OFF (the
    default) it stays :class:`NoOpStrategyAdvisor` so the frozen-config smoke
    contract — "loop boots and ticks", not "L3 generates proposals" — is
    byte-unchanged.

    ``GeminiClient()`` constructs without ``GEMINI_API_KEY`` (it raises only
    at call time, ``gemini_client.py:153``), so the helper never needs a key
    to build the real advisor — no live Gemini is touched here.
    """
    from agent.engines.strategy_advisor import NoOpStrategyAdvisor
    from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
    from agent.server import main as m

    monkeypatch.setenv("GENESIS_REAL_STRATEGY_ADVISOR", "1")
    assert isinstance(m._make_prod_strategy_advisor(), StrategyAdvisorImpl)

    monkeypatch.delenv("GENESIS_REAL_STRATEGY_ADVISOR")
    assert isinstance(m._make_prod_strategy_advisor(), NoOpStrategyAdvisor)


# ---------------------------------------------------------------------------
# Task L3 — settlement-time self-learning (prod-only, behind a flag).
# ---------------------------------------------------------------------------


def test_prod_learning_swaps_real_updater_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With ``GENESIS_REAL_LEARNING=1`` the factory-built loop's poller runs
    the real :class:`_SettlementLearningWeightUpdater` (re-assigns the loop's
    weights from realized PnL), holding the loop itself as its weights_holder."""
    from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater
    from agent.server import main as m

    monkeypatch.setenv("GENESIS_REAL_LEARNING", "1")
    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    updater = loop._poller.weight_updater
    assert isinstance(updater, _SettlementLearningWeightUpdater)
    # The adapter re-assigns THIS loop's weights.
    assert updater.weights_holder is loop


def test_prod_learning_default_off_keeps_noop_updater(tmp_path: Path) -> None:
    """Default OFF: the poller keeps the sandbox-safe NoOp updater so the
    frozen-config smoke contract is byte-unchanged."""
    from agent.server import main as m

    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    assert isinstance(loop._poller.weight_updater, m._NoopWeightUpdater)


# ---------------------------------------------------------------------------
# Phase B / Task B2 — L6 reflection→optimize closure (prod-only, behind a
# COMBINED flag gate). Reflection alone emits no proposals (codex R7): the
# NoOp advisor returns [], so the closed loop requires BOTH the real
# ReflectionEngine AND the real StrategyAdvisorImpl. The L6 mode therefore
# only activates when BOTH ``GENESIS_REAL_REFLECTION=1`` AND
# ``GENESIS_REAL_STRATEGY_ADVISOR=1`` are set; either-alone leaves the
# flag-off behaviour byte-unchanged.
# ---------------------------------------------------------------------------


def test_l6_enabled_requires_both_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """The L6 closure gate is the AND of both flags; any single flag (or
    neither) leaves L6 OFF so the closed reflect→optimize loop never spins
    up without its advisor half (which would emit no proposals anyway)."""
    from agent.server import main as m

    monkeypatch.delenv("GENESIS_REAL_REFLECTION", raising=False)
    monkeypatch.delenv("GENESIS_REAL_STRATEGY_ADVISOR", raising=False)
    assert m._l6_reflection_optimize_enabled() is False

    monkeypatch.setenv("GENESIS_REAL_REFLECTION", "1")
    assert m._l6_reflection_optimize_enabled() is False

    monkeypatch.setenv("GENESIS_REAL_STRATEGY_ADVISOR", "1")
    assert m._l6_reflection_optimize_enabled() is True

    # A non-"1" value on either flag drops L6 back OFF (exact-"1" convention).
    monkeypatch.setenv("GENESIS_REAL_REFLECTION", "true")
    assert m._l6_reflection_optimize_enabled() is False


def test_l6_factory_wires_real_reflection_and_real_advisor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With BOTH flags ON the factory-built loop carries a real
    :class:`ReflectionEngine`, the real Gemini-backed
    :class:`StrategyAdvisorImpl`, AND the B1 reflection-window population
    seam enabled — the full reflect→learn→optimize closure.

    ``GeminiClient()`` constructs without ``GEMINI_API_KEY`` (it raises only
    at call time), so no live Gemini is touched here.
    """
    from agent.engines.reflection import ReflectionEngine
    from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
    from agent.server import main as m

    monkeypatch.setenv("GENESIS_REAL_REFLECTION", "1")
    monkeypatch.setenv("GENESIS_REAL_STRATEGY_ADVISOR", "1")
    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    assert isinstance(loop._reflection_engine, ReflectionEngine)
    assert isinstance(loop._strategy_advisor, StrategyAdvisorImpl)
    assert loop._populate_reflection_window is True


def test_l6_default_off_keeps_noop_advisor_and_no_reflection_engine(
    tmp_path: Path,
) -> None:
    """Default OFF (neither flag): no reflection engine is wired, the advisor
    stays :class:`NoOpStrategyAdvisor`, and the B1 window-population seam is
    OFF — the frozen-config smoke contract is byte-unchanged."""
    from agent.engines.strategy_advisor import NoOpStrategyAdvisor
    from agent.server import main as m

    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    assert loop._reflection_engine is None
    assert isinstance(loop._strategy_advisor, NoOpStrategyAdvisor)
    assert loop._populate_reflection_window is False


def test_l6_advisor_flag_alone_does_not_wire_reflection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``GENESIS_REAL_STRATEGY_ADVISOR=1`` alone (the pre-B2 L2 path) keeps
    its real advisor but does NOT spin up the reflection half — L6 closure
    needs both flags. The B1 window-population seam stays OFF, so the L2
    advisor input is byte-unchanged vs the pre-B2 prod loop."""
    from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
    from agent.server import main as m

    monkeypatch.setenv("GENESIS_REAL_STRATEGY_ADVISOR", "1")
    monkeypatch.delenv("GENESIS_REAL_REFLECTION", raising=False)
    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    assert isinstance(loop._strategy_advisor, StrategyAdvisorImpl)
    assert loop._reflection_engine is None
    assert loop._populate_reflection_window is False


def test_l6_reflection_flag_alone_does_not_wire_reflection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``GENESIS_REAL_REFLECTION=1`` alone does NOT wire the reflection engine
    nor flip the B1 population seam in the PROD loop — without the real
    advisor (its other half) the closure would emit no proposals (codex R7),
    so the L6 mode stays atomic and the prod loop is byte-unchanged."""
    from agent.engines.strategy_advisor import NoOpStrategyAdvisor
    from agent.server import main as m

    monkeypatch.setenv("GENESIS_REAL_REFLECTION", "1")
    monkeypatch.delenv("GENESIS_REAL_STRATEGY_ADVISOR", raising=False)
    factory = m._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=m._SandboxChainAdapter(),
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
    )
    loop = factory()
    assert loop._reflection_engine is None
    assert isinstance(loop._strategy_advisor, NoOpStrategyAdvisor)
    assert loop._populate_reflection_window is False
