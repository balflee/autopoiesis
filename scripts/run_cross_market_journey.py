"""scripts/run_cross_market_journey.py — THREE-ARM cross-market journey driver.

Implements the TWO-LAYER statistical verdict from the plan (r4/r5/r6):

  LAYER 1 — GO edge CI (path-INDEPENDENT inferential gate)
    The inference unit is the TEST CLUSTER for a SINGLE selection.  On the
    HEADLINE LHS seed (``lhs_seeds[0]``), TREATMENT (``select_winner`` on the
    ACTIVE rows, walk-forward OOS) is scored on ACTIVE-test rows (real signal)
    and each PLACEBO (``select_winner`` on the placebo rows) is scored on
    PLACEBO-test rows (permuted signal) via ``score_config`` (fast-scorer, per-
    ROW aligned, NO path-dependent survival PnL).  The per-row PnL delta
    (treatment − mean placebo) is cluster-bootstrapped ONCE over ``iso_week``
    clusters → ``go_ci.n`` == matched test rows (NOT × LHS seeds).  The LHS-seed
    grid is a SEPARATE descriptive selection-robustness readout (per-seed point
    estimates).  The verdict routes through ``three_state_verdict`` with the
    pre-registered ``GO_CI_SESOI=0.0`` + ``min_clusters=10``/``min_n=200`` guards
    → EDGE / INCONCLUSIVE / REFUTED (a sub-floor CI reads INCONCLUSIVE).

  LAYER 2 — Survival descriptive gate
    BASELINE = v3 seed (κ_xm=0) over the IDENTICAL held-out TEST SurvivalRows the
    TREATMENT uses (same split + fragile physics — the honest "before").
    Gate = TREATMENT finished-alive + terminal PnL > BASELINE, with error bars
    from a sign test over the LHS-seed grid.  A short test window may saturate
    finished-alive, so Layer 2 is DESCRIPTIVE and Layer 1 is the inferential gate.

  Output → ``docs/backtest/cross_market_journey.md``

Usage (step 9 — run on a terminal with the augmented rows, NOT in Claude)::

    python scripts/run_cross_market_journey.py \\
        --active  reports/backtest/_signal_rows_v4.json \\
        --placebo reports/backtest/_signal_rows_v4_placebo.json \\
        --lhs-seeds 0,1,2,3,4 \\
        --placebo-seeds 0,1,2 \\
        --n 256 --walk-forward \\
        --out docs/backtest/cross_market_journey.md

Unit tests (offline, TDD composition) cover wiring + verdict logic but do NOT
run the heavy 4925-row experiment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allow ``python scripts/run_cross_market_journey.py`` from the repo root
# (scripts/ is outside the agent package).
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from agent.backtest.cached_sweep import (
    SignalRow,
    load_rows,
    score_config,
)
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    load_all_cached_markets,
)
from agent.backtest.sharp_line import (
    BootstrapCI,
    cluster_bootstrap_ci,
    three_state_verdict,
)
from agent.backtest.survival_season import (
    _build_corpus_resolver,
    build_survival_rows,
    run_survival_over_rows,
)
from agent.backtest.tennis_match_resolver import TennisMatchResolver
from agent.backtest.validate_value_seed import (
    _ENTRY_PRICE_FLOOR,
    _JOURNEY_KNOBS,
    _SEED_OUT,
    select_winner,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_ACTIVE = Path("reports/backtest/_signal_rows_v4.json")
_DEFAULT_PLACEBO = Path("reports/backtest/_signal_rows_v4_placebo.json")
_DEFAULT_CACHE_DIR = Path("agent/backtest/_cache_tennis")
_DEFAULT_OUT = Path("docs/backtest/cross_market_journey.md")
_DEFAULT_LHS_SEEDS = [0, 1, 2, 3, 4]
_DEFAULT_PLACEBO_SEEDS = [0, 1, 2]
_DEFAULT_N = 256
_N_BOOT = 1000
_CI_LEVEL = 0.95

# ---------------------------------------------------------------------------
# PRE-REGISTERED Layer-1 verdict parameters (FIX C — locked, not post-hoc).
#
# The GO gate is "the cluster-bootstrap CI excludes 0 AND the sign is positive",
# guarded by three_state_verdict's min-cluster / min-n floors.  The SESOI is
# intentionally 0.0 for the per-bet PnL-DELTA substrate: a per-bet PnL delta is
# already a real-money quantity, so any CI strictly above 0 is a meaningful
# edge.  We do NOT reuse A18's Brier SESOI (a different substrate) and we do NOT
# pick a positive PnL SESOI post-hoc (that would be a researcher-degrees-of-
# freedom leak).  three_state_verdict then reads:
#   * CI.lo > 0          -> EDGE
#   * CI.hi < GO_CI_SESOI -> REFUTED  (with SESOI=0 this is CI.hi < 0)
#   * else               -> INCONCLUSIVE
# and a sub-min_clusters / sub-min_n CI reads INCONCLUSIVE (never EDGE).
GO_CI_SESOI = 0.0
_GO_CI_MIN_CLUSTERS = 10
_GO_CI_MIN_N = 200  # three_state_verdict's default min_n


def layer1_verdict(go_ci: BootstrapCI) -> tuple[str, bool]:
    """Map the GO-CI to a three-state verdict + the layer1_pass boolean.

    Routes through :func:`~agent.backtest.sharp_line.three_state_verdict` with
    the PRE-REGISTERED :data:`GO_CI_SESOI` and the min-cluster / min-n guards,
    so a too-few-cluster or too-small-n CI reads INCONCLUSIVE (NOT EDGE).

    Returns ``(verdict, layer1_pass)`` where ``verdict`` is one of
    ``"EDGE"`` / ``"INCONCLUSIVE"`` / ``"REFUTED"`` and ``layer1_pass`` is True
    iff ``verdict == "EDGE"``.
    """
    verdict = three_state_verdict(
        go_ci,
        sesoi=GO_CI_SESOI,
        min_n=_GO_CI_MIN_N,
        min_clusters=_GO_CI_MIN_CLUSTERS,
    )
    return verdict, verdict == "EDGE"

# Realism-v3 knobs matching validate_value_seed and the plan:
_SCORE_KW: dict[str, Any] = {
    "entry_price_floor": _ENTRY_PRICE_FLOOR,
    "effective_entry_price_floor": _ENTRY_PRICE_FLOOR,
    "max_pnl_usd": 100.0,
    "side_correct_pricing": True,
    "value_betting": True,
}


# ---------------------------------------------------------------------------
# Layer-1 helpers: per-row delta assembly + cluster bootstrap
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerRowResult:
    """Aligned per-row scoring result for one arm."""

    pnls: list[float]
    """Per-ROW PnL vector (0.0 for NO_BET or effective-floor-skipped rows),
    length == number of rows that survive the entry_price_floor pre-filter."""
    cluster_keys: list[str]
    """Corresponding cluster_key values (one per row, same ordering)."""


def score_arm_per_row(
    test_signal_rows: list[SignalRow],
    cfg: StrategyConfig,
    *,
    score_kw: dict[str, Any] | None = None,
) -> PerRowResult:
    """Score one arm on the TEST signal rows, emitting a per-ROW aligned vector.

    Returns a :class:`PerRowResult` where ``pnls[i]`` and ``cluster_keys[i]``
    correspond to the same row in the ``entry_price_floor``-filtered set.
    ``pnls[i] == 0.0`` for NO_BET rows and effective-floor-skipped rows.
    """
    kw = dict(_SCORE_KW) if score_kw is None else dict(score_kw)
    kw["emit_per_row"] = True
    _metrics, per_row = asyncio.run(score_config(test_signal_rows, cfg, **kw))
    pnls = [p for p, _k in per_row]
    cluster_keys = [k for _p, k in per_row]
    return PerRowResult(pnls=pnls, cluster_keys=cluster_keys)


def compute_go_delta(
    result_t: PerRowResult,
    result_p: PerRowResult,
) -> tuple[list[float], list[str]]:
    """Compute per-row delta (treatment − placebo), dropping unmatched rows.

    TREATMENT and PLACEBO must be scored on the IDENTICAL test SignalRows
    (same row order, same entry_price_floor filter) so the per-row vectors are
    element-wise pairable by index (placebo only permutes cross_market_signal;
    the floor/split keys are config-independent).

    Returns ``(deltas, cluster_keys)`` with unmatched rows (cluster_key == "")
    excluded from the GO-CI substrate (per plan r6 MED-1 + MED-2):

    * both-NO_BET rows contribute ``delta = 0.0`` (kept — matched test rows).
    * unmatched rows (``cluster_key == ""``) are dropped to avoid a single
      ``|nodate`` super-cluster dominating the CI and to respect
      ``three_state_verdict``'s ``min_clusters=10`` gate.

    Raises ``ValueError`` if the vectors have different lengths (strict 1:1
    contract enforced by ``score_config``'s per-row emit).
    """
    if len(result_t.pnls) != len(result_p.pnls):
        raise ValueError(
            f"TREATMENT ({len(result_t.pnls)}) and PLACEBO ({len(result_p.pnls)}) "
            "per-row vectors have different lengths — test partitions do not agree."
        )
    deltas: list[float] = []
    cluster_keys: list[str] = []
    for pnl_t, pnl_p, ck_t, ck_p in zip(
        result_t.pnls, result_p.pnls, result_t.cluster_keys, result_p.cluster_keys,
        strict=True,
    ):
        # Both sides must agree on the cluster_key (sanity: placebo does not
        # change the cluster assignment, only the cross_market_signal value).
        if ck_t != ck_p:
            raise ValueError(
                f"cluster_key mismatch between arms: T={ck_t!r} P={ck_p!r}"
            )
        if ck_t == "":
            continue  # unmatched — exclude from GO-CI (per plan r6 MED-1)
        deltas.append(pnl_t - pnl_p)
        cluster_keys.append(ck_t)
    return deltas, cluster_keys


# ---------------------------------------------------------------------------
# Test-partition accessor (shared between select_winner and the driver)
# ---------------------------------------------------------------------------


def get_eval_survival_rows(
    rows: list[SignalRow],
    snapshots: list[MarketSnapshot],
    *,
    resolver: TennisMatchResolver,
    walk_forward: bool = True,
    entry_price_floor: float = _ENTRY_PRICE_FLOOR,
    train_fraction: float = 0.7,
) -> list[Any]:
    """Return the held-out EVAL SurvivalRows using the SAME split that
    ``select_winner`` uses internally (FIX D — shared split helper).

    * ``walk_forward=True`` → the held-out TEST window (the later
      ``1 - train_fraction`` of the post-floor, chronologically-sorted rows).
    * ``walk_forward=False`` → the FULL post-floor universe (v3 in-sample
      parity — ``select_winner`` evaluates on the full universe in that mode).

    The split is post-floor and config-independent (the entry_price_floor
    pre-filter runs before the chronological split, exactly as in
    ``select_winner``), so the BASELINE provably evaluates over the IDENTICAL
    row set the TREATMENT does — no apples-to-oranges row-count bias.
    """
    from agent.backtest.reincarnation import split_rows_by_time

    survival_all = build_survival_rows(
        rows, snapshots, resolver, entry_price_floor=entry_price_floor
    )
    if not walk_forward:
        return survival_all
    _train_rows, test_survival = split_rows_by_time(
        survival_all, train_fraction=train_fraction
    )
    return test_survival


def get_test_signal_rows(
    active_rows: list[SignalRow],
    snapshots: list[MarketSnapshot],
    *,
    resolver: TennisMatchResolver,
    entry_price_floor: float = _ENTRY_PRICE_FLOOR,
    train_fraction: float = 0.7,
) -> list[SignalRow]:
    """Return the held-out TEST SignalRows using the SAME split that
    ``select_winner(walk_forward=True)`` uses internally.

    This ensures TREATMENT and PLACEBO share the IDENTICAL test partition
    (the split is post-floor and config-independent — the entry_price_floor
    pre-filter is applied before the chronological split, exactly as in
    ``select_winner``).

    The returned list is the ``[r.signal for r in test_survival_rows]`` mapping
    — the same SignalRows the per-row scorer consumes.
    """
    test_survival = get_eval_survival_rows(
        active_rows,
        snapshots,
        resolver=resolver,
        walk_forward=True,
        entry_price_floor=entry_price_floor,
        train_fraction=train_fraction,
    )
    return [r.signal for r in test_survival]


# ---------------------------------------------------------------------------
# Layer-2 helpers: survival gate + sign test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedTriple:
    """Per-LHS-seed arm results for the survival gate."""

    lhs_seed: int
    # TREATMENT
    treatment_alive: bool
    treatment_pnl: float
    # BASELINE (v3 seed, κ_xm=0)
    baseline_alive: bool
    baseline_pnl: float
    # PLACEBO (averaged across placebo seeds)
    placebo_alive_rate: float  # fraction of placebo seeds that finish alive
    placebo_pnl_mean: float  # mean terminal PnL across placebo seeds


def treatment_beats_baseline(triple: SeedTriple) -> bool:
    """True iff the TREATMENT arm passes the survival gate for this seed.

    Both conditions must hold:
    1. TREATMENT finished-alive (``treatment_alive``).
    2. TREATMENT terminal PnL > BASELINE terminal PnL.
    """
    return triple.treatment_alive and (triple.treatment_pnl > triple.baseline_pnl)


def sign_test_over_seeds(triples: list[SeedTriple]) -> dict[str, Any]:
    """Sign-test summary: how many seeds have TREATMENT > BASELINE.

    Returns a dict with ``n_seeds``, ``n_treatment_wins``,
    ``n_treatment_losses``, ``sign_test_pvalue`` (one-sided binomial),
    and ``verdict`` (``"GO"`` if strictly more than half win and p-value <0.05,
    else ``"NO_GO"``).
    """
    from math import comb

    n = len(triples)
    wins = sum(1 for t in triples if treatment_beats_baseline(t))
    losses = n - wins

    # One-sided sign test: P(X >= wins | p=0.5) = sum_{k=wins}^{n} C(n,k)/2^n
    # Under H0: treatment wins with p=0.5
    pvalue = sum(comb(n, k) for k in range(wins, n + 1)) / (2**n) if n > 0 else 1.0
    verdict = "GO" if (wins > losses and pvalue < 0.05) else "NO_GO"
    return {
        "n_seeds": n,
        "n_treatment_wins": wins,
        "n_treatment_losses": losses,
        "sign_test_pvalue": round(pvalue, 4),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Core experiment runner: one LHS seed's triple (TREATMENT + BASELINE + PLACEBO)
# ---------------------------------------------------------------------------


def run_one_seed(
    lhs_seed: int,
    active_rows: list[SignalRow],
    placebo_rows_by_seed: dict[int, list[SignalRow]],
    snapshots: list[MarketSnapshot],
    resolver: TennisMatchResolver,
    *,
    n: int = _DEFAULT_N,
    walk_forward: bool = True,
    train_fraction: float = 0.7,
    verbose: bool = True,
) -> SeedTriple:
    """Run the three-arm experiment for one LHS seed.

    TREATMENT: ``select_winner`` on ``active_rows`` (winner evaluated on the
               held-out TEST window when ``walk_forward=True``).
    BASELINE: v3 seed (κ_xm=0, ``docs/backtest/value_seed_v3.json``) run via
              ``run_survival_over_rows`` over the IDENTICAL held-out TEST
              SurvivalRows the TREATMENT uses (FIX D — same split, same
              fragile physics).  This keeps the comparison apples-to-apples;
              the old code ran the baseline over the FULL active universe,
              biasing ``treatment_pnl > baseline_pnl`` by row count.
    PLACEBO: ``select_winner`` on each set of placebo rows, averaged.

    NOTE (Layer-2 caveat): a short test window may saturate finished-alive —
    fragile seeds rarely die in only ~30% of the season — so Layer 2 is
    DESCRIPTIVE and Layer 1 (the GO edge CI) is the inferential gate.

    Returns the :class:`SeedTriple` for this seed (survival gate only — the
    GO-CI layer is computed separately by :func:`compute_go_ci_for_seeds`).
    """
    if verbose:
        print(f"\n[seed {lhs_seed}] TREATMENT selecting winner …", flush=True)

    # TREATMENT — select_winner evaluates the winner on the held-out TEST window
    # (walk_forward) via the run-half (same fragile physics as the baseline).
    _cfg_t, summary_t = select_winner(
        active_rows,
        snapshots,
        lhs_seed,
        walk_forward=walk_forward,
        resolver=resolver,
        n=n,
        train_fraction=train_fraction,
        verbose=verbose,
    )
    treatment_alive = summary_t["deaths"] < summary_t["lives"]
    treatment_pnl = summary_t["learner_final_pnl"]

    # BASELINE: v3 seed (κ_xm=0) over the SAME held-out TEST SurvivalRows the
    # TREATMENT was evaluated on (FIX D — shared split helper, same physics).
    v3_seed = _load_v3_seed()
    if verbose:
        print(
            f"[seed {lhs_seed}] BASELINE (v3 seed, κ_xm=0) on the TEST partition …",
            flush=True,
        )
    eval_survival = get_eval_survival_rows(
        active_rows,
        snapshots,
        resolver=resolver,
        walk_forward=walk_forward,
        entry_price_floor=_ENTRY_PRICE_FLOOR,
        train_fraction=train_fraction,
    )
    baseline_journey = run_survival_over_rows(
        eval_survival, snapshots, base_seed=v3_seed, **_JOURNEY_KNOBS
    )
    baseline_summary = baseline_journey["summary"]
    baseline_alive = baseline_summary["deaths"] < baseline_summary["lives"]
    baseline_pnl = baseline_summary["learner_final_pnl"]

    # PLACEBO: average across placebo seeds
    placebo_alives: list[bool] = []
    placebo_pnls: list[float] = []
    for p_seed, p_rows in placebo_rows_by_seed.items():
        if verbose:
            print(
                f"[seed {lhs_seed}] PLACEBO (placebo_seed={p_seed}) selecting …",
                flush=True,
            )
        _cfg_p, summary_p = select_winner(
            p_rows,
            snapshots,
            lhs_seed,
            walk_forward=walk_forward,
            resolver=resolver,
            n=n,
            train_fraction=train_fraction,
            verbose=verbose,
        )
        placebo_alives.append(summary_p["deaths"] < summary_p["lives"])
        placebo_pnls.append(summary_p["learner_final_pnl"])

    n_p = len(placebo_pnls)
    placebo_alive_rate = sum(placebo_alives) / n_p if n_p else 0.0
    placebo_pnl_mean = sum(placebo_pnls) / n_p if n_p else 0.0

    return SeedTriple(
        lhs_seed=lhs_seed,
        treatment_alive=treatment_alive,
        treatment_pnl=treatment_pnl,
        baseline_alive=baseline_alive,
        baseline_pnl=baseline_pnl,
        placebo_alive_rate=placebo_alive_rate,
        placebo_pnl_mean=placebo_pnl_mean,
    )


# ---------------------------------------------------------------------------
# GO-CI computation (FIX A + FIX B)
#
#   FIX A — the GO-CI INFERENCE UNIT is the TEST CLUSTER for a SINGLE selection.
#           Compute the bootstrap ONCE on the HEADLINE LHS seed (lhs_seeds[0]):
#           one set of per-row deltas, one cluster_bootstrap_ci → go_ci.n equals
#           the number of matched test rows (NOT × the number of LHS seeds).
#           The LHS-seed grid is a SEPARATE descriptive replicate axis reported
#           as a "selection-robustness" readout (per-seed point estimates, with
#           their min/max/mean spread) — NOT fed into the bootstrap.
#
#   FIX B — TREATMENT is scored on ACTIVE-test rows (real signal); PLACEBO is
#           scored on PLACEBO-test rows (permuted signal).  Because
#           make_placebo_rows preserves row order + the floor/split keys (which
#           key on entry_price, signal-independent), placebo-test is the SAME
#           physical rows in the SAME order as active-test — only the
#           cross_market_signal column is permuted.  So per-row pairing by index
#           still holds and the delta isolates GENUINE signal value.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoCiResult:
    """Layer-1 GO edge CI (headline seed) + selection-robustness readout."""

    ci: BootstrapCI
    """The single cluster-bootstrap CI on the HEADLINE LHS seed's per-row
    deltas (``ci.n`` == matched test rows, NOT × seeds)."""
    headline_seed: int
    """The LHS seed the CI was computed on (``lhs_seeds[0]``)."""
    per_seed_point: dict[int, float]
    """Per-LHS-seed point estimate (mean per-row delta) — the descriptive
    selection-robustness replicate axis (NOT bootstrap input)."""
    point_min: float
    point_max: float
    point_mean: float


def _seed_mean_delta(
    lhs_seed: int,
    active_rows: list[SignalRow],
    placebo_rows_by_seed: dict[int, list[SignalRow]],
    snapshots: list[MarketSnapshot],
    resolver: TennisMatchResolver,
    *,
    n: int,
    walk_forward: bool,
    train_fraction: float,
    verbose: bool,
) -> tuple[list[float], list[str]]:
    """Compute the per-ROW mean delta (treatment − mean placebo) for ONE LHS seed.

    TREATMENT is scored on ACTIVE-test rows (real signal); each PLACEBO seed is
    scored on its own PLACEBO-test rows (permuted signal) — the SAME physical
    rows / order as active-test, so index-pairing holds.  The per-row delta is
    averaged across placebo seeds, then unmatched rows are dropped.

    Returns ``(mean_deltas, cluster_keys)`` for this seed (the bootstrap
    substrate for a single selection).
    """
    # TREATMENT: active-test rows (real signal) + winner selected on active rows.
    active_test = get_test_signal_rows(
        active_rows,
        snapshots,
        resolver=resolver,
        entry_price_floor=_ENTRY_PRICE_FLOOR,
        train_fraction=train_fraction,
    )
    cfg_t, _ = select_winner(
        active_rows,
        snapshots,
        lhs_seed,
        walk_forward=walk_forward,
        resolver=resolver,
        n=n,
        train_fraction=train_fraction,
        verbose=verbose,
    )
    result_t = score_arm_per_row(active_test, cfg_t)

    seed_deltas: list[list[float]] = []
    ck_list: list[str] = []
    for p_seed, p_rows in placebo_rows_by_seed.items():
        if verbose:
            print(
                f"[GO-CI] lhs_seed={lhs_seed} placebo_seed={p_seed} "
                "(placebo scored on PLACEBO-test) …",
                flush=True,
            )
        # FIX B: PLACEBO is scored on PLACEBO-test rows (permuted signal),
        # NOT active-test.  Same physical rows/order (floor/split keys on
        # entry_price are signal-independent), so per-row pairing holds.
        placebo_test = get_test_signal_rows(
            p_rows,
            snapshots,
            resolver=resolver,
            entry_price_floor=_ENTRY_PRICE_FLOOR,
            train_fraction=train_fraction,
        )
        cfg_p, _ = select_winner(
            p_rows,
            snapshots,
            lhs_seed,
            walk_forward=walk_forward,
            resolver=resolver,
            n=n,
            train_fraction=train_fraction,
            verbose=verbose,
        )
        result_p = score_arm_per_row(placebo_test, cfg_p)
        deltas, ck_list = compute_go_delta(result_t, result_p)
        seed_deltas.append(deltas)

    if not seed_deltas:
        return [], []

    n_rows = len(seed_deltas[0])
    if not all(len(d) == n_rows for d in seed_deltas):
        raise ValueError(
            "Per-row delta vectors differ in length across placebo seeds"
        )
    mean_deltas = [
        sum(seed_deltas[k][i] for k in range(len(seed_deltas))) / len(seed_deltas)
        for i in range(n_rows)
    ]
    return mean_deltas, ck_list


def compute_go_ci_for_seeds(
    lhs_seeds: list[int],
    active_rows: list[SignalRow],
    placebo_rows_by_seed: dict[int, list[SignalRow]],
    snapshots: list[MarketSnapshot],
    resolver: TennisMatchResolver,
    *,
    n: int = _DEFAULT_N,
    walk_forward: bool = True,
    train_fraction: float = 0.7,
    n_boot: int = _N_BOOT,
    verbose: bool = True,
) -> GoCiResult:
    """Layer-1 GO edge CI (FIX A + FIX B).

    The GO-CI inference unit is the TEST CLUSTER for a SINGLE selection, so the
    cluster bootstrap is computed ONCE on the HEADLINE LHS seed
    (``lhs_seeds[0]``): one set of per-row (treatment − placebo) deltas, one
    ``cluster_bootstrap_ci``.  ``ci.n`` == the number of matched test rows
    (NOT × the number of LHS seeds — pooling across seeds would be pseudo-
    replication and falsely narrow the CI).

    The full LHS-seed grid is reported as a SEPARATE descriptive "selection-
    robustness" readout: each seed's per-row delta POINT estimate (mean delta)
    and the spread across seeds (min/max/mean).  Those per-seed point estimates
    are NOT fed into the bootstrap.

    HARD INVARIANT: NEVER feed ``SurvivalStep.pnl_usd`` (path-dependent) into
    the bootstrap — only the path-independent fast-scorer per-row PnL.
    """
    if not lhs_seeds:
        raise ValueError("lhs_seeds must be non-empty")

    headline_seed = lhs_seeds[0]

    # --- Selection-robustness readout: per-seed point estimate (descriptive) ---
    per_seed_point: dict[int, float] = {}
    headline_deltas: list[float] = []
    headline_cluster_keys: list[str] = []
    for lhs_seed in lhs_seeds:
        if verbose:
            print(
                f"\n[GO-CI] lhs_seed={lhs_seed}: per-row mean delta "
                f"(headline={lhs_seed == headline_seed}) …",
                flush=True,
            )
        mean_deltas, ck_list = _seed_mean_delta(
            lhs_seed,
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=n,
            walk_forward=walk_forward,
            train_fraction=train_fraction,
            verbose=verbose,
        )
        per_seed_point[lhs_seed] = (
            sum(mean_deltas) / len(mean_deltas) if mean_deltas else float("nan")
        )
        if lhs_seed == headline_seed:
            headline_deltas = mean_deltas
            headline_cluster_keys = ck_list

    # --- Inferential gate: ONE bootstrap on the HEADLINE seed only (FIX A) ---
    rng = np.random.default_rng(42)
    ci = cluster_bootstrap_ci(
        headline_deltas,
        headline_cluster_keys,
        rng=rng,  # type: ignore[arg-type]
        n_boot=n_boot,
    )

    points = list(per_seed_point.values())
    return GoCiResult(
        ci=ci,
        headline_seed=headline_seed,
        per_seed_point=per_seed_point,
        point_min=min(points),
        point_max=max(points),
        point_mean=sum(points) / len(points),
    )


# ---------------------------------------------------------------------------
# V3 seed loader (baseline)
# ---------------------------------------------------------------------------


def _load_v3_seed(seed_path: Path | None = None) -> StrategyConfig:
    """Load the v3 committed seed (κ_xm=0 baseline)."""
    if seed_path is None:
        seed_path = _SEED_OUT  # docs/backtest/value_seed_v3.json

    from agent.core.state import Weights

    if not seed_path.exists():
        # Fallback: use the DEFAULT_OPTIMUM_SEED from survival_season
        from agent.backtest.survival_season import DEFAULT_OPTIMUM_SEED
        return DEFAULT_OPTIMUM_SEED

    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    w = raw["weights"]
    weights = Weights(
        w_r=w["w_r"],
        w_s=w["w_s"],
        alpha=w["alpha"],
        beta=w["beta"],
        rho=w["rho"],
    )
    return StrategyConfig(
        weights=weights,
        max_breath_risk_pct=raw["max_breath_risk_pct"],
        min_confidence=raw["min_confidence"],
        min_bet_size_usd=raw["min_bet_size_usd"],
        min_edge=raw["min_edge"],
        kappa=raw["kappa"],
        kappa_xm=raw.get("kappa_xm", 0.0),  # v3 seed may lack this field
    )


# ---------------------------------------------------------------------------
# Markdown report writer
# ---------------------------------------------------------------------------


def go_ci_headline_seed(
    go_robustness: GoCiResult | None, lhs_seeds: list[int]
) -> int:
    """The headline LHS seed the GO-CI was computed on (FIX A readout)."""
    if go_robustness is not None:
        return go_robustness.headline_seed
    return lhs_seeds[0] if lhs_seeds else 0


def _robustness_lines(go_robustness: GoCiResult | None) -> list[str]:
    """Render the descriptive selection-robustness readout (FIX A).

    The per-LHS-seed point estimates and their spread are reported here — they
    are NOT fed into the bootstrap (that would be pseudo-replication).
    """
    if go_robustness is None or not go_robustness.per_seed_point:
        return []
    lines = [
        "### Selection-robustness readout (per-LHS-seed point estimates)",
        "",
        "Descriptive ONLY — the LHS-seed grid is a separate replicate axis, NOT",
        "pooled into the headline bootstrap above.",
        "",
        "| LHS seed | mean per-row delta |",
        "|----------|--------------------|",
    ]
    for seed, point in sorted(go_robustness.per_seed_point.items()):
        lines.append(f"| {seed} | {point:.6f} |")
    lines += [
        "",
        f"Spread across seeds — min `{go_robustness.point_min:.6f}` | "
        f"mean `{go_robustness.point_mean:.6f}` | "
        f"max `{go_robustness.point_max:.6f}`.",
        "",
        "---",
        "",
    ]
    return lines


def write_report(
    out_path: Path,
    *,
    lhs_seeds: list[int],
    placebo_seeds: list[int],
    triples: list[SeedTriple],
    go_ci: BootstrapCI,
    sign_test: dict[str, Any],
    n: int,
    walk_forward: bool,
    train_fraction: float,
    active_path: Path,
    placebo_path: Path,
    go_robustness: GoCiResult | None = None,
) -> None:
    """Write the two-layer verdict to ``out_path`` as a Markdown document.

    ``go_ci`` is the HEADLINE-seed cluster-bootstrap CI (FIX A: the bootstrap is
    computed on a single selection, so ``go_ci.n`` is the matched test rows, NOT
    × seeds).  ``go_robustness`` (optional) carries the descriptive per-seed
    point-estimate spread for the selection-robustness section.
    """
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%MZ")

    # Layer-1 verdict — FIX C: route through three_state_verdict (min-cluster /
    # min-n guard).  Only the EDGE state maps to layer1_pass; INCONCLUSIVE /
    # REFUTED are rendered explicitly below.
    go_verdict, layer1_pass = layer1_verdict(go_ci)

    # Layer-2 verdict
    layer2_pass = sign_test["verdict"] == "GO"

    # Overall conclusion
    if layer1_pass and layer2_pass:
        conclusion = "EDGE CONFIRMED — both layers pass."
    elif not layer1_pass and not layer2_pass:
        conclusion = f"NO_GO — both layers fail (Layer 1 = {go_verdict})."
    elif layer1_pass:
        conclusion = "NO_GO — Layer 1 (GO edge CI) passes but Layer 2 (survival gate) fails."
    else:
        conclusion = (
            f"NO_GO — Layer 1 (GO edge CI) = {go_verdict}; "
            "Layer 2 (survival gate) irrelevant."
        )

    lines: list[str] = [
        "# Cross-Market κ_xm Journey — Three-Arm Backtest",
        "",
        f"Generated: {now}",
        f"Active rows: `{active_path}`",
        f"Placebo rows: `{placebo_path}`",
        f"LHS seeds: `{lhs_seeds}`  |  Placebo seeds: `{placebo_seeds}`",
        f"Walk-forward: `{walk_forward}`  |  Train fraction: `{train_fraction}`  |  n (LHS): `{n}`",
        "",
        "---",
        "",
        "## Honest Conclusion",
        "",
        f"**{conclusion}**",
        "",
        "Rule: EDGE recorded only if BOTH Layer 1 (three_state_verdict == EDGE: "
        "CI excludes 0, positive sign, ≥min clusters/n) AND",
        "Layer 2 (survival sign test p<0.05, majority of seeds beat baseline) pass.",
        "",
        "---",
        "",
        "## Layer 1 — GO Edge CI (path-independent cluster bootstrap)",
        "",
        "Substrate: per-ROW PnL delta — TREATMENT scored on ACTIVE-test (real",
        "signal) MINUS PLACEBO scored on PLACEBO-test (permuted signal), fast-",
        "scorer only (**NOT** survival path-dependent PnL). Unmatched rows",
        "(cluster_key == '') excluded. Both-NO_BET rows contribute delta=0.0 (kept).",
        "",
        f"Inference unit = the test cluster for a SINGLE selection (headline LHS "
        f"seed `{go_ci_headline_seed(go_robustness, lhs_seeds)}`). The bootstrap is "
        "computed ONCE on that selection — the LHS-seed grid is a SEPARATE",
        "descriptive replicate axis (NOT pooled into the bootstrap).",
        "",
        f"Pre-registered: GO_CI_SESOI = {GO_CI_SESOI} (per-bet PnL-delta substrate); "
        f"three_state_verdict(min_clusters={_GO_CI_MIN_CLUSTERS}, min_n={_GO_CI_MIN_N}).",
        "A sub-min-cluster / sub-min-n CI reads INCONCLUSIVE, never EDGE.",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| n (matched test rows, headline seed — NOT × LHS seeds) | {go_ci.n} |",
        f"| n_clusters | {go_ci.n_clusters} |",
        f"| point estimate (mean delta) | {go_ci.point:.6f} |",
        f"| 95% cluster-bootstrap CI lo | {go_ci.lo:.6f} |",
        f"| 95% cluster-bootstrap CI hi | {go_ci.hi:.6f} |",
        f"| iid CI lo (sensitivity) | {go_ci.iid_lo:.6f} |",
        f"| iid CI hi (sensitivity) | {go_ci.iid_hi:.6f} |",
        f"| **Layer 1 verdict (EDGE / INCONCLUSIVE / REFUTED)** | **{go_verdict}** |",
        "",
        *_robustness_lines(go_robustness),
        "---",
        "",
        "## Layer 2 — Survival Descriptive Gate (per-seed sign test)",
        "",
        "BASELINE = v3 seed (κ_xm=0), evaluated on the IDENTICAL held-out TEST",
        f"SurvivalRows the TREATMENT uses (same split, same fragile physics). "
        f"Error bars from n={len(triples)} LHS seeds.",
        "**NOT cluster-bootstrapped** (survival is path-dependent).",
        "Caveat: a short test window may saturate finished-alive (fragile seeds",
        "rarely die in ~30% of the season) — so Layer 2 is DESCRIPTIVE and",
        "Layer 1 (the GO edge CI) is the inferential gate.",
        "",
        "| LHS seed | T alive | T PnL | B alive | B PnL | T>B? |",
        "|----------|---------|-------|---------|-------|------|",
    ]
    for t in triples:
        wins = "YES" if treatment_beats_baseline(t) else "NO"
        lines.append(
            f"| {t.lhs_seed} "
            f"| {'Y' if t.treatment_alive else 'N'} "
            f"| {t.treatment_pnl:.2f} "
            f"| {'Y' if t.baseline_alive else 'N'} "
            f"| {t.baseline_pnl:.2f} "
            f"| {wins} |"
        )
    lines += [
        "",
        "Sign-test summary:",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| n seeds | {sign_test['n_seeds']} |",
        f"| T wins (T alive AND T PnL > B PnL) | {sign_test['n_treatment_wins']} |",
        f"| T losses | {sign_test['n_treatment_losses']} |",
        f"| one-sided p-value | {sign_test['sign_test_pvalue']} |",
        f"| **Layer 2 verdict** | **{sign_test['verdict']}** |",
        "",
        "---",
        "",
        "## Pre-registered verdict rule",
        "",
        "Both layers must pass for an EDGE to be recorded:",
        "",
        "| L1 (CI) | L2 (sign) | Conclusion |",
        "|---------|-----------|------------|",
        "| EDGE | GO | EDGE CONFIRMED |",
        "| EDGE | NO_GO | NO_GO |",
        "| NO_GO | GO | NO_GO |",
        "| NO_GO | NO_GO | NO_GO |",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[journey] report written to {out_path}", flush=True)


# ---------------------------------------------------------------------------
# Public entry point (reusable by tests)
# ---------------------------------------------------------------------------


def run_journey(
    *,
    active_rows: list[SignalRow],
    placebo_rows_by_seed: dict[int, list[SignalRow]],
    snapshots: list[MarketSnapshot],
    resolver: TennisMatchResolver,
    lhs_seeds: list[int],
    placebo_seeds: list[int],
    n: int = _DEFAULT_N,
    walk_forward: bool = True,
    train_fraction: float = 0.7,
    n_boot: int = _N_BOOT,
    out_path: Path = _DEFAULT_OUT,
    score_kw: dict[str, Any] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the full three-arm experiment and write the report.

    This is the reusable entry point for both the CLI and offline composition
    tests.  Returns a summary dict with both layer results.

    Parameters
    ----------
    active_rows:
        The TREATMENT signal rows (augmented with real cross_market_signal).
    placebo_rows_by_seed:
        ``{placebo_seed: placebo_rows}`` mapping.  Each set of placebo rows
        carries permuted signal values for that seed.
    snapshots:
        Market snapshots (cassettes) for the survival season run-half.
    resolver:
        TennisMatchResolver for the survival join.
    lhs_seeds:
        Pre-registered grid of LHS seeds (the SELECT randomness axis).
    placebo_seeds:
        Pre-registered set of placebo seeds (the PLACEBO randomness axis).
    n:
        Number of LHS configs per sweep.
    walk_forward:
        Whether to use the walk-forward OOS split (True for the real run).
    train_fraction:
        Train/test split ratio.
    n_boot:
        Bootstrap resamples for the GO-CI.
    out_path:
        Where to write the Markdown report.
    score_kw:
        Realism kwargs for ``score_config`` (defaults to ``_SCORE_KW``).
    verbose:
        Whether to print progress.

    Returns a dict with keys: ``go_ci`` (headline-seed :class:`BootstrapCI`),
    ``go_robustness`` (:class:`GoCiResult`), ``go_verdict`` (three-state),
    ``sign_test``, ``triples``, ``layer1_pass``, ``layer2_pass``,
    ``overall_edge``.
    """
    # Layer 1: GO edge CI (headline-seed single bootstrap + robustness readout)
    if verbose:
        print("\n=== LAYER 1: GO edge CI ===", flush=True)
    go_result = compute_go_ci_for_seeds(
        lhs_seeds,
        active_rows,
        placebo_rows_by_seed,
        snapshots,
        resolver,
        n=n,
        walk_forward=walk_forward,
        train_fraction=train_fraction,
        n_boot=n_boot,
        verbose=verbose,
    )
    go_ci = go_result.ci

    # Layer 2: survival gate + sign test
    if verbose:
        print("\n=== LAYER 2: survival gate ===", flush=True)
    triples: list[SeedTriple] = []
    for lhs_seed in lhs_seeds:
        triple = run_one_seed(
            lhs_seed,
            active_rows,
            placebo_rows_by_seed,
            snapshots,
            resolver,
            n=n,
            walk_forward=walk_forward,
            train_fraction=train_fraction,
            verbose=verbose,
        )
        triples.append(triple)
    sign_test = sign_test_over_seeds(triples)

    # FIX C: Layer-1 pass routes through three_state_verdict (min-cluster/min-n).
    go_verdict, layer1_pass = layer1_verdict(go_ci)
    layer2_pass = sign_test["verdict"] == "GO"

    write_report(
        out_path,
        lhs_seeds=lhs_seeds,
        placebo_seeds=placebo_seeds,
        triples=triples,
        go_ci=go_ci,
        sign_test=sign_test,
        n=n,
        walk_forward=walk_forward,
        train_fraction=train_fraction,
        active_path=Path("(active)"),
        placebo_path=Path("(placebo)"),
        go_robustness=go_result,
    )

    return {
        "go_ci": go_ci,
        "go_robustness": go_result,
        "go_verdict": go_verdict,
        "sign_test": sign_test,
        "triples": triples,
        "layer1_pass": layer1_pass,
        "layer2_pass": layer2_pass,
        "overall_edge": layer1_pass and layer2_pass,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Three-arm cross-market journey driver (two-layer statistical verdict). "
            "Run steps 8-9 on a terminal — NOT in Claude."
        )
    )
    ap.add_argument(
        "--active",
        type=Path,
        default=_DEFAULT_ACTIVE,
        help="Active (augmented) signal rows (default: %(default)s).",
    )
    ap.add_argument(
        "--placebo",
        type=Path,
        default=_DEFAULT_PLACEBO,
        help="Placebo signal rows (default: %(default)s). "
        "Provide a comma-separated list for multiple placebo seeds.",
    )
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=_DEFAULT_CACHE_DIR,
        help="Market snapshot cache directory (default: %(default)s).",
    )
    ap.add_argument(
        "--lhs-seeds",
        default=",".join(str(s) for s in _DEFAULT_LHS_SEEDS),
        help="Comma-separated LHS seeds (default: %(default)s).",
    )
    ap.add_argument(
        "--placebo-seeds",
        default=",".join(str(s) for s in _DEFAULT_PLACEBO_SEEDS),
        help="Comma-separated placebo seeds (default: %(default)s). "
        "The --placebo path must contain the default-seed permutation; extra "
        "seeds are generated in-memory via make_placebo_rows.",
    )
    ap.add_argument("--n", type=int, default=_DEFAULT_N, help="LHS sweep size.")
    ap.add_argument(
        "--walk-forward",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use walk-forward OOS split (default: True). Pass "
        "--no-walk-forward to reach the v3 in-sample path.",
    )
    ap.add_argument(
        "--train-fraction",
        type=float,
        default=0.7,
        help="Train/test split ratio (default: 0.7).",
    )
    ap.add_argument(
        "--n-boot",
        type=int,
        default=_N_BOOT,
        help="Bootstrap resamples for the GO-CI (default: %(default)s).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help="Output Markdown report path (default: %(default)s).",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _build_parser().parse_args(argv)

    lhs_seeds = [int(s) for s in args.lhs_seeds.split(",")]
    placebo_seeds = [int(s) for s in args.placebo_seeds.split(",")]

    print(f"[journey] loading active rows from {args.active} …", flush=True)
    active_rows = load_rows(args.active)
    print(f"[journey] {len(active_rows)} active rows loaded", flush=True)

    print(f"[journey] loading placebo rows from {args.placebo} …", flush=True)
    # Build placebo rows for each seed:
    # seed=0 comes from the pre-written file; extra seeds generated in-memory.
    # Import via the scripts package (consistent with the tests + the rest of
    # the file's agent.* imports); the bare `from setprob_augment import …`
    # ModuleNotFoundError'd at runtime since scripts/ is not on the package path.
    from scripts.setprob_augment import make_placebo_rows

    base_placebo = load_rows(args.placebo)
    placebo_rows_by_seed: dict[int, list[SignalRow]] = {}
    for pseed in placebo_seeds:
        if pseed == 0:
            placebo_rows_by_seed[0] = base_placebo
        else:
            placebo_rows_by_seed[pseed] = make_placebo_rows(active_rows, seed=pseed)

    print(f"[journey] loading snapshots from {args.cache_dir} …", flush=True)
    snapshots = load_all_cached_markets(cache_dir=args.cache_dir)
    resolver = _build_corpus_resolver()

    result = run_journey(
        active_rows=active_rows,
        placebo_rows_by_seed=placebo_rows_by_seed,
        snapshots=snapshots,
        resolver=resolver,
        lhs_seeds=lhs_seeds,
        placebo_seeds=placebo_seeds,
        n=args.n,
        walk_forward=args.walk_forward,
        train_fraction=args.train_fraction,
        n_boot=args.n_boot,
        out_path=args.out,
    )

    go_ci = result["go_ci"]
    sign = result["sign_test"]
    print(
        f"\n[journey] DONE\n"
        f"  Layer 1 GO-CI ({result['go_verdict']}): [{go_ci.lo:.4f}, {go_ci.hi:.4f}]  "
        f"point={go_ci.point:.4f}  n={go_ci.n} (matched test rows, headline seed)  "
        f"n_clusters={go_ci.n_clusters}\n"
        f"  Layer 2 sign test: {sign['n_treatment_wins']}/{sign['n_seeds']} "
        f"seeds T>B  p={sign['sign_test_pvalue']}\n"
        f"  Overall: {'EDGE' if result['overall_edge'] else 'NO_GO'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
