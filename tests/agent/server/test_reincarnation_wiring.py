import pytest

from agent.runtime.incarnation_supervisor import LiveIncarnationSupervisor
from agent.runtime.sandbox_phase2_loop import SandboxPhase2Loop
from agent.server import main as server_main


@pytest.fixture
def volume_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_STATE_DIR", str(tmp_path / "sandbox"))
    monkeypatch.setenv("BACKTEST_OUTPUT_ROOT", str(tmp_path / "bt" / "runs"))
    monkeypatch.setenv("BACKTEST_CACHE_DIR", str(tmp_path / "bt" / "cache"))
    return tmp_path


def test_reincarnation_off_factory_builds_single_loop(volume_env, monkeypatch):
    monkeypatch.delenv("SANDBOX_REINCARNATION", raising=False)
    app = server_main._build_default_app()
    handle = app.state.deps.agent_runner._loop_factory()
    assert isinstance(handle, SandboxPhase2Loop)


def test_reincarnation_on_factory_builds_supervisor(volume_env, monkeypatch):
    monkeypatch.setenv("SANDBOX_REINCARNATION", "1")
    app = server_main._build_default_app()
    handle = app.state.deps.agent_runner._loop_factory()
    assert isinstance(handle, LiveIncarnationSupervisor)
