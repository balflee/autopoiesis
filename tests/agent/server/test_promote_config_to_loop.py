"""Tests for the PROMOTE pipeline: backtest config → live loop cold-start.

Covers _reset_durable_life + _consume_staged_config + the factory wiring
that lets /api/agent/start adopt a promoted StartingWeightConfig
(workshop backtest → PROMOTE → live agent dogfood loop).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.server.main import (
    AGENT_CONFIG_APPLIED_FILENAME,
    AGENT_CONFIG_CONSUMING_FILENAME,
    AGENT_CONFIG_FILENAME,
    _reset_durable_life,
)

_LIFE_FILES = (
    "agent_state.json",
    "open_bets.jsonl",
    "settled_bets.jsonl",
    "decisions.jsonl",
    "reflections.jsonl",
    "proposals.jsonl",
)


# --------------------------------------------------------------------------- #
# Task 2 — _reset_durable_life
# --------------------------------------------------------------------------- #


def test_reset_durable_life_archives_all_state_files(tmp_path: Path) -> None:
    for name in _LIFE_FILES:
        (tmp_path / name).write_text("prior-life\n", encoding="utf-8")

    _reset_durable_life(tmp_path)

    # All six removed from the live root.
    for name in _LIFE_FILES:
        assert not (tmp_path / name).exists(), f"{name} should be archived out of root"
    # Present under exactly one timestamped subdir of _prev_life/.
    prev = tmp_path / "_prev_life"
    assert prev.is_dir()
    backup_dirs = sorted(p for p in prev.iterdir() if p.is_dir())
    assert len(backup_dirs) == 1, "all life files go to ONE backup subdir"
    backup = backup_dirs[0]
    for name in _LIFE_FILES:
        assert (backup / name).read_text(encoding="utf-8") == "prior-life\n"


def test_reset_durable_life_archives_memory_bank_dir(tmp_path: Path) -> None:
    (tmp_path / "_mb").mkdir()
    (tmp_path / "_mb" / "journal.txt").write_text("mem\n", encoding="utf-8")

    _reset_durable_life(tmp_path)

    assert not (tmp_path / "_mb").exists(), "memory bank should be archived"
    prev = tmp_path / "_prev_life"
    backup_dirs = [p for p in prev.iterdir() if p.is_dir()]
    assert len(backup_dirs) == 1
    assert (backup_dirs[0] / "_mb" / "journal.txt").read_text(encoding="utf-8") == "mem\n"


def test_reset_durable_life_is_noop_when_nothing_present(tmp_path: Path) -> None:
    _reset_durable_life(tmp_path)  # must not raise
    assert not (tmp_path / "_prev_life").exists(), "no backup dir when nothing to move"
    # agent_config.json is NOT a life-state file — must be left untouched.
    (tmp_path / AGENT_CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    _reset_durable_life(tmp_path)
    assert (tmp_path / AGENT_CONFIG_FILENAME).exists()


# --------------------------------------------------------------------------- #
# Shared helper for the _consume_staged_config tests (Task 3+)
# --------------------------------------------------------------------------- #


def _write_config(state_dir: Path, *, label: str, w_r: float, rho: float) -> None:
    """Mirror _write_agent_config_atomic's on-disk shape (a StartingWeightConfig)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "label": label,
        "w_r": w_r,
        "w_s": round(1.0 - w_r, 6),
        "alpha": 0.9,
        "beta": 1.0,
        "rho": rho,
    }
    (state_dir / AGENT_CONFIG_FILENAME).write_text(json.dumps(cfg), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Task 3 — _consume_staged_config
# --------------------------------------------------------------------------- #


def test_consume_staged_config_returns_none_when_absent(tmp_path: Path) -> None:
    from agent.server.main import _consume_staged_config

    assert _consume_staged_config(tmp_path) is None
    assert not (tmp_path / AGENT_CONFIG_APPLIED_FILENAME).exists()


def test_consume_staged_config_projects_resets_and_marks_applied(tmp_path: Path) -> None:
    from agent.server.main import _consume_staged_config

    # A prior life exists on disk.
    (tmp_path / "agent_state.json").write_text("prior\n", encoding="utf-8")
    (tmp_path / "decisions.jsonl").write_text("prior\n", encoding="utf-8")
    _write_config(tmp_path, label="TEST-EXTREME", w_r=0.9, rho=0.9)

    weights = _consume_staged_config(tmp_path)

    # 1. Projected to canonical Weights with the promoted values.
    assert weights is not None
    assert abs(weights.w_r - 0.9) < 1e-9
    assert abs(weights.rho - 0.9) < 1e-9
    # 2. Prior life reset — snapshot gone from root, archived under _prev_life/*/.
    assert not (tmp_path / "agent_state.json").exists()
    archived = list((tmp_path / "_prev_life").glob("*/agent_state.json"))
    assert len(archived) == 1
    # 3. Config consumed: claim path gone, applied marker present, source gone.
    assert not (tmp_path / AGENT_CONFIG_FILENAME).exists()
    assert not (tmp_path / AGENT_CONFIG_CONSUMING_FILENAME).exists()
    assert (tmp_path / AGENT_CONFIG_APPLIED_FILENAME).exists()


def test_consume_staged_config_second_call_is_noop(tmp_path: Path) -> None:
    from agent.server.main import _consume_staged_config

    _write_config(tmp_path, label="X", w_r=0.7, rho=0.6)
    first = _consume_staged_config(tmp_path)
    assert first is not None
    # No fresh promote → applied marker present, live config absent → None.
    assert _consume_staged_config(tmp_path) is None


def test_consume_recovers_orphaned_consuming_file(tmp_path: Path) -> None:
    """Crash mid-consume leaves agent_config.consuming.json with no
    agent_config.json — the next start must recover it (round-2 H1)."""
    from agent.server.main import _consume_staged_config

    tmp_path.mkdir(parents=True, exist_ok=True)
    orphan = {
        "label": "ORPHAN", "w_r": 0.8, "w_s": 0.2,
        "alpha": 0.9, "beta": 1.0, "rho": 0.85,
    }
    (tmp_path / AGENT_CONFIG_CONSUMING_FILENAME).write_text(
        json.dumps(orphan), encoding="utf-8"
    )
    # No agent_config.json present.

    weights = _consume_staged_config(tmp_path)

    assert weights is not None
    assert abs(weights.w_r - 0.8) < 1e-9
    assert (tmp_path / AGENT_CONFIG_APPLIED_FILENAME).exists()
    assert not (tmp_path / AGENT_CONFIG_CONSUMING_FILENAME).exists()


def test_consume_is_claim_first_under_racing_configure(
    tmp_path: Path, monkeypatch
) -> None:
    """A concurrent /api/agent/configure landing mid-consume must NOT be
    silently applied: the CLAIMED version is consumed, the racing version
    stays staged for the next start (round-2 H1 TOCTOU).

    This test injects a fresh config (B) during the read step. A claim-first
    implementation has already moved A -> consuming before the read, so B
    lands as a new agent_config.json and survives. A read-then-rename
    implementation would instead rename whatever is at agent_config.json
    (now B) to applied at the end, silently losing B — so this test FAILS
    on that broken ordering, unlike a plain sequential A-then-B test.
    """
    import agent.server.main as m

    _write_config(tmp_path, label="A", w_r=0.9, rho=0.9)
    orig = m.StartingWeightConfig.model_validate_json

    def racing(data, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Simulate configure racing in mid-consume (after the claim).
        if not (tmp_path / AGENT_CONFIG_FILENAME).exists():
            _write_config(tmp_path, label="B", w_r=0.3, rho=0.4)
        return orig(data, *args, **kwargs)

    monkeypatch.setattr(
        m.StartingWeightConfig, "model_validate_json", staticmethod(racing)
    )

    weights = m._consume_staged_config(tmp_path)
    # Claimed version A was consumed — NOT the racing B.
    assert weights is not None and abs(weights.w_r - 0.9) < 1e-9
    # Racing B survived as a fresh stage (NOT renamed to applied).
    assert (tmp_path / AGENT_CONFIG_FILENAME).exists()
    assert (tmp_path / AGENT_CONFIG_APPLIED_FILENAME).exists()

    monkeypatch.undo()
    # The next start picks up B (proves B was never consumed/lost).
    second = m._consume_staged_config(tmp_path)
    assert second is not None and abs(second.w_r - 0.3) < 1e-9


# --------------------------------------------------------------------------- #
# Task 4 — factory wiring (integration)
# --------------------------------------------------------------------------- #


def test_factory_cold_starts_with_promoted_weights_and_bets(tmp_path: Path) -> None:
    """End-to-end: a staged EXTREME config makes the factory cold-start the
    loop with those weights (deterministic primary assertion) and the agent
    actually places mock bets (behavioural secondary — EXTREME's ~85% bet
    rate on these cassettes makes >0 effectively certain in 120 ticks).

    cadence fast-path: tick_interval_seconds=0.0 → _resolve_decision_cadence
    yields timedelta(0) → the loop skips _real_sleep, so 120 ticks run with
    no real-time wait.
    """
    import asyncio
    from datetime import UTC, datetime, timedelta

    from agent.backtest.historical_fetcher import (
        MarketSnapshotProvider,
        load_all_cached_markets,
    )
    from agent.backtest.replay_runner import (
        _DeterministicSignalSource,
        _market_table_from_snapshots,
        _ReplaySettlementClient,
        _ReplayTickInputSource,
    )
    from agent.data._realtime_buffer import UtcClock
    from agent.server.main import (
        _SandboxChainAdapter,
        _build_production_loop_factory,
    )

    snaps = load_all_cached_markets(cache_dir=Path("agent/backtest/_cache"))
    assert snaps, "seeded cassettes must be present for this integration test"
    provider = MarketSnapshotProvider(snaps)
    mtable = _market_table_from_snapshots(snaps)
    clock = UtcClock()

    state_dir = tmp_path / "sandbox"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_config(state_dir, label="TEST-EXTREME", w_r=0.9, rho=0.9)

    factory = _build_production_loop_factory(
        state_dir=state_dir,
        chain_adapter=_SandboxChainAdapter(initial_breath=100.0),
        tick_input_source=_ReplayTickInputSource(
            provider=provider,
            signal_source=_DeterministicSignalSource(seed=0),
            selected_market_ids=provider.market_ids,
        ),
        settlement_client=_ReplaySettlementClient(provider=provider, clock=clock),
        market_resolver=mtable.get,
        wall_clock=clock,
        time_compression=1.0,
        tick_interval_seconds=0.0,
    )
    loop = factory()
    far = datetime.now(UTC) + timedelta(days=3650)
    summary = asyncio.run(loop.run(until=far, max_ticks=120))

    # Primary (deterministic): promoted weights were adopted on cold start.
    assert abs(loop.weights.w_r - 0.9) < 1e-9
    assert abs(loop.weights.rho - 0.9) < 1e-9
    # Config consumed.
    assert (state_dir / AGENT_CONFIG_APPLIED_FILENAME).exists()
    assert not (state_dir / AGENT_CONFIG_FILENAME).exists()
    # Secondary (behavioural): EXTREME conviction places real mock bets.
    assert summary.bets_placed > 0, "promoted EXTREME config should place bets"
