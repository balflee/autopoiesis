# 设计：锐线 edge 历史探针（A17 · D1 前置验证）

- 日期：2026-06-13
- 状态：设计（待批准）
- 关联：backlog **A17**（新立）；是 **D1**（live Pinnacle-vs-Polymarket 锐线，mock 相）的便宜历史前置验证；产出喂回 **A15**（smart-money 链上流）/ **A16**（跨市场）决策
- 前置认知：**A13**（LLM-融合探针，DONE/no-go）已实证「公开信息 edge ≈ 0」（三方都只比市场强约 +0.008 Brier，N=50 噪声内打平）；本探针验证「真 edge 必须来自市场未知信息」这条论点里**最便宜、最现成**的一支——锐线。
- 经一轮 36-agent adversarial review（23 条经证伪验证的 finding，high=1/med=10/low=12）修订，下文已并入全部修正。

---

## 1. 背景与要验证的论点

三相框架（`docs/optimization_backlog.md` 顶部，2026-06-13 锁定）：

| 相 | 目标 | 能用的信息 | 奖品 |
|---|---|---|---|
| 回测 | 公开信息 + 风险管理下活最久（生存天花板，非 edge 天花板） | 仅公开/历史 | 生存 |
| mock | 加未知信息推 edge 突破公开天花板 | 实时未知信息（链上 smart-money、**锐线**、跨市场） | 真 edge（纸面、无资金风险） |
| live | 实战部署 | 全部 | 收时差 |

A13 已证：对一个校准的市场用同样的公开信息（Sackmann 赛前数据）无法系统性赢过它。要有真 edge，必须引入市场**未知**的信息。三个候选里，**锐线**（Pinnacle 收盘价）是数据**最现成**的一支——而且与 smart-money 不同，它有**干净的历史**可离线验证。

**本探针要便宜地回答一个 go/no-go 问题**：

> 一条公开的锐线（Pinnacle 收盘隐含概率）预测网球**整场赢家**，是否系统性地比 Polymarket 的价更准（Brier）？若准，按「价格分歧时押向锐线」下注，去 vig/费/spread 后是否净正？

市场切换的背景（修订后，避免误导）：我们的回测/生产**主**市场是 **first-set-winner**（`reports/backtest/_signal_rows.json` 里 4544/4925 ≈ **92.3%** 是首盘盘，高方差子事件，**没有干净锐线数据**——博彩公司不开首盘盘）；但同一 cassette 宇宙里**已经夹带 378 个（≈7.7%）已结算的整场 match-winner 盘**（`<赛事>-A-vs-B`），各带收盘 tick + 结算结果（另有 3 个 `will-any-other-outcome` exotic 排除）。锐线只覆盖整场赢家。所以这个探针**顺带验证「该不该把主市场从首盘换成整场」**。

⚠️ **待测假设，不是既定前提**：「整场更可预测、流动性更厚」这一条**必须由 Rung 1 实测**。线上观察显示网球的厚流动性集中在 **per-set 子盘**（如 "X vs Y, Set 1" $30K–$60K 量级）与**锦标赛 outright**（如 "2026 温网冠军" $700K 量级）上，而整场单场 head-to-head 可能多为 ITF 低量盘。「锐线只覆盖整场」这点确定；但「迁移即获得 Polymarket 流动性增益」可能被 Rung 1 证伪。

## 2. 目标 / 非目标

**目标**
1. **清点并刻画已有的 match-winner 库存**（磁盘上 378 个已结算盘，已带收盘 tick），并从 Gamma `/events` 增量补足近期覆盖 + **厚度字段**（liquidity/volume/spread——cassette 里没有，只存了派生的 `liquidity_cap_usd`）；按「整场 match-winner / per-set / 锦标赛 outright」三类分别报数量与中位厚度。
2. 便宜地确认/证伪：锐线收盘隐含概率在历史上比 Polymarket 价（2b）/ 大盘均价（2a）更准（**逐场配对** Brier）。
3. 便宜地确认/证伪：「分歧时押向锐线」的模拟下注去 vig/费/spread 后净正（仅作辅助 sanity-check，见 §6）。
4. 产出一份可提交的 go/no-go 结论 `docs/backtest/sharp_line_probe.md`，明确回答「要不要做 D1 / 换市场」，且区分 **REFUTED / UNTESTED / GO** 三态。

**非目标**
- **不**做真实的市场迁移（首盘 → 整场）——那是探针 go 之后另立的工程。
- **不**做 smart-money 管道（A15）、不做跨市场（A16）、不做实时 D1 同时间戳管道。
- **不**碰合约、**不**部署（`vercel`）、**不**改前端、**不**改 live runtime。
- **不**用任何 LLM（MiniMax/Gemini）——纯数据拉取 + 算术；零 key、零 LLM 成本。
- **不**改现有 first-set 回测物理或 v3 seed——本探针与现有回测正交。

## 3. 探针阶梯（逐级 go/no-go）

最便宜的反驳先做。每一级都能独立把论点便宜地证伪。**任一级失败都不会整体中止探针**——2a 永远跑（§2 目标 2），结论永远产出。

### Rung 1 — 库存清点 + 流动性探针（Polymarket，分钟～1 小时）

1. **先复用已有 cassette（零重抓）**：过滤 `_signal_rows.json` 中**不含** `first-set-winner` 且匹配 `-([A-Za-z-]+)-vs-([A-Za-z-]+)$` 的 378 行（排除 3 个 `will-any-other-outcome` exotic）。其 `agent/backtest/_cache_tennis/<id>.json` 已含 `price_ledger`（真实 CLOB 流）+ outcome + winning_price——这批**直接喂 Rung 2**。
2. **增量补足（Gamma `/events`）**：`GET https://gamma-api.polymarket.com/events?tag_slug=tennis&closed=true&active=false`，分页（`tennis_fetcher.py:42,150-153`）。`/markets?tag=tennis` 是已知坏过滤器（`tennis_fetcher.py:7-9`），不用。筛 match-winner：复用 `is_per_match_market`（`tennis_fetcher.py:48-56`，`-vs-` 中缀），在其基础上**排除** slug 含 `first-set-winner`、**排除** `will-...-win-the-...` 锦标赛冠军盘。本步主要目的是补**厚度字段**（liquidity/volume/spread——cassette 没有），不是发现存在性（已由磁盘 378 盘证实）。
3. **收盘价**：对每个市场，`p_polymarket = ` CLOB `GET https://clob.polymarket.com/prices-history?market={token}&startTs=&endTs=&fidelity=`（`tennis_fetcher.py:43,187-190`）流中**时间戳严格早于 match start（无 startDate 时早于 resolution_ts）的最后一个 tick**。CLOB 查询窗口含结算延迟（`_MATCH_WINDOW_SECONDS` 8h，`tennis_fetcher.py:125-143`），故**必须显式排除 resolution_ts 及之后的结算 tick**（其值被赛果钉在 ~0/1，非市场价）。收盘价**只取自真实 CLOB intraday 流，绝不取 `_build_synthetic_ledger` 的 3 点合成账本**（其末点恒等于 winning_price 0/0.5/1）。剔除结算 tick 后无任何 pre-close tick 的市场 → 归入 §5「无收盘价」掉样桶，不退而读结算值。
4. **token / YES 球员身份**（关键，防 label 反转）：prices-history 用 `clobTokenIds[0]`（`tennis_fetcher.py:181`）= YES 腿；约定 `outcomes[0]` = YES 球员（`sandbox_settlement_poller.py:792-793`、`polymarket_settlement.py:341-344`）。**必须解析 gamma market 的 `outcomes` 数组**取 YES 球员姓，而非假设 slug 序 == outcomes 序（实测 34/34 一致，但 `*-vs-tbd` 占位盘会破例）。
5. **分类计数（一等产出）**：整场 match-winner（X-vs-Y，排除 first-set / 排除 outright）vs per-set（slug 含 set/tiebreak）vs 锦标赛 outright，各报 count + 中位 liquidity/volume。

- **Gate（改判流动性，非存在性）**：存在性已由磁盘 378 盘证实，**不再是 open question**。本 Gate 改判厚度：
  - 若整场 match-winner 盘**存在但厚度远低于 per-set 盘**（per-set 主导）→ 记录「首盘→整场迁移不会带来 Polymarket 流动性增益」，2b 降级为机会性子测试，主结论落在 2a。
  - 若拿不到足量 Polymarket 收盘价 → **2b 不可计算**（不中止探针）；2a 照常跑，「edge 可吃」记为 **UNTESTED（无 Polymarket 历史）**，改判为「只能 mock 相向前采集」。仅当连 2a 也拿不到数据时才整体停。

### Rung 2 — 锐线 edge 探针（tennis-data.co.uk 收盘，约半天）

详见 §4–§7。两个子测试拆开，是为了即便 Rung 1 拿不到足量 Polymarket 历史价，2a 仍能独立给出「edge 存不存在」的答案：

- **2a｜锐线 vs 大盘均价（纯 tennis-data，必定可做，不受 Polymarket 流动性选择偏置）**：在同一批比赛上比**逐场配对** Brier（`p_pinnacle` vs `p_avg` vs 真实赛果）。回答「网球这项运动里锐线到底比大盘准不准」——edge **存不存在**。这是 go/no-go 的**主判据**。
- **2b｜锐线 vs Polymarket（用 Rung 1 拿到的收盘价）**：在实体匹配上的同场比赛，比逐场配对 Brier（`p_pinnacle` vs `p_polymarket`）。回答 edge 在**我们这个场子**上可不可吃。受 §5 实体匹配衰减限制，很可能 n 不足。
- **模拟下注**（按 arm 分别跑，**绝不合并/平均**）：分歧 `d = p_pinnacle − p_soft`；`|d| > 阈值`时押向 Pinnacle 那一侧（`d>0?YES:NO`）。`p_soft`：**2a = `p_avg`，2b = `p_polymarket`**。
- **Gate**：连「乐观上界版」（§7）的 2a（全人群、不受选择偏置）都看不到配对 Brier 改善 → 论点便宜地死，停。

### Rung 3 — smart-money / mock 管道（条件触发，本探针不做）

仅当 Rung 2 确认锐线 edge 后才解锁。本探针**不投入工程**。这里只定义触发与去向：
- **触发**：**2b 的配对 Brier 改善显著**（主门）+ 模拟净正（辅助确认，非独立第二证据，见 §6）。
- **去向**：转 **D1**（实时**同时间戳** Pinnacle-vs-Polymarket 锐线管道，本探针的收盘-vs-赛果不能替代）+ 评估市场迁移（首盘 → 整场）；smart-money（A15）/ 跨市场（A16）按 backlog 排序。

## 4. 数据源与契约

| 源 | 用途 | 契约 |
|---|---|---|
| Gamma `/events` | match-winner 市场发现 + 流动性 | `?tag_slug=tennis&closed=true&active=false`，分页（`tennis_fetcher.py:42`） |
| CLOB `/prices-history` | Polymarket 历史收盘价（**真实流的 pre-close tick，排除结算 tick**） | `?market={token}&startTs=&endTs=&fidelity=`（`tennis_fetcher.py:43,187`） |
| gamma market `outcomes` | YES 球员身份（`outcomes[0]`） | 解析数组，归一姓；防 label 反转 |
| **tennis-data.co.uk** | Pinnacle + 大盘收盘赔率（**match-winner**，首盘没有） | 年度文件经核实为 **`.xls`**（旧 Excel 二进制，非 `.xlsx`；CRAN `welo` 包即 `paste0(year,".xls")` + `read_excel`），另有 `alldata.php` 的 `.zip`（内含 CSV+XLS）全量包；列 `PSW`/`PSL`（Pinnacle 赢家/输家十进制赔率）、`AvgW`/`AvgL`（大盘均价）、`Winner`/`Loser`（"Sinner J." 缩写式）、`Date`、`Tournament`、`Surface`、`Comment`（Completed/Retired/Walkover）。**本探针固定拉 2025、2026 两年**（见 §12 锁定窗口）。 |
| Sackmann（已在用） | 仅做赛事/球员校验，非主路 | 全名式 "Jannik Sinner"（`tennis_match_resolver.py`） |

- tennis-data.co.uk 仅在文档/backlog 中被提及（`TODOS.md`、`docs/PRD.md §12`、`optimization_backlog.md D1`），**从未被任何代码或数据路径 import/抓取**（`build_training_set.py:675` 仅注释里把 "Pinnacle ML" 当校准参照，非数据源）——本探针是首次把它接成真实数据拉取。仅离线、只读、不入下注路径。
- **格式/依赖决策**：实现首步先拉一个年度文件确认实际扩展名。①若仓内已有 pandas → `pandas.read_excel` 直接读 `.xls`（需 `xlrd`，是一处真实新依赖）；②否则改 pin 到已核实的 CSV 镜像（Kaggle/MLT）并在 §4 注明镜像非源站契约。最终核实到的格式与所选解析路径写进结论。
- **早年空列**：PS（Pinnacle）列约 ~2004 前、Avg/Max 更晚，**列名虽在但整列为空**——光校验表头不够。拉取时除锁列名外，须按年校验 `PSW`/`PSL` 非空填充率；空/NaN/≤1.0 的行归入 §5「无锐线」掉样桶（不可进 §6 去-vig 算 `1/PSW`，否则 NaN）。结论里按年报告 PS/Avg 填充率。
- 数据落地：原始文件缓存到 gitignored `reports/a17/raw/`；不提交体积大的原始赔率表。

## 5. 实体匹配（join 的承重点）

三方命名格式各异，必须显式归一并**报告匹配率 + 按原因分类的掉样**（A13 教训：绝不静默截断）。

**姓提取规则（统一归一到同一 compound key）**
- **tennis-data.co.uk**（`"Sinner J."` = 姓 + 名首字母缩写）：姓 = **去掉末尾首字母缩写 token 后剩下的全部 token**（拼接、小写、去重音、去连字符）。缩写 token = 「1+ 个大写字母各后跟一个点」串，正则 `^([A-Z]\.)+$`（匹配 `J.`、`J.M.`、`D.R.`）。**复姓在缩写前会拆成多 token**（`"De Minaur A."`、`"Del Potro J.M."`、`"Auger Aliassime F."`、`"Ramos-Vinolas A."`），必须保留除末尾缩写外的全部 token。**绝不取首 token**。
- **Polymarket slug**（`...-de-minaur-vs-djokovic`）：**不直接复用** `_VS_SUFFIX`（`tennis_match_resolver.py:34` 的 `-([A-Za-z]+)-vs-([A-Za-z]+)$` 单 token 会把复姓截成 `minaur`，与 tennis-data 侧的 `deminaur` 错配并静默掉样）；探针本地用**加宽版** `-([A-Za-z-]+)-vs-([A-Za-z-]+)$` 捕获连字符多 token 姓，再归一（内部 `-` 视作姓内连接：`de-minaur`→`deminaur`、`auger-aliassime`→`augeraliassime`）。
- **Polymarket `outcomes[0]`**（YES 球员）：用同一 format-aware 提取器归一（去尾随空格、去重音）；**2b 参考球员 = 归一后的 `outcomes[0]` 姓**（不是 slug 第一姓、也不是字母序）。
- **Sackmann**（`"Jannik Sinner"` 全名，仅校验）：取**末** token 作姓（末-token 取法在 `build_name_index`，`tennis_match_resolver.py:88`：`str(full_name).split()[-1]`，函数体 74-90）。
- 归一工具：**仅**复用 `_norm_surname`（`tennis_match_resolver.py:44-48`，**只**做去重音+小写，**不**取 token）；token 选取由探针本地 format-aware 薄封装各自实现。这是与现有 resolver 的唯一差异。**两侧产出必须逐字符相等才入 join**（须保证 tennis-data `"Del Potro J.M."`→`delpotro` == Sackmann `"Juan Martin Del Potro"` 拼接键——即 Sackmann 侧复姓也须拼接而非单末 token）。

**join key**：归一姓对 + 赛事日期（±1 天容差）+（可选）tournament/surface 校验。

**赛果完整性过滤（match before join）**
- tennis-data 行先按 `Comment` 过滤——**仅保留正常打完**（`Comment` 大小写无关 == `Completed`；任何非-Completed 值——退赛 RET、弃赛 W/O 及逐年措辞变体——一律剔除）。
- 跨源一致性：Polymarket 侧凡结算为 `void`（`polymarket_settlement` 把 50-50 弃赛塌成 void，`compute_bet_pnl` 对 void 返回 0.0 会把弃赛注静默记成中性、偏置 ROI）的市场，在 2b 里同样剔除；两源对「是否完整结束」判定不一致者按不完整处理——避免 `y=1`/`y=void` 标签矛盾污染 Brier 与 ROI。

**必报掉样桶（按原因）**：候选市场数、成功匹配数、匹配率、`无锐线(空/NaN PSW)`、`无收盘价`、`非完整赛果(RET/W-O)`、`姓歧义/复姓不可表示`、`outcomes-slug 不一致(含 *-vs-tbd)`、`日期对不上`。匹配率过低（<40%）应在结论里降级判定。

## 6. 指标与去 vig 数学

**去 vig（唯一新写的纯函数 `_implied_prob_two_way`）**：两路十进制赔率 → 去 overround 隐含概率。
```
raw_w = 1/PSW ; raw_l = 1/PSL
p_pinnacle(reference) = raw_ref / (raw_w + raw_l)   # 两路按比例归一去 vig
```
- 仓内**无**现成 de-vig 工具（grep 确认）。**已知近似偏差**：按比例归一去 vig 对收藏端（favorite）略有偏置（favourite-longshot / shin 效应），对一个 go/no-go 上界足够；结论里注明这是近似、未做 shin 校正。前置：空/NaN/≤1.0 的 PSW/PSL 在去 vig 前过滤为「无锐线」掉样，不参与计算。

**统一参考事件（避免退化）**：三方必须预测**同一事件**才能 apples-to-apples。每场固定一个参考球员：
- 2b（有 Polymarket）：参考 = `outcomes[0]` 球员；`y = 1` iff 该球员赢。`p_pinnacle`/`p_avg`/`p_polymarket` 都对这同一球员取值。
- 2a（纯 tennis-data）：参考 = 字母序在前的姓；`y` 同理。
- （不要用「永远算赢家的隐含概率」——那样 `y≡1`、Brier 退化成只测信心强度，且对三源不可比。）

**逐场配对 Brier 检验（修订：配对，非聚合差）**：两个 Brier 在**同一批**比赛上测、`y` 与 `p` 高度相关，故正确统计量是**逐场配对差**。**符号约定（与 plan/`agent/backtest/sharp_line.py` 的 `brier_edge` 一致）：`edge_i = (p_soft−y)² − (p_pin−y)²`，正值 = 锐线更准**：
- 2a：`edge_i = (p_avg−y_i)^2 − (p_pinnacle−y_i)^2`
- 2b：`edge_i = (p_polymarket−y_i)^2 − (p_pinnacle−y_i)^2`
- 对 `mean(edge_i)` 做**配对 bootstrap CI**（重采样单位=比赛，≥1000 次）或 Wilcoxon signed-rank；与下方 Go 判定的 `(Brier_soft − Brier_pinnacle)` 下界>0 同号自洽。Brier 本身复用 `_brier`（`probe_llm_fusion.py:130-131`）。

**功效 / MDE 预估（运行前写出）**：每场 Brier 差的配对 SD ≈ 0.05；文献/A13 给的效应量约 0.001–0.004 Brier。按 80% 功效：效应=0.004 需 n≈750 场，效应=0.0016 需 n≈4,600 场。**2a**（全量 tennis-data，每年数千场，不受匹配衰减）功效充足；**2b** 受 §5 衰减限制，n 可能只有数十到低百，**很可能欠功效**——必须在报告里写出 2b 的**最小可检测效应（MDE）**。

**模拟下注 P&L（辅助 sanity-check，非独立证据）**：
- 入场价 = 收盘 ask = `YES mid + 0.5 × 观察到的 spread`（吃单跨价差；spread 取自 Rung 1，缺失记掉样而非按 0）。
- 结算：`compute_bet_pnl(..., side_correct_pricing=True)`（`agent/backtest/cached_sweep.py:47-115`）按真实赛果（winning_price≈1.0 的清算值，**非**终盘 tick）。**必须**传 `side_correct_pricing=True`（默认 False 是 legacy，对 NO 侧按 YES 价错付，最高 81x）；其内部经 `effective_entry_price` 取实际下注腿成本，NO 侧押注尤其不能用默认。为让费进 P&L，给复用路径加 `taker_fee_usd: float = 0.0`（默认 0 = byte-unchanged）。
- 费：Polymarket Sports 吃单费 `fee = size_shares × p × 0.03 × (p×(1−p))`（50/50 处≈0.75%，向两端递减；2026-03 费率）。每档阈值报三栏 ROI：**fee={0（乐观上界对照）, 0.75%-formula（realistic，主数）, 1.0%（conservative 上限）}**。
- 阈值扫 `{0.02,0.03,0.05,0.08}`，**每个 arm（2a/2b）各自**报 bets / win-rate / 净 ROI，分行列出**禁止平均**。**Rung-3 触发与「edge 可吃」只读 2b（`p_soft=p_polymarket`）的 ROI**——因为 Polymarket 才是可成交场子。
- 所有 Brier / ROI 数字**只在完整结束（Completed）场次上**计算；附跑一档「含退赛(Retired)、按 tennis-data 实际盘中赛果结算」的**敏感性变体**，报其与主结果之差（弃赛 W/O 因无真实对抗赛果始终排除）。

**Go 判定（写进结论，区分三态）**：
- 「显著」定义：相应配对 Brier 差的 **95% 配对 bootstrap CI 整段不跨 0**（CI 跨 0 即「未显著」）。
- **edge 存在（2a，主判据）**：`(Brier_avg − Brier_pinnacle)` 逐场配对差的 95% CI 下界 > 0。
- **edge 可吃（2b）**：仅当 Rung 1 拿到足量收盘价时可判：
  - `(Brier_polymarket − Brier_pinnacle)` 配对 CI 下界 > 0 **且** 至少一档阈值在 **realistic 费档（0.75% formula + half-spread 入场）** 下模拟净 ROI > 0（该档 **bets ≥ 30** 才允许驱动 go；不足 30 注只报数不入判定）→ **可吃**。模拟 ROI 是**同一校准信号的二次 sanity check，不是独立第二证据**——它与 Brier 承载同一份信息，只加了「阈值筛选 + 赔付不对称」透镜，**不得**当成「两项皆过」累计信心。
  - 数据齐但不满足 → **REFUTED**。
  - 无 Polymarket 历史，2b 不可计算 → **UNTESTED**（≠ no-go）。
- **结论**：
  - 2a REFUTED（配对差显著为负或达 MDE 的功效充足 null）→ 整体 no-go，论点便宜地死。
  - 2a 通过但 2b REFUTED → no-go（我们场子吃不到）。
  - 2a 通过但 2b UNTESTED / n 不足未达 MDE → **部分 go / inconclusive**：记「锐线 edge 存在但本场子未验证」，去向改判「mock 相向前采集同时间戳数据」。**fail-to-reject ≠ no-edge——n 不足不得记『论点死』。**
  - **full go（推 D1）必须 2a 通过 + 2b 可吃。**

## 7. 诚实偏置（必须写进结论）

本探针两条价都是**收盘**：Pinnacle 收盘隐含概率 vs Polymarket 该市场的 pre-close 最后一个 tick（§3）。

**(a) 收盘价入场 = 无可成交 horizon**：模拟下注**不演示「能赚钱」**，只把校准对比换算成钱——入场价是已不存在的收盘价，没有时间也没有对手方去成交。真实可成交 edge 需要在**收盘前**某个相对 Pinnacle 已 stale 的 Polymarket 价入场、扣实际跨越的 spread/费后仍赢——这一步明确留给 **D1**（同时间戳/盘前管道），本探针不声称已证。

**(b) 净偏置方向先验未知（不是单向「乐观上界」）**：由两个反向效应共同决定，必须各自量化并列写进结论：
- **Polymarket tick 陈旧度（偏向 Pinnacle = 偏乐观）**：薄网球盘常在锁盘前数小时就停止成交，其 pre-close tick 可能远早于 Pinnacle 收盘；越陈旧 Pinnacle 越占信息时差，measured edge 越被高估。→ **必报 Polymarket 最后 tick 相对锁盘/赛果时间的年龄分布**（中位、p90）。
- **流动性/幸存者选择（偏向 Polymarket = 偏悲观）**：只有流动性足、关注度高的比赛才有可用 Polymarket 收盘价，而这些正是市场最有效、Pinnacle edge 最小的比赛；故 2b 在匹配子集上量到的 edge 可能是对全体人群的**下界**。→ 把 2b 子集的 liquidity/volume 分布与 Rung 1 全量分布对照，标注选择偏置方向。

**(c) 锚点**：真 edge 的**存在性**仍以 **2a**（锐线 vs 大盘均价、全 tennis-data 人群、无 Polymarket 选择）为准——2a 不受流动性选择影响。2b 只说明「在我们场子的（偏流动性）子集上能否吃」，其偏置 sign 由上述两效应实测大小定，据实记录，不许把任一方向包装成实证 edge。**上界 caveat 不能替代统计功效**——n 不足时见 §6 的 inconclusive 分支。

## 8. 范围 · 成本 · 交付物

- **只读、零下注、零部署、零 LLM、零 key**。唯一外部副作用 = 拉取公开 Gamma/CLOB/tennis-data 数据到本地缓存。
- 文件（镜像 `scripts/probe_llm_fusion.py` 结构）：
  - `scripts/probe_sharp_line.py`（新）：argparse `main(argv)->int`；从 gitignored 缓存读数据；写报告到 `reports/a17/`；纯 sync + 必要处 `asyncio.run`。**不**需要 `_load_dotenv_if_present`（无 key）。
  - `agent/backtest/sharp_line.py`（新）或脚本本地：`_implied_prob_two_way`、format-aware 姓提取、加宽 slug 正则、join、模拟下注——纯函数，便于单测。
  - 原始数据 → `reports/a17/raw/`（gitignored）；机器报告 → `reports/a17/probe_report.json`（gitignored）。
  - **可提交结论** → `docs/backtest/sharp_line_probe.md`（committed，judge 可读；含 go/no-go 三态、配对 Brier + CI、§6 功效/MDE、§7 双向偏置 + tick 年龄分布、匹配率与全部掉样桶、费/spread 三档 ROI、锁定窗口与各源计数）。
  - backlog：`docs/optimization_backlog.md` 新增 **A17** 条目，交叉引用 D1/A15/A16。
- **可能的新依赖**：若需读 `.xls` 且仓内无 pandas → `pandas`+`xlrd`（在 §4 决策后据实记录）。

## 9. 测试（TDD-lite）

复用仓内约定（sync pytest，`tests/agent/backtest/`，`pytest.approx`，class 分组，inline builder）。只给**纯函数**写单测，探针**运行结果本身**是交付物：
- `_implied_prob_two_way`：已知赔率去 vig（如 `1.5/2.75` 手算对照）；overround 正确剥离；空字符串/NaN/≤1.0 的 PSW 断言归类为「无锐线」而非参与计算。
- **format-aware 姓提取（tennis-data 侧）**：`"Sinner J."`→`sinner`、`"De Minaur A."`→`deminaur`、`"Del Potro J.M."`→`delpotro`（多首字母缩写 `J.M.` 整体剥离）、`"Auger Aliassime F."`→`augeraliassime`、`"Ramos-Vinolas A."`→`ramosvinolas`（去连字符）、`"Collins D.R."`→`collins`。Sackmann 侧：`"Jannik Sinner"`→`sinner`、`"Juan Martin Del Potro"`→`delpotro`（断言与 tennis-data 同键）。
- **slug 加宽正则 + join-key 等价**：`...-de-minaur-vs-djokovic` 经加宽正则 + 归一产出 `deminaur`，断言与 tennis-data `"De Minaur A."` 同键；`auger-aliassime` 同测。
- **YES 球员身份**：fixture 中 `outcomes[0]` = slug 第二姓（合成）断言参考正确翻转；`*-vs-tbd` fixture 断言被丢弃 + 计入「outcomes-slug 不一致」桶。
- **赛果完整性**：`Comment != Completed`（RET/W-O）行断言被剔除并计入桶；大小写无关匹配。
- 模拟下注：构造**分歧为负（d<0 → 押 NO 侧）** + 已知赛果的 fixture，断言 P&L == `compute_bet_pnl(..., side_correct_pricing=True)` 的 NO-leg 结果（按 `1 − yes_price` 计价），且**不**等于 legacy YES-价错付——锁死 side-correct。
- `_brier`：借 `probe_llm_fusion` 既有逻辑，加一两个 fixture 锚定。

## 10. backlog 接驳

- 新增 **A17 · 锐线 edge 历史探针**（D1 前置）：状态 = 本 spec 批准后转 plan → 执行。
- 结果回灌：
  - full go（2a+2b）→ 推进 **D1**（实时同时间戳锐线管道）+ 评估市场迁移（首盘→整场）。
  - 2a 过 / 2b UNTESTED → mock 相向前采集同时间戳数据（不推 A15 的 no-go 分支）。
  - 2a REFUTED 或 2b REFUTED（功效充足）→ 在 backlog 标注「锐线乐观上界无 edge」，mock 相 edge 注意力转向 **A15**（smart-money 链上流，数据更难但唯一未被证伪的真 edge 候选）。
- 与现有 first-set 回测**正交**：不动 v3 seed、不动 survival 物理。

## 11. 风险与回滚

- **read-only 低风险**：探针不写任何持久状态、不下注、不部署。回滚 = 删脚本/报告，无副作用。
- **主风险 = 数据可得性/功效**：①整场 match-winner 盘可能厚度不足（Rung 1 Gate 便宜地暴露，2a 不依赖它）；②实体匹配率可能低（§5 必报，<40% 降级；复姓/多 initial 已在 §5/§9 显式处理，防静默掉样）；③2b 很可能欠功效（§6 MDE + inconclusive 分支化解，绝不把 n 不足当 no-go）；④tennis-data 早年 PS 列整列为空（§4 按年校验填充率）。
- **诚实风险**：上界被误读成实证 edge / 单向上界 → §7 强制并列**双向** caveat + tick 年龄分布 + 「上界不替代功效」化解。
- **依赖风险**：tennis-data 年度文件实为 `.xls`，可能引入 `pandas`+`xlrd`（§4 首步核实后决策）。
- **toolchain**：Python 3.11 兼容；新外部源仅离线文件，无新运行时网络依赖。

## 12. 待批准的开放点（不阻塞，结论里据实记录）

- **样本范围（运行前锁定，禁止看到 Brier/ROI 后再改窗口）**：已结算 match-winner 市场取 `resolution_ts ∈ [2025-01-01, 2026-06-01]`；对应拉 tennis-data ATP+WTA **2025、2026** 两年。结论报告锁定窗口与各源窗口内计数（Polymarket 市场数 / tennis-data 行数 / 成功 join 数）。若功效不足，只能**整体放大窗口并重跑全套指标**（再报告新窗口），不得仅为提升 ROI 挑子区间。
- **最低 N**：匹配场次 < ~200 时 2b 的 CI 过宽 / 达不到 MDE → 结论记「inconclusive — 数据不足」，并写出所需样本量缺口（不判 no-go）。
- **费/spread**：已在 §6 锁定（realistic = Sports 0.75% formula + half-spread 为主数，fee=0 仅乐观上界对照）；仅留 conservative 1.0% 档比例据实记录。
- **去 vig 近似**：按比例归一（未做 shin 校正），结论注明对收藏端的偏置方向。
