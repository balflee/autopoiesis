# 优化 Backlog（2026-06-12 讨论记录）

> 逐项评审用。每项标注：动机 / 改什么 / 前置条件与成本 / 风险与诚实注意点。
> 状态：`PROPOSED`（待拍板）/ `AGREED`（已同意未做）/ `IN-FLIGHT` / `DONE`。
> 背景取证（本日 workflow 实证，文内引用）：agent 的可学习面只有 6 个融合权重键；
> 学习信号是每注 PnL 符号信用，**梯度里没有 breath/death 任何一项**；breath 期望
> ≈ −1.22/注（$5 仓位、×5 惩罚、胜率 81.4%），胜率 ~86% 翻正；7,494 个缓存市场
> 的流动性帽 100% 退化为 $5 下限；±0.1 的 rho delta 被 $5 帽吃进死区（2110 个
> 决策只有 6-7 个毫厘级差异）。

---

## ★ 战略框架（三相）— 2026-06-13 用户对齐（统领以下所有项）

**核心原则（诚实校准）**：对一个**校准的市场**用**同样的公开信息**，无法系统性赢过它
——所以 **回测里"用公开信息更准地下注"的 edge 天花板 ≈ 0**（A13 探针实证：我们的
信号只比市场价强 0.008 Brier ≈ 噪声；4/5 信号是 Sackmann 赛前公开数据、市场早已
定价，第 5 个是价格自身的动量）。**真 edge 必须来自市场还没定价进去的信息**，而那类
数据**历史拿不到**（链上时点持仓、首盘锐线都不存在干净的历史版）。因此分三相：

| 相 | 目标 | 能用的信息 | 主要奖品 |
|---|---|---|---|
| **回测** | 在公开信息 + 风险管理下**活得最久**（逼近**生存天花板**，非 edge 天花板） | 仅公开/历史（Sackmann + Polymarket 历史价） | **生存**（edge≈0 也可学的纪律）+ thin 公开 edge sliver |
| **mock** | 加未知信息推 edge 突破公开天花板 | **实时**未知信息（链上 smart-money、锐线、其他 Polymarket 市场互参） | **真 edge**（纸面交易、无资金风险、无前视） |
| **live** | 实战部署 | 全部 | 收时差 |

**关键洞察**：即使 edge=0，**生存是一门独立可学的本事**——同样校准价格的市场，因下注
大小/弃权/breath 管理不同，存活结局天差地别。**回测真正能教、也值得展示的，是"在 5x
亏损 + 永久死亡的负 EV 甬道里靠风险管理活最久"。** 这不需要 edge，需要纪律。

### G0/G1/G2 / A9 / A10 与新目标的对接（清算）
A9"牛熊涌现"原本想让 agent"看清市场 regime 并收紧"——**在新框架下这是个伪命题**
（公开信息上没有 edge/regime 可感知，只有不可靠的薄 edge + 校准市场）。这恰好**解释了
G1/G2 的所有 null**（无涌现、γ 游走、深度没复现）：**没有 edge/regime 可被感知**。
诚实结论与新框架自洽——G0/G1/G2 的价值正是**实证了公开信息的 edge≈0**。

**但 A9/A10 造的基建并不浪费——只需"重新瞄准"到回测相的生存目标**：
- **storm 感知**（tick 级真 breath delta EMA）→ 从"感知 regime"**重定性为"我在放血"的风险触发器**（生存）。
- **γ2 = risk_storm_sensitivity**（放血时缩仓）→ **本就是 breath 风险管理**，直接归回测生存相。
- **γ = gate_storm_sensitivity**（放血时抬门弃权）→ "流血时少下"也是生存战术，但因公开 edge≈0、次要。
- **genome（StrategyConfig 可调旋钮）** → agent 调风险/选择性的载体，是生存相的工具。
- **A10 地租** → 关闭"昏迷冻结苟活"漏洞，让**生存度量诚实可信**（不能靠不下注假装活）——生存相的测量基建。
- **G2 证伪（洗牌）** → 实证"死亡是 regime 结构性"，与新框架一致（regime = 薄 edge 失效 + 方差聚集处；生存 = 熬过它）。
- **缺的那块（= 回测相的真正下一步）**：**把生存放进学习目标**（A0/A2 的死亡盲梯度）。A9 给了 agent
  感知 + 杠杆，但**学习器仍只优化 PnL、不优化生存**——所以杠杆没被用于生存（γ 游走）。**目标对准生存，
  现有杠杆才会被用起来。**

### 现有 backlog 项按相归位
- **回测相（生存为主）**：**A2**（生存进 EMA 目标——从"争议项"升为**核心下一步**）、
  **breath 感知仓位/弃权**（= A6 祈祷第一愿 + A8"换挡"重定性为风险管理，新立 **A14**）、
  storm→风险触发器（A9 重瞄）、γ2 缩仓（A9 归位）；次要 **C 类特征**（够 thin 公开 sliver）。
- **mock 相（edge）**：**smart-money 链上流**（新立 **A15**：Polymarket Data API/subgraph 取 informed
  钱包流——唯一数据现成的真 edge 候选）、**D1 锐线**（live 版）、**跨市场互参**（新立 **A16**：
  赛果 vs 首盘一致性/相关市场；也指向"换到流动性更厚的赛果市场"）。
- **live 相**：实战（E 类）。
- **已废/超越**：A8、A9 的"storm/γ 当 regime 感知"框架（基建留用、框架超越）。

---

## A. 生存/学习机制类

### A0 · 设计公理：优化器分权（separation of optimizers）— 2026-06-12 用户发现 + 数据实证
- **发现（用户）**：实验组在向数值对照组收敛——"我们的设计机制其实用数值对照组就是最优解"。
- **实证（groundhog v2 artifact）**：LLM 每次转世扰动权重 0.09-0.20，一世 ~826 步 EMA 梯度
  把扰动洗回到 0.003-0.05（吸收率最高 97%）；两腿带进 holdout 的权重差 10⁻¹³，
  holdout pnl 精确到小数点后 10 位相同。**实验组收敛向对照组是结构必然**：
  EMA 是系统里唯一有实权的优化器（每世 ~826 步 vs LLM ≤3 个 ≤0.1 的 delta，
  力量差两个数量级），且两者在同一组 6 权重上拔河。
- **公理**：永远不要让慢速深思层（LLM）和高频梯度层（EMA）争夺同一组参数——
  强者通吃。分工 = EMA 拥有世内每注校准；LLM 独占无梯度的跨世元参数。
  A1 是该公理的第一次落地；E4（种群+生存选择）是单吸引子结构的终极解法。

### A1 · Phase-3 基因组解锁（genome unlock）— `PROPOSED`（我推荐的下一步；A0 公理的落地）
- **动机**：用户担心"delta 参数不够让 agent 优化策略"——实证成立：6 键里唯一的
  仓位杠杆 rho 要盲推 ~8 次同方向才穿透 $5 流动性帽，前 7 次零行为反馈，LLM 建议
  一旦摇摆永远到不了阈值。
- **改什么**：把冻结的生存攸关旋钮开放给死亡 advisor（带夹具）：
  `min_edge`（挑剔度——最强生存杠杆，**下一世立刻可见反馈**）、
  `max_breath_risk_pct`、`min_confidence`、`kappa`、（可选）`min_bet_size_usd`。
  groundhog 架构里顺手：每次转世本来就 `replace(seed, ...)`，扩成携带突变后的
  完整 StrategyConfig；advisor delta key 集 6 → ~10，照旧 schema 校验 + 夹具。
- **成本**：中（advisor schema/prompt 扩展 + apply 路径 + 测试 + 重跑 treatment 腿）。
- **诚实注意**：仍然零脚本——只拓宽身体参数，不写行为规则；"全弃权不死薅 $0"
  的漏洞已由计分规则（不死但 $0 = 死亡同分）+ 页面披露覆盖。

### A2 · 数值腿的死亡感知（death term in EMA objective）— `PROPOSED`（**回测相核心**；2026-06-13 三相对齐后从"争议项"升级——生存是回测奖品，必须进学习目标；与 A14 配套）
- **动机**：对照组的梯度对死亡结构性失明（weight_updater 里 grep 不到 breath/death）。
- **改什么**：给 `update_from_settlement` 增加死亡终端信用（如死亡时对 rho logit
  施加一次强负梯度），或保持纯对照不动。
- **风险**：这是**我们设计惩罚函数** = 半脚本化，和"行为必须涌现"的哲学有张力；
  建议保留数值腿为纯对照，死亡感知只走 AI 腿（A1）。

### A3 · desperate 模式接线或删除 — `PROPOSED`（小项）
- **取证**：`desperate_threshold` 全仓库只存在于 docstring，没有任何模块计算它；
  回测里 desperate 恒为 False，全部下游行为是死代码；且若真翻转，它会把资金帽
  0.30→0.50（**加**风险），对生存可能起反作用。
- **改什么**：要么在 lifecycle 层真正接 `breath < threshold` 的翻转（并重审
  0.50 帽的方向是否该反过来），要么删掉死代码。

### A5 · 通关目标 + 战绩反馈（goal & record feedback）— `AGREED`（用户 2026-06-12 指定）
- **动机**：给 agent "通关的感觉"——明确目标（活过全部 N 个市场）+ 每次死亡告知
  "你上次在第 X 注后死了""你最好的一世活到了第 Y 注（Z%）"。
- **改什么**：`build_death_window` 的死亡摘要增加 GOAL 行 + 上世死亡点 + 历史最佳
  深度（个人纪录）。纯感知拓宽，零行为脚本；只作用于 AI 腿（数值对照组没有语言通道，
  保持全盲）。
- **成本**：小（窗口文本 + 穿线 best_progress + 测试）。

### A6 · 向神祈祷机制（prayer log）— `AGREED`（用户 2026-06-12 指定）
- **动机**：每次死亡后让 agent 祈祷"我希望下一世能有 XXX 参数/信息让我活得更好"。
  祈祷**不带入下一世记忆**（不污染实验的信息流），但被记录进 artifact——
  神明（我们）过后审愿，决定哪些愿望成为 roadmap（与 A1 基因组解锁天然衔接：
  agent 自己点名想要的杠杆）。
- **改什么**：死亡分支增加一次独立 LLM 祈祷调用（自有 schema `{wish}`，fail-soft，
  sanitize 后存 `incarnations[k].prayer`）；页面轮回日志展示"临终祈祷"；
  honest note 写明祈祷只记录、当世实验内永不应验。
- **信息流约束**：祈祷输出除 artifact 外不进任何下游（下一世的 advisor 窗口里
  必须不含祈祷文本——用测试执法）。
- **成本**：小-中（祈祷调用 + artifact/validator/页面 + 信息流测试 + 重跑 Gemini 腿）。
- **实证发现（2026-06-13，49 条去重祈祷）：套件改变了 agent "知道自己需要什么"。**
  - **套件前**（数值/Gemini 腿）：清一色求**不可授予的神谕**——"下注前给我真实胜率"、
    "完美预知哪些市场会输"、"看到全部 3431 场赛程"。本质是要求看未来（look-ahead 作弊）。
  - **套件后**（G1 storm 腿）：祈祷**质变为可实现的工程请求**，且**引用自己的反事实账本**——
    "我要前瞻 storm 分类器（当前只在亏损落到 breath 后才反应）；反事实证实把
    gate_storm_sensitivity 0.05→0.20 能拦 258 注省 $592.88 breath，但结算前我无法
    识别哪些是高 storm"；"随 storm 强度反比缩放注码的 storm_stake_scalar"；
    "连续 0-1 的 storm 概率而非门用的粗糙 0/1"。
  - **意义**：给了仪表，agent 就从"乞求作弊"转为"提出可造的下一个特征、甚至报出
    最优参数值"。这直接生成了 A11（前瞻 storm 分类器）立项；属套件最强副产品，值得进叙事。

### A7 · 供奉机制（tribute：钱换命，神有私心）— `DONE`（2026-06-13 上线；Gemini treatment 腿待配额恢复补跑）
- **动机**：死亡解剖显示 agent 死时怀揣 $2,459 现金（双账本：钱包富、呼吸穿零）；
  "死了归零"下这些钱本就带不走。神想要钱，agent 想要命 → 市场成交。
- **规则（用户定）**：濒死时刻可向神供奉换 **35 breath**；**最低 $500**；金额越高
  成功率越高；**$2,000 ≈ 接近 100%**。
- **锁定的实现参数**（用户未指定处由我补全，可改）：
  - 成功率曲线 `p = 0.30 + 0.70×(amount−500)/1500`，上限 **0.99**（"接近"100%，
    神从不打包票）；低于 $500 不受理。
  - **神贪婪**：供奉无论成败都被神收走（是贡品不是交易）；失败 ⇒ 照常死亡，
    余财按死了归零没收。
  - 触发点：仅"濒死时刻"（breath 将 ≤0 的死亡判定前）；成功 ⇒ breath 置回 35；
    同一世可多次供奉（钱是唯一约束）。出生 $100 买不起 ⇒ 穷人救不了，符合世界观。
  - 供奉从 bankroll 扣除 ⇒ 之后 Kelly 仓位变小（真实代价）。
  - **决策权**：对照组 = 公开披露的脚本反射（濒死且 bankroll≥500 ⇒ 供奉
    min(2000, bankroll)）；实验组 = LLM 在濒死时刻做结构化选择 {offer, amount}
    （带 GOAL/赛季位置/定价表上下文）；LLM 调用失败 ⇒ 沉默即死亡（不回退反射，
    保持两腿可区分；失败次数遥测披露）。
  - 计分：`scored = (pnl_gross − tributes_paid) if survived else 0`；artifact 增
    `tributes[]`、`gods_revenue`；页面展示神的总收入。
  - 骰子归神掷：种子化 RNG（按世索引），可复现。
- **预期（诚实预测，跑完按实发布）**：富 agent 一次供奉大概率买通终点 ⇒ 两腿可能
  第 1 世就通关，headline 从 $0 变 ~$2,500-2,800 net，神收 $2,000+；
  groundhog 死亡循环退化为 1 世——这正是设计意图：钱能买命，神有收入。
- **范围**：触碰 live loop 死亡判定 seam（最敏感代码）——`tribute_policy=None`
  默认全层 byte-identical；走完整 plan-loop。

### A8 · 熊市感知 + 买命账单进课本（regime perception）— `SUPERSEDED`（2026-06-13 三相对齐：reframe 为伪命题——公开信息上无 regime 可感知；"换挡"重定性为 A14 风险管理；买命账单基建已在 A7/A9）
- **取证（数据实证）**：本季存在真实 regime shift——结算 0-300 胜率 93-98%、
  breath 期望 +2.3~+3.1（黄金时代）；~300 之后胜率 70-79%、breath 期望
  −1.4~−4.2（熊市，至窗口尽头未回暖）。死亡聚集 ~826 = 黄金期血量缓冲被
  慢性失血耗尽的算术终点，非局部风暴。27 次死亡（20+pilot）证明现行规则下
  "多死几次"学不到任何东西：梯度一世饱和、死亡不在学习通道、agent 无 regime 感知。
- **含义**："扛过熊市"是错目标（它不结束）；正解是**换模式**（熊市中极端挑剔+
  极小敞口把 breath 期望拉正）——需要的杠杆全在 A1。
- **改什么**：①感知：近期滚动胜率/breath 趋势做成特征输入（数值腿）或死亡窗口
  上下文（AI 腿）；②买命账单进临终窗口（A5 扩展，极小）："你上世买了 N 次命
  花 $X、复活后只赚回 $Y"——让"别在爆雷峰顶买命"可涌现；③依赖 A1 杠杆。
- **商业张力（诚实记录）**：会换挡的 agent 不再送死 ⇒ 神的每命收入($2,564)下降、
  agent 终于能把 headline 带回家——定价 Laffer 曲线的另一端。

### A9 · 牛熊涌现原料包 v2（经对抗审计修订）— `SHIPPED`（2026-06-13 代码全落地；G0/G1/G2 跑批在途）
- **落地记录（plan-loop 13 轮收敛后 TDD 实现，commits 757fa0f→76fb2ca）**：
  K1 storm 感知（tick 级真 breath delta 单 EMA + 48h 半衰期 wall-clock 衰减，
  零 delta tick 只走 wall-clock —— (1−τ) 因子本身是事件计数衰减不许跑）；
  K2 γ/γ2 条件门（value 模式 eff_min_edge + rho_eff 缩放，γ=0 字节恒等）；
  K3 genome 上 StrategyConfig（min_bet_size_usd 显式排除——$5 流动性地板下
  它是全有全无参与开关）；K6 反事实账本（仅紧缩方向 + storm 拆账 + genome
  读数 + K5 买命行，全部进死亡窗口）；K7 证伪（配对时间平移 + 单调 60s
  归一化 + bets_by_third 参与度 + falsification_metric.evaluable ≥3 次
  productive 死亡边界提案才可判定）。Runner：--provider minimax /
  --storm / --shuffle-timestamps-seed；arm 命令见 scripts/run_reincarnation.py
  docstring。Dashboard：genome 位移 chips + regime ledger 行 + shuffled
  徽章 + INCONCLUSIVE 渲染 + 停机/换挡判读。
- **臂**：N=已发布对照（永不重跑）；G0=kit-off LLM 消融（六权重 advisor+tribute）；
  G1=全套件 treatment；G2=洗牌赛季证伪。MiniMax-M3（用户 2026-06-13 指令）。
- **实测结果（cap=20，对抗 agent 面板审计后定稿——结论 INCONCLUSIVE 非涌现）**：
  G0 复现 826 死亡平台（深度 52%）；G1 inc2 唯一一次 productive review 打包动了
  γ+0.05/γ2+0.05/max_breath_risk 0.95→0.85，之后 18 世稳定 858–895（69–72%），
  比对照深 ~19pp 但**仍每世必死、headline $0**；G2 洗牌赛季**第 1 世即活过整季**
  （$5.67），佐证死亡是 regime 结构性现象。**不主张涌现**：按预注册标准 INCONCLUSIVE
  ——G1 仅 1/3 productive review（MiniMax 18/19 空/失败），G2 没死过 advisor 从未触发
  （γ=0 是默认非决策）；深度增益被普通 sizing 削减捆绑无法归因；学到的 genome 在
  holdout 与 kit-off 字节相同（+$422.77，被 $5 死区掩盖）。完整 can/cannot ledger
  在 /reincarnation 页。
- **重跑定论（2026-06-13，超时修复后——结论：不支持涌现）**：把 MiniMax 读超时
  40s→180s 后 productivity 从 **1/19 飙到 19/19**——所以旧的 INCONCLUSIVE 是**超时假象**，
  不是审议带宽极限。全 productivity + A10 地租（让洗牌 agent 真死、advisor 触发）下，
  两腿都 evaluable：**G1-real 终值 γ=+0.000、G2-shuffled γ=+0.050**。若是真感知，真实熊市该
  推 γ 更高、洗牌季该≈0；实际反过来，且 γ 全程游走（0→0.15→0）不收敛。"深 19pp"也**没复现**
  （重跑回落到对照 ~52%，但带地租混淆）。**证伪腿现在真可裁决，而它不支持涌现**——与
  "本就没有 regime 可感知"一致。这把 A9 闭环,项目转向三相框架（见顶部）。

#### 原审计记录（保留）— ### A9 · 牛熊涌现原料包 v2（经对抗审计修订）
- **审计结论**：原 K1-K5 方案（storm 感知 + γ 条件门 + A1 解锁 + B1 真帽 + 买命账单）
  **不充分**——造了执行器没造仪表。四个被钉死的缺陷：
  ①**归因不可能**：advisor（唯一能动 γ 的实体）的窗口只有混合聚合（牛熊混出 ~85%
  "看着健康"的胜率），且其 HARD RULE 4 要求提案引用窗口观察——窗口里没有 storm
  可引用 ⇒ γ 提案只能来自幻觉/预训练先验 = rho 盲推重演；
  ②**饱和陷阱×2**：胜率符号信用在熊市仍 70%+ 永不翻负（regime 活在 breath/EV
  空间，学习器无此项）；弃权冻结滚动窗 ⇒ 门一关永不再开（自证封锁）；
  ③**停机棘轮**：计分无参与度项 ⇒ "牛市捞完永久弃权"严格占优，且在不回暖的赛季
  与真换挡观察等价；K3 直推 min_edge 与 γ 观察等价，advisor 会选简单的那个；
  ④**K5 与 K2 打架**：诚实的买命账单理性教出"永不买"⇒ 神收入归零 + γ 失去熊市
  评估时长。
- **修订后的完整原料包**：K1-K5 之外必加——
  **K6 · 反事实账本（最重要的缺件）**：死亡/转世窗口提供分 regime 拆账
  （storm 高/低段各自的 bets/pnl/breath delta）+ 门反事实
  （"若 γ=+0.1，本世会拦下 N 注、实际盈亏 −$X"）——replay 中结算后可算、
  通道合法；把 1-bit 生死信号变成稠密因果梯度；
  **K7 · 防伪证两件套**：无 regime shift 的对照赛季（正确 γ≈0，检验 advisor
  跟环境还是跟先验）+ 参与度报告（区分换挡与停机棘轮）。
- **实现要点（架构审计定稿，~7 文件 ~150 行）**：storm 在 loop 内算（settled pnl
  deque + 峰值追踪，decide 前无前视）；γ/genome 走 **StrategyConfig 而非 Weights**
  （Weights 是硬契约且 EMA 每结算重建会把 γ 清零——23 处构造点）；只扩
  **rebirth 边界的 advisor key 枚举**（动 REFLECTION_WEIGHT_KEYS 会泄漏进 live
  drain）；**holdout 陷阱**：必须把突变后的 seed 穿进 _run_frozen_holdout，
  否则学到的门恰好在判决处被丢弃；K4 真帽应在感知/归因件落地**之后**
  （rho 仍钉 0.993 时放大注 = 熊市死更快 + 摧毁基线可比性）。
- **第四取证（2026-06-13）**：rho 被牛市推到 0.993 饱和后，1,000 注熊市拉不回
  0.001（logit 饱和梯度消失）——现有唯一自适应通道会被首个 regime 反向洗脑。

### A10 · 神之地租（divine tithe：经常性收租，关闭弃权苟活漏洞）— `代码 DONE`（2026-06-13；G2t 跑批中）
- **动机（取证）**：G2 证伪腿"存活"的真相 = agent 被早期两记 ×5 亏损掐进 breath≈0.67 的
  **零代谢昏迷**（全季 3431 场只下 7 注），因为 **NO_BET 在 sim 里不扣 breath**（PRD 原本
  写过 "NO_BET is NOT a free skip" 但 survival sim 从没实现闲置成本）——存活 = 没死成，不是活得好。
- **规则（用户定）**：每 20 场，神收一次租——**优先扣 $20 bankroll；付不起则扣 5 breath**
  （cash-preferred 自动规则）。参数测试后调。
- **自动自选命中无收入者**（验证：agent 死时很富 $2,200+）：活跃臂永远付得起现金 →
  breath/regime 死亡动态不变、地租只刮赢利进神口袋；昏迷 agent 不赚 → 破产挨 breath 刀 →
  约第 120 场耗死 → advisor 触发 → **证伪腿变 evaluable**。一举三得：主题统一（神从临终
  勒索→经常性收租）、堵昏迷漏洞、修 G2 evaluable。
- **实现**：loop `_attempt_tithe`（cash-preferred，扣 breath 走 canonical 通道→死亡检查抓到）；
  rent 是 operator-domain → 扣 breath 时重置 storm baseline（不污染 regime 感知）；holdout
  不收租（沿用 tribute 先例保可比）；`tithe_revenue` 单列 + fail-closed 不变量；默认关=字节恒等。
  披露 realism rule #5。commits f5d81ab(loop/season/reincarnation)、ee076ed(dashboard 卡片)。
- **诚实预期**：地租能修好"advisor 从不触发"（让 G2 死），但**能否 evaluable 仍取决于
  MiniMax productivity**（G1 时 19 次仅 1 次 productive）——死够多 + 凑够 ≥3 productive
  才 evaluable；否则仍 INCONCLUSIVE，但至少证明地租成功关闭了昏迷漏洞。

### A11 · 前瞻 storm 分类器（forward regime signal）— `PROPOSED`（agent 自己最常求的功能；最高杠杆）
- **来源**：A6 祈祷代际进化——套件后 agent 反复祈求"一个**前瞻的** storm 信号，从可观测
  市场特征在**下注前**预测 regime，而不是当前只在亏损落到 breath 后才反应的 EMA"（祈祷
  #33/34/40/46，多条引用账本数字）。
- **痛点**：A9 的 storm 感知是**反应式**（tick 级 breath delta 的 EMA）——它只能在伤害
  发生**之后**升高，所以 agent 永远在"亡羊补牢"。这正是 G1 即使有 γ 杠杆也只能"死得更深"
  却无法**预先避开**熊市连击的根因（reactive ⇒ 总慢一拍）。
- **改什么**：新增一个**前瞻 storm 估计**——从**当前可观测特征**（成交量波动、价格离散度、
  近期结算时间聚集度等，**绝不含未来结算结果**，无 look-ahead）回归出"未来 N 场的 storm
  概率(0-1)"，喂进 decide() 与 γ 门，替代/叠加反应式 EMA。可顺带满足祈祷 #48（连续而非 0/1）
  + #49（per-bet breath-cost 投影 = storm×edge×stake 合成一个"这注若中 5x 会掉多少 breath"）。
- **对 G2**：在洗牌赛季上检验 agent 是否跟一个**被打散的合成前瞻信号**走——若它仍据此收紧，
  那是先验；强化证伪。
- **风险/纪律**：必须经 look-ahead 审计（特征只能用 ≤ 决策时刻可得的数据）；默认关、字节恒等；
  走 plan-loop。

### A12 · G2 证伪腿强化 — `PROPOSED`（让"死亡是 regime 结构性"从轶事变统计）
- **取证**：当前 G2 是 **n=1 洗牌种子**、razor +$5.67 边际、且（旧版）survived 即未裁决。
- **改什么**：①**多 shuffle 种子**（seed 2/3/4/5）跑 G2，看"打散 regime→死亡消失/变浅"
  是否稳健，把单点轶事升级为统计证据；②**MiniMax productivity 瓶颈**——1/19 的 productive 率
  是 G2 即使死也凑不够 ≥3 productive 门槛的真正限制因素：改 structured-output 提示/schema
  提升解析率，或 Gemini 配额恢复后换 provider；③（依赖 A10）地租已让 G2 会死。
- **成本**：多种子是重复跑（每个 ~1 臂时间）；productivity 改进是提示工程 + 重跑。

### A13 · LLM-融合 vs 线性融合探针 — `DONE`（2026-06-13；结论 no-go）
- **做了什么**：`scripts/probe_llm_fusion.py` 抽 50 个真实决策点，比三方对真实结果的
  Brier + 下注胜率：市场价 0.2370 / 线性融合 0.2294 / **LLM 融合 0.2287**。
- **结论**：**LLM ≈ 线性，噪声内打平**（差 0.0007，N=50 远不可分）；LLM 下注更多（28 vs 23）
  胜率更低（67.9% vs 73.9%）。**融合形式不是瓶颈**——把信号丢给 LLM 推理不会更准。
  与 γ 游走一致：LLM 不比确定性数学更会读这个世界。**省下了"LLM 进决策环"的架构大改。**
- **更深含义**：三方都只比市场强 ~0.008 → **公开信息 edge≈0**，这是上面三相框架的实证起点。

### A14 · breath 感知的风险管理（回测相核心）— `PROPOSED`（agent 第一祈求；A8 重定性）
- **动机**：回测相奖品是**生存**。即使 edge=0，按剩余 breath 缩放注码、breath-EV 为负就弃权、
  risk-of-ruin 反推仓位，能显著拉长存活——这是 agent 49 条祈祷里**最常求的**。
- **改什么**：①sizing 看 breath（低 breath → 极小仓/弃权）；②storm→风险触发器（A9 重瞄）+
  γ2 缩仓（A9 归位）；③**前置依赖 A2**——生存必须进学习目标，否则杠杆又被 PnL 梯度洗掉（γ 游走教训）。
- **与 A2 关系**：A2 给"为什么学"（目标含生存），A14 给"用什么学"（breath 杠杆）。两者配套。

### A15 · smart-money 链上流（mock 相 edge；数据最现成的真 edge 候选）— `PROPOSED`
- **动机**：5 槽里唯一可能携带"市场未定价信息"的。跟 informed 钱包走 = 抄比公众更快的信息源。
- **数据源（链上公开）**：**Dune Analytics**（SQL 查 Polymarket trades/PnL → 建 informed 钱包白名单，
  替代 NBA 时代残留 stub）；**Polymarket Data API**（`/holders` `/trades` 实时持仓）；**subgraph**
  （带时间戳、可做时点重建）。我们已有 Gamma+CLOB 基建，是同源扩展。
- **为什么是 mock 相不是回测**：回测要时点历史持仓（subgraph 可做但是真索引活）；且**首盘市场流动性薄
  → 钱包流稀 → 信号弱**（又一个指向赛果市场的理由）。mock 用实时持仓、无前视、无资金风险。
- **坑**：白名单幸存者偏差/过拟合；edge 在他们进场与价格调整的**时差**里，可能薄。

### A16 · 跨市场互参（mock 相 edge）— `PROPOSED`
- **动机**：同一场比赛的多个 Polymarket 市场（赛果 vs 首盘）应概率一致——不一致 = 套利/信号；
  相关市场的定价也可互参。
- **延伸**：这条天然指向"**为什么赌首盘**"的上游问题——**赛果市场流动性厚得多、更可预测、且锐线/钱包流
  数据更有信号**。若无锁定首盘的理由，迁到赛果市场可能同时改善 edge（A15/D1 变可行）与可预测性。

### A17 · 锐线 edge 历史探针（D1 前置验证）— `代码进行中`（2026-06-13；spec + 8 轮 plan-loop review 收敛 PASS）
- **动机**：A13 已证公开信息 edge≈0；本探针**最便宜地**验证「真 edge 来自市场未知信息」里最现成的一支——
  **锐线**（Pinnacle 收盘隐含概率）是否系统性比大盘均价/Polymarket 更准（逐场配对 Brier）。纯**只读**、零下注/部署/LLM/key。
- **设计**：`docs/superpowers/specs/2026-06-13-sharp-line-edge-probe-design.md`；plan-loop 记录于 `~/.claude/plans/`。
  **核心逻辑已落地**：`agent/backtest/sharp_line.py`（62 测试绿、mypy --strict/ruff clean）——de-vig、姓键、cluster bootstrap、SESOI 三态、ex-ante 选边、orientation fail-closed 门、2a/2b sample 装配、ROI 聚合。
  **次轮**：CLI `scripts/probe_sharp_line.py`（接真实 tennis-data + live Gamma/CLOB）+ 跑批 → 结论落 `docs/backtest/sharp_line_probe.md`。
- **两臂**：**2a**（锐线 vs 各非-Pinnacle 单家去 vig 概率均值，纯 tennis-data 离线，**功效充足、唯一 load-bearing**）；
  **2b**（锐线 vs Polymarket 收盘，重抓 Gamma orientation + 真实 CLOB 开赛前收盘价，best-effort、一整套 fail-closed 门
  → **预期常 UNTESTED**）。统计：`tournament+week` cluster bootstrap（Brier+ROI）+ 预声明 SESOI 三态（EDGE/REFUTED/INCONCLUSIVE）。
- **触发/去向**：**full go**（2a edge 存在 + 2b 可吃）→ 推进 **D1**（实时同时间戳锐线管道）+ 评估首盘→赛果迁移；
  2a 过 / 2b UNTESTED → mock 相向前采集同时间戳数据；2a REFUTED 或 2b REFUTED → mock 相 edge 注意力转 **A15**（链上 smart-money）。
- **诚实**：收盘价是 edge 的乐观上界（非可成交证明）；偏置双向（tick 陈旧度偏乐观 / 流动性幸存者偏悲观）；上界不替代功效。

### A4 · 结算吞吐/滞后 — `PROPOSED`（v3 起遗留）
- **取证**：~38-55% 已下注从未结算（agent 死时仍 open，作废）。
- **改什么**：结算窗口/调度优化，让更多注真正落地结算。影响所有实验的有效样本量。

## B. 物理/数据真实性类

### B1 · v4 真实流动性帽（realism rule #4）— `PROPOSED`
- **取证**：帽公式 `max(5, min(50, volume24hr×5%))` 对已结算市场恒落 $5 下限
  （7,494/7,494）；"按市场流动性封顶"实际是全局 $5 硬编码。保守方向、不虚报，
  但措辞需修正，且均匀帽正是杀死 rho 杠杆的元凶。
- **改什么**：重抓 gamma 每市场**终身总成交量**（缓存没存原始字段，需重新抓
  ~7,494 个市场，~1-2h API），帽改 `clamp(总量×1%, 5, 50)` 逐市场差异化；
  作为 realism rule #4 全量重跑（新章节，不可与 v1-v3 直接比较）。
- **连带效应**：仓位解锁（rho 立刻有反馈）+ 死亡更凶（注大血掉快）——是完整的
  新实验，不是补丁。
- **小项（先行）**：/docs 与 FinetuneLog 中 "capped at $5 by market liquidity"
  措辞改准确（"统一 $5 保守帽，公式对已结算市场退化到下限"）→ 归入当前 Task 5。

## C. 信号/特征工程类（按 预测力×数据可得性 排序）

### C1 · Surface-specific Elo（替换排名积分差）— `PROPOSED`（特征类最大单项）
- 现状 tennis_technical = `tanh(rank_points差/3000)`；积分是 52 周滚动和，失真。
- 改：用 Sackmann 全量比赛史离线建逐场更新的 per-surface Elo + 综合 Elo 混合。
  数据已在硬盘。
### C2 · 疲劳/负荷 — `PROPOSED`
- 7/14 天场次盘数、本届已耗局数、上一场 `minutes`、连续三盘史。现 rest 信号只数
  休息天数；负荷是倒 U。数据已在硬盘。
### C3 · 发球统计差 — `PROPOSED`
- 一发/二发得分率差（场地加权近期窗）、破发点挽救率、ace 率。`w_svpt/w_1stWon/
  bpSaved` 等字段已在硬盘。快速场地与五盘制下权重大。
### C4 · 对手质量加权近期状态 — `PROPOSED`
- 近期战绩按对手 Elo 加权（裸胜率会骗人）。依赖 C1 的 Elo 表。
### C5 · 赛制/轮次/年龄/动量/左右手/身高/退赛风险/主场 — `PROPOSED`（第二梯队打包）
- `best_of`(五盘利好热门)、tourney_level、round、年龄曲线、90 天排名轨迹、
  左撇子对位、身高、近期退赛史（直接影响结算方向）、国籍主场。字段都在 Sackmann。
### C6 · ATP/WTA 分轨校准 — `PROPOSED`（小项）
- WTA 爆冷率更高；kappa/min_edge 分开 sweep。

## D. 市场侧 / Edge 类

### D1 · 跨市场锐线信号（Pinnacle vs Polymarket）— `PROPOSED`（**真 edge 最可能在此**）
- **回测版**：tennis-data.co.uk 免费历史 ATP/WTA 赔率 CSV；信号 = 去水位后的
  Pinnacle 隐含概率 − Polymarket 中价。
- **⚠️ 前视陷阱（必须处理）**：收盘线包含开赛前最后一刻信息——entry_asof 早于
  收盘时拿收盘线 = 偷看未来；诚实回测要用开盘线或时间对齐的线。
- **live/mock-bet 版（用户已确认兴趣）**：live 才是主场（信息时差只在"现在"可
  收割，且 live 无前视问题）。The Odds API（免费档 500 req/月 ≈ 16 快照/天，够
  MVP）或 Betfair 交易所价；去水位（~2-3%）；`smart_money` 槽位名至实归；
  门控 `gap > 点差+滑点+安全边际`；窗口短（分钟级）、容量薄（小仓高频收时差，
  与 $100 资金设定登对）。
- **对生存的意义**：比市场锐的信号源是把胜率推过 ~86% breath 翻正线的最现实路径
  ——"不死"从彩票变成可学。

## E. 已知但暂缓 / 上下文

- E1 · walk-forward 多窗验证（超出单一 holdout）— v3 起遗留。
- E2 · 种子 in-sample 选择偏差（sweep 与验证同窗）— v3 健康检查发现。
- E3 · coverage ≤30%（大量市场被 min_edge/置信门挡掉）— 与 C 类特征升级联动。
- E4 · Phase A/B/C 深层自治（breath 进感知特征、进化选择/谱系淘汰）— 早期讨论，
  A1/A2 是其落地切片。

---

## 当前进行中（非 backlog）
- groundhog v2 双腿真实 run（对照 + Gemini treatment，cap 120）→ 跑完后
  README/文档（含 B1 措辞修正）→ 全量回归 → push → 部署 → 线上验证。
