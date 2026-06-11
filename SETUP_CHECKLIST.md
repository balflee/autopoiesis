# 外部服务准备清单

> 项目: Autopoiesis (Arbitrum Open House London hackathon)
> 链策略: **Plan v3** (2026-05-19 锁定) — 3 链平行部署，**不**自建 Orbit L3
> 用途: 给 user 做账号注册 + API key 申请的 single-source-of-truth
> 维护: 项目开发自动消费 `.env.example`，本文档帮你按优先级注册

---

## 链部署（3 链平行 — Plan v3）

| 链 | 角色 | 公开 RPC | 状态 |
|---|---|---|---|
| **Robinhood Chain testnet** | 🎯 primary (sponsor synergy, top-3 reserved spot) | 需注册 | ❓ |
| Arbitrum Sepolia L2 | 🔥 hot fallback (相同 .sol 平行部署) | `https://sepolia-rollup.arbitrum.io/rpc` | ✅ 可直接用 |
| Polygon Amoy testnet | 🏷️ Polygon ecosystem 类别提交 | `https://rpc-amoy.polygon.technology` | ✅ 可直接用 |
| Polygon mainnet | 📊 READ-ONLY (Smart Money α₃ indexer) | `https://polygon-rpc.com` | ✅ 公开层，但建议申请 Alchemy 提升限速 |

---

## 必须注册的（按优先级）

### 🔴 P0 — sprint_3 末 / sprint_4 前必须

#### 1. Robinhood Chain testnet
- **链接:** https://docs.chain.robinhood.com
- **要做:**
  - 看 RPC URL 怎么拿
  - 看 testnet faucet 流程
  - 确认是否需要 KYC / waitlist
- **填到 env:** `ROBINHOOD_CHAIN_RPC=...`
- **风险:** 这是 v3 plan 的 primary chain，如果注册不下来要 fallback 到 Sepolia
- **时间预估:** 10-30 min（看是否需要 KYC）

#### 2. Testnet deployer 钱包
- **怎么做:**
  1. 用 MetaMask 或 Foundry `cast wallet new` 生成**新钱包**（**不**用 mainnet 钱包）
  2. 私钥填到 `DEPLOYER_PRIVATE_KEY=`
  3. 去 3 个 faucet 拿 testnet ETH:
     - Arbitrum Sepolia: https://www.alchemy.com/faucets/arbitrum-sepolia 或 https://faucet.quicknode.com/arbitrum/sepolia
     - Polygon Amoy: https://faucet.polygon.technology/
     - Robinhood Chain: 在 docs.chain.robinhood.com 文档里
- **填到 env:** `DEPLOYER_PRIVATE_KEY=0x...`
- **时间预估:** 15 min（每个 faucet 几分钟）

#### 3. Alchemy account (Polygon mainnet RPC)
- **链接:** https://alchemy.com
- **为什么:** T-B-002 smart_money engine 要扫几千个钱包历史，公开 RPC 会 throttle
- **要做:**
  1. 注册 free tier (300M compute units/月)
  2. 创建 app: chain=Polygon, network=mainnet
  3. 拿 HTTPS URL
- **填到 env:** `POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/...`
- **时间预估:** 5 min

### 🟡 P1 — sprint_5 之前

#### 4. Pinata IPFS pinning
- **链接:** https://pinata.cloud
- **为什么:** Tombstone NFT 的 memoryBankCid 字段需要把 agent 死亡时的 memory bank pin 到 IPFS
- **Free tier:** 1 GB 存储 + 1000 pins（够用）
- **要做:**
  1. 注册 → API Keys → New Key (full access OK)
  2. 拿 `apiKey` + `apiSecret`
- **填到 env:**
  ```
  PINATA_API_KEY=...
  PINATA_SECRET_KEY=...
  ```
- **时间预估:** 5 min

#### 5. Reddit PRAW credentials
- **链接:** https://reddit.com/prefs/apps
- **为什么:** T-B-002 β₂ crowd_volume engine 要读 NBA subreddit 的 post 量（Pushshift 长期挂了，改用 PRAW）
- **要做:**
  1. 登录你的 Reddit 账号 → "create another app" → 选 "script"
  2. 拿 client_id + client_secret
- **填到 env (待加到 .env.example):**
  ```
  REDDIT_CLIENT_ID=...
  REDDIT_CLIENT_SECRET=...
  REDDIT_USER_AGENT=autopoiesis-research/0.1
  ```
- **时间预估:** 3 min

### 🟢 P2 — hackathon demo 前

#### 6. Vercel (dashboard 部署)
- **链接:** https://vercel.com
- **为什么:** Demo 时不想现场跑 `npm run dev`；deploy 到 Vercel 给 judges URL
- **Free tier:** 100 GB 流量/月（够）
- **要做:** GitHub 连 repo → import dashboard/ → deploy
- **时间预估:** 5 min（自动部署）

#### 7. Etherscan API key（contract verify）
- **链接:** https://etherscan.io/myapikey (Arbitrum) + https://polygonscan.com/myapikey (Polygon)
- **为什么:** Contract 部署后自动 verify 让 judges 能看 source code
- **Free tier:** 5 calls/sec, 100K calls/天（够）
- **填到 env (待加):**
  ```
  ETHERSCAN_API_KEY=...
  POLYGONSCAN_API_KEY=...
  ```
- **时间预估:** 5 min（注册即时）

---

## 免费层限制 + 项目用量评估

| 服务 | 免费限制 | 项目用量 | 够不够 |
|---|---|---|---|
| Arbitrum Sepolia RPC（公开） | 无明确限速 | 部署 + 偶尔 read | ✅ |
| Polygon Amoy RPC（公开） | 无明确限速 | 同上 | ✅ |
| Polygon mainnet RPC（公开） | 经常 throttle | T-B-002 smart_money 历史扫描 ~10K req | ⚠️ 建议用 Alchemy |
| Alchemy free tier | 300M CU/月 | 单次扫描 + 实时监控 | ✅ |
| NBA balldontlie（无 key） | 60 req/min | Phase 1 训练 200 场 ≈ 1000 req ≈ 17 min | ✅ |
| Reddit PRAW | 60 req/min | 实时 + 历史训练 | ✅ |
| Pinata IPFS | 1 GB / 1000 pins | Tombstone NFT metadata 每个几 KB | ✅ |
| Vercel | 100 GB/月 | Demo 几百访问 | ✅ |
| Sepolia faucet | 0.5 ETH/24hr | 5 合约 deploy ≈ 0.25 ETH | ✅ 一次 faucet drip 够 |
| Polygon Amoy faucet | 0.5 MATIC/24hr | 同上 | ✅ |
| Etherscan API | 5 calls/sec, 100K/天 | Verify 一次性 | ✅ |

---

## 🚨 真正的项目风险（不只是免费层问题）

| 风险 | 影响 | Mitigation |
|---|---|---|
| **Robinhood Chain testnet** 状态未知（要 KYC？waitlist？dead？） | primary 部署目标可能不可用 | v3 plan 已设计 Sepolia 作 hot fallback；最坏情况 Sepolia 当 primary |
| Phase 2 实时 agent 需要 always-on 进程 | Hackathon 当天必须保持运行 | Demo 当天本地 PC 不睡 + 网络稳；或租 $5/月 VPS (Hetzner/DigitalOcean) |
| Polymarket KYC 限制（非美/欧地址不能注册） | 真下单不能演示 | v3 plan **不要求**真下单 — Phase 1 离线训练 + Phase 2 paper trade mode 够用 |
| Pushshift Reddit 服务长期挂 | β₂ 数据源缺失 | **已知** — TODOS 注释要用 PRAW（OAuth）替代 |

---

## ✅ Production LLM — Gemini 3.1 Flash Lite (AI Studio)

Production runtime LLM for Autopoiesis agent's sentiment + reflection
engines (PRD §4.3, §4.4). Wraps via `agent/llm/gemini_client.py`
(sprint_4 T-B-006) using the `google-genai` SDK.

| 项目 | 详情 |
|---|---|
| **Provider** | Google AI Studio (NOT Vertex AI — free tier sufficient for hackathon) |
| **Sign up** | https://aistudio.google.com/apikey |
| **Free tier** | 15 RPM / 1M tokens-per-day on Gemini Flash Lite series — generous for dev |
| **Env var** | `GEMINI_API_KEY=<key>` (see `.env.example`) |
| **Model id** | `gemini-3.1-flash-lite` |
| **Time to provision** | ~3 min (Google account → click "Get API key" → copy) |

> **Why Gemini, not Anthropic/OpenAI?** Development tooling (orchestrator
> spawning Track-B agents that write code) already uses Claude Code
> internally — adding Anthropic SDK to the production agent would duplicate.
> Per-call cost on Gemini Flash Lite is also ~10x cheaper than Claude Sonnet
> for the sentiment/reflection workload (short prompts, structured output).

---

## ❌ 明确不需要的

- ❌ **Anthropic / OpenAI / 任何非 Gemini LLM API key** — production agent
  exclusively uses Gemini (above); dev tooling uses Claude Code internal
  (`harness/claude_client.py`). Adding Anthropic/OpenAI SDK is a hard
  policy violation per `feedback_no_external_llm_api.md` memory.
- ❌ **自部署 Orbit L3 chain hosting** — Plan v3 决策不部署（v2 post-hackathon roadmap）
- ❌ **Polymarket 账号 + 真 USDC** — v3 不要求真下单
- ❌ **CI/CD service** — Public repo 用 GitHub Actions 免费层

---

## 注册时间总预估

| 优先级 | 服务 | 时间 |
|---|---|---|
| P0 | Robinhood Chain testnet | 10-30 min |
| P0 | Testnet 钱包 + 3 faucet drip | 15 min |
| P0 | Alchemy | 5 min |
| P0 | **Gemini AI Studio API key** | 3 min |
| **P0 小计** | | **~35-55 min** |
| P1 | Pinata | 5 min |
| P1 | Reddit PRAW | 3 min |
| **P1 小计** | | **~10 min** |
| P2 | Vercel | 5 min |
| P2 | Etherscan + Polygonscan API | 5 min |
| **P2 小计** | | **~10 min** |
| **总计** | | **~1 hr** |

---

## 操作顺序建议

1. **现在** (P0 关键路径): Robinhood Chain docs → 看到底能不能注册
   - 能 → 继续 P0 其他项
   - 不能 → 标记 Sepolia 为 primary（项目 already-supports）
2. **sprint_3 末** (T-B-002 跑之前): 完成 P0 + P1 — agent 跑 smart_money + reddit 要用
3. **sprint_5 之前**: 完成 P2 — demo 准备

---

## 跟项目代码的衔接

- 所有 env vars 已在 `.env.example` 列出（除 Reddit / Etherscan，新加的我会更新）
- Plan v3 的 3-chain 部署 script 是 sprint_3 task **T-A-005** 在做
- IPFS pinning 是 sprint_5 scope（v1 calibration → v2 phase1 → v3 dashboard → v4 IPFS → v5 demo）
- 你完成注册后只需更新本地 `.env`（gitignored，不会泄露）；下次 sprint launch 会自动消费

---

> 更新: 项目用 Claude Code orchestrator 跑，注册外部服务不影响开发节奏 —— 这些只是 sprint_4 起会实际调用的依赖。
