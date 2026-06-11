"""Tests for :mod:`agent.data.polymarket_settlement` — VCR-based replay.

Three cassettes pinned under
``tests/agent/data/vcr/polymarket_settlement/`` cover the three
worked-example markets sampled during the T-B-017 spike
(2026-05-26). Zero live network in this suite — vcrpy's ``none``
record mode forces all I/O through the committed cassettes; any
attempt to hit a non-cassette URL raises immediately.

A separate inline fixture exercises the schema-drift WARNING hook:
:func:`test_unknown_field_tolerated` injects a synthetic gamma-api
response with an extra ``someNewFutureField`` key and asserts both
(a) the parse succeeds and (b) exactly one WARNING log line names
the unknown field.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import vcr
import yaml

from agent.data.polymarket_settlement import (
    SettlementResult,
    _classify_outcome,
    _decode_json_list,
    _HttpClient,
    _parse_polymarket_ts,
    _project,
    resolve_market,
)

# --------------------------------------------------------------------------- #
# Cassette wiring
# --------------------------------------------------------------------------- #

CASSETTE_DIR = Path(__file__).parent / "vcr" / "polymarket_settlement"


# Replay-only VCR: any cassette-miss is a test-suite bug, not a network
# call. The conftest's network safety net also blocks real httpx use.
_replay_vcr = vcr.VCR(
    serializer="yaml",
    record_mode="none",
    cassette_library_dir=str(CASSETTE_DIR),
    decode_compressed_response=True,
    match_on=("method", "scheme", "host", "port", "path", "query"),
)


# Each pin is (cassette_basename, market_id, expected_outcome, expected_winning_price).
# Numbers reproduce the spike report's "lag matrix" — verifying any drift
# in projection logic surfaces here, not at runtime.
_PINNED_MARKETS = [
    (
        "atp-gaston-monfils-2026-05-24",
        "2328096",
        "yes",          # outcomes[0] = "Hugo Gaston", prices = ["1", "0"]
        1.0,
        datetime(2026, 5, 25, 23, 57, 11, tzinfo=UTC),
        datetime(2026, 5, 31, 9, 0, 0, tzinfo=UTC),
    ),
    (
        "atp-ilagan-uchiyam-2026-05-25-first-set-total-8pt5",
        "2348945",
        "yes",          # outcomes[0] = "Over", prices = ["1", "0"]
        1.0,
        datetime(2026, 5, 25, 22, 55, 18, tzinfo=UTC),
        datetime(2026, 6, 1, 15, 0, 0, tzinfo=UTC),
    ),
    (
        "wta-guo-kessler-2026-05-25",
        "2336501",
        "no",           # outcomes = ["Hanyu Guo", "McCartney Kessler"], prices = ["0", "1"]
        1.0,
        datetime(2026, 5, 25, 22, 43, 31, tzinfo=UTC),
        datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC),
    ),
]


# --------------------------------------------------------------------------- #
# Cassette-backed integration tests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "slug,market_id,outcome,winning_price,res_ts,end_date",
    _PINNED_MARKETS,
    ids=[m[0] for m in _PINNED_MARKETS],
)
def test_resolve_market_from_cassette(
    slug: str,
    market_id: str,
    outcome: str,
    winning_price: float,
    res_ts: datetime,
    end_date: datetime,
) -> None:
    """Each pinned cassette → exact :class:`SettlementResult` projection."""

    async def _run() -> SettlementResult | None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ``httpx.AsyncClient.get`` accepts more kwargs than the
            # ``_HttpClient`` Protocol declares (params=, headers=, …)
            # so mypy treats it as not-structurally-compatible. At
            # runtime the structural subtype IS satisfied — the
            # production wiring also passes an httpx.AsyncClient.
            return await resolve_market(market_id, client=cast(_HttpClient, client))

    with _replay_vcr.use_cassette(f"{slug}.yaml"):
        result = asyncio.run(_run())

    assert result is not None, f"market {market_id} should be resolved"
    assert result.resolved is True
    assert result.market_id == market_id
    assert result.outcome == outcome
    assert result.winning_price == pytest.approx(winning_price)
    assert result.resolution_ts == res_ts
    assert result.end_date == end_date


def test_all_cassettes_show_resolved_status() -> None:
    """Acceptance criterion: all 3 cassettes show umaResolutionStatus='resolved',
    non-null endDate, winning outcomePrice >= 0.99."""
    for slug, _market_id, _outcome, winning_price, _res_ts, end_date in _PINNED_MARKETS:
        cassette = CASSETTE_DIR / f"{slug}.yaml"
        assert cassette.exists(), f"missing cassette: {cassette}"
        raw = cassette.read_text(encoding="utf-8")
        # Spot-check by string match — cheap and avoids re-parsing the cassette YAML.
        assert '"umaResolutionStatus":"resolved"' in raw
        assert end_date is not None
        assert winning_price >= 0.99


# --------------------------------------------------------------------------- #
# Schema-drift tolerance — required by the locked CEO 2026-05-26 decision
# --------------------------------------------------------------------------- #


def test_unknown_field_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    """An unknown gamma-api field MUST (a) not raise, (b) emit one WARNING.

    Construct a SettlementResult directly from a dict that includes a
    synthetic ``someNewFutureField`` key. ``extra='ignore'`` drops it;
    the ``mode='before'`` validator logs it. Both conditions are
    asserted.
    """
    payload: dict[str, Any] = {
        "market_id": "9999999",
        "resolved": True,
        "outcome": "yes",
        "winning_price": 1.0,
        "resolution_ts": "2026-05-25T23:57:11+00:00",
        "end_date": "2026-05-31T09:00:00+00:00",
        # The schema-drift probe — gamma-api could add anything here at 3am.
        "someNewFutureField": {"nested": "value", "with": ["multiple", "shapes"]},
        "anotherUnknown": 42,
    }

    with caplog.at_level(logging.WARNING, logger="agent.data.polymarket_settlement"):
        # model_validate accepts an untyped dict, unlike the splat-init,
        # which mypy can't keyword-match against a Literal[...] field.
        result = SettlementResult.model_validate(payload)

    # (a) Parse succeeded — model is constructed.
    assert result.market_id == "9999999"
    assert result.outcome == "yes"
    # extra='ignore' means the unknown field is NOT present on the model.
    assert not hasattr(result, "someNewFutureField")
    assert "someNewFutureField" not in result.model_dump()

    # (b) Exactly one WARNING line names the unknown field set.
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "agent.data.polymarket_settlement"
    ]
    assert len(warnings) == 1, f"expected 1 WARNING, got {len(warnings)}: {warnings!r}"
    msg = warnings[0].getMessage()
    assert "someNewFutureField" in msg
    assert "anotherUnknown" in msg


# --------------------------------------------------------------------------- #
# Edge cases via the pure projection helper
# --------------------------------------------------------------------------- #


def test_project_returns_none_when_uma_not_resolved() -> None:
    """umaResolutionStatus != 'resolved' → caller polls again."""
    raw: dict[str, Any] = {
        "id": "1234567",
        "umaResolutionStatus": "proposed",
        "outcomePrices": '["0.5", "0.5"]',
        "closedTime": "2026-05-25 23:00:00+00",
        "endDate": "2026-05-31T09:00:00Z",
    }
    assert _project(raw) is None


def test_project_returns_none_when_uma_status_absent() -> None:
    """Markets that have never settled have no umaResolutionStatus field."""
    raw: dict[str, Any] = {
        "id": "1234567",
        "outcomePrices": '["0.5", "0.5"]',
    }
    assert _project(raw) is None


def test_project_void_outcome_for_walkover_split() -> None:
    """A 50-50 walkover (max price < 0.99) projects to 'void'."""
    raw: dict[str, Any] = {
        "id": "55555",
        "umaResolutionStatus": "resolved",
        "outcomePrices": '["0.5", "0.5"]',
        "closedTime": "2026-05-25 23:00:00+00",
        "endDate": "2026-05-31T09:00:00Z",
    }
    result = _project(raw)
    assert result is not None
    assert result.outcome == "void"
    assert result.winning_price == pytest.approx(0.5)


def test_project_raises_on_missing_closed_time() -> None:
    """resolved=True with no closedTime is a transport-level invariant break."""
    raw: dict[str, Any] = {
        "id": "55555",
        "umaResolutionStatus": "resolved",
        "outcomePrices": '["1", "0"]',
        "endDate": "2026-05-31T09:00:00Z",
    }
    with pytest.raises(ValueError, match="closedTime"):
        _project(raw)


def test_project_raises_on_missing_id() -> None:
    raw: dict[str, Any] = {
        "umaResolutionStatus": "resolved",
        "outcomePrices": '["1", "0"]',
        "closedTime": "2026-05-25 23:00:00+00",
        "endDate": "2026-05-31T09:00:00Z",
    }
    with pytest.raises(ValueError, match="'id'"):
        _project(raw)


def test_decode_json_list_handles_list_and_str() -> None:
    assert _decode_json_list('["1", "0"]') == [1.0, 0.0]
    assert _decode_json_list([1, "0.5"]) == [1.0, 0.5]
    assert _decode_json_list("not json") == []
    assert _decode_json_list(None) == []
    assert _decode_json_list(42) == []
    assert _decode_json_list('"a single string"') == []
    assert _decode_json_list('["nan", "x"]') == []


def test_classify_outcome_dispatch() -> None:
    assert _classify_outcome([1.0, 0.0]) == ("yes", 1.0)
    assert _classify_outcome([0.0, 1.0]) == ("no", 1.0)
    assert _classify_outcome([0.5, 0.5]) == ("void", 0.5)
    assert _classify_outcome([]) == ("void", 0.0)
    assert _classify_outcome([1.0]) == ("void", 1.0)
    assert _classify_outcome([0.4, 0.3, 0.3]) == ("void", 0.4)


def test_parse_polymarket_ts_handles_both_shapes() -> None:
    # Z-suffix ISO
    iso = _parse_polymarket_ts("2026-05-31T09:00:00Z")
    assert iso == datetime(2026, 5, 31, 9, 0, 0, tzinfo=UTC)
    # Space-separated +00
    space = _parse_polymarket_ts("2026-05-25 23:57:11+00")
    assert space == datetime(2026, 5, 25, 23, 57, 11, tzinfo=UTC)
    # Naive → coerced to UTC
    naive = _parse_polymarket_ts("2026-05-25T23:57:11")
    assert naive == datetime(2026, 5, 25, 23, 57, 11, tzinfo=UTC)
    # Junk
    assert _parse_polymarket_ts("") is None
    assert _parse_polymarket_ts("notadate") is None
    assert _parse_polymarket_ts(None) is None


# --------------------------------------------------------------------------- #
# Lag matrix — proves the spike report's numbers match the committed cassettes
# --------------------------------------------------------------------------- #


def _load_cassette_body(slug: str) -> dict[str, Any]:
    """Parse a vcrpy cassette + return the JSON body of the first interaction."""
    cassette = CASSETTE_DIR / f"{slug}.yaml"
    doc = yaml.safe_load(cassette.read_text(encoding="utf-8"))
    body_str = doc["interactions"][0]["response"]["body"]["string"]
    parsed = json.loads(body_str)
    assert isinstance(parsed, dict)
    return parsed


def test_lag_matrix_matches_spike_report() -> None:
    """Re-derive the lag numbers from the cassettes so the spike report
    can't silently drift from the data it claims to summarise.

    Pinned to ±3 min (0.05 h) so a rounding fix in the report doesn't
    break this test — but any substantive cassette swap does.
    """
    lag_pins = {
        "atp-gaston-monfils-2026-05-24": 5.62,
        "atp-ilagan-uchiyam-2026-05-25-first-set-total-8pt5": 3.25,
        "wta-guo-kessler-2026-05-25": 4.89,
    }
    for slug, expected_hours in lag_pins.items():
        m = _load_cassette_body(slug)
        gst = _parse_polymarket_ts(m["gameStartTime"])
        closed = _parse_polymarket_ts(m["closedTime"])
        assert gst is not None and closed is not None
        delta_hours = (closed - gst).total_seconds() / 3600.0
        assert abs(delta_hours - expected_hours) < 0.05, (
            f"{slug}: gameStart→closed {delta_hours:.2f}h drifted from "
            f"reported {expected_hours:.2f}h — update the spike report"
        )
        # Sanity: also matches the resolved-status acceptance.
        assert m["umaResolutionStatus"] == "resolved"
        assert m["endDate"]
        # Acceptance criterion: winning outcomePrice >= 0.99
        prices = json.loads(m["outcomePrices"])
        assert max(float(p) for p in prices) >= 0.99
