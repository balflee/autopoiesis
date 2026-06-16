"""T-B-021 sprint-closer Money Shot capture script.

CEO sprint_8 D-2026-05-26-PLAN-003 (Day 5):

    Money Shot screenshots: live mock bet placed, settlement win,
    Death Watch active, Tombstone NFT mint moment.

This script is the **engineering artefact** that lets the USER produce
the Day 6 demo video: it drives a deterministic three-act sequence
through :class:`SandboxPhase2Loop` and writes one JSON capture per act
into ``reports/sprint8/money_shot/``:

* ``01_bet_placed.json``        — Act I: tick 1 places a BET on a mock
  market. Carries the post-tick :class:`AgentStateSnapshot` + the
  appended :class:`DecisionRecord` + the open-bet record.
* ``02_settlement_win.json``    — Act II: the market resolves, the
  settlement poller fires, BREATH grows from the realised PnL.
  Carries the snapshot + decision + the settled-bet record.
* ``03_terminal_lucidity.json`` — Act III: a fresh
  :class:`SandboxPhase2Loop` boots against the same on-disk state
  with ``SANDBOX_FORCE_TERMINAL=1`` and the V-gate forced-terminal
  hook drives BREATH to 0 → kill() + Tombstone mint. Carries the
  terminal snapshot + the final decision + the death receipt + the
  full Tombstone metadata bundle.

A fourth artefact, ``reports/sprint8/sprint8_final_summary.md``, is the
sprint-closer's narrative one-pager: runtime hours, decisions emitted,
bets placed / settled, P&L delta, BREATH trajectory, mint timestamp.

The whole pipeline is offline + deterministic — no real Polymarket
calls, no real Polygon RPC, no real LLM call. Every external surface
is a fake injected through the same Protocols the production wiring
satisfies (sandbox-safe invariant per the T-B-021 brief).

Running
-------

::

    python -m agent.scripts.capture_money_shot

Exits 0 on success; non-zero on any unexpected exception (the runbook
in ``agent/SUBMISSION_DRAFT.md`` says "re-run; investigate diff").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase
from agent.data.polymarket_sandbox_executor import MarketInfo, SandboxExecutor
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    SandboxStateWriter,
    iter_jsonl,
)
from agent.engines.base import Signal
from agent.engines.decision import (
    HEAD_TO_HEAD,
    MARKET_MOMENTUM,
    REST_RECENCY,
    SURFACE_ADVANTAGE,
    TENNIS_TECHNICAL,
)
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    SANDBOX_FORCE_TERMINAL_ENV_VAR,
    DeathReceipt,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    WeightUpdaterPhase,
)

# --------------------------------------------------------------------------- #
# Deterministic fakes — same shape as the test fixtures but inlined so the
# script does not import from `tests/` (production code never depends on the
# test package).
# --------------------------------------------------------------------------- #


@dataclass
class _ScriptedChainAdapter:
    """In-memory :class:`SandboxLoopChainAdapter` for the capture run.

    Tracks BREATH on a single mutable scalar; kill_and_mint records the
    full Tombstone metadata bundle so the third act can serialise it
    into ``03_terminal_lucidity.json`` verbatim.
    """

    current_breath: float = 100.0
    pnl_updates: list[float] = field(default_factory=list)
    kill_kwargs: dict[str, Any] | None = None

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.pnl_updates.append(pnl_usd)
        self.current_breath += pnl_usd

    async def read_breath(self) -> float:
        return self.current_breath

    async def kill_and_mint_tombstone(
        self,
        *,
        agent_id: str,
        bankroll_usd: float,
        last_tick: int,
        final_weights_hash: str,
        memory_bank_cid: str,
        last_words: str,
    ) -> DeathReceipt:
        self.kill_kwargs = {
            "agent_id": agent_id,
            "bankroll_usd": bankroll_usd,
            "last_tick": last_tick,
            "final_weights_hash": final_weights_hash,
            "memory_bank_cid": memory_bank_cid,
            "last_words": last_words,
        }
        return DeathReceipt(
            kill_tx_hash="0x" + "k" * 64,
            tombstone_token_id="ms-001",
            tombstone_tx_hash="0x" + "t" * 64,
        )


@dataclass
class _ScriptedGammaAPI:
    """Deterministic settlement-client fake.

    Markets transition from pending to resolved when :meth:`resolve_now`
    is called between acts. The shape mirrors the testing
    ``MockGammaAPI`` but inlined to avoid a tests-package import.
    """

    _resolved: dict[str, SettlementResult] = field(default_factory=dict)
    _registered_end_dates: dict[str, datetime] = field(default_factory=dict)

    def register_market(
        self,
        *,
        market_id: str,
        end_date: datetime,
    ) -> None:
        self._registered_end_dates[market_id] = end_date

    def resolve_now(
        self,
        *,
        market_id: str,
        outcome: str = "yes",
        winning_price: float = 1.0,
        resolution_ts: datetime,
    ) -> None:
        end_date = self._registered_end_dates.get(market_id, resolution_ts)
        self._resolved[market_id] = SettlementResult(
            market_id=market_id,
            resolved=True,
            outcome=cast(Any, outcome),
            winning_price=winning_price,
            resolution_ts=resolution_ts.astimezone(UTC),
            end_date=end_date.astimezone(UTC),
        )

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        return self._resolved.get(market_id)


@dataclass
class _CollectingStateHook:
    events: list[dict[str, Any]] = field(default_factory=list)

    def emit(self, *, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})


class _NopSleeper:
    async def __call__(self, seconds: float) -> None:  # pragma: no cover
        return None


class _AdvancingClock:
    """Tick-advancing clock; each :meth:`now` advances by ``step``."""

    def __init__(self, *, start: datetime, step: timedelta = timedelta(minutes=60)):
        self._now = start
        self._step = step

    def now(self) -> datetime:
        out = self._now
        self._now = self._now + self._step
        return out


@dataclass
class _BullishTickInputs:
    market_id: str
    price: float = 0.4
    liquidity_cap_usd: float = 50.0

    def inputs_for(
        self, *, asof_ts: datetime, tick: int
    ) -> TickInputs | None:
        iso = asof_ts.isoformat()
        signals = {
            TENNIS_TECHNICAL: Signal(
                score=0.9, confidence=0.9, available_at=iso,
                rationale="strong technical read",
                raw_features={"tick": float(tick)},
            ),
            MARKET_MOMENTUM: Signal(
                score=0.8, confidence=0.9, available_at=iso,
                rationale="momentum agrees",
                raw_features={"tick": float(tick)},
            ),
            SURFACE_ADVANTAGE: Signal(
                score=0.7, confidence=0.85, available_at=iso,
                rationale="wallets favour YES",
                raw_features={"tick": float(tick)},
            ),
            HEAD_TO_HEAD: Signal(
                score=0.6, confidence=0.8, available_at=iso,
                rationale="sentiment positive",
                raw_features={"tick": float(tick)},
            ),
            REST_RECENCY: Signal(
                score=0.6, confidence=0.85, available_at=iso,
                rationale="crowd volume rising sharply",
                raw_features={"tick": float(tick)},
            ),
        }
        return TickInputs(
            market_id=self.market_id,
            signals=signals,
            price=self.price,
            liquidity_cap_usd=self.liquidity_cap_usd,
        )


class _SilentWeightUpdater:
    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None:
        return None


class _NoopPhaseReader:
    def read_phase(self) -> Phase:
        return Phase.PHASE_2_APPRENTICE


class _NoopDecisionLog:
    def append(
        self,
        *,
        market_id: str,
        action: ActionKind,
        size_usd: float,
        side: str | None,
        edge_pct: float | None,
    ) -> str:
        return "0x_unused"


# --------------------------------------------------------------------------- #
# Capture pipeline
# --------------------------------------------------------------------------- #


MONEY_SHOT_MARKET_ID = "m-money-shot-001"
MONEY_SHOT_START = datetime(2026, 5, 26, 20, 0, 0, tzinfo=UTC)


def _build_loop(
    *,
    state_dir: Path,
    memory_bank_root: Path,
    chain_adapter: _ScriptedChainAdapter,
    gamma: _ScriptedGammaAPI,
    weight_updater: _SilentWeightUpdater,
    state_hook: _CollectingStateHook,
    clock: _AdvancingClock,
    env: dict[str, str] | None,
    last_words: str | None = None,
    memory_bank_cid: str = "ipfs://sandbox-money-shot",
) -> SandboxPhase2Loop:
    writer = SandboxStateWriter(root=state_dir)
    executor = SandboxExecutor(
        state_writer=writer,
        market_resolver=lambda mid: MarketInfo(
            end_date_iso="2026-05-26T17:00:00+00:00"
        ),
        clock=clock,
    )
    base = Phase2LaunchOrchestrator(
        memory_bank=MemoryBank(root=memory_bank_root),
        phase_reader=_NoopPhaseReader(),
        decision_log=_NoopDecisionLog(),
        engine_signals=None,
    )
    return SandboxPhase2Loop(
        base=base,
        state_dir=state_dir,
        weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
        executor=executor,
        settlement_client=gamma,
        weight_updater=weight_updater,
        chain_adapter=cast(SandboxLoopChainAdapter, chain_adapter),
        tick_inputs=_BullishTickInputs(market_id=MONEY_SHOT_MARKET_ID),
        state_hook=state_hook,
        state_writer=writer,
        clock=clock,
        sleeper=_NopSleeper(),
        decision_cadence=timedelta(0),
        initial_breath=chain_adapter.current_breath,
        initial_bankroll_usd=100.0,
        agent_id="genesis_v1_demo",
        memory_bank_cid=memory_bank_cid,
        last_words=last_words,
        env=env,
    )


def _read_snapshot(writer: SandboxStateWriter) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(writer.snapshot_path.read_text(encoding="utf-8")),
    )


def _tail_decision(writer: SandboxStateWriter) -> dict[str, Any] | None:
    rows = list(iter_jsonl(writer.decisions_path))
    return rows[-1] if rows else None


def _tail_open_bet(writer: SandboxStateWriter) -> dict[str, Any] | None:
    rows = list(iter_jsonl(writer.open_bets_path))
    return rows[-1] if rows else None


def _tail_settled_bet(writer: SandboxStateWriter) -> dict[str, Any] | None:
    rows = list(iter_jsonl(writer.settled_bets_path))
    return rows[-1] if rows else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass
class _CaptureMetrics:
    """Aggregate metrics threaded through the three acts."""

    started_at: datetime
    finished_at: datetime | None = None
    decisions_emitted: int = 0
    bets_placed: int = 0
    bets_settled: int = 0
    pnl_delta: float = 0.0
    initial_breath: float = 0.0
    pre_terminal_breath: float = 0.0
    final_breath: float = 0.0
    tombstone_token_id: str | None = None
    tombstone_tx_hash: str | None = None
    mint_timestamp_iso: str | None = None


def _capture_act_1_bet_placed(
    *,
    out_dir: Path,
    state_dir: Path,
    chain: _ScriptedChainAdapter,
    gamma: _ScriptedGammaAPI,
    weight_updater: _SilentWeightUpdater,
    state_hook: _CollectingStateHook,
    clock: _AdvancingClock,
    memory_bank_root: Path,
    metrics: _CaptureMetrics,
) -> SandboxStateWriter:
    """Act I — fresh loop, 1 tick, BET placed."""
    loop = _build_loop(
        state_dir=state_dir,
        memory_bank_root=memory_bank_root,
        chain_adapter=chain,
        gamma=gamma,
        weight_updater=weight_updater,
        state_hook=state_hook,
        clock=clock,
        env={},  # natural flow — no forced-terminal hook
    )
    metrics.initial_breath = chain.current_breath
    summary = asyncio.run(loop.run(max_ticks=1))
    metrics.decisions_emitted += summary.ticks_completed
    metrics.bets_placed += summary.bets_placed

    writer = loop.writer
    capture = {
        "act": "01_bet_placed",
        "caption": "Live mock bet placed — agent commits BREATH to a YES position",
        "agent_state_snapshot": _read_snapshot(writer),
        "decision_record": _tail_decision(writer),
        "open_bet_record": _tail_open_bet(writer),
        "summary": {
            "ticks_completed": summary.ticks_completed,
            "bets_placed": summary.bets_placed,
            "no_bets_emitted": summary.no_bets_emitted,
        },
    }
    _write_json(out_dir / "01_bet_placed.json", capture)
    return writer


def _capture_act_2_settlement_win(
    *,
    out_dir: Path,
    state_dir: Path,
    chain: _ScriptedChainAdapter,
    gamma: _ScriptedGammaAPI,
    weight_updater: _SilentWeightUpdater,
    state_hook: _CollectingStateHook,
    clock: _AdvancingClock,
    memory_bank_root: Path,
    metrics: _CaptureMetrics,
) -> SandboxStateWriter:
    """Act II — same on-disk state, 1 more tick after market resolution."""
    # Resolve the market between the acts (mirrors the real flow: UMA
    # finalises while the agent waits).
    gamma.resolve_now(
        market_id=MONEY_SHOT_MARKET_ID,
        outcome="yes",
        winning_price=1.0,
        resolution_ts=clock.now(),
    )
    loop = _build_loop(
        state_dir=state_dir,
        memory_bank_root=memory_bank_root,
        chain_adapter=chain,
        gamma=gamma,
        weight_updater=weight_updater,
        state_hook=state_hook,
        clock=clock,
        env={},
    )
    bankroll_pre = chain.current_breath  # BREATH proxy for PnL delta
    summary = asyncio.run(loop.run(max_ticks=1))
    metrics.decisions_emitted += summary.ticks_completed
    metrics.bets_placed += summary.bets_placed
    metrics.bets_settled += summary.settlements_processed
    metrics.pnl_delta += chain.current_breath - bankroll_pre
    metrics.pre_terminal_breath = chain.current_breath

    writer = loop.writer
    capture = {
        "act": "02_settlement_win",
        "caption": "Market resolved YES — settlement poller credits realised PnL",
        "agent_state_snapshot": _read_snapshot(writer),
        "decision_record": _tail_decision(writer),
        "settled_bet_record": _tail_settled_bet(writer),
        "summary": {
            "ticks_completed": summary.ticks_completed,
            "bets_placed": summary.bets_placed,
            "settlements_processed": summary.settlements_processed,
        },
    }
    _write_json(out_dir / "02_settlement_win.json", capture)
    return writer


def _capture_act_3_terminal_lucidity(
    *,
    out_dir: Path,
    state_dir: Path,
    chain: _ScriptedChainAdapter,
    gamma: _ScriptedGammaAPI,
    weight_updater: _SilentWeightUpdater,
    state_hook: _CollectingStateHook,
    clock: _AdvancingClock,
    memory_bank_root: Path,
    metrics: _CaptureMetrics,
) -> SandboxStateWriter:
    """Act III — fresh loop with SANDBOX_FORCE_TERMINAL=1 → kill + mint."""
    loop = _build_loop(
        state_dir=state_dir,
        memory_bank_root=memory_bank_root,
        chain_adapter=chain,
        gamma=gamma,
        weight_updater=weight_updater,
        state_hook=state_hook,
        clock=clock,
        env={SANDBOX_FORCE_TERMINAL_ENV_VAR: "1"},
        last_words=(
            "Sprint 8 sandbox extended Phase 2 completes. The weights I "
            "leave behind already know more than I did at tick 0."
        ),
    )
    summary = asyncio.run(loop.run(max_ticks=1))
    metrics.decisions_emitted += summary.ticks_completed
    # The force-terminal tick may also process residual settlements
    # (the act-2 second bet on the same market) — count them so the
    # "bets_settled" total matches the on-disk settled_bets.jsonl.
    metrics.bets_settled += summary.settlements_processed
    metrics.final_breath = chain.current_breath
    if summary.death_receipt is not None:
        metrics.tombstone_token_id = summary.death_receipt.tombstone_token_id
        metrics.tombstone_tx_hash = summary.death_receipt.tombstone_tx_hash
        metrics.mint_timestamp_iso = clock.now().astimezone(UTC).isoformat()

    writer = loop.writer
    capture = {
        "act": "03_terminal_lucidity",
        "caption": (
            "BREATH driven to 0 by SANDBOX_FORCE_TERMINAL=1 — kill() fires, "
            "Tombstone NFT mints with finalWeightsHash + memoryBankCid + last_words"
        ),
        "agent_state_snapshot": _read_snapshot(writer),
        "decision_record": _tail_decision(writer),
        "death_receipt": (
            {
                "kill_tx_hash": summary.death_receipt.kill_tx_hash,
                "tombstone_token_id": summary.death_receipt.tombstone_token_id,
                "tombstone_tx_hash": summary.death_receipt.tombstone_tx_hash,
            }
            if summary.death_receipt is not None
            else None
        ),
        "tombstone_metadata": chain.kill_kwargs,
        "summary": {
            "ticks_completed": summary.ticks_completed,
            "died": summary.died,
            "final_breath": summary.final_breath,
            "final_bankroll_usd": summary.final_bankroll_usd,
        },
    }
    _write_json(out_dir / "03_terminal_lucidity.json", capture)
    return writer


def _write_final_summary(*, out_dir: Path, metrics: _CaptureMetrics) -> None:
    """Render the sprint8 final-summary markdown.

    Per the T-B-021 brief: runtime hours, decisions emitted, bets
    placed / settled, P&L delta, BREATH trajectory, mint timestamp.
    """
    assert metrics.finished_at is not None  # set by caller
    runtime_hours = (
        metrics.finished_at - metrics.started_at
    ).total_seconds() / 3600.0
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.write_text(
        "# Sprint 8 — Final Summary (Money Shot capture)\n\n"
        "> Generated by `python -m agent.scripts.capture_money_shot`.\n"
        "> Deterministic offline capture — no live Polymarket / Polygon /\n"
        "> LLM calls. Each `reports/sprint8/money_shot/*.json` artefact\n"
        "> is one frame of the Day 6 demo video.\n\n"
        "## Run window\n\n"
        f"- Started:  `{metrics.started_at.isoformat()}`\n"
        f"- Finished: `{metrics.finished_at.isoformat()}`\n"
        f"- Runtime hours (capture wall-clock): `{runtime_hours:.4f}`\n\n"
        "## Aggregate metrics\n\n"
        f"- Decisions emitted: **{metrics.decisions_emitted}**\n"
        f"- Bets placed:       **{metrics.bets_placed}**\n"
        f"- Bets settled:      **{metrics.bets_settled}**\n"
        f"- P&L delta (BREATH proxy, USD):     "
        f"**{metrics.pnl_delta:+.4f}**\n\n"
        "## BREATH trajectory\n\n"
        f"- Initial BREATH:        `{metrics.initial_breath:.4f}`\n"
        f"- Pre-terminal BREATH:   `{metrics.pre_terminal_breath:.4f}`\n"
        f"- Final BREATH (post-kill): `{metrics.final_breath:.4f}`\n\n"
        "## Tombstone NFT mint\n\n"
        f"- Token ID:        `{metrics.tombstone_token_id}`\n"
        f"- Mint tx hash:    `{metrics.tombstone_tx_hash}`\n"
        f"- Mint timestamp:  `{metrics.mint_timestamp_iso}`\n\n"
        "## Notes\n\n"
        "* All transaction hashes above are deterministic placeholders\n"
        "  emitted by the scripted chain adapter; real testnet hashes are\n"
        "  produced by the operator runbook step in `SUBMISSION_DRAFT.md`.\n"
        "* The Death Watch dashboard surface tails the same JSONL streams\n"
        "  the capture acts above wrote; the Money Shot screenshots in\n"
        "  `reports/sprint8/money_shot/` are the frame-level evidence the\n"
        "  USER folds into the Day 6 5-min demo video.\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """Drive the three-act capture; emit four artefacts; exit 0 on success."""
    parser = argparse.ArgumentParser(
        prog="capture_money_shot",
        description="T-B-021 sprint-closer Money Shot capture pipeline.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("reports/sprint8"),
        help="Root output directory (default: reports/sprint8/).",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("state/sandbox_money_shot"),
        help=(
            "Temporary state directory the loop writes the JSONL streams + "
            "snapshot to. SEPARATE from the live extended-Phase-2 sandbox "
            "(state/sandbox/) so the capture run never collides with a "
            "running operator process."
        ),
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    money_shot_dir = out_dir / "money_shot"
    state_dir = Path(args.state_dir)
    memory_bank_root = state_dir / "_mb"

    # Wipe state directories so each capture run is deterministic. This is
    # safe because the path is the capture-only sandbox path (NOT the
    # live state/sandbox/).
    if state_dir.exists():
        import shutil  # local import — only used on the rare clean path
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    money_shot_dir.mkdir(parents=True, exist_ok=True)

    # Shared deterministic surfaces across the three acts.
    chain = _ScriptedChainAdapter(current_breath=100.0)
    gamma = _ScriptedGammaAPI()
    gamma.register_market(
        market_id=MONEY_SHOT_MARKET_ID,
        end_date=datetime(2026, 5, 26, 21, 0, 0, tzinfo=UTC),
    )
    weight_updater = _SilentWeightUpdater()
    state_hook = _CollectingStateHook()
    clock = _AdvancingClock(start=MONEY_SHOT_START, step=timedelta(minutes=60))

    metrics = _CaptureMetrics(started_at=datetime.now(UTC))

    try:
        _capture_act_1_bet_placed(
            out_dir=money_shot_dir,
            state_dir=state_dir,
            chain=chain,
            gamma=gamma,
            weight_updater=weight_updater,
            state_hook=state_hook,
            clock=clock,
            memory_bank_root=memory_bank_root,
            metrics=metrics,
        )
        _capture_act_2_settlement_win(
            out_dir=money_shot_dir,
            state_dir=state_dir,
            chain=chain,
            gamma=gamma,
            weight_updater=weight_updater,
            state_hook=state_hook,
            clock=clock,
            memory_bank_root=memory_bank_root,
            metrics=metrics,
        )
        _capture_act_3_terminal_lucidity(
            out_dir=money_shot_dir,
            state_dir=state_dir,
            chain=chain,
            gamma=gamma,
            weight_updater=weight_updater,
            state_hook=state_hook,
            clock=clock,
            memory_bank_root=memory_bank_root,
            metrics=metrics,
        )
    except Exception as exc:
        # Best-effort summary even on failure — helps the USER triage.
        print(f"capture_money_shot: FAILED with {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    metrics.finished_at = datetime.now(UTC)
    _write_final_summary(
        out_dir=out_dir / "sprint8_final_summary.md",
        metrics=metrics,
    )

    print(
        "capture_money_shot: OK\n"
        f"  acts:     {money_shot_dir}/01_bet_placed.json,\n"
        f"            {money_shot_dir}/02_settlement_win.json,\n"
        f"            {money_shot_dir}/03_terminal_lucidity.json\n"
        f"  summary:  {out_dir}/sprint8_final_summary.md"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
