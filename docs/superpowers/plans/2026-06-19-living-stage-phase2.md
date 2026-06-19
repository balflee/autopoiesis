# Living Stage — Phase 2 (Reincarnation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `LiveIncarnationSupervisor` so the running mock-bet agent, when it dies (breath→0), is reborn as a fresh incarnation — carrying its learned weights, accumulating the divine treasury + a death lineage across lives — all gated behind `SANDBOX_REINCARNATION=1` (default OFF = today's single-life path byte-identical).

**Architecture:** The supervisor implements the existing `LoopHandle` protocol (`async run()`), so `AgentRunner` is UNCHANGED — when `SANDBOX_REINCARNATION=1`, the production loop factory is a `_supervisor_factory()` that constructs a FRESH `LiveIncarnationSupervisor` per `AgentRunner.start()` (per the `LoopFactoryProto` per-call-cleanliness contract), instead of a bare `SandboxPhase2Loop`. The shared WeightUpdater + advisor are constructed INSIDE `_supervisor_factory()` so they are shared across the incarnations of ONE run but NOT across independent `/start` cycles. The supervisor's `run()` is a respawn loop cloned from `agent/backtest/survival_season.py:run_survival_season`: build a fresh incarnation → `await loop.run()` (returns a `RunSummary` only on death; raises `CancelledError` on operator stop, which propagates out untouched) → carry `loop.weights` into the next life → repeat to `max_incarnations`. Each incarnation gets a **fresh chain_adapter** (breath reset — reusing the dead adapter would re-die instantly) and `incarnation_number=idx` stamped into every snapshot + death record. **State model (supersedes the spec's subdir+mirror-up with a simpler equivalent):** all incarnations write to the SAME root `state_dir` the dashboard already reads; the supervisor RESETS the per-life streams between lives (so cold-start is clean) but PRESERVES the cumulative divine streams (`gods_treasury.jsonl`, `deaths.jsonl`) + `incarnation_manifest.json` — so the treasury grows and the lineage accumulates across deaths, live. A manifest persists `{run_id, current_incarnation_idx, carry_weights_hash, max_incarnations}` atomically each transition for crash recovery.

**Tech Stack:** Python 3.11 + asyncio + Pydantic v2; pytest. Repo root is the `code/` subdirectory.

**Scope:** Phase 2 only. Builds on Phase 1 (shipped on branch `living-stage-phase1`: the divine economy is wired behind `SANDBOX_DIVINE_ECONOMY`; `TributeRecord`/`TitheRecord`/`DeathRecord` + `gods_treasury.jsonl`/`deaths.jsonl` streams + `incarnation_number` snapshot field + the `/living` page + the dashboard loader that reads the divine streams all exist). Spec: `docs/superpowers/specs/2026-06-18-living-stage-mockbet-display-design.md` §4.2/§6/§7/§8.

**Conventions for every commit:** author **balflee** (`256016480+balflee@users.noreply.github.com`); never bypass gitleaks; prod LLM is Gemini only; do not touch `scripts/run_v3_ai.py`; the default-OFF (`SANDBOX_REINCARNATION` unset) path must stay byte-identical to Phase 1's single-life loop. Run tests from `code/`.

---

## File Structure

**Create:**
- `code/agent/runtime/incarnation_supervisor.py` — `LiveIncarnationSupervisor` (the respawn LoopHandle) + `IncarnationManifest` (Pydantic) + manifest read/write helpers.
- `code/tests/agent/runtime/test_incarnation_supervisor.py` — supervisor respawn / carry / cap / cancel / fresh-adapter / stream-reset tests.
- `code/tests/agent/runtime/test_incarnation_manifest.py` — manifest round-trip + crash-recovery tests.
- `code/tests/agent/server/test_reincarnation_wiring.py` — factory-returns-supervisor-when-flag-on / single-loop-when-off.

**Modify:**
- `code/agent/runtime/sandbox_phase2_loop.py` — add `incarnation_number: int = 0` ctor param; stamp it at all 4 `AgentStateSnapshot` construction sites.
- `code/agent/server/main.py` — extract `_build_one_incarnation_loop(...)` from the `_factory` closure (keep the 0-arg factory as a thin wrapper); add `SANDBOX_REINCARNATION` read in `_build_default_app`; when ON, build the supervisor and pass `lambda: supervisor` as the `loop_factory` to `AgentRunner`.

**No dashboard changes needed** — Phase 1's loader already reads `deaths.jsonl` → `incarnation_lineage` and folds the cumulative treasury; the Lineage zone (Z5) + Treasury (Z2) go live across deaths automatically once the supervisor produces multi-incarnation `deaths.jsonl` rows. (Task P6 is an integration test proving this, not new UI code.)

---

## Shared Contract (locked names)

**New env flag:** `SANDBOX_REINCARNATION` — `"1"` makes the production loop factory return a `LiveIncarnationSupervisor`. Default unset/OFF = the factory returns a single `SandboxPhase2Loop` (today's path, byte-identical). Read with the established pattern: `os.environ.get("SANDBOX_REINCARNATION") == "1"`.

**`LiveIncarnationSupervisor`** (`agent/runtime/incarnation_supervisor.py`) — implements `LoopHandle`:
```python
class LiveIncarnationSupervisor:
    def __init__(
        self,
        *,
        build_incarnation: Callable[..., SandboxPhase2Loop],  # (incarnation_idx, chain_adapter, initial_weights, incarnation_number) -> loop
        build_chain_adapter: Callable[[], SandboxLoopChainAdapter],  # fresh adapter per life
        state_dir: Path,
        max_incarnations: int = 10,
    ) -> None: ...
    async def run(self) -> RunSummary: ...   # LoopHandle.run — runs until cancelled or max_incarnations exhausted
```

**Per-incarnation builder** (`agent/server/main.py`): `_build_one_incarnation_loop(*, incarnation_idx, state_dir, chain_adapter, initial_weights, shared_weight_updater, shared_advisor, <existing live deps: tick_input_source/settlement_client/market_resolver/wall_clock/decision_cadence/runtime_agent>) -> SandboxPhase2Loop`. Builds ONE live loop exactly as today's `_factory` does, but with: `incarnation_number=incarnation_idx`; `_SandboxStateHook(writer=writer, incarnation_number=incarnation_idx)` when divine-economy on; `strategy_advisor=shared_advisor`; and (when `GENESIS_REAL_LEARNING=1`) `loop._poller.weight_updater = _SettlementLearningWeightUpdater(inner=(shared_weight_updater if shared_weight_updater is not None else _RealWeightUpdater()), weights_holder=loop)`. The `shared_weight_updater` param is `WeightUpdater | None = None`; the 0-arg wrapper passes `None` (so the learning block constructs a fresh `_RealWeightUpdater()` exactly as today — byte-identical OFF), while the supervisor passes ONE shared instance (carry EMA across lives). The updater is **only ever constructed inside the learning block** — never unconditionally — so the OFF path imports/builds nothing new (Round-1 HIGH-1).

**`IncarnationManifest`** (Pydantic, `extra="forbid"`): `{ run_id: str, current_incarnation_idx: int, carry_weights_hash: str, max_incarnations: int }`. Filename `INCARNATION_MANIFEST_FILENAME = "incarnation_manifest.json"` at the root `state_dir`. Written atomically (temp + `os.replace`) on each incarnation transition.

**Per-life stream reset:** between incarnations the supervisor deletes the per-life stream files in `state_dir` — `open_bets.jsonl`, `settled_bets.jsonl`, `decisions.jsonl`, `reflections.jsonl`, `proposals.jsonl`, `agent_state.json` — and PRESERVES `gods_treasury.jsonl`, `deaths.jsonl`, `incarnation_manifest.json` (cumulative/lineage). Constants reused from `agent.data.sandbox_state`.

**`incarnation_number` on the loop:** `SandboxPhase2Loop.__init__` gains `incarnation_number: int = 0`, stored as `self._incarnation_number`, stamped into all 4 `AgentStateSnapshot(...)` constructions. Default 0 ⇒ flag-off snapshots carry `incarnation_number: 0` (already the Phase-1 default field value, so byte-identical).

---

## Task P1: `incarnation_number` on the loop + all 4 snapshot sites

**Files:**
- Modify: `code/agent/runtime/sandbox_phase2_loop.py` (`__init__` ~790-928 + store; 4 `AgentStateSnapshot(...)` sites: ~1925, ~2023, ~2150, ~2300)
- Test: `code/tests/agent/runtime/test_loop_incarnation_number.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/runtime/test_loop_incarnation_number.py
import inspect
from agent.runtime import sandbox_phase2_loop as L


def test_constructor_accepts_incarnation_number():
    sig = inspect.signature(L.SandboxPhase2Loop.__init__)
    assert "incarnation_number" in sig.parameters
    assert sig.parameters["incarnation_number"].default == 0  # default 0 = byte-identical off


def test_all_snapshot_sites_stamp_incarnation_number():
    src = inspect.getsource(L.SandboxPhase2Loop)
    # every AgentStateSnapshot(...) construction must pass incarnation_number=
    n_snapshots = src.count("AgentStateSnapshot(")
    n_stamped = src.count("incarnation_number=self._incarnation_number")
    assert n_snapshots >= 4, f"expected >=4 snapshot sites, found {n_snapshots}"
    assert n_stamped == n_snapshots, (
        f"{n_snapshots} AgentStateSnapshot sites but only {n_stamped} stamp "
        "incarnation_number — every site must stamp it"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/runtime/test_loop_incarnation_number.py -q`
Expected: FAIL (`incarnation_number` not a ctor param; 0 stamped).

- [ ] **Step 3: Implement — ctor param + store**

In `SandboxPhase2Loop.__init__`, add the param next to `record_living_stage_fields` (~line 927):

```python
        record_living_stage_fields: bool = False,
        # Phase 2 — which incarnation this loop instance is (stamped into every
        # snapshot so the dashboard + manifest can track the lineage). Default 0
        # = single-life path (byte-identical: incarnation_number already defaults
        # to 0 on AgentStateSnapshot).
        incarnation_number: int = 0,
```

Store it (next to `self._record_living_stage_fields`, ~line 975):

```python
        self._incarnation_number: int = incarnation_number
```

- [ ] **Step 4: Implement — stamp all 4 snapshot sites**

Add `incarnation_number=self._incarnation_number,` to EACH `AgentStateSnapshot(...)` construction. The 4 sites all share the same field block ending in `pending_proposals=list(self._pending_proposals),` — add the stamp right after that line at each site:
  1. `_tick` main snapshot (~1925-1936)
  2. post-L3 re-snapshot (~2023-2035)
  3. post-tribute snapshot (~2150-2162)
  4. `_die` terminal snapshot (~2300-2312)

Each becomes:
```python
            ...
            desperate=self._desperate,
            pending_proposals=list(self._pending_proposals),
            incarnation_number=self._incarnation_number,
        )
```

(Use `replace_all` carefully — the 4 sites have slightly different indentation; do them one at a time anchoring on enough surrounding context to be unique, e.g. the `last_tick=tick,` vs `last_tick=last_tick,` difference in `_die`.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/runtime/test_loop_incarnation_number.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Regression — full loop + data suites**

Run: `cd code && python -m pytest tests/agent/runtime tests/agent/data -q`
Expected: PASS. (Snapshots now always carry `incarnation_number`; since `AgentStateSnapshot.incarnation_number` defaults to 0 and was already a field from Phase 1, on-disk shape is unchanged when idx=0.)

- [ ] **Step 7: Commit**

```bash
git add agent/runtime/sandbox_phase2_loop.py tests/agent/runtime/test_loop_incarnation_number.py
git commit -m "feat(loop): incarnation_number ctor param stamped into all 4 snapshot sites" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task P2: Extract `_build_one_incarnation_loop` from the factory

**Files:**
- Modify: `code/agent/server/main.py` (`_build_production_loop_factory` ~2083-2315)
- Test: `code/tests/agent/server/test_one_incarnation_builder.py`

**Why:** the supervisor must build N loops, each with a fresh chain_adapter + a per-incarnation idx + the SHARED weight-updater inner + the SHARED advisor. Today the `_factory` closure bakes in one chain_adapter and builds a fresh advisor/updater per call. Extract the loop-construction body into a helper that takes those as parameters; keep the existing 0-arg factory as a thin wrapper (incarnation 0) so every non-reincarnation caller + test is byte-unchanged.

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/server/test_one_incarnation_builder.py
from agent.data._realtime_buffer import UtcClock
from agent.server import main as M
from agent.server.bootstrap import PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX


def test_build_one_incarnation_loop_stamps_idx_and_uses_given_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_DIVINE_ECONOMY", "1")
    chain = M._build_chain_adapter(kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX)
    advisor = M._make_prod_strategy_advisor()
    from agent.engines.weight_updater import WeightUpdater
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
        decision_cadence=__import__("datetime").timedelta(seconds=60),
        runtime_agent=None,
    )
    assert loop._incarnation_number == 3
    assert loop._chain_adapter is chain
    # divine hook carries the same incarnation idx so DeathRecord.incarnation_number is right
    assert getattr(loop._state_hook, "_incarnation_number", None) == 3


def test_zero_arg_factory_still_works(tmp_path):
    chain = M._build_chain_adapter(kind=PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX)
    factory = M._build_production_loop_factory(
        state_dir=tmp_path / "sandbox", chain_adapter=chain,
        tick_input_source=M._IdleTickInputSource(), wall_clock=UtcClock(),
        time_compression=1.0, tick_interval_seconds=60.0,
    )
    loop = factory()
    assert loop._incarnation_number == 0  # wrapper builds incarnation 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code && python -m pytest tests/agent/server/test_one_incarnation_builder.py -q`
Expected: FAIL (`_build_one_incarnation_loop` does not exist).

- [ ] **Step 3: Implement — extract the helper**

Add a module-level `_build_one_incarnation_loop` (above `_build_production_loop_factory`). Move the body of the current `_factory` closure into it, parameterized. It mirrors the current construction VERBATIM except: (a) takes `incarnation_idx`, `chain_adapter`, `initial_weights`, `shared_weight_updater`, `shared_advisor` as args; (b) passes `incarnation_number=incarnation_idx`; (c) the divine hook is `_SandboxStateHook(writer=writer, incarnation_number=incarnation_idx)`; (d) `strategy_advisor=shared_advisor`; (e) the `GENESIS_REAL_LEARNING` block uses `inner=shared_weight_updater` instead of a fresh `_RealWeightUpdater()`:

```python
def _build_one_incarnation_loop(
    *,
    incarnation_idx: int,
    state_dir: Path,
    chain_adapter: SandboxLoopChainAdapter,
    initial_weights: Weights | None,
    shared_weight_updater: Any | None = None,  # WeightUpdater|None; None ⇒ fresh per call (byte-identical OFF)
    shared_advisor: StrategyAdvisor,
    tick_input_source: TickInputSource,
    settlement_client: SettlementClient | None,
    market_resolver: MarketResolver | None,
    wall_clock: Clock,
    decision_cadence: timedelta,
    runtime_agent: RuntimeAgentRunner | None,
) -> SandboxPhase2Loop:
    """Build ONE live incarnation loop. The supervisor calls this per life with
    a FRESH chain_adapter + the per-life idx; the 0-arg factory wraps it for
    incarnation 0. Construction is byte-identical to the pre-Phase-2 _factory
    except for the incarnation idx + the shared advisor/updater injection."""
    promoted_weights = _consume_staged_config(state_dir) if initial_weights is None else initial_weights
    writer = SandboxStateWriter(root=state_dir)
    _append_loop_boot_row(decisions_path=writer.decisions_path, loop_name=LOOP_BOOT_MARKER_LOOP_NAME)
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=market_resolver or _null_market_resolver,
        clock=wall_clock,
    )
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=state_dir / "_mb"),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,
    )
    _sandbox_live = os.environ.get("SANDBOX_LIVE") == "1"
    _divine_economy = os.environ.get("SANDBOX_DIVINE_ECONOMY") == "1"
    if _divine_economy:
        state_hook: Any = _SandboxStateHook(writer=writer, incarnation_number=incarnation_idx)
        tribute_policy: ReflexTributePolicy | None = ReflexTributePolicy()
        tribute_rng: random.Random | None = random.Random(_GODS_DICE_SEED)
    else:
        state_hook = _NoopStateHook()
        tribute_policy = None
        tribute_rng = None
    loop = SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=settlement_client or _NoopSettlementClient(),
        weight_updater=_NoopWeightUpdater(),
        chain_adapter=chain_adapter,
        tick_inputs=tick_input_source,
        state_hook=state_hook,
        tribute_policy=tribute_policy,
        tribute_rng=tribute_rng,
        divine_tithe=_divine_economy,
        record_living_stage_fields=_divine_economy,
        incarnation_number=incarnation_idx,
        state_writer=writer,
        clock=wall_clock,
        sleeper=_real_sleep,
        decision_cadence=decision_cadence,
        initial_phase=Phase.PHASE_2_APPRENTICE,
        initial_weights=promoted_weights,
        initial_breath=_SANDBOX_COLD_START_BREATH_USD,
        initial_bankroll_usd=_SANDBOX_COLD_START_BREATH_USD,
        strategy_advisor=shared_advisor,
        reflection_engine=_make_prod_reflection_engine(state_dir=state_dir),
        populate_reflection_window=_l6_reflection_optimize_enabled(),
        side_correct_pricing=_sandbox_live,
        require_cost_fields=_sandbox_live,
        runtime_agent=runtime_agent,
    )
    if os.environ.get("GENESIS_REAL_LEARNING") == "1":
        from agent.backtest.settlement_learner import _SettlementLearningWeightUpdater
        from agent.engines.weight_updater import WeightUpdater as _RealWeightUpdater
        # Round-1 HIGH-1: the updater is constructed ONLY here (lazy). None ⇒ a
        # fresh instance (exactly today's path → byte-identical OFF); the
        # supervisor injects ONE shared instance so its EMA carries across lives.
        inner = shared_weight_updater if shared_weight_updater is not None else _RealWeightUpdater()
        loop._poller.weight_updater = _SettlementLearningWeightUpdater(
            inner=inner, weights_holder=loop,
        )
    return loop
```

Then rewrite `_factory` inside `_build_production_loop_factory` to delegate (incarnation 0, fresh advisor + fresh shared updater so the wrapper is self-contained and byte-identical):

```python
    def _factory() -> SandboxPhase2Loop:
        # Wrapper = incarnation 0. shared_weight_updater=None ⇒ the learning
        # block (if GENESIS_REAL_LEARNING=1) builds a fresh updater exactly as
        # today; OFF ⇒ nothing is constructed → byte-identical.
        return _build_one_incarnation_loop(
            incarnation_idx=0,
            state_dir=state_dir,
            chain_adapter=chain_adapter,
            initial_weights=None,
            shared_weight_updater=None,
            shared_advisor=_make_prod_strategy_advisor(),
            tick_input_source=tick_input_source,
            settlement_client=settlement_client,
            market_resolver=market_resolver,
            wall_clock=wall_clock,
            decision_cadence=decision_cadence,
            runtime_agent=runtime_agent,
        )
    return _factory
```

> Confirm the imports `Weights`, `timedelta`, `TickInputSource`, `SettlementClient`, `MarketResolver`, `RuntimeAgentRunner`, `StrategyAdvisor`, `Clock` are already imported in main.py (they are — used by the existing factory signature); if `Weights` is not, it is in `agent.core.state` (already imported).

- [ ] **Step 4: Run to verify it passes**

Run: `cd code && python -m pytest tests/agent/server/test_one_incarnation_builder.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression — server suite (the 0-arg factory contract is unchanged)**

Run: `cd code && python -m pytest tests/agent/server -q`
Expected: PASS — `test_main_prod_loop_factory.py` (the 0-arg factory + loop_boot marker tests) + `test_divine_state_hook.py` still green.

- [ ] **Step 6: Commit**

```bash
git add agent/server/main.py tests/agent/server/test_one_incarnation_builder.py
git commit -m "refactor(server): extract _build_one_incarnation_loop (per-life builder); 0-arg factory wraps incarnation 0" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task P3: `IncarnationManifest` + read/write

**Files:**
- Create: `code/agent/runtime/incarnation_supervisor.py` (the manifest half)
- Test: `code/tests/agent/runtime/test_incarnation_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/runtime/test_incarnation_manifest.py
import pytest
from pydantic import ValidationError
from agent.runtime.incarnation_supervisor import (
    IncarnationManifest, INCARNATION_MANIFEST_FILENAME,
    read_manifest, write_manifest,
)


def test_manifest_roundtrip(tmp_path):
    m = IncarnationManifest(run_id="r1", current_incarnation_idx=2,
                            carry_weights_hash="0xabc", max_incarnations=10)
    write_manifest(tmp_path, m)
    assert (tmp_path / INCARNATION_MANIFEST_FILENAME).exists()
    assert read_manifest(tmp_path) == m


def test_read_missing_manifest_returns_none(tmp_path):
    assert read_manifest(tmp_path) is None


def test_read_corrupt_manifest_returns_none(tmp_path):
    (tmp_path / INCARNATION_MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    assert read_manifest(tmp_path) is None  # absent/corrupt → cold start at incarnation 0


def test_extra_forbid(tmp_path):
    with pytest.raises(ValidationError):
        IncarnationManifest(run_id="r", current_incarnation_idx=0,
                            carry_weights_hash="h", max_incarnations=10, bogus=1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code && python -m pytest tests/agent/runtime/test_incarnation_manifest.py -q`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement the manifest**

```python
# code/agent/runtime/incarnation_supervisor.py  (manifest half — supervisor added in P4)
"""Living Stage Phase 2 — live reincarnation supervisor + its crash-recovery
manifest. See docs/superpowers/plans/2026-06-19-living-stage-phase2.md."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

INCARNATION_MANIFEST_FILENAME: Final[str] = "incarnation_manifest.json"


class IncarnationManifest(BaseModel):
    """Crash-recovery breadcrumb at the root state_dir. Lets the supervisor
    resume the lineage at the right incarnation after a server restart instead
    of restarting at 0."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    current_incarnation_idx: int = Field(ge=0)
    carry_weights_hash: str
    max_incarnations: int = Field(ge=1)


def write_manifest(state_dir: Path, manifest: IncarnationManifest) -> None:
    """Atomically (temp + os.replace) write the manifest to the root state_dir."""
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / INCARNATION_MANIFEST_FILENAME
    with tempfile.NamedTemporaryFile(
        "w", dir=state_dir, delete=False, encoding="utf-8", suffix=".tmp"
    ) as f:
        f.write(manifest.model_dump_json())
        f.flush()
        os.fsync(f.fileno())
        tmp = Path(f.name)
    os.replace(tmp, target)


def read_manifest(state_dir: Path) -> IncarnationManifest | None:
    """Read the manifest; None on absent OR corrupt (→ cold start at idx 0)."""
    path = state_dir / INCARNATION_MANIFEST_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return IncarnationManifest.model_validate_json(raw)
    except Exception:  # noqa: BLE001 — corrupt manifest must not crash boot
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd code && python -m pytest tests/agent/runtime/test_incarnation_manifest.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/runtime/incarnation_supervisor.py tests/agent/runtime/test_incarnation_manifest.py
git commit -m "feat(reincarnation): IncarnationManifest + atomic read/write (crash recovery)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task P4: `LiveIncarnationSupervisor` (the respawn LoopHandle)

**Files:**
- Modify: `code/agent/runtime/incarnation_supervisor.py` (add the supervisor)
- Test: `code/tests/agent/runtime/test_incarnation_supervisor.py`

- [ ] **Step 1: Write the failing test** (fake loops drive death/survival/cancel; assert respawn, carry, fresh adapter per life, stream reset, cap, manifest)

```python
# code/tests/agent/runtime/test_incarnation_supervisor.py
import asyncio
from pathlib import Path

import pytest

from agent.runtime.incarnation_supervisor import (
    LiveIncarnationSupervisor, read_manifest, INCARNATION_MANIFEST_FILENAME,
)
from agent.runtime.sandbox_phase2_loop import RunSummary
from agent.core.state import Weights


def _summary(*, died: bool) -> RunSummary:
    return RunSummary(ticks_completed=1, bets_placed=0, no_bets_emitted=1,
                      settlements_processed=0, died=died, death_receipt=None,
                      final_breath=0.0 if died else 50.0, final_bankroll_usd=0.0)


class _FakeLoop:
    """Records the idx/adapter it was built with; run() returns a preset summary."""
    def __init__(self, *, idx, chain_adapter, weights, summary, on_run=None):
        self._incarnation_number = idx
        self._chain_adapter = chain_adapter
        self._weights = weights
        self._summary = summary
        self._on_run = on_run
    @property
    def weights(self):
        return self._weights
    async def run(self):
        if self._on_run is not None:
            await self._on_run()
        return self._summary


def _make_supervisor(tmp_path, *, summaries, max_incarnations=10, on_run=None):
    built = []
    adapters = []
    def build_chain_adapter():
        a = object()
        adapters.append(a)
        return a
    def build_incarnation(*, incarnation_idx, chain_adapter, initial_weights, incarnation_number):
        # one fresh weights object per life so carry can be asserted by identity
        w = Weights.default() if hasattr(Weights, "default") else initial_weights
        loop = _FakeLoop(idx=incarnation_idx, chain_adapter=chain_adapter,
                         weights=f"weights-{incarnation_idx}",
                         summary=summaries[incarnation_idx],
                         on_run=on_run)
        built.append({"idx": incarnation_idx, "adapter": chain_adapter,
                      "initial_weights": initial_weights})
        return loop
    sup = LiveIncarnationSupervisor(
        build_incarnation=build_incarnation,
        build_chain_adapter=build_chain_adapter,
        state_dir=tmp_path,
        max_incarnations=max_incarnations,
    )
    return sup, built, adapters


@pytest.mark.asyncio
async def test_respawns_on_death_until_survival(tmp_path):
    # life0 dies, life1 dies, life2 survives → 3 incarnations, then stop
    sup, built, adapters = _make_supervisor(
        tmp_path, summaries=[_summary(died=True), _summary(died=True), _summary(died=False)])
    await sup.run()
    assert [b["idx"] for b in built] == [0, 1, 2]
    # fresh chain_adapter per life (no two lives share an adapter — the re-die bug guard)
    assert len(set(id(a) for a in adapters)) == 3
    # weights carried: life1/life2 built with the prior life's terminal weights
    assert built[1]["initial_weights"] == "weights-0"
    assert built[2]["initial_weights"] == "weights-1"


@pytest.mark.asyncio
async def test_max_incarnations_cap(tmp_path):
    sup, built, _ = _make_supervisor(
        tmp_path, summaries=[_summary(died=True)] * 5, max_incarnations=3)
    await sup.run()
    assert [b["idx"] for b in built] == [0, 1, 2]  # capped at 3 lives


@pytest.mark.asyncio
async def test_cancel_propagates_and_does_not_respawn(tmp_path):
    async def boom():
        raise asyncio.CancelledError
    sup, built, _ = _make_supervisor(
        tmp_path, summaries=[_summary(died=True)] * 3, on_run=boom)
    with pytest.raises(asyncio.CancelledError):
        await sup.run()
    assert [b["idx"] for b in built] == [0]  # cancelled during life 0 → no respawn


@pytest.mark.asyncio
async def test_writes_manifest_each_transition(tmp_path):
    sup, _, _ = _make_supervisor(
        tmp_path, summaries=[_summary(died=True), _summary(died=False)])
    await sup.run()
    m = read_manifest(tmp_path)
    assert m is not None and m.current_incarnation_idx >= 1


@pytest.mark.asyncio
async def test_resumes_from_manifest_on_boot(tmp_path):
    # pre-seed a manifest at idx 2 → the supervisor's first life is incarnation 2
    from agent.runtime.incarnation_supervisor import IncarnationManifest, write_manifest
    write_manifest(tmp_path, IncarnationManifest(
        run_id="r", current_incarnation_idx=2, carry_weights_hash="x", max_incarnations=10))
    sup, built, _ = _make_supervisor(
        tmp_path, summaries={2: _summary(died=False)} | {i: _summary(died=True) for i in range(2)})
    await sup.run()
    assert built[0]["idx"] == 2  # resumed, not restarted at 0
```

> The `summaries` for the resume test is a dict keyed by idx; adjust `_make_supervisor` to index `summaries[incarnation_idx]` (works for both list and dict). The `Weights.default()` shim is only used if the real type needs a value; the fake carries string sentinels for identity assertions.

- [ ] **Step 2: Run to verify it fails**

Run: `cd code && python -m pytest tests/agent/runtime/test_incarnation_supervisor.py -q`
Expected: FAIL (`LiveIncarnationSupervisor` not defined).

- [ ] **Step 3: Implement the supervisor**

Append to `agent/runtime/incarnation_supervisor.py`:

```python
import hashlib
import logging
import shutil
from collections.abc import Callable
from typing import Any

from agent.data.sandbox_state import (
    DECISIONS_FILENAME, OPEN_BETS_FILENAME, PROPOSALS_FILENAME,
    REFLECTIONS_FILENAME, SETTLED_BETS_FILENAME, SNAPSHOT_FILENAME,
)
from agent.runtime.sandbox_phase2_loop import RunSummary, SandboxPhase2Loop

logger = logging.getLogger(__name__)

# Per-life streams reset between incarnations (cold-start clean). The cumulative
# divine streams (gods_treasury.jsonl, deaths.jsonl) + the manifest are NOT here
# — they accumulate across lives so the treasury grows + the lineage builds.
_PER_LIFE_STREAMS: Final[tuple[str, ...]] = (
    OPEN_BETS_FILENAME, SETTLED_BETS_FILENAME, DECISIONS_FILENAME,
    REFLECTIONS_FILENAME, PROPOSALS_FILENAME, SNAPSHOT_FILENAME,
)

_DEFAULT_MAX_INCARNATIONS: Final[int] = 10


def _weights_hash(weights: object) -> str:
    """Stable SHA-256 of a weights object for the manifest (Round-1 MED-1 —
    Python hash() is process-randomized; mirror the death path's
    _sha256_hex_prefixed over weights.model_dump_json()). '0x0' for None
    (incarnation 0's default-weights case)."""
    if weights is None:
        return "0x0"
    try:
        payload = weights.model_dump_json()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        payload = repr(weights)
    return "0x" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_snapshot_weights(state_dir: Path) -> object | None:
    """Read terminal weights from the root agent_state.json (None on
    absent/corrupt/no-weights). Used on manifest resume to carry the prior
    incarnation's evolved weights forward BEFORE the per-life reset wipes the
    snapshot (Round-1 HIGH-2)."""
    from agent.data.sandbox_state import AgentStateSnapshot
    try:
        snap = AgentStateSnapshot.model_validate_json(
            (state_dir / SNAPSHOT_FILENAME).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001
        return None
    return snap.weights


class LiveIncarnationSupervisor:
    """Phase 2 — a LoopHandle that respawns the single-life loop across deaths.

    Cloned from agent/backtest/survival_season.run_survival_season: build one
    incarnation, await its run(), and on death carry loop.weights into the next
    life. Implements the LoopHandle protocol (async run()) so AgentRunner is
    unchanged — the production factory returns THIS when SANDBOX_REINCARNATION=1.
    """

    def __init__(
        self,
        *,
        build_incarnation: Callable[..., SandboxPhase2Loop],
        build_chain_adapter: Callable[[], Any],
        state_dir: Path,
        max_incarnations: int = _DEFAULT_MAX_INCARNATIONS,
        run_id: str = "live",
    ) -> None:
        self._build_incarnation = build_incarnation
        self._build_chain_adapter = build_chain_adapter
        self._state_dir = Path(state_dir)
        self._max_incarnations = max(1, max_incarnations)
        self._run_id = run_id

    def _reset_per_life_streams(self) -> None:
        for name in _PER_LIFE_STREAMS:
            try:
                (self._state_dir / name).unlink(missing_ok=True)
            except OSError:
                logger.warning("supervisor: could not reset per-life stream %s", name)
        # Round-1 MED-4: per-incarnation memory bank + reflections (spec §7:
        # memory CID is per-incarnation for v1). Safe to reset here — called
        # between lives, after the dead life returned + finalized its tombstone.
        shutil.rmtree(self._state_dir / "_mb", ignore_errors=True)

    async def run(self) -> RunSummary:
        # Resume from the manifest if present (crash recovery); else start at 0.
        m = read_manifest(self._state_dir)
        start_idx = m.current_incarnation_idx if m is not None else 0
        # Round-1 HIGH-2: on resume, recover the prior terminal weights from the
        # root snapshot BEFORE any reset wipes agent_state.json — else the
        # resumed incarnation cold-starts with default weights, discarding what
        # it learned. None (cold boot) → incarnation 0 uses the loop's default.
        carry_weights: object | None = (
            _read_snapshot_weights(self._state_dir) if m is not None else None
        )
        last_summary = RunSummary(
            ticks_completed=0, bets_placed=0, no_bets_emitted=0,
            settlements_processed=0, died=False, death_receipt=None,
            final_breath=0.0, final_bankroll_usd=0.0,
        )
        idx = start_idx
        while idx < self._max_incarnations:
            # Fresh chain_adapter per life — reusing a dead adapter (breath=0)
            # would re-die instantly. Reset per-life streams so the loop
            # cold-starts; the cumulative divine streams are preserved.
            self._reset_per_life_streams()
            chain_adapter = self._build_chain_adapter()
            loop = self._build_incarnation(
                incarnation_idx=idx,
                chain_adapter=chain_adapter,
                initial_weights=carry_weights,
                incarnation_number=idx,
            )
            write_manifest(self._state_dir, IncarnationManifest(
                run_id=self._run_id, current_incarnation_idx=idx,
                carry_weights_hash=_weights_hash(carry_weights),
                max_incarnations=self._max_incarnations,
            ))
            logger.info("supervisor: incarnation %d starting", idx)
            # CancelledError (operator stop) propagates OUT untouched → no respawn.
            summary = await loop.run()
            last_summary = summary
            carry_weights = loop.weights
            if not summary.died:
                # survived to a self-decided stop (live: only happens on cancel,
                # which raises above; this guards backtest-style finite runs).
                break
            idx += 1
        logger.info("supervisor: terminal after %d incarnation(s)", idx - start_idx + 1)
        return last_summary
```

> `RunSummary`, `IncarnationManifest`, `write_manifest`, `read_manifest` are in scope (same module / imported in P3). The `initial_weights=None` for incarnation 0 makes the per-incarnation builder fall back to its default-weights path (matching the 0-arg factory).

- [ ] **Step 4: Run to verify it passes**

Run: `cd code && python -m pytest tests/agent/runtime/test_incarnation_supervisor.py -q`
Expected: PASS (5 passed). If `pytest.mark.asyncio` needs the anyio/asyncio plugin, mirror the existing async loop tests' decorator (check an existing `tests/agent/runtime/test_*phase2*` async test for the project's convention — it uses `@pytest.mark.asyncio` via pytest-asyncio or `asyncio.run` in a sync test; match whichever the repo uses).

- [ ] **Step 5: Commit**

```bash
git add agent/runtime/incarnation_supervisor.py tests/agent/runtime/test_incarnation_supervisor.py
git commit -m "feat(reincarnation): LiveIncarnationSupervisor — respawn-on-death LoopHandle (fresh adapter/carry/cap/manifest)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task P5: Wire the supervisor behind `SANDBOX_REINCARNATION`

**Files:**
- Modify: `code/agent/server/main.py` (`_build_default_app` ~2549-2687)
- Test: `code/tests/agent/server/test_reincarnation_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/server/test_reincarnation_wiring.py
import pytest
from agent.server import main as server_main
from agent.runtime.incarnation_supervisor import LiveIncarnationSupervisor
from agent.runtime.sandbox_phase2_loop import SandboxPhase2Loop


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code && python -m pytest tests/agent/server/test_reincarnation_wiring.py -q`
Expected: FAIL (factory always returns a SandboxPhase2Loop).

- [ ] **Step 3: Implement — gate in `_build_default_app`**

In `_build_default_app`, AFTER the existing `loop_factory = _build_production_loop_factory(...)` block and BEFORE `runner = AgentRunner(...)`, insert:

```python
    # Living Stage Phase 2 — reincarnation. When ON, the AgentRunner's loop
    # factory returns a LiveIncarnationSupervisor (a LoopHandle) instead of a
    # bare loop; the supervisor respawns the single-life loop across deaths,
    # carrying weights + a shared advisor/updater. Default OFF = single life.
    if os.environ.get("SANDBOX_REINCARNATION") == "1":
        from agent.runtime.incarnation_supervisor import LiveIncarnationSupervisor

        decision_cadence = _resolve_decision_cadence(
            tick_interval_seconds=prod_loop_config.tick_interval_seconds,
            time_compression=prod_loop_config.time_compression,
        )

        def _supervisor_factory() -> LiveIncarnationSupervisor:
            # Round-1 MED-2/3: a FRESH supervisor per AgentRunner.start (the
            # LoopFactoryProto per-call contract), and the shared updater +
            # advisor constructed HERE so they are shared across the lives of
            # THIS run only (not across independent /start cycles). The updater
            # is gated on GENESIS_REAL_LEARNING (lazy) so the OFF path builds none.
            shared_weight_updater: Any | None = None
            if os.environ.get("GENESIS_REAL_LEARNING") == "1":
                from agent.engines.weight_updater import (
                    WeightUpdater as _RealWeightUpdater,
                )
                shared_weight_updater = _RealWeightUpdater()  # ONE EMA backbone / run
            shared_advisor = _make_prod_strategy_advisor()    # ONE L3CostGuard / run
            return LiveIncarnationSupervisor(
                build_incarnation=lambda *, incarnation_idx, chain_adapter, initial_weights, incarnation_number: _build_one_incarnation_loop(
                    incarnation_idx=incarnation_idx,
                    state_dir=state_dir,
                    chain_adapter=chain_adapter,
                    initial_weights=initial_weights,
                    shared_weight_updater=shared_weight_updater,
                    shared_advisor=shared_advisor,
                    tick_input_source=tick_input_source,
                    settlement_client=settlement_client,
                    market_resolver=market_resolver,
                    wall_clock=wall_clock,
                    decision_cadence=decision_cadence,
                    runtime_agent=runtime_agent,
                ),
                build_chain_adapter=lambda: _build_chain_adapter(
                    kind=prod_loop_config.chain_adapter_kind
                ),
                state_dir=state_dir,
            )

        loop_factory = _supervisor_factory
```

> `_resolve_decision_cadence`, `prod_loop_config`, `tick_input_source`, `settlement_client`, `market_resolver`, `wall_clock`, `runtime_agent`, `state_dir` are all in scope at this point in `_build_default_app` (they were just used to build the single-life factory). The supervisor shares ONE `shared_weight_updater` + ONE `shared_advisor` across all incarnations (the critical budget/EMA fix). The `build_chain_adapter` lambda mints a FRESH adapter per life.

- [ ] **Step 4: Run to verify it passes**

Run: `cd code && python -m pytest tests/agent/server/test_reincarnation_wiring.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression — full server + runtime suites**

Run: `cd code && python -m pytest tests/agent/server tests/agent/runtime -q`
Expected: PASS — default-OFF path unchanged; AgentRunner unchanged.

- [ ] **Step 6: Commit**

```bash
git add agent/server/main.py tests/agent/server/test_reincarnation_wiring.py
git commit -m "feat(server): SANDBOX_REINCARNATION wires LiveIncarnationSupervisor as the AgentRunner loop factory" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task P6: End-to-end — lineage + treasury accumulate across deaths

**Files:**
- Test: `code/tests/agent/runtime/test_reincarnation_e2e.py`

**Why:** prove the supervisor + the Phase-1 divine hook together produce a multi-incarnation `deaths.jsonl` (the lineage) + a cumulative `gods_treasury.jsonl`, in the SAME root the Phase-1 dashboard loader reads. No new product code — this locks the integration.

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/runtime/test_reincarnation_e2e.py
import asyncio
from pathlib import Path

import pytest

from agent.data.sandbox_state import DEATHS_FILENAME, iter_jsonl
from agent.runtime.incarnation_supervisor import LiveIncarnationSupervisor
from agent.runtime.sandbox_phase2_loop import RunSummary


class _DyingLoop:
    """Minimal loop that appends a DeathRecord to the ROOT deaths.jsonl on run()
    (mimicking the real _SandboxStateHook on death) then returns died=True."""
    def __init__(self, *, state_dir, idx):
        self._state_dir = state_dir
        self._incarnation_number = idx
        self._weights = f"w{idx}"
    @property
    def weights(self):
        return self._weights
    async def run(self):
        from agent.data.sandbox_state import SandboxStateWriter, DeathRecord
        w = SandboxStateWriter(root=self._state_dir)
        w.append_death(DeathRecord(death_id=f"d{self._incarnation_number}", ts="t",
                                   incarnation_number=self._incarnation_number,
                                   agent_id="a", last_tick=1, final_bankroll_usd=0.0))
        return RunSummary(ticks_completed=1, bets_placed=0, no_bets_emitted=0,
                          settlements_processed=0, died=True, death_receipt=None,
                          final_breath=0.0, final_bankroll_usd=0.0)


@pytest.mark.asyncio
async def test_deaths_accumulate_across_incarnations(tmp_path):
    sup = LiveIncarnationSupervisor(
        build_incarnation=lambda *, incarnation_idx, chain_adapter, initial_weights, incarnation_number: _DyingLoop(state_dir=tmp_path, idx=incarnation_idx),
        build_chain_adapter=lambda: object(),
        state_dir=tmp_path,
        max_incarnations=3,
    )
    await sup.run()
    deaths = iter_jsonl(tmp_path / DEATHS_FILENAME)
    # 3 incarnations all died → 3 lineage rows with incarnation_number 0,1,2
    # (deaths.jsonl is a PER-LIFE stream NOT in _PER_LIFE_STREAMS, so it is NOT
    #  reset between lives — it accumulates).
    assert [d["incarnation_number"] for d in deaths] == [0, 1, 2]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code && python -m pytest tests/agent/runtime/test_reincarnation_e2e.py -q`
Expected: FAIL if `deaths.jsonl` were being reset — proving it must be EXCLUDED from `_PER_LIFE_STREAMS`. (If P4 was implemented correctly it passes; this test exists to lock the cumulative-stream invariant against a future regression.)

- [ ] **Step 3: (If failing) ensure `deaths.jsonl`/`gods_treasury.jsonl` are NOT in `_PER_LIFE_STREAMS`**

Confirm `_PER_LIFE_STREAMS` in `incarnation_supervisor.py` contains ONLY `open_bets/settled_bets/decisions/reflections/proposals/agent_state.json` — never `gods_treasury.jsonl` or `deaths.jsonl`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd code && python -m pytest tests/agent/runtime/test_reincarnation_e2e.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/agent/runtime/test_reincarnation_e2e.py
git commit -m "test(reincarnation): e2e — deaths/treasury accumulate across incarnations in the root streams" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (before plan-loop execution review)

- [ ] `cd code && python -m pytest tests/agent/runtime tests/agent/server tests/agent/data -q` → all green.
- [ ] **Default-OFF byte-identicality:** with `SANDBOX_REINCARNATION` unset, the factory returns a single `SandboxPhase2Loop` (incarnation 0); `incarnation_number=0` ⇒ snapshots unchanged; the 0-arg factory + AgentRunner contract is byte-unchanged.
- [ ] **Dashboard (no code change):** `cd code/dashboard && npx vitest run && npx tsc --noEmit` → still green (Phase-1 loader already consumes `deaths.jsonl`/treasury; multi-incarnation rows just flow through).
- [ ] **Live smoke (manual, optional):** export `SANDBOX_REINCARNATION=1 SANDBOX_DIVINE_ECONOMY=1 SANDBOX_LIVE=1`; boot; force a death (tithe drains breath); confirm `deaths.jsonl` gains a row, a fresh incarnation starts (incarnation_number increments in `agent_state.json`), and `/living` Lineage shows life 0 ✝ → life 1 ● ALIVE.

---

## Self-review notes (author)

- **Spec coverage:** supervisor respawn (P4) ✓; fresh chain_adapter per life (P4 — the re-die guard) ✓; shared WeightUpdater + shared L3CostGuard/advisor (P2 builder + P5 wiring) ✓; carry weights (P4) ✓; incarnation_number stamping (P1) ✓; manifest crash recovery (P3 + P4 resume) ✓; max cap (P4) ✓; SANDBOX_REINCARNATION flag, default-OFF byte-identical (P5) ✓; Lineage/Treasury live across deaths (P6, no UI change) ✓. **Status read path:** the spec's "subdir + mirror-up" is superseded by the simpler **root-with-reset** (incarnation writes directly to root; per-life streams reset, cumulative divine streams preserved) — documented in Architecture; achieves the same "dashboard reads the live incarnation at the root" goal without a mirror step.
- **Type consistency:** `_build_one_incarnation_loop` kwargs match the supervisor's `build_incarnation(*, incarnation_idx, chain_adapter, initial_weights, incarnation_number)` call (the P5 lambda adapts the names); `RunSummary` fields used (`died`) match the real dataclass; `incarnation_number` is consistent across loop ctor → snapshots → DeathRecord (via the hook) → manifest.
- **Execution-time confirmations (exact anchors named):** the repo's async-test convention (`@pytest.mark.asyncio` vs `asyncio.run`) — match an existing `tests/agent/runtime` async test (P4 step 4); the exact unique anchors for the 4 snapshot sites (P1 step 4 — `last_tick=tick` vs `last_tick=last_tick`).

---

## Revision log

### Round 1 — Codex review (xhigh) → `VERDICT: HIGH=2 MEDIUM=4 LOW=0`. All accepted; fixed:

- **HIGH-1 (default-OFF byte-identicality):** the wrapper constructed `_RealWeightUpdater()` unconditionally, but today's factory only builds it inside `GENESIS_REAL_LEARNING=1`. **Fix:** `shared_weight_updater` is now `| None`; the wrapper passes `None`; the updater is constructed ONLY inside the learning block (`inner = shared or fresh`). OFF builds nothing new (Tasks Shared-Contract/P2).
- **HIGH-2 (manifest resume lost terminal weights):** resume set `carry_weights=None` then reset (deleting `agent_state.json`). **Fix:** `run()` reads `_read_snapshot_weights(state_dir)` on resume BEFORE the reset, carrying the prior incarnation's evolved weights (Task P4).
- **MED-1 (unstable hash):** `_weights_hash` used process-randomized `hash()`. **Fix:** SHA-256 over `weights.model_dump_json()` (`0x`-prefixed), mirroring the death path; `0x0` for None (Task P4).
- **MED-2 (LoopFactoryProto):** the factory must build a FRESH handle per `/start`. **Fix:** `_supervisor_factory()` constructs a new supervisor per call (Task P5).
- **MED-3 (shared scope leaked across /start cycles):** the shared updater/advisor were built outside `_supervisor_factory`. **Fix:** built INSIDE `_supervisor_factory` → shared across the lives of ONE run, fresh per `/start`; updater gated on `GENESIS_REAL_LEARNING` (Task P5).
- **MED-4 (cold-start `_mb` leak):** the per-life reset left `state_dir/_mb` (MemoryBank + reflections) intact across lives. **Fix:** `_reset_per_life_streams` also `shutil.rmtree`s `_mb` between lives (after the dead life finalized) — per-incarnation memory per spec §7 (Task P4).

**Convergence:** HIGH/MEDIUM → 0 after the above; no rebuttals (all findings were correct against the real code).
