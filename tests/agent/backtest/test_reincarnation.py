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
