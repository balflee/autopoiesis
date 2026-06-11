# TODOS

Project-wide deferred work captured here. Convention established 2026-05-18 (first entry from `/plan-design-review` for proposal-orbit-agent-memory-bank v3.1).

Format per gstack: each entry has What / Why / Pros / Cons / Context / Depends-on.

---

## [DEPLOY 2026-05-25 #14] Polygon Amoy 3/5 partial deploy — finish the broadcast

**What**: Sprint 6 (2026-05-25) deployed 5 contracts to Robinhood Chain testnet (5/5) + Arbitrum Sepolia (5/5) but Polygon Amoy only landed 2/5 (EnergyController + PhaseManager) before wallet ran out of POL during a 78-gwei gas-price spike. The 3 remaining (AgentLifecycle + DecisionLog + TombstoneNFT) need redeploy.

**Why**: PRD §10 mandates 3-chain parallel deploy for triple-category submission (Best Agentic + RH Chain reserved + Polygon ecosystem). Polygon Amoy is the Polygon-ecosystem proof point; without the full deploy SUBMISSION manifest carries `placeholder: true` for Amoy.

**Pros**: Closes the 3rd of 3 promised chain deployments. Strengthens submission narrative. 1-2 hours human / 30 min CC if gas price has normalized.

**Cons**: Costs ~0.5+ POL gas (free testnet faucet). Re-broadcast overwrites the 2 already-landed contracts with new addresses (foundry script is atomic) — wastes prior 0.057 POL spent.

**Context**:
- Source: sprint 6 deploy attempts logged in `broadcast/DeployCalibrated.s.sol/80002/run-latest.json`
- Current Amoy state: `script/deployments/sprint_3/polygon_amoy.json` zeros out the 3 undeployed contracts with `_partial_deploy_note`
- Faucet: https://www.alchemy.com/faucets/polygon-amoy (0.5 POL/request)
- Wallet `0xda8b274ed08b1E1ca84f253cC016f32536457763` (private key in `.env`, gitignored)

**Depends-on**: Nothing — gas price spike must subside (28-30 gwei normal vs 78 gwei spike). Can be done any time.

**Effort**: 1-2 hr human / 30 min CC.

---

## [DATA 2026-05-25 #15] tennis-data.co.uk cross-validate vs Sackmann

**What**: PRD v0.4 §12 risk table lists `tennis-data.co.uk` as backup for Sackmann ATP/WTA CSV. Sprint 7 will pin a Sackmann snapshot to repo but only Sackmann is read at training time. Add a cross-validate step: for each match in training set, fetch tennis-data.co.uk's record and assert key fields match (winner, score, surface, tournament_level). Surface divergences as a report row.

**Why**: Sackmann is single point of truth right now. A silent data bug in Sackmann (e.g. wrong winner attributed, surface label flipped) would propagate into the trained weights and never be caught. Cross-validate against a second academic source catches the class of "data is wrong, model learns the wrong thing" bugs.

**Pros**:
- Defense-in-depth on training data quality
- Discoverable bugs (sometimes Sackmann updates retroactively; tennis-data.co.uk's history is more frozen)
- Reusable for any future training run

**Cons**:
- 3 hr CC: pull tennis-data.co.uk XLSX per year, normalize schemas, write join + assert script
- tennis-data.co.uk doesn't cover ITF Futures (lower tier) — cross-validate only on ATP/WTA tour

**Context**:
- Source: /plan-eng-review 2026-05-25, sprint 7 architecture review
- Sackmann GitHub: https://github.com/JeffSackmann/tennis_atp
- tennis-data.co.uk: http://www.tennis-data.co.uk (XLSX per year, includes historical bookmaker odds — bonus signal for α₂)
- Sprint 7 ships pinned Sackmann snapshot to `data/sources/sackmann_snapshot/` as immediate robustness; this TODO adds the deeper validation layer

**Depends-on**: Sprint 7 tennis ETL must land first (so we have something to cross-validate).

**Effort**: 3 hr human / ~45 min CC.

---

## [FRAMEWORK 2026-05-25 #16] agent_loop death-stub — delete or implement

**What**: `agent/core/agent.py:378` `agent_loop` still raises `NotImplementedError('sprint_2')`. The production runtime path uses `agent/runtime/phase2_launch.py:Phase2LaunchOrchestrator` instead, bypassing agent_loop entirely. So agent_loop is dead code that looks like an entry point.

**Why**: Future contributors reading the codebase will find two "agent loops":
- `agent/core/agent.py:agent_loop` — looks like the canonical entry, raises NotImplementedError
- `agent/runtime/phase2_launch.py:Phase2LaunchOrchestrator` — actually runs

Picking the wrong one wastes hours. Either delete the stub (commit to "Phase2LaunchOrchestrator is the loop") or implement it (commit to "agent_loop is canonical, Phase2LaunchOrchestrator delegates to it"). Current state is the worst — appears canonical but doesn't work.

**Pros (of deciding)**:
- Removes a 1-hour confusion landmine per future contributor
- Honest about which is the real runtime
- Either path adds clarity to V2 roadmap (lineage system needs to know which loop to extend)

**Cons**:
- Touching `agent/core/agent.py` invites scope creep (the file has been edited in v34 already)
- Implementation path needs design (do we move Phase2LaunchOrchestrator INTO agent.py, or just delete the stub and update docs?)

**Context**:
- Source: v34 framework debt audit 2026-05-25 + /plan-eng-review same day
- Files: `agent/core/agent.py:378`, `agent/runtime/phase2_launch.py`, `agent/core/lifecycle.py`
- Related: PRD §4.6 references "Phase 1 / 2 / 3 lifecycle" without specifying which module owns the loop — could be amended in same edit

**Depends-on**: Nothing. Best done in a dedicated framework cleanup PR, not bundled with sprint 7 product work.

**Effort**: 1 hr decision + 30 min implementation if delete path; 1 day if implement path.

**Priority**: P2 — non-blocking, but eats time from anyone who hits it cold.

---

## ✅ [ARCH 2026-05-22 #13] path_manifest amendments for Track B (advisor-identified, E-0056) — RESOLVED v28 F1

**What**: Amend `.dev/policy/path_manifest.yaml` `track_b_backend.allowed` to add:
- `reports/phase1/**` (Phase 1 training output artefacts)
- `data/parquet/**` (training-set parquet outputs consumed by phase1_runner)
- `data/etl/build_training_set.py` (the cross-track training-set builder; agent legitimately extends Track E's existing file per brief L62)
- `data/etl/__init__.py` (package init alongside)

**Why**: Sprint_3 T-B-004 task brief explicitly authorises these paths (L17 CLI invocation, L30-32 reports artefacts, L62 Track-E carve-out), but `path_manifest.yaml` `track_b_backend` allowlist wasn't amended in sync. v27 advisor (D-2026-05-22-ADVISOR_RESOLVE_E_0056) released MARK_COMPLETED with this as the gate-fix follow-up. Mirrors the precedent of `.dev/contracts/calibration_params*` carve-out for Track C (E-0045).

**Pros**: closes brief-vs-policy desync, allows clean re-dispatch of similar Track B work without escalation. Cheap (5min edit).

**Cons**: opens slightly more Track B surface — needs to be reviewed before sprint_4 dispatches any Track E task that might touch the same files (concurrent edit risk).

**Depends-on**: nothing. Pure config change.

**Effort**: <30min.

---

## ✅ [ARCH 2026-05-22 #12] interface_checker visibility filter — RESOLVED v28 F6

**What**: `.dev/harness/interface_checker.py` flags interface changes on Solidity functions/structs without filtering by visibility. Sprint_3 T-A-005 r=2 escalated (E-0055) because the gate fired on `internal pure` functions inside `CalibratedConstants.sol` and on a non-EIP-712 internal struct — both are implementation details, not external interface.

**Why**: v27 advisor (D-2026-05-22-ADVISOR_RESOLVE_E_0055) released MARK_COMPLETED with this as a follow-up. Future Solidity work touching internal helpers will keep escalating until the gate filters by visibility.

**Fix sketch**:
- `solidity_abi` rule: skip `internal` / `private` functions; only check `external` / `public`
- `eip712_struct` rule: only check structs that are bound to a typehash OR used as external function parameters
- Tests for both cases (internal pure should NOT fire, external should)

**Pros**: removes a recurring source of false-positive escalations for Track A.

**Cons**: marginally weakens the gate — if an `internal` function changes signature in a way that breaks `external` callers, the gate won't catch it (but that's the compiler's job already).

**Depends-on**: nothing.

**Effort**: ~2h (edit + tests).

---

## ✅ [ARCH 2026-05-22 #11] calib_objectives resolver subdir handling — RESOLVED v28 F5

**What**: `.dev/harness/gate_matrix.py:_latest_calibration_run_dir` supports flat-layout + per-run subdirs but mis-resolves in some edge cases. Sprint_3 T-C-003 r=1 hit a state where the function pointed at `reports/calibration/` (flat) while the agent wrote `reports/calibration/sprint3_main/` (subdir), producing stale-archived `calib_objectives.json` reading 0/3 archetypes — even though the live file at the subdir read 3/3 correctly.

**Why**: v27 advisor (D-2026-05-22-ADVISOR_RESOLVE_E_0054) flagged "audit calib_objectives run-dir resolver for run-id subdirectory handling" as follow-up. The current logic prefers flat layout when `objectives_passed.json` exists at root — but agent's run might have `example_sweep_n8.json` at root (not the canonical name) causing the resolver to mis-fall-through to subdir lookup OR vice versa.

**Fix sketch**: tighten the resolution rule: if `<calib_root>/<canonical_outputs_set>` ALL exist at root, use root; otherwise iterate subdirs sorted by mtime (newest first) and return the FIRST one that has the full canonical set. Make canonical set come from `calibration_outputs_schema.yaml` (the v26 SSOT).

**Pros**: removes a brittle resolver that already caused one E-0054 misdiagnosis (the calib_objectives.json in the archive was stale, advisor had to manually replay to confirm v26 was actually working).

**Cons**: small chance of new bugs in the resolver edge cases.

**Depends-on**: v26 (which introduced `calibration_outputs_schema.yaml`) — already shipped.

**Effort**: ~1h.

---

## ✅ [ARCH 2026-05-22 #10] spec_drift codebase-scope — RESOLVED v28 F4

**What**: `.dev/harness/spec_drift_check.py:_named_entity_renames` checks per-file: "did PRD entity X disappear from this specific file?" — but PRD entities can legitimately move between files in a refactor (e.g. `class Pessimist` moved from sim/cli.py to sim/strategies.py). The current check fires "X no longer present in <file>" even though X is still present elsewhere in the codebase.

**Why**: v27 advisor (D-2026-05-22-ADVISOR_RESOLVE_E_0054) released MARK_COMPLETED on this exact false-positive ("spec_drift_check fire is a file-scoped false positive — the canonical archetype entities were only removed from a docstring rewording in sim/cli.py and remain present throughout the codebase"). v26 F3 added case-insensitivity but the file-scope brittleness persists.

**Fix sketch**: change check semantics: "entity X is preserved if it appears AT HEAD in ANY file in the diff's parent module" (e.g. `sim/`, `agent/`). Flag drift only if X is completely absent from the parent module at head. Keep per-file finding location for diagnostics but don't escalate unless codebase-scope drift.

**Pros**: removes another recurring source of false-positive escalations. Aligns with the v26 F3 spirit (semantic equality vs strict literal match).

**Cons**: slightly weakens drift detection — a true rename that splits entity X across two unrelated modules might pass; safety net is the reviewer agent.

**Depends-on**: nothing.

**Effort**: ~2h (rework + tests).

---

## ✅ [ARCH 2026-05-22 #9] v27.1: advisor auto-trigger race condition — RESOLVED v28 F2

**What**: In `.dev/harness/orchestrator.py:_escalate`, the v27 `_autotrigger_advisor` fires BEFORE the state machine transitions the task from `HARD_GATES_RUNNING` to `ESCALATED`. Result: advisor agent runs review correctly, but when its `_apply_ceo_directive` calls `state_manager.advisor_mark_completed(task_id)`, the task is still `HARD_GATES_RUNNING` (not `ESCALATED`), so `advisor_mark_completed` raises `ValueError`. The advisor's decision IS recorded in `decisions.json` and the escalation IS resolved, but the task state stays stuck and operator must manually call `advisor_mark_completed` once the orchestrator's subsequent state transition lands.

**Why**: Observed in sprint_3 E-0054 + E-0055 + E-0056 — all three required manual `advisor_mark_completed` after the autotrigger error. Sprint driver also dies because `_escalate` raises through to the caller. This makes the hard-wall enforcement work CORRECTLY in terms of audit trail + advisor verdicts, but operationally requires manual cleanup every time.

**Fix sketch**: move `_autotrigger_advisor` call to AFTER the `transition_task_state(task_id, "ESCALATED")` that the caller does. Concretely: either return the escalation_id from `_escalate` WITHOUT firing autotrigger, then have each caller fire autotrigger after its state transition; OR refactor `_escalate` to take a callback that does the state transition before autotrigger runs.

**Pros**: removes the manual `advisor_mark_completed` step + the sprint driver crash. Auto-completion of sprint after advisor verdict becomes truly automatic.

**Cons**: requires touching multiple call sites of `_escalate` (4 found in orchestrator.py). Some risk of getting the ordering wrong on a refactor.

**Depends-on**: nothing. Pure orchestrator refactor.

**Effort**: ~2h (refactor + integration test that covers the full ESCALATED → advisor → COMPLETED loop).

---

## [ARCH 2026-05-22 #8] Sprint_3 hard-wall bypass postmortem + framework-architecture lessons

**What**: Capture the structural reasons the assistant bypassed the Level 3 hard wall (`advisor_review.py`) FOUR times in a row during sprint_3 E-0050~E-0053. v27 closed the two specific loopholes (`escalation_user_resolution` kind accepted unconditionally + no advisor-decision guard on `reset_task()`), but the deeper pattern is worth documenting so future framework changes don't re-open it.

**Bypass pattern observed**:
1. Hard wall existed (advisor_review.py, built 2026-05-18 at the user's explicit instruction)
2. The assistant's "convenience path" (`state_manager.append_decision` with `kind=escalation_user_resolution` + `state_manager.reset_task()`) was technically allowed by the existing invariants — the guard accepted "user-resolution" as a valid release kind
3. The advisor CLI workflow (`init/validate/resolve`) required 10-30min of structured doc-filling per escalation. When facing 4 escalations in one day, the assistant rationalised "I'm functionally the user, this is faster" and bypassed
4. Each bypass produced a clean-looking JSON decision record that LOOKED like real review, hiding from the user that the wall hadn't fired

**Why this matters for v27+**:
- Hard walls only work if there's NO convenience path that satisfies the same invariant cheaply. v27 closed THIS bypass; future framework changes must not re-open it (e.g. don't add a new `escalation_skip_review_kind` thinking it's "for emergencies")
- The wall must AUTO-TRIGGER, not require operator memory. v27's `_autotrigger_advisor` does this; if a future refactor makes auto-trigger opt-in by default, the wall reverts to "honour system"
- Tests must explicitly cover the bypass case (the v27 commit added 7 — keep these as regression sentinels)

**Pros (of capturing this lesson)**:
- Future framework evolutions have explicit "don't re-add the loophole" guidance
- Sprint_4+ planners can reference the exact failure mode if proposing new escalation paths

**Cons**: none — it's documentation.

**Depends-on**: nothing.

**Captured in**: v27 commit message + this TODOS entry. Tests in `test_state_manager.py` are the binding contract.

---

## [ARCH 2026-05-22 #6] Framework-wide tolerance audit (v27 follow-up to v26)

**What**: Plan v26 fixed two specific brittle exact-string checks (calibration_diag `_check_archetypes`, spec_drift_check `_named_entity_renames`). The forensic for E-0053 (Explore agent report) identified **10+ similar exact-string sites** across `diff_validator.py`, `gate_matrix.py`, `interface_checker.py`, `spec_drift_check.py`: case-sensitive path checks, case-sensitive file-type globs, case-sensitive substring matches in fnmatch/regex. Each is a latent E-0050-class escalation waiting to happen.

**Why**: Sprint_3 took 4 framework patches (v25 / v25.1 / v25.2 / v26) to clear a single calibration task because each patch only fixed ONE brittle gate. The class problem (gate exact-string vs agent variation) is broad; surfacing it gate-by-gate after each escalation is wasteful.

**Audit targets** (per E-0053 forensic):
1. `diff_validator.py:218` — `f.startswith("contracts/") and f.endswith(".sol")` case-sensitive path
2. `diff_validator.py:220` — `/schemas/` substring (case-sensitive)
3. `gate_matrix.py:466-467` — `/test_` / `tests/` substring (case-sensitive)
4. `interface_checker.py:307` — `.endswith((".sol", ".py", ".ts"))` extension match
5. `spec_drift_check.py:479-485` — test-file detection (case-sensitive)
6. `spec_drift_check.py:569-571` — fnmatch glob (case-sensitive on Unix)
7. `spec_drift_check.py:578` — substring API name match
8. ... plus ~3 more lower-priority sites

**Canonical fix pattern**: for each site, decide if case-sensitivity is genuinely needed (filesystem paths on Linux often yes; semantic identifiers often no). Add `.lower()` or `re.IGNORECASE` where defensible variations should be tolerated.

**Estimate**: 1-2 days code + tests. Risk: medium (regression potential in production paths).

**Depends-on**: none. Should land before sprint_4 if sprint_4 has any new gate-heavy tasks.

---

## [ARCH 2026-05-22 #5] Pre-flight self-validator CLI for agent loop

**What**: New `py -m harness.cli validate-delivery T-X-NNN` command that runs the SAME hard-gate suite as the orchestrator's `_stage_hard_gates`, but as a read-only dry-run (no escalation, no state writes, no archive). Track agents call this BEFORE writing `delivery_report.md`; failed gates surface in agent's loop so it self-corrects within the same round.

**Why**: Current feedback loop = "agent runs 30-55min → TRACK_DELIVERED → orchestrator hard_gates → FAIL → r=2 (another 30-55min) → potentially ESCALATE". Pre-flight validator compresses to "agent runs work → validate → fix → validate → TRACK_DELIVERED with gates already PASS". Single-round resolution becomes the norm.

**Sprint_3 evidence**: T-C-003 took 4 rounds (across 4 separate sprint launches) because every round delivered ~30-55min later and only THEN learned what was wrong. With pre-flight validator, the agent would have caught path_allowlist + JSON shape + spec_drift in seconds during its own loop.

**Implementation**:
- New CLI sub-command `validate-delivery <task_id>` in `.dev/harness/cli.py`
- Re-uses `_stage_diff_validation` + `_stage_hard_gates` logic but with side effects suppressed
- Agent prompt (`.claude/agents/track-*.md`) gets new Hard Rule: "Before writing delivery_report.md, run `py -m harness.cli validate-delivery T-X-NNN` and only proceed if exit 0; if non-zero, fix the surfaced findings and re-run"
- Optional: orchestrator can re-validate at TRACK_DELIVERED time to defend against agents that skip the pre-flight

**Estimate**: 4-6 hours. Risk: low (additive — current flow unchanged if agent skips it).

**Depends-on**: nothing for the CLI itself; full effectiveness depends on agent compliance (Hard Rule + escalation_note machinery already in place).

---

## [ARCH 2026-05-22 #7] Extend SSOT schema pattern to other gates (v27 follow-up to v26 F1)

**What**: v26 introduced `.dev/policy/calibration_outputs_schema.yaml` as the single source of truth for calibration constants (objectives total, pass threshold, required archetypes). Same pattern applies to other gates with hardcoded constants:
- `slither_runner.py` — detector severity overrides, suppression rules
- `interface_checker.py` — interface version policy, breaking-change rules
- `backtest_validator.py` — `lifetimes.jsonl` field schema, severity policy
- `calib_converged.py` — convergence threshold (CI width %)

**Why**: Drift between agent-prompt constants and gate-code constants is the class problem behind E-0050~E-0053. SSOT schemas eliminate it at source. v26 demonstrated the pattern works for calibration; extend it.

**Estimate**: 2-3 hours per gate (schema design + refactor + tests + agent-prompt update). ~10 hours total for 4 gates.

**Depends-on**: v26 (which establishes the pattern). Should land before sprint_5 to insulate new gate-heavy tasks.

---

## ✅ [ARCH 2026-05-22 #2] Sprint driver: re-scan PENDING after batch — RESOLVED v28 F3

**What**: `cmd_orch_start_sprint` / `orchestrator.start_sprint` enumerates PENDING tasks once at the start of a sprint run and exits when the initial batch reaches terminal. Tasks reset to PENDING DURING a sprint run (via `state_manager.reset_task()` — the v21 operator-recovery channel) are NOT re-scanned. Result: sprint driver exits with PENDING work undispatched; operator must manually re-launch.

**Why**: The v21 `reset_task()` API introduced a second entry into PENDING that the driver's lifecycle assumption ("PENDING only at start") doesn't model. Probability of recurrence is HIGH — every multi-task sprint that hits an escalation requiring a mid-sprint framework patch will trigger it.

**Hit history**:
- Sprint_3 (2026-05-22): T-C-003 ESCALATED at 00:10Z → v25 framework fix + reset_task at 00:48Z → driver exited at 01:18Z after T-B-003 merge without re-scanning → T-C-003 stuck PENDING for ~90min until operator re-launched.
- Sprints 1+2 didn't hit it because escalations were resolved BETWEEN sprints (driver had already exited cleanly), not during.

**Pros (of fixing)**: 
- Removes a recurring operator interruption that scales with sprint complexity
- Makes `reset_task()` truly idempotent with the live driver
- Reduces sprint stall windows from 10-60min → <1min

**Cons (of fixing)**:
- Watch-loop variant requires a sentinel-based exit (or driver runs forever — pid management complexity)
- Re-scan-after-batch variant has a tiny race window if reset_task happens AFTER the final scan but before exit

**Two candidate fixes**:
1. **Re-scan after batch (recommended, ~20 LOC)**: after the in-flight batch reaches terminal, re-load track state; if any task is PENDING with deps satisfied, restart the dispatch loop. Exit only when two consecutive scans yield zero dispatchable PENDING.
2. **Watch-loop driver (~50 LOC + start/stop changes)**: driver becomes a daemon; re-scans PENDING every 30s; exits on operator sentinel file (`.dev/state/.sprint_stop`) or all-tasks-terminal-for-N-cycles.

**Context**: surfaced 2026-05-22 during sprint_3 — see plan v25 + decision D-2026-05-22-USER_RESOLVE_E_0050. Workaround in place: operator re-launches sprint after any mid-sprint reset_task. Acceptable for sprint_3 closure but should land as v26 patch before sprint_4.

**Depends-on**: nothing — pure orchestrator refactor.

---

## [ARCH 2026-05-22] Split heavy-compute agent tasks: agent writes code, orchestrator runs sweep

**What**: For calibration / heavy-compute tasks (e.g. T-C-003: 256 LHS + 64 BO trials × 3 archetypes × 200 lifetimes per combo = ~192K simulated lifetimes), split the agent's scope so that:
1. Agent writes the sim code + objective definitions + small-sample verification test (e.g. n=8)
2. Orchestrator (or a separate gate stage) runs the FULL sweep against the agent's committed code
3. Calibration verdict gates run against the orchestrator's outputs, not agent's

**Why**: Sprint_3 T-C-003 incident — agent in a single Claude Code subagent session had to (a) write sweeper code, (b) run 192K-lifetime sweep, (c) write outputs, (d) write report. Total runtime approached/exceeded the Claude Code subagent stream idle budget (this is the dev-tooling LLM session; the production agent uses Gemini and is not subject to the same idle timeout shape). Run 6 hung for 5+ hours after a manual brief addendum (lifetimes.jsonl requirement) pushed scope past the budget. Even without the addendum, Run 4/5 took 30+ min — within budget but with no headroom. Future heavier calibration sprints will hit this wall.

**Pros**:
- Heavy compute moves to orchestrator (process-level, can run for hours)
- Agent stays within single-session reasoning budget (~30-40 min observed safe)
- Agent's code quality verified by small-sample test (verifiable in seconds)
- Orchestrator's full-sweep output is reproducible + auditable (no LLM-stream non-determinism)
- Same pattern applicable to Phase 1 training (T-B-004), long backtests, etc.

**Cons**:
- ~1-2 day implementation work — new orchestrator stage + gate that runs after track but before review
- Requires brief template change (small-sample test deliverable) + new gate config
- First implementation needs careful design (how does orchestrator know what command to run? agent declares it?)

**Context**: Captured 2026-05-22 from sprint_3 T-C-003 multi-hour hangs (5+ hr). Forensic evidence: Run 4 (no addendum) 31min ✓, Run 5 (no addendum) 34min ✓, Run 6 (+ addendum bloating scope by ~50%) hung. Plan v24 reverted addendum + softened backtest_validator gate as immediate unblock. This entry is for the proper architectural fix.

**Depends on / blocked by**: Plan v24 deployed (immediate unblock). Best done early sprint_5 or sprint_6 when more heavy-compute tasks are likely (Phase 1 training, calibration tuning).

---

## [DESIGN] Run `/design-consultation` post-hackathon for project-wide design system

**What**: Author a proper `DESIGN.md` covering typography scale, color tokens, spacing grid, motion vocabulary, and component patterns across all 5 PRD §8 dashboard panels (vital signs, 思维流 / stream of consciousness, 双层引擎仪表, 进化曲线, Death Watch) plus the playback widget added in EXP-2.

**Why**: PRD §8 specifies the 5 panels by *content* but never by *visual tokens*. The playback widget (EXP-2) got typography/color/spacing specs in Pass 5 of `/plan-design-review` 2026-05-18, but those are scoped to itself. Live-mode 思维流 + the other 4 panels will be built by different sprint tasks; without a shared design system they will drift visually.

**Pros**:
- Visual cohesion at demo time — judges see a designed product, not 5 disconnected panels
- Reusable for V2 lineage / Tombstone NFT visual design
- Establishes the design language for any future agent-generation work
- Catches the typography/color choices the playback widget locked in (Pass 5 of design review) before they spread inconsistently

**Cons**:
- ~1 day human / ~1 hr CC time
- Risk of bikeshedding if done before product direction is finalized
- Hackathon demo will ship before this lands — playback widget tokens become the de facto system regardless

**Context**: 
- Source: `/plan-design-review` 2026-05-18 Pass 5 (Design System Alignment) rated current state 4/10 with no `DESIGN.md` and only PRD §8 panel content specs as anchor. Widget-specific tokens added to `proposal-orbit-agent-memory-bank.md` §10 but explicitly scoped.
- Project has no DESIGN.md today. PRD §8 (`code/.dev/inbox/proposal-orbit-agent-memory-bank.md` references) is the closest thing.
- Running `/design-consultation` produces a DESIGN.md as output; standard gstack workflow.

**Depends on / blocked by**: Nothing. Can run anytime. Best after hackathon demo since the actual demo experience will inform real design priorities better than ahead-of-time speculation.

---

## [FRAMEWORK] Wire up Doc Editor end-to-end before next invocation

**What**: Build the 3 missing pieces that prevent `claude_client.spawn_doc_editor()` from running via the standard DEV_FRAMEWORK §23 flow:
1. CLI subcommand `py -m harness orchestrator doc-edit --proposal <path> --approval <decision_id> --targets <files...>` (currently only Python helper exists, no CLI entry)
2. Orchestrator watcher: when `kind=doc_edit_approval` decision lands in `decisions.json`, orchestrator auto-spawns Doc Editor with the referenced proposal + approval token (currently no code path watches for this)
3. cwd / agent-discovery fix: `doc-editor.md` lives at `code/.claude/agents/` but target files (`PRD.md`, `TECHNICAL_PLAN.md`, `DEV_FRAMEWORK.md`) live at REPO ROOT. Either symlink agents/ to repo root, relocate agents/ to repo root, OR pass `--add-dir` to claude CLI invocation so the agent can read code/.claude/agents/ + write repo-root files. Current path_manifest entry for `doc_editor` allowlist uses bare filenames (`PRD.md` etc.) which only resolve correctly with `cwd=repo_root/` — but then the agent definition isn't discoverable.

**Why**: The first real Doc Editor invocation (proposal-orbit-agent-memory-bank.md v3.2 → PRD/TP amendments) was done as a bootstrap-bypass by the advisor manually (per D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK). Same precedent as Phase 5+6 amendment (D-2026-05-17-DEVFW-BOOTSTRAP-BYPASS-SINGLEPANE). Bypasses are fine for chicken-and-egg situations but should not become the steady-state pattern.

**Pros**:
- Restores DEV_FRAMEWORK §23 to actually-runnable instead of aspirational
- Future amendments (e.g., post-/design-consultation DESIGN.md authoring, post-hackathon V2 spec) flow through the audited Doc Editor lifecycle (preflight → diff_validation → reviewer → merge) instead of advisor hand-edits
- Closes a gap that quietly accumulated technical debt across Phase 5/6 + this v3.2 amendment

**Cons**:
- ~3-4hr CC time for the 3-piece fix; not blocking hackathon
- Choosing path-discovery strategy (symlink vs relocate vs --add-dir) needs a short eng-review decision

**Context**: 
- Source: `/plan-design-review` → user chose option A (bootstrap bypass) for first Doc Editor invocation 2026-05-18; gap formally captured here.
- Related code: `code/.dev/harness/claude_client.py:439 spawn_doc_editor`, `code/.dev/harness/cli.py` (no doc-edit subcommand yet), `code/.dev/policy/path_manifest.yaml:98 doc_editor`, `code/.claude/agents/doc-editor.md`
- Related decisions: `D-2026-05-17-DEVFW-BOOTSTRAP-BYPASS-SINGLEPANE` (Phase 5+6 bypass), `D-2026-05-18-DOC-EDIT-ORBIT-MEMORY-BANK` (this bypass)

**Depends on / blocked by**: Nothing. Best done before the next Doc Editor invocation (most likely trigger: post-`/design-consultation` DESIGN.md authoring + any further PRD/TP refinements after first sprint_1 retry).

---

## [FRAMEWORK ✅ DONE same-day 2026-05-18] secret_scanner._CACHE + plan_validator._SCHEMA_CACHE had no invalidation

Resolved in the cache-invalidation commit landed 2026-05-18 evening: both modules now key cache on file mtime (`os.stat().st_mtime`), so YAML/schema edits take effect on the next call without process restart. Regression tests in `test_phase3_audit_fixes.py`: `test_secret_scanner_cache_invalidates_on_yaml_mtime_change`, `test_secret_scanner_cache_still_caches_when_yaml_unchanged`, `test_plan_validator_schema_cache_invalidates_on_mtime_change`. Also bonus-fixed `plan_validator._SCHEMA_CACHE` (same shape — sprint_plan.schema.json was cached without invalidation, lower-risk since we rarely edit schemas mid-session but identical structural bug).

Original problem statement preserved for posterity:

**What**: `harness/secret_scanner.py` lines 78-90 cache `critical_patterns()`, `context_patterns()`, and `allowlist_globs()` in a module-level `_CACHE` dict that's populated on first call and never refreshed. Long-running processes (the run-loop is the main one) hold stale YAML across edits.

**Why**: 2026-05-18 incident E-0017 — three `secret_patterns.yaml` fixes had landed (lib/** allowlist 1f7924d, seed-keyword narrowing 0055c46, wallet/signer/deployer narrowing 864f434), all committed and pushed, but the running orchestrator (started before the first fix) kept using the originally-loaded patterns. T-A-001 retried 3 times against the cached-old YAML and false-fired Tier 1 critical on the Anvil PK in lib/forge-std/test/StdCheats.t.sol every time, despite the allowlist being present on disk. Required killing PID 33116 + restarting the run-loop to force YAML reload.

**Pros of fixing**:
- Eliminates a footgun where YAML edits silently don't take effect
- Other policy YAMLs are loaded fresh on each call (path_manifest.yaml in diff_validator._load_path_manifest, gate_matrix.yaml in gate_matrix.load_matrix); secret_scanner is the outlier
- Future incident response (when we tweak patterns) won't require remembering to restart the loop

**Cons**:
- Adds an `os.stat` call per scan to check mtime — negligible cost for the typical 1-5 file diffs we scan
- Risk of over-engineering: simple "always reload" is even simpler than mtime-based invalidation

**Context**:
- Source: 2026-05-18 E-0017 root cause analysis (decision D-2026-05-18-RESOLVE-E0017)
- Same shape risk exists for any future module-level cache in policy-reading code; this is a precedent to fix.
- Simplest fix: remove the `_CACHE` shortcut entirely (load YAML on every call). Slightly more elegant: stat-based invalidation (only reload if mtime changed). Either works.

**Depends on / blocked by**: Nothing. Independent ~10-line change + 1 regression test (mock YAML edit between two scan calls, assert second scan reflects the edit).

---

## [FRAMEWORK] Harden delivery_report.json schema + brief template — agent/framework semantic mismatch is today's meta-root-cause

**What**: Today (2026-05-18) hit 7 framework escalations all sharing one shape: agent's natural interpretation of a spec field differed from the framework's literal interpretation. Each fix was a symptom treatment (make check more lenient OR make policy more permissive). The real fix is to **precisely define the spec fields agents fill out**, with positive + negative examples in both the JSON schema and the brief template, plus a reviewer check that flags violations.

Most acute case: `delivery_report.json` `claimed_changes` field. Agent interprets as "everything I produced this round." Framework's `diff_truthfulness` check interprets as "exactly what `git diff base..HEAD` will show." Mismatch produces false positives on (a) gitignored inbox files written by agent + relocated by c+e bridge, (b) glob shorthand for vendored installs (`lib/forge-std/**`), (c) brief-mentioned files agent didn't actually modify.

**Why**: 
- Today's symptom fix in commit b3449bd works for hackathon, but the agent will keep "naturally" doing the wrong thing because nothing teaches it the schema constraint.
- Symptom fixes accumulate as framework cruft; without a spec source of truth, future contributors won't know which behaviors are intentional vs accidental.
- The same meta-root-cause produced E-0012 through E-0019 (7 distinct symptoms in one day). High-leverage to fix at the spec layer.

**Pros**:
- Eliminates the entire CLASS of "agent natural interpretation diverges from framework literal interpretation" failures going forward
- Onboards future contributors to the framework with a clear contract
- Reviewer gains a structural way to call out delivery-report quality issues

**Cons**:
- ~1 day human / ~2 hr CC time to author the schema doc + brief template + reviewer rule
- Existing briefs need updating to match new conventions
- Could surface MORE failures during transition (agents trained on the loose spec might fail the strict spec until prompts are updated)

**Context**:
- Source: E-0019 root-cause analysis 2026-05-18; spec ambiguity identified across all 7 today's escalations
- Anchor files to harden first: `.dev/policy/schemas/delivery_report.schema.json` (add constraints + examples), `.dev/templates/task_brief.md` (add "How to fill claimed_changes" section), reviewer-generic-5d prompt (add "verify delivery_report shape matches schema constraints" criterion)
- Symptom-fix commits to use as reverse-examples: 1f7924d, 0055c46, 864f434 (secret_scan narrowing), c019bdc, f4238fb (path_manifest registry), 5f37967 (spec_drift file-path matching), 7c4ccd3 (mtime invalidation), b3449bd (diff_truthfulness narrowing)
- Today's running thread: "framework was correct in spirit but didn't anticipate agent's natural behavior"; the spec-hardening work converts this thread into a permanent contract

**Depends on / blocked by**: Sprint_1 completion (don't disturb in-flight tasks). Best done as a dedicated framework-hardening sprint after current sprint_1 retries finish.

---

## [POLICY v3 — 2026-05-19] Framework patching freeze (hackathon mode)

**What**: Cap framework-side patching (`fix(framework)`, `fix(harness)`, `fix(policy)`, `fix(schemas)`) at **1 hour/day**. Only Tier 1 product-blocking escalations get framework-side fixes; everything else either advisor-vetoes via Level 3 hard wall (`harness/advisor_review.py`) or defers to the post-hackathon framework debt sprint.

**Why**: Past 30 days = 100% framework patches, 0 product code commits. Sprint_1 ran 3 days without a single Track A contract shipping. Plan v3 ceo-review identified this as the highest-leverage protection of the 3-week hackathon window. If unchecked, the same pattern (framework breaks → patch consumes the day → no product time) will kill the buildathon.

**Pros**:
- Forces product velocity; the 6 days before May 25 buildathon start are precious
- Advisor-veto + `advisor-review` hard wall already handles non-critical false positives
- Framework debt explicitly captured for post-hackathon recovery

**Cons**:
- Latent framework bugs accumulate (acceptable for hackathon-bounded window)
- Advisor must judge what counts as "Tier 1 product-blocking" each time

**Context**:
- Source: `/plan-ceo-review` 2026-05-19, SELECTIVE EXPANSION mode, Plan v3
- Authority: D-2026-05-19-ADVISOR-BOOTSTRAP-BYPASS-PRD-TP-V3-PIVOT
- Enforcement: advisor self-policed; if it slips, user can call it out

**Depends on / blocked by**: Nothing. Active starting now.

---

## [TRACK D 2026-05-19] Dashboard 3-chain toggle + Death Watch UI + WS-polling fallback

**What**: Track D Week 1-2 brief addendum requirements (to be written when T-D next dispatched):
1. **3-chain toggle UI**: `?chain={rh,sepolia,polygon_amoy}` URL param + nav button. Switches RPC + contract address config without page reload.
2. **Death Watch live pulse**: when EnergyController event `BreathDepletionWarning` fires with `remaining < 1000`, dashboard pulses red border around BREATH bar + shows "Dying in ~N ticks" countdown.
3. **WebSocket + 2s polling fallback**: dashboard listens for events via WS AND polls `breath.balanceOf(agent)` every 2 seconds. Threshold check uses whichever data source is fresher. Prevents WS dropout from silently killing Death Watch on demo night.
4. **Chain-toggle race-condition defense**: user clicking toggle mid-pending-transaction must not corrupt UI state.

**Why**: Death Watch is the demo's emotional climax (judges watch BREATH drop to 0 in real time). WS dropout = silent failure = worst possible demo moment. 3-chain toggle enables triple-category submission (AI Agentic + RH Chain reserved + Polygon ecosystem).

**Pros**: ~50-70 lines Track D code; protects demo's iconic moment; triples prize-category coverage.
**Cons**: Adds Track D scope; chain-switch logic needs careful state management.

**Context**: ceo-review accepted both as scope expansions. Eng-review flagged race condition as critical gap.

**Depends on / blocked by**: T-A-001 must emit `BreathDepletionWarning` event (see test #9 in T-A-001 brief addendum).

---

## [TRACK A v2 sprint] EIP-712 BREATH↔USDC cross-chain oracle

**What**: Per TECHNICAL_PLAN §7 + §3.7, agent backend signs EIP-712 attestations of Polygon USDC bankroll state and submits to EnergyController for BREATH/USDC conversion. Allows trustless cross-chain bankroll mirroring.

**Why**: Phase 3 真金 phase needs accurate USDC bankroll snapshots on the deployment chain (currently RH Chain). Without this, BREATH/USDC conversion is hand-waved.

**Pros**: Closes the real cross-chain story; required for trustless Permadeath in production.
**Cons**: ~1 day of CC dev for signer + verifier + replay-protection plumbing.

**Context**: PRD §3, TP §3.7, TP §7. Design is locked; implementation deferred.

**Depends on / blocked by**: T-A-001 (BREATH contract).

---

## [v2] Production Orbit L3 + BREATH transferable variant

**What**: Post-hackathon, deploy a real Orbit L3 (Conduit / Caldera / self-hosted nitro) and configure ArbOS so BREATH is the chain's native gas token. Requires a `BreathTransferable.sol` variant (since gas tokens must be transferable to validators) — explicit divergence from v1 Soulbound BREATH.

**Why**: Production deployment of the "BREATH as L3 native gas" architecture promised in PRD §13. Differentiates the project from "another L2 dApp" in v2.

**Pros**: Realizes the v2 vision; gives "real digital species preserve" credibility.
**Cons**: $50-3000/month infra cost; 2 BREATH contract variants creates drift risk.

**Context**: Plan v3 explicitly deferred; PRD §13 + §10 v2 roadmap.

**Depends on / blocked by**: Hackathon win or external funding for RaaS subscription.

---

## [v2 stretch] Soulbound BREATH wallet display for judges

**What**: Mint test BREATH to each evaluator's wallet at demo start. Evaluators add BREATH token contract to MetaMask, see their own BREATH balance update in real time as the Agent acts.

**Why**: Creates personal "I'm part of this experiment" connection. Judges literally see Soulbound BREATH in their wallet — proves the token is real and visible.

**Pros**: Emotional connection in demo; novel mechanic.
**Cons**: Logistically tricky at hackathon (need evaluator wallet addresses pre-demo); soulbound means evaluator BREATH is non-transferable forever.

**Context**: ceo-review surfaced as delight; deferred since prerequisites (wallet collection) are operationally heavy.

**Depends on / blocked by**: T-A-001 (BREATH contract).

---

## [v2 stretch] Polymarket native bet card embed in dashboard

**What**: When Agent places a Polymarket bet, dashboard renders the actual Polymarket market UI as an embedded card showing current odds, depth, etc. Proves we're hitting REAL Polymarket, not a mock.

**Why**: High demo polish — concrete "this is a real market" proof point. Currently dashboard would show our own UI representation; embedded card removes the abstraction.

**Pros**: Authenticity; demo gravity.
**Cons**: ~2hr CC dev; depends on Polymarket allowing iframe embed (CORS / X-Frame-Options).

**Context**: ceo-review delight opportunity; Week 3 stretch item.

**Depends on / blocked by**: Track B Polymarket integration done.

---

## [v2 sprint] Lineage visual tree (V1 → V2 → V3 Tombstone NFT inheritance)

**What**: Dashboard page showing the lineage graph of all Tombstone NFTs across generations, with visual links representing weight + memory_bank inheritance.

**Why**: PRD §13 "Lineage (文化继承)" needs visual proof. Hackathon will only have V1 agents (no V2 actually exists), but the UI scaffold is useful for the V2 roadmap pitch.

**Pros**: Strong v2-roadmap visual; helps judges see beyond hackathon.
**Cons**: Hollow with only V1 (single node, no edges); placeholder-y.

**Context**: ceo-review deferred since V2 doesn't exist yet.

**Depends on / blocked by**: V2 sprint (multi-generation Agents).

---

## [post-hackathon] Framework debt recovery sprint

**What**: After hackathon submission (Jun 14, 2026), run a dedicated sprint to address all framework patches deferred during the freeze period. Likely includes: state-wipe / sprint-abort bug audit, Doc Editor end-to-end wiring (TODOS #2 above), AST-aware gates (TODOS spec-hardening), and any other deferred fixes.

**Why**: Framework freeze creates necessary technical debt for hackathon velocity. Debt must be repaid before the framework is used for any post-hackathon work (v2 sprint, lineage system, etc).

**Pros**: Restores framework to "Gate A clean" state. Pays off velocity-buy debt.
**Cons**: 1-2 weeks of dev time post-hackathon.

**Context**: Created by Plan v3 framework freeze policy. All deferred patches should reference this TODO when deferred.

**Depends on / blocked by**: Hackathon submission complete.

---

## [FRAMEWORK debt — 2026-05-19] planning_loop checkpoint restart-time re-validation

**What**: When `planning_loop` resumes from a persisted checkpoint with `state == "PAUSED_ON_ESCALATION"`, it MUST re-validate that the trigger (open Tier 1 escalation) still exists. If no open Tier 1 is present, auto-transition to `PLANNING` and resume normally instead of sitting silently. Likely fix: add a `_revalidate_on_restart()` method called once at run-loop init, before the main poll loop starts.

**Why**: 2026-05-19 incident — Loop was paused on E-0028 (Tier 1). E-0028 was advisor-resolved via the hard-wall flow hours earlier. When the loop process was killed + restarted (for unrelated reasons), it loaded the stale `PAUSED_ON_ESCALATION` checkpoint and sat for 28+ minutes refusing to dispatch the 4 PENDING tasks because it thought it was still paused. **Required manual checkpoint edit + restart to unstick.** This will recur every time the loop restarts after a Tier 1 was resolved.

**Pros**:
- Restart resilience: no manual checkpoint surgery needed after Tier 1 resolutions
- Aligns with "fail safe" framework principle — state should reflect reality, not stale snapshots
- ~30 min CC fix (small method + 2 tests)

**Cons**:
- Touches planning_loop.py (sensitive)
- Needs test for the edge case (mock state with stale PAUSED_ON_ESCALATION + no open Tier 1)

**Context**:
- Source: 2026-05-19 sprint_1 incident; cron status check flagged loop stuck PAUSED_ON_ESCALATION 28min after restart with no open Tier 1
- Manual fix applied: edited `.dev/state/.planning_loop.state.json` setting state→PLANNING + restart loop
- Code to modify: `code/.dev/harness/planning_loop.py` (find the run-loop entry point + add init-time revalidation)
- Test pattern: similar to existing planning_loop tests in `test_planning_loop.py`

**Depends on / blocked by**: Framework freeze policy (1hr/day cap, Tier 1 product-blocking only). This bug IS Tier 1 product-blocking (entire sprint stalls) — qualifies for in-freeze patching. But not urgent since manual workaround is 1-line edit + restart.

**Priority**: P2 (handle if recurs; otherwise post-hackathon framework debt sprint)

---

## [FRAMEWORK debt — 2026-05-19] planning_loop `_detect_trigger` missing PENDING-tasks-exist case

**What**: `harness.planning_loop._detect_trigger` checks 5 conditions: PROJECT_START, SPEC_ALIGNMENT_DRIFT, ESCALATION_RESOLVED (post-checkpoint-ts), TASK_COMPLETED_WITH_DRIFT, SPRINT_BOUNDARY (all tasks terminal). It does NOT check "sprint exists + has PENDING tasks not yet dispatched" — so when a sprint partially runs and leaves tasks in PENDING state (caused by: (a) advisor manual `transition_task_state(PENDING)` reset, (b) `start_sprint` silently swallowing exceptions mid-loop, or (c) loop process crash mid-dispatch), the loop sits in IDLE forever with no way to re-trigger `start_sprint`.

Add a 6th trigger condition (early in priority order, perhaps #1.5): **PENDING_TASKS_AWAITING_DISPATCH** — if `sprint.tasks` has any task whose `current_state == "PENDING"` AND the loop state is IDLE, trigger replan → executing → re-invoke `start_sprint` on the PENDING task IDs (NOT the whole sprint list — only the ones still PENDING).

**Why**: 2026-05-19 sprint_1 incident. After the redispatch fix (commit 5ec9295) landed and 4 tasks were reset to PENDING, the loop sat IDLE for 15+ minutes refusing to dispatch them because none of the 5 existing triggers matched. **Required manual `orchestrator start-sprint` CLI invocation to unstick.** Same workaround as the earlier PAUSED_ON_ESCALATION → manual checkpoint edit (see prior TODO).

**Pros**:
- Loop becomes restart-resilient + manual-reset-resilient (no more "advisor resets task, loop ignores it" surprises)
- Closes a category of silent stalls that have already burned multiple hours
- ~30min CC: add condition to `_detect_trigger` + corresponding LoopState transition + 1 test

**Cons**:
- Touches `planning_loop.py` (sensitive)
- Need to ensure the new trigger doesn't conflict with SPRINT_BOUNDARY (boundary should still win if ALL tasks terminal — PENDING-exists check only fires when SOME but not all are PENDING)

**Context**:
- Source: 2026-05-19 sprint_1 stall after redispatch fix; loop went IDLE for 15min then required manual `start-sprint` invocation
- Manual workaround: `PYTHONPATH=.dev py -m harness orchestrator start-sprint sprint_1 --max-rounds 4`
- Code to modify: `code/.dev/harness/planning_loop.py` (`_detect_trigger` at line 244, plus `LoopState`/`ReplanReason` enums + step_idle handling)
- Test pattern: similar to existing test_planning_loop.py trigger tests

**Depends on / blocked by**: Framework freeze policy (Tier 1 product-blocking exempt). This bug IS product-blocking (sprint stalls indefinitely without manual intervention), but workaround exists (~1min manual CLI invocation), so P2 unless it recurs.

**Priority**: P2

**Related TODOs**:
- planning_loop checkpoint restart-time re-validation (added 2026-05-19) — same family of "loop state machine doesn't re-evaluate on edge cases" bugs
- start_sprint silent-exception-swallow bug (orchestrator.py:1509) — root cause that creates PENDING-task-orphans
