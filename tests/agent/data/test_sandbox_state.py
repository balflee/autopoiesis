"""Tests for :mod:`agent.data.sandbox_state` — append-only + atomic snapshot.

Covers the five architectural invariants:

1. **Append-only**: re-reading the JSONL after N appends returns exactly
   N records in insertion order.
2. **Atomic snapshot**: mid-write crash leaves the previous snapshot intact.
3. **Concurrent append**: two threads writing to the same writer
   serialise into well-formed whole lines (no half-lines).
4. **Corrupt-line tolerance**: :func:`iter_jsonl` skips malformed
   lines instead of raising.
5. **UTF-8 round-trip**: non-ASCII content survives the append/read cycle.

Plus a smoke test that the Pydantic models reject ``extra=...`` kwargs
(schema-drift guard).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.data.sandbox_state import (
    AgentStateSnapshot,
    BetRecord,
    DecisionRecord,
    SandboxStateWriter,
    SettledBetRecord,
    iter_jsonl,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def writer(tmp_path: Path) -> SandboxStateWriter:
    """Writer rooted at a pytest tmp_path so the real
    ``state/sandbox/`` is never touched."""
    return SandboxStateWriter(root=tmp_path / "sandbox")


def _make_bet(bet_id: str = "abc", market_id: str = "m1") -> BetRecord:
    return BetRecord(
        bet_id=bet_id,
        ts="2026-05-26T20:00:00+00:00",
        market_id=market_id,
        side="YES",
        price=0.55,
        size_usd=10.0,
        expected_settle_ts="2026-05-26T22:00:00+00:00",
        status="open",
    )


def _make_decision(tick: int = 0) -> DecisionRecord:
    return DecisionRecord(
        tick=tick,
        ts="2026-05-26T20:00:00+00:00",
        market_id="m1",
        kind="NO_BET",
        size_usd=0.0,
        side=None,
        edge_pct=None,
        no_bet_reason="edge_below_threshold",
        breath_after=95.0,
        bankroll_usd_after=100.0,
    )


# --------------------------------------------------------------------------- #
# 1. Append-only invariant via re-read.
# --------------------------------------------------------------------------- #


def test_append_only_invariant_open_bets(writer: SandboxStateWriter) -> None:
    """N appends → exactly N records, preserving insertion order."""
    bets = [_make_bet(bet_id=f"b{i}") for i in range(5)]
    for b in bets:
        writer.append_open_bet(b)
    records = iter_jsonl(writer.open_bets_path)
    assert len(records) == 5
    assert [r["bet_id"] for r in records] == ["b0", "b1", "b2", "b3", "b4"]


def test_append_only_invariant_decisions(writer: SandboxStateWriter) -> None:
    """Decisions: same append-only invariant."""
    for t in range(3):
        writer.append_decision(_make_decision(tick=t))
    records = iter_jsonl(writer.decisions_path)
    assert len(records) == 3
    assert [r["tick"] for r in records] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# 2. Atomic snapshot — temp file + os.replace.
# --------------------------------------------------------------------------- #


def test_atomic_snapshot_survives_overwrite(writer: SandboxStateWriter) -> None:
    """write_snapshot is atomic: the file is either fully old or fully new."""
    s1 = AgentStateSnapshot(
        snapshot_ts="2026-05-26T20:00:00+00:00",
        phase="PHASE_2_APPRENTICE",
        breath=100.0,
        bankroll_usd=100.0,
        phase_age_days=0.5,
        open_bet_ids=["a"],
        last_tick=0,
    )
    writer.write_snapshot(s1)
    assert writer.snapshot_path.exists()
    payload1 = json.loads(writer.snapshot_path.read_text(encoding="utf-8"))
    assert payload1["last_tick"] == 0

    s2 = s1.model_copy(update={"last_tick": 1, "breath": 95.5})
    writer.write_snapshot(s2)
    payload2 = json.loads(writer.snapshot_path.read_text(encoding="utf-8"))
    assert payload2["last_tick"] == 1
    assert payload2["breath"] == 95.5

    # No stray .tmp files left over.
    tmp_files = list(writer.root.glob(".agent_state.*.tmp"))
    assert tmp_files == [], f"snapshot writer leaked temp files: {tmp_files}"


def test_atomic_snapshot_partial_write_tolerated(
    writer: SandboxStateWriter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If os.replace fails AFTER the tmp file is written, the previous
    snapshot remains intact and the tmp file is cleaned up."""
    s1 = AgentStateSnapshot(
        snapshot_ts="2026-05-26T20:00:00+00:00",
        phase="PHASE_2_APPRENTICE",
        breath=100.0,
        bankroll_usd=100.0,
        phase_age_days=0.5,
    )
    writer.write_snapshot(s1)
    original_payload = writer.snapshot_path.read_text(encoding="utf-8")

    # Patch os.replace to raise — simulates a partial write.
    def _boom(_src: object, _dst: object) -> None:
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr("agent.data.sandbox_state.os.replace", _boom)

    s2 = s1.model_copy(update={"breath": 50.0})
    with pytest.raises(OSError, match="simulated"):
        writer.write_snapshot(s2)

    # Previous snapshot unchanged.
    assert writer.snapshot_path.read_text(encoding="utf-8") == original_payload
    # Tmp file cleaned up.
    tmp_files = list(writer.root.glob(".agent_state.*.tmp"))
    assert tmp_files == []


# --------------------------------------------------------------------------- #
# 3. Concurrent append from 2 threads — serialisable.
# --------------------------------------------------------------------------- #


def test_concurrent_append_serialises_into_whole_lines(
    writer: SandboxStateWriter,
) -> None:
    """Two threads each append 50 lines → file has 100 well-formed lines."""
    N = 50

    def _worker(tag: str) -> None:
        for i in range(N):
            writer.append_open_bet(_make_bet(bet_id=f"{tag}-{i}"))

    t1 = threading.Thread(target=_worker, args=("a",))
    t2 = threading.Thread(target=_worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Read raw — confirms every line is well-formed JSON (no half-writes).
    raw_lines = writer.open_bets_path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2 * N
    parsed = [json.loads(ln) for ln in raw_lines]
    bet_ids = {r["bet_id"] for r in parsed}
    expected = {f"a-{i}" for i in range(N)} | {f"b-{i}" for i in range(N)}
    assert bet_ids == expected


# --------------------------------------------------------------------------- #
# 4. Corrupt-line tolerance — iter_jsonl skips bad lines.
# --------------------------------------------------------------------------- #


def test_iter_jsonl_skips_corrupt_lines(writer: SandboxStateWriter) -> None:
    """Manually inject a bad line; iter_jsonl returns only good records."""
    writer.append_open_bet(_make_bet(bet_id="good-1"))
    # Bypass writer to simulate a corrupt downstream tail (filesystem
    # crash mid-write). Sandbox writer NEVER produces these in normal
    # operation; this is the dashboard-side robustness test.
    with open(writer.open_bets_path, "a", encoding="utf-8") as f:
        f.write("this-is-not-json\n")
        f.write('{"truncated": "no closing brace"\n')
    writer.append_open_bet(_make_bet(bet_id="good-2"))

    records = iter_jsonl(writer.open_bets_path)
    bet_ids = [r["bet_id"] for r in records]
    assert bet_ids == ["good-1", "good-2"]


def test_iter_jsonl_returns_empty_on_missing_file(tmp_path: Path) -> None:
    """Missing file → empty list, not FileNotFoundError."""
    assert iter_jsonl(tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------------------- #
# 5. UTF-8 round-trip — emoji, CJK, accented chars all survive.
# --------------------------------------------------------------------------- #


def test_utf8_round_trip(writer: SandboxStateWriter) -> None:
    """Non-ASCII payload survives append + read."""
    decision = DecisionRecord(
        tick=0,
        ts="2026-05-26T20:00:00+00:00",
        market_id="m1",
        kind="NO_BET",
        size_usd=0.0,
        side=None,
        edge_pct=None,
        no_bet_reason="赛前情报不足 / sentiment too noisy 😬",
        breath_after=95.0,
        bankroll_usd_after=100.0,
    )
    writer.append_decision(decision)
    records = iter_jsonl(writer.decisions_path)
    assert len(records) == 1
    assert records[0]["no_bet_reason"] == "赛前情报不足 / sentiment too noisy 😬"


# --------------------------------------------------------------------------- #
# Schema-drift guard — extra='forbid' on every model.
# --------------------------------------------------------------------------- #


def test_models_forbid_extra_fields() -> None:
    """Pydantic ``extra='forbid'`` is what catches schema drift at write time."""
    with pytest.raises(ValidationError):
        BetRecord(  # type: ignore[call-arg]
            bet_id="x",
            ts="2026-05-26T20:00:00+00:00",
            market_id="m1",
            side="YES",
            price=0.5,
            size_usd=1.0,
            expected_settle_ts="2026-05-26T22:00:00+00:00",
            status="open",
            extra_evil_field="oops",
        )

    with pytest.raises(ValidationError):
        SettledBetRecord(  # type: ignore[call-arg]
            bet_id="x",
            market_id="m1",
            settled_ts="2026-05-26T22:00:00+00:00",
            outcome="yes",
            winning_price=1.0,
            pnl_usd=5.0,
            status="settled",
            mystery="bad",
        )


def test_settled_bet_round_trip(writer: SandboxStateWriter) -> None:
    """SettledBetRecord append + read."""
    s = SettledBetRecord(
        bet_id="x",
        market_id="m1",
        settled_ts="2026-05-26T22:00:00+00:00",
        outcome="yes",
        winning_price=1.0,
        pnl_usd=5.0,
    )
    writer.append_settled_bet(s)
    records = iter_jsonl(writer.settled_bets_path)
    assert len(records) == 1
    assert records[0]["bet_id"] == "x"
    assert records[0]["outcome"] == "yes"


def test_writer_creates_root_directory(tmp_path: Path) -> None:
    """Writer constructor creates the directory if missing."""
    root = tmp_path / "nested" / "sandbox"
    assert not root.exists()
    SandboxStateWriter(root=root)
    assert root.is_dir()
