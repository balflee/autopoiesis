"""A17 — sharp-line edge historical probe (pure helpers + injectable fetch).

Read-only probe that asks the cheapest go/no-go question behind the
"real edge" thesis: does a public sharp line (Pinnacle closing implied
probability) systematically beat the consensus (2a) / Polymarket (2b) at
predicting tennis MATCH-WINNER outcomes, measured by a per-match PAIRED
Brier difference?

See docs/superpowers/specs/2026-06-13-sharp-line-edge-probe-design.md and the
plan-loop record. This module holds ONLY pure functions + injectable fetch
protocols (no real network in unit tests). The CLI driver that performs the
actual fetches and writes the report lives in scripts/probe_sharp_line.py.

Sign convention (locked): ``edge_i = (p_soft - y)**2 - (p_pin - y)**2`` — a
POSITIVE edge means the sharp (Pinnacle) line was more accurate than the soft
comparator. The verdict uses a cluster bootstrap CI + a pre-declared SESOI
three-state rule: CI lower > 0 => EDGE; CI upper < SESOI => REFUTED; else
INCONCLUSIVE (fail-to-reject != no-edge).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from agent.backtest.cached_sweep import compute_bet_pnl, effective_entry_price

# --------------------------------------------------------------------------- #
# de-vig
# --------------------------------------------------------------------------- #


def implied_prob_two_way(odds_ref: object, odds_other: object) -> float | None:
    """Two-way proportional de-vig -> implied prob of the ``odds_ref`` side.

    Returns ``None`` when either price is missing / non-numeric / NaN / <= 1.0
    (a decimal odd must exceed 1.0). Proportional normalisation removes the
    overround; it carries a small favourite-longshot bias (no shin
    correction), acceptable for an upper-bound go/no-go — callers disclose it.
    """
    parsed: list[float] = []
    for raw in (odds_ref, odds_other):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        try:
            val = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if math.isnan(val) or val <= 1.0:
            return None
        parsed.append(val)
    inv_ref = 1.0 / parsed[0]
    inv_other = 1.0 / parsed[1]
    return inv_ref / (inv_ref + inv_other)


# --------------------------------------------------------------------------- #
# name normalisation / matching
# --------------------------------------------------------------------------- #

_INITIALS_RE = re.compile(r"^(?:[A-Za-z]\.)+$")


def _strip_accents_lower(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, keep only ``[a-z]`` (drops spaces/hyphens/dots)."""
    return re.sub(r"[^a-z]", "", _strip_accents_lower(name))


def tennis_data_surname(name: str) -> str | None:
    """Surname key from tennis-data ``"Surname I."`` / ``"De Minaur A."`` form.

    Strips the trailing initials token (``^([A-Za-z]\\.)+$`` — matches ``J.``
    and multi-initial ``J.M.``/``D.R.``), keeps the remaining (possibly
    compound) surname tokens, then normalises. ``None`` if nothing remains.
    """
    if not name:
        return None
    tokens = str(name).strip().split()
    while tokens and _INITIALS_RE.match(tokens[-1]):
        tokens.pop()
    if not tokens:
        return None
    return normalize_name("".join(tokens)) or None


def surname_matches(td_surname: str, display_name: str) -> bool:
    """True iff a tennis-data surname key is a suffix of the normalised display name.

    Robust to a variable number of given-name tokens (``"Juan Martin Del
    Potro"`` -> ``"juanmartindelpotro"`` endswith ``"delpotro"``).
    """
    if not td_surname or not display_name:
        return False
    return normalize_name(display_name).endswith(td_surname)


# --------------------------------------------------------------------------- #
# Brier / edge / cluster bootstrap (verdict statistics)
# --------------------------------------------------------------------------- #


def brier_edge(p_pin: float, p_soft: float, y: int) -> float:
    """Per-match paired Brier edge. POSITIVE => sharp (Pinnacle) more accurate."""
    return (p_soft - y) ** 2 - (p_pin - y) ** 2


class _RNG(Protocol):
    """Minimal structural type for ``numpy.random.Generator`` (deterministic)."""

    def integers(self, low: int, high: int, size: int) -> Sequence[int]: ...


@dataclass(frozen=True)
class BootstrapCI:
    n: int
    n_clusters: int
    point: float
    lo: float
    hi: float
    iid_lo: float
    iid_hi: float


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo_idx = math.floor(pos)
    hi_idx = math.ceil(pos)
    frac = pos - lo_idx
    return sorted_vals[lo_idx] * (1 - frac) + sorted_vals[hi_idx] * frac


def cluster_bootstrap_ci(
    values: list[float],
    cluster_keys: list[str],
    *,
    rng: _RNG,
    n_boot: int = 1000,
    ci: float = 0.95,
) -> BootstrapCI:
    """Paired bootstrap CI of ``mean(values)``.

    The verdict CI resamples CLUSTERS (e.g. ``tournament+week``) with
    replacement — same tournament/day/player conditions make per-match values
    correlated, so an iid bootstrap is too narrow and can flip the load-bearing
    verdict. The iid CI is also computed and returned for sensitivity only.

    ``rng`` is any object exposing ``integers(low, high, size)`` (e.g.
    ``numpy.random.default_rng(seed)``) so callers stay deterministic.
    """
    n = len(values)
    if n != len(cluster_keys):
        raise ValueError("values and cluster_keys must align")
    lo_q = (1.0 - ci) / 2.0
    hi_q = 1.0 - lo_q
    if n == 0:
        nan = float("nan")
        return BootstrapCI(0, 0, nan, nan, nan, nan, nan)

    point = sum(values) / n

    # group values by cluster
    clusters: dict[str, list[float]] = {}
    for v, k in zip(values, cluster_keys, strict=True):
        clusters.setdefault(k, []).append(v)
    cluster_list = list(clusters.values())
    n_clusters = len(cluster_list)

    def _draws(num_items: int, num_boot: int) -> list[list[int]]:
        flat = rng.integers(0, num_items, size=num_boot * num_items)
        flat_list = [int(x) for x in flat]
        return [
            flat_list[i * num_items : (i + 1) * num_items] for i in range(num_boot)
        ]

    # cluster bootstrap
    cluster_means: list[float] = []
    for draw in _draws(n_clusters, n_boot):
        total = 0.0
        count = 0
        for ci_idx in draw:
            grp = cluster_list[ci_idx]
            total += sum(grp)
            count += len(grp)
        cluster_means.append(total / count if count else float("nan"))
    cluster_means.sort()

    # iid bootstrap (sensitivity)
    iid_means: list[float] = []
    for draw in _draws(n, n_boot):
        iid_means.append(sum(values[i] for i in draw) / n)
    iid_means.sort()

    return BootstrapCI(
        n=n,
        n_clusters=n_clusters,
        point=point,
        lo=_percentile(cluster_means, lo_q),
        hi=_percentile(cluster_means, hi_q),
        iid_lo=_percentile(iid_means, lo_q),
        iid_hi=_percentile(iid_means, hi_q),
    )


def three_state_verdict(
    ci: BootstrapCI, *, sesoi: float, min_n: int = 200, min_clusters: int = 10
) -> str:
    """Map a (cluster) CI to EDGE / REFUTED / INCONCLUSIVE via SESOI.

    - n too small (or too few clusters) -> INCONCLUSIVE (never no-go).
    - CI lower bound > 0 -> EDGE (sharp significantly more accurate).
    - CI upper bound < SESOI -> REFUTED (a meaningful edge is ruled out).
    - otherwise -> INCONCLUSIVE (CI still compatible with a meaningful edge;
      fail-to-reject != no-edge).
    """
    if ci.n < min_n or ci.n_clusters < min_clusters:
        return "INCONCLUSIVE"
    if ci.lo > 0.0:
        return "EDGE"
    if ci.hi < sesoi:
        return "REFUTED"
    return "INCONCLUSIVE"


# --------------------------------------------------------------------------- #
# simulated betting (ex-ante side, YES-mid contract, local fee, spread grid)
# --------------------------------------------------------------------------- #

_FEE_RATE = 0.03  # Polymarket Sports taker fee coefficient (≈0.75% at p=0.5)


def taker_fee_usd(size_usd: float, price: float, *, rate: float = _FEE_RATE) -> float:
    """Polymarket Sports taker fee: ``size * p * rate * p*(1-p)`` (peaks at 0.5)."""
    return size_usd * price * rate * (price * (1.0 - price))


@dataclass(frozen=True)
class BetResult:
    placed: bool
    side: str | None = None
    entry_yes: float | None = None
    net_pnl: float | None = None
    reason: str | None = None  # drop bucket when not placed


def simulate_bet(
    *,
    p_pin_ref: float,
    p_soft_ref: float,
    p_yes_close: float,
    y_ref: int,
    threshold: float,
    half_spread: float,
    fee_rate: float = _FEE_RATE,
    size_usd: float = 1.0,
) -> BetResult:
    """One simulated 2b bet, settled by the real outcome.

    The reference player is the YES token (``outcomes[0]``), so ``p_yes_close``
    is P(reference wins) and the market ``outcome`` is ``"yes"`` iff the
    reference won (``y_ref == 1``).

    Side is chosen ONLY from the ex-ante edge ``d_edge = p_pin_ref -
    p_soft_ref`` (never from ``y`` — that would leak the result): bet the
    reference (YES) iff ``d_edge > 0``, else against it (NO). The price passed
    to ``compute_bet_pnl`` is always the YES-token mid adjusted for the spread
    we cross (YES pays ``p_yes + hs``; the NO side's YES-equivalent is
    ``p_yes - hs``); ``compute_bet_pnl`` complements internally for NO via
    ``effective_entry_price`` — so we must NOT pre-complement. Spread-adjusted
    prices outside ``(0,1)`` (or a degenerate effective price) are rejected as
    ``invalid_spread_price``.
    """
    d_edge = p_pin_ref - p_soft_ref
    if abs(d_edge) < threshold:
        return BetResult(placed=False, reason="below_threshold")

    side = "YES" if d_edge > 0 else "NO"
    entry_yes = p_yes_close + half_spread if side == "YES" else p_yes_close - half_spread
    if not (0.0 < entry_yes < 1.0):
        return BetResult(placed=False, reason="invalid_spread_price")
    eff = effective_entry_price(side=side, yes_price=entry_yes)
    if not (0.0 < eff <= 1.0):
        return BetResult(placed=False, reason="invalid_spread_price")

    outcome = "yes" if y_ref == 1 else "no"
    # winning_price ~ 1.0 for a clean resolution (settle at the true outcome).
    gross = compute_bet_pnl(
        side=side,
        entry_price=entry_yes,
        size_usd=size_usd,
        outcome=outcome,
        winning_price=1.0,
        side_correct_pricing=True,
    )
    fee = taker_fee_usd(size_usd, eff, rate=fee_rate)
    return BetResult(placed=True, side=side, entry_yes=entry_yes, net_pnl=gross - fee)


# --------------------------------------------------------------------------- #
# tennis-data row model + 2a comparator (sharp vs non-Pinnacle consensus)
# --------------------------------------------------------------------------- #

# Individual non-Pinnacle two-way bookmaker odds columns (winner, loser).
# Avg*/Max* are aggregates (Avg may CONTAIN Pinnacle), excluded from the clean
# 2a comparator; used only as a sensitivity fallback.
_NON_PINNACLE_BOOKS: tuple[tuple[str, str], ...] = (
    ("B365W", "B365L"),
    ("EXW", "EXL"),
    ("LBW", "LBL"),
    ("SJW", "SJL"),
    ("CBW", "CBL"),
    ("GBW", "GBL"),
    ("IWW", "IWL"),
    ("UBW", "UBL"),
)


def soft_consensus_prob(
    row: dict[str, object], *, ref_is_winner: bool
) -> tuple[float | None, bool]:
    """Soft-line implied prob of the reference player from non-Pinnacle books.

    De-vigs EACH available non-Pinnacle two-way book and averages the
    PROBABILITIES (never averages odds; never uses Avg*, which may contain
    Pinnacle). Returns ``(prob, used_avg_fallback)``: when no individual book
    is available it falls back to ``Avg*`` and flags it (2a sensitivity-only).
    """
    probs: list[float] = []
    for win_col, lose_col in _NON_PINNACLE_BOOKS:
        w = row.get(win_col)
        loss = row.get(lose_col)
        if ref_is_winner:
            p = implied_prob_two_way(w, loss)
        else:
            p = implied_prob_two_way(loss, w)
        if p is not None:
            probs.append(p)
    if probs:
        return sum(probs) / len(probs), False
    # fallback: Avg* aggregate (sensitivity-only; may contain Pinnacle)
    aw, al = row.get("AvgW"), row.get("AvgL")
    p = (
        implied_prob_two_way(aw, al)
        if ref_is_winner
        else implied_prob_two_way(al, aw)
    )
    return p, True


# --------------------------------------------------------------------------- #
# 2b — raw Gamma index + orientation gates + CLOB close extraction
# --------------------------------------------------------------------------- #


class GammaIndex(Protocol):
    def get(self, market_id: str) -> list[dict[str, object]] | None:
        """All raw gamma market dicts for ``market_id`` (>1 => duplicate)."""


class ClobHistory(Protocol):
    def prices_history(
        self, token_id: str, *, start_ts: int | None, end_ts: int | None
    ) -> list[dict[str, object]]:
        """Raw CLOB tick list, each ``{"t": <unix_s>, "p": <mid>}`` (any order)."""


@dataclass(frozen=True)
class TwoBClose:
    ok: bool
    reason: str | None = None
    reference_display: str | None = None  # outcomes[0]
    other_display: str | None = None  # outcomes[1]
    p_polymarket: float | None = None  # YES-token closing mid (ref player)
    tick_age_h: float | None = None
    game_start: datetime | None = None


def _parse_iso(ts: object) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # Coerce naive timestamps to UTC so all comparisons are tz-aware.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def resolve_2b_close(
    *,
    market_id: str,
    cassette_outcome: str | None,
    gamma_index: GammaIndex,
    clob: ClobHistory,
    max_tick_age_h: float = 24.0,
) -> TwoBClose:
    """Resolve a verified pre-match Polymarket close for one 2b market.

    Fail-closed: returns ``ok=False`` with a drop-bucket ``reason`` whenever any
    correctness pre-condition cannot be established result-independently:

    - ``gamma_missing`` / ``gamma_duplicate`` / ``slug_missing``
    - ``outcome_drift``: refetched outcome (from ``outcomePrices``) != cassette
    - ``token_orientation_unverified``: ``clobTokenIds``<->``outcomes`` parallel
      ordering not verifiable from primary metadata
    - ``missing_gameStartTime`` / ``no_clob_history`` / ``no_prematch_tick`` /
      ``stale_premarket_tick``
    """
    rows = gamma_index.get(market_id)
    if not rows:
        return TwoBClose(ok=False, reason="gamma_missing")
    if len(rows) > 1:
        return TwoBClose(ok=False, reason="gamma_duplicate")
    raw = rows[0]

    outcomes = raw.get("outcomes")
    prices = raw.get("outcomePrices")
    token_ids = raw.get("clobTokenIds")
    if not isinstance(outcomes, list) or len(outcomes) != 2:
        return TwoBClose(ok=False, reason="orientation_unverified")
    if not isinstance(prices, list) or len(prices) != 2:
        return TwoBClose(ok=False, reason="outcome_drift")
    if not isinstance(token_ids, list) or len(token_ids) != 2:
        return TwoBClose(ok=False, reason="token_orientation_unverified")

    # (a) refetched outcome must agree with the cassette's recorded outcome.
    try:
        p0 = float(prices[0])
        p1 = float(prices[1])
    except (TypeError, ValueError):
        return TwoBClose(ok=False, reason="outcome_drift")
    refetched = "yes" if p0 > p1 else "no" if p1 > p0 else "void"
    if cassette_outcome is None or refetched != str(cassette_outcome).lower():
        return TwoBClose(ok=False, reason="outcome_drift")

    # (b) token<->outcome parallel ordering must be verifiable result-independently.
    # Fail-closed: only proceed when the raw market exposes an explicit
    # per-token outcome label that confirms clobTokenIds[0] is the outcomes[0]
    # (YES) token. Absent that primary metadata, exclude (do NOT guess, and
    # never pick the token by which one makes the result look right).
    token0 = _verified_yes_token(raw)
    if token0 is None:
        return TwoBClose(ok=False, reason="token_orientation_unverified")

    # (cutoff) match-start time, not settlement time.
    game_start = _parse_iso(raw.get("gameStartTime"))
    if game_start is None:
        return TwoBClose(ok=False, reason="missing_gameStartTime")

    ticks = clob.prices_history(
        token0, start_ts=None, end_ts=int(game_start.timestamp())
    )
    pre: list[tuple[datetime, float]] = []
    for t in ticks:
        ts = _parse_unix(t.get("t"))
        mid = _parse_float(t.get("p"))
        if ts is None or mid is None:
            continue
        if ts < game_start:
            pre.append((ts, mid))
    if not ticks:
        return TwoBClose(ok=False, reason="no_clob_history")
    if not pre:
        return TwoBClose(ok=False, reason="no_prematch_tick")
    pre.sort(key=lambda x: x[0])
    last_ts, last_mid = pre[-1]
    age_h = (game_start - last_ts).total_seconds() / 3600.0
    if age_h > max_tick_age_h:
        return TwoBClose(
            ok=False, reason="stale_premarket_tick", tick_age_h=age_h
        )

    return TwoBClose(
        ok=True,
        reference_display=str(outcomes[0]),
        other_display=str(outcomes[1]),
        p_polymarket=last_mid,
        tick_age_h=age_h,
        game_start=game_start,
    )


def _verified_yes_token(raw: dict[str, object]) -> str | None:
    """Return the clobTokenId proven to be the outcomes[0] (YES) token, else None.

    Result-INDEPENDENT verification only. Accepts an explicit per-token label
    map if the raw market exposes one (``tokens: [{token_id, outcome}, ...]``);
    otherwise fail-closed (None) — the bare ``clobTokenIds`` array's ordering
    relative to ``outcomes`` is not guaranteed and is not assumed.
    """
    tokens = raw.get("tokens")
    outcomes = raw.get("outcomes")
    if isinstance(tokens, list) and isinstance(outcomes, list) and outcomes:
        yes_label = str(outcomes[0])
        for tok in tokens:
            if not isinstance(tok, dict):
                return None
            label = tok.get("outcome")
            tid = tok.get("token_id")
            if label is not None and tid is not None and str(label) == yes_label:
                return str(tid)
        return None
    return None


def _parse_unix(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


# --------------------------------------------------------------------------- #
# 2b join (one-to-one, gameStartTime date) + drop-bucket accounting
# --------------------------------------------------------------------------- #


@dataclass
class DropBuckets:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, reason: str) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def total(self) -> int:
        return sum(self.counts.values())


def join_one_to_one(
    *,
    ref_surname: str,
    other_surname: str,
    game_start: datetime,
    td_by_date: dict[str, list[dict[str, object]]],
    date_tol_days: int = 1,
) -> tuple[dict[str, object] | None, bool, str | None]:
    """Match a Polymarket market to a unique tennis-data row.

    ``td_by_date`` is tennis-data rows keyed by ISO date (``YYYY-MM-DD``).
    Matches by the normalised surname PAIR within ``±date_tol_days`` of the
    match-start date. Returns ``(row, ref_is_winner, reason)``: a non-None
    ``reason`` is a drop bucket (``date_miss`` / ``ambiguous_join``); on success
    ``reason is None`` and ``ref_is_winner`` says which tennis-data leg the
    reference (outcomes[0]) player is.
    """
    candidates: list[tuple[dict[str, object], bool]] = []
    base = game_start.date()
    for delta in range(-date_tol_days, date_tol_days + 1):
        key = (base + timedelta(days=delta)).isoformat()
        for row in td_by_date.get(key, []):
            w = tennis_data_surname(str(row.get("Winner", "")))
            loser = tennis_data_surname(str(row.get("Loser", "")))
            if w is None or loser is None:
                continue
            pair = {w, loser}
            if pair != {ref_surname, other_surname}:
                continue
            ref_is_winner = ref_surname == w
            candidates.append((row, ref_is_winner))
    if not candidates:
        return None, False, "date_miss"
    if len(candidates) > 1:
        return None, False, "ambiguous_join"
    row, ref_is_winner = candidates[0]
    return row, ref_is_winner, None
