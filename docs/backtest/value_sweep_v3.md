# Value sweep v3 — the earnings-aligned re-seed (2026-06-12)

The chapter-3 seed selection, run AFTER realism rule #3 (side-correct payouts)
fixed the NO-side pricing artifact that had subsidized every earlier headline.

## Objective

Two-stage, earnings-aligned, survival-validated:

1. **Fast scorer** (`python -m agent.backtest.cached_sweep sweep --rows
   reports/backtest/_signal_rows.json --n 256 --seed 0 --min-bets 200
   --realism --value --rank pnl`): 256 LHS configs (10 dims — the 8 legacy
   dims + `min_edge` ∈ [0, 0.15] + `kappa` ∈ [0.05, 0.5]) scored over the
   4,902 post-floor rows as INDEPENDENT bets under the journey physics
   (entry floors 0.05 on the row AND the effective side, $100 profit cap,
   side-correct payouts, EV-gated value decisions). Ranked by **total net
   PnL**, double-gated on `bets ≥ 200` AND `t_stat = sharpe·√bets ≥ 2`
   (the edge must be statistically real, not just large). 38/256 configs
   passed the gate.
2. **Sequential survival validation** (`python -m
   agent.backtest.validate_value_seed --top 5`): the top 5 run the REAL
   multi-life season (`run_survival_export`, fragile 0.95, loss multiplier
   5.0, breath 35, max lives 12 — the exact journey knobs). Final ranking:
   finished-alive DESC, then season terminal PnL DESC.

## Fast top 10 (PnL-ranked, t-gated)

| # | sharpe | t | net PnL | win% | bets | min_edge | kappa | notes |
|---|-------:|----:|--------:|-----:|-----:|---------:|------:|-------|
| 1 | 0.371 | 12.9 | $1,448.89 | 79.1 | 1,213 | 0.020 | 0.40 | season $2,749.76 (11 lives/10 deaths) |
| 2 | 0.173 | 7.7 | $1,326.62 | 68.1 | 1,982 | 0.035 | 0.49 | **WINNER** — season $3,248.90 (10/9) |
| 3 | 0.194 | 8.3 | $1,028.96 | 72.7 | 1,812 | 0.013 | 0.41 | season $3,100.92 (10/9) |
| 4 | 0.078 | 4.2 | $984.79 | 60.1 | 2,879 | 0.009 | 0.47 | season $3,080.75 (10/9) |
| 5 | 0.185 | 7.3 | $927.78 | 73.2 | 1,552 | 0.028 | 0.45 | season $2,956.75 (11/10) |
| 6 | 0.078 | 3.9 | $877.00 | 58.7 | 2,480 | 0.005 | 0.48 | |
| 7 | 0.077 | 3.7 | $824.32 | 56.6 | 2,282 | 0.041 | 0.49 | |
| 8 | 0.088 | 4.2 | $797.19 | 62.9 | 2,273 | 0.018 | 0.44 | |
| 9 | 0.337 | 8.9 | $715.16 | 80.7 | 698 | 0.032 | 0.35 | |
| 10 | 0.143 | 5.5 | $671.38 | 72.2 | 1,473 | 0.012 | 0.45 | |

All five validated candidates finished the full universe ALIVE at the data
edge. The fast-rank-2 config won the season (sequence + learning + fragility
reorder the podium — exactly why the validation stage exists).

## The v3 seed (committed: `docs/backtest/value_seed_v3.json`)

```
w_r=0.584  alpha=[0.178, 0.070, 0.752]  beta=[0.768, 0.232]  rho=0.850
max_breath_risk_pct=0.381  min_confidence=0.076  min_bet_size_usd=4.0
min_edge=0.0349  kappa=0.4921
```

Fast: $1,326.62 over 1,982 bets, 68.1% win, t = 7.7.
Season: **$3,248.90**, 10 lives / 9 deaths, alive at the end,
**+$1,922.27 vs the frozen static baseline** under identical physics.

## The chapter-2 seed, rescored under the same physics (the honest contrast)

| decisions | net PnL | bets | win% | t-stat |
|---|--------:|-----:|-----:|------:|
| legacy (sign-of-signal) | **$5.52** | 66 | 81.8 | 0.3 |
| value (EV-gated) | −$8.88 | 97 | 86.6 | −0.5 |

The celebrated chapter-1/2 optimum (0.649 per-bet Sharpe, $853 summed PnL)
collapses to statistical ZERO once winners are paid at the leg they actually
bought. Its earnings were the NO-side payout artifact, not edge. The v3 sweep
found a genuinely different animal: a high-volume, h2h-weighted
(`beta_0=0.77`), value-gated config whose edge survives correct pricing at
t = 7.7.

## Honest limits (disclosed, not hidden)

- Entry is the cassette ledger's mid-point price with no spread, slippage or
  fees; `winning_price` is 1.0 on clean resolutions. The floor + cap are
  crude liquidity proxies (locked design).
- The fast scorer treats bets as independent at fixed bankroll; sequence
  effects are exactly what stage 2 validates.
- 4,902 markets, one sport, 2024–2026 — a single regime. No out-of-sample
  split; the LHS + t-gate guard against overfit but cannot eliminate it.

Raw sweep output: `reports/backtest/value_sweep_v3_raw.txt` +
`reports/backtest/value_seed_validation.txt` (local, gitignored).
