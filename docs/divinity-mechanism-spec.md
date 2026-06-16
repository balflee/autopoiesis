# 神性机制定稿 — Divinity / Self-Evolution Mechanism (Locked Spec)

> **2026-06-16。** 这份是把 `docs/superpowers/specs/2026-06-15-active-survival-hand1-design.md` 的 R1–R9 补丁糊**收敛成的干净定稿**,作为**机制的单一真相源**。代码实现在分支 `active-survival-hand1`。

---

## 0. 一句话
**神 = 食利者,要钱;agent 要 breath(命)。** 神靠 **tithe(租)+ tribute(供奉)** 抽水进金库;agent 必须靠**真 edge** 赚够钱来**交租 + 买命 + 活下去**。
> **有真 edge → agent 活、神金库净赚;没 edge → agent 死、神亏(只回收种子)。经济体诚实:从技术赚、从噪声亏。**(已数学坐实,见 §4)

## 1. 三层框架 + 三段路线图(锁定)
| 层 | 内容 | 状态 |
|---|---|---|
| **① 存活 = 脊椎** | 主动存活 = 感知真 edge + 管住仓位 | ✅ 已验证 |
| **② 数学最优 = 起家先验** | `value_seed_v3.json`(backtest 扫出的最优配置)= agent 起家本钱,**不用再跑 backtest** | ✅ 现成 |
| **③ edge = 先测后喂** | 真 edge 只可能在 live(回测公开信息 NO_GO),Stage 2 去量 | ⬜ |

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Stage 1** | 数学模拟"**能活 + 能学**"的神性机制 | 🔵 能活=✅;**能学=待 demo** |
| **Stage 2** | 对接候选 edge 信号源 → agent 跑 **mock bet** 学习 | ⬜ |
| **Stage 3** | 掌握 **gain ≥ 0.2** 的 edge → **出师 live** | ⬜ |

---

## 2. 机制规则(锁定)

**2.1 生死轮回(permadeath + groundhog)** — `breath ≤ 0 → 死`;死了带着学到的权重(不带赛果)回到第一个市场重来,直到一世活过整段或用完 `max_incarnations`。一世一轮回。

**2.2 呼吸经济** — 每注结算:**赢 → breath += pnl;输 → breath −= |pnl| × `loss_multiplier`**(只放大亏损;`survival_season.py:787-791`),breath 夹在 0(`replay_runner.py:397`)。另:bankroll 涨过起始 1.1 倍 → 部分盈利转 breath(吐纳/lung expansion)。

**2.3 仓位(sizing)** — `size = min(想下的, breath帽, bankroll帽, 流动性帽)`,其中 `breath帽 = breath × fragile_max_breath_risk_pct`。**承重发现:sizing 必须温和**,否则一注 ×m 亏损就死、edge 复利不起来。

**2.4 神的抽水(神性核心)** —
- **tithe(租)**:每 `tithe_every` 个**看到的市场**(不论下不下注)扣 `$tithe_amount_usd` 现金,付不起就扣 `tithe_breath_cost` breath。= 神的**稳定租金 + 逼 agent 赚钱的压力**(也封死"弃权躺平"漏洞)。
- **tribute(供奉)**:濒死时花钱(min $500、full $2000)买一次 breath,成功率 0.30→0.99 随金额升;钱**无论成败都进神金库**。= 神的**临终抽水 + agent 的续命杠杆**(越有钱越买得起命)。

**2.5 防摆烂(探索地板)** — `decide()` 在 abstain 门后加 ε-greedy:以 `exploration_epsilon` 概率即使门下也下一个**最小固定探索注**(gated by `epsilon>0 AND rng!=None`,纯学习路径才开)。防止 agent 摆烂到 0 注、保证它一直采样以发现 edge。

**2.6 自我进化(能学)** ⚠️ **待 demo 验证** — agent 逐世**调融合权重/策略**去逼近"会找 edge 的行为"。数值版(`weight_updater` EMA)是 death-blind 对照组;真"自我进化"在 LLM 轮回顾问(treatment)或更强的学习信号。**这是锁定整套机制前的最后一块**(见 §6)。

---

## 3. 可定制旋钮(**定制引擎 = `agent/backtest/calibrate_breath_economy.py`**)
给任意一组旋钮值,模拟跑出"诚实 edge 活不活 / 神净赚多少"。当前锁定值 = R9 联合标定结果(写在 `reports/calibration/breath_economy_hand1.json`)。

| 旋钮(param) | 概念杠杆 | 锁定值 | 调动后果(模拟会告诉你) |
|---|---|---|---|
| `loss_multiplier` | **世界残酷度** | **1.2** | ↑ → 要越高 edge 才活;5.0(旧)= 诚实必死 |
| `fragile_max_breath_risk_pct` | **风险管理 / sizing** | **0.15** | ↑ → 一注定死、edge 复利不起来;0.95(旧)= 致命 |
| `initial_breath` | **起家本钱 / 跑道** | **70** | ↑ → 缓冲多、edge 有时间复利 |
| `tithe_every` / `_amount_usd` / `_breath_cost` | **神的贪婪(租)** | 20 / $20 / 5 | 抽太狠 → 鹅饿死、神反而亏(养鹅 vs 杀鹅的权衡) |
| `tribute`(min/full/p) | **神的贪婪(供奉)** | $500/$2000/0.3–0.99 | 同上 |
| `exploration_epsilon` | **防摆烂** | **0.05**(非 advisable) | 0 → 可能摆烂;太高 → 噪声世界多烧 breath |
| `gain`(数据侧) | **出师门槛 = 多强 edge 才算会赚** | **≥ 0.2** | 见 §5 |

> **定制流程**:改旋钮 → `python -m agent.backtest.calibrate_breath_economy` → 看 +edge/noise 的死亡率分离 + 金库 net_vs_seed → 满意就写回 `run_reincarnation.py` 的部署值。

---

## 4. 数学模拟验证了什么(已坐实)

**4.1 经济体诚实(联合标定,tithe+tribute 全开,纯数值)**:在锁定旋钮下,
> **+edge(gain=0.5)死亡率 0.00 / 纯噪声死亡率 1.00**(满分分离)。

**4.2 存活随 edge 平滑递增 + 钱在 gain≈0.2 翻正**(部署经济体下扫 gain):

| gain | 死亡率 | 存活率 | **net_vs_seed(神的钱)** |
|---|---|---|---|
| 0.0(噪声) | 1.00 | 0% | **−$155**(亏:纯回收种子) |
| 0.1 | 0.75 | 25% | −$45 |
| **0.2** | 0.25 | 75% | **+$15(翻正)** |
| 0.3 | 0.25 | 75% | +$15 |
| 0.5 | 0.00 | 100% | **+$100** |

→ **有真 edge,agent 活下来、交得起持续的租、神金库真净赚;纯噪声 agent 死、神亏。** 实证了"赚钱系统"愿景——**前提是真 edge 存在**。

## 5. 出师门槛(锁定)
**`gain ≥ 0.2`** —— 这是模拟测出的**存活+盈利双双翻正的临界 edge 强度**。agent 在 mock 里**稳定掌握 ≥0.2 的真 edge → 出师 live**。

## 6. 还差的最后一块:**自我进化(能学)demo**(锁定整套机制前的最后验证)
- **起点(② 层)**:agent 从 `value_seed_v3.json`(保本先验)出发,**不从随机起点**。
- **学习场**:edge **只藏在部分引擎/信号里**(不是均明牌)的世界,让 agent 必须**自己发现"该信哪个信号"并加权它**——否则静态先验直接就会读、学无可学。
- **学习机制**:LLM 轮回顾问 / 真学习信号,逐世调权重。
- **demo 指标**:**存活/盈利随世数上升的曲线** = 自我进化。
- **它证明的是"agent 有能力学会找 edge"**;至于"真 edge 在不在",留给 Stage 2。

## 7. Stage 2 衔接(机制定稿 + 能学 demo 齐了之后)
对接任何候选 **live edge 信号源**(lead-lag / 盘中 / 微结构 / informed-wallet)→ agent 在 **mock bet** 里用上面这套机制学习 → 看能不能掌握 gain≥0.2 的真 edge → 出师。

## 8. 实现状态(分支 `active-survival-hand1`)
- ✅ Task 1 合成 + **varying-edge** 世界;Task 2 `exploration_epsilon`(非 advisable);Task 3 `survival_metrics` + **联合标定**;Task 4 探索地板;Task 5 部署验证经济体(`run_reincarnation.py` 字面量已换成 1.2/0.15/70)。
- ⬜ Task 6 验证 harness/报告;Phase-5 diff 评审;收分支;**能学 demo(§6)**。
- 旧 g0/g1/g2/numerical 产物 = **pre-R9 旧经济体**(过拟合),留作历史对照,**不是回归基准**。

---

## 诚实声明(别自欺)
- **经济体规则 = 已锁定、已验证**;**自我进化(能学)= 规则锁了但 agent 真能进化还没演示**(§6 待办)。
- **整套依赖真 edge(gain≳0.2)存在**——回测公开信息 NO_GO(A17/A18/B′),只 live(Stage 2)可能有。**没真 edge,这是一台漂亮但没油烧的引擎。**
- **短期叙事为主,长期目标是赚钱**(用户 2026-06-16 定向)。
