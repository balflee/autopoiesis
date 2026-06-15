# Hand 1 — Active Survival 设计 spec（2026-06-15）

> superplan 范围：**仅 Hand 1**。Hand 2（live 观测台 / mock / live 信号）是**下一个** superplan，本文件不含。

## 0. 一句话目标

让 agent 在**诚实胜率（~50–58%）下也能靠风险管理活下去**，把它从"摆烂等死 / 只有过拟合才活"修成"**会动地、聪明地存活**"——并用一个**注入已知 edge 的合成 harness** 证明这件事，而不是靠回测里那个会骗人的过拟合数字。

---

## 1. 背景与根因（已用代码 + 数据查实）

### 1.1 三层框架（本项目的承重结构）
- **① 存活 = 脊椎**（本 superplan）。
- **② 数学最优 = 只当 agent 起跑先验**，不当真利润。
- **③ edge = 先测后喂**（下个 superplan）。

### 1.2 诚实账（已爬 `dashboard/public/backtest/reincarnation*.json`）
神有**两条**收入：供奉（deathbed buy-breath，`gods_revenue`）+ 什一税/租（A10 divine-tithe，`tithe_revenue`）。神总收入 = 租 + 供奉 − 种子（每世 $100 × 20 = $2000）：

| 臂 | 时间线 | 胜率 | 租 | 供奉 | 神总收入 | 净 vs 种子 |
|---|---|---|---|---|---|---|
| numerical | 真实 | 82% | 关 | $50,719 | $50,719 | **+$48,719** |
| g0 | 真实 | 82–96% | $32,700 | $6,000 | $38,700 | **+$36,700** |
| g1 | 真实 | 82% | $11,420 | $0 | $11,420 | **+$9,420** |
| **g2** | **打乱时间线** | **~50%** | $1,880 | $0 | $1,880 | **−$120** |

**读法**：真实 timeline 的"利润"全是 **82% 样本内过拟合**（OOS 不可能）被租+供奉抽走；**一旦打乱时间线杀掉过拟合（g2），神的收入塌成 −$120 = 纯回收种子**。**抽水（租+供奉）只搬钱、不造钱。**

### 1.3 根因（本 spec 要解决的核心）
**呼吸经济 `loss_multiplier=5`：输一注扣 breath 是赢一注的 5 倍**（`agent/backtest/survival_season.py:787-791`，`update_breath_from_pnl`：`pnl<0 → effective = pnl*loss_multiplier`）。叠加 A10 租（`tithe_every=20` 场扣 `tithe_breath_cost=5` 或 `$20` 现金；`reincarnation.py:1367-1370`，扣费逻辑 `sandbox_phase2_loop.py:1875-1897`），结果：

> **存活门槛被顶到 ~80%+ 胜率。** 真实数据上 82%（过拟合）活很久；打乱后 ~50%（诚实）120 场就被租+输扣死。**当前设定下，诚实胜率的死亡是数学注定的——A2/A14/探索地板都救不回，因为船被设计成必沉。**

### 1.4 g2 "0 注命怎么死的"已查实
租按**看到的市场数**走（不论下不下注，`sandbox_phase2_loop.py:1880`：`if self._divine_tithe and inputs is not None: self._markets_since_start += 1`）。g2 L4：0 注、看 140 场、付 $100 + 扣 10 breath → 死。**"不出手"惩罚机制（A10 租）已存在并生效；弃权逃不掉。** 所以本 spec 的重点**不是**"惩罚弃权"（已做），而是"**让诚实胜率可生存 + 保证 agent 不冻结而能发现 edge**"。

### 1.5 运行时澄清（已查实）
- groundhog/reincarnation 回测路径 = `scripts/run_reincarnation.py` → `agent/backtest/reincarnation.py::run_groundhog_export`（~1342-1823）→ `agent/backtest/survival_season.py::run_survival_season`（per-life）→ `agent/runtime/sandbox_phase2_loop.py::SandboxPhase2Loop.run/_tick`（per-tick）→ `agent/engines/decision.py::decide`。
- `sim/runner.py`（initial_breath=1000 + 每 tick 税）是**遗留/未用**于此路径（已 grep 确认无 import）。**本 spec 一律不碰 `sim/`。**
- **共享件**（回测 + live 共用）：`decision.py`、`weight_updater.py`。**回测专属**：`survival_season.py`、`reincarnation.py`。

---

## ⚠️ R9 执行实证修订（2026-06-16，supersedes 下文冲突处）

Phase-4 执行实测推翻了"恒定信号 + 激进 sizing"的原设计：宽参数扫(m 1→6、breath、sizing、tithe+tribute 全开)下 +edge 与 0-edge **逐字节相同、全死、金库 < 种子**,agent **几乎不下注(~2/命,被租扣死)**。根因:**信号恒定**(agent 没法挑 edge)+ **sizing=0.95 太激进**(一注 ×m 亏损就死,edge 来不及复利)。**已验证的修法(干净 0.00 vs 1.00 分离)三件套**:
1. **varying-edge 世界**:每行信号 `t=Uniform(-0.6,0.6)`(符号=边、|t|=强度),`scores={k:t}`;agent 选的边赢率 `min(0.95, 0.5+gain·|t|)`——`gain>0`=真 edge、`gain=0`=噪声。agent 的 `decide()` 据此**挑注**(强信号下、弱信号弃),edge 才用得上。
2. **温和静态 sizing**(承重杠杆):`fragile_max_breath_risk_pct` 从部署的 0.95 降到 **≈0.2**(现成参数,**不需新代码机制**)——小注多打让 edge 复利。
3. **足够 breath 跑道**(`initial_breath≈70`)。

**验证甜点**:gain=0.5、breath=70、fragile≈0.15–0.2、m 1.2–1.5 → **+edge 死亡率 0.00 / noise 1.00**。
**对 §3 的影响**:§3.A 标定改成**联合扫 (m, fragile, breath)** + varying 世界;§3.E 验证用 varying 世界;§3.C 的**静态温和 sizing 进 scope**(即 A14 意图的静态版),而 §3.C 那套**自适应 desperate-flag A14 仍留 Hand-1.5**(只是把可生存区间做宽的 refinement)。

## 2. 成功判据（存活类，**非 PnL**）

1. **可生存性（核心）**：重标定后，胜率 ~52–58% 的 agent 在合成 harness 上**存活显著长于现状**；而胜率 ~50%（纯噪声）的 agent **仍大概率死**（保住 permadeath 赌注，不能把世界改成"谁都死不了"）。
2. **不冻结**：探索地板生效——出手率有正下限，不再像旧 g2 那样 20 世只下 20 注。
3. **能发现并吃下 edge**：在注入已知 edge `e` 的合成世界里，active agent **找到并利用 e**（出手率 + PnL/存活随 e 增大而上升），而不是冻结错过。
4. **地板诚实（g1/g2 回归已 DESCOPE）**：探索地板的**价值**=在**隐藏-edge 世界**（信号低于门但真有 edge）agent 用 ε-探针**发现并（经学习）吃下**这条 frozen agent 永不碰的 edge → `total_bets`↑ + `mean_final_breath`↑（三指标同读；纯弃权者不死，故不用死亡率）；**纯噪声世界探索成本为正**（loss×m 物理使然），据实**记录、不假装为零**。⚠️ **g1/g2 回归已剔除**：它们是 m=5 + tithe-ON + 打乱的**另一个经济体**，`run_survival_over_rows` 无法重建，且 Hand-1 故意把 m 降到 ≈1.2、tithe OFF——不能拿被替换的旧经济体当回归基准（§8 的 −$120 是异经济体数字）。

> **评审后降级（2026-06-15 Phase 2 panel）**：原 A14（濒死缩仓）、A2（死亡梯度）判据移到 follow-up **Hand 1.5**——见 §3.C/§3.D/§6。本次成功判据只剩 1–4。

---

## 3. 设计（每块附 file:line 落点）

### 3.A 数据驱动呼吸经济重标定（**第一步，用户明确要求**）
**做什么**：一个**离线分析/sweep**（无 LLM），把呼吸经济参数（`loss_multiplier`、`tithe_breath_cost`/`tithe_every`、`initial_breath`、必要时 `conversion_rate`/lung-expansion 阈值）标定到：
- 诚实胜率 `p≈0.55` + 良好风险管理 → 可生存目标 horizon；
- 无 edge `p≈0.50` → 仍大概率死（permadeath 赌注不破）。

**关键约束（写死）**：标定目标 = **可生存性 + edge 可探测性**，**绝不是 PnL**（零 edge 数据上 PnL 优化必把一切压成"别下注"，退回 g2）。

**方法（2026-06-15 评审后改：不用闭式玩具模型）**：真实 breath 物理是 **odds 依赖、size 缩放的 USD PnL，只对亏损 ×`loss_multiplier`**（`survival_season.py:787-791`）；闭式 `break_even=m/(1+m)` 是失真假象，评审实测在 35/0.5/5 下双判据**根本不可行**。改为**直接跑真实短 numerical season** 标定：用合成-edge 世界（§3.E）把胜率控到 `p≈0.55`（注入 edge）与 `p≈0.50`（edge=0），在参数网格（`loss_multiplier`，必要时 `initial_breath`/tithe）上**实测寿命/死亡**，选满足双判据者。基线锚在真实调用点 **`run_reincarnation.py:156-157`**（`loss_multiplier=5.0, initial_breath=35.0`）——参数 plumbing Task 要把这俩字面量替换成标定值，否则重标定不生效。

**落点**：新脚本，sibling 于 `agent/backtest/find_optimal_config.py`（后者已 sweep `min_edge/min_confidence/min_bet_size/kappa` 等，bounds 在 :90-112）。⚠️注意 `sim/calibrate_sprint7_tennis.py` sweep 的是**遗留 `sim/` 呼吸模型（breath0=1000+每 tick 税），与 groundhog 不同**，不可直接复用，但其 LHS+BO 框架（`sim/sweeper.py`）可借。数据：`reports/backtest/_signal_rows.json`（4925 行）、`agent/backtest/_cache_tennis/`、seed `docs/backtest/value_seed_v3.json`。

### 3.B 探索地板（decision engine）
**做什么**：让出手率**永不归零**——abstain 门后加 ε-greedy：以概率 ε 即使在 `min_edge`/`min_confidence` 门下也下一个**小注（探索注）**；探索注 size 受风险管理约束（微注）。新增 genome 旋钮 `exploration_epsilon`（bounded，初值取自 3.A 标定；是否让 advisor 微调 = plan 里定）。

**落点**：`agent/engines/decision.py:242-424`（`decide`）。当前**完全确定、无 RNG**；abstain 门在 :317-322（min_confidence）、:367-384（min_edge，storm 条件 `eff_min_edge`）、:407-415（min_bet_size $5，**故意不可 advise**）。注入点：min_edge 门（~:380）与 min_bet_size 门（~:411）之间。**需引入可 seed 的 RNG 线**（harness 确定性 → RNG 必须 seeded、可复现；现 `survival_season` 的 "random" baseline 已用 `random.Random(seed)` 可参照）。

### 3.C A14 呼吸感知 sizing
> ⏸️ **DEFERRED → Hand 1.5（2026-06-15 评审后降级）**：`desperate` 是 **latched + snapshot 持久化 + 8 处读取**的状态，且 `DESPERATE_BET_SIZE_CAP` 是 **live/backtest 共享的锁定常量**（TP §4.7，改它会动 live）。要做对得用独立 near-death 缩仓参数 + 正确 latch 接 snapshot——本次不做。下面留作 reference。

**做什么**：把濒死**放大仓位**掰正成**缩小仓位**。
**⚠️前置（已查实）**：`desperate` 在回测路径**从不被计算**（`sandbox_phase2_loop.py:1033` 初始化 False、:1734 传入 `decide` 恒 False）——所以现有 `DESPERATE_BET_SIZE_CAP=0.50`（decision.py:399-402）在回测**休眠**。A14 = ①在回测 tick 里**接通 desperate 计算**（`breath < desperate_threshold`，阈值见 `sim/params.py` 概念但需在 groundhog 侧定义）；②把 `desperate=True` 时的 `bet_size_cap` 从 0.50 改为 **< NORMAL(0.30)**（如 0.10–0.15）或令 `rho_eff` 随 survival pressure 线性下调。
**附带**：`agent/engines/weight_updater.py` 的 desperate `LR×2` + 解冻 β/ρ（:86, :332-339）是否一并掰正，plan 里评估（可能加剧濒死乱学）。

### 3.D A2 死亡感知信用分配
> ⏸️ **DEFERRED → Hand 1.5（2026-06-15 评审后降级）**：loop 持有的 `self._weight_updater` 是 settlement-poller 的 **Protocol（只有 update()）**，非 engine 的 WeightUpdater（numerical 臂是 Noop）→ 直接调会 AttributeError；且**没有死亡窗口分数累加器**（只有 per-tick）；还涉及 tribute 顺序、Terminal phase 冻结、改 tombstone hash、look-ahead 审计。本次不做。下面留作 reference。

**做什么**：给死亡一个梯度。新增 `WeightUpdater.update_from_death(...)`（与 `update_from_settlement` :349-425 平行），把**负信号**回传给驱动死亡螺旋的引擎/决策（最后一段亏损轨迹里高置信却错的引擎）。
**落点**：在 `sandbox_phase2_loop.py:1891-1897` 死亡检查处、`_die` 之前调用。当前死亡路径（`_die` :2153-2234）**只 hash 权重 + kill，零学习信号**（已查实：唯一学习通道是结算 `settlement_learner.py:68-101`，死亡瞬间无结算 → 零梯度）。

### 3.E 验证 harness — 合成已知-edge（**不能只用 g2**）
**为什么**：g2（打乱 = 零 edge by design）里"冻结+优雅死"本就是**正确且必死**的——逼它多下注只会死更快。测不出"探索地板帮 agent 找 edge"。
**做什么**：新 harness 往数据注入一个**已知大小 `e` 的合成 edge**（ground truth 已知），跑重标定+active agent，验证 §2 的 1–5。并回归 g1（真实，确认仍吃下过拟合 edge）与 g2（打乱，确认仍正确最小化损失）。
**落点**：新测试 harness，复用 `survival_season`/`run_reincarnation` 入口；合成信号生成器（确定性、seeded）。

---

## 4. 数据流 / 接口
- 3.A 标定输出（推荐参数 + `exploration_epsilon` + 重标定的 `loss_multiplier`/`tithe`/`breath0` + desperate 阈值/缩仓系数）→ 写入 seed/genome 配置（`docs/backtest/value_seed_v3.json` 或新 seed）→ 被 `run_reincarnation.py` / `survival_season` 读取。
- seeded RNG 从 harness 顶层穿到 `decide`（新增构造参数），保证确定性可复现。
- 合成 edge harness 通过新 CLI flag 注入 `e`，与既有 `--shuffle-timestamps-seed` 并列。

## 5. 测试
- 单测：探索地板按 ε 触发（统计出手率）；A14 在 desperate 下缩仓（size↓）；A2 死亡产非零梯度且方向正确；重标定参数给出目标存活曲线。
- 集成：合成-edge harness 跑通 §2 判据；现有 reincarnation 产物回归（schema/account 不破，`reincarnation.py:2002-2034` 的 tithe 会计自检仍过）。

## 6. 非目标 / 推迟
- **A14（濒死缩仓）、A2（死亡梯度）→ Hand 1.5**（2026-06-15 评审后降级）：深缠共享 live 代码（decision.py 锁定 DESPERATE cap）、持久化 latch 的 `desperate`、settlement-poller 的 WeightUpdater Protocol 身份、tombstone hash、look-ahead——值得单独一轮做对。本次只落 §3.A 重标定 + §3.B 探索地板 + §3.E 验证。
- **Hand 2 全部**：live 观测台、mock、live 信号接线、真 edge 主张。
- ② 数学最优"作为先验"的完整形式化（本 spec 只用现成 seed 当起跑先验）。
- `sim/` 遗留运行时的任何改动。

## 7. 风险与诚实声明
- **数值对照组陷阱**：若重标定后的可生存策略本身是个固定函数，回测里 agent 学习仍可能冗余（趋向静态最优）。**接受**：回测证"**能**学会主动存活"+ 出 demo；live 才是它不可替代的地方（Hand 2）。
- **重标定可能只是搬动死亡陷阱**：必须用 §2.1 双判据（p≈0.55 可活 **且** p≈0.50 仍死）约束，避免把世界改成"谁都不死"。
- **探索地板在零 edge 世界只是换种死法**：靠 §3.E 合成-edge 验证其真实价值；g2 上的死亡被接受为正确。
- **provider**：标定 = 离线数值分析（无 LLM）；机制单测/合成验证用 numerical provider 即可（不需 Gemini/MiniMax）。
- **commit 前确认 git 账号 = balflee**（256016480+balflee@users.noreply.github.com）。

## 8. 基线（冻结，用于对照）
- 现状 g2（诚实臂）：净 −$120、~50% 胜率、20 世仅 20 注、租扣死。
- 现状 g1/g0/numerical：+$9,420 / +$36,700 / +$48,719，**全为 82% 样本内过拟合**经租+供奉抽取，OOS 句号已由 NO_GO 系列坐实。
