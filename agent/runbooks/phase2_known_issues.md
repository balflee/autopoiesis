# Phase 2 Known Issues / Deferred Items

> Per the T-B-008 brief: enumerate every limitation the Phase 2 launch
> ships with so reviewers do NOT confuse "not yet implemented" with
> "regression". Each entry below cites the canonical spec section + the
> sprint that lands the fix.

---

## TECHNICAL_PLAN §15 Gap 1 — `polymarket_executor.py` real-money path

The brief states explicitly: **the Polymarket real-money executor is
deferred to Phase 3 sprint (sprint_5)**.

* **Status today**: `agent/data/polymarket_executor.py` does NOT exist.
  The Phase 2 launch smoke routes decisions through an injected
  `_DecisionLogWriter` Protocol. Live operators wire a `web3.py` adapter
  to `DecisionLog.append`; that records the agent's *intent* on-chain
  but does NOT broadcast a Polymarket order.
* **Why deferred**: the EIP-712 settlement attestation surface
  (`.dev/contracts/eip712_settlement.v0.1.0.json`) needs the
  `Polymarket → Settlement → BREATH` reconciliation chain before money
  flows, and that's still being calibrated. Shipping the executor on
  D11 would have broken the brief's "no live USDC during Phase 2 launch
  smoke" rule.
* **Lands when**: sprint_5 — `T-B-009` or equivalent will own the
  executor. Track A's settlement attestation receiver is already
  shipped per `eip712_settlement.v0.1.0.json`.
* **Operator note**: a Phase 2 launch operator MUST NOT attempt to
  point the agent at the real Polymarket API. The DecisionLog records
  *what the agent would do* — that's the audit trail the calibration
  team uses to back-test the executor before it ever broadcasts a real
  USDC order.

## Hard ban on `anthropic` / `openai` imports under `agent/**`

Per `.claude/agents/track-b-backend.md` **Rule 7** (authoritative):

> Production LLM = Gemini 3.1 Flash Lite via `google-genai` SDK + AI
> Studio (env var `GEMINI_API_KEY`). [...] **NEVER** import `anthropic`
> or `openai` in production agent code — that's a hard policy violation.

* **Enforcement**: AST scan in
  `tests/agent/llm/test_no_forbidden_imports.py`. The test walks every
  `*.py` under `agent/` and fails if either top-level package is
  imported (including dotted submodules — see
  `test_scan_handles_dotted_imports`). Production code MUST go through
  `agent/llm/gemini_client.py`.
* **Scope of ban**: production source under `agent/**` only.
  `tests/agent/**` is exempt (a future negative test that asserts
  "importing anthropic raises X" would need the forbidden module).
  Today no test under `tests/agent/` imports either banned module.
* **Status today**: PASS — `python -m pytest tests/agent/llm/test_no_forbidden_imports.py -v`
  reports 4 passed.

## Reflection on the launch tick is templated, not Gemini-driven

The Phase 2 launch smoke must not call Gemini (the brief's "verified
by zero-outbound-call test"). The launch boot uses
`agent.runtime.phase2_launch._template_reflection`, which deterministically
fills a 2-sentence string off the chosen `Action`.

* **Why**: a live Gemini call on the launch tick would:
  1. require `GEMINI_API_KEY` at the moment the operator runs the
     advancePhase tx — operationally fragile, and
  2. burn budget against `CostGuard` for a tick where the reflection
     payload is purely cosmetic.
* **Lands when**: the persistent decision loop in sprint_5 fires
  `ReflectionEngine.reflect()` every tick. Reflections from
  tick 1 onwards are live Gemini outputs.

## Demo §9 1:30-2:30 PLAYBACK fixture is curated, not live-captured

The shipped `data/fixtures/phase2_demo_tape.json` is generated via
`Phase2LaunchOrchestrator.capture_demo_tape` — its 5-tick "first
Twitter mistake" arc is canned copy, not a real Phase 2 agent run.

* **Why**: PRD §12 mandates a "保险动作" (insurance action) — a
  pre-recorded backup of the Phase 2 highlights to play during Demo if
  the live agent is unavailable. A canned fixture IS the pre-recorded
  backup.
* **Lands when**: Phase 2 ships in production (post-Demo). The
  `phase2_launch_log.md` entry will then carry a real captured tape
  alongside the canned one; Track D's playback loader can switch
  between them.

## Persistent agent loop NOT wired by T-B-008

The brief explicitly scopes T-B-008 to "integration + runbook + Demo
asset capture only". The persistent 60-minute-cadence asyncio scheduler
that fires `agent.core.agent.agent_loop` every tick lands in sprint_5
under a separate task.

* **Status today**: `agent/main.py:cmd_run` still raises
  `NotImplementedError("sprint_2 — see ... TECHNICAL_PLAN §4.1")`. The
  message keeps the existing `tests/agent/test_run_cli.test_run_stub_raises_not_implemented`
  contract green by referencing "sprint_2" verbatim.
* **Phase 2 launch operator workaround**: drive a one-shot boot via the
  Python entry in `phase2_launch.md` §3.2, then keep the process alive
  with an external supervisor (systemd, tmux). The persistent loop
  arrives in sprint_5.

## State change between Track B and Track D wire schemas

The `WS_CONTRACT_VERSION` string in
`agent/dashboard_bridge/event_emitter.py` MUST match the Track D
TypeScript constant in `dashboard/lib/wsContract.ts`. Both are pinned
to `"0.4.0"` today.

* **Risk**: a future MINOR bump on Track D's side (e.g. adding a 13th
  WS kind) would land without bumping Track B's producer. The
  interface_matrix hard gate catches it.
* **Mitigation today**: the `dashboard_ws_message.v0.4.0.json` schema
  is the single source of truth; Pydantic models in
  `event_emitter.py` mirror it field-for-field. A future schema bump
  bumps both sides simultaneously via the existing T-B-* / T-D-*
  interface-bump dance.
