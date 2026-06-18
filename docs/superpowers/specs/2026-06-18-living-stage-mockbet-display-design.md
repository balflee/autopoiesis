# Living Stage — Mock-Bet Showcase Dashboard (Design)

- **Date:** 2026-06-18
- **Status:** Design — pending user review, then writing-plans
- **Audience for the product:** Arbitrum Open House London + Buildathon judges/visitors (narrative + impact; mechanism shown but not foregrounded)
- **Language of the product UI:** English
- **Owner conversation language:** Chinese (this spec is the English repo artifact; the design was approved via a visual mockup)

---

## 1. Goal

Build a single, cinematic, real-time dashboard — **the Living Stage** — that shows the running mock-bet agent as a *living organism* that:

1. places **real paper bets on live Polymarket tennis** (real odds, mock fills),
2. **thinks** (5-engine fusion signals + a one-line reasoning), and
3. lives inside a **divine economy**: it pays the gods' rent (tithe), offers tribute on its deathbed, and — when it finally dies — is **reborn as a new incarnation**, while the **Divine Treasury** accumulates across lives.

The 5-second takeaway is **"it's alive and placing real bets."** Everything else (the gods, the mind, the lineage) is supporting depth.

### Non-negotiable honesty constraints (the project's "honest ledger" DNA)
- **No backtest data on this surface.** Every number is produced by the running agent. The Divine Treasury starts at **$0** and grows live.
- **No manufactured action.** No fake highlight reels, no scripted demo match. When no tennis market is bettable, the screen honestly shows scanning + heartbeat — and the gods' rent gives the screen real, continuous survival drama anyway (see §3).
- **Real dice.** Tribute success is the gods' seeded RNG; an offering can be paid and still fail.
- **Breath-paid tithe is rendered as breath**, not converted to a fabricated USD figure.

---

## 2. Design decisions locked (brainstorm outcomes)

| Decision | Choice |
|---|---|
| Primary audience | Roadshow / judges — narrative + impact |
| Hero (visual center) | "It's alive · placing real bets" — the organism + the current bet |
| Idle handling | Pure real-time, honest; rent provides organic drama |
| Divine treasury source | **Wire the real economy into the running mock-bet loop** (never backtest) |
| Afterlife | **Real death → reincarnation** (reborn as a new incarnation; treasury accumulates across lives) |
| Layout | **A · Living Stage** (single continuous stage), all-English |

---

## 3. The experience — Living Stage layout (5 zones)

A single page, one protagonist, one continuous live stage.

```
┌─────────────────────────────────────────────────────────────────────┐
│ ◆ AUTOPOIESIS   live mock-bet · Polymarket tennis    Incarnation #3 · ALIVE │  Top bar
├───────────────┬───────────────────────────────┬─────────────────────┤
│  ⛧ THE GODS   │        THE ORGANISM           │   ⊕ THE MIND        │
│  (Z2 left)    │        (Z1 center)            │   (Z4 right)        │
│               │   ♥ breath ring 72            │  5 engine bars      │
│  TITHE −$40   │   $1,240 bankroll · 3rd life  │  (signed, ± vs 0)   │
│  TRIBUTE      │                               │  fused edge +0.041  │
│   $2000→dice  │   ── THE ACT (Z3) ──          │   › fee floor ✓     │
│   → SURVIVED  │   Sinner def. Alcaraz?        │                     │
│               │   YES .58 / NO .42            │  reasoning (1 line) │
│  TREASURY ⛩   │   ▸ BET YES $50 @ .58         │                     │
│   Σ $0 → live │   holding to resolution       │                     │
├───────────────┴───────────────────────────────┴─────────────────────┤
│ ⟲ LINEAGE   life1 ✝ rent  —  life2 ✝ tribute refused  —  life3 ● ALIVE │  Z5 bottom
└─────────────────────────────────────────────────────────────────────┘
```

- **Z1 — The Organism (center, hero):** a breathing/pulsing breath ring (breath 0–100), bankroll, current incarnation number, and alive/dying/terminal state. This is the protagonist and the visual center.
- **Z2 — The Gods (left rail, antagonist):** a live event stream of divine interventions — **tithe** (rent) deductions, **tribute** (deathbed offerings) with the dice outcome and survived/died, and deaths. Anchored by the **Divine Treasury** total (cumulative gods revenue, from $0 upward).
- **Z3 — The Act (center, under organism):** what it's doing *right now* — the current Polymarket market (players + YES/NO odds), the agent's bet (side / size / fill price / status), and the last settled result. When idle: "scanning global tennis markets — no bettable match," heartbeat still beating.
- **Z4 — The Mind (right rail):** the 5 fusion-engine signals as signed diverging bars (tennis_technical, market_momentum, surface_advantage, head_to_head, rest_recency), the fused edge vs the fee floor, and a one-line reasoning ("why I bet / why NO_BET").
- **Z5 — Lineage (bottom strip):** the reincarnation timeline — each past life with cause of death, lifespan, and a marker; the current life highlighted; "the gods earned $N from this soul."

Aesthetic: the existing **abyss** design system (dark, `--ab-*` tokens, Instrument Serif display + IBM Plex Mono), same variant the `/mock` page uses.

---

## 4. Architecture

### 4.0 Two tracks
- **Track A — Backend:** make the divine economy + reincarnation real in the running loop, and emit the data.
- **Track B — Frontend:** the `/living` Living Stage page reading that real data.

The live sandbox dashboard is **poll-driven** (2s poll of `/api/sandbox` → `loadSandboxBundle`), **not** SSE. So Track B rides the existing poll; no SSE change is required (an SSE addition is an optional follow-up).

### 4.1 Backend — divine economy wiring

Today the live loop is constructed with `tribute_policy=None`, `divine_tithe=False` (deliberate: keeps the live death-check byte-identical to a bare loop), and its `state_hook` is `_NoopStateHook`, which **drops** the `tribute`/`tithe`/`agent_died` events the loop already emits.

Changes:
1. **Turn the economy on** in the production loop builder: pass `tribute_policy=ReflexTributePolicy()`, `divine_tithe=True`, and a seeded `tribute_rng` (the gods' dice). Gate the whole feature behind an env flag so the default path is unchanged.
2. **Route the events to disk:** replace `_NoopStateHook` with a `_SandboxStateHook` that wraps the loop's `SandboxStateWriter`:
   - `kind='tribute'` → `writer.append_tribute(TributeRecord)`
   - `kind='tithe'` → `writer.append_tithe(TitheRecord)`
   - `kind='agent_died'` → `writer.append_death(DeathRecord)`
   The hook must be constructed *with* the writer instance (built before the loop) to avoid a TOCTOU gap.

### 4.2 Backend — reincarnation supervisor (the hard part)

Today death is one-way terminal on the live path: `_die()` mints a Tombstone, flips `_alive=False`, persists a terminal snapshot, and `run()` returns; `AgentRunner` then idles. There is no live respawn.

Add **`agent/runtime/incarnation_supervisor.py` — `LiveIncarnationSupervisor`**, structurally cloned from the *validated* backtest respawn loop (`agent/backtest/survival_season.py:1898-2007`):

- Exposes **one** `async run()` so `AgentRunner`'s existing single-task model is unchanged. `start()` awaits `supervisor.run()`; `stop()` cancels it (CancelledError propagates into the current incarnation's between-tick sleeper — the existing graceful-cancel seam).
- Loop: `for idx in 0..max_incarnations`: build a **fresh** single-life loop, `await loop.run()`; on `RunSummary.died==True`, capture `loop.weights` and pass them as `initial_weights` to the next incarnation; stop on survival-to-end, cap reached, or `respawn_policy`.

**Resets per incarnation** (mirrors `life_{idx}`):
- fresh `state_dir/incarnation_{idx}` (cold-start reconstruction never re-reads the prior life's `open_bets.jsonl`);
- **a fresh `chain_adapter`** — ⚠️ **critical correction**: the current loop factory closes over one *stateful* `chain_adapter` (its breath is already 0 after a death); reusing it would re-die instantly. The supervisor must mint a new adapter every life.
- breath/bankroll reset to the cold-start constants.

**Carries across incarnations:**
- the **single shared `WeightUpdater`** instance (EMA backbone survives deaths) — built once at supervisor start;
- `loop.weights` at death → next life's `initial_weights`;
- **one shared `L3CostGuard` + `StrategyAdvisor`** instance — ⚠️ **critical correction**: `_make_prod_strategy_advisor` builds a fresh `L3CostGuard.from_env()` per call, so calling it per-incarnation would reset the monthly LLM budget every life (runaway spend). Build the advisor once, inject the same instance into every life.
- monotonic `incarnation_number` (supervisor-owned, scoped per run).
- **persisted across server restarts** via `incarnation_manifest.json` at the root (`run_id`, `current_incarnation_idx`, `carry_weights_hash`, `max_incarnations`), written atomically on each transition so a crash between deaths resumes the lineage instead of restarting at incarnation 0.

**Not carried (LIVE is v3.0 baseline):** the backtest genome layer (`genome_dict`/`apply_genome_deltas`), rebirth/death-window advisor re-triggering, and the fixed-schedule cursor — all backtest-only. The supervisor is a **pure orchestrator of respawns**, not a decision-maker.

**Supporting refactor:** extract `_build_one_incarnation_loop(state_dir, chain_adapter, initial_weights, shared_weight_updater, shared_advisor)` from the current 0-arg `_build_production_loop_factory`; keep the 0-arg factory as a thin wrapper (incarnation 0, fresh adapter) so non-reincarnation and test callers are byte-unchanged.

### 4.3 Data contract

**New record models** (`agent/data/sandbox_state.py`, `extra='forbid'`, mirroring `BetRecord`):
- `TributeRecord`: `type='tribute'`, `tribute_id`, `ts`, `tick`, `amount_usd`, `success`, `breath_after`, `bankroll_after`, `dice_roll` (omit-when-None).
- `TitheRecord`: `type='tithe'`, `tithe_id`, `ts`, `tick`, `paid_usd` (0.0 if breath paid), `breath_cost` (0.0 if cash paid), `breath_after`, `bankroll_after`.
- `DeathRecord`: `death_id`, `ts`, `incarnation_number`, `agent_id`, `last_tick`, `cause`, `kill_tx_hash`, `tombstone_token_id`, `tombstone_tx_hash`, `final_bankroll_usd`, `final_weights_hash`, `memory_bank_cid`, `last_words`.

**New JSONL streams** (reuse the verbatim atomic+lock `_append_jsonl` helper):
- `gods_treasury.jsonl` — single interleaved tribute+tithe stream, discriminated by `type` (one read path).
- `deaths.jsonl` — one `DeathRecord` per incarnation death (drives the lineage timeline + incarnation count).

**Snapshot field:** add `incarnation_number: int = 0` to `AgentStateSnapshot` (stored; stamped by the loop from the supervisor-injected idx; `AliasChoices`/`extra='forbid'` back-compat so old snapshots load as 0).

**Decision frame additions (for Z3/Z4 fidelity):** add `odds_yes`, `odds_no`, and `fee_floor_pct` to the decision payload so the Act card can show absolute YES/NO odds and the Mind rail can show "edge vs fee floor" (the mockup requires these; `edge_pct` already exists, absolute odds + fee floor do not).

**Dashboard read path (poll, primary):** `loadSandboxBundle` reads `gods_treasury.jsonl` + `deaths.jsonl` defensively (ENOENT → `[]`), computes `gods_revenue_cumulative_usd = Σ(successful tribute.amount_usd) + Σ(tithe.paid_usd)`, and folds `incarnation_lineage[]` from `deaths.jsonl`. `SandboxStateBundle` gains `recent_gods_treasury`, `gods_revenue_cumulative_usd`, `incarnation_number`, `incarnation_lineage`. The poll hook ingests these into new `useWsStore` slices.

### 4.4 Frontend — Living Stage (`/living`)

New route `dashboard/app/living/page.tsx` + `LivingStageBody.tsx`, cloning the `/mock` bootstrap chain (`WsBootstrap` → `SandboxLiveBootstrap` → zones), `variant='abyss'`, reusing the `SectionHead` motif. One `useWsStore` singleton + the 2s poll feeds all zones.

| Zone | Reuse vs new | Components |
|---|---|---|
| Z1 Organism | ADAPT `VitalsPanel` bar logic into a radial ring + new | `living/LivingOrganism.tsx` (new); reuse `VitalsPanel`, `DeathWatch` energyPct/latch |
| Z2 Gods + Treasury | BUILD NEW; reuse `LiveStream` typewriter + `DecisionFeed` row styling | `living/DivineEventStream.tsx`, `living/DivineTreasury.tsx` (new) |
| Z3 Act | ADAPT existing `CurrentMatchCard` (if present) + reuse `DecisionFeed` market/signal render | `living/CurrentMarketCard.tsx` |
| Z4 Mind | ADAPT `DualEngineMeter` + extract `DecisionFeed` `SignalScore` into signed diverging bars | `living/FusionSignalsRail.tsx`, `living/EngineSignalsPanel.tsx` (new) |
| Z5 Lineage | BUILD NEW; compress `/reincarnation` `IncarnationRow` into a horizontal strip | `living/IncarnationLineage.tsx` (new) |
| Store (cross-cutting) | EXTEND `wsStore` with `divineEvents`, `divineTreasury`, `incarnationNumber`, `reincarnationLineage` slices; extend `SandboxStateBundle`; wire poll ingest | `lib/wsStore.ts`, `lib/sandbox_state_shared.ts`, `lib/load_sandbox_state.ts` |

---

## 5. Data flow (end to end)

1. Live loop ticks → pays tithe at every `tithe_every` markets → emits `tithe`; at deathbed → `_attempt_tribute` → emits `tribute`; at death → emits `agent_died`.
2. `_SandboxStateHook` writes each to `gods_treasury.jsonl` / `deaths.jsonl`; the loop stamps `incarnation_number` into every snapshot.
3. Supervisor, on death, mints a fresh adapter + state-dir and respawns the next incarnation carrying weights + the shared advisor/cost-guard.
4. Dashboard `/api/sandbox` poll (2s) → `loadSandboxBundle` reads the new streams, computes cumulative treasury + lineage → ingests into `useWsStore`.
5. Living Stage zones render reactively from the store.

---

## 6. Error handling & edge cases

- **Incarnation boundary continuity:** poll reader must tolerate a state-dir switch; supervisor mirrors the live incarnation's snapshot + JSONL up to the **root** `state_dir` so the existing single-dir reader keeps working (least invasive vs teaching the reader to follow subdirs).
- **Open-bet drain at death:** `_die` already folds open bets via `poller.terminal_close()` before returning; the supervisor awaits `run()` fully and asserts `open_bet_ids` empty before the next life (LIVE poller is in-loop, serial — no concurrent-settlement race).
- **Runaway respawn:** hard-capped `max_incarnations` (default 10 for live, floor ≥1).
- **LLM budget:** the shared cost-guard prevents per-life budget reset (see §4.2).
- **Dashboard cold start:** missing `gods_treasury.jsonl` / `deaths.jsonl` → `[]`, never throw (ENOENT defensive try/catch, matching existing reads).
- **Restart resilience (in scope):** the supervisor writes a small `incarnation_manifest.json` at the root on every incarnation transition (`{run_id, current_incarnation_idx, carry_weights_hash, max_incarnations}`), written atomically (temp + rename). On boot it reads the manifest to resume the lineage at the right incarnation instead of restarting at 0; an absent or corrupt manifest → cold start at incarnation 0. Carry-weights themselves are recovered from the prior incarnation's terminal snapshot (already persisted), validated against the manifest hash.

---

## 7. Decisions taken on open questions (with rationale)

1. **Tombstone cadence:** keep **per-death minting** (current behavior; each death is a real on-chain tombstone — on-brand and already wired). Revisit only if PRD §5.1 demands final-only.
2. **respawn_policy:** default **ALWAYS-with-cap** (matches the groundhog spirit, keeps the screen alive). `NEVER`/`LEARNED_ONLY` are configurable extras, not v1.
3. **Advisor at incarnation boundaries:** **NO for v1** — keep the LIVE advisor per-tick within a life; the supervisor stays a pure orchestrator. (Possible v2.)
4. **Breath-paid tithe rendering:** render as **breath units**, not a fabricated USD conversion (honesty). Treasury USD total counts only cash tributes + cash tithes.
5. **incarnation_number:** **stored** in the snapshot (supervisor knows the idx) rather than folded from events.
6. **SSE vs poll:** **poll** (the existing sandbox path); SSE addition deferred.
7. **Memory bank CID across lives:** **per-incarnation** for v1 (no chain-contract change).
8. **Status read path:** **mirror-up** the live incarnation's state to the root `state_dir`.
9. **Restart resilience:** **confirmed in scope** — `incarnation_manifest.json` at the root persists `{run_id, current_incarnation_idx, carry_weights_hash, max_incarnations}`, written atomically (temp + rename) on every incarnation transition; on boot the supervisor resumes from it (absent/corrupt → incarnation 0). Carry-weights are recovered from the prior incarnation's terminal snapshot and validated against the manifest hash.

All open questions are resolved; the design is ready for writing-plans.

---

## 8. Scope & phasing

Total rough effort: **~7–11 working days** (backend supervisor is the hard ~2–3 days). Recommended **two-phase** delivery so the showcase value lands early and the riskiest piece is isolated:

- **Phase 1 — The Living Divine Economy (no auto-rebirth yet).** Turn on tithe + tribute, route events to disk, add records/streams/snapshot field + decision-frame odds, build the full Living Stage page (all 5 zones) reading real data. At death the agent is terminal (one life) and the Lineage strip shows the single current life. This alone is a complete, honest, live showcase: a real agent struggling against the gods' rent and tributing at death. *(~4–5 days.)*
- **Phase 2 — Reincarnation.** Add the `LiveIncarnationSupervisor` (fresh adapter/state-dir per life, shared weight-updater + advisor, carry weights, mirror-up, manifest). The Lineage strip comes alive across deaths; the Treasury accumulates across souls. *(~3–4 days + tests/buffer.)*

Both phases ship behind env flags; Phase 1 does not require the supervisor.

> Note: the backend half (supervisor lifecycle, budget sharing, fresh-adapter wiring) has real subtlety — recommend driving it through `plan-loop`/`superplan` with adversarial review.

---

## 9. Testing strategy

- **Records/streams:** unit tests for `TributeRecord`/`TitheRecord`/`DeathRecord` (round-trip, omit-when-None, `extra='forbid'`); `append_*` atomic-write tests.
- **State-hook routing:** test that `tribute`/`tithe`/`agent_died` emits land in the right JSONL (and that the default `_NoopStateHook` path stays byte-identical when the flag is off).
- **Supervisor (Phase 2):** respawn-on-death builds a fresh adapter (not the dead one) and a fresh state-dir; weights carry; the cost-guard/advisor instance is shared (budget does not reset); `max_incarnations` cap stops the loop; `open_bet_ids` empty before respawn; `stop()` cancels gracefully mid-life. **Manifest round-trip:** a simulated crash mid-transition resumes at the recorded incarnation idx; a missing/corrupt manifest cold-starts at 0.
- **Dashboard loader:** ENOENT → `[]`; cumulative treasury math; lineage fold from `deaths.jsonl`.
- **Living Stage:** a Playwright smoke that boots the page against a seeded fixture and asserts all 5 zones render with real-shaped data.
- Respect the repo's existing bar (the suite is currently green; keep it green).

---

## 10. Non-goals / out of scope

- Real-money trading (this stays paper/mock).
- Any backtest data on the Living Stage surface.
- Genome evolution on the live path (backtest-only).
- Cross-incarnation cumulative memory-bank CID / chain-contract changes.
- Replacing or removing the existing `/mock`, `/reincarnation`, or other pages — Living Stage is a new `/living` route.

---

## 11. Key risks

- **Supervisor lifecycle subtleties** (fresh adapter, shared budget, cancel seam, mirror-up) — the most error-prone area; isolate in Phase 2 and review adversarially.
- **Decision-frame contract change** (odds/fee-floor) touches the backend→dashboard payload; keep additive and back-compat.
- **24/7 showcase robustness** — restart resilience + runaway-respawn cap matter more here than in a backtest.
