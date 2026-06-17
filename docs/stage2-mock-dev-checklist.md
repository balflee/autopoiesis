# Stage 2 — Mock Bet + Live Signal: Dev Checklist (for later detailed planning)

> **2026-06-16.** Readiness + live-signal mining + ordered checklist for Stage 2.
> Source: 6-agent readiness workflow over the mock runtime / live data / engines /
> dashboard + the backlog signal mining. This is a **planning reference**, not the
> implementation plan — each item still needs a detailed spec/plan when picked up.
>
> **⚠️ Correction (2026-06-16, after the PR #3 slot-key rename merged):** §3, the A15
> row, and steps ③/④ were rewritten. The original draft assumed the 5 engine modules
> carry live wallet/LLM/Reddit signals "once wired" and that A15 rides the existing
> `smart_money` slot. **That premise was refuted and the rename merged** — the engine
> modules are dead code (never instantiated), the 5 slots carry Sackmann/CLOB
> EVERYWHERE incl. prod (`RealSignalSource`), and there is no `smart_money` slot
> anymore (it is `surface_advantage`, carrying Sackmann surface edge). Live edge
> signals now ride a SEPARATE additive Edge layer. See `docs/optimization_backlog.md`
> F1 for the ground truth.

## 0. Goal & honest framing

**Stage 2 = MOCK BET**: connect a candidate **live** edge signal, let the agent
learn in mock (real live prices, paper trades, no capital at risk, no look-ahead),
and see whether it can master an edge of **gain ≥ 0.2** → graduate to live.

**We do NOT know a real edge exists.** Every public-info probe was negative
(A17 sharp-line REFUTED/INCONCLUSIVE, A18 setprob INCONCLUSIVE → post-fee NO-GO,
B′ cross_market NO-GO). So the bar is now *"info the market has not priced in,
live-only."* **A NO-GO is a likely and acceptable outcome** — Stage 2 is the
experiment to find out, run honestly.

> **UPDATE (2026-06-17) — research + Codex review reframe this from a "build" into a
> bounded FALSIFICATION experiment.** A 23-agent, web-grounded, adversarially-verified
> search over 11 candidate alpha signals (your 4 — micro-climate, medical/physio,
> Hawk-Eye/biomechanics, umpire/equipment — plus live point-by-point, PM crowd-fade,
> cross-market timing, news/withdrawal velocity, schedule/fatigue, weather-disruption,
> retirement-propensity) returned **11/11 NO_GO** (§2). The only thing passing the
> *obtainability* gate is **PM-internal microstructure** (order book / trades / holders —
> free, no auth, genuinely untested). A code-grounded Codex review hardened the design
> (§3/§4 deferrals + mandatory guards); its verdict was **GO-WITH-CHANGES** on a *small*
> falsification Stage-2, else **NO-GO → ship the Stage-1 能学 demo as the deliverable**.
>
> **Separate two questions — do not conflate them:**
> - **(a) START the mock-bet loop** — agent paper-trades live PM tennis on the 5
>   Sackmann/CLOB baseline signals. **Doable fast** = 2 stubs + a flag + ONE minimal
>   honesty guard (§4 Block 1). Needs NO edge signal. It will most likely *lose to
>   fees* (no edge) — that is the honest baseline, and it is fine; the loop running is
>   the milestone.
> - **(b) FIND a real edge** — likely NO-GO. Run it as a bounded falsification
>   experiment: ONE isolated PM-internal probe at a time, pre-registered metric +
>   placebo + holdout + hard NO-GO rule + fee/spread-net scoring (§4 Block 2).

> **DIRECTION (2026-06-17) — Stage-2 transitions the agent from PREDICTION + SURVIVAL
> to PREDICTION + TRADING + SURVIVAL.** Backtest was hold-to-resolution on first-set
> markets = pure outcome prediction (the efficiency wall). Mock bet adds a **TRADING**
> dimension: Polymarket lets you **SELL a position before the market resolves**, so the
> agent can take **full-match + in-play** positions (NOT limited to first set) and EXIT
> dynamically (滚球 / dynamic close) — buy, then sell into a favorable price move instead
> of holding to settlement.
> - ⚠️ This is **NOT arbitrage** (the move can reverse; in-play EV = market fair value —
>   the winning leg of the example is matched by a symmetric losing leg), and **trading
>   capability ≠ edge**.
> - But it (a) is the execution **PREREQUISITE for the only surviving theses** — P1
>   order-flow reversion and P3 spread capture both require *exiting before resolution*;
>   (b) shifts the game from "predict the final outcome" to "trade short-term price moves"
>   = the PM-internal microstructure surface; (c) improves the **survival** profile (cut
>   losers / bank winners before a reversal kills the breath).
> - Catch: it relocates to the most **bot-contested** arena (sub-100ms, $40M/yr) and pays
>   the **spread TWICE per round-trip** on thin books. Build it because the survivors need
>   it — do not mistake "I can exit" for "I have edge."

## 0.5 Architecture overview — predict + trade + survive

One **per-tick decision loop** nested in a **per-life survival/learning meta-loop**
(✅ = exists, 🔴 = to build):

```
                       LIVE POLYMARKET   (Gamma REST + CLOB WS / Data API)
                              │   open tennis markets · price · order book · holders
                              ▼
 ┌──────────────── per-tick decision loop  (SandboxPhase2Loop ✅) ────────────────────┐
 │  TickInputSource 🔴 ─► { 5 baseline signals · price · liquidity cap · position mark } │
 │       ▼                                                                              │
 │  ① PREDICT   RealSignalSource ✅ (5 Sackmann/CLOB slots = p_base)                    │
 │              + Edge layer · Lane A 🔴 → p_model = clip(p_base + κ_edge·Σ wᵢ·sᵢ·cᵢ)   │
 │       ▼ p_model                                                                      │
 │  ② TRADE     DecisionEngine ✅ (fuse + Kelly + 4-constraint sizing)                   │
 │              + position manager 🔴 (entry / exit / size)                             │
 │       ▼ action:  BET (buy) │ SELL (in-play unwind, taker) │ NO_BET                   │
 │  Executor place_order  (buy ✅ / sell 🔴)                                            │
 └───────┼───────────────────────────────────────────────────────────────────────────────┘
         ▼  persist: open_bets.jsonl ✅ · positions + mark-to-market 🔴
  settle: SettlementClient 🔴 (resolution)  ‖  exit fill 🔴 (in-play)  → realized PnL (net 2× spread)
         ▼
 ┌── ③ SURVIVE + LEARN meta-loop ───────────────────────────────────────────────────┐
 │  BREATH economy ✅ → permadeath → reincarnation ✅                                  │
 │  WeightUpdater「能学」EMA ✅(flag) → updates fusion weights across lives (+ harden 🔴)│
 └──────────────────────────────────────────────────────────────────────────────────┘
         ▼  Dashboard /mock ✅ (2s file-poll)
```

**Three pillars:** PREDICT (`p_model` = 5 baseline + Edge layer) · TRADE (entry/exit/size
incl. in-play unwind) · SURVIVE+LEARN (breath/permadeath + 能学 EMA across lives).

## 0.6 Scope — build Minimal V1 first (Codex review 2026-06-17: "NO-GO as scoped")

The full design (trading layer + 4 probes + learner-hardening + P4 wallet reconstruction +
Lane B) is **scope-overloaded vs what the code supports today** (buy-and-hold-to-resolution
only). Codex's full-architecture review → **cut to a Minimal Coherent V1, run it, then
decide.** The trading layer + P4 are NOT cancelled — they are **sequenced to V2**, each
behind a gate.

**V1 — build now (coherent, runnable, honest):**
- **Block 1** live paper loop on the 5 baseline signals (hold-to-resolution), AND
- **P2** (favorite-longshot calibration, backtest-only) as the **ONLY** probe, with the
  **gain metric + placebo/holdout guard CODED and default-ON** (not doc-only).
- Present the result as a **bounded NO-GO search** unless P2 clears the cost-net threshold.

**V2 — deferred, each gated:**
- **Trading layer (Block 1.5)** — **BLOCKED on §5 #10**: the survival economy is
  *settlement-triggered*; open positions / unrealized MtM / exits are undefined against
  breath / death / reincarnation / learner-credit. **Decide the accounting before writing
  any trading code** (realized-only vs MtM vs forced-liquidation).
- **P4 smart/dumb-wallet tracker** — a separate **data-engineering project** (as-of
  reconstruction from per-wallet `/activity`); defer until V1 runs.
- **P1 order-flow reversion** — needs the live loop + the trading layer.
- **P3 spread capture / Lane B maker** — no executor skeleton; deferred.

**Graduation honesty (Codex HIGH):** the `gain≥0.2` metric + placebo/holdout/isolation must
be **coded + default-ON before any edge claim** (settlement PnL is still the resolution
formula with realism switches default-OFF). Trading + a per-wallet learner *raise* the
manufacture-a-false-graduation risk — V1 is kept minimal precisely to limit the degrees of
freedom.

## 1. Readiness matrix

| component | status | gap |
|---|---|---|
| Mock loop driver (`SandboxPhase2Loop`) | ✅ ready | tick→fusion→execute→settle→persist, restart-resilient, single-writer |
| Settlement poller | ✅ ready | idle only because no real `SettlementClient` injected |
| State persistence (JSONL + atomic snapshot) | ✅ ready | open/settled/decisions/reflections + `agent_state.json` |
| Sandbox chain adapter (BREATH + death/Tombstone) | ✅ ready | in-memory; sufficient for testnet mock |
| Real `WeightUpdater` (EMA on 6 fusion weights) | 🟡 partial | dead until settlements flow **and** `GENESIS_REAL_LEARNING=1` |
| REST source clients (`data/sources/*`) | ✅ ready | **live-capable now** via `asof_ts=now`; not yet injected |
| Gamma settlement parser (`agent/data/polymarket_settlement.py`) | ✅ ready | ready to wrap as `SettlementClient` |
| Dashboard `/mock` + 2s file-poll read-path | ✅ ready | L5-gated route, 4 widgets, store, contract v0.3.0 |
| **`TickInputSource` production adapter** | 🔴 stubbed | **CRITICAL**: `_IdleTickInputSource` → NO_BET every tick |
| **`SettlementClient` adapter** | 🔴 stubbed | **CRITICAL but cheap**: `_NoopSettlementClient`; parser exists |
| Live OPEN-market discovery (closed=false) | 🟡 partial | batch path filters `closed=true`; need continuous open polling |
| `tennis_technical` live compute (T-B-015) | 🟡 partial | `RealSignalSource.elo_signal` already computes this slot; the separate async-Engine wrapper is likely redundant post-rename — the live TickInputSource should reuse RealSignalSource (decide in step ②) |
| A15 → **P4** smart/dumb-wallet tracker | 🔴 to build | **data CONFIRMED obtainable** (holders/positions/profit endpoints, §2 P4); build the tennis *as-of*, confidence-weighted tracker; `PolygonChainLive` watch skeleton exists, NBA-stub whitelist is the only dead part |
| **Trading layer — SELL/EXIT + position manager + mark-to-market** | 🔴 missing | **VERIFIED absent**: `ActionKind` = BET/NO_BET only (`state.py:48-49`); zero sell/exit/close/mark-to-market code → today the agent is **hold-to-resolution only**. Required for in-play / P1 / P3. **Taker exit — simpler than the Lane-B maker subsystem.** |
| No-look-ahead / paper-safety guards | 🟡 partial | PIT gate exists; missing far-future audit test + decision-time-fill assert |
| Graduation gate (`gain ≥ 0.2`) | 🔴 missing | units now DEFINED (§5 #1: net ROI/EV, cost-haircut, placebo NO-GO guard) — still uncoded; required for *claiming edge*, not for *starting the loop* |
| Async streaming (`T-B-007`) | ⬜ missing | **NOT needed for v1 polling**; only if edge window is sub-second |
| Agent→dashboard real-time WS | ⬜ missing | low priority; file-poll already delivers telemetry |
| Env config + L5 gate flag | 🔴 missing | `NEXT_PUBLIC_L5_COMPLETE`, `SANDBOX_STATE_DIR`, etc. |

**Verdict**: a live paper loop is **2 critical stubs + a flag + 1 honesty guard**
away (§4 Block 1). Shortest path: a polling spine on the REST layer (skip async
`T-B-007`), the **5 Sackmann/CLOB baseline signals** (NO edge signal needed to
start — A15/D1 are deferred, see §2), paper trades to JSONL, dashboard via file-poll.
The "graduation gate" row stays 🔴 — it is required for *claiming an edge*, not for
*starting the loop*.

## 2. Live-signal candidates — research verdict: 11/11 NO_GO (2026-06-17)

A 23-agent, web-grounded, adversarially-verified search killed every external-data
candidate on TWO gates: *obtainable by us* AND *not already priced*. **Do NOT reopen
these** (each fails for the stated, durable reason):

| candidate | why killed |
|---|---|
| Hawk-Eye / radar / biomechanics raw | proprietary (Sony / Tennis Data Innovations), live feed sold only to books via Sportradar — unobtainable AND already priced. |
| Micro-climate & aerodynamics | obtainable slice (lat-lon weather + altitude) is fully priced; the unpriced slice (court-level micro-climate) has no accessible feed. |
| Medical & physiological intel | only structured feed (Sportradar MTO) is enterprise-paywalled AND a simultaneously-broadcast public event = zero latency edge; no mandatory injury report. |
| Umpire & equipment bias | umpire feed paywalled + not pre-published; its line-call mechanism was **abolished by ELC Live across ATP + slams in 2025**; CPI/ball static + redundant with the surface baseline. |
| Weather → schedule disruption | raw forecast free, but the edge variable (in-play resumption state) is unobtainable; PM explicitly reprices on weather/suspensions; only a handful of qualifying events/yr. |
| News / withdrawal / social velocity | **PM last-fair-price settlement on pre-match withdrawals (ITF flat $0.50) nullifies it by VENUE RULE** regardless of speed; in-play window beats any news API. |
| Retirement / walkover propensity | static leg priced + weak post-fee (set-consensus class); live distress signal unobtainable + look-ahead (RET is an end-of-match label). |
| Schedule / travel / fatigue | crude proxy already in the `rest_recency` baseline + every sharp line; live layer has no free historical order-of-play archive (not backtestable) + no timing gap. |
| Live point-by-point microstructure | indie feeds 5–15s downstream of the 1–2s sharp reprice; a non-co-located indie is structurally last-in-line in the most latency-arbitraged niche. |
| **D1** cross-market sharp-line timing | bot-farmed (~$40M/yr extracted, sub-100ms co-located, 14/20 top PM wallets are bots); sharp feed **dead** (Pinnacle public API closed 2025-07-23) or paywalled (Betfair Live £499). Only lives as the existing A15/D1 timing thesis, NOT a fresh external-data probe. |
| **A15** on-chain wallet flow | the NAIVE form (NBA-stub whitelist) stays dead (survivorship). BUT **the data to build a tennis-specific tracker is now CONFIRMED obtainable** (2026-06-17 live probe) → re-promoted as **probe P4** below, built the *as-of* way. |

**The only obtainable, genuinely-untested residual is PM-INTERNAL market structure**
(order book / trade tape / **holder + wallet track-record** distribution — all free, no
auth, from the Gamma / CLOB / Data APIs). **Four** cheap probes (~$0) — *expect each to
die by spread/fees; each retires a distinct untested hypothesis*:

| # | probe | the new question it asks | first step |
|---|---|---|---|
| **P2** | PM favorite-longshot calibration (thin tennis subset) | a documented PM bias (longshots overpriced ~−26%, favorites ~−3.6%) — does it hold on OUR thin ATP/WTA-250 subset NET of fee? **Backtest-only, no executor, low look-ahead (entry price only)** → the cheapest first falsification | pull historical PM tennis entry-price vs resolution (Gamma/Data API); bin by price decile; net-ROI per decile after spread+fees; reject unless the favorite bin clears the fee dead-zone; emit the calibration curve (reusable even if NO-GO) |
| **P1** | PM order-flow reversion, decoupled from any sharp anchor | does an order-flow shock on a thin PM tennis book mean-revert WITHOUT an external fair value? **Codex Q4b: define as *short-term PM price regression after a flow shock*, NOT "mispricing" — else circular; a value edge requires an execution-PnL test net of cost** | stream CLOB WS + snapshot `/book` + Data API `/holders` on 20–30 live ATP/WTA-250 markets, strict as-of timestamps; log order-flow imbalance / holder skew vs forward 5/15/60-min PM price; EV net of the ACTUAL thin-book spread; filter 0.99/0.01 stale quotes (py-clob #180) |
| **P3** | thin-book stale-quote SPREAD CAPTURE (market-MAKING, not prediction) | rest limit orders inside the absurd 0.99/0.01 spread — sidesteps the "is the price wrong" question that failed all 11. **Codex CRITICAL: needs a maker executor that does NOT exist — DEFERRED until built (§3 Lane B)** | (when built) scan Gamma for wide-spread markets; paper-sim symmetric resting orders; measure captured spread − adverse-selection toxicity |
| **P4** | PM smart/dumb-wallet tracking — **confidence-weighted per-wallet, as-of** (re-promoted A15) | does the net positioning of a **confidence-weighted, as-of "smart" cohort** (or the inverse — *fade the proven-dumb*) predict the tennis outcome NET of cost? **Empirical (2026-06-17): the top whale on a live tennis market is a −$207k lifetime loser → "follow the whale" is likely BACKWARDS; "smart" must be a sample-size-SHRUNK track-record score, NOT position size.** | per market pull `data-api/holders`; for each wallet compute an **as-of shrinkage-adjusted edge** (large-sample +EV → high weight; small-sample lucky → ≈0) from `lb-api/profit` + `data-api/positions`+`/activity`; signal = Σ (wallet_confidence · side) with a **fade-dumb** variant + an **elite tier** tracked finely; STRICT out-of-sample/placebo (per-wallet granularity = huge overfit surface) |

> **Verified-obtainable endpoints (2026-06-17 live probe, free, no auth):** tennis markets
> `gamma-api.polymarket.com/events?tag_slug=tennis&closed=false`; per-market holders
> `data-api.polymarket.com/holders?market=<conditionId>` (wallet · size · side); per-wallet
> positions+PnL `data-api.polymarket.com/positions?user=<w>`; lifetime profit (the "smart"
> score) `lb-api.polymarket.com/profit?address=<w>`. **As-of caveat:** `/holders` is a
> CURRENT snapshot — historical "who held at time T" must be reconstructed from per-wallet
> trade history (`/activity`), and the profit score must be recomputed as-of T (today's
> lifetime PnL = look-ahead). `PolygonChainLive.smart_money_positions()` already has the
> on-chain watch skeleton; the gap was only the NBA-stub whitelist.

**Order:** P2 first (backtest-only, ~$0, no new infra) → P4 (backtest-first: as-of holders
× as-of profit × outcome; needs trade-history reconstruction) → P1 (needs the live loop) →
P3 (needs a new maker executor — deferred). Bottom line: no *information* edge survived;
the residuals are all **PM-internal market-structure** — if anything graduates it is a
structure/timing finding, not an information edge.

## 3. Architecture — how the agent uses live signals (CORRECTED 2026-06-16 post-rename + Codex-hardened 2026-06-17)

**The 5 fusion slots are the Sackmann/CLOB BASELINE, not live-edge inlets.** Post-
rename (PR #3) the slots are named for what they carry — `tennis_technical`=elo,
`market_momentum`=CLOB momentum, `surface_advantage`/`head_to_head`/`rest_recency`=
Sackmann surface/h2h/rest — and `real_signal_source.py::RealSignalSource` feeds them
EVERYWHERE (backtest, demo, **and prod** via `_make_prod_signal_source`). The old
engine modules (`smart_money.py`=wallets, `sentiment_llm.py`=Gemini, `crowd_volume.py`=
Reddit) are **dead code, never instantiated** — they were NEVER on the live path, so a
live signal **cannot just "ride an existing slot"** (overwriting `surface_advantage`
with wallet flow would destroy its Sackmann baseline). In a LIVE mock the same 5
baseline signals are computed live (`asof_ts=now`) → `p_model_base`.

**Live edge signals ride a SEPARATE Edge layer ON TOP of the 5-slot baseline, in TWO
LANES** (Codex review hardened this), NOT "wire a live signal into a Sackmann slot":

**Lane A — DIRECTIONAL (fusible into `p_model`):**
- `p_model = clip(p_model_base + κ_edge · Σ wᵢ·signalᵢ·confᵢ, 0, 1)` — additive;
  `κ_edge=0` / all `wᵢ=0` reverts exactly to v3 (the honest null, à la κ_xm / B′).
  ⚠️ The scalar being *safe* is NOT evidence of edge — κ_xm had the same shape and was NO-GO.
- **Plug-in**: each signal implements one `EdgeSignal.fetch(market, asof_ts) →
  {value∈[-1,1], confidence, available_at, rationale}`. **Pre-register the full schema**
  (`available_at`, as-of order-book snapshot id, decision price, fill price, post-fill
  move) so look-ahead is auditable.
- **First directional candidates = PM-internal P2 then P1 (§2), NOT A15.** **A15 is
  DEFERRED** (Codex: the wallet whitelist is NBA stubs — wiring it now leaks survivorship
  bias; the kept `SmartMoneyEngine` becomes the A15 EdgeSignal ONLY after a tennis-specific
  *as-of* wallet queue exists).
- **Learner = the 能学 mechanism on real signals, but HARDENED** (Codex HIGH — the raw
  per-engine `sign(pnl)·bet_dir·score` credit, `weight_updater.py:371-399`, will overfit
  sparse, coincidentally co-firing signals): per-signal **isolation + holdout + placebo**
  before any signal earns weight; sparsity gating (junk auto-zeros); candidate→active
  two-tier (top-K by *proven* edge); a **per-signal exploration BUDGET CAP + statistical-
  uselessness stop** so probing junk does not bleed the $100 bankroll; gate *ingestion*
  by fetch cost, not the model.

**Lane B — MARKET-MAKING (P3 spread capture; NOT fused into `p_model`):**
- A different *action* (provide liquidity) with a different *metric* (captured spread −
  adverse-selection toxicity) — outside the 能学 directional-weight survival loop.
- **Codex CRITICAL — it does not exist yet.** The loop today passes one decision price to
  `place_order(price,size,side)`; there is **no order-book state, no maker/limit orders,
  no cancels, no queue position, no partial-fill / toxicity tracking**
  (`sandbox_phase2_loop.py:510-548`, `polymarket_sandbox_executor.py:166-269`). Lane B is a
  **separate executor + inventory/risk ledger — a future subsystem, DEFERRED**, not a flag.

**Trading layer (orthogonal to the Edge layer — prediction vs execution):** the Edge
layer above produces a *directional view* (`p_model`); the **trading layer** decides
*entry, exit, and sizing* on that view. Stage-2 adds **dynamic EXIT** (`SELL`) so a
position can be closed before resolution (§0 DIRECTION). This is a **TAKER** action (hit
the bid to unwind) — distinct from, and simpler than, Lane B's *maker* market-making. It
needs a `SELL`/`EXIT` `ActionKind`, a position-state tracker, and mark-to-market PnL. The
agent's three pillars become **prediction (`p_model`) + trading (entry/exit/size) +
survival (breath/permadeath)**.

**Isolation-first** (A0 strong-optimizer-wins): run ONE isolated probe at a time for a
clean causal read before any fusing.

## 4. Ordered dev checklist (dependency-aware)

### Block 1 — Minimal viable START: the loop on baseline (NO edge signal needed)
*Goal: agent paper-trades live PM tennis on the 5 Sackmann/CLOB baseline signals →
"mock bet started." It will likely lose to fees (no edge) — that is the honest
baseline, and the loop running IS the milestone.*

1. **SettlementClient** — wrap `polymarket_settlement.py` (inject http client), swap
   `_NoopSettlementClient` (`main.py:2102`). *small* — restores PnL→BREATH→weight.
   **(Codex's first build step)** + one integration test: open bet → Gamma resolution →
   settled row → breath/bankroll/weight update.
2. **TickInputSource (REST polling)** — live OPEN tennis-market discovery (gamma
   `closed=false`) + live CLOB price + liquidity cap; return the 5-key BASELINE signals
   from `RealSignalSource` with `asof_ts=now`; swap `_IdleTickInputSource` (`main.py:2418`).
   *medium* — reuses live-today REST clients; skips async T-B-007; makes the old
   per-engine wrapper (T-B-015) redundant. *(depends: 1)*
   ⚠️ **RISKIEST V1 STEP (Codex):** prod currently uses the cached `_ReplayTickInputSource`;
   `RealSignalSource` expects snapshot/ledger semantics that do NOT map cleanly to
   `asof_ts=now` (`real_signal_source.py:256-296`, `main.py:2399-2425`) — budget for a real
   rabbit hole, not a 1-hour swap.
3. **Flag + env** — `GENESIS_REAL_LEARNING=1`, `SANDBOX_STATE_DIR`, etc.; confirm real
   `WeightUpdater` on the poller; keep the β₁ slot (`head_to_head`) frozen for v1. *small* *(depends: 2)*
4. **Honesty guard + execution-cost SCHEMA (MANDATORY; Codex: NOT just a flag)** —
   decision-time fill price (NOT post-move); `asof_ts=now` PIT on every signal; settlement
   only from gamma resolution; far-future-timestamp audit test. The fee/spread/liquidity
   haircut is a **SCHEMA CHANGE**: `BetRecord` has no fill/fee/spread fields and
   `_compute_pnl` only does the legacy formula/cap (`sandbox_state.py:113-165`,
   `sandbox_settlement_poller.py:778-844`) — add an immutable execution-cost snapshot to
   `BetRecord` BEFORE wiring settlement PnL. *medium* *(depends: 2)* — **the A18
   +pre-fee/−post-fee trap; without this every number is a lie.**
4b. **Terminal-unsettled-bet rule (Codex — V1 gap)** — even hold-to-resolution, death can
   stop the loop with open `open_bet_ids` still in the terminal snapshot
   (`sandbox_phase2_loop.py` ~:2203). Define + TEST what happens to a still-open bet at
   death (settle-on-next-poll / void / carry). This is a *settlement-finality* edge — NOT
   the §5 #10 MtM accounting (that's V2). *small* *(depends: 1)*
5. **Surface /mock** — `NEXT_PUBLIC_L5_COMPLETE` + `SANDBOX_STATE_DIR`; point `/api/sandbox`
   at the loop state dir; file-poll, no WS server. *small* *(depends: 3)*
   → **At this point the mock bet is RUNNING** (hold-to-resolution baseline). Everything
   below is the *trading* layer + the *find-edge* experiment.

### Block 1.5 — Trading layer (V2 — ⚠️ BLOCKED until §5 #10 is decided)
> **Codex: NO-GO until the trading × survival accounting (§5 #10) is a written decision.**
> The survival economy is settlement-triggered and undefined for open / unrealized
> positions — building SELL/MtM first makes the breath/death economy self-contradictory.
*Goal (V2): the agent can EXIT a position before resolution (滚球 / dynamic close) — the
execution capability the surviving probes require. NOT needed for the Block-1 V1 loop.*

- **T1. `SELL`/`EXIT` action + position manager** — add a `SELL` `ActionKind`; track open
  positions (token, entry price, size) + their live mark from the CLOB mid; let the agent
  decide to unwind (TAKER — hit the bid). *medium* *(depends: 4)*
- **T2. Mark-to-market PnL + round-trip cost** — realize PnL on exit at the fill price
  (not resolution); charge the spread on BOTH legs (paid twice per round-trip); feed the
  realized PnL into the BREATH / weight loop. *medium* *(depends: T1)* — **the spread-tax
  doubling is the honest killer of most in-play round-trips.**
- **T3. In-play tick source** — extend TickInputSource to also poll OPEN markets the agent
  already HOLDS (live mark + signals), not just new-entry candidates; allow full-match +
  in-play markets, not first-set only. *small* *(depends: 2, T1)*

### Block 2 — Falsification probes (FIND edge — bounded, one at a time)
> **V1 = steps 6 + 7 only** (define `gain`, then P2 backtest). Steps 7b/8/9/10/11/12 (P4,
> learner-isolation, cross-life, P1, live-loop run, E2E) are **V2**, gated behind V1 running
> + the Block-1.5 accounting decision (§5 #10).

6. **DEFINE `gain` (Codex CRITICAL — BEFORE any edge claim; CODE it, default-ON)** — net ROI/EV per $ staked,
   after fees + half-spread/crossing + failed fills + liquidity caps; Brier a *diagnostic
   only*, monetary edge primary; with a **cluster-bootstrap / placebo NO-GO-capable stat
   guard**. Without this the design can manufacture a false graduation. *medium*
7. **Probe P2 — PM favorite-longshot calibration** (§2) — **backtest-only, no executor,
   no live loop**; historical PM tennis entry-price vs resolution, net-ROI per price
   decile, NO-GO unless the favorite bin clears the fee dead-zone. **Cheapest first
   falsification (~$0).** *small* *(depends: 6)*
   ⚠️ **Codex: gate on REAL historical entry prices** — `historical_fetcher.py:454-497`
   pulls `closed=true` Gamma + **synthesizes a 3-point ledger** (closed markets lack
   intraday ticks); a synthetic entry price would MANUFACTURE P2's result. Use actual
   recorded fills/prices, not the synthetic ledger.
7b. **Probe P4 — PM smart/dumb-wallet tracker** (§2, re-promoted A15) — backtest-first:
   reconstruct **as-of** holders (from per-wallet `/activity`) × **as-of** wallet profit ×
   outcome; **confidence-weighted per-wallet** (sample-size shrinkage, elite tier) + a
   **fade-dumb** variant; net of cost; STRICT out-of-sample/placebo (per-wallet = huge
   overfit surface). Data confirmed obtainable. *medium* *(depends: 6, 8)*
8. **Learner-isolation harness (Codex HIGH)** — per-candidate isolation + holdout + placebo
   + exploration budget cap + statistical-uselessness stop + cluster/market-family guard
   (no single event/player/bot-wallet may dominate apparent edge). *medium* *(depends: 6)*
   — **required before trusting the 能学 learner on real sparse signals.**
9. **Verify cross-life weight persistence + decouple lifecycle (Codex relay)** — confirm
   the 能学 EMA carries learning ACROSS reincarnations (Stage-1 did); if a sparse real
   signal needs 50–100+ returns but a life is ~20 bets ($100/$5), decouple Stage-2's
   lifecycle/survival params from the Stage-1 survival game, or accept the convergence
   NO-GO. *small (analysis) + medium (decouple)* *(depends: 6)*
10. **Probe P1 — PM order-flow reversion** (§2) — on the live loop; measure short-term PM
    price regression after a flow shock, then an **execution-PnL test net of cost** (Q4b:
    else circular). *medium* *(depends: 4, 8, T1–T3 — trading the reversion REQUIRES exit)*
11. **Run the live paper loop** (multi-day) → JSONL; verify open→settled, BREATH deltas,
    weight updates; the first honest go/no-go on whether any PM-internal edge survives net
    of cost. *medium* *(depends: 10)*
12. **E2E integration test** — market→paper-bet→settle→weight across ticks. *medium* *(depends: 4)*

### Block 3 — DEFERRED (do NOT start with these)
- **A15 on-chain wallet flow (naive whitelist form)** — the NBA-stub version stays dead;
  the obtainable-data version is **re-promoted to P4 in Block 2**. The kept
  `PolygonChainLive.smart_money_positions()` skeleton becomes P4's producer once the
  tennis as-of, confidence-weighted tracker is built.
- **D1 / cross-market timing** — NO_GO as a fresh probe (bot-farmed, sharp feed dead/paywalled).
- **Lane B / P3 spread capture** — no maker executor exists (§3); a separate subsystem.
- **T-B-007 async streaming** — only if polling proves too slow for an edge window P1 finds.
- **Real-time WS/SSE push** — low priority; file-poll already delivers telemetry.

## 5. Open decisions (resolve before/early in planning)

1. **`gain ≥ 0.2` units** — ~~Brier? ROI? EV/bet?~~ **Codex answer (adopt):** net
   ROI/EV per $ staked, computed after fees + half-spread/crossing + failed fills +
   liquidity caps; Brier as a *secondary diagnostic only*, monetary edge primary; with a
   cluster-bootstrap / placebo guard that can return NO-GO. ⚠️ the spec's `gain≥0.2`
   comes from *synthetic* deployment economics — it is NOT a real PM edge measurement.
2. **Track-E wallet scan** — runnable now? Does it produce a *tennis*-relevant
   whitelist or only the NBA-trained set the fixture comment mentions?
3. **Polling cadence vs edge half-life** — is the P1 order-flow→PM-price reversion
   window long enough for a 2–30s poll, or sub-second (→ T-B-007)? Determines whether a
   null means "no edge" or "too slow." (A15/D1 deferred, so this is now a P1 question.)
4. **Isolation vs fusion** — **ADOPTED: isolation-first** (one probe at a time, P2 then
   P1); fusing many candidates risks A0 strong-optimizer-wins. Both the research and
   Codex flagged this independently.
5. **Fee/spread/liquidity model** — the haircut that turned A18 +pre-fee → −post-fee;
   needed to net-score mock wins on thin PM tennis markets.
6. **On-chain L5→Phase-2 gate** — must the `advancePhase` tx fire before the loop
   boots (competition narrative), or is the sandbox `_NoopPhaseReader` acceptable?
7. **Edge-engine architecture (§3)** — sign off on the 2-lane Edge layer. **The rename +
   Codex review settle most of this**: Lane A = additive `κ_edge` term on `p_model` +
   hardened 能学 learner; Lane B (market-making) = a separate deferred subsystem. The 5
   slots are a Sackmann/CLOB baseline and can't absorb a live edge signal, so "dump
   everything into the 5-slot fusion" is foreclosed. Open sub-choice: how much of the
   learner-isolation harness (decision #8 below) is v1 vs later.
8. **能学 EMA across reincarnations + Stage-2 lifecycle (Codex)** — does the learned EMA
   persist across lives (Stage-1 implies yes) or reset on death? If sparse-signal
   convergence (50–100+ returns) exceeds a life (~20 bets), do we decouple Stage-2's
   lifecycle/survival params from the Stage-1 survival game, or accept the NO-GO?
9. **Lane B scope** — build the maker executor + inventory/risk ledger for P3 spread
   capture as a separate Stage-2 module, or declare it out of scope and Stage-2 stays
   directional-only?
10. **Trading × survival accounting (Codex BLOCKER — decide BEFORE writing any Block-1.5
    code)** — the survival economy (breath / death / reincarnation, `sandbox_phase2_loop.py`
    death path ~:2203-2216) is **settlement-triggered**; trading adds open inventory +
    unrealized MtM + early exits. Define explicitly: (a) does unrealized MtM move
    breath/bankroll (risk: double-count at settlement) or realized-only (risk: delay death
    while bleeding)? (b) what happens to OPEN positions at death / reincarnation — the
    backtest voids unsettled bets (`survival_season.py` ~:1834), adopt or reject? (c) how is
    learner credit assigned for an exited-early position vs a settled one? **No trading code
    until this is a written decision.** This is the single biggest blind spot Codex found.

## 6. Honest caveats (carry into every readout)

- We do **not** know any edge exists; NO-GO is likely & acceptable.
- **Cost/liquidity blindness**: paper has no fee/spread/impact — re-score net before
  claiming an edge (A18 was +pre-fee, −post-fee).
- **Look-ahead is the silent killer** — PIT gate + audit test mandatory.
- **Latency confound** — null on polling ≠ no edge; may be too slow.
- **A15 survivorship/selection bias** — whitelist retro-fit on past winners; NBA→tennis transfer unproven.
- **Goalpost risk** — fix `gain≥0.2` + its cost-net/stat guard up front, or risk manufacturing a graduation.
- **(Codex) Learner overfits sparse signals** — the per-engine `sign(pnl)·score` credit rewards coincidentally co-firing signals; isolation/holdout/placebo are not optional.
- **(Codex) Lane B is a subsystem, not a flag** — spread capture needs a maker executor + inventory/risk ledger that does NOT exist today.
- **(Codex) Convergence vs lifetime** — ~20 bets/life vs 50–100+ for a weak real signal; verify cross-life EMA persistence + decouple lifecycle, or it is a structural NO-GO.
- **(Codex) "Internal price-move" ≠ "outcome edge"** — a PM-internal signal predicting the next PM *price* move is not enough; it must predict the final outcome *net of cost*, proven by an execution-PnL test (else circular).
- **(Codex) Honest fallback** — if the guards above aren't accepted, the correct deliverable is the **Stage-1 能学 demo + a "no proven real edge yet" writeup**, not a forced graduation.
- **(P4) Smart ≠ whale, and per-wallet ≠ free** — on PM the biggest holders often LOSE (a live tennis whale was −$207k lifetime); "smart" must be a **sample-size-shrunk as-of** track record, and per-wallet granularity is a huge overfitting / luck-mining surface — confidence-weight + strict holdout, NEVER "pick the wallet that predicts best in-sample." Even a *real* elite wallet's signal is likely already in the price (their bet moves the thin book + others copy).
- **Trading ≠ edge** — being able to EXIT (滚球) is optionality, not alpha; in-play EV = the market's fair value (a martingale). The example's winning leg (sell the spike for +60%) has a symmetric losing leg (the run reverses, you cut at −60%). It enables the surviving theses; it does not create them.
- **Spread tax DOUBLES on round-trips** — a buy-then-sell pays the thin-book spread twice; this is the honest killer of most in-play trades and must be in the cost model from day one.
- **In-play is the MOST bot-contested arena** — the sub-100ms co-located cohort that extracted ~$40M/yr lives here; a polling indie is structurally late.
