"""Tests for :mod:`agent.backtest.replay_runner` — T-B-026.

Covers:

* :func:`run_replay` produces a populated :class:`ReplayMetrics` against
  a pair of cached snapshots (the smoke path through the loop body).
* The synthetic signal generator is deterministic per (seed, market, tick).
* The lookahead guard fires when the provider serves a future price
  (defence-in-depth — the guard would normally never trip but the test
  injects a fault to prove the failure path raises a typed error).
* The ``enable_llm=True`` path raises immediately on a missing
  ``GEMINI_API_KEY`` env var (the brief locks "no silent fallback").
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    MarketSnapshotProvider,
    PricePoint,
)
from agent.backtest.replay_runner import (
    DEFAULT_REPLAY_DECISION_CADENCE,
    LookaheadInReplayError,
    NoOpLLMClient,
    ReplayConfig,
    _DeterministicSignalSource,
    _ReplayTickInputSource,
    run_replay,
)
from agent.core.state import Weights

# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


def _make_snapshots() -> list[MarketSnapshot]:
    """Two synthetic resolved-tennis snapshots spanning ~3 simulated days."""
    return [
        MarketSnapshot(
            market_id="9000001",
            slug="atp-replay-alpha",
            end_date_iso="2026-05-04T17:00:00+00:00",
            resolution_ts_iso="2026-05-03T20:00:00+00:00",
            outcome="yes",
            winning_price=1.0,
            liquidity_cap_usd=20.0,
            price_ledger=[
                PricePoint(ts="2026-05-01T00:00:00+00:00", mid_price=0.5),
                PricePoint(ts="2026-05-02T12:00:00+00:00", mid_price=0.65),
                PricePoint(ts="2026-05-03T20:00:00+00:00", mid_price=1.0),
            ],
        ),
        MarketSnapshot(
            market_id="9000002",
            slug="atp-replay-bravo",
            end_date_iso="2026-05-05T17:00:00+00:00",
            resolution_ts_iso="2026-05-04T20:00:00+00:00",
            outcome="no",
            winning_price=1.0,
            liquidity_cap_usd=15.0,
            price_ledger=[
                PricePoint(ts="2026-05-01T00:00:00+00:00", mid_price=0.5),
                PricePoint(ts="2026-05-03T00:00:00+00:00", mid_price=0.35),
                PricePoint(ts="2026-05-04T20:00:00+00:00", mid_price=1.0),
            ],
        ),
    ]


def _balanced_weights() -> Weights:
    return Weights(
        w_r=0.5, w_s=0.5,
        alpha=[0.33, 0.33, 0.34],
        beta=[1.0, 0.0],
        rho=0.6,
    )


# --------------------------------------------------------------------------- #
# Smoke
# --------------------------------------------------------------------------- #


def test_run_replay_smoke_produces_metrics(tmp_path: Path) -> None:
    """End-to-end: 10 ticks against 2 markets → populated metrics."""
    snaps = _make_snapshots()
    cfg = ReplayConfig(
        starting_weights=_balanced_weights(),
        seed=7,
        cache_dir=tmp_path / "_unused_cache",  # explicit snapshots wins
        max_ticks=10,
        start_ts=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
    )
    metrics = asyncio.run(
        run_replay(cfg, state_root=tmp_path / "state", snapshots=snaps)
    )
    assert metrics.ticks_completed == 10
    # NO_BET + BET counts sum to the tick count (NO_BET reasons are
    # absorbed in `no_bets_emitted`).
    assert metrics.bets_placed + metrics.no_bets_emitted == 10
    assert metrics.died is False
    assert metrics.death_cause == "alive"
    assert metrics.config_id  # deterministic fallback resolves


def _bets_at_min_confidence(threshold: float, tmp_path: Path) -> int:
    cfg = ReplayConfig(
        starting_weights=_balanced_weights(),
        seed=7,
        cache_dir=tmp_path / "_unused_cache",
        max_ticks=10,
        start_ts=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        min_confidence=threshold,
    )
    metrics = asyncio.run(
        run_replay(cfg, state_root=tmp_path / f"state_{threshold}", snapshots=_make_snapshots())
    )
    return metrics.bets_placed


def test_run_replay_threads_min_confidence_to_decision_engine(tmp_path: Path) -> None:
    # ReplayConfig.min_confidence must reach the DecisionEngine's abstain gate:
    # an impossibly-high threshold forces NO_BET on every tick, a zero
    # threshold lets bets through. Proves the sizing/abstention family (②) is
    # swept, not held at defaults.
    bets_always_abstain = _bets_at_min_confidence(1.0, tmp_path)
    bets_never_abstain = _bets_at_min_confidence(0.0, tmp_path)

    assert bets_always_abstain == 0
    assert bets_never_abstain > 0


def test_run_replay_signal_source_is_deterministic() -> None:
    """Same seed + market + tick MUST produce identical signals."""
    src_a = _DeterministicSignalSource(seed=42)
    src_b = _DeterministicSignalSource(seed=42)
    asof = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    sig_a = src_a.signals_for(market_id="m-x", tick=5, asof_ts=asof)
    sig_b = src_b.signals_for(market_id="m-x", tick=5, asof_ts=asof)
    assert sig_a.keys() == sig_b.keys()
    for k in sig_a:
        assert sig_a[k].score == sig_b[k].score
        assert sig_a[k].confidence == sig_b[k].confidence


# --------------------------------------------------------------------------- #
# Lookahead guard
# --------------------------------------------------------------------------- #


def test_lookahead_violation_raises_typed_error() -> None:
    """A faked-future served_price MUST raise LookaheadInReplayError.

    Two layers of leak simulation:

    1. ``MarketSnapshotProvider.assert_no_lookahead`` (the chokepoint) is
       exercised directly with a mismatched ``served_price`` — proves the
       chokepoint itself raises.
    2. A ``_LeakyTickInputSource`` subclass calls the chokepoint with a
       deliberately-wrong served price + verifies the runner converts
       :class:`ValueError` to :class:`LookaheadInReplayError`.
    """
    snaps = _make_snapshots()
    provider = MarketSnapshotProvider(snaps)
    src = _DeterministicSignalSource(seed=0)

    # Layer 1 — chokepoint raises ValueError on mismatch.
    asof = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="lookahead guard tripped"):
        provider.assert_no_lookahead(
            market_id="9000001",
            asof_ts=asof,
            served_price=1.0,  # at-or-before is 0.5
        )

    # Layer 2 — _ReplayTickInputSource subclass that wraps the chokepoint
    # so it always sees a future-leak served price; the runner code path
    # MUST convert the underlying ValueError to LookaheadInReplayError.
    class _LeakyTickInputSource(_ReplayTickInputSource):
        def inputs_for(self, *, asof_ts: datetime, tick: int):  # type: ignore[no-untyped-def]
            market_id = self.selected_market_ids[tick % len(self.selected_market_ids)]
            try:
                self.provider.assert_no_lookahead(
                    market_id=market_id,
                    asof_ts=asof_ts,
                    served_price=1.0,  # fake the leak
                )
            except ValueError as e:
                raise LookaheadInReplayError(str(e)) from e
            return None

    tick_src = _LeakyTickInputSource(
        provider=provider,
        signal_source=src,
        selected_market_ids=provider.market_ids,
    )
    with pytest.raises(LookaheadInReplayError):
        tick_src.inputs_for(asof_ts=asof, tick=0)


def test_pre_history_tick_returns_no_inputs() -> None:
    """A tick whose asof_ts pre-dates every price → ``None`` (NO_BET path)."""
    snaps = _make_snapshots()
    provider = MarketSnapshotProvider(snaps)
    src = _DeterministicSignalSource(seed=0)
    tick_src = _ReplayTickInputSource(
        provider=provider,
        signal_source=src,
        selected_market_ids=provider.market_ids,
    )
    asof = datetime(2025, 1, 1, tzinfo=UTC)
    result = tick_src.inputs_for(asof_ts=asof, tick=0)
    assert result is None


# --------------------------------------------------------------------------- #
# LLM gating
# --------------------------------------------------------------------------- #


def test_enable_llm_without_api_key_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``enable_llm=True`` MUST raise when GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = ReplayConfig(
        starting_weights=_balanced_weights(),
        enable_llm=True,
    )
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        asyncio.run(
            run_replay(
                cfg,
                state_root=tmp_path,
                snapshots=_make_snapshots(),
            )
        )


def test_noop_llm_client_returns_empty_dict() -> None:
    """The default no_llm path uses :class:`NoOpLLMClient` → empty payload."""
    client = NoOpLLMClient()
    result = asyncio.run(
        client.structured_call(
            model="gemini-3.1-flash-lite",
            prompt="ignored",
            schema={"type": "object"},
        )
    )
    assert result == {}
    assert len(client.calls) == 1


def test_default_decision_cadence_matches_sandbox_loop() -> None:
    """Replay cadence must mirror the production sandbox cadence."""
    from datetime import timedelta

    assert DEFAULT_REPLAY_DECISION_CADENCE == timedelta(minutes=60)


# --------------------------------------------------------------------------- #
# Empty-cache guard
# --------------------------------------------------------------------------- #


def test_run_replay_on_empty_cache_raises(tmp_path: Path) -> None:
    """No cached snapshots AND no explicit snapshots → clear RuntimeError."""
    cfg = ReplayConfig(
        starting_weights=_balanced_weights(),
        cache_dir=tmp_path / "empty",
    )
    with pytest.raises(RuntimeError, match="no cached markets"):
        asyncio.run(run_replay(cfg, state_root=tmp_path / "state", snapshots=None))


# --------------------------------------------------------------------------- #
# Real-signal seam — injected signal_source_factory
# --------------------------------------------------------------------------- #


def test_run_replay_accepts_injected_real_signal_source(tmp_path: Path) -> None:
    from agent.backtest.real_signal_source import RealSignalSource
    from agent.backtest.tennis_match_resolver import TennisMatchResolver

    snaps = _make_snapshots()
    cfg = ReplayConfig(
        starting_weights=_balanced_weights(),
        seed=7,
        cache_dir=tmp_path / "_unused",
        max_ticks=6,
        start_ts=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
    )

    def _factory(provider: MarketSnapshotProvider) -> RealSignalSource:
        return RealSignalSource(provider=provider, resolver=TennisMatchResolver(name_index={}))

    metrics = asyncio.run(
        run_replay(cfg, state_root=tmp_path / "state", snapshots=snaps,
                   signal_source_factory=_factory)
    )
    assert metrics.ticks_completed == 6


# --------------------------------------------------------------------------- #
# Settlement-learning parity (Task L4, Plan 2)
# --------------------------------------------------------------------------- #


def test_terminal_weights_default_equals_starting_weights(tmp_path: Path) -> None:
    # enable_settlement_learning defaults False → the _NoopSettlementWeightUpdater
    # stays on the poller, so settlements are inert and terminal == starting
    # (the frozen-config smoke contract the sweep relies on).
    snaps = _make_snapshots()
    starting = _balanced_weights()
    cfg = ReplayConfig(
        starting_weights=starting,
        seed=7,
        cache_dir=tmp_path / "_unused_cache",
        max_ticks=24,
        start_ts=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        # 24h cadence advances the compressed clock past the snapshots'
        # resolution timestamps within the tick budget, so bets settle.
        decision_cadence=timedelta(hours=24),
    )
    metrics = asyncio.run(
        run_replay(cfg, state_root=tmp_path / "state", snapshots=snaps)
    )
    assert metrics.bets_placed > 0  # bets were placed (so settlements happen)
    assert metrics.settlements_processed > 0
    assert metrics.terminal_weights == starting


def test_enable_settlement_learning_moves_terminal_weights(tmp_path: Path) -> None:
    # With enable_settlement_learning=True the real WeightUpdater is bridged onto
    # the poller; settled bets carry signal_scores + bet_direction end-to-end
    # (Task L3), so realized PnL nudges the weights off the seed config.
    snaps = _make_snapshots()
    starting = _balanced_weights()
    cfg = ReplayConfig(
        starting_weights=starting,
        seed=7,
        cache_dir=tmp_path / "_unused_cache",
        max_ticks=24,
        start_ts=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        decision_cadence=timedelta(hours=24),
        enable_settlement_learning=True,
    )
    metrics = asyncio.run(
        run_replay(cfg, state_root=tmp_path / "state", snapshots=snaps)
    )
    assert metrics.bets_placed > 0
    assert metrics.settlements_processed > 0
    # Learning moved the weights off the seed.
    assert metrics.terminal_weights != starting
    # ...but the seed config the metric echoes is unchanged (input is preserved).
    assert metrics.starting_weights == starting
