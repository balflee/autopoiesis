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


def _build_loop_with_env(tmp_path, monkeypatch, *, tithe_env):
    """Build one incarnation loop with SANDBOX_DIVINE_ECONOMY=1 and the given
    SANDBOX_DIVINE_TITHE env (None = unset)."""
    monkeypatch.setenv("SANDBOX_DIVINE_ECONOMY", "1")
    if tithe_env is None:
        monkeypatch.delenv("SANDBOX_DIVINE_TITHE", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_DIVINE_TITHE", tithe_env)
    chain = M._build_chain_adapter(kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX)
    return M._build_one_incarnation_loop(
        incarnation_idx=0,
        state_dir=tmp_path / "sandbox",
        chain_adapter=chain,
        initial_weights=None,
        shared_weight_updater=WeightUpdater(),
        shared_advisor=M._make_prod_strategy_advisor(),
        tick_input_source=M._IdleTickInputSource(),
        settlement_client=None,
        market_resolver=None,
        wall_clock=UtcClock(),
        decision_cadence=datetime.timedelta(seconds=60),
        runtime_agent=None,
    )


def test_tithe_on_by_default_when_economy_on(tmp_path, monkeypatch):
    """SANDBOX_DIVINE_TITHE unset → tithe stays coupled to the economy (the
    pre-decoupling behaviour: divine economy on ⇒ tithe on)."""
    loop = _build_loop_with_env(tmp_path, monkeypatch, tithe_env=None)
    assert loop._divine_tithe is True


def test_tithe_can_be_disabled_independently_of_the_economy(tmp_path, monkeypatch):
    """SANDBOX_DIVINE_TITHE=0 turns OFF only the tithe; the rest of the divine
    economy (the recording hook → tribute/treasury + living-stage fields) stays
    on. This is the lever for stopping the flat rent from draining the bankroll
    into the $5 min-bet dead zone without losing the /living economy display."""
    loop = _build_loop_with_env(tmp_path, monkeypatch, tithe_env="0")
    assert loop._divine_tithe is False
    # economy still on: the divine recording hook (not the noop) is wired,
    # carrying the incarnation idx — so tribute/treasury + record_living_stage
    # are unaffected.
    assert getattr(loop._state_hook, "_incarnation_number", None) == 0


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
