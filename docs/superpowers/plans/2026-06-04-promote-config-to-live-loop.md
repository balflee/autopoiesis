# PROMOTE backtest config → live loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the workshop→PROMOTE→live dogfood pipeline so the live agent runs with the backtest-winning weight config the operator promoted, instead of the conservative `PHASE2_DEFAULT` it currently ignores.

**Architecture:** The dashboard PROMOTE button already calls `POST /api/agent/configure`, which writes the chosen `StartingWeightConfig` to `<state_dir>/agent_config.json`. The gap: the production loop factory (`agent/server/main.py::_build_production_loop_factory._factory`) never reads that file — it always cold-starts with `_phase2_default_weights()` (rho=0.25, which dampens Kelly stakes below the $5 min-bet floor → every tick NO_BET). We close the gap by making each `/api/agent/start` **consume** a staged `agent_config.json`: project it to the canonical `Weights` 6-vector, reset the prior agent life (snapshot + JSONL streams) so reconstruction cold-starts with the promoted weights, and rename the config to `agent_config.applied.json` so a plain restart (no fresh promote) reconstructs the running life normally.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest. All changes in `agent/server/main.py` + a new test file. No new dependencies. No schema bump (`agent_config.json` already holds a `StartingWeightConfig`).

---

## Background facts (verified against current code)

- `agent/server/main.py:113` — `AGENT_CONFIG_FILENAME: Final[str] = "agent_config.json"`.
- `agent/server/main.py:593-614` — `_write_agent_config_atomic(*, state_dir, config: StartingWeightConfig)` writes `config.model_dump_json(indent=2)` to `<state_dir>/agent_config.json`. So the on-disk shape is a `StartingWeightConfig` (scalar form: `{label, w_r, w_s, alpha, beta, rho}`).
- `agent/server/models.py:184` — `StartingWeightConfig.to_weights() -> Weights` projects the scalar form to the canonical 6-vector.
- `agent/runtime/sandbox_phase2_loop.py:760` — `SandboxPhase2Loop.__init__(..., initial_weights: Weights | None = None, ...)`. When `None` it falls back to `_phase2_default_weights()` (line 844-845).
- `agent/runtime/sandbox_phase2_loop.py:1306-1322` — reconstruction: `cold_start = not snapshot_path.exists()`. If `agent_state.json` exists, weights are loaded FROM the snapshot (overriding `initial_weights`). So promoted weights only take effect on a **cold start** (no snapshot on disk).
- `agent/server/main.py:1794-1856` — `_factory()` (inside `_build_production_loop_factory`) constructs the loop. It currently constructs `SandboxStateWriter`, appends a `loop_boot` row, builds the executor, and calls `SandboxPhase2Loop(...)` WITHOUT `initial_weights`. `state_dir` is closed over from the enclosing factory builder.
- The runtime JSONL/snapshot files the loop writes under `state_dir` (from `agent/data/sandbox_state.py`): `agent_state.json` (`SNAPSHOT_FILENAME`), `open_bets.jsonl` (`OPEN_BETS_FILENAME`), `settled_bets.jsonl` (`SETTLED_BETS_FILENAME`), `decisions.jsonl` (`DECISIONS_FILENAME`), `reflections.jsonl` (`REFLECTIONS_FILENAME`), `proposals.jsonl` (`PROPOSALS_FILENAME`).
- `agent/server/main.py:56-62` already imports `DECISIONS_FILENAME, PROPOSALS_FILENAME, REFLECTIONS_FILENAME, SNAPSHOT_FILENAME, SandboxStateWriter` from `agent.data.sandbox_state`. `OPEN_BETS_FILENAME` + `SETTLED_BETS_FILENAME` are NOT yet imported there.
- `StartingWeightConfig` is imported in `main.py` (line 102, from `agent.server.models`).
- `Weights` is NOT currently imported in `main.py` (only `ActionKind, Phase` from `agent.core.state`). The factory passes `initial_weights` as `Weights | None`; a type annotation needs the import.

## File Structure

- **Modify:** `agent/server/main.py`
  - Add import: `OPEN_BETS_FILENAME`, `SETTLED_BETS_FILENAME` to the existing `agent.data.sandbox_state` import block; add `Weights` to the `agent.core.state` import.
  - Add module constant `AGENT_CONFIG_APPLIED_FILENAME`.
  - Add helper `_reset_durable_life(state_dir: Path) -> None` — moves the 6 runtime state files into `<state_dir>/_prev_life/` (one-generation backup) so the next reconstruction cold-starts.
  - Add helper `_consume_staged_config(state_dir: Path) -> Weights | None` — reads + projects + resets + renames.
  - Modify `_factory()` to call `_consume_staged_config(state_dir)` and pass the result as `initial_weights=` to `SandboxPhase2Loop(...)`.
- **Create:** `tests/agent/server/test_promote_config_to_loop.py` — unit tests for the two helpers + an integration test that a staged EXTREME config makes the loop cold-start with those weights and place bets.

## Self-contained changes principle

The two helpers are pure filesystem functions (no app/loop coupling) → unit-testable in isolation. The factory change is a 2-line wiring edit. The configure endpoint is UNCHANGED (it already writes `agent_config.json` correctly). No behavior change when no `agent_config.json` is staged (helper returns `None` → loop uses default weights → identical to today).

---

### Task 1: Imports + applied-config constant

**Files:**
- Modify: `agent/server/main.py` (import block ~line 52-62, constant block ~line 113)

- [ ] **Step 1: Add `Weights` to the core.state import**

Find:
```python
from agent.core.state import ActionKind, Phase
```
Replace with:
```python
from agent.core.state import ActionKind, Phase, Weights
```

- [ ] **Step 2: Add the two filename constants to the sandbox_state import**

Find:
```python
from agent.data.sandbox_state import (
    DECISIONS_FILENAME,
    PROPOSALS_FILENAME,
    REFLECTIONS_FILENAME,
    SNAPSHOT_FILENAME,
    SandboxStateWriter,
)
```
Replace with:
```python
from agent.data.sandbox_state import (
    DECISIONS_FILENAME,
    OPEN_BETS_FILENAME,
    PROPOSALS_FILENAME,
    REFLECTIONS_FILENAME,
    SETTLED_BETS_FILENAME,
    SNAPSHOT_FILENAME,
    SandboxStateWriter,
)
```

- [ ] **Step 3: Add the applied-config filename constant**

Find:
```python
AGENT_CONFIG_FILENAME: Final[str] = "agent_config.json"
```
Add immediately AFTER its docstring block (keep the existing docstring intact; insert this new constant after it):
```python
AGENT_CONFIG_APPLIED_FILENAME: Final[str] = "agent_config.applied.json"
"""Renamed-to marker after ``/api/agent/start`` consumes a staged config.

PROMOTE semantics: a fresh ``agent_config.json`` (written by
``/api/agent/configure``) means "start a NEW agent life with these
backtest-winning weights". Once a start consumes it, we rename it to
this applied marker so a plain restart (no fresh promote) reconstructs
the running life normally instead of resetting it again.
"""
```

- [ ] **Step 4: Verify syntax**

Run: `MSYS_NO_PATHCONV=1 python -c "import ast; ast.parse(open('agent/server/main.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add agent/server/main.py
git commit -m "prod loop: imports + applied-config constant for PROMOTE pipeline"
```

---

### Task 2: `_reset_durable_life` helper

**Files:**
- Modify: `agent/server/main.py` (add helper near `_write_agent_config_atomic`, ~after line 614)
- Test: `tests/agent/server/test_promote_config_to_loop.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/server/test_promote_config_to_loop.py`:
```python
"""Tests for the PROMOTE pipeline: backtest config → live loop cold-start.

Covers _reset_durable_life + _consume_staged_config + the factory wiring
that lets /api/agent/start adopt a promoted StartingWeightConfig.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.server.main import (
    AGENT_CONFIG_APPLIED_FILENAME,
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


def test_reset_durable_life_moves_all_state_files(tmp_path: Path) -> None:
    for name in _LIFE_FILES:
        (tmp_path / name).write_text("prior-life\n", encoding="utf-8")

    _reset_durable_life(tmp_path)

    # All six removed from the live root.
    for name in _LIFE_FILES:
        assert not (tmp_path / name).exists(), f"{name} should be moved out"
    # And preserved one-generation under _prev_life/.
    prev = tmp_path / "_prev_life"
    assert prev.is_dir()
    for name in _LIFE_FILES:
        assert (prev / name).read_text(encoding="utf-8") == "prior-life\n"


def test_reset_durable_life_is_noop_when_nothing_present(tmp_path: Path) -> None:
    # No state files → no error, no _prev_life dir churn required.
    _reset_durable_life(tmp_path)  # must not raise
    # agent_config.json is NOT a life-state file — must be left untouched.
    (tmp_path / AGENT_CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    _reset_durable_life(tmp_path)
    assert (tmp_path / AGENT_CONFIG_FILENAME).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/test_promote_config_to_loop.py -x -q`
Expected: FAIL with `ImportError: cannot import name '_reset_durable_life'`

- [ ] **Step 3: Write minimal implementation**

In `agent/server/main.py`, add immediately AFTER `_write_agent_config_atomic` (ends ~line 614):
```python
_DURABLE_LIFE_FILENAMES: Final[tuple[str, ...]] = (
    SNAPSHOT_FILENAME,
    OPEN_BETS_FILENAME,
    SETTLED_BETS_FILENAME,
    DECISIONS_FILENAME,
    REFLECTIONS_FILENAME,
    PROPOSALS_FILENAME,
)
"""The six runtime files one agent life writes under ``state_dir``.

Moved aside by :func:`_reset_durable_life` when a PROMOTE starts a fresh
life so :meth:`SandboxPhase2Loop._reconstruct_from_disk` sees no snapshot
and cold-starts with the promoted weights.
"""


def _reset_durable_life(state_dir: Path) -> None:
    """Move the current agent life's state files into ``state_dir/_prev_life``.

    PROMOTE = "run a NEW agent life with the backtest-winning weights".
    The loop's reconstruction cold-starts ONLY when ``agent_state.json`` is
    absent (see :meth:`SandboxPhase2Loop._reconstruct_from_disk`), so a
    fresh promote must clear the prior snapshot + JSONL streams. We MOVE
    (not delete) into a single-generation ``_prev_life/`` backup so the
    prior life is recoverable for one cycle. ``agent_config.json`` is NOT a
    life-state file and is intentionally left untouched.
    """
    prev = state_dir / "_prev_life"
    moved = False
    for name in _DURABLE_LIFE_FILENAMES:
        src = state_dir / name
        if not src.exists():
            continue
        if not moved:
            prev.mkdir(parents=True, exist_ok=True)
            moved = True
        os.replace(src, prev / name)
    if moved:
        logger.info(
            "prod loop: reset durable life — moved prior state to %s "
            "(PROMOTE fresh-life cold-start)",
            prev,
        )
```

(`os`, `logger`, `Final`, `Path` are already imported at module top.)

- [ ] **Step 4: Run test to verify it passes**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/test_promote_config_to_loop.py -x -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add agent/server/main.py tests/agent/server/test_promote_config_to_loop.py
git commit -m "prod loop: _reset_durable_life helper (PROMOTE fresh-life)"
```

---

### Task 3: `_consume_staged_config` helper

**Files:**
- Modify: `agent/server/main.py` (add helper after `_reset_durable_life`)
- Test: `tests/agent/server/test_promote_config_to_loop.py` (append)

- [ ] **Step 1: Write the failing test (append to the test file)**

Append to `tests/agent/server/test_promote_config_to_loop.py`:
```python
def _write_config(state_dir: Path, *, label: str, w_r: float, rho: float) -> None:
    # Mirrors _write_agent_config_atomic's on-disk shape (a StartingWeightConfig).
    cfg = {
        "label": label,
        "w_r": w_r,
        "w_s": round(1.0 - w_r, 6),
        "alpha": 0.9,
        "beta": 1.0,
        "rho": rho,
    }
    (state_dir / AGENT_CONFIG_FILENAME).write_text(json.dumps(cfg), encoding="utf-8")


def test_consume_staged_config_returns_none_when_absent(tmp_path: Path) -> None:
    from agent.server.main import _consume_staged_config

    assert _consume_staged_config(tmp_path) is None


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
    # 2. Prior life reset (snapshot gone from live root → next start cold-starts).
    assert not (tmp_path / "agent_state.json").exists()
    assert (tmp_path / "_prev_life" / "agent_state.json").exists()
    # 3. Config renamed to applied marker (plain restart won't re-reset).
    assert not (tmp_path / AGENT_CONFIG_FILENAME).exists()
    assert (tmp_path / AGENT_CONFIG_APPLIED_FILENAME).exists()


def test_consume_staged_config_second_call_is_noop(tmp_path: Path) -> None:
    from agent.server.main import _consume_staged_config

    _write_config(tmp_path, label="X", w_r=0.7, rho=0.6)
    first = _consume_staged_config(tmp_path)
    assert first is not None
    # No fresh promote → applied marker present, live config absent → None.
    second = _consume_staged_config(tmp_path)
    assert second is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/test_promote_config_to_loop.py -x -q`
Expected: FAIL with `ImportError: cannot import name '_consume_staged_config'`

- [ ] **Step 3: Write minimal implementation**

In `agent/server/main.py`, add immediately AFTER `_reset_durable_life`:
```python
def _consume_staged_config(state_dir: Path) -> Weights | None:
    """Adopt a promoted ``agent_config.json`` for a fresh agent life.

    Completes the workshop→PROMOTE→live dogfood pipeline. The dashboard
    PROMOTE button → ``POST /api/agent/configure`` → ``_write_agent_config_atomic``
    stages a :class:`StartingWeightConfig` at ``<state_dir>/agent_config.json``.
    Each ``/api/agent/start`` calls THIS to:

      1. read + project the staged config to the canonical
         :class:`agent.core.state.Weights` 6-vector,
      2. :func:`_reset_durable_life` so reconstruction cold-starts with the
         promoted weights (a live snapshot would otherwise override them),
      3. rename the config to :data:`AGENT_CONFIG_APPLIED_FILENAME` so a
         plain restart (no fresh promote) reconstructs the running life.

    Returns the promoted :class:`Weights`, or ``None`` when no fresh config
    is staged (→ caller passes ``initial_weights=None`` → the loop's
    ``_phase2_default_weights`` default, i.e. unchanged behaviour).
    """
    cfg_path = state_dir / AGENT_CONFIG_FILENAME
    if not cfg_path.exists():
        return None
    raw = cfg_path.read_text(encoding="utf-8")
    config = StartingWeightConfig.model_validate_json(raw)
    weights = config.to_weights()
    _reset_durable_life(state_dir)
    os.replace(cfg_path, state_dir / AGENT_CONFIG_APPLIED_FILENAME)
    logger.info(
        "prod loop: adopted promoted config label=%r (w_r=%.3f rho=%.3f) "
        "— fresh life cold-start",
        config.label, weights.w_r, weights.rho,
    )
    return weights
```

- [ ] **Step 4: Run test to verify it passes**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/test_promote_config_to_loop.py -x -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add agent/server/main.py tests/agent/server/test_promote_config_to_loop.py
git commit -m "prod loop: _consume_staged_config helper (PROMOTE pipeline)"
```

---

### Task 4: Wire `_consume_staged_config` into the loop factory

**Files:**
- Modify: `agent/server/main.py` (`_factory` body, ~line 1794-1856)
- Test: `tests/agent/server/test_promote_config_to_loop.py` (append integration test)

- [ ] **Step 1: Write the failing integration test (append)**

Append to `tests/agent/server/test_promote_config_to_loop.py`:
```python
def test_factory_cold_starts_with_promoted_weights_and_bets(tmp_path: Path) -> None:
    """End-to-end: stage an aggressive config → factory cold-starts the loop
    with those weights → the agent actually places mock bets (vs the
    conservative default which falls below the min-bet floor)."""
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
    # Stage an aggressive promoted config (workshop EXTREME shape).
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
    summary = asyncio.run(loop.run(until=far, max_ticks=60))

    # Config consumed → renamed to applied; fresh-life cold start happened.
    assert (state_dir / AGENT_CONFIG_APPLIED_FILENAME).exists()
    assert not (state_dir / AGENT_CONFIG_FILENAME).exists()
    # The aggressive weights clear the min-bet floor → real mock bets land.
    assert summary.bets_placed > 0, "promoted EXTREME config should place bets"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/test_promote_config_to_loop.py::test_factory_cold_starts_with_promoted_weights_and_bets -x -q`
Expected: FAIL — `assert (state_dir / AGENT_CONFIG_APPLIED_FILENAME).exists()` fails (factory does not yet consume the config), or `bets_placed == 0`.

- [ ] **Step 3: Wire the factory**

In `agent/server/main.py`, inside `_factory()`, find the writer construction:
```python
    def _factory() -> SandboxPhase2Loop:
        # Shared single-writer per process — both executor + loop write
        # through THIS instance (single-writer invariant locked by
        # SandboxStateWriter docstring). Constructing the writer first
        # mkdirs ``state_dir`` so the T-B-043 boot-marker append below
        # cannot hit a missing-directory race.
        writer = SandboxStateWriter(root=state_dir)
```
Replace with:
```python
    def _factory() -> SandboxPhase2Loop:
        # PROMOTE pipeline (workshop backtest → /api/agent/configure → live):
        # consume any staged agent_config.json BEFORE constructing the writer
        # so the fresh-life reset clears the prior snapshot/streams and the
        # loop cold-starts with the promoted weights. Returns None when no
        # fresh config is staged → default-weight behaviour (unchanged).
        promoted_weights = _consume_staged_config(state_dir)
        # Shared single-writer per process — both executor + loop write
        # through THIS instance (single-writer invariant locked by
        # SandboxStateWriter docstring). Constructing the writer first
        # mkdirs ``state_dir`` so the T-B-043 boot-marker append below
        # cannot hit a missing-directory race.
        writer = SandboxStateWriter(root=state_dir)
```

Then find the loop construction's cold-start hints:
```python
            initial_phase=Phase.PHASE_2_APPRENTICE,
            # Cold-start hints — overridden on the first tick by the chain
            # adapter's read_breath per the reconstruction step 4 contract.
            initial_breath=_SANDBOX_COLD_START_BREATH_USD,
            initial_bankroll_usd=_SANDBOX_COLD_START_BREATH_USD,
```
Replace with:
```python
            initial_phase=Phase.PHASE_2_APPRENTICE,
            # PROMOTE pipeline: a promoted backtest config (consumed above)
            # cold-starts the loop with operator-chosen weights; None →
            # the loop's _phase2_default_weights default (unchanged path).
            initial_weights=promoted_weights,
            # Cold-start hints — overridden on the first tick by the chain
            # adapter's read_breath per the reconstruction step 4 contract.
            initial_breath=_SANDBOX_COLD_START_BREATH_USD,
            initial_bankroll_usd=_SANDBOX_COLD_START_BREATH_USD,
```

- [ ] **Step 4: Run the integration test**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/test_promote_config_to_loop.py -x -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add agent/server/main.py tests/agent/server/test_promote_config_to_loop.py
git commit -m "prod loop: consume promoted config in factory (PROMOTE → live cold-start)"
```

---

### Task 5: Full regression + local PROMOTE round-trip verify

**Files:**
- Verify only (no new prod code). Optional throwaway script — delete before commit.

- [ ] **Step 1: Run the full server + backtest suites (no regression)**

Run: `MSYS_NO_PATHCONV=1 python -m pytest tests/agent/server/ tests/agent/backtest/ -q`
Expected: all pass (sprint_11/12/13 backend tests + the 6 new PROMOTE tests). No failures.

- [ ] **Step 2: Local end-to-end PROMOTE round-trip via the FastAPI app**

Create `_verify_promote.py` at repo root:
```python
"""Local PROMOTE round-trip: configure(EXTREME) → start consumes it → bets fire."""
import os, asyncio, tempfile, json
from pathlib import Path

os.environ["SANDBOX_STATE_DIR"] = str((Path(tempfile.mkdtemp()) / "sandbox").resolve())
os.environ["BACKTEST_CACHE_DIR"] = str(Path("agent/backtest/_cache").resolve())
os.environ["BACKTEST_OUTPUT_ROOT"] = str((Path(tempfile.mkdtemp()) / "runs").resolve())
os.environ["DASHBOARD_API_TOKEN"] = "local-verify-token"

from fastapi.testclient import TestClient
from agent.server.main import _build_default_app

app = _build_default_app()
c = TestClient(app)
H = {"Authorization": "Bearer local-verify-token"}

# PROMOTE an aggressive backtest-winner config.
r = c.post("/api/agent/configure", headers=H, json={
    "starting_weights": {"label": "TEST-EXTREME", "w_r": 0.9, "w_s": 0.1,
                          "alpha": 0.9, "beta": 1.0, "rho": 0.9},
})
print("configure:", r.status_code, r.json().get("persisted_path"))
assert r.status_code == 202

state_dir = Path(os.environ["SANDBOX_STATE_DIR"])
assert (state_dir / "agent_config.json").exists(), "config staged"

# Start consumes the staged config (cold-start with promoted weights).
r = c.post("/api/agent/start", headers=H, json={})
print("start:", r.status_code, r.json())
assert r.status_code in (200, 202)

# Give the background loop a moment to consume + tick.
import time; time.sleep(2)
assert (state_dir / "agent_config.applied.json").exists(), "config consumed → applied"
print("PASS: configure → start consumed promoted config; applied marker present")
```

Run: `MSYS_NO_PATHCONV=1 python _verify_promote.py`
Expected: `configure: 202 ...`, `start: ...`, `PASS: ...`

- [ ] **Step 3: Delete the throwaway script**

```bash
rm _verify_promote.py
```

- [ ] **Step 4: Commit (if any state/doc changes remain)**

```bash
git status --short
# only the plan doc + (already-committed) source; nothing else expected
```

---

### Task 6: Push + deploy + live PROMOTE demo

**Files:** none (ops only).

- [ ] **Step 1: Confirm balflee git identity, then push**

```bash
gh auth switch --user balflee --hostname github.com
git push origin dev
```
Expected: `dev -> dev` updated. Railway auto-redeploys from the dev push.

- [ ] **Step 2: Wait for Railway redeploy, confirm fresh uptime**

Run: `curl -s https://autopoiesis-api-production.up.railway.app/healthz`
Expected: `{"status":"ok","uptime_s":<small>,...}` — uptime resets to a low number after redeploy (poll until < 120).

- [ ] **Step 3: Operator runs the live PROMOTE demo**

This is the operator-facing payoff (the demo arc):
1. In the workshop, run a backtest sweep with a few configs; note the winner (highest net PnL / win rate).
2. Click PROMOTE on the winning row → the dashboard calls `POST /api/agent/configure`.
3. Restart the agent: `POST /api/agent/stop` then `POST /api/agent/start`.
4. Watch `GET /api/agent/status` (`current_weights` now matches the promoted config, not `w_r=0.65` default) and the SSE `/api/state/stream` (BET rows appear with real market_ids, breath moves with wins/losses).

Note: tick cadence is still governed by `PROD_LOOP_TICK_INTERVAL_SECONDS` / `PROD_LOOP_TIME_COMPRESSION` (Railway env, default 60s/tick). Lower them in the Railway UI for a faster live demo. The on-chain death→Tombstone NFT path remains a separate follow-up (`PROD_LOOP_CHAIN_ADAPTER_KIND=rh_chain` + web3 deps + `RH_CHAIN_*` env).

---

## Self-Review

**1. Spec coverage:**
- "live agent runs backtest-winning config not default" → Task 4 wires `initial_weights` from the consumed config. ✓
- "config comes from PROMOTE (workshop backtest)" → unchanged configure endpoint writes `agent_config.json`; Task 3 reads it; Task 6 step 3 documents the operator flow. ✓
- "promoted config must override the prior life's snapshot" → Task 2 `_reset_durable_life` + Task 3 reset call → cold-start. ✓
- "plain restart shouldn't keep resetting" → Task 3 renames to applied marker; Task 3 second-call test. ✓
- "no behavior change when nothing promoted" → helper returns None → default weights; Task 3 `returns_none` test + Task 5 full regression. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows full code. ✓

**3. Type consistency:** `_reset_durable_life(state_dir: Path) -> None`, `_consume_staged_config(state_dir: Path) -> Weights | None`, `_DURABLE_LIFE_FILENAMES` used in both Task 2 def and Task 1 imports. `AGENT_CONFIG_APPLIED_FILENAME` defined Task 1, used Tasks 3+4 tests. `StartingWeightConfig.model_validate_json` + `.to_weights()` match models.py. `SandboxPhase2Loop(initial_weights=...)` matches the ctor param at sandbox_phase2_loop.py:760. ✓

## What this plan does NOT do (out of scope)

- On-chain death → real Tombstone NFT (rh_chain adapter: web3 deps in Dockerfile + `RH_CHAIN_*` env + `PROD_LOOP_CHAIN_ADAPTER_KIND=rh_chain`).
- Tick-cadence / time-compression env tuning on Railway (operator sets `PROD_LOOP_*` in the UI).
- Cassette re-seed with real (non-flat) Polymarket price ledgers.
- Dashboard PROMOTE UX changes (the button + `configureAgent` already exist and work).
