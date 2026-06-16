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
Also DELETE/rewrite the now-false `weight_updater.py:171-175` "Re-imported here would create a cycle — keep the tuple local" comment (verified no cycle: `agent/engines/__init__.py` imports `decision` before `weight_updater`, and `decision.py` imports only `agent.core.state` + `agent.engines.base`, neither of which imports back). State that decision.py is now the import-safe SoT. Verify BOTH imports: `python -c "import agent.dashboard_bridge.event_emitter"` and `python -c "import agent.engines.weight_updater"`.

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
(`settlement_learner.py` ~:78-82 — a dict-comprehension over the `score_`-prefixed
keys; there is NO scalar `engine` variable, so adapt the alias INTO the
comprehension), apply a static alias so a legacy persisted bet (`open_bets.jsonl`)
keyed by an OLD slot name still credits the NEW slot:

```python
# agent/backtest/settlement_learner.py (module-level)
# Legacy persisted bets carry score_<old-slot-key>; remap to the renamed slot so an
# in-flight bet settled after the rename credits the right slot (zero behavior change
# — identity for unrenamed keys).
_SLOT_KEY_ALIASES = {
    "smart_money": "surface_advantage",
    "sentiment_llm": "head_to_head",
    "crowd_volume": "rest_recency",
}
def _alias(engine: str) -> str:
    return _SLOT_KEY_ALIASES.get(engine, engine)
# In the score_ unflatten comprehension, alias the extracted engine token, e.g.:
#   {_alias(k[len("score_"):]): v for k, v in signals.items() if k.startswith("score_")}
```
Add a **pure unit test** on the unflatten/alias (it passes INDEPENDENT of the
constant rename): a `score_smart_money` input yields a `surface_advantage` key, and
unrenamed keys pass through unchanged. (The end-to-end "credit lands on the slot" is
NOT asserted here — `_ALPHA_ENGINES` is still OLD-named until Step 3; the full credit
path is verified by Step 6's suite after the rename.)

- [ ] **Step 2: Run the remap unit test — fails (no alias) then passes.** `pytest <new remap test> -v`.

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
+ its loud header. In `scripts/probe_llm_fusion.py::_ENGINE_DESC` rename the keys
AND **rewrite the description VALUES** to the real Sackmann payload (the old values
"informed wallet flow" / "news/sentiment lean" / "crowd volume pressure" describe
the dead module — replace with surface-advantage / head-to-head / rest-recency, or
the slot won't describe what it carries).

**Python in-code prose/docstrings that NAME the renamed slots** (the anchored
quoted/constant greps DON'T catch these — enumerate + REWRITE, distinct from kept
`*_score`/`*_positions`/module-name refs): `real_signal_source.py:154,181,214` slot
docstrings ("Surface advantage -> smart_money slot" etc.); `weight_updater.py:394-395`
group comment ("…tennis_technical + market_momentum + smart_money … sentiment_llm +
crowd_volume"); `agent/server/models.py:104-105` ("α₂ (market_momentum + smart_money
composite) and α₃ (crowd_volume)"); `agent/training/phase1_runner.py:762` generated
report label `| smart_money |` — **this label is asserted by `test_phase1_runner`,
so the label and the test expectation rename in LOCKSTEP** (check the test before
editing the label).

> The named sites are illustrative; the **authoritative worklist is the grep
> output** (both the `SMART_MONEY|SENTIMENT_LLM|CROWD_VOLUME` constants AND the
> quoted `"smart_money"|…"` literals). A missed Python constant-importer is a loud
> ImportError → red pytest, so completeness is enforced by Step 6's full suite +
> the post-sweep gate. Known constant-importers to include: `agent/runtime/
> phase2_launch.py`, `agent/runtime/sprint7_dryrun.py`, `agent/scripts/
> capture_money_shot.py`. Known extra test sites: `tests/agent/engines/conftest.py`,
> `tests/agent/runtime/{test_sandbox_restart,test_sandbox_phase2_loop_l3,
> test_sandbox_decision_telemetry,test_l2_wire}.py`, `tests/agent/engines/
> test_value_decision.py`, `tests/agent/integration/{_l3_stubs,test_phase2_launch_smoke}.py`.

- [ ] **Step 4: Wire schema v0.4.0 (BREAKING bump)**

```bash
git mv .dev/contracts/dashboard_ws_message.v0.3.0.json .dev/contracts/dashboard_ws_message.v0.4.0.json
```
Wait — do NOT git mv (immutable versioning keeps v0.3.0). Instead **copy**:
`cp .dev/contracts/dashboard_ws_message.v0.3.0.json .dev/contracts/dashboard_ws_message.v0.4.0.json`, then in the v0.4.0 file make it internally consistent (repo-wide `$id`-matches-filename invariant): replace BOTH `propertyNames.enum` arrays' 3 keys with the new names; set internal `"version":"0.4.0"`; rewrite `"$id"` to `.../dashboard_ws_message.v0.4.0.json`; retitle `"title"` to `(v0.4.0)`; update the v0.3.0-referencing `"description"` prose; add `supersedes` v0.3.0. **Leave the v0.3.0 file frozen/verbatim** (its old-key enum is intentionally retained — the read shims handle legacy payloads; do NOT edit it chasing zero hits). Update `.dev/contracts/_registry.json` `dashboard_ws_message` pin (version 0.4.0, file v0.4.0.json, supersedes, a BREAKING `version_bump_reason` naming the renamed enum keys). Bump the bare constant `WS_CONTRACT_VERSION: Final[str] = "0.4.0"` in `event_emitter.py` (it is `"0.3.0"`, NOT `"v0.3.0"`). Sweep stale `.v0.3.0.json` references in `event_emitter.py` docstrings, `agent/dashboard_bridge/__init__.py`, `agent/dashboard_bridge/death_watch_emitter.py`, `agent/runbooks/*`. `git mv tests/agent/dashboard_bridge/test_ws_contract_v0_3_0.py tests/agent/dashboard_bridge/test_ws_contract_v0_4_0.py`; repoint its `_SCHEMA_PATH` to v0.4.0, update its version assertion + expected keys.

- [ ] **Step 5: Update every Python test asserting an old slot KEY** (`test_decision`, `test_weight_updater_settlement`, `test_real_signal_source`, `test_cached_sweep`, `test_value_sweep`, `test_survival_*`, `test_run_cross_market_journey`, `test_phase1_runner`, `test_sandbox_settlement_poller`, `test_validate_value_seed`, the parity test now asserts the new tuple, plus whatever the grep surfaces). **`test_each_engine.py` is NOT in this list** — it is a kept-MODULE unit test (wallet positions / LLM calls / crowd windows) and asserts the modules' frozen `.name=="smart_money"`/`"sentiment_llm"`/`"crowd_volume"`; it needs **zero change**. **RULE**: LEAVE any assertion of a KEPT engine module's `.name` (== the old string) UNCHANGED — "updating" it to the new slot name would FAIL (the frozen module still returns the old name). Only rename DECISION-SLOT-KEY assertions.

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

## Task 3: Dashboard rename + read-side alias shim + regenerate fixtures (ends dashboard vitest + next build green)

**Files:** `dashboard/lib/{types.ts,wsContract.ts,load_static_sweep.ts,load_survival_journey.ts,load_survival_journey.server.ts,load_reincarnation.ts}`, `dashboard/components/DecisionFeed.tsx`, `dashboard/app/{backtest,mechanism}/page.tsx`, `dashboard/scripts/{build_static_sweep.py,build_stage1.py}`, `dashboard/public/backtest/static_sweep.json`, `dashboard/public/stage1/stage1_learning.json`, **all THREE wire/loader TS test files** + the dashboard test files asserting old keys: `dashboard/__tests__/lib/{wsContract.test.ts,wsContract_v0_3_0.test.ts,load_static_sweep.test.ts,load_survival_journey.test.ts}`, `dashboard/__tests__/{survival,survival_toggle,mock}.test.tsx`.

> The named lists are NOT exhaustive — the authoritative worklist is the grep
> sweep below (every `.ts/.tsx` hit, incl. error-regex assertions like
> `toThrow(/signals\.crowd_volume/)`); edit EVERY hit, not just the named subset.

- [ ] **Step 0 (the load-bearing fix): read-side alias shim mirroring the Python remap**

The on-disk journey artifacts (`survival_journey*.json`, `reincarnation*.json`) carry the OLD slot keys, and several are **non-regenerable** (the verbatim `*_run1/*_run2` finetune-log exhibits) or **Gemini-gated** (`*_ai*`/`*_gemini*`). A strict renamed loader (`load_survival_journey.ts::validateSignals` iterates `SURVIVAL_SIGNAL_KEYS` → `asFinite(o[k])` → throws on a missing key) would break `/survival` (and `/reincarnation`) at request time. Add a static old→new alias normalize at every loader that reads per-step slot signals from an on-disk journey, BEFORE validation (identity for new-key files ⇒ zero behavior change):

```ts
// dashboard/lib/slot_key_aliases.ts (new)
export const SLOT_KEY_ALIASES: Record<string, string> = {
  smart_money: "surface_advantage",
  sentiment_llm: "head_to_head",
  crowd_volume: "rest_recency",
};
/** Normalize legacy slot keys in a per-step signals object (old→new); identity
 *  for already-renamed keys. Lets archived/old-key journeys validate post-rename. */
export function normalizeSlotKeys(
  o: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...o };
  for (const [oldK, newK] of Object.entries(SLOT_KEY_ALIASES)) {
    if (oldK in out && !(newK in out)) {
      out[newK] = out[oldK];
      delete out[oldK];
    }
  }
  return out;
}
```
Apply `normalizeSlotKeys(...)` in `load_survival_journey.ts::validateSignals` (before the `for (const k of SURVIVAL_SIGNAL_KEYS)` loop) and audit `load_reincarnation.ts` / `load_learning_demo.ts` for the same per-step-signals pattern; apply there too if present. (Regenerated committed fixtures — static_sweep, stage1 — already carry new keys; the shim is for the un-regenerable on-disk journeys.) Add a vitest test: a synthetic journey with OLD keys validates and exposes the NEW keys.

- [ ] **Step 1: Rename the 3 keys across the TS defs + components + bump the TS wire version**

Apply the 3 key replacements across `dashboard/` (TS/TSX): the `SignalSlotKey` union (`load_static_sweep.ts` `SIGNAL_SLOT_KEYS`) + `SIGNAL_SLOT_LABEL` keys (keep the human labels: surface_advantage→"Surface", head_to_head→"Head-to-Head", rest_recency→"Rest / Recency"), `types.ts`, `wsContract.ts` (bump `WS_CONTRACT_VERSION = "0.4.0"`), the loaders, `DecisionFeed.tsx`. **`/backtest` + `/mechanism` carry the slot keys as PROSE** explaining the misnaming (e.g. backtest/page.tsx:604 "smart_money = on-chain wallets…", mechanism/page.tsx:429) — **REWRITE that prose** to the post-rename framing (the slots are named for their Sackmann payload; the genuine wallet/LLM/Reddit engine modules are KEPT as future edge prototypes), do NOT token-replace it into a false statement. **Repoint all THREE contract tests**: `git mv …/wsContract_v0_3_0.test.ts …/wsContract_v0_4_0.test.ts` (repoint SCHEMA_PATH + version), AND repoint the **two** hardcoded `dashboard_ws_message.v0.3.0.json` code-path literals in `dashboard/__tests__/lib/wsContract.test.ts:53,:79` to v0.4.0 (leave the death-watch resolve at :64), and refresh the two stale `v0.3.0` docstring mentions at `:17,:22` so the file is internally consistent. Its version assert (`:57 schema.version === WS_CONTRACT_VERSION`) then passes via the bumped constant. Update the error-message-regex assertions in `load_survival_journey.test.ts` / `load_static_sweep.test.ts` (the validator string + the test regex rename in lockstep).
```bash
rg -n 'smart_money|sentiment_llm|crowd_volume' dashboard/ --glob '!**/*.json'
```

- [ ] **Step 2: Update the build scripts' slot labels + regenerate the committed fixtures**

In `dashboard/scripts/build_stage1.py`: update the `weight_trajectory` slot labels
(`edge_slot_label`/`noise_slot_label` reference the old keys `market_momentum`/
`smart_money` — `market_momentum` stays; `smart_money` → `surface_advantage`).
**Save the pre-rename committed fixtures, regenerate, then PROVE numeric equivalence**
(the "zero numeric change" criterion — a regenerator with any RNG/non-determinism
would silently drift the numbers, not just the keys):
```bash
cp dashboard/public/backtest/static_sweep.json /tmp/static_sweep.orig.json
cp dashboard/public/stage1/stage1_learning.json /tmp/stage1.orig.json
PYTHONPATH="$(pwd)" python dashboard/scripts/build_static_sweep.py
PYTHONPATH="$(pwd)" python dashboard/scripts/build_stage1.py
# Equivalence guard: the regenerated fixture must equal the original AFTER applying
# the old->new key rename — i.e. differ ONLY in the 3 keys, with byte-identical
# numbers. A small python check: load both, rename the 3 keys in the ORIGINAL, assert
# deepequal vs regenerated. If it differs beyond keys → the producer is
# non-deterministic; PIN ITS SEED (or do a deterministic key-only rewrite of the
# committed fixture instead of regenerating) before committing. Do NOT claim
# zero-change until this passes.
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

- [ ] **Step 1b: Runtime journey-load gate (the named gates CANNOT see this)**

`/survival` + `/reincarnation` are `force-dynamic` (skipped by `next build`), and
vitest exercises inline fixtures — so the gates above never load the real on-disk
journeys. Add a runtime smoke that loads EVERY journey artifact present on disk via
the server loaders and asserts no throw (this is what proves the read-side alias
shim actually rescues the old-key + verbatim-archive journeys):
```bash
# dashboard/scripts/smoke_journeys.mjs — iterate every SurvivalJourneyMode +
# reincarnation arm present under public/backtest/, call the loader, assert no throw.
node --import tsx dashboard/scripts/smoke_journeys.mjs   # or a vitest that reads the real files
```
It MUST iterate every variant actually on disk (numerical + the stale `_ai`/
`_gemini`/`_run1`/`_run2` legs) AND **assert it loaded ≥1 journey** (the loader
returns null on a missing file, so on a fresh/CI checkout with the gitignored
journeys absent it would otherwise pass VACUOUSLY). Run it AFTER Task 4 Step 2 (so at
least the numerical journey exists). The **authoritative, environment-independent
proof of the shim** is the synthetic-old-key vitest from Task 3 Step 0 (a committed
test that always runs); Step 1b is the on-disk integration check on top. "Green
pytest+vitest+build" alone is NOT accepted as proof the `/survival` showpiece works.

- [ ] **Step 2: Regenerate the gitignored derived journeys (clean cutover, never hand-edit)**

Run the producers so freshly-served journeys carry new keys (numerical legs only):
```bash
PYTHONPATH="$(pwd)" python scripts/run_reincarnation.py --provider numerical  # + survival_season producer per its docstring
```
**The `{slot}_quality` EMA keys (e.g. `surface_advantage_quality`) rename implicitly
by derivation** — no separate rename; the §D `score_<old>` settlement alias + the
TS read shim cover persisted/served legacy data. The `_ai`/`_gemini`/`_run1`/`_run2`
journeys are **intentionally left with old keys** (Gemini-gated / verbatim finetune-log
exhibits — re-running them needs Gemini quota and would destroy the archive); the
TS alias shim (Task 3 Step 0) is exactly what keeps them loading. Do NOT hand-edit
them (violates "regenerate, never hand-edit").

- [ ] **Step 3: Final completeness sweep — two gates (CODE must be clean; ARTIFACTS may intentionally retain old keys)**

ripgrep skips gitignored paths (the `public/backtest/*` journeys + the whole `.dev/`
tree), so a bare `rg` returns a FALSE "clean". Run with `--no-ignore` and split the
gate:

**(a) CODE/active-config must be CLEAN of the 3 slot KEYS** — use the SAME
**anchored** pattern Task 2 uses (quoted keys + the constants), NOT a bare
substring (a bare sweep collides with the kept `smart_money_score`/`_conf`,
`smart_money_positions`, `smart_money_quality`, `smart_money_wallets`, and the
unrelated proposal id `smart_money_ofi_5m` — ~40 legitimate hits, never zero):
```bash
rg -n --no-ignore '"smart_money"|"sentiment_llm"|"crowd_volume"|\bSMART_MONEY\b|\bSENTIMENT_LLM\b|\bCROWD_VOLUME\b' \
  agent/ scripts/ tests/ dashboard/ \
  --glob '!agent/engines/smart_money.py' --glob '!agent/engines/sentiment_llm.py' \
  --glob '!agent/engines/crowd_volume.py' \
  --glob '!.dev/contracts/dashboard_ws_message.v0.3.0.json'
rg -n --no-ignore 'SmartMoneyEngine|SentimentLLMEngine|CrowdVolumeEngine' agent/ tests/
```
Expect: ZERO for the quoted/constant gate (the anchored pattern excludes
`_score`/`_ofi_5m`/`_positions`/`_wallets`/`_quality` by construction), EXCEPT the
kept `.name="smart_money"` literals inside the 3 engine modules (globbed out above)
and any kept engine-module `.name` test assertions. The CamelCase grep returns only
the 3 module defs + their importer tests. **NOTE**: TS member-access forms
(`SIGNAL_SLOT_LABEL.smart_money`) and bare-name prose are NOT matched by the quoted
pattern — those are caught by `tsc --noEmit` (a missed `Record` member is a compile
error) and by the explicit prose-rewrite steps (Task 2 prose step + Task 3 prose),
so the quoted gate + tsc + pytest + vitest are a complete backstop. Known frozen
bare-name comments to leave: `agent/core/agent.py:345-346` engine-fanout comment;
the `smart_money_ofi_5m` proposal id in the sprint10 e2e spec.

**(b) The intentionally-FROZEN old-key sites are EXPECTED, not failures** — enumerate
so the next reviewer is not surprised and so nobody edits them chasing zero:
`.dev/contracts/dashboard_ws_message.v0.3.0.json` (frozen enum, superseded by v0.4.0);
the historical `version_bump_reason` text in `.dev/contracts/_registry.json`; the
immutable `.dev/contracts/{weights_schema.v0.1.0,engine_signal.v0.1.0}.json` prose
(also still says `nba_technical` — historical, leave verbatim); and the on-disk
`_ai`/`_gemini`/`_run*` journeys (covered by the read shim + Step 1b runtime gate).
**Do NOT edit any of these to reach zero — they are immutable/archival.**

- [ ] **Step 4: Docs + commit**

Update `docs/optimization_backlog.md` F1 → `DONE`; refresh the slot-name legends in `docs/divinity-mechanism-spec.md`, the `real_signal_source.py` header, and the `decision.py` caveat to the NEW names (slots named for their real Sackmann payload; the genuine wallet/LLM/Reddit engine modules kept as future Stage-2 edge prototypes). The dashboard PROSE narratives (`/backtest`, `/mechanism`) + the `load_static_sweep.ts` / `build_static_sweep.py` doc-comments were rewritten in Task 3 (not token-replaced). **Left historical/verbatim (out of scope by design):** the immutable `.dev/contracts/{dashboard_ws_message.v0.3.0,weights_schema.v0.1.0,engine_signal.v0.1.0}.json` + the registry's historical bump-reason text.
```bash
git add -A
git commit -m "docs: mark F1 rename DONE + refresh slot-name legends to the real payloads; regenerate journeys"
```

---

## Self-review notes

- **Spec coverage:** Task 1 = SoT/parity (spec §A); Task 2 = Python rename + real_signal_source + remap (§B/§C/§D) + wire (§E); Task 3 = dashboard + read-shim + fixtures (§F); Task 4 = regression + runtime gate + journeys (§G) + docs. ✓
- **Backward-compat read shims (both sides):** Python `score_<old>` settlement alias (§D / Task 2) + the TS `normalizeSlotKeys` journey-loader shim (Task 3 Step 0) keep persisted bets AND non-regenerable on-disk journeys (verbatim run1/run2, Gemini-gated AI legs) loading. Identity for new-key data ⇒ zero behavior change.
- **Anchoring guards repeated in every sweep:** exclude `smart_money_wallets`/`smart_money_positions`/`*_score`/`*_conf` + the engine modules; never a bare substring replace; PROSE (dashboard pages, doc-comments) is REWRITTEN, not token-replaced.
- **Three wire-contract tests** (1 Python `test_ws_contract_v0_3_0.py` → v0.4.0; 2 TS `wsContract_v0_3_0.test.ts` → v0.4.0 + `wsContract.test.ts` repoint). The wire version is bumped ONCE and identically (schema `$id`/title/description/version, filename, registry, both `WS_CONTRACT_VERSION` constants, the renamed/repointed test files) Python↔TS.
- **Verification covers the runtime path:** Task 4 Step 1b loads every on-disk journey via the server loaders (the `force-dynamic` `/survival`+`/reincarnation` routes are invisible to `next build`); the completeness sweep uses `--no-ignore` and splits CODE-clean from intentionally-frozen artifacts.
- **Kept/frozen (out of scope):** the 3 engine modules; the immutable `.dev/contracts` v0.3.0/v0.1.0 schemas + historical registry text; the parquet `*_score`/`*_conf` columns + `data/`.

## Revision log

- **2026-06-16 round 1** (panel VERDICT HIGH=3 MEDIUM=5 LOW=6, 0 vote-rejected — all accepted, vetted against real code):
  - HIGH (TS journey loader breaks on renamed keys for non-regenerable journeys): added the **TS `normalizeSlotKeys` read shim** (Task 3 Step 0) mirroring the Python remap — the only non-destructive fix for the verbatim/Gemini-gated exhibits.
  - HIGH (3rd contract test `wsContract.test.ts` invisible): added it to Task 3 + repoint its hardcoded v0.3.0 literals; self-review now enumerates THREE contract tests.
  - MED (success gates can't see `force-dynamic` runtime breakage): added **Task 4 Step 1b runtime journey-load gate** over every on-disk variant.
  - MED (v0.4.0 schema `$id`/title/description left at v0.3.0): Task 2 Step 4 now bumps them.
  - MED (grep sweeps skip gitignored → false PASS): Task 4 Step 3 now uses `--no-ignore`, targets the gitignored journeys + `.dev/`, and splits CODE-clean from the intentionally-frozen v0.3.0 enum / registry / v0.1.0 prose (enumerated as KEPT, do-not-edit).
  - MED (blind sweep would corrupt the /backtest+/mechanism honesty PROSE into false statements): Task 3 now REWRITES that prose to the post-rename framing.
  - MED (Task 3 Files undercounts dashboard tests): enumerated the dashboard test files + the error-regex assertions; named lists declared illustrative, grep authoritative.
  - LOW: rewrite `_ENGINE_DESC` description VALUES + `models.py:104-105` prose; delete the false weight_updater "cycle" comment + verify its import; named the extra constant-importers; noted `{slot}_quality` renames by derivation + the AI-journey freeze.
- **2026-06-16 round 2** (panel VERDICT HIGH=0 MEDIUM=5 LOW=4, 0 vote-rejected — all accepted):
  - MED ×2 (Task 4(a) completeness gate unanchored → ~40 kept-identifier hits, never zero): the gate now uses the ANCHORED quoted-key + constant pattern (excludes `_score`/`_ofi_5m`/`_positions`/`_wallets`/`_quality` by construction); TS member-access caught by tsc, prose by the rewrite steps; frozen bare-name comments enumerated.
  - MED (Python in-code prose/docstrings not in the worklist): added an explicit Task 2 enumeration to REWRITE `real_signal_source.py:154/181/214`, `weight_updater.py:394-395`, `models.py:104-105`, and the `phase1_runner.py:762` report label (lockstep with `test_phase1_runner`).
  - MED (no numeric-equivalence guard on regenerated committed fixtures vs "zero numeric change"): Task 3 Step 2 now saves the originals + asserts the regenerated fixtures equal them modulo the 3 keys (else pin the seed / do a deterministic key-only rewrite).
  - MED (`test_each_engine` mis-scoped → would break the kept module's frozen `.name`): removed from Step 5; added the explicit RULE to LEAVE kept-module `.name` assertions unchanged.
  - LOW (score remap test can't be end-to-end pre-rename + snippet didn't match the dict-comprehension): made it a pure unit test on the alias; fixed the snippet to a `_alias()` helper applied inside the comprehension.
  - LOW (Step 1b runtime gate vacuous if journeys absent): assert ≥1 loaded + run after regen; the synthetic-old-key vitest (Task 3 Step 0) is the authoritative shim guard.
  - LOW (miscount "THREE literals"): corrected to two code literals (:53,:79) + the :17/:22 docstring refresh.
