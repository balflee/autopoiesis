# Greek letters (α₃, Σ) mirror PRD §4 / §6.6 notation. Disambiguating
# them to Latin fallbacks would silently desync the code from the spec.
"""α₃ — Smart Money engine (PRD §4 third Rational-stream component).

Computes a top-wallet alignment signal: for each whitelisted "smart
money" wallet, sum their net dollar exposure on the YES side of the
target market, and emit ``(Σ_yes − Σ_no) / Σ_total`` clipped to
[-1, 1]. Per TECHNICAL_PLAN §4.8 the whitelist filter is

    ≥30 NBA settled records ∧ win_rate ≥ 60% ∧ net P&L > $5k

The actual filter is run by Track E's wallet-scan job (PRD §7) and
materialised into ``data/fixtures/smart_money_wallets.json``. This
engine reads the JSON, intersects with the chain client's position
iterator for the target market, aggregates, and scores.

**Look-ahead rules**: positions are sourced from chain events whose
``available_at = block_time + confirmation_depth*block_time``. We
pipe through :func:`assert_no_lookahead` so the chokepoint catches
any caller that hands in a forged position list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

from agent.engines.base import Engine, Signal, assert_no_lookahead, require_asof_ts

# Default whitelist path: data/fixtures/smart_money_wallets.json under
# the repo root. Caller can override via the constructor when the
# Track E scan job materialises the production list elsewhere.
DEFAULT_WALLETS_PATH = Path("data/fixtures/smart_money_wallets.json")


@dataclass(frozen=True)
class _WalletPosition:
    """One wallet's net position on one market at one observation time.

    ``side`` ∈ {"YES", "NO"}; ``size_usd`` is dollar exposure.
    ``available_at`` is the chain confirmation time of the position
    (block_time + confirmation_depth*block_time).
    """

    wallet: str
    side: str
    size_usd: float
    available_at: datetime


class _PositionsClient(Protocol):
    """Iterator-like client that emits :class:`_WalletPosition` rows
    for the target market filtered by ``asof_ts``.

    Production wiring: Track E's chain-scan reducer surfaces this
    interface via a thin adapter over
    :class:`data.sources.polygon.PolygonChainClient`. Tests inject a
    fake that returns deterministic rows.
    """

    def fetch_positions(
        self, market_id: str, *, asof_ts: datetime
    ) -> list[_WalletPosition]: ...


class SmartMoneyEngine(Engine):
    """Engine implementing the α₃ Smart Money score."""

    name = "smart_money"

    def __init__(
        self,
        *,
        positions_client: _PositionsClient,
        wallets_path: Path | str = DEFAULT_WALLETS_PATH,
    ) -> None:
        self._positions = positions_client
        self._wallets_path = Path(wallets_path)
        # Whitelist loaded lazily so the constructor stays cheap and
        # tests that inject their own iterator don't need the file on
        # disk at construction time.
        self._whitelist: frozenset[str] | None = None

    def _load_whitelist(self) -> frozenset[str]:
        if self._whitelist is None:
            raw = json.loads(self._wallets_path.read_text(encoding="utf-8"))
            wallets_list: list[dict[str, str]] = raw.get("wallets", [])
            # Case-insensitive — wallets in JSON are mixed-case but
            # on-chain addresses are canonicalised lower-case.
            self._whitelist = frozenset(
                str(w["address"]).lower() for w in wallets_list
            )
        return self._whitelist

    async def evaluate(self, *, target: str, asof_ts: datetime) -> Signal:
        """Score Smart Money alignment for market ``target`` (market_id)."""
        cutoff = require_asof_ts(asof_ts)
        whitelist = self._load_whitelist()
        positions = self._positions.fetch_positions(target, asof_ts=cutoff)

        # Chokepoint defence-in-depth: build a tiny feature frame so
        # the auditor sees the canonical assert_no_lookahead call.
        if positions:
            feat_df = pd.DataFrame(
                [
                    {
                        "wallet": p.wallet,
                        "side": p.side,
                        "size_usd": p.size_usd,
                        "available_at": p.available_at,
                    }
                    for p in positions
                ]
            )
            assert_no_lookahead(feat_df, cutoff)

        # Filter to whitelisted wallets + aggregate per side.
        yes_sum = 0.0
        no_sum = 0.0
        n_whitelisted = 0
        for p in positions:
            if p.wallet.lower() not in whitelist:
                continue
            n_whitelisted += 1
            if p.side.upper() == "YES":
                yes_sum += p.size_usd
            elif p.side.upper() == "NO":
                no_sum += p.size_usd

        total = yes_sum + no_sum
        if total <= 0.0 or n_whitelisted == 0:
            return Signal(
                score=0.0,
                confidence=0.0,
                available_at=cutoff.isoformat(),
                rationale=(
                    f"no smart-money positions on market={target} "
                    f"(whitelist size={len(whitelist)}, hits={n_whitelisted})"
                ),
                raw_features={
                    "yes_sum_usd": 0.0,
                    "no_sum_usd": 0.0,
                    "n_whitelisted_positions": 0.0,
                    "whitelist_size": float(len(whitelist)),
                },
            )

        # TP §4.8 spec: signal = (yes - no) / total ∈ [-1, 1].
        score = (yes_sum - no_sum) / total
        # Confidence rises with dollar volume + number of distinct
        # whitelisted wallets touching the market. Cap at 1.0.
        confidence = min(1.0, n_whitelisted / 5.0)

        raw_features: dict[str, float] = {
            "yes_sum_usd": yes_sum,
            "no_sum_usd": no_sum,
            "total_usd": total,
            "n_whitelisted_positions": float(n_whitelisted),
            "whitelist_size": float(len(whitelist)),
        }

        return Signal(
            score=score,
            confidence=confidence,
            available_at=cutoff.isoformat(),
            rationale=(
                f"yes=${yes_sum:,.0f} no=${no_sum:,.0f} "
                f"wallets={n_whitelisted}/{len(whitelist)}"
            ),
            raw_features=raw_features,
        )


__all__ = ["DEFAULT_WALLETS_PATH", "SmartMoneyEngine"]
