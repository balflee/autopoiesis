"""Backward-compat slot-key aliases for persisted old-name data.

The 2026-06-16 rename changed three fusion-slot keys to match what they
actually carry (RealSignalSource Sackmann/CLOB payloads, not the dead engine
modules they were misnamed after):

    smart_money   -> surface_advantage   (Sackmann surface edge)
    sentiment_llm -> head_to_head        (Sackmann H2H record)
    crowd_volume  -> rest_recency        (Sackmann rest / recent form)

Old-name keys persist in already-written data that is read back AFTER the
rename — in-flight bets (``open_bets.jsonl`` via ``score_<engine>``) and the
replay input ``reports/backtest/_signal_rows.json``. :func:`alias_slot` upgrades
an old key to its new name and is the IDENTITY for every already-new key, so
applying it at a read boundary is a zero-behavior-change normalization.
"""

from __future__ import annotations

from typing import Final

SLOT_KEY_ALIASES: Final[dict[str, str]] = {
    "smart_money": "surface_advantage",
    "sentiment_llm": "head_to_head",
    "crowd_volume": "rest_recency",
}


def alias_slot(k: str) -> str:
    """Return the post-rename slot key for ``k`` (identity if already new)."""
    return SLOT_KEY_ALIASES.get(k, k)


__all__ = ["SLOT_KEY_ALIASES", "alias_slot"]
