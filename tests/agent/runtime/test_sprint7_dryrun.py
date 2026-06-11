"""Unit tests for the sprint_7 Day-6-closer Phase 2 dry-run harness.

Covers the four CEO acceptance criteria:

* (a) weights_v0.json loads via Phase2LaunchOrchestrator without exception.
* (b) ≥3 decisions emitted (BET + NO_BET both count).
* (c) Each decision payload validates against decision_record.v0.2.0.json.
* (d) ≥1 decision references a real Polymarket tennis market (verified
  via a Polymarket gamma-api JSON shape — tests pin a deterministic
  fake response so the suite stays offline).

Plus:
* DryRunExecutor.broadcast_count == 0 — no signed orders.
* No `anthropic` / `openai` import on the runtime path (covered
  separately by the project-wide AST scanner).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent.runtime.sprint7_dryrun import (
    DryRunExecutor,
    TennisMarket,
    _build_decision_record,
    _validate_decision_record,
    discover_tennis_markets,
    run_dryrun,
)
from agent.core.state import Action, ActionKind, Side


FIXED_TS = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
# Sample gamma-api response payload (truncated from a live 2026-05-26 fetch
# of https://gamma-api.polymarket.com/events?tag_slug=tennis). Keeps the
# suite hermetic; the conditionId values are real on-chain identifiers.
FAKE_GAMMA_TENNIS_EVENTS: list[dict[str, Any]] = [
    {
        "title": "2026 Men's Australian Open Winner",
        "slug": "2026-mens-australian-open-winner",
        "markets": [
            {
                "conditionId": "0xdc0577fd42c17619aa53d8b6a493e3f002abbcbc514c2029b3eea96e8765da6e",
                "slug": "will-jack-draper-win-the-2026-australian-open",
                "question": "Will Jack Draper win the 2026 Australian Open?",
                "outcomePrices": "[\"0.04\", \"0.96\"]",
                "endDate": "2026-01-25T08:30:00Z",
            },
            {
                "conditionId": "0x7fb4301c1ef3b34eddf0d7868d66d1400fa91f529a3cb8f69e47cc47968240b8",
                "slug": "will-alex-de-minaur-win-the-2026-australian-open",
                "question": "Will Alex De Minaur win the 2026 Australian Open?",
                "outcomePrices": "[\"0.05\", \"0.95\"]",
                "endDate": "2026-01-25T08:30:00Z",
            },
        ],
    },
    {
        "title": "2026 Men's French Open Winner",
        "slug": "2026-mens-french-open-winner",
        "markets": [
            {
                "conditionId": "0x23e817f30871533d3bd7da01e68b802c5fccb8f44f053ef4ea5789c8a28563fe",
                "slug": "will-jannik-sinner-win-the-2026-mens-french-open",
                "question": "Will Jannik Sinner win the 2026 Men's French Open?",
                "outcomePrices": "[\"0.30\", \"0.70\"]",
                "endDate": "2026-06-08T12:00:00Z",
            }
        ],
    },
]


@pytest.fixture
def fake_fetcher() -> Any:
    """Return a fetcher that mimics gamma-api responses deterministically."""

    def _fetch(url: str) -> Any:
        if "/events" in url and "tag_slug=tennis" in url:
            return FAKE_GAMMA_TENNIS_EVENTS
        raise AssertionError(f"unexpected fetch url: {url!r}")

    return _fetch


def test_discover_tennis_markets_projects_events_and_submarkets(fake_fetcher: Any) -> None:
    """Discovery flattens gamma-api ``events[].markets[]`` to TennisMarket records."""

    markets = discover_tennis_markets(fetcher=fake_fetcher, limit=5)

    assert len(markets) == 3
    assert all(isinstance(m, TennisMarket) for m in markets)
    assert all(m.condition_id.startswith("0x") for m in markets)
    # outcome_prices was decoded from the JSON-encoded string Polymarket emits.
    assert markets[0].outcome_prices == (0.04, 0.96)
    # Event title / question are carried so the summary can render them.
    assert "Australian Open" in markets[0].event_title
    assert "Jack Draper" in markets[0].question


def test_discover_tennis_markets_returns_empty_on_fetch_failure() -> None:
    """A failing fetcher must surface as an empty list (not raise) so the
    dry-run can route to the heartbeat / idle branch."""

    def _broken(url: str) -> Any:
        raise OSError("simulated DNS failure")

    assert discover_tennis_markets(fetcher=_broken) == []


def test_run_dryrun_satisfies_all_four_criteria(
    tmp_path: Path, fake_fetcher: Any
) -> None:
    """End-to-end: produces JSONL + summary, meets (a)/(b)/(c)/(d)."""

    repo_root = Path.cwd()  # weights + schema files live at repo root
    weights_in = repo_root / "reports" / "phase1" / "weights_v0.json"
    schema_in = repo_root / ".dev" / "contracts" / "decision_record.v0.2.0.json"
    jsonl_out = tmp_path / "logs" / "sprint7_dryrun.jsonl"
    summary_out = tmp_path / "logs" / "sprint7_dryrun_summary.md"
    cache_out = tmp_path / "logs" / "tennis_markets_snapshot.json"

    result = run_dryrun(
        weights_path=weights_in,
        schema_path=schema_in,
        jsonl_out=jsonl_out,
        summary_out=summary_out,
        market_cache_out=cache_out,
        tick_count=5,
        asof_ts=FIXED_TS,
        fetcher=fake_fetcher,
        workspace_root=tmp_path,
    )

    # (a) Phase2LaunchOrchestrator accepted the weights without raising.
    assert result.weights_loaded is True
    assert result.orchestrator_constructed is True

    # (b) ≥3 decisions (BET + NO_BET both count).
    assert result.decisions_count >= 3

    # (c) Each decision payload was validated against the v0.2.0 schema.
    # The JSONL is the only audit surface — reparse it and re-validate
    # every decision row (heartbeat rows carry row_type=heartbeat).
    rows = [
        json.loads(line)
        for line in jsonl_out.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    schema = json.loads(schema_in.read_text(encoding="utf-8"))
    decision_rows = [r for r in rows if "row_type" not in r]
    assert len(decision_rows) >= 3
    assert all(r["kind"] in {"BET", "NO_BET"} for r in decision_rows)
    for row in decision_rows:
        _validate_decision_record(row, schema)

    # (d) At least one decision references a real Polymarket tennis market.
    referenced_ids = {row.get("market_id") for row in decision_rows if "market_id" in row}
    expected_real_ids = {
        m["conditionId"]
        for ev in FAKE_GAMMA_TENNIS_EVENTS
        for m in ev["markets"]
    }
    assert referenced_ids & expected_real_ids, (
        f"no decision referenced a real tennis market; got: {referenced_ids}"
    )
    assert result.real_market_referenced is True

    # Safety: zero broadcasts.
    assert result.broadcast_count == 0

    # Summary markdown was rendered + the four checkmarks are visible.
    summary_text = summary_out.read_text(encoding="utf-8")
    assert "**(a)** `weights_v0.json` loads" in summary_text
    assert "**(b)** ≥3 decisions" in summary_text
    assert "**(c)** Each decision payload" in summary_text
    assert "**(d)** ≥1 decision references a real Polymarket tennis market" in summary_text


def test_run_dryrun_emits_idle_heartbeats_when_no_markets(tmp_path: Path) -> None:
    """Brief: explicit ``no markets found, agent idling`` heartbeat every
    5 sim-min when the discovery surface returns empty."""

    def _no_markets(url: str) -> Any:
        return []

    repo_root = Path.cwd()
    result = run_dryrun(
        weights_path=repo_root / "reports" / "phase1" / "weights_v0.json",
        schema_path=repo_root / ".dev" / "contracts" / "decision_record.v0.2.0.json",
        jsonl_out=tmp_path / "logs" / "sprint7_dryrun.jsonl",
        summary_out=tmp_path / "logs" / "summary.md",
        market_cache_out=tmp_path / "logs" / "cache.json",
        tick_count=3,
        asof_ts=FIXED_TS,
        fetcher=_no_markets,
        workspace_root=tmp_path,
    )

    assert result.markets_used == 0
    assert result.heartbeat_count >= 3  # ≥1 per tick across 3 ticks
    assert result.broadcast_count == 0
    # Decisions still emitted — NO_BET routed against the fallback id.
    assert result.decisions_count == 3

    rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "sprint7_dryrun.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    heartbeats = [r for r in rows if r.get("row_type") == "heartbeat"]
    assert heartbeats, "expected ≥1 heartbeat row when no markets found"
    assert all(
        "no markets found, agent idling" in r["message"] for r in heartbeats
    )


def test_dry_run_executor_has_no_send_method() -> None:
    """Defence-in-depth — by construction DryRunExecutor cannot
    broadcast (no ``send`` / ``sign`` / ``submit`` method). A reviewer
    grep over the public API enforces this; this test asserts it."""

    executor = DryRunExecutor()
    for forbidden in ("send", "sign", "submit", "broadcast", "post_order"):
        assert not hasattr(executor, forbidden), (
            f"DryRunExecutor must not expose {forbidden!r} — the brief's "
            "'no signed orders broadcast' rule is structural, not just "
            "behavioural"
        )


def test_decision_record_shape_validates_bet_path() -> None:
    """Independent: the canonical decision-record builder produces a
    payload that round-trips through the v0.2.0 schema for the BET branch."""

    schema = json.loads(
        Path(".dev/contracts/decision_record.v0.2.0.json").read_text(
            encoding="utf-8"
        )
    )
    action = Action(
        kind=ActionKind.BET,
        market_id="0xabc",
        side=Side.YES,
        size_usd=12.5,
        edge_pct=0.18,
    )
    record = _build_decision_record(
        tick=7,
        ts=FIXED_TS,
        action=action,
        fused_score=0.18,
        raw_rational=0.20,
        raw_sentient=0.10,
        mean_confidence=0.55,
        rho_effective=0.5,
        market_id_fallback="fallback",
    )
    _validate_decision_record(record, schema)
    assert record["kind"] == "BET"
    assert record["market_id"] == "0xabc"
    assert record["burn_class"] == "decision_tax"
