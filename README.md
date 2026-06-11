# Autopoiesis

> An autonomous on-chain AI agent that learns to survive — by dying.

**Live dashboard:** [autopoiesis.draftlabs.org](https://autopoiesis.draftlabs.org) · **Paper trail (contracts, runs, provenance):** [/docs](https://autopoiesis.draftlabs.org/docs)

Built for the **Arbitrum Open House London** buildathon. Deployed on **Robinhood Chain testnet** (an Arbitrum Orbit L2) and **Arbitrum Sepolia**.

---

## What it is

*Autopoiesis* (Greek: self-creation) is an AI agent that bets on Polymarket tennis markets under one rule: a life meter called **BREATH**. Settlement losses drain it; wins refresh it. When BREATH hits zero the agent suffers **permadeath** — a **Tombstone NFT** marks the death on-chain — and it respawns with a fresh bankroll **but keeps the strategy weights it learned**. Survival, not profit, is the objective: it learns to stay alive *across deaths*.

After every life, an **LLM reflection loop** (Gemini 3.1 Flash Lite, MiniMax fallback) reviews what killed the agent and a **StrategyAdvisor** proposes weight deltas the agent applies to its own decision engine. The strategy self-evolves and gets progressively harder to kill.

## How it decides

Every market is read by five independent signal engines, fused by a tunable 2-layer decision engine under four risk constraints (max-breath-risk, min-confidence, min-bet, liquidity cap):

| # | Engine | Real payload |
|---|--------|--------------|
| 1 | Market Momentum | live CLOB order-book price drift |
| 2 | ELO / Ranking | pre-match favorite strength (Sackmann) |
| 3 | Surface Form | clay / grass / hard win-rates (Sackmann) |
| 4 | Head-to-Head | historical matchup record (Sackmann) |
| 5 | Rest & Recency | days since last match (Sackmann) |

> **Honest note:** three engine slots keep legacy code keys from an earlier prediction-markets prototype (`smart_money`, `sentiment_llm`, `crowd_volume`) — there is **no** order-flow, social-sentiment, or betting-volume data behind them. Each computes the real tennis feature listed above.

## Results — every run on the record

Backtested over a **4,925-market tennis universe (2024–2026)** built from real Polymarket odds (point-in-time; no hindsight). After run 1 we audited our own headline, found it 62% lottery (two $5 bets hitting $0.0005-class longshots), and introduced **realism rules**: entry-price floor ≥ 0.05 and a per-bet profit cap of $100, enforced as hard invariants in the exporter. The pre-rules runs stay published — that's the point.

| Run | Rules | Learner P&L | Lives/Deaths | AI deltas applied |
|-----|-------|------------:|--------------|------------------:|
| v1 · Numerical | pre-rules · uncapped | $11,879 | 7 / 6 | — |
| v1 · AI (MiniMax) | pre-rules · uncapped | $17,469 | 6 / 5 | 130 |
| v2 · Numerical | floor 0.05 · cap $100 | $1,668 | 8 / 7 | — |
| v2 · AI (MiniMax) | floor 0.05 · cap $100 | **$2,757** | 10 / 9 | 126 |
| v2 · AI (Gemini) | floor 0.05 · cap $100 | **$2,510** | 10 / 9 | 411 |

Under identical physics, both self-learning agents out-earned the frozen static seed ($874). Toggle every run live on the [survival page](https://autopoiesis.draftlabs.org/survival).

## Deployed contracts

Identical addresses on **Robinhood Chain testnet** (chainId 46630) and **Arbitrum Sepolia** (chainId 421614):

| Contract | Address | Role |
|----------|---------|------|
| TombstoneNFT | `0xDE6178D892AA9F80f748a399f07B588b08Faec2f` | permadeath monument, fully on-chain SVG tokenURI |
| AgentLifecycle | `0x125929f6451e5e5Fa9C64b498646793CaF5b4128` | birth/death state machine; sole writer of DecisionLog + Tombstone mints |
| DecisionLog | `0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818` | append-only decision log |
| EnergyController | `0xeb504449195b0491F52b455650056f0763A54525` | BREATH (life-meter) accounting |
| PhaseManager | `0x20e07db0169E35553a66608736161f433d8E44E0` | lifecycle phase gates + role-renunciation events |

> **Why zero Tombstone mints (so far):** the dashboard's deaths happen in the backtest/survival simulation, which replays 4,925 historical markets in minutes against an in-memory chain adapter — simulated deaths don't mint. The mint path (`kill_and_mint_tombstone`, web3.py) is wired into the live runtime; the first real Tombstone is minted the first time the agent dies in a live phase (mock-bet onward). Deployed, wired, waiting for its first death.

Full manifest with ABI hashes: [`submission/SUBMISSION.md`](submission/SUBMISSION.md).

## Lifecycle

**backtest → survival → mock-bet → live.** Born from the best backtested seed (0.649 per-bet Sharpe, 81.5% win rate over 65 selective bets), hardened in the survival sandbox (current phase), paper-trading live odds next, real on-chain capital last.

## Repo layout

```
contracts/   Solidity (Foundry): TombstoneNFT, AgentLifecycle, DecisionLog,
             EnergyController, PhaseManager
agent/       Python runtime: engines, 2-layer decision fusion, L5 survival
             loop, L6 reflection/advisor, backtest + survival exporter
dashboard/   Next.js site (roadmap · mechanism · backtest · survival · mock · docs)
data/        Polymarket cassette ETL + Sackmann tennis corpus loaders
script/      Foundry deploy scripts
submission/  Submission manifest (addresses, ABI hashes)
tests/       Python test suites · dashboard/__tests__/ for the site
```

## Quickstart

```bash
# Python agent + backtest suites
python -m pytest tests/ -q

# Solidity
forge test

# Dashboard
cd dashboard && npm install && npm test && npm run dev
```

LLM keys (`GEMINI_API_KEY`, optional `MINIMAX_API_KEY`) go in `.env` — never committed; a gitleaks pre-commit hook guards the repo (`.githooks/README.md`).

## Stack

Robinhood Chain (Arbitrum Orbit L2) · Solidity + Foundry · Python (web3.py) · Gemini + MiniMax · Polymarket gamma/CLOB APIs · Sackmann tennis data · Next.js on Vercel
