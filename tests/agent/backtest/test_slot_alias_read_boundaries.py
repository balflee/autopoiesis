"""Read-boundary alias tests: persisted OLD-name slot data upgrades to NEW keys.

Both boundaries are exercised with EXPLICIT old-key literals so they pass
regardless of the constant rename state. End-to-end settlement credit / sweep
equivalence is covered by the full suite after the rename (Task 2 Step 6)."""

from __future__ import annotations

import json
from pathlib import Path

from agent.backtest.cached_sweep import SignalRow, load_rows, save_rows
from agent.backtest.settlement_learner import _unflatten_scores


def test_unflatten_scores_strips_prefix_and_upgrades_legacy_keys() -> None:
    flat = {
        "pnl_usd": 3.0,
        "size_usd": 5.0,
        "bet_direction": 1.0,
        "score_smart_money": 0.7,
        "score_sentiment_llm": 0.4,
        "score_crowd_volume": 0.2,
        "score_tennis_technical": 0.9,
    }
    scores = _unflatten_scores(flat)
    assert scores == {
        "surface_advantage": 0.7,
        "head_to_head": 0.4,
        "rest_recency": 0.2,
        "tennis_technical": 0.9,
    }
    # non-score keys are excluded
    assert "pnl_usd" not in scores and "bet_direction" not in scores


def test_unflatten_scores_is_identity_on_new_keys() -> None:
    flat = {"score_surface_advantage": 0.5, "score_market_momentum": 0.1}
    assert _unflatten_scores(flat) == {
        "surface_advantage": 0.5,
        "market_momentum": 0.1,
    }


def test_load_rows_upgrades_old_key_signal_row(tmp_path: Path) -> None:
    path = tmp_path / "_signal_rows.json"
    payload = [
        {
            "market_id": "m1",
            "slug": "a-vs-b",
            "scores": {
                "tennis_technical": 0.9,
                "market_momentum": 0.1,
                "smart_money": 0.7,
                "sentiment_llm": 0.4,
                "crowd_volume": 0.2,
            },
            "confidences": {
                "tennis_technical": 1.0,
                "market_momentum": 1.0,
                "smart_money": 1.0,
                "sentiment_llm": 1.0,
                "crowd_volume": 1.0,
            },
            "entry_price": 0.5,
            "outcome": "YES",
            "winning_price": 1.0,
            "liquidity_cap_usd": 100.0,
        }
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    rows = load_rows(path)
    assert len(rows) == 1
    assert set(rows[0].scores) == {
        "tennis_technical",
        "market_momentum",
        "surface_advantage",
        "head_to_head",
        "rest_recency",
    }
    assert rows[0].scores["surface_advantage"] == 0.7
    assert set(rows[0].confidences) == set(rows[0].scores)


def test_load_rows_roundtrips_new_keys_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "_signal_rows.json"
    row = SignalRow(
        market_id="m1",
        slug="a-vs-b",
        scores={"surface_advantage": 0.7, "head_to_head": 0.4},
        confidences={"surface_advantage": 1.0, "head_to_head": 1.0},
    )
    save_rows([row], path)
    loaded = load_rows(path)
    assert loaded[0].scores == {"surface_advantage": 0.7, "head_to_head": 0.4}
