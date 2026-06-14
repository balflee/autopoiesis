# 设计：第6引擎 `cross_market`（赛果共识 → 首盘电平信号）

- 日期：2026-06-14
- 状态：设计（待批准）
- 关联：A18（[[project_a18_setprob_signal]]）实证「赛果大盘共识反推首盘概率」比 Polymarket 首盘价更准（n=1704/61簇，点估 +0.0035 Brier > SESOI，但 cluster CI 含 0=偏正未坐实，两市场 23% 分歧≥5%）；A17 census 定论留首盘别迁。本设计把该信号做成 agent 的**第6决策引擎**并跑生存回测,看能否在 backtest 相建立小而真的 edge → 活更久 + 赚更久。
- 三相定位：**这是对「backtest 相 edge 天花板≈0」的一次正面挑战**——edge 不来自"我方信号比市场聪明"(A13 已否),而来自**两个 Polymarket 市场的效率差**(薄首盘 vs 厚赛果世界的共识),用**公开赛前数据**就能吃,故**可在 backtest 相做**。

---

## 1. 目标 / 非目标

**目标**：把「赛果共识反推首盘概率」做成第6个融合引擎 `cross_market`（**电平信号**,价格无关),全量接进 v3(扩 RATIONAL 3→4、重扫 11 维、新 seed、weight_updater 接 quality),跑生存回测对照,判定带它 agent 是否**活更久 + 终值更高**。

**非目标**：不做市场迁移(留首盘);不做 prod-live 实时赔率源(本期回测用 tennis-data,prod 在建好实时源前该信号 OFF/中性);不改 v3 的物理(side-correct/realism/breath/permadeath 不变);不碰 Anthropic/OpenAI(cross_market 纯数值;advisor 仍 Gemini)。

## 2. 已 ground 的 v3 关键契约（file:line 本会话核实）

- `agent/engines/decision.py`：`RATIONAL_ENGINES`=(tennis_technical, market_momentum, smart_money)（`:93-97`，3 槽）；`SENTIENT_ENGINES`=(sentiment_llm, crowd_volume)（`:98-101`，2 槽）。
- `_fuse_signals`（`:415`）：`rational = Σ alpha[i]·score·conf for i in range(3)`（`:433`）、`sentient = Σ beta[i]·…for i in range(2)`（`:437`）、`fused = w_r·rational + w_s·sentient`（`:440`）；mean_conf 对全 5 槽平均（`:446`）。**`range(3)`/`range(2)` 是写死的字面量。**
- `decide()` 缺信号检查（`:289`）：`for n in (*RATIONAL_ENGINES,*SENTIENT_ENGINES) if n not in signals → NO_BET missing_signal`。**⇒ 把 cross_market 加进 RATIONAL_ENGINES 后,每次 decide() 都要求 signals 里有 cross_market,否则 NO_BET。故 cross_market 必须永远 emit(无匹配时 emit 中性 0/0,而非省略)。**
- `agent/core/state.py` `Weights(BaseModel)`：`w_r/w_s/alpha[3]/beta[2]/rho` + 归一校验(alpha 3 元 simplex)。→ alpha 改 4 元 simplex + 校验。
- `agent/backtest/cached_sweep.py`：`SignalRow{market_id,slug,scores:dict,confidences:dict,entry_price,outcome,winning_price,liquidity_cap_usd}`；scores/confidences 按 5 引擎名键。`precompute_rows`/`row_to_signals` 构造/还原。
- `agent/engines/weight_updater.py`：结算学习按 per-engine quality 信用分配 PnL（`:349-425`）；决策时读 `<engine>_quality` 特征（`:238-261`）；`WeightDelta` 携 alpha_l1 等。**⇒ 加 cross_market 要补 `cross_market_quality` 特征,否则它的权重学不到。** L3 advisor(Gemini,`strategy_advisor_impl.py`)只看聚合权重轨迹、不看 per-signal,**无法自动发现新维度** → 故"全量"路径需手动扩维 + 重扫,之后结算学习才能 up-weight。
- `agent/backtest/find_optimal_config.py`：LHS **10 维**(w_r 1 + alpha 2 断点 + beta 1 + rho 1 + sizing 3 + value 2)。→ alpha 3 断点 ⇒ **11 维**。
- `docs/backtest/value_seed_v3.json`：`alpha[3]/beta[2]/w_r/w_s/rho + max_breath_risk_pct/min_confidence/min_edge/kappa`。→ v4 alpha[4]。
- 复用：`agent/backtest/sharp_line.py:match_to_set_prob`(已建,7 测试)+ A18 探针(`scripts/probe_setprob_signal.py`)的实体匹配/de-vig/orientation 逻辑。

## 3. 信号设计（cross_market，电平，无 look-ahead）

- **score** = `clamp((p_set_implied − 0.5) · k_scale, −1, 1)`,其中 `p_set_implied = match_to_set_prob(devig(AvgW,AvgL for reference), best_of)`。电平、**价格无关**(符合 engine 抽象;edge vs 当前价由 value-betting 的 `p_model=price+κ·fused` 自己算)。`k_scale` 把 [−0.5,+0.5] 映到 [−1,1] 的标度(默认 2.0,或 sweep)。
- **confidence** = `clamp(|p_set_implied − 0.5| · 2, 0, 1)`(离 coinflip 越远越自信);可选乘"书商一致度"。
- **reference** = 首盘市场 outcomes[0](YES 球员);de-vig 取 reference 那侧的 Avg 赔率(reference==tennis-data Winner→AvgW,==Loser→AvgL)。
- **无匹配/无赔率/同姓碰撞/orientation 不可验 → cross_market 中性(score 0, confidence 0)**,该市场退回靠原 5 信号(优雅降级;且满足 `decide():289` 的"必须 emit")。
- **无 look-ahead**：用赛前 Avg 赔率(赛前可得);`y`(首盘结果)只用于回测打分,不入信号;iid 转换是特征变换、非真值,信任度交给学到的 α₆。
- **诚实命名**：`cross_market`(不复用 smart_money 等 vestigial 死名);docstring 写清"赛果共识反推首盘电平"。

## 4. 接进 v3（RATIONAL 3→4，全量）— 文件级改动

| 文件 | 改动 |
|---|---|
| `agent/engines/decision.py` | `RATIONAL_ENGINES += "cross_market"`(常量 + 名);`_fuse_signals` rational `range(3)→range(4)`;mean_conf over 6;`__all__`。**SENTIENT 不变。** |
| `agent/core/state.py` | `Weights.alpha` 3→4 元 + 归一/simplex 校验改 4 元;`model_dump` 兼容。 |
| `agent/backtest/cached_sweep.py` | `precompute_rows`/`row_to_signals` 认 6 键;新增 cross_market 不破坏旧路径。 |
| `agent/backtest/real_signal_source.py` | 新增 cross_market facet(prod 路径;backtest 走预计算增广) — 缺源时 emit 中性。 |
| `agent/engines/weight_updater.py` | 加 `cross_market_quality` 特征 + alpha delta 覆盖 4 元;freeze 结构沿用(cross_market 属 RATIONAL/α,Phase 1 与其他 α 同步训练)。 |
| `agent/backtest/find_optimal_config.py` | LHS 10→11 维(alpha 3 断点);determinism:前缀维度保持,新维度追加(旧 seed 复现规则照 v3 套路)。 |
| `agent/backtest/setprob_augment.py`（新） | 读 `_signal_rows.json` → 每行实体匹配 tennis-data + de-vig Avg + match_to_set_prob → score/conf → 写 `_signal_rows_v4.json`;掉样桶按原因报。复用 A18 逻辑。 |
| `docs/backtest/value_seed_v4.json`（重扫产出） | alpha[4] 的新优化 seed。 |
| 测试 | decision/weights/fuse 的 4-α 单测;augment 的实体匹配/中性降级单测;sweep 11 维边界。 |

## 5. 预计算（augment，不重跑全信号）

`setprob_augment.py`：读现有 `_signal_rows.json`(首盘宇宙)→ 逐行 entity-match tennis-data(姓对 + slug 日期 ±1)→ de-vig AvgW/AvgL(reference 侧)→ `match_to_set_prob(best_of)` → 写 `scores["cross_market"]`/`confidences["cross_market"]` → 输出 `_signal_rows_v4.json`。无匹配 → 中性(0,0)。**必报匹配率 + 掉样桶**(无匹配/无赔率/同姓碰撞)。比重跑整套 5 信号轻、且不动旧 cassette/Sackmann 路径。

## 6. 重扫 + 新 seed（全量）

`find_optimal_config` 11 维 LHS（复用 value-betting sweep + t-stat 闸 + 按"生存(finished-alive)→终值 PnL"排名）跑在 `_signal_rows_v4.json` 上 → 写 `docs/backtest/value_seed_v4.json`(alpha[4])。agent 拿到 cross_market 的优化起点;结算学习(weight_updater 信用分配)让它在 cross_market 真有预测力时 up-weight = **自我演化叙事落地**。

## 7. 生存回测对照 + 判据（三臂,干净隔离）

跑 survival journey 三臂,同种子、同 realism、同对照基线:
1. **baseline**：v3 seed + 原 5 信号(`_signal_rows.json`)。
2. **treatment**：v4 seed + 6 信号(`_signal_rows_v4.json`,cross_market 活)。
3. **control（隔离重扫混杂）**：v4 seed 但 **cross_market 强制中性(0,0)** — 用来分离"改善来自新信号" vs "只是重扫了个更好的 seed"。

**判据**：treatment **活更久(finished-alive 多)+ 终值 PnL 更高**,且**显著优于 control**(否则改善只是重扫红利,非信号本身)。数值腿先判;真 AI(MiniMax/Gemini)腿可选补。结论落 `docs/backtest/cross_market_journey.md`。

## 8. 风险 / 边界 / 回滚

- **稀释**：重扫给优化起点 + weight_updater 结算学习两道防;若仍被稀释,control 三臂会暴露。
- **edge 太小白做**：A18 +0.0035 可能扛不住 realism;**回测是诚实判官,treatment≈baseline 就老实记 no-go**(不硬上)。
- **v3 回归**：动决策/权重/sweep 核心 → 全程 **plan-loop(codex 审到 0 HIGH/MED)**;**旧 5 信号路径必须 byte 级不变**——cross_market 缺省中性(0,0)时,`_fuse_signals` 的第4项 alpha[3]·0·0=0,fused 与旧 5-信号一致(需保证 4-α 归一在 alpha[3] 那维退化时数值等价,或对照用旧 seed 跑旧路径验证 byte-identical)。
- **prod-live**：回测用 tennis-data(历史赛前赔率);**prod 需实时赛果赔率源(后续工程),在建好前 prod 该信号 OFF/中性**(合 flag-off 默认纪律)。
- **回滚**：cross_market 全程缺省中性 + v3 seed 保留 → 退回 v3 行为零成本。
- **LLM 层**：cross_market 纯数值(非 LLM);advisor 仍 Gemini,**禁** Anthropic/OpenAI。

## 9. 验证

- 单测:4-α 融合(cross_market 中性时 fused==旧 5-信号)、augment 实体匹配/中性降级、Weights 4 元归一、sweep 11 维。
- 端到端:augment 产 `_signal_rows_v4.json`(报匹配率)→ 11 维重扫产 value_seed_v4 → 三臂 survival journey → `cross_market_journey.md`(treatment vs baseline vs control 的 finished-alive + 终值 PnL)。
- gates:`pytest` 全绿 + `mypy --strict` + `ruff`;balflee 账号 + gitleaks;旧路径 byte-identical 验证。
