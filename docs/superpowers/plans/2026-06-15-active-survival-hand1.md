# Active Survival (Hand 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]`. **Anchor edits by SYMBOL; confirm line numbers in each task's Read-first step.** Tasks are ordered by dependency — execute in order.

**Goal:** Make honest-win-rate survival achievable by recalibrating the breath economy with a SIM-BASED calibration (running the REAL prod sim), and stop the agent freezing via a non-advisable exploration floor — validated on synthetic worlds.

**Architecture:** A deterministic generator returns `SurvivalRow`s + matching `MarketSnapshot`s with **mid-schedule settlement timestamps** (so losing bets drain breath repeatedly). Calibration runs short real numerical seasons through `run_survival_over_rows` across a loss-multiplier grid on an above-gate `+edge` world (agent bets, wins, survives) vs a `0-edge` world (agent bets, coin-flips, loss×m drains → dies), reading the SERIALIZED journey dict. `exploration_epsilon` is a non-advisable `StrategyConfig` field; `DecisionEngine` adds ONE post-fusion FLAT-`min_bet_size_usd` probe gated by `epsilon>0 AND rng is not None`, threaded only on the LEARNING path. Validation proves recalibration (survive/die) and the floor's VALUE (harvests a BELOW-gate hidden edge a frozen agent would abstain on).

**Breath economy facts (verified):** breath changes ONLY on settled bets (`loss×loss_multiplier`, `survival_season.py:787-791`), tithe (off by default), and lung-expansion (bankroll>1.1×initial). **A pure abstainer never dies** (no per-tick decay in this runtime). So: above-gate worlds die via losing bets; below-gate worlds are where a frozen agent abstains and stays FLAT while the explorer harvests hidden edge — measured by `mean_final_breath`, not death-rate.

**Spec:** `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md`. **Branch:** `active-survival-hand1`. One commit/task; git identity `balflee`. **Descoped → Hand 1.5:** A14, A2.

---

## Grounded contracts (verified)

- `SignalRow` (`cached_sweep.py:131-154`); settlement (`cached_sweep.py:95-112`): win iff `(side=="YES")==(outcome=="yes")`; **`winning_price=1.0`** for any resolved row.
- `SurvivalRow` (`survival_season.py:197-265`, frozen). `build_survival_rows(rows, snapshots, resolver, *, entry_fraction=0.5, entry_price_floor=0.05)` raises if `abs(recomputed_mid−entry_price)>1e-9`; flat ledger at `p` ⇒ exact. Calls `parse_slug` + `resolver.resolve`. **Synthetic slugs `"alpha{i}-vs-beta{i}"` deliberately DO NOT parse (digit suffix; `_VS_SUFFIX` is letters-only, `tennis_match_resolver.py:34`) → `players/surface=None`, which the season tolerates everywhere — benign.** Resolver = `TennisMatchResolver(name_index={})`.
- **Settlement clock (`survival_season.py:397-418`, final drain `:486-488`):** a bet settles when `max(end_date_iso+settle_lag, resolution_ts_iso) <= now` as the clock walks the ENTRY-ordered schedule. **If every market resolves after the last entry, settlement happens only at the final drain → the agent dies at most ONCE → `death_rate` caps near `1/max_lives`.** Synthetic rows MUST set `resolution_ts_i`/`end_date_iso_i` a small lag AFTER row `i`'s entry but BEFORE the entry of a row a few positions later, so bets settle MID-schedule and the dead world can die repeatedly.
- `PricePoint(ts=ISO, mid_price=0..1)` kw-only pydantic (`historical_fetcher.py:107-121`); `MarketSnapshot` kw-only — confirm exact fields in Task 1.
- Row→agent: `row_to_signals(row.signal)`; value-mode `edge_abs=|kappa·fused|` must clear `min_edge`. v3: `kappa≈0.492, min_edge≈0.0349`. All scores `=C`, conf `=0.8` ⇒ `edge_abs≈0.394C`. **`C=0.30` ⇒ above gate (bets); `C=0.05` ⇒ below gate (abstains).**
- `StrategyConfig` (`find_optimal_config.py:57-82`). Loader `run_v3_numerical.py:30-40 load_v3_seed`; serializer `validate_value_seed.py _seed_payload`. Journey `seed` block `survival_season.py:2596-2606`; groundhog `knobs` block `reincarnation.py:1828-1835` — both must ALSO disclose `exploration_epsilon`.
- Calibration entry `run_survival_over_rows(survival_rows, snapshots, *, base_seed, loss_multiplier, initial_breath, max_lives, with_ai=False, preflight=False, exploration_rng=None, value_betting=True, side_correct_pricing=True)` (`survival_season.py:2710-2733`) — owns `loss_multiplier` via `SurvivalRecorder`; **returns the SERIALIZED journey dict** (`:2596-2648`): `journey["summary"]["deaths"]` (int), per-life LIST `journey["lives"]` (`:2456-2480`, keys `idx/start_ts/bets/settlements/final_breath/final_bankroll_usd/pnl/death`, NO `consumed_market_ids`). `max_steps` is down-sample only (NOT season length).
- `journey_metric(journey, max_lives)`: assert `isinstance(journey["lives"], list)`; `death_rate=journey["summary"]["deaths"]/max_lives`, `mean_final_breath=mean(l["final_breath"] for l in journey["lives"])`, `total_bets=sum(l["bets"] for l in journey["lives"])`.
- `groundhog_metric(artifact)`: bets are per-incarnation — `total_bets=sum(inc["bets"] for inc in artifact["incarnations"])` (`reincarnation.py:1565`). **Two distinct accessors — do not cross them.**
- `_decision_engine_from_seed(seed, *, effective_entry_price_floor=None, exploration_rng=None)` (`survival_season.py:1515`) called at **`:1739` (LEARNER, in `_build_life_loop`)** and inside **`_static_baseline_curve_async` (the FROZEN baseline, ~`:2057`)**. Thread rng only via the `:1739` call; baseline + holdout (`_run_frozen_holdout` → `run_survival_season(learning_enabled=False)`) keep `rng=None`.
- `Action` BET (`agent/core/state.py:114-124`) requires `market_id + side + size_usd`, and a BET must NOT carry `no_bet_reason`.
- Run harness: `run_reincarnation.py:118-124` loads the cache UNCONDITIONALLY; `:156-157` literals → `run_groundhog_export` (keyword-only, `reincarnation.py:1342`); `:226-227` → `run_reincarnation_export` (passes, out of scope).

---

## Task 1: Synthetic worlds (above-gate edge + below-gate subgate)

**Files:** Create `agent/backtest/synthetic_edge.py`, `tests/agent/backtest/test_synthetic_edge.py`.

- [ ] **Step 1: Read first** — `cached_sweep.py:95-154`; `survival_season.py:197-418` (SurvivalRow, build_survival_rows, the settlement clock); `historical_fetcher.py:107-170` (PricePoint+MarketSnapshot exact fields); `tennis_match_resolver.py:100-114`.
- [ ] **Step 2: Failing tests**

```python
# tests/agent/backtest/test_synthetic_edge.py
from agent.backtest.synthetic_edge import (
    build_synthetic_world, build_subgate_world, agent_ev, _edge_abs_for)

def test_zero_edge_ev_zero():
    rows, _ = build_synthetic_world(n=4000, edge=0.0, seed=7)
    assert abs(agent_ev(rows)) < 0.02

def test_positive_edge_ev_matches():
    rows, _ = build_synthetic_world(n=4000, edge=0.10, seed=7)
    assert abs(agent_ev(rows) - 0.10) < 0.03

def test_rows_build_and_winning_price_one():
    rows, _ = build_synthetic_world(n=50, edge=0.08, seed=3)
    assert len(rows) == 50 and all(r.winning_price == 1.0 for r in rows)

def test_subgate_rows_are_below_gate(v3_seed):
    rows, _ = build_subgate_world(n=200, edge=0.08, seed=5)
    assert all(_edge_abs_for(r, v3_seed) < v3_seed.min_edge for r in rows)

def test_mid_schedule_settlement_enables_repeated_death():
    # a death-inducing economy on the 0-edge above-gate world dies >1 time,
    # proving bets settle mid-schedule (not only at the final drain)
    from agent.backtest.run_survival_helpers import quick_numerical_deaths  # thin test helper
    rows, snaps = build_synthetic_world(n=400, edge=0.0, seed=1)
    assert quick_numerical_deaths(rows, snaps, loss_multiplier=5.0,
                                  initial_breath=35.0, max_lives=6) > 1
```

- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement.**
  - `build_synthetic_world(n, edge, seed)`: `price=0.5`, `C=0.30` (above gate), `true_prob=clip(0.5+edge)`; per row `i`: `won=rng.random()<true_prob`. **Timestamps:** entries strictly increasing `entry_i = T0 + i*STEP`; `resolution_ts_i = entry_i + RES_LAG` with `RES_LAG ≈ 2*STEP` (so row `i` settles around row `i+2`'s entry, mid-schedule); `end_date_iso_i = resolution_ts_i`. Flat ledger `[PricePoint(ts=iso(entry_i), mid_price=0.5), PricePoint(ts=iso(entry_i+1s), mid_price=0.5)]`. Return `build_survival_rows(signal_rows, snaps, TennisMatchResolver(name_index={})), snaps`.
  - `build_subgate_world(n, edge, seed)`: identical but `C=0.05` (BELOW gate). `edge>0` ⇒ hidden edge (frozen abstains, explorer harvests); `edge=0` ⇒ pure noise (exploration cost).
  - `agent_ev(rows) = mean((1.0 if r.outcome=="yes" else 0.0) - r.entry_price for r in rows)`.
  - `_edge_abs_for(row, seed) = abs(seed.kappa * 0.8 * row.scores[ENGINE_KEYS[0]])`.
  - Provide `quick_numerical_deaths(...)` (a thin wrapper over `run_survival_over_rows` returning `journey["summary"]["deaths"]`) so the settlement test is self-contained.
- [ ] **Step 5: Run, verify pass. Commit** `feat(survival): synthetic above/below-gate worlds + mid-schedule settlement`

---

## Task 2: `exploration_epsilon` on StrategyConfig + loader + disclosure

**Files:** Modify `find_optimal_config.py` (StrategyConfig), `scripts/run_v3_numerical.py` (`load_v3_seed`), `agent/backtest/validate_value_seed.py` (`_seed_payload`), the journey `seed` block (`survival_season.py:2596-2606`), the groundhog `knobs` block (`reincarnation.py:1828-1835`).

- [ ] **Step 1: Failing test** — default 0.0; NOT in `GENOME_KEYS`; round-trips through `load_v3_seed`; a journey/groundhog run discloses `exploration_epsilon` in its `seed`/`knobs` block.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** Add `exploration_epsilon: float = 0.0` as the LAST `StrategyConfig` field (NOT `GENOME_KEYS`). `load_v3_seed`: `exploration_epsilon=raw.get("exploration_epsilon", 0.0)`. `_seed_payload`: add the key. Add `"exploration_epsilon": seed.exploration_epsilon` to the journey `seed` block and `reincarnation.py` `knobs` block (additive optional key; if a golden-file/schema validator does exact-keyset comparison, update its allowed keyset in this task). Verify `fragile_seed_from_config` (`dataclasses.replace`) preserves it.
- [ ] **Step 4: Run, verify pass. Commit** `feat(config): non-advisable exploration_epsilon (StrategyConfig + loader + disclosure)`

---

## Task 3: Sim-based breath-economy calibration

**Files:** Create `agent/backtest/calibrate_breath_economy.py`, `tests/agent/backtest/test_calibrate_breath_economy.py`. (Depends on Task 1 + Task 2.)

- [ ] **Step 1: Failing test**

```python
from agent.backtest.calibrate_breath_economy import calibrate, CalibrationResult

def test_multiplier_varies_physics():
    res = calibrate(loss_multiplier_grid=[1.0, 5.0], initial_breath=35.0,
                    edge_live=0.10, edge_dead=0.0, n_rows=400, max_lives=6, seed=0)
    assert res.grid[0]["mean_final_breath_live"] != res.grid[1]["mean_final_breath_live"]

def test_recommended_separates_live_from_dead():
    res = calibrate(loss_multiplier_grid=[1.0,1.2,1.5,2.0,3.0,5.0], initial_breath=35.0,
                    edge_live=0.10, edge_dead=0.0, n_rows=400, max_lives=6, seed=0)
    assert res.death_rate_live < res.death_rate_dead
    assert res.loss_multiplier in [1.0,1.2,1.5,2.0,3.0,5.0]
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** `base = dataclasses.replace(load_v3_seed(), exploration_epsilon=0.0)`. For each `m`: FRESH `build_synthetic_world(n_rows, edge_live, seed)` + `(edge_dead)`; `journey = run_survival_over_rows(rows, snaps, base_seed=base, loss_multiplier=m, initial_breath=initial_breath, max_lives=max_lives, with_ai=False, preflight=False, exploration_rng=None)`; `journey_metric(...)`. Fail-closed sentinel = `mean_final_breath_live` (non-saturating). Dual criterion: max `(death_rate_dead − death_rate_live)` with `death_rate_live<0.5` and `death_rate_dead>0.5`; raise if none separate. `CalibrationResult(loss_multiplier, death_rate_live, death_rate_dead, initial_breath, exploration_epsilon=0.05, grid)`. `main()` → `reports/calibration/breath_economy_hand1.json`. Small grids.
- [ ] **Step 4: Run, verify pass.** Run the CLI; confirm `loss_multiplier < 5.0`.
- [ ] **Step 5: Commit** `feat(survival): sim-based breath-economy calibration`

---

## Task 4: Exploration floor in the decision engine

**Files:** Modify `agent/engines/decision.py`; `agent/backtest/survival_season.py` (`_decision_engine_from_seed`/`_build_life_loop`/`run_survival_season`/`run_survival_over_rows`). Tests + `conftest`.

**Design:** explore only if `epsilon>0 AND rng is not None`. Probe = FLAT `min_bet_size_usd` via shared `_clamped_size(*, desired, breath, max_breath_risk_pct, conversion_rate, bankroll_usd, bet_size_cap, liquidity_cap_usd)` (`desired=min_bet_size_usd`, `bet_size_cap=NORMAL_BET_SIZE_CAP`); return probe BET iff clamped `≥ min_bet_size_usd`, else the NO_BET. Explorable abstains: low-confidence (`:319`), price-floor (`:363`), no-edge (`:380`), zero-kelly (`:386`), below-min-size (`:411`). **Never** missing-signal (`:303`) / exactly-neutral (`:329`/`:352`). Side via `_value_side(price, fused, kappa, kappa_xm, cross_market_signal)` = `sign(p_model − price)` (matches the value-mode side even when `kappa_xm≠0`; Hand-1 has `xm=0` so it reduces to `sign(fused)`). rng gated on the LEARNING path.

- [ ] **Step 1:** Add `tests/agent/engines/conftest.py` defining `no_edge_kwargs` (all 10 decide kwargs; `price=0.5`; signals computed so value-mode `edge_abs≈0.03 < 0.05`, with generous `breath/bankroll`, `liquidity_cap_usd=1000`) and `missing_signal_kwargs` (signals missing one engine). Pin `kappa=0.25` in the test `_eng` so the band is local. Write the failing tests:

```python
import random, asyncio
from agent.engines.decision import DecisionEngine, NO_BET_NO_EDGE
class _Raising(random.Random):
    def random(self): raise AssertionError("rng touched")
def _eng(**kw): return DecisionEngine(kappa=0.25, min_edge=0.05, min_confidence=0.05, **kw)

def test_no_edge_fixture_actually_abstains(no_edge_kwargs):           # guard the fixture
    act = asyncio.run(_eng().decide(**no_edge_kwargs))
    assert act.kind.name == "NO_BET" and act.no_bet_reason.startswith(NO_BET_NO_EDGE)
def test_epsilon_zero_never_touches_rng(no_edge_kwargs):
    assert asyncio.run(_eng(exploration_epsilon=0.0, exploration_rng=_Raising()).decide(**no_edge_kwargs)).kind.name == "NO_BET"
def test_rng_none_never_explores(no_edge_kwargs):
    assert asyncio.run(_eng(exploration_epsilon=1.0, exploration_rng=None).decide(**no_edge_kwargs)).kind.name == "NO_BET"
def test_epsilon_lifts_to_bet_rate(no_edge_kwargs):
    eng=_eng(exploration_epsilon=0.2, exploration_rng=random.Random(0))
    bets=sum(asyncio.run(eng.decide(**no_edge_kwargs)).kind.name=="BET" for _ in range(2000))
    assert 0.15 < bets/2000 < 0.25
def test_explored_bet_is_clean_and_capped(no_edge_kwargs):
    eng=_eng(exploration_epsilon=1.0, exploration_rng=random.Random(1), min_bet_size_usd=5.0)
    a=asyncio.run(eng.decide(**no_edge_kwargs))
    assert a.kind.name=="BET" and a.size_usd>=5.0 and a.no_bet_reason is None
    assert asyncio.run(eng.decide(**{**no_edge_kwargs,"liquidity_cap_usd":0.0})).kind.name=="NO_BET"
def test_missing_signal_never_explores(missing_signal_kwargs):
    assert asyncio.run(_eng(exploration_epsilon=1.0, exploration_rng=random.Random(0)).decide(**missing_signal_kwargs)).kind.name=="NO_BET"
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: decision.py.** ctor adds the two params (`import random`, validate). Extract `_clamped_size` + `_value_side`. Add `self._explore_or(nobet, *, side, market_id, bankroll_usd, breath, liquidity_cap_usd)`: if gate open and `rng.random()<eps` and `side is not None`, `size=_clamped_size(desired=self._min_bet_size_usd, ...)`; return `Action(BET, market_id=market_id, side=side, size_usd=size, edge_pct=0.0)` (no `no_bet_reason`) iff `size>=min_bet_size_usd`, else `nobet`. Replace the 5 explorable NO_BET returns; leave `:303/:329/:352`.
- [ ] **Step 4: Thread rng.** Add `exploration_rng=None` to `_decision_engine_from_seed` (→ DecisionEngine), `_build_life_loop` (→ the `:1739` learner call only), `run_survival_season`, `run_survival_over_rows`. Defaults `None` ⇒ holdout/baseline/all non-Hand1 callers byte-identical.
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/ -v` → all PASS.
- [ ] **Step 6: Commit** `feat(decision): flat-stake post-fusion exploration floor, learning-gated rng`

---

## Task 5: Plumb through the groundhog harness

**Files:** Modify `agent/backtest/reincarnation.py` (`run_groundhog_export`), `scripts/run_reincarnation.py`. Groundhog ONLY.

- [ ] **Step 1: Failing test** (Task 6 file): `run_groundhog_export(rows=rows, snapshots=snaps, base_seed=<v3>, loss_multiplier=1.2, initial_breath=35.0, exploration_epsilon=0.1, max_incarnations=3)` (KEYWORD-only) on a tiny SUBGATE world; assert `artifact["knobs"]["loss_multiplier"]==1.2`, `artifact["knobs"]["exploration_epsilon"]==0.1`, and `sum(inc["bets"] for inc in artifact["incarnations"]) > 0` (proves exploration fired on a would-abstain world).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: reincarnation.py.** Add `exploration_epsilon: float = 0.0`; `base_seed = dataclasses.replace(base_seed, exploration_epsilon=exploration_epsilon)` up front. In-sample per-incarnation `run_survival_season(..., exploration_rng=random.Random(incarnation_k) if exploration_epsilon>0 else None)` (per-incarnation seed, since `max_lives=1` ⇒ `idx` always 0); holdout call → `exploration_rng=None`. Surface `exploration_epsilon` in the `knobs` block (Task 2).
- [ ] **Step 4: run_reincarnation.py.** Flags `--loss-multiplier/--initial-breath/--exploration-epsilon/--synthetic-edge`. **`--synthetic-edge E` ⇒ SKIP the `:118-124` cache load**, use `build_synthetic_world(...)`. groundhog branch: replace `:156-157` literals with flag-resolved values + pass `exploration_epsilon=`. Defaults from `reports/calibration/breath_economy_hand1.json` if present; active flag requested but unset + JSON absent ⇒ **fail closed**. `--design passes` + active flag ⇒ **error**.
- [ ] **Step 5: Run** the test → PASS. Smoke: `... --design groundhog --loss-multiplier 1.2 --exploration-epsilon 0.1 --synthetic-edge 0.1 --max-incarnations 3 --out reports/_smoke/smoke.json` (gitignore `reports/_smoke/`).
- [ ] **Step 6: Commit** `feat(reincarnation): groundhog calibrated economy + per-incarnation exploration + synthetic-edge`

---

## Task 6: Validation harness

**Files:** Create `scripts/run_active_survival_validation.py`, `tests/agent/runtime/test_active_survival_integration.py`.

`run_validation(world, exploration_epsilon, loss_multiplier, n, max_lives, seed, out) -> metrics` builds the world (`"edge:E"`→`build_synthetic_world`; `"subgate:E"`→`build_subgate_world`; `"cache"`/`"cache_g2"`→real cache) and calls `run_survival_over_rows(..., exploration_rng=random.Random(0) if exploration_epsilon>0 else None)`, returning `journey_metric(...)`.

- [ ] **Step 1: Integration tests**

```python
from scripts.run_active_survival_validation import run_validation

def test_survives_edge_dies_without(tmp_path):
    hi = run_validation(world="edge:0.10", exploration_epsilon=0.0, loss_multiplier=1.2,
                        n=600, max_lives=6, seed=0, out=tmp_path/"hi.json")
    lo = run_validation(world="edge:0.0", exploration_epsilon=0.0, loss_multiplier=1.2,
                        n=600, max_lives=6, seed=0, out=tmp_path/"lo.json")
    assert hi.death_rate < lo.death_rate and lo.deaths >= 1     # recalibration works

def test_floor_harvests_hidden_edge(tmp_path):
    # below-gate hidden edge: frozen abstains and stays flat; explorer harvests
    on  = run_validation(world="subgate:0.10", exploration_epsilon=0.1, loss_multiplier=1.2,
                         n=600, max_lives=6, seed=1, out=tmp_path/"on.json")
    off = run_validation(world="subgate:0.10", exploration_epsilon=0.0, loss_multiplier=1.2,
                         n=600, max_lives=6, seed=1, out=tmp_path/"off.json")
    assert on.total_bets > off.total_bets               # floor fires (off abstains)
    assert on.mean_final_breath > off.mean_final_breath  # and harvests the hidden edge
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `run_active_survival_validation.py` with `journey_metric` (run_survival_over_rows path) AND `groundhog_metric` (incarnations path) — keep them DISTINCT. `main()`: (a) survive/die `edge∈{0,0.05,0.10}`; (b) **floor VALUE** on `subgate:0.10` (on vs off → `mean_final_breath` lift); (c) **floor COST** on `subgate:0.0` (pure noise) — RECORD `off.mean_final_breath − on.mean_final_breath` as the measured exploration premium (no magic-number pass/fail; just disclose it, and assert `on` does not catastrophically die: `on.death_rate < 0.9`); (d) g1/g2 real cache (g1 captures overfit edge; g2 = cache + shuffle). Writes `reports/validation/active_survival_hand1.md` + JSON.
- [ ] **Step 4: Run** the integration tests → PASS; run the full harness; read the report.
- [ ] **Step 5: Commit** `feat(validation): survive/die + floor-value(hidden-edge) + cost disclosure + g1/g2`

---

## Final verification

- [ ] `python -m pytest -q` → green.
- [ ] `python -m agent.backtest.calibrate_breath_economy` writes recommended params; record in the report.
- [ ] `python scripts/run_active_survival_validation.py` → spec §2 (1–4) hold; floor harvests hidden edge; exploration premium on pure noise is finite & recorded.
- [ ] One SEPARATE `--divine-tithe` arm to exercise the tithe self-check (`reincarnation.py:2002-2034`).
- [ ] git identity `balflee`.

## Notes for executor
- numerical only; seeded randomness; exploration RNG per-incarnation; scope wall (no `sim/`, no A14/A2, no `--design passes`).
- **Two metric accessors** — journey-dict (`run_survival_over_rows`) vs groundhog-incarnations (`run_groundhog_export`); never cross them.
- **Exploration is a FLAT min-stake probe** on the value-mode side; rng gated on the learning path; baseline lives in `_static_baseline_curve_async` (stays `rng=None`).

---

## Revision log

- **R1 (HIGH=13):** descoped A14+A2; sim-based calibration; real symbols; non-advisable epsilon; synthetic via real schema.
- **R2 (HIGH=12):** calibration → `run_survival_over_rows`; `(rows,snapshots)` + `winning_price=1.0`; edge `C` clears gate; rng-gated baseline; `dataclasses.replace`/`load_v3_seed`; death-aware metric; groundhog-only.
- **R3 (HIGH=4):** serialized journey-dict metric; exploration rng gated on the learning path (holdout safe); FLAT probe (no Kelly); per-incarnation rng; abstain world added; non-saturating sentinel; `--synthetic-edge` bypass; resolver/PricePoint kw.
- **R4 (HIGH=3):** (1) **reordered** — StrategyConfig field (Task 2) before calibration (Task 3). (2) **Mid-schedule settlement timestamps** in the generator (resolution between own entry and a later row's entry) so the dead world dies repeatedly — else `death_rate_dead>0.5` is unreachable and `calibrate()` raises; added a `>1-death` test. (3) **Honest floor criterion:** a flat probe on a coin-flip is negative breath-EV (loss×m), so the floor DOES cost survival on pure noise. Reframed: floor VALUE measured on a BELOW-gate HIDDEN-edge world via `mean_final_breath` lift (a pure abstainer never dies in this runtime, so death-rate is the wrong metric there); pure-noise exploration premium RECORDED, not asserted-zero. Added `build_subgate_world`. (4) `_explore_or` takes `market_id`, returns a CLEAN BET (`no_bet_reason=None`). (5) side via `_value_side` (matches value-mode side under `kappa_xm`). (6) `conftest` defines the test fixtures with pinned `kappa`. (7) `run_groundhog_export` called KEYWORD-only; assert `sum(inc["bets"])` (groundhog has no top-level `total_bets`); two distinct metric accessors. (8) `exploration_epsilon` disclosed in journey `seed` + groundhog `knobs`. (9) baseline re-anchored to `_static_baseline_curve_async`; synthetic slugs noted as non-parsing (benign).
