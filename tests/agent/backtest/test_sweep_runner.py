"""Tests for :mod:`agent.backtest.sweep_runner` — T-B-026.

Covers the brief's load-bearing contracts:

* 4-config sweep produces ``results.json`` + ``lifetimes.jsonl`` under
  the run directory.
* ``lifetimes.jsonl`` carries the 7 fields the backtest_validator gate
  consumes.
* Three sequential runs with identical inputs produce byte-identical
  ``results.json`` (modulo the deliberately-excluded timestamps), the
  ``determinism`` contract.
* The 4-config sweep completes in < 60 s on the test machine —
  guarded by ``pytest`` timeout per the brief.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    PricePoint,
)
from agent.backtest.replay_runner import DEFAULT_REPLAY_MAX_TICKS
from agent.backtest.sweep_runner import (
    DEFAULT_SWEEP_WEIGHTS,
    SweepConfig,
    SweepResult,
    new_run_id,
    run_sweep,
    run_sweep_sync,
)
from agent.core.state import Weights


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _two_synthetic_snapshots() -> list[MarketSnapshot]:
    """A pair of resolved tennis snapshots, identical shape to the replay tests."""
    return [
        MarketSnapshot(
            market_id="7100001",
            slug="atp-sweep-alpha",
            end_date_iso="2026-05-04T17:00:00+00:00",
            resolution_ts_iso="2026-05-03T20:00:00+00:00",
            outcome="yes",
            winning_price=1.0,
            liquidity_cap_usd=20.0,
            price_ledger=[
                PricePoint(ts="2026-05-01T00:00:00+00:00", mid_price=0.5),
                PricePoint(ts="2026-05-03T20:00:00+00:00", mid_price=1.0),
            ],
        ),
        MarketSnapshot(
            market_id="7100002",
            slug="atp-sweep-bravo",
            end_date_iso="2026-05-05T17:00:00+00:00",
            resolution_ts_iso="2026-05-04T20:00:00+00:00",
            outcome="no",
            winning_price=1.0,
            liquidity_cap_usd=15.0,
            price_ledger=[
                PricePoint(ts="2026-05-01T00:00:00+00:00", mid_price=0.5),
                PricePoint(ts="2026-05-04T20:00:00+00:00", mid_price=1.0),
            ],
        ),
    ]


def _short_sweep_config(tmp_path: Path) -> SweepConfig:
    """4-config sweep with a small tick cap so the test budget is tight."""
    return SweepConfig(
        starting_weights=DEFAULT_SWEEP_WEIGHTS,
        seed=11,
        cache_dir=tmp_path / "_cache_unused",
        output_root=tmp_path / "out",
        max_ticks=8,  # ≤ 60 s budget on the test harness
    )


# --------------------------------------------------------------------------- #
# Smoke + artefact contract
# --------------------------------------------------------------------------- #


def test_run_sweep_writes_results_and_lifetimes(tmp_path: Path) -> None:
    """4-config sweep produces results.json + lifetimes.jsonl + per-config state."""
    cfg = _short_sweep_config(tmp_path)
    result = asyncio.run(run_sweep(cfg, snapshots=_two_synthetic_snapshots()))

    assert result.results_path.exists()
    assert result.lifetimes_path.exists()
    assert result.output_dir.is_dir()
    assert len(result.metrics) == len(DEFAULT_SWEEP_WEIGHTS)
    assert result.results_path.parent == result.output_dir

    # results.json shape
    payload = json.loads(result.results_path.read_text(encoding="utf-8"))
    assert payload["configs_run"] == len(DEFAULT_SWEEP_WEIGHTS)
    assert payload["run_id"] == result.run_id
    assert isinstance(payload["results"], list)
    assert len(payload["results"]) == len(DEFAULT_SWEEP_WEIGHTS)

    # lifetimes.jsonl — one line per replay, each carrying the 7
    # backtest_validator fields.
    lines = result.lifetimes_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(DEFAULT_SWEEP_WEIGHTS)
    required = {
        "archetype",
        "lifetime_days",
        "death_cause",
        "terminal_afterglow",
        "apprenticeship_failures",
        "deepen_count",
        "donations_received",
    }
    for line in lines:
        row = json.loads(line)
        assert required.issubset(row.keys())
        # Type guards mirroring the gate's required types.
        assert isinstance(row["archetype"], str)
        assert isinstance(row["lifetime_days"], (int, float))
        assert isinstance(row["death_cause"], str)
        assert isinstance(row["terminal_afterglow"], bool)
        assert isinstance(row["apprenticeship_failures"], int)
        assert isinstance(row["deepen_count"], int)
        assert isinstance(row["donations_received"], (int, float))


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def _stable_slice(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip the timestamps + run_id so the determinism check stays focused.

    ``started_at`` / ``finished_at`` are inherently non-deterministic (wall
    clock); ``run_id`` is derived from inputs so it's deterministic, but
    we strip it too because the test rewrites it under different sweep
    invocations.
    """
    return {k: v for k, v in payload.items() if k not in ("started_at", "finished_at")}


def test_determinism_3x_identical(tmp_path: Path) -> None:
    """3 sequential runs with the same inputs → byte-identical results (mod timestamps)."""
    cfg = SweepConfig(
        starting_weights=DEFAULT_SWEEP_WEIGHTS,
        seed=99,
        cache_dir=tmp_path / "_cache_unused",
        output_root=tmp_path / "out",
        max_ticks=6,
        run_id="determinism-pin",  # pinned so the artefact dir is stable
    )
    sliced_payloads: list[dict[str, Any]] = []
    lifetimes_files: list[bytes] = []
    for _ in range(3):
        # Each iteration writes to the SAME dir (deterministic run_id) and
        # overwrites both artefacts atomically.
        snaps = _two_synthetic_snapshots()
        result = asyncio.run(run_sweep(cfg, snapshots=snaps))
        payload = json.loads(result.results_path.read_text(encoding="utf-8"))
        sliced_payloads.append(_stable_slice(payload))
        lifetimes_files.append(result.lifetimes_path.read_bytes())

    # All three payloads structurally identical (minus timestamps).
    assert sliced_payloads[0] == sliced_payloads[1] == sliced_payloads[2]
    # lifetimes.jsonl IS byte-identical (no timestamps in the lifetime row).
    assert lifetimes_files[0] == lifetimes_files[1] == lifetimes_files[2]


def test_new_run_id_is_a_pure_function() -> None:
    """Same (seed, configs) → same run_id; different seed → different run_id."""
    a = new_run_id(seed=42, configs=DEFAULT_SWEEP_WEIGHTS)
    b = new_run_id(seed=42, configs=DEFAULT_SWEEP_WEIGHTS)
    c = new_run_id(seed=43, configs=DEFAULT_SWEEP_WEIGHTS)
    assert a == b
    assert a != c
    assert a.startswith("sweep-")


# --------------------------------------------------------------------------- #
# Performance ceiling
# --------------------------------------------------------------------------- #


def test_4_config_sweep_completes_under_60s(tmp_path: Path) -> None:
    """Brief acceptance: 4-config sweep (no-llm) completes in < 60 s.

    Runs at the DEFAULT max_ticks (240 → 10 simulated days at 60-min
    cadence) so the perf budget is exercised end-to-end at the same
    scale a production sweep hits.
    """
    cfg = SweepConfig(
        starting_weights=DEFAULT_SWEEP_WEIGHTS,
        seed=11,
        cache_dir=tmp_path / "_cache_unused",
        output_root=tmp_path / "out",
        # max_ticks defaults to DEFAULT_REPLAY_MAX_TICKS (240) — same
        # as the brief's "default sweep" scale.
    )
    assert cfg.max_ticks == DEFAULT_REPLAY_MAX_TICKS
    t0 = time.monotonic()
    asyncio.run(run_sweep(cfg, snapshots=_two_synthetic_snapshots()))
    elapsed = time.monotonic() - t0
    assert elapsed < 60.0, f"sweep took {elapsed:.2f}s (budget 60s per brief)"


# --------------------------------------------------------------------------- #
# CLI sync wrapper
# --------------------------------------------------------------------------- #


def test_run_sweep_sync_returns_sweep_result(tmp_path: Path) -> None:
    cfg = _short_sweep_config(tmp_path)
    result = run_sweep_sync(cfg, snapshots=_two_synthetic_snapshots())
    assert isinstance(result, SweepResult)
    assert result.results_path.exists()


def test_run_sweep_empty_weights_raises(tmp_path: Path) -> None:
    cfg = SweepConfig(
        starting_weights=(),
        cache_dir=tmp_path,
        output_root=tmp_path / "out",
    )
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(run_sweep(cfg, snapshots=_two_synthetic_snapshots()))
