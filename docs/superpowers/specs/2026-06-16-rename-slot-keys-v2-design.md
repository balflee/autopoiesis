# Rename the 3 misnamed engine-slot keys — Design (v2, premise-corrected)

═══════════════════════════════════════════════════════════════
## ⚠️ GROUND TRUTH — verified 2026-06-16. READ FIRST. Every subagent (planner, reviewer, implementer) must read this before touching anything. The slot names misled the lead THREE times; do not be misled.

1. The ACTIVE signal source in everything that runs — backtest, the survival/能学
   demo, AND prod (`agent/server/main.py:2291` `_make_prod_signal_source` →
   `RealSignalSource`, used at `:2401 signal_source=`) — is
   `agent/backtest/real_signal_source.py::RealSignalSource`. It feeds all 5 fusion
   slots with REAL Sackmann/CLOB data.

2. The 5 engine MODULES (`agent/engines/{smart_money,sentiment_llm,crowd_volume,
   tennis_technical,market_momentum}.py`) — `SmartMoneyEngine` (reads on-chain
   wallets), `SentimentLLMEngine` (Gemini), `CrowdVolumeEngine` (Reddit) — are
   DEFINED but NEVER instantiated in any active path (verified: grep finds only
   `class X(Engine):`, zero `X(...)` instantiation in `agent/`). They are
   dead / prototype code. RealSignalSource BYPASSES them.

3. What each slot ACTUALLY carries (via RealSignalSource):

   | slot key | fed by | real payload | name correct? |
   |---|---|---|---|
   | `tennis_technical` | `elo_signal` | ELO diff | ✅ |
   | `market_momentum` | `momentum_signal` | CLOB price drift | ✅ |
   | `smart_money` | `surface_signal` | **surface advantage** (Sackmann) | ❌ |
   | `sentiment_llm` | `h2h_signal` | **head-to-head** (Sackmann) | ❌ |
   | `crowd_volume` | `rest_signal` | **rest / recency** (Sackmann) | ❌ |

4. **RED FLAG RULE**: if ANY analysis/claim says a slot "carries an on-chain
   wallet / smart-money / LLM-sentiment / Reddit-volume signal," that is the DEAD
   MODULE talking, NOT what the slot carries. The slot carries the Sackmann/CLOB
   payload above. Treat such a claim as UNTRUSTWORTHY and re-verify against
   `real_signal_source.py`.

5. THE RENAME: `smart_money`→`surface_advantage`, `sentiment_llm`→`head_to_head`,
   `crowd_volume`→`rest_recency`. `tennis_technical` / `market_momentum` unchanged.
═══════════════════════════════════════════════════════════════

## Goal

Rename the 3 misnamed fusion-slot KEYS to their real Sackmann payload, so the slot
names stop describing data we don't have (wallet/LLM/Reddit) and instead name what
RealSignalSource actually feeds them. **Zero behavior change** — full regression
green (Python `pytest` + dashboard `vitest` + `next build`) is the success
criterion; no PnL / economy / weight numeric change.

## Scope decisions (brainstorming-approved 2026-06-16)

- **Rename: the slot KEYS + fusion addressing + data/wire/dashboard.**
- **KEEP the engine MODULES** (`SmartMoneyEngine` etc.) untouched — they are
  genuine future Stage-2 edge-layer prototypes (on-chain wallets / Gemini / Reddit),
  decoupled from the slot keys. They are dead code (never instantiated), so the
  rename does not touch `agent/engines/{smart_money,sentiment_llm,crowd_volume}.py`.
  *Watch-point*: if a test cross-asserts a dead engine's `.name` against a slot key,
  update that test (it asserts a now-decoupled label), do NOT rename the module.
- **EXCLUDE the parquet training-feature-column layer** — `data/parquet/
  training_set_v1.parquet` columns (`smart_money_score`/`*_conf`), its writer
  `data/etl/build_training_set.py`, and readers (`phase1_runner`,
  `feature_engineering`, `tennis_features`) are a separate training schema;
  renaming + regenerating a committed binary risks the value_seed zero-behavior
  guarantee. Add `*_score`/`*_conf`/`data/`/`sim/` to the verification-grep
  exclusions and document the exclusion.

## Architecture

**A. Single source of truth.** `decision.py` holds the only slot-key definitions
(`SMART_MONEY`→`SURFACE_ADVANTAGE`="surface_advantage", etc.; `RATIONAL_ENGINES`/
`SENTIENT_ENGINES`). `weight_updater._ALPHA/_BETA_ENGINES` + `event_emitter.
SIGNAL_ENGINE_KEYS` **derive** from it (a parity test enforces equality). Update
the `⚠️ SLOT NAME` caveat block in `decision.py` to reflect the corrected names.

**B. RealSignalSource slot mapping.** `real_signal_source.py` maps each `*_signal`
to its slot constant — rename the target slot keys; update the loud header.

**C. String-literal addressing sites.** Every `.py` that addresses a slot by the
3 old string keys (`replay_runner`, `reincarnation`, `survival_season`,
`sandbox_phase2_loop`, `cached_sweep`, `find_optimal_config`, `training/*`,
`scripts/probe_llm_fusion` `_ENGINE_DESC`, etc.). Anchor replacements so they do
NOT touch `smart_money_wallets`/`smart_money_positions`/`*_score` (overloaded
tokens — see §D/§E).

**D. The `score_{engine}` landmine — backward-compat remap (safe-by-construction).**
Persisted in-flight bets (`state/sandbox/open_bets.jsonl`) carry
`BetRecord.signal_scores` keyed by the OLD slot names; the settlement learner
strips `score_` keeping the key, and `weight_updater` reads by the NEW names via
`.get(..., 0.0)` → silent zero credit on a legacy bet settled post-rename. **Add a
static old→new key remap at the settlement read boundary** (a dict, zero behavior
change) so a legacy persisted bet still credits the right slot. Do NOT gate on
grep-absence (`open_bets.jsonl` is `.jsonl`; a fresh worktree lacks the dir).

**E. Wire contract — BREAKING bump.** The authoritative schema
`.dev/contracts/dashboard_ws_message.v0.3.0.json` (the contract tests LOAD it +
assert its `propertyNames.enum` + version) carries the old keys. This is an
immutable-versioned contract: **create `…v0.4.0.json`** (enum → new keys, version
bumped, `supersedes` v0.3.0), update `.dev/contracts/_registry.json`
(`dashboard_ws_message` pin + a BREAKING `version_bump_reason` — renamed/removed
enum keys = MAJOR per the registry rules), repoint both contract tests'
`SCHEMA_PATH` to v0.4.0, keep the v0.3.0 file (superseded, immutable). Bump the
**bare** `WS_CONTRACT_VERSION = "0.x.0"` constants (event_emitter.py + wsContract.ts)
— note they are `"0.3.0"` not `"v0.3.0"`; the `v0.3.0` token is only filenames /
docstrings. Sweep stale `v0.3.0.json` references in event_emitter docstrings,
`dashboard_bridge/__init__.py`, `death_watch_emitter.py`, runbooks. Pick the new
version once and apply it consistently (schema `$id`/version, filename, registry,
both constants, renamed test filenames).

**F. Dashboard data + pages.** `load_static_sweep.ts` (`SIGNAL_SLOT_KEYS`,
`SIGNAL_SLOT_LABEL`), `/backtest`, `/mechanism`, `DecisionFeed`, the 4 TS wire
defs; regenerate the committed `dashboard/public/backtest/static_sweep.json` via
`build_static_sweep.py` (do not hand-edit). Update the 能学 fixture
`dashboard/public/stage1/stage1_learning.json` + `build_stage1.py` weight-trajectory
slot labels (they reference the old keys), and regenerate it.

**G. Derived gitignored JSON** (`survival_journey*`, `reincarnation*`): regenerate
via producers, never hand-edit.

## Verified-safe (DO NOT touch)

- `value_seed_v3.json` / `value_seed_v4.json` — weights are positional (alpha/beta
  arrays), 0 name hits → no seed migration.
- Production LLM prompts/schema — address weights positionally (`alpha_0`, `beta_0`,
  `w_r`, `rho`), never the slot names → no prompt/parser change, no LLM re-validation.
- EMA in-memory state — recreated on restart.

## Keep (out of scope by design)

- `data/fixtures/smart_money_wallets.json` — genuine smart-money wallet whitelist =
  the A15 future-edge prototype data.
- `agent/data/polygon_chain.py::smart_money_positions` + the wallet API — genuine.
- `agent/engines/{smart_money,sentiment_llm,crowd_volume}.py` — genuine future-edge
  prototypes (see §scope).

## Error handling & testing

- Failures are loud (KeyError / import error / Pydantic / TS-union). The parity
  test + SoT derivation close the silent-drift path; the §D remap closes the
  silent-zero-credit path.
- **Success**: full `pytest tests/` green; full dashboard `vitest` green;
  `next build` 12/12; `static_sweep.json` + `stage1_learning.json` regenerated and
  the pages render; a final grep for `smart_money|sentiment_llm|crowd_volume`
  (and CamelCase `SmartMoneyEngine|…`) across `agent/ scripts/ tests/ dashboard/
  .dev/` returns ONLY the kept wallet-API/fixture/engine-module sites (enumerated),
  excluding `*_score`/`*_conf`/`data/`. **Zero behavior change.**

## Constraints

- Git account **balflee** (verify before every commit); gitleaks not bypassed;
  ignore LF/CRLF warnings.
- Do not touch active-survival / 能学 demo logic (only the slot names it references).
- Branch: new **`rename-slot-keys-v2`** off `main`.
- **Propagation**: the GROUND TRUTH block above MUST be copied verbatim to the top
  of the implementation plan, so every reviewer/implementer subagent reads it; the
  RED FLAG RULE (any "smart_money = wallet signal" claim is untrustworthy) is a
  standing instruction for the review lenses.
