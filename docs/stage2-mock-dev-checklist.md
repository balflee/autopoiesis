# Stage 2 — Mock Bet + Live Signal: Dev Checklist (for later detailed planning)

> **2026-06-16.** Readiness + live-signal mining + ordered checklist for Stage 2.
> Source: 6-agent readiness workflow over the mock runtime / live data / engines /
> dashboard + the backlog signal mining. This is a **planning reference**, not the
> implementation plan — each item still needs a detailed spec/plan when picked up.

## 0. Goal & honest framing

**Stage 2 = MOCK BET**: connect a candidate **live** edge signal, let the agent
learn in mock (real live prices, paper trades, no capital at risk, no look-ahead),
and see whether it can master an edge of **gain ≥ 0.2** → graduate to live.

**We do NOT know a real edge exists.** Every public-info probe was negative
(A17 sharp-line REFUTED/INCONCLUSIVE, A18 setprob INCONCLUSIVE → post-fee NO-GO,
B′ cross_market NO-GO). So the bar is now *"info the market has not priced in,
live-only."* **A NO-GO is a likely and acceptable outcome** — Stage 2 is the
experiment to find out, run honestly.

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
| `tennis_technical` async Engine wrapper (T-B-015) | 🟡 partial | primitives ready, no async wrapper; workaround = neutral-0 slot |
| A15 smart-money wallet whitelist | 🔴 stubbed | 10 dummy wallets; real Track-E scan is the **edge-data dependency** |
| No-look-ahead / paper-safety guards | 🟡 partial | PIT gate exists; missing far-future audit test + decision-time-fill assert |
| Graduation gate (`gain ≥ 0.2`) | 🔴 missing | no coded metric; units undefined; needs net-of-cost + stat guard |
| Async streaming (`T-B-007`) | ⬜ missing | **NOT needed for v1 polling**; only if edge window is sub-second |
| Agent→dashboard real-time WS | ⬜ missing | low priority; file-poll already delivers telemetry |
| Env config + L5 gate flag | 🔴 missing | `NEXT_PUBLIC_L5_COMPLETE`, `SANDBOX_STATE_DIR`, etc. |

**Verdict**: a live paper loop is **2 critical stubs + a flag** away. Shortest path:
a polling spine on the REST layer (skip async `T-B-007`), one live signal (A15)
end-to-end, paper trades to JSONL, dashboard via file-poll.

## 2. Live-signal candidates (ranked)

| id | signal | data source | verdict |
|---|---|---|---|
| **A15** | smart_money — informed on-chain wallet flow | `PolygonChainClient` (read-only `eth_getLogs`) + Track-E whitelist | **WIRE FIRST** — only market-unpriced candidate; infra mostly exists; cheapest honest probe |
| **D1** | sharp_line — sharp-vs-soft order-flow *timing* | new `data/sources/odds_api.py` (Odds API / Betfair) + de-vig (`sharp_line.py` ready) vs live CLOB | **2nd probe** — tight 30s–5m window; may force streaming; defer until A15 proves the loop |
| A16 | cross_market — set/match consensus consistency | `cross_market_signal.py` (built) | **learned component**, not standalone; INCONCLUSIVE pre-fee |
| A18 | setprob — consensus → first-set prob | A18 probe (built) | **deprioritize** — +0.0035 Brier, NO-GO post-fee |
| A11 | forward storm classifier | — | **not an edge** — survival/risk-mgmt; revisit as Stage-1 sizing |

## 3. Architecture — how the agent uses live signals (PROPOSED, pending sign-off)

**Keep the 5 engines unchanged.** In LIVE they already carry 3 live signals
(`smart_money`=wallets, `sentiment_llm`=Gemini, `crowd_volume`=Reddit — the
backtest only substituted proxies because live wasn't wired). So:

- **A15 (first probe) rides the EXISTING `smart_money` slot** — no new engine.
- **A separate additive "Edge layer" handles signals BEYOND the 5 slots** (D1,
  cross-market, future arbitrary signals):
  - `p_model = clip(p_model_base + κ_edge · Σ wᵢ·signalᵢ·confᵢ, 0, 1)` — additive;
    `κ_edge=0` / all `wᵢ=0` reverts exactly to v3 (the honest null, à la κ_xm).
  - **Plug-in**: each signal implements one `EdgeSignal.fetch(market, asof_ts) →
    {value∈[-1,1], confidence, available_at, rationale}`; register one line.
  - **Learner = the 能学 mechanism on real signals**: per-signal predictiveness
    EMA (edge/ROI track record, NOT raw accuracy — target is *mispricing*), sparse
    weights, exploration floor keeps measuring candidates.
  - **"Too many / too messy" handling**: (1) sparsity/track-record gating → junk
    auto-zeros, so adding signals is free; (2) candidate→active two-tier with an
    active-set cap (top-K by proven edge); (3) decorrelate/dedup; (4) gate signal
    *ingestion* by fetch cost (latency/$/look-ahead), not the model.
- **Isolation-first** (A0 strong-optimizer-wins): run the first probe as a single
  isolated signal for a clean causal read before fusing — readiness independently
  flagged this. Favors a **separate edge estimator** over dumping into the shared
  fusion EMA.

## 4. Ordered dev checklist (dependency-aware)

1. **SettlementClient** — wrap `polymarket_settlement.py` (inject http client),
   swap over `_NoopSettlementClient` (`main.py:2102`). *small* — restores the whole
   PnL→BREATH→weight feedback chain. **(do first)**
2. **TickInputSource (REST polling)** — live OPEN tennis-market discovery
   (gamma `closed=false`) + live CLOB price + liquidity cap; return a full 5-key
   signals dict (unwired engines emit neutral-0); swap over `_IdleTickInputSource`
   (`main.py:2418`). *medium* — uses the live-today REST clients, skips async T-B-007.
   *(depends: 1)*
3. **Flip real learning** — `GENESIS_REAL_LEARNING=1`, confirm real `WeightUpdater`
   on the poller; keep `sentiment_llm` frozen for v1 to reduce variables. *small* *(depends: 2)*
4. **Wire A15 end-to-end** — `smart_money` via `PolygonChainClient` into the fanout,
   initially with the stub whitelist, to prove a real tick→paper-bet→settle→weight
   cycle. *medium* *(depends: 2)*
5. **No-look-ahead + paper-safety pass** — assert `asof_ts=now` PIT on every signal;
   fill price recorded at decision time pre-move; settlement only from gamma
   resolution; far-future-timestamp audit test. *medium* *(depends: 4)* **mandatory before trusting any number.**
6. **Real A15 Track-E whitelist** (≥30 settled, ≥60% win, >$5k PnL) — the actual
   edge-data dependency; flag survivorship/NBA→tennis transfer risk in the run log.
   *large* *(depends: 4)*
7. **Run the live paper loop** (multi-day) → JSONL; verify open→settled, BREATH
   deltas, weight updates; capture the first A15 edge readout. *medium* *(depends: 6)*
   **first go/no-go on whether any unpriced-info edge exists.**
8. **Graduation gate** — implement net-of-cost `gain ≥ 0.2` over a held-out window
   with fee/spread/thin-liquidity haircut + cluster-CI / honest-account guard that
   can return NO-GO. *medium* *(depends: 7)*
9. **Surface /mock** — `NEXT_PUBLIC_L5_COMPLETE` + `SANDBOX_STATE_DIR`, point
   `/api/sandbox` at the loop state dir; file-poll, no WS server. *small* *(depends: 7)*
10. **2nd probe — D1 sharp_line** — `data/sources/odds_api.py` + de-vig vs live CLOB
    as a second edge signal. *medium* *(depends: 7)*
11. **tennis_technical wrapper (T-B-015)** — move from 4 live engines to full 5. *medium* *(depends: 4)*
12. **E2E integration test** — market→paper-bet→settle→weight across ticks. *medium* *(depends: 7)*
13. *(optional)* real-time WS/SSE push. *(depends: 9)*
14. *(deferred)* `T-B-007` async streaming — **only if** polling proves too slow for
    the edge window (a null could be latency-limited, not edge-absent). *large* *(depends: 10)*

## 5. Open decisions (resolve before/early in planning)

1. **`gain ≥ 0.2` units** — Brier improvement? ROI? EV/bet? breath-flip-positive?
   `divinity-mechanism-spec §4` references it but code has no definition.
2. **Track-E wallet scan** — runnable now? Does it produce a *tennis*-relevant
   whitelist or only the NBA-trained set the fixture comment mentions?
3. **Polling cadence vs edge half-life** — is the wallet→price (A15) / sharp→PM (D1)
   lag long enough for a 2–30s poll, or sub-second (→ T-B-007)? Determines whether a
   null means "no edge" or "too slow."
4. **Isolation vs fusion** — run A15 alone (cleanest causal read; recommended) or all
   candidates fused (risks A0 strong-optimizer-wins).
5. **Fee/spread/liquidity model** — the haircut that turned A18 +pre-fee → −post-fee;
   needed to net-score mock wins on thin PM tennis markets.
6. **On-chain L5→Phase-2 gate** — must the `advancePhase` tx fire before the loop
   boots (competition narrative), or is the sandbox `_NoopPhaseReader` acceptable?
7. **Edge-engine architecture (§3)** — sign off on the separate additive Edge layer
   + sparse/candidate-active gating, or keep everything in the 5-engine fusion.

## 6. Honest caveats (carry into every readout)

- We do **not** know any edge exists; NO-GO is likely & acceptable.
- **Cost/liquidity blindness**: paper has no fee/spread/impact — re-score net before
  claiming an edge (A18 was +pre-fee, −post-fee).
- **Look-ahead is the silent killer** — PIT gate + audit test mandatory.
- **Latency confound** — null on polling ≠ no edge; may be too slow.
- **A15 survivorship/selection bias** — whitelist retro-fit on past winners; NBA→tennis transfer unproven.
- **Goalpost risk** — fix `gain≥0.2` + its cost-net/stat guard up front, or risk manufacturing a graduation.
