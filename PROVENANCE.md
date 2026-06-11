# Provenance — the Buildathon timeline

**TL;DR:** 100% of this project was built inside the Arbitrum Open House London
Buildathon window (May 15 – Jun 11, 2026). This repository was **re-initialized
on Jun 11, 2026** to scrub dev-session logs that had leaked an API key (the key
was revoked; see the gitleaks pre-commit hook in `.githooks/` that now guards
the repo). That reset erased the public commit history — so this file
republishes the **sanitized log of all 480 development commits** (dates +
messages only) from the local history backup, plus tamper-proof on-chain
anchors.

## Tamper-proof anchors (verify on-chain)

| Event | Date (UTC) | Proof |
| --- | --- | --- |
| First contract deploy (EnergyController) | 2026-05-25 02:19:09 | [Robinhood Chain block 60897767](https://explorer.testnet.chain.robinhood.com/block/60897767) |
| TombstoneNFT deploy | 2026-05-25 02:19:24 | [tx 0xe2fc2783…](https://explorer.testnet.chain.robinhood.com/tx/0xe2fc2783c21513bd3711265ac2f9d6101096d1d55cd0ee8e9ea13503ad534d78) |
| Same 5 contracts on Arbitrum Sepolia | 2026-05-25 | [deploy block 10917212](https://sepolia.arbiscan.io/address/0xDE6178D892AA9F80f748a399f07B588b08Faec2f) |

## Daily commit activity (480 commits / 22 active days)

| Date | Commits | | Date | Commits |
| --- | ---: | --- | --- | ---: |
| 2026-05-15 | 18 | | 2026-05-25 | 19 |
| 2026-05-16 | 37 | | 2026-05-26 | 24 |
| 2026-05-17 | 15 | | 2026-05-27 | 29 |
| 2026-05-18 | 26 | | 2026-05-28 | 26 |
| 2026-05-19 | 19 | | 2026-05-29 | 25 |
| 2026-05-20 | 28 | | 2026-06-01 | 10 |
| 2026-05-21 | 24 | | 2026-06-03 | 1 |
| 2026-05-22 | 24 | | 2026-06-04 | 5 |
| 2026-05-23 | 28 | | 2026-06-08 | 21 |
| 2026-05-24 | 32 | | 2026-06-09 | 36 |
| | | | 2026-06-10 | 23 |
| | | | 2026-06-11 | 10 |

## Milestones

- **May 15** — bootstrap: dev-framework governance layer, orchestrator harness
- **May 19** — first Solidity skeletons (EnergyController + TombstoneNFT)
- **May 25** — all 5 contracts deployed to Robinhood Chain testnet + Arbitrum Sepolia; NBA → tennis pivot
- **May 26** — sandbox path locked: real Polymarket data + mock orders; submission manifest
- **Jun 8–9** — real 5-engine signal source (CLOB + Sackmann); dashboard pages (roadmap/backtest/survival/mock)
- **Jun 10–11** — L6 self-learning runs (MiniMax + Gemini), realism rules + invariants, repo re-init + secret hygiene

## Full commit log (dates + messages, oldest first)

Author identities: `balflee` plus two local orchestrator bot identities. File
contents are NOT republished — only this metadata. The pre-reset history is
preserved locally and can be shown to judges on request.

```text
2026-05-15 Bootstrap: Genesis Dev Framework v1.1 governance layer
2026-05-15 Day 1 PM partial: harness package scaffold + state_manager + journal + CLI + preflight
2026-05-15 Day 1 PM: secret_scanner + diff_validator + review_validator
2026-05-15 fix: render-state should not eagerly create empty track skeletons
2026-05-15 Day 1 PM e2e smoke: record I-001 worktree desync issue for Day 2 remediation
2026-05-15 fix(I-001): worktree pruning + post-add verification
2026-05-15 fix(preflight): filter orchestrator-owned paths from dirty-tree check
2026-05-15 issues: close I-001 (worktree desync) after Day 2 AM smoke validation
2026-05-15 Day 2 AM: tools/{base,forge_runner,pytest_runner,mypy_runner} + gate_runner + CLI run-gates
2026-05-15 Day 2 PM: escalation + finalize + lookahead + slither + reviewer_packet + calibration_diag
2026-05-15 roles: wire Claude Code skills into 10 agent prompts
2026-05-15 Day 3 AM: integration_tests + 70 unit/e2e cases + 2 small harness extensions
2026-05-15 Gate A: CONDITIONAL PASS sign-off (D-2026-05-15-GATEA)
2026-05-15 Gate A: clean PASS — 183 tests, 87% coverage, T8 10/10, live mode automated
2026-05-15 Gate A APPROVED by User — framework bootstrap closed, Track A unlocked
2026-05-15 fix(preflight): preserve leading space in porcelain status filter
2026-05-15 framework(T-C-001 flow gap): archive diff/test_logs before reviewer
2026-05-15 housekeep(T-C-001 reviewer non-blocking #1): resolve test path-shim at framework level
2026-05-16 state: reset to framework_build phase per cleanup plan
2026-05-16 framework(Phase 1.2): add claude_client.py — Claude CLI subagent wrapper
2026-05-16 framework(Phase 1.1): add orchestrator.py — 9-stage task lifecycle driver
2026-05-16 framework(Phase 1.5): add test_integrity.py — canonical §14 implementation
2026-05-16 framework(Phase 1.7): add state_write_guard.py — canonical §7 dual-check
2026-05-16 framework(Phase 1.4): add interface_checker.py — §11 6-category detection
2026-05-16 framework(Phase 1.8): add spec_alignment_auditor.py — CEO macro drift check
2026-05-16 framework(Phase 1.6): add 7 missing tool wrappers per §20
2026-05-16 framework(Phase 1.3): add gate_matrix.py + gate_matrix.yaml; deprecate gate_runner
2026-05-16 framework(Phase 2.1): add 5 Track Agent + 1 Generic Reviewer subagent defs
2026-05-16 framework(Phase 2.2): add 6 specialized reviewers + doc-editor subagent defs
2026-05-16 framework(Phase 2.3): add 14th subagent reviewer-spec-alignment
2026-05-16 framework(closeout): delete 14th subagent reviewer-spec-alignment
2026-05-16 framework(closeout): fix /review alignment drifts #1 #2 #3
2026-05-16 framework(closeout): Phase 1.9 — unit tests for Phase 1 modules
2026-05-16 framework(closeout): fix F1+F2+F3 brittleness from test-quality review
2026-05-16 framework(Phase 3.1): T1 scaffold + 2 orchestrator bugs surfaced
2026-05-16 framework(Phase 3.2): Bug-2 fix + 4 more framework gaps surfaced by T1.1
2026-05-16 framework(Phase 3.3): T1.1 + T1.5 GREEN — Bug-6 fixed, T1 happy-path suite complete
2026-05-16 framework(Phase 3.4): T2 hard-gates suite — 10 PASS, 3 deferred (cross-cutting)
2026-05-16 framework(Phase 3.5): T3 blocker-gates suite + Bug-7 (signature semantics)
2026-05-16 framework(Phase 3.6): T4 high-gates suite — 13 PASS, 2 deferred
2026-05-16 framework(Phase 3.7): close T1.2 skip + Bug-8 (reviewer-name normalization)
2026-05-16 framework(Phase 3.8): close T4.12 skip — F4 no_unapproved_external_call wrapper
2026-05-16 framework(Phase 3.9): close T2.11 skip — cross-task tool-failure counter
2026-05-16 framework(Phase 3.10): close T2.12 + T2.13 — run_cross_cutting dispatcher (F3)
2026-05-16 framework(Phase 3.11): T5 reviewer-behaviour suite — 14 PASS, 0 skip
2026-05-16 framework(Phase 3.12): T6 state-lifecycle + resume suite — 10 PASS
2026-05-16 framework(Phase 3.13): T7 escalation suite — 9 PASS + decision validator
2026-05-16 framework(Phase 3.14): T8 upgrade — T8.9 now drives real external_call_audit
2026-05-16 framework(Phase 3.15): T9 interface & schema suite — 8 PASS
2026-05-16 framework(Phase 3.16): T10 + T11 sprint suites + start_sprint dispatcher
2026-05-16 framework(Phase 3.17): close all 4 critical review findings + Gate A signed
2026-05-16 framework(Phase 4): /start-dev skill + orchestrator CLI + Monitor-Mode handoff
2026-05-16 framework(Phase 4 audit): close 2 MAJOR findings + clean docs
2026-05-16 framework(Phase 4.1): relocate /start-dev skill to project-root .claude/skills/
2026-05-16 framework(Phase 5.1+5.2): sprint_plan schema + plan_validator (28/28 tests)
2026-05-17 framework(Phase 5.3+5.4): sprint-planner subagent + dispatch + planning_loop (69 tests)
2026-05-17 framework(Phase 5.5): planner CLI subcommands + 21 unit tests
2026-05-17 framework(Phase 5.6+5.7+5.8): planner e2e (mock + live) + policy/SKILL updates
2026-05-17 framework(Phase 5.10): Phase 5 signoff decision — D-2026-05-17-PLANNER
2026-05-17 test(integration): reset decisions.json fixture to gate-A baseline only
2026-05-17 test(integration): reset sprint.json + escalations.json fixture baselines
2026-05-17 framework(Phase 6.1): detect sprint-wide preflight failure + audit + resume
2026-05-17 test(e2e): fix _REAL_PROJECT_ROOT path arithmetic in live planner test
2026-05-17 chore(state): orchestrator audit trail from 2026-05-17 sprint_1 cycle
2026-05-17 fix(planning_loop): populate plan/sprint IDs in IDLE→EXECUTING recovery
2026-05-17 fix(framework): L4 dispatch permission + L1 delivery-artifact gate
2026-05-17 chore(state): orchestrator audit trail 2026-05-17 dispatch-perm incident
2026-05-17 fix(framework): inbox path bridge + auto-commit for Track Agents (c+e)
2026-05-17 chore(state): discard sprint_1 dev + record c+e fix audit trail
2026-05-17 test(e2e): expand live-test fixture cleanup to track state + archive
2026-05-18 chore: establish TODOS.md convention
2026-05-18 docs: bootstrap-bypass Doc Editor amendment for orbit-agent memory_bank v3.2
2026-05-18 fix(framework): conditional-dispatch for slither + reviewer evidence cap (INC-2026-05-18)
2026-05-18 chore(state): audit trail for 2026-05-18 conditional-dispatch fix cycle
2026-05-18 framework: conditional-dispatch pattern + close 20 audit bugs (Phases 1-4)
2026-05-18 fix(framework): expand subagent env passthrough to include standard Windows vars
2026-05-18 orchestrator: auto-commit T-A-000 round 1 agent delivery
2026-05-18 fix(schemas): allow optional `notes` field in review schemas
2026-05-18 merge task/t-a-000-orchestrated into dev
2026-05-18 fix(framework): word-boundary trigger matching in _detect_escalation_tier
2026-05-18 fix(policy): allow track_a_chain to write .dev/contracts/_registry.json
2026-05-18 fix(policy): extend .dev/contracts/_registry.json allowance to all 5 tracks
2026-05-18 fix(framework): spec_drift_check auto-authorizes APIs in brief-listed files
2026-05-18 orchestrator: auto-commit T-D-001 round 1 agent delivery
2026-05-18 merge task/t-d-001-orchestrated into dev
2026-05-18 fix(framework): forge_test suppresses "0 tests collected" only when brief authorises
2026-05-18 fix(policy): allowlist vendored deps from secret_scan (closes E-0015)
2026-05-18 fix(framework): apply vendored-path skip to 3 more user-content gates
2026-05-18 fix(policy): narrow secret_scan 'seed' keyword to wallet-specific compounds (closes E-0016)
2026-05-18 fix(policy): narrow bare keywords + tighten env-var critical patterns
2026-05-18 fix(framework): mtime-keyed cache invalidation in secret_scanner + plan_validator
2026-05-18 fix(framework): diff_truthfulness handles task-inbox + glob claims (closes E-0019)
2026-05-18 fix(framework): diff_truthfulness exempts already-tracked-unchanged files (class 3 of E-0019)
2026-05-18 fix(schemas): relax evidence_seen.additionalProperties on both review schemas (closes E-0020)
2026-05-18 fix(schemas): preemptive notes+metadata escape valves on 3 agent-written schemas
2026-05-18 feat(harness): advisor-review module — Level 3 hard wall for advisor veto
2026-05-19 fix(harness): resolve_executable Python Scripts fallback + slither --solc passthrough (closes E-0024)
2026-05-19 fix(framework): transition_task_state clears dispatch context on PENDING (closes E-0028)
2026-05-19 docs(plan-v3): pivot L3→RH-Chain+Sepolia+Polygon-Amoy, BREATH soulbound ERC-20, framework freeze
2026-05-19 feat(framework): programmatic hard-wall enforcement for Tier 1 escalations (user-requested)
2026-05-19 docs(todos): capture planning_loop checkpoint restart-time re-validation framework debt
2026-05-19 fix(framework): preflight repopulates base_commit on PENDING re-dispatch (closes sprint_1 stall)
2026-05-19 docs(todos): capture planning_loop _detect_trigger PENDING-tasks-exist gap
2026-05-19 fix(framework): slither abnormal-exit + path_manifest .gas-snapshot (closes sprint_1 T-A-001 r1+r2)
2026-05-19 fix(framework): base.status_from_findings_and_exit gains parsed_clean_output escape valve
2026-05-19 fix(framework): exclude vendored deps from diff.patch (closes E-0031 generic_review:spawn_failed)
2026-05-19 fix(framework): severity-aware spec_drift + round-aware finalize blocking check
2026-05-19 feat(T-A-001): sprint_1 foundation scaffold — EnergyController + TombstoneNFT skeletons
2026-05-19 merge task/t-a-001-orchestrated into dev
2026-05-19 orchestrator: auto-commit T-B-001 round 1 agent delivery
2026-05-19 merge task/t-b-001-orchestrated into dev
2026-05-19 orchestrator: auto-commit T-C-001 round 1 agent delivery
2026-05-19 merge task/t-c-001-orchestrated into dev
2026-05-19 orchestrator: auto-commit T-E-001 round 1 agent delivery
2026-05-19 merge task/t-e-001-orchestrated into dev
2026-05-20 orchestrator: auto-commit T-A-003 round 1 agent delivery
2026-05-20 merge task/t-a-003-orchestrated into dev
2026-05-20 fix(framework): foundry.lock in path_manifest + track-a-chain delivery sequence rule
2026-05-20 Revert "merge task/t-a-003-orchestrated into dev"
2026-05-20 fix(framework): generalize Delivery Sequence hard rule to all 4 non-A track agents
2026-05-20 fix(framework): broaden path_manifest contracts dir + dep-gate in start_sprint
2026-05-20 fix(framework): spec_drift parse_brief numbered bullets + prune method names from PRD entities
2026-05-20 fix(framework): locked_parameter_changed honors PRD authoritative value (F8)
2026-05-20 fix(framework): reviewer JSON resilience + start_sprint lockfile + orphan recovery (P1-P4)
2026-05-20 fix(framework): pass --json-schema to reviewer subprocesses for guaranteed structured output (P5)
2026-05-20 fix(framework): pass --json-schema to sprint-planner too (P5-ext: same fix as reviewers)
2026-05-20 fix(framework): cap reviewer packet diff @30KB + always include task brief (P6+P7)
2026-05-20 revert(framework): drop --json-schema (hangs CLI); keep P1+P2 archive+forgiving-parser
2026-05-20 fix(framework): start_task exception guard around _run_one_round (v17) — prevents mid-state orphan + logs trace to stderr
2026-05-20 fix(framework): claude CLI stream-json output mode for live telemetry (P9, v18)
2026-05-20 fix(framework): specialized_review schema accepts 'informational' severity + extra reviewer fields
2026-05-20 orchestrator: auto-commit T-C-002 round 2 agent delivery
2026-05-20 orchestrator: auto-commit T-C-002 round 3 agent delivery
2026-05-20 merge task/t-c-002-orchestrated into dev
2026-05-20 fix(framework): external_call_audit AST post-filter (v19 F1)
2026-05-20 fix(framework): hard-wall init runs after escalation save (v19 F2)
2026-05-20 fix(framework): spec_drift_check honours glob in brief deliverables (v19 F3)
2026-05-20 orchestrator: auto-commit T-A-002 round 1 agent delivery
2026-05-20 merge task/t-a-002-orchestrated into dev
2026-05-20 fix(framework): external_call_audit blanket-skips documentation files (v20 F4)
2026-05-20 fix(framework): state_manager rejects transitions from terminal states (v20 F5 L3)
2026-05-20 fix(framework): start_sprint + start_task skip terminal-state tasks (v20 F5 L1+L2)
2026-05-20 recover(state): restore T-C-002 to COMPLETED after F5 framework bug (v20 F6)
2026-05-21 fix(framework): state_write_guard recognises actor='advisor_recovery' (v20 F7)
2026-05-21 recover(state): rewrite advisor entries → advisor_recovery + resolve E-0044 (v20 F8)
2026-05-21 orchestrator: auto-commit T-E-002 round 1 agent delivery
2026-05-21 merge task/t-e-002-orchestrated into dev
2026-05-21 feat(policy): slither response playbook (v22 L1)
2026-05-21 feat(framework): round-aware task brief addendum generator (v22 L2 + L2.5)
2026-05-21 feat(framework): slither_runner reports suppression usage + ungrounded annotations (v22 L3)
2026-05-21 recover(state): resolve E-0042 + reset T-A-003 → PENDING for v22 re-dispatch
2026-05-21 chore: ignore .claude/settings.local.json + reset T-A-003 for re-dispatch
2026-05-21 orchestrator: auto-commit T-A-003 round 1 agent delivery
2026-05-21 merge task/t-a-003-orchestrated into dev
2026-05-21 orchestrator: auto-commit T-A-004 round 1 agent delivery
2026-05-21 merge task/t-a-004-orchestrated into dev
2026-05-21 feat(framework): state_manager.reset_task() operator recovery API (v21)
2026-05-21 fix(policy): track_c_sim may write .dev/contracts/calibration_params*.json
2026-05-21 docs: SETUP_CHECKLIST.md for external service registration
2026-05-21 orchestrator: auto-commit T-D-002 round 1 agent delivery
2026-05-21 merge task/t-d-002-orchestrated into dev
2026-05-21 fix(policy): track_b_backend may write engine_signal* + decision_record* schemas (E-0046)
2026-05-21 fix(framework): _latest_calibration_run_dir supports flat layout + skips plots (E-0047)
2026-05-21 feat(policy): gate_input_schema.yaml + Track B fixtures allowlist (v23 L1 + E-0049)
2026-05-21 feat(framework): v23 L2 calibration playbook + addendum + parser fix (E-0047/E-0048)
2026-05-21 feat(framework): sprint-planner gate input awareness + plan_validator warnings (v23 L3)
2026-05-21 state(recover): resolve E-0048+E-0049 + reset T-C-003/T-B-002 + manual brief addendum
2026-05-22 revert(state): remove T-C-003 brief addendum + document split-task arch in TODOS (v24 F1+F3)
2026-05-22 fix(framework): backtest_validator lifetimes.jsonl-missing softened to LOW (v24 F2)
2026-05-22 orchestrator: auto-commit T-B-002 round 1 agent delivery
2026-05-22 feat(framework): close path_allowlist feedback loop (v25 F1-F4 — E-0050 fix)
2026-05-22 merge task/t-b-002-orchestrated into dev
2026-05-22 orchestrator: auto-commit T-B-003 round 1 agent delivery
2026-05-22 merge task/t-b-003-orchestrated into dev
2026-05-22 docs(todos): capture sprint driver enumeration bug as v26 candidate
2026-05-22 fix(framework): align calib_objectives gate with task_brief budget (v25.1 — E-0051)
2026-05-22 fix(framework): accept nested archetype shape + title-first addendum matcher (v25.2 — E-0052)
2026-05-22 fix(framework): schema SSOT + semantic-tolerant gates (v26 — class fix for E-0050~E-0053)
2026-05-22 orchestrator: auto-commit T-C-003 round 1 agent delivery
2026-05-22 fix(framework): hard-wall escalation enforcement — close 4-time bypass loophole (v27)
2026-05-22 docs(todos): v27 hard-wall bypass postmortem captured
2026-05-22 orchestrator: auto-commit T-A-005 round 1 agent delivery
2026-05-22 orchestrator: auto-commit T-B-004 round 1 agent delivery
2026-05-22 orchestrator: auto-commit T-B-004 round 2 agent delivery
2026-05-22 fix(framework): Track B path_manifest amendments per E-0056 advisor (v28 F1 — TODO #13)
2026-05-22 fix(framework): advisor autotrigger race condition (v28 F2 — TODO #9)
2026-05-22 feat(framework): sprint driver re-scan PENDING after batch (v28 F3 — TODO #2)
2026-05-22 fix(framework): spec_drift codebase-scope presence check (v28 F4 — TODO #10)
2026-05-22 fix(framework): calib_objectives run-dir resolver — canonical-set + mtime (v28 F5 — TODO #11)
2026-05-22 fix(framework): interface_checker visibility filter (v28 F6 — TODO #12)
2026-05-22 docs(cleanup): clarify dev/prod LLM split — Gemini 3.1 Flash Lite for production (v29)
2026-05-23 fix(framework): state_write_guard recognises advisor_autotrigger actor (v30 — E-0057)
2026-05-23 orchestrator: auto-commit T-B-005 round 1 agent delivery
2026-05-23 orchestrator: auto-commit T-B-005 round 2 agent delivery
2026-05-23 orchestrator: auto-commit T-A-006 round 1 agent delivery
2026-05-23 merge task/t-a-006-orchestrated into dev
2026-05-23 orchestrator: auto-commit T-D-003 round 1 agent delivery
2026-05-23 merge task/t-d-003-orchestrated into dev
2026-05-23 orchestrator: auto-commit T-B-006 round 1 agent delivery
2026-05-23 merge task/t-b-006-orchestrated into dev
2026-05-23 orchestrator(v32 F1): preserve manual task_brief_addendum across rounds
2026-05-23 orchestrator: auto-commit T-B-008 round 1 agent delivery
2026-05-23 state_manager(v32 F5): preserve round across reset+redispatch
2026-05-23 state_manager(v32 F1): reset_task cleans stale branch+worktree and preserves round
2026-05-23 orchestrator(v32 F4): addendum formatters for 5 remaining hard gates
2026-05-23 orchestrator(v32 F3): classify agent exit kind + transient-aware duplicate detection
2026-05-23 orchestrator(v32 F2): sprint driver settle loop for open escalations
2026-05-23 tests(v32): adapt pre-existing fixtures to F2+F3 signature changes
2026-05-23 orchestrator: auto-commit T-D-004 round 1 agent delivery
2026-05-23 merge task/t-d-004-orchestrated into dev
2026-05-23 orchestrator: auto-commit T-A-007 round 1 agent delivery
2026-05-23 orchestrator: auto-commit T-A-008 round 1 agent delivery
2026-05-23 merge task/t-a-008-orchestrated into dev
2026-05-23 orchestrator: auto-commit T-B-009 round 1 agent delivery
2026-05-23 merge task/t-b-009-orchestrated into dev
2026-05-23 orchestrator: auto-commit T-A-009 round 1 agent delivery
2026-05-23 orchestrator: auto-commit T-A-009 round 2 agent delivery
2026-05-23 merge task/t-a-009-orchestrated into dev
2026-05-23 orchestrator: auto-commit T-B-010 round 1 agent delivery
2026-05-24 orchestrator: auto-commit T-D-005 round 1 agent delivery
2026-05-24 merge task/t-d-005-orchestrated into dev
2026-05-24 state_manager(v33 F1): advisor_mark_completed actually merges branch to dev
2026-05-24 backfill v33: merge advisor-released task/t-b-005-orchestrated into dev
2026-05-24 backfill v33: merge advisor-released task/t-b-008-orchestrated into dev
2026-05-24 backfill v33: merge advisor-released task/t-a-007-orchestrated into dev
2026-05-24 backfill v33: merge advisor-released task/t-b-010-orchestrated into dev
2026-05-24 fix(test): Phase3 tests adopt TombstoneNFT v0.2.0 API (v33 backfill fallout)
2026-05-24 backfill v33: merge T-A-005 (DeployCalibrated + CalibratedConstants + 3-chain configs) into dev
2026-05-24 fix(script): DeployCalibrated adopts TombstoneNFT v0.2.0 API (v33 backfill)
2026-05-24 backfill v33: merge T-B-004 (phase1_runner + training set builder) into dev
2026-05-24 backfill v33: merge T-C-003 (sim sweeper + Bayes opt + calibration validation tests) into dev
2026-05-24 chore(gas): regenerate .gas-snapshot post v33 backfill (T-A-005 + T-A-007 contracts)
2026-05-24 orchestrator(v33 F5b): run_task for-loop starts at state.round
2026-05-24 orchestrator(v33 F1b): addendum preserve→combine (prepend auto, keep manual)
2026-05-24 orchestrator(v33 F2b): settle-post-rescan covers state=None DEFERRED + refresh stale cache
2026-05-24 orchestrator: auto-commit T-B-011 round 1 agent delivery
2026-05-24 merge task/t-b-011-orchestrated into dev
2026-05-24 orchestrator: auto-commit T-D-006 round 1 agent delivery
2026-05-24 orchestrator: auto-commit T-D-006 round 2 agent delivery
2026-05-24 merge task/t-d-006-orchestrated into dev
2026-05-24 orchestrator: auto-commit T-A-010 round 1 agent delivery
2026-05-24 orchestrator: auto-commit T-A-010 round 3 agent delivery
2026-05-24 merge task/t-a-010-orchestrated into dev
2026-05-24 orchestrator: auto-commit T-B-013 round 1 agent delivery
2026-05-24 merge task/t-b-013-orchestrated into dev
2026-05-24 orchestrator: auto-commit T-D-007 round 2 agent delivery
2026-05-24 orchestrator: auto-commit T-D-007 round 3 agent delivery
2026-05-24 merge task/t-d-007-orchestrated into dev
2026-05-24 orchestrator: auto-commit T-B-012 round 1 agent delivery
2026-05-24 orchestrator: auto-commit T-B-012 round 2 agent delivery
2026-05-24 merge task/t-b-012-orchestrated into dev
2026-05-25 plan_validator(v34 F3): report ALL schema violations not just first
2026-05-25 preflight(v34 F4): safe_remove_worktree retry-with-backoff on Windows
2026-05-25 orchestrator(v34 F2): cache refresh + state=None in regular rescan loop
2026-05-25 claude_client(v34 F1): post-delivery hang watchdog for track agents
2026-05-25 diff_validator(v34 F5): duplicate-content detection for added files
2026-05-25 claude_client(v34 F7): process-tree teardown for subagent kills
2026-05-25 journal(v34 F8): optional state-change notification hook
2026-05-25 orchestrator(v34 F2 followup): narrow state=None acceptance
2026-05-25 deploy: ship Autopoiesis to Robinhood Chain + Arbitrum Sepolia
2026-05-25 TODOS: add 3 new items from sprint 7 planning reviews
2026-05-25 lookahead_auditor(sprint7-prep): diff-aware scope via changed_files
2026-05-25 docs: migrate PRD/TECHNICAL_PLAN/DEV_FRAMEWORK into code/docs/ (version-controlled)
2026-05-25 state: fix Codex review P1 (planning_loop checkpoint stuck) + P2 (stale open_count)
2026-05-25 sprint_7: persist + approve + commit plan (6 tasks; tennis pivot)
2026-05-25 orchestrator: auto-commit T-E-003 round 1 agent delivery
2026-05-25 merge task/t-e-003-orchestrated into dev
2026-05-25 orchestrator: auto-commit T-C-004 round 1 agent delivery
2026-05-25 orchestrator: auto-commit T-C-004 round 2 agent delivery
2026-05-25 merge task/t-c-004-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-B-014 round 1 agent delivery
2026-05-26 merge task/t-b-014-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-B-015 round 1 agent delivery
2026-05-26 merge task/t-b-015-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-D-008 round 1 agent delivery
2026-05-26 merge task/t-d-008-orchestrated into dev
2026-05-26 T-B-016: sprint_7 Day-6 closer — Phase 2 dry-run + SUBMISSION regen
2026-05-26 merge task/t-b-016-orchestrated into dev
2026-05-26 sprint_8 enablement: planner cwd bug fix + sandbox path carve-outs
2026-05-26 framework state sync: sprint 7 archive + state churn from sprint 8 planning
2026-05-26 orchestrator: auto-commit T-B-017 round 1 agent delivery
2026-05-26 merge task/t-b-017-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-B-018 round 1 agent delivery
2026-05-26 merge task/t-b-018-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-B-019 round 1 agent delivery
2026-05-26 merge task/t-b-019-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-B-020 round 1 agent delivery
2026-05-26 merge task/t-b-020-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-D-009 round 1 agent delivery
2026-05-26 orchestrator: auto-commit T-D-009 round 2 agent delivery
2026-05-26 merge task/t-d-009-orchestrated into dev
2026-05-26 orchestrator: auto-commit T-B-021 round 1 agent delivery
2026-05-26 merge task/t-b-021-orchestrated into dev
2026-05-26 sprint_8 framework state sync: all 6 tasks COMPLETED + 5 advisor escalations resolved
2026-05-27 sprint_9 direction lock: D-S09-001 (live product + LLM L1+L2 wire + L3 scaffolding)
2026-05-27 sprint_9 wire: add GEMINI_API_KEY + prod runtime creds to env passthrough
2026-05-27 framework state sync after T-B-022 kill + restart (sprint_9 setup)
2026-05-27 archive T-B-022 round 1+2 from killed run (sprint_9 restart)
2026-05-27 sprint_9 path_manifest carve-outs: backtest engine + FastAPI server + L3 schema
2026-05-27 orchestrator: auto-commit T-B-022 round 1 agent delivery
2026-05-27 merge T-B-022 — sprint_9 Day 0 Gemini live spike (manual finalize after run kill artifact)
2026-05-27 T-B-022 → COMPLETED (manual advance after run-kill artifact)
2026-05-27 T-B-022 archive: sync successful-run artifacts (transcript + events)
2026-05-27 state_write_guard: add 'operator' actor with required justification
2026-05-27 T-B-023 E-0080 resolution: advisor doc verdict updated to PARTIAL_RESOLVE + state_write_guard patch
2026-05-27 orchestrator: auto-commit T-B-024 round 1 agent delivery
2026-05-27 merge task/t-b-024-orchestrated into dev
2026-05-27 orchestrator: auto-commit T-B-025 round 1 agent delivery
2026-05-27 merge task/t-b-025-orchestrated into dev
2026-05-27 orchestrator: auto-commit T-B-026 round 1 agent delivery
2026-05-27 orchestrator: auto-commit T-B-026 round 2 agent delivery
2026-05-27 merge task/t-b-026-orchestrated into dev
2026-05-27 orchestrator: auto-commit T-B-027 round 1 agent delivery
2026-05-27 orchestrator: auto-commit T-B-027 round 2 agent delivery
2026-05-27 merge task/t-b-027-orchestrated into dev
2026-05-27 orchestrator: auto-commit T-D-010 round 1 agent delivery
2026-05-27 orchestrator: auto-commit T-D-010 round 2 agent delivery
2026-05-27 orchestrator: auto-commit T-D-010 round 4 agent delivery
2026-05-27 merge task/t-d-010-orchestrated into dev
2026-05-27 orchestrator: auto-commit T-B-028 round 1 agent delivery
2026-05-27 orchestrator: auto-commit T-B-028 round 2 agent delivery
2026-05-27 merge task/t-b-028-orchestrated into dev
2026-05-27 sprint_9 framework state sync: all 8 tasks COMPLETED + 5 advisor escalations
2026-05-28 deploy: root-level railway.toml + .dockerignore for Railway GitHub deploy
2026-05-28 dockerignore: stop excluding data/ (Dockerfile needs to COPY it)
2026-05-28 Dockerfile: add numpy + pandas + pyarrow to runtime deps
2026-05-28 dashboard: placeholder snapshot for memoryBank import + drop legacy @secret
2026-05-28 dashboard: fix memoryBank snapshot import to stay inside dashboard/ root
2026-05-28 dashboard: upgrade next 15.0.3 -> 15.5.18 (CVE-2025-66478)
2026-05-28 vercel: installCommand fallback for legacy peer deps
2026-05-28 sprint_10 direction lock: D-S10-001 (L3 implementation + auth proxy)
2026-05-28 orchestrator: auto-commit T-D-011 round 1 agent delivery
2026-05-28 orchestrator: auto-commit T-D-011 round 2 agent delivery
2026-05-28 merge task/t-d-011-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-B-029 round 1 agent delivery
2026-05-28 merge task/t-b-029-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-B-030 round 1 agent delivery
2026-05-28 orchestrator: auto-commit T-B-030 round 2 agent delivery
2026-05-28 merge task/t-b-030-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-B-031 round 1 agent delivery
2026-05-28 merge task/t-b-031-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-D-012 round 1 agent delivery
2026-05-28 merge task/t-d-012-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-B-032 round 1 agent delivery
2026-05-28 merge task/t-b-032-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-D-013 round 1 agent delivery
2026-05-28 merge task/t-d-013-orchestrated into dev
2026-05-28 orchestrator: auto-commit T-B-033 round 1 agent delivery
2026-05-28 merge task/t-b-033-orchestrated into dev
2026-05-29 railway: force numReplicas=1 (in-memory state is per-process)
2026-05-29 seed: 42 closed Polymarket tennis markets in data/backtest/cache/
2026-05-29 fix cache location: move from data/backtest/cache/ to agent/backtest/_cache/
2026-05-29 dashboard: adapter for backend's sprint_9 sweep shape
2026-05-29 sprint_11 direction lock: D-S11-001 (backlog cleanup, 7 tasks)
2026-05-29 orchestrator: auto-commit T-B-034 round 1 agent delivery
2026-05-29 merge task/t-b-034-orchestrated into dev
2026-05-29 T-B-035: schema bump for BetSettlement (sprint 11 replay PnL)
2026-05-29 framework state sync after sprint 11 T-B-034/035 cycle
2026-05-29 orchestrator: auto-commit T-B-036 round 1 agent delivery
2026-05-29 merge task/t-b-036-orchestrated into dev
2026-05-29 orchestrator: auto-commit T-B-037 round 1 agent delivery
2026-05-29 orchestrator: auto-commit T-B-037 round 2 agent delivery
2026-05-29 merge task/t-b-037-orchestrated into dev
2026-05-29 orchestrator: auto-commit T-D-015 round 1 agent delivery
2026-05-29 orchestrator: auto-commit T-D-015 round 2 agent delivery
2026-05-29 merge task/t-d-015-orchestrated into dev
2026-05-29 orchestrator: auto-commit T-B-038 round 1 agent delivery
2026-05-29 merge task/t-b-038-orchestrated into dev
2026-05-29 orchestrator: auto-commit T-D-016 round 3 agent delivery
2026-05-29 merge task/t-d-016-orchestrated into dev
2026-05-29 sprint_11 framework state sync: all 7 tasks COMPLETED + ~5 advisor escalations
2026-05-29 sprint_12 direction lock: D-S12-001 (PnL ledger fix + label preservation, 3 tasks)
2026-05-29 sprint_12 T-B-039 + T-B-040: PnL ledger projection + label preservation
2026-05-29 sprint_12 T-B-039 follow-up: synthetic-outcome fallback for legacy cache
2026-06-01 orchestrator: auto-commit T-B-041 round 1 agent delivery
2026-06-01 T-B-041 round 2 cleanup: revert docs/DEPLOYMENT.md (out of Track B allowlist; T-D-018 will land the env-var docs)
2026-06-01 merge task/t-b-041-orchestrated into dev (sprint_13 T-B-041 prod loop factory)
2026-06-01 orchestrator: auto-commit T-B-042 round 1 agent delivery
2026-06-01 merge task/t-b-042-orchestrated into dev
2026-06-01 orchestrator: auto-commit T-B-043 round 1 agent delivery
2026-06-01 merge task/t-b-043-orchestrated into dev
2026-06-01 orchestrator: auto-commit T-D-018 round 1 agent delivery
2026-06-01 merge task/t-d-018-orchestrated into dev
2026-06-01 sprint_13 closure: prod loop + RhChainAdapter + boot smoke + dashboard live verify (4 tasks all COMPLETED)
2026-06-03 prod loop: wire real pseudo-bet path (mock orders + real Polymarket settlement)
2026-06-04 prod loop: imports + consuming/applied constants for PROMOTE pipeline
2026-06-04 prod loop: _reset_durable_life + _consume_staged_config helpers (PROMOTE pipeline)
2026-06-04 prod loop: _consume_staged_config tests (TOCTOU + orphan recovery)
2026-06-04 prod loop: consume promoted config in factory (PROMOTE → live cold-start)
2026-06-04 prod loop: rigorous TOCTOU test (racing-configure mid-consume, fails on read-then-rename)
2026-06-08 chore: green all preflight gates on newer toolchain (mypy 2.1 / ruff 0.15 / py3.14)
2026-06-08 feat(backtest): real per-match tennis cassette fetcher + 1569-market dataset
2026-06-08 feat(backtest): --start-offset resume + grow tennis dataset to 4823 markets
2026-06-08 feat(backtest): config sweep (find_optimal_config) + sizing params; ignore cassettes
2026-06-08 docs(plans): real-signal-source + layer2-self-evolution implementation plans
2026-06-08 data(sackmann): vendor full 2024-2026 ATP/WTA corpus into a separate dir
2026-06-08 data(sackmann): pin vendored corpus to LF (idempotent re-vendor)
2026-06-08 feat(backtest): tennis slug parser (players + surface)
2026-06-08 feat(backtest): Sackmann name->id index + slug resolver
2026-06-08 feat(backtest): resolver from SackmannLoader + coverage report (resolved 4023/7494)
2026-06-08 fix(backtest): coverage CLI reads full corpus dir (53.7% mixed -> 65.8% offline)
2026-06-08 docs(plan1): thread DEFAULT_CORPUS_DIR through B2/D1 real-signal loader (A0 correction)
2026-06-08 fix(backtest): surface keyword matches on segment boundary, not substring
2026-06-08 feat(backtest): pure sync momentum signal helper
2026-06-08 feat(backtest): RealSignalSource with real momentum + neutral tennis
2026-06-08 feat(backtest): run_replay accepts a signal_source_factory (real-signal seam)
2026-06-08 feat(backtest): 4 Sackmann facet signal normalizers (elo/surface/h2h/rest)
2026-06-08 feat(backtest): wire 4 Sackmann facets into RealSignalSource via resolver
2026-06-08 feat(server): GENESIS_REAL_SIGNALS flag wires RealSignalSource into mock-bet loop
2026-06-08 feat(backtest): run_sweep helper + --real flag point find_optimal_config at RealSignalSource
2026-06-08 test(backtest): type D2 run_sweep factory wrapper Callable, not object (mypy --strict)
2026-06-09 docs(plan): signal-cached config sweep (replaces D3 replay-per-config at scale)
2026-06-09 feat(backtest): add compute_bet_pnl faithful PnL mirror for cached sweep
2026-06-09 feat(backtest): SignalRow + precompute_rows for cached config sweep
2026-06-09 feat(backtest): cached sweep row_to_signals + score_config (T3)
2026-06-09 feat(backtest): add run_cached_sweep + save/load + CLI to cached_sweep
2026-06-09 fix(server): thread shared RuntimeAgentRunner into loop factory (approval deltas now reach the loop)
2026-06-09 feat(server): GENESIS_REAL_STRATEGY_ADVISOR un-stubs the real L3 StrategyAdvisorImpl
2026-06-09 feat(weights): add update_from_settlement gradient entrypoint (direction-aware credit assignment)
2026-06-09 feat(backtest): add _SettlementLearningWeightUpdater adapter (poller -> real WeightUpdater)
2026-06-09 feat(learning): thread signal_scores + bet_direction end-to-end; wire prod settlement learning behind GENESIS_REAL_LEARNING
2026-06-09 feat(backtest): cached_sweep --min-bets gate (per-bet Sharpe ignores tiny samples)
2026-06-09 docs(backtest): real-signal config sweep results (optimal seed config)
2026-06-09 feat(backtest): settlement-learning parity (enable_settlement_learning + terminal_weights)
2026-06-09 docs(plan): MASTER plan — Autopoiesis competition site (4 pages + L5/L6 backend)
2026-06-09 docs(plan): competition-site MASTER plan converged (plan-loop, 10 codex rounds)
2026-06-09 feat(dashboard): Phase C — abyssal design system + Roadmap landing page
2026-06-09 fix(dashboard): roadmap readability + Robinhood Chain L2 branding
2026-06-09 A0: SurvivalRow joined schema (signals + settlement + entry_asof + players/surface)
2026-06-09 feat(survival): A1 entry-time-ordered cached tick source + controllable clock
2026-06-09 fix(survival): A1 schedule edge cases — tied entries, checkpoint overshoot, clock drift
2026-06-09 A2: multi-life FRESH-loop respawn driver for the L5 survival season
2026-06-09 fix(survival): A2 test mypy --strict clean — annotate tmp_path + outcome literal
2026-06-09 A3: survival recorder + journey schema + frozen static baseline + fragile-seed calibration
2026-06-09 test(A3): make loss-multiplier test actually verify amplification
2026-06-09 A4: CLI + export to dashboard/public/backtest/survival_journey.json
2026-06-09 docs(backtest): L5 survival-season results — dies 6x then learns to survive
2026-06-09 feat(dashboard): D1 static_sweep.json data contract + validated loader
2026-06-09 feat(dashboard): D2 — restyle /backtest to abyssal sweep showpiece
2026-06-09 feat(dashboard): E1 — survival journey loader + adapter (codex M5)
2026-06-09 fix(dashboard): E1 — scrubber boundary field-name mismatch (codex M5 review)
2026-06-09 fix(dashboard): E1 — unify PnLBaselineChart line/dot/legend color (codex M5 review)
2026-06-09 feat(dashboard): E2 — /survival STAR page (auto-play, tombstones, vitals, bet feed)
2026-06-09 fix(dashboard): /survival charts abyss-native via additive variant prop
2026-06-09 fix(dashboard): paint abyss page root #060d0b so fixed-bg never flashes navy
2026-06-09 feat(runtime): B1 — fold recent reflections into the L3 advisor window
2026-06-09 feat(runtime): B2 — wire BOTH real reflection AND real advisor for L6 (default OFF)
2026-06-10 test(backtest): B3 — reflection-event journey annotation + L6-closure tests
2026-06-10 feat(backtest): opt-in AI mode for the survival season (L6 reflect->optimize)
2026-06-10 feat(dashboard): E-toggle — numerical vs AI survival journey switch on /survival
2026-06-10 fix(backtest): AI-mode preflight guard + live-entry tests (review M1/M2/L3)
2026-06-10 feat(contract): F0 — dashboard_ws_message v0.3.0 (market_id+bet_id+signals on decision & decision_feed)
2026-06-10 fix(contract): F0-polish — producer minLength parity + NO_BET telemetry test + runbook
2026-06-10 feat(dashboard): F1 — L5-complete gate for the mock-bet stage
2026-06-10 feat(dashboard): F2 — /mock live mock-bet page (abyss, reuses live widgets, v0.3.0 signals)
2026-06-10 fix(dashboard): F2 follow-up — restore /live navy borders + abyss-theme DeathWatch takeover + gate survival next-link
2026-06-10 refactor(dashboard): G1 — shared lifeline StageShell + nav across the 4 abyss pages
2026-06-10 chore(dashboard): G2 — redirect legacy /workshop + /playback to the lifeline
2026-06-10 feat(dashboard): G3 — responsive breakpoints for the 4 abyss pages + live widgets
2026-06-10 feat(dashboard): G4 — a11y (reduced-motion coverage, focus rings, aria, skip-link, contrast)
2026-06-10 chore(dashboard): G follow-up — fix folded-route e2e specs + /mock dim back-link + root-redirect test
2026-06-10 feat(llm): MiniMax client + Gemini->MiniMax fallback chain (default OFF)
2026-06-10 fix(llm): MiniMaxClient robust JSON extraction for M3 reasoning (<think>) output
2026-06-10 fix(llm): fallback passes own model to MiniMax + per-call httpx client (cross-loop) + 120s timeout
2026-06-10 fix(llm): GeminiClient request timeout + per-call client (cross-loop) + switch default model to gemini-3.5-flash
2026-06-10 feat(dashboard): /mechanism — project explainer (competition, data, engines, lifecycle, learning, params)
2026-06-10 T-D-018: strict weight_delta advisor + applicability gate + hard zero-delta invariant
2026-06-10 plan-loop iter 2: address Codex diff-review (UNRESOLVED=4)
2026-06-10 plan-loop iter 3: address Codex re-review (UNRESOLVED=1) + ruff agent/llm
2026-06-10 plan-loop iter 4: complete the gemini-3.5-flash doc sweep (Codex re-review)
2026-06-11 deploy(dashboard): trace survival journeys into /survival fn + .vercelignore
2026-06-11 realism rules: entry-price floor 0.05 + per-bet PnL cap $100 (numerical + AI shared)
2026-06-11 plan-loop iter 2: sweep the remaining old steps/total_steps equality lock-ins
2026-06-11 chore: gitignore the preserved run1 journey snapshots (finetune exhibits)
2026-06-11 feat(dashboard): finetune log + archived run1 toggle on /survival
2026-06-11 obs(llm): include the exception MESSAGE in the fallback warning
2026-06-11 fix(llm): Gemini timeout 15s -> 45s (deadline too tight for big generations)
2026-06-11 chore: gitignore the Gemini-only provider-comparison journey artifact
2026-06-11 feat(dashboard): Gemini provider-comparison leg on /survival (run 3)
2026-06-11 i18n(dashboard): translate all page-visible Chinese to English
```

