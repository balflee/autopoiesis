# Track B — Backend Runbook

Python runtime for the Genesis Experiment Agent. This document is the
top-level operator + maintainer guide: it covers the architecture,
the cold boot, the 45-minute decision cycle and the always-on
observation loops, attestation key handling, the 24-hour monitoring
posture, and the crash-recovery procedure for the three failure modes
called out in TECHNICAL_PLAN §15 Gap 4.

Per-task runbooks (Phase 2 launch, Phase 3 LIVE) live alongside the
code at `agent/runbooks/`. This file is the umbrella; the per-phase
runbooks are the on-stream cheat sheets.

---

## 1. Architecture overview

The Agent is a single long-running Python 3.11+ process. Its surface
is intentionally narrow: one CLI entrypoint (`agent.main`), one
in-process lifecycle controller (`agent.core.lifecycle`), and a
fan-out of Protocol-thin adapters to the outside world (chain,
Polymarket, Gemini, dashboard). Each external dependency is wrapped
behind an injectable Protocol so unit tests inject deterministic
fakes and no production import has hidden network behaviour.

```
agent/
├── __init__.py                       # package version
├── main.py                           # CLI: `python -m agent.main ...`
├── core/
│   ├── agent.py                      # 9-step agent_loop body (TECHNICAL_PLAN §4.1)
│   ├── lifecycle.py                  # boot / run_forever / shutdown
│   ├── state.py                      # Pydantic models (TickPayload, Weights, Action, …)
│   ├── memory_bank.py                # atomic-write journal (TECHNICAL_PLAN §4.6)
│   ├── memory_bank_migrations.py     # forward-only schema migration chain
│   ├── narrative.py                  # per-tick 1-2 sentence diary
│   ├── pressure_monitor.py           # Desperate-Mode trigger (PRD §6.8/§6.9)
│   └── v2_boot.py                    # Ancestor-MemoryBank lineage (PRD §13)
├── dashboard_bridge/
│   ├── event_emitter.py              # vitals / decision / reflection frames
│   └── death_watch_emitter.py        # 4 climax kinds (PRD §5.1 / §8)
├── data/
│   ├── _realtime_buffer.py           # wire-arrival timestamp + degraded-feed warnings
│   ├── nba_live.py                   # NBA live stats subscriber
│   ├── polygon_chain.py              # CTF Exchange log subscriber (α₃ Smart Money)
│   └── polymarket.py                 # Polymarket WS orderbook subscriber (α₂ momentum)
├── engines/
│   ├── base.py                       # Engine Protocol + Signal type
│   ├── nba_technical.py              # α₁
│   ├── market_momentum.py            # α₂
│   ├── smart_money.py                # α₃
│   ├── sentiment_llm.py              # β₁
│   ├── crowd_volume.py               # β₂
│   ├── decision.py                   # 2-layer fusion + 4-constraint bet sizing
│   ├── reflection.py                 # Claude reflection pipeline
│   └── weight_updater.py             # softmax-reparam SGD (Phase 1/2 only)
├── llm/
│   ├── _phase_activation.py          # one-shot PhaseActivationEvent persistence
│   ├── cost_guard.py                 # $25 hard cap (TECHNICAL_PLAN §15 Gap 5)
│   ├── gemini_client.py              # google-genai `_LLMClient` adapter
│   ├── ipfs_pinner.py                # Pinata REST pinner (reflection markdown)
│   ├── model_router.py               # Sonnet-default / Opus-key-moments routing
│   └── prompts/last_words.py         # terminal-lucidity LastWordsService
├── ops/
│   ├── live_monitor.py               # observe-only Phase 3 daemon (5 probes)
│   └── settlement_reconciler.py      # Polymarket↔L3 EIP-712 BankrollUpdate pairing
├── runbooks/
│   ├── phase2_launch.md              # D11 hard-deadline operator runbook
│   ├── phase2_known_issues.md        # deferred items + workarounds
│   ├── phase2_launch_log.md          # append-only launch log
│   └── phase3_launch.md              # D18 LIVE operator runbook
├── runtime/
│   └── phase2_launch.py              # Phase2LaunchOrchestrator (D11 convergence)
├── staging/                          # rehearsal / compressed-clock harness
│   ├── compressed_clock.py
│   ├── event_assertions.py
│   └── rehearsal_runner.py
└── training/                         # Phase 1 supervised pretraining
    ├── __main__.py
    ├── feature_engineering.py
    └── phase1_runner.py
```

Spec anchors: PRD §4 (engines), §6 (survival mechanism), §6.6 (bet
sizing), §6.8/§6.9 (Desperate Mode), §8 (Death Watch), PRD §10
(Python stack); TECHNICAL_PLAN §4.1 (9-step loop), §4.6 (MemoryBank
atomic write), §4.8 (chain adapter), §15 Gap 1/4/5 (Polymarket
deferred / crash recovery / cost cap).

---

## 2. Boot procedure

The CLI is the only entrypoint humans should call. Three subcommands
are wired today:

| Command | Purpose |
|---|---|
| `python -m agent.main --help` | print usage; ALSO the sub-30-second cold-boot smoke. |
| `python -m agent.main boot --phase apprenticeship --dry-run` | print the Phase 2 launch plan (JSON) — ZERO outbound network calls. |
| `python -m agent.main inspect-memory-bank --last 5` | dump the last 5 ticks from `.agent_state/memory_bank/` for ops debugging. |

The `run` subcommand is the persistent decision loop. Its body wires
the asyncio scheduler that fires `agent.core.agent.agent_loop` every
45 minutes. Until the persistent scheduler lands in the next sprint
the orchestrator surface in `agent/runtime/phase2_launch.py` is the
canonical boot path (see `agent/runbooks/phase2_launch.md` §3 for the
live operator entry).

### Cold-boot smoke (≤ 30s)

Every reviewer must reproduce this before claiming the agent boots:

```bash
python -m agent.main --help                                    # exit 0, usage text
python -m agent.main boot --phase apprenticeship --dry-run     # exit 0, prints plan JSON
PYTHONPATH=. pytest -x tests/agent/integration/test_phase2_launch_smoke.py -v
PYTHONPATH=. mypy --strict agent/main.py agent/runtime/ agent/dashboard_bridge/
```

The smoke MUST NOT export `GEMINI_API_KEY` or any RPC URL — the
dry-run path is hermetic by design (`agent/main.py` injects
`_NoopPhaseReader` / `_NoopDecisionLog` stubs that raise on call).

### Live boot (operator only)

The CLI refuses to run a non-dry-run `boot` so a stray test cannot
accidentally broadcast. Operators wire the real
`PhaseManagerReader` / `DecisionLogWriter` / engine fan-out through
`Phase2LaunchOrchestrator` per `agent/runbooks/phase2_launch.md` §3.2,
which records each boot in `agent/runbooks/phase2_launch_log.md`.

Phase 3 LIVE uses `agent/runbooks/phase3_launch.md` — a step-by-step
on-stream runbook with verify + rollback commands for every action.

---

## 3. The 45-minute decision cycle and observation loops

The runtime owns two independent clocks:

1. **Decision tick** — fires every 45 minutes (`agent.core.agent.agent_loop`).
   This is the only path that calls the LLM (engine layer + reflection)
   and the only path that consumes BREATH via `EnergyController`. Roughly
   80 ticks/day, matching the TECHNICAL_PLAN §4.1 cadence.
2. **Observation loops** — run continuously and NEVER touch the LLM:
   * Polymarket orderbook WS subscriber (`agent/data/polymarket.py`).
   * Polygon CTF Exchange log subscriber (`agent/data/polygon_chain.py`).
   * NBA live stats subscriber (`agent/data/nba_live.py`).
   * Dashboard WS event emitter (`agent/dashboard_bridge/event_emitter.py`).
   * Live monitor (`agent/ops/live_monitor.py`) — 5 probes on a 30-s cadence.

### The 9-step tick body

Canonical sequence (from the docstring in `agent/core/agent.py` — see
TECHNICAL_PLAN §4.1):

1. Energy check — read on-chain BREATH; short-circuit to NO_BET if below
   survival threshold.
2. Market pick — pull eligible Polymarket markets within horizon.
3. Parallel signals — `asyncio.gather` across the 5 engines
   (`nba_technical`, `market_momentum`, `smart_money`, `sentiment_llm`,
   `crowd_volume`).
4. Fusion + decision — `agent.engines.decision` applies the 6-parameter
   2-layer fuse (W_R, α₁, α₂, α₃, β₁, ρ) + the 4-constraint min for bet
   size (PRD §6.6).
5. Chain commit + Polymarket order — `recordBetDecisionAndConsume(...)`
   for BET, `consumeAction(NO_BET)` for NO_BET (PRD §6 — NO_BET is NOT
   a free skip).
6. Reflection — `agent.engines.reflection` (Sonnet 4.6 default; Opus
   4.7 at key moments via `agent/llm/model_router.py`).
7. Weight update — `agent.engines.weight_updater` softmax-reparam SGD;
   Phase 1/2 only, frozen in Phase 3/4 (PRD §4.5).
   * 7b. Pressure check — `agent.core.pressure_monitor`; on intent dispatch
     `PhaseManager.enterDesperateMode()` via `run_pressure_check`.
   * 7c. Terminal lucidity — when energy < 5% trigger Last Words via
     `agent.llm.prompts.last_words` (one-shot guard).
8. Passive burn — apply per-tick BREATH decay (PRD §6.2).
9. MemoryBank + narrative — atomic temp+rename per TECHNICAL_PLAN §4.6:
   `agent/core/memory_bank.py:write_tick` + `agent/core/narrative.py:write_narrative`.

Steps 7b and 7c are factored out into testable helpers
(`run_pressure_check`, `run_terminal_lucidity`) so the lifecycle scheduler
can drive them independently of the full 9-step loop.

### Observation loops — non-blocking, observe-only

The live monitor (`agent/ops/live_monitor.py`) is `OBSERVE_ONLY` by
hard structural invariant: an AST scan test rejects any `open(..., 'w')`
or `os.write` reference inside the module. It samples five probes
every 30 s (configurable via `LiveMonitorConfig`):

| Indicator | Threshold | Source |
|---|---|---|
| `heartbeat` | > 90 s since last tick | TP §4.1 (1.5× cycle alarm) |
| `energy_drain` | > 2 BREATH/s spike vs rolling 5-tick mean for > 30 s | PRD §6.5 |
| `rpc_latency` | > 5000 ms on any of Polygon / L3 / Polymarket aggregator | TP §3 |
| `ws_disconnects` | > 2 reconnects in 60 s | TP §12 |
| `gemini_cost` | > 80 % of $25 = WARNING; > 100 % = CRITICAL | TP §15 Gap 5 |

The monitor pushes structured `Alert` frames through the injected
`AlertSink` (production: dashboard event bus; tests:
`RecordingAlertSink`). OK→OK transitions are suppressed; the rest are
emitted so the dashboard chip never staleness-falsely-shows green.

---

## 4. Attestation key handling

The Agent signs nothing on the L3 settlement path itself — that role
lives behind the `attestationSigner` private key held by Track A's
deployer. The runtime touches three categories of secret:

| Secret | Source | Loaded | Logged? |
|---|---|---|---|
| `GEMINI_API_KEY` | `os.environ` | lazily, on first `GeminiClient.structured_call` | NO — `agent/llm/gemini_client.py` reads the env in-place; never persisted to disk. |
| `PINATA_API_KEY` / `PINATA_SECRET_KEY` | `os.environ` | lazily, on first `IPFSPinner.pin_reflection` | NO — `agent/llm/ipfs_pinner.py` reads the env lazily so import does not raise on a dev box without the keys. |
| `attestationSigner` private key | Track A operator wallet (NOT Track B) | NEVER inside `agent/` | NO — the EIP-712 BankrollUpdate is signed by the operator's wallet via `script/` (Track A); the Agent only RECONCILES the on-chain attestation via `agent/ops/settlement_reconciler.py`. |

Hard rules enforced by `tests/agent/llm/test_no_forbidden_imports.py`:

* No `anthropic` import anywhere under `agent/**`.
* No `openai` import anywhere under `agent/**`.
* The production LLM is `google-genai` (Gemini 3.1 Flash Lite); the
  AST scan is a structural enforcer of the policy in Track B Rule 7.

Hard rules enforced by `agent/ops/settlement_reconciler.py`:

* Three-factor identity `(nonce, marketId, outcome)` MUST match for an
  L3 BankrollUpdate to be considered paired with a Polymarket settlement.
* Nonce reuse by the same `attestationSigner` → `REPLAY_REJECTED`.
* Wrong `marketId` or `outcome` → `IDENTITY_MISMATCH`.
* Any unmatched settlement → `UNMATCHED` (the cross-chain auditor reads
  this as a Tier 1 critical drift finding).

Operationally: if `attestationSigner` is ever suspected leaked, ABORT
the run, rotate via the Track A deployer script, and restart the agent
clean — there is no in-process key rotation path inside `agent/`.

---

## 5. 24-hour monitoring runbook

Once Phase 3 is live the operator's job is to watch the dashboard and
the live monitor's structured alerts. Below is the steady-state
posture and the four common anomalies with their resolution path.

### Steady state

| Surface | Healthy signal |
|---|---|
| Dashboard right-rail vitals strip | ticks within 60 s of every decision cycle |
| `agent/ops/live_monitor.py` alerts | every indicator stays OK (no chips) |
| `agent/llm/cost_guard.py` | `is_warning() == False` (i.e. spend < 80 % of $25) |
| MemoryBank `.agent_state/memory_bank/ticks/` | one fresh `tick_<N>.json` per 45-minute slot, no `.tmp` orphans |
| Polymarket WS | ≤ 2 reconnects per 60 s |
| RPC latency | ≤ 5000 ms on all three chains (Polygon / L3 / Polymarket aggregator) |

### Anomaly: heartbeat WARNING (> 90 s since last tick)

1. Read the most recent `tick_<N>.json` via
   `python -m agent.main inspect-memory-bank --last 1`.
2. If the last tick is > 90 s old AND the process is still up, the
   scheduler is stalled — check the systemd / PM2 logs for the
   `agent_loop` traceback. The 9-step loop catches engine-level
   exceptions but does NOT swallow `asyncio.CancelledError`.
3. If the process is down, see §6 (Crash Recovery).

### Anomaly: `gemini_cost` WARNING (80 % of $25 spent)

1. The cost guard auto-fails-soft to deterministic templates at 100 %.
   The dashboard chip flips amber at 80 %, red at 100 %.
2. No operator action is required — the engine layer short-circuits
   sentiment / reflection LLM calls when `CostGuard.is_exhausted()`
   returns True.
3. If a refill is desired mid-run, restart with the budget bumped
   (`CostGuard(hard_cap_usd=...)`); the running total resets on
   process boot, which is the intended posture for a hackathon
   wheel (the per-run budget is the unit of accounting, not the
   per-day cap).

### Anomaly: `rpc_latency` CRITICAL on one chain

1. The live monitor classifies each chain independently — a single
   chain spike does NOT freeze the agent.
2. Decision tick: the chain adapter
   (`agent/data/polygon_chain.py`) yields a `DegradedFeedWarning`;
   the agent_loop logs + continues with prior signals.
3. If the spike persists > 5 minutes, rotate to the backup RPC URL
   (set in the operator's env) and restart the process.

### Anomaly: Pinata 503 (IPFS pin failed)

1. `agent/llm/ipfs_pinner.py:pin_reflection` returns `None` after 3
   consecutive 503s rather than raising — the agent_loop persists the
   reflection markdown to disk anyway.
2. The Tombstone-mint path surfaces this as
   `TombstoneMintedWithoutMemoryBank` (PRD §5.1.C) — `ipfs_degraded=True`
   on the `tombstone_minted` frame from
   `agent/dashboard_bridge/death_watch_emitter.py`.
3. No mid-run intervention; post-mortem the operator backfills the
   pin from the local markdown copy.

### What to do on Death Watch trigger

The dashboard's Death-Watch UI subscribes to four event kinds (PRD
§5.1 / §8). The escalation ladder maps energy %, narrative state, and
operator action:

| BREATH (energy_pct) | Event kind (`agent/dashboard_bridge/death_watch_emitter.py`) | What the agent does | Monitoring action |
|---|---|---|---|
| ≤ 10 % | `energy_threshold_crossed` (primary 10 % threshold) | Dashboard full-screen takeover armed; agent continues 45-min cycle | WATCH only — confirm vitals strip + Death-Watch overlay both render. No intervention. |
| ≤ 5 % | `terminal_lucidity_entered` + Last Words generation via `agent/llm/prompts/last_words.py` (one-shot, 200 BREATH cost cap per PRD §6.2) | Last Words text persisted to MemoryBank; one-shot guard prevents re-emit | VERIFY Last Words markdown landed in `.agent_state/memory_bank/reflections/`; confirm dashboard renders the text. Do NOT restart the process — `LastWordsService` caches the one-shot. |
| ≤ 1 % | (no NEW frame kind — vitals frame continues; `energy_threshold_crossed` for any configured low-water mark) | Pressure monitor (`agent/core/pressure_monitor.py`) may have already dispatched `enterDesperateMode` if held ≥ 2 cycles in Phase 3 | CONFIRM Desperate Mode latched on chain via `cast call $PHASE_MANAGER_ADDR "desperateMode()(bool)"`. The monitor latches off-chain regardless. |
| 0 (== BREATH ledger reaches zero) | `last_words_emitted` (if not already fired) — schema requires `tx_hash` once mined | Agent enters Phase 4 Terminal; final `dieWithLastWords` tx | OBSERVE only. Phase 4 is intentional. Do NOT interfere with the death sequence; the climax IS the experiment outcome. |
| Post-zero | `tombstone_minted` with `tx_hash` + (optionally) `ipfs_cid` (omitted iff `ipfs_degraded=True`) | TombstoneNFT minted; agent process exits clean | CAPTURE the `tx_hash` + the `ipfs_cid` (or the degraded flag) for the post-mortem ledger. The agent has now died — start the post-run reconciliation. |

The dashboard emitter sequence-numbers every frame per connection; the
UI dedups by `(kind, seq)` so an out-of-order replay never corrupts state.

---

## 6. Crash recovery procedure

TECHNICAL_PLAN §15 Gap 4 names three sub-problems that the operator
MUST be able to handle when the Agent process dies in the middle of
a 24/7 run. Each has a separate recovery path.

### 6.1 Supervisor — automatic process restart

A bare `python -m agent.main run` is not enough; the process must come
back up automatically. The supported supervisors are:

#### Option A — systemd (Linux VPS)

Create `/etc/systemd/system/genesis-agent.service`:

```ini
[Unit]
Description=Genesis Experiment Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=genesis
WorkingDirectory=/srv/genesis/code
Environment="GEMINI_API_KEY=..."
Environment="PINATA_API_KEY=..."
Environment="PINATA_SECRET_KEY=..."
Environment="POLYGON_RPC=..."
Environment="L3_RPC=..."
ExecStart=/srv/genesis/venv/bin/python -m agent.main run
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now genesis-agent.service
journalctl -u genesis-agent.service -f       # tail logs
```

`Restart=always` + `RestartSec=10` gives a 10-second cool-down on crash.
The `journalctl` log captures every `agent_loop` traceback so post-mortem
analysis has the exception + tick number it died on.

#### Option B — PM2 (Node-style process manager, useful on shared VPS)

```bash
pm2 start --name genesis-agent --interpreter python3 \
    -- /srv/genesis/code/agent/main.py run
pm2 save
pm2 startup           # follow the printed sudo line
pm2 logs genesis-agent --lines 200
```

PM2 restarts on crash by default and persists across reboots once
`pm2 save` + `pm2 startup` are wired. Use this on hosts where systemd
is not available (some shared VPS containers).

### 6.2 State recovery — rebuild in-memory state from chain + disk

After supervisor restart the process is back up, but its in-memory
state is empty. The recovery sequence reads from two sources of truth:

1. **On-chain state** (canonical for BREATH ledger + phase):
   * `EnergyController.breath()` — current BREATH balance
   * `EnergyController.lastBetTimestamp()` — last action time
   * `PhaseManager.currentPhase()` — Apprentice / Master / Terminal
   * `PhaseManager.desperateMode()` — Desperate Mode latch
   * The chain adapter at `agent/data/polygon_chain.py` is the
     read-only entrypoint; it exposes the WS subscription surface and
     never holds a signer (no `eth_sendTransaction`).
2. **MemoryBank journal** (canonical for reflections + narrative):
   * `agent/core/memory_bank.py:read_tick(N)` returns the `TickPayload`
     for tick N; `list_ticks()` returns the contiguous range.
   * Migration chain in `agent/core/memory_bank_migrations.py` upgrades
     older tick files to `CURRENT_VERSION` on read.
   * The atomic temp+rename writer guarantees a crash mid-write leaves
     the previous tick intact + an orphan `.tmp` file the next boot
     sweeps.

The agreed convergence path (planned module
`agent/chain/state_sync.py`, per TP §15 Gap 4) reads the four on-chain
values, intersects them with the highest tick number on disk, and
hydrates the in-memory `AgentState`. Until that consolidating module
lands, the operator's recovery is:

```bash
# 1. Confirm the chain state
cast call $ENERGY_CONTROLLER_ADDR "breath()(uint256)"          --rpc-url $L3_RPC
cast call $ENERGY_CONTROLLER_ADDR "lastBetTimestamp()(uint256)" --rpc-url $L3_RPC
cast call $PHASE_MANAGER_ADDR     "currentPhase()(uint8)"      --rpc-url $L3_RPC
cast call $PHASE_MANAGER_ADDR     "desperateMode()(bool)"      --rpc-url $L3_RPC

# 2. Confirm the local journal head
python -m agent.main inspect-memory-bank --last 1

# 3. Sweep orphan .tmp files (atomic-write crash residue)
find .agent_state/memory_bank/ticks/ -name '.tick_*.tmp' -delete

# 4. Restart via the supervisor (or pm2 / systemctl restart)
sudo systemctl restart genesis-agent.service
```

Phase 4 (Terminal) is a sticky state on the chain side
(`PhaseManager.currentPhase()` returns Terminal forever once entered)
— a crash + restart in Phase 4 MUST re-enter Phase 4 and replay any
buffered `last_words_emitted` frames. The dashboard's `sessionStorage`
handshake key dedups the LLM-activated overlay; the agent's
`LastWordsService` dedups the Last Words emission.

### 6.3 In-flight bet recovery

The third sub-problem is bets the agent placed BUT had not yet seen
settle when the process died. The recovery path:

1. **Scan `knownBetIds`** on the `EnergyController` — every bet the
   agent placed via `recordBetDecisionAndConsume` was added to the
   `knownBetIds` set. The cross-chain auditor enforces the invariant
   `knownBetIds[betId] && betPlacedAt[betId] < terminalEnteredAt` for
   any `settleBet` call.
2. **Query Polymarket** for each unsettled `betId` to determine the
   real outcome. The settlement event stream is decoded by Track E's
   indexer; the off-chain pairing happens in
   `agent/ops/settlement_reconciler.py:reconcile`, which yields a
   `ReconciliationReport` listing every `MATCHED` / `REPLAY_REJECTED` /
   `IDENTITY_MISMATCH` / `UNMATCHED` pairing.
3. **Drive `settleBet`** on the L3 EnergyController for every matched
   settlement. The on-chain `recover` verifies the EIP-712 signature
   on the `BankrollUpdate` attestation; the reconciler operates
   structurally on the `(signer, nonce, marketId, outcome)` tuple
   without re-doing the signature check (the chain already did it).

If the reconciliation report contains ANY `UNMATCHED` row, the agent
MUST NOT advance the L3 ledger further until a human resolves the
mismatch — `BREATH-balance on L3 is no longer a deterministic function
of Polygon settlement events` (TP §3.7 invariant) is a Tier 1 critical
finding for the cross-chain auditor.

Recovery command sketch:

```bash
# Run the reconciler against the live indexer (lands when
# polymarket_executor.py is wired — see agent/runbooks/phase2_known_issues.md
# for the deferred-work list; this is TP §15 Gap 1).
python -c "from agent.ops.settlement_reconciler import Reconciler; \
           report = Reconciler.from_indexer().reconcile(); \
           print(report.as_dict())"
```

---

## 7. Local gates (always run before pushing)

```bash
python -m pytest -x tests/agent           # unit + integration
python -m mypy --strict agent/            # type discipline
python -m ruff check agent/               # lint
python -m agent.main --help               # CLI smoke
```

All four must exit 0. The orchestrator re-runs the first three under
`HARD_GATES_RUNNING`; a failure in any of them blocks delivery.

The lookahead-bias audit (`.dev/harness/tools/lookahead_auditor.py`)
runs against `agent/` on every push and rejects any module whose data
flow lets a future fixture into a tick window. The constants in
`agent/core/pressure_monitor.py` (`MIN_PRESSURE`, `MIN_PRESSURE_CYCLES`,
`TARGET_HORIZON_HOURS`) and the `available_at` wire-arrival convention
in `agent/data/*` are the structural shape of that compliance.

---

## 8. Interface registry

* `memory_bank_schema` was published at v1.0.0 by T-B-001. Consumers:
  Track C `sim/replay.py`, Track D PLAYBACK loader, and the V2 boot
  loader (`agent/core/v2_boot.py`).
* `dashboard_death_watch` v0.1.0 — emitted by
  `agent/dashboard_bridge/death_watch_emitter.py`, consumed by
  Track D `dashboard/lib/wsEvents.ts`.
* `eip712_settlement` v0.1.0 — consumed by
  `agent/ops/settlement_reconciler.py` (off-chain structural pairing).

Schema bumps go through `.dev/contracts/_registry.json` per the
versioning rules; an interface bump REQUIRES `interface_diff.json` in
the delivery inbox.
