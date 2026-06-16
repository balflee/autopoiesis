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
"""weight_updater + event_emitter slot lists MUST equal decision.py's SoT (guards a
future re-hardcode from drifting), AND the SoT tuples carry the EXPECTED slot
VALUES (pins the rename so it can actually fail if a key is wrong)."""
from __future__ import annotations
from agent.dashboard_bridge.event_emitter import SIGNAL_ENGINE_KEYS
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES
from agent.engines.weight_updater import _ALPHA_ENGINES, _BETA_ENGINES

def test_weight_updater_derives_from_decision_sot() -> None:
    assert _ALPHA_ENGINES == RATIONAL_ENGINES
    assert _BETA_ENGINES == SENTIENT_ENGINES

def test_event_emitter_signal_keys_derive_from_decision_sot() -> None:
    assert tuple(SIGNAL_ENGINE_KEYS) == (*RATIONAL_ENGINES, *SENTIENT_ENGINES)

def test_sot_carries_the_expected_slot_values() -> None:
    # NOTE: these are the OLD values in Task 1 (pre-rename); Task 2 Step 5 updates
    # them to the new names — this is the assertion that PINS the rename.
    assert RATIONAL_ENGINES == ("tennis_technical", "market_momentum", "smart_money")
    assert SENTIENT_ENGINES == ("sentiment_llm", "crowd_volume")
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

- [ ] **Step 1: Add the backward-compat slot-key aliases at BOTH persisted-data read boundaries, with extracted, unit-tested helpers**

Old-key persisted data exists on disk and is read back: (i) in-flight bets
(`open_bets.jsonl`) via `score_<engine>` at settlement; (ii) **the replay INPUT
`reports/backtest/_signal_rows.json` (+ `_signal_rows_v4.json`), keyed by the OLD
slot names in `SignalRow.scores`** — consumed by `cached_sweep.load_rows` →
`row_to_signals`, by `build_static_sweep.py`, `survival_season`, and
`run_reincarnation`. WITHOUT a load-side alias, after the rename `decision.py`'s
missing-signal guard iterates the NEW keys while the rows yield OLD keys → **every
row routes to NO_BET → every regenerated fixture/journey COLLAPSES** (HIGH). Add one
shared alias map applied at both boundaries (identity for new keys ⇒ zero behavior
change):

```python
# a shared module (e.g. agent/engines/slot_aliases.py)
SLOT_KEY_ALIASES = {"smart_money": "surface_advantage",
                    "sentiment_llm": "head_to_head", "crowd_volume": "rest_recency"}
def alias_slot(k: str) -> str: return SLOT_KEY_ALIASES.get(k, k)
```
- **settlement boundary** (`settlement_learner.py` ~:78-82 — currently an INLINE
  dict-comprehension inside `_SettlementLearningWeightUpdater.update`): **EXTRACT** it
  to a module-level `def _unflatten_scores(signals) -> dict[str,float]` that strips
  `score_` AND applies `alias_slot`, and call it from `update`. (Extraction is what
  makes the comprehension's aliasing actually testable.)
- **load boundary** (`cached_sweep.load_rows`): normalize `SignalRow.scores` (and
  `confidences`) keys via `alias_slot` at load, so `row.scores[k]` over the NEW
  `SLOT_KEYS` (e.g. `build_static_sweep.py:198`) finds new keys instead of KeyError-ing.

Add **pure unit tests** (pass INDEPENDENT of the constant rename): `_unflatten_scores`
maps `score_smart_money`→`surface_advantage` (+ identity passthrough); `load_rows`
upgrades an OLD-key SignalRow to NEW keys. (End-to-end credit is verified by Step 6's
full suite after the rename.) **NOTE**: these aliases are a THIRD kept-alias surface —
whitelist `slot_aliases.py` / the alias call sites in Task 4(a)'s gate.

- [ ] **Step 2: Run the alias unit tests — fail (no alias) then pass.** `pytest <new alias tests> -v`.

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

**Python in-code prose/docstrings that NAME the renamed slots** — the anchored
quoted/constant greps DON'T see bare names, so run a **MANDATED bare-name triage
sweep** (a checklist gate, NOT a "must be zero" assertion — most bare-name hits are
legitimately KEPT module/`.name`/`*_score` refs):
```bash
rg -nw 'smart_money|sentiment_llm|crowd_volume' agent/ scripts/ \
  | rg -v 'engines\.(smart_money|sentiment_llm|crowd_volume)|name = "|_score|_conf|_wallet|_position|_quality'
```
Triage EACH surviving hit rename-vs-keep. Known slot-prose to REWRITE (verified
in-scope): `real_signal_source.py:154,181,214` slot docstrings; `weight_updater.py:
394-395` group comment; `scripts/run_learning_demo.py:13-18` docstring (calls the
keys "LEGACY NBA-era labels … smart_money=surface advantage" — rewrite to the
post-rename framing); `agent/training/{phase1_runner.py:480,tennis_runner.py:492}`
("# sentiment_llm frozen β₁=0" → `head_to_head`); `agent/training/phase1_runner.py:762`
generated report label `| smart_money |` (verified NOT asserted by `test_phase1_runner`
— it only checks the report path exists + non-empty; the label rename is safe but
UNVERIFIED, do not claim a lockstep test).
- **`agent/server/models.py:104-105` is PRE-EXISTINGLY WRONG** — it says the mass
  splits "α₂ (market_momentum + smart_money composite) and α₃ (crowd_volume)", but
  per `decision.py` α₂=market_momentum, **α₃=smart_money**, and crowd_volume is β₂
  (sentient), not in the α split. **REWRITE to the true mapping** (α₃ =
  surface_advantage; DELETE the crowd_volume/rest_recency ref from the rational-stream
  prose). A naive token-rename here would produce "α₃ (rest_recency)" — a NEW false
  statement (a sentient slot in the rational position), the exact dishonesty the
  rename exists to remove.

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
`cp .dev/contracts/dashboard_ws_message.v0.3.0.json .dev/contracts/dashboard_ws_message.v0.4.0.json`, then in the v0.4.0 file make it internally consistent (repo-wide `$id`-matches-filename invariant): replace BOTH `propertyNames.enum` arrays' 3 keys with the new names; **also rename the 3 slot-key NAMES listed inside the `"description"` prose** (the description enumerates "tennis_technical, market_momentum, smart_money, sentiment_llm, crowd_volume" — the exact misnomer this rename deletes; rename the 3 there too, or it survives in the canonical contract doc); set internal `"version":"0.4.0"`; rewrite `"$id"` to `.../dashboard_ws_message.v0.4.0.json`; retitle `"title"` to `(v0.4.0)`; update the v0.3.0 version reference in the description; add `supersedes` v0.3.0. **Leave the v0.3.0 file frozen/verbatim** (its old-key enum is intentionally retained — the read shims handle legacy payloads; do NOT edit it chasing zero hits). Update `.dev/contracts/_registry.json` `dashboard_ws_message` pin (version 0.4.0, file v0.4.0.json, supersedes, a BREAKING `version_bump_reason` naming the renamed enum keys). Bump the bare constant `WS_CONTRACT_VERSION: Final[str] = "0.4.0"` in `event_emitter.py` (it is `"0.3.0"`, NOT `"v0.3.0"`). **Wire-version reference sweep — ANCHORED, not a hand-list, and NEVER a bare `v0.3.0` replace** (a blanket replace CORRUPTS unrelated contracts legitimately at v0.3.0 — `agent_lifecycle_abi.v0.3.0`, `phase_manager_abi`, `energy_controller`, `rh_chain_adapter.py`, `docs/page.tsx` ABI entries). Sweep the `dashboard_ws_message`/WS-contract context ONLY:
```bash
rg -n 'dashboard_ws_message\.v0\.3\.0' agent/ dashboard/ tests/ --glob '!*v0.3.0.json'
rg -n 'WS_CONTRACT_VERSION' agent/ dashboard/   # the constants + their 0.3.0 neighbours
```
Update EVERY hit's literal/comment to v0.4.0 (known sites incl. `agent/runtime/{phase2_launch.py,sandbox_phase2_loop.py}`, `dashboard/lib/{types.ts,wsContract.ts,wsEvents.ts}`, `dashboard/components/DecisionFeed.tsx`, `event_emitter.py`/`__init__`/`death_watch_emitter`/runbooks). Completeness gate: `rg -n 'dashboard_ws_message\.v0\.3\.0' agent/ dashboard/ --glob '!*v0.3.0.json'` → ZERO in active code. **Python contract test**: `git mv …/test_ws_contract_v0_3_0.py …/test_ws_contract_v0_4_0.py`; repoint `_SCHEMA_PATH` to v0.4.0; it has **TWO** version assertions (`test_schema_file_pins_v0_3_0` asserts `raw['version']=='0.3.0'`; `test_emitter_constant_is_v0_3_0` asserts `WS_CONTRACT_VERSION=='0.3.0'`) — update BOTH assertions AND rename BOTH functions to `…_v0_4_0`; update expected keys. **Gate the description-prose rename** (otherwise a forgotten rename leaves the misnomer in the canonical doc, caught by nothing — the `.dev/` tree is outside the grep gate and only the enum is validated): add a one-line assertion `assert not any(k in json.dumps(raw) for k in ("smart_money","sentiment_llm","crowd_volume"))` (the committed v0.4.0 file is readable by the test).

- [ ] **Step 5: Update every Python test asserting an old slot KEY or `{slot}_quality` literal** (`test_decision`, `test_weight_updater_settlement`, **`test_weight_updater.py`** (`smart_money_quality`/`sentiment_llm_quality` at :53,:200), **`test_weight_updater_desperate.py`** (`smart_money_quality`/`sentiment_llm_quality`/`crowd_volume_quality` :46-48), `test_real_signal_source`, `test_cached_sweep`, `test_value_sweep`, `test_survival_*`, `test_run_cross_market_journey`, `test_phase1_runner`, `test_sandbox_settlement_poller`, `test_validate_value_seed`, the parity test now asserts the new tuple, plus whatever the grep surfaces). **`{slot}_quality` keys**: in PRODUCTION CODE they rename automatically (derived `f"{engine}_quality"` from the renamed `RATIONAL/SENTIENT_ENGINES` in `weight_updater.py:404`, `phase1_runner.py:120,123`) — no code edit — but TESTS that hardcode `smart_money_quality` literals must rename them (else `.get(...,0.0)` silently zeroes the gradient → red pytest). The `_quality` keys in the FROZEN `reincarnation*.json` `carry.ema_keys` stay old (Step 3b). **`test_each_engine.py` is NOT in this list** — it is a kept-MODULE unit test (wallet positions / LLM calls / crowd windows) and asserts the modules' frozen `.name=="smart_money"`/`"sentiment_llm"`/`"crowd_volume"`; it needs **zero change**. **RULE**: LEAVE any assertion of a KEPT engine module's `.name` (== the old string) UNCHANGED — "updating" it to the new slot name would FAIL (the frozen module still returns the old name). Only rename DECISION-SLOT-KEY assertions.

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

The on-disk journey artifacts (`survival_journey*.json`, `reincarnation*.json`) carry the OLD slot keys, and several are **non-regenerable** (the verbatim `*_run1/*_run2` finetune-log exhibits) or **Gemini-gated** (`*_ai*`/`*_gemini*`). A strict renamed loader (`load_survival_journey.ts::validateSignals` iterates `SURVIVAL_SIGNAL_KEYS` → `asFinite(o[k])` → throws on a missing key) would break `/survival` at request time. (`/reincarnation` is rename-AGNOSTIC: `load_reincarnation.ts` does NO slot-key validation — `carry.ema_keys` is only `Array.isArray`-checked, genomes/knobs validated by VALUE not key — so it neither breaks nor needs the shim; do not claim it as shim evidence.) Add a static old→new alias normalize at the SURVIVAL loader that reads per-step slot signals from an on-disk journey, BEFORE validation (identity for new-key files ⇒ zero behavior change):

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

Apply the 3 key replacements across `dashboard/` (TS/TSX): the `SignalSlotKey` union (`load_static_sweep.ts` `SIGNAL_SLOT_KEYS`) + `SIGNAL_SLOT_LABEL` keys (keep the human labels: surface_advantage→"Surface", head_to_head→"Head-to-Head", rest_recency→"Rest / Recency"), `types.ts`, `wsContract.ts` (bump `WS_CONTRACT_VERSION = "0.4.0"`), the loaders, `DecisionFeed.tsx`. **`/backtest` + `/mechanism` carry the slot keys as PROSE** explaining the misnaming (e.g. backtest/page.tsx:604 "smart_money = on-chain wallets…", mechanism/page.tsx:429) — **REWRITE that prose** to the post-rename framing (the slots are named for their Sackmann payload; the genuine wallet/LLM/Reddit engine modules are KEPT as future edge prototypes), do NOT token-replace it into a false statement. **Repoint all THREE contract tests**: `git mv …/wsContract_v0_3_0.test.ts …/wsContract_v0_4_0.test.ts` (repoint SCHEMA_PATH + version), AND repoint the **two** hardcoded `dashboard_ws_message.v0.3.0.json` code-path literals in `dashboard/__tests__/lib/wsContract.test.ts:53,:79` to v0.4.0 (leave the death-watch `dashboard_death_watch.v0.1.0.json` resolve at :66 untouched), and refresh the two stale `v0.3.0` docstring mentions at `:17,:22` so the file is internally consistent. Its version assert (`:57 schema.version === WS_CONTRACT_VERSION`) then passes via the bumped constant. Update the error-message-regex assertions in `load_survival_journey.test.ts` / `load_static_sweep.test.ts` (the validator string + the test regex rename in lockstep).
```bash
rg -n 'smart_money|sentiment_llm|crowd_volume' dashboard/ --glob '!**/*.json'
```

- [ ] **Step 2: Deterministic key-only rewrite of the committed fixtures (provably zero numeric change) + update the producers for future correctness**

The committed fixtures (`static_sweep.json`, `stage1_learning.json`) must change ONLY
the 3 slot strings, with byte-identical numbers. Do NOT regenerate-and-diff (that is
fragile: producer determinism, a possibly-stale baseline, and the 4 MB journeys being
absent on a fresh checkout all defeat it). Instead apply a **deterministic key-only
rewrite** — this IS the new committed artifact and makes the zero-numeric-change
guarantee structural, not empirical:
```bash
# tiny in-place rewrite (a python one-off): in static_sweep.json rename the 3 SIGNAL
# KEYS inside every sample_bets[].signals object; in stage1_learning.json rewrite the
# VALUE of weight_trajectory.{edge,noise}_slot_label (smart_money→surface_advantage).
# Assert the parsed JSON is IDENTICAL except those exact paths (no other byte moves).
```
THEN update the PRODUCERS so a future regen stays correct + consistent: `build_static_sweep.py`
(its `SLOT_KEYS`) and `build_stage1.py` (the `{edge,noise}_slot_label` values). A LIGHT
producer smoke (KEY-presence only) proves the producer + the `load_rows` alias are
wired — but the producers write to a **FIXED committed path with no `--out`**, so
re-running them OVERWRITES the byte-stable key-rewrite with freshly-computed numbers,
which a later `git add -A` would then commit, **defeating the zero-change guarantee**.
So the smoke MUST: (1) be **gated on its gitignored inputs existing** (`_signal_rows.json`
/ `reports/learning_demo/*` — else skip, per Step 1b's non-vacuity rule); and (2)
**never write to the committed path** — add a `--out` flag to `build_static_sweep.py`
/ `build_stage1.py` and point the smoke at a **tempfile**, asserting key-presence on
that temp output only. **Do NOT use `git checkout -- <fixture>` to "restore"** — at
smoke time the key-rewrite is unstaged and HEAD still holds the OLD-key fixture, so
`git checkout --` would revert to OLD keys and DESTROY the rewrite (→ static_sweep
fails `load_static_sweep` module-eval; stage1 silently reverts its label). The
deterministic key-rewrite + the producer-source update already give the
zero-numeric-change guarantee; the `--out` smoke is only a wiring-proof extra (it is
also fine to DROP the smoke entirely and rely on the key-rewrite + Task 6's full suite).
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
the server loaders and asserts no throw (an integration check that the SURVIVAL
shim rescues old-key + verbatim-archive survival journeys; the reincarnation arms
are loaded only as a generic "still parses" smoke — they are rename-agnostic, not
shim evidence):
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

- [ ] **Step 2: Journeys — correctness is the TS shim, NOT regeneration**

The gitignored journeys (`survival_journey*.json`, `reincarnation*.json`) are NOT
committed, so the "zero committed change" criterion does not apply to them; their
rename-correctness comes entirely from the **TS `normalizeSlotKeys` shim (Task 3 Step
0)**, proven by the synthetic-old-key vitest. The `_ai`/`_gemini`/`_run1`/`_run2` legs
are intentionally left with old keys (Gemini-gated / verbatim finetune-log archives —
re-running needs Gemini quota and would destroy the exhibits); the shim is exactly
what keeps them loading. **The `{slot}_quality` EMA keys (`surface_advantage_quality`)
rename implicitly by derivation.**

OPTIONAL local smoke (NOT a zero-change gate, NOT on a fresh/CI checkout where the
gitignored inputs are absent): regenerate the NUMERICAL `survival_journey.json` via
its CORRECT producer — `scripts/run_v3_numerical.py` (which writes
`dashboard/public/backtest/survival_journey.json` and loads rows via the aliased
`load_rows`), NOT `run_reincarnation.py` (which writes the unrelated
`reincarnation.json`) — and confirm it carries the NEW keys with non-trivial
`summary.total_steps>0` and `learner_final_pnl != static_final_pnl` (a collapsed
all-NO_BET journey would mean the `load_rows` alias is unwired). Gate this on the
input `reports/backtest/_signal_rows.json` existing (else skip, per Step 1b's
non-vacuity rule).

- [ ] **Step 3: Final completeness sweep — two gates (CODE must be clean; ARTIFACTS may intentionally retain old keys)**

ripgrep skips gitignored paths (the `public/backtest/*` journeys + the whole `.dev/`
tree), so a bare `rg` returns a FALSE "clean". Run with `--no-ignore` and split the
gate:

**THE AUTHORITATIVE COMPLETENESS GATE IS THE GREEN SUITE**, not these greps:
full `pytest` (a missed Python key/`_quality` test literal → red), `tsc --noEmit`
(a missed TS slot-key / `Record` member → compile error), `vitest`, `next build`,
the synthetic-old-key shim vitest (Task 3 Step 0), and the deterministic-key-rewrite
zero-change proof (Task 3 Step 2). The greps below are **DISCOVERY AIDS** to find
sites + a sanity readout — they are NOT a "must be zero" gate, and you must NEVER
edit a flagged KEPT/alias site to force zero.

**(a) CODE slot-KEY discovery sweep** — anchored (quoted keys + constants; excludes
`_score`/`_conf`/`_ofi_5m`/`_positions`/`_wallets`/`_quality` by construction since
e.g. `"smart_money"` ≠ `"smart_money_quality"`):
```bash
rg -n --no-ignore '"smart_money"|"sentiment_llm"|"crowd_volume"|\bSMART_MONEY\b|\bSENTIMENT_LLM\b|\bCROWD_VOLUME\b' \
  agent/ scripts/ tests/ dashboard/ \
  --glob '!agent/engines/smart_money.py' --glob '!agent/engines/sentiment_llm.py' \
  --glob '!agent/engines/crowd_volume.py' --glob '!agent/engines/slot_aliases.py' \
  --glob '!dashboard/lib/slot_key_aliases.ts' \
  --glob '!.dev/contracts/dashboard_ws_message.v0.3.0.json'
rg -n --no-ignore 'SmartMoneyEngine|SentimentLLMEngine|CrowdVolumeEngine' agent/ tests/
```
Surviving hits should be ONLY the enumerated KEPT-ALIAS surfaces (do NOT delete them
to reach zero — that destroys the old→new map and collapses every legacy bet/row/
journey): the `SLOT_KEY_ALIASES` maps in `slot_aliases.py` + `slot_key_aliases.ts`
and their **alias unit tests** (in `tests/` — globs don't reach them; they legitimately
assert `alias_slot("smart_money")=="surface_advantage"`); the kept engine-module
`.name="smart_money"` literals + their `.name` test assertions. **`{slot}_quality`**:
renames in code by DERIVATION (the f-string is not a literal → no gate hit; correct);
its test literals are renamed by Step 5; the frozen-artifact `_quality` stays (3b).
TS member-access (`SIGNAL_SLOT_LABEL.smart_money`) + bare-name prose are caught by tsc
+ the prose-rewrite steps. Known frozen bare-name comments to leave: `agent/core/agent.py:345-346` engine-fanout comment;
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
- **2026-06-16 round 3** (panel VERDICT HIGH=1 MEDIUM=4 LOW=3, 0 vote-rejected — all accepted, all NEW (not re-reports)):
  - HIGH (replay input `reports/backtest/_signal_rows.json` keyed by OLD names → every regen collapses to NO_BET): Task 2 Step 1 now adds a shared `alias_slot` at BOTH read boundaries — settlement `_unflatten_scores` (extracted module-level) AND `cached_sweep.load_rows` (upgrades persisted `SignalRow.scores`); whitelisted as a 3rd alias site in the gate.
  - MED (no equivalence guard on regenerated gitignored journeys — a collapsed journey passes green): Task 4 Step 2 now asserts the numerical journey equals pre-rename modulo keys (total_steps + learner_final_pnl non-trivial + unchanged).
  - MED (bare-name slot prose invisible to the gate + hand-list incomplete): replaced the illustrative list with a MANDATED bare-name triage sweep (checklist, not assertion); added run_learning_demo:13-18, phase1_runner:480, tennis_runner:492.
  - MED (`models.py:104-105` is PRE-EXISTINGLY WRONG: α₃=crowd_volume): rewrite to the true mapping (α₃=surface_advantage; delete the sentient crowd_volume from the rational prose) — a token-rename would manufacture "α₃ (rest_recency)".
  - MED (equivalence guard modeled KEY rename but stage1's change is a label VALUE): split the guard per artifact (static_sweep=key-normalize, stage1=value-rewrite); corrected the mis-targeted "pin seed" remedy.
  - LOW (parity test tautology): added a concrete-VALUE assertion that pins the rename (updated old→new in Task 2 Step 5).
  - LOW (false "phase1_runner:762 asserted by test" claim): corrected — the label has no test coverage (rename safe but unverified).
  - LOW (score-remap "pure unit test" couldn't reach the inline comprehension): extract `_unflatten_scores` to a module-level function + unit-test that.
- **2026-06-16 round 4** (panel VERDICT HIGH=1 MEDIUM=3 LOW=1, 0 vote-rejected — all in the VERIFICATION machinery, core rename solid):
  - HIGH/MED (journey equivalence regenerated the WRONG artifact — `run_reincarnation` writes `reincarnation.json`, not `survival_journey.json` → vacuous guard + showpiece never cut over) **and** MED (equivalence-guard baseline never proven idempotent; 4 MB journeys absent on fresh CI): root-cause SIMPLIFICATION — Task 3 Step 2 now does a **deterministic key-only rewrite** of the COMMITTED fixtures (structural zero-numeric-change, no producer/baseline/determinism dependence) + updates the producers + a key-presence smoke; Task 4 Step 2 makes journey correctness the TS shim (not regeneration; they're uncommitted), with an OPTIONAL smoke via the CORRECT producer `run_v3_numerical.py` gated on the input existing.
  - MED (wire-version `v0.3.0` sweep incomplete + a blanket replace corrupts unrelated ABI contracts): replaced the hand-list with an ANCHORED `dashboard_ws_message.v0.3.0` + `WS_CONTRACT_VERSION` sweep + a completeness gate + an explicit "never bare v0.3.0" warning; and rename the 3 slot-key NAMES inside the v0.4.0 schema description prose.
  - LOW (contract test under-enumerated): the Python test has TWO version assertions + TWO `v0_3_0` function names (update both, rename both); the wsContract.test.ts death-watch resolve is at :66, not :64.
- **2026-06-16 round 5** (panel VERDICT HIGH=1 MEDIUM=2 LOW=2, 0 vote-rejected):
  - HIGH (`{slot}_quality` derived keys: 2 test files feed old `*_quality` → red pytest; plan self-contradicted — Step 2 "renames" vs Step 3a "kept"): added `test_weight_updater.py` + `test_weight_updater_desperate.py` to Step 5; clarified `_quality` renames in code by derivation, test literals must rename, frozen-artifact `_quality` stays; removed the "kept smart_money_quality" framing.
  - MED (root-cause for the recurring gate self-inconsistency): **demoted the anchored greps to DISCOVERY AIDS** and made the **GREEN SUITE the authoritative completeness gate** (pytest/tsc/vitest/build + shim vitest + key-rewrite proof); added `slot_aliases.py`/`slot_key_aliases.ts` + their alias unit tests to the kept-alias allow-list (NEVER edit the alias to reach zero).
  - MED (producer smoke clobbers the key-rewrite + fails on fresh checkout): gate it on inputs existing + redirect output (`--out` tempfile OR `git checkout --` restore the key-rewrite BEFORE `git add`).
  - LOW (`/reincarnation` shim over-claim): its loader does NO slot-key validation → rename-agnostic; scoped the shim claim + Step 1b to SURVIVAL journeys (reincarnation = generic parse smoke only).
  - LOW (v0.4.0 description prose rename ungated): added a one-line contract-test assertion that the loaded schema contains none of the 3 old strings.
- **2026-06-16 round 6** (panel VERDICT HIGH=0 MEDIUM=1 LOW=0 — converging):
  - MED (the round-5 `git checkout -- <fixture>` "restore" reverts to HEAD's OLD-key version, destroying the key-rewrite — the rewrite is unstaged at smoke time): dropped the `git checkout` path entirely; the producer smoke now uses ONLY a `--out` tempfile (or may be dropped — the key-rewrite + producer-source update already guarantee zero numeric change).
