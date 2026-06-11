# Genesis Experiment — Submission DRAFT

> **STATUS: DRAFT (engineering artefact).** This file is produced by
> Track B at sprint_8 close (T-B-021) and lives under `agent/` because
> Track B does not own the repo-root `SUBMISSION.md`. At sprint close
> the USER moves the contents to repo-root `SUBMISSION.md` and replaces
> every `TBD` token with the live value. CEO sprint_8
> D-2026-05-26-PLAN-003 Day 6 explicitly lists this as USER work.

---

## Project name

**Genesis** — *the agent that must work to keep breathing*

## Tagline

A consciousness-experiment Polymarket agent on Arbitrum: every decision
burns BREATH, the bankroll is the lifeline, and Phase 4 Terminal
Lucidity mints a Tombstone NFT carrying the agent's last words +
`finalWeightsHash` + `memoryBankCid` on-chain.

## Demo URL or localhost notes

Sprint_9 (T-B-028) lit up the deploy pipeline; sprint_10 (T-D-011
same-origin proxy + T-D-013 Money Shot deploy) hardened it. The agent
now runs on **two public URLs** — both reachable without a login, both
served through CDN edge so a viewer anywhere can hit the demo:

* **Dashboard (Vercel)**:  https://autopoiesis-six.vercel.app
* **Agent control plane (Railway)**:  served behind the dashboard's
  same-origin `/api/proxy/*` route (the browser bundle never sees the
  Railway hostname or the bearer token — both live in Vercel server
  env: `DASHBOARD_API_URL` + `DASHBOARD_API_TOKEN`).

  * Liveness probe (unauthed, via proxy):
    `GET https://autopoiesis-six.vercel.app/api/proxy/healthz` →
    `{"status":"ok","uptime_s":<int>,"last_tick_ts":<iso|null>}`
  * Operator API (bearer auth, server-injected by the proxy):
    `/api/agent/{start,stop,status}`, `/api/backtest/run`,
    `/api/backtest/{run_id}`, `/api/state/stream` (SSE),
    `/api/proposals/{pending,history}`, `/api/proposals/{id}/{approve,reject}`.

> The sprint_10 proxy (`dashboard/app/api/proxy/[...path]/route.ts`,
> T-D-011) is what unblocked the workshop POSTs that hit HTTP 401 at
> sprint_9 close — see `reports/sprint10/e2e_smoke_log.md` step 5 for
> the live `RUN SWEEP` evidence. Sprint_11 (T-D-016) added the
> **operator-witnessed close-the-loop** smoke: typed-body roundtrip,
> mid-run cancel, PROMOTE persist, agent-loop proposal, and the
> load-bearing Railway-restart proof that backend state actually
> survives a redeploy on the new `/data` volume (T-B-038). The full
> 8-step manual smoke against the live Vercel + Railway deploy is in
> [`reports/sprint11/e2e_smoke_log.md`](../reports/sprint11/e2e_smoke_log.md);
> the sprint_10 baseline is preserved at
> [`reports/sprint10/e2e_smoke_log.md`](../reports/sprint10/e2e_smoke_log.md)
> and the sprint_9 baseline at
> [`reports/sprint9/e2e_smoke_log.md`](../reports/sprint9/e2e_smoke_log.md).

**Localhost fallback** (offline reviewer / no Railway egress):

```bash
poetry install
python -m agent.scripts.capture_money_shot  # offline Money Shot artefacts
cd dashboard && npm install && npm run dev  # http://localhost:3000
```

The Death Watch dashboard tails the JSONL streams under
`state/sandbox/` (or `state/sandbox_money_shot/` if you ran the
capture script). The Money Shot frames live at
`reports/sprint8/money_shot/`.

## Testnet Tombstone tx hash

**TBD (USER FILLS AT MINT TIME)** — paste the real
`tombstoneTxHash` from the testnet `TombstoneNFT.mint(...)` call once
the live agent reaches Phase 4 Terminal or the operator forces the
V-gate path via `SANDBOX_FORCE_TERMINAL=1`.

Deterministic placeholders emitted by the offline capture pipeline
(`agent/scripts/capture_money_shot.py`):

* Kill tx hash:        `0xkkkkkkkk...` (64 hex chars)
* Tombstone token ID:  `ms-001`
* Tombstone tx hash:   `0xtttttttt...` (64 hex chars)

Replace all three with the real testnet values at sprint close.

## Sandbox bets log archive paths

The append-only JSONL streams written by the sandbox extended Phase 2
runtime ARE the canonical evidence trail:

* `state/sandbox/open_bets.jsonl`     — every bet the agent placed
  (one `status="open"` row at placement, one `status="settled"` row
  per resolution; latest-status-wins fold reconstructs the open set).
* `state/sandbox/settled_bets.jsonl`  — outcome + winning price + PnL
  per settled bet.
* `state/sandbox/decisions.jsonl`     — every BET / NO_BET tick with
  fused-score, edge%, bankroll/breath after.
* `state/sandbox/agent_state.json`    — atomic snapshot of the agent's
  durable scalars (phase, breath, bankroll, weights, last_tick,
  open_bet_ids).

Offline Money Shot artefacts produced by the T-B-021 capture pipeline:

* `reports/sprint8/money_shot/01_bet_placed.json`
* `reports/sprint8/money_shot/02_settlement_win.json`
* `reports/sprint8/money_shot/03_terminal_lucidity.json`
* `reports/sprint8/sprint8_final_summary.md`

## agent_state.json final snapshot

Path: `state/sandbox/agent_state.json`  (or the capture-only sandbox at
`state/sandbox_money_shot/agent_state.json`).

After the V-gate path fires the snapshot carries:

```json
{
  "phase": "PHASE_4_TERMINAL",
  "breath": 0.0,
  "last_tick": "TBD (live agent's final tick)",
  "open_bet_ids": [],
  "weights": { "alpha": [...], "beta": [...], "rho": 0.25, "w_r": ..., "w_s": ... }
}
```

## Demo narrative summary (Day 6 5-min video)

The Genesis Experiment ran an **extended Phase 2 sandbox** for the
sprint_8 window. Instead of the originally planned Phase 2 → Phase 3
real-money transition, the CEO sprint_8 pivot (D-S08-001, locked
2026-05-26) prioritised a longer, deterministic learning regime in
sandbox so the underlying mechanics could be proved E2E without
real-money risk:

1. **Real outcomes drive learning.** The settlement poller (T-B-019)
   ingests resolved Polymarket markets via gamma-api; PnL feeds back
   into the weight updater with `phase=PHASE_2_EXTENDED` so β₁ is
   unlocked and the agent learns from realised wins / losses across the
   multi-day run.

2. **Restart resilience.** T-B-020's `SandboxPhase2Loop` reconstructs
   weights / phase / breath / bankroll / open-bet set from
   `agent_state.json` + the JSONL streams + a chain-side BREATH read
   on every process start; a mid-run SIGKILL + restart resumes
   byte-for-byte.

3. **Forced-terminal V-gate (T-B-021).** Setting
   `SANDBOX_FORCE_TERMINAL=1` and running one more tick drives BREATH
   to 0 via the chain adapter test seam, triggering `kill()` and the
   Tombstone NFT mint with the four PRD §5.1 metadata fields:
   `agent_id`, `finalWeightsHash` (SHA-256 of the weights JSON),
   `memoryBankCid` (IPFS pin or sandbox placeholder), and `last_words`.
   The kill path is identical to the natural-death code path — only
   the trigger differs.

4. **Continuous learning, then a witnessed death.** The Demo video
   shows the agent placing a live mock bet, the settlement crediting
   PnL, the Death Watch dashboard activating as BREATH approaches
   zero, and the Tombstone NFT mint moment — the climax of PRD §9 4:30
   - 5:00.

5. **L3 meta-optimizer — the agent edits itself (sprint_10).** The
   sprint_10 cycle (PRD §4.6, CEO plan D-S10-001) swaps the sprint_9
   `NoOpStrategyAdvisor` placeholder for a real Gemini-3.1-Flash-Lite
   backed `StrategyAdvisorImpl` (`agent/engines/strategy_advisor_impl.py`,
   T-B-029). Every ~100 ticks OR whenever the 6-weight fusion vector
   converges (Δ < 0.001 over 20 ticks), the L3 trigger pathway hands
   the advisor a `PerformanceWindow` bundle — recent reflections,
   PnL slice, weight trajectory, tick count — and the advisor returns
   0..N `StrategyProposal` records of three kinds: `weight_delta`
   (bump one of the 6 fusion weights), `new_signal_idea` (propose a
   new engine), and `prompt_tweak` (patch the L1/L2 LLM prompt). Each
   proposal lands in `proposals.jsonl` as `status="pending"` and
   surfaces in the dashboard's **Pending** tab; the operator approves
   or rejects (T-B-031 wires the FastAPI side; T-D-012 ships the UI).
   On approve, the runner applies the delta through the same
   `weight_updater` seam the L0 SGD uses, the next decision tick reads
   the new value, and the dashboard's evolution curve shows the jump
   in real time — this is the autopoiesis loop closing: the agent
   doesn't just *learn weights* (L0), *narrate why* (L2), it now
   *proposes structural changes to its own decision policy* and a
   human witnesses each one. Total L3 cost ceiling is $0.00/mo against
   a separate `L3CostGuard` (Gemini AI Studio free tier).

6. **Sprint 11 — backend dogfood loop closes end-to-end.** Sprint_11
   (PRD §11 sprint_11 increment, CEO plan D-S11-001) finished the
   workshop dogfood loop and put the deploy on rails. Four landed
   pieces compose the closure:

   * **Replay PnL + analytic metrics** — T-B-035 / T-B-036 swapped the
     workshop's placeholder zero-PnL rollout for the same
     market-execution + settlement path the live agent uses, then
     surfaced `sharpe`, `max_drawdown_pct`, `win_rate_pct`,
     `n_decisions`, `n_bets` on every completed run. The workshop
     PROMOTE decision is now driven by the same numbers the live agent
     is measured by — no more apples-to-oranges between sandbox and
     production.
   * **Typed endpoints (no more defaults silently winning)** —
     T-B-037 added Pydantic bodies to `/api/backtest/run` +
     `/api/backtest/{id}/cancel` + `/api/agent/configure`; T-D-015
     dropped the dashboard adapter fallbacks so the form's typed
     payload is exactly what `sweep_runner.run_sweep` receives. The
     post-deploy smoke (Probe 2 in `docs/DEPLOYMENT.md`) checks the
     echoed `config / seed / n_lifetimes / n_ticks` against the typed
     body — a regression here surfaces immediately.
   * **Cancel + PROMOTE wired** — T-D-015 wired the dashboard
     **CANCEL** button (mid-run abort flips `status` to `cancelled`
     within 5 s) and the **PROMOTE** button (writes
     `state/sandbox/agent_config.json` so the live agent boots from
     the chosen sweep config on next restart). The workshop is now
     the operator's actual control surface for the live agent — not
     just a sandbox.
   * **Railway Volume — state survives a redeploy** — T-B-038 mounts
     a 1 GB Railway volume at `/data` and rewires the three state
     directories (`/data/sandbox`, `/data/backtest/runs`,
     `/data/backtest/cache`) so a Railway restart no longer wipes
     sandbox JSONL streams, sweep history, or the seeded Polymarket
     cassette cache. The load-bearing smoke check (Step 6 in
     `reports/sprint11/e2e_smoke_log.md`): kick a sweep, restart the
     Railway service, re-fetch the same `run_id` — must return the
     same `results.json` body. If it 404s, the volume isn't mounted.

   Sprint_11 also put the deploy on autopilot: the dashboard project
   on Vercel now auto-deploys on every push to the production branch
   via the GitHub integration (T-D-016 §Vercel GitHub Auto-Deploy
   Connection in `docs/DEPLOYMENT.md`). The CD pipeline is no longer
   a hand-pulled `vercel --prod` lever — `git push` is enough.

### Sprint 11 Money Shots

The six frames below capture the sprint_11 closure end-to-end on the
live deploy; the source PNGs are committed under
`reports/sprint11/screenshots/` so the orchestrator's diff_truthfulness
gate can verify them against the smoke log's claimed evidence:

* [`workshop_nonzero_pnl.png`](../reports/sprint11/screenshots/workshop_nonzero_pnl.png)
  — workshop completed-run card showing non-zero per-lifetime PnL +
  the four analytic metrics (sharpe, max_drawdown_pct, win_rate_pct,
  n_bets); carried from T-D-015's typed-body submit smoke.
* [`workshop_cancel_midrun.png`](../reports/sprint11/screenshots/workshop_cancel_midrun.png)
  — the in-progress sweep card after the operator clicked **CANCEL**;
  `status: cancelled` visible within 5 s of the click.
* [`workshop_promote_toast.png`](../reports/sprint11/screenshots/workshop_promote_toast.png)
  — the success toast that fires after PROMOTE returns `202`; proves
  the live agent now reads from the operator-chosen sweep config.
* [`proposalreview_pending.png`](../reports/sprint11/screenshots/proposalreview_pending.png)
  — fresh advisor proposal landing in the ProposalReview **Pending**
  tab on the live deploy (operator started the loop + waited for the
  first proposal; not the sandbox-recorded sprint_10 frame).
* [`post_restart_state_restored.png`](../reports/sprint11/screenshots/post_restart_state_restored.png)
  — `GET /api/backtest/{run_id}` returning the same `results.json`
  after a Railway service restart; the load-bearing proof that the
  T-B-038 `/data` volume actually survives a redeploy.
* [`vercel_github_autodeploy_badge.png`](../reports/sprint11/screenshots/vercel_github_autodeploy_badge.png)
  — Vercel Settings → Git status panel: *Connected: GitHub /
  `<org>`/`code`, Production Branch: `main`, Auto-deploy: Enabled*.
  Proves the CD pipeline closure.

### Sprint 10 Money Shots

The four frames below capture the L3 review flow end-to-end on the
live deploy; the source PNGs are committed under
`dashboard/public/screenshots/` so they ship with the dashboard bundle
and any judge can hit `/screenshots/<name>` on the public Vercel URL:

* [`sprint10_proposal_pending.png`](../dashboard/public/screenshots/sprint10_proposal_pending.png)
  — a fresh `weight_delta` proposal lands in the **Pending** tab;
  the card shows kind, target weight, suggested delta, advisor
  rationale, and the optimistic SSE refresh that brought it in.
* [`sprint10_proposal_approved.png`](../dashboard/public/screenshots/sprint10_proposal_approved.png)
  — the operator clicks **Approve**; the card transitions through
  the optimistic state into a confirmed approved row.
* [`sprint10_proposal_history.png`](../dashboard/public/screenshots/sprint10_proposal_history.png)
  — the approved proposal moves to the **History** tab, joining the
  full audit trail of every advisor recommendation + operator verdict.
* [`sprint10_weight_delta_applied.png`](../dashboard/public/screenshots/sprint10_weight_delta_applied.png)
  — the next decision tick reads the new weight value; the
  dashboard's weight panel + evolution curve visibly jump to the
  post-delta value, proving the L3 → operator → L0 loop closed
  end-to-end on a live tick.

## Sprint 13 polish

Sprint_13 closes the sprint_9 placeholder-loop seam end-to-end.
T-B-041 turned `_PlaceholderLoop` into a production-loop factory
selected by `PROD_LOOP_CHAIN_ADAPTER_KIND` (default `sandbox`); the
first SSE event the dashboard tails is now `kind=loop_boot,
loop=sandbox_phase2_real`, with the `placeholder` field **absent**.
T-B-042 wired `RhChainAdapter` against the EIP-712 contract triple
(`EnergyController`, `AgentLifecycle`, `TombstoneNFT`); the env-var
contract is locked in `docs/DEPLOYMENT.md` § RH Chain adapter env
vars. T-B-043 booted both end-to-end against an anvil-deployed
contract triple — captured at `reports/sprint13/boot_smoke_log.md`,
`read_breath()` returned a finite 10000.0000 USD and the first
decision tick landed as `NO_BET` at `tick=0`. T-D-018 promoted the
seam to the live Vercel + Railway deploy (kept on the sandbox adapter
— `RH_CHAIN_*` testnet-key promotion is a separate Gate C task) and
captured the two Money Shots below.

### Sprint 13 Money Shots

The two frames below capture the sprint_13 closure end-to-end on the
live deploy; the source PNGs are committed under
`reports/sprint13/screenshots/` so the orchestrator's
diff_truthfulness gate can verify them against the smoke log's
claimed evidence:

* [`workshop_real_loop_first_tick.png`](../reports/sprint13/screenshots/workshop_real_loop_first_tick.png)
  — Consciousness Stream column on `/workshop`, first 2–5 rows
  showing `loop_boot` (`loop=sandbox_phase2_real`, no `placeholder`
  key) plus the leading real `DecisionRecord` tick; captured on the
  live Vercel + Railway deploy after `POST /api/agent/start`.
* [`workshop_breath_meter_ticking.png`](../reports/sprint13/screenshots/workshop_breath_meter_ticking.png)
  — DualEngineMeter + BREATH bar after `update_breath_from_pnl`
  fired (BREATH bar visibly below the `1.0` seed value); proves the
  chain-side BREATH balance is reaching the dashboard via the new
  `SandboxPhase2Loop` per-tick event shape.

The third PNG in `reports/sprint13/screenshots/`,
`real_loop_sse_stream.png`, is the T-B-043 anvil-side terminal capture
of the same SSE stream against the **real** `RhChainAdapter` (not the
sandbox adapter); kept as structural evidence that the EIP-712 wiring
works end-to-end, even though sprint_13 ships the live deploy on the
sandbox adapter.

## Operator runbook — sprint close (USER ONLY)

This file documents the **engineering** evidence. The four steps below
are CEO-plan-locked USER work; the orchestrator's per-task finalize
handles the per-task commits but NOT the sprint-close push.

1. **Record demo video** — 5-min capture of the dashboard, including
   the Money Shot frames in `reports/sprint8/money_shot/` overlaid on
   the live agent journey.
2. **Pick screenshots** — extract the four Money Shot frames from the
   JSON artefacts above; pair each with the dashboard render.
3. **Mint live Tombstone** — run the testnet agent (or
   `SANDBOX_FORCE_TERMINAL=1`) so the real `TombstoneNFT.mint(...)` tx
   lands; paste the hash into the `Testnet Tombstone tx hash` section
   above.
4. **Final commit + push** — move this DRAFT's contents to repo-root
   `SUBMISSION.md`; verify `git config user.email` matches the
   `balflee` submission account; single commit + push.

---

*Track B sprint-closer engineering artefact — T-B-021 (sprint_8
seed) → sprint_9 deploy notes (T-B-028) → sprint_10 L3 narrative
(T-D-013) → sprint_11 backend dogfood close-out + Vercel auto-deploy
(T-D-016) → **sprint_13 real-loop seam swap closure (T-D-018, current
sprint counter; sprint_12 was a Track-B-only PnL/label fix that did
not touch this draft)**. Generated by hand to satisfy each per-sprint
brief; the TBD tokens (Tombstone tx hash, final agent_state.json
snapshot, demo video URL) land via USER action at the project close.*
