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


# ========================================================================= #
# Groundhog design (v2): one incarnation = one life from market #1.
# ========================================================================= #



def _clustered_dying_fixture():
    """Six markets, the first four ENTERING before any of them settles —
    settlement lag means all four bets are placed at FULL breath, so their
    combined losses guarantee death (the sequential `_dying_fixture` lets a
    breath-capped agent shrink below min_bet after loss #1 and limp to the
    finish line alive). Markets 5-6 are the later holdout window."""
    from tests.agent.backtest.test_survival_ai_mode import _row, _snap

    snaps = [
        _snap(
            f"m{i}",
            entry_ts=f"2025-06-01T0{i}:00:00+00:00",
            end_date="2025-06-02T00:00:00+00:00",
            resolution="2025-06-02T12:00:00+00:00",
        )
        for i in range(4)
    ] + [
        _snap(
            "h1",
            entry_ts="2025-06-10T00:00:00+00:00",
            end_date="2025-06-10T12:00:00+00:00",
            resolution="2025-06-10T20:00:00+00:00",
        ),
        _snap(
            "h2",
            entry_ts="2025-06-11T00:00:00+00:00",
            end_date="2025-06-11T12:00:00+00:00",
            resolution="2025-06-11T20:00:00+00:00",
        ),
    ]
    return [_row(s) for s in snaps], snaps


def test_groundhog_caps_when_every_life_dies(tmp_path) -> None:
    """Dying fixture + low breath: every incarnation dies, the loop stops at
    the cap, survived=False, and EVERY incarnation scores ZERO (the
    permadeath-economics rule)."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    out = tmp_path / "g.json"
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=out,
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
    )
    assert out.exists()
    assert artifact["experiment"] == "reincarnation"
    assert artifact["design"] == "groundhog_day"
    assert artifact["schema_version"] == 2
    assert artifact["survived"] is False
    assert artifact["surviving_incarnation"] is None
    assert len(artifact["incarnations"]) == 2
    for k, inc in enumerate(artifact["incarnations"], start=1):
        assert inc["incarnation"] == k
        assert inc["died"] is True
        # The permadeath-economics rule: dead incarnations score zero.
        assert inc["scored_pnl"] == 0.0
        assert "pnl_at_death" in inc  # telemetry stays disclosed
        assert "markets_seen" in inc and "progress_pct" in inc
        assert "carry" in inc and isinstance(inc["carry"]["ema_keys"], list)
    # Headline: nobody survived => headline pnl is 0 (not the dead lives' sum).
    assert artifact["headline_pnl"] == 0.0
    # Numerical leg: zero treatment telemetry.
    assert artifact["rebirth"]["calls"] == 0
    # Holdout still runs (frozen) with the three baselines.
    assert artifact["holdout"]["summary"]["learning_enabled"] is False
    assert set(artifact["holdout"]["baselines"]) == {
        "static",
        "random",
        "always_favorite",
    }


def test_groundhog_terminates_on_first_surviving_life(tmp_path) -> None:
    """High breath: the first incarnation survives to the final market —
    the loop terminates immediately and the headline is THAT life's pnl."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _fragile_seed,
    )

    rows, snaps = _dying_fixture()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g.json",
        max_incarnations=5,
        train_fraction=0.5,
        initial_breath=1000.0,
        entry_price_floor=0.0,
    )
    assert artifact["survived"] is True
    assert len(artifact["incarnations"]) == 1
    last = artifact["incarnations"][-1]
    assert last["died"] is False
    assert artifact["surviving_incarnation"] == 1
    assert last["scored_pnl"] == last["pnl_at_death"]
    assert artifact["headline_pnl"] == last["scored_pnl"]
    assert last["progress_pct"] == 100.0


def test_groundhog_rejects_bad_config_and_dirty_state_root(tmp_path) -> None:
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import (
        _dying_fixture,
        _fragile_seed,
    )

    rows, snaps = _dying_fixture()
    with pytest.raises(ValueError):
        run_groundhog_export(
            rows=rows,
            snapshots=snaps,
            base_seed=_fragile_seed(),
            out_path=tmp_path / "g.json",
            max_incarnations=0,
            train_fraction=0.5,
            initial_breath=3.0,
            entry_price_floor=0.0,
        )
    # Dirty explicit state dir => fail closed (stale loop state would resume).
    root = tmp_path / "root"
    dirty = root / "inc_1"
    dirty.mkdir(parents=True)
    (dirty / "stale.jsonl").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="dirty state dir"):
        run_groundhog_export(
            rows=rows,
            snapshots=snaps,
            base_seed=_fragile_seed(),
            out_path=tmp_path / "g.json",
            max_incarnations=1,
            train_fraction=0.5,
            initial_breath=3.0,
            entry_price_floor=0.0,
            state_root=root,
        )


def test_build_death_window_carries_death_context_not_market_specifics() -> None:
    from agent.backtest.reincarnation import build_death_window

    seed_w = _w()
    window = build_death_window(
        incarnation=5,
        max_incarnations=120,
        terminal_weights=seed_w,
        seed_weights=seed_w,
        pnl_at_death=-12.5,
        recent_step_pnls=[2.5, -8.0, -7.0],
        settled=134,
        target_markets=1715,
        markets_seen=420,
        avg_stake_usd=4.87,
        win_rate=0.79,
        initial_breath=35.0,
        loss_multiplier=5.0,
        best_markets_seen=480,
        best_progress_pct=27.988,
    )
    assert window.trigger == "tick_interval"
    assert window.recent_pnl == [2.5, -8.0, -7.0]
    assert window.recent_pnl_window_usd == pytest.approx(-12.5)
    # The death summary rides the EXISTING recent_reflections field — the
    # renderer already shows it to the LLM; schema untouched.
    assert len(window.recent_reflections) == 1
    note = window.recent_reflections[0]
    # Goal framing + personal record (A5): the agent sees the finish line,
    # where it died this life, and its best life so far.
    for token in (
        "died", "134", "420", "1715", "incarnation 5", "35", "5x",
        "GOAL", "480", "28.0",
    ):
        assert token in note, token
    # Information hygiene: no market identities of any kind.
    for forbidden in ("market_id", "slug", "wta", "atp"):
        assert forbidden not in note.lower()


def test_groundhog_ai_leg_applies_deltas_at_each_death(tmp_path) -> None:
    from agent.backtest.reincarnation import (
        apply_weight_deltas,
        run_groundhog_export,
    )
    from agent.core.state import Weights
    from agent.llm.cost_guard import L3CostGuard
    from tests.agent.backtest.test_survival_ai_mode import (
        _FakeAdvisorLLM,
        _fragile_seed,
    )

    rows, snaps = _clustered_dying_fixture()
    fake = _FakeAdvisorLLM()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_ai.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
        rebirth_llm=fake,
        rebirth_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    assert artifact["provider"] == "ai"
    incs = artifact["incarnations"]
    assert incs[0]["rebirth_note"] is None  # incarnation 1 starts cold
    assert isinstance(incs[1]["rebirth_note"], str)
    # Boundary application proof (deterministic fake: alpha_2 +0.04).
    t1 = Weights(**incs[0]["terminal_weights"])
    expected = apply_weight_deltas(t1, [{"key": "alpha_2", "delta": 0.04}])
    assert incs[1]["start_weights"]["alpha"][2] == pytest.approx(
        expected.alpha[2]
    )
    # Treatment telemetry: ONE death has a successor (the final death gets
    # NO retrospective — it would feed the holdout hidden training state).
    assert artifact["rebirth"]["expected"] == 1
    assert artifact["rebirth"]["calls"] == 1
    assert artifact["rebirth"]["productive"] == 1
    assert incs[0]["advisor"] == {"called": True, "proposals": 1, "applied": 1}
    # The death summary reached the LLM prompt (any(): fake.calls[0] is the
    # PREFLIGHT probe prompt, not the death prompt).
    assert any("died" in c["prompt"] for c in fake.calls)


# ========================================================================= #
# A6 — the prayer mechanism: recorded for the gods, never carried forward.
# ========================================================================= #


def test_groundhog_ai_leg_records_prayers_but_never_carries_them(
    tmp_path,
) -> None:
    from dataclasses import dataclass, field
    from typing import Any

    from agent.backtest.reincarnation import run_groundhog_export
    from agent.llm.cost_guard import L3CostGuard
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    MARKER = "XYZZY_BREATH_SIGHT"

    @dataclass
    class _PrayerfulFakeLLM:
        """Serves BOTH schemas: advisor calls get a weight_delta proposal,
        prayer calls (schema carries 'wish') get a marked dying wish."""

        calls: list[dict[str, Any]] = field(default_factory=list)

        async def structured_call(
            self, *, model: str, prompt: str, schema: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append(
                {"model": model, "prompt": prompt, "schema": schema}
            )
            props = schema.get("properties", {})
            if "wish" in props:
                return {
                    "wish": (
                        f"please {MARKER}: let me see my own breath "
                        "before I bet"
                    )
                }
            return {
                "proposals": [
                    {
                        "kind": "weight_delta",
                        "rationale": "trim alpha_2 after the loss streak",
                        "proposed_change": {"key": "alpha_2", "delta": 0.04},
                        "expected_impact": "reduce drawdown",
                        "confidence_pct": 60,
                    }
                ]
            }

    rows, snaps = _clustered_dying_fixture()
    fake = _PrayerfulFakeLLM()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_pray.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
        rebirth_llm=fake,
        rebirth_guard=L3CostGuard(hard_cap_usd=10.0),
    )
    incs = artifact["incarnations"]
    # EVERY death prays — including the final one (no successor needed; the
    # prayer is for the gods' record, not for the next life).
    assert all(isinstance(inc["prayer"], str) for inc in incs)
    assert MARKER in incs[0]["prayer"]
    # Information-flow contract: the prayer is NEVER carried forward — no
    # later prompt (advisor window or next prayer) may contain it.
    first_prayer_idx = next(
        i for i, c in enumerate(fake.calls) if "wish" in c["schema"].get("properties", {})
    )
    for c in fake.calls[first_prayer_idx + 1 :]:
        assert MARKER not in c["prompt"], "prayer leaked into a later prompt"
    # The goal/record framing (A5) reached the death prompts.
    death_prompts = [
        c["prompt"]
        for c in fake.calls
        if "GOAL" in c["prompt"]
    ]
    assert death_prompts, "goal framing missing from death-context prompts"
    # Numerical leg untouched: prayer key exists but is null.
    art_num = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_num.json",
        max_incarnations=1,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
    )
    assert all(inc["prayer"] is None for inc in art_num["incarnations"])
