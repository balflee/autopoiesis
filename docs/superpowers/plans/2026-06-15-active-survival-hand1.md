# Active Survival (Hand 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use `- [ ]`. **Anchor edits by SYMBOL; confirm line numbers in each task's Read-first step. All paths are the canonical tree (`agent/backtest/...`, `scripts/...`), NOT a `.worktrees/*` copy.** Tasks are dependency-ordered.

**Goal:** Make honest-win-rate survival achievable by recalibrating the breath economy with a SIM-BASED calibration (running the REAL prod sim), and stop the agent freezing via a non-advisable exploration floor — validated on synthetic worlds.

**Architecture:** A deterministic generator returns `SurvivalRow`s + matching `MarketSnapshot`s spaced **1 day apart** so each bet settles MID-schedule (the season's default 2h settle-lag lands ~22h before the next decision) and the dead world can die repeatedly. Calibration runs short real numerical seasons through `run_survival_over_rows` across a loss-multiplier grid on an above-gate `+edge` world vs a `0-edge` world, reading the SERIALIZED journey dict. `exploration_epsilon` is a non-advisable `StrategyConfig` field; `DecisionEngine` adds ONE post-fusion FLAT-`min_bet_size_usd` probe gated by `epsilon>0 AND rng is not None`, on FOUR side-resolvable abstains (NOT the price-floor gate). Validation proves recalibration (survive/die) and the floor's VALUE (harvests a BELOW-gate hidden edge a frozen agent would abstain on).

**Breath economy facts (verified):** breath changes on EVERY settled bet — a WIN **adds `+pnl`**, a LOSS **subtracts `loss_multiplier×|pnl|`** (`agent/backtest/survival_season.py:787-791` amplifies only the negative leg; `agent/backtest/replay_runner.py:396-397` adds `pnl` to breath) — plus tithe (off by default) + lung-expansion (bankroll>1.1×initial). **A pure abstainer (no settled bets) never dies.** Consequences: an above-gate 0-edge world dies via the loss-amplified net drift on coin-flips (`drift/bet ≈ size·0.5·(1−m) < 0` for `m>1`); on a below-gate **+edge** world a frozen agent abstains and stays FLAT, while the explorer's probes win >50% (side-correct `+size` on win, `−size` on loss) → **net POSITIVE breath** → `on.mean_final_breath > off.mean_final_breath` (this is WHY the floor-VALUE inequality holds — wins lift breath). On a below-gate **0-edge** world the probe is negative-EV (loss×m) → a finite exploration premium, RECORDED not asserted-zero.

**Spec:** `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md`. **Branch:** `active-survival-hand1`. One commit/task; git identity `balflee`. **Descoped → Hand 1.5:** A14, A2.

---

## Grounded contracts (verified — canonical tree)

- `SignalRow` (`agent/backtest/cached_sweep.py:131-154`); settlement (`agent/backtest/cached_sweep.py:95-112`): win iff `(side=="YES")==(outcome=="yes")`; **`winning_price=1.0`** for any resolved row.
- `SurvivalRow` (`agent/backtest/survival_season.py:197-265`, frozen). `build_survival_rows(rows, snapshots, resolver, *, entry_fraction=0.5, entry_price_floor=0.05)` raises if `abs(recomputed_mid−entry_price)>1e-9`; flat ledger at `p` ⇒ exact. Synthetic slugs `"alpha{i}-vs-beta{i}"` deliberately do NOT parse (digit suffix) ⇒ `players/surface=None`, benign. Resolver = `TennisMatchResolver(name_index={})` (`agent/backtest/tennis_match_resolver.py:100-114`).
- **Settlement clock:** a bet at `entry_i` settles when `max(end_date_iso_i + settle_lag, resolution_ts_i) <= now`; `settle_lag` defaults to `DEFAULT_SETTLE_LAG=timedelta(hours=2)` (`survival_season.py:107`). **Recipe (settle-lag-aware): space entries 1 DAY apart, resolve immediately** — `entry_i = T0 + i*timedelta(days=1)`, `resolution_ts_i = end_date_iso_i = entry_i + 1min` ⇒ `due_i ≈ entry_i+2h`, ~22h before `entry_{i+1}` ⇒ each bet settles mid-schedule (no `settle_lag` threading needed).
- `PricePoint(ts=ISO_str, mid_price=0..1)` kw-only pydantic (`agent/backtest/historical_fetcher.py:107-121`). `MarketSnapshot` (`:157-166`) kw-only `extra='forbid'`. **REQUIRED (no default):** `liquidity_cap_usd: Field(gt=0)`, `outcome`, `market_id`, `slug`, `end_date_iso`, `resolution_ts_iso`, `price_ledger`. **`winning_price (0..1)` is OPTIONAL (default None)** — Task 1 sets it `=1.0` explicitly. Confirm the exact set in Task 1 Read-first.
- Row→agent: `row_to_signals(row.signal)`; value-mode `edge_abs=|kappa·fused|` (fused via the 5 slots `RATIONAL_ENGINES`+`SENTIENT_ENGINES`, `decision.py:93/98`) must clear `min_edge`. v3: `kappa≈0.492, min_edge≈0.0349`; all scores `=C`, conf `=0.8` ⇒ `edge_abs≈0.394C`. **`C=0.30` ⇒ above gate; `C=0.05` ⇒ below gate.** Assert gate membership by running `decide()`→`NO_BET_NO_EDGE` (not a one-engine proxy).
- `StrategyConfig` (`agent/backtest/find_optimal_config.py:57-82`). Loader `scripts/run_v3_numerical.py:30-40 load_v3_seed`; serializer `agent/backtest/validate_value_seed.py:97-108 _seed_payload` (emits weights/max_breath_risk_pct/min_confidence/min_bet_size_usd/min_edge/kappa/kappa_xm — **already partial: storm γ knobs are omitted**). Journey `seed` block `survival_season.py:2596-2606`; groundhog `knobs` block `reincarnation.py:1828-1835`.
- Calibration entry `run_survival_over_rows(survival_rows, snapshots, *, base_seed, loss_multiplier, initial_breath, max_lives, fragile_max_breath_risk_pct=1.0, with_ai=False, preflight=False, value_betting=True, side_correct_pricing=True)` (`agent/backtest/survival_season.py:2710-2733`) — internally re-derives a FRAGILE seed (`fragile_seed_from_config(base_seed, max_breath_risk_pct=fragile_max_breath_risk_pct)`, `:2773-2775`; preserves kappa/min_edge so C-gates hold) and owns `loss_multiplier` via `SurvivalRecorder`. Returns the SERIALIZED journey dict: `journey["summary"]["deaths"]` (int), per-life LIST `journey["lives"]` (keys `idx/start_ts/bets/settlements/final_breath/final_bankroll_usd/pnl/death`; NO `consumed_market_ids`). **`exploration_rng` does NOT exist on this signature yet — it is ADDED in Task 4; Tasks 1–3 must NOT pass it.**
- **Metric module `agent/backtest/survival_metrics.py` (created Task 3) — SINGLE home:** `journey_metric(journey, max_lives)` (assert `isinstance(journey["lives"], list)`; `death_rate=deaths/max_lives`, `mean_final_breath=mean(l["final_breath"])`, `total_bets=sum(l["bets"])`) + `groundhog_metric(artifact)` (`total_bets=sum(inc["bets"] for inc in artifact["incarnations"])`, `reincarnation.py:1565`). Never cross.
- `_decision_engine_from_seed(seed, *, effective_entry_price_floor=None, exploration_rng=None)` (`survival_season.py:1515`) called at **`:1739` (LEARNER, in `_build_life_loop`)** and inside **`_static_baseline_curve_async` (FROZEN baseline)**. It builds `DecisionEngine` reading seed fields by keyword. Thread rng only via the `:1739` call; baseline + holdout (`_run_frozen_holdout` → `run_survival_season(learning_enabled=False)`) keep `rng=None` (so even with `seed.exploration_epsilon>0` they do NOT explore — the gate needs `rng is not None`).
- `Action` BET (`agent/core/state.py:114-124`) requires `market_id+side+size_usd` and NO `no_bet_reason`.
- **Journey-physics invariant:** `build_survival_journey` raises if any PLACED bet's effective entry price `< effective_entry_price_floor` (`survival_season.py:2566-2594`, scans `recorder.placed_bets` incl. probes) ⇒ price-floor abstain (`decision.py:363`) is NOT explorable.
- Run harness: `scripts/run_reincarnation.py:118-124` loads the cache UNCONDITIONALLY; `run_groundhog_export` (keyword-only) opens at `:146` with `loss_multiplier=5.0`/`initial_breath=35.0` literal args at `:156-157`; `run_reincarnation_export` (passes, out of scope) at `:218`/literals `:226-227`.

---

## File Structure

(unchanged from R5) — `synthetic_edge.py`, `find_optimal_config.py`(field), `run_v3_numerical.py`+`validate_value_seed.py`+seed/knobs disclosure, `survival_metrics.py`, `calibrate_breath_economy.py`, `decision.py`, `survival_season.py`(rng+epsilon threading), `reincarnation.py`, `run_reincarnation.py`, `run_active_survival_validation.py`, plus their tests + `tests/agent/engines/conftest.py`.

---

## Task 1: Synthetic worlds (1-day-spaced, above/below-gate)

**Files:** Create `agent/backtest/synthetic_edge.py`, `tests/agent/backtest/test_synthetic_edge.py`.

- [ ] **Step 1: Read first** — `agent/backtest/cached_sweep.py:95-154`; `agent/backtest/survival_season.py:197-418`; `agent/backtest/historical_fetcher.py:107-170` (PricePoint+MarketSnapshot EXACT required set — note `winning_price` is OPTIONAL); `agent/backtest/tennis_match_resolver.py:100-114`.
- [ ] **Step 2: Failing tests** — EV≈edge for 0.0 and 0.10; `winning_price==1.0`; subgate rows abstain (assert a v3-seeded `DecisionEngine.decide()` returns `NO_BET_NO_EDGE` on a subgate row); `quick_numerical_deaths(build_synthetic_world(400,0.0,1)..., loss_multiplier=5.0, initial_breath=35.0, max_lives=6) > 1`; deterministic.
- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement.**
  - `_make_pair(i, *, price, C, won, base_ts)`: `SignalRow(market_id=f"syn-{i:06d}", slug=f"alpha{i}-vs-beta{i}", scores={k:C for k in ALL_ENGINES}, confidences={k:0.8 for k in ALL_ENGINES}, entry_price=price, outcome=("yes" if won else "no"), winning_price=1.0, liquidity_cap_usd=1000.0)`. `MarketSnapshot(market_id=..., slug=..., outcome=("yes" if won else "no"), winning_price=1.0, liquidity_cap_usd=1000.0, end_date_iso=iso(entry_i+1min), resolution_ts_iso=iso(entry_i+1min), price_ledger=[PricePoint(ts=iso(entry_i), mid_price=price), PricePoint(ts=iso(entry_i+1s), mid_price=price)])`, `entry_i = base_ts + i*timedelta(days=1)`. Generous `liquidity_cap_usd` so the 4-constraint min never clamps below `min_bet_size_usd`.
  - `build_synthetic_world(n, edge, seed)`: `price=0.5, C=0.30`, `true_prob=clip(0.5+edge)`, `won=rng.random()<true_prob`. Return `build_survival_rows(signal_rows, snaps, TennisMatchResolver(name_index={})), snaps`.
  - `build_subgate_world(n, edge, seed)`: `C=0.05` (below gate), else identical.
  - `agent_ev(rows) = mean((1.0 if r.outcome=="yes" else 0.0) - r.entry_price for r in rows)`.
  - `quick_numerical_deaths(rows, snaps, *, loss_multiplier, initial_breath, max_lives)`: → `run_survival_over_rows(...)["summary"]["deaths"]`.
- [ ] **Step 5: Run, verify pass. Commit** `feat(survival): synthetic above/below-gate worlds (1-day-spaced, mid-schedule settle)`

---

## Task 2: `exploration_epsilon` on StrategyConfig + loader + disclosure

Add `exploration_epsilon: float = 0.0` (last `StrategyConfig` field; NOT `GENOME_KEYS`); `load_v3_seed` reads `raw.get("exploration_epsilon", 0.0)`. **Append** `exploration_epsilon` as an additive optional key to `_seed_payload`, the journey `seed` block, and the groundhog `knobs` block — **matching each block's existing (already-partial) keyset convention; do NOT assume a full-genome golden.** If a strict-keyset validator exists for a block, extend THAT validator only. Verify `fragile_seed_from_config` (`dataclasses.replace`) preserves the field. Tests: default 0.0; not in `GENOME_KEYS`; round-trips through `load_v3_seed`. **Commit** `feat(config): non-advisable exploration_epsilon + loader + disclosure`.

---

## Task 3: Metric module + sim-based calibration

**Files:** Create `agent/backtest/survival_metrics.py`, `agent/backtest/calibrate_breath_economy.py`, `tests/agent/backtest/test_calibrate_breath_economy.py`. (Depends on Tasks 1–2.)

- [ ] **Step 1:** Create `survival_metrics.py` (`journey_metric` + `groundhog_metric`, each with a shape assertion). Write the failing test. **First EMPIRICALLY pin a feasible point** via `quick_numerical_deaths`: find a concrete `(m, initial_breath, n_rows)` where the 0-edge above-gate world has `death_rate>0.5` AND the +edge world has `death_rate<0.5` (because wins replenish breath, the dual window is NOT guaranteed at any `m<5` — if the default `initial_breath=35` doesn't separate, **tune `initial_breath` DOWN** (e.g. 20) or the grid, and record the chosen point). The test asserts the pinned point separates AND that `calibrate(...)` recommends an `m` from the grid; "no m separates" is a surfaced calibration FAILURE, not an unhandled crash.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `calibrate(...)`: `base = dataclasses.replace(load_v3_seed(), exploration_epsilon=0.0)`. For each `m`: FRESH `build_synthetic_world(n_rows, edge_live, seed)` + `(edge_dead)`; `journey = run_survival_over_rows(rows, snaps, base_seed=base, loss_multiplier=m, initial_breath=initial_breath, max_lives=max_lives, fragile_max_breath_risk_pct=0.95, with_ai=False, preflight=False)` (**`fragile_max_breath_risk_pct=0.95` to match the groundhog deployment, `run_reincarnation.py:155`; do NOT pass `exploration_rng` — not added until Task 4**); `journey_metric(journey, max_lives)`. Fail-closed sentinel = `mean_final_breath_live`. Dual criterion: max `(death_rate_dead − death_rate_live)` with `death_rate_live<0.5` and `death_rate_dead>0.5`; raise if none. `CalibrationResult(loss_multiplier, death_rate_live, death_rate_dead, initial_breath, exploration_epsilon=0.05, grid)`. `main()` → `reports/calibration/breath_economy_hand1.json`. Small grids. Document in the report that calibration runs on the fragile-derived seed (risk_pct 0.95), not the raw v3 seed.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat(survival): survival_metrics + sim-based breath-economy calibration`

---

## Task 4: Exploration floor in the decision engine

**Files:** Modify `agent/engines/decision.py`; `agent/backtest/survival_season.py` (rng+epsilon threading). Tests + conftest.

**Design:** explore only if `epsilon>0 AND rng is not None`. Probe = FLAT `min_bet_size_usd` via shared `_clamped_size(*, desired, breath, max_breath_risk_pct, conversion_rate, bankroll_usd, bet_size_cap, liquidity_cap_usd)` (`desired=min_bet_size_usd`); return probe BET iff clamped `≥ min_bet_size_usd`, else NO_BET. **Explorable abstains (FOUR): low-confidence (`:319`), no-edge (`:380`), zero-kelly (`:386`), below-min-size (`:411`).** NOT price-floor (`:363`), missing-signal (`:303`), exactly-neutral (`:329`/`:352`). Side via `_value_side(price, fused, kappa, kappa_xm, cross_market_signal)` = `sign(p_model−price)` in value mode, **`None` when `price is None`** (legacy → no probe). Exploration is value-mode-only by contract (all wired callers pass `price`).

- [ ] **Step 1:** Create `tests/agent/engines/conftest.py` defining `no_edge_kwargs` (the **10 required** decide kwargs `signals, weights_alpha, weights_beta, w_r, w_s, rho, bankroll_usd, breath, liquidity_cap_usd, market_id` **PLUS `price=0.5`** (value mode; `cross_market_signal` defaults 0.0); signals so `edge_abs≈0.03<0.05`; generous breath/bankroll; `liquidity_cap_usd=1000`) and `missing_signal_kwargs`. Pin `kappa=0.25, min_edge=0.05` in `_eng`. Failing tests: fixture abstains with `NO_BET_NO_EDGE`; ε=0/rng=None never touch rng (raising-RNG); ε>0 lifts abstain→bet≈ε; explored BET clean (`no_bet_reason is None`, `size>=min`), `liquidity_cap_usd=0` ⇒ NO_BET; missing-signal never explores.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: decision.py.** ctor adds the two params (`import random`, validate). `_clamped_size` + `_value_side`. `self._explore_or(nobet, *, side, market_id, bankroll_usd, breath, liquidity_cap_usd)`: if `eps>0 and rng is not None and rng.random()<eps and side is not None`, `size=_clamped_size(desired=self._min_bet_size_usd, ...)`; return clean `Action(ActionKind.BET, market_id=market_id, side=side, size_usd=size, edge_pct=0.0)` iff `size>=min_bet_size_usd`, else `nobet`. Replace the FOUR explorable NO_BET returns; leave `:303/:329/:352/:363`.
- [ ] **Step 4: Thread rng + epsilon.** In `_decision_engine_from_seed`: add `exploration_rng=None` param AND **pass BOTH `exploration_epsilon=seed.exploration_epsilon` and `exploration_rng=exploration_rng` into the `DecisionEngine(...)` ctor** (rng alone is inert — the gate needs `epsilon>0` too). Thread `exploration_rng=None` through `_build_life_loop` (the `:1739` learner call only — the `_static_baseline_curve_async` baseline + holdout keep `None`), `run_survival_season`, `run_survival_over_rows`. Defaults `None`. **Season-level test:** `run_survival_over_rows` on a subgate world with `base_seed.exploration_epsilon=0.1` + non-None rng places strictly MORE bets than the ε=0 control (proves epsilon reaches the engine and rng=None suppresses on baseline/holdout).
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/ -v` → PASS.
- [ ] **Step 6: Commit** `feat(decision): flat-stake exploration floor on 4 abstains, epsilon+rng wired, learning-gated`

---

## Task 5: Plumb through the groundhog harness

(As R5, groundhog ONLY.) `run_groundhog_export(..., exploration_epsilon=0.0)` keyword-only; `base_seed=dataclasses.replace(base_seed, exploration_epsilon=...)`; in-sample per-incarnation `exploration_rng=random.Random(incarnation_k) if exploration_epsilon>0 else None`; holdout `None`. CLI flags `--loss-multiplier/--initial-breath/--exploration-epsilon/--synthetic-edge`; `--synthetic-edge E` SKIPS the `:118-124` cache load; replace `:156-157` literals; defaults from calibration JSON, fail-closed if requested+absent; `--design passes`+active-flag ⇒ error. Test (keyword-only, SUBGATE world): `artifact["knobs"]["loss_multiplier"]==1.2`, `["exploration_epsilon"]==0.1`, `groundhog_metric(artifact).total_bets>0`. **Commit** `feat(reincarnation): groundhog calibrated economy + per-incarnation exploration + synthetic-edge`.

---

## Task 6: Validation harness

**Files:** Create `scripts/run_active_survival_validation.py` (imports `journey_metric`/`groundhog_metric` from `survival_metrics`), `tests/agent/runtime/test_active_survival_integration.py`.

`run_validation(world, exploration_epsilon, loss_multiplier, n, max_lives, seed, out)` builds the world (`"edge:E"`/`"subgate:E"`→synthetic; `"cache"`/`"cache_g2"`→real cache) → `run_survival_over_rows(..., exploration_rng=random.Random(0) if exploration_epsilon>0 else None)` → `journey_metric(...)`. **Cache worlds run with `exploration_epsilon=0`** (variable prices ⇒ a probe could hit the price-floor invariant).

- [ ] **Step 1: Integration tests:** (a) `edge:0.10` vs `edge:0.0` (both ε=0): `hi.death_rate < lo.death_rate and lo.deaths>=1`. (b) `subgate:0.10` ε=0.1 vs ε=0: `on.total_bets > off.total_bets and on.mean_final_breath > off.mean_final_breath`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** `main()`: (a) survive/die `edge∈{0,0.05,0.10}` (ε=0); (b) floor VALUE on `subgate:0.10` (`mean_final_breath` lift); (c) floor COST on `subgate:0.0` — RECORD `off.mean_final_breath − on.mean_final_breath` (assert only `on.death_rate < 0.9`); (d) g1/g2 real cache with `exploration_epsilon=0`. Writes `reports/validation/active_survival_hand1.md` + JSON.
- [ ] **Step 4: Run** the integration tests → PASS; run the full harness; read the report.
- [ ] **Step 5: Commit** `feat(validation): survive/die + floor-value + cost disclosure + g1/g2`

---

## Final verification

- [ ] `python -m pytest -q` → green.
- [ ] `python -m agent.backtest.calibrate_breath_economy` writes recommended params; record in the report.
- [ ] `python scripts/run_active_survival_validation.py` → spec §2 (1–4) hold; floor harvests hidden edge; pure-noise premium finite & recorded.
- [ ] One SEPARATE `--divine-tithe` arm to exercise the tithe self-check (`reincarnation.py:2002-2034`).
- [ ] git identity `balflee`.

## Notes for executor
- numerical only; seeded randomness; exploration RNG per-incarnation; scope wall (no `sim/`, no A14/A2, no `--design passes`).
- **Two metric accessors in `survival_metrics.py`** — journey-dict vs groundhog-incarnations; never cross.
- **Exploration = FLAT min-stake probe on 4 abstains (NOT price-floor), value-mode-only**; `epsilon` AND `rng` both wired into the engine; rng gated on the learning path; cache/g1/g2 validation runs with ε=0; baseline (`_static_baseline_curve_async`) stays `rng=None`.

---

## Revision log

- **R1 (HIGH=13):** descoped A14+A2; sim-based calibration; real symbols; non-advisable epsilon.
- **R2 (HIGH=12):** `run_survival_over_rows`; `(rows,snaps)`+`winning_price=1.0`; edge `C` clears gate; rng-gated baseline; `load_v3_seed`; death-aware metric; groundhog-only.
- **R3 (HIGH=4):** serialized journey-dict metric; rng on the learning path; FLAT probe; per-incarnation rng; subgate world; non-saturating sentinel; `--synthetic-edge` bypass.
- **R4 (HIGH=3):** reorder; mid-schedule settlement; honest floor (value via `mean_final_breath`); `_explore_or` market_id/clean BET; `_value_side`; conftest; keyword-only groundhog + `sum(inc["bets"])`; epsilon disclosure.
- **R5 (HIGH=2 distinct):** 1-day-spaced settle-lag-aware timestamps; dropped premature `exploration_rng=None` from calibration; full `MarketSnapshot` fields; `survival_metrics.py` single home; dropped price-floor `:363` from explorable; subgate via `decide()→NO_BET_NO_EDGE`; canonical paths.
- **R6 (HIGH=1):** (1) **wire the epsilon VALUE** — `_decision_engine_from_seed` now passes `exploration_epsilon=seed.exploration_epsilon` (not just `rng`) into the ctor (rng alone is inert → engine never explored), with a season-level test that ε>0+rng places more bets. (2) **Corrected breath physics** — wins ADD `+pnl`, losses subtract `loss×m` (not "losses only"); restated WHY `on.mean_final_breath > off` (explorer's +edge wins lift breath). (3) `_value_side` returns `None` when `price is None` (exploration value-mode-only). (4) Calibration passes `fragile_max_breath_risk_pct=0.95` (match deployment) and EMPIRICALLY pins a feasible `(m, initial_breath)` point (wins replenish breath ⇒ the dual window isn't guaranteed at `m<5`; tune `initial_breath` down if needed; "no m separates" is a surfaced failure). (5) Disclosure = additive optional key matching each block's already-partial keyset (storm γ are omitted) — no full-genome golden assumed. (6) `winning_price` is OPTIONAL; `run_groundhog_export` call opens at `:146` (literals `:156-157`); `exploration_rng` flagged as Task-4-added, not pre-existing.
