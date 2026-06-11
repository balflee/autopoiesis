"""GENESIS_REAL_SIGNALS flag wiring tests (Task D1).

The prod mock-bet loop in :func:`agent.server.main._build_default_app`
constructs the per-tick signal source. Historically that was always the
synthetic :class:`agent.backtest.replay_runner._DeterministicSignalSource`
(hash-derived fake signals). Task D1 introduces a flag-gated seam — the
module-level helper :func:`agent.server.main._make_prod_signal_source` —
so that with ``GENESIS_REAL_SIGNALS=1`` the loop wires the real
:class:`agent.backtest.real_signal_source.RealSignalSource` (real
momentum + Sackmann facets) instead.

These tests pin BOTH branches of that seam:

1. Flag UNSET / not ``"1"`` → synthetic ``_DeterministicSignalSource``
   (preserves the existing default — the change is reversible by simply
   not setting the env var).
2. Flag == ``"1"`` → ``RealSignalSource`` constructed against the
   re-vendored corpus loader (offline-resolvable).

The helper takes a ``provider`` (a
:class:`agent.backtest.historical_fetcher.MarketSnapshotProvider`); the
test passes an empty provider because the helper only stores it on the
returned source — it does not read from it at construction time.

Runs hermetically under ``GENESIS_SERVER_AUTOBUILD=0`` (set by the
conftest before :mod:`agent.server.main` import) so no FastAPI app or
state dir is built.
"""

from __future__ import annotations

import pytest

from agent.backtest.historical_fetcher import MarketSnapshotProvider
from agent.backtest.real_signal_source import RealSignalSource
from agent.backtest.replay_runner import _DeterministicSignalSource
from agent.server import main as server_main

REAL_SIGNALS_ENV_VAR = "GENESIS_REAL_SIGNALS"


def test_flag_off_keeps_deterministic_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag unset → the synthetic ``_DeterministicSignalSource`` default.

    The prod-loop default must stay synthetic so the real-signal path is
    strictly opt-in (and the change is one env var away from rollback).
    """
    monkeypatch.delenv(REAL_SIGNALS_ENV_VAR, raising=False)
    provider = MarketSnapshotProvider([])

    source = server_main._make_prod_signal_source(provider)

    assert isinstance(source, _DeterministicSignalSource)
    assert not isinstance(source, RealSignalSource)


def test_flag_non_one_value_keeps_deterministic_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the exact value ``"1"`` flips the seam — anything else is off.

    Guards against a truthy-but-not-``"1"`` value (e.g. ``"0"``,
    ``"true"``) accidentally enabling the real path.
    """
    monkeypatch.setenv(REAL_SIGNALS_ENV_VAR, "0")
    provider = MarketSnapshotProvider([])

    source = server_main._make_prod_signal_source(provider)

    assert isinstance(source, _DeterministicSignalSource)


def test_flag_on_wires_real_signal_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GENESIS_REAL_SIGNALS=1`` → a real ``RealSignalSource``.

    Asserts the helper returns the real source AND that it was built
    against the re-vendored corpus loader (the A0 correction: the loader
    must point at ``DEFAULT_CORPUS_DIR``, NOT the bare synthetic-fixture
    snapshot default).
    """
    monkeypatch.setenv(REAL_SIGNALS_ENV_VAR, "1")
    provider = MarketSnapshotProvider([])

    source = server_main._make_prod_signal_source(provider)

    assert isinstance(source, RealSignalSource)
    # The helper threaded the provided provider through verbatim.
    assert source.provider is provider
    # A0 correction: loader reads the full re-vendored corpus dir.
    from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR

    assert source.loader.snapshot_dir == DEFAULT_CORPUS_DIR
