# tests/agent/backtest/test_survival_ai_mode.py
"""Opt-in AI mode for the survival season — the L6 reflect→optimize closure.

The numerical survival season (``ai=None``) constructs every life's loop with
``NoOpStrategyAdvisor`` + no reflection engine — pure NUMERICAL EMA learning.
The opt-in AI mode (``ai=<AISeasonContext>``) instead wires the REAL
``ReflectionEngine`` + REAL ``StrategyAdvisorImpl`` into each life and
AUTO-APPROVES the advisor's ``weight_delta`` proposals (via
``_AutoApprovingAdvisor`` → the loop's per-life ``runtime_agent`` queue) so the
AI genuinely drives the weights ON TOP of the numerical backbone.

CRITICAL: every test injects a FAKE ``_LLMClient`` (``structured_call``); NEVER
a live Gemini call (``GEMINI_API_KEY`` is unset by ``conftest``). The real
Gemini run is the orchestrator's job, not the test suite's.

The numerical path MUST stay byte-for-byte unchanged when AI mode is off — the
regression test proves ``ai=None`` is identical to today (no reflection
annotations, no extra weight movement beyond the numerical EMA).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.find_optimal_config import StrategyConfig
from agent.backtest.historical_fetcher import MarketSnapshot, PricePoint
from agent.backtest.survival_season import (
    AIPreflightError,
    AISeasonContext,
    SurvivalRecorder,
    SurvivalRow,
    _AutoApprovingAdvisor,
    build_survival_journey,
    preflight_ai_advisor_applicable,
    preflight_ai_connectivity,
    run_survival_export,
    run_survival_season,
)
from agent.core.state import Weights
from agent.engines._performance_window import PerformanceWindow
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.llm.cost_guard import L3CostGuard
from agent.runtime.agent_runner import AgentRunner

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "smart_money",
    "sentiment_llm",
    "crowd_volume",
)


# =========================================================================== #
# Shared fixture builders (mirror test_survival_reflection_journey.py).
# =========================================================================== #


def _bullish_weights() -> Weights:
    return Weights(
        w_r=0.5, w_s=0.5, alpha=[0.34, 0.33, 0.33], beta=[1.0, 0.0], rho=0.6
    )


def _fragile_seed() -> StrategyConfig:
    return StrategyConfig(
        weights=_bullish_weights(),
        max_breath_risk_pct=1.0,
        min_confidence=0.05,
        min_bet_size_usd=1.0,
    )


def _snap(
    market_id: str,
    *,
    entry_ts: str,
    end_date: str,
    resolution: str,
    entry_price: float = 0.50,
    outcome: Literal["yes", "no", "void"] = "no",
) -> MarketSnapshot:
    return MarketSnapshot(
        market_id=market_id,
        slug=f"atp-{market_id}",
        end_date_iso=end_date,
        resolution_ts_iso=resolution,
        outcome=outcome,
        winning_price=1.0,
        liquidity_cap_usd=20.0,
        price_ledger=[PricePoint(ts=entry_ts, mid_price=entry_price)],
    )


def _row(snap: MarketSnapshot, *, score: float = 0.8) -> SurvivalRow:
    entry_ts = snap.price_ledger[0].ts
    entry_price = snap.price_ledger[0].mid_price
    signal = SignalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        scores={k: score for k in _SLOTS},
        confidences={k: 0.95 for k in _SLOTS},
        entry_price=entry_price,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap_usd=snap.liquidity_cap_usd,
    )
    return SurvivalRow(
        market_id=snap.market_id,
        slug=snap.slug,
        signal=signal,
        entry_asof_ts_iso=entry_ts,
        resolution_ts_iso=snap.resolution_ts_iso,
        end_date_iso=snap.end_date_iso,
        outcome=snap.outcome or "no",
        winning_price=snap.winning_price or 1.0,
        liquidity_cap=snap.liquidity_cap_usd,
        players=("alpha", "bravo"),
        surface="Hard",
    )


def _dying_fixture() -> tuple[list[SurvivalRow], list[MarketSnapshot]]:
    snaps = [
        _snap(
            "m1",
            entry_ts="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
        _snap(
            "m2",
            entry_ts="2025-06-05T00:00:00+00:00",
            end_date="2025-06-05T12:00:00+00:00",
            resolution="2025-06-05T20:00:00+00:00",
        ),
        _snap(
            "m3",
            entry_ts="2025-06-10T00:00:00+00:00",
            end_date="2025-06-10T12:00:00+00:00",
            resolution="2025-06-10T20:00:00+00:00",
        ),
    ]
    return [_row(s) for s in snaps], snaps


@dataclass
class _FakeAdvisorLLM:
    """Protocol-conformant ``_LLMClient`` — NEVER a live Gemini call.

    Records every prompt so a test can introspect what reached the LLM. Returns
    the advisor wrapper shape (one ``weight_delta`` proposal). When the
    ``ReflectionEngine`` calls it the same payload comes back — the engine's
    structured schema rejects it (no ``summary`` key) and FAILS SOFT to a
    deterministic template, which STILL emits ``reflection_emitted`` (verified by
    the integration test). Either way no real money is spent.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt})
        return {
            "proposals": [
                {
                    "kind": "weight_delta",
                    "rationale": (
                        "the reflection flagged a loss streak — trim alpha_2"
                    ),
                    "proposed_change": {"key": "alpha_2", "delta": 0.04},
                    "expected_impact": "reduce drawdown",
                    "confidence_pct": 60,
                }
            ]
        }


# =========================================================================== #
# Part 1 — ``_AutoApprovingAdvisor`` unit (the auto-apply seam).
# =========================================================================== #


@dataclass
class _FakeInnerAdvisor:
    """A ``StrategyAdvisor``-conformant fake returning a scripted proposal list."""

    proposals: list[StrategyProposal]
    seen: list[PerformanceWindow] = field(default_factory=list)

    def review_window(
        self, window: PerformanceWindow
    ) -> list[StrategyProposal]:
        self.seen.append(window)
        return list(self.proposals)


def _weight_delta_proposal(
    *, key: str = "alpha_2", delta: float = 0.04
) -> StrategyProposal:
    from datetime import UTC, datetime

    return StrategyProposal(
        proposal_id="p-wd",
        ts=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
        kind="weight_delta",
        rationale="trim alpha_2",
        proposed_change={"key": key, "delta": delta},
        confidence_pct=60,
        requires_human_approval=True,
    )


def _new_signal_proposal() -> StrategyProposal:
    from datetime import UTC, datetime

    return StrategyProposal(
        proposal_id="p-ns",
        ts=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
        kind="new_signal_idea",
        rationale="add a serve-speed engine",
        proposed_change={"description": "serve speed"},
        confidence_pct=40,
        requires_human_approval=True,
    )


def _empty_window() -> PerformanceWindow:
    from datetime import UTC, datetime

    from agent.core.state import Phase

    return PerformanceWindow(
        tick=1,
        ts=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
        agent_id="agent-test",
        phase=Phase.PHASE_2_APPRENTICE,
        current_weights=_bullish_weights(),
        baseline_weights=_bullish_weights(),
        recent_pnl_window_usd=-3.0,
    )


def test_auto_approving_advisor_enqueues_weight_delta_and_returns_unchanged() -> None:
    """A weight_delta proposal is auto-applied to the runtime_agent AND returned.

    The wrapper enqueues ``proposed_change`` onto the runtime agent (what the
    FastAPI approve handler does) so the proposal takes effect in the sim, but
    STILL returns the proposals unchanged so the loop persists them pending +
    emits ``strategy_advisor_fired`` with the real count (recorder annotation).
    """
    proposal = _weight_delta_proposal()
    inner = _FakeInnerAdvisor(proposals=[proposal])
    runtime_agent = AgentRunner()
    advisor = _AutoApprovingAdvisor(inner=inner, runtime_agent=runtime_agent)

    out = advisor.review_window(_empty_window())

    # The proposals are returned unchanged (loop still persists + counts them).
    assert out == [proposal]
    # The weight delta reached the runtime agent's queue (auto-applied in-sim).
    drained = runtime_agent.drain_pending_deltas()
    assert drained == [{"key": "alpha_2", "delta": 0.04}]


def test_auto_approving_advisor_does_not_enqueue_non_weight_delta() -> None:
    """A non-weight_delta proposal is returned but NOT auto-applied.

    Mirrors prod, which routes ``new_signal_idea`` / ``prompt_tweak`` to a TODO
    file rather than the weight queue.
    """
    weight = _weight_delta_proposal()
    other = _new_signal_proposal()
    inner = _FakeInnerAdvisor(proposals=[other, weight])
    runtime_agent = AgentRunner()
    advisor = _AutoApprovingAdvisor(inner=inner, runtime_agent=runtime_agent)

    out = advisor.review_window(_empty_window())

    assert out == [other, weight]
    # ONLY the weight_delta was enqueued (one item).
    drained = runtime_agent.drain_pending_deltas()
    assert drained == [{"key": "alpha_2", "delta": 0.04}]


def test_auto_approving_advisor_survives_malformed_proposed_change() -> None:
    """A malformed ``proposed_change`` must not crash the season (fail-soft)."""
    from datetime import UTC, datetime

    bad = StrategyProposal(
        proposal_id="p-bad",
        ts=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
        kind="weight_delta",
        rationale="malformed payload",
        proposed_change={},  # no key/delta — the loop drain skips it
        confidence_pct=10,
        requires_human_approval=True,
    )
    inner = _FakeInnerAdvisor(proposals=[bad])
    runtime_agent = AgentRunner()
    advisor = _AutoApprovingAdvisor(inner=inner, runtime_agent=runtime_agent)

    # Must not raise, and the proposal is still returned for the loop to persist.
    out = advisor.review_window(_empty_window())
    assert out == [bad]


# =========================================================================== #
# Part 2 — season AI integration (real engines, fake LLM).
# =========================================================================== #


def _ai_context(llm: _FakeAdvisorLLM) -> AISeasonContext:
    """A fake AI context: real engines built per life off this fake LLM.

    Tiny cadence overrides force the advisor + reflection to fire on the very
    first ticks so a settlement consumes the resulting annotation.
    """
    return AISeasonContext(
        llm_client=llm,
        l3_guard=L3CostGuard(hard_cap_usd=10.0),
        strategy_advisor_tick_interval=1,
        reflection_tick_interval=1,
    )


def test_season_ai_mode_fires_advisor_moves_weights_and_annotates(
    tmp_path: Path,
) -> None:
    """AI mode: advisor fires with proposals, weights diverge, journey annotated.

    Asserts the three contract points:
    (a) the real ``StrategyAdvisorImpl`` fired with ``proposals_emitted > 0``
        (the fake LLM was actually called);
    (b) the AI run's terminal weights DIFFER from the SAME season run WITHOUT ai
        (auto-approve genuinely moved the weights — AI diverges from numerical);
    (c) at least one journey step carries a ``reflection`` annotation (the real
        ``ReflectionEngine`` fired ``reflection_emitted`` — even on its fail-soft
        path — and the advisor fire enriched it).
    """
    rows, snaps = _dying_fixture()
    seed = _fragile_seed()

    # --- numerical baseline (ai=None) --- #
    base_recorder = SurvivalRecorder(rows=rows)
    base_result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "numerical",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
        recorder=base_recorder,
    )

    # --- AI run (ai=<fake context>) --- #
    fake_llm = _FakeAdvisorLLM()
    ai_recorder = SurvivalRecorder(rows=rows)
    ai_result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "ai",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
        recorder=ai_recorder,
        ai=_ai_context(fake_llm),
    )

    # (a) the advisor fired and the fake LLM was actually called.
    assert fake_llm.calls, "the real StrategyAdvisorImpl must have called the LLM"

    # (b) the AI run's terminal weights diverge from the numerical run's.
    base_terminal = base_result.lives[0].terminal_weights
    ai_terminal = ai_result.lives[0].terminal_weights
    assert ai_terminal != base_terminal, (
        "auto-approved AI proposals must move the weights vs the numerical-only "
        f"run (numerical={base_terminal!r} ai={ai_terminal!r})"
    )

    # (c) at least one journey step carries a reflection annotation.
    journey = build_survival_journey(
        result=ai_result, recorder=ai_recorder, rows=rows, seed=seed, max_steps=500
    )
    annotated = [s for s in journey["steps"] if "reflection" in s]
    assert annotated, "AI mode must stamp >=1 reflection annotation on the journey"
    # The advisor-enriched annotation records the optimisation.
    assert any("proposed" in s["reflection"] for s in annotated)


def test_season_ai_none_is_byte_identical_to_numerical(tmp_path: Path) -> None:
    """Regression: ``ai=None`` (and omitted) is byte-identical to today.

    Every step's ``reflection`` is ``None`` (no annotation key in the journey),
    the season behaves exactly as the pre-AI numerical path, and two ``ai=None``
    runs are identical to each other (deterministic numerical EMA backbone).
    """
    rows, snaps = _dying_fixture()
    seed = _fragile_seed()

    rec_a = SurvivalRecorder(rows=rows)
    result_a = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "a",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
        recorder=rec_a,
    )
    # Explicit ai=None must behave the same as omitting it.
    rec_b = SurvivalRecorder(rows=rows)
    result_b = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "b",
        initial_breath=3.0,
        initial_bankroll_usd=100.0,
        max_lives=5,
        recorder=rec_b,
        ai=None,
    )

    # No reflection annotations anywhere (the L6 closure is OFF).
    assert all(s.reflection is None for s in rec_a.steps)
    assert all(s.reflection is None for s in rec_b.steps)

    journey = build_survival_journey(
        result=result_a, recorder=rec_a, rows=rows, seed=seed, max_steps=500
    )
    for step_dict in journey["steps"]:
        assert "reflection" not in step_dict

    # Deterministic: two ai=None runs produce identical terminal weights.
    assert (
        result_a.lives[0].terminal_weights == result_b.lives[0].terminal_weights
    )


# =========================================================================== #
# Part 3 — the live-Gemini PRE-FLIGHT guard (review M1) + the default-context
# branch (review M2) + the CLI plumbing (review L3).
#
# CRITICAL: every fake ``structured_call`` here is OFFLINE — a real Gemini call
# would need ``GEMINI_API_KEY`` (unset under pytest) and would raise
# ``MissingApiKeyError`` long before any network I/O. The probe MUST surface
# that as a LOUD abort, not a silent mislabel.
# =========================================================================== #


@dataclass
class _OkLLM:
    """A fake ``_LLMClient`` whose ``structured_call`` SUCCEEDS (both probes pass).

    Schema-aware (T-D-018): the CONNECTIVITY probe passes a ``{"ok": boolean}``
    schema → returns ``{"ok": True}``; the APPLICABILITY gate (and the in-run
    strict advisor) passes a ``{"proposals": [...]}`` schema → returns ONE valid
    strict ``weight_delta`` proposal so the gate sees an applicable delta. Without
    this branch the new ``preflight_ai_advisor_applicable`` gate would abort.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if "proposals" in schema.get("properties", {}):
            return {
                "proposals": [
                    {
                        "kind": "weight_delta",
                        "rationale": "probe: agent is losing; nudge alpha_0.",
                        "proposed_change": {"key": "alpha_0", "delta": 0.04},
                        "expected_impact": "small Sharpe improvement",
                        "confidence_pct": 60,
                    }
                ]
            }
        return {"ok": True}


@dataclass
class _RaisingLLM:
    """A fake ``_LLMClient`` whose ``structured_call`` RAISES (unreachable).

    Mirrors a leaked/disabled key (``google.genai`` ``ClientError`` 403) or the
    unset-key ``MissingApiKeyError`` — both fire BEFORE useful output, so the
    engines would fail-soft to empty and the AI journey would be a mislabeled
    numerical run. The probe must convert this into a loud abort.
    """

    exc: Exception
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        raise self.exc


def test_preflight_passes_when_client_succeeds() -> None:
    """A working client makes the probe fire ONE structured_call + not raise."""
    llm = _OkLLM()
    preflight_ai_connectivity(llm)
    assert len(llm.calls) == 1, "the probe must fire exactly one structured_call"
    # A trivial {"ok": boolean} schema + 1-line prompt (the cheapest possible probe).
    schema = llm.calls[0]["schema"]
    assert schema.get("type") == "object"
    assert "ok" in schema.get("properties", {})


def test_preflight_aborts_loudly_when_client_raises() -> None:
    """ANY exception → a clear ``AIPreflightError`` (Gemini unreachable, aborted)."""
    llm = _RaisingLLM(exc=RuntimeError("403 PERMISSION_DENIED leaked key"))
    with pytest.raises(AIPreflightError) as ei:
        preflight_ai_connectivity(llm)
    msg = str(ei.value)
    # The message names Gemini unreachable + the underlying reason + the abort.
    assert "unreachable" in msg.lower()
    assert "403 PERMISSION_DENIED leaked key" in msg
    assert "abort" in msg.lower()
    # The underlying cause is chained (not swallowed).
    assert isinstance(ei.value.__cause__, RuntimeError)


def _tiny_universe(tmp_path: Path) -> tuple[Path, Path]:
    """Write a tiny ``_cache_tennis`` dir + matching cached rows for the export."""
    from agent.backtest.cached_sweep import save_rows
    from agent.backtest.historical_fetcher import save_cached_market

    _, snaps = _dying_fixture()
    cache_dir = tmp_path / "_cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for s in snaps:
        save_cached_market(snapshot=s, cache_dir=cache_dir)
    rows_path = tmp_path / "_signal_rows.json"
    save_rows([_row(s).signal for s in snaps], rows_path)
    return rows_path, cache_dir


def _empty_resolver() -> Any:
    from agent.backtest.tennis_match_resolver import TennisMatchResolver

    return TennisMatchResolver(name_index={})


def test_export_with_ai_aborts_when_default_client_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1+M2: ``with_ai=True`` + a default client that RAISES → loud abort.

    The default-context branch is exercised by monkeypatching ``GeminiClient``
    (so NO real client is built) with a fake whose ``structured_call`` raises.
    The preflight must abort BEFORE the season runs — NO journey written.
    """
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"

    # Keep the default-context branch a BARE patched Gemini (no MiniMax wrapping):
    # with MINIMAX_API_KEY set, make_llm_client() returns RetryLLMClient(Fallback(..))
    # and the patched fake would not be the direct client.
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    raising = _RaisingLLM(exc=RuntimeError("GEMINI_API_KEY is not set"))
    import agent.llm.gemini_client as gc

    monkeypatch.setattr(gc, "GeminiClient", lambda: raising)

    with pytest.raises(AIPreflightError):
        run_survival_export(
            rows_path=rows_path,
            cache_dir=cache_dir,
            out_path=out_path,
            base_seed=_fragile_seed(),
            initial_breath=3.0,
            max_lives=5,
            resolver=_empty_resolver(),
            with_ai=True,
        )

    # The probe fired on the default client + NO season ran → NO journey written.
    assert raising.calls, "the preflight must have probed the default client"
    assert not out_path.exists(), "no AI journey may be written on a failed probe"


def test_export_with_ai_default_context_uses_l3_guard_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: the default ``with_ai`` context is built with ``L3CostGuard.from_env``.

    A fake client that SUCCEEDS lets the export proceed; we capture the
    ``AISeasonContext`` actually constructed to prove the default branch wires
    ``L3CostGuard.from_env()`` (the season-long shared L3 cap).
    """
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"

    # Force the BARE-Gemini default path: with MINIMAX_API_KEY set, make_llm_client()
    # wraps the patched fake in RetryLLMClient(Fallback(..)) and `ctx.llm_client is ok`
    # would be False. Stripping the env var keeps the assertion meaningful + offline.
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    ok = _OkLLM()
    import agent.llm.gemini_client as gc

    monkeypatch.setattr(gc, "GeminiClient", lambda: ok)

    captured: list[AISeasonContext] = []
    import agent.backtest.survival_season as ss

    real_ctor = ss.AISeasonContext

    def _spy(*args: Any, **kwargs: Any) -> AISeasonContext:
        ctx = real_ctor(*args, **kwargs)
        captured.append(ctx)
        return ctx

    monkeypatch.setattr(ss, "AISeasonContext", _spy)

    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        with_ai=True,
    )

    # (a) the default context was constructed off the fake client + an L3 guard.
    assert captured, "the default with_ai branch must construct an AISeasonContext"
    ctx = captured[-1]
    assert ctx.llm_client is ok
    assert isinstance(ctx.l3_guard, L3CostGuard)
    # from_env() with no L3_MONTHLY_BUDGET_USD set → the documented $10 default.
    assert ctx.l3_guard.hard_cap_usd == 10.0
    # (b) the probe fired and the export proceeded to write a journey.
    assert ok.calls, "the preflight must have probed the default client"
    assert out_path.exists()
    assert isinstance(journey, dict)


def test_export_with_ai_preflight_false_skips_probe(
    tmp_path: Path,
) -> None:
    """``preflight=False`` skips the probe (offline fake season still runs)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"

    fake = _FakeAdvisorLLM()
    ctx = AISeasonContext(
        llm_client=fake,
        l3_guard=L3CostGuard(hard_cap_usd=10.0),
        strategy_advisor_tick_interval=1,
        reflection_tick_interval=1,
    )
    # An injected ``ai`` wins over with_ai; preflight=False disables the probe.
    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        ai=ctx,
        preflight=False,
    )
    assert out_path.exists()
    assert isinstance(journey, dict)


def test_export_numerical_path_never_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The numerical path (``with_ai=False``) must NEVER run the probe."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey.json"

    import agent.backtest.survival_season as ss

    def _boom(_client: Any) -> None:  # pragma: no cover - must never be called
        raise AssertionError("preflight must NOT run on the numerical path")

    monkeypatch.setattr(ss, "preflight_ai_connectivity", _boom)

    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
    )
    assert out_path.exists()
    assert isinstance(journey, dict)


# =========================================================================== #
# T-D-018 — applicability gate + hard zero-delta invariant + apply tally +
# subset cap. Every fake here is OFFLINE.
# =========================================================================== #


@dataclass
class _SchemaAwareLLM:
    """Schema-aware offline fake: connectivity → {"ok": True}; advisor → a
    SCRIPTED proposals payload (so a test can make it applicable or not)."""

    advisor_response: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self, *, model: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt})
        if "proposals" in schema.get("properties", {}):
            return self.advisor_response
        return {"ok": True}


def _applicable_advisor_response() -> dict[str, Any]:
    return {
        "proposals": [
            {
                "kind": "weight_delta",
                "rationale": "alpha_0 stale while losing.",
                "proposed_change": {"key": "alpha_0", "delta": 0.04},
                "expected_impact": "rebalance.",
                "confidence_pct": 60,
            }
        ]
    }


def _empty_proposed_change_response() -> dict[str, Any]:
    return {
        "proposals": [
            {
                "kind": "weight_delta",
                "rationale": "the model forgot the structured change.",
                "proposed_change": {},
                "expected_impact": "n/a.",
                "confidence_pct": 50,
            }
        ]
    }


def test_advisor_applicable_gate_passes_on_valid_delta() -> None:
    """The fail-fast gate does NOT raise when the strict advisor yields a delta."""
    llm = _SchemaAwareLLM(advisor_response=_applicable_advisor_response())
    preflight_ai_advisor_applicable(llm)  # must not raise
    assert llm.calls, "the gate must have called the advisor at least once"


def test_advisor_applicable_gate_aborts_on_empty_proposed_change() -> None:
    """The gate raises ``AIPreflightError`` when no applicable delta is produced."""
    llm = _SchemaAwareLLM(advisor_response=_empty_proposed_change_response())
    with pytest.raises(AIPreflightError) as ei:
        preflight_ai_advisor_applicable(llm)
    assert "applicable" in str(ei.value).lower()


def test_advisor_applicable_gate_does_not_touch_season_guard(tmp_path: Path) -> None:
    """Budget isolation (r2 M-2): the fail-fast gate builds its OWN guard, so the
    season's ``l3_guard`` is spent IDENTICALLY whether or not the preflight ran —
    the gate never bills the season budget. Exercised through the REAL
    ``run_survival_export`` call path (not the gate function in isolation)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)

    def _season_spend(*, preflight: bool, out_name: str) -> float:
        guard = L3CostGuard(hard_cap_usd=10.0)
        ctx = AISeasonContext(
            llm_client=_SchemaAwareLLM(
                advisor_response=_applicable_advisor_response()
            ),
            l3_guard=guard,
            strategy_advisor_tick_interval=1,
            reflection_tick_interval=1,
        )
        run_survival_export(
            rows_path=rows_path,
            cache_dir=cache_dir,
            out_path=tmp_path / out_name / "j.json",
            base_seed=_fragile_seed(),
            initial_breath=3.0,
            max_lives=5,
            resolver=_empty_resolver(),
            ai=ctx,
            preflight=preflight,
        )
        return guard.total_usd

    with_gate = _season_spend(preflight=True, out_name="withgate")
    without_gate = _season_spend(preflight=False, out_name="nogate")
    # The season is deterministic; the only difference is the preflight gate, which
    # bills its OWN guard. So the season guard's spend is identical either way —
    # proving the gate consumed zero of the season budget.
    assert with_gate == without_gate


def test_export_tallies_applied_deltas_into_summary(tmp_path: Path) -> None:
    """An AI run that applies weight deltas surfaces a non-zero
    ``proposals_applied`` in the journey summary (genuine-AI signal)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"
    ctx = AISeasonContext(
        llm_client=_FakeAdvisorLLM(),
        l3_guard=L3CostGuard(hard_cap_usd=10.0),
        strategy_advisor_tick_interval=1,
        reflection_tick_interval=1,
    )
    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        ai=ctx,
        preflight=False,
    )
    summary = journey["summary"]
    assert "proposals_applied" in summary and "proposals_apply_failed" in summary
    assert summary["proposals_applied"] >= 1, "the AI run must have moved weights"


def test_apply_failed_tally_surfaces_in_summary(tmp_path: Path) -> None:
    """A ``weight_delta_apply_failed`` hook event increments
    ``proposals_apply_failed`` and PROPAGATES into the journey summary.

    Strict mode prevents a bad delta reaching the queue in a real run, so the
    FAILED branch is exercised at the loop's state-hook seam (the same kind the
    loop's per-tick drain emits) and then read back through the journey summary.
    """
    rows, snaps = _dying_fixture()
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "season",
        initial_breath=3.0,
        max_lives=2,
        recorder=recorder,
    )
    # Numerical (ai=None) run emits NO weight-delta events → both tallies start 0.
    assert recorder.proposals_applied == 0
    assert recorder.proposals_apply_failed == 0
    hook = recorder.state_hook()
    hook.emit(
        kind="weight_delta_applied", tick=1, key="rho", amount=0.01,
        pending_count_after=0,
    )
    hook.emit(
        kind="weight_delta_apply_failed", tick=2,
        error="weight delta key must be one of [...] (got None)", payload={},
    )
    assert recorder.proposals_applied == 1
    assert recorder.proposals_apply_failed == 1
    journey = build_survival_journey(
        result=result, recorder=recorder, rows=rows, seed=_fragile_seed()
    )
    assert journey["summary"]["proposals_applied"] == 1
    assert journey["summary"]["proposals_apply_failed"] == 1


def test_require_applied_deltas_aborts_when_zero_applied(tmp_path: Path) -> None:
    """``require_applied_deltas=True`` + an AI run that applies ZERO deltas →
    ``AIPreflightError`` AND no artifact is written (hard invariant H-1)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"
    # An advisor LLM that always returns an EMPTY proposals list → nothing applies.
    ctx = AISeasonContext(
        llm_client=_SchemaAwareLLM(advisor_response={"proposals": []}),
        l3_guard=L3CostGuard(hard_cap_usd=10.0),
        strategy_advisor_tick_interval=1,
        reflection_tick_interval=1,
    )
    with pytest.raises(AIPreflightError) as ei:
        run_survival_export(
            rows_path=rows_path,
            cache_dir=cache_dir,
            out_path=out_path,
            base_seed=_fragile_seed(),
            initial_breath=3.0,
            max_lives=5,
            resolver=_empty_resolver(),
            ai=ctx,
            preflight=False,  # skip the fail-fast gate to exercise the HARD one
            require_applied_deltas=True,
        )
    assert "zero" in str(ei.value).lower()
    assert not out_path.exists(), "no artifact may be written when the run is bad"


def test_require_applied_deltas_default_false_still_writes(tmp_path: Path) -> None:
    """Default ``require_applied_deltas=False`` keeps the byte-unchanged write
    even when zero deltas applied (the invariant is opt-in)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"
    ctx = AISeasonContext(
        llm_client=_SchemaAwareLLM(advisor_response={"proposals": []}),
        l3_guard=L3CostGuard(hard_cap_usd=10.0),
        strategy_advisor_tick_interval=1,
        reflection_tick_interval=1,
    )
    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        ai=ctx,
        preflight=False,
    )
    assert out_path.exists()
    assert journey["summary"]["proposals_applied"] == 0


def test_max_markets_caps_the_universe(tmp_path: Path) -> None:
    """``max_markets`` slices the universe to first-N; the full snapshots dict is
    still passed (no KeyError) and the capped run sees strictly fewer markets."""
    rows_path, cache_dir = _tiny_universe(tmp_path)  # 3-market universe

    def _run(cap: int | None) -> set[str]:
        out_path = tmp_path / f"out_{cap}" / "survival_journey.json"
        journey = run_survival_export(
            rows_path=rows_path,
            cache_dir=cache_dir,
            out_path=out_path,
            base_seed=_fragile_seed(),
            initial_breath=3.0,
            max_lives=5,
            resolver=_empty_resolver(),
            max_markets=cap,
        )
        return {s["market"]["market_id"] for s in journey["steps"]}

    full_markets = _run(None)
    capped_markets = _run(1)
    assert len(capped_markets) <= 1, "max_markets=1 must bound the universe"
    assert len(full_markets) > len(capped_markets), "the slice must reduce coverage"


# =========================================================================== #
# Realism rules (entry-price floor 0.05 + per-bet PnL cap $100) — shared by
# the numerical and AI paths; disclosed + invariant-validated in the export.
# =========================================================================== #


def _tiny_universe_with_longshot(tmp_path: Path) -> tuple[Path, Path]:
    """The 3-market dying fixture + ONE sub-floor extreme longshot (0.001)."""
    from agent.backtest.cached_sweep import save_rows
    from agent.backtest.historical_fetcher import save_cached_market

    _, snaps = _dying_fixture()
    snaps.append(
        _snap(
            "m_lot",
            entry_ts="2025-06-15T00:00:00+00:00",
            end_date="2025-06-15T12:00:00+00:00",
            resolution="2025-06-15T20:00:00+00:00",
            entry_price=0.001,
        )
    )
    cache_dir = tmp_path / "_cache_tennis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for s in snaps:
        save_cached_market(snapshot=s, cache_dir=cache_dir)
    rows_path = tmp_path / "_signal_rows.json"
    save_rows([_row(s).signal for s in snaps], rows_path)
    return rows_path, cache_dir


def test_export_floor_excludes_longshot_and_discloses_rules(
    tmp_path: Path,
) -> None:
    """Numerical (ai=None) export under the DEFAULT rules: the sub-floor market
    never appears, and the summary discloses the rules + full-data evidence."""
    rows_path, cache_dir = _tiny_universe_with_longshot(tmp_path)
    out_path = tmp_path / "out" / "survival_journey.json"
    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
    )
    stepped_markets = {s["market"]["market_id"] for s in journey["steps"]}
    assert "m_lot" not in stepped_markets, "sub-floor market must never be bet"
    s = journey["summary"]
    # Rule disclosure (defaults ON at the export level).
    assert s["entry_price_floor"] == 0.05
    assert s["max_bet_pnl_usd"] == 100.0
    # Full-data evidence (r1 M-1).
    assert s["rows_after_floor"] == 3
    assert s["rows_dropped_by_floor"] == 1
    assert s["min_entry_price"] is not None and s["min_entry_price"] >= 0.05
    assert s["max_step_pnl"] is None or s["max_step_pnl"] <= 100.0
    assert s["max_baseline_pnl"] is None or s["max_baseline_pnl"] <= 100.0


def test_export_rules_none_restores_legacy_physics(tmp_path: Path) -> None:
    """``entry_price_floor=None, max_bet_pnl_usd=None`` → legacy universe (the
    longshot is eligible again) and the summary discloses both rules as off."""
    rows_path, cache_dir = _tiny_universe_with_longshot(tmp_path)
    out_path = tmp_path / "out" / "survival_journey.json"
    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        entry_price_floor=None,
        max_bet_pnl_usd=None,
    )
    s = journey["summary"]
    assert s["entry_price_floor"] is None
    assert s["max_bet_pnl_usd"] is None
    assert s["rows_after_floor"] == 4
    assert s["rows_dropped_by_floor"] == 0


def test_export_rules_apply_identically_on_ai_path(tmp_path: Path) -> None:
    """The SHARED-rules guarantee: the AI path (injected fake ctx) excludes the
    same sub-floor market and discloses the same rules."""
    rows_path, cache_dir = _tiny_universe_with_longshot(tmp_path)
    out_path = tmp_path / "out" / "survival_journey_ai.json"
    ctx = AISeasonContext(
        llm_client=_FakeAdvisorLLM(),
        l3_guard=L3CostGuard(hard_cap_usd=10.0),
        strategy_advisor_tick_interval=1,
        reflection_tick_interval=1,
    )
    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        ai=ctx,
        preflight=False,
    )
    stepped_markets = {s["market"]["market_id"] for s in journey["steps"]}
    assert "m_lot" not in stepped_markets
    s = journey["summary"]
    assert s["entry_price_floor"] == 0.05
    assert s["max_bet_pnl_usd"] == 100.0
    assert s["min_entry_price"] is not None and s["min_entry_price"] >= 0.05


def test_realism_invariant_blocks_violating_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A journey whose FULL data violates the cap → RuntimeError BEFORE the
    write; no artifact lands (r1 M-1)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "out" / "survival_journey.json"

    import agent.backtest.survival_season as ss

    def _fake_journey(**_kwargs: Any) -> dict[str, Any]:
        return {
            "summary": {
                "max_step_pnl": 150.0,  # violates the $100 cap
                "max_baseline_pnl": 5.0,
                "min_entry_price": 0.5,
            }
        }

    monkeypatch.setattr(ss, "build_survival_journey", _fake_journey)
    with pytest.raises(RuntimeError) as ei:
        run_survival_export(
            rows_path=rows_path,
            cache_dir=cache_dir,
            out_path=out_path,
            base_seed=_fragile_seed(),
            initial_breath=3.0,
            max_lives=5,
            resolver=_empty_resolver(),
        )
    assert "realism invariant violated" in str(ei.value)
    assert not out_path.exists(), "no violating artifact may be written"


# =========================================================================== #
# Realism v3 (value physics) — export defaults, sentinel, physics invariant.
# =========================================================================== #


def test_export_v3_defaults_disclose_and_mirror_sentinel(tmp_path: Path) -> None:
    """run_survival_export DEFAULTS flip v3 ON: side-correct + value mode, and
    the MIRROR_ROW_FLOOR sentinel resolves the bet-level floor to the row
    floor's value (r5 M-1). The summary carries all six typed keys."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "v3.json"

    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
    )
    s = journey["summary"]
    assert s["side_correct_pricing"] is True
    assert s["value_betting"] is True
    assert s["effective_entry_price_floor"] == s["entry_price_floor"] == 0.05
    assert isinstance(s["min_edge"], float)
    assert isinstance(s["kappa"], float)
    assert "min_effective_entry_price" in s
    assert out_path.exists()


def test_export_v3_explicit_none_disables_effective_floor(tmp_path: Path) -> None:
    """Explicit ``effective_entry_price_floor=None`` disables the bet-level
    floor (the sentinel's other meaning, r5 M-1)."""
    rows_path, cache_dir = _tiny_universe(tmp_path)
    out_path = tmp_path / "v3_nofloor.json"

    journey = run_survival_export(
        rows_path=rows_path,
        cache_dir=cache_dir,
        out_path=out_path,
        base_seed=_fragile_seed(),
        initial_breath=3.0,
        max_lives=5,
        resolver=_empty_resolver(),
        effective_entry_price_floor=None,
    )
    assert journey["summary"]["effective_entry_price_floor"] is None


def test_journey_v3_physics_invariant_rejects_tampered_step(tmp_path: Path) -> None:
    """r1 H-1: a learner step whose pnl does not match the side-correct
    recompute aborts the journey build (no artifact can carry it)."""
    rows, snaps = _dying_fixture()
    seed = _fragile_seed()
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "season",
        initial_breath=3.0,
        max_lives=2,
        recorder=recorder,
        side_correct_pricing=True,
        value_betting=False,
    )
    assert recorder.steps, "fixture must settle at least one bet"
    import dataclasses as _dc

    recorder.steps[0] = _dc.replace(
        recorder.steps[0], pnl_usd=recorder.steps[0].pnl_usd + 1.0
    )  # tamper (SurvivalStep is frozen)
    with pytest.raises(RuntimeError, match="physics invariant violated"):
        build_survival_journey(
            result=result,
            recorder=recorder,
            rows=rows,
            seed=seed,
            side_correct_pricing=True,
        )


def test_journey_v3_physics_invariant_rejects_tampered_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r1 H-1: a baseline point violating the physics also aborts the build —
    the invariant covers ALL THREE curves, not just the learner."""
    rows, snaps = _dying_fixture()
    seed = _fragile_seed()
    recorder = SurvivalRecorder(rows=rows)
    result = run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=seed,
        state_root=tmp_path / "season",
        initial_breath=3.0,
        max_lives=2,
        recorder=recorder,
        side_correct_pricing=True,
    )

    import agent.backtest.survival_season as ss

    real_builder = ss.build_archetype_curve

    def _tampered(*args: Any, **kwargs: Any) -> Any:
        curve = real_builder(*args, **kwargs)
        import dataclasses as _dc

        bets = [i for i, p in enumerate(curve) if p.is_bet]
        if bets:
            i = bets[0]
            curve[i] = _dc.replace(curve[i], pnl_usd=curve[i].pnl_usd + 7.0)
        return curve

    monkeypatch.setattr(ss, "build_archetype_curve", _tampered)
    with pytest.raises(RuntimeError, match="physics invariant violated"):
        build_survival_journey(
            result=result,
            recorder=recorder,
            rows=rows,
            seed=seed,
            side_correct_pricing=True,
        )


def test_fragile_seed_preserves_value_knobs() -> None:
    """r7: the fragile derivation preserves min_edge/kappa exactly like the
    fusion weights (they are part of the strategy identity)."""
    base = _fragile_seed()
    custom = StrategyConfig(
        weights=base.weights,
        max_breath_risk_pct=0.2,
        min_confidence=0.05,
        min_bet_size_usd=3.0,
        min_edge=0.07,
        kappa=0.40,
    )
    import agent.backtest.survival_season as ss

    fragile = ss.fragile_seed_from_config(custom, max_breath_risk_pct=0.95)
    assert fragile.min_edge == 0.07
    assert fragile.kappa == 0.40
    assert fragile.weights == custom.weights


def test_ai_context_model_threads_into_all_three_constructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r6 H-1 + r7 H-1/H-2: AISeasonContext.model reaches (i) the advisor in
    _build_life_loop, (ii) the ReflectionEngine sonnet/opus pair, and (iii)
    the preflight probe -- with "" each client self-resolves its own default,
    so a provider-pure leg can never be sent a foreign model id."""
    import agent.backtest.survival_season as ss

    captured: dict[str, object] = {}

    real_advisor = ss.StrategyAdvisorImpl
    real_reflection = ss.ReflectionEngine

    class _SpyAdvisor(real_advisor):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: Any) -> None:
            captured.setdefault("advisor_models", []).append(  # type: ignore[union-attr]
                kwargs.get("model", "<DEFAULT>")
            )
            super().__init__(**kwargs)

    class _SpyReflection(real_reflection):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: Any) -> None:
            captured["reflection_models"] = (
                kwargs.get("sonnet_model", "<DEFAULT>"),
                kwargs.get("opus_model", "<DEFAULT>"),
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(ss, "StrategyAdvisorImpl", _SpyAdvisor)
    monkeypatch.setattr(ss, "ReflectionEngine", _SpyReflection)

    rows, snaps = _dying_fixture()
    ctx = AISeasonContext(
        _FakeAdvisorLLM(),
        L3CostGuard(hard_cap_usd=10.0),
        1,
        1,
        model="",
    )
    recorder = SurvivalRecorder(rows=rows)
    run_survival_season(
        rows=rows,
        snapshots=snaps,
        seed=_fragile_seed(),
        state_root=tmp_path / "season",
        initial_breath=3.0,
        max_lives=1,
        recorder=recorder,
        ai=ctx,
    )
    # (i)+(ii): the in-loop constructions saw the ctx model verbatim.
    assert "" in captured["advisor_models"]  # type: ignore[operator]
    assert captured["reflection_models"] == ("", "")

    # (iii): the preflight probe also receives it.
    probe_client = _FakeAdvisorLLM()
    ss.preflight_ai_advisor_applicable(probe_client, model="")
    assert "<DEFAULT>" not in captured["advisor_models"]  # type: ignore[operator]
