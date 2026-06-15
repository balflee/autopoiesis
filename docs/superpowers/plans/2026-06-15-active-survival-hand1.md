# Active Survival (Hand 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Anchor edits by SYMBOL (function name + grep sentinel), not raw line numbers — confirm in each task's Read-first step.**

**Goal:** Make honest-win-rate survival achievable by recalibrating the breath economy with a SIM-BASED calibration (running the REAL prod sim), and stop the agent freezing via a non-advisable exploration floor — validated on a synthetic known-edge harness.

**Architecture:** A deterministic synthetic generator returns BOTH `SurvivalRow`s and matching `MarketSnapshot`s. Calibration runs short *real* numerical seasons through `run_survival_over_rows` (owns `loss_multiplier` via `SurvivalRecorder`, value mode on) across a loss-multiplier grid on a `+edge` and a `0-edge` world, and picks the multiplier that lets the edge world survive while the no-edge world dies — measured from the SERIALIZED journey dict (death-aware). `exploration_epsilon` is a non-advisable `StrategyConfig` field; `DecisionEngine` adds ONE post-fusion ε-greedy branch that places a FLAT `min_bet_size_usd` probe (no Kelly), gated by `epsilon>0 AND rng is not None` and threaded only on the LEARNING path (the frozen holdout + static baseline keep `rng=None`). A validation harness proves the criteria.

**Tech Stack:** Python 3.14, pytest, the `agent/backtest/` groundhog runtime. numerical provider only (no LLM).

**Spec:** `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md`. **Branch:** `active-survival-hand1`. One commit/task; git identity `balflee`. **Descoped → Hand 1.5:** A14, A2.

**Honest framing (review):** the synthetic signal is a CONSTANT YES tilt, so the agent does NOT "learn" the edge — it blindly bets YES into a world whose YES-rate = `0.5+edge`. The success criterion is therefore **"survives the recalibrated economy on a +edge world and dies on a 0-edge world"**, not "learns/exploits the edge."

---

## Grounded contracts (verified — do not re-derive)

- `SignalRow` (`cached_sweep.py:131-154`): `SignalRow(market_id, slug, scores:dict, confidences:dict, entry_price, outcome:str, winning_price, liquidity_cap_usd, cross_market_signal=0.0, cluster_key="")`. Keys = `tennis_technical, market_momentum, smart_money, sentiment_llm, crowd_volume`. `outcome ∈ {"yes","no","void"}`.
- **Settlement (`cached_sweep.py:95-112`):** win iff `(side=="YES")==(outcome=="yes")`; **`winning_price` ≈ 1.0 for ANY resolved market** (`historical_fetcher.py:140`; `<0.99 → void`). Synthetic rows set `winning_price=1.0` (side carried by `outcome`).
- `SurvivalRow` (`survival_season.py:197-265`, frozen): own fields + read-only delegations to `signal`. `build_survival_rows(rows, snapshots, resolver, *, entry_fraction=0.5, entry_price_floor=0.05)` raises if `abs(recomputed_mid − entry_price) > 1e-9`; flat `price_ledger` at `p` ⇒ exact. It ALSO calls `parse_slug(snap.slug)` (slugs must be `"<a>-vs-<b>"`-shaped) and `resolver.resolve(slug)` (returns `ResolvedMatch|None`; `.surface` used only when not None). **Resolver type = `TennisMatchResolver(name_index={})`** (frozen dataclass, `tennis_match_resolver.py:100-114`), NOT a `_null_resolver`.
- `PricePoint` (`historical_fetcher.py:107-121`) = pydantic `extra='forbid', frozen`; fields `ts:str` (ISO, non-decreasing), `mid_price:float (0..1)`. **KW-only:** `PricePoint(ts=iso, mid_price=p)`. `MarketSnapshot` (pydantic, kw-only) needs exactly `market_id, slug, end_date_iso, resolution_ts_iso, outcome, winning_price(0..1), liquidity_cap_usd(>0), price_ledger` — confirm exact fields in Task 1 Read-first.
- Row→agent: `row_to_signals(row.signal)` → `Signal(score, confidence)`. Value mode `edge_abs=|kappa·fused|` must clear `min_edge`. v3 seed: `kappa≈0.492, min_edge≈0.0349`. All scores `=C`, conf `=0.8` ⇒ `fused≈0.8C`, `edge_abs≈0.394C` → `C≈0.30` clears the gate on EVERY row (so the +edge/0-edge calibration worlds are ALL-BETTING; a separate sub-gate world is needed to exercise abstention — see Task 1/6).
- `StrategyConfig` (`find_optimal_config.py:57-82`, frozen): `weights, max_breath_risk_pct, min_confidence, min_bet_size_usd, min_edge=0.0, kappa=0.25, gate_storm_sensitivity=0.0, risk_storm_sensitivity=0.0, kappa_xm=0.0`. Loader = `run_v3_numerical.py:30-40 load_v3_seed`; serializer = `validate_value_seed.py _seed_payload`.
- **Calibration entry = `run_survival_over_rows(survival_rows, snapshots, *, base_seed, loss_multiplier, initial_breath, max_lives, with_ai=False, value_betting=True, side_correct_pricing=True, effective_entry_price_floor=MIRROR_ROW_FLOOR)`** (`survival_season.py:2710-2733`). Fragilizes `base_seed` via `fragile_seed_from_config` (`dataclasses.replace`) + builds `SurvivalRecorder(rows, loss_multiplier=m)` (ONLY place `loss_multiplier` touches breath, `:787-791`). `run_survival_season` has NO `loss_multiplier` kwarg. **`max_steps` does NOT bound the season** (it's journey down-sample only) — control length via `n_rows` (schedule len) + `max_lives`.
- **RETURN SHAPE: `run_survival_over_rows` returns the SERIALIZED JOURNEY DICT** (`survival_season.py:2596-2648`), NOT `SeasonResult`. `journey["summary"]["deaths"]` is an int; `journey["summary"]["lives"]` is an int COUNT; the per-life LIST is **top-level `journey["lives"]`** (`:2456-2480`) with keys `idx/start_ts/bets/settlements/final_breath/final_bankroll_usd/pnl/death` (`death` is `None` ⇔ alive). **There is NO `consumed_market_ids` in the serialized payload.**
- **Metric (death-aware, locked):** `deaths=journey["summary"]["deaths"]`; `lives=journey["lives"]` (list); `death_rate=deaths/max_lives`; `mean_final_breath=mean(l["final_breath"] for l in lives)`; `total_bets=sum(l["bets"] for l in lives)`. Assert `isinstance(journey["lives"], list)` in the helper.
- `_decision_engine_from_seed(seed, *, effective_entry_price_floor=None, exploration_rng=None)` (`survival_season.py:1515`) is called at **`:1739` (LEARNER, in `_build_life_loop`)** and **`:2057` (FROZEN baseline twin)**. Thread rng only via the `:1739` call; baseline + holdout pass `None`.
- Holdout: `_run_frozen_holdout` → `run_survival_season(..., learning_enabled=False)` (`reincarnation.py:871-886`) ALSO runs `_build_life_loop`. So exploration MUST be gated on the LEARNING path, not just the seed (epsilon rides the seed into the holdout via `dataclasses.replace`).
- Run harness: `run_reincarnation.py:118-124` loads the REAL cache UNCONDITIONALLY before the design branch. `:156-157` literals feed `run_groundhog_export` (groundhog); `:226-227` feed `run_reincarnation_export` (passes — out of scope). Loop at `agent/runtime/sandbox_phase2_loop.py`.

---

## File Structure

| File | C/M | Responsibility |
|---|---|---|
| `agent/backtest/synthetic_edge.py` | Create | `build_synthetic_world(n,edge,seed)->(rows,snaps)` (all-betting); `build_abstain_world(n,seed)->(rows,snaps)` (sub-gate, exercises abstention); `agent_ev(rows)`. |
| `tests/agent/backtest/test_synthetic_edge.py` | Create | EV≈edge; rows pass build_survival_rows; winning_price==1.0; abstain-world produces sub-gate rows; deterministic. |
| `agent/backtest/calibrate_breath_economy.py` | Create | Sweep via `run_survival_over_rows`; death-aware dual criterion; non-saturating fail-closed sentinel; write JSON. |
| `tests/agent/backtest/test_calibrate_breath_economy.py` | Create | mean_final_breath moves with m (fail-closed); recommended separates live from dead. |
| `agent/backtest/find_optimal_config.py` | Modify | `exploration_epsilon: float = 0.0` on StrategyConfig (NOT in GENOME_KEYS). |
| `scripts/run_v3_numerical.py` | Modify | `load_v3_seed` reads `raw.get("exploration_epsilon", 0.0)`. |
| `agent/backtest/validate_value_seed.py` | Modify | `_seed_payload` serializes `exploration_epsilon`. |
| `agent/engines/decision.py` | Modify | ctor `exploration_epsilon`+`exploration_rng`; shared `_clamped_size(*, desired, ...)`; ONE post-fusion FLAT-stake ε-branch. |
| `tests/agent/engines/test_decision_exploration.py` | Create | ε=0/rng=None never touch rng; ε>0 (with `min_edge>0`) lifts abstain→bet ≈ε; explored size obeys caps; missing-signal/neutral never explore. |
| `agent/backtest/survival_season.py` | Modify | `exploration_rng` param on `_decision_engine_from_seed` (`:1739` only), `_build_life_loop`, `run_survival_season`, `run_survival_over_rows`. |
| `agent/backtest/reincarnation.py` | Modify | `run_groundhog_export(..., exploration_epsilon=0.0)`; per-incarnation `random.Random(incarnation_k)` on the in-sample call; `None` on holdout. |
| `scripts/run_reincarnation.py` | Modify | groundhog-only flags; replace `:156-157`; `--synthetic-edge` BYPASSES the `:118-124` cache load; fail-closed; error on passes+active-flags. |
| `scripts/run_active_survival_validation.py` | Create | Survive-+edge / die-0-edge + floor-safety (abstain world) + g1/g2 real-cache → report. |
| `tests/agent/runtime/test_active_survival_integration.py` | Create | Short end-to-end; journey-dict metrics; schema unbroken. |

---

## Task 1: Synthetic worlds (edge + abstain)

**Files:** Create `agent/backtest/synthetic_edge.py`, `tests/agent/backtest/test_synthetic_edge.py`.

- [ ] **Step 1: Read first** — `cached_sweep.py:95-154`; `survival_season.py:197-376` (incl. `parse_slug` use at `:351-356`); `MarketSnapshot`+`PricePoint` (`historical_fetcher.py:107-170` — confirm exact required fields); `TennisMatchResolver` (`tennis_match_resolver.py:100-114`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/agent/backtest/test_synthetic_edge.py
from agent.backtest.synthetic_edge import build_synthetic_world, build_abstain_world, agent_ev

def test_zero_edge_ev_is_zero():
    rows, _ = build_synthetic_world(n=4000, edge=0.0, seed=7)
    assert abs(agent_ev(rows)) < 0.02

def test_positive_edge_ev_matches():
    rows, _ = build_synthetic_world(n=4000, edge=0.10, seed=7)
    assert abs(agent_ev(rows) - 0.10) < 0.03

def test_rows_pass_build_survival_rows_and_winning_price_one():
    rows, _ = build_synthetic_world(n=50, edge=0.08, seed=3)
    assert len(rows) == 50 and all(r.winning_price == 1.0 for r in rows)

def test_abstain_world_has_below_gate_rows(v3_seed):
    rows, _ = build_abstain_world(n=200, seed=5)
    # at least some rows fuse below the v3 min_edge gate so the agent abstains
    from agent.backtest.synthetic_edge import _edge_abs_for
    assert any(_edge_abs_for(r, v3_seed) < v3_seed.min_edge for r in rows)

def test_deterministic():
    a, _ = build_synthetic_world(n=200, edge=0.08, seed=3)
    b, _ = build_synthetic_world(n=200, edge=0.08, seed=3)
    assert [r.market_id for r in a] == [r.market_id for r in b]
    assert [r.outcome for r in a] == [r.outcome for r in b]
```

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement.**
  - `_make_pair(i, *, price, C, won, rng)`: build `SignalRow(market_id=f"syn-{i:06d}", slug=f"alpha{i}-vs-beta{i}", scores={k:C for k in ENGINE_KEYS}, confidences={k:0.8 for k in ENGINE_KEYS}, entry_price=price, outcome=("yes" if won else "no"), winning_price=1.0, liquidity_cap_usd=1000.0)` and a `MarketSnapshot(market_id=..., slug=..., outcome=..., winning_price=1.0, liquidity_cap_usd=1000.0, end_date_iso=ISO, resolution_ts_iso=ISO, price_ledger=[PricePoint(ts=iso_t0_i, mid_price=price), PricePoint(ts=iso_t1_i, mid_price=price)])` with **distinct monotonically-increasing `iso_t*` per row `i`** (so the entry-sorted schedule is well-ordered).
  - `build_synthetic_world(n, edge, seed)`: `rng=random.Random(seed)`; `price=0.5`; `true_prob=clip(0.5+edge)`; `C=0.30` (clears v3 gate ⇒ all-betting). For each i: `won=rng.random()<true_prob`. Return `build_survival_rows(signal_rows, snaps, TennisMatchResolver(name_index={})), snaps`.
  - `build_abstain_world(n, seed)`: same but per row draw `C` from `{0.05, 0.30}` (50/50) so ~half the rows fuse BELOW `min_edge` (agent abstains there) and `true_prob=0.5` (no edge). Used ONLY for the floor-safety test.
  - `agent_ev(rows)`: `mean((1.0 if r.outcome=="yes" else 0.0) - r.entry_price for r in rows)`.
  - `_edge_abs_for(row, seed)`: helper returning `abs(seed.kappa * 0.8 * row.scores[ENGINE_KEYS[0]])` for the test.
  - All randomness via `random.Random(seed)`.

- [ ] **Step 5: Run, verify pass. Commit** `feat(survival): synthetic edge + abstain worlds (rows+snapshots)`

---

## Task 2: Sim-based breath-economy calibration

**Files:** Create `agent/backtest/calibrate_breath_economy.py`, `tests/agent/backtest/test_calibrate_breath_economy.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_calibrate_breath_economy.py
from agent.backtest.calibrate_breath_economy import calibrate, CalibrationResult

def test_multiplier_actually_varies_physics():
    res = calibrate(loss_multiplier_grid=[1.0, 5.0], initial_breath=35.0,
                    edge_live=0.10, edge_dead=0.0, n_rows=400, max_lives=6, seed=0)
    # NON-SATURATING sentinel: mean_final_breath on the LIVE world moves with m
    assert res.grid[0]["mean_final_breath_live"] != res.grid[1]["mean_final_breath_live"]

def test_recommended_separates_live_from_dead():
    res = calibrate(loss_multiplier_grid=[1.0,1.2,1.5,2.0,3.0,5.0], initial_breath=35.0,
                    edge_live=0.10, edge_dead=0.0, n_rows=400, max_lives=6, seed=0)
    assert isinstance(res, CalibrationResult)
    assert res.death_rate_live < res.death_rate_dead
    assert res.loss_multiplier in [1.0,1.2,1.5,2.0,3.0,5.0]
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** `_metrics(journey, max_lives) -> dict`: assert `isinstance(journey["lives"], list)`; return `{death_rate: journey["summary"]["deaths"]/max_lives, mean_final_breath: mean(l["final_breath"] for l in journey["lives"]), total_bets: sum(l["bets"] for l in journey["lives"])}`.
  - `base = dataclasses.replace(load_v3_seed(), exploration_epsilon=0.0)` (clean economy; exploration off).
  - For each `m`: build FRESH `(rows_live,snaps_live)=build_synthetic_world(n_rows, edge_live, seed)` and `(rows_dead,snaps_dead)=build_synthetic_world(n_rows, edge_dead, seed)`. For each, `journey = run_survival_over_rows(rows, snaps, base_seed=base, loss_multiplier=m, initial_breath=initial_breath, max_lives=max_lives, with_ai=False, preflight=False)`; record `_metrics`.
  - Fail-closed sentinel = `mean_final_breath_live` (non-saturating; live world has headroom). Dual criterion: pick `m` maximizing `(death_rate_dead − death_rate_live)` with `death_rate_live` low (`<0.5`) and `death_rate_dead` high (`>0.5`); raise `ValueError` if none separate.
  - `CalibrationResult(loss_multiplier, death_rate_live, death_rate_dead, initial_breath, exploration_epsilon=0.05, grid=[...])`. `main()` writes `reports/calibration/breath_economy_hand1.json`. Grids small (≤8 m, ≤6 lives, ≤400 rows).
- [ ] **Step 4: Run, verify pass.** Run `python -m agent.backtest.calibrate_breath_economy`; confirm JSON with `loss_multiplier < 5.0`.
- [ ] **Step 5: Commit** `feat(survival): sim-based breath-economy calibration (journey-dict, non-saturating sentinel)`

---

## Task 3: `exploration_epsilon` on StrategyConfig + loader round-trip

(unchanged from prior — see File Structure.) Add `exploration_epsilon: float = 0.0` to `StrategyConfig` (NOT `GENOME_KEYS`); `load_v3_seed` reads `raw.get("exploration_epsilon", 0.0)`; `_seed_payload` serializes it. Test: default 0.0; not in GENOME_KEYS; round-trips through `load_v3_seed`. Verify `fragile_seed_from_config` (`dataclasses.replace`) preserves it. **Commit** `feat(config): non-advisable exploration_epsilon + loader round-trip`.

---

## Task 4: Exploration floor in the decision engine (FLAT stake, learning-gated rng)

**Files:** Modify `agent/engines/decision.py`; `agent/backtest/survival_season.py` (`_decision_engine_from_seed`, `_build_life_loop`, `run_survival_season`, `run_survival_over_rows`). Test `tests/agent/engines/test_decision_exploration.py`.

**Design (resolves round-3 HIGH):**
- Gate: explore only if `self._exploration_epsilon > 0 AND self._exploration_rng is not None`. RNG touched ONLY inside the branch ⇒ ε=0 / rng=None byte-identical.
- **Exploration bet is a FLAT `min_bet_size_usd` probe — NO Kelly** (Kelly is undefined at the `:363/:380` abstains and would collapse to ~0 on no-edge). Size via shared `_clamped_size(*, desired, breath, max_breath_risk_pct, conversion_rate, bankroll_usd, bet_size_cap, liquidity_cap_usd)` (extracted from `:392-405`); exploration passes `desired=min_bet_size_usd, bet_size_cap=NORMAL_BET_SIZE_CAP`; normal path passes `desired=rho_eff*kelly*conf*bankroll`. Return the probe BET iff clamped `≥ min_bet_size_usd`, else the original NO_BET.
- Explorable abstains (side resolvable, post-fusion): low-confidence (`:319`, side=`sign(fused)`), price-floor (`:363`), no-edge (`:380`), zero-kelly (`:386`), below-min-size (`:411`). **Never explore** missing-signal (`:303`) or exactly-neutral (`:329`/`:352`).
- **rng is gated on the LEARNING path** (the frozen holdout `learning_enabled=False` + the `:2057` static baseline keep `rng=None`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/engines/test_decision_exploration.py
import random, asyncio
from agent.engines.decision import DecisionEngine

class _Raising(random.Random):
    def random(self): raise AssertionError("rng touched")

def _eng(**kw):  # no-edge gate needs a positive min_edge (default is 0.0)
    return DecisionEngine(min_edge=0.05, min_confidence=0.05, **kw)

def test_epsilon_zero_never_touches_rng(no_edge_kwargs):
    assert asyncio.run(_eng(exploration_epsilon=0.0, exploration_rng=_Raising())
                       .decide(**no_edge_kwargs)).kind.name == "NO_BET"

def test_rng_none_never_explores(no_edge_kwargs):
    assert asyncio.run(_eng(exploration_epsilon=1.0, exploration_rng=None)
                       .decide(**no_edge_kwargs)).kind.name == "NO_BET"

def test_epsilon_lifts_abstain_to_bet_rate(no_edge_kwargs):
    eng = _eng(exploration_epsilon=0.2, exploration_rng=random.Random(0))
    bets = sum(asyncio.run(eng.decide(**no_edge_kwargs)).kind.name == "BET" for _ in range(2000))
    assert 0.15 < bets/2000 < 0.25

def test_explored_size_respects_caps(no_edge_kwargs):
    eng = _eng(exploration_epsilon=1.0, exploration_rng=random.Random(1), min_bet_size_usd=5.0)
    assert asyncio.run(eng.decide(**{**no_edge_kwargs, "liquidity_cap_usd": 0.0})).kind.name == "NO_BET"

def test_missing_signal_never_explores(missing_signal_kwargs):
    assert asyncio.run(_eng(exploration_epsilon=1.0, exploration_rng=random.Random(0))
                       .decide(**missing_signal_kwargs)).kind.name == "NO_BET"
```

(`no_edge_kwargs`: value-mode signals fusing non-zero with `edge_abs < 0.05` so the no-edge gate fires with a resolvable side. `missing_signal_kwargs`: signals dict missing an engine.)

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: decision.py.** ctor adds `exploration_epsilon: float = 0.0`, `exploration_rng: random.Random | None = None` (validate `0<=eps<=1`; `import random`). Extract `_clamped_size(...)`. Add `self._explore_or(nobet, *, side, bankroll_usd, breath, liquidity_cap_usd)`: if `eps>0 and rng is not None and rng.random()<eps and side is not None`, return `_clamped_size(desired=self._min_bet_size_usd, ...) ≥ min_bet_size_usd ? BET(side, size) : nobet`, else `nobet`. Replace the 5 explorable NO_BET returns with `return self._explore_or(<that NO_BET>, side=<sign(fused) or computed side>, ...)`. Leave `:303/:329/:352` plain.
- [ ] **Step 4: Thread rng (survival_season.py).** Add `exploration_rng: random.Random | None = None` to `_decision_engine_from_seed` → pass `exploration_epsilon=seed.exploration_epsilon, exploration_rng=exploration_rng` to `DecisionEngine`. Add the same kwarg (default `None`) to `_build_life_loop` (pass to the `:1739` learner call; the `:2057` baseline call stays `None`), to `run_survival_season`, and to `run_survival_over_rows` (thread straight through). **All defaults `None` ⇒ every non-Hand1 caller + holdout + baseline byte-identical.**
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/test_decision_exploration.py tests/agent/engines/test_decision.py -v` → all PASS.
- [ ] **Step 6: Commit** `feat(decision): flat-stake post-fusion exploration floor, learning-gated rng`

---

## Task 5: Plumb calibrated params + exploration through the groundhog harness

**Files:** Modify `agent/backtest/reincarnation.py` (`run_groundhog_export`), `scripts/run_reincarnation.py`. **Groundhog ONLY.**

- [ ] **Step 1: Failing test** (in Task 6's file): `run_groundhog_export(rows, snaps, base_seed=<v3>, loss_multiplier=1.2, initial_breath=35.0, exploration_epsilon=0.1, max_incarnations=3)` on a tiny ABSTAIN world (so exploration matters); assert artifact `knobs.loss_multiplier==1.2`, run completes numerical, and `total_bets>0` (proves exploration reached the engine on a world that would otherwise abstain).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: reincarnation.py.** Add `exploration_epsilon: float = 0.0` to `run_groundhog_export`; set `base_seed = dataclasses.replace(base_seed, exploration_epsilon=exploration_epsilon)` once, up front. At the per-incarnation in-sample `run_survival_season(...)` call (the respawn loop), pass `exploration_rng=random.Random(incarnation_k)` (varies per incarnation — `idx` alone is always 0 since `max_lives=1` per incarnation) ONLY when `exploration_epsilon>0` else `None`. At the holdout call (`_run_frozen_holdout`, `learning_enabled=False`), pass `exploration_rng=None`. Do NOT re-add `loss_multiplier`/`initial_breath`/`tithe_*`.
- [ ] **Step 4: run_reincarnation.py.** Add flags `--loss-multiplier`, `--initial-breath`, `--exploration-epsilon`, `--synthetic-edge`. **If `--synthetic-edge E` is set: SKIP the unconditional cache load (`:118-124`)** and instead `rows, snaps = build_synthetic_world(n=..., edge=E, seed=...)`. In the `design=="groundhog"` branch: replace the `:156-157` literals with flag-resolved values + pass `exploration_epsilon=`. Resolve defaults from `reports/calibration/breath_economy_hand1.json` if present; if an active flag is requested but unset AND the JSON absent, **fail closed**. **If `--design passes` + any active flag → error** ("Hand-1 active params support --design groundhog only"); leave `run_reincarnation_export` untouched.
- [ ] **Step 5: Run** the focused test → PASS. Smoke (Windows-safe): `python scripts/run_reincarnation.py --provider numerical --design groundhog --loss-multiplier 1.2 --exploration-epsilon 0.1 --synthetic-edge 0.1 --max-incarnations 3 --out reports/_smoke/smoke.json` (gitignore `reports/_smoke/`).
- [ ] **Step 6: Commit** `feat(reincarnation): groundhog calibrated economy + per-incarnation exploration + synthetic-edge`

---

## Task 6: Validation harness

**Files:** Create `scripts/run_active_survival_validation.py`, `tests/agent/runtime/test_active_survival_integration.py`.

`run_validation(world, exploration_epsilon, loss_multiplier, n, max_lives, seed, out)` builds the requested world (`"edge:E"` → `build_synthetic_world`; `"abstain"` → `build_abstain_world`; `"cache"`/`"cache_g2"` → real cache, g2 with shuffle), calls `run_survival_over_rows(..., exploration_rng=random.Random(0) if exploration_epsilon>0 else None)`, returns `_metrics(...)`.

- [ ] **Step 1: Integration test**

```python
# tests/agent/runtime/test_active_survival_integration.py (sketch)
from scripts.run_active_survival_validation import run_validation

def test_survives_edge_dies_without(tmp_path):
    hi = run_validation(world="edge:0.10", exploration_epsilon=0.1, loss_multiplier=1.2,
                        n=600, max_lives=6, seed=0, out=tmp_path/"hi.json")
    lo = run_validation(world="edge:0.0", exploration_epsilon=0.1, loss_multiplier=1.2,
                        n=600, max_lives=6, seed=0, out=tmp_path/"lo.json")
    assert hi.death_rate < lo.death_rate     # survives +edge, dies 0-edge
    assert lo.deaths >= 1

def test_floor_does_not_accelerate_death_on_abstain_world(tmp_path):
    on  = run_validation(world="abstain", exploration_epsilon=0.1, loss_multiplier=1.2,
                         n=600, max_lives=6, seed=1, out=tmp_path/"on.json")
    off = run_validation(world="abstain", exploration_epsilon=0.0, loss_multiplier=1.2,
                         n=600, max_lives=6, seed=1, out=tmp_path/"off.json")
    assert on.total_bets > off.total_bets               # floor actually fires (off abstains more)
    assert on.death_rate <= off.death_rate + 0.15       # but does not materially worsen survival
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `run_active_survival_validation.py` with the shared `_metrics` helper. `main()` runs: (a) survive/die `edge ∈ {0,0.05,0.10}`; (b) floor-safety on the **abstain world** (on vs off); (c) g1/g2 on the **real cache** (g1 should still capture its overfit edge; g2 = cache + shuffle, floor on/off comparable). Writes `reports/validation/active_survival_hand1.md` + JSON.
- [ ] **Step 4: Run** the integration test → PASS; run the full harness once; read the report.
- [ ] **Step 5: Commit** `feat(validation): survive/die + abstain-world floor-safety + g1/g2 real-cache`

---

## Final verification

- [ ] `python -m pytest -q` → green.
- [ ] `python -m agent.backtest.calibrate_breath_economy` writes recommended params; record in the report.
- [ ] `python scripts/run_active_survival_validation.py` → spec §2 (1–4) hold on non-toy sizes; floor-safety holds.
- [ ] One SEPARATE `--divine-tithe` arm to actually exercise the tithe self-check (`reincarnation.py:2002-2034`); do NOT fold tithe into the calibration worlds.
- [ ] git identity `balflee`.

## Notes for the executor
- Provider numerical only. All randomness seeded; exploration RNG is per-incarnation. Scope wall: no `sim/`, no A14/A2, no `--design passes`.
- **Exploration is a FLAT min-stake probe** (no Kelly); rng gated on the learning path (holdout/baseline never explore).
- **Read the serialized journey dict, never SeasonResult/LifeOutcome dataclass fields** in calibration/validation metrics.

---

## Revision log

- **R1 (panel HIGH=13):** descoped A14+A2 → Hand 1.5; calibration → sim-based; real symbols; exploration_epsilon non-advisable; synthetic via real schema; literal replacement + fail-closed.
- **R2 (panel HIGH=12):** calibration entry → `run_survival_over_rows`; generator returns `(rows,snaps)` + `winning_price=1.0`; edge `C` clears `min_edge`; exploration gated by `rng!=None`; seed via `dataclasses.replace`/`load_v3_seed`; death-aware metric; groundhog-only; §2.4 zero-edge synthetic.
- **R3 (panel HIGH=4):** (1) **metric reads the SERIALIZED journey dict** (`journey["lives"]` list w/ `final_breath`/`death`/`bets`; NO `consumed_market_ids`; `summary["lives"]` is an int). (2) **Exploration rng gated on the LEARNING path** (threaded as a `None`-default kwarg through `run_survival_over_rows`/`run_survival_season`/`_build_life_loop`; the frozen holdout `learning_enabled=False` + `:2057` baseline keep `rng=None`) — the holdout is no longer contaminated. (3) **Exploration bet = FLAT `min_bet_size_usd` probe, no Kelly** (Kelly is unbound at `:363/:380` and collapses to ~0 on no-edge → ε never lifts; flat-stake-via-`_clamped_size` is well-defined everywhere). (4) **Per-incarnation rng seed** `random.Random(incarnation_k)` (groundhog runs `max_lives=1`/incarnation ⇒ `idx` always 0). (5) **Added `build_abstain_world`** (sub-gate rows) so the floor is actually exercised in integration; reframed success as "survives +edge / dies 0-edge" (constant signal is unlearnable by design). (6) **Non-saturating fail-closed sentinel** (`mean_final_breath_live`, not `death_rate` which pins at 1.0). (7) `--synthetic-edge` BYPASSES the `:118-124` cache load. (8) resolver = `TennisMatchResolver(name_index={})`; `PricePoint(ts=, mid_price=)` kw-only; per-row monotonic timestamps; dropped inert `max_steps`; Task-4 tests construct the engine with `min_edge=0.05`.
