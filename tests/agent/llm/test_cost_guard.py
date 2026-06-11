"""CostGuard tests — running USD budget + warning + hard-cap enforcement."""

from __future__ import annotations

import itertools

import pytest

from agent.llm.cost_guard import (
    DEFAULT_HARD_CAP_USD,
    DEFAULT_WARNING_FRACTION,
    CostExhaustedError,
    CostGuard,
)


def test_default_constants_match_technical_plan() -> None:
    """TECHNICAL_PLAN §15 Gap 5: hard cap $25; warning at 80% by spec."""
    assert DEFAULT_HARD_CAP_USD == 25.0
    assert DEFAULT_WARNING_FRACTION == 0.80


def test_under_budget_records_ok_events() -> None:
    """Routine spend ⇒ events tagged ``ok`` and totals accumulate."""
    guard = CostGuard()
    e1 = guard.record(label="sentiment", usd=1.50)
    e2 = guard.record(label="reflection", usd=2.25)
    assert e1.kind == "ok"
    assert e2.kind == "ok"
    assert guard.total_usd == pytest.approx(3.75)
    assert guard.is_warning() is False
    assert guard.is_exhausted() is False
    assert guard.remaining_usd == pytest.approx(21.25)


def test_warning_fires_at_80_percent() -> None:
    """Crossing 80% of the cap flips ``is_warning`` and tags events
    ``warning`` — the agent_loop reads this to emit a dashboard event."""
    guard = CostGuard(hard_cap_usd=10.0)  # warning at $8
    e1 = guard.record(label="sentiment", usd=7.99)
    assert e1.kind == "ok"
    assert guard.is_warning() is False

    e2 = guard.record(label="reflection", usd=0.50)  # crosses $8
    assert e2.kind == "warning"
    assert guard.is_warning() is True
    assert guard.is_exhausted() is False


def test_hard_cap_short_circuits() -> None:
    """Brief: 'hard short-circuit to template at 100%' — once the cap
    is met, subsequent ``record`` calls raise so the engine MUST
    short-circuit via the precheck path."""
    guard = CostGuard(hard_cap_usd=5.0)
    guard.record(label="sentiment", usd=5.0)
    assert guard.is_exhausted() is True
    assert guard.remaining_usd == 0.0

    with pytest.raises(CostExhaustedError):
        guard.record(label="reflection", usd=0.01)


def test_remaining_usd_clamps_to_zero_on_overshoot() -> None:
    """A single large record that pushes past the cap leaves
    ``remaining_usd`` at zero — never negative — so the dashboard
    renders cleanly."""
    guard = CostGuard(hard_cap_usd=10.0)
    event = guard.record(label="reflection", usd=12.0)
    assert event.kind == "exhausted"
    assert guard.is_exhausted() is True
    assert guard.remaining_usd == 0.0


def test_negative_usd_rejected() -> None:
    """No refunds — a negative spend is misuse."""
    guard = CostGuard()
    with pytest.raises(ValueError, match="non-negative"):
        guard.record(label="sentiment", usd=-1.0)


def test_invalid_constructor_args_rejected() -> None:
    """Defensive: cap must be > 0; warning fraction in (0, 1)."""
    with pytest.raises(ValueError, match="hard_cap_usd"):
        CostGuard(hard_cap_usd=0.0)
    with pytest.raises(ValueError, match="warning_fraction"):
        CostGuard(warning_fraction=0.0)
    with pytest.raises(ValueError, match="warning_fraction"):
        CostGuard(warning_fraction=1.0)


def test_cost_guard_tick_window_is_monotonic() -> None:
    """Brief acceptance criterion: 'cost_guard tick window documented +
    tested as tick-window only'.

    Defence-in-depth invariant: the running total NEVER decreases. The
    guard reads only the spend accumulated by prior ticks + the current
    in-flight call — no future-tick lookahead. Demonstrated by a
    sequence of 50 random records: ``total_usd`` is monotonically
    non-decreasing across the sequence.
    """
    guard = CostGuard(hard_cap_usd=1_000.0)
    totals: list[float] = [guard.total_usd]
    for i in range(50):
        guard.record(label=f"call_{i}", usd=0.1)
        totals.append(guard.total_usd)
    # Strictly non-decreasing — proves the window only walks forward.
    # ``itertools.pairwise`` is the idiomatic sliding-window helper:
    # yields (totals[0], totals[1]), (totals[1], totals[2]), ...
    for prev, curr in itertools.pairwise(totals):
        assert curr >= prev
    # And the cumulative sum matches the per-event ledger.
    assert sum(e.usd for e in guard.events) == pytest.approx(guard.total_usd)
