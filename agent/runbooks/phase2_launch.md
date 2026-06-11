# Phase 2 Launch — Operator Runbook (D11 Hard Deadline)

> **Status**: Sprint_4 T-B-008.
> **Audience**: a fresh developer / operator who has the repo cloned and
> Python 3.11+ available. The runbook is reproducible cold-start in
> **under 10 minutes**.

Phase 2 is the Apprenticeship phase — TECHNICAL_PLAN §8 Day 11 ("🚨
Hard Deadline 🚨") + PRD §6.13. Until this runbook is executed
successfully, the Demo §9 1:30-2:30 PLAYBACK has no Phase 2 footage to
play and the sprint cannot be declared closed.

The smoke run NEVER hits the real network. Real chain / Polymarket /
Gemini wiring happens in the **operator boot** documented in §4 — that
step is performed by hand outside CI.

---

## 1. Prerequisites

| Item | Required? | Notes |
|------|-----------|-------|
| Python 3.11+ | yes | `pyproject.toml § requires-python >= 3.11` |
| `pip install -e .[dev]` | yes | installs `pydantic`, `google-genai`, pytest, mypy |
| `GEMINI_API_KEY` in environment | **smoke: no** / live: yes | smoke uses `FakeGeminiClient`; live operator boot reads `os.environ['GEMINI_API_KEY']`. **Do NOT export the key for the smoke** — the smoke MUST run hermetically. |
| `DEPLOYER_PRIVATE_KEY` in environment | **smoke: no** / live: yes | live `advancePhase` tx broadcast needs the deployer signer |
| `ROBINHOOD_CHAIN_RPC` / `SEPOLIA_RPC` / `POLYGON_AMOY_RPC` | **smoke: no** / live: yes | RPC endpoint for the deployed `PhaseManager` |
| `git` worktree at the repo root | yes | runbook commands assume `cwd = repo_root` |

## 2. Smoke run (sub-10-minute cold start)

The smoke run is what every reviewer must reproduce. It proves the
Phase 2 launch convergence works without touching the network.

```bash
# 1. Cold-start sanity check (≤ 30s)
python -m agent.main --help

# 2. Print the Phase 2 launch plan — dry-run mode, ZERO outbound calls
#    (chain, Polymarket, Gemini). Expected JSON contains:
#       "target_phase": "PHASE_2_APPRENTICE"
#       "network_calls_planned": 0
python -m agent.main boot --phase apprenticeship --dry-run

# 3. Run the full Phase 2 launch smoke suite (≥ 8 tests, finishes in ≤ 30s)
PYTHONPATH=. pytest -x tests/agent/integration/test_phase2_launch_smoke.py -v

# 4. mypy --strict (≤ 60s)
PYTHONPATH=. mypy --strict agent/main.py agent/runtime/ agent/dashboard_bridge/

# 5. Lookahead-bias audit (≤ 5s)
PYTHONPATH=.dev python -c \
  "from pathlib import Path; from harness.tools.lookahead_auditor import audit; \
   r = audit(cwd=Path('.'), paths=['agent']); \
   assert r.status == 'PASS', r; print('lookahead PASS')"
```

**Success criteria (every line below must hold):**

* `python -m agent.main --help` prints non-empty usage, exit 0.
* `python -m agent.main boot --phase apprenticeship --dry-run` prints JSON, exit 0, **`network_calls_planned == 0`**.
* Smoke suite reports **8 passed**.
* mypy reports **Success: no issues found in 5 source files**.
* Lookahead audit reports **`status == "PASS"`** with **0 critical findings**.

If any of those fail, **do not advance**: the boot is broken upstream;
file an escalation against T-B-008 before touching the live chain.

## 3. Live launch (operator only — runs OUTSIDE CI)

> Only after the smoke run above is green do we touch the chain.

### 3.1 Broadcast `advancePhase` (Phase 1 → Phase 2)

The chain side of the D11 transition is owned by Track A (`script/`
deployment scripts + the `PhaseManager.transitionToApprenticeship`
function). The Phase 2 launch orchestrator REFUSES to proceed if the
chain still reports `PHASE_1_INFANCY` — `test_boot_refuses_when_chain_phase_is_still_phase1`
asserts this invariant.

```bash
# Track A's deployer script (NOT part of T-B-008 — runs from Track A's
# script/ allowlist; cross-track command listed here for completeness)
forge script script/AdvancePhase.s.sol \
  --rpc-url $ROBINHOOD_CHAIN_RPC \
  --private-key $DEPLOYER_PRIVATE_KEY \
  --broadcast

# Record the tx hash + the new on-chain phase value (eth_call):
cast call $PHASE_MANAGER_ADDR "currentPhase()(uint8)" --rpc-url $ROBINHOOD_CHAIN_RPC
# expected: 1 (Apprenticeship; Childhood == 0)
```

Capture both numbers — they go into `phase2_launch_log.md` (§5).

### 3.2 Boot the agent against the real chain

The CLI surface refuses to run a non-dry-run `boot` because that would
risk an accidental live call from a test invocation. Operators wire the
real adapters via a small Python entrypoint:

```python
# ops/boot_phase2_live.py  — NOT checked in to track_b allowlist;
# operator keeps this script local.
import os
from pathlib import Path

from agent.core.memory_bank import MemoryBank
from agent.llm.gemini_client import GeminiClient
from agent.runtime import Phase2LaunchOrchestrator

# --- wire your real adapters here ---
phase_reader = YourPhaseManagerReader(rpc_url=os.environ["ROBINHOOD_CHAIN_RPC"])
decision_log = YourDecisionLogWriter(rpc_url=os.environ["ROBINHOOD_CHAIN_RPC"])
signal_source = YourEngineFanout(asof_ts_provider=...)
# Gemini is constructed once at boot; lazy auth read on first call.
gemini = GeminiClient()
# Engines that consume Gemini are injected via signal_source's
# constructor; the orchestrator never instantiates Gemini directly.

bank = MemoryBank(root=Path(".agent_state/memory_bank"))
bank.ensure_layout()

orch = Phase2LaunchOrchestrator(
    memory_bank=bank,
    phase_reader=phase_reader,
    decision_log=decision_log,
    engine_signals=signal_source,
)

result = orch.boot()
print(result)
```

### 3.3 Open the dashboard

```bash
cd dashboard && pnpm run dev
# Open http://localhost:3000
```

Look for:

* **right-rail vitals strip ticks within 60s** (the boot emits two
  `vitals` frames so the first render is byte-stable).
* **`LLMActivationOverlay` renders exactly once** — the dashboard's
  `sessionStorage` handshake key prevents replay.
* **`PhaseTransitionBanner` shows "Childhood → Apprenticeship"** with the
  copy `β₁ unfreezes; passive metabolism resumes at half rate`.
* **first NO_BET decision in the recent-decisions feed** with the fake
  tx hash from §3.1 (live mode echoes the real broadcast hash instead).

If any of the above fails, terminate the agent process + roll back via
the Track A redeploy runbook; do NOT leave a Phase 2 agent live without
a working dashboard.

## 4. Phase 2 success — what the operator records

After §3 succeeds, append a timestamped entry to
`agent/runbooks/phase2_launch_log.md` (see the file's own header for
the entry template). Required fields:

* operator's name + UTC timestamp
* `advancePhase` tx hash + observed `currentPhase()` value (must be 1)
* dashboard URL
* vitals at t=0 (breath, bankroll, gas_per_min)
* whether the `llm_activated` overlay fired

## 5. Known limitations

See `phase2_known_issues.md` (sibling file). Highlights:

* **Polymarket real-money path is deferred to Phase 3 sprint** — `agent/data/polymarket_executor.py` doesn't exist yet (TECHNICAL_PLAN §15 Gap 1). The smoke uses `FakeDecisionLog`; live mode broadcasts via your `DecisionLog.append` wrapper but does NOT yet route to Polymarket.
* **Reflection is templated, not LLM-generated, on the launch boot tick.** Live reflections from `agent.engines.reflection.ReflectionEngine` fire on subsequent ticks once the persistent loop lands in sprint_5.
* **The `anthropic` / `openai` SDK imports are banned anywhere under `agent/**`** — see `.claude/agents/track-b-backend.md` Rule 7. The AST scan in `tests/agent/llm/test_no_forbidden_imports.py` enforces it.
