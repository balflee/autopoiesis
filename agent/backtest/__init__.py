"""Track B sprint_9 backtest engine — T-B-026.

The backtest module replays historical Polymarket tennis markets through
the :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop` so the
6-parameter fusion weight tuple can be tuned offline before sprint_10
goes live. It is deliberately **layered on top** of the existing sandbox
loop (composition, not extension) so a future change to the loop body
flows through here without a fork.

Modules
-------

* :mod:`agent.backtest.historical_fetcher` — async fetcher + on-disk
  cache for the gamma-api closed-tennis-markets endpoint. Point-in-time
  ledger of (timestamp, mid_price) tuples so the replay can read prices
  at-or-before any tick wall time without lookahead.

* :mod:`agent.backtest.replay_runner` — wires the cached snapshots
  into the :class:`SandboxPhase2Loop` via Protocol-conformant fakes
  (executor, settlement client, chain adapter, weight updater, LLM
  client). Default ``no_llm=True``; ``enable_llm`` flips to the L1 / L2
  trace path that goes through the existing VCR cassettes
  (:mod:`tests.agent.llm.cassettes`).

* :mod:`agent.backtest.sweep_runner` — serial driver across a list of
  starting-weight tuples. Writes the per-config metrics +
  ``lifetimes.jsonl`` consumed by the
  :mod:`harness.tools.backtest_validator` gate (Track C, but the file
  contract is symmetric — see PRD §14.3).

Determinism contract
--------------------

Three sequential ``sweep_runner.run_sweep`` invocations with the same
seed MUST produce byte-identical ``results.json`` artefacts. The
contract is enforced by :func:`tests.agent.backtest.test_sweep_runner.test_determinism_3x_identical`.

Lookahead guard
---------------

Every cached market is a frozen point-in-time ledger. The replay
runner asserts at every tick that the price feed served the tick comes
from a snapshot timestamp ``<=`` the tick's simulated wall time; a
violation raises :class:`agent.backtest.replay_runner.LookaheadInReplayError`
loudly rather than silently producing a "winning" backtest from leaked
future data.
"""

from __future__ import annotations

from agent.backtest.historical_fetcher import (
    GAMMA_MARKETS_URL,
    MarketSnapshot,
    MarketSnapshotProvider,
    PricePoint,
    cache_filename,
    fetch_closed_tennis_markets,
    load_cached_market,
    save_cached_market,
)
from agent.backtest.replay_runner import (
    DEFAULT_REPLAY_DECISION_CADENCE,
    DEFAULT_REPLAY_INITIAL_BANKROLL_USD,
    DEFAULT_REPLAY_INITIAL_BREATH,
    LookaheadInReplayError,
    NoOpLLMClient,
    ReplayConfig,
    ReplayMetrics,
    run_replay,
)
from agent.backtest.sweep_runner import (
    SweepConfig,
    SweepResult,
    new_run_id,
    run_sweep,
)

__all__ = [
    "DEFAULT_REPLAY_DECISION_CADENCE",
    "DEFAULT_REPLAY_INITIAL_BANKROLL_USD",
    "DEFAULT_REPLAY_INITIAL_BREATH",
    "GAMMA_MARKETS_URL",
    "LookaheadInReplayError",
    "MarketSnapshot",
    "MarketSnapshotProvider",
    "NoOpLLMClient",
    "PricePoint",
    "ReplayConfig",
    "ReplayMetrics",
    "SweepConfig",
    "SweepResult",
    "cache_filename",
    "fetch_closed_tennis_markets",
    "load_cached_market",
    "new_run_id",
    "run_replay",
    "run_sweep",
    "save_cached_market",
]
