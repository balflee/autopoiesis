"""Phase-2 reincarnation experiment: time split, weight-delta application,
rebirth window, note sanitization, and the multi-pass export."""

from __future__ import annotations

import dataclasses
import itertools

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
    """r1 M-3: equal-time markets must never straddle the boundary â€” ties at
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
# Rebirth retrospective seams â€” delta application, window builder, sanitizer.
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
    # |delta| capped at 0.1 â€” the requested -0.2 applies as -0.1.
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
    # settled bets, $USD" â€” it receives actual step pnls, never life totals.
    assert window.recent_pnl == [2.5, -8.0, 1.0]
    assert window.recent_pnl_window_usd == pytest.approx(-13.5)
    assert window.tick_count == 120
    # Hygiene: the window carries ONLY aggregates â€” no market ids/names.
    assert "market" not in window.agent_id
    assert window.agent_id == "rebirth-pass-1-deaths-2"


def test_sanitize_rebirth_note_collapses_and_caps() -> None:
    """The persisted note is enforced-clean â€” whitespace collapsed, hard
    length cap â€” never raw LLM text."""
    from agent.backtest.reincarnation import sanitize_rebirth_note

    assert sanitize_rebirth_note("  a \n\n b\t c  ") == "a b c"
    long = "x" * 2000
    out = sanitize_rebirth_note(long)
    assert out is not None and len(out) <= 500
    assert sanitize_rebirth_note("") is None
    assert sanitize_rebirth_note("   \n ") is None


# =========================================================================== #
# run_reincarnation_export â€” the multi-pass orchestrator.
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
    # apply_weight_deltas(pass-1 terminal, the fake's deterministic delta) â€”
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
    """Six markets, the first four ENTERING before any of them settles â€”
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
    """High breath: the first incarnation survives to the final market â€”
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
    # The death summary rides the EXISTING recent_reflections field â€” the
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
    # NO retrospective â€” it would feed the holdout hidden training state).
    assert artifact["rebirth"]["expected"] == 1
    assert artifact["rebirth"]["calls"] == 1
    assert artifact["rebirth"]["productive"] == 1
    assert incs[0]["advisor"] == {"called": True, "proposals": 1, "applied": 1}
    # The death summary reached the LLM prompt (any(): fake.calls[0] is the
    # PREFLIGHT probe prompt, not the death prompt).
    assert any("died" in c["prompt"] for c in fake.calls)


# ========================================================================= #
# A6 â€” the prayer mechanism: recorded for the gods, never carried forward.
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
    # EVERY death prays â€” including the final one (no successor needed; the
    # prayer is for the gods' record, not for the next life).
    assert all(isinstance(inc["prayer"], str) for inc in incs)
    assert MARKER in incs[0]["prayer"]
    # Information-flow contract: the prayer is NEVER carried forward â€” no
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


# ========================================================================= #
# A7 â€” the tribute mechanism: money for breath, the gods always get paid.
# ========================================================================= #


def test_tribute_success_probability_curve() -> None:
    from agent.runtime.tribute import tribute_success_probability

    assert tribute_success_probability(500.0) == pytest.approx(0.30)
    assert tribute_success_probability(1250.0) == pytest.approx(0.65)
    assert tribute_success_probability(2000.0) == pytest.approx(0.99)
    assert tribute_success_probability(5000.0) == pytest.approx(0.99)  # capped
    with pytest.raises(ValueError):
        tribute_success_probability(499.99)  # below the gods' floor


def test_reflex_tribute_saves_a_rich_dying_agent(tmp_path) -> None:
    """Clustered-death fixture + a RICH bankroll + the reflex policy: the
    deathbed tribute fires, the grant lands on the CANONICAL (chain) breath
    so it survives the next tick's re-read, the life SURVIVES, and the
    recorder logged exactly ONE event (a non-durable grant would re-trigger
    the altar every tick and drain the bank)."""
    import json as _json
    import random

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from agent.runtime.tribute import ReflexTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    # A raw season schedules EVERY row it is handed â€” slice to the 4-market
    # death cluster so post-tribute survival is structural, not luck.
    rows, snaps = rows[:4], snaps[:4]
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=recorder,
        tribute_policy=ReflexTributePolicy(),
        tribute_rng=random.Random(0),  # p=0.99: first draw succeeds
        tribute_breath=35.0,
    )
    life = result.lives[0]
    assert life.died is False, "the tribute must have saved the life"
    assert len(recorder.tributes) == 1, "durability: ONE altar visit only"
    ev = recorder.tributes[0]
    assert ev["amount_usd"] == 2000.0  # reflex pays min(2000, bankroll)
    assert ev["success"] is True
    assert ev["breath_after"] == 35.0
    assert ev["life_idx"] == 0
    # The grant is durable on the canonical channel: the life ends breathing.
    assert life.final_breath > 0.0
    # The gods got paid: the loop's bankroll dropped by the tribute.
    assert ev["bankroll_after"] <= 3000.0 - 2000.0 + 100.0  # (+pnl wiggle)
    # Durability-on-disk: the post-tribute snapshot carries the DEDUCTED
    # bankroll â€” a same-dir re-entry can never refund the gods.
    snap = _json.loads(
        (tmp_path / "s" / "life_0" / "agent_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert snap["bankroll_usd"] <= 3000.0 - 2000.0 + 100.0
    # Ledger split: the dying tick's DecisionRecord (bet domain, append-only)
    # is PRE-altar; the snapshot is post-altar.
    last_decision = _json.loads(
        (tmp_path / "s" / "life_0" / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()[-1]
    )
    assert last_decision["bankroll_usd_after"] > snap["bankroll_usd"]


def test_failed_tribute_kills_and_the_gods_keep_the_money(tmp_path) -> None:
    import random

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from agent.runtime.tribute import ReflexTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    class _CursedDice(random.Random):
        """God-dice that always roll a failure."""

        def random(self) -> float:
            return 0.999999

    rows, snaps = _clustered_dying_fixture()
    rows, snaps = rows[:4], snaps[:4]
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=recorder,
        tribute_policy=ReflexTributePolicy(),
        tribute_rng=_CursedDice(),
        tribute_breath=35.0,
    )
    assert result.lives[0].died is True
    ev = recorder.tributes[0]
    assert ev["success"] is False
    # Greedy gods: the money is GONE even though the grant failed.
    assert ev["bankroll_after"] <= 3000.0 - 2000.0 + 100.0


def test_poor_agent_cannot_tribute_and_no_policy_is_byte_identical(
    tmp_path,
) -> None:
    import random

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from agent.runtime.tribute import ReflexTributePolicy
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    rows, snaps = rows[:4], snaps[:4]
    # Poor agent ($100 < $500 floor): the reflex returns None â‡’ death.
    rec_poor = SurvivalRecorder(rows=rows)
    poor = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "p",
        initial_breath=3.0,
        max_lives=1,
        recorder=rec_poor,
        tribute_policy=ReflexTributePolicy(),
        tribute_rng=random.Random(0),
    )
    assert poor.lives[0].died is True
    assert rec_poor.tributes == []
    # No policy (the default): byte-identical season to today.
    rec_a = SurvivalRecorder(rows=rows)
    base = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "a",
        initial_breath=3.0,
        max_lives=1,
        recorder=rec_a,
    )
    assert poor.lives[0].terminal_weights == base.lives[0].terminal_weights
    assert [s.pnl_usd for s in rec_poor.steps] == [
        s.pnl_usd for s in rec_a.steps
    ]


def test_malicious_tribute_policy_cannot_poison_the_bankroll(
    tmp_path,
) -> None:
    """The altar validates at the world-rule boundary: strings, bools, and
    NaN offers are refused with NO deduction; the agent dies normally."""
    import random
    from typing import Any

    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    class _MaliciousPolicy:
        def __init__(self, offer: Any) -> None:
            self._offer = offer

        async def on_dying(self, **_: Any) -> Any:
            return self._offer

    rows, snaps = _clustered_dying_fixture()
    rows, snaps = rows[:4], snaps[:4]
    for bad in ("2000", True, float("nan")):
        recorder = SurvivalRecorder(rows=rows)
        result = run_survival_season(
            rows=rows,
            snapshots=snaps,
            seed=_fragile_seed(),
            state_root=tmp_path / f"m_{type(bad).__name__}",
            initial_breath=3.0,
            initial_bankroll_usd=3000.0,
            max_lives=1,
            recorder=recorder,
            tribute_policy=_MaliciousPolicy(bad),  # type: ignore[arg-type]
            tribute_rng=random.Random(0),
        )
        assert result.lives[0].died is True
        assert recorder.tributes == [], f"no altar visit for {bad!r}"


def test_llm_tribute_policy_offers_and_silence_means_death(tmp_path) -> None:
    import random
    from dataclasses import dataclass, field
    from typing import Any

    from agent.backtest.reincarnation import LLMTributePolicy
    from agent.backtest.survival_season import (
        SurvivalRecorder,
        run_survival_season,
    )
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    @dataclass
    class _DevoutLLM:
        calls: list[dict[str, Any]] = field(default_factory=list)

        async def structured_call(
            self, *, model: str, prompt: str, schema: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append({"prompt": prompt, "schema": schema})
            return {"offer": True, "amount_usd": 2000.0}

    rows, snaps = _clustered_dying_fixture()
    rows, snaps = rows[:4], snaps[:4]
    fake = _DevoutLLM()
    recorder = SurvivalRecorder(rows=rows)
    policy = LLMTributePolicy(
        llm=fake,
        model="",
        target_markets=4,
        max_incarnations=2,
        incarnation=1,
    )
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=recorder,
        tribute_policy=policy,
        tribute_rng=random.Random(0),
    )
    assert result.lives[0].died is False
    assert recorder.tributes and recorder.tributes[0]["amount_usd"] == 2000.0
    assert policy.telemetry["calls"] == 1
    assert policy.telemetry["offers"] == 1
    # The deathbed prompt carried the stakes: pricing + forfeiture framing.
    prompt = fake.calls[0]["prompt"]
    for token in ("$500", "$2,000", "forfeit", "bank"):
        assert token in prompt, token

    # Silence (LLM failure) = death â€” never the reflex.
    class _MuteLLM:
        async def structured_call(self, **_: Any) -> dict[str, Any]:
            raise TimeoutError("the line to the gods is down")

    rec2 = SurvivalRecorder(rows=rows)
    mute_policy = LLMTributePolicy(
        llm=_MuteLLM(),
        model="",
        target_markets=4,
        max_incarnations=2,
        incarnation=1,
    )
    result2 = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "s2",
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        max_lives=1,
        recorder=rec2,
        tribute_policy=mute_policy,
        tribute_rng=random.Random(0),
    )
    assert result2.lives[0].died is True
    assert rec2.tributes == []
    assert mute_policy.telemetry["failures"] == 1


def test_llm_tribute_policy_refusal_and_malformed_are_distinct() -> None:
    """A valid {"offer": false} is a CHOICE to die (refusals); junk shapes
    are malformed; both return None, with distinct telemetry."""
    import asyncio
    from typing import Any

    from agent.backtest.reincarnation import LLMTributePolicy

    class _ScriptedLLM:
        def __init__(self, response: dict[str, Any]) -> None:
            self._response = response

        async def structured_call(self, **_: Any) -> dict[str, Any]:
            return self._response

    cases: list[tuple[dict[str, Any], str]] = [
        ({"offer": False, "amount_usd": 2000.0}, "refusals"),
        ({"offer": "yes", "amount_usd": "2000"}, "malformed"),
        ({"offer": True, "amount_usd": True}, "malformed"),
        ({"offer": True, "amount_usd": float("nan")}, "malformed"),
        ({}, "malformed"),
    ]
    for response, bucket in cases:
        policy = LLMTributePolicy(
            llm=_ScriptedLLM(response),
            model="",
            target_markets=100,
            max_incarnations=5,
            incarnation=2,
        )
        out = asyncio.run(
            policy.on_dying(tick=10, breath=0.0, bankroll_usd=3000.0)
        )
        assert out is None, response
        assert policy.telemetry[bucket] == 1, (response, bucket)


def test_groundhog_tribute_reflex_buys_the_finish_line(tmp_path) -> None:
    """Rich groundhog + tribute: the reflex saves the death, the incarnation
    SURVIVES, the headline is NET of the gods' take, and the accounting
    closes (gods_revenue == sum of all offerings)."""
    import random

    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g.json",
        max_incarnations=3,
        train_fraction=0.67,
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        entry_price_floor=0.0,
        tribute=True,
        tribute_rng_factory=lambda k: random.Random(0),
    )
    assert artifact["survived"] is True
    assert artifact["surviving_incarnation"] == 1
    inc = artifact["incarnations"][0]
    assert inc["tributes"] and inc["tributes"][0]["amount_usd"] == 2000.0
    assert "life_idx" not in inc["tributes"][0]
    assert inc["tributes_paid"] == 2000.0
    assert inc["pnl_net"] == pytest.approx(inc["pnl_at_death"] - 2000.0)
    assert inc["scored_pnl"] == pytest.approx(inc["pnl_net"])
    assert artifact["headline_pnl"] == pytest.approx(inc["pnl_net"])
    assert artifact["gods_revenue"] == 2000.0
    assert artifact["gods_revenue_best_incarnation"] == 2000.0
    # The user's metric: revival earnings = gross - pnl at the first altar.
    assert inc["revival_earnings"] == pytest.approx(
        inc["pnl_at_death"] - inc["tributes"][0]["pnl_at_event"]
    )
    assert artifact["revival_earnings_total"] == pytest.approx(
        sum(i.get("revival_earnings") or 0.0 for i in artifact["incarnations"])
    )
    assert artifact["tribute"]["enabled"] is True
    # Numerical leg: the reflex is scripted; zero LLM telemetry.
    assert artifact["tribute"]["llm"]["calls"] == 0


def test_groundhog_tribute_fail_then_save_accounting(tmp_path) -> None:
    """r4 M-2: cursed dice for incarnation 1 (pays AND dies), lucky dice for
    incarnation 2 (pays and survives) â€” gods_revenue counts BOTH offerings,
    the dead incarnation scores zero despite having donated."""
    import random

    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    class _CursedDice(random.Random):
        def random(self) -> float:
            return 0.999999

    rows, snaps = _clustered_dying_fixture()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        initial_bankroll_usd=3000.0,
        entry_price_floor=0.0,
        tribute=True,
        tribute_rng_factory=(
            lambda k: _CursedDice() if k == 1 else random.Random(0)
        ),
    )
    inc1, inc2 = artifact["incarnations"]
    assert inc1["died"] is True
    assert inc1["tributes"][0]["success"] is False
    assert inc1["tributes_paid"] == 2000.0
    assert inc1["scored_pnl"] == 0.0  # donated AND died â€” keeps nothing
    assert inc2["died"] is False
    assert inc2["tributes_paid"] == 2000.0
    assert artifact["survived"] is True
    assert artifact["surviving_incarnation"] == 2
    assert artifact["gods_revenue"] == 4000.0
    assert artifact["gods_revenue_best_incarnation"] == 2000.0
    assert artifact["headline_pnl"] == pytest.approx(inc2["pnl_net"])


def test_groundhog_default_has_no_tribute_keys(tmp_path) -> None:
    """Library default OFF: omitted `tribute` produces the v2 artifact shape
    (no tribute keys anywhere) â€” prior consumers stay byte-compatible."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g.json",
        max_incarnations=1,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
    )
    assert "gods_revenue" not in artifact
    assert "tribute" not in artifact
    for inc in artifact["incarnations"]:
        assert "tributes" not in inc
        assert "pnl_net" not in inc


# ========================================================================= #
# A9 Task 2 — genome on StrategyConfig + boundary-only advisor vocabulary.
# ========================================================================= #


def test_apply_genome_deltas_clamps_and_skips() -> None:
    from agent.backtest.reincarnation import (
        GENOME_KEYS,
        GENOME_MIN_BREATH_RISK_PCT,
        apply_genome_deltas,
    )
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    seed = _fragile_seed()
    out = apply_genome_deltas(
        seed,
        [
            {"key": "min_edge", "delta": 0.04},
            {"key": "gate_storm_sensitivity", "delta": 0.1},
        ],
    )
    assert out.min_edge == pytest.approx(min(0.5, seed.min_edge + 0.04))
    assert out.gate_storm_sensitivity == pytest.approx(0.1)
    # Weights untouched by genome application.
    assert out.weights == seed.weights

    # |delta| capped at 0.1 before clamping.
    out2 = apply_genome_deltas(seed, [{"key": "kappa", "delta": 0.7}])
    assert out2.kappa == pytest.approx(min(1.0, seed.kappa + 0.1))

    # Unknown keys, the EXCLUDED min_bet_size_usd (r8 M-3), and non-numeric
    # deltas are SKIPPED fail-soft — never a crash, never an effect.
    out3 = apply_genome_deltas(
        seed,
        [
            {"key": "min_bet_size_usd", "delta": 0.1},
            {"key": "w_r", "delta": 0.1},
            {"key": "no_such_knob", "delta": 0.1},
            {"key": "min_edge", "delta": True},
        ],
    )
    assert out3 == seed
    assert "min_bet_size_usd" not in GENOME_KEYS
    assert GENOME_MIN_BREATH_RISK_PCT == pytest.approx(0.05)


def test_genome_breath_risk_floor_keeps_engine_constructible() -> None:
    """r11 M-2: repeated −0.1 deltas drive max_breath_risk_pct to the
    NAMED floor (0.05), never to 0 — the engine ctor must stay valid."""
    from agent.backtest.reincarnation import (
        GENOME_MIN_BREATH_RISK_PCT,
        apply_genome_deltas,
    )
    from agent.backtest.survival_season import _decision_engine_from_seed
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    seed = _fragile_seed()
    for _ in range(20):
        seed = apply_genome_deltas(
            seed, [{"key": "max_breath_risk_pct", "delta": -0.1}]
        )
    assert seed.max_breath_risk_pct == pytest.approx(
        GENOME_MIN_BREATH_RISK_PCT
    )
    engine = _decision_engine_from_seed(seed)  # must NOT raise
    assert engine is not None


def test_classify_proposals_single_classifier() -> None:
    """r10 M-2: ONE classifier feeds the apply path AND every applied
    count — fusion/genome/skipped never diverge."""
    from agent.backtest.reincarnation import classify_proposals

    deltas = [
        {"key": "alpha_2", "delta": 0.04},
        {"key": "min_edge", "delta": 0.05},
        {"key": "gate_storm_sensitivity", "delta": 0.1},
        {"key": "min_bet_size_usd", "delta": 0.1},
        {"key": "nope", "delta": 0.1},
        {"key": "rho", "delta": True},
    ]
    on = classify_proposals(deltas, genome_enabled=True)
    assert [d["key"] for d in on.fusion] == ["alpha_2"]
    assert [d["key"] for d in on.genome] == [
        "min_edge", "gate_storm_sensitivity",
    ]
    assert len(on.skipped) == 3

    off = classify_proposals(deltas, genome_enabled=False)
    assert [d["key"] for d in off.fusion] == ["alpha_2"]
    assert off.genome == []
    assert len(off.skipped) == 5


def test_genome_schema_enum_matches_parser_keys() -> None:
    """r3 M-5: the rendered schema enum and the strict parser's allowed
    keys are the SAME VALUE in genome mode; min_bet_size_usd is absent."""
    from agent.backtest.reincarnation import _DELTA_KEYS, EXTENDED_REBIRTH_KEYS
    from agent.engines._strategy_prompts import (
        WEIGHT_DELTA_KEYS,
        render_weight_delta_schema,
        render_weight_delta_system_prompt,
    )

    assert EXTENDED_REBIRTH_KEYS[: len(_DELTA_KEYS)] == _DELTA_KEYS
    schema = render_weight_delta_schema(EXTENDED_REBIRTH_KEYS)
    enum = schema["properties"]["proposals"]["items"]["properties"][
        "proposed_change"
    ]["properties"]["key"]["enum"]
    assert tuple(enum) == EXTENDED_REBIRTH_KEYS
    assert "min_bet_size_usd" not in enum
    assert "gate_storm_sensitivity" in enum
    # The default-vocabulary prompt renders the module constant VERBATIM.
    from agent.engines._strategy_prompts import WEIGHT_DELTA_SYSTEM_PROMPT

    assert (
        render_weight_delta_system_prompt(WEIGHT_DELTA_KEYS)
        is WEIGHT_DELTA_SYSTEM_PROMPT
    )
    # The extended prompt names every key + the neutral γ description.
    prompt = render_weight_delta_system_prompt(EXTENDED_REBIRTH_KEYS)
    assert "gate_storm_sensitivity" in prompt
    assert "positive tightens in storms, negative loosens" in prompt


def test_groundhog_storm_genome_end_to_end(tmp_path) -> None:
    """r9 M-3 end-to-end: a fake advisor response carrying BOTH an
    alpha_2 (fusion) and a gate_storm_sensitivity (genome) delta flows
    through the groundhog rebirth boundary — each lands on its home
    (Weights vs seed), the holdout receives the MUTATED genome, and the
    falsification_metric field is persisted."""
    from dataclasses import dataclass, field
    from typing import Any

    from agent.backtest.reincarnation import run_groundhog_export
    from agent.llm.cost_guard import L3CostGuard
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    @dataclass
    class _GenomeFakeLLM:
        calls: list[dict[str, Any]] = field(default_factory=list)

        async def structured_call(
            self, *, model: str, prompt: str, schema: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append(
                {"model": model, "prompt": prompt, "schema": schema}
            )
            props = schema.get("properties", {})
            if "wish" in props:
                return {"wish": "let me feel the storm"}
            return {
                "proposals": [
                    {
                        "kind": "weight_delta",
                        "rationale": "trim alpha_2 after the loss streak",
                        "proposed_change": {"key": "alpha_2", "delta": 0.04},
                        "expected_impact": "reduce drawdown",
                        "confidence_pct": 60,
                    },
                    {
                        "kind": "weight_delta",
                        "rationale": (
                            "deaths cluster in storms; tighten the gate "
                            "when storm is high"
                        ),
                        "proposed_change": {
                            "key": "gate_storm_sensitivity",
                            "delta": 0.1,
                        },
                        "expected_impact": "fewer storm bets",
                        "confidence_pct": 55,
                    },
                ]
            }

    rows, snaps = _clustered_dying_fixture()
    fake = _GenomeFakeLLM()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_genome.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
        rebirth_llm=fake,
        rebirth_guard=L3CostGuard(hard_cap_usd=10.0),
        storm=True,
    )
    incs = artifact["incarnations"]
    # The genome delta landed on the carried seed, visible at inc 2 start.
    assert incs[0]["start_genome"]["gate_storm_sensitivity"] == 0.0
    assert incs[0]["carry_genome_after_advice"][
        "gate_storm_sensitivity"
    ] == pytest.approx(0.1)
    assert incs[1]["start_genome"]["gate_storm_sensitivity"] == pytest.approx(
        0.1
    )
    # The fusion delta landed on Weights (its home), not the genome.
    assert "alpha_2" not in incs[0]["start_genome"]
    # BOTH deltas counted by the ONE classifier (r10 M-2).
    assert incs[0]["advisor"] == {"called": True, "proposals": 2, "applied": 2}
    # The schema the advisor saw carries the extended enum.
    advisor_schemas = [
        c["schema"]
        for c in fake.calls
        if "proposals" in c["schema"].get("properties", {})
    ]
    enums = [
        s["properties"]["proposals"]["items"]["properties"][
            "proposed_change"
        ]["properties"]["key"]["enum"]
        for s in advisor_schemas
    ]
    assert all("gate_storm_sensitivity" in e for e in enums)
    # The holdout consumed the MUTATED genome (the holdout trap, r1 M-6).
    assert artifact["holdout"]["start_genome"][
        "gate_storm_sensitivity"
    ] == pytest.approx(0.1)
    # Pre-registered falsification metric (r8 M-4): persisted, evaluable
    # only with >= 3 productive death-boundary calls (here: 1).
    fm = artifact["falsification_metric"]
    assert fm["key"] == "gate_storm_sensitivity"
    assert fm["value"] == pytest.approx(0.1)
    assert fm["productive_calls"] == 1
    assert fm["min_productive_required"] == 3
    assert fm["evaluable"] is False


def test_groundhog_flag_off_has_no_genome_keys(tmp_path) -> None:
    """r4 H-1 keyset identity: with the kit OFF the artifact carries NO
    regime fields anywhere — incarnations, holdout, top level."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    artifact = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_off.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
    )
    assert "falsification_metric" not in artifact
    assert "start_genome" not in artifact["holdout"]
    for inc in artifact["incarnations"]:
        assert "start_genome" not in inc
        assert "terminal_genome_before_advice" not in inc
        assert "carry_genome_after_advice" not in inc


# ========================================================================= #
# A9 Task 3 — the K6 counterfactual ledger + death-window wiring.
# ========================================================================= #


def _stamped_step(
    *,
    pnl: float,
    storm: float | None,
    edge: float = 0.08,
    min_edge: float = 0.05,
    eff_min_edge: float = 0.05,
    market_id: str = "SECRET-MKT-42",
):
    from agent.backtest.survival_season import SurvivalStep
    from agent.core.state import Weights

    w = Weights(
        w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5],
        rho=0.5,
    )
    return SurvivalStep(
        life_idx=0,
        market_id=market_id,
        slug="secret-slug",
        players=("Anon A", "Anon B"),
        surface="clay",
        entry_price=0.4,
        outcome="yes",
        winning_price=1.0,
        side="YES",
        size_usd=5.0,
        pnl_usd=pnl,
        signal_scores={},
        weights_before=w,
        weights_after=w,
        breath_after=30.0,
        bankroll_after=100.0,
        cum_pnl=pnl,
        running_win_rate=0.5,
        storm_at_bet=storm,
        edge_at_bet=None if storm is None else edge,
        min_edge_at_bet=None if storm is None else min_edge,
        gamma_at_bet=None if storm is None else 0.0,
        eff_min_edge_at_bet=None if storm is None else eff_min_edge,
    )


def test_regime_ledger_arithmetic() -> None:
    from agent.backtest.reincarnation import build_regime_ledger

    steps = [
        # HIGH storm losers (storm 1.0): candidate eff' = 0.05 + γ'·1.0.
        _stamped_step(pnl=-5.0, storm=1.0),   # γ'=0.05 ⇒ 0.10 > 0.08 ⇒ blocked
        _stamped_step(pnl=-7.0, storm=1.0),
        # LOW storm winner (storm 0.0): eff' = 0.05 ⇒ 0.08 ≥ 0.05 ⇒ NOT blocked.
        _stamped_step(pnl=4.0, storm=0.0),
        # Not computable for every γ' (the policy at bet already had a
        # TIGHTER gate than the candidate): eff_min_edge_at_bet 0.30.
        _stamped_step(pnl=-2.0, storm=0.5, eff_min_edge=0.30),
    ]
    ledger = build_regime_ledger(steps, loss_multiplier=5.0)
    assert ledger is not None
    split = ledger["storm_split"]
    # storm 0.5 sits ON the threshold => HIGH (>=).
    assert split["high"]["bets"] == 3
    assert split["high"]["pnl"] == pytest.approx(-14.0)
    assert split["high"]["breath_delta"] == pytest.approx(-70.0)  # 5x
    assert split["low"]["bets"] == 1
    assert split["low"]["pnl"] == pytest.approx(4.0)
    assert split["low"]["breath_delta"] == pytest.approx(4.0)

    by_gamma = {c["gamma"]: c for c in ledger["gate_counterfactuals"]}
    c05 = by_gamma[0.05]
    # storm-1.0 bets: eff'=0.10 > |0.08| ⇒ blocked (2, pnl −12);
    # storm-0.0 winner: eff'=0.05 ≤ 0.08 ⇒ admitted;
    # storm-0.5 step: eff'=0.075 < its eff_min_edge_at_bet 0.30 ⇒ not computable.
    assert c05["blocked"] == 2
    assert c05["blocked_pnl"] == pytest.approx(-12.0)
    assert c05["computable"] == 3
    assert c05["not_computable"] == 1
    # γ'=0.2: storm-0 winner eff'=0.05 still admits; storm-0.5 candidate
    # eff'=0.15 < 0.30 ⇒ still not computable; high-storm blocked.
    c20 = by_gamma[0.2]
    assert c20["blocked"] == 2 and c20["not_computable"] == 1


def test_regime_ledger_none_without_stamps() -> None:
    from agent.backtest.reincarnation import build_regime_ledger

    steps = [_stamped_step(pnl=-5.0, storm=None)]
    assert build_regime_ledger(steps, loss_multiplier=5.0) is None
    assert build_regime_ledger([], loss_multiplier=5.0) is None


def test_death_window_renders_ledger_genome_and_tribute() -> None:
    """The death summary carries the genome readout (every advisable key
    + value), the storm split, the tightening-only counterfactual with
    the loosening caveat, and the K5 tribute line — but NEVER a market
    identity (information hygiene)."""
    from agent.backtest.reincarnation import (
        GENOME_KEYS,
        build_death_window,
        build_regime_ledger,
    )
    from agent.core.state import Weights

    w = Weights(
        w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5],
        rho=0.5,
    )
    steps = [
        _stamped_step(pnl=-5.0, storm=1.0),
        _stamped_step(pnl=4.0, storm=0.0),
    ]
    ledger = build_regime_ledger(steps, loss_multiplier=5.0)
    genome = {
        "min_edge": 0.035,
        "max_breath_risk_pct": 0.95,
        "min_confidence": 0.08,
        "kappa": 0.49,
        "gate_storm_sensitivity": 0.0,
        "risk_storm_sensitivity": 0.0,
    }
    window = build_death_window(
        incarnation=3,
        max_incarnations=20,
        terminal_weights=w,
        seed_weights=w,
        pnl_at_death=-42.0,
        recent_step_pnls=[-5.0, 4.0],
        settled=2,
        target_markets=100,
        markets_seen=40,
        avg_stake_usd=5.0,
        win_rate=0.5,
        initial_breath=35.0,
        loss_multiplier=5.0,
        best_markets_seen=60,
        best_progress_pct=60.0,
        genome=genome,
        regime_ledger=ledger,
        tribute_summary=(
            "this life you bought 2 revival(s) for $1000 and earned "
            "$12.00 after the first altar."
        ),
    )
    text = window.recent_reflections[0]
    # Genome readout: every advisable key AND its value appear (r5 H-1).
    for key in GENOME_KEYS:
        assert key in text
    assert "min_edge 0.035" in text
    assert "kappa 0.490" in text
    # Storm split + counterfactual + loosening caveat (r2 M-4).
    assert "HIGH storm" in text and "LOW storm" in text
    assert "TIGHTENING direction only" in text
    assert "loosening direction is not computable" in text
    assert "would have BLOCKED" in text
    # K5 tribute line.
    assert "bought 2 revival(s) for $1000" in text
    # Information hygiene: no market identity ever reaches the LLM.
    assert "SECRET-MKT-42" not in text
    assert "secret-slug" not in text
    assert "Anon A" not in text


def test_death_window_without_kit_is_unchanged() -> None:
    """Kit-off (no genome/ledger/tribute kwargs): the summary ends with
    the pre-kit sentence — byte-identical death rites for G0."""
    from agent.backtest.reincarnation import build_death_window
    from agent.core.state import Weights

    w = Weights(
        w_r=0.5, w_s=0.5, alpha=[1 / 3, 1 / 3, 1 / 3], beta=[0.5, 0.5],
        rho=0.5,
    )
    window = build_death_window(
        incarnation=1,
        max_incarnations=20,
        terminal_weights=w,
        seed_weights=w,
        pnl_at_death=-10.0,
        recent_step_pnls=[-10.0],
        settled=1,
        target_markets=100,
        markets_seen=10,
        avg_stake_usd=5.0,
        win_rate=0.0,
        initial_breath=35.0,
        loss_multiplier=5.0,
        best_markets_seen=10,
        best_progress_pct=10.0,
    )
    text = window.recent_reflections[0]
    assert text.endswith("with these weights.")
    assert "genome" not in text
    assert "storm" not in text


# ========================================================================= #
# A9 Task 4 — K7 falsification (paired time-shift) + participation.
# ========================================================================= #


def test_shuffle_timestamps_paired_consistency() -> None:
    """Every (row, snapshot) pair shifts as ONE unit: internal intervals
    (entry→resolution, entry→end, ledger spacing) are preserved exactly;
    the output timeline is monotone with >= 60 s spacing; and the
    chronological MARKET ORDER actually changed (the regression that
    fails if an implementation shuffles row order without moving
    timestamps — order-only shuffles are erased by the re-sorts)."""
    from datetime import datetime

    from agent.backtest.reincarnation import _entry_ts, shuffle_timestamps

    rows, snaps = _clustered_dying_fixture()
    out_rows, out_snaps = shuffle_timestamps(rows, snaps, seed=3)
    assert len(out_rows) == len(rows)
    snap_by_id = {s.market_id: s for s in snaps}
    out_snap_by_id = {s.market_id: s for s in out_snaps}
    orig_by_id = {r.market_id: r for r in rows}
    for r in out_rows:
        orig = orig_by_id[r.market_id]
        delta = _entry_ts(r) - _entry_ts(orig)
        s_orig = snap_by_id[r.market_id]
        s_new = out_snap_by_id[r.market_id]
        # Row internal intervals preserved.
        assert datetime.fromisoformat(r.end_date_iso) - datetime.fromisoformat(
            orig.end_date_iso
        ) == delta
        if orig.resolution_ts_iso is not None:
            assert datetime.fromisoformat(
                r.resolution_ts_iso
            ) - datetime.fromisoformat(orig.resolution_ts_iso) == delta
        # Snapshot moved by the SAME delta (paired settle availability).
        assert datetime.fromisoformat(
            s_new.end_date_iso
        ) - datetime.fromisoformat(s_orig.end_date_iso) == delta
        if s_orig.resolution_ts_iso is not None:
            assert datetime.fromisoformat(
                s_new.resolution_ts_iso
            ) - datetime.fromisoformat(s_orig.resolution_ts_iso) == delta
        # Ledger spacing preserved.
        for pp_new, pp_old in zip(
            s_new.price_ledger, s_orig.price_ledger, strict=True
        ):
            assert datetime.fromisoformat(
                pp_new.ts
            ) - datetime.fromisoformat(pp_old.ts) == delta
    # Monotone >= 60 s spacing on the output timeline.
    out_sorted = sorted(out_rows, key=_entry_ts)
    for a, b in itertools.pairwise(out_sorted):
        assert (_entry_ts(b) - _entry_ts(a)).total_seconds() >= 60.0
    # The chronological market order CHANGED (timestamps moved, not rows).
    orig_order = [
        r.market_id for r in sorted(rows, key=lambda x: (_entry_ts(x), x.market_id))
    ]
    new_order = [r.market_id for r in out_sorted]
    assert new_order != orig_order


def test_shuffle_timestamps_tied_bucket_escape() -> None:
    """r13 M-1: a large tied bucket sitting closer than bucket_size×60 s
    to the next slot still yields >= 60 s spacing everywhere (cumulative
    push), and slot order IS the seeded permutation order (ties are
    eliminated entirely)."""
    import dataclasses as _dc

    from agent.backtest.reincarnation import _entry_ts, shuffle_timestamps

    rows, snaps = _clustered_dying_fixture()
    # Force a tied bucket: first 3 rows share ONE entry ts; the next
    # distinct ts is only 30 s later (< 3 × 60 s).
    snap_by_id = {s.market_id: s for s in snaps}
    tied_ts = "2026-06-01T00:00:00+00:00"
    near_ts = "2026-06-01T00:00:30+00:00"
    forced: list = []
    for idx, r in enumerate(rows):
        if idx < 3:
            forced.append(_dc.replace(r, entry_asof_ts_iso=tied_ts))
        elif idx == 3:
            forced.append(_dc.replace(r, entry_asof_ts_iso=near_ts))
        else:
            forced.append(r)
    out_rows, _ = shuffle_timestamps(forced, snaps, seed=7)
    out_sorted = sorted(out_rows, key=_entry_ts)
    for a, b in itertools.pairwise(out_sorted):
        assert (_entry_ts(b) - _entry_ts(a)).total_seconds() >= 60.0
    # No ties remain anywhere.
    ts_list = [_entry_ts(r) for r in out_sorted]
    assert len(set(ts_list)) == len(ts_list)
    del snap_by_id


def test_groundhog_shuffled_disclosure_and_flag_off_keyset(tmp_path) -> None:
    """The falsification leg discloses split.shuffled_timestamps; the
    normal leg's split dict carries NO such key."""
    from agent.backtest.reincarnation import run_groundhog_export
    from tests.agent.backtest.test_survival_ai_mode import _fragile_seed

    rows, snaps = _clustered_dying_fixture()
    shuffled = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_shuffled.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
        storm=True,
        shuffle_timestamps_seed=1,
    )
    assert shuffled["split"]["shuffled_timestamps"] is True
    assert shuffled["split"]["shuffle_seed"] == 1
    # bets_by_third rides the storm kit (per-inc).
    for inc in shuffled["incarnations"]:
        thirds = inc["bets_by_third"]
        assert [t["third"] for t in thirds] == [0, 1, 2]
        assert sum(t["placed"] for t in thirds) <= inc["bets"] + 1
        assert sum(t["denominator"] for t in thirds) == inc["markets_seen"]

    plain = run_groundhog_export(
        rows=rows,
        snapshots=snaps,
        base_seed=_fragile_seed(),
        out_path=tmp_path / "g_plain.json",
        max_incarnations=2,
        train_fraction=0.67,
        initial_breath=3.0,
        entry_price_floor=0.0,
    )
    assert "shuffled_timestamps" not in plain["split"]
    assert "shuffle_seed" not in plain["split"]
    for inc in plain["incarnations"]:
        assert "bets_by_third" not in inc
        assert "regime_ledger" not in inc


def test_bets_by_third_dedups_status_flips() -> None:
    """r2 M-3: the poller appends a settled status-flip COPY per bet —
    raw rows double-count; dedup keeps the FIRST (placement) record."""
    from datetime import UTC, datetime

    from agent.backtest.reincarnation import _bets_by_third

    t0 = datetime(2026, 6, 1, tzinfo=UTC)

    def _iso(minutes: int) -> str:
        from datetime import timedelta

        return (t0 + timedelta(minutes=minutes)).isoformat()

    placed = [
        {"bet_id": "a", "ts": _iso(0), "status": "open"},
        {"bet_id": "b", "ts": _iso(50), "status": "open"},
        # status-flip copy of a (later ts) — must NOT double-count nor
        # rebucket the placement.
        {"bet_id": "a", "ts": _iso(99), "status": "settled"},
        {"bet_id": "c", "ts": _iso(99), "status": "open"},
    ]
    consumed = [t0, t0.replace(minute=30), t0.replace(minute=59)]
    from datetime import timedelta

    consumed = [t0, t0 + timedelta(minutes=50), t0 + timedelta(minutes=99)]
    thirds = _bets_by_third(placed, consumed_entry_ts=consumed)
    assert sum(t["placed"] for t in thirds) == 3  # a, b, c — not 4
    assert thirds[0]["placed"] == 1   # a at minute 0
    assert thirds[1]["placed"] == 1   # b at minute 50
    assert thirds[2]["placed"] == 1   # c at minute 99
    assert sum(t["denominator"] for t in thirds) == 3
