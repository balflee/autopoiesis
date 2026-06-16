# Rename the 3 misnamed engine-slot keys — Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

═══════════════════════════════════════════════════════════════
## ⚠️ GROUND TRUTH — verified 2026-06-16. READ FIRST (every implementer/reviewer). The slot names misled the lead THREE times; do not be misled.

1. The ACTIVE signal source in everything that runs — backtest, the 能学 demo, AND
   prod (`agent/server/main.py:2291` `_make_prod_signal_source` → `RealSignalSource`)
   — is `agent/backtest/real_signal_source.py::RealSignalSource`. It feeds all 5
   fusion slots with REAL Sackmann/CLOB data.
2. The 5 engine MODULES (`SmartMoneyEngine`=on-chain wallets, `SentimentLLMEngine`
   =Gemini, `CrowdVolumeEngine`=Reddit) are DEFINED but NEVER instantiated (verified:
   only `class X(Engine):`, zero `X(...)` in `agent/`). Dead/prototype; RealSignalSource
   BYPASSES them. **KEEP them untouched** (future Stage-2 edge prototypes).
3. What each slot ACTUALLY carries: `tennis_technical`=ELO ✅, `market_momentum`=CLOB
   drift ✅, `smart_money`=**surface advantage** ❌, `sentiment_llm`=**head-to-head** ❌,
   `crowd_volume`=**rest/recency** ❌.
4. **RED FLAG RULE**: any claim that a slot "carries a wallet / smart-money / LLM /
   Reddit signal" is the DEAD MODULE talking — UNTRUSTWORTHY. The slot carries the
   Sackmann/CLOB payload. Re-verify against `real_signal_source.py`.
5. RENAME: `smart_money`→`surface_advantage`, `sentiment_llm`→`head_to_head`,
   `crowd_volume`→`rest_recency`. `tennis_technical`/`market_momentum` unchanged.
═══════════════════════════════════════════════════════════════

**Goal:** Rename the 3 slot KEYS to their real Sackmann payload. **Zero behavior
change** — full regression green (Python `pytest` + dashboard `vitest` + `next build`)
+ zero PnL/economy/weight numeric change = success.

**Rename mapping (the ONLY semantic change):**

| old slot key | new slot key | decision.py constant |
|---|---|---|
| `smart_money` | `surface_advantage` | `SMART_MONEY` → `SURFACE_ADVANTAGE` |
| `sentiment_llm` | `head_to_head` | `SENTIMENT_LLM` → `HEAD_TO_HEAD` |
| `crowd_volume` | `rest_recency` | `CROWD_VOLUME` → `REST_RECENCY` |

**Out of scope (do NOT touch):** the 3 engine MODULES (`agent/engines/{smart_money,
sentiment_llm,crowd_volume}.py`), `data/fixtures/smart_money_wallets.json`,
`agent/data/polygon_chain.py::smart_money_positions`, the parquet training columns
(`*_score`/`*_conf`, `data/etl/build_training_set.py`, `data/parquet/*`),
`value_seed_v3/v4.json`, production LLM prompts/schema, EMA state.

**Anchoring rule for every replace:** rename the 3 slot KEYS only where they are the
fusion-slot key. NEVER touch `smart_money_wallets`, `smart_money_positions`,
`smart_money_score`/`*_conf`, or the kept engine modules. Use word/quote-anchored
replacement (e.g. the quoted `"smart_money"` slot key, the `SMART_MONEY` constant
value) — never a bare substring sweep.

---

## Task 1: SoT derivation + parity test (NO rename yet — zero value change)

Make `decision.py` the single source of truth so the rename touches one definition
site; values stay the OLD names — this task only changes where the two duplicate
lists get their values + adds a guard test.

**Files:** Modify `agent/engines/weight_updater.py`, `agent/dashboard_bridge/event_emitter.py`; Create `tests/agent/engines/test_engine_slot_parity.py`

- [ ] **Step 1: Write the parity test**

```python
# tests/agent/engines/test_engine_slot_parity.py
"""weight_updater + event_emitter slot lists MUST equal decision.py's SoT —
guards the {name}_quality credit-assignment from drift across the rename."""
from __future__ import annotations
from agent.dashboard_bridge.event_emitter import SIGNAL_ENGINE_KEYS
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES
from agent.engines.weight_updater import _ALPHA_ENGINES, _BETA_ENGINES

def test_weight_updater_derives_from_decision_sot() -> None:
    assert _ALPHA_ENGINES == RATIONAL_ENGINES
    assert _BETA_ENGINES == SENTIENT_ENGINES

def test_event_emitter_signal_keys_derive_from_decision_sot() -> None:
    assert tuple(SIGNAL_ENGINE_KEYS) == (*RATIONAL_ENGINES, *SENTIENT_ENGINES)
```

- [ ] **Step 2: Run it.** `PYTHONPATH="$(pwd)" python -m pytest tests/agent/engines/test_engine_slot_parity.py -q` — expect PASS (values currently match; the test now LOCKS equality). If the real symbol names differ, fix the import first.

- [ ] **Step 3: Refactor the two consumers to DERIVE from decision.py** (keep symbol names, so no other site changes; values still the OLD names):

```python
# agent/engines/weight_updater.py
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES
_ALPHA_ENGINES = RATIONAL_ENGINES
_BETA_ENGINES = SENTIENT_ENGINES
```
```python
# agent/dashboard_bridge/event_emitter.py
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES
SIGNAL_ENGINE_KEYS = (*RATIONAL_ENGINES, *SENTIENT_ENGINES)
```
Watch for import cycles (decision.py must not import these back). Verify: `python -c "import agent.dashboard_bridge.event_emitter"`.

- [ ] **Step 4: Run parity + the two affected suites + ruff**

```bash
PYTHONPATH="$(pwd)" python -m pytest tests/agent/engines/test_engine_slot_parity.py tests/agent/engines/test_weight_updater_settlement.py tests/agent/dashboard_bridge/test_ws_contract_v0_3_0.py -q
PYTHONPATH="$(pwd)" python -m ruff check agent/engines/weight_updater.py agent/dashboard_bridge/event_emitter.py tests/agent/engines/test_engine_slot_parity.py
```
Expect PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/engines/weight_updater.py agent/dashboard_bridge/event_emitter.py tests/agent/engines/test_engine_slot_parity.py
git commit -m "refactor(engines): derive slot-key lists from decision.py SoT + parity test"
```

---

## Task 2: Python-side rename + wire schema v0.4.0 + the score_{engine} remap (atomic; ends Python pytest green)

**Files (Python):** `agent/engines/decision.py` (3 constants + the `⚠️ SLOT NAME` caveat block), `agent/backtest/real_signal_source.py` (slot-mapping targets + header), every Python string-literal slot-key site (`replay_runner`, `reincarnation`, `survival_season`, `sandbox_phase2_loop`, `cached_sweep`, `find_optimal_config`, `agent/training/*`, `scripts/probe_llm_fusion.py::_ENGINE_DESC`, etc.), the settlement read boundary (`agent/backtest/settlement_learner.py` ~:78-82), `.dev/contracts/dashboard_ws_message.v0.4.0.json` (new), `.dev/contracts/_registry.json`, `agent/dashboard_bridge/event_emitter.py` (`WS_CONTRACT_VERSION` constant), the renamed Python contract test, and every Python test asserting an old slot key.

- [ ] **Step 1: Add the score_{engine} backward-compat remap (the landmine), with a test**

At the settlement read boundary where `score_<engine>` keys are unflattened
(`settlement_learner.py` ~:78-82), apply a static alias map so a legacy persisted
bet (`open_bets.jsonl`) keyed by an OLD slot name still credits the NEW slot:

```python
# agent/backtest/settlement_learner.py (near the score_ unflatten)
# Legacy persisted bets carry score_<old-slot-key>; remap to the renamed slot so
# an in-flight bet settled after the rename credits the right slot (zero behavior
# change — identity for the unrenamed keys).
_SLOT_KEY_ALIASES = {
    "smart_money": "surface_advantage",
    "sentiment_llm": "head_to_head",
    "crowd_volume": "rest_recency",
}
# when extracting engine from a "score_<engine>" key:
engine = _SLOT_KEY_ALIASES.get(engine, engine)
```
Add a test asserting a legacy `score_smart_money` settlement credits the
`surface_advantage` slot (and unrenamed keys pass through unchanged).

- [ ] **Step 2: Run the remap test — fails (no remap) then passes.** `pytest <new remap test> -v`.

- [ ] **Step 3: Rename the 3 constants at the SoT + sweep every Python string-literal site**

In `agent/engines/decision.py`: rename the 3 constants + their VALUES
(`SURFACE_ADVANTAGE = "surface_advantage"`, etc.); update `RATIONAL_ENGINES`/
`SENTIENT_ENGINES`; update the `⚠️ SLOT NAME` caveat block to the new names + the
corrected framing. Update every importer of the renamed CONSTANTS (`SMART_MONEY`→
`SURFACE_ADVANTAGE` etc.) — grep `SMART_MONEY|SENTIMENT_LLM|CROWD_VOLUME`. Then
preview + apply the 3 quoted-key replacements across Python, ANCHORED (exclude
`smart_money_wallets`, `smart_money_positions`, `*_score`, `*_conf`, the kept engine
modules, `data/`):

```bash
rg -n '"smart_money"|"sentiment_llm"|"crowd_volume"' agent/ scripts/ tests/ \
  --glob '!agent/engines/smart_money.py' --glob '!agent/engines/sentiment_llm.py' \
  --glob '!agent/engines/crowd_volume.py'
```
Update `real_signal_source.py` slot-mapping targets (`surface_signal`→
`surface_advantage` slot, `h2h_signal`→`head_to_head`, `rest_signal`→`rest_recency`)
+ its loud header. Update `scripts/probe_llm_fusion.py::_ENGINE_DESC` keys.

- [ ] **Step 4: Wire schema v0.4.0 (BREAKING bump)**

```bash
git mv .dev/contracts/dashboard_ws_message.v0.3.0.json .dev/contracts/dashboard_ws_message.v0.4.0.json
```
Wait — do NOT git mv (immutable versioning keeps v0.3.0). Instead **copy**:
`cp .dev/contracts/dashboard_ws_message.v0.3.0.json .dev/contracts/dashboard_ws_message.v0.4.0.json`, then in the v0.4.0 file: replace BOTH `propertyNames.enum` arrays' 3 keys with the new names, set internal `"version":"0.4.0"`, add `supersedes` v0.3.0. Update `.dev/contracts/_registry.json` `dashboard_ws_message` pin (version 0.4.0, file v0.4.0.json, supersedes, a BREAKING `version_bump_reason` naming the renamed enum keys). Bump the bare constant `WS_CONTRACT_VERSION: Final[str] = "0.4.0"` in `event_emitter.py` (it is `"0.3.0"`, NOT `"v0.3.0"`). Sweep stale `.v0.3.0.json` references in `event_emitter.py` docstrings, `agent/dashboard_bridge/__init__.py`, `agent/dashboard_bridge/death_watch_emitter.py`, `agent/runbooks/*`. `git mv tests/agent/dashboard_bridge/test_ws_contract_v0_3_0.py tests/agent/dashboard_bridge/test_ws_contract_v0_4_0.py`; repoint its `_SCHEMA_PATH` to v0.4.0, update its version assertion + expected keys.

- [ ] **Step 5: Update every Python test asserting an old slot key** (`test_decision`, `test_weight_updater_settlement`, `test_each_engine`, `test_real_signal_source`, `test_cached_sweep`, `test_value_sweep`, `test_survival_*`, `test_run_cross_market_journey`, `test_phase1_runner`, `test_sandbox_settlement_poller`, the parity test now asserts the new tuple, etc.). If a test cross-asserts a KEPT engine module's `.name` against a slot key, update the test (do NOT rename the module).

- [ ] **Step 6: Run the FULL Python suite + ruff + (mypy if configured)**

```bash
PYTHONPATH="$(pwd)" python -m pytest tests/ -q
PYTHONPATH="$(pwd)" python -m ruff check agent/ scripts/ tests/
```
Expect ALL PASS. Then verify the sweep is clean: `rg '"smart_money"|"sentiment_llm"|"crowd_volume"' agent/ scripts/ tests/` returns ONLY kept sites (engine modules, wallet API/fixture) — zero slot-key hits elsewhere.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(engines): rename slot keys smart_money/sentiment_llm/crowd_volume -> surface_advantage/head_to_head/rest_recency (Python + wire v0.4.0 + score remap)"
```

---

## Task 3: Dashboard rename + regenerate fixtures (ends dashboard vitest + next build green)

**Files:** `dashboard/lib/{types.ts,wsContract.ts,load_static_sweep.ts,load_survival_journey.ts}`, `dashboard/components/DecisionFeed.tsx`, `dashboard/app/{backtest,mechanism}/page.tsx`, `dashboard/scripts/{build_static_sweep.py,build_stage1.py}`, `dashboard/public/backtest/static_sweep.json`, `dashboard/public/stage1/stage1_learning.json`, the TS contract test.

- [ ] **Step 1: Rename the 3 keys across the TS defs + components + bump the TS wire version**

Apply the 3 key replacements across `dashboard/` (TS/TSX): the `SignalSlotKey` union (`load_static_sweep.ts` `SIGNAL_SLOT_KEYS`) + `SIGNAL_SLOT_LABEL` keys (keep the human labels: surface_advantage→"Surface", head_to_head→"Head-to-Head", rest_recency→"Rest / Recency"), `types.ts`, `wsContract.ts` (bump `WS_CONTRACT_VERSION = "0.4.0"`), the loaders, `DecisionFeed.tsx`, `/backtest`, `/mechanism`. `git mv dashboard/__tests__/lib/wsContract_v0_3_0.test.ts dashboard/__tests__/lib/wsContract_v0_4_0.test.ts`; repoint its SCHEMA_PATH + version.
```bash
rg -n 'smart_money|sentiment_llm|crowd_volume' dashboard/ --glob '!**/*.json'
```

- [ ] **Step 2: Update the build scripts' slot labels + regenerate the committed fixtures**

In `dashboard/scripts/build_stage1.py`: update the `weight_trajectory` slot labels
(`edge_slot_label`/`noise_slot_label` reference the old keys `market_momentum`/
`smart_money` — `market_momentum` stays; `smart_money` → `surface_advantage`).
Regenerate both committed fixtures:
```bash
PYTHONPATH="$(pwd)" python dashboard/scripts/build_static_sweep.py
PYTHONPATH="$(pwd)" python dashboard/scripts/build_stage1.py
```
Verify: `rg -c 'smart_money|sentiment_llm|crowd_volume' dashboard/public/backtest/static_sweep.json dashboard/public/stage1/stage1_learning.json` → 0.

- [ ] **Step 3: Run dashboard vitest + tsc + next build**

```bash
node dashboard/node_modules/typescript/bin/tsc -p dashboard/tsconfig.json --noEmit
npm --prefix dashboard run test
npm --prefix dashboard run build
```
Expect ALL PASS, 12/12 pages, no TS-union/Pydantic-mirror failure.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(dashboard): rename slot keys + wire v0.4.0 + regenerate static_sweep + stage1 fixtures"
```

---

## Task 4: Full regression + regenerate journeys + final sweep + docs

- [ ] **Step 1: Full Python + dashboard regression**

```bash
PYTHONPATH="$(pwd)" python -m pytest tests/ -q
npm --prefix dashboard run test && npm --prefix dashboard run build
```
Expect ALL green.

- [ ] **Step 2: Regenerate the gitignored derived journeys (clean cutover, never hand-edit)**

Run the producers so locally-served journeys carry new keys (numerical legs):
```bash
PYTHONPATH="$(pwd)" python scripts/run_reincarnation.py --provider numerical  # + survival_season producer per its docstring
```
(Scope-limited: only enough to confirm the loaders accept new-name journeys.)

- [ ] **Step 3: Final grep sweep (with the documented exclusions)**

```bash
rg -n 'smart_money|sentiment_llm|crowd_volume' agent/ scripts/ tests/ dashboard/ .dev/ \
  --glob '!**/smart_money_wallets.json'
rg -n 'SmartMoneyEngine|SentimentLLMEngine|CrowdVolumeEngine' agent/ tests/
```
Expect: ONLY the kept sites — the 3 engine modules + their tests, `smart_money_wallets`/`smart_money_positions` wallet API, and `*_score`/`*_conf` training columns (out of scope). ZERO slot-key hits in the fusion/wire/dashboard paths.

- [ ] **Step 4: Docs + commit**

Update `docs/optimization_backlog.md` F1 → `DONE`; refresh the slot-name legend in `docs/divinity-mechanism-spec.md` + the `real_signal_source.py` / `decision.py` caveats to the NEW names (slots named for their real payload; engine modules kept as future-edge prototypes).
```bash
git add -A
git commit -m "docs: mark F1 rename DONE + refresh slot-name legends to the real payloads; regenerate journeys"
```

---

## Self-review notes

- **Spec coverage:** Task 1 = SoT/parity (spec §A); Task 2 = Python rename + real_signal_source + remap (§B/§C/§D) + wire (§E); Task 3 = dashboard + fixtures (§F); Task 4 = regression + journeys (§G) + docs. ✓
- **Anchoring guards repeated in every sweep:** exclude `smart_money_wallets`/`smart_money_positions`/`*_score`/`*_conf` + the engine modules; never a bare substring replace.
- **Type/version consistency:** the wire version is bumped ONCE (schema `$id`/version, filename, registry, both `WS_CONTRACT_VERSION` constants, both renamed contract-test filenames) and stays identical Python↔TS.
- **Modules kept:** the rename does NOT touch `agent/engines/{smart_money,sentiment_llm,crowd_volume}.py`.
