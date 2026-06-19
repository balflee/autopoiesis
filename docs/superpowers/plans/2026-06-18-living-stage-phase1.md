# Living Stage — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the divine economy ON in the running mock-bet loop (tithe rent + deathbed tribute, real dice, events persisted to disk) and ship a new `/living` "Living Stage" dashboard page that renders the live organism, its bets, its mind, and the divine treasury from real data — no backtest, treasury grows from $0.

**Architecture:** Backend emits `tribute`/`tithe`/`agent_died` events that today are dropped by `_NoopStateHook`; we add record models + JSONL streams (`gods_treasury.jsonl`, `deaths.jsonl`) and a `_SandboxStateHook` that writes them, gated behind `SANDBOX_DIVINE_ECONOMY=1` (default OFF = byte-identical). The dashboard's existing 2 s `/api/sandbox` poll loader is extended to read the new streams; new Zustand slices feed a new `/living` page composed of 5 zones that reuse the abyss-themed widgets where possible.

**Tech Stack:** Python 3.11 + Pydantic v2 (backend records/loop), pytest (backend tests); Next.js (app router) + TypeScript + Zustand + Tailwind (dashboard), Vitest/RTL + Playwright (frontend tests). Repo root is the `code/` subdirectory.

**Scope:** Phase 1 only. No reincarnation supervisor (death is terminal; the Lineage zone shows the single current life). Phase 2 (`LiveIncarnationSupervisor` + `incarnation_manifest.json`) is a separate plan. Spec: `docs/superpowers/specs/2026-06-18-living-stage-mockbet-display-design.md`.

**Conventions for every commit:** commit author must be **balflee** (`256016480+balflee@users.noreply.github.com`); never bypass the gitleaks hook; prod LLM is Gemini only (no Anthropic/OpenAI). Run backend tests from `code/`; run dashboard tests from `code/dashboard/`.

---

## File Structure

**Backend (modify):**
- `code/agent/data/sandbox_state.py` — add `GODS_TREASURY_FILENAME`, `DEATHS_FILENAME`; `TributeRecord`, `TitheRecord`, `DeathRecord`; `_DECISION_LIVING_KEYS`; `odds_yes`/`odds_no`/`fee_floor_pct` + `signal_scores` fields on `DecisionRecord`; `incarnation_number` on `AgentStateSnapshot`; `SandboxStateWriter` paths + `append_tribute`/`append_tithe`/`append_death`; make `append_decision` `model_dump_json(exclude=…)` the unpopulated living keys (byte-identical when off).
- `code/agent/runtime/sandbox_phase2_loop.py` — surface `dice_roll` in the tribute emit; populate `odds_yes`/`odds_no`/`fee_floor_pct` on the `DecisionRecord`.
- `code/agent/server/main.py` — add `_SandboxStateHook`; read `SANDBOX_DIVINE_ECONOMY`; in the loop factory pass `tribute_policy`/`tribute_rng`/`divine_tithe` + the writing hook when enabled.

**Dashboard (modify):**
- `code/dashboard/lib/sandbox_state_shared.ts` — extend `SandboxStateBundle` + add data types.
- `code/dashboard/lib/load_sandbox_state.server.ts` — read the two new streams; compute cumulative treasury + lineage.
- `code/dashboard/lib/wsStore.ts` — new slices + ingest cases + selectors.
- `code/dashboard/lib/load_sandbox_state.ts` — lift new bundle fields into the store.

**Dashboard (create):**
- `code/dashboard/app/living/page.tsx`, `code/dashboard/app/living/LivingStageBody.tsx`
- `code/dashboard/components/living/LivingOrganism.tsx` (Z1)
- `code/dashboard/components/living/DivineEventStream.tsx`, `DivineTreasury.tsx` (Z2)
- `code/dashboard/components/living/CurrentMarketCard.tsx` (Z3)
- `code/dashboard/components/living/FusionSignalsRail.tsx` (Z4)
- `code/dashboard/components/living/IncarnationLineage.tsx` (Z5)

---

## Shared Contract (locked names — every task below uses these exact identifiers)

**New `sandbox_state.py` symbols:**

```python
GODS_TREASURY_FILENAME: Final[str] = "gods_treasury.jsonl"   # interleaved tribute+tithe, 'type' discriminant
DEATHS_FILENAME: Final[str] = "deaths.jsonl"                  # one DeathRecord per death

class TributeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tribute"] = "tribute"
    tribute_id: str
    ts: str                                  # ISO-8601 UTC
    tick: int = Field(ge=0)
    amount_usd: float = Field(ge=0.0)
    success: bool
    breath_after: float = Field(ge=0.0)
    bankroll_after: float
    dice_roll: float | None = Field(default=None, ge=0.0, le=1.0)  # the gods' roll, omit-when-None

class TitheRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["tithe"] = "tithe"
    tithe_id: str
    ts: str
    tick: int = Field(ge=0)
    paid_usd: float = Field(ge=0.0)          # cash rent (0.0 if breath was taken)
    breath_cost: float = Field(ge=0.0)       # breath rent (0.0 if cash was taken)
    breath_after: float = Field(ge=0.0)
    bankroll_after: float

class DeathRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    death_id: str
    ts: str
    incarnation_number: int = Field(ge=0, default=0)
    agent_id: str
    last_tick: int
    cause: Literal["breath_zero", "forced_terminal"] = "breath_zero"
    kill_tx_hash: str | None = None
    tombstone_token_id: str | None = None
    tombstone_tx_hash: str | None = None
    final_bankroll_usd: float
    final_weights_hash: str | None = None
    memory_bank_cid: str | None = None
    last_words: str | None = None
```

**New TS types (`sandbox_state_shared.ts`):**

```typescript
export interface TributeRecordData {
  readonly type: "tribute";
  readonly tribute_id: string;
  readonly ts: string;
  readonly tick: number;
  readonly amount_usd: number;
  readonly success: boolean;
  readonly breath_after: number;
  readonly bankroll_after: number;
  readonly dice_roll?: number;
}
export interface TitheRecordData {
  readonly type: "tithe";
  readonly tithe_id: string;
  readonly ts: string;
  readonly tick: number;
  readonly paid_usd: number;
  readonly breath_cost: number;
  readonly breath_after: number;
  readonly bankroll_after: number;
}
export type GodsTreasuryRecordData = TributeRecordData | TitheRecordData;
export interface IncarnationLineageEntry {
  readonly incarnation_number: number;
  readonly last_tick: number;
  readonly cause: string;
  readonly final_bankroll_usd: number;
  readonly ts: string;
}
```

**New `SandboxStateBundle` fields:** `recent_gods_treasury: readonly GodsTreasuryRecordData[]`, `gods_revenue_cumulative_usd: number`, `incarnation_number: number`, `incarnation_lineage: readonly IncarnationLineageEntry[]`.

**New env flag:** `SANDBOX_DIVINE_ECONOMY` — `"1"` enables tithe+tribute wiring + the writing state hook + persistence of the new `DecisionRecord` living-stage fields. Default unset/OFF = today's behavior **byte-identical** (no new JSONL keys anywhere).

**New constant (main.py):** `_GODS_DICE_SEED: Final[int] = 0xA7D1CE` (deterministic, audit-reproducible dice).

**New loop constructor param:** `record_living_stage_fields: bool = False` — when `True` (set by the factory = `_divine_economy`), `_tick` stamps `odds_yes`/`odds_no`/`fee_floor_pct`/`signal_scores` onto the `DecisionRecord`; when `False` those stay `None`/`{}` and are OMITTED on disk, so `decisions.jsonl` is byte-identical to today. This is what keeps the flag-OFF guarantee: the odds/signals are NOT populated unconditionally (Round-1 HIGH-1 / byte-identicality root cause).

**`DecisionRecord` additions (Task A4):** `odds_yes`/`odds_no`/`fee_floor_pct` (`float | None = None`) **and** `signal_scores: dict[str, float] = Field(default_factory=dict)` — the per-engine scores, mirroring `BetRecord.signal_scores`. `signal_scores` is the ONLY live source of the 5-engine values on the **poll** path (the loop already builds this dict at decide-time; `DecisionRecord` did not persist it, so the dashboard had no signals without it — Round-1 MED-2/3 root cause). All four are omitted on disk when None/empty.

**Serialization rule (Round-1 HIGH-1):** `append_decision` must stay **compact** Pydantic JSON. Do NOT switch to `json.dumps(...)` — its default separators add spaces (`", "`/`": "`) and would change every existing decision row's bytes. Use `decision.model_dump_json(exclude=<keys that are None/empty>)`, which keeps the compact format and omits the new keys when absent → byte-identical flag-OFF.

**Decision-source for Z3/Z4 (Round-1 MED-2/3):** the `/living` zones read the **newest `decisionFeed` entry** (`selectDecisionFeed(s)[0]`), NOT `latestDecision` — the 2 s poll path populates `decisionFeed` (via the `decision_feed` ingest) but never `latestDecision` (only a live WS `decision` frame sets that). `DecisionFeedEntry` uses `action` (NOT `kind`), `side`, `size_usd`, `edge_pct`, `signals`, `market_id`; Task B1 extends it + `toDecisionFeedEntries` + `DecisionPayload` with `odds_yes`/`odds_no`/`fee_floor_pct` and maps `signals` from `signal_scores`.

---

## Track A — Backend: divine economy data

### Task A1: New record models + filename constants

**Files:**
- Modify: `code/agent/data/sandbox_state.py` (constants block ~92-105; new models after `SettledBetRecord` ~263)
- Test: `code/tests/agent/data/test_divine_records.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/data/test_divine_records.py
import json
import pytest
from pydantic import ValidationError
from agent.data.sandbox_state import (
    TributeRecord, TitheRecord, DeathRecord,
    GODS_TREASURY_FILENAME, DEATHS_FILENAME,
)


def test_filenames():
    assert GODS_TREASURY_FILENAME == "gods_treasury.jsonl"
    assert DEATHS_FILENAME == "deaths.jsonl"


def test_tribute_record_roundtrip_with_dice():
    rec = TributeRecord(
        tribute_id="t1", ts="2026-06-18T00:00:00+00:00", tick=812,
        amount_usd=2000.0, success=True, breath_after=35.0,
        bankroll_after=1240.0, dice_roll=0.42,
    )
    row = json.loads(rec.model_dump_json())
    assert row["type"] == "tribute"
    assert row["dice_roll"] == 0.42
    assert TributeRecord.model_validate(row) == rec


def test_tithe_record_breath_paid():
    rec = TitheRecord(
        tithe_id="h1", ts="2026-06-18T00:00:00+00:00", tick=20,
        paid_usd=0.0, breath_cost=5.0, breath_after=70.0, bankroll_after=0.0,
    )
    assert rec.type == "tithe"
    assert TitheRecord.model_validate(json.loads(rec.model_dump_json())) == rec


def test_death_record_defaults_incarnation_zero():
    rec = DeathRecord(
        death_id="d1", ts="2026-06-18T00:00:00+00:00", agent_id="agent-x",
        last_tick=999, final_bankroll_usd=12.5,
    )
    assert rec.incarnation_number == 0
    assert rec.cause == "breath_zero"


def test_extra_forbid_rejects_unknown_field():
    with pytest.raises(ValidationError):
        TributeRecord(
            tribute_id="t1", ts="x", tick=1, amount_usd=1.0, success=False,
            breath_after=0.0, bankroll_after=0.0, bogus=1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/data/test_divine_records.py -q`
Expected: FAIL with `ImportError: cannot import name 'TributeRecord'`.

- [ ] **Step 3: Implement — add constants + models**

In `code/agent/data/sandbox_state.py`, add to the filename constants block (after `PROPOSALS_FILENAME`, ~line 105):

```python
# Living Stage Phase 1 — the divine economy streams. gods_treasury.jsonl
# interleaves tribute + tithe rows (discriminated by ``type``); deaths.jsonl
# carries one DeathRecord per incarnation death (drives the lineage timeline).
GODS_TREASURY_FILENAME: Final[str] = "gods_treasury.jsonl"
DEATHS_FILENAME: Final[str] = "deaths.jsonl"
```

Add the three model classes after `SettledBetRecord` (~line 263) — exactly the bodies from the Shared Contract above.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/data/test_divine_records.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add code/agent/data/sandbox_state.py code/tests/agent/data/test_divine_records.py
git commit -m "feat(sandbox-state): add TributeRecord/TitheRecord/DeathRecord + treasury stream filenames"
```

---

### Task A2: Writer paths + append methods

**Files:**
- Modify: `code/agent/data/sandbox_state.py` (`SandboxStateWriter` paths ~437-482, append methods ~488-537)
- Test: `code/tests/agent/data/test_divine_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/data/test_divine_writer.py
import json
from agent.data.sandbox_state import (
    SandboxStateWriter, TributeRecord, TitheRecord, DeathRecord,
    GODS_TREASURY_FILENAME, DEATHS_FILENAME, iter_jsonl,
)


def test_append_tribute_and_tithe_interleave(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_tithe(TitheRecord(tithe_id="h1", ts="t", tick=20, paid_usd=20.0,
                               breath_cost=0.0, breath_after=80.0, bankroll_after=980.0))
    w.append_tribute(TributeRecord(tribute_id="t1", ts="t", tick=40, amount_usd=2000.0,
                                   success=True, breath_after=35.0, bankroll_after=0.0,
                                   dice_roll=0.5))
    rows = iter_jsonl(tmp_path / GODS_TREASURY_FILENAME)
    assert [r["type"] for r in rows] == ["tithe", "tribute"]
    assert "dice_roll" not in iter_jsonl(tmp_path / GODS_TREASURY_FILENAME)[0]  # tithe has none


def test_append_death(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_death(DeathRecord(death_id="d1", ts="t", agent_id="a", last_tick=9,
                               final_bankroll_usd=1.0))
    rows = iter_jsonl(tmp_path / DEATHS_FILENAME)
    assert len(rows) == 1 and rows[0]["cause"] == "breath_zero"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/data/test_divine_writer.py -q`
Expected: FAIL with `AttributeError: 'SandboxStateWriter' object has no attribute 'append_tithe'`.

- [ ] **Step 3: Implement — paths + append methods**

Add two path properties beside `proposals_path` (~line 482):

```python
    @property
    def gods_treasury_path(self) -> Path:
        return self._root / GODS_TREASURY_FILENAME

    @property
    def deaths_path(self) -> Path:
        return self._root / DEATHS_FILENAME
```

Add three append methods beside `append_proposal` (~line 537). `exclude_none=True` drops the optional `dice_roll` on tithe/no-dice rows:

```python
    def append_tribute(self, tribute: TributeRecord) -> None:
        """Append one tribute offering to ``gods_treasury.jsonl`` (Living Stage P1)."""
        self._append_jsonl(
            self.gods_treasury_path, tribute.model_dump_json(exclude_none=True)
        )

    def append_tithe(self, tithe: TitheRecord) -> None:
        """Append one tithe (divine rent) row to ``gods_treasury.jsonl``."""
        self._append_jsonl(self.gods_treasury_path, tithe.model_dump_json())

    def append_death(self, death: DeathRecord) -> None:
        """Append one death row to ``deaths.jsonl`` (drives the lineage timeline)."""
        self._append_jsonl(self.deaths_path, death.model_dump_json(exclude_none=True))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/data/test_divine_writer.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add code/agent/data/sandbox_state.py code/tests/agent/data/test_divine_writer.py
git commit -m "feat(sandbox-state): SandboxStateWriter append_tribute/append_tithe/append_death + paths"
```

---

### Task A3: `incarnation_number` on the snapshot

**Files:**
- Modify: `code/agent/data/sandbox_state.py` (`AgentStateSnapshot` fields ~331-345)
- Test: `code/tests/agent/data/test_snapshot_incarnation.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/data/test_snapshot_incarnation.py
from agent.data.sandbox_state import AgentStateSnapshot


def _base(**kw):
    d = dict(snapshot_ts="t", phase="PHASE_2_APPRENTICE", breath=50.0,
             bankroll_usd=100.0, phase_age_days=0.0)
    d.update(kw)
    return d


def test_incarnation_defaults_zero():
    assert AgentStateSnapshot(**_base()).incarnation_number == 0


def test_incarnation_roundtrips():
    snap = AgentStateSnapshot(**_base(incarnation_number=3))
    reloaded = AgentStateSnapshot.model_validate_json(snap.model_dump_json())
    assert reloaded.incarnation_number == 3


def test_old_snapshot_without_field_still_loads():
    # a pre-P1 snapshot JSON has no incarnation_number key
    snap = AgentStateSnapshot(**_base())
    row = snap.model_dump()
    row.pop("incarnation_number", None)
    assert AgentStateSnapshot.model_validate(row).incarnation_number == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/data/test_snapshot_incarnation.py -q`
Expected: FAIL — `incarnation_number` is unknown (`extra='forbid'`) / attribute missing.

- [ ] **Step 3: Implement**

In `AgentStateSnapshot`, add the field after `last_tick` (~line 339):

```python
    # Living Stage P1 — which incarnation this snapshot belongs to. Always 0
    # until the Phase 2 reincarnation supervisor lands (it stamps the running
    # idx). Default 0 + extra-tolerant load so pre-P1 snapshots rehydrate.
    incarnation_number: int = Field(ge=0, default=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/data/test_snapshot_incarnation.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add code/agent/data/sandbox_state.py code/tests/agent/data/test_snapshot_incarnation.py
git commit -m "feat(sandbox-state): AgentStateSnapshot.incarnation_number (default 0)"
```

---

### Task A4: `DecisionRecord` odds fields + omit-when-None on disk

**Files:**
- Modify: `code/agent/data/sandbox_state.py` (`DecisionRecord` ~277-288; new `_DECISION_LIVING_KEYS`; `append_decision` ~515-517 → `model_dump_json(exclude=…)`)
- Test: `code/tests/agent/data/test_decision_odds.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/data/test_decision_odds.py
import json
from agent.data.sandbox_state import (
    DecisionRecord, SandboxStateWriter, iter_jsonl, DECISIONS_FILENAME,
)


def _dec(**kw):
    d = dict(tick=1, ts="t", market_id="m1", kind="BET", size_usd=50.0,
             side="YES", edge_pct=0.04, no_bet_reason=None,
             breath_after=72.0, bankroll_usd_after=1240.0)
    d.update(kw)
    return DecisionRecord(**d)


def test_living_fields_present_serialized(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_decision(_dec(odds_yes=0.58, odds_no=0.42, fee_floor_pct=0.018,
                           signal_scores={"tennis_technical": 0.12}))
    row = iter_jsonl(tmp_path / DECISIONS_FILENAME)[0]
    assert row["odds_yes"] == 0.58 and row["odds_no"] == 0.42 and row["fee_floor_pct"] == 0.018
    assert row["signal_scores"] == {"tennis_technical": 0.12}


def test_living_fields_absent_omitted_AND_byte_identical(tmp_path):
    # HIGH-1: with no living fields, the on-disk line must be the COMPACT
    # Pydantic JSON of the pre-P1 fields only — byte-identical, no spaces, no
    # odds_*/signal_scores keys.
    w = SandboxStateWriter(root=tmp_path)
    dec = _dec()                               # no odds, empty signal_scores
    w.append_decision(dec)
    raw = (tmp_path / DECISIONS_FILENAME).read_text(encoding="utf-8").strip()
    # LOW-1: the STRONG byte assertion — the on-disk line is EXACTLY the compact
    # Pydantic serialization with the 4 living keys excluded. Since those keys
    # are the only P1 additions and exclusion preserves the original field set
    # AND order, this string is byte-identical to the pre-P1 model_dump_json().
    assert raw == dec.model_dump_json(
        exclude={"odds_yes", "odds_no", "fee_floor_pct", "signal_scores"}
    )
    assert "odds_yes" not in raw and "signal_scores" not in raw and "fee_floor_pct" not in raw
    assert ", " not in raw and '": ' not in raw   # compact: no separator spaces (NOT json.dumps default)
    parsed = json.loads(raw)
    assert parsed["no_bet_reason"] is None          # existing nullable still serialized as null
    assert "odds_no" not in parsed


def test_no_bet_idle_tick_byte_identical(tmp_path):
    w = SandboxStateWriter(root=tmp_path)
    w.append_decision(_dec(kind="NO_BET", side=None, edge_pct=None,
                           no_bet_reason="no_eligible_market", market_id=None, size_usd=0.0))
    row = iter_jsonl(tmp_path / DECISIONS_FILENAME)[0]
    assert "odds_yes" not in row and "signal_scores" not in row
    assert row["no_bet_reason"] == "no_eligible_market"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/data/test_decision_odds.py -q`
Expected: FAIL — `DecisionRecord` rejects `odds_yes`/`odds_no`/`fee_floor_pct`/`signal_scores` (`extra='forbid'`, fields not yet added).

- [ ] **Step 3: Implement**

Add the three fields to `DecisionRecord` (after `bankroll_usd_after`, ~line 288):

```python
    # Living Stage P1 — absolute YES/NO odds at decision time + the edge floor
    # the fused edge had to clear, AND the per-engine scores. Optional/empty so
    # non-live + flag-OFF ticks stay valid; OMITTED from JSONL when None/empty
    # (see ``append_decision``) so flag-off rows stay BYTE-identical to the
    # pre-P1 shape. Populated by ``_tick`` ONLY when ``record_living_stage_fields``
    # is on (Task A5). ``signal_scores`` mirrors ``BetRecord.signal_scores`` and
    # is the live source for the dashboard's 5-engine rail on the poll path.
    odds_yes: float | None = Field(default=None, ge=0.0, le=1.0)
    odds_no: float | None = Field(default=None, ge=0.0, le=1.0)
    fee_floor_pct: float | None = None
    signal_scores: dict[str, float] = Field(default_factory=dict)
```

Add the key-name constant next to `bet_record_jsonl_dict` (~line 209):

```python
# Living Stage P1 — the decision fields that are omitted on disk when
# unpopulated (None odds / empty signal_scores), so a flag-OFF decision row
# stays byte-identical to the pre-P1 shape.
_DECISION_LIVING_KEYS: Final[tuple[str, ...]] = (
    "odds_yes", "odds_no", "fee_floor_pct",
)
```

Rewrite `append_decision` (~line 515) — **compact** Pydantic JSON with `exclude` (Round-1 HIGH-1: do NOT use `json.dumps`, whose default separators add spaces and would change every row's bytes):

```python
    def append_decision(self, decision: DecisionRecord) -> None:
        """Append one decision line to ``decisions.jsonl``.

        P1: the living-stage fields (odds_*, signal_scores) are EXCLUDED when
        unpopulated so a flag-off row is byte-identical to the pre-P1 compact
        Pydantic JSON. ``model_dump_json`` (not ``json.dumps``) keeps the
        compact ``,``/``:`` separators of every existing decision line.
        """
        exclude: set[str] = {
            k for k in _DECISION_LIVING_KEYS if getattr(decision, k) is None
        }
        if not decision.signal_scores:
            exclude.add("signal_scores")
        self._append_jsonl(
            self.decisions_path,
            decision.model_dump_json(exclude=exclude or None),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/data/test_decision_odds.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full sandbox_state suite to confirm no regression**

Run: `cd code && python -m pytest tests/agent/data/ -q`
Expected: PASS (existing tests green; decisions.jsonl byte-identical when odds absent).

- [ ] **Step 6: Commit**

```bash
git add code/agent/data/sandbox_state.py code/tests/agent/data/test_decision_odds.py
git commit -m "feat(sandbox-state): DecisionRecord odds_yes/odds_no/fee_floor_pct (omit-when-None on disk)"
```

---

### Task A5: Surface `dice_roll` + odds in the loop

**Files:**
- Modify: `code/agent/runtime/sandbox_phase2_loop.py` (tribute roll ~2108-2140; DecisionRecord build ~1863-1876)
- Test: `code/tests/agent/runtime/test_loop_divine_emit_fields.py`

- [ ] **Step 1: Write the failing test** (drives both the dice_roll emit kwarg and the odds on the decision record)

```python
# code/tests/agent/runtime/test_loop_divine_emit_fields.py
import inspect
from agent.runtime import sandbox_phase2_loop as L


def test_tribute_emit_carries_dice_roll():
    src = inspect.getsource(L.SandboxPhase2Loop._attempt_tribute)
    # the inline rng draw is hoisted to a named `roll` and forwarded to the emit
    assert "roll = self._tribute_rng.random()" in src
    assert "success = roll < p" in src
    assert "dice_roll=roll" in src


def test_constructor_accepts_record_living_stage_fields():
    import inspect as _i
    sig = _i.signature(L.SandboxPhase2Loop.__init__)
    assert "record_living_stage_fields" in sig.parameters
    assert sig.parameters["record_living_stage_fields"].default is False  # default OFF


def test_decision_record_living_fields_gated():
    src = inspect.getsource(L.SandboxPhase2Loop._tick)
    # odds + signal_scores are stamped ONLY behind the flag (byte-identical OFF)
    assert "self._record_living_stage_fields" in src
    assert "odds_yes=" in src and "odds_no=" in src and "fee_floor_pct=" in src
    assert "signal_scores=" in src
```

> Note: this guards the wiring at source level (the emit kwargs + record fields). The end-to-end behavioral assertion (a real tithe/tribute row lands on disk) is covered in Task A6's integration test once the writing hook exists.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/runtime/test_loop_divine_emit_fields.py -q`
Expected: FAIL (`dice_roll=roll` / `odds_yes=` not found).

- [ ] **Step 3: Implement — hoist the roll + forward it**

In `_attempt_tribute` (~line 2108) change the inline draw:

```python
        offering = float(amount)
        self._bankroll_usd -= offering
        p = tribute_success_probability(offering)
        roll = self._tribute_rng.random()
        success = roll < p
```

and add `dice_roll=roll` to the emit (~line 2133):

```python
        self._state_hook.emit(
            kind="tribute",
            tick=tick,
            amount_usd=offering,
            success=success,
            breath_after=self._breath,
            bankroll_after=self._bankroll_usd,
            dice_roll=roll,
        )
```

- [ ] **Step 4a: Implement — the gating constructor param**

Add `record_living_stage_fields: bool = False` to `SandboxPhase2Loop.__init__` (beside the other feature flags, ~line 902-923) and store it (~line 945-973):

```python
        record_living_stage_fields: bool = False,
```
```python
        # Living Stage P1 — when True, _tick stamps odds + signal_scores onto
        # the DecisionRecord. Default False → those fields stay None/{} and are
        # omitted on disk, keeping decisions.jsonl byte-identical (the factory
        # sets this = the SANDBOX_DIVINE_ECONOMY flag).
        self._record_living_stage_fields: bool = record_living_stage_fields
```

- [ ] **Step 4b: Implement — GATED odds + signal_scores on the DecisionRecord**

In `_tick`, `inputs.price` is the YES implied probability (NO = `1.0 - inputs.price`, confirmed by the effective-price gate ~line 1779-1783), and the loop already builds a `signal_scores` local (`{name: signal.score ...}`) before `decide()` when `inputs is not None`. Before building the record (~line 1863), derive the gated values:

```python
        # Living Stage P1 — odds + per-engine scores for the dashboard, stamped
        # ONLY behind the flag so decisions.jsonl is byte-identical when off.
        if self._record_living_stage_fields and inputs is not None:
            odds_yes_for_record: float | None = float(inputs.price)
            odds_no_for_record: float | None = 1.0 - float(inputs.price)
            signal_scores_for_record: dict[str, float] = dict(signal_scores or {})
        else:
            odds_yes_for_record = None
            odds_no_for_record = None
            signal_scores_for_record = {}
        # fee_floor_pct: the effective min-edge the fused edge had to clear.
        # If the _tick scope exposes the effective-floor local (read lines
        # 1774-1790 to confirm its exact name — do NOT invent a computation),
        # use it behind the same flag; otherwise leave None (the Mind rail
        # renders the floor only when present).
        fee_floor_for_record: float | None = None  # set from the real local if present
```

Then extend the `DecisionRecord(...)` call (~line 1863) with:

```python
            odds_yes=odds_yes_for_record,
            odds_no=odds_no_for_record,
            fee_floor_pct=fee_floor_for_record,
            signal_scores=signal_scores_for_record,
```

> The exact effective-floor local name is the one execution-time confirmation here (read lines 1774-1790). If no such local exists at the record-build site, `fee_floor_pct` stays `None` — acceptable; the field is optional and the rail degrades gracefully.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/runtime/test_loop_divine_emit_fields.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the loop suite to confirm no regression**

Run: `cd code && python -m pytest tests/agent/runtime/ -q -k "phase2 or loop or tribute or tithe"`
Expected: PASS (existing loop tests green; the new emit kwarg is additive — `_NoopStateHook.emit(**payload)` already absorbs it).

- [ ] **Step 7: Commit**

```bash
git add code/agent/runtime/sandbox_phase2_loop.py code/tests/agent/runtime/test_loop_divine_emit_fields.py
git commit -m "feat(loop): surface tribute dice_roll + decision odds_yes/odds_no/fee_floor"
```

---

### Task A6: `_SandboxStateHook` + flag-gated factory wiring

**Files:**
- Modify: `code/agent/server/main.py` (add `_SandboxStateHook` near `_NoopStateHook` ~1744; wire in `_factory` ~2135-2150)
- Test: `code/tests/agent/server/test_divine_state_hook.py`

- [ ] **Step 1: Write the failing test**

```python
# code/tests/agent/server/test_divine_state_hook.py
import json
from agent.data.sandbox_state import (
    SandboxStateWriter, GODS_TREASURY_FILENAME, DEATHS_FILENAME, iter_jsonl,
)
from agent.server.main import _SandboxStateHook


def test_hook_routes_tribute_tithe_death(tmp_path):
    writer = SandboxStateWriter(root=tmp_path)
    hook = _SandboxStateHook(writer=writer)
    hook.emit(kind="tithe", tick=20, amount_usd=20.0, breath_cost=0.0,
              breath_after=80.0, bankroll_after=980.0)
    hook.emit(kind="tribute", tick=40, amount_usd=2000.0, success=True,
              breath_after=35.0, bankroll_after=0.0, dice_roll=0.5)
    hook.emit(kind="agent_died", agent_id="a", last_tick=99, kill_tx_hash=None,
              tombstone_token_id=None, tombstone_tx_hash=None, bankroll_usd=0.0,
              final_weights_hash=None, memory_bank_cid=None, last_words="bye")
    treasury = iter_jsonl(tmp_path / GODS_TREASURY_FILENAME)
    assert [r["type"] for r in treasury] == ["tithe", "tribute"]
    assert treasury[1]["dice_roll"] == 0.5
    deaths = iter_jsonl(tmp_path / DEATHS_FILENAME)
    assert deaths[0]["final_bankroll_usd"] == 0.0 and deaths[0]["last_words"] == "bye"


def test_hook_ignores_unknown_kind(tmp_path):
    writer = SandboxStateWriter(root=tmp_path)
    hook = _SandboxStateHook(writer=writer)
    hook.emit(kind="phase_transition", to="PHASE_3_MASTER")   # no-op, no crash
    assert not (tmp_path / GODS_TREASURY_FILENAME).exists()


def test_hook_never_raises_on_bad_payload_or_io(tmp_path):
    # MED-4: emit must NEVER raise into the loop (StateHook contract).
    writer = SandboxStateWriter(root=tmp_path)
    hook = _SandboxStateHook(writer=writer)
    hook.emit(kind="tribute", tick=1)          # missing required keys → swallowed
    class _BoomWriter:
        def append_tithe(self, *_a, **_k): raise OSError("disk full")
    boom = _SandboxStateHook(writer=_BoomWriter())   # type: ignore[arg-type]
    boom.emit(kind="tithe", tick=2, amount_usd=20.0, breath_cost=0.0,
              breath_after=80.0, bankroll_after=980.0)   # IO error → swallowed
    # no assertion needed: the test passes iff neither emit raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd code && python -m pytest tests/agent/server/test_divine_state_hook.py -q`
Expected: FAIL with `ImportError: cannot import name '_SandboxStateHook'`.

- [ ] **Step 3: Implement — the writing hook**

Add to `code/agent/server/main.py` after `_NoopStateHook` (~line 1744). It stamps `ts`/ids itself (the loop emits don't carry them) and maps `bankroll_usd`→`final_bankroll_usd`:

```python
class _SandboxStateHook:
    """Living Stage P1 — routes the loop's divine-economy emits to JSONL.

    Unlike _NoopStateHook this PERSISTS the events the dashboard's Living Stage
    reads: tribute + tithe → gods_treasury.jsonl, agent_died → deaths.jsonl.
    The loop emits carry no ``ts``/id (operator-domain), so we stamp them here.
    Unknown kinds are ignored so the hook stays forward-compatible.
    """

    def __init__(self, *, writer: SandboxStateWriter, incarnation_number: int = 0) -> None:
        self._writer = writer
        self._incarnation_number = incarnation_number

    def emit(self, *, kind: str, **payload: Any) -> None:
        # Round-1 MED-4: the StateHook contract (sandbox_settlement_poller.py
        # :209-220) is that emit MUST NOT raise into the caller — a malformed
        # payload or a disk error here must never abort _attempt_tribute /
        # _attempt_tithe / _die. Catch broadly, log, swallow (with a WARN so a
        # treasury/death write loss is visible in logs, not silent).
        try:
            if kind == "tribute":
                self._writer.append_tribute(TributeRecord(
                    tribute_id=uuid.uuid4().hex,
                    ts=datetime.now(timezone.utc).isoformat(),
                    tick=payload["tick"], amount_usd=payload["amount_usd"],
                    success=payload["success"], breath_after=payload["breath_after"],
                    bankroll_after=payload["bankroll_after"],
                    dice_roll=payload.get("dice_roll"),
                ))
            elif kind == "tithe":
                self._writer.append_tithe(TitheRecord(
                    tithe_id=uuid.uuid4().hex,
                    ts=datetime.now(timezone.utc).isoformat(),
                    tick=payload["tick"], paid_usd=payload["amount_usd"],
                    breath_cost=payload["breath_cost"],
                    breath_after=payload["breath_after"],
                    bankroll_after=payload["bankroll_after"],
                ))
            elif kind == "agent_died":
                self._writer.append_death(DeathRecord(
                    death_id=uuid.uuid4().hex,
                    ts=datetime.now(timezone.utc).isoformat(),
                    incarnation_number=self._incarnation_number,
                    agent_id=payload["agent_id"], last_tick=payload["last_tick"],
                    # LOW-6: _die's sole trigger is breath<=0 (forced-terminal
                    # also routes through breath→0), so breath_zero is the true
                    # mechanism label; honor an explicit cause if a future emit
                    # supplies one. forced_terminal is reserved for Phase 2.
                    cause=payload.get("cause", "breath_zero"),
                    kill_tx_hash=payload.get("kill_tx_hash"),
                    tombstone_token_id=payload.get("tombstone_token_id"),
                    tombstone_tx_hash=payload.get("tombstone_tx_hash"),
                    final_bankroll_usd=payload["bankroll_usd"],
                    final_weights_hash=payload.get("final_weights_hash"),
                    memory_bank_cid=payload.get("memory_bank_cid"),
                    last_words=payload.get("last_words"),
                ))
            # any other kind → ignored (forward-compatible)
        except Exception:  # noqa: BLE001 — hook contract: never raise into the loop
            logger.warning("‹_SandboxStateHook› dropped a %s event", kind, exc_info=True)
```

> The `try/except` is mandatory (Round-1 MED-4) and `logger` must be the module logger already imported in `main.py`. Add a test that a malformed payload (missing a required key) and a writer raising do NOT propagate out of `emit`.

Ensure the imports exist at the top of `main.py` (add any missing): `import uuid`, `from datetime import datetime, timezone`, and from `agent.data.sandbox_state` add `TributeRecord, TitheRecord, DeathRecord` to the existing import group; from `agent.runtime.tribute` add `ReflexTributePolicy`; and `import random` + the dice seed constant near the other module constants:

```python
_GODS_DICE_SEED: Final[int] = 0xA7D1CE  # deterministic, audit-reproducible gods' dice
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd code && python -m pytest tests/agent/server/test_divine_state_hook.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the factory-wiring test**

```python
# append to code/tests/agent/server/test_divine_state_hook.py
import os
from agent.server import main as M


def test_factory_off_uses_noop_hook(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX_DIVINE_ECONOMY", raising=False)
    loop = _build_test_loop(M, tmp_path)              # helper builds via the prod factory
    assert type(loop._state_hook).__name__ == "_NoopStateHook"
    assert loop._tribute_policy is None and loop._divine_tithe is False
    assert loop._record_living_stage_fields is False   # byte-identical OFF


def test_factory_on_enables_divine_economy(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_DIVINE_ECONOMY", "1")
    loop = _build_test_loop(M, tmp_path)
    assert type(loop._state_hook).__name__ == "_SandboxStateHook"
    assert loop._tribute_policy is not None and loop._tribute_rng is not None
    assert loop._divine_tithe is True
    assert loop._record_living_stage_fields is True
```

> `_build_test_loop` constructs a loop through `_build_production_loop_factory(...)` with in-memory test doubles (sandbox chain adapter, a stub tick source, a `Clock` double). Build it by mirroring the existing factory-construction test in `code/tests/agent/server/` (search for the existing `_build_production_loop_factory` test and reuse its fixtures); do not invent new doubles.

- [ ] **Step 6: Run to verify it fails**

Run: `cd code && python -m pytest tests/agent/server/test_divine_state_hook.py -q`
Expected: FAIL (factory still wires `_NoopStateHook` + no tribute policy).

- [ ] **Step 7: Implement — flag-gated factory wiring**

In `_build_production_loop_factory._factory` (~line 2135), after `writer = SandboxStateWriter(root=state_dir)` and the `_sandbox_live` read, add:

```python
        _divine_economy = os.environ.get("SANDBOX_DIVINE_ECONOMY") == "1"
        if _divine_economy:
            state_hook: Any = _SandboxStateHook(writer=writer)
            tribute_policy: TributePolicy | None = ReflexTributePolicy()
            tribute_rng: random.Random | None = random.Random(_GODS_DICE_SEED)
        else:
            state_hook = _NoopStateHook()
            tribute_policy = None
            tribute_rng = None
```

Then change the `SandboxPhase2Loop(...)` construction kwargs: replace `state_hook=_NoopStateHook(),` with `state_hook=state_hook,`, and add the divine kwargs alongside the existing ones:

```python
            tribute_policy=tribute_policy,
            tribute_rng=tribute_rng,
            divine_tithe=_divine_economy,
            record_living_stage_fields=_divine_economy,
```

(Leave `tithe_every`/`tithe_amount_usd`/`tithe_breath_cost` at their loop defaults — 20 markets / $20 / 5 breath. The operator can raise `tithe_every` later to slow the bleed for a calm demo; not needed for P1.)

- [ ] **Step 8: Run to verify it passes**

Run: `cd code && python -m pytest tests/agent/server/test_divine_state_hook.py -q`
Expected: PASS (4 passed).

- [ ] **Step 9: Run the server suite + assert default byte-identicality**

Run: `cd code && python -m pytest tests/agent/server/ -q`
Expected: PASS — with the flag OFF the loop is constructed exactly as before (`_NoopStateHook`, no tribute policy, `divine_tithe=False`).

- [ ] **Step 10: Commit**

```bash
git add code/agent/server/main.py code/tests/agent/server/test_divine_state_hook.py
git commit -m "feat(server): _SandboxStateHook + SANDBOX_DIVINE_ECONOMY flag wires tithe/tribute into the live loop"
```

---

## Track B — Dashboard data path (poll bundle → store)

### Task B1: Extend the bundle type + server loader

**Files:**
- Modify: `code/dashboard/lib/sandbox_state_shared.ts` (add types + extend `SandboxStateBundle` ~96-106)
- Modify: `code/dashboard/lib/load_sandbox_state.server.ts` (filename consts ~45-47; reads + fold ~96-143)
- Test: `code/dashboard/lib/__tests__/load_sandbox_divine.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// code/dashboard/lib/__tests__/load_sandbox_divine.test.ts
import { describe, it, expect } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { loadSandboxBundle } from "../load_sandbox_state.server";

async function tmpRoot(): Promise<string> {
  return await fs.mkdtemp(path.join(os.tmpdir(), "living-"));
}

describe("loadSandboxBundle divine streams", () => {
  it("reads treasury + deaths, computes cumulative + lineage", async () => {
    const root = await tmpRoot();
    await fs.writeFile(path.join(root, "gods_treasury.jsonl"),
      [JSON.stringify({ type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 }),
       JSON.stringify({ type: "tribute", tribute_id: "t1", ts: "t", tick: 40, amount_usd: 2000, success: true, breath_after: 35, bankroll_after: 0, dice_roll: 0.5 }),
       JSON.stringify({ type: "tribute", tribute_id: "t2", ts: "t", tick: 50, amount_usd: 600, success: false, breath_after: 0, bankroll_after: 0, dice_roll: 0.9 })].join("\n") + "\n");
    await fs.writeFile(path.join(root, "deaths.jsonl"),
      JSON.stringify({ death_id: "d1", ts: "t", incarnation_number: 0, agent_id: "a", last_tick: 50, cause: "breath_zero", final_bankroll_usd: 0 }) + "\n");
    const bundle = await loadSandboxBundle({ root });
    // cumulative = successful tributes ($2000) + cash tithes ($20); failed tribute NOT counted
    expect(bundle.gods_revenue_cumulative_usd).toBe(2020);
    expect(bundle.recent_gods_treasury).toHaveLength(3);
    expect(bundle.incarnation_lineage).toHaveLength(1);
    expect(bundle.incarnation_lineage[0].cause).toBe("breath_zero");
  });

  it("missing streams → empty, no throw", async () => {
    const root = await tmpRoot();
    const bundle = await loadSandboxBundle({ root });
    expect(bundle.recent_gods_treasury).toEqual([]);
    expect(bundle.gods_revenue_cumulative_usd).toBe(0);
    expect(bundle.incarnation_lineage).toEqual([]);
    expect(bundle.incarnation_number).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run lib/__tests__/load_sandbox_divine.test.ts`
Expected: FAIL (`gods_revenue_cumulative_usd` undefined).

- [ ] **Step 3: Implement — types**

In `sandbox_state_shared.ts` add the `TributeRecordData`/`TitheRecordData`/`GodsTreasuryRecordData`/`IncarnationLineageEntry` types from the Shared Contract, and extend `SandboxStateBundle`:

```typescript
export interface SandboxStateBundle {
  readonly snapshot: AgentStateSnapshotData | null;
  readonly recent_decisions: readonly DecisionRecordData[];
  readonly recent_settled: readonly SettledBetRecordData[];
  readonly lag_alerts: readonly LagAlert[];
  readonly served_ts: string;
  readonly is_mock: boolean;
  // Living Stage P1 — divine economy.
  readonly recent_gods_treasury: readonly GodsTreasuryRecordData[];
  readonly gods_revenue_cumulative_usd: number;
  readonly incarnation_number: number;
  readonly incarnation_lineage: readonly IncarnationLineageEntry[];
}
```

Also extend the decision types so the poll path can carry the new fields into the store (Round-1 MED-2/3 — Z3/Z4 read the `decisionFeed`, whose entries come from `toDecisionFeedEntries(recent_decisions, recent_settled)`):

- In `sandbox_state_shared.ts`, add to `DecisionRecordData`: `odds_yes?: number; odds_no?: number; fee_floor_pct?: number; signal_scores?: Record<string, number>;`
- In `types.ts`, add to BOTH `DecisionPayload` and `DecisionFeedEntry`: `readonly odds_yes?: number; readonly odds_no?: number; readonly fee_floor_pct?: number;` (`DecisionFeedEntry` already has `signals?: EngineSignalMap` and `action`/`side`/`size_usd`/`edge_pct`/`market_id`).
- In `toDecisionFeedEntries` (`sandbox_state_shared.ts:282`), map the new fields onto each entry (mirroring the existing conditional-spread style):

```typescript
      ...(d.odds_yes != null ? { odds_yes: d.odds_yes } : {}),
      ...(d.odds_no != null ? { odds_no: d.odds_no } : {}),
      ...(d.fee_floor_pct != null ? { fee_floor_pct: d.fee_floor_pct } : {}),
      ...(d.signal_scores && Object.keys(d.signal_scores).length
        ? { signals: d.signal_scores as EngineSignalMap }
        : {}),
```

(Import `EngineSignalMap` into `sandbox_state_shared.ts` if not already.) This makes the 5-engine `signals` + odds flow through the EXISTING `decision_feed` ingest the poll path already fires — no `latestDecision` dependency.

- [ ] **Step 4: Implement — loader**

In `load_sandbox_state.server.ts` add filename constants (~line 47):

```typescript
const GODS_TREASURY_FILENAME = "gods_treasury.jsonl";
const DEATHS_FILENAME = "deaths.jsonl";
```

Before the `return` in `loadSandboxBundle` (~line 130), add two defensive reads mirroring the decisions/settled pattern, then the fold:

```typescript
  const treasuryPath = path.join(root, GODS_TREASURY_FILENAME);
  let treasury: GodsTreasuryRecordData[] = [];
  try {
    const raw = await fs.readFile(treasuryPath, "utf-8");
    treasury = lastN(parseJsonl<GodsTreasuryRecordData>(raw), tailN);
  } catch (err) {
    if (dirExists && err instanceof Error && err.message && !/ENOENT/.test(err.message)) {
      errors.push({ kind: "fs_error", detail: `gods_treasury.jsonl read failed: ${err.message}`, severity: "error" });
    }
    treasury = [];
  }

  const deathsPath = path.join(root, DEATHS_FILENAME);
  let deaths: IncarnationLineageEntry[] = [];
  try {
    const raw = await fs.readFile(deathsPath, "utf-8");
    deaths = parseJsonl<IncarnationLineageEntry>(raw);
  } catch (err) {
    if (dirExists && err instanceof Error && err.message && !/ENOENT/.test(err.message)) {
      errors.push({ kind: "fs_error", detail: `deaths.jsonl read failed: ${err.message}`, severity: "error" });
    }
    deaths = [];
  }

  // Cumulative gods revenue = successful tributes + cash tithes (breath-paid
  // tithes are NOT converted to USD — see spec §1 honesty constraint). The fold
  // walks the FULL stream, not the tailed slice, so cumulative is exact.
  let gods_revenue_cumulative_usd = 0;
  try {
    const allRaw = await fs.readFile(treasuryPath, "utf-8");
    for (const r of parseJsonl<GodsTreasuryRecordData>(allRaw)) {
      if (r.type === "tribute" && r.success) gods_revenue_cumulative_usd += r.amount_usd;
      else if (r.type === "tithe") gods_revenue_cumulative_usd += r.paid_usd;
    }
  } catch { /* ENOENT → 0 */ }
```

Extend the returned object:

```typescript
  return {
    snapshot,
    recent_decisions: decisions,
    recent_settled: settled,
    lag_alerts,
    served_ts: new Date(now()).toISOString(),
    is_mock: false,
    recent_gods_treasury: treasury,
    gods_revenue_cumulative_usd,
    incarnation_number: snapshot?.incarnation_number ?? 0,
    incarnation_lineage: deaths,
  };
```

(Add `incarnation_number?: number` to `AgentStateSnapshotData` in `sandbox_state_shared.ts` so `snapshot?.incarnation_number` type-checks. Import the new types at the top of the loader.)

- [ ] **Step 5: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run lib/__tests__/load_sandbox_divine.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 6: Update the mock bundle + any EMPTY_BUNDLE constants**

Search `code/dashboard/lib` for `is_mock: true` / `EMPTY_BUNDLE` literals and add the four new fields (`recent_gods_treasury: []`, `gods_revenue_cumulative_usd: 0`, `incarnation_number: 0`, `incarnation_lineage: []`) so the type compiles everywhere.

Run: `cd code/dashboard && npx tsc --noEmit`
Expected: no type errors.

- [ ] **Step 7: Commit**

```bash
git add code/dashboard/lib/sandbox_state_shared.ts code/dashboard/lib/load_sandbox_state.server.ts code/dashboard/lib/__tests__/load_sandbox_divine.test.ts
git commit -m "feat(dashboard): loadSandboxBundle reads gods_treasury + deaths, folds cumulative + lineage"
```

---

### Task B2: wsStore slices + ingest + selectors

**Files:**
- Modify: `code/dashboard/lib/wsStore.ts` (WsState ~102; `initial`; ingest switch ~200; selectors ~end)
- Test: `code/dashboard/lib/__tests__/wsStore_divine.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// code/dashboard/lib/__tests__/wsStore_divine.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { useWsStore, selectDivineEvents, selectDivineTreasury, selectIncarnationNumber, selectReincarnationLineage } from "../wsStore";

describe("wsStore divine slices", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("setDivineState replaces the divine slices", () => {
    useWsStore.getState().setDivineState({
      events: [{ type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 }],
      treasury_usd: 2020, incarnation_number: 0,
      lineage: [{ incarnation_number: 0, last_tick: 50, cause: "breath_zero", final_bankroll_usd: 0, ts: "t" }],
    });
    const s = useWsStore.getState();
    expect(selectDivineEvents(s)).toHaveLength(1);
    expect(selectDivineTreasury(s)).toBe(2020);
    expect(selectIncarnationNumber(s)).toBe(0);
    expect(selectReincarnationLineage(s)).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run lib/__tests__/wsStore_divine.test.ts`
Expected: FAIL (`selectDivineEvents` undefined / no `setDivineState` action).

- [ ] **Step 3: Implement — WsState fields + initial**

Add to the `WsState` interface (after `tombstone`):

```typescript
  /** Living Stage P1 — latest divine events (the poll's tailed list, newest last). */
  readonly divineEvents: readonly GodsTreasuryRecordData[];
  /** Cumulative gods revenue (successful tributes + cash tithes), USD. */
  readonly divineTreasury: number;
  /** Current incarnation (0 until Phase 2 supervisor). */
  readonly incarnationNumber: number;
  /** Past-life lineage (from deaths.jsonl). */
  readonly reincarnationLineage: readonly IncarnationLineageEntry[];
  /**
   * Poll-path setter for the divine slices. Round-2 MED: the divine data is
   * NOT a WebSocket wire message (it never travels the socket — it is derived
   * from the 2 s /api/sandbox poll bundle), so it is a dedicated store action,
   * NOT a `WsMessage` kind. This avoids the repo's full WS-contract surface
   * (dashboard_ws_message.v*.json / _registry.json / ws-client sniff tests).
   */
  setDivineState: (p: {
    events: readonly GodsTreasuryRecordData[];
    treasury_usd: number;
    incarnation_number: number;
    lineage: readonly IncarnationLineageEntry[];
  }) => void;
```

Add to the `initial` object: `divineEvents: [], divineTreasury: 0, incarnationNumber: 0, reincarnationLineage: [],`. Import `GodsTreasuryRecordData, IncarnationLineageEntry` from `./sandbox_state_shared`.

**No new `WsMessage` kind** (Round-2 MED): the divine data never rides the socket — it is poll-bundle-derived — so it does NOT enter the `WsMessage` union / `KNOWN_KINDS` / `wsContract.ts` / `dashboard_ws_message.v*.json` contract surface. It is set via the dedicated `setDivineState` store action declared above. `types.ts` and `wsContract.ts` are untouched by this task.

- [ ] **Step 4: Implement — the `setDivineState` action + selectors**

In the store creator object (`create<WsState>((set) => ({ ...initial, ingest: ..., setConnection: ..., reset: ... }))`), add the action alongside `setConnection`/`reset`. It REPLACES the four slices on each poll (the loader already tail-bounds `events`, so no append/dedup is needed — unlike the WS delta streams):

```typescript
  setDivineState: (p) =>
    set({
      divineEvents: p.events,
      divineTreasury: p.treasury_usd,
      incarnationNumber: p.incarnation_number,
      reincarnationLineage: p.lineage,
    }),
```

(`reset()` already restores the four slices via `...initial`. The `ingest` `switch` is NOT touched — no new `WsMessage` kind.) Add selectors at the bottom:

```typescript
export const selectDivineEvents = (s: WsState): readonly GodsTreasuryRecordData[] => s.divineEvents;
export const selectDivineTreasury = (s: WsState): number => s.divineTreasury;
export const selectIncarnationNumber = (s: WsState): number => s.incarnationNumber;
export const selectReincarnationLineage = (s: WsState): readonly IncarnationLineageEntry[] => s.reincarnationLineage;
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run lib/__tests__/wsStore_divine.test.ts`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add code/dashboard/lib/wsStore.ts code/dashboard/lib/__tests__/wsStore_divine.test.ts
git commit -m "feat(dashboard): wsStore divine slices (events/treasury/incarnation/lineage) + setDivineState action"
```

---

### Task B3: Lift bundle fields into the store on poll

**Files:**
- Modify: `code/dashboard/lib/load_sandbox_state.ts` (`fetchOnce` ingest block ~210-238)
- Test: `code/dashboard/lib/__tests__/sandbox_poll_divine.test.ts`

- [ ] **Step 1: Write the failing test** (drive that a polled bundle sets the divine slices via `setDivineState`)

```typescript
// code/dashboard/lib/__tests__/sandbox_poll_divine.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useSandboxState } from "../load_sandbox_state";
import { useWsStore, selectDivineTreasury } from "../wsStore";

function bundle() {
  return {
    snapshot: { snapshot_ts: "t", phase: "PHASE_2_APPRENTICE", breath: 72, bankroll_usd: 1240, phase_age_days: 0, last_tick: 50, incarnation_number: 0 },
    recent_decisions: [], recent_settled: [], lag_alerts: [], served_ts: "t", is_mock: false,
    recent_gods_treasury: [{ type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 }],
    gods_revenue_cumulative_usd: 20, incarnation_number: 0, incarnation_lineage: [],
  };
}

describe("useSandboxState lifts divine fields", () => {
  beforeEach(() => useWsStore.getState().reset());
  it("lifts divine bundle fields into the store via setDivineState", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => bundle() }) as any;
    renderHook(() => useSandboxState({ fetchImpl, pollMs: 10 }));
    await waitFor(() => expect(selectDivineTreasury(useWsStore.getState())).toBe(20));
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run lib/__tests__/sandbox_poll_divine.test.ts`
Expected: FAIL (treasury stays 0 — nothing lifts it).

- [ ] **Step 3: Implement** — grab the action next to the existing `ingest` selector (`const setDivineState = useWsStore((s) => s.setDivineState);`), add `setDivineState` to the `fetchOnce` `useCallback` dependency array, and in `fetchOnce`, after the existing `decision_feed` ingest block (~line 238), add:

```typescript
      setDivineState({
        events: data.recent_gods_treasury,
        treasury_usd: data.gods_revenue_cumulative_usd,
        incarnation_number: data.incarnation_number,
        lineage: data.incarnation_lineage,
      });
```

(No seq/ordering concern — `setDivineState` replaces the divine slices directly, independent of the vitals/`decision_feed` frame seq.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run lib/__tests__/sandbox_poll_divine.test.ts`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add code/dashboard/lib/load_sandbox_state.ts code/dashboard/lib/__tests__/sandbox_poll_divine.test.ts
git commit -m "feat(dashboard): poll loader lifts divine bundle fields into wsStore"
```

---

## Track C — Living Stage frontend (`/living`)

> All components are client components (`"use client"`), read from `useWsStore` selectors (never props for live data), and theme via `widgetPalette("abyss")` + `--ab-*` tokens. Each ships with a render smoke test under `code/dashboard/components/living/__tests__/`.

### Task C2: Z1 — `LivingOrganism`

**Files:**
- Create: `code/dashboard/components/living/LivingOrganism.tsx`
- Test: `code/dashboard/components/living/__tests__/LivingOrganism.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// code/dashboard/components/living/__tests__/LivingOrganism.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useWsStore } from "../../../lib/wsStore";
import { LivingOrganism } from "../LivingOrganism";

describe("LivingOrganism", () => {
  beforeEach(() => useWsStore.getState().reset());
  it("renders breath, bankroll, incarnation, alive state", () => {
    useWsStore.setState({
      vitals: { breath: 72, bankroll: 1240, countdown_s: 0, gas_per_min: 0, phase: "PHASE_2_APPRENTICE" },
      incarnationNumber: 3,
    } as any);
    render(<LivingOrganism />);
    expect(screen.getByTestId("organism-breath").textContent).toContain("72");
    expect(screen.getByTestId("organism-bankroll").textContent).toContain("1,240");
    expect(screen.getByTestId("organism-incarnation").textContent).toContain("3");
    expect(screen.getByTestId("organism-state").textContent).toMatch(/ALIVE/i);
  });

  it("shows DYING when breath <= 10", () => {
    useWsStore.setState({ vitals: { breath: 8, bankroll: 5, countdown_s: 0, gas_per_min: 0, phase: "PHASE_2_APPRENTICE" } } as any);
    render(<LivingOrganism />);
    expect(screen.getByTestId("organism-state").textContent).toMatch(/DYING/i);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/LivingOrganism.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```tsx
// code/dashboard/components/living/LivingOrganism.tsx
"use client";
import { useWsStore, selectVitals, selectIncarnationNumber } from "../../lib/wsStore";
import { widgetPalette } from "../../lib/colorTokens";

const BREATH_FULL = 100;

export function LivingOrganism(): JSX.Element {
  const vitals = useWsStore(selectVitals);
  const incarnation = useWsStore(selectIncarnationNumber);
  const terminal = useWsStore((s) => s.terminalLucidityEntered);
  const pal = widgetPalette("abyss");

  const breath = vitals?.breath ?? 0;
  const bankroll = vitals?.bankroll ?? 0;
  const state = terminal || breath <= 0 ? "TERMINAL" : breath <= 10 ? "DYING" : "ALIVE";
  const ringColor = state === "ALIVE" ? pal.accent : pal.danger;
  const pct = Math.max(0, Math.min(1, breath / BREATH_FULL));

  return (
    <div className="flex flex-col items-center gap-2" data-testid="living-organism">
      <div
        className="relative flex h-40 w-40 items-center justify-center rounded-full"
        style={{ border: `4px solid ${pal.border ? ringColor : ringColor}`,
                 boxShadow: state === "ALIVE" ? `0 0 28px ${ringColor}44` : "none" }}
      >
        <div className="absolute inset-[-4px] rounded-full"
             style={{ border: "4px solid transparent", borderTopColor: ringColor, borderRightColor: ringColor,
                      transform: `rotate(${pct * 360}deg)`, transition: "transform .6s ease" }} />
        <div className="text-center">
          <div className="text-2xl" style={{ color: ringColor }}>♥</div>
          <div className="font-mono text-lg" data-testid="organism-breath" style={{ color: pal.ink }}>{breath.toFixed(0)}</div>
          <div className="font-mono text-[9px]" style={{ color: pal.inkMuted }}>breath</div>
        </div>
      </div>
      <div className="font-mono text-xl" data-testid="organism-bankroll" style={{ color: pal.ink }}>
        ${bankroll.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>
      <div className="flex items-center gap-3 font-mono text-[10px]" style={{ color: pal.inkMuted }}>
        <span data-testid="organism-incarnation">Incarnation #{incarnation}</span>
        <span data-testid="organism-state" style={{ color: state === "ALIVE" ? pal.accent : pal.danger }}>
          ● {state}
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/LivingOrganism.test.tsx`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add code/dashboard/components/living/LivingOrganism.tsx code/dashboard/components/living/__tests__/LivingOrganism.test.tsx
git commit -m "feat(living): Z1 LivingOrganism (breath ring + bankroll + incarnation + alive state)"
```

---

### Task C3: Z2 — `DivineTreasury` + `DivineEventStream`

**Files:**
- Create: `code/dashboard/components/living/DivineTreasury.tsx`, `DivineEventStream.tsx`
- Test: `code/dashboard/components/living/__tests__/DivineZone.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// code/dashboard/components/living/__tests__/DivineZone.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useWsStore } from "../../../lib/wsStore";
import { DivineTreasury } from "../DivineTreasury";
import { DivineEventStream } from "../DivineEventStream";

describe("Divine zone", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("treasury shows cumulative usd", () => {
    useWsStore.setState({ divineTreasury: 50719 } as any);
    render(<DivineTreasury />);
    expect(screen.getByTestId("divine-treasury-total").textContent).toContain("50,719");
  });

  it("event stream renders tithe + tribute rows with outcome", () => {
    useWsStore.setState({ divineEvents: [
      { type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 },
      { type: "tribute", tribute_id: "t1", ts: "t", tick: 40, amount_usd: 2000, success: true, breath_after: 35, bankroll_after: 0, dice_roll: 0.99 },
    ] } as any);
    render(<DivineEventStream />);
    expect(screen.getByText(/TITHE/i)).toBeTruthy();
    expect(screen.getByText(/TRIBUTE/i)).toBeTruthy();
    expect(screen.getByText(/SURVIVED/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/DivineZone.test.tsx`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement**

```tsx
// code/dashboard/components/living/DivineTreasury.tsx
"use client";
import { useWsStore, selectDivineTreasury } from "../../lib/wsStore";
import { widgetPalette } from "../../lib/colorTokens";

export function DivineTreasury(): JSX.Element {
  const total = useWsStore(selectDivineTreasury);
  const pal = widgetPalette("abyss");
  return (
    <div className={`rounded-md border p-3 text-center ${pal.panelFaint}`}>
      <div className="font-mono text-[9px] uppercase tracking-[0.18em]" style={{ color: pal.inkMuted }}>
        Divine Treasury ⛩
      </div>
      <div className="font-mono text-2xl" data-testid="divine-treasury-total" style={{ color: pal.accent2 }}>
        ${total.toLocaleString(undefined, { maximumFractionDigits: 0 })}
      </div>
      <div className="font-mono text-[8px]" style={{ color: pal.inkMuted }}>collected from this soul</div>
    </div>
  );
}
```

```tsx
// code/dashboard/components/living/DivineEventStream.tsx
"use client";
import { useWsStore, selectDivineEvents } from "../../lib/wsStore";
import { widgetPalette } from "../../lib/colorTokens";
import type { GodsTreasuryRecordData } from "../../lib/sandbox_state_shared";

function EventCard({ ev, pal }: { ev: GodsTreasuryRecordData; pal: ReturnType<typeof widgetPalette> }): JSX.Element {
  if (ev.type === "tithe") {
    const cost = ev.paid_usd > 0 ? `− $${ev.paid_usd.toFixed(2)}` : `− ${ev.breath_cost.toFixed(0)} breath`;
    return (
      <div className="rounded border-l-2 px-2 py-1" style={{ borderColor: pal.danger, background: `${pal.danger}14` }}>
        <div className="font-mono text-[9px]" style={{ color: pal.danger }}>TITHE · the rent</div>
        <div className="font-mono text-[11px]" style={{ color: pal.ink }}>{cost}</div>
      </div>
    );
  }
  return (
    <div className="rounded border-l-2 px-2 py-1" style={{ borderColor: pal.accent, background: `${pal.accent}14` }}>
      <div className="font-mono text-[9px]" style={{ color: pal.accent }}>TRIBUTE · deathbed</div>
      <div className="font-mono text-[11px]" style={{ color: pal.ink }}>
        ${ev.amount_usd.toFixed(0)}{ev.dice_roll != null ? ` → dice ${ev.dice_roll.toFixed(2)}` : ""}
      </div>
      <div className="font-mono text-[9px]" style={{ color: ev.success ? pal.accent : pal.danger }}>
        {ev.success ? "▲ SURVIVED" : "✝ REFUSED"}
      </div>
    </div>
  );
}

export function DivineEventStream(): JSX.Element {
  const events = useWsStore(selectDivineEvents);
  const pal = widgetPalette("abyss");
  const newestFirst = [...events].reverse();
  return (
    <div className="flex flex-col gap-1.5" data-testid="divine-event-stream">
      <div className="font-mono text-[9px] uppercase tracking-[0.18em]" style={{ color: pal.danger }}>⛧ The Gods</div>
      {newestFirst.length === 0 ? (
        <div className="font-mono text-[9px]" style={{ color: pal.inkMuted }}>the gods are quiet…</div>
      ) : newestFirst.map((ev) => (
        <EventCard key={ev.type === "tithe" ? ev.tithe_id : ev.tribute_id} ev={ev} pal={pal} />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/DivineZone.test.tsx`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add code/dashboard/components/living/DivineTreasury.tsx code/dashboard/components/living/DivineEventStream.tsx code/dashboard/components/living/__tests__/DivineZone.test.tsx
git commit -m "feat(living): Z2 DivineTreasury + DivineEventStream (real treasury + tithe/tribute feed)"
```

---

### Task C4: Z3 — `CurrentMarketCard`

**Files:**
- Create: `code/dashboard/components/living/CurrentMarketCard.tsx`
- Test: `code/dashboard/components/living/__tests__/CurrentMarketCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// code/dashboard/components/living/__tests__/CurrentMarketCard.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useWsStore } from "../../../lib/wsStore";
import { CurrentMarketCard } from "../CurrentMarketCard";

describe("CurrentMarketCard", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("renders market, odds, and the bet from the newest decisionFeed entry", () => {
    useWsStore.getState().ingest({ kind: "decision_feed", ts: "t", seq: 1, entries: [
      { id: "d1", ts: "t", action: "BET", side: "YES", size_usd: 50,
        market_id: "Sinner def. Alcaraz?", edge_pct: 0.04, odds_yes: 0.58, odds_no: 0.42 },
    ] } as any);
    render(<CurrentMarketCard />);
    expect(screen.getByTestId("act-market").textContent).toContain("Sinner def. Alcaraz?");
    expect(screen.getByText(/\.58/)).toBeTruthy();
    expect(screen.getByTestId("act-bet").textContent).toMatch(/YES.*\$50/);
  });

  it("shows scanning when newest entry is NO_BET / idle", () => {
    useWsStore.getState().ingest({ kind: "decision_feed", ts: "t", seq: 1, entries: [
      { id: "d1", ts: "t", action: "NO_BET", reasoning: "no_eligible_market" },
    ] } as any);
    render(<CurrentMarketCard />);
    expect(screen.getByTestId("act-idle").textContent).toMatch(/scanning/i);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/CurrentMarketCard.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```tsx
// code/dashboard/components/living/CurrentMarketCard.tsx
"use client";
import { useWsStore, selectDecisionFeed } from "../../lib/wsStore";
import { widgetPalette } from "../../lib/colorTokens";

export function CurrentMarketCard(): JSX.Element {
  // Round-1 MED-2/3: read the NEWEST decisionFeed entry (the poll path fills
  // decisionFeed, never latestDecision). DecisionFeedEntry uses `action`, not
  // `kind`, and decisionFeed is newest-first (entry [0]).
  const entry = useWsStore(selectDecisionFeed)[0];
  const pal = widgetPalette("abyss");

  if (!entry || entry.action !== "BET" || !entry.market_id) {
    return (
      <div className={`rounded-lg border p-3 ${pal.panelFaint}`} data-testid="act-idle">
        <div className="font-mono text-[9px] uppercase tracking-[0.18em]" style={{ color: pal.inkMuted }}>▸ The Act · now</div>
        <div className="font-mono text-[11px]" style={{ color: pal.inkMuted }}>
          scanning global tennis markets — no bettable match. heartbeat steady.
        </div>
      </div>
    );
  }

  const yes = entry.odds_yes, no = entry.odds_no;
  return (
    <div className={`rounded-lg border p-3 ${pal.panelFaint}`}>
      <div className="font-mono text-[9px] uppercase tracking-[0.18em]" style={{ color: pal.inkMuted }}>▸ The Act · now</div>
      <div className="font-mono text-sm" data-testid="act-market" style={{ color: pal.ink }}>{entry.market_id}</div>
      <div className="mt-2 flex gap-2">
        <div className="flex-1 rounded border p-1 text-center" style={{ borderColor: pal.accent }}>
          <div className="font-mono text-[8px]" style={{ color: pal.accent }}>YES</div>
          <div className="font-mono text-sm" style={{ color: pal.accent }}>{yes != null ? yes.toFixed(2).replace(/^0/, "") : "—"}</div>
        </div>
        <div className="flex-1 rounded border p-1 text-center" style={{ borderColor: pal.inkMuted }}>
          <div className="font-mono text-[8px]" style={{ color: pal.inkMuted }}>NO</div>
          <div className="font-mono text-sm" style={{ color: pal.inkMuted }}>{no != null ? no.toFixed(2).replace(/^0/, "") : "—"}</div>
        </div>
      </div>
      <div className="mt-2 rounded p-2" style={{ background: `${pal.accent}10` }} data-testid="act-bet">
        <span className="font-mono text-[11px]" style={{ color: pal.accent }}>
          ▸ BET {entry.side} ${entry.size_usd?.toFixed(0)}{entry.odds_yes != null && entry.side === "YES" ? ` @ ${entry.odds_yes.toFixed(2).replace(/^0/, "")}` : ""}
        </span>
        <div className="font-mono text-[8px]" style={{ color: pal.inkMuted }}>paper fill · holding to resolution</div>
      </div>
    </div>
  );
}
```

> Relies on `odds_yes`/`odds_no` being added to `DecisionFeedEntry` and mapped in `toDecisionFeedEntries` (Task B1). `selectDecisionFeed` already exists in `wsStore.ts`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/CurrentMarketCard.test.tsx`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add code/dashboard/components/living/CurrentMarketCard.tsx code/dashboard/components/living/__tests__/CurrentMarketCard.test.tsx
git commit -m "feat(living): Z3 CurrentMarketCard (market + YES/NO odds + the bet, idle scanning state)"
```

---

### Task C5: Z4 — `FusionSignalsRail`

**Files:**
- Create: `code/dashboard/components/living/FusionSignalsRail.tsx`
- Test: `code/dashboard/components/living/__tests__/FusionSignalsRail.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// code/dashboard/components/living/__tests__/FusionSignalsRail.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useWsStore } from "../../../lib/wsStore";
import { FusionSignalsRail } from "../FusionSignalsRail";

describe("FusionSignalsRail", () => {
  beforeEach(() => useWsStore.getState().reset());
  it("renders the 5 engine bars + fused edge vs floor from the newest feed entry", () => {
    useWsStore.getState().ingest({ kind: "decision_feed", ts: "t", seq: 1, entries: [
      { id: "d1", ts: "t", action: "BET", side: "YES", size_usd: 50, market_id: "m",
        edge_pct: 0.041, fee_floor_pct: 0.018,
        signals: { tennis_technical: 0.12, market_momentum: 0.08, surface_advantage: -0.05, head_to_head: 0.03, rest_recency: 0.01 } },
    ] } as any);
    render(<FusionSignalsRail />);
    expect(screen.getByText(/tennis_technical/i)).toBeTruthy();
    expect(screen.getByText(/rest_recency/i)).toBeTruthy();
    expect(screen.getByTestId("fused-edge").textContent).toContain("0.041");
    expect(screen.getByTestId("fee-floor").textContent).toContain("0.018");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/FusionSignalsRail.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement** (a self-contained signed diverging bar; engine signals come from `latestDecision.payload.signals`)

```tsx
// code/dashboard/components/living/FusionSignalsRail.tsx
"use client";
import { useWsStore, selectDecisionFeed } from "../../lib/wsStore";
import { widgetPalette } from "../../lib/colorTokens";

const ENGINES = ["tennis_technical", "market_momentum", "surface_advantage", "head_to_head", "rest_recency"] as const;

export function FusionSignalsRail(): JSX.Element {
  // Round-1 MED-2/3: newest decisionFeed entry (poll path), not latestDecision.
  const p = useWsStore(selectDecisionFeed)[0];
  const pal = widgetPalette("abyss");
  const signals: Record<string, number> = (p?.signals as Record<string, number>) ?? {};

  return (
    <div className="flex flex-col gap-1.5" data-testid="fusion-rail">
      <div className="font-mono text-[9px] uppercase tracking-[0.18em]" style={{ color: pal.accent }}>⊕ The Mind · 5 engines</div>
      {ENGINES.map((name) => {
        const v = signals[name] ?? 0;
        const clamped = Math.max(-1, Math.min(1, v));
        const width = Math.abs(clamped) * 50; // % of half-track
        const color = clamped >= 0 ? pal.accent : pal.danger;
        return (
          <div key={name} className="flex items-center gap-2 font-mono text-[8px]" style={{ color: pal.inkMuted }}>
            <span className="w-[88px]">{name}</span>
            <span className="relative h-[6px] flex-1 rounded" style={{ background: pal.track ? undefined : "#11161f" }}>
              <span className="absolute top-0 h-[6px] rounded"
                    style={{ [clamped >= 0 ? "left" : "right"]: "50%", width: `${width}%`, background: color } as React.CSSProperties} />
            </span>
            <span style={{ color }}>{clamped >= 0 ? "+" : ""}{clamped.toFixed(2)}</span>
          </div>
        );
      })}
      <div className="mt-1 font-mono text-[9px]" style={{ color: pal.accent }}>FUSED EDGE</div>
      <div className="font-mono text-sm" style={{ color: pal.ink }}>
        <span data-testid="fused-edge">{p?.edge_pct != null ? p.edge_pct.toFixed(3) : "—"}</span>
        {p?.fee_floor_pct != null && (
          <span className="ml-2 text-[8px]" data-testid="fee-floor" style={{ color: pal.inkMuted }}>
            › fee floor {p.fee_floor_pct.toFixed(3)} {p.edge_pct != null && p.edge_pct > p.fee_floor_pct ? "✓" : ""}
          </span>
        )}
      </div>
    </div>
  );
}
```

> Relies on `signals` + `edge_pct` + `fee_floor_pct` on `DecisionFeedEntry`. `signals` (EngineSignalMap) + `edge_pct` already exist on `DecisionFeedEntry` (types.ts:106-124); `fee_floor_pct` is added to `DecisionFeedEntry` in Task B1, and `signals` now flows on the poll path because Task B1 maps `signal_scores` → `signals` in `toDecisionFeedEntries`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/FusionSignalsRail.test.tsx`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add code/dashboard/components/living/FusionSignalsRail.tsx code/dashboard/components/living/__tests__/FusionSignalsRail.test.tsx
git commit -m "feat(living): Z4 FusionSignalsRail (5 signed engine bars + fused edge vs fee floor)"
```

---

### Task C6: Z5 — `IncarnationLineage`

**Files:**
- Create: `code/dashboard/components/living/IncarnationLineage.tsx`
- Test: `code/dashboard/components/living/__tests__/IncarnationLineage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// code/dashboard/components/living/__tests__/IncarnationLineage.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useWsStore } from "../../../lib/wsStore";
import { IncarnationLineage } from "../IncarnationLineage";

describe("IncarnationLineage", () => {
  beforeEach(() => useWsStore.getState().reset());
  it("renders past lives + the current living one", () => {
    useWsStore.setState({
      incarnationNumber: 1,
      reincarnationLineage: [{ incarnation_number: 0, last_tick: 50, cause: "breath_zero", final_bankroll_usd: 0, ts: "t" }],
    } as any);
    render(<IncarnationLineage />);
    expect(screen.getByText(/life 0/i)).toBeTruthy();
    expect(screen.getByTestId("lineage-current").textContent).toMatch(/life 1.*ALIVE/i);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/IncarnationLineage.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```tsx
// code/dashboard/components/living/IncarnationLineage.tsx
"use client";
import { useWsStore, selectReincarnationLineage, selectIncarnationNumber } from "../../lib/wsStore";
import { widgetPalette } from "../../lib/colorTokens";

export function IncarnationLineage(): JSX.Element {
  const lineage = useWsStore(selectReincarnationLineage);
  const current = useWsStore(selectIncarnationNumber);
  const pal = widgetPalette("abyss");
  return (
    <div className="flex flex-col gap-1" data-testid="incarnation-lineage">
      <div className="font-mono text-[9px] uppercase tracking-[0.18em]" style={{ color: pal.inkMuted }}>⟲ Lineage · reincarnations</div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[9px]">
        {lineage.map((l) => (
          <span key={l.incarnation_number} style={{ color: pal.inkMuted }}>
            life {l.incarnation_number} <span style={{ color: pal.danger }}>✝</span> {l.cause.replace("_", " ")}
            <span style={{ color: pal.inkMuted }}> · ${l.final_bankroll_usd.toFixed(0)}</span>
          </span>
        ))}
        <span data-testid="lineage-current" style={{ color: pal.accent }}>life {current} ● ALIVE</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run components/living/__tests__/IncarnationLineage.test.tsx`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add code/dashboard/components/living/IncarnationLineage.tsx code/dashboard/components/living/__tests__/IncarnationLineage.test.tsx
git commit -m "feat(living): Z5 IncarnationLineage (past lives + current living one)"
```

---

### Task C1+C7: `/living` route + `LivingStageBody` assembly

**Files:**
- Create: `code/dashboard/app/living/page.tsx`, `code/dashboard/app/living/LivingStageBody.tsx`
- Test: `code/dashboard/app/living/__tests__/LivingStageBody.test.tsx`

- [ ] **Step 1: Write the failing test** (assembly smoke — all 5 zones mount under the bootstrap)

```tsx
// code/dashboard/app/living/__tests__/LivingStageBody.test.tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useWsStore } from "../../../lib/wsStore";
import { LivingStageBody } from "../LivingStageBody";

describe("LivingStageBody", () => {
  beforeEach(() => useWsStore.getState().reset());
  it("mounts all five zones", () => {
    render(<LivingStageBody />);
    expect(screen.getByTestId("living-organism")).toBeTruthy();   // Z1
    expect(screen.getByTestId("divine-event-stream")).toBeTruthy(); // Z2
    expect(screen.getByTestId("divine-treasury-total")).toBeTruthy();
    expect(screen.getByTestId("act-idle")).toBeTruthy();           // Z3 (idle by default)
    expect(screen.getByTestId("fusion-rail")).toBeTruthy();        // Z4
    expect(screen.getByTestId("incarnation-lineage")).toBeTruthy();// Z5
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd code/dashboard && npx vitest run app/living/__tests__/LivingStageBody.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the body** (clone the `/mock` bootstrap chain; reuse `SandboxLiveBootstrap` so the 2 s poll runs)

```tsx
// code/dashboard/app/living/LivingStageBody.tsx
"use client";
import { WsBootstrap } from "../../components/WsBootstrap";          // same import path /mock uses
import { SandboxLiveBootstrap } from "../../components/SandboxLiveBootstrap";
import { DeathWatch } from "../../components/DeathWatch";
import { LivingOrganism } from "../../components/living/LivingOrganism";
import { DivineEventStream } from "../../components/living/DivineEventStream";
import { DivineTreasury } from "../../components/living/DivineTreasury";
import { CurrentMarketCard } from "../../components/living/CurrentMarketCard";
import { FusionSignalsRail } from "../../components/living/FusionSignalsRail";
import { IncarnationLineage } from "../../components/living/IncarnationLineage";

export function LivingStageBody(): JSX.Element {
  return (
    <WsBootstrap>
      <SandboxLiveBootstrap>
        <DeathWatch variant="abyss" />
        <div className="flex flex-col gap-4" data-testid="living-stage-body">
          <div className="flex items-center justify-between font-mono text-[10px]" style={{ color: "var(--ab-dim)" }}>
            <span style={{ color: "var(--ab-glow)" }}>◆ AUTOPOIESIS</span>
            <span>live mock-bet · Polymarket tennis · paper-traded, real odds</span>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[23%_minmax(0,1fr)_25%]">
            <aside className="flex flex-col gap-3">
              <DivineEventStream />
              <DivineTreasury />
            </aside>
            <section className="flex flex-col items-center gap-4">
              <LivingOrganism />
              <CurrentMarketCard />
            </section>
            <aside><FusionSignalsRail /></aside>
          </div>
          <IncarnationLineage />
        </div>
      </SandboxLiveBootstrap>
    </WsBootstrap>
  );
}
```

> Confirm the exact import paths/names for `WsBootstrap` + `SandboxLiveBootstrap` from `app/mock/MockLiveBody.tsx` during execution (they are the same components `/mock` wraps). If `SandboxLiveBootstrap` is not a standalone export, mount the `useSandboxState()` hook in `LivingStageBody` directly (call it once at the top) — the effect is identical (it starts the 2 s poll that feeds the store).

- [ ] **Step 4: Implement the route**

```tsx
// code/dashboard/app/living/page.tsx
import { LivingStageBody } from "./LivingStageBody";

export const metadata = { title: "Autopoiesis · Living Stage" };

export default function LivingPage(): JSX.Element {
  return (
    <main className="abyss min-h-screen px-6 py-8" style={{ background: "var(--ab-bg)" }}>
      <LivingStageBody />
    </main>
  );
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd code/dashboard && npx vitest run app/living/__tests__/LivingStageBody.test.tsx`
Expected: PASS (1 passed). If the bootstrap components require a provider in tests, mock them (`vi.mock("../../components/WsBootstrap", ...)`) to pass-through children — mirror how `MockLiveBody`'s test handles the bootstrap.

- [ ] **Step 6: Typecheck + full dashboard test run**

Run: `cd code/dashboard && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all tests pass.

- [ ] **Step 7: Commit**

```bash
git add code/dashboard/app/living/
git commit -m "feat(living): /living route + LivingStageBody assembling all five zones"
```

---

## Final verification (run before handing to plan-loop execution review)

- [ ] **Backend:** `cd code && python -m pytest tests/agent/data tests/agent/runtime tests/agent/server -q` → all green.
- [ ] **Default byte-identicality:** with `SANDBOX_DIVINE_ECONOMY` unset, the loop factory builds `_NoopStateHook` + `tribute_policy=None` + `divine_tithe=False`; existing server/loop tests unchanged.
- [ ] **Dashboard:** `cd code/dashboard && npx tsc --noEmit && npx vitest run` → all green.
- [ ] **Live smoke (manual, optional):** export `SANDBOX_DIVINE_ECONOMY=1 SANDBOX_LIVE=1 GENESIS_REAL_SIGNALS=1`, boot the server, confirm `gods_treasury.jsonl` gains a tithe row within `tithe_every` markets and `/living` renders the treasury climbing from $0.

---

## Self-review notes (author)

- **Spec coverage:** divine economy wiring (A5,A6) ✓; records/streams (A1,A2) ✓; incarnation_number (A3) ✓; decision odds/fee-floor (A4,A5) ✓; treasury-from-$0 + breath-paid-not-USD honesty (B1 fold) ✓; poll-not-SSE (B1,B3) ✓; all 5 zones (C2–C6) + page (C1/C7) ✓; abyss reuse (C* via widgetPalette) ✓. **Deferred to Phase 2 (by design):** reincarnation supervisor, fresh-adapter-per-life, manifest, lineage going multi-life — the Lineage zone renders correctly with a single life now.
- **Type consistency:** record field names match across Python models, TS types, store slices, and component reads (`type` discriminant, `amount_usd`/`paid_usd`/`breath_cost`, `dice_roll`, `incarnation_number`, `odds_yes`/`odds_no`/`fee_floor_pct`).
- **Known execution-time confirmation (one, not a placeholder — exact anchor named):** the `eff_min_edge` local name in `_tick` (Task A5 step 4b, read lines 1774-1790). The `WsBootstrap`/`SandboxLiveBootstrap` export shape is RESOLVED — Codex round 1 verified both are real standalone exports (`dashboard/components/WsBootstrap.tsx:26`, `SandboxLiveBootstrap.tsx:35`).

---

## Revision log

### Round 1 — Codex review (xhigh) → `VERDICT: HIGH=1 MEDIUM=3 LOW=2`. All accepted (no rebuttals); fixed in the plan:

- **HIGH-1 (byte-identicality of `decisions.jsonl`):** the original `append_decision` switched to `json.dumps(decision_record_jsonl_dict(...))`, whose default separators add spaces and would change every existing decision row's bytes. **Fix:** `append_decision` now uses compact `decision.model_dump_json(exclude=<None/empty living keys>)` (Task A4); added a test asserting no separator spaces + no new keys when the living fields are absent.
- **MED-2/3 (Z3/Z4 data source):** the plan conflated the persisted `DecisionRecordData` (`kind`) with the live `DecisionPayload`/`DecisionFeedEntry` (`action`), and the poll path never populates `latestDecision`. **Fix:** Z3/Z4 now read the newest `decisionFeed` entry (`selectDecisionFeed(s)[0]`, `action` not `kind`); per-engine signals are sourced by persisting `DecisionRecord.signal_scores` (gated by `record_living_stage_fields`) and mapping it to `signals` in `toDecisionFeedEntries` (Tasks A4/A5/B1/C4/C5). Odds/signals stamping is gated on the flag so flag-OFF stays byte-identical (same root as HIGH-1).
- **MED-4 (`StateHook` must not raise):** `_SandboxStateHook.emit` now wraps its body in `try/except` → `logger.warning`, never propagating into `_attempt_tribute`/`_attempt_tithe`/`_die`; added a never-raises test (Task A6).
- **LOW-5 (`divine_update` whitelist):** added `DivineUpdateMessage` to the `WsMessage` union + `KNOWN_KINDS` + `wsContract.ts` enumeration, not just the store (Task B2).
- **LOW-6 (death `cause`):** `DeathRecord.cause` defaults `breath_zero` and the hook honors `payload.get("cause", ...)`. Rationale recorded: `_die`'s sole trigger is `breath<=0` (forced-terminal also routes through breath→0), so `breath_zero` is the true mechanism label in Phase 1; `forced_terminal` is reserved for Phase 2.

**Verified-correct by Codex (no action):** `_NoopStateHook.emit(**payload)` absorbs the new `dice_roll` kwarg flag-OFF; `inputs.price` is the in-scope YES probability at the `_tick` record site; existing `exclude_none=True` usage supports the omit assumption; `WsBootstrap`/`SandboxLiveBootstrap` are real standalone exports.

### Round 2 — Codex confirmation review → `VERDICT: HIGH=0 MEDIUM=1 LOW=2`. All accepted; fixed:

- **MED (`divine_update` contract surface):** rather than satisfy the full WS-message contract (`dashboard_ws_message.v*.json` / `_registry.json` / ws-client sniff test), the divine data is reclassified as **poll-only state** — it never rides the socket. Removed the `DivineUpdateMessage`/`WsMessage`-union/`KNOWN_KINDS`/`wsContract.ts` changes; added a dedicated `setDivineState` store action that the poll hook calls (Tasks B2/B3). `types.ts`/`wsContract.ts` are now untouched.
- **LOW-1 (byte test too weak):** the A4 byte-identicality test now asserts the on-disk line is EXACTLY `dec.model_dump_json(exclude={the 4 living keys})` — a true byte-level equality to the pre-P1 shape, not just "keys absent + no spaces".
- **LOW-2 (plan internal inconsistency):** removed stale references to `_ODDS_STAMP_KEYS` / `decision_record_jsonl_dict` (File Structure line + the A4 Step-2 expected-failure message); the canonical names are `_DECISION_LIVING_KEYS` + `model_dump_json(exclude=…)`.

**Verified-correct by Codex round 2 (no action):** the serialization fix path; `selectDecisionFeed` exists + newest-first; `toDecisionFeedEntries` extension style; the `signal_scores` local + None handling; the hook has no raise path; the gating keeps decisions.jsonl byte-identical off; `record_living_stage_fields` factory pass-through; `EngineSignalMap` import target; the divine setter does not perturb `decisionFeed` ordering.

**Convergence:** HIGH/MEDIUM are 0 after the above; the 2 residual LOWs are fixed inline. Plan is execution-ready.
