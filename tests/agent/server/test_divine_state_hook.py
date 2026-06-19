"""Living Stage P1 — _SandboxStateHook routing + flag-gated factory wiring."""

from __future__ import annotations

from agent.data._realtime_buffer import UtcClock
from agent.data.sandbox_state import (
    DEATHS_FILENAME,
    GODS_TREASURY_FILENAME,
    SandboxStateWriter,
    iter_jsonl,
)
from agent.server import main as server_main
from agent.server.bootstrap import PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX
from agent.server.main import _SandboxStateHook


def _build_test_loop(tmp_path):
    """Build a loop via the real prod factory (mirrors test_main_prod_loop_factory)."""
    state_dir = tmp_path / "sandbox"
    chain = server_main._build_chain_adapter(kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX)
    factory = server_main._build_production_loop_factory(
        state_dir=state_dir,
        chain_adapter=chain,
        tick_input_source=server_main._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=60.0,
    )
    return factory()


def test_hook_routes_tribute_tithe_death(tmp_path):
    writer = SandboxStateWriter(root=tmp_path)
    hook = _SandboxStateHook(writer=writer)
    hook.emit(
        kind="tithe", tick=20, amount_usd=20.0, breath_cost=0.0,
        breath_after=80.0, bankroll_after=980.0,
    )
    hook.emit(
        kind="tribute", tick=40, amount_usd=2000.0, success=True,
        breath_after=35.0, bankroll_after=0.0, dice_roll=0.5,
    )
    hook.emit(
        kind="agent_died", agent_id="a", last_tick=99, kill_tx_hash=None,
        tombstone_token_id=None, tombstone_tx_hash=None, bankroll_usd=0.0,
        final_weights_hash=None, memory_bank_cid=None, last_words="bye",
    )
    treasury = iter_jsonl(tmp_path / GODS_TREASURY_FILENAME)
    assert [r["type"] for r in treasury] == ["tithe", "tribute"]
    assert treasury[1]["dice_roll"] == 0.5
    deaths = iter_jsonl(tmp_path / DEATHS_FILENAME)
    assert deaths[0]["final_bankroll_usd"] == 0.0 and deaths[0]["last_words"] == "bye"


def test_hook_ignores_unknown_kind(tmp_path):
    writer = SandboxStateWriter(root=tmp_path)
    hook = _SandboxStateHook(writer=writer)
    hook.emit(kind="phase_transition", to="PHASE_3_MASTER")  # no-op, no crash
    assert not (tmp_path / GODS_TREASURY_FILENAME).exists()


def test_hook_never_raises_on_bad_payload_or_io(tmp_path):
    # MED-4: emit must NEVER raise into the loop (StateHook contract).
    writer = SandboxStateWriter(root=tmp_path)
    hook = _SandboxStateHook(writer=writer)
    hook.emit(kind="tribute", tick=1)  # missing required keys → swallowed

    class _BoomWriter:
        def append_tithe(self, *_a, **_k):
            raise OSError("disk full")

    boom = _SandboxStateHook(writer=_BoomWriter())  # type: ignore[arg-type]
    boom.emit(
        kind="tithe", tick=2, amount_usd=20.0, breath_cost=0.0,
        breath_after=80.0, bankroll_after=980.0,
    )  # IO error → swallowed
    # passes iff neither emit raised


def test_factory_off_uses_noop_hook(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX_DIVINE_ECONOMY", raising=False)
    loop = _build_test_loop(tmp_path)
    assert type(loop._state_hook).__name__ == "_NoopStateHook"
    assert loop._tribute_policy is None and loop._divine_tithe is False
    assert loop._record_living_stage_fields is False  # byte-identical OFF


def test_factory_on_enables_divine_economy(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_DIVINE_ECONOMY", "1")
    loop = _build_test_loop(tmp_path)
    assert type(loop._state_hook).__name__ == "_SandboxStateHook"
    assert loop._tribute_policy is not None and loop._tribute_rng is not None
    assert loop._divine_tithe is True
    assert loop._record_living_stage_fields is True
