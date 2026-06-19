import datetime

from agent.data._realtime_buffer import UtcClock
from agent.engines.weight_updater import WeightUpdater
from agent.server import main as M
from agent.server.bootstrap import PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX


def test_build_one_incarnation_loop_stamps_idx_and_uses_given_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_DIVINE_ECONOMY", "1")
    chain = M._build_chain_adapter(kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX)
    advisor = M._make_prod_strategy_advisor()
    shared = WeightUpdater()
    loop = M._build_one_incarnation_loop(
        incarnation_idx=3,
        state_dir=tmp_path / "sandbox",
        chain_adapter=chain,
        initial_weights=None,
        shared_weight_updater=shared,
        shared_advisor=advisor,
        tick_input_source=M._IdleTickInputSource(),
        settlement_client=None,
        market_resolver=None,
        wall_clock=UtcClock(),
        decision_cadence=datetime.timedelta(seconds=60),
        runtime_agent=None,
    )
    assert loop._incarnation_number == 3
    assert loop._chain_adapter is chain
    # the divine hook carries the same incarnation idx so DeathRecord.incarnation_number is right
    assert getattr(loop._state_hook, "_incarnation_number", None) == 3


def test_zero_arg_factory_still_works(tmp_path):
    chain = M._build_chain_adapter(kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX)
    factory = M._build_production_loop_factory(
        state_dir=tmp_path / "sandbox",
        chain_adapter=chain,
        tick_input_source=M._IdleTickInputSource(),
        wall_clock=UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=60.0,
    )
    loop = factory()
    assert loop._incarnation_number == 0  # wrapper builds incarnation 0
