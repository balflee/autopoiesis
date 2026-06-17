# Stage-2 Mock Bet — Implementation Plan (V1 + V2)

> **plan-loop artifact, 2026-06-17.** Converges the full V1+V2 design from
> `docs/stage2-mock-dev-checklist.md` (Codex-reviewed twice) into a sequenced execution
> plan. **Implementation is staged: V1 built first; V2 deferred** until V1 runs + the
> trading×survival accounting decision (resolved below) is accepted. Codex is review-only.

**Goal:** an AI agent paper-trades live Polymarket tennis. **V1** = a live hold-to-resolution
loop on the 5 Sackmann/CLOB baseline signals + the P2 falsification probe, all cost-net,
no-look-ahead, reproducible. **V2** = adds the trading layer (in-play dynamic exit) + the
PM-internal edge probes (P4 wallet tracker, P1 order-flow). Three pillars: predict + trade +
survive.

**Non-goals (out of scope, do NOT build):** Lane B maker / spread-capture (P3); D1
cross-market; async streaming (T-B-007); any Anthropic/OpenAI LLM (prod LLM is Gemini);
touching `scripts/run_v3_ai.py` (pre-existing unrelated working-tree change).

**Constraints:** commit account MUST be `balflee` (256016480+balflee@users.noreply.github.com);
numerical / no-LLM by default; gitleaks not bypassed; all randomness seeded; respect the
verified code anchors below (line numbers drift — anchor by symbol).

---

## ⏯️ IMPLEMENTATION PROGRESS — resume handoff (2026-06-17)

**Plan converged via plan-loop (6 Codex rounds → HIGH=0 MED=0). V1 is ~half built.**
The plan-loop **Phase-3 Codex diff-review runs over the FULL V1 diff** — base commit
**`5f6942d`** (the plan commit) → `git diff 5f6942d HEAD` once V1 is COMPLETE. Do NOT run it
on the partial diff.

**DONE (committed, tested, zero regression):**
| step | commit | delivered |
|---|---|---|
| V1.1 SettlementClient | `36986dd` | `agent/runtime/polymarket_settlement_client.py` — prod gamma-api client (real parser) |
| V1.6 Graduation gate | `609c4df` | `agent/backtest/graduation_gate.py` — net-ROI `gain` + market-efficiency placebo → NO_GO on zero-edge/cost-eaten |
| V1.4a cost schema + PnL | `7833c4d` | `BetRecord` cost stamps (Optional, omit-when-None) + cost-NET `_compute_pnl` (legacy byte-identical) + `assert_cost_fields_present` |
| V1.4 executor threading | `c5ce794` | cost stamps through `place_order` (Protocol + impl) |
| V1.4b reason schema | `23364ab` | `SettledBetRecord.reason` + exclude_none (settled rows byte-identical) |
| **V1.4b terminal-close BEHAVIOR** | `3486926` | `_die` folds ALL open bets via poller `terminal_close()` (resolved→full `_resolve_and_settle` side-effects + bankroll fold; pending→void; query-fail→`void(reason=terminal_query_failed)`); clears `_open_bet_ids`. 191 runtime+data + 636 survival/backtest/sim/dashboard pass. |
| **V1.2 step-1 cost threading + grounding** | `0679444` | `TickInputs` size-independent LIVE cost inputs (`fill_price`/`fee_bps`/`half_spread_frac`) + `_tick` threads them (gated on `fill_price`-is-not-None; derives `spread_paid_usd = half_spread_frac × stake`). + the V1.2 GROUNDING deliverable written into this plan (§V1.2 grounding). |
| **V1.2 LiveTickInputSource** | `ecdcd44` | `agent/runtime/live_tick_input_source.py`: `GammaLiveDiscovery` (`/events?closed=false`, identifier bundle) + `parse_open_tennis_markets` + `LiveLedgerProvider` (rolling buffer backing `RealSignalSource`) + p1↔YES orientation (both + fail-closed) + identifier contract + `ClobBookPriceSource`. 14 hermetic tests incl. golden-vector parity. |
| **V1.3 SANDBOX_LIVE mode switch** | `1ec0bda` | `main.py` `_select_loop_sources` (live\|replay\|idle, mutually exclusive) + `_build_live_sources` + `_live_json_fetch`; `LiveTickInputSource.market_resolver`. 4 isolation tests (live never builds replay w/ cassettes; defaults unchanged). |

**REMAINING — resume in this order:**
- (none) — **V1 is COMPLETE.** Remaining-tail commits below.

| step | commit | delivered |
|---|---|---|
| V1.7 ACTUAL-trades source | `ba599d5` | `data/sources/polymarket_trades.py` — single canonical `actual_trade` provenance, fail-closed, no synthetic/midpoint fallback |
| V1.7 P2 probe | `b7bcd99` | `scripts/probe_p2_favorite_longshot.py` — favorite-longshot calibration + V1.6 gate + placebo, fail-closed skip |
| V1.5 env docs | `fd79ad1` | `.env.example` Stage-2 section (the dashboard wiring already existed; SANDBOX_LIVE/GENESIS_REAL_LEARNING/SANDBOX_STATE_DIR/NEXT_PUBLIC_L5_COMPLETE) |
| Phase-3 fixes r1 | `c83c16c` | Codex round-1 HIGH=2 MED=2 LOW=1 fixed (fail-closed cost guard, NO-side `side_correct_pricing`, flip cost-stamp copy, P2 PIT, import-cycle root fix) |
| Phase-3 fixes r2 | `99de91b` | round-2: MED-1 naive-asof→UTC coercion; HIGH-1 pushback (guard ON for void by design — `_compute_pnl` charges -cost on void) + doc + test |

**Phase-3 Codex diff-review (`git diff 5f6942d HEAD`) via `codex:codex-rescue`: r1 HIGH=2 MED=2 LOW=1 → fixed →
r2 (3 resolved, 2 flagged) → fixed/pushed-back → r3 UNRESOLVED=0.** Full suite green (only the 9 pre-existing
`test_tennis_technical` live-404 env failures). V1 = the live loop runs (hold-to-resolution, cost-net,
no-look-ahead, fail-closed) + P2 returns an honest reproducible go/no-go. V2 (trading layer / P4 / P1) deferred.

**GOTCHAS (learned this session):** repo runs async via `asyncio.run(...)` in SYNC tests — NO `pytest.mark.asyncio`.
Schema additions follow the storm-stamp Optional+omit-when-None pattern (`bet_record_jsonl_dict` / `exclude_none`)
to keep old JSONL rows byte-identical. Run `tests/agent/data/ tests/agent/runtime/` after any schema/loop change.
`MarketInfo`/`SandboxExecutor` are in `agent/data/polymarket_sandbox_executor.py`; `BetRecord`/`SettledBetRecord`
in `agent/data/sandbox_state.py` (NOT `agent/runtime/`). Commit `balflee`; leave `scripts/run_v3_ai.py` untouched.

---

## Resolved design fork — Trading × survival accounting (§5 #10; the V2 BLOCKER)

The survival economy (breath/death/reincarnation) is settlement-triggered; trading adds open
inventory + unrealized MtM + early exits. **Decision (realized-only + forced-liquidation-at-death):**

1. **Breath/bankroll move ONLY on REALIZED PnL** — a settlement (hold-to-resolution) OR an
   exit fill (early sell). **Unrealized MtM never moves breath/bankroll** → no double-count at
   settlement, and the 能学 settlement-credit mechanism extends unchanged to exits.
2. **The decision layer MAY READ unrealized MtM** (for exit timing + a separate max-open-exposure
   risk cap) but the breath-risk/Kelly gate sizes off REALIZED bankroll only (keeps the economy clean).
3. **At death** (breath depletes): force-liquidate every open position at the current CLOB mark,
   realizing its PnL into the terminal bankroll, THEN finalize death. Prevents "delay death while
   bleeding"; gives a clean terminal bankroll. (V1 has no open positions at death only because it
   holds to resolution — see V1 step 4b for the V1 settlement-finality edge, which is the simpler
   subset of this rule.)
4. **At reincarnation:** everything zeros — no position carry (consistent with permadeath +
   `survival_season.py` voiding unsettled bets).

This is the written decision V2's Block-1.5 is gated on. V1 does not touch it (no exits, no MtM).

---

## V1 — file-level change list + sequenced steps (build first)

### V1.1 SettlementClient (real) — restore PnL→BREATH→weight
**Files:** Modify `agent/server/main.py` (the `_NoopSettlementClient` injection seam, ~:2102);
new adapter `agent/runtime/polymarket_settlement_client.py`; new test
`tests/agent/runtime/test_polymarket_settlement_client.py`.
- [ ] Step 1 — write a failing integration test: a stubbed Gamma resolution for an open
  `BetRecord` → poller settles it → a `SettledBetRecord` with realized PnL → breath/bankroll/weight
  update fires. Run, see it fail (no real client).
- [ ] Step 2 — implement `PolymarketSettlementClient` by **promoting the already-tested
  `HttpSettlementClient` shape** (`tests/agent/runtime/test_sandbox_settlement_poller.py:965-981`,
  which wraps `agent.data.polymarket_settlement.resolve_market` over an injected `httpx.AsyncClient`)
  into runtime — do NOT reimplement from scratch (Codex-9: avoids drift from the VCR-tested parser
  shape). Map gamma resolution → the poller's `SettlementClient` Protocol. Swap it in at the
  `main.py` seam **only via the explicit V1.3 mode switch** (never co-active with replay).
- [ ] Step 3 — test green; `ruff` clean; commit.

### V1.2 TickInputSource (REST polling) — ⚠️ RISKIEST STEP
**Files:** Modify `agent/server/main.py` (`_IdleTickInputSource` seam, ~:2418); new
`agent/runtime/live_tick_input_source.py`; reuse `agent/backtest/real_signal_source.py`
(`signals_for`, :256-296) + the gamma/CLOB REST clients; test
`tests/agent/runtime/test_live_tick_input_source.py`.
- [ ] Step 1 — **Ground the RealSignalSource semantics FIRST** (Codex: snapshot/ledger vs
  `asof_ts=now` mismatch). Read `real_signal_source.py:256-296` + `main.py:2399-2425`; document in
  this plan exactly what `signals_for` needs (resolved players, surface, price ledger) and how to
  satisfy each from a LIVE open market with `asof_ts=now`. If a baseline signal cannot be computed
  live, it emits neutral-0 (documented), not a crash.
- [ ] Step 2 — failing test: a stubbed live open-tennis market (gamma `closed=false`) →
  `LiveTickInputSource` returns a `TickInputs` with the 5 baseline keys + live CLOB price +
  liquidity cap.
- [ ] Step 3 — implement a **DEDICATED live discovery method** (Codex-r2-M2a: do NOT mutate
  `PolymarketHistoryClient.list_tennis_markets`, which has no `closed=false` and whose callers are PIT
  historical) that lists open tennis markets (gamma `tag_slug=tennis&closed=false`) AND **carries the
  per-market outcome/token labels** (Codex-r2-M2b: `fetch_market`/`MarketHistory` strips them); fetch
  live CLOB mid + liquidity; build signals via `RealSignalSource(asof_ts=now)`; swap at the
  `main.py:2418` seam **via the V1.3 mode switch**.
- [ ] Step 3b — **SIDE-ORIENTATION validation (Codex-7, MANDATORY)**: `RealSignalSource` scores
  positive for the resolver's `p1` (parsed from the slug), and `DecisionEngine` maps positive→`YES`
  (`decision.py:23-25,370-403`). In LIVE there is NO guarantee the Gamma/CLOB **YES token** == resolver
  `p1`. Using the token labels from Step 3 (NOT inferred from slug/order — Codex-r2-M2b), `LiveTickInputSource`
  MUST assert `resolver.p1 ↔ YES` or **invert the signal/price side explicitly**; a test covers BOTH
  orientations (right-side + inverted) — else live bets silently invert while PnL still "looks valid."
  **Per-slot convention guard (Codex-r3-M):** the assert above is MARKET-level, but individual slots have
  their OWN sign conventions (e.g. `rest_signal` = `tanh((d2-d1)/14)` is positive when **p2** is the
  more-rested side — opposite the p1-positive elo/surface/h2h). So the live path MUST call `RealSignalSource`
  with the SAME resolver-derived `(p1,p2)` as backtest, and a **golden-vector parity test** asserts the
  LIVE signal vector == the BACKTEST signal vector **sign-for-sign** for a fixture match (catches any
  per-slot inversion, not just the fused side).
- [ ] Step 3c — **Identifier contract (Codex-r4-1, MANDATORY)**: Polymarket has THREE ids — the Gamma
  `conditionId` (market), the `clobTokenIds` (YES/NO tokens), and the settlement key. `LiveTickInputSource`
  prices by **token** while `PolymarketSettlementClient` / `Executor.place_order` key off **market_id** —
  pin the mapping so a tick cannot price one id and settle another. The live tick carries `(conditionId,
  yes_token_id, no_token_id)`; `BetRecord` stores the id the poller settles on; a test asserts the priced
  token's market resolves to the SAME `conditionId` the settlement uses (else: failed/mis-settled bets).
- [ ] Step 4 — test green; ruff; commit. **Budget for iteration here — do not assume a 1-hour swap.**

#### V1.2 GROUNDING (Step-1 deliverable — code-grounded 2026-06-17 via the v12-grounding workflow)
**Exact contracts the LIVE path must honor (anchor by symbol; line numbers drift):**
- `TickInputSource.inputs_for(*, asof_ts: datetime, tick: int) -> TickInputs | None` (`sandbox_phase2_loop.py:529`).
  `TickInputs(market_id: str, signals: dict[str, Signal], price: float, liquidity_cap_usd: float,
  cross_market_signal: float = 0.0)` (`:537-548`). **Gap found:** V1.4 added the cost stamps to
  `place_order` (`polymarket_sandbox_executor.py:166-183`) + `BetRecord`, but `_tick`
  (`sandbox_phase2_loop.py:1801-1808`) does NOT pass them and `TickInputs` does NOT carry them — so the
  LIVE cost-net path is INCOMPLETE. V1.2 completes it: add OPTIONAL `fill_price`/`fee_bps`/
  `spread_paid_usd` to `TickInputs` (liquidity_cap already present; default None → replay/idle unchanged) +
  thread them `_tick → place_order` so the fail-closed LIVE settlement guard (`assert_cost_fields_present`)
  is satisfied.
- `RealSignalSource.signals_for(*, market_id, tick, asof_ts) -> dict[str, Signal]`
  (`real_signal_source.py:256`). Reuse VERBATIM. It needs only a `provider.get(market_id) -> snap`
  exposing `.price_ledger: list[PricePoint(ts: iso-str, mid_price: float)]` (structural Protocol,
  `:231-232`) + a `TennisMatchResolver` + `SackmannLoader`. The 4 tennis facets use ONLY `asof_ts`
  (static Sackmann corpus); MOMENTUM is the ONLY slot consuming `price_ledger`, filtered `ts <= asof_ts`
  by `_snapshots_until` (`:290-297`). Empty/unresolved → `_neutral` (score 0, conf 0); momentum is ALWAYS
  real (even if the slug doesn't resolve).
- **THE RABBIT HOLE (confirmed):** `price_ledger` is a frozen retrospective list in backtest. LIVE has no
  ledger at decision time. **Resolution:** `LiveTickInputSource` maintains a mutable per-market rolling
  buffer `dict[market_id, list[PricePoint]]`, appends `(now, mid)` each tick, and exposes a tiny live
  provider whose `.get()` returns a snap backed by that buffer. `asof_ts=now` then makes `_snapshots_until`
  include the just-appended tick. Momentum is neutral until ≥2 ticks accrue (acceptable for the V1
  hold-to-resolution baseline). Sorted-ascending append (momentum math needs it, `:59-101`).
- **SIGN / ORIENTATION (Codex-7 + r3-M) — the core correctness gate.** Within `RealSignalSource` the 4
  tennis facets + momentum are **p1-oriented** (positive favors p1), EXCEPT `rest_recency` which is
  **p2-oriented** (`tanh((d2-d1)/14)`, positive when p2 more rested, `:221`). `DecisionEngine` maps
  positive fused → market YES token (`decision.py:25,378,401`). resolver.p1 comes from the slug
  (`tennis_match_resolver.py:63-71,110`); **NO guarantee p1 == the Gamma YES token.** Design (single
  consistent frame, then ONE uniform flip):
  1. Determine `p1_is_yes` by matching the YES `clobTokenId`'s OUTCOME LABEL to the slug's p1 surname
     (token labels from discovery, NOT slug order — Codex-r2-M2b).
  2. Feed the ledger the **p1-side** mid: `yes_mid if p1_is_yes else 1 - yes_mid` → the WHOLE 5-vector is
     p1-oriented and matches backtest sign-for-sign (the golden-vector parity invariant).
  3. At the boundary, emit YES-frame signals: if `not p1_is_yes`, negate ALL 5 scores
     (`Signal.model_copy(update={"score": -s})`); the uniform flip is a p1→YES frame swap that preserves
     each slot's internal convention (incl. rest). Report `TickInputs.price = yes_mid` (the loop bets the
     market YES token at this price). **Tests cover BOTH orientations.**
- **IDENTIFIER CONTRACT (Codex-r4-1).** Three ids: Gamma `conditionId` (market), `clobTokenIds`
  (YES/NO token), settlement key. `market_id` == settlement key == Gamma `id` everywhere
  (`polymarket_settlement.py:resolve_market /markets/{id}`, `place_order(market_id)`). Discovery carries
  `(conditionId, yes_token_id, no_token_id, yes_outcome_label, no_outcome_label, slug, end_date, market_id)`;
  the tick prices the YES TOKEN but the BetRecord/settlement key off `market_id` — a test asserts the priced
  token's market resolves to the SAME `conditionId`/`market_id` the settlement uses.
- **DISCOVERY.** Reuse the `/events?tag_slug=tennis&active=true&closed=false` pattern (proven in
  `sprint7_dryrun.discover_tennis_markets:164-223` via an injectable fetcher), but a DEDICATED method
  carrying the token/outcome labels (`discover_tennis_markets`'s `TennisMarket` strips `clobTokenIds`/
  outcomes; `list_tennis_markets` uses the WRONG `/markets` endpoint and must NOT be mutated). Live CLOB
  mid + liquidity: no REST live-mid client exists yet → an injectable `LivePriceSource` Protocol
  (`price_and_liquidity(token_id) -> (mid, liquidity_usd)`); the loop logic is hermetically tested with a
  fake, the real HTTP impl is thin + wired by V1.3.
- **GOLDEN-VECTOR PARITY.** Fixtures: `tests/agent/backtest/fixtures/tennis_real_capture.json` (real market
  506130 Shelton-vs-Tiafoe, 75 real CLOB ticks) + `cached_sweep.SignalRow`/`precompute_rows`/`row_to_signals`
  (`cached_sweep.py:132-253`). Parity test: a fixture match's p1-oriented LIVE signal vector ==
  the backtest `signals_for` vector sign-for-sign (catches any per-slot inversion the market-level
  orientation check misses).

### V1.3 Explicit LIVE mode switch (Codex-1, HIGH — isolation, not a bare flag)
**Files:** Modify `agent/server/main.py` (`_build_default_app` ~:2396-2420 + the production loop
factory ~:2160-2169); `.env.example`; test `tests/agent/server/test_live_mode_isolation.py`.
- [ ] Step 1 — failing test: with `SANDBOX_LIVE=1`, `_build_default_app` constructs the live
  `PolymarketSettlementClient` + `LiveTickInputSource` and **NEVER** constructs `_ReplayTickInputSource`,
  `_ReplaySettlementClient`, or calls `_synth_outcome_from_market_id` (`replay_runner.py:455-488`) —
  even when cached snapshots exist on disk (the current default prefers replay when cassettes are
  present). Assert mutual exclusivity.
- [ ] Step 2 — implement ONE top-level mutually-exclusive mode selector (`live` | `replay` | `idle`);
  `SANDBOX_LIVE=1` ⇒ `live`, never falls back to replay/synthetic. **With `SANDBOX_LIVE` UNSET the
  default is the EXISTING selection unchanged — replay-when-cassettes-present, else idle (Codex-r2-H1:
  NOT "noop stubs") — so existing replay tests are untouched.** Wire `GENESIS_REAL_LEARNING=1` to
  the real `WeightUpdater` on the poller; add `SANDBOX_STATE_DIR`. **(Drop the earlier "freeze β₁"
  claim — Codex-6: the `GENESIS_REAL_LEARNING` seam wires `_SettlementLearningWeightUpdater`, which
  maps `PHASE_2_EXTENDED`→`Phase.PHASE_2_APPRENTICE` and trains all 6 params (`settlement_learner.py:40-43`,
  `weight_updater.py:307-315`); β₁ is NOT frozen there. V1 trains all 6; a β₁ freeze mask is a deferred
  variance-reduction nicety, not a v1 claim.)**
- [ ] Step 3 — test green (incl. the no-replay-leak assertion); ruff; commit.

### V1.4 Execution-cost SCHEMA threaded through the WHOLE path (Codex-3, HIGH; Codex-5 paths)
**Files (correct paths):** `agent/data/sandbox_state.py` (`BetRecord` :113-166, `SettledBetRecord`
:193-210 — NOT `agent/runtime/...`); `agent/data/polymarket_sandbox_executor.py`
(`Executor.place_order` :166-179, impl :246-327); `agent/runtime/sandbox_phase2_loop.py` (`_tick`
passes only `inputs.price`+`action.size_usd` :1801-1807); `agent/runtime/sandbox_settlement_poller.py`
(`_compute_pnl` :778-844 + status-flip copying); the executor fakes in tests; `tests/agent/runtime/
test_sandbox_settlement_poller.py`.
- [ ] Step 1 — failing test: a settled bet's realized PnL is NET of fee + half-spread/crossing +
  liquidity cap, using the **recorded fill price** (not a post-move price); AND a test that live/probe
  settlement **REJECTS** a `BetRecord` missing execution-cost fields (no silent zero-cost).
- [ ] Step 2 — thread cost fields through the ENTIRE path together (Codex-3: BetRecord-only +
  default-zero would silently book zero costs): add `fill_price`/`fee_bps`/`spread_paid`/
  `liquidity_cap_usd` to `TickInputs` → `Executor.place_order` (+ all fakes) → `SandboxExecutor` →
  `BetRecord` (immutable, set at decision time) → poller status-flip copy → `_compute_pnl` haircut.
  **Migration boundary (Codex-r2-H3 — `BetRecord` is `extra="forbid"`):** the fields are declared
  **Optional** on `BetRecord` so existing `open_bets.jsonl` rows + replay cassettes still load; the
  **LIVE / probe settlement path RAISES if any is None (fail-closed)**, while the replay/legacy path
  tolerates None. Decision-time fill price + `asof_ts=now` PIT on every signal.
- [ ] Step 3 — far-future-timestamp audit test (no-look-ahead). Test green; ruff; commit.

### V1.4b Terminal-close path (Codex-4, HIGH) — chained with V1.3 to kill ghost PnL on reincarnation
**Files:** `agent/runtime/sandbox_phase2_loop.py` (`_tick` :1648-1655/1891-1897, `_die` :2204-2216);
`agent/runtime/sandbox_settlement_poller.py` (the poller only selects bets DUE — `expected_settle_ts <
now`, :397-407); `agent/data/sandbox_state.py` (`SettledBetRecord` :193-210 — **add an optional bounded
`reason` field; it is `extra="forbid"`, Codex-r5-1, so the `reason="terminal_query_failed"` record below
cannot validate without it**); test in `tests/agent/runtime/test_sandbox_phase2_loop_*.py`.
- [ ] Step 1 — failing test: death with non-empty `open_bet_ids` → EVERY latest-open bet is terminally
  resolved (none left dangling), INCLUDING resolved-but-not-yet-due ones.
- [ ] Step 2 — add a dedicated **terminal-close** path (NOT a normal `poller.tick()` — that skips
  not-due bets, Codex-4): fold all latest-open bets regardless of `expected_settle_ts`; for each, query
  the market. **A resolved bet MUST go through the poller's full `_resolve_and_settle` side-effect path
  (Codex-r2-H4: bankroll `_bankroll_usd` + chain breath + weight-update), NOT just a file marker** —
  else the ledger shows settled while terminal bankroll/breath/weights miss the realized PnL. An
  unresolved bet → `SettledBetRecord(outcome="void", pnl_usd=0)` + flip `BetRecord` to `settled` (no
  economic side effect). **On a settlement-QUERY FAILURE at terminal-close (Gamma retries exhausted,
  Codex-r4-2):** the bet is still recorded as `SettledBetRecord(outcome="void", pnl_usd=0,
  reason="terminal_query_failed")` (NOT dangled, NOT cleared without a ledger entry) — the fold-all
  guarantee is "every open bet gets a terminal ledger record," even when the query errors. Then
  `_open_bet_ids` → empty; snapshot; `_die`. (Chained with V1.3 isolation — without terminal void a
  reincarnation carries ghost PnL.) V2 replaces "void" with "force-liquidate at mark" per the §5#10 decision.
- [ ] Step 3 — test green; ruff; commit.

### V1.5 Surface /mock
**Files:** Modify dashboard env (`NEXT_PUBLIC_L5_COMPLETE`, `SANDBOX_STATE_DIR`); point
`/api/sandbox` at the loop state dir.
- [ ] Step 1 — set the env + state-dir pointer; file-poll, no WS server. Manual check: `/mock`
  renders the live loop's JSONL. Commit. → **mock bet is RUNNING (hold-to-resolution baseline).**

### V1.6 Gain metric (coded, default-ON) — Block 2 prerequisite
**Files:** new `agent/backtest/graduation_gate.py`; test `tests/agent/backtest/test_graduation_gate.py`.
- [ ] Step 1 — failing test: `gain` = net ROI/EV per $ staked after fee + half-spread/crossing +
  failed fills + liquidity cap; a cluster-bootstrap / placebo guard that RETURNS NO-GO on a
  shuffled-label control; Brier secondary/diagnostic only.
- [ ] Step 2 — implement; default-ON. Test green (incl. the placebo returns NO-GO); ruff; commit.

### V1.7 Probe P2 — PM favorite-longshot calibration (backtest-only)
**Files:** new **provenance-tagged historical trades source** `data/sources/polymarket_trades.py`
(Codex-2 — NO such source exists: `historical_fetcher.py:328-395/454-497` SYNTHESIZES a ledger, and
`data/sources/polymarket.py:PolymarketHistoryClient.fetch_market:102-149` returns `/prices-history`
MIDPOINTS only, NOT actual fills); new `scripts/probe_p2_favorite_longshot.py`; fixtures; test
`tests/.../test_probe_p2.py` + `tests/data/test_polymarket_trades.py`.
- [ ] Step 1 — **build the real-fills source FIRST**: pull ACTUAL historical trades from the Polymarket
  Data API (`/trades` / `/activity`), tag each price with the **single canonical** `provenance="actual_trade"`
  (Codex-r2-M5: ONE spelling, no `actual_fill` alias). **P2 FAILS CLOSED**: it skips (and logs) any market
  whose entry price provenance is not exactly `actual_trade` — it must NEVER fall back to the synthetic
  ledger or midpoint history (Codex-2: that preserves the manufactured-result bypass). Fixture + test the
  fail-closed.
- [ ] Step 2 — failing test on a fixture: bin tennis markets by REAL entry-price decile; net-ROI per
  decile after cost; the favorite bin must clear the fee dead-zone or NO-GO; emit the calibration curve.
  Placebo (shuffled outcomes) → NO-GO. *(this makes P2 medium, not small — the new source is the cost.)*
- [ ] Step 3 — run on real PM tennis history; record the honest go/no-go; ruff; commit.

**V1 done = the loop runs live (hold-to-resolution, cost-net, no-look-ahead) + P2 returns an honest,
reproducible go/no-go.** Expected outcome: likely loses to fees (baseline, no edge) + P2 likely NO-GO
post-fee — both acceptable; the milestone is a HONEST running system.

---

## V2 — sequenced (deferred; each gated)

> Built only after V1 runs. The accounting fork above is the V2 entry gate.

### V2.0 Accept the trading×survival accounting decision (above) — written sign-off, no code.
### V2.1 Trading layer (Block 1.5): `SELL`/`EXIT` `ActionKind` (`state.py:45-49`) + position manager
(token/entry/size + live mark) + mark-to-market + round-trip (2×) spread; force-liquidate-at-death per
the accounting decision; in-play tick source (poll held markets; full-match not first-set).
### V2.2 Probe P4 — PM smart/dumb-wallet tracker (re-promoted A15): as-of reconstruction from
per-wallet `/activity` (NOT current `/holders` snapshot — that's look-ahead); confidence-weighted
per-wallet (sample-size shrinkage; elite tier; fade-dumb variant); strict out-of-sample/placebo.
**Producer (Codex-8):** `PolygonChainLive` is an `eth_subscribe` CTF-fill STREAM filtered by the
NBA-stub whitelist (`polygon_chain.py:135-225,341-396`) — it is a possible LIVE-event helper ONLY,
NOT a historical `/activity` reconstructor. P4 needs a **separate historical `/activity` reconstructor**
(new, built when V2 begins); do NOT force P4 onto the live-stream skeleton.
### V2.3 Learner-isolation harness: per-candidate isolation + holdout + placebo + exploration budget
cap + uselessness-stop + cluster/market-family guard (before any EdgeSignal earns weight).
### V2.4 Probe P1 — PM order-flow reversion (needs the live loop + trading layer): short-term PM price
regression after a flow shock, validated by an execution-PnL test net of cost (else circular).
### V2.5 Cross-life learner persistence check + lifecycle decoupling if sparse-signal convergence
(50–100+ returns) exceeds a life (~20 bets).

---

## Test / verification plan
- Per step: a failing test first, then minimal impl, then green (TDD). `ruff check` + the touched
  pytest suites green per commit. No-look-ahead audit test in V1.4. Placebo-returns-NO-GO test in
  V1.6/V1.7 (the anti-manufactured-graduation guard).
- V1 acceptance: boot the live loop (`SANDBOX_LIVE=1 GENESIS_REAL_LEARNING=1`) against live gamma;
  observe ≥1 open→settled cycle with cost-net PnL + a breath/weight update; P2 emits a calibration
  curve + a go/no-go that reproduces on re-run (seeded).
- Full regression: `PYTHONPATH=. python -m pytest tests/ -q` green except the known-environmental
  `test_tennis_technical.py` live-data 404s.

## Risk + rollback
- **TickInputSource (V1.2)** is the riskiest — the RealSignalSource asof_ts mapping may force a
  refactor; rollback = keep `_IdleTickInputSource` (loop runs, NO_BET every tick) until resolved.
- **All live behaviour is flag-gated** (`SANDBOX_LIVE`); default-off reverts to the current noop
  stubs → zero impact on existing tests/behaviour.
- **Cost model wrong** = the A18 trap; mitigated by V1.4's cost-net test + V1.6's placebo guard.
- **P2 synthetic-price leak** = manufactured result; mitigated by V1.7 step 1 (real prices only, skip+log).

## Revision log
- (init 2026-06-17) first draft from the converged checklist; V2 accounting fork resolved
  (realized-only + force-liquidation-at-death).
- (round 1 — Codex VERDICT HIGH=4 MED=4 LOW=1; all 9 accepted, no rebuttals) **H1** V1.3 made an
  explicit mutually-exclusive LIVE mode switch + test that `SANDBOX_LIVE=1` never builds the replay/
  synth path. **H2** V1.7 must build a provenance-tagged real-trades source first + fail closed (no
  synthetic/midpoint fallback). **H3** V1.4 threads cost fields through the WHOLE path (TickInputs→
  place_order→executor→BetRecord→poller), rejects missing-cost, not just terminal `_compute_pnl`.
  **H4** V1.4b uses a dedicated terminal-close fold-ALL path (not `poller.tick()` which skips not-due
  bets), chained with H1 to kill ghost-PnL on reincarnation. **M5** corrected paths to
  `agent/data/sandbox_state.py` + `agent/data/polymarket_sandbox_executor.py`. **M6** dropped the
  unsupported "β₁ frozen" claim (the learning seam trains all 6 in Phase-2-extended). **M7** V1.2 adds
  mandatory live side-orientation validation (resolver-p1 ↔ YES token, test both). **M8** relabeled
  `PolygonChainLive` as a live-event helper only; P4 needs a separate `/activity` reconstructor.
  **L9** V1.1 reuses the tested `HttpSettlementClient` shape instead of reimplementing.
- (round 2 — Codex VERDICT HIGH=3 MED=3; fixes 5-9 confirmed correct; all 6 new boundary-gaps accepted)
  **r2-H1** corrected the mode default: `SANDBOX_LIVE` unset = existing default (replay-if-cassettes-else-
  idle), NOT "noop stubs". **r2-H3** cost fields are Optional on `BetRecord` (old rows/cassettes load);
  LIVE/probe settlement raises on None (fail-closed), replay tolerates. **r2-H4** terminal-close routes
  resolved bets through the full `_resolve_and_settle` side-effects (bankroll/breath/weight), not just file
  markers. **r2-M2a** V1.2 uses a dedicated live discovery method (not mutating `list_tennis_markets`).
  **r2-M2b** that method carries outcome/token labels for the orientation check (not slug-inferred).
  **r2-M5** single canonical provenance `actual_trade` (dropped the `actual_fill` alias).
- (round 3 — Codex VERDICT HIGH=0 MED=1; all round-2 fixes confirmed landed) **r3-M** added a per-slot
  convention guard to V1.2 3b: `rest_signal` has an opposite (p2-positive) sign convention, so the live
  path must use the same resolver `(p1,p2)` as backtest + a golden-vector parity test (live signals ==
  backtest signals sign-for-sign) catches any per-slot inversion the market-level orientation check misses.
- (round 4 — Codex HIGH=0 MED=2; both new + accepted) **r4-1** V1.2 3c pins the Polymarket identifier
  contract (conditionId vs clobTokenIds vs settlement key) so a tick can't price one id and settle another.
  **r4-2** V1.4b records a settlement-query failure at terminal-close as `void(reason=terminal_query_failed)`
  — every open bet gets a terminal ledger record even when the Gamma query errors (no dangling/clear-without-ledger).
- (round 5 — Codex HIGH=0 MED=1; accepted, a consequence of r4-2) **r5-1** V1.4b adds an optional bounded
  `reason` field to `SettledBetRecord` (it is `extra="forbid"`) so the `terminal_query_failed` void record validates.
