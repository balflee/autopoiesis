# Groundhog Reincarnation (Phase 2, design v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TRUE reincarnation — one incarnation = ONE life from the season's FIRST market; death sends the agent back to market #1 carrying experience (weights + EMA + a death-context rebirth retrospective in the AI leg) but never outcomes; the loop runs until one life survives to the season's final bet (or an incarnation cap); a DEAD incarnation's profit is SCORED ZERO (the user's permadeath-economics rule: 死了归零，钱带不进棺材); then the frozen cold-start holdout walk. The /reincarnation page is rebuilt around this design.

**Architecture:** `run_groundhog_export` in `agent/backtest/reincarnation.py` loops single-life seasons (`run_survival_season(max_lives=1, shared_inner=carried)`) over the SAME 70% train window; the AI leg fires the strict advisor at EVERY death with a death-context window (death summary delivered through `PerformanceWindow.recent_reflections` — an existing field, schema-untouched); the artifact (schema v2, `design: "groundhog_day"`) keeps the `reincarnation.json` filenames; the dashboard shell is rebuilt (survival-frontier staircase + scored-zero economics + design-history note).

**Tech Stack:** Python 3.11 (pytest, mypy --strict, ruff), v3 physics throughout, Next.js dashboard (vitest), Vercel CLI deploy.

**User directives (verbatim-equivalent, locked):**
1. 真正轮回 = 开始bet → 中途归零死亡 → 轮回到最初bet → 不断loop一直存活到最终bet（一世死 6 次是错的——那是 6 世）。
2. 轮回次数不固定为 3：loop 直到通关，配上限（太久怕跑不完）。
3. PnL 以当世死亡前为准，死了归零没有 profit —— headline 只认活到终点那一世的 PnL；死掉的轮回 scored_pnl = 0（raw at-death pnl 仍作 telemetry 披露）。
4. 行为必须涌现（既有哲学）：不写"血少就少下注"脚本；只拓宽 AI 腿的感知（死亡情境进 advisor 窗口），杠杆仍是既有 6 个权重 key（rho 已能表达收缩/弃权）。

**Grounded contracts (verified this session — forensics workflow + prior implementation):**
- `run_survival_season(rows=, snapshots=, seed=, state_root=, initial_breath=, initial_bankroll_usd=, max_lives=, max_bet_pnl_usd=, recorder=, side_correct_pricing=, value_betting=, effective_entry_price_floor=, shared_inner=, learning_enabled=)` — with `max_lives=1` it runs EXACTLY one life from `rows[0]` and returns (`while remaining and len(lives) < max_lives`, survival_season.py:1755+); `LifeOutcome.died/bets_placed/settlements_processed/consumed_market_ids/terminal_weights` carried; recorder harvests `placed_bets` per life (`:1798`).
- Carry seams exist (this session): `shared_inner: WeightUpdater | None` injects the EMA owner; `learning_enabled=False` freezes weights via `_FrozenInnerUpdater`.
- Existing helpers in `agent/backtest/reincarnation.py` to REUSE: `split_rows_by_time`, `apply_weight_deltas`, `sanitize_rebirth_note`, `_season_summary`, `_curve_points`, `_validate_learner_physics`, `_validate_baseline_physics`, plus the holdout/baseline block of `run_reincarnation_export` (extract shared private helpers, do NOT duplicate).
- `PerformanceWindow` (engines/_performance_window.py:141) carries `recent_reflections: list[str]` (T-B-029 history field, rendered to the LLM by `render_user_prompt` — VERIFY the renderer includes it at implementation; if it does not, fall back to embedding the death summary in `agent_id` is FORBIDDEN (ugly) — instead extend `build_rebirth_window` to also place it in `recent_pnl_window_usd`-adjacent prose is impossible ⇒ the correct fallback is passing it via the `trigger`-adjacent… NO: the ONLY acceptable fallback is to confirm `_strategy_prompts.render_user_prompt` renders `recent_reflections` (it renders the sprint_10 history fields; `_strategy_prompts.py:369` region) — codex review MUST verify this.
- Strict advisor: `StrategyAdvisorImpl(llm_client=, cost_guard=, weight_delta_only=True, model=)` + `review_window(window)`; delta keys w_r/alpha_0..2/beta_0/rho, |delta| ≤ 0.1; rho is the ONLY learnable size lever (`desired = clamp(rho,0,1)·kelly·conf·bankroll`, decision.py:313-314; size < min_bet_size_usd($4) ⇒ NO_BET ⇒ rho-shrink expresses selectivity/abstention, decision.py:328-332).
- Forensics (published claims the page/docs may cite): breath EV −1.22/settled bet at $5 stakes (win +4.22 / loss −25 at ×5); two near-consecutive full-stake losses kill from 35 breath (35/24.65=1.4); ~6 deaths per 1000 settled bets is the variance band median (bootstrap p50=6); pass-1→2 weight change moved +$130 pnl while deaths stayed 6 — death schedule lives in physics, not weights. The numerical leg is therefore the CONTROL (predicted to plateau: its gradient is death-blind — weight_updater.py:400-418 has zero breath/death terms); the AI leg is the TREATMENT (death-context retrospectives + the rho lever = survival behavior CAN emerge, not scripted).
- v3 journey knobs passed EXPLICITLY by the runner: fragile 0.95 / loss_multiplier 5.0 / breath 35 / max_lives(per-incarnation)=1 / floor 0.05; orchestrator defaults stay cheap for tiny fixtures.
- Dashboard conventions: thin async server page + client shell carries all markup/testids (async pages never rendered in vitest); loaders graceful-null; `next.config.ts` tracing already lists `reincarnation.json`/`reincarnation_ai.json` (filenames unchanged ⇒ no config change); artifacts gitignored, deploy via `vercel --prod` CLI.
- Test fixtures: `tests/agent/backtest/test_survival_ai_mode.py::{_dying_fixture,_fragile_seed,_FakeAdvisorLLM,_row,_snap}` (fake always proposes `{"key":"alpha_2","delta":0.04}`); `_survival_row` in test_survival_season.py:277.

**Honest-notes contract (page, verbatim-equivalent):**
1. 死亡经济学：死掉的轮回收益清零（带得走经验，带不走钱）— headline 只属于通关那一世；全程弃权零下注的"不死之身"同样得 0（规则如实陈述）。
2. 携带面 = 8 权重标量 + EMA 学习器 ~8 个 quality 聚合 + （AI 腿）一条 sanitize 过的策略级转世遗言 — ~20 标量存不下 3,431 个赛果；`carry.ema_keys` 逐世披露。
3. 数值腿是 CONTROL：其梯度对死亡不可见（每注 PnL 符号信用），预测它会停在统计相似的死亡深度 — 如果它通关了，那是方差或惊喜，按实发布。AI 腿是 TREATMENT：advisor 看得见死亡聚合（死在第几注/目标、血量统计、仓位统计）**外加一段匿名的逐注 pnl 尾巴（r2 H-1 精确口径：金额序列不带任何市场身份/选手/赛果标签，无法映射回具体市场，因此无法用于"背题"）**；杠杆只有既有 6 key；"为活命收缩"若出现即为涌现行为。
4. 冷启动 holdout（学习冻结、未见时间窗）仍是唯一的泛化证明。
5. 物理取证披露：35 breath / ×5 惩罚 / $5 注 ⇒ breath 期望 −1.22/注 — 一世通关对任何固定 $5 仓位策略都是 ~0.2% 的彩票；这正是把死亡情境交给 agent 的理由。

---

### Task 1: groundhog orchestrator — `run_groundhog_export`

**Files:**
- Modify: `agent/backtest/reincarnation.py` (new public `run_groundhog_export`; extract the holdout+baselines block shared with `run_reincarnation_export` into `_run_frozen_holdout(...)` so both designs share one implementation)
- Test: append to `tests/agent/backtest/test_reincarnation.py`

- [ ] **Step 1: failing tests**

```python
# ========================================================================= #
# Groundhog design (v2): one incarnation = one life from market #1.
# ========================================================================= #


def test_groundhog_caps_when_every_life_dies(tmp_path) -> None:
    """Dying fixture + low breath: every incarnation dies, the loop stops at
    the cap, survived=False, and EVERY incarnation scores ZERO (死了归零)."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _fragile_seed,
    )

    rows, snaps = _dying_fixture()
    out = tmp_path / "g.json"
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=out,
        max_incarnations=2,
        train_fraction=0.5,
        initial_breath=3.0,
        entry_price_floor=0.0,
    )
    assert out.exists()
    assert artifact["experiment"] == "reincarnation"
    assert artifact["design"] == "groundhog_day"
    assert artifact["schema_version"] == 2
    assert artifact["survived"] is False
    assert artifact["surviving_incarnation"] is None
    assert len(artifact["incarnations"]) == 2
    for k, inc in enumerate(artifact["incarnations"], start=1):
        assert inc["incarnation"] == k
        assert inc["died"] is True
        # The permadeath-economics rule: dead incarnations score zero.
        assert inc["scored_pnl"] == 0.0
        assert "pnl_at_death" in inc  # telemetry stays disclosed
        assert "markets_seen" in inc and "progress_pct" in inc
        assert "carry" in inc and isinstance(inc["carry"]["ema_keys"], list)
    # Headline: nobody survived ⇒ headline pnl is 0 (not the dead lives' sum).
    assert artifact["headline_pnl"] == 0.0
    # Holdout still runs (frozen) with the three baselines.
    assert artifact["holdout"]["summary"]["learning_enabled"] is False
    assert set(artifact["holdout"]["baselines"]) == {
        "static", "random", "always_favorite",
    }


def test_groundhog_terminates_on_first_surviving_life(tmp_path) -> None:
    """High breath: the first incarnation survives to the final market —
    the loop terminates immediately and the headline is THAT life's pnl."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _fragile_seed,
    )

    rows, snaps = _dying_fixture()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g.json",
        max_incarnations=5,
        train_fraction=0.5,
        initial_breath=1000.0,
        entry_price_floor=0.0,
    )
    assert artifact["survived"] is True
    assert len(artifact["incarnations"]) == 1
    last = artifact["incarnations"][-1]
    assert last["died"] is False
    assert artifact["surviving_incarnation"] == 1
    assert last["scored_pnl"] == last["pnl_at_death"]
    assert artifact["headline_pnl"] == last["scored_pnl"]
    assert last["progress_pct"] == 100.0
```

- [ ] **Step 2: run to fail** — `python -m pytest tests/agent/backtest/test_reincarnation.py -q -p no:cacheprovider` → ImportError on `run_groundhog_export`.

- [ ] **Step 3: implement.** Signature + responsibilities (full code at implementation; every referenced symbol exists today):

```python
def run_groundhog_export(
    *,
    rows: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    base_seed: StrategyConfig,
    out_path: Path,
    max_incarnations: int = 120,
    train_fraction: float = 0.7,
    fragile_max_breath_risk_pct: float = 0.95,
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER,
    initial_breath: float = DEFAULT_INITIAL_BREATH,
    initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
    max_bet_pnl_usd: float | None = DEFAULT_MAX_BET_PNL_USD,
    entry_price_floor: float = DEFAULT_ENTRY_PRICE_FLOOR,
    effective_entry_price_floor: float | None = None,  # None ⇒ mirror row floor
    rebirth_llm: _LLMClient | None = None,
    rebirth_guard: L3CostGuard | None = None,
    rebirth_model: str = "",
    preflight: bool = True,
    state_root: Path | None = None,
) -> dict[str, Any]:
```

  1. Knob resolution + row-floor fail-closed validation + `split_rows_by_time` — IDENTICAL to `run_reincarnation_export` (extract `_resolve_physics_and_split` if it stays readable; otherwise repeat the 6 lines — reviewer's call).
  1b. **Fresh-state guard (r2 H-2 + r3 M-3)**: the life loop reconstructs from disk on entry (`sandbox_phase2_loop.py:1318` restores snapshot weights `:1380` and resumes tick counts from old `decisions.jsonl` `:1441`; `SandboxStateWriter` is append-only) — a DIRTY state dir silently corrupts an incarnation. With `state_root=None` the run uses a fresh `tempfile.TemporaryDirectory` (no exposure); when a caller passes an explicit `state_root`, a new `_require_fresh_dir(path)` helper (exists-and-non-empty ⇒ `RuntimeError`; never a recursive delete) is applied **per CHILD dir, immediately before each `run_survival_season` call** (checking the parent root would false-positive after incarnation 1 writes) — in the groundhog loop, in the extracted `_run_frozen_holdout`, AND in v1 `run_reincarnation_export`'s `pass_{i}` dirs (r3 M-3: the refactor touches those call sites anyway; same corruption path, same guard).
  1c. **AI preflight (r2 H-3, gated `preflight: bool = True`)**: when `rebirth_llm is not None`, run the existing `preflight_ai_advisor_applicable` probe (survival_season.py — the same fail-fast gate the v3 journey uses) BEFORE the loop so a misconfigured key/model aborts immediately instead of producing ~cap fail-soft no-ops. `_FakeAdvisorLLM` passes the probe (it returns a valid weight_delta), so tests keep `preflight=True`.
  1d. **Run-scoped cost guard (r4 M-1)**: resolve `run_guard = rebirth_guard if rebirth_guard is not None else L3CostGuard.from_env()` ONCE before the loop and reuse it for every death advisor (a per-call `from_env()` fallback would hand each death a FRESH budget — `CostGuard.record()` mutates one instance, cost_guard.py:128-145 — making the cap a lie for direct callers). The preflight probe keeps its OWN probe guard by design (`preflight_ai_advisor_applicable` builds one internally, survival_season.py:1343-1353) — do not share the runtime guard with it.
  2. `shared_inner = WeightUpdater()`; `carry = fragile.weights`; `rebirth_note=None`; loop `k in 1..max_incarnations`:
     - fresh `SurvivalRecorder(rows=train, loss_multiplier=loss_multiplier)`;
     - `result = run_survival_season(rows=train, snapshots=snapshots, seed=replace(fragile, weights=carry), state_root=root/f"inc_{k}", initial_breath=..., initial_bankroll_usd=..., max_lives=1, max_bet_pnl_usd=..., recorder=..., side_correct_pricing=True, value_betting=True, effective_entry_price_floor=eff_floor, shared_inner=shared_inner)` — **max_lives=1 IS the incarnation primitive**: one life from market #1, no internal respawn;
     - `all_eff_prices.extend(_validate_learner_physics(recorder, ..., label=f"incarnation {k}"))`;
     - `life = result.lives[0]` (guard: empty ⇒ RuntimeError, a season must produce one life); `died = life.died`; `pnl = recorder.steps[-1].cum_pnl if recorder.steps else 0.0`;
     - incarnation record: `{"incarnation": k, "died": died, "pnl_at_death": pnl, "scored_pnl": 0.0 if died else pnl, "markets_seen": len(life.consumed_market_ids), "progress_pct": 100.0 * len(life.consumed_market_ids) / len(train), "settled": len(recorder.steps), "bets": life.bets_placed, "win_rate": <wins/settled or 0>, "start_weights": carry.model_dump(), "terminal_weights": terminal.model_dump(), "rebirth_note": rebirth_note, "carry": {"ema_keys": sorted(shared_inner._ema), "ema_size": len(shared_inner._ema)}, "curve": _curve_points(recorder)}` — curve only when `k <= 8 or not died or k == max_incarnations` (artifact size guard: 120 curves × 500 pts would bloat; keep first-8 + survivor + last; ALWAYS keep the scalar fields for every incarnation);
     - `carry = life.terminal_weights`; `rebirth_note = None`;
     - if `not died`: `survived=True; surviving_incarnation=k`; break;
     - else if `rebirth_llm is not None` **and `k < max_incarnations` (r1 H-1: NO retrospective after the FINAL death — a post-cap delta would feed the holdout's carry as hidden training state no reported incarnation ever lived with; mirrors v1's `i < passes` guard)**: death-context retrospective (Task 2's `build_death_window`, **with the run's actual `loss_multiplier` interpolated — r1 M-4**) → `advisor.review_window` → `carry = apply_weight_deltas(terminal, deltas)`; `rebirth_note = sanitize_rebirth_note(...)`; **record per-incarnation treatment telemetry `advisor: {"called": true, "proposals": n, "applied": m}` (r1 M-5)**.
  2b. **Fail-fast config validation (r1 L-7)**: `max_incarnations < 1` ⇒ `ValueError` (no silent clamp — a bad experiment config must be a caller error).
  2c. **Treatment-integrity invariant (r1 M-5 + r2 H-3 + r3 H-1, fail-closed before write)**: `review_window` returns `[]` indistinguishably for cost-exhaustion/LLM-exception/parse-failure AND for a legitimate "no change" (strategy_advisor_impl.py:237/:248/:258; the prompt itself permits an empty list, _strategy_prompts.py:399) — the API cannot separate them, so the telemetry does NOT pretend to: per-incarnation `advisor = {"called": true, "proposals": n, "applied": m}`; artifact-level `rebirth = {"expected": <deaths with a successor>, "calls": N, "productive": <calls with >=1 proposal>, "empty_or_failed": <calls with 0 — fail-soft OR deliberate no-change, indistinguishable at this API; disclosed as such>, "proposals": P, "applied": A}`. Fail-closed rule: `calls == expected` (deterministic orchestration ⇒ equality). `productive < calls` stays legal and DISCLOSED. The preflight probe (1c) is what keeps a dead key/model from masquerading as a string of "no-change" calls.
  2d. **Cross-field scoring invariants (r1 H-2, enforced in PYTHON before write AND re-checked by the TS validator)**: `died ⇒ scored_pnl == 0.0`; `survived == False ⇒ headline_pnl == 0.0 and surviving_incarnation is None and every incarnation died`; `survived == True ⇒ incarnations[surviving_incarnation-1].died == False and headline_pnl == that row's scored_pnl == its pnl_at_death`. Violation ⇒ `RuntimeError` before write.
  3. Holdout (frozen) + 3 baselines + their physics validation + the global effective-floor check — EXTRACTED `_run_frozen_holdout(holdout, fragile, carry, knobs...) -> tuple[dict, list[float]]` shared with `run_reincarnation_export` (refactor that function to call it too; its tests stay green = the refactor proof).
  4. Artifact:

```python
{
  "experiment": "reincarnation",
  "design": "groundhog_day",
  "schema_version": 2,
  "provider": "ai"|"numerical",
  "physics": {...same 8 keys as v1...},
  "split": {...same...},
  "knobs": {..., "max_incarnations": max_incarnations},
  "scoring": "dead incarnations score zero; the headline belongs to the surviving life only",
  "survived": bool,
  "surviving_incarnation": int | None,
  "headline_pnl": <surviving life's pnl, else 0.0>,
  "rebirth": {"expected": E, "calls": N, "productive": K, "empty_or_failed": N-K, "proposals": P, "applied": A},  // r1 M-5 + r2 H-3 + r4 M-2: ONE telemetry contract — "productive" (>=1 proposal) is the only observable success signal; empty calls are fail-soft-or-no-change, disclosed as indistinguishable. The same names flow through the TS validator, fixtures, page copy, and tests.
  "incarnations": [...],
  "holdout": {...same shape as v1...},
}
```

  5. Same fail-closed write discipline (invariants raise BEFORE `out_path.write_text`).
- [ ] **Step 4: run to pass + ruff + mypy --strict on the module; the v1 3-pass tests MUST stay green (additive).**
- [ ] **Step 5: commit** — `feat(groundhog): true reincarnation — one life per incarnation, death scores zero, loop-until-survival`

### Task 2: death-context rebirth window (AI treatment leg)

**Files:**
- Modify: `agent/backtest/reincarnation.py` (`build_death_window` — a sibling of `build_rebirth_window`)
- Test: append to `tests/agent/backtest/test_reincarnation.py`

- [ ] **Step 1: failing tests**

```python
def test_build_death_window_carries_death_context_not_market_specifics() -> None:
    from agent.backtest.reincarnation import build_death_window

    seed_w = _w()
    window = build_death_window(
        incarnation=5,
        max_incarnations=120,
        terminal_weights=seed_w,
        seed_weights=seed_w,
        pnl_at_death=-12.5,
        recent_step_pnls=[2.5, -8.0, -7.0],
        settled=134,
        target_markets=1715,
        markets_seen=420,
        avg_stake_usd=4.87,
        win_rate=0.79,
        initial_breath=35.0,
        loss_multiplier=5.0,
    )
    assert window.trigger == "tick_interval"
    assert window.recent_pnl == [2.5, -8.0, -7.0]
    assert window.recent_pnl_window_usd == pytest.approx(-12.5)
    # The death summary rides the EXISTING recent_reflections field — the
    # renderer already shows it to the LLM; schema untouched.
    assert len(window.recent_reflections) == 1
    note = window.recent_reflections[0]
    for token in ("died", "134", "420", "1715", "incarnation 5", "35", "5x"):
        assert token in note, token
    # Information hygiene: aggregates only.
    for forbidden in ("market_id", "slug", "@", "wta", "atp"):
        assert forbidden not in note.lower()


def test_groundhog_ai_leg_applies_deltas_at_each_death(tmp_path) -> None:
    from agent.backtest.reincarnation import (
        apply_weight_deltas,
        run_groundhog_export,
    )
    from agent.core.state import Weights
    from agent.llm.cost_guard import L3CostGuard
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _fragile_seed,
        _FakeAdvisorLLM,
    )

    rows, snaps = _dying_fixture()
    fake = _FakeAdvisorLLM()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_ai.json",
        max_incarnations=2,
        train_fraction=0.5,
        initial_breath=3.0,
        entry_price_floor=0.0,
        rebirth_llm=fake,
        rebirth_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    assert artifact["provider"] == "ai"
    incs = artifact["incarnations"]
    assert incs[0]["rebirth_note"] is None  # incarnation 1 starts cold
    assert isinstance(incs[1]["rebirth_note"], str)
    # Boundary application proof (deterministic fake: alpha_2 +0.04).
    t1 = Weights(**incs[0]["terminal_weights"])
    expected = apply_weight_deltas(t1, [{"key": "alpha_2", "delta": 0.04}])
    assert incs[1]["start_weights"]["alpha"][2] == pytest.approx(
        expected.alpha[2]
    )
    # The advisor really was called once per death that has a successor
    # (PLUS the preflight probe call — r3 H-1: fake.calls[0] is the PREFLIGHT
    # prompt, so the death-prompt assertion scans ALL calls).
    assert artifact["rebirth"]["expected"] == 1
    assert artifact["rebirth"]["calls"] == 1
    # The death summary reached the LLM prompt (any(), never calls[0]).
    assert any("died" in c["prompt"] for c in fake.calls)
```

- [ ] **Step 2: run to fail.**
- [ ] **Step 3: implement** `build_death_window` (mirrors `build_rebirth_window`; the death summary is ONE pre-formatted aggregate string in `recent_reflections`):

```python
def build_death_window(
    *,
    incarnation: int,
    max_incarnations: int,
    terminal_weights: Weights,
    seed_weights: Weights,
    pnl_at_death: float,
    recent_step_pnls: list[float],
    settled: int,
    target_markets: int,
    markets_seen: int,
    avg_stake_usd: float,
    win_rate: float,
    initial_breath: float,
    loss_multiplier: float,
) -> PerformanceWindow:
    """Death-context retrospective window (groundhog design).

    Information-hygiene contract, stated PRECISELY (r2 H-1): the LLM receives
    (a) the death summary — aggregates only — riding the EXISTING
    ``recent_reflections`` history field (rendered verbatim by the prompt
    renderer; schema untouched), and (b) the ANONYMOUS settled-bet pnl tail
    in ``recent_pnl`` (its documented semantics — last settled bets, $USD).
    Neither carries a market id, slug, player name, or outcome label: the pnl
    sequence cannot be mapped back to specific markets, so nothing the
    advisor sees lets a later incarnation cheat a specific market. We say
    exactly this on the page rather than over-claiming "aggregates only"."""
    summary = (
        f"incarnation {incarnation}/{max_incarnations}: died after {settled} "
        f"settled bets, {markets_seen} of {target_markets} markets seen. "
        f"avg stake ${avg_stake_usd:.2f}, win rate {win_rate:.2f}, "
        f"pnl at death ${pnl_at_death:.2f}. physics: {initial_breath:.0f} "
        f"breath, losses hit breath at {loss_multiplier:g}x; profit is "
        f"FORFEIT on death — only a life that survives the whole season "
        f"keeps its earnings. you will be reborn at the season's first "
        f"market with these weights."
    )
    return PerformanceWindow(
        tick=settled,
        ts=datetime(1970, 1, 1, tzinfo=UTC),
        agent_id=f"groundhog-incarnation-{incarnation}",
        phase=Phase.PHASE_2_APPRENTICE,
        current_weights=terminal_weights,
        baseline_weights=seed_weights,
        recent_pnl_window_usd=pnl_at_death,
        trigger="tick_interval",
        recent_pnl=list(recent_step_pnls),
        recent_reflections=[summary],
        tick_count=settled,
    )
```

  Wire it into Task 1's death branch: `avg_stake_usd = mean(s.size_usd for steps) or 0`, `win_rate` from steps, `recent_step_pnls = [s.pnl_usd][-20:]`. (`render_user_prompt` rendering of `recent_reflections` is ALREADY verified firsthand: `_strategy_prompts.py:391-394` renders each entry verbatim as a bullet — the `any("died" in c["prompt"] ...)` assertion re-proves it in CI.) **Also in this task (r3 L-5): correct the v1 wording overclaim — `build_rebirth_window`'s docstring ("aggregates, never market specifics", reincarnation.py:153) and `sanitize_rebirth_note`'s ("entire input is the aggregates-only window", :181) get the same precise "anonymous settled-pnl tail + aggregates; no market identities" language as the death window.**
- [ ] **Step 4: run to pass + ruff/mypy.**
- [ ] **Step 5: commit** — `feat(groundhog): death-context rebirth retrospective via recent_reflections`

### Task 3: runner — `--design groundhog` + archive the 3-pass artifacts

**Files:**
- Modify: `scripts/run_reincarnation.py` (`--design {groundhog,passes}` default **groundhog**, `--max-incarnations` default 120; groundhog → `run_groundhog_export`, passes → the v1 path preserved. **r1 M-6: the `passes` design's DEFAULT out paths move to `reincarnation_3pass.json` / `reincarnation_ai_3pass.json` so a v1 rerun can never overwrite the v2 page's input with a schema the v2 validator rejects** — the primary filenames belong to the groundhog design now)
- Archive: copy the deployed 3-pass artifacts to `dashboard/public/backtest/reincarnation_3pass.json` / `reincarnation_ai_3pass.json` (gitignored already via `reincarnation*.json`; local record only — the README keeps the v1 numbers)

- [ ] **Step 1:** edit the runner (per-incarnation one-liner printed from the artifact: `inc 5: died@134 settled, 12.2% progress, pnl_at_death=$-12.50 → scored $0`), archive copies, `ruff` clean.
- [ ] **Step 2: commit** — `feat(groundhog): runner --design groundhog (default) + archive 3-pass artifacts`
- [ ] **Step 3: RUN numerical control** (background, detached): `python scripts/run_reincarnation.py --provider numerical --design groundhog`. Expected (forensics): plateaus and hits the cap — report the survival-frontier trajectory honestly.
- [ ] **Step 4: RUN gemini treatment** (background, detached): `python scripts/run_reincarnation.py --provider gemini --design groundhog`. Watch: does rho shrink across deaths? Does progress_pct climb? Report as measured. (~120 advisor calls max; fail-soft; keys via `.env`, never printed.)

### Task 4: dashboard v2 — rebuild /reincarnation around the groundhog story

**Files:**
- Modify: `dashboard/lib/load_reincarnation.ts` (validator v2: `design === "groundhog_day"`, `schema_version === 2`, `incarnations` non-empty with died/scored_pnl/progress_pct/pnl_at_death numbers; **`curve` is OPTIONAL per incarnation (r1 M-3: the python size guard omits it beyond first-8/survivor/last — validate only when present; the shell must tolerate its absence, rendering the frontier from scalars and the survivor curve only if present)**; **the FULL r1 H-2 cross-field invariants re-checked client-side: `died ⇒ scored_pnl === 0`, `survived === false ⇒ headline_pnl === 0 && surviving_incarnation === null`, `survived === true ⇒ the pointed row exists, died === false, headline_pnl === its scored_pnl`**; holdout unchanged; REPLACES the v1 validator — the page only ever renders the current design; types renamed accordingly)
- Modify: `dashboard/app/reincarnation/ReincarnationShell.tsx` (rebuild: hero "die. remember. restart." framing; §1 the rule (死了归零 — permadeath economics, scored vs at-death pnl); §2 survival frontier — staircase/scatter SVG: x = incarnation #, y = progress_pct, dead = dim dots, survivor = glow; §3 incarnation table (first 8 + last + survivor rows: died@, settled, pnl_at_death struck-through → scored $0, rebirth-note snippets for AI leg); §4 the verdict (survived after N incarnations + headline pnl, or capped-out honest statement + best depth reached); §5 cold-start holdout panel (kept); §6 honest notes (the 5-point contract above incl. CONTROL vs TREATMENT framing + the −1.22 breath-EV forensics); §7 design history note (v1 3-pass superseded: "一个 pass 里有 7 条命" — the user's correction IS the changelog); links to /survival + /docs kept)
- Modify: `dashboard/app/reincarnation/PassCurves.tsx` → keep for the survivor's curve if present; new `SurvivalFrontier.tsx` (client SVG: dots/steps per incarnation, viewBox 800×280, non-scaling strokes, abyss vars)
- Modify: `dashboard/__tests__/reincarnation.test.tsx` (fixture builder v2; testids: `reincarnation-route`, `reincarnation-frontier`, `reincarnation-inc-1`, `reincarnation-verdict`, `reincarnation-coldstart`, `reincarnation-honest`, scored-zero strike-through assertion, design-history text, roadmap link test kept)
- The thin `page.tsx`, server loader, next.config tracing: UNCHANGED — **which requires the v2 fixture/pass types to keep the SAME exported names (`ReincarnationFixture` etc.; r3 M-2: `load_reincarnation.server.ts:16/:59` and `page.tsx:13/:21` import that name — change the SHAPE under the existing name, never rename, or `tsc` breaks the "unchanged" files)**.
- Modify (one line, r3 M-4): `dashboard/app/survival/SurvivalJourneyShell.tsx` — the Phase-1 banner's comment/copy describing the experiment as "same-season passes + frozen cold-start" is updated to the groundhog wording ("die → restart at bet #1 → loop until one life survives"); the route link and the rest of the shell stay untouched.

- [ ] **Step 1: failing vitest** (rebuild the suite against the v2 fixture; assert `died ⇒ scored $0` rendering + frontier testid + verdict for both survived/capped fixtures).
- [ ] **Step 2: implement shell + frontier + validator.**
- [ ] **Step 3: `npx vitest run` + `npx tsc --noEmit` green.**
- [ ] **Step 4: commit** — `feat(dashboard): groundhog reincarnation page — survival frontier + permadeath economics`

### Task 5: docs + ship

- [ ] **Step 1:** README: REWRITE the Phase-2 section around the groundhog design (design v1 numbers kept as a paragraph of record + why superseded — "a pass contained 7 lives; the user's correction: death must send you back to bet #1, and dead lives keep nothing"); add the forensics paragraph (breath EV −1.22, deaths = physics not weights — cite the numbers) + REAL run results from Task 3. `/docs` caveat line updated (groundhog wording).
- [ ] **Step 2:** full regression (`python -m pytest tests/agent/engines tests/agent/backtest tests/agent/llm tests/agent/runtime tests/agent/server -q`; mypy --strict + ruff changed files; `npx vitest run`; `npx tsc --noEmit`).
- [ ] **Step 3:** commit; **`gh auth status` → MUST be balflee (flips back!)**; push; `vercel --prod --yes`; live verify `/reincarnation` (v2 markup + real data) + `/survival` banner intact.

---

## Verification
- Unit: cap-out/terminate-on-survival semantics; scored-zero rule; death-window hygiene + renderer proof ("died" reaches the fake LLM's prompt); delta application at each death; v1 3-pass tests stay green (additive seams).
- Integration: per-incarnation physics invariant + holdout/baselines invariant (shared helper); artifact fail-closed.
- Experiment: numerical control vs gemini treatment frontier trajectories; survived-or-capped verdict; holdout cold start. ALL reported as measured — a capped-out numerical control is the PREDICTED result, not a failure.
- Live: /reincarnation serves the groundhog story; /survival untouched.

## Risks + honest expectations
- **Numerical control likely never survives** (death-blind gradient; ~0.2%/incarnation by luck) — that is the experiment's control arm and gets published as such.
- **Gemini treatment may also fail** (the advisor may not discover rho-shrink, or rho-shrink → sub-$4 sizes → near-total abstention → survives with ~$0 — which under 死了归零 scores the same as dying). Every one of these outcomes is a publishable finding about emergence; none is hidden.
- **Runtime**: incarnations are death-bounded (mean life ~130 settles ⇒ ~20-60s each); cap 120 ⇒ ~1-2h/leg worst case; advisor calls ≤ cap-1, fail-soft.
- Artifact size guard: full curves only for first-8/survivor/last incarnations; scalars for all.

## Revision log (plan-loop)

- **round 1 (Codex `VERDICT: HIGH=2 MEDIUM=4 LOW=1`; all seven vetted, all accepted):**
  - **H-1** (post-cap death retrospective would fold an LLM delta into the holdout carry that no reported incarnation ever lived with — hidden training state): advisor branch now guarded `k < max_incarnations`, mirroring v1's `i < passes`.
  - **H-2** (scoring fields not fail-closed across `survived`/`surviving_incarnation`/`headline_pnl`/`scored_pnl`): full cross-field invariants enforced in Python before write AND re-checked in the TS validator (body step 2d + Task 4) — both layers, because python-only leaves stale/hand-edited JSON unprotected and TS-only lets a bad artifact deploy.
  - **M-3** (curve size guard contradicted the v1 validator/shell, which require `curve` on every entry): `curve` is now optional per incarnation in types/validator/shell, with a no-curve fixture test.
  - **M-4** (death prompt hardcoded "5x" while `loss_multiplier` defaults 1.0 and is configurable): `build_death_window(loss_multiplier=)` interpolated into the summary; cheap defaults untouched.
  - **M-5** (treatment labeled by client presence — a Gemini run whose every advisor call failed would publish as "treatment"): per-incarnation `advisor` telemetry + artifact-level `rebirth {calls, proposals, applied}` + fail-closed `calls >= 1` when deaths-with-successors exist; zero proposals/applied stays a disclosed finding, not an error.
  - **M-6** (`--design passes` would overwrite the v2 page input with a v1-schema artifact the new validator rejects): the passes design's default out paths moved to the `_3pass` filenames.
  - **L-7** (`max_incarnations < 1` produced an undefined artifact): explicit `ValueError`, no silent clamp.
- **round 2 (Codex `VERDICT: HIGH=3 MEDIUM=0 LOW=0`; all three vetted, all accepted):**
  - **H-1** (the death window's `recent_pnl` carries the ORDERED per-bet pnl tail while the contract claimed "aggregates only" — and v1's `build_rebirth_window` has the same wording gap): chose "admit + justify" over data removal — the pnl tail is anonymous dollar amounts with no market identity, unmappable to specific markets, and genuinely useful strategy context; docstrings + honest-notes now state the precise input surface instead of over-claiming. v1's docstring gets the same correction in passing.
  - **H-2** (deterministic state dirs are NOT fresh-state guarantees — the loop resumes snapshot weights `sandbox_phase2_loop.py:1380` and tick counts `:1441` from a dirty dir; the writer is append-only): `_require_fresh_dir` fail-closed guard on every per-incarnation + holdout dir under an explicit `state_root` (temp-dir default has no exposure); never a recursive delete.
  - **H-3** (`rebirth.calls >= 1` could not distinguish a real review from cap-1 fail-soft no-ops): per-incarnation `advisor.ok` + artifact `rebirth{expected, calls, ok, empty_or_failed, proposals, applied}` + fail-closed `calls == expected` + the existing `preflight_ai_advisor_applicable` probe before the loop (gated, default True).

- **round 3 (Codex `VERDICT: HIGH=1 MEDIUM=3 LOW=1`; all five vetted, all accepted):**
  - **H-1** (the `ok` telemetry definition was self-contradictory — `review_window`'s `[]` is API-indistinguishable between fail-soft and deliberate no-change; the AI test's `fake.calls[0]` would hit the PREFLIGHT prompt; the signature lacked the `preflight` param): telemetry reworked to `productive`/`empty_or_failed` with the indistinguishability DISCLOSED rather than papered over; test scans `any(...)` and asserts `rebirth.expected == rebirth.calls == 1`; `preflight: bool = True` added to the signature.
  - **M-2** (renaming the fixture types would break the "unchanged" server loader + page, `load_reincarnation.server.ts:16/:59`, `page.tsx:13/:21`): shapes change under the SAME exported names; no renames.
  - **M-3** (the fresh-state guard skipped v1's `pass_{i}` dirs — same dirty-resume corruption path): guard applied per child dir immediately before every `run_survival_season` call in both designs + the shared holdout helper; parent-root checks would false-positive and are explicitly forbidden.
  - **M-4** (the /survival Phase-1 banner copy still describes the superseded "same-season passes" design): one-line wording update added to Task 4's file list; the route and shell otherwise untouched.
  - **L-5** ("corrected in passing" had no owning task): v1 docstring corrections (`build_rebirth_window`, `sanitize_rebirth_note`) are now an explicit Task 2 step.

- **round 4 (Codex `VERDICT: HIGH=0 MEDIUM=2 LOW=0`; both vetted, both accepted):**
  - **M-1** (a per-call `L3CostGuard.from_env()` fallback inside the death branch would hand every death a FRESH budget — the cap would be run-scoped in name only for direct callers): guard resolved once before the loop (body step 1d); the preflight probe keeps its own internal probe guard by design.
  - **M-2** (the artifact JSON block still carried the stale `"ok"` field the r3 rework had renamed — two telemetry contracts for one unobservable state): artifact block now uses `productive`/`empty_or_failed` only, with the same names contractually flowing through validator/fixtures/copy/tests.

## Self-review
- Spec coverage: user directive 1 (one life per incarnation, restart at bet #1) = Task 1 via max_lives=1; directive 2 (loop until survival, cap) = Task 1 termination; directive 3 (死了归零) = scored_pnl rule + page §1; directive 4 (emergence) = Task 2 treatment design (information widened, levers unchanged) + control/treatment framing. ✓
- Placeholders: Task 1 body is a responsibility contract with exact signatures (grounded against code that exists and was exercised this session); Tasks 2/4 carry full test code. ✓
- Type consistency: `run_groundhog_export`/`build_death_window`/`_run_frozen_holdout` names used consistently; artifact keys match between Task 1 and Task 4 validator/tests. ✓
