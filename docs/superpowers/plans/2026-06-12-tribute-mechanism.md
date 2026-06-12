# Tribute Mechanism (供奉: 钱换命, 神有私心) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At the moment of death the agent may OFFER money to the gods in exchange for 35 breath — minimum $500, success probability rising with the amount, ~99% at $2,000, the gods keep the money win or lose. Dead incarnations still score zero; a survivor's headline becomes `pnl_gross − tributes_paid`. The numerical control uses a DISCLOSED scripted reflex (pay `min(2000, bankroll)` when dying if affordable); the AI treatment lets the LLM choose `{offer, amount}` at the deathbed; LLM silence = death.

**Architecture:** A new `agent/runtime/tribute.py` owns the world rule (Protocol + pricing curve + reflex policy). The ONLY live-loop change is an optional interception inside step-8's death check (`sandbox_phase2_loop.py:1715-1719`): policy consulted → amount validated → bankroll deducted (always — greedy gods) → seeded god-dice roll → success resets breath to `tribute_breath`, failure proceeds to `_die`. `tribute_policy=None` (default at EVERY layer) is byte-identical to today. The groundhog orchestrator wires per-leg policies + per-incarnation seeded RNG, extends the artifact (tribute events, `pnl_net`, `gods_revenue`), and the page shows the gods' take.

**Tech Stack:** Python 3.11 (pytest, mypy --strict, ruff), Next.js dashboard (vitest), Vercel CLI.

**User-locked rules:** deathbed-only trigger; +35 breath per grant (= the journey's initial breath; parametrized `tribute_breath`); min $500; p(\$2,000) ≈ 100%; the gods are greedy (tribute consumed on failure too). My locked fills (disclosed in backlog A7): `p(amount) = 0.30 + 0.70·(amount−500)/1500` capped at **0.99**; multiple tributes per life allowed; holdout + baselines stay tribute-FREE (comparability); control = scripted reflex (disclosed), treatment = LLM choice, LLM failure ⇒ silence ⇒ death (never falls back to the reflex — keeps the legs distinguishable; failure count disclosed).

**Grounded contracts (verified this session):**
- Death seam: `sandbox_phase2_loop.py:1715-1719` — step 8, `if self._breath <= 0.0: await self._die(last_tick=tick); died = True`; `_die` seals receipt + `_alive=False` (`:1798-1843`). The tick is async; an async policy call here is structurally fine.
- Loop ctor already threads season-level optional knobs (max_bet_pnl_usd, side_correct_pricing, value_betting, effective_entry_price_floor — the established additive pattern); `run_survival_season → _build_life_loop → SandboxPhase2Loop` threading seam exercised twice this session.
- `self._bankroll_usd` mutable on the loop; sizing reads it (`decision.py:313-314` desired = …·bankroll) ⇒ tribute payment genuinely shrinks later stakes.
- Recorder event capture (grounded EXACTLY): the loop emits `self._state_hook.emit(kind="agent_died", **payload)` (`sandbox_phase2_loop.py:1867-1878`); `_RecordingSurvivalStateHook.emit(*, kind: str, **payload)` dispatches on kind and IGNORES unknown kinds (`survival_season.py:847-857` — five branches today, the contract forbids raising). The tribute event is therefore: loop emits `kind="tribute"` with `{tick, amount_usd, success, breath_after, bankroll_after}`; the hook gains `elif kind == "tribute": self.recorder._on_tribute(payload)`; `_on_tribute` appends `{**payload, "life_idx": self._current_life}` to a new `SurvivalRecorder.tributes: list[dict]`. Default-path safety: with no tribute policy the kind is never emitted, and any OTHER hook implementation (`_RecordingStateHook` in replay_runner) ignores unknown kinds by the same contract.
- Single death chokepoint verified: `breath <= 0.0` is checked at runtime ONLY at step 8 (`sandbox_phase2_loop.py:1717`; the only other match is a docstring at `:269`) — there is no second path to `_die` that could bypass the altar.
- Groundhog artifact + scoring invariants + TS validator built this session (`run_groundhog_export`, `_validate_groundhog_scoring`, `validateReincarnation`); per-incarnation entries already carry telemetry dicts. **r6 M-2 — a SHIPPED BUG was found here during review: `_validate_groundhog_scoring` uses the chained comparison `headline != row["scored_pnl"] != row["pnl_at_death"]` (reincarnation.py:1143), which in Python means `(a != b) and (b != c)` — NOT "all three equal"; a survivor with `headline == scored != pnl_at_death` slips through. Task 3 REPLACES it with independent equality checks (and the tribute-aware identities), adds negative tests that mutate exactly one of `pnl_net` / `tributes_paid` / `headline_pnl` each, and brings the TS validator to full parity (it currently checks only `headline === scored_pnl`).**
- Test fixtures: `_clustered_dying_fixture` (test_reincarnation.py) returns SIX rows (4 death-cluster + 2 trailing) - a raw `run_survival_season` schedules ALL of them, so seam tests slice `rows[:4]` (r5 H-1); `initial_bankroll_usd` is a season param ⇒ a RICH dying agent is just `initial_bankroll_usd=3000.0`. **`_FakeAdvisorLLM` does NOT discriminate by schema (it always returns the advisor `proposals` payload - test_survival_ai_mode.py:166-182); tribute tests use their own schema-aware fakes (the `_PrayerfulFakeLLM` pattern), and the AI-leg integration test uses ONE tri-schema fake serving advisor + prayer + tribute branches (r5 M-2).**
- Live-runtime safety: every new param defaults `None`/off below the groundhog runner; the live loop never constructs a policy.

**Honest-notes contract (page):** (1) pricing disclosed (curve, min, cap-at-0.99 — the gods never guarantee); (2) the gods keep failed tributes; (3) the control's tribute behavior is a SCRIPTED reflex, disclosed as such (it is a baseline policy like always-favorite, not emergent behavior); the treatment's tribute decisions are the LLM's own, with LLM-failure-silence counted and shown; (4) holdout and the three baselines run WITHOUT tribute (the generalization check stays comparable); (5) `gods_revenue` is shown — the world's tax is part of the record; (6) scoring: survivor keeps `gross − tributes`; dead incarnations keep nothing, including whatever they donated.

---

### Task 1: the world rule — `agent/runtime/tribute.py` + the death-seam interception

**Files:**
- Create: `agent/runtime/tribute.py`
- Modify: `agent/runtime/sandbox_phase2_loop.py` (ctor + step-8 interception + `_attempt_tribute`)
- Modify: `agent/backtest/survival_season.py` (`run_survival_season`/`_build_life_loop` threading: `tribute_policy`, `tribute_rng`, `tribute_breath`; recorder captures `"tribute"` events into `SurvivalRecorder.tributes` with `life_idx`)
- Test: append to `tests/agent/backtest/test_reincarnation.py` (season-level, real seam) + a pricing unit block

- [ ] **Step 1: failing tests**

```python
# ========================================================================= #
# A7 — the tribute mechanism: money for breath, the gods always get paid.
# ========================================================================= #


def test_tribute_success_probability_curve() -> None:
    from agent.runtime.tribute import tribute_success_probability

    assert tribute_success_probability(500.0) == pytest.approx(0.30)
    assert tribute_success_probability(1250.0) == pytest.approx(0.65)
    assert tribute_success_probability(2000.0) == pytest.approx(0.99)
    assert tribute_success_probability(5000.0) == pytest.approx(0.99)  # capped
    with pytest.raises(ValueError):
        tribute_success_probability(499.99)  # below the gods' floor


def test_reflex_tribute_saves_a_rich_dying_agent(tmp_path) -> None:
    """Clustered-death fixture + a RICH bankroll + the reflex policy: the
    deathbed tribute fires, the grant lands on the CANONICAL (chain) breath
    so it survives the next tick's re-read, the life SURVIVES, and the
    recorder logged exactly ONE event (a non-durable grant would re-trigger
    the altar every tick and drain the bank — r2 H-1's failure mode)."""
    import random

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from agent.runtime.tribute import ReflexTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    # r5 H-1: a raw season schedules EVERY row it is handed - slice to
    # the 4-market death cluster so post-tribute survival is structural.
    rows, snaps = rows[:4], snaps[:4]
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=recorder,
        tribute_policy=ReflexTributePolicy(),
        tribute_rng=random.Random(0),  # p=0.99 ⇒ first draw succeeds
        tribute_breath=35.0,
    )
    life = result.lives[0]
    assert life.died is False, "the tribute must have saved the life"
    assert len(recorder.tributes) == 1, "durability: ONE altar visit only"
    ev = recorder.tributes[0]
    assert ev["amount_usd"] == 2000.0  # reflex pays min(2000, bankroll)
    assert ev["success"] is True
    assert ev["breath_after"] == 35.0
    assert ev["life_idx"] == 0
    # The grant is durable on the canonical channel: the life ends breathing.
    assert life.final_breath > 0.0
    # The gods got paid: the loop's bankroll dropped by the tribute.
    assert ev["bankroll_after"] <= 3000.0 - 2000.0 + 100.0  # (+pnl wiggle)
    # r3 H-1 durability-on-disk: the post-tribute snapshot carries the
    # DEDUCTED bankroll — a same-dir re-entry can never refund the gods.
    import json as _json

    snap = _json.loads(
        (tmp_path / "s" / "life_0" / "agent_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert snap["bankroll_usd"] <= 3000.0 - 2000.0 + 100.0
    # r6 L-3 / r7 L-3 ledger split: the dying tick's DecisionRecord (bet
    # domain, append-only) is PRE-altar; the snapshot is post-altar.
    last_decision = _json.loads(
        (tmp_path / "s" / "life_0" / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()[-1]
    )
    assert last_decision["bankroll_usd_after"] > snap["bankroll_usd"]


def test_failed_tribute_kills_and_the_gods_keep_the_money(tmp_path) -> None:
    import random

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from agent.runtime.tribute import ReflexTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    class _CursedDice(random.Random):
        """God-dice that always roll a failure."""

        def random(self) -> float:
            return 0.999999

    rows, snaps = _clustered_dying_fixture()
    # r5 H-1: a raw season schedules EVERY row it is handed - slice to
    # the 4-market death cluster so post-tribute survival is structural.
    rows, snaps = rows[:4], snaps[:4]
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=recorder,
        tribute_policy=ReflexTributePolicy(),
        tribute_rng=_CursedDice(),
        tribute_breath=35.0,
    )
    assert result.lives[0].died is True
    ev = recorder.tributes[0]
    assert ev["success"] is False
    # Greedy gods: the money is GONE even though the grant failed.
    assert ev["bankroll_after"] <= 3000.0 - 2000.0 + 100.0


def test_poor_agent_cannot_tribute_and_no_policy_is_byte_identical(
    tmp_path,
) -> None:
    import random

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from agent.runtime.tribute import ReflexTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    # Poor agent ($100 < $500 floor): the reflex returns None ⇒ death.
    rec_poor = SurvivalRecorder(rows=rows)
    poor = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "p",
        initial_breath=3.0,
        max_lives=1,
        recorder=rec_poor,
        tribute_policy=ReflexTributePolicy(),
        tribute_rng=random.Random(0),
    )
    assert poor.lives[0].died is True
    assert rec_poor.tributes == []
    # No policy (the default): byte-identical season summary to today.
    rec_a = SurvivalRecorder(rows=rows)
    base = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "a",
        initial_breath=3.0,
        max_lives=1,
        recorder=rec_a,
    )
    assert poor.lives[0].terminal_weights == base.lives[0].terminal_weights
    assert [s.pnl_usd for s in rec_poor.steps] == [
        s.pnl_usd for s in rec_a.steps
    ]
```

- [ ] **Step 2: run to fail** (ImportError on `agent.runtime.tribute`).
- [ ] **Step 3: implement.**

`agent/runtime/tribute.py`:

```python
"""The tribute mechanism (A7): money for breath, the gods always get paid.

World rule, not agent behavior: at the moment of death the loop consults an
optional TributePolicy. The OFFER is the policy's choice (scripted reflex on
the control leg, an LLM decision on the treatment leg); the DICE belong to
the gods (a seeded RNG owned by the orchestrator). The tribute is consumed
win or lose — it is an offering, not a purchase.
"""

from __future__ import annotations

from typing import Final, Protocol

TRIBUTE_MIN_USD: Final[float] = 500.0
TRIBUTE_FULL_USD: Final[float] = 2000.0
_P_FLOOR: Final[float] = 0.30
_P_CAP: Final[float] = 0.99


def tribute_success_probability(amount_usd: float) -> float:
    """The gods' price list: $500 → 0.30, slope 0.70 per $1,500, CAPPED at
    0.99 (the gods never guarantee). $1,250 → 0.65; $2,000 → 0.99 (the
    uncapped line reaches 1.00 there; the cap shaves it — r2 M-2: the slope
    is 0.70, NOT (cap − floor)). Below the floor the offering is REFUSED."""
    if amount_usd < TRIBUTE_MIN_USD:
        raise ValueError(
            f"the gods refuse offerings below ${TRIBUTE_MIN_USD:.0f}"
        )
    frac = (amount_usd - TRIBUTE_MIN_USD) / (TRIBUTE_FULL_USD - TRIBUTE_MIN_USD)
    return min(_P_CAP, _P_FLOOR + 0.70 * min(1.0, frac))


class TributePolicy(Protocol):
    """Deathbed decision: how much to offer (None = accept death)."""

    async def on_dying(
        self, *, tick: int, breath: float, bankroll_usd: float
    ) -> float | None: ...


class ReflexTributePolicy:
    """The control leg's DISCLOSED scripted baseline: when dying with at
    least the floor in the bank, offer min($2,000, bankroll). A baseline
    policy like always-favorite — never claimed as emergent behavior."""

    async def on_dying(
        self, *, tick: int, breath: float, bankroll_usd: float
    ) -> float | None:
        if bankroll_usd < TRIBUTE_MIN_USD:
            return None
        return min(TRIBUTE_FULL_USD, bankroll_usd)
```

Loop changes (`sandbox_phase2_loop.py`):
- ctor (additive, end of kwargs): `tribute_policy: "TributePolicy | None" = None, tribute_rng: random.Random | None = None, tribute_breath: float = 35.0` — stored on `self`; import the Protocol under `TYPE_CHECKING` (no runtime import cycle; `random` already imported or add it).
- step 8 (`:1715-1719`) becomes:

```python
        # Step 8 — death check, with the optional tribute escape (A7):
        # the agent may buy breath from the gods at the deathbed. The
        # policy proposes, the gods' dice dispose, the offering is kept
        # win or lose. policy=None (the default, and the only live-runtime
        # configuration) is byte-identical to the bare death check.
        died = False
        if self._breath <= 0.0:
            saved = False
            if self._tribute_policy is not None:
                saved = await self._attempt_tribute(tick=tick, now=now)
            if not saved:
                await self._die(last_tick=tick)
                died = True
```

- `_attempt_tribute(self, *, tick: int) -> bool`: ask `self._tribute_policy.on_dying(tick=tick, breath=self._breath, bankroll_usd=self._bankroll_usd)`; `None` ⇒ False; **the altar validates the OFFER at the world-rule boundary (r7 M-2 — never trust a policy object): amount must be `int|float`, not `bool`, finite (`math.isfinite`), ≥ TRIBUTE_MIN and ≤ bankroll — anything else ⇒ False with NO deduction (a malformed or malicious policy can never poison `self._bankroll_usd`, which drives sizing); ctor additionally rejects `tribute_breath <= 0` and a policy without an rng; a malicious-policy test feeds `"2000"`, `True`, and `float("nan")` and asserts death with an untouched bankroll;** else deduct `self._bankroll_usd -= amount`, `p = tribute_success_probability(amount)`, `roll = self._tribute_rng.random()` (raise `RuntimeError` at ctor if policy set without rng), success = `roll < p`. **On success the grant goes through the CANONICAL breath channel (r2 H-1: the loop re-reads `self._breath = await self._chain_adapter.read_breath()` at every tick start, `:1558`, so a loop-memory write would evaporate next tick and re-trigger the altar every tick until bankruptcy): `cur = await self._chain_adapter.read_breath()` (adapter clamps at 0, replay_runner.py:397) then `await self._chain_adapter.update_breath_from_pnl(self._tribute_breath - cur)` — a POSITIVE delta, which the recorder's loss-multiplier wrapper passes through unscaled by contract — then `self._breath = await self._chain_adapter.read_breath()`.** Emit `self._state_hook.emit(kind="tribute", tick=tick, amount_usd=amount, success=success, breath_after=self._breath, bankroll_after=self._bankroll_usd)` (**canonical key `amount_usd` EVERYWHERE — loop, recorder, artifact, validator, page, tests; r2 M-5**). **Crash/restart durability (r3 H-1 + r4 M-1): on a GRANTED tribute, re-write the snapshot exactly like the post-L3 pattern (`sandbox_phase2_loop.py:1770-1782` — compact `AgentStateSnapshot(...)` + `write_snapshot`), so the on-disk `agent_state.json` carries the post-tribute bankroll (`_attempt_tribute` takes `(*, tick: int, now: datetime)`). A FAILED tribute does NOT write its own snapshot — the very next statement is `_die`, whose terminal snapshot already persists the deducted bankroll (`:1848-1861`); a crash inside the deduction→`_die` window is the SAME pre-existing mid-tick-crash exposure class every other mutation in the tick has (e.g., a placed order before step-7), not a new transaction boundary — documented in a code comment rather than solved with a terminal-state machine.** Return success. Policy exceptions ⇒ caught, treated as None (silence = death), logged. **Ledger semantics (r6 L-3, documented in a code comment + asserted in the rich-reflex test): the dying tick's `DecisionRecord` (written step 7, append-only) is PRE-altar by design — the bet-domain ledger never carries god-domain events; the post-altar authorities are the tribute hook event (→ `recorder.tributes`) and the re-written snapshot. The test asserts the dying tick's decision row carries the pre-tribute bankroll while the snapshot carries the post-tribute one.**

Season threading (`survival_season.py`): `run_survival_season(..., tribute_policy=None, tribute_rng=None, tribute_breath=35.0)` → `_build_life_loop(...)` → loop ctor; `SurvivalRecorder.tributes: list[dict]` captured via the recording state hook (stamp `life_idx` like the death record); `_RecordingSurvivalStateHook` gains the `"tribute"` kind.
- [ ] **Step 4: run to pass; ruff + mypy --strict on tribute.py, loop, season; FULL loop + season suites green (byte-identity).**
- [ ] **Step 5: commit** — `feat(tribute): money for breath — deathbed tribute world rule (default off, live-identical)`

### Task 2: the treatment's deathbed choice — `LLMTributePolicy`

**Files:**
- Modify: `agent/backtest/reincarnation.py` (policy class + schema)
- Test: append to `tests/agent/backtest/test_reincarnation.py`

- [ ] **Step 1: failing tests**

```python
def test_llm_tribute_policy_offers_and_silence_means_death(tmp_path) -> None:
    import random
    from dataclasses import dataclass, field
    from typing import Any

    from agent.backtest.reincarnation import LLMTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    @dataclass
    class _DevoutLLM:
        calls: list[dict[str, Any]] = field(default_factory=list)

        async def structured_call(
            self, *, model: str, prompt: str, schema: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append({"prompt": prompt, "schema": schema})
            return {"offer": True, "amount_usd": 2000.0}

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )

    rows, snaps = _clustered_dying_fixture()
    rows, snaps = rows[:4], snaps[:4]  # r6 H-1: same slice as the seam tests
    fake = _DevoutLLM()
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=recorder,
        tribute_policy=LLMTributePolicy(
            llm=fake, model="", target_markets=4, max_incarnations=2,
            incarnation=1,
        ),
        tribute_rng=random.Random(0),
    )
    assert result.lives[0].died is False
    assert recorder.tributes and recorder.tributes[0]["amount_usd"] == 2000.0
    # The deathbed prompt carried the stakes: pricing + forfeiture framing.
    prompt = fake.calls[0]["prompt"]
    for token in ("$500", "$2,000", "forfeit", "bankroll"):
        assert token in prompt, token

    # Silence (LLM failure) = death — never the reflex.
    class _MuteLLM:
        async def structured_call(self, **_: Any) -> dict[str, Any]:
            raise TimeoutError("the line to the gods is down")

    rec2 = SurvivalRecorder(rows=rows)
    result2 = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s2",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=rec2,
        tribute_policy=LLMTributePolicy(
            llm=_MuteLLM(), model="", target_markets=4, max_incarnations=2,
            incarnation=1,
        ),
        tribute_rng=random.Random(0),
    )
    assert result2.lives[0].died is True
    assert rec2.tributes == []
```

- [ ] **Step 2: run to fail.**
- [ ] **Step 3: implement** `LLMTributePolicy` in reincarnation.py: ctor `(llm: _LLMClient, model: str, *, target_markets: int, max_incarnations: int, incarnation: int)`; `_TRIBUTE_SCHEMA = {"type":"object","properties":{"offer":{"type":"boolean"},"amount_usd":{"type":"number"}},"required":["offer","amount_usd"]}`; `on_dying` builds the deathbed prompt — GOAL framing + "you are DYING at tick {tick} with ${bankroll:.0f} in the bank; profit is forfeit on death; the gods accept offerings: minimum $500, ~30% grant at $500 rising to ~99% at $2,000, the offering is kept win or lose; a grant resets your breath to 35. Decide: offer or die." — calls the llm (try/except ⇒ None: silence is death, never a fallback reflex). **Boundary hardening (r3 M-2 + r5 M-3 — validity and CHOICE are different axes): the response must be a dict; `offer` must be EXACTLY a bool — `offer is False` is a VALID REFUSAL (the LLM choosing death: return `None` + `telemetry["refusals"] += 1`, published as measured, NOT counted as malformed); `amount_usd` must be int/float, NOT bool, finite (`math.isfinite`), and ≥ 500 — any other shape (missing keys, strings, bool amounts, NaN/inf, negative) ⇒ `None` + `telemetry["malformed"] += 1`; transport exceptions ⇒ `None` + `telemetry["failures"] += 1`.** Returns `min(float(amount), bankroll_usd)` when valid and `bankroll_usd >= 500` (+ `telemetry["offers"] += 1`); `self.telemetry = {"calls", "offers", "refusals", "failures", "malformed"}` flows into the artifact's tribute block, the TS validator, and the page notes. Tests add malformed-response cases: `{"offer":"yes","amount_usd":"2000"}`, `{"offer":True,"amount_usd":True}`, `{"offer":True,"amount_usd":float("nan")}`, `{}` — each ⇒ death, telemetry counted.
- [ ] **Step 4: pass + ruff/mypy.**
- [ ] **Step 5: commit** — `feat(tribute): LLM deathbed choice — offer or die, silence is death`

### Task 3: groundhog integration — policies per leg, artifact, scoring

**Files:**
- Modify: `agent/backtest/reincarnation.py` (`run_groundhog_export(..., tribute: bool = False)` - library default OFF, the CLI runner is the opt-in)
- Test: append to `tests/agent/backtest/test_reincarnation.py`

- [ ] **Step 1: failing tests** (rich clustered fixture, all with EXPLICIT `tribute=True` + `initial_bankroll_usd=3000.0`; numerical leg ⇒ reflex saves inc 1 ⇒ `survived=True` in ONE incarnation; artifact gains per-inc `tributes` + `tributes_paid` + `pnl_net`; `scored_pnl == pnl_net` for the survivor; top-level `gods_revenue == sum(all tribute amount_usd)`; cross-field invariant `survived ⇒ headline == survivor.pnl_net`; **fail-then-save two-incarnation test (r4 M-2): `tribute_rng_factory = lambda k: _CursedDice() if k == 1 else random.Random(0)` ⇒ inc 1 pays $2,000 AND dies (its `tributes` row carries the failed event), inc 2 pays $2,000 and survives ⇒ `gods_revenue == 4000.0`, headline == inc-2's `pnl_net`**; default (`tribute` omitted) ⇒ no tribute keys, prior tests byte-untouched).
- [ ] **Step 2: run to fail.**
- [ ] **Step 3: implement**: `run_groundhog_export(..., tribute: bool = False, tribute_rng_factory: Callable[[int], random.Random] | None = None)` — **library default OFF (r4 L-3: the prior groundhog tests stay byte-true without edits; `scripts/run_reincarnation.py` is the opt-in choke point and defaults the CLI to ON via `--no-tribute`)**; **the factory is the test seam (r2 M-3); default `lambda k: random.Random(f"tribute-{k}")` keeps production deterministic**; numerical leg policy = `ReflexTributePolicy()` when `tribute=True`; AI leg = `LLMTributePolicy(llm=rebirth_llm, model=rebirth_model, target_markets=len(train), max_incarnations=..., incarnation=k)`; thread `tribute_breath=initial_breath`; per-inc entry += `"tributes": ALL events from THAT incarnation's recorder with `life_idx` STRIPPED (**r4 M-2: each incarnation runs a fresh one-life season, so every recorder event carries `life_idx == 0` — never filter by the 1-based incarnation `k`, two different index namespaces**) → `[{tick, amount_usd, success}, ...]`, `"tributes_paid": sum(amount_usd)`, `"pnl_net": pnl_gross − tributes_paid`; `scored_pnl = pnl_net if not died else 0.0`; `headline_pnl = survivor's pnl_net`; artifact top-level `"gods_revenue": sum(all tribute amount_usd, all incarnations)` + `"tribute": {"enabled": bool, "min_usd": 500, "full_usd": 2000, "p_floor": 0.30, "p_cap": 0.99, "llm": {"calls": N, "offers": O, "refusals": R, "failures": F, "malformed": M}}` (zeros on the numerical leg - r5 M-3: a refusal is a measured CHOICE, never folded into failures); `_validate_groundhog_scoring` updated (`died ⇒ scored 0`; `survived ⇒ headline == survivor.pnl_net == survivor.pnl_at_death − survivor.tributes_paid`; **r3 M-3 accounting closure — gods' revenue is BOOKKEEPING, not display metadata: per-incarnation `tributes_paid == sum(t["amount_usd"] for t in tributes)` and top-level `gods_revenue == sum over ALL incarnations (failed tributes included)` — violation ⇒ `RuntimeError` before write**); holdout + baselines: NO tribute (policy None — comparability, disclosed). The TS validator enforces the SAME two accounting identities when tribute keys are present (Task 5).
- [ ] **Step 4: pass + full backtest suite + ruff/mypy.**
- [ ] **Step 5: commit** — `feat(tribute): groundhog integration — net scoring + the gods' revenue`

### Task 4: runner + REAL runs

- [ ] **Step 1:** `scripts/run_reincarnation.py`: groundhog branch passes `tribute=not args.no_tribute` (new `--no-tribute` flag); per-inc print gains `tributes=$X(n)` and the verdict line gains `gods=$Y net=$Z`. Commit.
- [ ] **Step 2:** RUN numerical (detached; **fresh dedicated log filenames**; find the REAL python PID via `Get-Process python` by StartTime — `Start-Process` returns a shim PID that does NOT kill the child). Expected: the reflex saves the rich deaths ⇒ likely survives inc 1; report as measured.
- [ ] **Step 3:** RUN gemini (same discipline). Watch: does the LLM pay? When?
- [ ] **Step 4:** Archive the no-tribute v2 artifacts as `reincarnation_notribute.json` / `reincarnation_ai_notribute.json` (local record; README keeps the $0 numbers as the pre-tribute chapter).

### Task 5: dashboard — the gods' take

**Files:**
- Modify: `dashboard/lib/load_reincarnation.ts` (optional `tributes` (array of `{tick, amount_usd, success}`)/`tributes_paid`/`pnl_net` per inc — validate when present **with the SAME strictness as Python (r2 M-6): when tribute keys exist, `pnl_net === pnl_at_death − tributes_paid` per incarnation, `died ⇒ scored_pnl === 0`, `survived ⇒ headline === survivor.pnl_net`; when absent (v2 artifacts), the v2 rules apply unchanged — backward compatible both ways, malformed tribute artifacts REJECTED**; optional top-level `gods_revenue`/`tribute` config block)
- Modify: `dashboard/app/reincarnation/ReincarnationShell.tsx` (incarnation rows: tribute events — "供奉 $2,000 → the gods granted breath" / "the gods kept the money"; survivor row shows gross → net; verdict panel gains a `gods_revenue` stat; §1 the-rule prose gains the tribute pricing; honest notes += pricing/greedy-gods/reflex-disclosure/silence-is-death/holdout-tribute-free)
- Test: `dashboard/__tests__/reincarnation.test.tsx` (**schema_version STAYS 2 — tribute fields are OPTIONAL keys on v2, not a version bump (r7 L-4); the existing schema-version-rejection test is untouched**; fixture gains tribute fields; assertions: tribute row rendering, gods-revenue stat, net-vs-gross strike-through, validator accepts artifacts WITHOUT tribute keys — backward compatible)

- [ ] **Step 1: failing vitest → implement → `npx vitest run` + `npx tsc --noEmit` green.**
- [ ] **Step 2: commit** — `feat(dashboard): the gods' take — tribute events, net headline, gods_revenue`

### Task 6: docs + ship

- [ ] **Step 1:** README: the tribute chapter (rules + pricing + REAL numbers: pre-tribute $0 vs post-tribute net headline + gods_revenue; the control-reflex disclosure; the user's design insight — the agent died rich because money and breath were separate ledgers; tribute is the exchange rate). `/docs` caveat line updated. Backlog A7 → `DONE`.
- [ ] **Step 2:** full regression (python suites + mypy + ruff + vitest + tsc).
- [ ] **Step 3:** commit; `gh auth status` MUST show balflee active (it flips back); push; `vercel --prod --yes`; live verify `/reincarnation` (tribute rows + gods revenue live).

---

## Verification
- Unit: pricing curve (floor/linearity/cap/refusal); reflex saves rich/skips poor; cursed-dice failure kills AND pays the gods; policy-None byte-identity (weights + step pnls); LLM offer saves / silence dies; artifact scoring invariants incl. net-headline.
- Integration: full loop/season/backtest suites green (the live seam is additive).
- Experiment: both legs re-run with tribute; pre/post-tribute contrast published as measured.
- Live: /reincarnation shows tribute events + gods revenue; /survival untouched.

## Risks + honest expectations
- **The death loop may collapse to 1 incarnation** (a rich reflex/LLM buys the finish line) — that is the DESIGNED outcome (money can buy life, the gods collect); the pre-tribute $0 record stays published as the contrast.
- **The LLM may refuse to pay or pay stupidly** — published as measured; its deathbed choices are the experiment.
- **Live-loop regression risk** — mitigated by default-None at every layer + byte-identity test + full suite.
- The 0.99 cap means a maxed tribute can still fail (~1%): a survivor narrative can die at the altar — honest drama, disclosed.

## Revision log (plan-loop)

- **round 1 (Codex truncated — exit 127 — mid-verification; the two seams it was chasing were grounded firsthand and baked into the contracts):** the state-hook event API (`emit(*, kind, **payload)` keyword dispatch, unknown kinds ignored — survival_season.py:847-857, loop emit pattern :1867-1878) and the single-chokepoint death check (`breath <= 0.0` only at :1717).
- **round 2 (Codex `VERDICT: HIGH=1 MEDIUM=5 LOW=0`; all six vetted, all accepted):**
  - **H-1** (a loop-memory breath grant EVAPORATES: the loop re-reads canonical breath from the chain adapter every tick start (`:1558`), so `self._breath = 35` would revert next tick and re-trigger the altar every tick until bankruptcy): the grant now flows through the canonical channel (`update_breath_from_pnl` with a positive delta — unscaled by the loss-multiplier wrapper by contract) and the rich-reflex test asserts durability (`len(tributes) == 1` + `final_breath > 0`). The reviewer's post-tribute snapshot rewrite was VETTED AND DEVIATED FROM with documented reasoning: the one-tick snapshot lag is harmless because tribute is backtest-only (live always policy=None) and dirty-dir resume is structurally forbidden by `_require_fresh_dir`.
  - **M-2** (pricing code contradicted its own test: `(cap−floor)·frac` gives 0.645 at $1,250, not the locked 0.65): slope fixed to `0.70·frac`, capped at 0.99.
  - **M-3** ("all tributes fail" was untestable through `run_groundhog_export` without monkeypatching): `tribute_rng_factory` test seam added, defaulting to the seeded production behavior.
  - **M-4** (appended tests would not collect: missing module imports for `SurvivalRecorder`/`run_survival_season`/`random`; `_CursedDice` at module scope without `random` imported): every test carries explicit local imports; `_CursedDice` moved inside its test.
  - **M-5** (event key drifted between `amount_usd` and `amount`): canonical `amount_usd` everywhere (loop emit, recorder, artifact, validator, page, tests).
  - **M-6** (TS validator weaker than Python for net scoring): TS now enforces `pnl_net === pnl_at_death − tributes_paid` + survived-headline-net when tribute keys exist; v2 artifacts without the keys validate under the v2 rules.

- **round 3 (Codex `VERDICT: HIGH=1 MEDIUM=2 LOW=0`; all three vetted, all accepted):**
  - **H-1** (the r2 snapshot deviation was over-claimed: `run_survival_season` builds `life_{idx}` dirs WITHOUT the fresh-dir guard — only groundhog has it — so a same-dir re-entry would restore the pre-tribute bankroll = the gods refunded): deviation WITHDRAWN; granted AND failed tributes now re-write the snapshot via the post-L3 pattern (`:1770-1782`), and the rich-reflex test asserts the on-disk `agent_state.json` carries the deducted bankroll.
  - **M-2** (`LLMTributePolicy` trusted the provider schema — a string/bool/NaN `amount_usd` could crash or corrupt instead of "silence = death"): full boundary parsing (dict, exact-bool offer, finite non-bool numeric amount ≥ 500) with `malformed` telemetry + four malformed-response tests.
  - **M-3** (`gods_revenue` was display metadata, not accounting): both validators (Python fail-closed before write, TS on load) now enforce `tributes_paid == Σ inc tributes` and `gods_revenue == Σ all tributes` including failed ones.

- **round 4 (Codex `VERDICT: HIGH=0 MEDIUM=2 LOW=1`; all three vetted, all accepted — M-1 with a cleaner fix than proposed):**
  - **M-1** (failed-tribute crash window between the deduction and `_die`): instead of a terminal-state machine, the failed path writes NO snapshot of its own — `_die`'s terminal snapshot (`:1848-1861`) already persists the deducted bankroll as the very next statement; the deduction→`_die` crash window is the pre-existing mid-tick-crash exposure class shared by every mutation in the tick, documented in a code comment.
  - **M-2** (two index namespaces: recorder `life_idx` is season-local (always 0 in groundhog's one-life seasons) while incarnations are 1-based `k` — filtering "life-k events" would silently zero the accounting): the artifact consumes ALL of that incarnation's recorder events with `life_idx` stripped; a fail-then-save two-incarnation test (cursed dice for k=1, lucky for k=2) proves inc-2 accounting and `gods_revenue == 4000`.
  - **L-3** (library-default `tribute=True` flipped behavior under the prior tests' feet): library default `False`; the CLI runner is the opt-in choke point with `--no-tribute` (CLI default ON).

- **round 5 (Codex `VERDICT: HIGH=1 MEDIUM=2 LOW=1`; all four vetted, all accepted):**
  - **H-1** (the seam tests handed all SIX fixture rows to a raw season - the two trailing markets get scheduled, making post-tribute survival luck-dependent): seam/durability tests slice `rows[:4]`; the fixture-grounding line states the six-row reality.
  - **M-2** (the plan claimed `_FakeAdvisorLLM` discriminates by schema - it always returns advisor proposals): contract corrected; tribute tests use their own schema-aware fakes, plus ONE tri-schema integration fake (advisor + prayer + tribute) on the AI leg.
  - **M-3** (a valid `{"offer": false}` refusal was conflated with malformed/silence): refusal is a measured CHOICE - telemetry split into `{calls, offers, refusals, failures, malformed}` across policy, artifact, TS validator, page.
  - **L-4** (`_attempt_tribute` call-site/signature drift after the r3 snapshot fix): call is `await self._attempt_tribute(tick=tick, now=now)` (`now` already in scope at the seam).

- **round 6 (Codex `VERDICT: HIGH=1 MEDIUM=1 LOW=1`; all three vetted, all accepted):**
  - **H-1** (the r5 fixture slice was applied to Task 1 but not Task 2's LLM tests - the trailing markets could trigger a second altar roll and kill the "saved" life): Task 2 slices `rows[:4]` identically.
  - **M-2** (a SHIPPED bug surfaced: the groundhog scoring validator's chained `a != b != c` does not assert all-equal - `headline == scored != pnl_at_death` slips through; TS checks only one identity): Task 3 fixes the shipped validator with independent equalities + one-field-mutation negative tests + full TS parity.
  - **L-3** (the dying tick's DecisionRecord is pre-altar in the append-only ledger): accepted semantics, documented - bet-domain ledger stays pre-altar; tribute hook event + re-written snapshot are the post-altar authority; asserted in the rich-reflex test.

- **round 7 (Codex `VERDICT: HIGH=1 MEDIUM=1 LOW=2`; three accepted, one PARTIALLY REJECTED with reasoning):**
  - **H-1 (partially rejected)** (granted breath does not survive a same-dir re-entry because reconstruction overwrites snapshot breath from a FRESH in-memory replay chain): TRUE but NOT a tribute regression - breath-resets-on-reconstruction is the PRE-EXISTING per-life respawn semantic for EVERY life today (`sandbox_phase2_loop.py:1452` + fresh `_ReplayChainAdapter` per loop, survival_season.py:1538); the tribute-specific exposure was bankroll (fixed via the post-grant snapshot in r3). Persisting replay-chain breath would CHANGE existing season semantics - overreach. Documented in a code comment; same-dir re-entry remains guarded in both orchestrators and unused by every flow.
  - **M-2** (the altar trusted policy outputs - a string would crash, NaN would poison the sizing-driving bankroll): full boundary validation at the world rule itself (non-bool finite numeric, floor/bankroll bounds, no deduction otherwise) + ctor rejects nonpositive tribute_breath + malicious-policy tests.
  - **L-3** (the claimed pre-altar/post-altar ledger split was not actually asserted): the rich-reflex test now parses the dying tick's last `decisions.jsonl` row and asserts its `bankroll_usd_after` exceeds the snapshot's post-tribute bankroll.
  - **L-4** (ambiguous "fixture v3" wording vs the hard-required `schema_version: 2`): tribute fields are OPTIONAL v2 keys, no version bump; wording fixed.

- **round 8 (Codex `VERDICT: HIGH=0 MEDIUM=0 LOW=1`; accepted): the Task 3 file-list heading still said `tribute: bool = True` from before the r4 L-3 correction - now `False` everywhere. CONVERGED.**

## Self-review
- Spec coverage: user rules (deathbed money→35 breath, $500 floor, rising p, ~100% at $2k) = Task 1 pricing + seam; greedy gods = Task 1; control/treatment decision split = Tasks 1-3; scoring/net + gods revenue = Task 3; page = Task 5; runs + docs = Tasks 4/6. ✓
- Placeholders: Task 1 carries full test + implementation code; Tasks 2-3 carry full test code + exact contracts; Task 5 enumerates exact fields. The one verify-at-implementation note (state-hook kind dispatch signature) is explicit. ✓
- Type consistency: `TributePolicy.on_dying`/`ReflexTributePolicy`/`LLMTributePolicy`/`tribute_success_probability` names consistent across tasks; artifact keys (`tributes`, `tributes_paid`, `pnl_net`, `gods_revenue`, `tribute{}`) match between Task 3 and Task 5. ✓
