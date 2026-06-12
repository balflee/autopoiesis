# Reincarnation Experiment (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent lives the SAME season three times, carrying experience (weights + EMA state + an LLM "rebirth retrospective") but never market outcomes, then proves generalization with one learning-frozen cold-start pass on a held-out time window — presented on a new `/reincarnation` page (Phase 2) while `/survival` is reframed as Phase 1 with its data untouched.

**Architecture:** A new `agent/backtest/reincarnation.py` orchestrates N full `run_survival_season` passes over a time-split training window (cross-pass carry via two NEW additive params on the season: `shared_inner` + `learning_enabled`), with an optional pass-boundary strict-advisor retrospective (season-level `PerformanceWindow`, strategy-level lessons only), then a frozen holdout pass + baselines. The artifact is a light (~100 KB) pass-curve JSON, NOT a full journey. The dashboard gets a dedicated server-loaded page with an SVG multi-pass overlay chart.

**Tech Stack:** Python 3.11 (pytest, mypy --strict, ruff), existing v3 physics (side-correct + value mode defaulted at export layer), Next.js dashboard (vitest), Vercel CLI deploy.

**Grounded contracts (verified this session):**
- `run_survival_season(rows=, snapshots=, seed=, state_root=, initial_breath=, initial_bankroll_usd=, max_lives=, settle_lag=, max_bet_pnl_usd=, recorder=, ai=, side_correct_pricing=, value_betting=, effective_entry_price_floor=)` creates `shared_inner = WeightUpdater()` INTERNALLY (`survival_season.py:1725`) and swaps the learning adapter onto the poller (`:1637-1646`); terminal weights live at `result.lives[-1].terminal_weights`. **`LifeOutcome` (`survival_season.py:1147-1162`) carries bets/settlements but NO pnl field (review-r1 M-4)** — per-life pnl is computed from `recorder.steps` grouped by `SurvivalStep.life_idx` (`:658-688`; the journey's own pnl derivation at `:2282-2288` is the pattern).
- Season scheduling's canonical chronological order sorts by `(parsed timestamp, market_id)` (`survival_season.py:481-482`); tied entry timestamps are explicitly plausible (`:134-143`) — the split must use the SAME key and keep ties on one side (review-r1 M-3).
- `WeightUpdater`'s carried inner state = the per-feature EMA buffer `dict[str, float]` (`weight_updater.py:299-302`, smoothed at `:470-474`). The poller feeds the settlement path `score_<engine>` / `pnl_usd` / `size_usd` / `bet_direction` (`settlement_learner.py:75-98`), but what the EMA actually STORES are the DERIVED quality features built at `weight_updater.py:400-418` (r4 H-1 correction): `<engine>_quality = pnl_sign·bet_direction·score` per engine, `rational_stream_quality` / `sentient_stream_quality` aggregates, and `rho_quality = tanh(pnl/size)` — ~ten settlement-credit scalar aggregates, no raw outcomes, no market identities. This is part of the carried experience and MUST be disclosed (review-r1 H-1).
- The strict advisor's entire LLM input is `render_user_prompt(window)` (`strategy_advisor_impl.py:312-313`) — the rebirth LLM sees ONLY the aggregates-only window we build, so its rationale cannot leak real market specifics it never received; the persisted note is STILL post-sanitized as enforced hygiene, not assumption (review-r1 H-2).
- Cold-start freeze seam: if the learning-adapter swap is SKIPPED, the poller keeps its inert `_NoopSettlementWeightUpdater` placeholder (`:1599`) — weights stay byte-frozen while breath/death/value-mode physics run unchanged.
- `PerformanceWindow` construction template: `_GATE_PROBE_WINDOW` (`survival_season.py:1312-1327`) — fields tick/ts/agent_id/phase/current_weights/baseline_weights/recent_pnl_window_usd/trigger/recent_pnl/tick_count.
- Strict advisor: `StrategyAdvisorImpl(llm_client=, cost_guard=, weight_delta_only=True, model=)` + `review_window(window) -> list[StrategyProposal]`; applicable proposals have `kind=="weight_delta"` and `proposed_change={"key": one of w_r/alpha_0/alpha_1/alpha_2/beta_0/rho, "delta": float}` with |delta| ≤ 0.1 (validated by the impl).
- `SurvivalRow.entry_asof_ts_iso` is the entry timestamp; journey/season order == rows order (verified earlier).
- v3 export defaults: `run_survival_export(side_correct_pricing=True, value_betting=True, effective_entry_price_floor=MIRROR_ROW_FLOOR)`. **`run_survival_export`'s OWN knob defaults are loss_multiplier 1.0 / breath 100.0 / max_lives 50 (`survival_season.py:2531-2536`) — the shipped v3 journey knobs (fragile 0.95 / loss_multiplier 5.0 / breath 35 / max_lives 12) are passed EXPLICITLY by `scripts/run_v3_numerical.py:52-60` and `scripts/run_v3_ai.py:78-86` (review-r1 M-6); the reincarnation runner must pass them explicitly the same way.**
- Dashboard conventions: graceful `loadSurvivalJourneyOrNull` pattern, `next.config.ts` `outputFileTracingIncludes` for fn-bundle tracing, `.gitignore`d deploy-only artifacts, abyss design system (`.abyss`, `--ab-*`, SpineSection idiom from /docs page).

**Honest-notes contract (must appear verbatim-equivalent on the page; r1 H-1 + r4 H-1 rewrite):** (1) cross-pass improvement on the SAME season includes a memorization channel — the defense is the parameter bottleneck, stated COMPLETELY: the carried experience is the 8 fusion-weight scalars PLUS the EMA learner's ~ten derived quality aggregates (per-engine settlement-credit EMAs, two stream qualities, `rho_quality` — smoothed scalars; no raw outcomes, no market identities) plus, in the AI variant, one sanitized strategy-level text note — a ~20-scalar surface that cannot store ~3,400 market outcomes, so improvement must come from generalizable structure; the artifact disclosed the carried EMA keyset per pass so the claim is auditable; (2) the cold-start pass on unseen markets with learning frozen is the only number that proves generalization; (3) retrospectives are constrained to strategy-level aggregates BY INFORMATION FLOW (the advisor's entire input is the season-aggregate window) and the persisted note is post-sanitized.

---

### Task 1: time split + pass-curve schema (`agent/backtest/reincarnation.py` core)

**Files:**
- Create: `agent/backtest/reincarnation.py`
- Test: `tests/agent/backtest/test_reincarnation.py` (create)

- [ ] **Step 1: failing tests**

```python
# tests/agent/backtest/test_reincarnation.py
"""Phase-2 reincarnation experiment: time split, weight-delta application,
rebirth window, and the multi-pass export."""

from __future__ import annotations

import pytest

from agent.backtest.reincarnation import (
    apply_weight_deltas,
    build_rebirth_window,
    split_rows_by_time,
)
from agent.core.state import Weights

# Reuse the survival-season test fixtures for SurvivalRow construction.
from tests.agent.backtest.test_survival_season import _survival_row


def _row_at(ts: str, market_id: str):
    row = _survival_row(market_id=market_id, entry_price=0.5, outcome="yes")
    # _survival_row pins a fixed ts; rebuild with the desired one.
    import dataclasses

    return dataclasses.replace(row, entry_asof_ts_iso=ts)


def test_split_rows_by_time_orders_then_splits() -> None:
    rows = [
        _row_at("2025-06-01T00:00:00+00:00", "m_b"),
        _row_at("2024-01-01T00:00:00+00:00", "m_a"),
        _row_at("2026-01-01T00:00:00+00:00", "m_c"),
        _row_at("2025-12-01T00:00:00+00:00", "m_d"),
    ]
    train, holdout = split_rows_by_time(rows, train_fraction=0.5)
    assert [r.market_id for r in train] == ["m_a", "m_b"]
    assert [r.market_id for r in holdout] == ["m_d", "m_c"]
    # No leakage: every train entry STRICTLY precedes every holdout entry.
    assert max(r.entry_asof_ts_iso for r in train) < min(
        r.entry_asof_ts_iso for r in holdout
    )


def test_split_keeps_tied_timestamps_on_the_train_side() -> None:
    """r1 M-3: equal-time markets must never straddle the boundary — ties at
    the cut are pulled INTO train so holdout starts strictly later."""
    tie = "2025-06-01T00:00:00+00:00"
    rows = [
        _row_at("2024-01-01T00:00:00+00:00", "m_a"),
        _row_at(tie, "m_b"),
        _row_at(tie, "m_c"),
        _row_at("2026-01-01T00:00:00+00:00", "m_d"),
    ]
    train, holdout = split_rows_by_time(rows, train_fraction=0.5)
    assert [r.market_id for r in train] == ["m_a", "m_b", "m_c"]
    assert [r.market_id for r in holdout] == ["m_d"]
    assert max(r.entry_asof_ts_iso for r in train) < min(
        r.entry_asof_ts_iso for r in holdout
    )


def test_split_rejects_degenerate_fractions_and_all_tied_rows() -> None:
    rows = [_row_at("2024-01-01T00:00:00+00:00", "m_a")]
    with pytest.raises(ValueError):
        split_rows_by_time(rows, train_fraction=0.0)
    with pytest.raises(ValueError):
        split_rows_by_time(rows, train_fraction=1.0)
    # Tie-absorption exhausting the holdout is degenerate, not silent.
    tied = [
        _row_at("2025-01-01T00:00:00+00:00", f"m_{i}") for i in range(4)
    ]
    with pytest.raises(ValueError):
        split_rows_by_time(tied, train_fraction=0.5)
```

- [ ] **Step 2: run to fail** — `python -m pytest tests/agent/backtest/test_reincarnation.py -q -p no:cacheprovider` → ImportError.

- [ ] **Step 3: implement** (module header + split):

```python
# agent/backtest/reincarnation.py
"""Phase-2 reincarnation experiment (plan 2026-06-12).

The agent lives the SAME training season N times. Across passes it carries
its EXPERIENCE — the 8 fusion-weight scalars, the EMA learner's ~ten derived
quality aggregates (per-engine settlement-credit EMAs + stream qualities +
rho_quality; weight_updater.py builds them at update_from_settlement), and
(AI variant) one sanitized strategy-level "rebirth retrospective" — but never
the market outcomes themselves. The defense against memorization is the
parameter bottleneck: the whole carried surface is ~20 scalars, disclosed
per pass in the artifact (`carry.ema_keys`). After the passes, ONE
learning-frozen cold-start pass on a held-out later time window measures
generalization. v3 physics (side-correct payouts, EV-gated value mode,
effective floor) apply throughout.
"""

from __future__ import annotations

from datetime import datetime

from agent.backtest.survival_season import SurvivalRow


def _entry_ts(row: SurvivalRow) -> datetime:
    return datetime.fromisoformat(row.entry_asof_ts_iso)


def split_rows_by_time(
    rows: list[SurvivalRow], *, train_fraction: float = 0.7
) -> tuple[list[SurvivalRow], list[SurvivalRow]]:
    """Chronological split: first ``train_fraction`` of entry-time-ordered
    rows = the reincarnation training season; the remainder = the held-out
    cold-start window.

    Ordering matches the season scheduler's canonical key
    ``(parsed timestamp, market_id)`` (survival_season.py:481-482), and rows
    whose entry timestamp EQUALS the last train timestamp are pulled into
    train so the holdout starts STRICTLY later — equal-time markets can
    never straddle the boundary (no look-ahead leakage, r1 M-3)."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction must be in (0,1) (got {train_fraction})")
    if len(rows) < 2:
        raise ValueError("need at least 2 rows to split")
    ordered = sorted(rows, key=lambda r: (_entry_ts(r), r.market_id))
    cut = round(len(ordered) * train_fraction)
    cut = max(1, min(len(ordered) - 1, cut))
    # Absorb boundary ties into train.
    while cut < len(ordered) and _entry_ts(ordered[cut]) == _entry_ts(ordered[cut - 1]):
        cut += 1
    if cut >= len(ordered):
        raise ValueError(
            "split degenerate: tie absorption exhausted the holdout window"
        )
    return ordered[:cut], ordered[cut:]
```

- [ ] **Step 4: run to pass; ruff + mypy --strict on the new module.**
- [ ] **Step 5: commit** — `feat(reincarnation): time split for the phase-2 experiment`

### Task 2: season seams — `shared_inner` + `learning_enabled` (additive)

**Files:**
- Modify: `agent/backtest/survival_season.py` (`run_survival_season` signature + `:1725` + the adapter swap `:1637-1646`; `_build_life_loop` gains `learning_enabled`)
- Test: append to `tests/agent/backtest/test_survival_ai_mode.py`

- [ ] **Step 1: failing tests** (uses the file's existing `_dying_fixture`/`_fragile_seed`)

```python
def test_season_shared_inner_is_used_and_carries_state(tmp_path: Path) -> None:
    """The injected WeightUpdater IS the season's learner (its EMA buffer
    mutates), and its state survives into a second season — the carry seam.

    r4 H-2: do NOT assert carried-vs-fresh terminal-weight divergence on the
    constant-score `_dying_fixture` — with a constant feature stream a fresh
    EMA initializes AT the first value and equals a carried one
    (weight_updater.py:473), so divergence is not guaranteed. The seam
    contract is asserted directly on the injected instance's state, over a
    VARIED-score fixture so the EMA keeps moving.
    """
    from agent.engines.weight_updater import WeightUpdater

    # Varied scores (NOT the constant 0.8 default) — rebuild rows off the
    # fixture's snaps; `_row(snap, score=...)` is the file's own helper.
    _, snaps = _dying_fixture()
    rows = [_row(s, score=x) for s, x in zip(snaps, (0.8, 0.3, 0.6))]
    seed = _fragile_seed()
    inner = WeightUpdater()
    assert inner._ema == {}
    r1 = run_survival_season(
        rows=rows, snapshots=snaps, seed=seed, state_root=tmp_path / "s1",
        initial_breath=3.0, max_lives=2, shared_inner=inner,
    )
    assert r1.lives, "sanity: season ran"
    assert inner._ema, "the injected inner must be the instance that learned"
    ema_after_1 = dict(inner._ema)
    run_survival_season(
        rows=rows, snapshots=snaps, seed=seed, state_root=tmp_path / "s2",
        initial_breath=3.0, max_lives=2, shared_inner=inner,
    )
    assert inner._ema != ema_after_1, (
        "season 2 must keep learning FROM the carried state (varied scores "
        "guarantee the tau-blend moves the buffer)"
    )


def test_season_learning_disabled_freezes_weights(tmp_path: Path) -> None:
    """learning_enabled=False: weights stay byte-frozen at the seed across the
    whole season (the cold-start contract) while the season still runs."""
    rows, snaps = _dying_fixture()
    seed = _fragile_seed()
    result = run_survival_season(
        rows=rows, snapshots=snaps, seed=seed, state_root=tmp_path / "s",
        initial_breath=3.0, max_lives=2, learning_enabled=False,
    )
    assert result.lives
    for life in result.lives:
        assert life.terminal_weights == seed.weights
```

- [ ] **Step 2: run to fail.**
- [ ] **Step 3: implement** — `run_survival_season(..., shared_inner: WeightUpdater | None = None, learning_enabled: bool = True)`:
  - replace `shared_inner = WeightUpdater()` (`:1725`) with `shared_inner = shared_inner if shared_inner is not None else WeightUpdater()`;
  - thread `learning_enabled` into `_build_life_loop(...)`; inside, wrap the adapter swap:

```python
    if learning_enabled:
        learning_adapter = _SettlementLearningWeightUpdater(
            inner=shared_inner, weights_holder=loop,
        )
        loop._poller.weight_updater = (
            recorder.wrap_updater(learning_adapter)
            if recorder is not None
            else learning_adapter
        )
    # learning_enabled=False (cold-start contract): the poller keeps its inert
    # _NoopSettlementWeightUpdater placeholder — weights stay byte-frozen while
    # breath/death/physics run unchanged. The recorder still captures steps via
    # wrap_updater around the NoOp so the pass curve exists.
    elif recorder is not None:
        loop._poller.weight_updater = recorder.wrap_updater(
            _SettlementLearningWeightUpdater(
                inner=_FrozenInnerUpdater(), weights_holder=loop,
            )
        )
```

  where `_FrozenInnerUpdater` is a tiny module-level no-op satisfying the inner-updater interface (returns the current weights unchanged). NOTE: verify the actual inner-updater Protocol during implementation and mirror `_SettlementLearningWeightUpdater`'s expectations; if the recorder path works equally with the poller's `_NoopSettlementWeightUpdater`, prefer that simpler wiring — the test above is the contract, not the wiring.
- [ ] **Step 4: run both new tests + the full survival/ai suites to pass.**
- [ ] **Step 5: commit** — `feat(reincarnation): season seams — shared EMA inner + learning freeze (additive)`

### Task 3: rebirth retrospective — window builder + delta application

**Files:**
- Modify: `agent/backtest/reincarnation.py`
- Test: append to `tests/agent/backtest/test_reincarnation.py`

- [ ] **Step 1: failing tests**

```python
def test_apply_weight_deltas_clamps_and_renormalizes() -> None:
    w = Weights(w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=0.5)
    out = apply_weight_deltas(
        w,
        [
            {"key": "w_r", "delta": 0.1},
            {"key": "alpha_2", "delta": 0.1},
            {"key": "rho", "delta": -0.2},
        ],
    )
    assert out.w_r == pytest.approx(0.6) and out.w_s == pytest.approx(0.4)
    assert sum(out.alpha) == pytest.approx(1.0)
    assert out.alpha[2] > w.alpha[2]
    assert out.rho == pytest.approx(0.3)


def test_apply_weight_deltas_skips_invalid_and_caps_magnitude() -> None:
    w = Weights(w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=0.5)
    out = apply_weight_deltas(
        w,
        [
            {"key": "nonsense", "delta": 0.4},   # unknown key: skipped
            {"key": "beta_0", "delta": 9.0},      # |delta| capped to 0.1
        ],
    )
    assert out.beta[0] == pytest.approx(0.6)
    assert out.beta[1] == pytest.approx(0.4)


def test_build_rebirth_window_is_strategy_level_only() -> None:
    seed_w = Weights(w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5], rho=0.5)
    window = build_rebirth_window(
        pass_index=1,
        terminal_weights=seed_w,
        seed_weights=seed_w,
        season_pnl_usd=-13.5,
        recent_step_pnls=[2.5, -8.0, 1.0],  # tail of SETTLED step pnls
        total_settles=120,
        deaths=2,
    )
    assert window.trigger == "tick_interval"
    # r4 M-5: recent_pnl's REAL semantics (prompt renderer + dataclass) are
    # "last settled bets, $USD" — feed it actual step pnls, never life totals.
    assert window.recent_pnl == [2.5, -8.0, 1.0]
    assert window.recent_pnl_window_usd == pytest.approx(-13.5)
    # Hygiene: the window carries ONLY aggregates — no market ids/names.
    assert "market" not in window.agent_id


def test_sanitize_rebirth_note_collapses_and_caps() -> None:
    """r1 H-2: the persisted note is enforced-clean — whitespace collapsed,
    hard length cap — never raw LLM text."""
    from agent.backtest.reincarnation import sanitize_rebirth_note

    assert sanitize_rebirth_note("  a \n\n b\t c  ") == "a b c"
    long = "x" * 2000
    out = sanitize_rebirth_note(long)
    assert len(out) <= 500
    assert sanitize_rebirth_note("") is None
    assert sanitize_rebirth_note("   \n ") is None
```

- [ ] **Step 2: run to fail.**
- [ ] **Step 3: implement** in `reincarnation.py`:

```python
import math
import re
from datetime import UTC, datetime

from agent.core.state import Phase, Weights
from agent.engines._performance_window import PerformanceWindow

_DELTA_CAP = 0.1
_KEYS = ("w_r", "alpha_0", "alpha_1", "alpha_2", "beta_0", "rho")
_NOTE_MAX_CHARS = 500


def sanitize_rebirth_note(text: str) -> str | None:
    """Enforced hygiene for the persisted rebirth note (r1 H-2): collapse
    whitespace, hard-cap length, empty ⇒ None. The advisor's entire input is
    the aggregates-only window (strategy_advisor_impl.py:312-313), so real
    market specifics cannot flow into the rationale — this makes the
    persistence layer enforce that contract rather than assume it."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return None
    return collapsed[:_NOTE_MAX_CHARS]


def apply_weight_deltas(
    weights: Weights, deltas: list[dict[str, object]]
) -> Weights:
    """Apply strict-advisor weight deltas, fail-soft (r4 L-6: deliberately
    NOT the runtime's `_apply_weight_delta`, which RAISES on unknown keys and
    leaves magnitude capping to the advisor parser —
    sandbox_phase2_loop.py:2571-2629 / strategy_advisor_impl.py:430-449).
    Here: |delta| capped to 0.1 (the advisor-schema bound), unknown keys and
    non-numeric deltas SKIPPED (a failed retrospective must never crash the
    experiment); w_r/w_s and beta complements; alpha shifted then
    renormalized to the simplex; rho clamped to [-1, 1]."""
    w_r, alpha, beta, rho = weights.w_r, list(weights.alpha), list(weights.beta), weights.rho
    for d in deltas:
        key = d.get("key")
        delta = d.get("delta")
        if key not in _KEYS or not isinstance(delta, (int, float)) or isinstance(delta, bool):
            continue
        dv = max(-_DELTA_CAP, min(_DELTA_CAP, float(delta)))
        if key == "w_r":
            w_r = max(0.0, min(1.0, w_r + dv))
        elif key == "rho":
            rho = max(-1.0, min(1.0, rho + dv))
        elif key == "beta_0":
            b0 = max(0.0, min(1.0, beta[0] + dv))
            beta = [b0, 1.0 - b0]
        else:  # alpha_0/1/2
            i = int(key[-1])
            alpha[i] = max(0.0, alpha[i] + dv)
            s = sum(alpha)
            alpha = [a / s for a in alpha] if s > 0 else [1 / 3, 1 / 3, 1 / 3]
    return Weights(w_r=w_r, w_s=1.0 - w_r, alpha=alpha, beta=beta, rho=rho)


def build_rebirth_window(
    *,
    pass_index: int,
    terminal_weights: Weights,
    seed_weights: Weights,
    season_pnl_usd: float,
    recent_step_pnls: list[float],
    total_settles: int,
    deaths: int,
) -> PerformanceWindow:
    """The season-level retrospective window the strict advisor reviews at a
    pass boundary. STRATEGY-LEVEL ONLY: aggregates, never market specifics —
    the information-hygiene contract of the reincarnation experiment.

    ``recent_pnl`` keeps its REAL semantics — "last settled bets, $USD"
    (prompt renderer `_strategy_prompts.py:369`, dataclass
    `_performance_window.py:186-187`) — so it receives the TAIL of settled
    step pnls (r4 M-5), while the season total goes in
    ``recent_pnl_window_usd``. Feeding life totals into recent_pnl would
    hand the advisor false semantics."""
    return PerformanceWindow(
        tick=total_settles,
        ts=datetime(1970, 1, 1, tzinfo=UTC),
        agent_id=f"rebirth-pass-{pass_index}-deaths-{deaths}",
        phase=Phase.PHASE_2_APPRENTICE,
        current_weights=terminal_weights,
        baseline_weights=seed_weights,
        recent_pnl_window_usd=season_pnl_usd,
        trigger="tick_interval",
        recent_pnl=list(recent_step_pnls),
        tick_count=total_settles,
    )
```

- [ ] **Step 4: run to pass + ruff/mypy.**
- [ ] **Step 5: commit** — `feat(reincarnation): rebirth window + clamped delta application`

### Task 4: `run_reincarnation_export` — the multi-pass orchestrator

**Files:**
- Modify: `agent/backtest/reincarnation.py`
- Test: append to `tests/agent/backtest/test_reincarnation.py` (tiny fixture via `test_survival_ai_mode._dying_fixture`, fake LLM for the AI variant)

- [ ] **Step 1: failing tests**

```python
def test_reincarnation_export_three_passes_plus_frozen_holdout(tmp_path) -> None:
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture, _fragile_seed,
    )
    from agent.backtest.reincarnation import run_reincarnation_export

    rows, snaps = _dying_fixture()
    out = tmp_path / "reincarnation.json"
    artifact = run_reincarnation_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=out,
        passes=3,
        train_fraction=0.5,
        initial_breath=3.0,
        max_lives=2,
    )
    assert out.exists()
    assert len(artifact["passes"]) == 3
    for i, p in enumerate(artifact["passes"], start=1):
        assert p["pass"] == i
        for key in ("pnl", "deaths", "lives", "settled", "coverage_pct", "win_rate"):
            assert key in p["summary"]
        assert p["curve"], "each pass carries a cumulative curve"
    h = artifact["holdout"]
    assert h["summary"]["learning_enabled"] is False
    assert set(h["baselines"]) == {"static", "random", "always_favorite"}
    # Physics disclosure (v3 throughout).
    assert artifact["physics"]["side_correct_pricing"] is True
    assert artifact["physics"]["value_betting"] is True
    # The split is chronological and disjoint.
    assert artifact["split"]["train_rows"] + artifact["split"]["holdout_rows"] == len(rows)


def test_reincarnation_ai_variant_records_rebirth_notes(tmp_path) -> None:
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture, _fragile_seed, _FakeAdvisorLLM,
    )
    from agent.backtest.reincarnation import run_reincarnation_export
    from agent.llm.cost_guard import L3CostGuard

    rows, snaps = _dying_fixture()
    artifact = run_reincarnation_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "r_ai.json",
        passes=2,
        train_fraction=0.5,
        initial_breath=3.0,
        max_lives=2,
        rebirth_llm=_FakeAdvisorLLM(),
        rebirth_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    # Pass boundaries: passes-1 retrospectives (after pass 1, before pass 2).
    notes = [p.get("rebirth_note") for p in artifact["passes"]]
    assert notes[0] is None  # pass 1 starts cold
    assert isinstance(notes[1], str) and len(notes[1]) > 0
    # r1 M-5: EMA learning alone moves start-vs-start weights, so that proves
    # nothing. The PROOF of boundary application: pass 2 starts EXACTLY at
    # apply_weight_deltas(pass-1 terminal, the fake's deterministic delta)
    # — _FakeAdvisorLLM always proposes {"key": "alpha_2", "delta": 0.04}.
    from agent.backtest.reincarnation import apply_weight_deltas

    p1_terminal = Weights(**artifact["passes"][0]["terminal_weights"])
    expected = apply_weight_deltas(p1_terminal, [{"key": "alpha_2", "delta": 0.04}])
    got = artifact["passes"][1]["start_weights"]
    assert got["alpha"][2] == pytest.approx(expected.alpha[2])
    assert got != artifact["passes"][0]["terminal_weights"]
    # r1 H-1: the carried state is disclosed per pass — auditable bottleneck.
    assert "carry" in artifact["passes"][1]
    assert isinstance(artifact["passes"][1]["carry"]["ema_keys"], list)
```

- [ ] **Step 2: run to fail.**
- [ ] **Step 3: implement** `run_reincarnation_export`:

```python
def run_reincarnation_export(
    *,
    rows: list[SurvivalRow],
    snapshots: list[MarketSnapshot],
    base_seed: StrategyConfig,
    out_path: Path,
    passes: int = 3,
    train_fraction: float = 0.7,
    fragile_max_breath_risk_pct: float = 0.95,
    loss_multiplier: float = DEFAULT_LOSS_MULTIPLIER,
    initial_breath: float = DEFAULT_INITIAL_BREATH,
    initial_bankroll_usd: float = DEFAULT_PHASE2_BANKROLL_USD,
    max_lives: int = DEFAULT_MAX_LIVES,
    max_bet_pnl_usd: float | None = DEFAULT_MAX_BET_PNL_USD,
    entry_price_floor: float = DEFAULT_ENTRY_PRICE_FLOOR,
    effective_entry_price_floor: float | None = None,  # None ⇒ mirror entry_price_floor
    rebirth_llm: _LLMClient | None = None,
    rebirth_guard: L3CostGuard | None = None,
    rebirth_model: str = "",
    state_root: Path | None = None,
) -> dict[str, Any]:
    """N passes over the SAME training season (weights + EMA inner carried;
    optional strict-advisor retrospective at each pass boundary), then ONE
    learning-frozen cold-start pass on the held-out window + the three
    baselines. Writes the pass-curve artifact (light: down-sampled cumulative
    curves + summaries, NOT full journeys) and returns it."""
```

  Body (exact responsibilities, full code at implementation):
  0. **Physics knobs thread, never hard-code (r3 finding; `run_survival_export` is the local precedent)**: resolve `eff_floor = entry_price_floor if effective_entry_price_floor is None else effective_entry_price_floor` ONCE at the top; the SAME `max_bet_pnl_usd` / `eff_floor` / `side_correct_pricing=True` / `value_betting=True` values then thread into every season call (passes + holdout), both baseline builders, the invariant recompute, AND the `physics` block of the artifact — one source of truth, no literal `0.05`/`100.0` in the body.
  1. **Row-floor provenance (r1 L-7)**: raise `ValueError` if any `row.entry_price < entry_price_floor` (the runner loads via `build_survival_rows` with the same floor, so this is a fail-closed cross-check, and the floor lands in `physics`). Then `train, holdout = split_rows_by_time(rows, train_fraction=...)`; derive fragile seed via `fragile_seed_from_config(base_seed, max_breath_risk_pct=fragile_max_breath_risk_pct)`.
  2. `shared_inner = WeightUpdater()`; `carry = seed.weights`; for each pass: fresh `SurvivalRecorder(rows=train, loss_multiplier=...)`, `run_survival_season(rows=train, ..., seed=dataclasses.replace(seed, weights=carry)... )` — NOTE `StrategyConfig` is frozen and holds `weights`; replace works. v3 physics flags ON (threaded knobs from step 0), `shared_inner=shared_inner`. Collect per-pass summary: `pnl` (= recorder cum), `deaths`, `lives`, `settled=len(recorder.steps)`, `coverage_pct`, `win_rate`, **`per_life_pnls` computed from `recorder.steps` grouped by `step.life_idx` (r1 M-4: `LifeOutcome` has NO pnl field — never read pnl from it)**, `start_weights`/`terminal_weights` (via `weights.model_dump()` — round-trips through `Weights(**d)` and matches `value_seed_v3.json`'s shape; NOT `_weights_to_dict`, whose flat `alpha_0/beta_0` keys cannot reconstruct — r2 finding), down-sampled `curve` (≤ 500 pts of `(i, cum_pnl)` from `recorder.steps`, reusing `_downsample`), **and the carried-state disclosure `carry = {"ema_keys": sorted(shared_inner._ema.keys()), "ema_size": len(shared_inner._ema)}` (r1 H-1; same-package private access, the module documents it)**.
  3. Pass boundary (if `rebirth_llm` is not None and more passes remain): `window = build_rebirth_window(season_pnl_usd=<recorder cum>, recent_step_pnls=[s.pnl_usd for s in recorder.steps][-20:], ...)` (r4 M-5: recent_pnl gets the SETTLED-step tail, the season total goes in recent_pnl_window_usd); `advisor = StrategyAdvisorImpl(llm_client=rebirth_llm, cost_guard=rebirth_guard or L3CostGuard.from_env(), weight_delta_only=True, model=rebirth_model)`; `proposals = advisor.review_window(window)`; `carry = apply_weight_deltas(carry_terminal, [p.proposed_change for p in proposals])`; **`rebirth_note = sanitize_rebirth_note("; ".join(p.rationale for p in proposals))` (r1 H-2 — never raw LLM text)** recorded on the NEXT pass entry. Numerical variant: `carry = terminal_weights` directly, `rebirth_note=None`.
  4. Holdout: `run_survival_season(rows=holdout, ..., seed=replace(seed, weights=carry_final), learning_enabled=False, shared_inner=None)` + recorder; baselines on holdout — each builder gets EXACTLY its own knob surface (r5 finding: they are NOT symmetric):
     - `build_static_baseline_curve(holdout, replace(seed, weights=carry_final), bankroll=initial_bankroll_usd, breath=initial_breath, max_pnl_usd=max_bet_pnl_usd, side_correct_pricing=True, value_betting=True, effective_entry_price_floor=eff_floor)` (full signature `survival_season.py:1973-1983`) — **r4 H-3: defaults are bankroll 100/breath 100 (`:1977-1978`); pass the season's values explicitly, mirroring `run_survival_export` (`:2753-2760`), or the holdout-vs-static verdict is non-comparable**;
     - `build_archetype_curve(holdout, archetype="random"|"always_favorite", seed=0, max_pnl_usd=max_bet_pnl_usd, side_correct_pricing=True, effective_entry_price_floor=eff_floor)` (full signature `:2013-2022`) — **NO bankroll/breath/value_betting params exist here: archetypes bet a flat $5 stake with no sizer and never consult Weights; do not invent kwargs**.
     Final cum only.
  5. Physics invariant — copy `run_survival_export`'s validator wholesale (`survival_season.py:2381-2412`), not a subset (r4 H-4): (a) every pass + holdout recorder step recomputed via `compute_bet_pnl(..., side_correct_pricing=True, max_pnl_usd=max_bet_pnl_usd)`; (b) every `is_bet` baseline point recomputed the same way; (c) the effective-floor check runs over ALL PLACED learner bets (`recorder.placed_bets` — `run_survival_season` already harvests each life's `open_bets.jsonl` at `:1768`) AND all baseline bets, so a placed-never-settled sub-floor bet cannot evade the backstop; `RuntimeError` before write; `physics.min_effective_entry_price` disclosed. Validation-only — no full market outcomes enter the artifact.
  6. Artifact: `{"experiment": "reincarnation", "provider": "numerical"|"ai", "physics": {...6 keys incl. entry_price_floor...}, "split": {train_rows, holdout_rows, train_end_ts, holdout_start_ts}, "passes": [...], "holdout": {...}}`; `out_path.write_text(json.dumps(..., sort_keys=True, indent=2), encoding="utf-8")`.
  7. `state_root=None` ⇒ tempfile dirs per pass (mirroring `run_survival_export`).
  8. **Knob defaults (r1 M-6)**: the orchestrator's own defaults mirror `run_survival_export`'s (loss 1.0 / breath 100 / lives 50) so tiny-fixture tests stay cheap; the REAL journey knobs (fragile 0.95 / loss 5.0 / breath 35 / lives 12) are passed EXPLICITLY by the Task 5 runner, exactly as `scripts/run_v3_numerical.py:52-60` does.
- [ ] **Step 4: run to pass + full backtest suite + ruff/mypy.**
- [ ] **Step 5: commit** — `feat(reincarnation): multi-pass export with frozen cold-start + rebirth retrospectives`

### Task 5: runner script + real runs

**Files:**
- Create: `scripts/run_reincarnation.py` (loads cached rows+snapshots, v3 seed json, `--provider numerical|gemini`, gemini = `RetryLLMClient(inner=GeminiClient())` as `rebirth_llm` with `model=""`; dotenv via `agent.llm._smoke._load_dotenv_if_present`; outputs `dashboard/public/backtest/reincarnation.json` / `reincarnation_ai.json`)
- Modify: `.gitignore` += `dashboard/public/backtest/reincarnation*.json`

- [ ] **Step 1: write the script** (mirror `scripts/run_v3_ai.py` structure: sys.path bootstrap, argparse, seed via `run_v3_numerical.load_v3_seed()`, rows via `build_survival_rows(load_rows(...), load_all_cached_markets(...), resolver)` with floor 0.05 — reuse `run_survival_export`'s loading idiom; print the per-pass + holdout one-liner). **The runner passes the v3 journey knobs EXPLICITLY (r1 M-6) — `fragile_max_breath_risk_pct=0.95, loss_multiplier=5.0, initial_breath=35.0, max_lives=12, passes=3, train_fraction=0.7, entry_price_floor=0.05` — exactly mirroring `scripts/run_v3_numerical.py:52-60`; the orchestrator's own defaults are NOT the journey knobs.**
- [ ] **Step 2: ruff/mypy; commit script + gitignore** — `feat(reincarnation): runner script`.
- [ ] **Step 3: RUN numerical** (`python scripts/run_reincarnation.py --provider numerical`, background; ~3 sequential seasons + holdout ≈ 30-50 min). Verify: 3 passes present; report the pass-over-pass trajectory (pnl/deaths/coverage) + holdout verdict HONESTLY (improvement is hoped for, not promised).
- [ ] **Step 4: RUN gemini** (background, ~1-2 h; `require` nothing — retrospectives are fail-soft; record notes).

### Task 6: dashboard — `/reincarnation` page (Phase 2) + Phase-1 reframe

**Files:**
- Create: `dashboard/lib/load_reincarnation.server.ts` (graceful loader, env overrides `REINCARNATION_PATH`/`REINCARNATION_AI_PATH`, filenames `reincarnation.json`/`reincarnation_ai.json`; minimal structural validation: experiment tag, ≥1 pass, physics booleans)
- Create: `dashboard/app/reincarnation/page.tsx` (THIN async server component, force-dynamic: loads the two fixtures, hands them to the client shell — NO testable markup of its own) + `dashboard/app/reincarnation/ReincarnationShell.tsx` (CLIENT component owning ALL the markup + the numerical/AI toggle state — abyss shell; hero "PHASE 2 · THE REINCARNATION EXPERIMENT / the same season, lived three times"; sections: §1 why (epoch test + lives-comparability), §2 per-pass metric cards (pass N: pnl / deaths / coverage / win rate), §3 the overlay chart, §4 cold-start verdict panel (holdout pnl vs static/random/always-favorite finals, learning frozen), §5 honest notes (the three-point contract above), §6 back-links to /survival (Phase 1) + /docs). **r7 finding: async server pages are NOT rendered in vitest in this repo (survival.test.tsx's own header documents the convention — client body tested against fixtures, loader tested separately); docs.test.tsx renders DocsPage directly ONLY because it is sync/static. All testids live in the shell.**
- Create: `dashboard/app/reincarnation/PassCurves.tsx` (client SVG multi-polyline: normalize each pass curve to shared axes; pass 1→3 in rising glow opacity 0.35/0.6/1.0; holdout dashed; viewBox 800×280, `preserveAspectRatio="none"`, mirroring the BreathWaveform polyline idiom)
- Modify: `dashboard/app/survival/SurvivalJourneyShell.tsx` (banner above the learning-mode section: `PHASE 1 · BACKTEST WITH AI` chip + one line "Phase 2 — the reincarnation experiment — lives at /reincarnation ▸"; in the CLIENT shell so the survival.test.tsx pattern can assert it; DATA UNTOUCHED — `app/survival/page.tsx` is not edited at all)
- Modify: `dashboard/app/roadmap/page.tsx` (cross-link "phase 2 ▸ /reincarnation" below the existing /docs link)
- Modify: `dashboard/next.config.ts` (`outputFileTracingIncludes` for `/reincarnation` += the two json files)
- Test: `dashboard/__tests__/reincarnation.test.tsx` (create — repo convention per r7: render `ReincarnationShell` DIRECTLY with an inline fixture object (hero, 3 pass cards, chart testid, cold-start panel, honest-notes text, /survival back-link); a pure `validateReincarnation` unit test on the loader's exported validator (no disk IO); the survival Phase-1 banner asserted by rendering `SurvivalJourneyShell` with the existing survival fixture-builder; the roadmap link by rendering `RoadmapPage` directly (sync component — same as docs.test.tsx already does))

- [ ] **Step 1: failing vitest suite** (assert testids: `reincarnation-route` (on the shell root), `reincarnation-pass-1..3`, `reincarnation-chart`, `reincarnation-coldstart`, `reincarnation-honest`, `survival-phase1-banner`, `roadmap-reincarnation-link`).
- [ ] **Step 2: implement page + loader + chart + banner + links.**
- [ ] **Step 3: `npx vitest run` + `npx tsc --noEmit` green.**
- [ ] **Step 4: commit** — `feat(dashboard): /reincarnation Phase-2 page + Phase-1 reframe of /survival`

### Task 7: docs + ship

- [ ] **Step 1:** README: add a "Phase 2 — the reincarnation experiment" paragraph (design + the honest memorization/cold-start framing + REAL numbers from Task 5 runs); `/docs` runs context: one line in the timeline/§4 caveat pointing to /reincarnation.
- [ ] **Step 2:** full regression (`python -m pytest tests/agent/... -q`; mypy --strict on changed modules; ruff on changed files; `npx vitest run`).
- [ ] **Step 3:** commit; push (verify `gh auth status` active == balflee FIRST — it reverts after reboots); `vercel --prod --yes`; live verify `/reincarnation` + `/survival` banner + roadmap link.

---

## Verification
- Unit: split determinism/no-leakage; EMA carry divergence; frozen-weights season; delta clamp/renormalize; window hygiene; 3-pass artifact shape; AI rebirth notes.
- Integration: physics invariant recompute over every pass + holdout step (no artifact written on violation).
- Experiment: pass-over-pass table (pnl/deaths/coverage) + holdout-vs-baselines verdict reported as measured.
- Live: /reincarnation serves real data; /survival data byte-unchanged (only the banner added).

## Risks + honest expectations
- **Improvement across passes is a hypothesis, not a promise** — if pass 3 ≤ pass 1, that's a finding about the EMA learner and gets published as-is.
- **Holdout may show no edge** — that's the walk-forward truth surfacing; publish it.
- The anti-memorization defense is the FULL carried-state inventory (r1 H-1): 8 weight scalars + the EMA learner's ~dozen feature-keyed smoothed floats (outcome-prefixed keys refused by construction, `weight_updater.py:104-112`) + one sanitized strategy-level note — disclosed per pass in the artifact (`carry.ema_keys`). Retrospective hygiene is structural: the advisor's only input is the aggregate window (`strategy_advisor_impl.py:312-313`) AND the persisted note passes `sanitize_rebirth_note` (r1 H-2).
- Gemini retrospectives are fail-soft: a failed advisor call ⇒ carry weights unchanged + `rebirth_note=null` (never a crashed experiment).

## Revision log (plan-loop)

- **round 1 (Codex `VERDICT: HIGH=2 MEDIUM=4 LOW=1`; all seven vetted against real code, all accepted):**
  - **H-1** (the "~8 scalars" bottleneck claim understated the carried state — `WeightUpdater` carries a per-feature EMA buffer, `weight_updater.py:299-302`/`:470-474`, deliberately carried across passes by Task 2/4): honest-notes contract rewritten to the FULL inventory (8 weights + ~dozen feature-keyed EMA floats + sanitized note); artifact gains a per-pass `carry = {ema_keys, ema_size}` disclosure so the claim is auditable. Verified firsthand that EMA keys are feature NAMES (score_<engine>/pnl_usd/size_usd/bet_direction, `settlement_learner.py:75-82`) and outcome-prefixed keys are refused (`weight_updater.py:104-112`) — the carried state stays aggregate-only, which is the honest framing.
  - **H-2** (rebirth notes not schema-constrained — raw `rationale` passes through `strategy_advisor_impl.py:470-475`): added `sanitize_rebirth_note` (whitespace collapse + 500-char cap + empty⇒None) at the persistence seam + test; documented the information-flow argument (the advisor's ONLY input is `render_user_prompt(window)`, `strategy_advisor_impl.py:312-313`, and the window is aggregates-only by construction) — enforcement now structural, not assumed.
  - **M-3** (split sorted raw ISO strings, not the season's canonical `(parsed ts, market_id)` key, and allowed tied timestamps to straddle the boundary): split now parses timestamps, uses the canonical key, absorbs boundary ties into train, raises on tie-exhausted holdout; tests cover ties + strict `<` separation.
  - **M-4** (grounded contract invented a `LifeOutcome` pnl field — `survival_season.py:1147-1162` has none): contract corrected; per-life pnls computed from `recorder.steps` grouped by `SurvivalStep.life_idx` (journey-pnl derivation pattern `:2282-2288`).
  - **M-5** (the AI-variant test's `start_weights != start_weights` assertion passes from EMA learning alone): test now asserts pass-2 start == `apply_weight_deltas(pass-1 terminal, {"alpha_2": +0.04})` — `_FakeAdvisorLLM`'s delta is deterministic (`test_survival_ai_mode.py:170-182`) — plus `!=` pass-1 terminal.
  - **M-6** (journey knobs mis-grounded as export defaults — `run_survival_export` defaults are loss 1.0/breath 100/lives 50, `survival_season.py:2531-2536`; the v3 scripts pass 5.0/35/12 explicitly): grounded-contracts corrected; Task 5 runner passes the journey knobs explicitly; orchestrator defaults stay cheap for tiny-fixture tests.
  - **L-7** (artifact could claim floor physics over an unfiltered row universe): `run_reincarnation_export(entry_price_floor=0.05)` fail-closed validates every row ≥ floor and discloses the floor in `physics` + row counts in `split`.

- **round 2 (Codex truncated mid-run — exit 127, no turn.completed — but surfaced 1 concrete finding first; vetted, accepted):** the Task 4 test reconstructs `Weights(**terminal_weights)` while the body said serialize via `_weights_to_dict`, whose FLAT `alpha_0`/`beta_0` keys cannot reconstruct (`survival_season.py:2135-2146`). Artifact weights now serialize via `weights.model_dump()` (list-shaped `alpha`/`beta`; round-trips and matches `value_seed_v3.json`).

- **round 3 (Codex truncated again — exit 127, no turn.completed — mid-statement of 1 finding; vetted, accepted):** Task 4 hard-coded physics literals (`0.05`/`100.0`) in the body instead of threading knobs the way `run_survival_export` does. Signature now carries `max_bet_pnl_usd=DEFAULT_MAX_BET_PNL_USD`, `entry_price_floor=DEFAULT_ENTRY_PRICE_FLOOR`, `effective_entry_price_floor=None ⇒ mirror`; one resolved set of knobs threads into seasons + baselines + invariant + the artifact's `physics` block (new body step 0).

- **round 4 (Codex `VERDICT: HIGH=4 MEDIUM=1 LOW=1`; all six vetted against real code, all accepted):**
  - **H-1** (carry-state wording still wrong: module docstring kept "~8 scalars", and the claimed EMA keys were the poller's INPUT signals, not what the EMA stores — it stores DERIVED quality features, `weight_updater.py:400-418`): docstring + grounded contract + honest-notes now name the real keyset (`<engine>_quality` = pnl_sign·direction·score, two stream qualities, `rho_quality`) and the ~20-scalar total carried surface; artifact `carry.ema_keys` stays the audit hook.
  - **H-2** (the shared-inner divergence test was unsatisfiable on the constant-score `_dying_fixture`: a fresh EMA initializes AT the first value, `weight_updater.py:473`, so carried == fresh under constant features): test redesigned — varied-score rows via the file's own `_row(snap, score=...)` helper (signature verified `test_survival_ai_mode.py:100`) + direct assertions on the injected instance's `_ema` (empty → learned → keeps moving in season 2). The seam contract is now instance-identity + state-carry, not a trajectory-sensitivity bet.
  - **H-3** (holdout static baseline would run at default bankroll 100/breath 100, `survival_season.py:1977-1978`, while the learner runs breath 35 — non-comparable verdict): Task 4 step 4 passes `bankroll=initial_bankroll_usd` + `breath=initial_breath` explicitly, mirroring `run_survival_export` (`:2753-2760`).
  - **H-4** (invariant only recomputed settled steps — a placed-never-settled sub-floor bet would evade it while the artifact claims floor physics): Task 4 step 5 now copies the full `run_survival_export` validator (`:2381-2412`): settled recompute + baseline-point recompute + effective-floor over ALL placed bets (`recorder.placed_bets`, harvested at `:1768`) + `min_effective_entry_price` disclosure.
  - **M-5** (`recent_pnl` semantics: the prompt renderer labels it "last settled bets, $USD" (`_strategy_prompts.py:369`) — feeding per-life TOTALS would hand the advisor false semantics): `build_rebirth_window` signature reworked to `season_pnl_usd` + `recent_step_pnls` (settled-step tail, last 20); per-life pnls stay in the artifact summary only.
  - **L-6** (`apply_weight_deltas` docstring claimed "the loop's semantics" — the runtime RAISES on unknown keys and does not cap magnitude; the 0.1 cap lives in the advisor parser): docstring rewritten as deliberately fail-soft + advisor-schema-bounded, with both groundings cited.

- **round 5 (Codex truncated — exit 127, no turn.completed — mid-statement of 1 finding; vetted, accepted):** Task 4 step 4 wrote `build_archetype_curve(...same knobs...)`, over-generalizing the static-baseline surface onto the archetype builder, which has NO `bankroll`/`breath`/`value_betting` params (`survival_season.py:2013-2022` — flat $5 stake, no sizer, never consults Weights). Step 4 now spells each builder's exact signature.

- **rounds 6-7 (Codex truncated twice more — exit 127, no turn.completed; r6 produced zero findings; r7's interim assessment: "the planned Python changes are mostly grounded", then it was investigating the React test shape when cut):** I resolved the React lead firsthand — async server pages are NOT rendered in this repo's vitest (survival.test.tsx documents the convention: client body vs fixtures, loader tested separately; docs.test.tsx renders DocsPage only because it is sync). Task 6 restructured: thin async `page.tsx` + client `ReincarnationShell.tsx` carrying all markup/testids; Phase-1 banner moved into `SurvivalJourneyShell` (client) so it is assertable; roadmap link tested by direct sync render. **Convergence judgment (per the established double-cutoff rule):** three consecutive truncations with 0-1 findings each, every surfaced finding vetted + fixed, last full round's classes (carry-state honesty, fixture satisfiability, baseline comparability, invariant completeness, window semantics) all addressed — review converged.

## Self-review
- Spec coverage: split (T1), carry+freeze seams (T2), retrospective (T3), orchestrator+invariant (T4), runs (T5), page+banner+links (T6), docs+deploy (T7). ✓
- Placeholders: Task 4 body is a responsibility contract with exact call signatures rather than full inline code — acceptable because every referenced symbol is defined in Tasks 1-3 or grounded against the existing codebase with file:line; plan-loop review will pressure-test it. Task 2's `_FrozenInnerUpdater` carries an explicit verify-at-implementation note (the test is the contract). ✓
- Type consistency: `split_rows_by_time`/`apply_weight_deltas`/`build_rebirth_window`/`run_reincarnation_export` names and signatures used consistently across tasks; artifact keys (`passes[].summary`, `holdout.baselines`, `physics`) match between Task 4 and Task 6 page/tests. ✓
