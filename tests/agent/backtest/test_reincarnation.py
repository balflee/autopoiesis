"""Phase-2 reincarnation experiment: time split, weight-delta application,
rebirth window, note sanitization, and the multi-pass export."""

from __future__ import annotations

import dataclasses

import pytest

from agent.backtest.reincarnation import split_rows_by_time
from agent.backtest.survival_season import SurvivalRow

# Reuse the survival-season test fixture for SurvivalRow construction (the
# established cross-file private-helper idiom in this suite).
from tests.agent.backtest.test_survival_season import _survival_row


def _row_at(ts: str, market_id: str) -> SurvivalRow:
    row = _survival_row(market_id=market_id, entry_price=0.5, outcome="yes")
    # _survival_row pins a fixed ts; rebuild with the desired one.
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
    rows = [
        _row_at("2024-01-01T00:00:00+00:00", "m_a"),
        _row_at("2025-01-01T00:00:00+00:00", "m_b"),
    ]
    with pytest.raises(ValueError):
        split_rows_by_time(rows, train_fraction=0.0)
    with pytest.raises(ValueError):
        split_rows_by_time(rows, train_fraction=1.0)
    with pytest.raises(ValueError):
        split_rows_by_time([rows[0]], train_fraction=0.5)
    # Tie-absorption exhausting the holdout is degenerate, not silent.
    tied = [_row_at("2025-01-01T00:00:00+00:00", f"m_{i}") for i in range(4)]
    with pytest.raises(ValueError):
        split_rows_by_time(tied, train_fraction=0.5)


# =========================================================================== #
# Rebirth retrospective seams — delta application, window builder, sanitizer.
# =========================================================================== #


def _w(
    *,
    w_r: float = 0.5,
    alpha: tuple[float, float, float] = (1 / 3, 1 / 3, 1 / 3),
    beta: tuple[float, float] = (0.5, 0.5),
    rho: float = 0.5,
):
    from agent.core.state import Weights

    return Weights(
        w_r=w_r, w_s=1.0 - w_r, alpha=list(alpha), beta=list(beta), rho=rho
    )


def test_apply_weight_deltas_clamps_and_renormalizes() -> None:
    from agent.backtest.reincarnation import apply_weight_deltas

    w = _w()
    out = apply_weight_deltas(
        w,
        [
            {"key": "w_r", "delta": 0.1},
            {"key": "alpha_2", "delta": 0.1},
            {"key": "rho", "delta": -0.2},
        ],
    )
    assert out.w_r == pytest.approx(0.6)
    assert out.w_s == pytest.approx(0.4)
    assert sum(out.alpha) == pytest.approx(1.0)
    assert out.alpha[2] > w.alpha[2]
    # |delta| capped at 0.1 — the requested -0.2 applies as -0.1.
    assert out.rho == pytest.approx(0.4)


def test_apply_weight_deltas_skips_invalid_and_caps_magnitude() -> None:
    from agent.backtest.reincarnation import apply_weight_deltas

    w = _w()
    out = apply_weight_deltas(
        w,
        [
            {"key": "nonsense", "delta": 0.4},  # unknown key: skipped
            {"key": "beta_0", "delta": 9.0},  # |delta| capped to 0.1
            {"key": "rho", "delta": "bad"},  # non-numeric: skipped
            {"key": "rho", "delta": True},  # bool: skipped, not coerced
        ],
    )
    assert out.beta[0] == pytest.approx(0.6)
    assert out.beta[1] == pytest.approx(0.4)
    assert out.rho == pytest.approx(w.rho)


def test_build_rebirth_window_is_strategy_level_only() -> None:
    from agent.backtest.reincarnation import build_rebirth_window

    seed_w = _w()
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
    # recent_pnl's REAL semantics (prompt renderer + dataclass) are "last
    # settled bets, $USD" — it receives actual step pnls, never life totals.
    assert window.recent_pnl == [2.5, -8.0, 1.0]
    assert window.recent_pnl_window_usd == pytest.approx(-13.5)
    assert window.tick_count == 120
    # Hygiene: the window carries ONLY aggregates — no market ids/names.
    assert "market" not in window.agent_id
    assert window.agent_id == "rebirth-pass-1-deaths-2"


def test_sanitize_rebirth_note_collapses_and_caps() -> None:
    """The persisted note is enforced-clean — whitespace collapsed, hard
    length cap — never raw LLM text."""
    from agent.backtest.reincarnation import sanitize_rebirth_note

    assert sanitize_rebirth_note("  a \n\n b\t c  ") == "a b c"
    long = "x" * 2000
    out = sanitize_rebirth_note(long)
    assert out is not None and len(out) <= 500
    assert sanitize_rebirth_note("") is None
    assert sanitize_rebirth_note("   \n ") is None


# =========================================================================== #
# run_reincarnation_export — the multi-pass orchestrator.
# =========================================================================== #


def test_reincarnation_export_three_passes_plus_frozen_holdout(tmp_path) -> None:
    from agent.backtest.reincarnation import run_reincarnation_export
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _fragile_seed,
    )

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
        entry_price_floor=0.0,
    )
    assert out.exists()
    assert artifact["experiment"] == "reincarnation"
    assert artifact["provider"] == "numerical"
    assert len(artifact["passes"]) == 3
    for i, p in enumerate(artifact["passes"], start=1):
        assert p["pass"] == i
        for key in ("pnl", "deaths", "lives", "settled", "coverage_pct", "win_rate"):
            assert key in p["summary"], key
        assert p["curve"], "each pass carries a cumulative curve"
        assert "start_weights" in p and "terminal_weights" in p
        assert "carry" in p and isinstance(p["carry"]["ema_keys"], list)
        # Numerical variant: no LLM, no notes.
        assert p["rebirth_note"] is None
    h = artifact["holdout"]
    assert h["summary"]["learning_enabled"] is False
    assert set(h["baselines"]) == {"static", "random", "always_favorite"}
    # Physics disclosure (v3 throughout).
    assert artifact["physics"]["side_correct_pricing"] is True
    assert artifact["physics"]["value_betting"] is True
    # The split is chronological and disjoint.
    assert (
        artifact["split"]["train_rows"] + artifact["split"]["holdout_rows"]
        == len(rows)
    )
    assert artifact["split"]["train_end_ts"] < artifact["split"]["holdout_start_ts"]


def test_reincarnation_ai_variant_records_rebirth_notes(tmp_path) -> None:
    from agent.backtest.reincarnation import (
        apply_weight_deltas,
        run_reincarnation_export,
    )
    from agent.core.state import Weights
    from agent.llm.cost_guard import L3CostGuard
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _FakeAdvisorLLM,
        _fragile_seed,
    )

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
        entry_price_floor=0.0,
        rebirth_llm=_FakeAdvisorLLM(),
        rebirth_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    assert artifact["provider"] == "ai"
    # Pass boundaries: passes-1 retrospectives (after pass 1, before pass 2).
    notes = [p.get("rebirth_note") for p in artifact["passes"]]
    assert notes[0] is None  # pass 1 starts cold
    assert isinstance(notes[1], str) and len(notes[1]) > 0
    # EMA learning alone moves start-vs-start weights, so that proves nothing.
    # The PROOF of boundary application: pass 2 starts EXACTLY at
    # apply_weight_deltas(pass-1 terminal, the fake's deterministic delta) —
    # _FakeAdvisorLLM always proposes {"key": "alpha_2", "delta": 0.04}.
    p1_terminal = Weights(**artifact["passes"][0]["terminal_weights"])
    expected = apply_weight_deltas(
        p1_terminal, [{"key": "alpha_2", "delta": 0.04}]
    )
    got = artifact["passes"][1]["start_weights"]
    assert got["alpha"][2] == pytest.approx(expected.alpha[2])
    assert got != artifact["passes"][0]["terminal_weights"]
