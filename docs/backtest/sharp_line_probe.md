# A17 — 锐线 edge 历史探针：结论

**OVERALL：INCONCLUSIVE on Polymarket (2b CI compatible with both 0 and SESOI).**

只读探针（零下注/部署/LLM/key）。2a = 锐线 vs 非-Pinnacle 大盘共识（纯 tennis-data，载重判据）；2b = 锐线 vs Polymarket 赛前收盘（378 cassette，best-effort、fail-closed）。
符号约定：`edge_i=(p_soft−y)²−(p_pin−y)²`，正=锐线更准；verdict = cluster bootstrap CI + SESOI 三态。

## 2a — 锐线 vs 大盘共识（载重）
- 候选 12886，计入 9856，Avg 回退 18
- 掉样：{"incomplete_ret_wo": 500, "no_sharp": 2524, "no_soft": 1, "surname_collision": 5}
- 配对 Brier-diff 点估 0.00014；cluster 95% CI [-0.00019, 0.00044]（n=9856, clusters=294）；iid 敏感 [-0.00014, 0.00044]
- SESOI=0.002 → **REFUTED**

## 2b — 锐线 vs Polymarket 收盘（best-effort）
- 候选 378，匹配 303（匹配率 80%）
- 掉样：{"incomplete_ret_wo": 13, "missing_gameStartTime": 5, "date_miss": 19, "sparse_ledger": 24, "no_sharp": 5, "no_prematch_tick": 8, "void": 1}
- 配对 Brier-diff cluster 95% CI [-0.00318, 0.00906]（n=303）→ Brier **INCONCLUSIVE**
- ROI 主门（thr=0.03, realistic fee+spread, bets=55/30）→ pass=False
- tick 年龄：{"median_h": 9.831111111111111, "p90_h": 15.164722222222222, "n": 340}

## 解读与去向

- **2a 载重（功效充足，n=9856/294簇）**：锐线比庄家共识只 +0.00014 Brier，cluster CI 上界 < SESOI → **REFUTED**：锐线本身不比高效的庄家市场更准。
- **2b 赚钱问题（best-effort，n=303/26簇）**：锐线 vs Polymarket 收盘点估 +0.00255（明显大于 2a 的 +0.00014），realistic 档 ROI 点估为正，但 cluster CI 跨 0 → **INCONCLUSIVE**：点估暗示 Polymarket 比庄家更软、锐线/共识可能赢它，但簇数不足、功效不够确认。

**去向**：
- 历史 2b 无法定论：Polymarket 已清空已结算市场的 CLOB 历史价（重抓全空），cassette 仅留 303 场 / 26 个 tournament-week 簇，CI 太宽。
- 推进 **D1**（实时、同时间戳，锐线/庄家共识 vs Polymarket；更多市场 → 更多簇 → 足够功效）确认 2b 的正点估是否为真。
- 2a 的 REFUTED 不否定这条：它说的是「锐线 ≠ 比庄家共识有 edge」；2b 问的是「庄家/锐线共识 vs 更软的 Polymarket」——后者才是可吃的差价（softer-venue 套利）。
- 若 D1 证实，评估首盘 → 赛果迁移；smart-money（A15）/ 跨市场（A16）按 backlog 排序。

## 诚实 caveat
- 2b Polymarket close is sourced from the cassette's CAPTURED-LIVE CLOB ledger: the CLOB prices-history endpoint now returns EMPTY for these long-closed resolved markets (history purged post-resolution), so the cassette (fetched while live) is the only historical source. Orientation is verified result-independently against refetched Gamma outcomes + CLOB token labels; sparse ledgers (<min ticks, possible synthetic) are dropped.
- Closing-line vs realized-outcome = an OPTIMISTIC UPPER BOUND on edge, not a tradeable demonstration; real same-timestamp validation is D1.
- Two-sided bias: Polymarket tick staleness (optimistic for sharp) vs liquidity/survivorship selection on which markets have a close (pessimistic).
- de-vig is proportional (no shin); slight favourite bias.
- 2b spread is a declared assumed grid, not measured.
- 2a is the load-bearing arm; 2b is best-effort and may be UNTESTED.
