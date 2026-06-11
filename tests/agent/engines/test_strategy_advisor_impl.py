"""Tests for :mod:`agent.engines.strategy_advisor_impl` — T-B-029 sprint_10.

Eight tests per the T-B-029 brief acceptance criteria:

1. ``test_happy_one_proposal`` — fake Gemini returns 1 well-shaped
   proposal → impl returns one :class:`StrategyProposal` with the
   locked locked fields, no truncation, no warning log.

2. ``test_max_three_proposal_cap`` — fake Gemini returns 5 proposals
   (overshoot — the schema's ``maxItems`` isn't always honoured by the
   model); impl tail-trims to :data:`MAX_PROPOSALS_PER_CALL` and emits
   a WARNING log.

3. ``test_cost_guard_tripped_returns_empty`` — :class:`L3CostGuard`
   exhausted at entry → impl SHORT-CIRCUITS to ``[]`` BEFORE the LLM
   call (no SDK invocation, no charge against the cost guard), and
   logs a WARNING.

4. ``test_malformed_response_returns_empty`` — fake Gemini returns
   malformed JSON (missing ``proposals`` key) → impl returns ``[]`` +
   WARNING log, and does NOT charge the cost guard (failed parse
   doesn't burn budget — defensive against a bug-and-retry loop
   draining the budget).

5. ``test_all_three_kinds_parsed`` — fake Gemini returns one proposal
   of EACH locked kind (``weight_delta``, ``new_signal_idea``,
   ``prompt_tweak``); impl parses all three correctly.

6. ``test_performance_window_folder_helpers_edge_cases`` — covers the
   pure folder helpers in
   :mod:`agent.engines._performance_window`:

   * Missing files → ``[]``.
   * Empty PnL stream → ``[]``.
   * All-same-weights trajectory → still folds (size matches input).
   * Corrupt rows (missing keys / wrong types / NaN) → silently skipped.

7. ``test_vcr_cassette_happy_call`` — the brief's "VCR cassette covers
   1 happy live Gemini call" criterion. Uses a YAML cassette under
   ``tests/agent/engines/cassettes/test_strategy_advisor_impl.yaml``
   served by a Protocol-conformant stub (same pattern as the T-B-022
   smoke cassette). The cassette response carries 2 mixed-kind
   proposals; impl returns both.

8. ``test_swap_with_existing_loop_scaffold`` — :class:`StrategyAdvisorImpl`
   structurally satisfies the
   :class:`agent.engines.strategy_advisor.StrategyAdvisor` Protocol so
   it can drop into :class:`SandboxPhase2Loop` exactly as the
   sprint_9 swap test contract specified.

All tests inject a Protocol-conformant fake or cassette client — no
real Gemini call under pytest. The autouse fixture from
``tests/agent/llm/conftest.py`` (which strips ``GEMINI_API_KEY``)
does NOT auto-apply here (different package), but the impl's
narrow protocol seam means the fake client is the only path tests
touch — no SDK import needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from agent.core.state import Phase, Weights
from agent.engines._performance_window import (
    PerformanceWindow,
    fold_pnl_from_settled,
    fold_recent_reflections_from_jsonl,
    fold_weight_trajectory_from_jsonl,
)
from agent.engines._strategy_prompts import (
    MAX_PROPOSALS_PER_CALL,
    WEIGHT_DELTA_RESPONSE_SCHEMA,
)
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.engines.strategy_advisor import (
    PerformanceWindow as ReexportedPerformanceWindow,
)
from agent.engines.strategy_advisor import (
    StrategyAdvisor,
)
from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
from agent.llm.cost_guard import L3CostGuard

# --------------------------------------------------------------------------- #
# Shared fakes — Protocol-conformant LLM stub used by every test except
# the cassette test (which uses a YAML-backed stub for parity with the
# sprint_9 T-B-022 smoke replay shape).
# --------------------------------------------------------------------------- #


@dataclass
class _FakeLLM:
    """In-memory ``_LLMClient`` for unit tests.

    Same shape as :class:`tests.agent.llm.conftest.FakeGeminiClient`
    (we don't import it because the conftest fixture there autouses
    a different fixture chain — keeping this module self-contained
    avoids cross-package autouse leakage).

    Attributes
    ----------
    responses:
        FIFO queue of scripted responses. ``dict`` returned directly,
        :class:`BaseException` raised.
    calls:
        Records every ``structured_call`` invocation for post-hoc
        assertions.
    """

    responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise AssertionError(
                f"_FakeLLM exhausted after {len(self.calls)} calls — "
                f"test wired fewer responses than calls."
            )
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, dict):
            raise AssertionError(
                f"_FakeLLM unsupported response type: {type(item).__name__}"
            )
        return cast(dict[str, Any], item)


def _build_window(**overrides: Any) -> PerformanceWindow:
    """Construct a :class:`PerformanceWindow` with sane defaults.

    Tests override only the fields they care about; the rest get
    representative defaults that exercise the prompt rendering path.
    """
    weights = Weights(
        w_r=0.5,
        w_s=0.5,
        alpha=[0.34, 0.33, 0.33],
        beta=[1.0, 0.0],
        rho=0.05,
    )
    base: dict[str, Any] = {
        "tick": 100,
        "ts": datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
        "agent_id": "advisor_test_agent",
        "phase": Phase.PHASE_2_APPRENTICE,
        "current_weights": weights,
        "baseline_weights": weights,
        "recent_pnl_window_usd": -3.25,
        "trigger": "tick_interval",
        "recent_pnl": [1.0, -2.5, 0.5, -1.0, 0.75],
        "weight_trajectory": [weights, weights, weights],
        "recent_reflections": [
            "Trusted smart-money on a coin-flip market; lost.",
            "Crowd-volume was loud but unreliable.",
        ],
        "tick_count": 100,
    }
    base.update(overrides)
    return PerformanceWindow(**base)


def _make_advisor(
    *,
    fake: _FakeLLM,
    cap_usd: float = 1.0,
    per_call_usd: float = 0.006,
) -> tuple[StrategyAdvisorImpl, L3CostGuard]:
    """Build an advisor + matching cost guard for tests."""
    cost_guard = L3CostGuard(hard_cap_usd=cap_usd)
    advisor = StrategyAdvisorImpl(
        llm_client=fake,
        cost_guard=cost_guard,
        per_call_usd_estimate=per_call_usd,
    )
    return advisor, cost_guard


# --------------------------------------------------------------------------- #
# Test 1 — Happy path: 1 well-shaped proposal.
# --------------------------------------------------------------------------- #


def test_happy_one_proposal(caplog: pytest.LogCaptureFixture) -> None:
    """One LLM proposal → one :class:`StrategyProposal` returned."""
    fake = _FakeLLM(
        responses=[
            {
                "proposals": [
                    {
                        "kind": "weight_delta",
                        "rationale": "alpha_2 drifted +0.03 with negative PnL.",
                        "proposed_change": {"key": "alpha_2", "delta": -0.02},
                        "expected_impact": "Reduce smart-money over-confidence.",
                        "confidence_pct": 68,
                    }
                ]
            }
        ]
    )
    advisor, cost_guard = _make_advisor(fake=fake)

    with caplog.at_level(logging.WARNING, logger="agent.engines.strategy_advisor_impl"):
        result = advisor.review_window(_build_window())

    assert len(result) == 1
    proposal = result[0]
    assert isinstance(proposal, StrategyProposal)
    assert proposal.kind == "weight_delta"
    assert proposal.rationale == "alpha_2 drifted +0.03 with negative PnL."
    assert proposal.proposed_change == {"key": "alpha_2", "delta": -0.02}
    assert proposal.expected_impact == "Reduce smart-money over-confidence."
    assert proposal.confidence_pct == 68
    assert proposal.requires_human_approval is True
    # proposal_id is a UUID4 hex (32 chars, all hex) — defensive shape check.
    assert len(proposal.proposal_id) == 32
    int(proposal.proposal_id, 16)  # raises if not hex
    # Single happy call ⇒ no WARNING logs.
    assert caplog.records == []
    # Cost guard charged exactly once.
    assert len(cost_guard.events) == 1
    assert cost_guard.events[0].label == "l3_advisor"
    # LLM was invoked once with the structured-JSON schema.
    assert len(fake.calls) == 1
    assert fake.calls[0]["schema"]["type"] == "object"


# --------------------------------------------------------------------------- #
# Test 2 — Max-3 cap when LLM overshoots.
# --------------------------------------------------------------------------- #


def test_max_three_proposal_cap(caplog: pytest.LogCaptureFixture) -> None:
    """LLM returns 5 proposals → impl trims to 3 + WARNING."""

    def _item(idx: int) -> dict[str, Any]:
        return {
            "kind": "weight_delta",
            "rationale": f"observation {idx}.",
            "proposed_change": {"key": "w_r", "delta": 0.01},
            "expected_impact": f"impact {idx}.",
            "confidence_pct": 50 + idx,
        }

    fake = _FakeLLM(responses=[{"proposals": [_item(i) for i in range(5)]}])
    advisor, _ = _make_advisor(fake=fake)

    with caplog.at_level(logging.WARNING, logger="agent.engines.strategy_advisor_impl"):
        result = advisor.review_window(_build_window())

    assert len(result) == MAX_PROPOSALS_PER_CALL == 3
    # Tail-trim keeps the FIRST `MAX_PROPOSALS_PER_CALL` (rationale 0, 1, 2).
    assert [p.rationale for p in result] == [
        "observation 0.",
        "observation 1.",
        "observation 2.",
    ]
    # Exactly one WARNING log fired for the overshoot.
    overshoot_warnings = [
        r for r in caplog.records if "cap=3" in r.getMessage()
    ]
    assert len(overshoot_warnings) == 1


# --------------------------------------------------------------------------- #
# Test 3 — Cost-guard tripped → [].
# --------------------------------------------------------------------------- #


def test_cost_guard_tripped_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exhausted cost guard → [] + WARNING, NO SDK call."""
    fake = _FakeLLM(responses=[])  # would raise if called
    # cap=0.01; the test_calls_so_far burned via direct record() makes
    # the guard exhausted before the advisor's precheck.
    advisor, cost_guard = _make_advisor(fake=fake, cap_usd=0.01, per_call_usd=0.006)
    cost_guard.record(label="warmup", usd=0.01)
    assert cost_guard.is_exhausted()

    with caplog.at_level(logging.WARNING, logger="agent.engines.strategy_advisor_impl"):
        result = advisor.review_window(_build_window())

    assert result == []
    # NO SDK call — _FakeLLM would have raised if invoked (empty responses).
    assert fake.calls == []
    # Exactly one WARNING about the exhausted guard.
    exhausted_logs = [
        r for r in caplog.records if "cost guard exhausted" in r.getMessage()
    ]
    assert len(exhausted_logs) == 1


# --------------------------------------------------------------------------- #
# Test 4 — Malformed response → [].
# --------------------------------------------------------------------------- #


def test_malformed_response_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing 'proposals' key → [] + WARNING, NO cost charged."""
    fake = _FakeLLM(responses=[{"not_proposals": "oops"}])
    advisor, cost_guard = _make_advisor(fake=fake)

    with caplog.at_level(logging.WARNING, logger="agent.engines.strategy_advisor_impl"):
        result = advisor.review_window(_build_window())

    assert result == []
    # SDK WAS called, but the parse failed → no cost recorded
    # (defence against a bug-and-retry loop draining the budget).
    assert len(fake.calls) == 1
    assert cost_guard.events == []
    parse_logs = [
        r for r in caplog.records if "response parse failed" in r.getMessage()
    ]
    assert len(parse_logs) == 1


# --------------------------------------------------------------------------- #
# Test 5 — All 3 kinds parsed.
# --------------------------------------------------------------------------- #


def test_all_three_kinds_parsed() -> None:
    """One proposal of each locked kind round-trips through the parser."""
    fake = _FakeLLM(
        responses=[
            {
                "proposals": [
                    {
                        "kind": "weight_delta",
                        "rationale": "alpha drift detected.",
                        "proposed_change": {"key": "alpha_1", "delta": 0.02},
                        "expected_impact": "+1% Sharpe.",
                        "confidence_pct": 60,
                    },
                    {
                        "kind": "new_signal_idea",
                        "rationale": "Reflections mention referee bias repeatedly.",
                        "proposed_change": {
                            "description": "Add a referee-bias engine.",
                            "data_source": "nba_stats_referee_table",
                        },
                        "expected_impact": "Potential new edge on home-court games.",
                        "confidence_pct": 35,
                    },
                    {
                        "kind": "prompt_tweak",
                        "rationale": "Reflections lack a numeric anchor.",
                        "proposed_change": {
                            "target": "L2_reflection",
                            "after": "Anchor each paragraph on a numeric observation.",
                        },
                        "expected_impact": "Sharper future advice cycles.",
                        "confidence_pct": 72,
                    },
                ]
            }
        ]
    )
    advisor, _ = _make_advisor(fake=fake)

    result = advisor.review_window(_build_window())

    assert [p.kind for p in result] == [
        "weight_delta",
        "new_signal_idea",
        "prompt_tweak",
    ]
    # Each proposal carries the locked invariants.
    for p in result:
        assert p.requires_human_approval is True
        assert isinstance(p.proposed_change, dict)
        assert p.rationale.strip() != ""
        assert p.expected_impact is not None
        assert 0 <= p.confidence_pct <= 100


# --------------------------------------------------------------------------- #
# Test 6 — PerformanceWindow folder helper edge cases.
# --------------------------------------------------------------------------- #


def test_performance_window_folder_helpers_edge_cases(tmp_path: Path) -> None:
    """Pure folder helpers handle missing / empty / corrupt JSONL."""
    # ── 6a. Missing files → empty lists. ───────────────────────────────
    missing = tmp_path / "absent.jsonl"
    assert fold_pnl_from_settled(missing) == []
    assert fold_recent_reflections_from_jsonl(missing) == []
    assert fold_weight_trajectory_from_jsonl(missing) == []

    # ── 6b. Empty file → empty lists. ──────────────────────────────────
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert fold_pnl_from_settled(empty) == []
    assert fold_recent_reflections_from_jsonl(empty) == []
    assert fold_weight_trajectory_from_jsonl(empty) == []

    # ── 6c. PnL with mixed valid / corrupt rows. ───────────────────────
    settled = tmp_path / "settled.jsonl"
    settled.write_text(
        "\n".join(
            [
                '{"pnl_usd": 1.5}',
                '{"pnl_usd": -2.0}',
                '{"pnl_usd": "not-a-number"}',     # skipped (wrong type)
                '{"pnl_usd": true}',                # skipped (bool guard)
                '{"pnl_usd": NaN}',                 # invalid JSON — iter_jsonl skips
                'this is not json at all',          # iter_jsonl skips
                '{"pnl_usd": 3.14}',
                '{"no_pnl_field_here": 0}',         # skipped (missing key)
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pnls = fold_pnl_from_settled(settled, tail=10)
    assert pnls == [1.5, -2.0, 3.14]
    # Tail-trim works on shorter inputs.
    assert fold_pnl_from_settled(settled, tail=2) == [-2.0, 3.14]
    # tail=0 → empty.
    assert fold_pnl_from_settled(settled, tail=0) == []

    # ── 6d. Reflections with empty / non-string narratives. ────────────
    reflections = tmp_path / "reflections.jsonl"
    reflections.write_text(
        "\n".join(
            [
                '{"narrative": "First reflection."}',
                '{"narrative": ""}',                # skipped (empty)
                '{"narrative": "   "}',             # skipped (whitespace)
                '{"narrative": 42}',                # skipped (wrong type)
                '{"narrative": "Second reflection."}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert fold_recent_reflections_from_jsonl(reflections, tail=5) == [
        "First reflection.",
        "Second reflection.",
    ]

    # ── 6e. Weight trajectory with "all same weights" inputs (the brief's
    #       explicit edge case — the no-drift sentinel should still
    #       round-trip, NOT collapse). ───────────────────────────────────
    same_snapshot = {
        "w_r": 0.5,
        "alpha_0": 0.34,
        "alpha_1": 0.33,
        "alpha_2": 0.33,
        "beta_0": 1.0,
        "rho": 0.05,
    }
    weights_file = tmp_path / "weights.jsonl"
    rows = [{"weight_snapshot": same_snapshot} for _ in range(4)]
    # Add one corrupt row (snapshot missing alpha_0) — should be skipped.
    rows.append({"weight_snapshot": {"w_r": 0.5, "rho": 0.05}})
    weights_file.write_text(
        "\n".join(__import__("json").dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    trajectory = fold_weight_trajectory_from_jsonl(weights_file, tail=10)
    assert len(trajectory) == 4
    # All four are byte-equal because the snapshot was identical.
    assert all(w.model_dump() == trajectory[0].model_dump() for w in trajectory)
    # tail=2 keeps the LAST 2.
    assert len(fold_weight_trajectory_from_jsonl(weights_file, tail=2)) == 2


# --------------------------------------------------------------------------- #
# Test 7 — VCR cassette covers 1 happy live Gemini call.
# --------------------------------------------------------------------------- #


CASSETTE_PATH = (
    Path(__file__).parent / "cassettes" / "test_strategy_advisor_impl.yaml"
)


@dataclass
class _CassetteLLMClient:
    """YAML cassette-backed Protocol-conformant LLM stub.

    Mirrors the pattern from
    :class:`tests.agent.llm.test_gemini_smoke_offline._CassetteLLMClient`.
    Each cassette interaction's ``response.body`` is returned directly
    by :meth:`structured_call`; the request envelope is asserted via
    ``prompt_starts_with`` so cassette mismatches surface as test
    failures rather than silent drift.
    """

    cassette_path: Path
    interactions: list[dict[str, Any]] = field(init=False)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def __post_init__(self) -> None:
        raw = yaml.safe_load(self.cassette_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "interactions" not in raw:
            raise AssertionError(
                f"cassette {self.cassette_path} missing 'interactions' key"
            )
        self.interactions = list(raw["interactions"])

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if self._idx >= len(self.interactions):
            raise AssertionError(
                f"cassette {self.cassette_path} exhausted at call "
                f"#{len(self.calls)}"
            )
        interaction = self.interactions[self._idx]
        self._idx += 1
        # Soft assertion on the prompt envelope so cassette drift surfaces.
        starts_with = interaction.get("request", {}).get("prompt_starts_with")
        if isinstance(starts_with, str) and not prompt.startswith(starts_with):
            raise AssertionError(
                f"cassette prompt drift at call #{len(self.calls)}: "
                f"expected starts_with={starts_with!r}, got prefix={prompt[:80]!r}"
            )
        body = interaction.get("response", {}).get("body")
        if not isinstance(body, dict):
            raise AssertionError(
                f"cassette interaction #{self._idx - 1} response.body must be a dict"
            )
        return cast(dict[str, Any], body)


def test_vcr_cassette_happy_call() -> None:
    """One cassette-driven Gemini call → two parsed StrategyProposals."""
    cassette = _CassetteLLMClient(cassette_path=CASSETTE_PATH)
    cost_guard = L3CostGuard(hard_cap_usd=1.0)
    advisor = StrategyAdvisorImpl(
        llm_client=cassette,
        cost_guard=cost_guard,
        per_call_usd_estimate=0.006,
    )

    result = advisor.review_window(_build_window())

    assert len(result) == 2
    kinds = [p.kind for p in result]
    assert kinds == ["weight_delta", "prompt_tweak"]
    # First proposal: weight_delta on alpha_2.
    assert result[0].proposed_change == {"key": "alpha_2", "delta": -0.03}
    assert result[0].confidence_pct == 62
    # Second proposal: prompt_tweak with target.
    assert result[1].proposed_change.get("target") == "L2_reflection"
    assert result[1].confidence_pct == 71
    # Both flagged for human approval.
    assert all(p.requires_human_approval for p in result)
    # Cost guard charged once for the live call.
    assert len(cost_guard.events) == 1
    assert cost_guard.events[0].usd == 0.006
    # Cassette consumed exactly one interaction.
    assert len(cassette.calls) == 1


# --------------------------------------------------------------------------- #
# Test 8 — StrategyAdvisorImpl satisfies the sprint_9 Protocol.
# --------------------------------------------------------------------------- #


def test_swap_with_existing_loop_scaffold() -> None:
    """Impl structurally satisfies the sprint_9 ``StrategyAdvisor`` Protocol.

    The sprint_9 swap test in
    :mod:`tests.agent.engines.test_strategy_advisor_scaffold` proves the
    loop accepts any object structurally implementing
    ``review_window(window: PerformanceWindow) -> list[StrategyProposal]``.
    This test asserts :class:`StrategyAdvisorImpl` qualifies — mypy's
    structural-subtyping verifier signs off at the assignment site.
    """
    fake = _FakeLLM(responses=[{"proposals": []}])
    advisor, _ = _make_advisor(fake=fake)
    # The structural-subtyping check is the assignment itself; mypy
    # --strict verifies it. At runtime we exercise the method on a
    # re-exported window type to confirm the alias from
    # :mod:`agent.engines.strategy_advisor` still routes to the same
    # :class:`PerformanceWindow`.
    typed: StrategyAdvisor = advisor
    result = typed.review_window(_build_window())
    assert result == []
    # The re-exported alias is the SAME class as the impl-module one
    # (sprint_10 move-and-re-export, see strategy_advisor.py header).
    assert ReexportedPerformanceWindow is PerformanceWindow


# --------------------------------------------------------------------------- #
# T-D-018 — opt-in STRICT weight_delta-only mode (survival self-evolution sim).
# --------------------------------------------------------------------------- #


def _make_strict_advisor(
    *,
    fake: _FakeLLM,
    cap_usd: float = 1.0,
    per_call_usd: float = 0.006,
) -> tuple[StrategyAdvisorImpl, L3CostGuard]:
    """Build a STRICT (``weight_delta_only=True``) advisor + matching guard."""
    cost_guard = L3CostGuard(hard_cap_usd=cap_usd)
    advisor = StrategyAdvisorImpl(
        llm_client=fake,
        cost_guard=cost_guard,
        per_call_usd_estimate=per_call_usd,
        weight_delta_only=True,
    )
    return advisor, cost_guard


def _wd_item(**overrides: Any) -> dict[str, Any]:
    """One well-shaped weight_delta wrapper item; override per test."""
    item: dict[str, Any] = {
        "kind": "weight_delta",
        "rationale": "alpha_0 looks stale while PnL is negative.",
        "proposed_change": {"key": "alpha_0", "delta": 0.04},
        "expected_impact": "Rebalance toward the resolver signal.",
        "confidence_pct": 60,
    }
    item.update(overrides)
    return item


def test_strict_mode_uses_weight_delta_schema() -> None:
    """``weight_delta_only=True`` passes the strict (weight_delta-only) schema."""
    fake = _FakeLLM(responses=[{"proposals": [_wd_item()]}])
    advisor, _ = _make_strict_advisor(fake=fake)
    advisor.review_window(_build_window())
    assert len(fake.calls) == 1
    assert fake.calls[0]["schema"] is WEIGHT_DELTA_RESPONSE_SCHEMA
    # Sanity: the strict schema locks kind to a single-value enum.
    item_schema = fake.calls[0]["schema"]["properties"]["proposals"]["items"]
    assert item_schema["properties"]["kind"]["enum"] == ["weight_delta"]


def test_strict_mode_empty_proposed_change_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A weight_delta with ``proposed_change={}`` is DROPPED (the orig bug)."""
    fake = _FakeLLM(responses=[{"proposals": [_wd_item(proposed_change={})]}])
    advisor, _ = _make_strict_advisor(fake=fake)
    with caplog.at_level(
        logging.WARNING, logger="agent.engines.strategy_advisor_impl"
    ):
        result = advisor.review_window(_build_window())
    assert result == [], "empty proposed_change must never survive strict mode"


def test_strict_mode_valid_delta_returned_and_coerced() -> None:
    """A valid ``{key, delta}`` is returned with the delta coerced to float."""
    # A schema-valid integer literal (JSON "type":"number" permits it).
    fake = _FakeLLM(
        responses=[
            {"proposals": [_wd_item(proposed_change={"key": "w_r", "delta": 0})]}
        ]
    )
    advisor, _ = _make_strict_advisor(fake=fake)
    result = advisor.review_window(_build_window())
    assert len(result) == 1
    assert result[0].kind == "weight_delta"
    assert result[0].proposed_change["key"] == "w_r"
    delta = result[0].proposed_change["delta"]
    assert isinstance(delta, float) and delta == 0.0


def test_strict_mode_bad_key_and_oversized_delta_skipped() -> None:
    """Unknown key OR ``|delta| > 0.1`` → the item is dropped in strict mode."""
    bad_key = _FakeLLM(
        responses=[
            {"proposals": [_wd_item(proposed_change={"key": "nope", "delta": 0.02})]}
        ]
    )
    advisor_a, _ = _make_strict_advisor(fake=bad_key)
    assert advisor_a.review_window(_build_window()) == []

    too_big = _FakeLLM(
        responses=[
            {"proposals": [_wd_item(proposed_change={"key": "rho", "delta": 0.2})]}
        ]
    )
    advisor_b, _ = _make_strict_advisor(fake=too_big)
    assert advisor_b.review_window(_build_window()) == []


def test_strict_mode_kind_allow_list() -> None:
    """Non-weight_delta kinds are dropped in strict mode but kept in prod mode."""
    other = {
        "kind": "prompt_tweak",
        "rationale": "tighten the L2 reflection prompt.",
        "proposed_change": {"target": "L2_reflection", "after": "..."},
        "expected_impact": "less noise.",
        "confidence_pct": 55,
    }
    # Strict: the prompt_tweak item is rejected → empty.
    strict_fake = _FakeLLM(responses=[{"proposals": [dict(other)]}])
    strict_advisor, _ = _make_strict_advisor(fake=strict_fake)
    assert strict_advisor.review_window(_build_window()) == []

    # Default (prod) mode: the SAME item is accepted (3-kind contract intact).
    prod_fake = _FakeLLM(responses=[{"proposals": [dict(other)]}])
    prod_advisor, _ = _make_advisor(fake=prod_fake)
    prod_result = prod_advisor.review_window(_build_window())
    assert len(prod_result) == 1
    assert prod_result[0].kind == "prompt_tweak"


def test_strict_mode_inclusive_delta_bound() -> None:
    """The bound is INCLUSIVE: ``|delta| == 0.1`` accepted; just over → dropped."""
    at_bound = _FakeLLM(
        responses=[
            {"proposals": [_wd_item(proposed_change={"key": "rho", "delta": 0.1})]}
        ]
    )
    advisor_a, _ = _make_strict_advisor(fake=at_bound)
    assert len(advisor_a.review_window(_build_window())) == 1

    just_over = _FakeLLM(
        responses=[
            {
                "proposals": [
                    _wd_item(proposed_change={"key": "rho", "delta": 0.1000001})
                ]
            }
        ]
    )
    advisor_b, _ = _make_strict_advisor(fake=just_over)
    assert advisor_b.review_window(_build_window()) == []
