# tests/agent/backtest/test_survival_tick_source.py
"""A1 — entry-time-ordered cached tick source + controllable clock.

The L5 survival runner drives ``SandboxPhase2Loop`` directly (NOT
``run_replay``'s round-robin ``_ReplayTickInputSource``). Decisions are
scheduled by **ENTRY time** (``SurvivalRow.entry_asof_ts_iso``), NOT settlement
time, because the loop uses the SAME ``now`` for the settlement poll AND the
decision (``sandbox_phase2_loop.py:1431,:1448,:1484``). As the controllable
clock advances forward to each entry time:

* the loop's poller settles any due open bets "in flight" (whose
  ``expected_settle_ts = end_date_iso + settle_lag`` is STRICTLY < now,
  ``sandbox_settlement_poller.py:474``);
* the tick source serves that market's cached signals (no Sackmann recompute —
  reuses ``cached_sweep.row_to_signals``) as ``TickInputs``.

The ``_tick()`` seam has NO clean settle-only path (it always polls THEN
decides, ``sandbox_phase2_loop.py:1447,:1484``), so settle-only advancement is
modelled as a **no-market tick**: the source returns ``None`` →
``no_bet_reason="no_eligible_market"`` (``sandbox_phase2_loop.py:1489-1492``);
the A3 recorder later filters these synthetic NO_BETs.

TDD on a tiny 3-market schedule (NOT the 4925-row cache).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent.backtest.cached_sweep import SignalRow
from agent.backtest.survival_season import (
    SurvivalRow,
    SurvivalSchedule,
    SurvivalTickSource,
    _ControllableClock,
    build_survival_schedule,
)
from agent.engines.base import Signal
from agent.runtime.sandbox_phase2_loop import TickInputs

_SLOTS = (
    "tennis_technical",
    "market_momentum",
    "smart_money",
    "sentiment_llm",
    "crowd_volume",
)

# Match the executor default so the schedule's settle checkpoints line up with
# what the loop's poller will actually see (end_date + 2h).
_SETTLE_LAG = timedelta(hours=2)


def _row(
    market_id: str,
    *,
    entry_asof: str,
    end_date: str,
    resolution: str,
    entry_price: float = 0.5,
    score: float = 0.3,
) -> SurvivalRow:
    signal = SignalRow(
        market_id=market_id,
        slug=f"slug-{market_id}",
        scores={k: score for k in _SLOTS},
        confidences={k: 0.8 for k in _SLOTS},
        entry_price=entry_price,
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=20.0,
    )
    return SurvivalRow(
        market_id=market_id,
        slug=f"slug-{market_id}",
        signal=signal,
        entry_asof_ts_iso=entry_asof,
        resolution_ts_iso=resolution,
        end_date_iso=end_date,
        outcome="yes",
        winning_price=1.0,
        liquidity_cap=20.0,
        players=("alpha", "bravo"),
        surface="Hard",
    )


def _rows() -> list[SurvivalRow]:
    # Deliberately OUT of entry order on input so the schedule sort is exercised.
    # mB enters first (06-01), mA second (06-03), mC third (06-10).
    return [
        _row(
            "mA",
            entry_asof="2025-06-03T00:00:00+00:00",
            end_date="2025-06-03T12:00:00+00:00",
            resolution="2025-06-03T20:00:00+00:00",
            entry_price=0.40,
            score=0.6,
        ),
        _row(
            "mB",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
            entry_price=0.55,
            score=0.2,
        ),
        _row(
            "mC",
            entry_asof="2025-06-10T00:00:00+00:00",
            end_date="2025-06-10T12:00:00+00:00",
            resolution="2025-06-10T20:00:00+00:00",
            entry_price=0.48,
            score=-0.1,
        ),
    ]


# --------------------------------------------------------------------------- #
# Controllable clock
# --------------------------------------------------------------------------- #


def test_controllable_clock_returns_cursor_and_advances_within_tick() -> None:
    # The loop reads clock.now() several times WITHIN one tick (until-guard +
    # _tick top + poller + executor); a tiny auto_advance keeps those reads in
    # the same tick window (mirrors replay_runner._CompressedClock). set_to lets
    # the runner pin the cursor to an exact entry/settle time.
    start = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    clock = _ControllableClock(start, within_tick_advance=timedelta(seconds=1))
    first = clock.now()
    second = clock.now()
    assert first == start
    assert second == start + timedelta(seconds=1)  # bumped within the tick

    clock.set_to(datetime(2025, 6, 3, 0, 0, 0, tzinfo=UTC))
    assert clock.now() == datetime(2025, 6, 3, 0, 0, 0, tzinfo=UTC)

    # set_to in the PAST must be rejected — the survival clock only moves
    # forward (a backwards jump would re-settle already-settled bets / lookahead).
    try:
        clock.set_to(datetime(2025, 6, 2, 0, 0, 0, tzinfo=UTC))
    except ValueError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("set_to(past) should raise")


# --------------------------------------------------------------------------- #
# Schedule construction — entry-time order + interleaved settle checkpoints
# --------------------------------------------------------------------------- #


def test_schedule_orders_entries_and_inserts_settle_checkpoints() -> None:
    schedule = build_survival_schedule(_rows(), settle_lag=_SETTLE_LAG)
    assert isinstance(schedule, SurvivalSchedule)

    # The DECISION stops are the entry times in sorted order: mB, mA, mC.
    decision_stops = [s for s in schedule.stops if s.market_id is not None]
    assert [s.market_id for s in decision_stops] == ["mB", "mA", "mC"]
    assert [s.asof_ts for s in decision_stops] == [
        datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC),
        datetime(2025, 6, 3, 0, 0, 0, tzinfo=UTC),
        datetime(2025, 6, 10, 0, 0, 0, tzinfo=UTC),
    ]

    # mB's expected_settle_ts = end_date(06-01T12) + 2h = 06-01T14, and its
    # resolution_ts = 06-01T20. By the time mA is DECIDED (06-03T00) both are
    # already < now, so mB settles in-flight at mA's decision tick — no separate
    # checkpoint needed between them. The schedule MUST be strictly increasing.
    times = [s.asof_ts for s in schedule.stops]
    assert times == sorted(times)
    assert len(set(times)) == len(times)  # strictly increasing, no dupes

    # A FINAL-DRAIN settle-only checkpoint must exist AFTER the last decision,
    # strictly past max(expected_settle_ts, resolution_ts) over ALL markets so
    # mC's bet can settle (else PnL/learning/death are suppressed, codex R6).
    last = schedule.stops[-1]
    assert last.market_id is None  # no-market settle tick
    # mC: max(end+lag=06-10T14, resolution=06-10T20) = 06-10T20; strictly past.
    assert last.asof_ts > datetime(2025, 6, 10, 20, 0, 0, tzinfo=UTC)


def test_no_market_settle_only_checkpoint_between_distant_entries() -> None:
    # Two entries far apart in time, where the first bet's settle time falls
    # strictly BETWEEN the two entries -> a settle-only checkpoint is inserted
    # so the open bet drains in-flight rather than waiting for the next entry.
    rows = [
        _row(
            "mEarly",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T01:00:00+00:00",
            resolution="2025-06-01T02:00:00+00:00",
        ),
        _row(
            "mLate",
            entry_asof="2025-06-20T00:00:00+00:00",
            end_date="2025-06-20T01:00:00+00:00",
            resolution="2025-06-20T02:00:00+00:00",
        ),
    ]
    schedule = build_survival_schedule(rows, settle_lag=_SETTLE_LAG)
    # mEarly's settle time (max(01:00+2h, 02:00) = 03:00 on 06-01) is strictly
    # before mLate's entry (06-20) -> a no-market checkpoint sits between them.
    kinds = [(s.asof_ts, s.market_id) for s in schedule.stops]
    early_entry = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    late_entry = datetime(2025, 6, 20, 0, 0, 0, tzinfo=UTC)
    between = [
        ts for ts, mid in kinds if early_entry < ts < late_entry and mid is None
    ]
    assert between, "expected a settle-only checkpoint between the two entries"
    assert any(ts > datetime(2025, 6, 1, 3, 0, 0, tzinfo=UTC) for ts in between)


# --------------------------------------------------------------------------- #
# Tick source — cached signals, entry order, no-market -> None
# --------------------------------------------------------------------------- #


def test_tick_source_serves_cached_signals_in_entry_order() -> None:
    rows = _rows()
    schedule = build_survival_schedule(rows, settle_lag=_SETTLE_LAG)
    source = SurvivalTickSource(schedule)

    # tick 0 -> first stop is mB's entry (06-01). Cached signals, NOT recomputed.
    out0 = source.inputs_for(
        asof_ts=datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC), tick=0
    )
    assert isinstance(out0, TickInputs)
    assert out0.market_id == "mB"
    assert out0.price == 0.55  # cached entry_price, not a live recompute
    assert out0.liquidity_cap_usd == 20.0
    assert set(out0.signals) == set(_SLOTS)
    assert all(isinstance(s, Signal) for s in out0.signals.values())
    # mB's cached score is 0.2 across the 5 slots.
    assert out0.signals["smart_money"].score == 0.2

    # The next DECISION stop is mA (06-03). Find its tick index in the schedule.
    decision_ticks = [
        i for i, s in enumerate(schedule.stops) if s.market_id == "mA"
    ]
    assert decision_ticks, "mA must be a decision stop"
    ta = decision_ticks[0]
    outa = source.inputs_for(
        asof_ts=schedule.stops[ta].asof_ts, tick=ta
    )
    assert isinstance(outa, TickInputs)
    assert outa.market_id == "mA"
    assert outa.price == 0.40
    assert outa.signals["smart_money"].score == 0.6


def test_tick_source_returns_none_on_settle_only_tick() -> None:
    # A no-market (settle-only) stop -> inputs_for returns None, which the loop
    # routes to NO_BET via no_eligible_market (no real decision fired).
    rows = [
        _row(
            "mEarly",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T01:00:00+00:00",
            resolution="2025-06-01T02:00:00+00:00",
        ),
        _row(
            "mLate",
            entry_asof="2025-06-20T00:00:00+00:00",
            end_date="2025-06-20T01:00:00+00:00",
            resolution="2025-06-20T02:00:00+00:00",
        ),
    ]
    schedule = build_survival_schedule(rows, settle_lag=_SETTLE_LAG)
    source = SurvivalTickSource(schedule)
    settle_ticks = [
        i for i, s in enumerate(schedule.stops) if s.market_id is None
    ]
    assert settle_ticks, "expected at least one settle-only stop"
    for i in settle_ticks:
        assert source.inputs_for(asof_ts=schedule.stops[i].asof_ts, tick=i) is None


def test_tick_source_out_of_range_tick_returns_none() -> None:
    # A tick index past the end of the schedule (loop ran more ticks than stops)
    # -> None (NO_BET), never an IndexError.
    schedule = build_survival_schedule(_rows(), settle_lag=_SETTLE_LAG)
    source = SurvivalTickSource(schedule)
    n = len(schedule.stops)
    assert source.inputs_for(asof_ts=datetime(2099, 1, 1, tzinfo=UTC), tick=n) is None
    assert (
        source.inputs_for(asof_ts=datetime(2099, 1, 1, tzinfo=UTC), tick=n + 5)
        is None
    )


# --------------------------------------------------------------------------- #
# Review-fix regressions — scheduling edge cases the algorithm can ITSELF
# generate (tied entries, checkpoint/next-entry collisions, 1s-apart stops).
# All previously masked because the original happy-path tests put stops days
# apart.
# --------------------------------------------------------------------------- #


def test_tied_entry_times_keep_both_decision_stops() -> None:
    # Two markets with the IDENTICAL entry_asof_ts_iso are entirely plausible
    # across the real cache (entry asof is a chosen PricePoint.ts and markets
    # share cassette tick boundaries). The strictly-increasing invariant must
    # dedupe redundant SETTLE-ONLY checkpoints only — it must NEVER drop a
    # DECISION stop. Two decisions at the same cassette instant are legal: they
    # are separate ticks, and the within-tick clock advances between them.
    rows = [
        _row(
            "mA",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
        _row(
            "mB",
            entry_asof="2025-06-01T00:00:00+00:00",  # TIED with mA
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
    ]
    schedule = build_survival_schedule(rows, settle_lag=_SETTLE_LAG)
    decision_markets = [
        s.market_id for s in schedule.stops if s.market_id is not None
    ]
    # BOTH markets must get a decision stop (broken by market_id for order).
    assert decision_markets == ["mA", "mB"]
    # The schedule timestamps stay strictly increasing so the runner can always
    # pin the clock FORWARD to each stop (no set_to(past) crash). The tied entry
    # is nudged a hair forward — sub-second, never crossing a real market.
    times = [s.asof_ts for s in schedule.stops]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    # The nudge stays within the same cassette instant (well under a minute).
    mb_stop = next(s for s in schedule.stops if s.market_id == "mB")
    assert mb_stop.asof_ts >= datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    assert mb_stop.asof_ts < datetime(2025, 6, 1, 0, 1, 0, tzinfo=UTC)


def test_settle_checkpoint_never_overshoots_next_entry() -> None:
    # A due settle time within the margin of the NEXT entry must NOT produce a
    # checkpoint at/after that entry (which the strictly-increasing guard would
    # then silently drop, killing the next decision). mEarly's due settle =
    # max(end 00:00 +2h, resolution 02:00) = 02:00; mLate enters at 02:00:01 —
    # only the +1s margin away. The checkpoint must be clamped strictly below
    # mLate's entry (or skipped), and mLate's decision stop MUST survive.
    rows = [
        _row(
            "mEarly",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T00:00:00+00:00",
            resolution="2025-06-01T02:00:00+00:00",
        ),
        _row(
            "mLate",
            entry_asof="2025-06-01T02:00:01+00:00",
            end_date="2025-06-01T05:00:00+00:00",
            resolution="2025-06-01T06:00:00+00:00",
        ),
    ]
    schedule = build_survival_schedule(rows, settle_lag=_SETTLE_LAG)
    decision_markets = [
        s.market_id for s in schedule.stops if s.market_id is not None
    ]
    assert decision_markets == ["mEarly", "mLate"]
    times = [s.asof_ts for s in schedule.stops]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    # No stop may land AT-OR-AFTER mLate's entry except mLate's own decision.
    late_entry = datetime(2025, 6, 1, 2, 0, 1, tzinfo=UTC)
    at_or_after = [
        s for s in schedule.stops if s.asof_ts >= late_entry and s.market_id != "mLate"
    ]
    # Only the trailing final-drain checkpoint (after mLate, past its settle).
    assert all(s.market_id is None and s.asof_ts > late_entry for s in at_or_after)


def test_schedule_min_inter_stop_gap_exceeds_within_tick_drift() -> None:
    # The algorithm's own minimum inter-stop gap must be strictly GREATER than
    # the max within-tick clock drift, OR the controllable clock must tolerate a
    # forward-within-window pin — otherwise pinning the next stop a hair ahead
    # after the cursor auto-advanced 2-4x in one tick calls set_to(past) and
    # crashes the run. Build tied entries (the tightest stops the algorithm can
    # emit) and drive set_to over every stop with a realistic per-tick drift.
    from agent.backtest.survival_season import _ControllableClock

    rows = [
        _row(
            "mA",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
        _row(
            "mB",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
        _row(
            "mC",
            entry_asof="2025-06-01T00:00:00+00:00",
            end_date="2025-06-01T12:00:00+00:00",
            resolution="2025-06-01T20:00:00+00:00",
        ),
    ]
    schedule = build_survival_schedule(rows, settle_lag=_SETTLE_LAG)
    clock = _ControllableClock(
        schedule.stops[0].asof_ts, within_tick_advance=timedelta(seconds=1)
    )
    # Replay the runner's pin-then-tick loop. Each tick reads now() up to 4x
    # (run() until-guard + _tick top + poller + executor order-stamp).
    for stop in schedule.stops:
        clock.set_to(stop.asof_ts)  # must NOT raise set_to(past)
        for _ in range(4):
            clock.now()


def test_controllable_clock_tolerates_forward_within_window_pin() -> None:
    # set_to validates the next pin against the PINNED BASE, not the cursor that
    # the within-tick reads auto-advanced. So a forward pin a hair ahead of the
    # base succeeds even after the cursor crept past it inside the prior tick.
    from agent.backtest.survival_season import _ControllableClock

    base = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    clock = _ControllableClock(base, within_tick_advance=timedelta(seconds=1))
    # Simulate one tick: 4 now() reads drift the scratch cursor +4s.
    for _ in range(4):
        clock.now()
    # The next stop is only +1s from the base — well behind the drifted cursor,
    # but still strictly forward of the base. It MUST be accepted.
    clock.set_to(base + timedelta(seconds=1))
    assert clock.now() == base + timedelta(seconds=1)
    # A pin strictly BEFORE the base is still rejected (true backwards move).
    try:
        clock.set_to(base - timedelta(seconds=1))
    except ValueError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("set_to before the base must still raise")


# --------------------------------------------------------------------------- #
# Task 5 — cross_market_signal threading through inputs_for (H3 seam).
# --------------------------------------------------------------------------- #


def test_inputs_for_threads_cross_market_signal_from_signal_row() -> None:
    """``SurvivalTickSource.inputs_for`` must carry ``row.signal.cross_market_signal``
    into the returned ``TickInputs.cross_market_signal`` so the live-learner
    ``SandboxPhase2Loop.decide()`` receives the signal in value mode.

    Uses a row with ``cross_market_signal=0.5`` (non-zero, distinguishable from
    the default ``0.0``) so the assertion is not vacuously true.
    """
    xm_signal = SignalRow(
        market_id="mXm",
        slug="slug-mXm",
        scores={k: 0.4 for k in _SLOTS},
        confidences={k: 0.7 for k in _SLOTS},
        entry_price=0.45,
        outcome="yes",
        winning_price=1.0,
        liquidity_cap_usd=15.0,
        cross_market_signal=0.5,  # non-zero to make the test non-vacuous
    )
    row = SurvivalRow(
        market_id="mXm",
        slug="slug-mXm",
        signal=xm_signal,
        entry_asof_ts_iso="2025-08-01T00:00:00+00:00",
        resolution_ts_iso="2025-08-01T20:00:00+00:00",
        end_date_iso="2025-08-01T12:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        liquidity_cap=15.0,
        players=("alpha", "bravo"),
        surface="Hard",
    )
    schedule = build_survival_schedule([row], settle_lag=_SETTLE_LAG)
    source = SurvivalTickSource(schedule)
    decision_ticks = [
        i for i, s in enumerate(schedule.stops) if s.market_id == "mXm"
    ]
    assert decision_ticks, "mXm must appear as a decision stop"
    ti = source.inputs_for(
        asof_ts=schedule.stops[decision_ticks[0]].asof_ts,
        tick=decision_ticks[0],
    )
    assert isinstance(ti, TickInputs)
    assert ti.cross_market_signal == row.signal.cross_market_signal  # 0.5
