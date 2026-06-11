# Signal-Cached Config Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) tracking. TDD throughout.

**Goal:** Replace Plan 1 Task D3's replay-per-config sweep (infeasible at 7494-cassette scale: only `tick % len` → first 240 markets evaluated, ~333 s/config → ~9 h for 96 configs, signals recomputed per config) with a **signal-cached sweep**: precompute the REAL 5-slot signals + settlement facts for every resolvable cassette ONCE, then sweep all configs in-memory over the faithful `DecisionEngine.decide` fusion+sizing and the faithful settlement PnL formula. Full coverage (~4900 resolved markets), seconds per config.

**Why this is correct & faithful:** Signals are config-INDEPENDENT (only fusion weights + sizing knobs vary per config), so computing them per config is pure waste. The sweep reuses the SAME `DecisionEngine.decide` (real fusion + 4-constraint sizing) and the SAME per-bet PnL formula as the production settlement poller — it is not a re-derivation.

**Tech Stack:** Python 3.11+ (runs 3.14), pydantic v2, pytest, mypy --strict, ruff. Reuses `agent/backtest/real_signal_source.py` (RealSignalSource), `agent/backtest/tennis_match_resolver.py`, `agent/backtest/historical_fetcher.py` (load_all_cached_markets, MarketSnapshotProvider, MarketSnapshot), `agent/engines/decision.py` (DecisionEngine), `agent/backtest/find_optimal_config.py` (StrategyConfig, generate_lhs_strategy_configs), `data/sources/tennis_sackmann.py` (SackmannLoader, DEFAULT_CORPUS_DIR).

---

## Grounded facts (verified against real code 2026-06-09 — do NOT re-derive, but DO confirm the cited signatures still match)

- **`DecisionEngine.decide`** (`agent/engines/decision.py:181`) is `async`, keyword-only:
  `decide(*, signals: dict[str, EngineSignal], weights_alpha: tuple[float,float,float], weights_beta: tuple[float,float], w_r: float, w_s: float, rho: float, bankroll_usd: float, breath: float, liquidity_cap_usd: float, market_id: str, desperate: bool=False) -> Action`. Returns `Action` with `.kind` (`ActionKind.BET`/`ActionKind.NO_BET`), and for BET: `.side` (`Side.YES`/`Side.NO`), `.size_usd`, `.edge_pct`.
- **`EngineSignal`** is the same type the engines emit (`agent/engines/base.py` `Signal`: pydantic BaseModel, fields `score` ge=-1 le=1, `confidence` ge=0 le=1, `available_at: str`, `rationale: str`, `raw_features: dict[str,float]`). Only `score`+`confidence` feed fusion; the other fields are inert in `decide`. `RealSignalSource.signals_for(*, market_id, tick, asof_ts) -> dict[str, Signal]` returns the 5 slots keyed by the `decision.py` constants.
- **Sizing** (`decision.py:248-268`): `desired = rho_eff*kelly*mean_confidence*bankroll_usd`; `size = min(desired, breath_cap, bankroll_cap, liquidity_cap)` where `bankroll_cap = bankroll_usd * NORMAL_BET_SIZE_CAP`. With bankroll **$100** the cap is **$5** (NORMAL_BET_SIZE_CAP=0.05), so **`min_bet_size_usd` must be < 5.0** or every bet is rejected (this is the root cause of the D3 zero-bets). Sweep `min_bet` range MUST be sub-$5.
- **Per-bet PnL** — the faithful production formula `_compute_pnl` (`agent/runtime/sandbox_settlement_poller.py:730-767`):
  - `outcome == "void"` → `0.0`
  - winner (`(side=="YES") == (outcome=="yes")`) → `size_usd * (winning_price / entry_price - 1.0)` (entry_price>0; if entry_price<=0 → `size_usd * winning_price`)
  - loser → `-size_usd`
- **MarketSnapshot** carries `market_id`, `slug`, `price_ledger: list[PricePoint(ts, mid_price)]`, `outcome` (`"yes"/"no"/"void"/None`), `winning_price` (float|None), `liquidity_cap_usd`, `end_date_iso`, `resolution_ts_iso`. `load_all_cached_markets(cache_dir: Path)` returns the list; sort by `market_id` for determinism.
- **Agent bankroll** = `DEFAULT_REPLAY_INITIAL_BANKROLL_USD` (= 100.0), breath = `DEFAULT_REPLAY_INITIAL_BREATH` (= 100.0), both importable from `agent.backtest.replay_runner`.

## Design decisions (locked)

- **Per-market INDEPENDENT decision at a fixed bankroll/breath.** Each market is decided with `bankroll_usd=100`, `breath=100` (the agent's initial state) — NO equity compounding and NO breath depletion across markets. This isolates the signal-quality + sizing question and matches the deferred-compounded-Sharpe caveat already in Plan 1. Document it in the report.
- **Decision point = mid-market.** For each market, the entry asof = the price-ledger snapshot nearest **50% of the ledger's time span** (`first_ts + 0.5*(last_ts-first_ts)`, pick the last point at-or-before it; if only 1 point, use it). `entry_price` = that snapshot's `mid_price`. Signals are computed at that asof (momentum sees the first half; Sackmann facets are PIT-correct at that ts). This is a backtest convention — momentum at market-open is empty and entry at the last tick is near-lookahead, so mid-market balances both. Expose `entry_fraction: float = 0.5` as a parameter and record it in the report; sweeping the decision-time is a future enhancement.
- **Skip markets that cannot score a PnL:** `outcome is None`, `winning_price is None`, empty `price_ledger`, or unresolvable slug → excluded from the sweep universe (counted + reported, not silently dropped).
- **Sharpe = per-bet** `mean(pnl)/stdev(pnl)` across a config's BETs (0.0 if <2 bets or stdev==0). Report alongside `net_pnl`, `win_rate`, `bets`, `avg_size`. Label it "per-bet Sharpe (un-compounded)".

---

## File Structure

| File | Responsibility | Create/Modify |
|---|---|---|
| `agent/backtest/cached_sweep.py` | `SignalRow` dataclass; `precompute_rows(snapshots, resolver, src, *, entry_fraction)`; `row_to_signals(row)`; `compute_bet_pnl(...)`; `score_config(rows, cfg, *, bankroll, breath)`; `run_cached_sweep(rows, configs, ...)`; `save_rows`/`load_rows` (JSON); `__main__` CLI (`--precompute` → build+cache rows; `--sweep` → load rows + LHS configs + ranked print). | Create |
| `tests/agent/backtest/test_cached_sweep.py` | PnL formula parity vs `_compute_pnl`; precompute row shape + mid-market entry; score_config bet/no-bet + PnL; run_cached_sweep ranking; save/load round-trip. | Create |

---

## Task 1: `compute_bet_pnl` — faithful PnL (parity with production)

**Files:** Create `agent/backtest/cached_sweep.py`; Test `tests/agent/backtest/test_cached_sweep.py`

- [ ] **Step 1 (RED):** test parity with the production formula for all branches.

```python
# tests/agent/backtest/test_cached_sweep.py
from __future__ import annotations

from agent.backtest.cached_sweep import compute_bet_pnl


def test_pnl_winner_yes() -> None:
    # entered YES at 0.40, YES wins at 1.0, $10 stake -> 10*(1/0.4 - 1) = 15.0
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="yes", winning_price=1.0) == 15.0


def test_pnl_loser_is_minus_stake() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.40, size_usd=10.0,
                           outcome="no", winning_price=1.0) == -10.0


def test_pnl_no_side_wins_when_outcome_no() -> None:
    assert compute_bet_pnl(side="NO", entry_price=0.25, size_usd=8.0,
                           outcome="no", winning_price=1.0) == 8.0 * (1.0/0.25 - 1.0)


def test_pnl_void_is_zero() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.4, size_usd=10.0,
                           outcome="void", winning_price=1.0) == 0.0


def test_pnl_zero_entry_winner_clips() -> None:
    assert compute_bet_pnl(side="YES", entry_price=0.0, size_usd=10.0,
                           outcome="yes", winning_price=1.0) == 10.0 * 1.0
```

- [ ] **Step 2:** Run → fail (module missing).
- [ ] **Step 3 (GREEN):** implement `compute_bet_pnl(*, side: str, entry_price: float, size_usd: float, outcome: str, winning_price: float) -> float` EXACTLY mirroring `sandbox_settlement_poller._compute_pnl` (void→0; winner `size*(winning_price/entry-1)`, entry<=0→`size*winning_price`; loser→`-size`). Winner = `(side=="YES") == (outcome=="yes")`.
- [ ] **Step 4:** Run → pass. **Step 5:** gates (`ruff`, `mypy --strict agent/backtest/cached_sweep.py`) + commit.

## Task 2: `SignalRow` + `precompute_rows` (mid-market entry, real signals)

**Files:** Modify `agent/backtest/cached_sweep.py`; Test same.

- [ ] **Step 1 (RED):** with a fake provider+resolver returning a known resolvable snapshot (reuse the `_FakeProvider`/`_snap` pattern from `tests/agent/backtest/test_real_signal_source.py`, but give it a `-vs-` slug the injected resolver resolves and a multi-point ledger), assert `precompute_rows([...], resolver, src, entry_fraction=0.5)` returns one `SignalRow` with: the 5 slot scores+confidences populated, `entry_price` == the mid-market snapshot's mid_price, `outcome`/`winning_price` copied from the snap, and that an unresolved/None-outcome snapshot is EXCLUDED.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3 (GREEN):** implement:
  - `@dataclass SignalRow`: `market_id: str`, `slug: str`, `scores: dict[str,float]`, `confidences: dict[str,float]`, `entry_price: float`, `outcome: str`, `winning_price: float`, `liquidity_cap_usd: float`.
  - `_entry_asof(ledger, entry_fraction) -> (datetime, float)`: compute `first_ts + entry_fraction*(last_ts-first_ts)`, return the last `(ts, mid_price)` at-or-before it (fallback: the single/first point).
  - `precompute_rows(snapshots, resolver, src, *, entry_fraction=0.5) -> list[SignalRow]`: for each snap, skip if `outcome is None or winning_price is None or not price_ledger or resolver.resolve(slug) is None`; else compute entry asof+price, `sig = src.signals_for(market_id=snap.market_id, tick=0, asof_ts=asof)`, build a row from `{k: s.score}`/`{k: s.confidence}`. Return rows.
- [ ] **Step 4:** Run → pass. **Step 5:** gates + commit.

## Task 3: `row_to_signals` + `score_config` (real decide + PnL)

**Files:** Modify `agent/backtest/cached_sweep.py`; Test same.

- [ ] **Step 1 (RED):** given a hand-built `SignalRow` whose scores/confidences clear the confidence floor and fuse to a known sign, assert `score_config([row], cfg)` runs `DecisionEngine.decide` and returns metrics with `bets==1` and `net_pnl == compute_bet_pnl(side, entry, size, outcome, winning_price)` for that side/size; and that a low-confidence row yields `bets==0`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3 (GREEN):** implement:
  - `row_to_signals(row) -> dict[str, Signal]`: reconstruct `Signal(score=row.scores[k], confidence=row.confidences[k], available_at="", rationale="", raw_features={})` per slot key.
  - `@dataclass SweepMetrics`: `bets: int`, `net_pnl: float`, `win_rate: float`, `sharpe: float`, `avg_size: float`.
  - `score_config(rows, cfg: StrategyConfig, *, bankroll=DEFAULT_REPLAY_INITIAL_BANKROLL_USD, breath=DEFAULT_REPLAY_INITIAL_BREATH) -> SweepMetrics`: for each row run `await engine.decide(...)` with `cfg.weights` unpacked (`weights_alpha=cfg.weights.alpha`, `weights_beta=cfg.weights.beta`, `w_r`/`w_s`/`rho` from `cfg.weights`, `bankroll_usd=bankroll`, `breath=breath`, `liquidity_cap_usd=row.liquidity_cap_usd`, `market_id=row.market_id`); on BET compute `compute_bet_pnl(side=action.side.value, entry_price=row.entry_price, size_usd=action.size_usd, outcome=row.outcome, winning_price=row.winning_price)`; aggregate. Construct one `DecisionEngine(max_breath_risk_pct=cfg.max_breath_risk_pct, min_bet_size_usd=cfg.min_bet_size_usd, min_confidence=cfg.min_confidence)`. `score_config` is `async` (decide is async); provide a sync `score_config_sync` wrapper via `asyncio.run` for the sweep loop. **Verify `StrategyConfig.weights` field names (`alpha`/`beta`/`w_r`/`w_s`/`rho`) against `find_optimal_config.py` before use.**
  - per-bet Sharpe = `mean(pnls)/pstdev(pnls)` (0.0 if <2 bets or stdev 0).
- [ ] **Step 4:** Run → pass. **Step 5:** gates + commit.

## Task 4: `run_cached_sweep` + save/load + CLI

**Files:** Modify `agent/backtest/cached_sweep.py`; Test same.

- [ ] **Step 1 (RED):** (a) `save_rows(rows, path)` then `load_rows(path)` round-trips equal rows. (b) `run_cached_sweep(rows, [cfgA, cfgB])` returns a list of `(StrategyConfig, SweepMetrics)` in input order, each with finite sharpe.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3 (GREEN):** implement `save_rows`/`load_rows` (JSON; serialize the dataclass via `dataclasses.asdict`, reload into `SignalRow`); `run_cached_sweep(rows, configs, *, bankroll, breath) -> list[tuple[StrategyConfig, SweepMetrics]]` (calls `score_config_sync` per config, input order). Add a `__main__` CLI:
  - `python -m agent.backtest.cached_sweep precompute --cache-dir <dir> --out <rows.json> [--entry-fraction 0.5]` → load markets, build resolver from `SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)`, `RealSignalSource`, `precompute_rows`, `save_rows`, print `wrote N rows (resolved N / total M)`.
  - `python -m agent.backtest.cached_sweep sweep --rows <rows.json> --n 96 --seed 0` → `load_rows`, `generate_lhs_strategy_configs(n, seed)`, `run_cached_sweep`, sort by sharpe desc, print the Top-15 table + the OPTIMAL block (weights + sizing + sharpe/pnl/win%/bets). **Override the LHS `min_bet_size_usd` range to sub-$5 (e.g. [0.5, 4.0]) — at $100 bankroll the bankroll_cap is $5, so any min_bet ≥ 5 can never bet. If `generate_lhs_strategy_configs` already samples sub-$5, reuse it; otherwise post-clamp `cfg.min_bet_size_usd = min(cfg.min_bet_size_usd, 4.0)` and note it.**
  - `main(argv) -> int` returns an exit code; keep CLI UTF-8 safe.
- [ ] **Step 4:** Run → pass. **Step 5:** gates + commit.

## Verification (controller runs after the build)

1. `python -m agent.backtest.cached_sweep precompute --cache-dir agent/backtest/_cache_tennis --out reports/backtest/_signal_rows.json` (background; ~80 min — precomputes ~4900 rows ONCE).
2. `python -m agent.backtest.cached_sweep sweep --rows reports/backtest/_signal_rows.json --n 96 --seed 0` (seconds).
3. Write `reports/backtest/real_signal_sweep.md`: optimal config (α/β/w/ρ + sizing), Top-15 table, resolved coverage %, entry-fraction + bankroll/breath/no-compounding caveats, the $5 bankroll-cap note. Commit the report + the module (NOT `_signal_rows.json` — gitignore it or keep it out).

## Self-Review
- PnL formula is a byte-faithful mirror of `_compute_pnl` (Task 1 asserts parity). ✓
- decide + sizing reused, not re-derived (Task 3). ✓
- `min_bet` sub-$5 constraint honored (Task 4). ✓
- Coverage = ALL resolved markets (not 240); per-config cost is fusion-only (signals cached). ✓
- Caveats (mid-market entry, no compounding, per-bet Sharpe) recorded in the report. ✓
