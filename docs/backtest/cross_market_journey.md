# Cross-Market κ_xm Journey — Three-Arm Backtest

Generated: 2026-06-14T22:30Z
Active rows: `(active)`
Placebo rows: `(placebo)`
LHS seeds: `[0, 1, 2, 3, 4]`  |  Placebo seeds: `[0, 1, 2]`
Walk-forward: `True`  |  Train fraction: `0.7`  |  n (LHS): `256`

---

## Honest Conclusion

**NO_GO — Layer 1 (GO edge CI) = INCONCLUSIVE; Layer 2 (survival gate) irrelevant.**

Rule: EDGE recorded only if BOTH Layer 1 (three_state_verdict == EDGE: CI excludes 0, positive sign, ≥min clusters/n) AND
Layer 2 (survival sign test p<0.05, majority of seeds beat baseline) pass.

---

## Layer 1 — GO Edge CI (path-independent cluster bootstrap)

Substrate: per-ROW PnL delta — TREATMENT scored on ACTIVE-test (real
signal) MINUS PLACEBO scored on PLACEBO-test (permuted signal), fast-
scorer only (**NOT** survival path-dependent PnL). Unmatched rows
(cluster_key == '') excluded. Both-NO_BET rows contribute delta=0.0 (kept).

Inference unit = the test cluster for a SINGLE selection (headline LHS seed `0`). The bootstrap is computed ONCE on that selection — the LHS-seed grid is a SEPARATE
descriptive replicate axis (NOT pooled into the bootstrap).

Pre-registered: GO_CI_SESOI = 0.0 (per-bet PnL-delta substrate); three_state_verdict(min_clusters=10, min_n=200).
A sub-min-cluster / sub-min-n CI reads INCONCLUSIVE, never EDGE.

| Metric | Value |
|--------|-------|
| n (matched test rows, headline seed — NOT × LHS seeds) | 523 |
| n_clusters | 14 |
| point estimate (mean delta) | -0.118673 |
| 95% cluster-bootstrap CI lo | -0.324143 |
| 95% cluster-bootstrap CI hi | 0.143315 |
| iid CI lo (sensitivity) | -0.374484 |
| iid CI hi (sensitivity) | 0.126120 |
| **Layer 1 verdict (EDGE / INCONCLUSIVE / REFUTED)** | **INCONCLUSIVE** |

### Selection-robustness readout (per-LHS-seed point estimates)

Descriptive ONLY — the LHS-seed grid is a separate replicate axis, NOT
pooled into the headline bootstrap above.

| LHS seed | mean per-row delta |
|----------|--------------------|
| 0 | -0.118673 |
| 1 | -0.023458 |
| 2 | -0.126211 |
| 3 | 0.151048 |
| 4 | -0.075321 |

Spread across seeds — min `-0.126211` | mean `-0.038523` | max `0.151048`.

---

---

## Layer 2 — Survival Descriptive Gate (per-seed sign test)

BASELINE = v3 seed (κ_xm=0), evaluated on the IDENTICAL held-out TEST
SurvivalRows the TREATMENT uses (same split, same fragile physics). Error bars from n=5 LHS seeds.
**NOT cluster-bootstrapped** (survival is path-dependent).
Caveat: a short test window may saturate finished-alive (fragile seeds
rarely die in ~30% of the season) — so Layer 2 is DESCRIPTIVE and
Layer 1 (the GO edge CI) is the inferential gate.

| LHS seed | T alive | T PnL | B alive | B PnL | T>B? |
|----------|---------|-------|---------|-------|------|
| 0 | Y | 119.91 | Y | 0.21 | YES |
| 1 | Y | 221.72 | Y | 0.21 | YES |
| 2 | Y | 220.08 | Y | 0.21 | YES |
| 3 | Y | 232.30 | Y | 0.21 | YES |
| 4 | Y | 71.74 | Y | 0.21 | YES |

Sign-test summary:

| Metric | Value |
|--------|-------|
| n seeds | 5 |
| T wins (T alive AND T PnL > B PnL) | 5 |
| T losses | 0 |
| one-sided p-value | 0.0312 |
| **Layer 2 verdict** | **GO** |

---

## Pre-registered verdict rule

Both layers must pass for an EDGE to be recorded:

| L1 (CI) | L2 (sign) | Conclusion |
|---------|-----------|------------|
| EDGE | GO | EDGE CONFIRMED |
| EDGE | NO_GO | NO_GO |
| NO_GO | GO | NO_GO |
| NO_GO | NO_GO | NO_GO |
