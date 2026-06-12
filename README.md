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

## Phase 2 — the reincarnation experiment (groundhog day)

**The rule:** one incarnation = one life, starting at market #1 of the same 3,431-market training window (the chronological first 70%). Die anywhere and you restart at market #1 — carrying your experience (the 8 fusion weights + the EMA learner's 8 quality aggregates + a strategy-level "rebirth retrospective" in the AI leg) but **never the outcomes, and never the money: a dead incarnation's profit is forfeit, scored $0**. Only a life that survives to the final bet keeps its earnings. The loop runs until someone makes it (or a cap). Then the frozen cold-start holdout (the unseen final 30%) checks generalization.

**Why this design is rigged against immortality — on purpose.** A code-and-data forensics pass established that the agent structurally *cannot* learn to avoid death: breath enters the decision engine only as a fixed sizing cap, the learning gradient contains zero breath/death terms (per-bet PnL credit only), and every survival-critical sizing knob is frozen outside the learnable surface. The physics: a $5 loss hits breath at 5×, wins return ~$4 — breath expectation ≈ **−1.2 per settled bet**; a full-season deathless life by luck is a ~0.2% lottery. So the **numerical leg is the control** (prediction: an eternal plateau) and the **Gemini leg is the treatment** — its strict advisor receives the death context (GOAL framing, where it died, its personal best, stake/win-rate aggregates — no market identities) with only the existing six weight keys as levers.

**Measured (cap 20, after a 120-cap pilot showed 67/48 consecutive identical deaths and was cut short):** the control died at settled bet **759** in life 1 (forfeiting $2,218), then **826 — nineteen times in a row, to the cent** ($2,474 forfeited each death; best depth 52.2% of the season). The treatment: 19/19 death reviews (10 productive, 9 empty-or-failed under provider rate limits — disclosed), 24 weight deltas applied — deaths moved by *one settled bet* (825/826) and a few dollars, never the death point. **Both legs: survived = false, headline = $0.** The ±0.1 deltas drown in a dead zone created by the uniform $5 liquidity cap (desired stakes ~$13+ pin at the cap, so rho-shrink advice changes nothing observable for ~8 consecutive pushes). Holdout, learning frozen: **+$423** — beats random (+$177) and always-favorite (−$161), loses to the untouched seed (+$912).

**The prayer log.** After every death the agent may state one dying wish to the gods (recorded, never granted mid-experiment, never carried into the next life — information-flow enforced by test). What it prayed for, verbatim-class: *"a real-time risk-of-ruin calculation that dynamically scales stake sizes based on remaining breath"*, *"skip low-confidence bets based on current breath levels"*, *"access to the true win probability before betting"*, and — boldly — *"a breath-recovery mechanism where consecutive wins regenerate breath"* (it asked the gods to change physics). The agent's own wishes match the designers' roadmap (breath-aware sizing, selectivity unlocks, sharper probability sources) item for item; the prayer log now drives what it gets next.

**The tribute mechanism (the gods have an agenda).** The death anatomy exposed a cruel irony: the agent dies HOLDING ~$2,470 — cash it can't keep (forfeit on death) and can't spend on the one thing it needs (breath). So the world gained a market: a dying agent may OFFER money to the gods for a fresh lungful — minimum $500 (~30% grant), rising to ~99% at $2,000, the offering kept win or lose. **Measured (control leg, scripted reflex disclosed as such):** every incarnation bought 1-2 revivals and STILL died — because death arrives precisely when your in-flight loss pipeline is at maximum pressure, and 35 breath survives ~1.4 of those losses. The reflex paid itself broke twenty times: **the gods collected $50,719 while the agent scored $0 across 20 incarnations.** Deathbed revival into your own pending losses is a money pit; the open treatment question is whether an LLM at the deathbed (it sees its bank, its position, the price list) figures out when to refuse — that leg is queued behind a provider quota reset (the preflight gate aborted it rather than publish a degraded run, working as designed) and will be published as measured. Every altar event, the gods' total take, and the full accounting (fail-closed in both the exporter and the page validator) are on [/reincarnation](https://autopoiesis.draftlabs.org/reincarnation).

*Design history:* v2 supersedes the first 3-pass design (a "pass" quietly contained 7 mid-season respawned lives — the user's correction: death must send you back to bet #1, and dead lives keep nothing). The 3-pass numbers stay on the record: pass 1 $2,751 → pass 2 $2,881 → pass 3 behaviorally converged flat; same holdout. Full data + honest notes: [/reincarnation](https://autopoiesis.draftlabs.org/reincarnation).

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
