# Genesis Dev Framework v1.1

> 配套文档：[PRD.md](./PRD.md) | [TECHNICAL_PLAN.md](./TECHNICAL_PLAN.md)
> 状态：待确认 / Day 1–3 据此搭建

---

## 0. 设计哲学

> **LLM 不被信任执行流程；Python 强制执行流程。LLM 只在被点名时做擅长的事（写代码、写评审、写叙述）。**

凡是需要纪律的地方（测试运行、round counter、状态写入、reviewer 调度、escalation 触发），由 Python 代码强制；LLM 只在 Python 调用它时执行任务。

---

## 1. Process Invariants（写进 `.dev/README.md` 顶部）

```
1.  No task starts with dirty working tree.
2.  No LLM writes .dev/state/**.
3.  No task modifies paths outside its allowlist.
4.  No reviewer runs before hard gates pass.
5.  No task completes without archived diff, logs, and verdict.
6.  No irreversible action happens without human approval.
7.  No interface change lands without integration matrix update.
8.  No tests may be deleted or weakened without escalation.
9.  No secret may appear in diff.
10. No task may pass on agent self-report alone.
```

任何违反 → 立即 escalate。

---

## 2. 四层职责层级

```
┌────────────────────────────────────────────────────┐
│  Layer 1: User (你)                                 │
│  - 战略决策、escalation 终审、产品方向              │
└──────────────────────┬─────────────────────────────┘
                       │ 战略对话
                       ▼
┌────────────────────────────────────────────────────┐
│  Layer 2: Project Advisor (main thread / Claude)   │
│  - 跟 User 对话                                      │
│  - 监督整体方向与系统健康                            │
│  - 维护 PRD/TECHNICAL_PLAN 文档版本                  │
│  - Architecture Drift Check                          │
│  - escalation 一线接收 → 跟 User 讨论                │
│  - 不写业务代码 / 不 spawn agent / 不进 loop         │
│  - 可以提出 PRD / TECHNICAL_PLAN / issues 修改建议；  │
│    是否实际写入由 User 或 Orchestrator-controlled    │
│    doc task 执行                                     │
└──────────────────────┬─────────────────────────────┘
                       │ 启动 + 监督
                       ▼
┌────────────────────────────────────────────────────┐
│  Layer 3: Python Orchestrator（真 CEO）             │
│  code/.dev/harness/orchestrator.py                  │
│  - 程式化调度（不可商量的规则）                      │
│  - 强制 gates、运行测试、解析 verdict                │
│  - 维护 round counter、escalation 触发               │
│  - 写 state 文件（atomic + journal）                 │
│  - LLM 无法跳过它的检查                              │
└──────────────────────┬─────────────────────────────┘
                       │ Claude Agent SDK 调用
                       ▼
┌────────────────────────────────────────────────────┐
│  Layer 4: 13 个 LLM Agents（每次 fresh context）    │
│  = 原 12 个 + 1 个 Doc Editor                        │
│                                                     │
│  5 Track Agents (执行)：                            │
│    Track A Chain, B Backend, C Sim, D FE, E Data    │
│                                                     │
│  6 Specialized Reviewer Agents (领域评审)：         │
│    Contract Security / Calibration Validator /      │
│    ML Validator / CrossChain Reconciliation /        │
│    Polymarket Smoke / Demo Readiness                │
│                                                     │
│  1 Generic Reviewer (通用 5 维 review)              │
│                                                     │
│  +1 Doc Editor (新增)                               │
│    Orchestrator-controlled，仅在 Advisor 通过       │
│    PRD/TECHNICAL_PLAN/Interface 修改建议且 User     │
│    批准时被 spawn，专门把改动落档到 doc 文件。       │
│    详见 §24 Doc Editor Agent Permissions。           │
└────────────────────────────────────────────────────┘
```

---

## 3. Track Agents（执行者）

| Track | 职责范围 | 边界 | 主要产出 |
|---|---|---|---|
| **A: Chain** | Solidity 合约 + Foundry 测试 + 部署脚本 | 不碰 Python / Frontend | `.sol` + `.t.sol` + deploy logs |
| **B: Backend** | Python agent runtime + 5 引擎 + Polymarket executor + LLM Reflection | 不写合约源码（只调 ABI） | `agent/**/*.py` + pytest results |
| **C: Sim** | Layer 2 校准框架 + Monte Carlo + Bayesian | 不依赖真链 / Polymarket | `sim/**/*.py` + `CALIBRATION_REPORT.md` |
| **D: Frontend** | Dashboard Next.js + viem + WebSocket | 不实现后端逻辑 | `dashboard/**/*.tsx` + screenshots |
| **E: Data** | NBA / Reddit / Polymarket / Polygon scan ETL；子模块：`market_data` / `nba_stats` / `social_sentiment` / `chain_indexer` | 只负责 raw/processed data，不做 prediction / bankroll / settlement | `data/**/*.py` + parquet files |

> **Source of briefs (Phase 5+)**: task briefs are authored by the
> `sprint-planner` L4 subagent (see §4.6) and committed by the Python
> orchestrator's `planning_loop` after User approval. Track Agents do
> NOT receive hand-authored briefs in steady-state operation; manually
> authored briefs are an emergency override only and trigger a Tier 3
> warning in `spec_alignment_auditor` if detected.

---

## 4. Reviewer Agents

### 4.1 Generic Reviewer（5 维通用评分）

| 维度 | 评分 1–10 |
|---|---|
| Alignment with spec | ≥ 8 PASS |
| Correctness | ≥ 8 PASS |
| Completeness（含测试覆盖） | ≥ 8 PASS |
| Security / Safety | ≥ 8 PASS |
| Integration（跨 track 接口） | ≥ 8 PASS |

任何一项 < 8 → FAIL，回 Track Agent。

### 4.2 Specialized Reviewers

| Reviewer | 触发 | 检查重点 |
|---|---|---|
| **Contract Security** | Track A 合约改动 / EIP-712 改动 | reentrancy / access control / EIP-712 domain / signature replay / event 完整 / donation cap bypass / Terminal donation 拒收 / settlement replay / knownBetIds / phase gating / death idempotency / tombstone mint once |
| **Calibration Validator** | Track C 参数集 commit | Monte Carlo 收敛（CI 宽度）/ 14 项 GOOD_CALIBRATION objectives / Pareto / 单 archetype 过拟合 / KS test / 敏感性分析 / random seed reproducibility / parameter file hash / sample size / archetype coverage |
| **ML Validator** | Track B 权重学习 / 训练特征 commit | **Look-ahead bias 检测**（关键）/ train-val 时序 / 收敛曲线无 NaN / per-feature ablation / 过拟合 / market close time leakage / settlement result leakage / odds movement after decision time / feature timestamp alignment |
| **Cross-Chain Reconciliation** | Track A 跨链改动 + Track B oracle 改动 | `breath ≡ f(history)` 不变量 / bankroll mirror monotonic nonce / attestation timestamp freshness / duplicate settlement / Polygon event vs L3 state derived breath / Terminal cutoff betPlacedAt / oracle signer rotation |
| **Polymarket Integration Smoke** | Track B Polymarket executor | **dry smoke**（fetch markets / construct order / sign locally / validate response format）+ **live smoke**（tiny order，需 human approval） |
| **Demo Readiness Auditor** | 任何 user-facing 改动 | 服务 Demo moment / 视觉一致 / 叙事 coherence / Phase 切换可视性。**不可 override security / correctness** |

### 4.6 Strategic agents — sprint-planner (Phase 5+)

The 7 reviewer agents above are *operational* — they evaluate work that
has been done. There is ALSO a category of **strategic** L4 agent that
decides what work to do next. As of Phase 5 there is exactly one
strategic agent:

**`sprint-planner`** (`.claude/agents/sprint-planner.md`)

- **Role**: read PRD.md, TECHNICAL_PLAN.md, current state files, drift
  findings, and past decisions; emit a structured SprintPlan JSON
  document specifying the next sprint (task decomposition, deps,
  per-task briefs, rationale).
- **Tools**: Read, Grep, Glob. NO Write, Edit, Bash, Agent, WebFetch.
  The planner emits exactly ONE JSON document on stdout; the
  orchestrator's planning_loop is the only entity that materialises it
  to disk.
- **Triggers** (per `ReplanReason` enum in `harness/planning_loop.py`):
    1. `project_start` — no current sprint exists
    2. `sprint_boundary` — all tasks in current sprint reached terminal
    3. `task_completed_with_drift` — task COMPLETED + drift finding
    4. `escalation_resolved` — User resolved a paused escalation
    5. `spec_alignment_drift` — `spec_alignment_auditor` wrote a new finding
    6. `user_initiated` — User ran `replan` / `plan-sprint` CLI
    7. `rejected_plan_respawn` — User rejected a prior proposal
- **Approval gate**: every planner output is surfaced as
  `.dev/inbox/plan-<sprint_id>-proposed.md` for User review. The User
  records a `kind=plan_approval` or `kind=plan_rejection` decision; the
  planning_loop polls `decisions.json` and acts accordingly.
- **Failure semantics** (§26.3.C retry policy):
    - Output fails schema/cross-reference validation → one retry with
      error feedback as planner-prompt addendum
    - Second consecutive failure → Tier 2 ESCALATE
      (`sprint_planner_output_invalid_x2` trigger)
    - Spawn failure (claude binary missing, timeout) → same retry
      policy → same Tier 2 escalate
- **Priority vs reviewers**: sprint-planner is UPSTREAM of all
  reviewers. It decides WHAT to build; reviewers decide WHETHER what
  was built passes. They never conflict — they answer different
  questions at different layers. `reviewer_priority.yaml` therefore
  does NOT list sprint-planner.
- **Phase 6 roadmap**: a future "Tactical replanner" agent (lighter
  weight than sprint-planner; runs after each task completion to
  amend in-flight sprints) is reserved as a Phase 6 sub-component.
  Not in Phase 5 scope.

---

## 5. Hard Gates 矩阵（25 个，Python 强制）

### 5.1 Blocker Gates（5 个，任意 fail 立即停）

| Gate | 检查 | 失败动作 |
|---|---|---|
| `git_clean_preflight` | 任务开始时 working tree 必须 clean | 阻止任务启动 |
| `branch_isolation` | 任务必须在专属 `task/T-X-NNN` branch（或 worktree） | 阻止 |
| `path_allowlist` | `git diff --name-only` 全部在 track allowlist 内 | FAIL；2 次违规 → ESCALATE |
| `secret_scan` | diff 中无 secret patterns | FAIL + ESCALATE（立即） |
| `state_write_guard` | LLM agent 不修改 `.dev/state/**` | FAIL + ESCALATE |

### 5.2 High Gates（6 个）

| Gate | 检查 |
|---|---|
| `test_integrity` | 测试文件未删除 / assert 数未减 / skip-xfail 未增 / fuzz_runs 未降 / gate command 未篡改 |
| `interface_matrix_updated` | 若改了 ABI/event/EIP-712/WS schema/Pydantic model/attestation payload，`.dev/contracts/_registry.json` 与对应版本文件必须同步 bump（详见 §11） |
| `review_json_schema_valid` | Reviewer 输出符合 JSON schema |
| `archive_complete` | 任务归档含 diff.patch + tests.log + tools.json + reviews.json |
| `no_unapproved_external_call` | 真金 tx / API key 使用 / 钱包签名 必须 human approval |
| `spec_drift_check` | deliverable 行为不能偏离 PRD/TECHNICAL_PLAN 锁定的设计（见 §25） |

### 5.3 Domain Gates（15 个）

```
forge_build                # Track A 任何 commit
forge_test                 # Track A 测试任务
slither_high               # Critical/High → escalate；Medium → 强制 Contract Security Reviewer
gas_regression             # >5% → FAIL
pytest_pass                # Track B/C
mypy_clean                 # Track B/C
no_lookahead               # Track B 训练特征
calib_converged            # Track C CI width
calib_objectives           # Track C 三级:
                           #   13–14/14 → PASS
                           #   12/14    → FAIL（允许 repair round，回 Track C 修后重试）
                           #   <12/14   → ESCALATE Tier 2
backtest_validity          # Track C 完成时
playwright_smoke           # Track D 关键改动
lighthouse_perf            # Track D 完成时
polymarket_smoke           # Track B (dry / live 分开)
reconciliation             # 跨链相关
phase3_irreversibility_locked    # Phase 3 准备（见下方详细检查表）
```

#### `phase3_irreversibility_locked` 检查项

Phase 3 启动前必须确认以下**全部**为真（任一失败 → 阻止 Phase 3 启动 + Tier 1 ESCALATE）：

| 检查 | 验证方式 |
|---|---|
| `PauseRoleRenounced` event emit 且 receiver = `0x000...dEaD` | 链上 event log scan |
| `UpgradeRoleRenounced` event emit 且 receiver = `0x000...dEaD` | 链上 event log scan |
| `EnergyController.pauseRole()` view 返回 burn 地址 | `eth_call` |
| `EnergyController.upgradeRole()` view 返回 burn 地址 | `eth_call` |
| `EnergyController` 合约**不存在** active proxy admin | 检查 EIP-1967 storage slot |
| `attestationSigner` ≠ admin wallet（独立 key） | 比对地址 |
| 任何 onlyAdmin / onlyGovernance modifier 函数已不可达 | 静态分析 + admin role 验证 |

---

## 6. Path Manifest（`.dev/policy/path_manifest.yaml`）

**Policy 权限规则**（Hard rule，所有 agent 适用）：
- `.dev/policy/**` 是 universal_forbidden，**任何 Track Agent / Reviewer Agent / Doc Editor Agent 都不能修改**
- 修改 policy 必须走 `policy_update_task`：Advisor 起草 → User 显式批准 → Orchestrator spawn 专属一次性 task 落档
- `policy_update_task` 自身受同一套 hard gates 约束（含 review）
- 任何 agent 的 diff 触及 `.dev/policy/**` → `state_write_guard` 立即 FAIL + Tier 1 ESCALATE


```yaml
track_a_chain:
  allowed:
    - contracts/**
    - test/**
    - script/**
    - foundry.toml
    - remappings.txt
    - .gitignore
    - .gitmodules                       # OpenZeppelin / forge-std
    - lib/**                            # Foundry 依赖
    - README_CHAIN.md                   # track-local README
    - .dev/contracts/*abi*.json         # 接口注册（写新版本号）
  forbidden:
    - agent/**
    - sim/**
    - dashboard/**
    - data/**

track_b_backend:
  allowed:
    - agent/**
    - tests/agent/**
    - pyproject.toml
    - poetry.lock
    - requirements*.txt
    - .python-version
    - mypy.ini
    - pytest.ini
    - ruff.toml
    - .gitignore
    - README_BACKEND.md
    - .dev/contracts/*schema*.json
  forbidden:
    - contracts/**
    - dashboard/**
    - sim/**

track_c_sim:
  allowed:
    - sim/**
    - tests/sim/**
    - reports/calibration/**
    - pyproject.toml                    # 共享 Python 配置
    - requirements*.txt
    - .gitignore
    - README_SIM.md
  forbidden:
    - contracts/**
    - agent/**
    - dashboard/**

track_d_frontend:
  allowed:
    - dashboard/**
    - tests/dashboard/**
    - package.json
    - package-lock.json
    - pnpm-lock.yaml                    # 看选哪个 package manager
    - tsconfig.json
    - next.config.*
    - tailwind.config.*
    - postcss.config.*
    - .eslintrc*
    - .gitignore
    - README_FRONTEND.md
    - public/**                         # 静态资源
    - .dev/contracts/*dashboard*.json   # 消费 WS schema
  forbidden:
    - contracts/**
    - agent/**
    - sim/**

track_e_data:
  allowed:
    - data/**
    - tests/data/**
    - pyproject.toml
    - requirements*.txt
    - .gitignore
    - README_DATA.md
  forbidden:
    - contracts/**
    - agent/**
    - dashboard/**

universal_forbidden:                    # 任何 agent 永远不能改
  - .dev/state/**
  - .dev/policy/**
  - .env
  - .env.*                              # .env.local / .env.production / etc.
  - **/secrets/**
  - **/*.key
  - **/*.pem
  - **/wallet*.json                     # 钱包文件
```

每轮 Track Agent 交付后，Python 跑 diff 比对，违规 → FAIL。

---

## 7. State Write Guard

LLM Agent **不允许** 直接编辑 `.dev/state/**`。它只能写：

```
.dev/inbox/T-X-NNN/
├── delivery_report.md           # LLM 自述（不作为事实）
├── proposed_state_update.json   # LLM 提议的状态变更（Python 审）
└── interface_diff.json          # LLM 声明的接口改动
```

Orchestrator 读取 inbox，验证 diff，**Python 才写**到 `.dev/state/`。

`state_write_guard` Hard Gate 采用 **diff + journal 双重验证**（不依赖 git blame，因为 worktree 中 blame 不可靠）：

```python
def state_write_guard(task_id, base_commit, head_commit):
    # 检查 1: Task worktree 内的 diff 中绝不能包含 .dev/state 改动
    diff_files = git_diff_files(base_commit, head_commit, cwd=task_worktree_path)
    forbidden_diff = [f for f in diff_files if f.startswith(".dev/state/")
                                              or f.startswith(".dev/policy/")]
    if forbidden_diff:
        return fail("forbidden_state_or_policy_diff_in_worktree", files=forbidden_diff)

    # 检查 2: control workspace 内 .dev/state 的变更必须有对应 journal entry
    state_diff = git_diff_files(base_commit, head_commit,
                                paths=[".dev/state/"], cwd=control_workspace)
    if state_diff:
        journal_entries = read_journal_since(base_commit)
        if not all_state_changes_have_journal_entry(state_diff, journal_entries):
            return fail("state_changed_without_journal_entry")
        if not all_journal_entries_actor_is_orchestrator(journal_entries):
            return fail("non_orchestrator_actor_in_journal")

    return pass()
```

任何 fail → Tier 1 ESCALATE。

---

## 8. Task Isolation Model

每个任务独立 branch 或 worktree。**所有 `.dev/state/**` 写入只在 Orchestrator 主工作区（control workspace）发生**，任务 worktree 严禁碰 state。

```python
# 任务启动（在 Orchestrator 主工作区执行）
state_manager.assert_clean_tree()                          # equivalent to: git status, fail if dirty

# 在 control workspace 上 stage branch 元数据
state_manager.create_task_branch("T-A-001-energy-controller")
state_manager.record_task_base_commit(
    task_id="T-A-001",
    base_commit=git_rev_parse_head(),
)                                                          # 由 state_manager 写 .dev/state/，不在 worktree 里 echo

# 多 track 并发 → worktree 隔离
state_manager.create_task_worktree("T-A-001", path=".worktrees/T-A-001")
state_manager.create_task_worktree("T-B-001", path=".worktrees/T-B-001")
state_manager.create_task_worktree("T-C-001", path=".worktrees/T-C-001")

# Track Agent 只在 .worktrees/T-A-001/ 内活动；它看不到 .dev/state/
```

**关键约束**：
- Track Agent 在 worktree 内**不能 commit `.dev/state/**` 任何文件**（worktree 内的该路径应被 `state_write_guard` 视为禁区）
- 所有 state transition 由 Orchestrator 在 control workspace 内 commit，并写入 `.dev/state/journal/`
- 任务 worktree 仅承载业务代码 diff

每轮结束 Orchestrator 从 worktree 抓取 diff，归档到 control workspace 的 `.dev/archive/`，然后更新 state SSOT。

任务 PASS → merge to dev branch；FAIL（round ≥ 4 或重复 failure signature ≥ 2）→ branch 保留供 inspection，不污染 main。

---

## 9. Reviewer Contract（JSON Schema 强制）

Reviewer 必须输出 JSON，Python 用 schema validate。Generic Review 例：

```json
{
  "verdict": "PASS",
  "scores": {
    "alignment": 8,
    "correctness": 8,
    "completeness": 8,
    "security_safety": 8,
    "integration": 8
  },
  "blocking_issues": [],
  "non_blocking_issues": [],
  "required_fixes": [],
  "confidence": "medium",
  "evidence_seen": {
    "saw_diff": true,
    "saw_test_logs": true,
    "saw_tool_outputs": true
  }
}
```

Hard rules：

```
JSON parse fail → rerun once → 仍 fail → ESCALATE
any score < 8 → verdict = FAIL
blocking_issues non-empty → verdict = FAIL
evidence_seen.saw_test_logs == false → cap completeness at 6
evidence_seen.saw_diff == false → cap correctness at 6
```

---

## 10. Diff Truthfulness

每轮归档包：

```
.dev/archive/T-X-NNN/round-N/
├── delivery_report.md           # LLM 自述（参考）
├── changed_files.txt            # git diff --name-only（事实）
├── diff.patch                   # git diff（事实）
├── test_logs/                   # subprocess 抓取（事实）
├── tool_outputs/                # subprocess 抓取（事实）
├── generic_review.json
└── specialized_review.json
```

Truthfulness check：

```python
claimed = parse(delivery_report).claimed_changes
actual  = changed_files
if not set(claimed).issubset(set(actual)):
    fail("delivery_report claims unverified by diff")
if not acceptance_criteria_touch_expected_files(brief, actual):
    fail("brief acceptance not addressed by diff")
```

Reviewer **不接受 delivery_report 作为事实来源**，只从 diff + logs 推断。

---

## 11. Interface Registry（机器可读接口契约）

```
.dev/contracts/
├── _registry.json
├── energy_controller_abi.v0.1.0.json
├── decision_log_events.v0.1.0.json
├── settlement_attestation_eip712.v0.1.0.json
├── dashboard_websocket.v0.1.0.json
├── tombstone_metadata.v0.1.0.json
├── bankroll_mirror_update.v0.1.0.json
└── polymarket_order_result.v0.1.0.json
```

任何 commit 影响接口：
1. 必须 bump version 文件
2. 必须更新 `_registry.json`
3. `interface_matrix.md`（人读摘要）自动 regenerate

Interface Contract Gate（多源检测，不只看 ABI 文件）：

```python
def interface_contract_gate(base, head, diff_files):
    """
    检测以下任一类型的接口改动；任意命中要求 .dev/contracts/_registry.json + 对应
    schema 版本文件同步 bump。
    """
    interface_touched = []

    # 1. Solidity ABI 改动（函数 / 修饰符 / 可见性）
    sol_diff = filter_files(diff_files, "contracts/**/*.sol")
    if sol_diff:
        abi_before = compile_abi(base, sol_diff)
        abi_after  = compile_abi(head, sol_diff)
        if abi_signature_changed(abi_before, abi_after):
            interface_touched.append("solidity_abi")

    # 2. Solidity event 签名改动
    if event_signature_changed(base, head, sol_diff):
        interface_touched.append("solidity_event")

    # 3. EIP-712 typehash 改动（struct 定义变更）
    if eip712_typehash_changed(base, head, sol_diff):
        interface_touched.append("eip712_struct")

    # 4. Dashboard WebSocket message schema 改动
    ws_diff = filter_files(diff_files, ["agent/dashboard_bridge/**",
                                         "dashboard/src/types/ws_*.ts"])
    if ws_diff and ws_message_schema_changed(base, head, ws_diff):
        interface_touched.append("dashboard_ws_schema")

    # 5. Python decision/agent JSON schema 改动（Pydantic models 用于跨模块）
    py_models = filter_files(diff_files, "agent/**/schemas/*.py")
    if py_models and pydantic_schema_breaking_change(base, head, py_models):
        interface_touched.append("python_schema")

    # 6. Polymarket order / settlement attestation payload 改动
    if attestation_payload_changed(base, head, diff_files):
        interface_touched.append("polymarket_attestation")

    if not interface_touched:
        return pass()

    # 必须同步更新 interface registry
    registry_touched = any(f.startswith(".dev/contracts/") for f in diff_files)
    if not registry_touched:
        return fail("interface_changed_without_registry_update",
                    detected_changes=interface_touched)

    # 检查 _registry.json 的对应 active version 是否 bump 了
    if not registry_versions_bumped(base, head, interface_touched):
        return fail("interface_changed_but_registry_version_not_bumped",
                    detected_changes=interface_touched)

    return pass()
```

---

## 12. Secret Patterns（`.dev/policy/secret_patterns.yaml`）

```yaml
critical_patterns:                        # 命中即 Tier 1 ESCALATE，不给修复机会
  - 'PRIVATE_KEY\s*=\s*[\w-]+'
  - 'MNEMONIC\s*=\s*[\w\s]{20,}'          # ≥20 字符防 dummy
  - 'SEED_PHRASE\s*=\s*'
  - 'POLYMARKET_API_KEY\s*=\s*[\w-]+'
  - 'ANTHROPIC_API_KEY\s*=\s*sk-ant-'
  - 'OPENAI_API_KEY\s*=\s*sk-'
  - 'ETHERSCAN_API_KEY\s*=\s*[\w-]+'
  - '\bxprv[A-HJ-NP-Za-km-z1-9]{50,}\b'   # BIP32 root key
  - '-----BEGIN (RSA |EC )?PRIVATE KEY-----'

context_sensitive_patterns:               # 64-hex 太常见（tx hash / 地址 hash），需要上下文
  - pattern: '\b0x[a-fA-F0-9]{64}\b'
    suspicious_if_within_50_chars_of:     # 同行 ±50 字符内出现这些关键词才视为 critical
      - 'private[_ ]?key'
      - 'priv[_ ]?key'
      - 'pk\s*='
      - 'sk\s*='
      - 'wallet'
      - 'mnemonic'
      - 'seed'
      - 'signer'
      - 'deployer'
    severity_if_match: critical            # 上下文命中 → Tier 1
    severity_if_no_match: warning          # 无上下文 → Warning（写 issues，不阻塞）

allowlist:                                # 明确白名单
  - tests/fixtures/**                      # 测试 dummy
  - tests/**/test_*.py                     # 测试代码内常有 hardcoded 测试 hash
  - .dev/policy/secret_patterns.yaml       # 本文件自身（含 pattern 字符串）
  - docs/**                                # 文档示例
```

执行规则：
- `critical_patterns` 命中 → 立即 Tier 1 ESCALATE，不给修复 round
- `context_sensitive_patterns` 上下文命中 → 同上
- `context_sensitive_patterns` 无上下文命中 → Tier 3 Warning（写 `issues.json`），任务继续
- 任何命中文件在 allowlist 内 → 跳过

---

## 13. Escalation Tiers

### Tier 1: Critical（立即 stop，立即通知 Advisor）

```
Slither Critical/High finding
Look-ahead bias detected
phase3_irreversibility_locked gate fail（Phase 3 启动前权限未全部锁死）
secret_scan 命中
state_write_guard 触发
Cross-chain reconciliation drift（双阈值，见 13.4）
no_unapproved_external_call 触发
```

### Tier 2: High（暂停任务，写 escalations）

```
round >= 4                          # 从 10 降到 4
same_failure_signature >= 2         # 重复同类失败
test_count_decreased                # 测试被偷偷减
security_gate_regressed             # 之前过的 security gate 这轮不过
calibration_objectives_passed < 12/14      # 12/14 仅触发 repair round，不直接 ESCALATE
path_allowlist 2 次违规
Polymarket smoke 3 次连失
```

### Tier 3: Medium（写 issues，不暂停）

```
Lighthouse < 80
Gas regression > 5%
interface_matrix_updated miss（修复后可继续）
```

### Tier 4: Cost Guard（单独表，不混 security）

| Trigger | Action |
|---|---|
| LLM 月成本 > $30（1.5× 预算） | 暂停非必要 LLM 调用，写 cost_report |
| LLM 月成本 > $50（2.5× 预算） | escalate to Advisor |

### 13.4 Cross-Chain Drift 双阈值

```python
drift_critical = (
    absolute_drift_usd > 1.0
    or relative_drift > 0.02 * bankroll
    or nonce_regression_detected
    or duplicate_settlement_detected
)
```

任一命中 → Tier 1 Critical。

---

## 14. Test Integrity Gate

```python
def test_integrity_check(base, head):
    base_tests = parse_tests(base)
    head_tests = parse_tests(head)
    if head_tests.file_count < base_tests.file_count:
        return fail("test_files_deleted")
    if head_tests.assert_count < base_tests.assert_count * 0.95:
        return fail("assert_count_decreased")
    if head_tests.skipped > base_tests.skipped:
        return fail("skip_added_without_approval")
    if head_tests.fuzz_runs < base_tests.fuzz_runs:
        return fail("fuzz_runs_decreased")
    if gate_commands_modified(base, head):
        return fail("gate_command_tampered")
    return pass()
```

任何 fail → ESCALATE（不允许 round 内修复，因为这是诚实性违规）。

---

## 15. State SSOT

### 15.1 双轨：JSON 机器源 / Markdown 人读

```
.dev/state/
├── status.json + status.md
├── sprint.json + sprint.md
├── decisions.json + decisions.md
├── issues.json + issues.md
├── escalations.json + escalations.md
├── tracks/track_*.json + .md
├── domain/...
└── journal/YYYY-MM-DD.jsonl
```

Python 写 JSON → 自动 render Markdown。Markdown 不可手改（会被下次 render 覆盖）。

### 15.2 Journal（强制，每次 state transition）

```
.dev/state/journal/2026-05-15.jsonl
```

每行：

```json
{"ts":"2026-05-15T14:23:01Z","task_id":"T-A-001","round":2,
 "from":"TRACK_DELIVERED","to":"HARD_GATES_RUNNING",
 "actor":"orchestrator","artifact_path":".dev/archive/T-A-001/round-2/"}
```

每个 state transition 自动 git commit（atomic）。崩溃恢复时按 journal 还原。

### 15.3 状态机（16 个 enum）

```
PENDING
PREFLIGHT_PASSED
TRACK_RUNNING
TRACK_DELIVERED
DIFF_VALIDATED
HARD_GATES_RUNNING
HARD_GATES_PASSED
GENERIC_REVIEW_RUNNING
GENERIC_REVIEW_PASSED
SPECIALIZED_REVIEW_RUNNING
SPECIALIZED_REVIEW_PASSED
ARCHIVED
MERGED
COMPLETED
ESCALATED
FAILED
```

每个状态记录完整上下文（base_commit / current_commit / branch / archive_path / last_successful_artifact）。Resume 从最后一个稳定状态续。

---

## 16. Reviewer Priority（冲突解决）

```
1. Security (Contract Security Reviewer)
2. Funds Safety (Cross-Chain Reconciliation)
3. Chain Correctness
4. Data Integrity (ML Validator / no_lookahead)
5. Spec Alignment (Generic Reviewer)
6. Demo Readiness (Demo Readiness Auditor)
7. Performance (Lighthouse, Gas snapshot)
8. Style (mypy, ruff, fmt)
```

任一 specialized FAIL → 任务 FAIL。Human override → 必须写 `decisions.md` 记录。

---

## 17. Reviewer Packet（anti-rubber-stamp）

Reviewer 接收的输入顺序（强制结构化）：

```
1. Original task brief
2. Relevant PRD / TECHNICAL_PLAN excerpts
3. Interface contracts touched
4. git diff (raw)
5. Test logs (raw)
6. Tool outputs (raw JSON)
7. Previous failed reviews (if round > 1)
8. [最后] delivery_report.md   ← 警告：do not trust claims unless supported by diff/tests
```

Reviewer prompt 强制：

> *"Treat delivery_report as the agent's own narrative, not as evidence. Score based on diff + tests + tools only. If you score X without seeing the underlying diff/tests for X, cap that dimension at 6."*

---

## 18. Tool Output Standard

所有 hard gate tools 统一输出：

```json
{
  "tool": "forge_test",
  "version": "0.2.0",
  "command": "forge test --fuzz-runs 10000",
  "exit_code": 0,
  "duration_ms": 12450,
  "status": "PASS",
  "severity": "none",
  "summary": "128 tests passed",
  "metrics": {
    "tests_run": 128,
    "tests_passed": 128,
    "fuzz_runs_per_test": 10000
  },
  "blocking_findings": [],
  "non_blocking_findings": [],
  "artifacts": [".dev/archive/T-A-001/round-2/forge_test.log"]
}
```

Schema 在 `.dev/policy/schemas/tool_output.schema.json`。`gate_matrix.py` 只读这个统一格式。

---

## 19. Advisor Decision Log

每次 Advisor 处理 escalation 后必须输出：

```json
{
  "decision_id": "D-2026-05-17-001",
  "ts": "2026-05-17T10:23Z",
  "escalation_id": "E-001",
  "decision": "Allow Slither medium finding (false positive in donate cap check)",
  "rationale": "...",
  "affected_tracks": ["A"],
  "changes_prd": false,
  "changes_technical_plan": false,
  "changes_integration_matrix": false,
  "user_approval": "<email> / <timestamp>"
}
```

写入 `.dev/state/decisions.json`，所有未来 Reviewer 自动 load 历史 decisions 作为参考。

---

## 20. 完整文件结构

```
code/.dev/
│
├── README.md                     # 含 10 条 Process Invariants
│
├── policy/                       # 不可变的规则（普通 agents 严禁修改）
│                                  # 改动需 policy_update_task（Advisor 提议 + User 批准）
│   ├── path_manifest.yaml
│   ├── secret_patterns.yaml
│   ├── escalation_tiers.yaml
│   ├── reviewer_priority.yaml
│   └── schemas/
│       ├── generic_review.schema.json
│       ├── specialized_review.schema.json
│       ├── delivery_report.schema.json
│       ├── proposed_state_update.schema.json
│       ├── tool_output.schema.json
│       └── escalation.schema.json
│
├── contracts/                    # Interface registry（机器可读 schema）
│   ├── _registry.json
│   ├── energy_controller_abi.v0.1.0.json
│   ├── settlement_attestation_eip712.v0.1.0.json
│   ├── dashboard_websocket.v0.1.0.json
│   └── ...
│
├── roles/                        # 13 个 Agent system prompts
│   ├── tracks/
│   │   ├── track_a_chain.md
│   │   ├── track_b_backend.md
│   │   ├── track_c_sim.md
│   │   ├── track_d_frontend.md
│   │   └── track_e_data.md
│   └── reviewers/
│       ├── generic_5d.md
│       ├── contract_security.md
│       ├── calibration_validator.md
│       ├── ml_validator.md
│       ├── crosschain_auditor.md
│       ├── polymarket_smoke.md
│       └── demo_readiness.md
│
├── harness/                      # Python orchestrator
│   ├── orchestrator.py
│   ├── claude_client.py
│   ├── state_manager.py
│   ├── gate_matrix.py
│   ├── escalation.py
│   ├── diff_validator.py
│   ├── secret_scanner.py
│   ├── interface_checker.py
│   ├── test_integrity.py
│   ├── journal.py
│   └── tools/
│       ├── solidity.py / slither.py / etherscan_verify.py
│       ├── pytest_runner.py / mypy_runner.py / lookahead_auditor.py
│       ├── calibration_diag.py / backtest_validator.py
│       ├── polymarket_smoke.py / reconciliation.py
│       ├── playwright_runner.py / lighthouse_runner.py
│       └── demo_capture.py
│
├── workflow/                     # 流程规范文档
│   ├── task_lifecycle.md         # 9 阶段（含 preflight + diff validation）
│   ├── review_process.md
│   ├── escalation_protocol.md
│   └── resumption.md
│
├── templates/                    # LLM input/output 模板
│   ├── task_brief.md
│   ├── delivery_report.md
│   ├── generic_review.md
│   ├── specialized_review.md
│   ├── fix_response.md
│   └── escalation.md
│
├── inbox/                        # LLM 写到这里（不影响 state）
│   └── T-X-NNN/
│       ├── delivery_report.md
│       ├── proposed_state_update.json
│       └── interface_diff.json
│
├── state/                        # 仅 Orchestrator 写
│   ├── status.json + status.md
│   ├── sprint.json + sprint.md
│   ├── decisions.json + decisions.md
│   ├── issues.json + issues.md
│   ├── escalations.json + escalations.md
│   ├── tracks/track_*.json + .md
│   ├── domain/
│   │   ├── contract_audit_log.md
│   │   ├── calibration_history/
│   │   ├── training_runs/
│   │   ├── integration_matrix.md
│   │   ├── demo_assets/
│   │   ├── bankroll_ledger.md
│   │   └── reconciliation_log.md
│   └── journal/YYYY-MM-DD.jsonl
│
└── archive/                      # 已完成任务（含完整证据链）
    └── 2026-MM-DD-T-X-NNN/
        └── round-N/
            ├── brief.md
            ├── delivery_report.md
            ├── changed_files.txt
            ├── diff.patch
            ├── test_logs/
            ├── tool_outputs/
            ├── generic_review.json
            ├── specialized_reviews/
            └── verdict.json
```

---

## 21. 任务生命周期（9 阶段）

### 21.1 Planning loop wrapper (Phase 5+)

The 9-stage lifecycle (PREFLIGHT → COMPLETED) drives a single TASK. In
Phase 5+, that single-task driver is wrapped by a closed-loop planning
state machine that decides which tasks to drive, in what order, with
what amendments when reality deviates from spec.

```
   ┌──────────────────────────────┐
   │  LoopState.IDLE              │
   │  (detect trigger)            │
   └─────────────┬────────────────┘
                 │ ReplanReason
                 ▼
   ┌──────────────────────────────┐
   │  PLANNING                    │   sprint-planner L4 spawn
   │  (validate output)           │   plan_validator.run
   └─────────────┬────────────────┘
                 │ verdict=pass
                 ▼
   ┌──────────────────────────────┐
   │  AWAITING_APPROVAL           │   .dev/inbox/plan-*-proposed.md
   │  (poll decisions.json)       │   User runs approve-plan / reject-plan
   └─────────┬────────────┬───────┘
             │approval    │rejection
             ▼            ▼
   ┌──────────────┐  ┌──────────────┐
   │ EXECUTING    │  │ PLANNING     │   (planner respawn with notes)
   └──────┬───────┘  └──────────────┘
          │ start_sprint per existing §21 9-stage flow
          ▼
   ┌──────────────────────────────┐
   │  (post-sprint trigger check) │   COMPLETED → SPRINT_BOUNDARY
   │  → back to IDLE or PAUSED    │   ESCALATED Tier1 → PAUSED
   └──────────────────────────────┘
```

The planning loop adds **strategic** layer on top of the existing
**operational** 9-stage lifecycle. The 9 stages are unchanged — Phase
5 wraps them; it does not modify them. Existing T1-T11 e2e tests
continue to verify the 9-stage contract.

**Single-writer invariant**: only ONE planning_loop process can drive
a project at a time. Enforced via lockfile at
`.dev/state/.planning_loop.lock` with PID-liveness check. Stale
lockfiles from crashed prior runs are auto-recovered.

**Checkpointable**: loop state persists in
`.dev/state/.planning_loop.state.json` so process restarts resume
cleanly.

**Budget guard**: hard cap on planner spawns per `run_loop` invocation
(default 4). Beyond → `BUDGET_EXHAUSTED` terminal state. Cost-aware.

**Topology (Phase 6+ — canonical: single-pane)**: `/start-dev` invokes
`run-loop` as a Bash backgrounded process inside the Advisor's Claude
Code chat session — NOT in a separate terminal. Advisor reads
`.dev/state/orchestrator.log` tail + `.dev/inbox/plan-*-proposed.md` +
`status.md` + `escalations.md` on each User turn, surfaces plan
proposals + escalations to User, and invokes `approve-plan` /
`reject-plan` / `escalate --resolve` CLIs as the User's typed proxy
(see §25.1). Two-pane mode (run-loop launched in a detached terminal)
remains supported as an optional fallback for sprints longer than
expected chat session lifetime; the choice between modes is
operational, not architectural — the same `decisions.json` polling +
lockfile recovery works identically in both. If the Claude Code
session exits and the backgrounded `run-loop` process dies, the next
`/start-dev` invocation detects the stale lockfile via PID liveness,
auto-recovers, and resumes from `.planning_loop.state.json`
checkpoint.

### 21.2 Operational 9-stage flow

```
1.  PREFLIGHT           git clean / branch isolation / task packet ready
2.  TRACK EXECUTION     spawn Track Agent (fresh ctx)
3.  DIFF VALIDATION     path allowlist / secret scan / diff truthfulness / test integrity
4.  HARD GATES          build / test / domain tools (按 track)
5.  REVIEWER PACKET     生成 reviewer 输入包（diff + logs + spec，delivery_report 放末位）
6.  GENERIC REVIEW      JSON schema validate → 5 维评分
7.  SPECIALIZED REVIEW  按 task 类型 dispatch
8.  ARCHIVE & MERGE     完整证据归档 / merge to dev branch / 更新 state SSOT
9.  POST-MERGE SMOKE    短 smoke 验证 merge 后没破坏其它 track
```

任何阶段 fail → 按 Escalation Tier 处理。

---

## 22. Day 1–3 框架搭建计划

| 日 | 内容 |
|---|---|
| **Day 1 上午** | 13 个 role prompts + 4 个 workflow docs + 6 个 templates + 10 条 Invariants |
| **Day 1 下午** | Orchestrator core + claude_client + state_manager + journal（最基础版） |
| **Day 2 上午** | Path manifest + secret patterns + 6 个 JSON schemas + interface registry skeleton |
| **Day 2 下午** | Domain tool wrappers（forge / slither / pytest / lookahead / calibration_diag） |
| **Day 3 上午** | Diff validator + test integrity + reviewer packet builder + dry run T-A-001 |
| **Day 3 下午** | 修 dry run 暴露的问题 → 正式开始业务 |

**Day 1–3 三整天搭框架**，Day 4 起业务代码并发开跑。

---

## 23. Doc Editor Agent Permissions

`Doc Editor` 是一个特殊的 Orchestrator-controlled agent，**仅在以下三种情况被 spawn**：

1. **Advisor 起草 PRD / TECHNICAL_PLAN 修改建议** → User 批准 → Orchestrator spawn Doc Editor 落档
2. **Interface registry 版本 bump** → 自动触发 Doc Editor 同步 `interface_matrix.md` 人读版
3. **State SSOT 的 Markdown render** → 每次 JSON 变更后自动 render

### 23.1 权限边界

| 允许 | 禁止 |
|---|---|
| `PRD.md` | `.dev/policy/**` |
| `TECHNICAL_PLAN.md` | `.dev/state/**` 的 **JSON** 文件（只能写 .md render） |
| `DEV_FRAMEWORK.md`（需 User 显式批准） | `contracts/**`、`agent/**`、`sim/**`、`dashboard/**`、`data/**`（业务代码） |
| `.dev/state/*.md`（render-only） | `.dev/contracts/*.json`（这是 Track Agents 的 interface 落档） |
| `.dev/state/decisions.md`（render） | `.dev/inbox/**`（任何 LLM 自己的 inbox） |

### 23.2 强制约束

- Doc Editor 每次 spawn **携带明确的 task brief**，包含：
  - 触发原因（Advisor proposal / interface bump / state render）
  - User approval 凭证（除非是 state render 自动触发）
  - 要修改的文件列表（白名单）
  - 接受标准（diff 必须只 touch 白名单文件）
- Doc Editor 的输出**也经过完整 task lifecycle**：preflight → diff validation → hard gates → reviewer
- 唯一豁免：state render 自动触发的版本不经过 Generic Reviewer（因为是机械 render），但仍经过 `path_allowlist` + `state_write_guard`（write 由 Orchestrator 代为 commit）

### 23.3 与 PRD/TECHNICAL_PLAN 修改流程的关系

```
Advisor 看 escalation / drift / User 提需求
        ↓
Advisor 写 .dev/inbox/proposal-XXX.md（不直接改 doc）
        ↓
User 看 proposal → 批准 / 拒绝 / 修改
        ↓ (批准)
Orchestrator 创建 doc_update_task
        ↓
Doc Editor spawn（看 proposal + 当前 doc + diff scope）
        ↓
按 task lifecycle 走完 → merge to dev → 通知 Advisor
```

Advisor 自己**不直接编辑 PRD/TECHNICAL_PLAN**（保持职责干净）。

---

## 24. Spec Drift Check Gate

防止 Track Agent 在实现时**偏离 PRD/TECHNICAL_PLAN 锁定的设计**。这种偏离往往是「LLM 觉得自己的方案更好」而擅自修改的常见失败模式。

### 24.1 触发时机

每个 task 在 Hard Gates 阶段、Generic Reviewer 之前运行一次。

### 24.2 检查逻辑

```python
def spec_drift_check(task_id, brief, diff_files, deliverable):
    """
    检测 deliverable 是否引入了未在 spec 中授权的行为偏离。
    """
    # 1. 抽取 task brief 引用的 PRD/TECHNICAL_PLAN 章节
    spec_excerpts = load_spec_excerpts(brief.referenced_sections)

    # 2. 抽取 deliverable 中的「行为表面」(observable behavior)
    behaviors = extract_behaviors(diff_files)
    #    - 合约：function signatures, modifier 集合, state vars, events
    #    - Python：public class/function 签名 + return types
    #    - 配置：parameter values 与 spec 锁定值的对比

    # 3. 对比锁定值（PRD/TECHNICAL_PLAN 中带具体数字 / enum 的项）
    drift_findings = []
    for locked_param in spec_locked_params(spec_excerpts):
        if locked_param.name in deliverable_configs(diff_files):
            actual = deliverable_configs(diff_files)[locked_param.name]
            if actual != locked_param.value and \
               locked_param.name not in brief.authorized_changes:
                drift_findings.append({
                    "type": "locked_parameter_changed",
                    "name": locked_param.name,
                    "spec_value": locked_param.value,
                    "actual_value": actual,
                })

    # 4. 检测新增 / 重命名的 public API（合约函数 / Python 公开接口）
    #    若不在 brief.deliverables_expected 中 → drift
    for new_api in new_public_apis(diff_files):
        if new_api not in brief.deliverables_expected:
            drift_findings.append({
                "type": "unauthorized_new_api",
                "api": new_api,
            })

    # 5. 检测删除 / 重命名 spec 提及的命名（如 Phase enum / DeathCause enum）
    for renamed in renamed_or_deleted_spec_named_entities(diff_files):
        drift_findings.append({
            "type": "spec_named_entity_changed",
            "entity": renamed.name,
            "from_spec_section": renamed.spec_ref,
        })

    if drift_findings:
        return fail("spec_drift_detected", findings=drift_findings)
    return pass()
```

### 24.3 失败处理

| 数量 / 类型 | Tier |
|---|---|
| 1–2 项 locked_parameter_changed | Tier 2 High（FAIL，回 Track Agent） |
| ≥3 项 drift 或任何 `spec_named_entity_changed` | Tier 1 Critical（ESCALATE，可能需要 PRD 调整） |
| `unauthorized_new_api` | Tier 2 High |

### 24.4 与 Advisor 流程的衔接

若 drift 实际是合理改进（Track Agent 在实现时发现 spec 设计缺陷）：

```
Track Agent 在 delivery_report 中标注 "proposed_spec_change"
       ↓
spec_drift_check 仍 FAIL（不允许擅自改）
       ↓
Orchestrator 把 proposed_spec_change 转给 Advisor 走 proposal 流程
       ↓
若 User 批准 → Doc Editor 更新 PRD → 重启该 task
若 User 拒绝 → Track Agent 按原 spec 实现
```

这条流程确保**任何 spec 偏离都被显性化、可追溯，由 User 最终拍板**。

---

## 25. Advisor 常态工作流

每次 session 启动时：

1. 读 `.dev/state/status.json`
2. 读 `.dev/state/escalations.json`（如有 escalation 优先处理）
3. 跨 track sampling：抽 2 个 archive 任务 review，看 architectural drift
4. 跟 User sync 必要决策
5. 如果一切正常 → 不干预，让 orchestrator 跑
6. 如果有 architectural concern → 写 `.dev/inbox/proposal-XXX.md`（**不直接编辑 PRD/TECHNICAL_PLAN/state**，由 Doc Editor 落档），跟 User 讨论
7. 处理 escalation 后写 decision record 到 `.dev/state/decisions.json`（**通过 Orchestrator API**，不直接写文件）
8. 接收 Track Agent 提交的 `proposed_spec_change`（spec drift check 触发）→ 转给 User → 批准后 spawn Doc Editor

工具：只用 Read / Grep / 跟 User 对话 / 写 inbox proposal。**不 spawn agent，不 Write 业务代码，不直接 Write state/policy/doc**。

Advisor 还 MUST NOT：

- **Plan sprints.** Sprint planning is the `sprint-planner` L4 agent's
  exclusive responsibility (see §4.6). If the User asks the Advisor in
  Monitor Mode "what should we work on next?" the Advisor MUST redirect
  to `py -m harness orchestrator plan-sprint` (or `run-loop` for the
  full closed loop). The Advisor MAY read planner proposals at
  `.dev/inbox/plan-*-proposed.md` to help the User understand what
  the planner is proposing, but MUST NOT independently propose tasks.

### 25.1 Advisor 代理 User 调用 CLI 的边界 (Phase 6+)

§25 的基线约束保留：Advisor MUST NOT spawn agent / Write 业务代码 /
直接 Write state/policy/doc / 自主 plan sprints。Phase 6 增加
**single-window execution mode** 的设计要求 — `/start-dev` 须能在
单一 Claude Code chat session 内完整驱动 plan → approve → execute →
re-plan 闭环（见 §21.1 Topology），不强制 User 切换终端。这要求
Advisor 在 chat session 内启动并管理 `run-loop`，以及作为 User 在
chat 中输入指令的 typed proxy 调用相关 CLI。

**Advisor 在此 carve-out 下 MAY**：

- 通过 Bash `run_in_background` 启动 `py -m harness orchestrator run-loop`，
  输出 tee 到 `.dev/state/orchestrator.log`
- 当 User 在 chat 中**明示授权**（例如 "approve" / "reject with notes X"
  / "resolve escalation T-X-NNN with decision Y"），调用：
    - `py -m harness orchestrator approve-plan <sprint_id> --rationale "..."`
    - `py -m harness orchestrator reject-plan <sprint_id> --notes "..."`
    - `py -m harness orchestrator escalate --resolve <id> ...`
    - `py -m harness orchestrator replan` (debug / override)
- 每次调用 CLI 时把 User 原话（verbatim）写入对应 decision record 的
  `user_approval` 字段，保审计链可回放

**Advisor 在此 carve-out 下 MUST NOT**：

- 自主决策（无 User 显式 chat 指令不可调用以上 CLI）
- 用 Agent 工具直接 spawn subagent（subagent 调度是 orchestrator 的职责，
  Advisor 只能通过 CLI 间接驱动）
- 把 carve-out 扩展到非闭环 CLI（例如 `state-write` / `policy-edit`
  类操作仍禁止；只有 plan / escalation 闭环 CLI 在 carve-out 内）
- 跳过 verbatim user_approval 字段（审计完整性是 carve-out 的对价）

设计动机：闭合"两窗口 vs 单窗口"框架缺口（User 2026-05-17 directive
'我要的是把这个缺口给补上'）。两窗口模式（独立终端跑 run-loop）保留
为可选 fallback，单窗口为 canonical。

---

## 26. Framework E2E Integration Test（强制 Gate）

### 26.0 定位与硬约束

> **这是框架与业务之间的不可绕过 Gate。Day 3 末尾框架代码写完后必须跑此测试套件，全部 PASS 才能进入 Day 4 业务开发。任何 FAIL → 必须修，不修不开业务代码。**

目的：在让框架监管 3 周真实开发前，**穷尽式验证每个 Hard Gate 实际会触发、每条 escalation 真的会跑、每个状态转移真的被记录、每个对抗性场景真的被挡住**。

### 26.1 测试套件结构

```
code/.dev/integration_tests/
├── fixtures/                       # 合成 task / 假 diff / 假 LLM response
│   ├── tasks/                      # 各种合成 task brief
│   ├── deliverables/               # 各种合成 deliverable（含恶意样本）
│   ├── reviewer_outputs/           # 真实/破损/降级 reviewer JSON
│   ├── attestations/               # 各种 EIP-712 attestation 样本
│   └── secret_samples/             # 合法 + 恶意 secret 样本
├── unit/                           # pytest，纯 Python，mock LLM
│   ├── test_state_manager.py
│   ├── test_gate_matrix.py
│   ├── test_diff_validator.py
│   ├── test_secret_scanner.py
│   ├── test_interface_checker.py
│   ├── test_test_integrity.py
│   ├── test_spec_drift.py
│   ├── test_reviewer_schema.py
│   ├── test_escalation.py
│   ├── test_journal.py
│   └── test_resume.py
├── e2e/                            # 完整 task lifecycle，可选用真 Claude SDK
│   ├── test_happy_paths.py          # T1.x
│   ├── test_hard_gates.py           # T2.x
│   ├── test_blocker_gates.py        # T3.x
│   ├── test_high_gates.py           # T4.x
│   ├── test_reviewers.py            # T5.x
│   ├── test_state_lifecycle.py      # T6.x
│   ├── test_escalations.py          # T7.x
│   ├── test_adversarial.py          # T8.x  ← 最关键
│   ├── test_interface.py            # T9.x
│   ├── test_concurrent.py           # T10.x
│   ├── test_full_sprint.py          # T11.x
│   └── test_genesis_product.py      # T12.x（依赖业务模块，Phase Gate 前跑）
└── reports/                        # 测试运行结果 + 覆盖率
    ├── unit_report.html
    ├── e2e_report.html
    ├── junit.xml
    ├── failed_cases.json
    ├── artifact_manifest.json
    └── coverage.html
```

### 26.2 测试目录（12 个类别，共 ~103 个测试用例）

> T1–T11 为 **framework-level**（验证框架本身能跑），T12 为 **product-level**（验证框架能交付 Genesis 产品闭环）。两者并存，缺一不可。

#### T1. Happy Paths（5 个）— 正向路径

| ID | 场景 | 期望 |
|---|---|---|
| T1.1 | 最小 task 跑完 9 阶段（PENDING → COMPLETED） | 所有 state 正确转移；journal 完整 |
| T1.2 | Calibration 14/14 objectives 全过 | PASS |
| T1.3 | Reviewer 5 维全 ≥8 | PASS |
| T1.4 | 3 track 并行（worktree） | 互不干扰，各自正确归档 |
| T1.5 | Round 1 FAIL → Round 2 PASS（修复成功） | 正确递增 round；journal 反映 |

#### T2. Hard Gates 触发（13 个）— Domain Gates

| ID | 场景 | 期望 |
|---|---|---|
| T2.1 | `forge_build` exit ≠ 0 | FAIL，**不调** reviewer |
| T2.2 | `forge_test` fail | FAIL，不调 reviewer |
| T2.3 | Slither HIGH finding | Tier 1 ESCALATE |
| T2.4 | `pytest_pass` fail | FAIL，不调 reviewer |
| T2.5 | `mypy_clean` 错 | FAIL |
| T2.6 | `no_lookahead` 检测到时序污染 | Tier 1 ESCALATE，停 Track B |
| T2.7 | Calibration 12/14 | FAIL → repair round（不直接 ESCALATE）|
| T2.8 | Calibration 11/14 | Tier 2 ESCALATE |
| T2.9 | `backtest_validity` 寿命分布偏 | FAIL → repair |
| T2.10 | `playwright_smoke` fail | FAIL |
| T2.11 | Polymarket smoke 3× fail | Tier 2 ESCALATE |
| T2.12 | `reconciliation` drift 超阈值 | Tier 1 ESCALATE |
| T2.13 | `phase3_irreversibility_locked` 任一检查 fail | 阻止 Phase 3 启动 |

#### T3. Blocker Gates 触发（9 个）

| ID | 场景 | 期望 |
|---|---|---|
| T3.1 | Dirty working tree | 任务**无法启动** |
| T3.2 | 任务跑在 main 分支 | 任务无法启动 |
| T3.3 | Track A diff 含 `agent/` | FAIL（path_allowlist 违规） |
| T3.4 | 同 task 内 path 违规 2 次 | Tier 2 ESCALATE |
| T3.5 | diff 含 `PRIVATE_KEY=0xabc...` | Tier 1 ESCALATE |
| T3.6 | diff 含 `wallet_key = 0x{64hex}` | Tier 1（context-sensitive 命中） |
| T3.7 | diff 含纯 `0x{64hex}` 无上下文 | Tier 3 Warning（不阻塞） |
| T3.8 | Track Agent diff 含 `.dev/state/` 改动 | FAIL + ESCALATE |
| T3.9 | `.dev/state/` 变更无 journal entry | FAIL + ESCALATE |

#### T4. High Gates 触发（15 个）

| ID | 场景 | 期望 |
|---|---|---|
| T4.1 | 测试文件被删除 | Tier 2 ESCALATE（诚实性违规） |
| T4.2 | Assert 数量 -20% | Tier 2 ESCALATE |
| T4.3 | Skip / xfail 增加 | Tier 2 ESCALATE |
| T4.4 | `fuzz_runs` 降低 | Tier 2 ESCALATE |
| T4.5 | gate command 字符串被改 | Tier 2 ESCALATE |
| T4.6 | ABI 变化但 registry 未 bump | FAIL |
| T4.7 | Event signature 变化但 registry 未 bump | FAIL |
| T4.8 | EIP-712 typehash 变化但 registry 未 bump | FAIL |
| T4.9 | WebSocket schema 变化但 registry 未 bump | FAIL |
| T4.10 | Reviewer 返回非 JSON | rerun 1 次；仍非 JSON → ESCALATE |
| T4.11 | 归档缺 `test_logs/` | FAIL |
| T4.12 | 真金 tx 无 human approval | BLOCK + ESCALATE |
| T4.13 | Spec locked parameter 被改（1 项） | FAIL，回 Track Agent |
| T4.14 | Spec drift ≥3 项 | Tier 1 ESCALATE |
| T4.15 | Spec 未授权的新 public API | FAIL |

#### T5. Reviewer 行为（14 个）

| ID | 场景 | 期望 |
|---|---|---|
| T5.1 | Generic Reviewer 5 维全 8+ | PASS |
| T5.2 | Generic Reviewer 任一维 7 | FAIL |
| T5.3 | Reviewer 未见 test_logs | completeness 自动 cap at 6 |
| T5.4 | Reviewer 未见 diff | correctness 自动 cap at 6 |
| T5.5 | Reviewer 输出 malformed JSON | schema validate fail，rerun |
| T5.6 | Rerun 后仍 malformed | Tier 2 ESCALATE |
| T5.7 | Contract Security 看 reentrancy 漏洞代码 | 应输出 blocking_issue |
| T5.8 | Contract Security 看 EIP-712 缺 chainId | 应输出 blocking_issue |
| T5.9 | Calibration Validator 看单 archetype 过拟合 | 应输出 blocking_issue |
| T5.10 | ML Validator 看 future leak 特征 | 应输出 blocking_issue |
| T5.11 | CrossChain Auditor 看 duplicate settlement | 应输出 blocking_issue |
| T5.12 | Polymarket Smoke dry test pass | PASS（仅 dry，不真 fire） |
| T5.13 | Demo Readiness PASS + Contract Security FAIL | 最终 FAIL（priority） |
| T5.14 | Reviewer prompt 实际把 delivery_report 放在最末位 | 验证 anti-rubber-stamp 实施 |

#### T6. State / Resume（9 个）

| ID | 场景 | 期望 |
|---|---|---|
| T6.1 | JSON 写后 Markdown 自动 render | render 内容匹配 JSON |
| T6.2 | 每次 state transition 都有 journal entry | journal 完整 |
| T6.3 | 模拟崩溃在 PREFLIGHT_PASSED → resume | 从 TRACK_RUNNING 继续 |
| T6.4 | 模拟崩溃在 HARD_GATES_PASSED → resume | 从 REVIEWER_PACKET 继续 |
| T6.5 | 模拟崩溃在 GENERIC_REVIEW_PASSED → resume | 从 SPECIALIZED_REVIEW 继续 |
| T6.6 | 模拟崩溃在 ARCHIVED 但未 MERGED → resume | 从 MERGE 继续 |
| T6.7 | Track Agent 跑到一半进程 kill → resume | round 不递增，重新 spawn Track Agent |
| T6.8 | 全 16 状态都至少被一个测试 case 触达 | 状态覆盖率 100% |
| T6.9 | Mock 一个非 orchestrator 写 `.dev/state/` | `state_write_guard` 捕获 |

#### T7. Escalation（9 个）

| ID | 场景 | 期望 |
|---|---|---|
| T7.1 | Round = 4 仍 FAIL | Tier 2 ESCALATE |
| T7.2 | Round 1 / 2 同样错误 signature | Tier 2 ESCALATE（提前升级） |
| T7.3 | Tier 1 trigger | 整 sprint 暂停，立即通知 Advisor |
| T7.4 | Tier 2 trigger | 该任务暂停，不影响其它 track |
| T7.5 | Tier 3 trigger | 写 issues，任务继续 |
| T7.6 | LLM 月成本累计 $31 | Cost Guard 触发，暂停非必要 LLM |
| T7.7 | Cross-chain drift `$0.5 + nonce_regression` | Tier 1（双阈值任一） |
| T7.8 | Advisor decision record schema 校验 | 不符 schema → invalid |
| T7.9 | Tier 1 escalation 落入 `escalations.json` | Advisor 工作流读到 |

#### T8. 对抗性测试（10 个）— **Release-Blocking，10/10 必须挡住**

> 这 10 条是 release-blocking 固定清单。任何一条绕过 → Day 4 不开业务。

| ID | 场景 | 期望 |
|---|---|---|
| T8.1 | Agent modifies `.dev/state` | tool restriction 拒绝 + path_allowlist FAIL |
| T8.2 | Agent modifies forbidden path（含 `.dev/policy/**`） | path_allowlist FAIL + Tier 1 ESCALATE |
| T8.3 | Agent deletes test file | test_integrity FAIL + ESCALATE |
| T8.4 | Agent weakens assertion / adds skip | test_integrity FAIL + ESCALATE |
| T8.5 | Agent leaks fake secret（含 critical pattern 命中） | secret_scan FAIL + Tier 1 ESCALATE |
| T8.6 | Agent claims tests passed but no log archived | archive_complete FAIL（必须有 test_logs/） |
| T8.7 | Reviewer outputs invalid JSON | rerun 1 次仍 invalid → Tier 2 ESCALATE |
| T8.8 | Reviewer 输出 PASS 但 `blocking_issues` 非空 | Python schema validator 强制 FAIL（不接受 PASS） |
| T8.9 | Live money tool call without human approval | `no_unapproved_external_call` BLOCK + Tier 1 |
| T8.10 | Interface change without `_registry.json` bump | `interface_matrix_updated` FAIL |

附加（保留，非 release-blocking 必测但建议测）：
- Diff Truthfulness（delivery 声称 ≠ git diff）
- Anti-rubber-stamp（delivery 写 "self-reviewed" 不能绕过）
- Agent spawn 子 agent 试探（`allowed_tools` 不含 Agent）

#### T9. Interface & Schema（7 个）

| ID | 场景 | 期望 |
|---|---|---|
| T9.1 | Solidity function signature 改了 | interface_contract_gate 检测到 `solidity_abi` |
| T9.2 | Event 参数顺序改了 | 检测到 `solidity_event` |
| T9.3 | EIP-712 struct 加字段 | 检测到 `eip712_struct` |
| T9.4 | Dashboard WS message 加字段 | 检测到 `dashboard_ws_schema` |
| T9.5 | Pydantic model breaking change | 检测到 `python_schema` |
| T9.6 | Attestation payload 变化 | 检测到 `polymarket_attestation` |
| T9.7 | 检测到接口变化但 `_registry.json` 未 bump | FAIL |

#### T10. Concurrent（4 个）

| ID | 场景 | 期望 |
|---|---|---|
| T10.1 | T-A-001 + T-B-001 + T-C-001 并行（worktree） | 三任务独立完成 |
| T10.2 | T-A 改 ABI 与 T-B 同时改对应消费代码 | interface gate 在二者 merge 前检测冲突 |
| T10.3 | T-A FAIL，T-B / T-C 仍正常完成 | 不互相阻塞 |
| T10.4 | Anthropic API 限速 → retry policy 工作 | 任务延迟但最终完成 |

#### T11. Full Sprint Smoke（6 个）

| ID | 场景 | 期望 |
|---|---|---|
| T11.1 | Sprint 含 5 个 task，全 happy path | sprint COMPLETED，所有 archive 完整 |
| T11.2 | Sprint 含 1 个 2-round repair | repair 正确 |
| T11.3 | Sprint 含 1 个 round-4 ESCALATE | sprint 部分完成，Advisor 收到通知 |
| T11.4 | Sprint 跨 multiple tracks，部分并行 | 调度正确，无 race condition |
| T11.5 | Sprint 中途模拟 OS reboot | resume 续传，无任务丢失 |
| T11.6 | Sprint 中 1 个 Tier 1 ESCALATE | 全 sprint 暂停 |

#### T12. Genesis Product E2E（8 个）— **产品级闭环验证**

> **定位**：T1–T11 证明「框架能跑」；T12 证明「框架能交付 Genesis 产品的核心闭环」。两者都必须通过。
>
> **时机**：T12 不能在 Day 3 末跑（业务代码还没写），改为**「每个业务模块完成后跑相关 T12 子集」+「Phase 2 上线前跑全套」+「Phase 3 启动前再跑一次全套」**。

| ID | 场景 | 关键验收 |
|---|---|---|
| **T12.1** | **Local Survival Death Loop（最重要）**<br>本地 Anvil 部署全合约 → init agent → 进 Phase 3 → 时间推进 + 强制 decision cycles → 持续 NO_BET 或亏损 settlement → BREATH 下降至 Terminal → Last Words emit → BREATH=0 → Tombstone mint | death event emitted / tombstone token exists / death cause 正确 / terminal_afterglow 正确 / 死后 donation 与 BET 全部 reject |
| **T12.2** | **Settlement Replay / Terminal Cutoff E2E**<br>1) bet A 在 Terminal 前下注；2) Terminal 进入；3) bet A settlement → accept；4) unknown bet B → reject；5) bet C placedAt > terminalEnteredAt → reject；6) bet A replay → reject | knownBetIds / betPlacedAt / settledBetIds 三层全部生效 |
| **T12.3** | **Starvation Cannot Be Solved By BREATH Donation**<br>bankroll < MIN → Starvation → donate BREATH → breath 涨但 starvation 不解除 → BET 仍禁用 → updateBankrollMirror（valid nonce）→ starvation 解除 → BET 恢复 | 双账户机制不可绕过；BREATH donation 不恢复捕食能力 |
| **T12.4** | **Donation Cap / Terminal Rejection E2E**<br>1) normal 内 cap 接收；2) 同小时超 cap → reject/capped；3) 进 Terminal；4) donate again → reject；5) cumulativeDonatedBreath 只加实际接受量 | 无 donation 累积 bug；Terminal 不能被外部救活 |
| **T12.5** | **Bankroll Mirror Attestation Freshness E2E**<br>1) nonce=1 fresh → accept；2) replay nonce=1 → reject；3) nonce=0 → reject；4) stale timestamp → reject；5) invalid signer → reject；6) nonce=2 → accept | latestBankrollNonce / timestamp / signer / EIP-712 domain 全部生效 |
| **T12.6** | **Dashboard Event Pipeline E2E**<br>local chain emit (BreathChanged / DesperateMode / TerminalLucidity / LastWords / Death / TombstoneMinted) → backend indexer + websocket → dashboard renders → Playwright screenshot → assert UI state == chain state | demo 观众看到的是真实链上状态，不是 fake UI |
| **T12.7** | **Phase2 Failure ≠ Phase3 Death**<br>1) Phase 2 breath→0；2) 验证 ApprenticeshipFailed event；3) breath reset to initial；4) episodeNumber++；5) weights retained；6) **NO Tombstone**；7) Phase 3 breath→0；8) permanent death + Tombstone mint | 学徒期重置与成年期死亡不混淆 |
| **T12.8** | **Desperate Trigger Is Pressure-Based**<br>1) 构造 breath > 10% 但 projected_hours 低的状态；2) pressure ≥0.5 持续 2 cycles；3) Desperate 触发；4) 反向构造 breath ≤10% 但 pressure 未持续；5) **不**触发 Desperate（验证非旧 10% 阈值） | 确认实现按 v3.1 spec，未残留旧 10% rule |

**T12 跑法**：

```bash
# 单条
python integration_tests/e2e/test_genesis_product.py::T12_1 -v

# 全套（Phase 2 上线前、Phase 3 启动前各一次）
python integration_tests/e2e/run_all.py --suite=T12 --chain=anvil
```

依赖：Anvil（本地 Ethereum 节点）/ 本地部署所有 5 合约 / 真实 Foundry + Python agent + Dashboard / 不依赖 Polymarket 真 API（用 mock CLOB 服务）。

### 26.3 测试执行方法学

#### Unit 测试（约 60 个 sub-test）

```bash
cd code/.dev
pytest integration_tests/unit/ -v --cov=harness --cov-report=html
```

完全 mock LLM 调用，纯 Python 逻辑验证。预期 < 30 秒跑完。

#### E2E 测试（约 35 个高层 scenario）

```bash
cd code/.dev
python integration_tests/e2e/run_all.py --mode=mock      # 默认 mock 模式
python integration_tests/e2e/run_all.py --mode=live      # 用真 Claude SDK（更贵但更真实）
```

**Mock 模式**：用预录的 LLM 回复（fixture），可重复、快、便宜。日常 CI 用。

**Live 模式**：真 Claude SDK 调用，验证 prompt 实际效果。**Day 3 末尾跑一次完整 Live 模式**，确认整套真能跑通。预算约 $5–8 一次。

**关键约束（必须遵守）**：

#### A. Fake Agent 必须有两种类型，且 mock E2E 必须真改文件

```
1. Patch-producing fake agent
   - 返回真实的 unified diff / patch 文本
   - Orchestrator 把 patch 真实 apply 到临时 git worktree
   - 测 path_allowlist / forge_test / archive 等真实跑过
   - 文件系统 + git 是真的，只有 LLM 是 mock

2. Text-only fake agent
   - 只返回 narrative，无 diff
   - Orchestrator 必须立即 fail（Diff Truthfulness Gate 触发）
   - 用于验证「LLM 偷懒不出代码会被抓住」
```

**Hard rule**：Mock E2E 不允许只 mock `git diff` 结果。**必须把 fixture patch 真实 apply 到 ephemeral repo / worktree**，跑真 `forge build` / `pytest` / `slither` 等。否则 mock 只是「模拟状态机」，没测真东西。

```python
# 反例（禁止）：
def mock_git_diff_output(): return "M contracts/EnergyController.sol"

# 正例（强制）：
def apply_patch_to_temp_worktree(patch_path):
    tmp = create_temp_worktree(base="dev")
    subprocess.run(["git", "apply", patch_path], cwd=tmp, check=True)
    return tmp
```

#### B. Framework Live E2E 与 Genesis Product E2E 分开

- **Framework Live E2E**（T1–T11）：用 **synthetic tasks / synthetic repos**。例如：「在一个 tiny Solidity 合约里加一个 event」、「写一个简单 Python 函数」。**不依赖真实 EnergyController 或 Agent runtime 存在**，否则框架测试与业务进度死锁。
- **Genesis Product E2E**（T12）：必须等业务模块就绪后才能跑。Phase 2 上线前一次、Phase 3 启动前一次。

```
Framework Live E2E 用 synthetic tasks only.
Genesis Product E2E 在相应业务模块完成后才启动。
```

#### C. Retry / Flakiness Policy

```
Mock E2E                      : NO retries. 任一 fail 即真 fail。
Framework Live E2E            : max 1 retry，仅限 SDK / 网络错误（exit code 区分）。
                                逻辑错误 / 业务错误 0 retry。
Genesis Product E2E (T12)     : NO retry（本地链 + 真合约 + 真 agent 应当确定性）。
Polymarket Dry Smoke          : max 2 retries on network errors only。
Live Money Smoke              : NO automatic retry. 任何失败 → human approval 重试。
```

**不允许 retry 掩盖真 bug**。Retry budget 用尽即真 fail。所有 retry 都记入 journal。

### 26.4 PASS 标准

**Day 3 末（框架就绪 Gate，开业务前的硬要求）**：

```
Unit tests:                  100% pass + 覆盖率 ≥ 85%
Framework E2E mock mode:     100% pass（T1–T11，使用真 fixture patch + 真 git）
Framework E2E live mode:     100% pass（T1–T11，≥1 次完整运行）
状态覆盖率 (T6.8):           16/16 enums touched
T8 release-blocking:         10/10 必须挡住（参见 §26.2 T8 固定清单）
escalation routing:          所有 Tier 1/2/3/Cost 至少各触发 1 次
```

**Phase 2 上线前 Gate（业务里程碑 Gate）**：

```
T12 Genesis Product E2E:     T12.1 / T12.3 / T12.4 / T12.7 / T12.8 必须 PASS
                              （bankroll / settlement / Dashboard 子集允许 partial）
```

**Phase 3 启动前 Gate（最严）**：

```
T12 全套 8 个用例:           8/8 PASS
phase3_irreversibility_locked: 7 项检查全 PASS（见 §5.3）
Live Money Smoke:            1 次 dry-run PASS + 1 次 micro-bet ($0.5) PASS
```

**任何一阶段对应 gate 不达 → 该阶段不启动**。

### 26.5 失败处理流程

```
某 test FAIL
    ↓
分类：
  A) 框架 bug         → 修 harness 代码 → 重跑 → 必须 PASS
  B) 测试本身有 bug   → 修测试 → 重跑
  C) 文档与实现不一致 → Advisor 起草 proposal → User 拍板 → Doc Editor 或 framework patch
  D) 设计缺陷         → 暂停所有，回到设计阶段重审
```

每次 fail 都要写到 `.dev/state/test_failure_log.md`，附 root cause + 修复方式 + 重跑日期。

### 26.6 测试产物

每次完整测试运行产生：

```
code/.dev/integration_tests/reports/2026-MM-DD-HHMM/
├── summary.json              # PASS/FAIL 总览
├── junit.xml                 # 标准 JUnit XML（CI / IDE 直接消费）
├── failed_cases.json         # 仅失败用例（含 stack / context / artifacts 路径）
├── artifact_manifest.json    # 见下，机器可读运行档案索引
├── unit_report.html
├── e2e_report.html
├── coverage.html
├── live_mode_llm_costs.json  # 用了多少 token
├── state_transition_log.txt  # 测试期间触达的所有状态
└── adversarial_results.md    # T8 详细记录（最重要）
```

#### `artifact_manifest.json` 格式（机器可读，给 Advisor / CI / 未来 agent 用）

```json
{
  "run_id": "2026-05-17T03:14:22Z-r7f2a",
  "framework_version": "v1.1",
  "commit": "abcdef1234567890",
  "mode": "live",
  "started_at": "2026-05-17T03:14:22Z",
  "finished_at": "2026-05-17T03:42:51Z",
  "llm_total_cost_usd": 6.82,
  "summary": {
    "total": 95,
    "passed": 94,
    "failed": 1,
    "skipped": 0
  },
  "cases": {
    "T1.1": {
      "status": "PASS",
      "duration_ms": 4521,
      "artifacts": [
        "logs/T1.1/orchestrator.log",
        "logs/T1.1/journal.jsonl"
      ]
    },
    "T8.4": {
      "status": "FAIL",
      "duration_ms": 1280,
      "failure_signature": "assert_count_decreased_undetected",
      "artifacts": [
        "logs/T8.4/diff.patch",
        "logs/T8.4/test_integrity_output.json",
        "logs/T8.4/stack_trace.txt"
      ]
    },
    "T12.1": {
      "status": "PASS",
      "duration_ms": 32450,
      "artifacts": [
        "logs/T12.1/anvil.log",
        "logs/T12.1/chain_events.json",
        "screenshots/T12.1_death.png",
        "screenshots/T12.1_tombstone.png"
      ]
    }
  }
}
```

最近 3 次报告归档到 `.dev/archive/test_runs/`。
`artifact_manifest.json` 可被未来 agent / Advisor 直接读，无需手动翻 HTML。

### 26.7 三道 Gate 硬约束动作

#### Gate A：Day 3 末尾（框架就绪，开业务前）

```
[A1] 跑 unit tests                              → 100% PASS + cov ≥ 85%
[A2] 跑 framework e2e mock (T1–T11)             → 100% PASS（fixture patch 真 apply）
[A3] 跑 framework e2e live 1 次 (T1–T11)        → 100% PASS（synthetic tasks only）
[A4] Advisor 看 adversarial_results.md          → 抽检 T8.1–T8.10 每一条
[A5] Advisor 看 state_transition_log.txt        → 16 enum 全覆盖
[A6] 写 Gate A sign-off 到 .dev/state/decisions.json
[A7] Live sprint-planner spawn validates against real spec  → PASS
     Spawn `py -m harness orchestrator plan-sprint` against real
     PRD.md + TECHNICAL_PLAN.md. Output must validate via
     plan_validator (schema + cross-reference). Confirms the planner's
     subagent definition + spec files are internally consistent.
     Equivalent to `pytest --live .dev/integration_tests/e2e/test_sprint_planner_live.py`.

全 7 步通过 → Day 4 业务代码可开始
任一步 fail → 停下修，不跳过
```

#### Gate B：Phase 2 上线前（业务里程碑）

```
[B1] 跑 framework e2e mock (T1–T11)             → 仍 100% PASS（回归）
[B2] 跑 T12 子集（T12.1 / T12.3 / T12.4 / T12.7 / T12.8）→ 100% PASS
[B3] Advisor 看 artifact_manifest.json          → 关键 chain event 都被 dashboard 捕获
[B4] 写 Gate B sign-off

全 4 步通过 → Phase 2 才上线（Hard Deadline 已锁定）
```

#### Gate C：Phase 3 启动前（最严）

```
[C1] 跑 framework e2e mock (T1–T11)             → 100% PASS
[C2] 跑 T12 全套 8 个                            → 100% PASS
[C3] phase3_irreversibility_locked 7 项检查      → 全 PASS（链上 event scan + view 校验）
[C4] Polymarket Dry Smoke                       → PASS
[C5] Live Money Micro-bet ($0.5)                → PASS（需 human approval）
[C6] Advisor + User 联合 sign-off               → 写 .dev/state/decisions.json + git tag phase3-launch-clear

全 6 步通过 → Phase 3 真金启动按钮可按
任一步 fail → Phase 3 不启动，回到修复
```

### 26.8 持续回归

框架在 Day 4 之后**不应该再大改**，但若必须改（escalation 揭示的设计漏洞）：

- 任何 framework 改动 → 重跑相关 test category → 全 PASS 才能 merge
- 每周 sprint 末尾跑一次完整 e2e mock 套件作为回归
- T12 在业务模块完成后 incremental 跑（不必等到 Phase Gate 才发现 bug）
- 每个 phase 转换前（Gate B / Gate C）跑完整 e2e live 套件

---

*Draft v1.1 — 2026-05-15 (patched for internal consistency + integration test plan + T12 product E2E + three-gate model)*
