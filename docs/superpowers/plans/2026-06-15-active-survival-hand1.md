# Active Survival (Hand 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make honest-win-rate survival mathematically achievable (recalibrate the breath economy) and add active-survival mechanisms (exploration floor, breath-aware sizing, death-aware learning), validated on a synthetic known-edge harness.

**Architecture:** A new offline analytic survival model + calibration sweep produces breath-economy parameters under which a ~55% (honest) win rate can survive while ~50% (no edge) still dies. Three agent mechanisms then land in the shared decision/learning code: an exploration floor in `decision.py`, breath-aware down-sizing (A14) gated on a now-wired `desperate` flag, and a death-aware credit step (A2) in `weight_updater.py`. A synthetic known-edge generator + harness proves the agent finds/exploits a controlled edge instead of freezing.

**Tech Stack:** Python 3.14, pytest, numpy (already a dep), the existing `agent/backtest/` groundhog runtime (`reincarnation.py` → `survival_season.py` → `sandbox_phase2_loop.py` → `decision.py`/`weight_updater.py`). No LLM (numerical provider) for all calibration + mechanism tests.

**Spec:** `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md`

**Branch:** `active-survival-hand1` (already created). One commit per task. Confirm git identity is `balflee` before each commit.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `agent/backtest/survival_model.py` | Create | Pure analytic breath-walk model: drift, break-even win rate, expected lifetime, survival probability. No IO. |
| `tests/agent/backtest/test_survival_model.py` | Create | Unit tests for the analytic model. |
| `agent/backtest/calibrate_breath_economy.py` | Create | Offline sweep over breath params using `survival_model`; dual-criterion selector; writes recommended params JSON. |
| `tests/agent/backtest/test_calibrate_breath_economy.py` | Create | Selector picks params satisfying both criteria. |
| `agent/backtest/synthetic_edge.py` | Create | Deterministic generator of `SurvivalRow`s with an injected known edge `e`. |
| `tests/agent/backtest/test_synthetic_edge.py` | Create | Injected edge reproduces target win rate; e=0 ⇒ ~50%. |
| `agent/engines/decision.py` | Modify | Add `exploration_epsilon` + seeded RNG ctor params; ε-greedy floor at the abstain gates (B). A14 down-sizing constant/logic (C). |
| `tests/agent/engines/test_decision_exploration.py` | Create | ε=0 regression; ε>0 abstain→micro-bet at rate≈ε; explored size respects caps. |
| `tests/agent/engines/test_decision_a14.py` | Create | `desperate=True` ⇒ size < normal (down-size, not up). |
| `agent/backtest/survival_season.py` | Modify | Thread RNG seed + exploration_epsilon into `_make_decision_engine`; compute `desperate` (breath<threshold) and pass to `decide`. |
| `agent/engines/weight_updater.py` | Modify | Add `update_from_death` (A2), parallel to `update_from_settlement`. |
| `tests/agent/engines/test_weight_updater_death.py` | Create | Death gradient is non-zero, correctly signed, penalizes the death-window engines. |
| `agent/runtime/sandbox_phase2_loop.py` | Modify | Call `update_from_death` at the death check (:1891) before `_die`. |
| `agent/backtest/reincarnation.py` | Modify | Plumb recalibrated `loss_multiplier`/tithe/`initial_breath` + exploration/desperate params through `run_groundhog_export`. |
| `scripts/run_reincarnation.py` | Modify | CLI flags for the new params + `--synthetic-edge E`. |
| `scripts/run_active_survival_validation.py` | Create | Validation harness: synthetic-edge sweep + g1/g2 regression → report artifact. |
| `tests/agent/runtime/test_active_survival_integration.py` | Create | Short end-to-end run; schema + tithe self-check unbroken; success criteria smoke. |

---

## Task 1: Analytic breath-walk survival model (spec §3.A core)

**Files:**
- Create: `agent/backtest/survival_model.py`
- Test: `tests/agent/backtest/test_survival_model.py`

**Why:** The calibration (Task 2) needs a closed-form model of how breath drifts so we can pick parameters analytically (cheap, no full sim) where honest win rate survives and no-edge dies. Model one life as a random walk on breath with an absorbing barrier at 0.

Model (per market seen):
- With probability `bet_rate` the agent bets; conditional on betting, breath change is `+b` on win (prob `p`) and `-m*b` on loss (prob `1-p`), where `b` = breath units per bet (`bet_breath_unit`) and `m` = `loss_multiplier`.
- Every market seen also drains the tithe: amortized `tithe_breath_cost / tithe_every` breath per market.
- `drift = bet_rate * b * (p - (1-p)*m) - tithe_breath_cost/tithe_every`
- `break_even_winrate(m) = m / (1 + m)` (where bet EV on breath is zero, tithe ignored).
- Expected lifetime (markets) ≈ `initial_breath / -drift` when `drift < 0`, else `inf` (survives the horizon).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/backtest/test_survival_model.py
import math
import pytest
from agent.backtest.survival_model import (
    break_even_winrate, breath_drift_per_market, expected_lifetime_markets,
)

def test_break_even_winrate_matches_loss_multiplier_ratio():
    assert break_even_winrate(5.0) == pytest.approx(5.0 / 6.0)   # 0.8333
    assert break_even_winrate(1.0) == pytest.approx(0.5)
    assert break_even_winrate(1.2) == pytest.approx(1.2 / 2.2)   # ~0.545

def test_drift_zero_at_break_even_no_tithe():
    m = 5.0
    p = break_even_winrate(m)
    d = breath_drift_per_market(
        p=p, loss_multiplier=m, bet_rate=1.0, bet_breath_unit=1.0,
        tithe_breath_cost=0.0, tithe_every=20,
    )
    assert d == pytest.approx(0.0, abs=1e-9)

def test_tithe_makes_drift_negative_even_at_bet_break_even():
    m = 5.0
    p = break_even_winrate(m)
    d = breath_drift_per_market(
        p=p, loss_multiplier=m, bet_rate=1.0, bet_breath_unit=1.0,
        tithe_breath_cost=5.0, tithe_every=20,
    )
    assert d < 0.0  # rent pushes survival bar above the bet break-even

def test_expected_lifetime_finite_when_drift_negative_infinite_when_positive():
    neg = breath_drift_per_market(p=0.50, loss_multiplier=1.2, bet_rate=0.5,
                                  bet_breath_unit=1.0, tithe_breath_cost=5.0, tithe_every=20)
    pos = breath_drift_per_market(p=0.60, loss_multiplier=1.2, bet_rate=0.5,
                                  bet_breath_unit=1.0, tithe_breath_cost=0.0, tithe_every=20)
    assert math.isfinite(expected_lifetime_markets(35.0, neg))
    assert expected_lifetime_markets(35.0, pos) == math.inf
```

- [ ] **Step 2: Run, verify fail** — `python -m pytest tests/agent/backtest/test_survival_model.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# agent/backtest/survival_model.py
"""Closed-form breath-walk survival model (Hand 1 / spec §3.A).

One life = a random walk on BREATH with an absorbing barrier at 0. Used by
the offline calibration to pick a breath economy where an honest win rate
survives while a no-edge agent still dies. Pure math, no IO, no LLM.
"""
from __future__ import annotations
import math

__all__ = ["break_even_winrate", "breath_drift_per_market", "expected_lifetime_markets"]


def break_even_winrate(loss_multiplier: float) -> float:
    """Win rate at which a bet's expected BREATH change is zero (tithe ignored).

    p*b == (1-p)*m*b  =>  p = m / (1 + m).
    """
    if loss_multiplier < 0.0:
        raise ValueError(f"loss_multiplier must be >= 0 (got {loss_multiplier})")
    return loss_multiplier / (1.0 + loss_multiplier)


def breath_drift_per_market(
    *, p: float, loss_multiplier: float, bet_rate: float,
    bet_breath_unit: float, tithe_breath_cost: float, tithe_every: int,
) -> float:
    """Expected BREATH change per market seen.

    drift = bet_rate * b * (p - (1-p)*m) - tithe_breath_cost/tithe_every
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p must be in [0, 1] (got {p})")
    if tithe_every <= 0:
        raise ValueError(f"tithe_every must be > 0 (got {tithe_every})")
    bet_term = bet_rate * bet_breath_unit * (p - (1.0 - p) * loss_multiplier)
    tithe_term = tithe_breath_cost / tithe_every
    return bet_term - tithe_term


def expected_lifetime_markets(initial_breath: float, drift_per_market: float) -> float:
    """Markets survived ≈ breath0 / -drift (finite iff drift < 0)."""
    if initial_breath < 0.0:
        raise ValueError(f"initial_breath must be >= 0 (got {initial_breath})")
    if drift_per_market >= 0.0:
        return math.inf
    return initial_breath / (-drift_per_market)
```

- [ ] **Step 4: Run, verify pass** — `python -m pytest tests/agent/backtest/test_survival_model.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add agent/backtest/survival_model.py tests/agent/backtest/test_survival_model.py
git commit -m "feat(survival): analytic breath-walk model for calibration"
```

---

## Task 2: Breath-economy calibration sweep (spec §3.A)

**Files:**
- Create: `agent/backtest/calibrate_breath_economy.py`
- Test: `tests/agent/backtest/test_calibrate_breath_economy.py`

**Why:** Sweep `loss_multiplier` (and optionally tithe) using Task 1's model; pick params satisfying the **dual criterion**: at honest `p_live` (default 0.55) expected lifetime ≥ `target_markets`, AND at no-edge `p_dead` (default 0.50) expected lifetime ≤ `dead_markets` (permadeath stakes preserved). Objective is survivability, **never PnL**.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/backtest/test_calibrate_breath_economy.py
from agent.backtest.calibrate_breath_economy import calibrate, CalibrationResult

def test_selector_satisfies_dual_criterion():
    res = calibrate(
        p_live=0.55, p_dead=0.50,
        target_markets=300.0, dead_markets=200.0,
        initial_breath=35.0, bet_rate=0.5, bet_breath_unit=1.0,
        tithe_breath_cost=5.0, tithe_every=20,
        loss_multiplier_grid=[round(1.0 + 0.1 * i, 2) for i in range(0, 41)],  # 1.0..5.0
    )
    assert isinstance(res, CalibrationResult)
    # honest p survives the target horizon
    assert res.lifetime_at_p_live >= 300.0
    # no-edge p still dies inside dead_markets
    assert res.lifetime_at_p_dead <= 200.0
    # recommended multiplier is the LARGEST (highest stakes) that still passes
    assert 1.0 <= res.loss_multiplier <= 5.0
    assert len(res.tradeoff_curve) == 41

def test_no_feasible_point_raises():
    import pytest
    with pytest.raises(ValueError):
        calibrate(
            p_live=0.50, p_dead=0.50, target_markets=1e9, dead_markets=1.0,
            initial_breath=35.0, bet_rate=0.5, bet_breath_unit=1.0,
            tithe_breath_cost=5.0, tithe_every=20,
            loss_multiplier_grid=[1.0, 2.0, 3.0],
        )
```

- [ ] **Step 2: Run, verify fail** — `python -m pytest tests/agent/backtest/test_calibrate_breath_economy.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# agent/backtest/calibrate_breath_economy.py
"""Offline breath-economy calibration (Hand 1 / spec §3.A).

Picks breath params where an honest win rate survives a target horizon while
a no-edge win rate still dies. Survivability objective ONLY (never PnL).
Run: python -m agent.backtest.calibrate_breath_economy [--out PATH]
"""
from __future__ import annotations
import argparse, dataclasses, json
from pathlib import Path
from agent.backtest.survival_model import (
    breath_drift_per_market, expected_lifetime_markets, break_even_winrate,
)


@dataclasses.dataclass(frozen=True)
class CalibrationResult:
    loss_multiplier: float
    lifetime_at_p_live: float
    lifetime_at_p_dead: float
    break_even_winrate: float
    tradeoff_curve: list[dict[str, float]]


def _lifetime(p, m, *, initial_breath, bet_rate, bet_breath_unit,
              tithe_breath_cost, tithe_every) -> float:
    d = breath_drift_per_market(
        p=p, loss_multiplier=m, bet_rate=bet_rate, bet_breath_unit=bet_breath_unit,
        tithe_breath_cost=tithe_breath_cost, tithe_every=tithe_every,
    )
    return expected_lifetime_markets(initial_breath, d)


def calibrate(*, p_live, p_dead, target_markets, dead_markets, initial_breath,
              bet_rate, bet_breath_unit, tithe_breath_cost, tithe_every,
              loss_multiplier_grid) -> CalibrationResult:
    curve, feasible = [], []
    for m in loss_multiplier_grid:
        live = _lifetime(p_live, m, initial_breath=initial_breath, bet_rate=bet_rate,
                         bet_breath_unit=bet_breath_unit, tithe_breath_cost=tithe_breath_cost,
                         tithe_every=tithe_every)
        dead = _lifetime(p_dead, m, initial_breath=initial_breath, bet_rate=bet_rate,
                         bet_breath_unit=bet_breath_unit, tithe_breath_cost=tithe_breath_cost,
                         tithe_every=tithe_every)
        curve.append({"loss_multiplier": m, "lifetime_at_p_live": live,
                      "lifetime_at_p_dead": dead})
        if live >= target_markets and dead <= dead_markets:
            feasible.append((m, live, dead))
    if not feasible:
        raise ValueError(
            "no breath economy satisfies the dual criterion "
            f"(p_live>={target_markets} markets AND p_dead<={dead_markets})"
        )
    # Highest multiplier that still passes = highest stakes while honest-survivable.
    m, live, dead = max(feasible, key=lambda t: t[0])
    return CalibrationResult(
        loss_multiplier=m, lifetime_at_p_live=live, lifetime_at_p_dead=dead,
        break_even_winrate=break_even_winrate(m), tradeoff_curve=curve,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/calibration/breath_economy_hand1.json")
    ap.add_argument("--p-live", type=float, default=0.55)
    ap.add_argument("--p-dead", type=float, default=0.50)
    ap.add_argument("--target-markets", type=float, default=300.0)
    ap.add_argument("--dead-markets", type=float, default=200.0)
    ap.add_argument("--initial-breath", type=float, default=35.0)
    ap.add_argument("--bet-rate", type=float, default=0.5)
    ap.add_argument("--bet-breath-unit", type=float, default=1.0)
    ap.add_argument("--tithe-breath-cost", type=float, default=5.0)
    ap.add_argument("--tithe-every", type=int, default=20)
    a = ap.parse_args()
    grid = [round(1.0 + 0.05 * i, 2) for i in range(0, 81)]  # 1.0..5.0 step .05
    res = calibrate(
        p_live=a.p_live, p_dead=a.p_dead, target_markets=a.target_markets,
        dead_markets=a.dead_markets, initial_breath=a.initial_breath,
        bet_rate=a.bet_rate, bet_breath_unit=a.bet_breath_unit,
        tithe_breath_cost=a.tithe_breath_cost, tithe_every=a.tithe_every,
        loss_multiplier_grid=grid,
    )
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(res), indent=2), encoding="utf-8")
    print(f"recommended loss_multiplier={res.loss_multiplier} "
          f"(break-even winrate {res.break_even_winrate:.3f}); wrote {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify pass** — `python -m pytest tests/agent/backtest/test_calibrate_breath_economy.py -v` → PASS. Then run the CLI once: `python -m agent.backtest.calibrate_breath_economy` and confirm it prints a recommended multiplier (expected ~1.2 region) and writes the JSON.

- [ ] **Step 5: Commit**
```bash
git add agent/backtest/calibrate_breath_economy.py tests/agent/backtest/test_calibrate_breath_economy.py reports/calibration/breath_economy_hand1.json
git commit -m "feat(survival): breath-economy calibration sweep (dual-criterion)"
```

---

## Task 3: Synthetic known-edge data generator (spec §3.E foundation)

**Files:**
- Create: `agent/backtest/synthetic_edge.py`
- Test: `tests/agent/backtest/test_synthetic_edge.py`

**Why:** g2 is zero-edge by design (freezing is correct there) so it can't prove the exploration floor's value. We need worlds with a KNOWN edge `e`: markets where the true outcome probability is offset from the market price by `e`, so a correct model wins at a controllable rate. The generator emits the same `SurvivalRow` schema the groundhog loop consumes (see `agent/backtest/survival_season.py::build_survival_rows`).

- [ ] **Step 1:** Read `agent/backtest/survival_season.py` `SurvivalRow` definition first, then write the failing test asserting: (a) with `edge=0.0` a price-following model wins ≈50%; (b) with `edge=0.10` it wins ≈ `0.5 + scaled(e)`; (c) generation is deterministic for a fixed seed.

```python
# tests/agent/backtest/test_synthetic_edge.py
from agent.backtest.synthetic_edge import generate_synthetic_rows, realized_winrate

def test_zero_edge_is_coinflip():
    rows = generate_synthetic_rows(n=4000, edge=0.0, seed=7)
    assert abs(realized_winrate(rows) - 0.5) < 0.03

def test_positive_edge_lifts_winrate():
    rows = generate_synthetic_rows(n=4000, edge=0.10, seed=7)
    assert realized_winrate(rows) > 0.55

def test_deterministic_for_seed():
    a = generate_synthetic_rows(n=500, edge=0.08, seed=3)
    b = generate_synthetic_rows(n=500, edge=0.08, seed=3)
    assert [r.market_id for r in a] == [r.market_id for r in b]
    assert [r.outcome for r in a] == [r.outcome for r in b]
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `generate_synthetic_rows(n, edge, seed)` building `SurvivalRow`s with `entry_price` drawn from a realistic distribution and `outcome` sampled so `P(YES correct) = clip(price + edge_signal, 0, 1)`; expose a per-row truth signal the engine can pick up via the existing signal fields. `realized_winrate` = fraction of rows where betting the edge direction wins. Use `random.Random(seed)` only (deterministic).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** `feat(survival): synthetic known-edge row generator`

---

## Task 4: Exploration floor in the decision engine (spec §3.B)

**Files:**
- Modify: `agent/engines/decision.py` (ctor :182-237; decide gates :380-415)
- Modify: `agent/backtest/survival_season.py` (`_make_decision_engine` ~:1519-1547)
- Test: `tests/agent/engines/test_decision_exploration.py`

**Design decision (resolves spec §3.B open question):** `exploration_epsilon` is a **ctor param with a hard floor, NOT registered in `GENOME_KEYS`** — i.e. NOT advisor-adjustable, mirroring the deliberately-non-advisable `min_bet_size_usd`. Rationale: the whole point is to stop the advisor from driving the agent into a no-bet freeze; an advisable epsilon could be pushed to 0, reintroducing the failure. Seed its value from Task 2 calibration.

Mechanism: when a gate would abstain (low confidence / below min-edge), with probability ε take a **minimum-size exploratory bet** in the fused direction instead, sized at exactly `min_bet_size_usd` (clamped by liquidity/bankroll/breath caps). RNG is a `random.Random` passed in (seeded) so the deterministic harness stays reproducible.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agent/engines/test_decision_exploration.py
import random, asyncio
from agent.engines.decision import DecisionEngine
# ... build minimal signals/weights fixtures that would normally ABSTAIN
# (fused ~0 / edge below min) at price=0.5 ...

def _decide(engine, **kw):
    return asyncio.run(engine.decide(**kw))

def test_epsilon_zero_is_byte_identical_regression(abstaining_kwargs):
    eng = DecisionEngine(exploration_epsilon=0.0, rng=random.Random(0))
    act = _decide(eng, **abstaining_kwargs)
    assert act.kind.name == "NO_BET"   # unchanged behavior

def test_epsilon_forces_minbet_at_rate(abstaining_kwargs):
    eng = DecisionEngine(exploration_epsilon=0.2, rng=random.Random(0))
    bets = sum(_decide(eng, **abstaining_kwargs).kind.name == "BET" for _ in range(2000))
    assert 0.15 < bets / 2000 < 0.25     # ≈ ε

def test_explored_bet_respects_min_size_and_caps(abstaining_kwargs):
    eng = DecisionEngine(exploration_epsilon=1.0, rng=random.Random(1),
                         min_bet_size_usd=5.0)
    act = _decide(eng, **{**abstaining_kwargs, "liquidity_cap_usd": 1000.0})
    assert act.kind.name == "BET" and act.size_usd >= 5.0
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement**
  - Add to `__init__` (after :194): `exploration_epsilon: float = 0.0` and `rng: random.Random | None = None`; validate `0.0 <= exploration_epsilon <= 1.0`; store `self._exploration_epsilon`, `self._rng = rng or random.Random(0)`. Add `import random` at top.
  - At each abstain gate that currently `return Action(NO_BET ...)` for low-confidence (:317-322), neutral/edge (:351-355, :380-384), and min-size (:411-415): before returning, attempt `self._maybe_explore(...)`. Add a private helper that, with prob ε and a resolvable side (fused sign; default YES on exactly-neutral), returns a `min_bet_size_usd` BET clamped by `breath_cap`/`bankroll_cap`/`liquidity_cap`; else `None`. Only override the abstain when the helper returns a BET.
  - Keep the explored size ≥ `min_bet_size_usd` only if caps allow; if caps force it below, stay NO_BET (don't violate the floor).
- [ ] **Step 4:** Thread params in `survival_season.py::_make_decision_engine`: add `exploration_epsilon` (from seed/config) and a seeded `random.Random(season_seed)` to the `DecisionEngine(...)` construction. Default 0.0 keeps every existing test green.
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/test_decision_exploration.py tests/agent/engines/test_decision.py -v` → all PASS (new + regression).
- [ ] **Step 6: Commit** `feat(decision): exploration floor (non-advisable epsilon, seeded RNG)`

---

## Task 5: A14 — wire `desperate` in backtest + invert near-death sizing (spec §3.C)

**Files:**
- Modify: `agent/engines/decision.py` (:399-402)
- Modify: `agent/backtest/survival_season.py` / the tick caller that invokes `decide`
- Test: `tests/agent/engines/test_decision_a14.py`

**Prerequisite (verified):** in the backtest path `desperate` is hard-False (`sandbox_phase2_loop.py:1033`, passed at :1734). So first WIRE it, then INVERT the cap.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/engines/test_decision_a14.py
import asyncio
from agent.engines.decision import DecisionEngine

def test_desperate_downsizes_not_upsizes(betting_kwargs):
    eng = DecisionEngine()
    normal = asyncio.run(eng.decide(**{**betting_kwargs, "desperate": False}))
    desp = asyncio.run(eng.decide(**{**betting_kwargs, "desperate": True}))
    assert normal.kind.name == "BET" and desp.kind.name == "BET"
    assert desp.size_usd < normal.size_usd      # near death → SMALLER, not larger
```

- [ ] **Step 2: Run, verify fail** (today desperate UP-sizes → `desp.size_usd > normal.size_usd`, so this fails).
- [ ] **Step 3: Implement (invert):** replace the `DESPERATE_BET_SIZE_CAP` use at :399-402 with a down-sizing cap. Add constant `DESPERATE_BET_SIZE_CAP_DOWN: Final[float] = 0.10` and use it when `desperate`. Update the constant's doc comment (:103-107) and the now-stale test `tests/agent/engines/test_decision.py::test_desperate_mode_loosens_bankroll_cap` to assert down-sizing (rename to `test_desperate_mode_tightens_bankroll_cap`).
- [ ] **Step 4: Wire desperate in backtest:** in the groundhog tick caller (the code that builds the `decide(...)` kwargs in the backtest path — `sandbox_phase2_loop.py` step 4 ~:1719-1744, or its `survival_season` injection), set `desperate = self._breath < self._desperate_threshold`. Add `desperate_threshold` as a constructor/config value (default from calibration; e.g. 0.3×initial_breath). Confirm live-path behavior is unchanged when the flag is driven by the same rule.
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/test_decision_a14.py tests/agent/engines/test_decision.py tests/agent/engines/test_weight_updater_desperate.py -v` → PASS (fix any other desperate-cap assertions).
- [ ] **Step 6: Commit** `feat(decision): A14 breath-aware down-sizing + wire desperate in backtest`

---

## Task 6: A2 — death-aware credit assignment (spec §3.D)

**Files:**
- Modify: `agent/engines/weight_updater.py` (new method near :349-425)
- Modify: `agent/runtime/sandbox_phase2_loop.py` (:1891-1897 death check)
- Test: `tests/agent/engines/test_weight_updater_death.py`

**Why:** death currently emits zero gradient (`_die` only hashes weights). Add `update_from_death`: a negative credit step against the engines whose high-confidence signals drove the final losing window, mirroring `update_from_settlement`'s direction-aware credit but with a fixed loss sign.

- [ ] **Step 1: Write the failing test**

```python
# tests/agent/engines/test_weight_updater_death.py
import asyncio
from agent.engines.weight_updater import WeightUpdater
# build a Weights `current` + a death_window_scores dict where one engine
# had a strong signal driving the losing bets ...

def test_death_emits_nonzero_negative_gradient(current_weights, death_scores):
    up = WeightUpdater(...)
    new = asyncio.run(up.update_from_death(
        current=current_weights, phase=current_weights_phase,
        death_window_scores=death_scores, bet_direction=1.0,
        breath_drained=35.0,
    ))
    assert new != current_weights                      # death now changes weights
    # the engine that drove the losing window is penalized (its alpha share drops)
    assert _alpha_share(new, "market_momentum") < _alpha_share(current_weights, "market_momentum")
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `update_from_death` parallel to `update_from_settlement` (:349): build `features[f"{engine}_quality"] = -1.0 * bet_direction * score` (fixed loss sign), aggregate stream qualities the same way, set a negative `rho_quality` (cut risk), scale magnitude by `breath_drained` normalized; delegate to `self.update(current=..., phase=..., features=..., desperate=False)`. Document the look-ahead stance (death is a terminal channel, not a `features/` decision input).
- [ ] **Step 4: Wire the call:** in `sandbox_phase2_loop.py` at the death branch (:1891, before `_attempt_tribute`/`_die`), if a weight-updater + recent death-window scores are available, `await self._weight_updater.update_from_death(...)` and persist the new weights to the carry so the next incarnation starts from the death-corrected weights. Guard so live-path default (no death scores) is a no-op.
- [ ] **Step 5: Run** `python -m pytest tests/agent/engines/test_weight_updater_death.py tests/agent/engines/test_weight_updater.py -v` → PASS.
- [ ] **Step 6: Commit** `feat(learning): A2 death-aware credit assignment`

---

## Task 7: Plumb recalibrated params through the run harness (spec §3.A integration)

**Files:**
- Modify: `agent/backtest/reincarnation.py` (`run_groundhog_export` :1342-1370 params)
- Modify: `scripts/run_reincarnation.py` (CLI :91+)
- Test: extend `tests/agent/runtime/test_active_survival_integration.py` (Task 8 file) or a focused config test.

- [ ] **Step 1:** Write a failing test that calls `run_groundhog_export(..., loss_multiplier=1.2, exploration_epsilon=0.1, desperate_threshold=10.5, ...)` on a tiny synthetic dataset and asserts: the artifact's `knobs`/`physics` reflect the new params; the tithe accounting self-check (`reincarnation.py:2002-2034`) passes; the run completes with `provider="numerical"`.
- [ ] **Step 2: Run, verify fail** (params not yet accepted).
- [ ] **Step 3:** Add the params to `run_groundhog_export` signature with defaults = today's values (so existing arms are byte-identical), thread `exploration_epsilon`/`desperate_threshold` into the `DecisionEngine` build and `loss_multiplier` into the recorder; record them in the artifact `knobs`.
- [ ] **Step 4:** Add CLI flags to `run_reincarnation.py`: `--loss-multiplier`, `--exploration-epsilon`, `--desperate-threshold`, `--synthetic-edge` (float, optional; when set, source rows from Task 3 instead of the cache). Load defaults from `reports/calibration/breath_economy_hand1.json` if present.
- [ ] **Step 5: Run** the focused test → PASS; run one tiny numerical season with new flags to smoke it.
- [ ] **Step 6: Commit** `feat(reincarnation): plumb recalibrated breath economy + active params + synthetic-edge`

---

## Task 8: Validation harness — synthetic-edge sweep + g1/g2 regression (spec §3.E, §2)

**Files:**
- Create: `scripts/run_active_survival_validation.py`
- Test: `tests/agent/runtime/test_active_survival_integration.py`

- [ ] **Step 1:** Write the integration test (short, numerical, tiny n): run the active agent (recalibrated params + exploration floor + A14 + A2) on (a) `edge=0.10` synthetic world, (b) `edge=0.0` synthetic world. Assert the spec §2 criteria as smoke checks:
  - bet-rate at edge=0.10 is **> 0** and materially above the old freeze (criterion 2);
  - lifetime at edge=0.10 **>** lifetime at edge=0.0 (criterion 3 — the agent exploits the real edge);
  - edge=0.0 world still produces deaths (criterion 1 — stakes preserved);
  - artifact schema + tithe self-check unbroken.

```python
# tests/agent/runtime/test_active_survival_integration.py (sketch)
def test_active_agent_exploits_known_edge_and_dies_without_one(tmp_path):
    hi = run_validation(edge=0.10, seed=0, n=600, out=tmp_path/"hi.json")
    lo = run_validation(edge=0.00, seed=0, n=600, out=tmp_path/"lo.json")
    assert hi.total_bets > 0
    assert hi.mean_lifetime_markets > lo.mean_lifetime_markets
    assert lo.deaths >= 1
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3:** Implement `scripts/run_active_survival_validation.py`: a thin orchestrator that runs the synthetic-edge worlds (varying `e`) plus re-runs g1 (real cache) and g2 (`--shuffle-timestamps-seed 1`) with the active params, collects survival metrics (bets, deaths/life, longest survival, near-death rebounds, vault vs seed), and writes `reports/validation/active_survival_hand1.md` + JSON. Expose a `run_validation(edge, seed, n, out)` helper for the test.
- [ ] **Step 4: Run** `python -m pytest tests/agent/runtime/test_active_survival_integration.py -v` → PASS. Then run the full harness once and read the report.
- [ ] **Step 5: Commit** `feat(validation): synthetic-edge + g1/g2 active-survival harness`

---

## Final verification (before finishing the branch)

- [ ] Run the full suite: `python -m pytest -q` → green (fix any regressions from the desperate-cap inversion).
- [ ] Run `python -m agent.backtest.calibrate_breath_economy` and record the recommended params in the validation report.
- [ ] Run `python scripts/run_active_survival_validation.py` and confirm the spec §2 criteria hold on the real (non-toy) sizes.
- [ ] Confirm no `reincarnation.py` tithe/accounting self-check (:2002-2034) regressions and no look-ahead auditor failures.
- [ ] Confirm git identity `balflee` for every commit.

## Notes for the executor
- **Provider:** everything here uses `provider="numerical"` — NO Gemini/MiniMax, NO API keys.
- **Determinism:** all new randomness goes through a seeded `random.Random`; never use unseeded `random`/`numpy.random` global state (the harness must stay reproducible).
- **Scope wall:** do NOT touch `sim/` (legacy runtime) or build any live/mock/observation-station code (Hand 2).
- **YAGNI:** if Task 2's calibration shows a single scalar (`loss_multiplier`) suffices to hit the dual criterion, do NOT also sweep tithe/breath0 — keep the recommendation minimal.
