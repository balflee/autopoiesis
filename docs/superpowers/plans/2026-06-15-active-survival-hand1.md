# Active Survival (Hand 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Anchor edits by SYMBOL (function name + a grep sentinel), not raw line numbers — line numbers drift.**

**Goal:** Make honest-win-rate survival achievable by recalibrating the breath economy with a SIM-BASED calibration (running the REAL prod sim), and stop the agent freezing via a non-advisable exploration floor — validated on a synthetic known-edge harness.

**Architecture:** A deterministic synthetic known-edge generator returns BOTH `SurvivalRow`s and matching `MarketSnapshot`s whose fusion the agent actually sees, at a controllable edge. Calibration runs short *real* numerical seasons through `run_survival_over_rows` (the prod-faithful path that owns `loss_multiplier` + value mode) across a loss-multiplier grid on `edge=0.10` and `edge=0` worlds, and picks the multiplier that lets the edge world survive while the no-edge world dies. `exploration_epsilon` is a non-advisable `StrategyConfig` field; `DecisionEngine` adds ONE post-fusion ε-greedy branch (gated by `epsilon>0 AND rng is not None`, so the frozen baseline never explores). The run harness replaces the hard-coded breath literals with calibrated values on the groundhog path; a validation harness proves the criteria + g1/g2 regression.

**Tech Stack:** Python 3.14, pytest, the `agent/backtest/` groundhog runtime. numerical provider only (no LLM, no API keys).

**Spec:** `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md`. **Branch:** `active-survival-hand1`. One commit/task. Confirm git identity `balflee` before each commit. **Descoped → Hand 1.5:** A14 (breath-aware sizing) and A2 (death credit).

---

## Grounded contracts (verified — do not re-derive)

- `SignalRow` (`agent/backtest/cached_sweep.py:131-154`): `SignalRow(market_id, slug, scores:dict, confidences:dict, entry_price, outcome:str, winning_price, liquidity_cap_usd, cross_market_signal=0.0, cluster_key="")`. Score keys = `tennis_technical, market_momentum, smart_money, sentiment_llm, crowd_volume`. `outcome ∈ {"yes","no","void"}`.
- **Settlement (`cached_sweep.py:95-112`):** win/loss decided by `(side=="YES")==(outcome=="yes")`; payout uses `winning_price/eff`. **`winning_price` = the WINNING side's price ≈ 1.0 for ANY cleanly resolved market** (`historical_fetcher.py:140`, `<0.99 → void`). So synthetic rows MUST set `winning_price=1.0` for every resolved row (side carried by `outcome`), NOT `1.0/0.0`.
- `SurvivalRow` (`survival_season.py:197-265`, frozen): own fields `market_id, slug, signal:SignalRow, entry_asof_ts_iso, resolution_ts_iso, end_date_iso, outcome, winning_price, liquidity_cap, players, surface`; `scores/confidences/entry_price/cross_market_signal/cluster_key` delegate read-only to `signal`.
- `build_survival_rows(rows, snapshots, resolver, *, entry_fraction=0.5, entry_price_floor=0.05)` raises `ValueError` if `abs(recomputed_mid − row.entry_price) > 1e-9`. A **flat 2-point `price_ledger` at `p`** makes recomputed_mid == p exactly.
- Row→agent: `SurvivalTickSource.inputs_for` → `row_to_signals(row.signal)` → `Signal(score, confidence)`. Value mode `edge_abs = |kappa·fused|` must clear `min_edge` or the agent abstains. v3 seed: `kappa≈0.492, min_edge≈0.0349, min_confidence≈0.0756`. With all scores `=c`, conf `=0.8`: `fused≈0.8c`, `edge_abs≈0.492·0.8c=0.394c` → **need `c ≳ 0.10`** to clear `min_edge` with margin.
- `StrategyConfig` (`find_optimal_config.py:57-82`, frozen): `weights, max_breath_risk_pct, min_confidence, min_bet_size_usd, min_edge=0.0, kappa=0.25, gate_storm_sensitivity=0.0, risk_storm_sensitivity=0.0, kappa_xm=0.0`.
- Seed loader = `scripts/run_v3_numerical.py:30-40 load_v3_seed()` (returns StrategyConfig; called by `run_reincarnation.py:117`). `validate_value_seed.py:97-108 _seed_payload` is the SERIALIZER. `survival_season.py:2667 DEFAULT_OPTIMUM_SEED` is a literal, not a loader.
- **Calibration entry = `run_survival_over_rows(survival_rows, snapshots, *, base_seed, loss_multiplier, initial_breath, max_lives, max_steps, with_ai=False, value_betting=True, side_correct_pricing=True, effective_entry_price_floor=MIRROR_ROW_FLOOR)`** (`survival_season.py:2710-2733`). It fragilizes `base_seed` via `fragile_seed_from_config` (`dataclasses.replace` — so a NEW seed field carries through) and builds `SurvivalRecorder(rows, loss_multiplier=m)` (the ONLY place `loss_multiplier` touches breath, `survival_season.py:787-791`). **`run_survival_season` itself has NO `loss_multiplier` kwarg — do NOT use it directly.** Returns a journey dict whose `summary` carries `deaths`/`lives`.
- `SeasonResult` (`survival_season.py:1236-1245`): `lives:tuple[LifeOutcome], deaths:int`. `LifeOutcome` (`:1218-1233`): `consumed_market_ids, bets_placed, no_bets_emitted, settlements_processed, final_breath, final_bankroll_usd, died`. **No "lifetime" field — derive it.**
- `_decision_engine_from_seed(seed, *, effective_entry_price_floor=None)` (`survival_season.py:1515`) is called at **`:1739` inside `_build_life_loop` (the LEARNER)** and **`:2057` (the FROZEN static baseline twin)**. Thread exploration into the `:1739` call ONLY; the `:2057` baseline keeps `exploration_rng=None` (never explores).
- Run harness: `run_reincarnation.py:156-157` literals (`loss_multiplier=5.0, initial_breath=35.0`) feed `run_groundhog_export` (`--design groundhog`). `:226-227` feed a DIFFERENT function `run_reincarnation_export` (`--design passes`). **This plan touches the groundhog path ONLY.** The loop is at `agent/runtime/sandbox_phase2_loop.py`.

---

## File Structure

| File | C/M | Responsibility |
|---|---|---|
| `agent/backtest/synthetic_edge.py` | Create | `build_synthetic_world(n,edge,seed)->(list[SurvivalRow], list[MarketSnapshot])`; `agent_ev(rows)`. |
| `tests/agent/backtest/test_synthetic_edge.py` | Create | EV ≈ edge (price-invariant); rows pass build_survival_rows; deterministic; winning_price==1.0. |
| `agent/backtest/calibrate_breath_economy.py` | Create | Sim-based sweep via `run_survival_over_rows`; death-aware dual criterion; write JSON. |
| `tests/agent/backtest/test_calibrate_breath_economy.py` | Create | Different m ⇒ different deaths (fail-closed); recommended params separate live from dead. |
| `agent/backtest/find_optimal_config.py` | Modify | Add `exploration_epsilon: float = 0.0` to StrategyConfig (NOT in GENOME_KEYS). |
| `scripts/run_v3_numerical.py` | Modify | `load_v3_seed` reads `raw.get("exploration_epsilon", 0.0)`. |
| `agent/backtest/validate_value_seed.py` | Modify | `_seed_payload` serializes `exploration_epsilon` (round-trip). |
| `agent/engines/decision.py` | Modify | `exploration_epsilon`+`exploration_rng` ctor params; shared `_clamped_size`; ONE post-fusion ε-greedy branch. |
| `tests/agent/engines/test_decision_exploration.py` | Create | ε=0 or rng=None never touches rng; ε>0 lifts abstain→bet ≈ε; explored size obeys caps; missing-signal/neutral never explore. |
| `agent/backtest/survival_season.py` | Modify | `_decision_engine_from_seed(..., exploration_rng=None)`; learner call `:1739` passes `random.Random(idx)`; baseline `:2057` stays None. |
| `agent/backtest/reincarnation.py` | Modify | `run_groundhog_export(..., exploration_epsilon=0.0)` → `dataclasses.replace(base_seed, exploration_epsilon=...)`. |
| `scripts/run_reincarnation.py` | Modify | groundhog-only flags `--loss-multiplier/--initial-breath/--exploration-epsilon/--synthetic-edge`; replace `:156-157` literals; fail-closed; error on passes+active-flags. |
| `scripts/run_active_survival_validation.py` | Create | Synthetic sweep + edge=0 floor-safety + g1/g2 real-cache regression → report. |
| `tests/agent/runtime/test_active_survival_integration.py` | Create | Short end-to-end; metrics death-aware; schema unbroken. |

---

## Task 1: Synthetic known-edge world generator

**Files:** Create `agent/backtest/synthetic_edge.py`, `tests/agent/backtest/test_synthetic_edge.py`.

- [ ] **Step 1: Read first** — `cached_sweep.py:95-154` (SignalRow + `compute_bet_pnl`), `survival_season.py:197-376` (SurvivalRow + build_survival_rows + `_entry_asof`), and `MarketSnapshot` def (grep `class MarketSnapshot` in `agent/backtest/historical_fetcher.py`; note required fields incl. `price_ledger:list[PricePoint]`, `winning_price:Field(ge=0,le=1)`, `outcome`, `resolution_ts_iso`, `end_date_iso`, `liquidity_cap_usd`). Confirm `PricePoint(ts, mid_price)`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/agent/backtest/test_synthetic_edge.py
from agent.backtest.synthetic_edge import build_synthetic_world, agent_ev

def test_zero_edge_ev_is_zero():
    rows, _ = build_synthetic_world(n=4000, edge=0.0, seed=7)
    assert abs(agent_ev(rows)) < 0.02            # price-invariant EV metric

def test_positive_edge_ev_matches():
    rows, _ = build_synthetic_world(n=4000, edge=0.10, seed=7)
    assert abs(agent_ev(rows) - 0.10) < 0.03

def test_rows_pass_build_survival_rows_and_winning_price_is_one():
    rows, snaps = build_synthetic_world(n=50, edge=0.08, seed=3)
    assert len(rows) == 50                        # build_survival_rows did not raise
    assert all(r.winning_price == 1.0 for r in rows)   # resolved rows ⇒ 1.0

def test_deterministic():
    a, _ = build_synthetic_world(n=200, edge=0.08, seed=3)
    b, _ = build_synthetic_world(n=200, edge=0.08, seed=3)
    assert [r.market_id for r in a] == [r.market_id for r in b]
    assert [r.outcome for r in a] == [r.outcome for r in b]
```

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement** `build_synthetic_world(n, edge, seed)`:
  - `rng = random.Random(seed)`. For each i: `price = 0.5` (FIXED to isolate edge from price level); `true_prob = min(1.0, max(0.0, 0.5 + edge))`; `won = rng.random() < true_prob`; `outcome = "yes" if won else "no"`.
  - Encode the edge as a positive directional signal: all 5 engine `scores = C` where `C` is derived to clear the v3 gate — `C = max(0.30, (min_edge_ref + 0.02) / (kappa_ref * 0.8))` with `kappa_ref=0.492, min_edge_ref=0.0349` ⇒ `C≈0.30`; `confidences = 0.8`. (Document: `C` is a fixed directional tilt sized to clear `min_edge`; the WIN RATE is set by `true_prob`, independent of `C`.)
  - Build `SignalRow(... entry_price=price, winning_price=1.0, liquidity_cap_usd=1000.0, ...)`. Build matching `MarketSnapshot` with flat `price_ledger=[PricePoint(t0,price), PricePoint(t1,price)]`, `winning_price=1.0`, `outcome`, consistent `resolution_ts_iso`/`end_date_iso`.
  - Return `build_survival_rows(signal_rows, snapshots, resolver=_null_resolver())`, AND the `snapshots` list: `return rows, snapshots`. (If a real resolver is awkward, inject a minimal resolver that returns `(None, None)` players/surface — confirm the resolver Protocol in Step 1.)
  - `agent_ev(rows)`: `mean((1.0 if r.outcome=="yes" else 0.0) - r.entry_price for r in rows)` — the price-invariant per-unit EV of the agent's YES tilt; ≈ `edge`.
  - All randomness via `random.Random(seed)`.

- [ ] **Step 5: Run, verify pass. Commit** `feat(survival): synthetic known-edge world (rows+snapshots, winning_price=1.0)`

---

## Task 2: Sim-based breath-economy calibration

**Files:** Create `agent/backtest/calibrate_breath_economy.py`, `tests/agent/backtest/test_calibrate_breath_economy.py`.

**Metric (death-aware, grounded):** from the `run_survival_over_rows` result `summary`, read `deaths` and the per-life list; define `death_rate = deaths / max_lives` and `mean_markets = mean(len(life["consumed_market_ids"]) for life in lives)`. The honest world should have LOWER `death_rate` than the no-edge world (recalibrated economy lets a 55% agent survive while a 50% agent dies). **A capped-`max_lives` season can "die" (breath≤0) OR exhaust the schedule — so use `death_rate`/`final_breath`, NOT raw markets-consumed (an abstainer consumes the whole schedule without dying).**

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_calibrate_breath_economy.py
from agent.backtest.calibrate_breath_economy import calibrate, CalibrationResult

def test_multiplier_actually_varies_physics():
    res = calibrate(loss_multiplier_grid=[1.0, 5.0], initial_breath=35.0,
                    edge_live=0.10, edge_dead=0.0, n_rows=400, max_lives=6,
                    max_steps=400, seed=0)
    # fail-closed: a different m MUST move measured deaths (else multiplier inert)
    assert res.grid[0]["death_rate_dead"] != res.grid[1]["death_rate_dead"]

def test_recommended_separates_live_from_dead():
    res = calibrate(loss_multiplier_grid=[1.0, 1.2, 1.5, 2.0, 3.0, 5.0],
                    initial_breath=35.0, edge_live=0.10, edge_dead=0.0,
                    n_rows=400, max_lives=6, max_steps=400, seed=0)
    assert isinstance(res, CalibrationResult)
    assert res.death_rate_live < res.death_rate_dead      # honest survives, noise dies
    assert res.loss_multiplier in [1.0,1.2,1.5,2.0,3.0,5.0]
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `calibrate(...)`:
  - Build a non-exploring base seed: `base = dataclasses.replace(load_v3_seed(), exploration_epsilon=0.0)` (calibrate the CLEAN economy; exploration is added later).
  - For each `m`: build live world `build_synthetic_world(n_rows, edge_live, seed)` and dead world `(edge_dead)`. For EACH, call `run_survival_over_rows(rows, snapshots, base_seed=base, loss_multiplier=m, initial_breath=initial_breath, max_lives=max_lives, max_steps=max_steps, with_ai=False)`. Read `summary["deaths"]` + per-life entries; compute `death_rate` + `mean_final_breath`. **Build a FRESH world+call per (m, world) cell — no shared recorder/state.**
  - Dual criterion: pick the `m` maximizing `(death_rate_dead − death_rate_live)` subject to `death_rate_live` low (e.g. `< 0.5`) and `death_rate_dead` high (e.g. `> 0.5`). If none separate, raise `ValueError`.
  - Return `CalibrationResult(loss_multiplier, death_rate_live, death_rate_dead, initial_breath, exploration_epsilon=RECOMMENDED_EPS, grid=[…])`. `main()` writes `reports/calibration/breath_economy_hand1.json`. Keep grids small (≤8 m, ≤6 lives, ≤400 rows/steps). `RECOMMENDED_EPS` default = `0.05` (a small floor; document it is a design choice, not derived).
- [ ] **Step 4: Run, verify pass.** Run `python -m agent.backtest.calibrate_breath_economy`; confirm JSON written with `loss_multiplier < 5.0`.
- [ ] **Step 5: Commit** `feat(survival): sim-based breath-economy calibration via run_survival_over_rows`

---

## Task 3: `exploration_epsilon` on StrategyConfig + loader round-trip

**Files:** Modify `find_optimal_config.py` (StrategyConfig), `scripts/run_v3_numerical.py` (`load_v3_seed`), `agent/backtest/validate_value_seed.py` (`_seed_payload`). Test `tests/agent/backtest/test_strategyconfig_epsilon.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_strategyconfig_epsilon.py
import dataclasses, json
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.reincarnation import GENOME_KEYS
from scripts.run_v3_numerical import load_v3_seed

def test_default_zero_and_not_advisable(minimal_weights):
    c = StrategyConfig(weights=minimal_weights, max_breath_risk_pct=0.3,
                       min_confidence=0.05, min_bet_size_usd=5.0)
    assert c.exploration_epsilon == 0.0
    assert "exploration_epsilon" not in GENOME_KEYS   # anti-freeze guarantee

def test_load_v3_seed_round_trips_epsilon(tmp_path):
    seed = dataclasses.replace(load_v3_seed(), exploration_epsilon=0.07)
    # serialize via _seed_payload then reload preserves the field
    from agent.backtest.validate_value_seed import _seed_payload
    p = tmp_path / "s.json"; p.write_text(json.dumps(_seed_payload(seed)))
    assert load_v3_seed(p).exploration_epsilon == 0.07
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** Add `exploration_epsilon: float = 0.0` as the LAST field of `StrategyConfig` (frozen, default ⇒ all existing constructions valid). In `load_v3_seed`: add `exploration_epsilon=raw.get("exploration_epsilon", 0.0)`. In `_seed_payload`: add `"exploration_epsilon": cfg.exploration_epsilon`. **Do NOT add to `GENOME_KEYS`.** Verify `fragile_seed_from_config` (`survival_season.py:2265-2295`, uses `dataclasses.replace`) preserves it.
- [ ] **Step 4: Run, verify pass.** Run the seed suite. **Commit** `feat(config): non-advisable exploration_epsilon on StrategyConfig + loader round-trip`

---

## Task 4: Exploration floor in the decision engine

**Files:** Modify `agent/engines/decision.py` (ctor `:182-237`; the `decide` body), `agent/backtest/survival_season.py` (`_decision_engine_from_seed` + learner call `:1739`). Test `tests/agent/engines/test_decision_exploration.py`.

**Design (resolves the round-2 control-flow findings):**
- Gate: explore ONLY if `self._exploration_epsilon > 0 AND self._exploration_rng is not None`. (`rng=None` ⇒ never explore ⇒ the `:2057` frozen baseline stays a true control even if the seed's epsilon>0.)
- Extract the 4-constraint sizing (`decision.py:392-405`) into a module helper `_clamped_size(*, rho_eff, kelly, mean_confidence, bankroll_usd, breath, max_breath_risk_pct, conversion_rate, bet_size_cap, liquidity_cap_usd) -> float` used by BOTH the normal BET path and the exploration branch (no double-maintenance).
- Explorable abstains = the POST-fusion, side-resolvable ones: low-confidence (`:319`), price-floor (`:363`), no-edge (`:380`), zero-kelly (`:386`), below-min-size (`:411`). **Never explore** the missing-signal guard (`:303`, pre-fusion, no side) or the exactly-neutral returns (`:329`/`:352`, `fused==0`/`edge_yes==0`, no resolvable side).
- Mechanism at each explorable abstain: if the explore gate is open and `self._exploration_rng.random() < epsilon` and a side is resolvable (sign of `fused`/`edge_yes`), return a `min_bet_size_usd` bet sized via `_clamped_size` for that side; if the clamp drops below `min_bet_size_usd`, keep the original NO_BET. RNG is touched ONLY inside this branch (so ε=0 or rng=None is byte-identical).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/engines/test_decision_exploration.py
import random, asyncio
from agent.engines.decision import DecisionEngine

class _Raising(random.Random):
    def random(self): raise AssertionError("rng touched")

def test_epsilon_zero_never_touches_rng(no_edge_kwargs):
    eng = DecisionEngine(exploration_epsilon=0.0, exploration_rng=_Raising())
    assert asyncio.run(eng.decide(**no_edge_kwargs)).kind.name == "NO_BET"

def test_rng_none_never_explores(no_edge_kwargs):
    eng = DecisionEngine(exploration_epsilon=1.0, exploration_rng=None)
    assert asyncio.run(eng.decide(**no_edge_kwargs)).kind.name == "NO_BET"

def test_epsilon_lifts_no_edge_abstain_to_bet_rate(no_edge_kwargs):
    eng = DecisionEngine(exploration_epsilon=0.2, exploration_rng=random.Random(0))
    bets = sum(asyncio.run(eng.decide(**no_edge_kwargs)).kind.name == "BET"
               for _ in range(2000))
    assert 0.15 < bets/2000 < 0.25

def test_missing_signal_never_explores(missing_signal_kwargs):
    eng = DecisionEngine(exploration_epsilon=1.0, exploration_rng=random.Random(0))
    assert asyncio.run(eng.decide(**missing_signal_kwargs)).kind.name == "NO_BET"
```

(`no_edge_kwargs` = value-mode signals fusing to a non-zero score whose `edge_abs < min_edge` so the no-edge gate fires with a resolvable side. `missing_signal_kwargs` = signals dict missing an engine.)

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement (decision.py).** ctor: add `exploration_epsilon: float = 0.0`, `exploration_rng: random.Random | None = None`; validate `0<=epsilon<=1`; store. `import random`. Extract `_clamped_size(...)`. At each explorable abstain return, call a private `self._explore_or(reason_nobet, *, side, sizing_inputs)` that returns the explored BET (via `_clamped_size`) iff the gate+roll open and clamp≥min, else `reason_nobet`. Leave `:303`/`:329`/`:352` as plain NO_BET.
- [ ] **Step 4: Thread RNG (survival_season.py).** Add `exploration_rng: random.Random | None = None` to `_decision_engine_from_seed`; pass `exploration_epsilon=seed.exploration_epsilon, exploration_rng=exploration_rng` into `DecisionEngine(...)`. At the LEARNER call inside `_build_life_loop` (`:1739`), pass `exploration_rng=random.Random(idx)`. **Leave the `:2057` static-baseline call unchanged (default `None`).**
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/test_decision_exploration.py tests/agent/engines/test_decision.py -v` → all PASS (full existing decision suite under ε=0/rng=None is the regression oracle).
- [ ] **Step 6: Commit** `feat(decision): post-fusion exploration floor (gated by rng, baseline-safe)`

---

## Task 5: Plumb calibrated params + exploration through the groundhog harness

**Files:** Modify `agent/backtest/reincarnation.py` (`run_groundhog_export`), `scripts/run_reincarnation.py`. **Groundhog path ONLY.**

- [ ] **Step 1: Write a failing test** (in Task 6's file): call `run_groundhog_export(rows, snaps, base_seed=<v3>, loss_multiplier=1.2, initial_breath=35.0, exploration_epsilon=0.1, max_incarnations=3, ...)` on a tiny synthetic world; assert artifact `knobs` show `loss_multiplier=1.2` and the run completes numerical; assert bet-rate > 0 on a would-abstain world (proves epsilon reached the engine).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: reincarnation.py.** Add ONLY `exploration_epsilon: float = 0.0` to `run_groundhog_export`. Inside, set `base_seed = dataclasses.replace(base_seed, exploration_epsilon=exploration_epsilon)` BEFORE it derives the per-life + holdout seeds (so BOTH the in-sample journey and the frozen holdout — which derive from this one seed via `fragile_seed_from_config`'s `dataclasses.replace` — inherit it). Do NOT re-add `loss_multiplier`/`initial_breath`/`tithe_*` (already params).
- [ ] **Step 4: run_reincarnation.py.** Add CLI flags `--loss-multiplier`, `--initial-breath`, `--exploration-epsilon`, `--synthetic-edge` (float, optional). In the `design=="groundhog"` branch, **replace the `:156-157` literals** with flag-resolved values and pass `exploration_epsilon=`. Resolve `--loss-multiplier`/`--initial-breath`/`--exploration-epsilon` defaults from `reports/calibration/breath_economy_hand1.json` if present; if recalibration is requested (any active flag) but the JSON is absent and the flag unset, **fail closed** with a clear error. When `--synthetic-edge E` is set, source rows+snaps from Task 1. **If `--design passes` is combined with any active flag, error out** ("Hand-1 active params support --design groundhog only"); leave `run_reincarnation_export` (`:226-227`) untouched.
- [ ] **Step 5: Run** the focused test → PASS. Smoke (Windows-safe temp): `python scripts/run_reincarnation.py --provider numerical --design groundhog --loss-multiplier 1.2 --exploration-epsilon 0.1 --synthetic-edge 0.1 --max-incarnations 3 --out reports/_smoke/smoke.json` (ensure `reports/_smoke/` is gitignored).
- [ ] **Step 6: Commit** `feat(reincarnation): groundhog-path calibrated economy + exploration + synthetic-edge`

---

## Task 6: Validation harness — synthetic sweep + floor-safety + g1/g2 regression

**Files:** Create `scripts/run_active_survival_validation.py`, `tests/agent/runtime/test_active_survival_integration.py`.

**Metric helper:** `from a season-result summary, season_metrics(summary) -> {total_bets, deaths, death_rate, mean_final_breath}` (death-aware; see Task 2).

- [ ] **Step 1: Write the integration test** (short, numerical):

```python
# tests/agent/runtime/test_active_survival_integration.py (sketch)
from scripts.run_active_survival_validation import run_validation

def test_exploits_edge_and_dies_without_one(tmp_path):
    hi = run_validation(edge=0.10, exploration_epsilon=0.1, loss_multiplier=1.2,
                        n=600, max_lives=6, seed=0, out=tmp_path/"hi.json")
    lo = run_validation(edge=0.00, exploration_epsilon=0.1, loss_multiplier=1.2,
                        n=600, max_lives=6, seed=0, out=tmp_path/"lo.json")
    assert hi.total_bets > 0                       # §2.2 no freeze
    assert hi.death_rate < lo.death_rate           # §2.3 exploits edge (survives)
    assert lo.deaths >= 1                           # §2.1 stakes preserved

def test_floor_does_not_accelerate_no_edge_death(tmp_path):
    # §2.4 over a TRUE zero-edge SYNTHETIC world (NOT g2 — shuffle preserves edge)
    on  = run_validation(edge=0.0, exploration_epsilon=0.1, loss_multiplier=1.2,
                         n=600, max_lives=6, seed=1, out=tmp_path/"on.json")
    off = run_validation(edge=0.0, exploration_epsilon=0.0, loss_multiplier=1.2,
                         n=600, max_lives=6, seed=1, out=tmp_path/"off.json")
    assert on.death_rate <= off.death_rate + 0.15   # floor doesn't materially worsen
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `run_active_survival_validation.py`: `run_validation(edge, exploration_epsilon, loss_multiplier, n, max_lives, seed, out)` builds the synthetic world and calls `run_survival_over_rows(...)`, returning `season_metrics(...)`. `main()`: (a) synthetic sweep `edge ∈ {0, 0.05, 0.10}` (floor on); (b) the §2.4 floor-safety pair (edge=0, floor on vs off); (c) **g1/g2 on the REAL cache** — g1 = cache (confirm it still captures its overfit edge), g2 = cache + `--shuffle-timestamps-seed 1` (zero-edge temporal placebo; confirm floor on/off comparable). Writes `reports/validation/active_survival_hand1.md` + JSON.
- [ ] **Step 4: Run** `python -m pytest tests/agent/runtime/test_active_survival_integration.py -v` → PASS; run the full harness once; read the report.
- [ ] **Step 5: Commit** `feat(validation): synthetic sweep + floor-safety + g1/g2 real-cache regression`

---

## Final verification

- [ ] `python -m pytest -q` → green.
- [ ] `python -m agent.backtest.calibrate_breath_economy` writes recommended params; record in the validation report.
- [ ] `python scripts/run_active_survival_validation.py` → spec §2 (1–4) hold on non-toy sizes; floor-safety holds.
- [ ] `reincarnation.py` tithe self-check (`:2002-2034`) is a no-op unless `--divine-tithe`; run ONE tithe-on arm separately to actually exercise it (do NOT fold tithe into the calibration worlds — it changes the economy).
- [ ] git identity `balflee` for every commit.

## Notes for the executor
- **Provider:** numerical only. **Determinism:** all randomness via seeded `random.Random`; exploration RNG is per-life `random.Random(idx)`.
- **Scope wall:** do NOT touch `sim/`; do NOT implement A14/A2 (Hand 1.5); do NOT touch the `--design passes` / `run_reincarnation_export` path.
- **Anchor by symbol**, confirm line numbers in each task's Read-first step.

---

## Revision log

- **R1 (2026-06-15, after panel `HIGH=13`):** descoped A14+A2 → Hand 1.5; calibration → sim-based; fixed `_make_decision_engine`→`_decision_engine_from_seed`; `exploration_epsilon` on StrategyConfig (non-advisable); synthetic via real schema; exploration centralized; literal replacement + fail-closed.
- **R2 (2026-06-15, after panel `HIGH=12`):** (1) **Calibration entry → `run_survival_over_rows`** (`run_survival_season` has no `loss_multiplier`; the multiplier acts only via `SurvivalRecorder`; `run_survival_over_rows` owns it + `value_betting=True` + fragile seed). (2) **Generator returns `(rows, snapshots)`** (season runners + shuffle + settlement need snapshots) and sets **`winning_price=1.0`** for every resolved row (it is the winning side's price ≈1.0, not a YES flag; `0.0` would book correct NO wins as losses + void). (3) **Edge constant `C` derived to clear the v3 `min_edge` gate** (else universal abstain); EV metric `agent_ev≈edge` replaces the price-confounded win-rate. (4) **Exploration gated by `epsilon>0 AND rng is not None`**, single post-fusion branch with a shared `_clamped_size`; missing-signal + exactly-neutral never explore; learner call `:1739` gets `random.Random(idx)`, frozen baseline `:2057` stays `rng=None`. (5) **Seed plumbing via `dataclasses.replace`** + the REAL loader `load_v3_seed` (run_v3_numerical.py:30-40), not the serializer/literal cited before. (6) **Lifetime metric is death-aware** (`death_rate`/`final_breath` from `SeasonResult`, not markets-consumed — abstainers exhaust the schedule without dying). (7) **Run harness scoped to `--design groundhog`** only (`:226-227` is the separate `run_reincarnation_export`); error on `passes`+active-flags. (8) **§2.4 floor-safety uses a TRUE zero-edge synthetic world**, not g2 (shuffle preserves per-market edge); g1/g2 stay as the real-cache regression. (9) fixed loop path (`agent/runtime/`), engine call-site (`:1739`), Windows-safe smoke path.
