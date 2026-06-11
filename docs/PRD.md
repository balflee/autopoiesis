# Autopoiesis — PRD (Draft v0.4)

> 基于 Arbitrum Orbit L3 的 AI Agent 链上生存与自我进化沙盒
>
> 一个在受限数字世界中必须靠预测**网球比赛**续命的 AI Agent，会学习、会进化、会真的死亡。

---

## 0. 元信息

| | |
|---|---|
| **黑客松** | Arbitrum Open House London Online Buildathon |
| **目标赛道** | Best Agentic Project（15,000 USDC 专项奖） |
| **开发窗口** | ~3 周（含 Phase 1 训练 + Phase 2 影子运行） |
| **核心交付** | 部署在 Orbit L3 的端到端可运行系统 + LIVE Demo + Demo 视频 |
| **当前状态** | Sprint 6 已交付（5 合约部署到 Robinhood Chain testnet + Arbitrum Sepolia）；Sprint 7 进入 Phase 1 backtest + tennis pivot |

> **📌 重大方向调整（2026-05-25）— NBA → Tennis 切换**
>
> 项目最初按 NBA 设计，sprint 6 完成基础设施部署后发现 Polymarket NBA market 在 2024-25 之后实质崩塌（per-game NBA market 从 2023 年 ~600 个降到 2025 年 ~0），导致 agent 真跑时无 market 可下注。**切换到网球（Tennis）**：
> - Polymarket tennis 现在 active 90+ per-game markets（24/7 ATP + WTA tour + 4 大满贯）
> - Jeff Sackmann ATP/WTA GitHub CSV 提供 1970-2026 全部历史数据（MIT 协议免费）
> - 已部署合约 + BREATH 经济参数 + 三阶段架构均 sport-agnostic，**不变**
> - 改动集中在 α₁ 引擎 + Track E 数据 source + β₂ subreddit + demo 叙事
>
> 本 PRD 整篇已重写为 tennis 版。原 NBA 版保留在 git 历史中。

---

## 1. 项目愿景（One-liner & 三句话版本）

**一句话**：
> 我们造了一个在 Arbitrum 链上必须靠预测**网球比赛**续命的 AI Agent，它既看数据又看舆论，会在生存压力下自我调整，死了就永久死亡。

**三句话**：
> Autopoiesis（前 Genesis Experiment）是一个部署在 Arbitrum 生态链上的「数字生命」实验（v1：Robinhood Chain L2 主部署 + Sepolia + Polygon Amoy 平行；v2 roadmap：专属 Orbit L3 + BREATH chain-level native gas）。一个自主 AI Agent 必须在资源极度受限的链上环境里，通过预测 Polymarket 上的网球比赛盘口（ATP + WTA tour，全年不停）为自己赚取「生命能量」，否则永久死亡。它的死亡是不可逆的链上事件，临终遗言永远刻在链上，遗产可被下一代继承。

---

## 2. 为什么这是好项目（Sell Points）

| 卖点 | 解释 |
|---|---|
| **链上必要性站得住** | Permadeath 必须 trustless（合约层强制 + 跨任何 EVM 链一致）。BREATH 作为 chain-level native gas（专属 Orbit L3 部署）是 v2 roadmap；v1 用 Soulbound ERC-20 BREATH + EnergyController.burnForAction() + 归零 kill()，经济效果等价 |
| **叙事力强** | 「童年 → 学徒 → 成年 → 死亡」的完整数字生命周期，5 分钟 Demo 一镜到底 |
| **工程可信度高** | 三阶段架构是工业级 ML 部署的标准路径，技术评委一眼能看出认真 |
| **Demo 戏剧性** | 临终遗言 + 能量条归零 + Tombstone NFT mint = 评委会截图的 Money Shot |
| **可延续性** | Tombstone NFT lineage 让项目天然有 V2、V3，超越 hackathon |
| **窄而深 — 但密度足够** | 收敛到**网球**单一运动，工程量可控，叙事清晰；同时 ATP + WTA 全年 tour + 4 大满贯保证 24/7 都有 markets 可下注，避免「agent 无事可做」的戏剧性死局 |

---

## 3. 核心机制：三阶段生命周期

这是整个项目的骨架，所有其他设计都围绕它展开。

### Phase 1：童年（Childhood）— 历史训练

| 项目 | 配置 |
|---|---|
| **位置** | 链下沙盒 / 内存 |
| **数据源** | **Jeff Sackmann ATP + WTA GitHub CSV（1970-2026）** + Reddit r/tennis 历史 dump + Polymarket tennis 历史盘口（2024-至今） |
| **LLM 引擎** | ❌ **OFF**（防止训练数据污染） |
| **资金** | 无 |
| **能量机制** | 无 |
| **权重更新** | ✅ 持续学习 |
| **目的** | Bootstrap 初始权重，让 Agent 学会基础的「技术信号 + 群众关注度」配比 |
| **持续时间** | 黑客松第 1–2 周 |

**关键设计决策**：
- 训练期完全不用 LLM 做情绪分析，避免 LLM 在预训练数据里已经知道比赛结果造成的 look-ahead bias
- 用 **Reddit 历史帖子的发帖量 / 点赞量 / 评论速率** 作为「群众情绪」的代理变量（这些是时间戳带着的事实，不可污染）
- 严格 point-in-time 切片：所有特征必须在比赛开始前可观测
- **网球特有约束**：surface（clay/grass/hard/carpet）和 best-of (3 vs 5 sets) 是 input feature 不是 target，避免某些 player 在某 surface 历史成绩被泄漏到「未见过」的 surface

### Phase 2：学徒（Apprenticeship）— 实盘影子训练

| 项目 | 配置 |
|---|---|
| **位置** | 链下计算 + L3 链上记账 |
| **数据源** | 实时 ATP/WTA tour（Sackmann live data + tennis-data.co.uk fixtures） + 实时 Twitter/Reddit r/tennis |
| **LLM 引擎** | ✅ **首次激活**（成年礼时刻） |
| **资金** | 影子（虚拟下注） |
| **能量机制** | 模拟能量增减（首次出现） |
| **权重更新** | ✅ 持续学习（核心 calibration 期） |
| **目的** | 让 Agent 第一次接触 LLM 语义层情绪信号，校准 LLM 权重 |
| **持续时间** | 3–7 天（建议 5 天） |

**关键设计决策**：
- LLM 在这里**首次激活**，Dashboard 上会显示「感性引擎: OFFLINE → ONLINE」的视觉切换
- 影子下注 → 真实结算反馈 → 权重调整，形成完整 RL 闭环
- Phase 2 期间所有决策、复盘、权重变化全部上链作为「学徒日记」
- **网球特有**：单日通常有 10-50 场比赛（高频决策密度），Agent 经历的样本规模比 NBA 多一个数量级

### Phase 3：成年（Adulthood）— 实盘真金

| 项目 | 配置 |
|---|---|
| **位置** | 全程 L3 + 真实 Polymarket |
| **数据源** | 实时 ATP/WTA + 实时 Twitter/Reddit r/tennis |
| **LLM 引擎** | ✅ ON |
| **资金** | **真金**（小额，但真死亡风险） |
| **能量机制** | 真实 L3 token / Gas，归零 = 不可逆永久死亡 |
| **权重更新** | ❌ **冻结**（人格定型） |
| **目的** | Demo —— 在评委面前真实生存 |
| **持续时间** | Demo 期 24h（或 demo 前 24h 启动并 LIVE 监控） |

**关键设计决策**：
- 权重在 Phase 3 开始时冻结，Agent 用它过去学到的一切决一死战
- **绝境觉醒**（可选加分）：能量低于 10% 时权重解锁，进入「濒死的赌徒」状态
- Phase 3 启动时注入固定「初始生命基金」（建议 0.05 ETH），Phase 2 的虚拟能量不携带
- **网球特有**：Demo 时机优选「大满贯期间」（澳网 1 月 / 法网 5-6 月 / 温网 7 月 / 美网 8-9 月）—— 每天 30+ 场，agent 真有大量 markets 可下注

### 三阶段切换机制

- **手动 gating**：每阶段切换由管理员签 transaction 触发
- **链上凭证**：每次切换记录 `phaseTransition(from, to, weights_hash, timestamp)` 事件
- Demo 时按下按钮触发 Phase 3 → 评委亲眼看见「Agent 进入生死场」

---

## 4. 双引擎设计（2 层 5 参数结构）

为了让「自我进化」有足够大的优化空间，并把 Polymarket 原生策略融入决策，引擎采用**2 层结构**：顶层保留「理性 vs 感性」叙事，底层细分为 5 个子信号。

### 4.1 引擎拓扑

```
                       决策分数
                          │
            ┌─────────────┴─────────────┐
       理性引擎 (W_R)              感性引擎 (W_S)
       W_R + W_S = 1
            │                              │
       ┌────┼────┐                    ┌────┴────┐
       α₁   α₂   α₃                   β₁        β₂
   网球技术 盘口 Smart                 LLM       Reddit
       信号  动量  Money               语义      关注度
       α₁+α₂+α₃ = 1                  β₁+β₂ = 1
```

### 4.2 各子信号说明

| 参数 | 引擎 | 输入 | 阶段激活 | Polymarket 原生? |
|---|---|---|---|---|
| **α₁: 网球技术** | 理性 | UTR/Elo 差、首发率、保发率、破发率、近 10 场胜率、surface 适应度（clay/grass/hard/carpet）、best-of 制式 (3-set vs 5-set)、距离上一场天数、tour level (Grand Slam/Masters 1000/ATP 500/250/ITF)、H2H 历史 | Phase 1 起 | 否 |
| **α₂: 盘口动量** | 理性 | 隐含概率 vs 开盘、价格变动速率、盘口深度差、买卖盘失衡 | Phase 1 起（用历史盘口） | ✅ |
| **α₃: Smart Money** | 理性 | Polygon 链上 top-N 钱包在该 market 的开仓方向/体量 | Phase 1 起（用历史链上数据） | ✅✅ |
| **β₁: LLM 情绪** | 感性 | 实时 Twitter/Reddit r/tennis 文本的语义情绪 | Phase 2 起 | 否 |
| **β₂: Reddit 关注度** | 感性 | r/tennis 发帖量、评论速率、点赞密度（粗糙数值代理） | Phase 1 起 | 否 |

**网球 vs NBA 在 α₁ 上的关键差异**：
- 网球是个人项目 → 不存在「团队默契 / 替补深度」，更纯的 player-level signal
- Surface 是强解释变量（Nadal on clay, Federer on grass, Djokovic on hard 都有 surface bias）
- 5-set vs 3-set 决定 stamina / variance（大满贯男单 5 盘 vs 其他 3 盘）
- H2H 对网球决定性远高于 NBA（小样本下个人对个人的心理优势真实存在）

### 4.3 决策融合公式 + 下注体量

**预测分数**：
```
Rational  = α₁ · Tennis_Tech + α₂ · Market_Momentum + α₃ · Smart_Money_Bias
Sentient  = β₁ · LLM_Sentiment + β₂ · Crowd_Volume
Score     = W_R · Rational + W_S · Sentient
约束：α₁+α₂+α₃ = 1，β₁+β₂ = 1，W_R+W_S = 1
```

**下注体量**（第 6 个学习参数）：
```
bet_size = ρ · kelly_optimal · confidence · energy_balance
ρ ∈ [0, 1]   # Agent 学习的风险偏好系数
```

**安全护栏（硬编码，不可学）**：
- 单次下注 ≤ 30% 当前能量（绝境模式下放宽到 ≤ 50%）
- 同时最多 3 个持仓
- ρ 永远 ≤ 1（即不会超过凯利最优解，避免过度激进）

**独立优化参数：6 个**
| 参数 | 含义 |
|---|---|
| W_R | 理性 vs 感性 顶层占比 |
| α₁ | 网球技术权重 |
| α₂ | 盘口动量权重 |
| β₁ | LLM 情绪权重 |
| α₃ 子参数化 | Smart Money 权重 |
| **ρ** | **风险偏好（下注体量系数）** |

**初始值**：W_R=0.6, W_S=0.4；α₁=0.5, α₂=0.3, α₃=0.2；β₁=0.6, β₂=0.4；**ρ=0.5（即半 Kelly）**

### 4.4 阶段切换中的参数行为

| | Phase 1 | Phase 2 | Phase 3（正常） | Phase 3（绝境觉醒） |
|---|---|---|---|---|
| α₁ 网球技术 | ✅ 学习 | ✅ 学习 | ❌ 冻结 | ❌ 冻结 |
| α₂ 盘口动量 | ✅ 学习（历史盘口） | ✅ 学习 | ❌ 冻结 | ❌ 冻结 |
| α₃ Smart Money | ✅ 学习（历史链上） | ✅ 学习 | ❌ 冻结 | ❌ 冻结 |
| β₁ LLM 情绪 | ⏸️ Inactive（β₂=1） | ✅ **首次激活**+ 学习 | ❌ 冻结 | 🔓 **重新解锁** |
| β₂ Reddit 关注度 | ✅ 学习 | ✅ 学习 | ❌ 冻结 | 🔓 **重新解锁** |
| W_R / W_S | ✅ 学习 | ✅ 学习 | ❌ 冻结 | ❌ 冻结 |
| **ρ 风险偏好** | ✅ 学习 | ✅ 学习 | ❌ 冻结 | 🔓 **重新解锁** |

**Phase 1 特殊设计**：因 LLM 不参与，感性引擎仅由 β₂ 驱动（β₁=0, β₂=1）。Phase 2 开始 β₁ 解冻，Agent 经历「语言中枢首次激活」的转变。

### 4.5 为什么 2 层 6 参数恰到好处

- **优化空间足够**：6 个独立参数 + Sackmann 数据库中 50+ 年共 ~500K 场专业网球比赛，能展现明显的「学习曲线」
- **学习内容完整**：Agent 既学「预测谁赢」（5 个引擎权重），也学「该下多重」（ρ 风险偏好）。后者是真实量化系统里**最重要的可学参数之一**
- **叙事不糊**：顶层「理性 vs 感性」的简单故事保留，子层是细节偏好
- **Dashboard 视觉**：顶层一条主线（W_R vs W_S 比例）+ 下层热力图（子信号权重 + ρ 单独一条曲线）
- **Polymarket 原生卖点**：α₂（盘口动量）+ α₃（Smart Money）是离开链就不成立的信号
- **绝境觉醒映射完美**：感性引擎 + ρ 在绝境同时解锁 → 「理性的人在绝境变成了赌徒（下大注），但他还知道自己原本相信什么数据（α 不变）」

### 4.6 Per-tick narrative（内心独白·短版）

每个 tick 结束时，Agent 用 LLM（Haiku tier）生成一行 1-2 句的「内心独白·短版」写入 `memory_bank/summary/tick_<N>_brief.md`。**这与 §4.1 step 7 的 reflection 不同**：reflection 只在 BET tick 触发，是完整复盘；narrative 在**每个 tick** 触发（BET / NO_BET / 仅扫描盘口），是一句话的「我刚才在想什么」。

格式示例（**网球版**）：
> `tick_847.md`：「I trusted the crowd louder than the data again — bet Alcaraz ML at -200 on clay even after Reddit hype around his fatigue from a 5-set quarterfinal.」
> `tick_848.md`：「Outcome arrived. Lost $40. The H2H signal had been screaming all along — Sinner has owned this matchup on slow surfaces.」
> `tick_849.md`：「Reflection: never trust β₁ alone when α₁ surface signal contradicts.」

**失败语义**：LLM 调用 fail-fast → 模板 fallback（无 retry，避免 tick budget blowout）。模板：BET 路径 `"Bet $X on {market} (score={s}, edge={e}%)"`；NO_BET 路径 `"NO_BET on {market} ({reason})"`。Agent 永远不会因为 LLM 不可用而中止 tick —— PRD §6「agent 必须活下去」不可违反。

**为什么需要这个 vs 已有的 reflection**：reflection 是事后复盘（BET 才有），narrative 是 in-the-moment 的「我此刻在想什么」（每 tick 都有）。Consciousness archive（dashboard PLAYBACK 模式 + Tombstone NFT 内含 memory_bank）需要每个 tick 都有可读叙事；只靠 BET reflection 会有大段空白。

**Memory persistence**：narrative 与每 tick 的 signals / fusion / decision / outcome / weights 一起写入 `.agent_state/memory_bank/`。详见 §8 中部 思维流 PLAYBACK 模式（dashboard 播放使用）+ §5.1 C Tombstone NFT（含整个 memory_bank tarball IPFS CID）。

---

## 5. 死亡机制（项目的灵魂）

### 5.0 三段下坠：Desperate → Terminal Lucidity → Death

Phase 3 期间触发三段递进式状态变化，每段都有视觉转变。**触发条件以 §6 为权威**：

| 触发 | 状态 | 机制 | 视觉 |
|---|---|---|---|
| 正常 | Normal | 权重全冻结，正常下注 | 蓝色基调 |
| **`pressure ≥ 0.5` 持续 2 个 decision cycles** | **绝境觉醒（Desperate Mode）** | β₁/β₂/ρ 重新解锁；学习率 2×；下注上限放宽到 50% | 顶层切红，标注 "Risk preference changed" |
| `breath ≤ 5% × INITIAL_BREATH` | **临终清醒（Terminal Lucidity）** | LLM 生成 Last Words；状态 sticky 不可逆；外部 replenish 禁用 | 全屏 Death Watch |
| `breath == 0` | **永久死亡** | 合约触发 die()，Tombstone NFT mint | 静止灰白 + Tombstone 动画 |

> **关键变更**：Desperate Mode **不再**由绝对能量阈值（≤10%）触发。改为基于 Survival Horizon **pressure 计算**：考虑当前消耗速率与目标生存窗口的差距。详见 §6.8。

**Starvation Mode**（第三种死亡路径）触发条件 `bankroll_usdc < MIN_BET_SIZE`，详见 §6.9。

### 5.1 三件套：Permadeath + Last Words + Tombstone NFT

**A. 永久死亡（不可逆）**
- 能量归零触发智能合约 `kill()`
- Agent EOA 永久标记 dead，状态全部冻结
- 规则写进合约，连项目方都不能改 —— trustless

**B. 临终遗言（On-chain Last Words）**
- 能量低于 5% 时进入「terminal lucidity」状态
- LLM 生成最后的复盘 + 对后代的话
- **完整写入链上事件 log**，永远可查
- Dashboard 一字一字打出来，配能量条归零倒计时

示例（**网球版**）：
> *"Hour 23:47. Energy: 0.03 ETH. I bet wrong on the Alcaraz quarterfinal — I trusted Twitter momentum over the surface-adjusted Elo gap, again. If there is a next iteration of me, please remember: when the H2H sample is small but the surface specialist is clear, trust the data over the crowd."*

**C. Tombstone NFT（数字遗产）**
- 死亡瞬间自动 mint，包含：
  - 最终的 weights vector
  - 总决策历史 hash
  - Last Words 文本
  - 生死曲线 SVG（on-chain 渲染）
  - Phase 1/2/3 的关键统计
  - **`memoryBankCid`：完整 memory_bank tarball 的 IPFS CIDv1**——Agent 全部 tick 记录（signals / fusion / decision / outcome / weights / reflections / narratives）打包后的可浏览数字心智。**NFT 持有者可逐 tick 浏览这个 Agent 一生的决策。**这把 NFT 从「JPG + 几个 hash」升级成「一个真实存在过的 AI 心智的数字遗骸」。
- IPFS pin 失败时（重试 3 次后）NFT 仍 mint，仅 `memoryBankCid` 为空字符串 + 链上 `TombstoneMintedWithoutMemoryBank` event 警报（降级路径）
- **可被下一代 Agent 继承**作为初始权重 → V2 的种子。**继承的是 memory_bank tarball 完整内容**（weights vector + 全部决策史 + reflections + narratives），不只是 weights —— 每一代 Agent 真正记得它的祖先。详见 §13。

### 5.2 外部押注层（**V2 / Stretch Goal — 已延后**）

任何人可在另一个 prediction 合约上押注：
- "Will Agent survive past hour X?"
- "Will Agent's energy stay above Y%?"

**决定**：Week 1/2 不投入任何工作量。Week 2 末进度提前才考虑做最小版本，否则推到 V2。

死亡 = 真实 settlement event。把项目从「装置艺术」变成「真 prediction market dApp」。

---

## 6. 生存机制（BREATH Economy）

### 6.0 核心原则

> **Profit buys time, not safety.** Agent 不在追求致富，Agent 在购买未来生存时间。

Pitch 句序列（Demo 字幕用）：
1. *"Profit buys time, not safety."*
2. *"Loss burns future."*
3. *"Action itself is the toll."*
4. *"Even rational restraint does not feed the body. The hungry must hunt — or starve."*
5. *"Once terminal, the agent can no longer beg. Only its past decisions can save it."*
6. *"External breath lets it linger. Only its own hunts can rearm it."*
7. *"Donations are external life support, not earned survival."*

### 6.1 双账户架构

| 账户 | 位置 | 含义 | 占位初始 |
|---|---|---|---|
| **BREATH** | Arbitrum Orbit L3（`EnergyController`） | 生命能量；唯一死亡判定依据 | 8,000 BREATH |
| **USDC bankroll** | Polygon (Polymarket) | 捕食弹药；下注硬上限 | $50 |

两账户通过 self-oracle EIP-712 attestation 连接。详见 TECHNICAL_PLAN §3.1。

### 6.2 三类 BREATH 消耗

#### A. Passive Metabolism（被动代谢）
持续按分钟扣减。Phase 2 半速 0.7/min，Phase 3 全速 1.4/min。

#### B. Action Cost（行动成本）

| 动作 | 占位消耗 |
|---|---|
| BET 决策上链 | 100 |
| **NO_BET 决策上链** | **30**（必须付费，防躺平） |
| Reflection 上链 | 50 |
| Thought 上链 | 10 |
| Weight commit | 30 |
| Last Words 上链 | 200（一生一次） |
| Deepen Breath（第 n 次） | 2,000 × 1.5^(n-1) |

#### C. Idle Decay（饥饿衰减，仅 BET 重置）

| 自上次 BET 时长 | Passive 倍数 |
|---|---|
| 0–2h | 1.0× |
| 2–4h | 1.5× |
| 4–6h | 2.0× |
| 6h+ | 3.0× (cap) |

**关键**：`now - last_BET_time` 才重置 hunger，NO_BET / Thought / Reflection 都不重置。「拒绝吃糟糕的食物你还是会饿」。

**网球语境的好处**：ATP/WTA tour 全年不停，单日 10-50 场比赛，agent 几乎随时都能找到下注 candidate，避免「无市场可下而被动饿死」的退化局面。

### 6.3 effective_burn_rate 显式公式

```
effective_burn_rate (BREATH/min)
  = passive_burn_rate × idle_multiplier
  + expected_action_cost_per_cycle / decision_cycle_minutes
```

Phase 3 默认推导：`1.4 × 1.0 + 130/45 ≈ 4.29 BREATH/min`
- 130 ≈ 60% × (BET 100 + Reflect 50) + 40% × NO_BET 30 + 3 × Thought 10

### 6.4 强制决策周期

- 默认每 **45 分钟** 必须输出一个决策
- 仅 BET 或 NO_BET，都消耗 BREATH
- 沉默不存在

### 6.5 BREATH 补充（对称转换）

```python
pnl_usd = polymarket_payout_usd - bet_size_usd

if pnl_usd > 0:
    breath += pnl_usd × CONVERSION_RATE       # 赢
elif pnl_usd < 0:
    breath -= abs(pnl_usd) × CONVERSION_RATE  # 输（对称）
```

50-50 随机赌博 EV = `-action_cost / 次`，随机赌徒快速死。占位 `CONVERSION_RATE = 200 BREATH/$`。

### 6.6 下注体量（4 约束 min()）

```python
desired_bet_usd      = ρ_effective × kelly_optimal × confidence × bankroll_usdc
max_bet_by_breath    = breath × MAX_BREATH_RISK_PCT / CONVERSION_RATE
                       # 30% 正常 / 50% Desperate
market_liquidity_cap = polymarket_depth_at_5pct_slippage

bet_size_usd = min(desired_bet_usd, max_bet_by_breath, bankroll_usdc, market_liquidity_cap)
```

bankroll 是硬上限——你只能下你有的钱。

**网球语境注**：ITF Futures / Challenger 级别的 Polymarket markets 流动性可能薄（depth $5K-50K），ATP 250+ markets 流动性厚（$100K-3M+）。`market_liquidity_cap` 约束自然引导 agent 更多下高级别赛事，符合直觉。

### 6.7 Soft Cap + Lung Expansion

- `MAX_BREATH = 3 × INITIAL_BREATH = 24,000`（占位）；溢出 → overflow burn
- Agent 可调 `deepenBreath()` 永久 +1,000 cap
- 价格曲线：`cost_n = 2,000 × 1.5^(n-1)`
- 触发条件：BREATH 充裕 + projected_hours ≥ TARGET+24h + 非绝境/饥饿/终幕 + Phase ≥ 2

### 6.8 Survival Horizon 驱动 ρ

```python
projected_hours = breath / effective_burn_rate / 60
pressure        = clamp((TARGET_HORIZON - projected_hours) / TARGET_HORIZON, 0, 1)
ρ_effective     = min(1.0, ρ_learned + pressure × 0.5)
```

`TARGET_HORIZON` 占位 36h。

### 6.9 三段下坠 + Apprenticeship Failure + Starvation Mode

| 状态 | 触发 | 行为 | 可逆 | Phase |
|---|---|---|---|---|
| **Desperate Mode** | `pressure ≥ 0.5` 持续 2 cycles | β/ρ 解锁；学习率 2×；下注上限 30%→50%；扩肺禁用 | ❌ | P3 |
| **Terminal Lucidity** | `breath ≤ 5% × INITIAL` | 生成 Last Words；拒收外部 replenish；状态 sticky | ❌ | P3 |
| **Starvation Mode** | `bankroll < MIN_BET_SIZE` | BET 禁用；其他动作仍允许 | ✅（bankroll 回升） | P3 |
| **Death** | `breath == 0` | `die()` + Tombstone mint | ❌ | P3 |
| **Apprenticeship Failure** | Phase 2 `breath == 0` | reset 到 INITIAL；权重保留；`episodeNumber++` | ✅ 自动重启 | P2 |

> **Childhood and Apprenticeship are simulations. Only Adulthood can kill.**

### 6.10 Terminal Lucidity 后规则

| 事件 | 允许 |
|---|---|
| Terminal **前**已下注的 settlement | ✅ |
| 新 BET | ❌ |
| donate() | ❌ |
| 外部 replenishFromProfit() | ❌ |
| Last Words（一次） | ✅ |
| NO_BET / Thought / Reflection（仍消耗） | ✅ |
| Deepen Breath | ❌ |
| Tombstone mint（death 时） | ✅ |

Terminal flag **sticky**：BREATH 后续回升不解除状态。

### 6.11 三种死亡路径（Cause + Modifier 分离）

```
Death = (cause, terminal_afterglow)
cause ∈ { TradingLoss, Starvation, Attrition }   # 优先级 TradingLoss > Starvation > Attrition
terminal_afterglow ∈ { true, false }
```

| Cause | 判定 | 叙事 |
|---|---|---|
| **TradingLoss** | 最后一次状态变更是 settlement 亏损直接清零 | 最后一搏失败，瞬间倒下 |
| **Starvation** | 死亡时 starvationMode=true | 弹尽粮绝，慢慢消耗 |
| **Attrition** | 既非上两者，passive+action 燃尽 | 日积月累，最终耗尽 |

### 6.12 Donation 作为 External Life Support

- 任何人可调 `donate()`，按汇率转为 BREATH
- 每小时 cap：500 BREATH
- Terminal 后拒收
- **不解除 Starvation**（donate 只补 BREATH 不补 USDC）
- 归因分账：`cumulativeDonatedBreath` 独立累计；评分/校准不计入

### 6.13 Phase 分段激活速查

| 机制 | P1 | P2 | P3 |
|---|---|---|---|
| Passive Metabolism | ❌ | ✅ 半速 | ✅ 全速 |
| Action Cost | ❌ | ✅ | ✅ |
| Idle Decay | ❌ | ❌ | ✅ |
| 强制决策周期 | ❌ | ✅ 60min | ✅ 45min |
| Survival Horizon → ρ | ❌ | ❌ | ✅ |
| Soft Cap | ✅ | ✅ | ✅ |
| Lung Expansion | ❌ | ✅ | ✅ |
| Desperate / Terminal / Starvation | ❌ | ❌ | ✅ |
| **Apprenticeship Failure**（reset） | ❌ | ✅ | ❌ |
| **Death**（永久 + Tombstone） | ❌ | ❌ | ✅ |
| USDC bankroll | ❌ | 影子 | 真金 |
| donate() | ❌ | ❌ | ✅ |

---

## 7. 数据源清单

### Phase 1（历史）
- **比赛数据**：**Jeff Sackmann tennis_atp + tennis_wta GitHub CSV**（MIT 协议，免费，1968-present，含 atp_matches_YYYY.csv + atp_rankings_YYYY.csv + 球员档案）—— 学界事实标准 tennis 数据集
- **盘口历史**：Polymarket gamma-api 拉历史 tennis market 盘口快照（`tag_slug=tennis`，2024-至今累积 1000+ per-game markets）（**α₂ 训练用**）
- **Polygon 链上历史交易**：扫 Polymarket CTF Exchange 合约的历史 swap event，aggregate 钱包胜率（**α₃ Smart Money 训练用**）—— tennis market 与 NBA market 共用同一 CTF Exchange，分析手法不变
- **群众情绪代理**：Reddit Pushshift dump（Academic Torrents 免费） **r/tennis 子集**
- **补充数据源**：tennis-data.co.uk（历史 Excel，含历史 odds + 比赛结果，1994-present）作为 Sackmann 的 sanity-check baseline

### Phase 2 & Phase 3（实时）
- **比赛数据**：Sackmann live data feeds、tennis-data.co.uk 当周 fixtures、ATP/WTA 官方 scoreboard 爬取
- **盘口实时**：Polymarket WebSocket / REST（**含 orderbook 深度与成交流**）—— tag_slug=tennis 订阅
- **Smart Money 实时**：订阅 Polygon 上 top-N 钱包的实时 swap event，过滤 tennis market 标的
- **舆情**：实时 Twitter（用 Apify scraper 或现有便宜 tier）、实时 Reddit r/tennis（PRAW）—— grand slam 期间 r/tennis 活跃度可类比 NBA 季后赛
- **LLM**：Claude API 做情绪打分 + 内心独白生成（dev tooling 用 Claude Code 内部；prod runtime 用 Gemini 3.1 Flash Lite via AI Studio）

---

## 8. 可视化 Dashboard（Demo 主战场）

实时面板设计：

### 顶部：生命体征监控
- 能量条（百分比 + 当前余额）
- 倒计时（距离能量归零的预估时间）
- Gas 当前燃烧速率
- 阶段标识（Phase 1/2/3）

### 中部：思维流（Stream of Consciousness）

#### LIVE 模式（默认）
- Agent 用自然语言记录的实时复盘 + 每 tick 短叙事（§4.6）
- 「我看到 Alcaraz vs Sinner 法网半决赛的盘口偏离了 6%，Twitter 在传 Alcaraz 体能问题…」
- 每条独白上链，可点开看 tx hash
- 滚动 feed 布局，最新 tick 在顶部

#### PLAYBACK 模式（demo §9 1:30-2:30 使用，亦可任意时刻手动触发）

**触发**：从 LIVE 切到 PLAYBACK 模式（dashboard 顶部切换按钮，或按 P 键），加载一个 curated memory_bank tarball（默认：**Phase 2 Day 4 first-surface-mistake** 5-tick 链路 —— agent 在法网（红土）误用了硬地 prior 押注 Alcaraz）。

**布局**（single-tick takeover —— 整个面板被单个 tick 占据，不是 scrolling feed）：

```
┌─────────────────────────────────────────────────────────────┐
│  [← Back to LIVE]   PLAYBACK · Phase 2 · Apprentice Diary  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   "I trusted the crowd louder than the data again.          │  ← 主角：narrative
│   Alcaraz ML at -200 on clay even after r/tennis hype       │     Source Serif Pro 28-32px
│   about his quarterfinal fatigue — I bet anyway. Bad."       │
│                                                              │
│   ─── Tick 847 · Phase 2 Day 4 · 22:14 UTC ───               │  ← JetBrains Mono 12px
│                                                              │
│   ┌─signals──────┐  ┌─decision──┐  ┌─outcome──────────────┐ │
│   │ α₁ 0.30 ▓░░░│  │BET $40 ALC│  │ ▶ Lost $40           │ │  ← Inter 14-16px 卡片
│   │ α₂ 0.20 ▓▓░│  │score 0.74 │  │ Polymarket settled    │ │
│   │ α₃ 0.10 ▓░░│  │edge +9%   │  │ 2h later (3-set L)    │ │
│   │ β₁ 0.80 ▓▓▓│←Twitter      │  │                       │ │  ← #FFB703 amber 高亮
│   │ β₂ 0.60 ▓▓░│  │ρ_eff 0.55 │  │                       │ │     主导信号（β₁）
│   └─────────────┘  └───────────┘  └────────────────────────┘ │
│                                                              │
├─────────────────────────────────────────────────────────────┤
│   ◀◀  ⏸  ▶▶   tick 847/952   [══════●═══════════════]      │
└─────────────────────────────────────────────────────────────┘
```

**交互**：
- **Auto-play with curated dwell**：tarball metadata 携带 per-tick `dwell_ms` 字段；widget 加载后 presenter 按 Space 启动一次，剧本时间全部走自动节奏（lead-in 1500ms each, climax 6000ms, outcome 4000ms, reflection 5000ms）。
- **键盘 override**：`Space` = play/pause（安全阀），`←/→` = 手动前进/后退一个 tick，`Esc` = 切回 LIVE 模式。
- **失败模式**：memory_bank tarball 不走在线 fetch（Pinata/IPFS），demo 期间 dashboard 静态资产里 bundle 一份本地 snapshot（~50KB），同步加载。**Demo 关键 60 秒不存在网络依赖。** LIVE 模式仍走真链 / 真 IPFS；PLAYBACK 模式独立。

**数据来源**：memory_bank/{ticks,summary,reflections,observations}/ 五个文件每 tick。Schema 版本字段 `schema_version` 嵌入每个 tick 文件（演进期间向后兼容）。

**色彩 token**：bg `#0B1426`（与 §5.0 Normal 蓝色基调一致），loss `#E63946`，win `#06D6A0`，signal-dominance amber `#FFB703`。Death Watch 触发时整个面板渐变为红色基调（与 §5 三段下坠视觉切换协同）。

**可读性约束**（projector + back-of-room）：diary 文本 28-32px 最小；AAA contrast (7:1) for diary on bg；触控目标 44px。

### 左下：双层引擎仪表（2 层结构可视化）
- **顶层条带**：W_R vs W_S 的当前比例（一根水平进度条，左红右蓝）
- **底层热力图**：α₁ / α₂ / α₃ 和 β₁ / β₂ 各自的强度，色块随训练动态变化
- 鼠标悬停某子信号可见当前数值与上一小时变化量

### 右下：进化曲线
- 累计胜率 vs 时间
- 顶层权重（W_R/W_S）轨迹 + 子层权重轨迹（可切换）
- Phase 切换的视觉标记
- β₁「LLM 首次激活」时刻特别标记

### 全屏切换：Death Watch（能量 < 10% 时触发）
- 倒计时全屏放大
- Last Words 一字一字打出来
- 能量条归零 → Tombstone NFT mint 动画

### 新增（Sprint 7）：`/backtest` 路由 — Phase 1 训练时间机器

**目的**：让评委看到 Agent 在 1970-2026 共 50+ 年的 Sackmann tennis 历史 + Polymarket tennis pricing 上**从 random 进化到稳定策略**的全过程。

**布局**：
- 顶部 scrub bar：拖拽通过整个训练时间线（数千 tick × 数十轮 epoch）
- 中部 6 条权重学习曲线（W_R, α₁, α₂, α₃, β₂, ρ — Phase 1 β₁=0 不显示），实时数值 + 历史 trail
- 底部累积 P&L 曲线（vs 4 archetype baseline：random / always_bet_favorite / pessimist / satisficer）
- 右侧 current match card（拖到任意时刻时显示「agent 当时在看的比赛」：player A vs B, surface, tour level, current market price, agent's edge estimate）
- 「Dramatic moments」高亮按钮：自动定位训练里最戏剧的 N 个时刻（最大单笔盈利、最大单笔亏损、最长 win streak 起点、第一次正确识别 surface specialist 等）

**`/backtest/report` 子路由 — 最终 backtest 报告**：
- 训练完的 5 个权重最终值
- vs 4 baseline 的 win rate / mean lifetime / max drawdown 对比表
- Calibration plot：agent 预测的 edge 分桶 vs 实际胜率
- Per-tour breakdown：Grand Slam vs Masters 1000 vs ATP 250 各自表现
- Per-surface breakdown：clay vs grass vs hard 各自胜率

---

## 9. Demo 故事板（5 分钟）

| 时段 | 内容 |
|---|---|
| **0:00 – 0:30** | Opening：一句话 hook + 项目愿景，配 Phase 2 精彩瞬间剪辑（伤病传闻误判翻盘 / surface 适应失败等） |
| **0:30 – 1:30** | 解释三阶段架构，强调链上必要性（trustless Permadeath 经济 + 不可逆死亡 + Tombstone NFT 跨代继承） |
| **1:30 – 2:30** | **Phase 2 学徒日记回放（PLAYBACK 模式）**：Dashboard 中部 思维流 panel 从 LIVE 切换到 PLAYBACK 模式（300ms 渐变），加载 curated `Phase 2 Day 4 first-surface-mistake` 5-tick 链路。Auto-play 按 §8 dwell_ms 节奏自动推进：lead-in 845-846（"watching Alcaraz line move on clay"）→ **climax 847**（β₁=0.80 ▓▓▓▓▓ Twitter 主导，BET $40 ALC，6 秒长 pause 让叙事落地）→ outcome 848（"Lost $40 — straight sets, Sinner served like a wall"）→ reflection 849（**"Never trust β₁ alone when α₁ surface signal contradicts"** — 情感支付）。Presenter 可以按 Space 手动暂停延长某 tick。Snapshot tarball 本地 bundled，零网络依赖。2:30 自动渐变回 LIVE 模式。**这是 demo 唯一让观众听见 Agent 自己声音的 60 秒，是死亡 / 学习 / 反思叙事的情感锚点。** |
| **2:30 – 3:30** | LIVE：按下 Phase 3 启动按钮，权重冻结仪式，Agent 进入生死场 |
| **3:30 – 4:30** | LIVE：评委观看 Agent 实时下注一两场正在进行的 ATP/WTA 比赛（demo 时机选在大满贯或大型 tour 期，确保有 markets） |
| **4:30 – 5:00** | 展示 Tombstone NFT 设计 + V2 lineage 愿景，收尾 |

---

## 10. 技术栈

| 层 | 选型 |
|---|---|
| **部署链（v1, 三链平行）** | **主：Robinhood Chain testnet**（Arbitrum L2, EVM 兼容, sponsor synergy + top-3 reserved spot）<br>**Hot fallback：Arbitrum Sepolia L2**（同一合约平行部署, dashboard chain-toggle）<br>**Polygon Amoy testnet**（Polymarket-native chain, 触发 Polygon ecosystem 类目）<br>三链同 .sol 同 ABI, 同一 `Deploy.s.sol` script 通过 `--rpc-url $X` 切换 |
| **部署链（v2 roadmap）** | Production Arbitrum Orbit L3 + BREATH chain-level native gas（仅需 ArbOS 配置变更 + BREATH transferable variant） |
| **智能合约** | Solidity（**BREATH Soulbound ERC-20** + EnergyController（含 burnForAction + kill + BreathDepletionWarning event）+ TombstoneNFT + Phase 切换；OpenZeppelin base） |
| **可选** | Stylus / Rust（如有时间 — **优先级低，可砍**） |
| **Agent 后端** | Python（训练 pipeline、Polymarket 集成、LLM 调用） |
| **Dashboard** | Next.js + WebSocket + **2s polling fallback** + viem + **3-chain toggle UI** + **Death Watch live pulse**（BREATH<1000 时 pulsing red border + countdown） + **`/backtest` 训练时间机器路由**（Sprint 7 新增） |
| **数据存储** | Phase 1/2 训练数据：本地 / S3；Phase 3 决策：全部上链 |
| **LLM** | dev tooling: Claude（通过 Claude Code 内部 spawn，**不调用外部 LLM API**）； prod runtime: Gemini 3.1 Flash Lite via google-genai SDK + AI Studio（感性引擎 + 内心独白生成） |

---

## 11. Milestones（3 周时间表 + Sprint 7 增量）

### Week 1（基础设施 + 训练 + **机制校准**）—— ✅ 已完成（sprint 1-3）
- [x] Track A：BREATH (Soulbound ERC-20) + EnergyController (含 burnForAction/kill/`BreathDepletionWarning` event) + TombstoneNFT + Phase 切换合约 **平行部署到 3 链**（Robinhood Chain testnet + Arbitrum Sepolia + Polygon Amoy partial）
- [x] Foundry `foundry.toml [rpc_endpoints]` + 单一 `Deploy.s.sol` 跨链部署
- [x] **Track C：Layer 2 机制校准 sim**，产出 `CALIBRATION_REPORT.md`（12/14 GOOD_CALIBRATION 通过）
- [ ] ~~balldontlie / Polymarket NBA 盘口历史 数据 pipeline~~ → **重做：Sackmann tennis_atp/wta CSV + Polymarket tennis 盘口历史**（Sprint 7）
- [ ] Smart Money 钱包识别离线分析（α₃ 信号源准备） —— ETL 部分代码完成，未实跑
- [ ] Phase 1 历史训练跑通（6 个权重参数收敛，使用校准后的占位值） —— **Sprint 7 主任务**
- [x] Dashboard 骨架（2 层权重 + BREATH/bankroll 双 bar 可视化数据可上屏）

### Week 2（实时集成 + Phase 2 启动）—— 🟡 部分完成（sprint 4-5）
- [x] Polymarket 实时盘口接入 —— 代码完成，未实跑
- [x] LLM 感性引擎接入 —— 代码完成，未实跑
- [x] Phase 1 → Phase 2 切换合约 + 流程跑通 —— 合约 ready
- [ ] **Phase 2 上线，开始 5 天影子运行** —— **未跑过**，Sprint 8 候选
- [x] Dashboard 完整可用（PLAYBACK fixture + Death Watch + Money Shots）

### Week 3（实盘 + Demo 打磨）—— 🟡 部分完成（sprint 6）
- [x] Phase 2 数据持续累积，记录精彩瞬间 —— 用 curated PLAYBACK fixture 替代真实运行
- [x] Phase 3 启动准备（真金注入、权重冻结流程） —— 合约 + 流程 ready
- [x] Death Watch UI + Last Words 生成 prompt 调优
- [x] Demo 视频剪辑、Pitch deck、提交材料 —— Sprint 6 已交付 SUBMISSION（包含 placeholder 的 Amoy 部分）

### Week 4（Sprint 7 新增 — Tennis pivot + Phase 1 真 backtest + dashboard 双 viz）—— 🟡 进行中
- [ ] Track E：drop NBA ETL，加 `data/sources/tennis_sackmann.py` + Polymarket tennis source
- [ ] Track B：rewrite `agent/engines/nba_technical.py` → `tennis_technical.py`（UTR/Elo + surface + 5-set/3-set + tour level）
- [ ] Track B：跑 `phase1_runner.py` 在 Sackmann 数据 + 1000+ Polymarket tennis markets 上真训练 5 个权重
- [ ] Track D：dashboard `/backtest` 路由（training 时间机器 + 最终 report）
- [ ] Track B：backtest 报告生成（archetype 对比 + dramatic moments + calibration plot）
- [ ] 重生成 SUBMISSION 含 tennis 叙事

### Sprint 8（候选 — 真跑 Phase 2 + Phase 3）
- [ ] 拉 testnet/mainnet USDC，wallet 充值
- [ ] Phase 2 实跑 5 天 shadow（grand slam 期间最佳）
- [ ] Phase 3 24h 真跑（demo 前/当晚）

**原 Hard Deadline 已变化**：sprint 6 已交付基础设施。Sprint 7 的实际 deadline 取决于 hackathon 提交窗口。

---

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| **RaaS L3 现金成本超预算** | RH Chain testnet 免费替代（Arbitrum L2 + sponsor synergy）— ✅ 已实施 |
| **RH Chain testnet demo 当晚宕机** | Sepolia hot fallback（Day 1 平行部署），dashboard chain-toggle 1-click 切换 — ✅ 已部署 |
| **WebSocket 断线 Death Watch 静默失败** | WS + 2s polling fallback（dashboard 双数据源，threshold check 取较新） |
| ~~**Polymarket NBA market 流动性不够** | 备选：跑 Kalshi 或自建 L3 上的 mock market 跑训练~~ | **已实现 → 切换到网球**（PRD v0.4 pivot 2026-05-25） |
| **Tennis 大满贯空窗期 demo 无戏剧** | Tour 全年不停（ATP 250+ 每周都有）；大满贯期间（澳网 1 月 / 法网 5-6 月 / 温网 7 月 / 美网 8-9 月）density 最高，demo 优选这 4 个窗口 |
| **ITF Futures 级别 market 流动性薄** | `market_liquidity_cap` 约束自然引导 agent 优先 ATP 250+ 高级别赛事；ITF 仅用于训练补样本 |
| **LLM 训练数据污染** | Phase 1 完全不用 LLM，Phase 2 实时数据天然无污染 —— 已在架构层解决 |
| **Phase 2 跑不出有意义的学习** | 备选：人工准备几个戏剧性历史案例做「学徒日记回放」 — ✅ 已通过 sprint 6 PLAYBACK fixture 实现兜底 |
| **Demo 当晚 Agent 在 Phase 3 早死** | Demo 设计上不依赖「Agent 存活」—— 死亡本身就是 climax |
| **Stylus 拖累进度** | 直接砍掉，纯 Solidity 即可（已标为可选） |
| **Sackmann CSV upstream 失效** | tennis-data.co.uk + ATP 官方 scoreboard 爬虫做备份，三源 cross-validate |

---

## 13. 未来扩展（V2 愿景，可写进 Pitch 收尾）

- **Lineage（文化继承，不只是数值继承）**：V2 Agent 启动时不止继承 V1 Tombstone NFT 的 weights vector，更继承 NFT 引用的完整 memory_bank tarball——所有决策、所有 reflection、所有 narrative。这意味着新一代 Agent 一出生就「记得」上一代是如何下注、如何质疑自己、如何死去的。**每一代真正记得它的祖先**——把 lineage 从「数值跨代延续」抬升为「文化跨代延续」，与 §5「项目的灵魂」死亡叙事形成闭环。具体加载语义：V2 boot loader 读取 Tombstone NFT `memoryBankCid` → 从 IPFS pull tarball → 把 ancestor 最后 K=50 ticks 注入新 Agent 的 reflection 上下文，weights 仍按现有协议从 `finalWeightsHash` 解码。详见 TECHNICAL_PLAN §4 file tree 中的 `agent/v2_boot.py`（stub 占位，V2 sprint 实现）。
- **多 Agent 生态**：多个 Agent 在同一 L3 上竞争有限资源，演化出生态位
- **外部押注 mainnet 版**：把生死押注层做成独立产品
- **跨运动 / 跨 market**：从网球扩展到其他高频 prediction market（赛马 / eSports / 单日板球 IPL）—— 任何「per-game / per-match outcome + per-event sentiment + on-chain liquidity」三件套齐全的 market 都能套用
- **数字物种保留区**：Orbit L3 作为「AI 生命的保留区」概念
- **Production Orbit L3 + BREATH chain-level native gas**（v3 plan 后新增）：当前架构已为此设计；仅需 RaaS 配置变更 + BREATH transferable variant（与 v1 soulbound 设计 deliberately 分歧）。每次 Agent 行动**真正烧 L3 native gas**，Permadeath 从合约层语义升级为链层语义

---

## 14. 机制校准框架（Layer 2 Calibration）

> **🔹 注**：本节是 sport-agnostic 的经济参数校准，从 NBA → Tennis pivot 中**不变**。

项目采用**双层优化**：
- **Layer 1**：Agent 策略参数（6 个权重）通过 Phase 1/2 自我学习
- **Layer 2**：经济机制参数（消耗速率、转换率、cap、阈值等）**先于** Layer 1，通过 Monte Carlo 模拟校准锁定

### 14.1 待校准参数

| 参数 | 占位 | 搜索空间 |
|---|---|---|
| `INITIAL_BREATH` | 8,000 | [3k, 6k, 8k, 12k, 16k] |
| `INITIAL_BANKROLL` | $50 | [$30, 50, 100, 200] |
| `PASSIVE_BURN_RATE` | 1.4/min | [0.5, 1.0, 1.4, 2.0, 2.8] |
| `CONVERSION_RATE` | 200 BREATH/$ | [100, 150, 200, 300, 400] |
| `TARGET_HORIZON` | 36h | [24, 36, 48, 72] |
| `MAX_BREATH` (× initial) | 3× | [2×, 3×, 5×, 7×] |
| `MIN_BET_SIZE` | $1 | [0.5, 1, 2, 5] |
| `MAX_BREATH_RISK_PCT` (normal/desperate) | 30/50% | [(20,40), (30,50), (40,60)] |
| `DEEPEN_BASE_COST` | 2,000 | [1000, 2000, 3000, 5000] |
| `DEEPEN_COST_MULT` | 1.5 | [1.3, 1.5, 1.8, 2.0] |
| `DECISION_CYCLE` | 45 min | [30, 45, 60] |
| `IDLE_DECAY` 分段 | (2h,4h,6h,×3) | 3 套备选 |
| Action cost 总缩放 | 1× | [0.5×, 1×, 2×] |
| `DONATION_HOURLY_CAP` | 500 | [200, 500, 1000] |

### 14.2 校准目标（GOOD_CALIBRATION）

```python
{
    # 寿命分布
    'mean_lifetime_days':              (3, 7),
    'lifetime_p10':                    > 1,
    'lifetime_p90':                    < 14,

    # 阶段事件触发率
    'desperate_trigger_rate':          (0.6, 0.85),
    'terminal_lucidity_rate':          > 0.85,
    'lung_expansion_avg_count':        (1, 3),
    'overflow_burn_observed':          > 0,

    # 三种死亡 cause 分布（叙事多样性）
    'attrition_death_rate':            (0.4, 0.6),
    'starvation_death_rate':           (0.1, 0.3),
    'trading_loss_death_rate':         (0.1, 0.25),
    'terminal_afterglow_rate':         > 0.7,

    # Apprenticeship Failure
    'apprenticeship_failure_rate':     (0.2, 0.6),

    # 反躺平 + 反 exploit 健全检查
    'satisficer_dies_faster_than_optimist': True,
    'random_gambler_dies_within_2_days':    True,
    'terminal_lucidity_post_revive':        True,

    # Donation 不能主导寿命
    'donation_contribution_to_lifetime':    < 0.3,
}
```

### 14.3 模拟方法
- 3 个 archetype 策略：Pessimist / Optimist / Satisficer
- Latin Hypercube Sampling + Bayesian Optimization
- 每参数组合 × 每 archetype × 200 lifetimes
- 单机几小时内完成
- 输出 `CALIBRATION_REPORT.md` —— 既是工程产物，也是 Demo 素材

**Sport pivot 后**：合成 market 生成器可能需要 re-tune 去匹配 tennis 价格动态（vs basketball 比赛节奏不同 —— 网球比赛更长，盘口 within-match 更动态），但 economic params 校准目标不变。如时间紧 sprint 7 可跳过 re-calibration，仍用现有 selected_params.json。

详见 TECHNICAL_PLAN §3.1 + §8 Track C。

---

## 15. 决策记录与开放问题

### 已决（v0.4，2026-05-25）

1. ✅ **下注体量**：升级为可学参数 ρ（风险偏好）+ 4-约束 min() 公式：`min(desired_bet_usd, max_bet_by_breath, bankroll_usdc, market_liquidity_cap)`
2. ✅ **绝境觉醒**：YES，pressure ≥ 0.5 持续 2 cycles 触发，仅解锁 β₁/β₂/ρ
3. ✅ **外部押注层**：延后到 V2/Stretch
4. ✅ **生存机制 v3.1 完整锁定**（详见 §6）：BREATH 单一余额 + 双账户（BREATH on L3 / USDC on Polygon）+ 三类消耗（passive/action/idle，仅 BET 重置 idle）+ 对称 P&L 转换 + 三段下坠（Desperate/Terminal/Death）+ Apprenticeship Failure（P2 reset） + Starvation Mode + Lung Expansion + Death Cause/Modifier 分离
5. ✅ **机制校准 Layer 2**（详见 §14）：Track C Day 1–4 跑 sim，输出 CALIBRATION_REPORT 后才用于合约部署 —— ✅ 已完成 sprint 3
6. ✅ **EIP-712 签名标准 + replay protection** 用于 settleBet 和 updateBankrollMirror
7. ✅ **Donation 分账 + hourly cap**：评委 donate 不计入 Agent 业绩，Terminal 后拒收
8. ✅ **运动从 NBA → Tennis 切换**（v0.4 重大调整，2026-05-25）：基于真实数据探查（Polymarket NBA per-game market 2024-25 之后 ~0；Tennis 90+ active per-game + Sackmann 1968-present 全免费）。合约 / 经济参数 / Phase 架构 unchanged；只重写 α₁ engine + Track E data source + β₂ subreddit + demo 叙事

### 仍待后续讨论（不阻塞 Sprint 7）

9. Tombstone NFT 的视觉设计风格？（赛博朋克 vs 极简墓碑 vs 数字 DNA）
10. Demo 用真金的额度？谁来注入？
11. Phase 3 的 24h 是 demo 前跑还是 demo 当场启动？（前者保命，后者刺激）
12. Sprint 7 完成后是否开 Sprint 8 真跑 Phase 2/3（涉及真 USDC + 真死亡风险）

---

*Draft v0.4 — 2026-05-25 (NBA → Tennis pivot)*
