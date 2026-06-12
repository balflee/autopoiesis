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

Backtested over a **4,925-market tennis universe (2024–2026)** built from real Polymarket odds (point-in-time; no hindsight). The headline has been **audited and corrected three times, in public**:

1. **Run 1 → 2:** the headline was 62% lottery (two $5 bets at $0.0005-class longshots). Fix: entry-price floor ≥ 0.05 + per-bet profit cap $100, enforced as hard exporter invariants.
2. **Run 2 → 3:** the payout formula paid winning NO bets at the YES leg's odds (81x overpaid at yes-mid 0.10) — always-favorite's "+$8,451" became **−$661** under side-correct pricing, and ~80–90% of our own learner P&L rode the same artifact. Fix (**realism rule #3**): side-correct payouts, a side-aware floor on the *effective* entry price, EV-gated value betting (the decision engine finally sees the market price), an exporter that **recomputes every bet from first principles** before an artifact can be written, and an earnings-aligned re-sweep (t-stat ≥ 2 gated, survival-validated — [the full report](docs/backtest/value_sweep_v3.md)). The old celebrated seed rescored under correct physics: **$5.52** — statistically zero.

| Run | Physics | Learner P&L | Lives/Deaths | AI deltas applied |
|-----|---------|------------:|--------------|------------------:|
| v1 · Numerical | uncapped (superseded) | $11,879 | 7 / 6 | — |
| v1 · AI (MiniMax) | uncapped (superseded) | $17,469 | 6 / 5 | 130 |
| v2 · Numerical | floors+cap, YES-priced (superseded) | $1,668 | 8 / 7 | — |
| v2 · AI (MiniMax) | floors+cap, YES-priced (superseded) | $2,757 | 10 / 9 | 126 |
| v2 · AI (Gemini) | floors+cap, YES-priced (superseded) | $2,510 | 10 / 9 | 411 |
| v3 · Numerical | **side-correct · EV-gated** | **$3,249** | 10 / 9 | — |
| v3 · AI (MiniMax) | **side-correct · EV-gated** | **$2,273** | 12 / 11 | 142 |
| v3 · AI (Gemini) | **side-correct · EV-gated** | **$2,222** | 10 / 9 | 395 |

Under the corrected physics the honest ladder reads: always-favorite **−$661** < random +$206 < frozen value seed +$1,327 < **every learning agent ($2,222–$3,249, all +$896 to +$1,922 ahead of the frozen twin)**. An honest wrinkle we publish rather than hide: in the corrected world the deterministic EMA learner out-earned both LLM-driven runs — the LLMs applied 142/395 weight deltas and survived, but their wandering cost P&L vs the numeric baseline. Superseded numbers stay published — the audit trail IS the product. Toggle every run live on the [survival page](https://autopoiesis.draftlabs.org/survival).

## Phase 2 — the reincarnation experiment

The Phase-1 season has a confound: every life faces *different* markets, so "life 5 beat life 1" mixes learning with luck. Phase 2 removes it. The agent lives the **same 3,431-market training window three times** (the first 70% of the timeline, chronologically), carrying its experience across deaths — the 8 fusion-weight scalars, the EMA learner's 8 derived quality aggregates, and (in the Gemini variant) one sanitized strategy-level "rebirth retrospective" written by the strict advisor from a season-aggregate window — **never the market outcomes themselves** (the whole carried surface is ~20 scalars; it cannot store 3,431 results, and the artifact discloses the carried keyset per pass). Then learning is **frozen** and the carried weights walk into the held-out final 30% (1,471 unseen markets) — the only number allowed to claim generalization.

Measured (numerical / Gemini-rebirth): pass 1 **$2,751** → pass 2 **$2,881 / $2,879** → pass 3 flat (the policy converged — pass 3's decisions are identical to pass 2's; the weights still micro-drift but below any decision threshold). The frozen cold-start holdout: **+$423**, beating random (+$177) and always-favorite (−$161) **but NOT the untouched static seed (+$912)** — the same-season gains did not transfer to unseen markets better than the original seed. That is a finding about the EMA learner, published as measured: the agent re-learns its season quickly and converges, but what it accumulates in-season is partly season-shaped. The Gemini retrospectives ("w_r is zeroed out — reintroduce +0.05 to re-engage reference signals…") read sensible, moved the weights, and changed nothing the holdout could detect. Full data + honest notes: [/reincarnation](https://autopoiesis.draftlabs.org/reincarnation).

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

**backtest → survival → mock-bet → live.** Born from the best backtested seed (v3: an EV-gated, h2h-weighted config — $1,327 over 1,982 bets at t-stat 7.7 under side-correct physics; the original 0.649-Sharpe seed it replaced rescored to $5.52), hardened in the survival sandbox (current phase), paper-trading live odds next, real on-chain capital last.

## Provenance — built entirely inside the Buildathon

The project began **May 15, 2026**, inside the Arbitrum Open House London window. This repo was **re-initialized on Jun 11** to scrub dev-session logs that had leaked an API key (revoked; a gitleaks pre-commit hook now guards every commit), which erased the public commit history. The full development record survives:

- **[`PROVENANCE.md`](PROVENANCE.md)** — the sanitized log of all **480 commits across 22 active days** (May 15 – Jun 11), with daily activity counts and milestones.
- **On-chain anchors** — all 5 contracts deployed **May 25, 2026 02:19 UTC** ([Robinhood Chain block 60897767](https://explorer.testnet.chain.robinhood.com/block/60897767)); timestamps no one can edit.

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
