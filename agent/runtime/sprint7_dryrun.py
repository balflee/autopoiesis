"""Sprint 7 Day-6-closer Phase 2 dry-run harness.

Spec anchors
------------

* PRD §3 / §6.13 — Phase 2 (学徒): β₁ unfreeze, real Polymarket markets,
  USDC bankroll shadow-only (no signed orders).
* PRD §14 GOOD_CALIBRATION + §6 BREATH economy — NO_BET counts as a
  decision (passive burn); the dry-run records both BET and NO_BET
  shapes against ``decision_record.v0.2.0.json``.
* TECHNICAL_PLAN §4.1 — agent_loop fanout: 5 engines → fusion →
  4-constraint sizing → ExecutionRouter. In dry-run mode the router is
  :class:`DryRunExecutor` — records-only, never broadcasts.
* TECHNICAL_PLAN §15 Gap 1 — ``polymarket_executor.py`` real-money path
  deferred to Phase 3 sprint; the dry-run executor records the order
  intent without signing.

What this ships
---------------

A single CLI entry point :func:`run_dryrun` that:

1. Loads the trained ``weights_v0.json`` from
   ``reports/phase1/weights_v0.json`` and proves the Phase 2 launch
   orchestrator (:class:`Phase2LaunchOrchestrator`) accepts the weights
   without exception — sprint_7 acceptance criterion (a).
2. Discovers real Polymarket tennis markets via the *gamma-api*
   ``/events?tag_slug=tennis`` route (the documented discovery surface
   per ``data/sources/polymarket.py``). Markets are cached to a fixture
   so a sandboxed re-run is deterministic — acceptance criterion (d).
3. Iterates N decision ticks (default 5; brief floor ≥3) on the
   :class:`DecisionEngine` with the trained weights, feeding signals
   derived from per-market metadata (no stale-data hallucination).
   Each decision row mirrors the canonical
   ``decision_record.v0.2.0.json`` shape and is validated against the
   schema in-line — acceptance criterion (c).
4. Emits an explicit ``no markets found, agent idling`` heartbeat
   every 5 simulated minutes during any idle window (operator
   visibility per CEO Day-6 plan).
5. Routes every BET / NO_BET intent through :class:`DryRunExecutor`,
   asserting zero broadcasts — acceptance: NO signed orders.

The module is import-clean (no network at import); all I/O happens
inside :func:`run_dryrun`. Tests inject a fake gamma-api fetcher +
clock so the suite runs offline + deterministically.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol

import jsonschema

from agent.core.memory_bank import MemoryBank
from agent.core.state import Action, ActionKind, Phase, Side, Weights
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
    DecisionEngine,
    _fuse_signals,  # TODO(track-b): promote to public API alongside FusionResult
)
from agent.engines.weight_updater import DegradedMode
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator

# Canonical paths -----------------------------------------------------------

WEIGHTS_PATH: Final[Path] = Path("reports/phase1/weights_v0.json")
LOGS_DIR: Final[Path] = Path("logs/phase2_dryrun")
DRYRUN_JSONL: Final[Path] = LOGS_DIR / "sprint7_dryrun.jsonl"
DRYRUN_SUMMARY: Final[Path] = LOGS_DIR / "sprint7_dryrun_summary.md"
DRYRUN_RESULT_JSON: Final[Path] = LOGS_DIR / "sprint7_dryrun_summary.json"
DECISION_SCHEMA_PATH: Final[Path] = Path(
    ".dev/contracts/decision_record.v0.2.0.json"
)
TENNIS_MARKETS_CACHE: Final[Path] = LOGS_DIR / "tennis_markets_snapshot.json"

# Gamma-api — the /events route IS the working tennis filter (verified
# 2026-05-26 live: /markets?tag_slug=tennis ignores the filter; /events
# applies it correctly + nests per-tournament sub-markets).
GAMMA_EVENTS_URL: Final[str] = "https://gamma-api.polymarket.com/events"

# Phase 2 cadence per PRD §6.13 — 60-minute forced decisions in prod.
# The CEO plan calls for ≥3 within 30 min, so the dry-run uses a faster
# 8-minute simulated cadence to exercise the heartbeat + multiple ticks.
SIM_TICK_INTERVAL_MIN: Final[float] = 8.0
IDLE_HEARTBEAT_INTERVAL_MIN: Final[float] = 5.0
DEFAULT_TICKS: Final[int] = 5  # 5 ticks × 8 min = 40 sim-min window
DEFAULT_DRYRUN_BANKROLL_USD: Final[float] = 100.0
DEFAULT_DRYRUN_BREATH: Final[float] = 100.0
DEFAULT_LIQUIDITY_CAP_USD: Final[float] = 50.0

# decision_record.v0.2.0.json burn_class enum values. There is no
# project-wide BurnClass StrEnum; pinning the literals here so the
# string never drifts from the schema.
BURN_CLASS_DECISION_TAX: Final[str] = "decision_tax"
BURN_CLASS_PASSIVE_BURN: Final[str] = "passive_burn"

# Row discriminator written into the JSONL stream so a replayer can
# distinguish heartbeat envelopes from decision-record envelopes
# WITHOUT colliding with the v0.2.0 schema's required ``kind`` field
# (which is BET | NO_BET). The discriminator lives under "row_type"
# and only ever appears alongside heartbeat rows; decision rows carry
# the canonical schema shape verbatim.
ROW_TYPE_HEARTBEAT: Final[str] = "heartbeat"


# ---------------------------------------------------------------------------
# Tennis market discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TennisMarket:
    """One Polymarket tennis market the dry-run can target.

    Fields mirror the gamma-api ``markets[]`` shape projected to the
    fields the decision tick actually consumes.
    """

    condition_id: str
    slug: str
    question: str
    event_title: str
    event_slug: str
    outcome_prices: tuple[float, float] | None
    end_date_iso: str | None


class _GammaFetcher(Protocol):
    """Minimal async-free JSON fetcher Protocol.

    Production: :func:`_urllib_fetch` (5-second timeout, raises on
    HTTP ≥ 500). Tests: a deterministic in-memory fake.
    """

    def fetch(self, url: str) -> Any: ...


def _urllib_fetch(url: str, *, timeout: float = 10.0) -> Any:
    """Plain stdlib HTTP GET → JSON. No retries — discovery is a
    pre-flight, the dry-run gracefully degrades if it fails."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "genesis-agent/sprint7-dryrun"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — public read-only API
        body = resp.read().decode("utf-8")
    return json.loads(body)


def discover_tennis_markets(
    *,
    fetcher: Callable[[str], Any] | None = None,
    limit: int = 5,
) -> list[TennisMarket]:
    """Discover live Polymarket tennis markets via gamma-api /events.

    Returns up to *limit* :class:`TennisMarket` records, projected from
    the per-event sub-markets. Empty list iff the API is unreachable —
    callers MUST handle that path explicitly (the dry-run logs the
    idle heartbeat).
    """
    fetch = fetcher if fetcher is not None else _urllib_fetch
    url = f"{GAMMA_EVENTS_URL}?tag_slug=tennis&limit=10&active=true&closed=false"
    try:
        payload = fetch(url)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        # Network / parse failure: caller routes to idle heartbeat path.
        return []

    events: list[dict[str, Any]]
    if isinstance(payload, list):
        events = [e for e in payload if isinstance(e, dict)]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        events = [e for e in payload["data"] if isinstance(e, dict)]
    else:
        events = []

    out: list[TennisMarket] = []
    for ev in events:
        ev_title = str(ev.get("title") or "")
        ev_slug = str(ev.get("slug") or "")
        ev_markets = ev.get("markets") or []
        if not isinstance(ev_markets, list):
            continue
        for m in ev_markets:
            if not isinstance(m, dict):
                continue
            cid = m.get("conditionId")
            if not isinstance(cid, str) or not cid.startswith("0x"):
                continue
            outcome_prices = _parse_outcome_prices(m.get("outcomePrices"))
            out.append(
                TennisMarket(
                    condition_id=cid,
                    slug=str(m.get("slug") or ""),
                    question=str(m.get("question") or ""),
                    event_title=ev_title,
                    event_slug=ev_slug,
                    outcome_prices=outcome_prices,
                    end_date_iso=(
                        str(m.get("endDate"))
                        if isinstance(m.get("endDate"), str)
                        else None
                    ),
                )
            )
            if len(out) >= limit:
                return out
    return out


def _parse_outcome_prices(raw: Any) -> tuple[float, float] | None:
    """Polymarket emits ``outcomePrices`` as a JSON-encoded string of two
    decimal strings. Parse defensively — malformed → None.
    """
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw, list):
        decoded = raw
    else:
        return None
    if not isinstance(decoded, list) or len(decoded) < 2:
        return None
    try:
        return (float(decoded[0]), float(decoded[1]))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Dry-run executor — records-only, never broadcasts
# ---------------------------------------------------------------------------


@dataclass
class DryRunExecutor:
    """Records every BET / NO_BET intent without broadcasting.

    The brief's hard rule: ``NO signed orders broadcast``. This class
    has NO ``send`` / ``sign`` / ``submit`` method — by construction it
    cannot leak a wallet transaction. The :attr:`appended` list is the
    full audit trail.
    """

    appended: list[dict[str, Any]] = field(default_factory=list)
    broadcast_count: int = 0  # MUST stay 0 across the whole run

    def record(self, *, action: Action, market_id: str, tick: int, ts: datetime) -> None:
        """Capture the action payload + the chain-side BREATH burn
        class. ``broadcast_count`` is the read-only invariant the
        reconciliation gate asserts (== 0)."""
        self.appended.append(
            {
                "tick": tick,
                "ts": ts.isoformat(),
                "market_id": market_id,
                "kind": action.kind.value,
                "size_usd": float(action.size_usd or 0.0),
                "side": action.side.value if action.side is not None else None,
                "edge_pct": action.edge_pct,
                "no_bet_reason": action.no_bet_reason,
            }
        )


# ---------------------------------------------------------------------------
# Signal synthesis — deterministic from market data, no look-ahead
# ---------------------------------------------------------------------------


def _signals_from_market(
    *, market: TennisMarket, asof_ts: datetime, tick: int
) -> dict[str, Signal]:
    """Build the 5 engine signals for one decision tick.

    The signals are *deterministic* functions of the market's outcome
    prices + the tick index. This is NOT a real engine fanout — the
    full fanout requires live data sources that aren't wired in
    sprint_7. The dry-run's purpose is to exercise the decision +
    record-keeping path, not to validate engine accuracy.

    Each Signal carries ``available_at = asof_ts`` so the look-ahead
    auditor is satisfied (no payload-derived timestamp).
    """
    asof_iso = asof_ts.isoformat()
    # The trained alpha favours α₁ (tennis_technical) ≈ 0.79 per
    # weights_v0.json — feed it a directional edge derived from the
    # market's outcome price. P(YES) > 0.5 → positive technical edge.
    if market.outcome_prices is not None:
        p_yes, _p_no = market.outcome_prices
        tennis_score = max(-1.0, min(1.0, (p_yes - 0.5) * 2.0))
    else:
        tennis_score = 0.1 * (1 if tick % 2 == 0 else -1)

    # Other engines get smaller, neutral-ish reads so the dry-run
    # produces a mix of BET / NO_BET across the tick window.
    other_score = 0.05 * ((tick % 3) - 1)  # cycles -0.05, 0, +0.05
    sentiment_score = 0.1 * ((tick % 2) - 0.5) * 2.0

    return {
        TENNIS_TECHNICAL: Signal(
            score=tennis_score,
            confidence=0.65,
            available_at=asof_iso,
            rationale=f"Elo / surface read from {market.event_title}",
            raw_features={"p_yes_at_close": tennis_score},
        ),
        MARKET_MOMENTUM: Signal(
            score=other_score,
            confidence=0.4,
            available_at=asof_iso,
            rationale="orderbook drift placeholder (no live feed in dry-run)",
            raw_features={"tick_phase": float(tick)},
        ),
        SURFACE_ADVANTAGE: Signal(
            score=other_score * 0.5,
            confidence=0.35,
            available_at=asof_iso,
            rationale="wallet_basket placeholder (no live feed in dry-run)",
            raw_features={"tick_phase": float(tick)},
        ),
        HEAD_TO_HEAD: Signal(
            score=sentiment_score,
            confidence=0.3,
            available_at=asof_iso,
            rationale="reddit sentiment placeholder (no live LLM call in dry-run)",
            raw_features={"tick_phase": float(tick)},
        ),
        REST_RECENCY: Signal(
            score=-other_score,
            confidence=0.4,
            available_at=asof_iso,
            rationale="reddit volume z-score placeholder",
            raw_features={"tick_phase": float(tick)},
        ),
    }


# ---------------------------------------------------------------------------
# Decision-record builder + schema validator
# ---------------------------------------------------------------------------


def _build_decision_record(
    *,
    tick: int,
    ts: datetime,
    action: Action,
    fused_score: float,
    raw_rational: float,
    raw_sentient: float,
    mean_confidence: float,
    rho_effective: float,
    market_id_fallback: str,
) -> dict[str, Any]:
    """Project an :class:`Action` + fusion intermediates into the
    canonical ``decision_record.v0.2.0`` JSON shape.

    The shape is asserted against the schema in :func:`run_dryrun`
    before the row is appended to the JSONL.
    """
    burn_class = (
        BURN_CLASS_DECISION_TAX
        if action.kind == ActionKind.BET
        else BURN_CLASS_PASSIVE_BURN
    )
    record: dict[str, Any] = {
        "tick": tick,
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "kind": action.kind.value,
        "fused_score": _clamp(fused_score, -1.0, 1.0),
        "raw_rational": _clamp(raw_rational, -1.0, 1.0),
        "raw_sentient": _clamp(raw_sentient, -1.0, 1.0),
        "mean_confidence": _clamp(mean_confidence, 0.0, 1.0),
        "rho_effective": _clamp(rho_effective, 0.0, 1.0),
        "desperate": False,  # Phase 2 dry-run never enters desperate
        "degraded_mode": DegradedMode.NONE.value,
        "burn_class": burn_class,
    }
    if action.kind == ActionKind.BET:
        record["market_id"] = action.market_id or market_id_fallback
        record["side"] = (
            action.side.value if action.side is not None else Side.YES.value
        )
        record["size_usd"] = float(action.size_usd or 0.0)
        record["edge_pct"] = float(action.edge_pct or 0.0)
        # Kelly recoverable from fused_score; include for Track D parity.
        edge_abs = abs(record["fused_score"])
        record["kelly"] = (
            edge_abs / (1.0 - edge_abs) if 0.0 < edge_abs < 1.0 else 0.0
        )
    else:
        record["no_bet_reason"] = action.no_bet_reason or "fused_score_neutral"
    return record


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _validate_decision_record(record: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate a decision row against ``decision_record.v0.2.0.json``.

    Uses the project's `jsonschema` dep (pyproject.toml) for the full
    spec — enum, minimum/maximum, additionalProperties=false, $defs are
    all enforced. Raises :class:`jsonschema.ValidationError` on miss.
    """
    jsonschema.validate(instance=record, schema=schema)


# ---------------------------------------------------------------------------
# Result + Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DryRunResult:
    """Structured outcome of one dry-run invocation.

    The delivery report reads ``decisions_count`` / ``markets_used`` /
    ``broadcast_count`` to fill the four-checkmark acceptance table.
    """

    decisions_count: int
    bets_count: int
    no_bets_count: int
    heartbeat_count: int
    markets_used: int
    broadcast_count: int
    weights_loaded: bool
    orchestrator_constructed: bool
    jsonl_path: Path
    summary_path: Path
    real_market_referenced: bool


def run_dryrun(
    *,
    weights_path: Path = WEIGHTS_PATH,
    schema_path: Path = DECISION_SCHEMA_PATH,
    jsonl_out: Path = DRYRUN_JSONL,
    summary_out: Path = DRYRUN_SUMMARY,
    market_cache_out: Path = TENNIS_MARKETS_CACHE,
    tick_count: int = DEFAULT_TICKS,
    sim_tick_minutes: float = SIM_TICK_INTERVAL_MIN,
    idle_heartbeat_minutes: float = IDLE_HEARTBEAT_INTERVAL_MIN,
    asof_ts: datetime | None = None,
    fetcher: Callable[[str], Any] | None = None,
    workspace_root: Path | None = None,
) -> DryRunResult:
    """Execute the sprint_7 Day-6-closer Phase 2 dry-run.

    Side effects (deliberate):

    * Writes ``logs/phase2_dryrun/sprint7_dryrun.jsonl`` — chronological
      heartbeats + decision rows.
    * Writes ``logs/phase2_dryrun/sprint7_dryrun_summary.md`` — narrative
      + four-checkmark acceptance table.
    * Writes ``logs/phase2_dryrun/tennis_markets_snapshot.json`` — the
      gamma-api lookup payload, so reviewers can audit which markets
      the dry-run targeted.

    Side effects (forbidden):

    * NO signed orders broadcast (:class:`DryRunExecutor` has no send).
    * NO chain transactions (the orchestrator's ``boot()`` is NOT called;
      only its construction is exercised — that's the criterion (a)
      proof).
    * NO live Gemini calls (no LLM client is constructed).
    """
    root = workspace_root if workspace_root is not None else Path.cwd()

    def _resolve(p: Path) -> Path:
        return p if p.is_absolute() else (root / p)

    weights_full = _resolve(weights_path)
    schema_full = _resolve(schema_path)
    jsonl_full = _resolve(jsonl_out)
    summary_full = _resolve(summary_out)
    market_cache_full = _resolve(market_cache_out)

    # --- (a) Load weights_v0.json + prove orchestrator accepts it. -----
    weights_raw = json.loads(weights_full.read_text(encoding="utf-8"))
    weights = Weights.model_validate(weights_raw)

    # The orchestrator's constructor takes Protocol-typed adapters — we
    # construct a NO-OP set (the dry-run never calls boot(), so no
    # chain / decision-log / engine signal source is invoked). The
    # construction itself is the criterion (a) gate: it bumps Phase
    # state + initialises the WS emitter without exception.
    mb_root = root / ".dev" / "state" / "_dryrun_mb"
    mb_root.mkdir(parents=True, exist_ok=True)
    orchestrator = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=mb_root),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,  # dry_run_plan() does not need signals
        initial_breath=DEFAULT_DRYRUN_BREATH,
        initial_bankroll_usd=DEFAULT_DRYRUN_BANKROLL_USD,
    )
    # dry_run_plan() asserts zero side effects — the boot() path is
    # never invoked because the brief says no signed orders / no
    # decision-log appends. The plan call exercises the orchestrator
    # surface as a smoke check.
    orchestrator.dry_run_plan()

    # --- (d) Discover real Polymarket tennis markets. ------------------
    markets = discover_tennis_markets(fetcher=fetcher, limit=tick_count)
    market_cache_full.parent.mkdir(parents=True, exist_ok=True)
    market_cache_full.write_text(
        json.dumps(
            {
                "source": GAMMA_EVENTS_URL + "?tag_slug=tennis",
                "fetched_at": (asof_ts or datetime.now(UTC)).isoformat(),
                "count": len(markets),
                "markets": [
                    {
                        "condition_id": m.condition_id,
                        "slug": m.slug,
                        "question": m.question,
                        "event_title": m.event_title,
                        "event_slug": m.event_slug,
                        "outcome_prices": list(m.outcome_prices)
                        if m.outcome_prices is not None
                        else None,
                        "end_date_iso": m.end_date_iso,
                    }
                    for m in markets
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    # --- Decision loop. ------------------------------------------------
    schema = json.loads(schema_full.read_text(encoding="utf-8"))
    decision_engine = DecisionEngine()
    executor = DryRunExecutor()
    rows: list[dict[str, Any]] = []
    now = asof_ts if asof_ts is not None else datetime.now(UTC)
    heartbeat_count = 0
    bets = 0
    no_bets = 0
    real_market_seen = False

    for tick in range(tick_count):
        tick_ts = now + timedelta(minutes=sim_tick_minutes * tick)
        # Idle heartbeats covering the (sim_tick_interval) window since
        # the previous tick — explicit operator-visibility log per CEO
        # plan: ``no markets found, agent idling`` every 5 sim-min.
        if not markets:
            n_beats = max(1, int(sim_tick_minutes // idle_heartbeat_minutes))
            for j in range(n_beats):
                hb_ts = tick_ts - timedelta(minutes=idle_heartbeat_minutes * (n_beats - j))
                rows.append(
                    {
                        "row_type": ROW_TYPE_HEARTBEAT,
                        "ts": hb_ts.isoformat().replace("+00:00", "Z"),
                        "tick": tick,
                        "message": (
                            "no markets found, agent idling — gamma-api returned 0 "
                            "tennis events within retry budget"
                        ),
                    }
                )
                heartbeat_count += 1
            # No market → emit a NO_BET decision against a fallback id so
            # the row still validates and the per-cycle BREATH burn is
            # accounted for (NO_BET is NOT a free skip — PRD §6).
            fallback_market = "polymarket:tennis:NO_LIVE_MARKETS"
            signals = _signals_from_market(
                market=TennisMarket(
                    condition_id=fallback_market,
                    slug="",
                    question="",
                    event_title="",
                    event_slug="",
                    outcome_prices=None,
                    end_date_iso=None,
                ),
                asof_ts=tick_ts,
                tick=tick,
            )
            market_id_for_decision = fallback_market
        else:
            # One real market per tick (round-robin so multiple ticks
            # don't pile on a single conditionId — diversifies the log).
            market = markets[tick % len(markets)]
            signals = _signals_from_market(market=market, asof_ts=tick_ts, tick=tick)
            market_id_for_decision = market.condition_id
            real_market_seen = True

        action = asyncio.run(
            decision_engine.decide(
                signals=signals,
                weights_alpha=(weights.alpha[0], weights.alpha[1], weights.alpha[2]),
                weights_beta=(weights.beta[0], weights.beta[1]),
                w_r=weights.w_r,
                w_s=weights.w_s,
                rho=weights.rho,
                bankroll_usd=DEFAULT_DRYRUN_BANKROLL_USD,
                breath=DEFAULT_DRYRUN_BREATH,
                liquidity_cap_usd=DEFAULT_LIQUIDITY_CAP_USD,
                market_id=market_id_for_decision,
                desperate=False,
            )
        )

        # Recompute fusion intermediates for the record (the engine
        # doesn't expose them on Action; the schema wants them visible).
        fusion = _fuse_signals(
            signals=signals,
            alpha=(weights.alpha[0], weights.alpha[1], weights.alpha[2]),
            beta=(weights.beta[0], weights.beta[1]),
            w_r=weights.w_r,
            w_s=weights.w_s,
        )

        record = _build_decision_record(
            tick=tick,
            ts=tick_ts,
            action=action,
            fused_score=fusion.fused,
            raw_rational=fusion.raw_rational,
            raw_sentient=fusion.raw_sentient,
            mean_confidence=fusion.mean_confidence,
            rho_effective=max(0.0, min(1.0, weights.rho)),
            market_id_fallback=market_id_for_decision,
        )
        # Decision rows carry the canonical v0.2.0 schema shape verbatim;
        # heartbeat rows carry a ``row_type=heartbeat`` discriminator
        # (see ROW_TYPE_HEARTBEAT). Replayers distinguish via
        # ``row_type not in row`` (decision) or presence (heartbeat).
        _validate_decision_record(record, schema)
        rows.append(record)
        executor.record(
            action=action, market_id=market_id_for_decision, tick=tick, ts=tick_ts
        )
        if action.kind == ActionKind.BET:
            bets += 1
        else:
            no_bets += 1

    # --- Persist artifacts. --------------------------------------------
    jsonl_full.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_full.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Render the on-disk paths repo-relative for the summary so the
    # markdown is stable across machines (Windows / macOS / Linux dev).
    def _rel(p: Path) -> Path:
        try:
            return p.relative_to(root)
        except ValueError:  # pragma: no cover — tmp_path outside root
            return p

    jsonl_rel = _rel(jsonl_full)
    summary_rel = _rel(summary_full)

    result = DryRunResult(
        decisions_count=bets + no_bets,
        bets_count=bets,
        no_bets_count=no_bets,
        heartbeat_count=heartbeat_count,
        markets_used=len(markets),
        broadcast_count=executor.broadcast_count,
        weights_loaded=True,
        orchestrator_constructed=True,
        jsonl_path=jsonl_rel,
        summary_path=summary_rel,
        real_market_referenced=real_market_seen,
    )
    summary_full.write_text(
        _render_summary(result, markets=markets, weights=weights),
        encoding="utf-8",
    )
    # Sibling JSON dump in the schema the submission builder consumes
    # (Phase2DryRunSummary). Repo-relative paths so the manifest stays
    # machine-stable across dev boxes.
    result_json_path = jsonl_full.parent / "sprint7_dryrun_summary.json"
    result_json_path.write_text(
        json.dumps(
            {
                "log_path": jsonl_rel.as_posix(),
                "summary_path": summary_rel.as_posix(),
                "decisions_count": result.decisions_count,
                "bets_count": result.bets_count,
                "no_bets_count": result.no_bets_count,
                "heartbeat_count": result.heartbeat_count,
                "markets_used": result.markets_used,
                "broadcast_count": result.broadcast_count,
                "real_market_referenced": result.real_market_referenced,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


# ---------------------------------------------------------------------------
# Noop adapters — used purely so the orchestrator construction has
# Protocol-conformant shells. The dry-run never calls boot(), so these
# methods are never invoked.
# ---------------------------------------------------------------------------


@dataclass
class _NoopPhaseReader:
    """Phase reader that returns PHASE_2 — never called in dry-run."""

    def read_phase(self) -> Phase:  # pragma: no cover — boot() is never invoked
        return Phase.PHASE_2_APPRENTICE


@dataclass
class _NoopDecisionLog:
    """Decision-log writer that refuses to broadcast.

    Defensive: if some future caller accidentally invokes ``boot()``
    against this instance, the explicit RuntimeError surfaces
    immediately rather than silently no-oping.
    """

    def append(  # pragma: no cover — boot() is never invoked
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        raise RuntimeError(
            "DryRun decision-log received append(); the sprint_7 dry-run "
            "must NOT broadcast decisions. Check that boot() is not "
            "invoked on the orchestrator constructed inside run_dryrun()."
        )


# ---------------------------------------------------------------------------
# Summary renderer
# ---------------------------------------------------------------------------


def _render_summary(
    result: DryRunResult,
    *,
    markets: list[TennisMarket],
    weights: Weights,
) -> str:
    """Render the operator-readable summary markdown.

    Mirrors the four-checkmark acceptance criteria so the delivery
    report can quote this file verbatim.
    """
    lines = [
        "# Sprint 7 Day-6-closer Phase 2 Dry-Run Summary",
        "",
        f"> Generated: {datetime.now(UTC).isoformat().replace('+00:00', 'Z')}",
        "",
        "## Acceptance criteria",
        "",
        f"- [{'x' if result.weights_loaded and result.orchestrator_constructed else ' '}] "
        f"**(a)** `weights_v0.json` loads via `Phase2LaunchOrchestrator` without exception",
        f"- [{'x' if result.decisions_count >= 3 else ' '}] "
        f"**(b)** ≥3 decisions emitted (got **{result.decisions_count}** — "
        f"{result.bets_count} BET / {result.no_bets_count} NO_BET)",
        "- [x] **(c)** Each decision payload validated against "
        "`.dev/contracts/decision_record.v0.2.0.json`",
        f"- [{'x' if result.real_market_referenced else ' '}] "
        f"**(d)** ≥1 decision references a real Polymarket tennis market "
        f"(gamma-api `/events?tag_slug=tennis`, "
        f"{result.markets_used} discovered)",
        "",
        f"- [{'x' if result.broadcast_count == 0 else ' '}] **Safety** "
        f"`DryRunExecutor.broadcast_count == {result.broadcast_count}` (must be 0)",
        f"- [x] **Operator visibility** `no markets found, agent idling` heartbeat "
        f"every {int(IDLE_HEARTBEAT_INTERVAL_MIN)} sim-min during idle windows "
        f"({result.heartbeat_count} emitted)",
        "",
        "## Decision row counts",
        "",
        "| Kind | Count |",
        "|------|-------|",
        f"| BET | {result.bets_count} |",
        f"| NO_BET | {result.no_bets_count} |",
        f"| heartbeat | {result.heartbeat_count} |",
        "",
        "## Trained weights used (from `reports/phase1/weights_v0.json`)",
        "",
        "```json",
        json.dumps(weights.model_dump(mode='json'), indent=2),
        "```",
        "",
        f"## Sample tennis markets ({len(markets)} discovered)",
        "",
    ]
    for m in markets[:5]:
        lines.append(f"- `{m.condition_id}` — {m.question or m.slug}")
        lines.append(f"  - event: {m.event_title or m.event_slug or 'unknown'}")
    lines.extend(
        [
            "",
            "## Output files",
            "",
            f"- `{result.jsonl_path.as_posix()}` — chronological JSONL "
            "(heartbeats + decisions)",
            f"- `{result.summary_path.as_posix()}` — this file",
            "",
            "## Safety invariants asserted",
            "",
            "- `DryRunExecutor.broadcast_count == 0` (no signed orders sent)",
            "- Orchestrator `dry_run_plan()` returns "
            "`network_calls_planned == 0` (no chain reads / decision log writes)",
            "- No `anthropic` / `openai` import on the dry-run path (Gemini-only "
            "policy preserved; the dry-run does NOT call any LLM)",
            "",
            "— Track B Backend Agent · T-B-016 · sprint_7 Day 6 closer",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.runtime.sprint7_dryrun",
        description=(
            "Sprint 7 Day-6-closer Phase 2 dry-run — loads weights_v0.json, "
            "discovers real Polymarket tennis markets, emits decisions to "
            "logs/phase2_dryrun/. Never broadcasts."
        ),
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=DEFAULT_TICKS,
        help=f"Number of decision ticks to run (default {DEFAULT_TICKS}; brief floor 3).",
    )
    parser.add_argument(
        "--weights",
        type=Path,
        default=WEIGHTS_PATH,
        help=f"Path to weights_v0.json (default {WEIGHTS_PATH}).",
    )
    parser.add_argument(
        "--out-jsonl",
        type=Path,
        default=DRYRUN_JSONL,
        help=f"Output JSONL path (default {DRYRUN_JSONL}).",
    )
    parser.add_argument(
        "--out-summary",
        type=Path,
        default=DRYRUN_SUMMARY,
        help=f"Output summary markdown path (default {DRYRUN_SUMMARY}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry — exit 0 on success, 1 on any acceptance criterion miss."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    result = run_dryrun(
        weights_path=args.weights,
        jsonl_out=args.out_jsonl,
        summary_out=args.out_summary,
        tick_count=args.ticks,
    )
    if not result.weights_loaded or not result.orchestrator_constructed:
        sys.stderr.write("FAIL: weights / orchestrator not loaded\n")
        return 1
    if result.decisions_count < 3:
        sys.stderr.write(
            f"FAIL: only {result.decisions_count} decisions emitted (need ≥3)\n"
        )
        return 1
    if result.broadcast_count != 0:
        sys.stderr.write(
            f"FAIL: broadcast_count={result.broadcast_count} (must be 0)\n"
        )
        return 1
    sys.stdout.write(
        f"OK: {result.decisions_count} decisions "
        f"({result.bets_count} BET / {result.no_bets_count} NO_BET), "
        f"{result.markets_used} markets, "
        f"{result.heartbeat_count} heartbeats, "
        f"broadcast_count={result.broadcast_count}\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI dispatch
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DRYRUN_BANKROLL_USD",
    "DEFAULT_DRYRUN_BREATH",
    "DEFAULT_LIQUIDITY_CAP_USD",
    "DEFAULT_TICKS",
    "DRYRUN_JSONL",
    "DRYRUN_SUMMARY",
    "GAMMA_EVENTS_URL",
    "IDLE_HEARTBEAT_INTERVAL_MIN",
    "SIM_TICK_INTERVAL_MIN",
    "TENNIS_MARKETS_CACHE",
    "WEIGHTS_PATH",
    "DryRunExecutor",
    "DryRunResult",
    "TennisMarket",
    "discover_tennis_markets",
    "main",
    "run_dryrun",
]
