# Active Survival (Hand 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make honest-win-rate survival achievable by recalibrating the breath economy with a SIM-BASED (not analytic) calibration, and stop the agent freezing via a non-advisable exploration floor — validated on a synthetic known-edge harness.

**Architecture:** A deterministic synthetic known-edge generator produces real `SurvivalRow`s whose fusion the agent actually sees, at a controllable win rate. A sim-based calibration runs short *real* numerical seasons across a breath-param grid on `p≈0.55` (edge) and `p≈0.50` (no edge) worlds and picks params satisfying a dual survival criterion. `exploration_epsilon` becomes a non-advisable `StrategyConfig` field threaded into `DecisionEngine`, which adds ONE post-caps ε-greedy floor. The run harness replaces the hard-coded breath literals with calibrated values; a validation harness proves the criteria + g1/g2 regression.

**Tech Stack:** Python 3.14, pytest, the `agent/backtest/` groundhog runtime (`reincarnation.py` → `survival_season.py` → `sandbox_phase2_loop.py` → `decision.py`). numerical provider only (no LLM, no API keys).

**Spec:** `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md`

**Branch:** `active-survival-hand1`. One commit per task. Confirm git identity is `balflee` before each commit.

**Descoped (→ Hand 1.5, NOT in this plan):** A14 breath-aware sizing and A2 death-aware credit — the Phase-2 review showed both are deeply coupled to shared live code (locked TP §4.7 `DESPERATE_BET_SIZE_CAP`), a latched/snapshot-persisted `desperate` flag, the settlement-poller `WeightUpdater` Protocol identity, tombstone-hash mutation, and the look-ahead auditor. They get their own superplan.

---

## Grounded contracts (verified against code — do not re-derive)

- `SignalRow` — `agent/backtest/cached_sweep.py:131-154`: `SignalRow(market_id:str, slug:str, scores:dict[str,float], confidences:dict[str,float], entry_price:float, outcome:str, winning_price:float, liquidity_cap_usd:float, cross_market_signal:float=0.0, cluster_key:str="")`. Score keys = the 5 engine names: `tennis_technical, market_momentum, smart_money, sentiment_llm, crowd_volume`. `outcome ∈ {"yes","no","void"}` (lowercase).
- `SurvivalRow` — `agent/backtest/survival_season.py:197-265`, frozen. OWN fields: `market_id, slug, signal:SignalRow, entry_asof_ts_iso, resolution_ts_iso, end_date_iso, outcome:str, winning_price, liquidity_cap, players, surface`. `scores/confidences/entry_price/cross_market_signal/cluster_key` are READ-ONLY @property delegations to `signal`.
- `build_survival_rows(rows, snapshots, resolver, *, entry_fraction=0.5, entry_price_floor=0.05)` — `survival_season.py:294-376`. RAISES `ValueError` if `abs(recomputed_mid - row.entry_price) > _ENTRY_PRICE_EPS` (`=1e-9`, line 194). `recomputed_mid` comes from `MarketSnapshot.price_ledger` (`PricePoint(.ts, .mid_price)`) via `_entry_asof` at `entry_fraction`. **A flat 2-point ledger at price `p` makes recomputed_mid == p exactly → check passes.**
- Row → agent inputs: `SurvivalTickSource.inputs_for` (`survival_season.py:576-596`) builds `TickInputs(signals=row_to_signals(row.signal), price=row.entry_price, cross_market_signal=row.signal.cross_market_signal, ...)`. `row_to_signals` (`cached_sweep.py:235-252`) maps each `scores[k]`+`confidences[k]` → `Signal(score, confidence,…)`. **To make the agent SEE an edge, set the 5 engine `scores` so the fused score has the same sign as the true (true_prob − price).** Value mode: `edge_yes = clip(price + kappa·fused, 0,1) − price`.
- `StrategyConfig` — `agent/backtest/find_optimal_config.py:57-82`, frozen: `weights:Weights, max_breath_risk_pct, min_confidence, min_bet_size_usd, min_edge=0.0, kappa=0.25, gate_storm_sensitivity=0.0, risk_storm_sensitivity=0.0, kappa_xm=0.0`. Loaded manually (no `.from_dict`) at `validate_value_seed.py:97-108` and constructed at `find_optimal_config.py:90-135` and `survival_season.py:2667-2672`.
- `_decision_engine_from_seed(seed, *, effective_entry_price_floor=None)` — `survival_season.py:1515-1547` (NOT `_make_decision_engine`). Seed-driven; docstring: "Sizing stays FIXED per seed across all lives."
- Per-life seed: `_build_life_loop(*, idx, seed, …)` — `survival_season.py:1574+`; `idx` (0,1,2,…) is the per-incarnation index, available where `_decision_engine_from_seed` is called (~:1767).
- Baseline literals: `scripts/run_reincarnation.py:156-157` pass `loss_multiplier=5.0, initial_breath=35.0` (and `divine_tithe=args.divine_tithe`). `run_groundhog_export` ALREADY has params `loss_multiplier, initial_breath, tithe_*` (defaults 1.0/100.0/off) — do NOT re-add; only change the call-site literals + add CLI flags.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `agent/backtest/synthetic_edge.py` | Create | Deterministic generator of `(SignalRow, MarketSnapshot)` pairs with a controllable known edge; `build_synthetic_survival_rows()`; `realized_winrate()`. |
| `tests/agent/backtest/test_synthetic_edge.py` | Create | edge=0 ⇒ ~50%; edge>0 ⇒ agent's fused-direction win rate lifts; rows pass `build_survival_rows`; deterministic. |
| `agent/backtest/calibrate_breath_economy.py` | Create | SIM-BASED sweep: run short numerical seasons on p≈0.55 / p≈0.50 worlds across a breath-param grid; dual-criterion select; write JSON. |
| `tests/agent/backtest/test_calibrate_breath_economy.py` | Create | Selector returns params whose MEASURED live-lifetime > dead-lifetime by margin; raises cleanly if none feasible. |
| `agent/backtest/find_optimal_config.py` | Modify | Add `exploration_epsilon: float = 0.0` to `StrategyConfig` (NOT in GENOME_KEYS). |
| `agent/backtest/survival_season.py` | Modify | Thread `exploration_epsilon` (from seed) + per-life `random.Random(idx)` into `_decision_engine_from_seed` and its call site in `_build_life_loop`. |
| `agent/backtest/reincarnation.py` | Modify | Add `exploration_epsilon` param to `run_groundhog_export`, thread into the seed used to build the engine. |
| `agent/core/state.py` or seed loader | Modify | Wherever `value_seed_v3.json` → `StrategyConfig` (manual dict load): accept optional `exploration_epsilon` (default 0.0). |
| `agent/engines/decision.py` | Modify | Add `exploration_epsilon` + `exploration_rng` ctor params; ONE post-caps ε-greedy floor. |
| `tests/agent/engines/test_decision_exploration.py` | Create | ε=0 never touches rng (raising-RNG); ε>0 lifts abstain→bet rate ≈ε; explored size obeys the 4-constraint min. |
| `scripts/run_reincarnation.py` | Modify | Replace :156-157 literals with flag/calibrated values; add `--loss-multiplier/--initial-breath/--exploration-epsilon/--synthetic-edge`; fail-closed if calibration JSON missing when recalibration requested. |
| `scripts/run_active_survival_validation.py` | Create | Synthetic-edge sweep + g1/g2 regression → report. |
| `tests/agent/runtime/test_active_survival_integration.py` | Create | Short end-to-end: edge=0.10 vs 0.0; g2 floor-on ≈ baseline; schema + tithe self-check unbroken. |

---

## Task 1: Synthetic known-edge generator

**Files:** Create `agent/backtest/synthetic_edge.py`, `tests/agent/backtest/test_synthetic_edge.py`.

- [ ] **Step 1: Read first.** Read `cached_sweep.py:131-252` (SignalRow + row_to_signals), `survival_season.py:197-376` (SurvivalRow + build_survival_rows + `_entry_asof`), and the `MarketSnapshot` definition (grep `class MarketSnapshot`; note its required fields incl. `price_ledger: list[PricePoint]`, `outcome`, `winning_price`, `liquidity_cap_usd`, `end_date_iso`, `resolution_ts_iso`). Confirm `PricePoint(ts, mid_price)`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/agent/backtest/test_synthetic_edge.py
from agent.backtest.synthetic_edge import (
    generate_synthetic_pairs, build_synthetic_survival_rows, realized_winrate,
)

def test_zero_edge_is_coinflip():
    rows = build_synthetic_survival_rows(n=4000, edge=0.0, seed=7)
    assert abs(realized_winrate(rows) - 0.5) < 0.03

def test_positive_edge_lifts_agent_visible_winrate():
    rows = build_synthetic_survival_rows(n=4000, edge=0.10, seed=7)
    # realized_winrate bets in the FUSED direction the agent would take
    assert realized_winrate(rows) > 0.55

def test_rows_pass_build_survival_rows_consistency():
    # flat price_ledger ⇒ recomputed mid == entry_price ⇒ no ValueError
    rows = build_synthetic_survival_rows(n=50, edge=0.08, seed=3)
    assert len(rows) == 50  # build_survival_rows did not raise

def test_deterministic_for_seed():
    a = build_synthetic_survival_rows(n=200, edge=0.08, seed=3)
    b = build_synthetic_survival_rows(n=200, edge=0.08, seed=3)
    assert [r.market_id for r in a] == [r.market_id for r in b]
    assert [r.outcome for r in a] == [r.outcome for r in b]
```

- [ ] **Step 3: Run, verify fail.** `python -m pytest tests/agent/backtest/test_synthetic_edge.py -v`

- [ ] **Step 4: Implement.** In `synthetic_edge.py`:
  - `generate_synthetic_pairs(n, edge, seed) -> tuple[list[SignalRow], list[MarketSnapshot]]`: for each i, draw `price ∈ [0.2,0.8]` (seeded), set `true_prob = clip(price + edge, 0, 1)`, sample `won = rng.random() < true_prob`, set `outcome = "yes" if won else "no"` (lowercase). Build the 5 engine `scores` all equal to a small positive constant scaled so `fused > 0` (agent tilts YES) — i.e. encode the edge as a positive directional signal of magnitude `~edge`; `confidences` = constant (e.g. 0.8). Build a `SignalRow` with `entry_price=price, winning_price=(1.0 if won else 0.0)` (or the schema's convention — confirm in Step 1), `liquidity_cap_usd=1000.0`. Build a matching `MarketSnapshot` with a **flat 2-point** `price_ledger=[PricePoint(t0, price), PricePoint(t1, price)]` so `_entry_asof` reconstructs exactly `price`, plus `outcome`/`winning_price`/`resolution_ts_iso`/`end_date_iso` consistent with the row.
  - `build_synthetic_survival_rows(n, edge, seed) -> list[SurvivalRow]`: call `build_survival_rows(signal_rows, snapshots, resolver=_synthetic_resolver())`. (If a real resolver is awkward to satisfy, construct `SurvivalRow` directly from each pair instead — but then document that the entry-asof reconstruction is skipped.)
  - `realized_winrate(rows)`: fraction of rows where a YES bet (the fused-positive direction) matches `outcome=="yes"`.
  - All randomness via `random.Random(seed)` only.

- [ ] **Step 5: Run, verify pass.** Commit:
```bash
git add agent/backtest/synthetic_edge.py tests/agent/backtest/test_synthetic_edge.py
git commit -m "feat(survival): synthetic known-edge generator (agent-visible fusion)"
```

---

## Task 2: Sim-based breath-economy calibration

**Files:** Create `agent/backtest/calibrate_breath_economy.py`, `tests/agent/backtest/test_calibrate_breath_economy.py`.

**Why sim-based (review HIGH):** the real breath delta is odds-dependent, size-scaled USD PnL with a loss-only multiplier (`survival_season.py:787-791`); the closed-form `m/(1+m)` walk is a fiction and its dual criterion is infeasible at 35/0.5/5. So calibrate by running the REAL sim at controlled win rates.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_calibrate_breath_economy.py
from agent.backtest.calibrate_breath_economy import calibrate, CalibrationResult

def test_recommended_params_separate_live_from_dead():
    res = calibrate(
        loss_multiplier_grid=[1.0, 1.2, 1.5, 2.0, 3.0, 5.0],
        initial_breath=35.0, edge_live=0.10, edge_dead=0.0,
        n_rows=400, lives=6, seed=0,
    )
    assert isinstance(res, CalibrationResult)
    # MEASURED (not analytic) mean lifetime: honest world outlives no-edge world
    assert res.measured_lifetime_live > res.measured_lifetime_dead
    assert res.loss_multiplier in [1.0, 1.2, 1.5, 2.0, 3.0, 5.0]
    assert len(res.grid) == 6

def test_raises_when_no_param_separates():
    import pytest
    with pytest.raises(ValueError):
        # a degenerate grid where even the best m cannot separate
        calibrate(loss_multiplier_grid=[50.0], initial_breath=1.0,
                  edge_live=0.0, edge_dead=0.0, n_rows=100, lives=3, seed=0)
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** `calibrate(...)`:
  - For each `m` in `loss_multiplier_grid`: build a live world (`build_synthetic_survival_rows(n_rows, edge_live, seed)`) and a dead world (`edge_dead`). Run a short numerical groundhog season on each via `run_survival_season(...)` (provider=numerical, `rebirth_llm=None`, `lives`, `initial_breath`, `loss_multiplier=m`, tithe defaults). Record measured mean lifetime (markets survived per life) + deaths from the season result.
  - Dual criterion: pick the `m` maximizing `(measured_lifetime_live − measured_lifetime_dead)` subject to `measured_lifetime_dead` still finite/bounded (no-edge still dies). If no `m` gives `live > dead` by a margin, raise `ValueError`.
  - Return `CalibrationResult(loss_multiplier, measured_lifetime_live, measured_lifetime_dead, initial_breath, grid=[{m, life_live, life_dead}…])`. `main()` writes `reports/calibration/breath_economy_hand1.json` (incl. `exploration_epsilon` recommendation — see Task 4 default — and the chosen `loss_multiplier`/`initial_breath`).
  - Keep grids SMALL by default (≤8 m-values, ≤6 lives, ≤400 rows) so the sim sweep is minutes, not hours; `log` the chosen values.

- [ ] **Step 4: Run, verify pass.** Run `python -m agent.backtest.calibrate_breath_economy` once; confirm it writes the JSON with a recommended `loss_multiplier` < 5.0.

- [ ] **Step 5: Commit** `feat(survival): sim-based breath-economy calibration (dual survival criterion)`

---

## Task 3: Add non-advisable `exploration_epsilon` to StrategyConfig + seed loader

**Files:** Modify `agent/backtest/find_optimal_config.py` (StrategyConfig :57-82), the seed-JSON loader (`validate_value_seed.py:97-108` + any other manual loaders), `agent/backtest/reincarnation.py` (GENOME_KEYS :105-113 — DO NOT add the key here).

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_strategyconfig_epsilon.py
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.reincarnation import GENOME_KEYS

def test_strategyconfig_has_epsilon_default_zero(minimal_weights):
    c = StrategyConfig(weights=minimal_weights, max_breath_risk_pct=0.3,
                       min_confidence=0.05, min_bet_size_usd=5.0)
    assert c.exploration_epsilon == 0.0

def test_epsilon_is_not_advisable():
    # the anti-freeze guarantee: advisor cannot mutate epsilon
    assert "exploration_epsilon" not in GENOME_KEYS
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** Add `exploration_epsilon: float = 0.0` as the last field of `StrategyConfig` (frozen dataclass — default keeps every existing positional/kw construction valid). In each manual loader (`validate_value_seed.py` `_seed_payload`/load, `survival_season.py:2667-2672`), read `payload.get("exploration_epsilon", 0.0)` on load and include it on serialize. **Do NOT add it to `GENOME_KEYS`** (keeps it non-advisable, mirroring the deliberately-non-advisable `min_bet_size_usd`).
- [ ] **Step 4: Run** `python -m pytest tests/agent/backtest/test_strategyconfig_epsilon.py -v` and the existing seed tests → PASS.
- [ ] **Step 5: Commit** `feat(config): non-advisable exploration_epsilon on StrategyConfig`

---

## Task 4: Exploration floor in the decision engine

**Files:** Modify `agent/engines/decision.py` (ctor :182-237; ONE insertion after the 4-constraint min at :405), `agent/backtest/survival_season.py` (`_decision_engine_from_seed` :1515 + its call site in `_build_life_loop` ~:1767). Test `tests/agent/engines/test_decision_exploration.py`.

**Design (resolves review findings):** centralize exploration as ONE decision AFTER the 4-constraint caps (decision.py:392-405), NOT four per-gate hooks. Mechanism: if `decide()` is about to return any NO_BET AND `exploration_epsilon > 0` AND `rng.random() < exploration_epsilon` AND a directional side is resolvable (`fused != 0`; skip exactly-neutral), compute a `min_bet_size_usd` bet run through the SAME `min(desired, breath_cap, bankroll_cap, liquidity_cap)`; return it as a BET only if the clamped size ≥ `min_bet_size_usd`, else stay NO_BET. RNG is a `random.Random` ctor param; **ε=0 must never touch the RNG**.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/engines/test_decision_exploration.py
import random, asyncio
from agent.engines.decision import DecisionEngine

class _RaisingRandom(random.Random):
    def random(self): raise AssertionError("rng touched at epsilon=0")

def test_epsilon_zero_never_touches_rng(abstaining_kwargs):
    eng = DecisionEngine(exploration_epsilon=0.0, exploration_rng=_RaisingRandom())
    assert asyncio.run(eng.decide(**abstaining_kwargs)).kind.name == "NO_BET"

def test_epsilon_lifts_abstain_to_bet_rate(abstaining_kwargs):
    eng = DecisionEngine(exploration_epsilon=0.2, exploration_rng=random.Random(0))
    bets = sum(asyncio.run(eng.decide(**abstaining_kwargs)).kind.name == "BET"
               for _ in range(2000))
    assert 0.15 < bets / 2000 < 0.25

def test_explored_bet_respects_4constraint_min(abstaining_kwargs):
    eng = DecisionEngine(exploration_epsilon=1.0, exploration_rng=random.Random(1),
                         min_bet_size_usd=5.0)
    act = asyncio.run(eng.decide(**{**abstaining_kwargs, "liquidity_cap_usd": 0.0}))
    assert act.kind.name == "NO_BET"   # liquidity cap 0 ⇒ clamp below floor ⇒ stay abstain
```

(`abstaining_kwargs` = a fixture whose signals fuse to a small but non-zero score below `min_edge`, so the engine abstains absent exploration but a side is resolvable.)

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement (decision.py).**
  - ctor: add `exploration_epsilon: float = 0.0`, `exploration_rng: random.Random | None = None`; validate `0.0 <= exploration_epsilon <= 1.0`; store `self._exploration_epsilon`, `self._exploration_rng = exploration_rng`. `import random`.
  - Refactor the NO_BET returns so they funnel through a single helper `self._abstain_or_explore(reason, *, fused, price, kappa..., bankroll_usd, breath, liquidity_cap_usd, market_id)` that: if `epsilon<=0` returns the plain NO_BET (no rng); else if `rng.random() < epsilon` and `fused != 0`, computes `side` from `fused` sign, sizes a `min_bet_size_usd` bet through the same 4-constraint `min(...)`, returns BET iff clamped ≥ `min_bet_size_usd`, else the NO_BET. Guard rng access strictly behind `epsilon>0` so ε=0 is byte-identical.
- [ ] **Step 4: Thread the RNG (survival_season.py).** Add `exploration_rng: random.Random | None = None` param to `_decision_engine_from_seed`; pass `exploration_epsilon=seed.exploration_epsilon, exploration_rng=exploration_rng` into the `DecisionEngine(...)` build. At the call site in `_build_life_loop` (~:1767), construct `random.Random(idx)` and pass it. Default `None`/0.0 keeps all existing callers byte-identical.
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/test_decision_exploration.py tests/agent/engines/test_decision.py -v` → all PASS (the FULL existing decision suite under ε=0 is the real regression oracle).
- [ ] **Step 6: Commit** `feat(decision): single post-caps exploration floor (non-advisable, seeded)`

---

## Task 5: Plumb calibrated params + exploration through the run harness

**Files:** Modify `agent/backtest/reincarnation.py` (`run_groundhog_export`), `scripts/run_reincarnation.py` (:91 CLI, :156-157 + :226 call sites). Test extends Task 6's integration file.

- [ ] **Step 1: Write a failing test** (in the Task 6 file) that calls `run_groundhog_export(..., loss_multiplier=1.2, exploration_epsilon=0.1, initial_breath=35.0, provider numerical)` on a tiny synthetic dataset and asserts: artifact `knobs` reflect `loss_multiplier=1.2`; the engine built per life received `exploration_epsilon=0.1` (assert via a season-result probe or that bet-rate > 0 on a would-abstain world); tithe self-check (`reincarnation.py:2002-2034`) passes.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: reincarnation.py.** Add ONLY the genuinely-new param `exploration_epsilon: float = 0.0` to `run_groundhog_export`; thread it into the `StrategyConfig` seed used to build the per-life engine (so `_decision_engine_from_seed` reads `seed.exploration_epsilon`). **Do NOT re-add `loss_multiplier`/`initial_breath`/`tithe_*`** — they already exist; just confirm they thread to the recorder.
- [ ] **Step 4: run_reincarnation.py.** Add CLI flags `--loss-multiplier` (float), `--initial-breath` (float), `--exploration-epsilon` (float), `--synthetic-edge` (float, optional). **Replace the hard-coded literals at :156-157** (`loss_multiplier=5.0, initial_breath=35.0`) and the second call (~:226) with the flag-resolved values. Resolve defaults from `reports/calibration/breath_economy_hand1.json` when present; if `--loss-multiplier` is left unset AND the JSON is absent, **fail closed** with a clear error (never silently run the old economy while claiming recalibration). When `--synthetic-edge E` is set, source rows from Task 1 instead of the cache.
- [ ] **Step 5: Run** the focused test → PASS. Smoke: `python scripts/run_reincarnation.py --provider numerical --loss-multiplier 1.2 --exploration-epsilon 0.1 --synthetic-edge 0.1 --max-incarnations 3 --out /tmp/smoke.json`.
- [ ] **Step 6: Commit** `feat(reincarnation): plumb calibrated breath economy + exploration + synthetic-edge`

---

## Task 6: Validation harness — synthetic-edge sweep + g1/g2 regression

**Files:** Create `scripts/run_active_survival_validation.py`, `tests/agent/runtime/test_active_survival_integration.py`.

- [ ] **Step 1: Write the integration test** (short, numerical, tiny n):

```python
# tests/agent/runtime/test_active_survival_integration.py (sketch)
from scripts.run_active_survival_validation import run_validation

def test_active_agent_exploits_known_edge_and_dies_without_one(tmp_path):
    hi = run_validation(edge=0.10, exploration_epsilon=0.1, loss_multiplier=1.2,
                        n=600, lives=6, seed=0, out=tmp_path/"hi.json")
    lo = run_validation(edge=0.00, exploration_epsilon=0.1, loss_multiplier=1.2,
                        n=600, lives=6, seed=0, out=tmp_path/"lo.json")
    assert hi.total_bets > 0                                  # §2.2 no freeze
    assert hi.mean_lifetime_markets > lo.mean_lifetime_markets # §2.3 exploits edge
    assert lo.deaths >= 1                                      # §2.1 stakes preserved

def test_g2_floor_does_not_accelerate_honest_death(tmp_path):
    # §2.4: with the exploration floor on, scrambled (zero-edge) g2 lifetime must
    # be >= baseline within tolerance (floor must not make honest noise die faster)
    on  = run_g2(exploration_epsilon=0.1, out=tmp_path/"g2on.json")
    off = run_g2(exploration_epsilon=0.0, out=tmp_path/"g2off.json")
    assert on.mean_lifetime_markets >= 0.8 * off.mean_lifetime_markets
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `scripts/run_active_survival_validation.py`: a `run_validation(edge, exploration_epsilon, loss_multiplier, n, lives, seed, out)` helper that builds the synthetic world (or scrambled g2 for `run_g2`), runs a numerical season, and returns a small metrics object (`total_bets, deaths, mean_lifetime_markets, vault_minus_seed`). `main()` sweeps `edge ∈ {0, 0.05, 0.10}` + runs g1 (real cache) and g2 (`--shuffle-timestamps-seed 1`) with calibrated params, writes `reports/validation/active_survival_hand1.md` + JSON.
- [ ] **Step 4: Run** `python -m pytest tests/agent/runtime/test_active_survival_integration.py -v` → PASS. Then run the full harness once and read the report.
- [ ] **Step 5: Commit** `feat(validation): synthetic-edge + g1/g2 active-survival harness`

---

## Final verification (before finishing the branch)

- [ ] `python -m pytest -q` → green.
- [ ] `python -m agent.backtest.calibrate_breath_economy` writes the recommended params; record them in the validation report.
- [ ] `python scripts/run_active_survival_validation.py` → spec §2 criteria (1–4) hold on non-toy sizes; g2 floor-on ≈ baseline.
- [ ] `reincarnation.py` tithe/accounting self-check (:2002-2034) green; no look-ahead auditor failures.
- [ ] git identity `balflee` for every commit.

## Notes for the executor
- **Provider:** numerical only — NO Gemini/MiniMax, NO API keys.
- **Determinism:** all randomness via a seeded `random.Random`; the exploration RNG is per-life `random.Random(idx)`. Never unseeded global `random`/`numpy.random`.
- **Scope wall:** do NOT touch `sim/`, and do NOT implement A14 (desperate sizing) or A2 (death credit) — they are Hand 1.5.
- **Don't re-add existing params:** `loss_multiplier/initial_breath/tithe_*` already exist on `run_groundhog_export`; only `exploration_epsilon` is new, and the call-site literals need replacing.

---

## Revision log

- **R1 (2026-06-15, after Phase-2 panel `HIGH=13 MEDIUM=16 LOW=6`):** wholesale rewrite. (1) **Descoped A14 + A2 → Hand 1.5** (shared-live-code coupling: locked `DESPERATE_BET_SIZE_CAP`, latched/persisted `desperate`, settlement-poller `WeightUpdater` Protocol identity, tombstone hash, look-ahead — user-approved). (2) **Calibration → sim-based** (the analytic `m/(1+m)` walk was infeasible at 35/0.5/5 and mismatched the real odds-dependent, size-scaled, loss-only-multiplier physics; now runs real short numerical seasons at controlled win rates). (3) Fixed every `_make_decision_engine` → real `_decision_engine_from_seed`; RNG seed routed from `_build_life_loop(idx)`. (4) `exploration_epsilon` lands on `StrategyConfig` (NOT `GENOME_KEYS` = non-advisable), with a dedicated config task. (5) Synthetic generator now builds full `SignalRow` + flat-`price_ledger` `MarketSnapshot` through `build_survival_rows`, injecting the edge into the engine `scores` the agent actually fuses (not just `outcome`). (6) Exploration centralized to ONE post-caps decision (not 4 per-gate hooks); ε=0 proven non-RNG-touching via a raising-RNG. (7) Task 5 explicitly replaces the `run_reincarnation.py:156-157` literals + fail-closed on missing calibration JSON; stops re-adding already-existing params. (8) Added the §2.4 g2-floor-on-≈-baseline regression to the validation harness.
