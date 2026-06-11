# Layer-2 Self-Evolution Wiring Implementation Plan (Plan 2 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Roadmap:** This is **Plan 2 of 2**. **Plan 1** (`2026-06-08-real-signal-source.md`) makes the 5 engine SIGNALS real (the prerequisite — learning on synthetic noise learns nothing). **Plan 2 (this doc)** plugs in the SELF-EVOLUTION machinery so the agent moves off its starting config: human-approval weight changes, settlement-time self-learning, and the L3 strategy advisor. **Do Plan 1 first.**

**Goal:** Turn the three currently-stubbed self-evolution mechanisms (operator weight-delta approval, settlement-time WeightUpdater self-learning, StrategyAdvisor L3 meta-optimizer) from NoOp/absent into real wired behaviour in BOTH the deployed mock-bet loop and the backtest replay — so the backtest-optimal config is a real SEED that the agent then adapts, and the backtest measures *config + learning trajectory* rather than a frozen config.

**Architecture:** Three wirings against the SAME `SandboxPhase2Loop` used by prod (`agent/server/main.py`) and backtest (`agent/backtest/replay_runner.py`): (1) **[prod]** thread the single shared `RuntimeAgentRunner` queue through the loop factory so approved weight deltas actually reach the loop's already-correct `_drain_and_apply_weight_deltas`; (2) **[prod only]** stop overriding the loop's real `StrategyAdvisorImpl` default with `NoOpStrategyAdvisor()` — the backtest replay KEEPS `NoOpStrategyAdvisor` because a sweep has no human-approval operator loop (codex fix: L2 scopes the un-stub to `main.py`; `replay_runner.py:990` intentionally stays NoOp); (3) **[prod + backtest]** bridge the settlement poller's `weight_updater.update()` (whose return is currently discarded) to the real `WeightUpdater`, re-assigning `loop._weights` so the agent learns from realized PnL — wired in the backtest behind a `ReplayConfig.enable_settlement_learning` flag so the sweep can optimize the seed-config-under-learning.

**Tech Stack:** Same as Plan 1. Real implementations already exist (`agent/engines/weight_updater.py`, `agent/engines/strategy_advisor_impl.py`, `agent/runtime/agent_runner.py`); this plan is mostly *wiring*, not building.

---

## Why this order (read first)

The synthesis verdict: today `self._weights = starting config` forever (all three mechanisms inert), which is *why* the Plan-1 backtest is a valid selector — live runs the config unchanged. The moment learning turns on, the backtest must learn too or it stops predicting live. Sequence so each step is independently shippable and safe:

1. **Task L1 — queue bug fix** (smallest, safest first taste of "config can change at runtime"; prerequisite for L2 proposals to actually apply).
2. **Task L2 — real StrategyAdvisor** (one-line un-stub; proposals now flow, gated by human approval through the L1-fixed queue).
3. **Task L3 — settlement-time self-learning** (the meaty one; the agent learns from PnL).
4. **Task L4 — backtest learning parity** (so the sweep optimizes seed-under-learning, not a frozen config).
5. **Task L5 — learning-enabled sweep** (run it, write it up).
6. **Task L6 — ReflectionEngine visibility** (optional; narrative-only by design).

---

## File Structure

| File | Responsibility | Modify/Create |
|---|---|---|
| `agent/server/main.py` | Add `runtime_agent` param to `_build_production_loop_factory`; thread the shared instance; un-stub the strategy advisor. | Modify (~`:1871`, `:2000`, `:2028`, `:2244`, `:2270`) |
| `agent/backtest/replay_runner.py` | Add `enable_settlement_learning`; inject the learning bridge; add `terminal_weights` to metrics. **KEEPS `NoOpStrategyAdvisor` at `:990`** (a sweep has no operator-approval loop — codex fix). | Modify (~`:666`, `:978`, `ReplayConfig`, `ReplayMetrics`) |
| `agent/engines/weight_updater.py` | Add a settlement-PnL gradient entrypoint (`update_from_settlement`). | Modify |
| `agent/runtime/sandbox_phase2_loop.py` | Expose a hook so the settlement learner can re-assign `self._weights` (or accept a weight-holder ref). | Modify (~`:863`, settlement path) |
| `agent/backtest/settlement_learner.py` | `_SettlementLearningWeightUpdater` adapter (signals→features→real updater→re-assign weights). | Create |
| Tests alongside each. | | Create/Modify |

---

## Conventions (apply to every task)

- Run tests: `python -m pytest <path> -q -p no:cacheprovider`. Gates: `ruff check` + `mypy --strict` clean before each commit.
- Confirm `git config user.email` is `256016480+balflee@users.noreply.github.com` before committing. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Never** call live Gemini in a unit test — inject a fake `_LLMClient`.

---

## Task L1: Fix the RuntimeAgentRunner queue wiring bug

The FastAPI approve route enqueues weight deltas on the `RuntimeAgentRunner` built at `main.py:2270`, but `_build_production_loop_factory` never receives it, so the loop builds its OWN fresh queue (`sandbox_phase2_loop.py:992-994`). Approved deltas never reach the running agent. The drain-and-apply consumer (`:1445`, `:2132-2184`) is already correct — only the threading is broken.

**Files:**
- Modify: `agent/server/main.py:1871` (factory signature), `:2000` (loop ctor call), `:2244` (factory call), `:2270` (move construction earlier)
- Test: `tests/agent/server/test_runtime_agent_wiring.py` (create)

- [ ] **Step 1: Write the failing test** — the loop built by the factory shares the SAME `RuntimeAgentRunner` instance that the API enqueues into.

```python
# tests/agent/server/test_runtime_agent_wiring.py
from __future__ import annotations

# NOTE (codex fix): class is `AgentRunner` (agent/runtime/agent_runner.py:91);
# `RuntimeAgentRunner` is only a local alias in main.py:76. Import + alias.
from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner


def test_factory_threads_shared_runtime_agent(monkeypatch, tmp_path) -> None:
    # The loop built by _build_production_loop_factory must use the SAME
    # RuntimeAgentRunner we pass in (not a fresh fallback instance), so deltas
    # enqueued via the API reach the loop.
    from agent.server import main as m

    shared = RuntimeAgentRunner()
    factory = m._build_production_loop_factory(
        state_dir=tmp_path,
        chain_adapter=m._SandboxChainAdapter(),    # real adapter (main.py:1562)
        tick_input_source=m._IdleTickInputSource(),
        wall_clock=m.UtcClock(),
        time_compression=1.0,
        tick_interval_seconds=1.0,
        runtime_agent=shared,
    )
    loop = factory()
    assert loop._runtime_agent is shared
```

(Verify the exact ctor args `_SandboxChainAdapter` (main.py:1562) / `_IdleTickInputSource` need at execution — or use `m._build_chain_adapter(...)`; the load-bearing assertion is `loop._runtime_agent is shared`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/server/test_runtime_agent_wiring.py -q -p no:cacheprovider`
Expected: FAIL — `TypeError: _build_production_loop_factory() got an unexpected keyword argument 'runtime_agent'`

- [ ] **Step 3: Implement the threading**

1. `main.py:1871` — add `runtime_agent: RuntimeAgentRunner | None = None,` to the factory signature.
2. `main.py:2000` — add `runtime_agent=runtime_agent,` to the `SandboxPhase2Loop(...)` constructor call inside the `_factory` closure.
3. `main.py:~2244` — add `runtime_agent=runtime_agent,` to the `_build_production_loop_factory(...)` call.
4. Move the `runtime_agent = RuntimeAgentRunner()` construction (currently `:2270`) to BEFORE the `loop_factory = _build_production_loop_factory(...)` call so it can be threaded in. Pass the same instance to `create_app(..., runtime_agent=runtime_agent)`.

- [ ] **Step 4: Run test + no regression**

Run: `python -m pytest tests/agent/server/ -q -p no:cacheprovider`
Expected: PASS (new test + existing server tests)

- [ ] **Step 5: Gates + commit** (`ruff`, `mypy --strict agent/server/main.py`)

```bash
git add agent/server/main.py tests/agent/server/test_runtime_agent_wiring.py
git commit -m "fix(server): thread shared RuntimeAgentRunner into loop factory (approval deltas now reach the loop)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task L2: Un-stub the StrategyAdvisor (real L3 meta-optimizer)

The loop's constructor ALREADY defaults to `StrategyAdvisorImpl(llm_client=GeminiClient(), cost_guard=L3CostGuard.from_env())` when `strategy_advisor=None` (`sandbox_phase2_loop.py:941-948`, verified by codex). Prod (`main.py:2028`) and backtest (`replay_runner.py:990`) override it with `NoOpStrategyAdvisor()`. Fail-soft precision (codex fix): `GeminiClient` *raises* `MissingApiKeyError` when called without `GEMINI_API_KEY` (raise at `gemini_client.py:153`); it is **`StrategyAdvisorImpl.review_window` that catches it and returns `[]`** (except/return at `strategy_advisor_impl.py:230-236`) — so the loop never crashes on a missing key, but the fail-soft lives in the advisor, not the client. So un-stubbing prod is a one-line change behind the env that gates Gemini.

**Files:**
- Modify: `agent/server/main.py:2028`
- Test: `tests/agent/server/test_runtime_agent_wiring.py`

- [ ] **Step 1: Write the failing test** — with `GENESIS_REAL_SIGNALS`/`GEMINI_API_KEY` semantics, the prod loop's strategy advisor is a real `StrategyAdvisorImpl`, not `NoOpStrategyAdvisor`. (Test the small helper if the builder is awkward — extract `_make_prod_strategy_advisor() -> StrategyAdvisor`.)

```python
def test_prod_strategy_advisor_is_real_when_enabled(monkeypatch) -> None:
    from agent.engines.strategy_advisor import NoOpStrategyAdvisor
    from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
    from agent.server import main as m

    monkeypatch.setenv("GENESIS_REAL_STRATEGY_ADVISOR", "1")
    adv = m._make_prod_strategy_advisor()
    assert isinstance(adv, StrategyAdvisorImpl)
    monkeypatch.delenv("GENESIS_REAL_STRATEGY_ADVISOR")
    assert isinstance(m._make_prod_strategy_advisor(), NoOpStrategyAdvisor)
```

- [ ] **Step 2: Run test to verify it fails** (`AttributeError: module ... has no attribute '_make_prod_strategy_advisor'`)

- [ ] **Step 3: Implement** — add the helper near `main.py:2028` and use it in the loop ctor:

```python
def _make_prod_strategy_advisor() -> "StrategyAdvisor":
    if os.environ.get("GENESIS_REAL_STRATEGY_ADVISOR") == "1":
        from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
        from agent.llm.gemini_client import GeminiClient
        from agent.llm.cost_guard import L3CostGuard

        return StrategyAdvisorImpl(llm_client=GeminiClient(), cost_guard=L3CostGuard.from_env())
    return NoOpStrategyAdvisor()
```

and replace `strategy_advisor=NoOpStrategyAdvisor(),` with `strategy_advisor=_make_prod_strategy_advisor(),`. (Keep the flag default OFF so the smoke contract is unchanged; flip it on for the real run.)

- [ ] **Step 4: Run test + `python -m agent.main --help` smoke (exit 0).**

- [ ] **Step 5: Gates + commit.**

```bash
git add agent/server/main.py tests/agent/server/test_runtime_agent_wiring.py
git commit -m "feat(server): GENESIS_REAL_STRATEGY_ADVISOR un-stubs the real L3 StrategyAdvisorImpl

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task L3: Settlement-time self-learning (the core "agent learns")

The settlement poller calls `weight_updater.update(phase, signals={pnl_usd,size_usd}, outcome)` and discards the return (`sandbox_settlement_poller.py:563-567`). The real `WeightUpdater.update(*, current: Weights, phase: Phase, features, desperate) -> Weights` needs per-engine quality features + the current weights, and returns new weights. We bridge them and re-assign `loop._weights`.

**Design decision (credit assignment):** to nudge the RIGHT weights from a realized win/loss, the settlement must know which engines drove the bet AND which side it took. Carry the decision-time per-engine signal scores onto the bet record so settlement can attribute reward. **(codex fix — direction matters for NO bets)** A NO bet is chosen when the fused score is negative (`decision.py:239`), so the engines that drove a *correct* NO bet had *negative* scores. The naive `sign(pnl)*score` would PUNISH them. Correct formula:

```
bet_direction = +1 if BetRecord.side == "YES" else -1
engine_quality = sign(pnl) * bet_direction * signal_score[engine]
```

i.e. reward engines whose signal AGREED with the bet's direction when the bet won (and penalize when it lost). `bet_direction` comes from the due `BetRecord.side` (`sandbox_state.py:147`); pass it as a flat float (e.g. a `bet_direction` key) — never nest it in the poller's `dict[str,float]`. Settlement win/loss for NO is handled at `sandbox_settlement_poller.py:741-755` (`side==NO` + `outcome=="no"` ⇒ win).

**Codex-review corrections baked in (do NOT regress these):**
1. **`BetRecord` is created INSIDE the executor**, not in the loop's BET path — `SandboxExecutor.place_order` builds + appends it (`polymarket_sandbox_executor.py:291,301`); the loop only calls `executor.place_order` (`sandbox_phase2_loop.py:1520`). So `signal_scores` must be threaded through the **Executor protocol** (`polymarket_sandbox_executor.py:163`) as an OPTIONAL param, persisted on the open `BetRecord` AND the poller's **status-flip `BetRecord`** (`sandbox_settlement_poller.py:544-554`) — NOT `SettledBetRecord` (no signal fields; `sandbox_state.py:154`). JSONL is append-only — write `signal_scores` at append time, never mutate after.
2. **The settlement-poller `update` Protocol is `signals: dict[str, float]`** (`sandbox_settlement_poller.py:187`) — a flat float map. Do NOT nest a `signal_scores` dict inside it. Instead **flatten** per-engine scores as float keys: `signals = {"pnl_usd": …, "size_usd": …, "score_tennis_technical": …, "score_market_momentum": …, …}`. **(codex fix) Source = the DUE OPEN `BetRecord`** the poller selects from `open_bets.jsonl` (`sandbox_settlement_poller.py:436`, validated `:477`) — flatten `due_bet.signal_scores` just before the `update` call (`:563`). The poller separately writes a `SettledBetRecord` (`:529-537`) and a status-flip `BetRecord` (`:544-554`); persist `signal_scores` on the open + status-flip `BetRecord`s (NOT relying on `SettledBetRecord`, which has no signal fields, `sandbox_state.py:154`).
3. **Phase type mismatch.** The poller passes `phase` as a `WeightUpdaterPhase` string like `"PHASE_2_EXTENDED"` (`sandbox_settlement_poller.py:131`), but the real `WeightUpdater.update` wants the `Phase` enum, which has NO `PHASE_2_EXTENDED` member (`state.py:39`). The adapter MUST map (e.g. `"PHASE_2_EXTENDED" -> Phase.PHASE_2_APPRENTICE`) — never `Phase[phase]` directly (KeyError). Lock the exact map by reading both enums at execution.
4. **Honor desperate.** Pass `desperate=getattr(holder, "_desperate", False)` to the real updater (the plan claims it; the adapter must do it).

**Files:**
- Modify: `agent/data/sandbox_state.py` (`BetRecord` gains `signal_scores: dict[str,float] = {}`).
- Modify: `agent/data/polymarket_sandbox_executor.py` (Executor protocol + `SandboxExecutor.place_order` gain optional `signal_scores`; persist on the open `BetRecord` + the poller's status-flip `BetRecord`, NOT `SettledBetRecord`).
- Modify: `agent/runtime/sandbox_phase2_loop.py` (pass `signal_scores=` from the decision's per-engine signals into `executor.place_order` at `:1520`).
- Modify: `agent/runtime/sandbox_settlement_poller.py` (flatten the settled bet's `signal_scores` into the `signals` float map passed to `update`).
- Modify: `agent/engines/weight_updater.py` (add `async def update_from_settlement(*, current: Weights, phase: Phase, pnl_usd: float, size_usd: float, signal_scores: dict[str,float], bet_direction: float, desperate: bool = False) -> Weights` — async to match the existing `WeightUpdater.update` (`weight_updater.py:304`); `bet_direction` (+1 YES/-1 NO) is REQUIRED for correct credit assignment; `size_usd` is REQUIRED because `rho_quality` normalizes pnl by stake (Step 3) — the poller already carries it in the flat `signals` map at the `update` call site (`sandbox_settlement_poller.py:563`)).
- Create: `agent/backtest/settlement_learner.py` (`_SettlementLearningWeightUpdater` adapter, phase-mapping + score-key reading).
- Tests alongside.

- [ ] **Step 1 (RED): test `update_from_settlement` credit direction.** (every call passes `size_usd=` e.g. `size_usd=10.0`, mandatory.) (a) YES bet, positive pnl, `signal_scores={'tennis_technical': +1, ...}`, `bet_direction=+1`, `size_usd=10.0` → α shifts toward tennis_technical. (b) **(codex fix) winning NO bet**: negative pnl-driving score `signal_scores={'tennis_technical': -1, ...}`, positive pnl, `bet_direction=-1`, `size_usd=10.0` → STILL rewards tennis_technical (because `sign(pnl)*bet_direction*score = (+)(-)(-) = +`). (c) losing NO bet penalizes it. All keep the simplex normalized.

- [ ] **Step 2: Run RED.**

- [ ] **Step 3: Implement `update_from_settlement`** on the real `WeightUpdater` with signature `async def update_from_settlement(*, current, phase, pnl_usd, size_usd, signal_scores, bet_direction, desperate=False) -> Weights`. Build the `features` dict the gradient layer expects (`weight_updater.py:405-421`):
  - per-engine `{f"{engine}_quality": sign(pnl)*bet_direction*score}`.
  - **(codex fix — rho signedness) `rho_quality = tanh(pnl_usd / max(size_usd, 1e-6))`** — SIGNED + bounded, using the `size_usd` PARAMETER now in the signature (above). The gradient reads `rho_quality` as `grad_rho` and ADDS it to the rho logit (`weight_updater.py:421,430-433`), so a *positive* value RAISES risk; a magnitude-only value would wrongly raise `rho` after losses. Win → raise/hold risk, loss → cut risk.
  - **(codex fix — train `w_r/w_s`) `rational_stream_quality` / `sentient_stream_quality`** aggregated from the rational group (tennis_technical+market_momentum+smart_money) vs the sentient group (sentiment_llm+crowd_volume) — the gradient at `:411-421` reads ONLY these two keys for the stream weights.
  Then call the existing internal gradient/EMA path; honor phase freeze + desperate LR. Keep it deterministic. Test: a loss reduces `rho`, a win does not reduce it.

- [ ] **Step 4: GREEN + commit.**

- [ ] **Step 5 (RED): test the `_SettlementLearningWeightUpdater` adapter** re-assigns the loop's weights. Given a fake holder with `_weights` + `_desperate`, calling `await adapter.update(phase="PHASE_2_EXTENDED", signals={"pnl_usd":5.0, "size_usd":10.0, "bet_direction":1.0, "score_tennis_technical":1.0, "score_market_momentum":0.0}, outcome=...)` mutates the holder's `_weights` (and does NOT raise on the `PHASE_2_EXTENDED` string). **Also test: a `signals` map MISSING `bet_direction` → the adapter SKIPS the update (no weight change, warns), not a silent YES-default.**

- [ ] **Step 6: Implement the adapter** in `agent/backtest/settlement_learner.py`:

```python
import logging

from agent.core.state import Phase

logger = logging.getLogger(__name__)

# WeightUpdaterPhase string -> Phase enum (Phase has no PHASE_2_EXTENDED member).
# Verify the exact members of BOTH enums at execution (state.py:39).
_PHASE_MAP: dict[str, Phase] = {
    "PHASE_1_INFANCY": Phase.PHASE_1_INFANCY,
    "PHASE_2_APPRENTICE": Phase.PHASE_2_APPRENTICE,
    "PHASE_2_EXTENDED": Phase.PHASE_2_APPRENTICE,   # extended apprenticeship -> apprentice freeze rules
    "PHASE_3_MASTER": Phase.PHASE_3_MASTER,         # (codex fix) members are INFANCY/APPRENTICE/MASTER/TERMINAL (state.py:39-42); no *_ADULT
    "PHASE_4_TERMINAL": Phase.PHASE_4_TERMINAL,
}


@dataclass
class _SettlementLearningWeightUpdater:
    inner: WeightUpdater
    weights_holder: object  # has mutable ._weights and ._desperate (the loop)

    async def update(self, *, phase: str, signals: dict[str, float], outcome: object) -> None:
        # signals is a FLAT dict[str,float]: pnl_usd/size_usd + "score_<engine>" keys
        # + "bet_direction" (+1 YES / -1 NO) — all flattened by the poller from the
        # due open BetRecord's persisted signal_scores + side.
        scores = {
            k[len("score_"):]: v for k, v in signals.items() if k.startswith("score_")
        }
        # (codex fix) bet_direction is REQUIRED — do NOT silently default to YES (+1),
        # which would invert learning for NO bets. Skip the update + warn if absent.
        if "bet_direction" not in signals:
            logger.warning("settlement learner: missing bet_direction — skipping update")
            return
        new_w = await self.inner.update_from_settlement(
            current=self.weights_holder._weights,             # type: ignore[attr-defined]
            phase=phase if isinstance(phase, Phase) else _PHASE_MAP[phase],
            pnl_usd=signals.get("pnl_usd", 0.0),
            size_usd=signals.get("size_usd", 0.0),            # stake; rho_quality guards max(.,1e-6)
            signal_scores=scores,
            bet_direction=signals["bet_direction"],           # +1 YES / -1 NO (mandatory)
            desperate=getattr(self.weights_holder, "_desperate", False),
        )
        self.weights_holder._weights = new_w
        # Timing (codex fix): the poller runs at the TOP of _tick
        # (sandbox_phase2_loop.py:1448) BEFORE the decision (:1497) — so learned
        # weights take effect on the SAME tick's decision, not the next one.
```

- [ ] **Step 7: Thread `signal_scores` + `side` end-to-end.** (a) `BetRecord` gains `signal_scores: dict[str,float] = {}` (sandbox_state); `side` already exists. (b) Extend the **Executor protocol** + `SandboxExecutor.place_order` with optional `signal_scores`, persisted on the open `BetRecord` AND the poller's **status-flip `BetRecord`** at append time — NOT `SettledBetRecord` (it has no signal fields; `sandbox_state.py:154`); never mutate appended JSONL. (c) The loop passes `signal_scores=` (per-engine `signal.score` from the tick's signals) into `executor.place_order` at `:1520`. (d) The settlement poller, for the **due open bet** (from `open_bets.jsonl`), flattens its `signal_scores` into `score_<engine>` keys AND adds `bet_direction = +1.0 if bet.side=="YES" else -1.0` into the `signals` float map just before `update`. RED→GREEN each.

- [ ] **Step 8: Wire into prod** — `main.py:2006` swap `_NoopWeightUpdater()` for `_SettlementLearningWeightUpdater(inner=WeightUpdater(...), weights_holder=loop)` (constructed AFTER the loop, swapped onto the poller — mirror the backtest Option-B pattern from Task L4). Behind `GENESIS_REAL_LEARNING=1` (default OFF).

- [ ] **Step 9: Gates + commit each sub-step.**

---

## Task L4: Backtest learning parity (sweep optimizes seed-under-learning)

The replay already polls settlements per tick (`sandbox_phase2_loop.py:1448`), so a real updater would be fed. Add the flag + bridge so the backtest can run the SAME learning loop, and capture the terminal weights so we can tell learning happened.

**Files:**
- Modify: `agent/backtest/replay_runner.py` — `ReplayConfig.enable_settlement_learning: bool = False`; after loop construction, if enabled, swap `loop._poller`'s updater for `_SettlementLearningWeightUpdater(inner=WeightUpdater(...), weights_holder=loop)`; add `terminal_weights: Weights` (or `weight_delta_l1: float`) to `ReplayMetrics`.
- Test: `tests/agent/backtest/test_replay_runner.py`

- [ ] **Step 1 (RED):** a replay with `enable_settlement_learning=True` over markets that produce settled bets yields `metrics.terminal_weights != config.starting_weights` (learning moved the weights), while `enable_settlement_learning=False` yields `terminal_weights == starting_weights`.

- [ ] **Step 2–4:** implement the flag + bridge swap (construct `WeightUpdater` INSIDE `run_replay` so EMA state is fresh per replay — see gotcha) + the `terminal_weights` metric; GREEN; commit.

---

## Task L5: Learning-enabled sweep + writeup

- [ ] **Step 1:** add `--learn` to `agent/backtest/find_optimal_config.py` → sets `ReplayConfig.enable_settlement_learning=True`. Note in `--help` that this ranks *(seed config + learning trajectory)*, the live-faithful objective.
- [ ] **Step 2:** run both: `... --n 96 --real` (static) and `... --n 96 --real --learn` (learning) on `agent/backtest/_cache_tennis`.
- [ ] **Step 3:** write `reports/backtest/real_signal_sweep.md` comparing the two — the optimal SEED config under learning, terminal-weight drift, Sharpe delta vs static, the resolver coverage %, and the standing caveats (β₂ recency lag; compounded-Sharpe vs family-③ economy still pending).
- [ ] **Step 4:** commit the report.

---

## Task L6 (optional): Wire ReflectionEngine for operator visibility

`SandboxPhase2Loop` accepts `reflection_engine`; both builders omit it (→ None → no-op). Wiring a real `ReflectionEngine(...)` produces the "consciousness stream" narrative for the dashboard. **Be explicit with stakeholders: this is narrative-only and does NOT influence decisions or weights (by PRD §4.4 design).**

- [ ] **Step 1–N:** behind `GENESIS_REAL_REFLECTION=1`, construct `ReflectionEngine(llm_client=GeminiClient(), cost_guard=...)` and pass `reflection_engine=` into the prod loop ctor; assert (test) the step-9 trigger now fires and persists a reflection record. Keep OFF by default (cost). Commit.

---

## Self-Review

**Spec coverage:** queue bug → L1 ✅; real StrategyAdvisor → L2 ✅; settlement self-learning → L3 ✅; backtest learning parity (keeps backtest predicting live) → L4 ✅; learning sweep + writeup → L5 ✅; reflection visibility → L6 ✅. Dependency on Plan 1 (real signals) stated in header ✅.

**Placeholder scan:** L3 sub-steps and L5/L6 use RED→GREEN→commit shorthand; the interfaces (`update_from_settlement`, `_SettlementLearningWeightUpdater`, `ReplayConfig.enable_settlement_learning`, `terminal_weights`) and the exact file:line wiring points are fully specified above. The L3 credit-assignment design (carry `signal_scores` + `side` on the bet record; `engine_quality = sign(pnl)*bet_direction*score`) is the one genuine design choice — specified concretely, lockable at execution.

**Type consistency:** the settlement poller Protocol (`update(*, phase:str, signals:dict[str,float], outcome) -> None`) is preserved by the adapter; the real `async WeightUpdater.update_from_settlement` takes `current: Weights, phase: Phase, pnl_usd, size_usd, signal_scores, bet_direction, desperate` and returns `Weights`; the loop re-assigns `self._weights`. Phase conversion via `_PHASE_MAP` in the adapter (NOT `Phase[phase]` — `Phase` lacks the `WeightUpdaterPhase` members). `bet_direction` is mandatory (adapter skips + warns if the flat `signals` map omits it).

**Known risks (call out at execution):** (1) **Two `WeightUpdater` interfaces** — the settlement-poller Protocol vs the engine class; the adapter bridges them, do not conflate. (2) **EMA state** — construct the real `WeightUpdater` INSIDE `run_replay` so a sweep's replays are independent (fresh EMA each). (3) **Credit assignment quality** — `sign(pnl)*bet_direction*score` is a crude-but-honest first gradient; if learning is noisy, refine the feature mapping (a follow-up, not a blocker). (4) **Order matters (codex fix — SAME tick, not next)** — the settlement poller runs at the TOP of `_tick` (`sandbox_phase2_loop.py:1448`) BEFORE the decision (`:1497`), so weights re-assigned at settlement take effect on the **same tick's** decision. Tests must assert same-tick post-settlement effect, not next-tick. (5) Everything here is gated behind `GENESIS_REAL_*` flags so the existing frozen-config smoke contract is preserved until each piece is validated.

---

## Revision log

- **Round 1** (codex review, combined VERDICT `HIGH=5 MEDIUM=1 LOW=2`; all findings vetted against real code and accepted). Plan 2 fixes:
  - **HIGH-1** L3 interface mismatches: poller `phase` is a `WeightUpdaterPhase` string (`PHASE_2_EXTENDED`) that `Phase` lacks (`state.py:39`) → adapter maps via `_PHASE_MAP` (never `Phase[phase]`); poller `signals` is `dict[str,float]` (`sandbox_settlement_poller.py:187`) → flatten per-engine scores as `score_<engine>` float keys, not a nested dict; pass `desperate`.
  - **HIGH-2** `signal_scores` persistence: `BetRecord` is built INSIDE `SandboxExecutor.place_order` (`polymarket_sandbox_executor.py:291`), not the loop → extend the Executor protocol with optional `signal_scores`, persist on open + settled records at append time (JSONL is append-only).
  - **HIGH-3** L1 test used non-existent names: class is `AgentRunner` (`agent_runner.py:91`), `RuntimeAgentRunner` is a main.py-local alias; chain double is `_SandboxChainAdapter` (`main.py:1562`), not `_NoopChainAdapter`.
  - **MED-1** learning timing: poller runs BEFORE decide (`sandbox_phase2_loop.py:1448` vs `:1497`) → learned weights take effect SAME tick, not next.
  - **LOW-2** fail-soft attribution: `GeminiClient` *raises* `MissingApiKeyError` (`gemini_client.py:153`); it is `StrategyAdvisorImpl.review_window` that catches → `[]` (`strategy_advisor_impl.py:230-236`).
- **Round 2** (codex, combined `HIGH=5 MEDIUM=2`; Plan 2 share, all accepted): L3 `_PHASE_MAP` used non-existent `Phase.PHASE_3_ADULT` → `Phase.PHASE_3_MASTER` (members INFANCY/APPRENTICE/MASTER/TERMINAL); `update_from_settlement` marked `async`; credit-assignment source corrected to the DUE OPEN `BetRecord` from `open_bets.jsonl` (persist on open + status-flip rows, not `SettledBetRecord`); refreshed stale citations (`AgentRunner` agent_runner.py:91, GeminiClient raise :153, advisor catch :230-236).
- **Round 3** (codex, combined `HIGH=4 MEDIUM=2 LOW=2`; Plan 2 share, all accepted): **HIGH-3 (important)** credit assignment must include bet direction — `engine_quality = sign(pnl)*bet_direction*signal_score` (NO bets had negative scores; the naive formula punished correct NO predictions); thread `bet_direction` (+1 YES/-1 NO from `BetRecord.side`) as a flat key. **HIGH-4** StrategyAdvisor un-stub is PROD-ONLY (backtest sweep has no operator-approval loop → keeps NoOp). **MED-1** add `rational_stream_quality`/`sentient_stream_quality` features so `w_r/w_s` actually train (`weight_updater.py:411-421`). **MED-2** persist on the poller's status-flip `BetRecord`, never `SettledBetRecord`. **LOW-2** refreshed revlog citations.
- **Round 4** (codex, combined `HIGH=1 MEDIUM=2 LOW=0`; Plan 2 share): **MED** propagated `bet_direction` through the `update_from_settlement` file-list signature + self-review formulas (were stale `sign(pnl)*score`); **MED** fixed the File-Structure row — backtest `replay_runner.py` KEEPS `NoOpStrategyAdvisor` (only prod un-stubs), consistent with the L2 task + architecture.
- **Round 5** (codex, combined `HIGH=2 MEDIUM=1`; Plan 2 share, all accepted): **HIGH** `bet_direction` made MANDATORY in the adapter (skip+warn if absent — the silent YES-default would invert NO-bet learning) + missing-key test; **HIGH** `rho_quality` made SIGNED+bounded `tanh(pnl/size)` (the gradient ADDS it to the rho logit, so magnitude-only would raise risk after losses) + win/loss risk test; propagated through self-review type-consistency.
- **Round 6** (codex, Plan 2 share; second-order miss from Round 5, accepted): **HIGH** `rho_quality = tanh(pnl_usd/max(size_usd,1e-6))` referenced `size_usd` but the `update_from_settlement` signature + adapter did NOT pass it → executor would hit a `NameError`. Added `size_usd: float` (REQUIRED kw) to the file-list signature (L195), the Step-3 signature (L203), the Step-1 direct-call test, and the self-review type line; adapter now forwards `size_usd=signals.get("size_usd", 0.0)` from the flat map (poller already carries it natively at the `update` call site `sandbox_settlement_poller.py:563`; full flattened map described at plan L186). Codex's other traced seams were CONFIRMED grounded (Plan 1 replay seam sync-compatible + keyword `signals_for`; momentum helper `tanh(0.6*drift+0.4*velocity)` matches the engine; `tennis_fetcher.py` is the real CLOB producer; L1 queue bug & L2 `StrategyAdvisorImpl` default both real; pyproject dep gap already captured Round 4).
- **Round 7** (codex, focused confirmation pass — SUBSTANTIALLY CONVERGED): codex confirmed the Round-6 `size_usd` fix is internally consistent — "No `update_from_settlement` site still omits `size_usd`"; all four signature/test sites + the adapter agree. No new HIGH/MEDIUM. Only nit: one **stale prose citation** (I had cited `sandbox_settlement_poller.py:173` — a *plan* line number, not a code line) — corrected to `:563` (the `update` call site, consistent with L186). Two consecutive rounds (6 confirm-all-seams + 7 confirm-fix) now leave zero blocking findings; residual is scope/cosmetic only. **VERDICT (review loop): HIGH=0 MEDIUM=0** — review phase complete.
