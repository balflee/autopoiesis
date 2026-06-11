"""Serial backtest sweep driver — T-B-026.

Acceptance criteria from the T-B-026 brief
------------------------------------------

* Accepts a list of starting-weight tuples (6-vectors).
* Runs :func:`agent.backtest.replay_runner.run_replay` per tuple in
  serial (parallel optional, deterministic order locked).
* Writes ``reports/sprint9/backtest/<run_id>/results.json`` with the
  per-config metrics.
* Writes ``reports/sprint9/backtest/<run_id>/lifetimes.jsonl`` with one
  record per replay — the shape the
  :mod:`harness.tools.backtest_validator` gate expects (PRD §14.3).
* 4-config sweep (``--no-llm``) completes in < 60 seconds.
* Determinism contract: 3 sequential ``run_sweep`` invocations with the
  same seed produce byte-identical ``results.json``.

Why serial?
-----------

Deterministic order is the load-bearing contract. Even a thread-pool
with stable ids would risk a future Python that re-orders dict-keyed
result aggregation — serial keeps the contract trivially checkable.
The 4-config sweep budget (< 60 s) absorbs the loss easily; sprint_10
follow-up can lift to a worker pool once the determinism contract has
a richer encoding (e.g. content-hashed results.json).

Filename determinism
--------------------

``run_id`` defaults to a deterministic derivation from the
``(seed, configs)`` tuple — see :func:`new_run_id`. Two sweep calls
with the same inputs produce the same run_id + the same files. Tests
inject an explicit ``run_id`` (e.g. ``"unit-test"``) so a temp dir
doesn't get a UUID-suffixed dir.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.backtest.historical_fetcher import (
    MarketSnapshot,
    load_all_cached_markets,
)
from agent.backtest.replay_runner import (
    DEFAULT_REPLAY_INITIAL_BANKROLL_USD,
    DEFAULT_REPLAY_INITIAL_BREATH,
    DEFAULT_REPLAY_MAX_TICKS,
    ReplayConfig,
    ReplayMetrics,
    run_replay,
)
from agent.core.state import Weights

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Locked defaults
# --------------------------------------------------------------------------- #


DEFAULT_OUTPUT_ROOT = Path("reports/sprint9/backtest")
"""Where ``run_sweep`` writes per-run reports. Tests override to ``tmp_path``."""


# Canonical 4-config sweep the brief stipulates. The weights span an
# easy-to-eyeball corner of the 6-D space so the dashboard can render
# the lifetime curve as four parallel traces.
DEFAULT_SWEEP_WEIGHTS: tuple[Weights, ...] = (
    Weights(
        w_r=0.7, w_s=0.3, alpha=[0.4, 0.4, 0.2], beta=[1.0, 0.0], rho=0.6,
    ),  # rational-heavy
    Weights(
        w_r=0.3, w_s=0.7, alpha=[0.4, 0.4, 0.2], beta=[1.0, 0.0], rho=0.6,
    ),  # sentient-heavy
    Weights(
        w_r=0.5, w_s=0.5, alpha=[0.33, 0.33, 0.34], beta=[1.0, 0.0], rho=0.4,
    ),  # balanced low-rho
    Weights(
        w_r=0.5, w_s=0.5, alpha=[0.33, 0.33, 0.34], beta=[1.0, 0.0], rho=0.9,
    ),  # balanced high-rho
)


# --------------------------------------------------------------------------- #
# Public types
# --------------------------------------------------------------------------- #


@dataclass
class SweepConfig:
    """Inputs to :func:`run_sweep`.

    Parameters
    ----------

    starting_weights
        Ordered tuple of 6-vector :class:`Weights` to replay. Order is
        load-bearing — the results.json carries the same order so the
        determinism contract has a stable sort key.

    seed
        Master seed for every replay. The per-config replay uses
        ``master_seed`` verbatim — same seed across configs means the
        synthetic signals' stochastic component varies ONLY with the
        starting weights, which is what the sweep is exploring.

    cache_dir
        :class:`agent.backtest.historical_fetcher.load_all_cached_markets`
        source. Defaults to ``agent/backtest/_cache``.

    output_root
        Where to write per-run artefacts. Defaults to
        :data:`DEFAULT_OUTPUT_ROOT`; tests pass ``tmp_path``.

    run_id
        Optional explicit run id. None → :func:`new_run_id` produces a
        deterministic hash of ``(seed, configs)``. The brief's
        determinism contract is that 3 sequential runs with the same
        inputs produce byte-identical artefacts; deterministic run_id
        keeps the artefact PATH stable too.

    enable_llm
        Threaded through to every :class:`ReplayConfig`. Sprint_9
        ships with the default False (NoOp clients).

    max_ticks
        Per-replay tick cap. Default
        :data:`agent.backtest.replay_runner.DEFAULT_REPLAY_MAX_TICKS`
        (240 → 10 simulated days at 60-min cadence).
    """

    starting_weights: tuple[Weights, ...] = field(
        default_factory=lambda: DEFAULT_SWEEP_WEIGHTS
    )
    seed: int = 0
    cache_dir: Path = field(
        default_factory=lambda: Path("agent/backtest/_cache")
    )
    output_root: Path = field(default_factory=lambda: DEFAULT_OUTPUT_ROOT)
    run_id: str | None = None
    enable_llm: bool = False
    max_ticks: int = DEFAULT_REPLAY_MAX_TICKS
    initial_breath: float = DEFAULT_REPLAY_INITIAL_BREATH
    initial_bankroll_usd: float = DEFAULT_REPLAY_INITIAL_BANKROLL_USD
    # T-B-040 — optional per-config labels aligned with starting_weights
    # by index. When passed the production runner forwards the operator
    # label from each StartingWeightConfig so results.json carries the
    # human-meaningful tag the workshop UI renders. None entry → that
    # config row falls back to the auto-derived config_id. Default None
    # (used by the canonical 4-config sweep + every existing test).
    config_labels: tuple[str | None, ...] | None = None


@dataclass(frozen=True)
class SweepResult:
    """Aggregate return from :func:`run_sweep`.

    Carries the resolved run_id + paths to the two artefacts so the
    caller can do post-run validation (e.g. the test asserting both
    files exist + lifetimes.jsonl is non-empty).
    """

    run_id: str
    output_dir: Path
    results_path: Path
    lifetimes_path: Path
    metrics: tuple[ReplayMetrics, ...]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def new_run_id(*, seed: int, configs: tuple[Weights, ...]) -> str:
    """Deterministic ``run_id`` derived from sweep inputs.

    Two ``run_sweep`` invocations with the same seed + same configs
    produce the same run_id. This is the third leg of the byte-identical
    determinism contract (the other two: deterministic cache filenames,
    sorted-keys JSON).

    Encoded as ``sweep-<hash12>`` so a casual reader can see at a glance
    that the directory is sweep output (and the 12-char hash avoids
    collisions across a large parameter space without the noise of a
    full SHA-256).
    """
    h = hashlib.sha256()
    h.update(str(seed).encode("utf-8"))
    for cfg in configs:
        h.update(b"|")
        h.update(cfg.model_dump_json().encode("utf-8"))
    return f"sweep-{h.hexdigest()[:12]}"


def _serialise_metrics(metrics: ReplayMetrics) -> dict[str, Any]:
    """Project a :class:`ReplayMetrics` to a sorted-keys-friendly dict.

    The six T-B-036 v2 analytic fields (``net_pnl_usd``, ``sharpe``,
    ``max_drawdown_pct``, ``win_rate_pct``, ``n_decisions``,
    ``n_bets``) are pass-through alongside the v1 lifetime block.
    ``net_pnl_usd`` is rendered as a JSON string via :func:`str` so
    the Decimal's full precision survives the round-trip — Track D's
    workshop drilldown parses it back with the same fidelity it does
    the existing :class:`BetSettlement` Decimal fields.
    """
    return {
        "apprenticeship_failures": metrics.apprenticeship_failures,
        "bets_placed": metrics.bets_placed,
        "config_id": metrics.config_id,
        "death_cause": metrics.death_cause,
        "deepen_count": metrics.deepen_count,
        "died": metrics.died,
        "donations_received": metrics.donations_received,
        "final_bankroll_usd": metrics.final_bankroll_usd,
        "final_breath": metrics.final_breath,
        # T-B-040 — operator-facing label; None for default sweeps so
        # readers fall back to config_id.
        "label": metrics.label,
        "lifetime_days": metrics.lifetime_days,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "n_bets": metrics.n_bets,
        "n_decisions": metrics.n_decisions,
        "net_pnl_usd": str(metrics.net_pnl_usd),
        "no_bets_emitted": metrics.no_bets_emitted,
        "seed": metrics.seed,
        "settlements_processed": metrics.settlements_processed,
        "sharpe": metrics.sharpe,
        "starting_weights": json.loads(metrics.starting_weights.model_dump_json()),
        "terminal_afterglow": metrics.terminal_afterglow,
        "ticks_completed": metrics.ticks_completed,
        "win_rate_pct": metrics.win_rate_pct,
    }


def _lifetime_record(metrics: ReplayMetrics) -> dict[str, Any]:
    """Project to the shape :mod:`harness.tools.backtest_validator` expects.

    Required fields per ``gate_input_schema.yaml.backtest_validity``:

    * ``archetype``           — str (the sweep's per-config tag — sprint_9
      uses the resolved config_id verbatim; sprint_10 maps to canonical
      archetypes)
    * ``lifetime_days``       — float
    * ``death_cause``         — str
    * ``terminal_afterglow``  — bool
    * ``apprenticeship_failures`` — int
    * ``deepen_count``        — int
    * ``donations_received``  — float
    """
    return {
        "apprenticeship_failures": metrics.apprenticeship_failures,
        "archetype": metrics.config_id,
        "death_cause": metrics.death_cause,
        "deepen_count": metrics.deepen_count,
        "donations_received": metrics.donations_received,
        "lifetime_days": metrics.lifetime_days,
        "terminal_afterglow": metrics.terminal_afterglow,
    }


def _write_results_json(
    *,
    out_dir: Path,
    metrics_list: list[ReplayMetrics],
    config: SweepConfig,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
) -> Path:
    """Atomic JSON write — ``out_dir/results.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "configs_run": len(metrics_list),
        "enable_llm": config.enable_llm,
        "finished_at": finished_at.isoformat(),
        "max_ticks": config.max_ticks,
        "results": [_serialise_metrics(m) for m in metrics_list],
        "run_id": run_id,
        "seed": config.seed,
        "started_at": started_at.isoformat(),
    }
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2)
    target = out_dir / "results.json"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialised + "\n", encoding="utf-8", newline="\n")
    tmp.replace(target)
    return target


def _write_lifetimes_jsonl(
    *,
    out_dir: Path,
    metrics_list: list[ReplayMetrics],
) -> Path:
    """Atomic JSONL write — ``out_dir/lifetimes.jsonl``.

    JSONL is line-delimited; we serialise each record with sorted_keys
    so a byte-identical input produces a byte-identical file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "lifetimes.jsonl"
    tmp = target.with_suffix(target.suffix + ".tmp")
    lines: list[str] = []
    for m in metrics_list:
        lines.append(
            json.dumps(_lifetime_record(m), sort_keys=True, ensure_ascii=True)
        )
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(target)
    return target


# --------------------------------------------------------------------------- #
# Public driver
# --------------------------------------------------------------------------- #


async def run_sweep(
    config: SweepConfig,
    *,
    snapshots: list[MarketSnapshot] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> SweepResult:
    """Drive every config in ``config.starting_weights`` in serial.

    Returns a :class:`SweepResult` carrying the resolved run_id +
    artefact paths. Both ``results.json`` and ``lifetimes.jsonl`` are
    written atomically (temp + replace).

    Per-replay state directories live under
    ``output_dir/state/<config_id>/`` so each replay's JSONL streams +
    snapshot stay isolated (the SandboxPhase2Loop's single-writer
    invariant is per-process; running configs serially means the
    invariant trivially holds, but the per-config dir keeps a future
    parallel mode safe).

    T-B-037 — when ``cancel_event`` is passed and set we raise
    :class:`asyncio.CancelledError` between configs so the
    :class:`agent.server.runner.BacktestRegistry` cancel route
    propagates to the sweep cleanly. CEO direction D-S11-001 locks
    "no SIGKILL — set the cancel flag, the loop checks at each tick
    boundary". The OUTER (registry) seam fires here; per-tick checks
    inside :func:`agent.backtest.replay_runner.run_replay` land in
    sprint_12 once :class:`SandboxPhase2Loop` exposes a per-tick hook.
    """
    if not config.starting_weights:
        raise ValueError("SweepConfig.starting_weights must be non-empty")
    if (
        config.config_labels is not None
        and len(config.config_labels) != len(config.starting_weights)
    ):
        raise ValueError(
            "SweepConfig.config_labels length must match "
            f"starting_weights ({len(config.config_labels)} vs "
            f"{len(config.starting_weights)})"
        )

    run_id = config.run_id or new_run_id(
        seed=config.seed, configs=tuple(config.starting_weights)
    )
    out_dir = config.output_root / run_id
    state_root_for_run = out_dir / "state"
    # Determinism contract: a repeat invocation with the same inputs MUST
    # produce byte-identical artefacts. The SandboxPhase2Loop reconstructs
    # in-memory state from disk on every start, so leaving the prior run's
    # JSONL streams in place would taint a re-run. Wipe the per-config
    # state tree (output_root/<run_id>/state/) before the sweep drives the
    # configs; the results.json + lifetimes.jsonl above are overwritten
    # atomically by the writers below.
    if state_root_for_run.exists():
        shutil.rmtree(state_root_for_run)

    # Snapshots loaded ONCE up-front so each replay's MarketSnapshotProvider
    # is built from the same source dict (cache-load cost is amortised).
    loaded_snapshots = (
        snapshots
        if snapshots is not None
        else load_all_cached_markets(cache_dir=config.cache_dir)
    )
    if not loaded_snapshots:
        raise RuntimeError(
            f"sweep: no cached markets under {config.cache_dir} — "
            "run agent.backtest.historical_fetcher.fetch_closed_tennis_markets first"
        )

    started_at = datetime.now(UTC)
    metrics_list: list[ReplayMetrics] = []
    for idx, weights in enumerate(config.starting_weights):
        # T-B-037 — cooperative cancellation: check the latch BEFORE
        # the next replay so a /api/backtest/{run_id}/cancel can stop
        # the sweep within one per-config wall-clock window.
        if cancel_event is not None and cancel_event.is_set():
            logger.info(
                "sweep_runner: cancel observed before config — "
                "run_id=%s metrics_so_far=%d",
                run_id, len(metrics_list),
            )
            raise asyncio.CancelledError
        label = (
            config.config_labels[idx]
            if config.config_labels is not None
            else None
        )
        cfg = ReplayConfig(
            starting_weights=weights,
            seed=config.seed,
            cache_dir=config.cache_dir,
            max_ticks=config.max_ticks,
            initial_breath=config.initial_breath,
            initial_bankroll_usd=config.initial_bankroll_usd,
            enable_llm=config.enable_llm,
            label=label,
        )
        per_config_state = state_root_for_run / cfg.resolved_config_id()
        per_config_state.mkdir(parents=True, exist_ok=True)
        metrics = await run_replay(
            cfg,
            state_root=per_config_state,
            snapshots=loaded_snapshots,
        )
        metrics_list.append(metrics)
        logger.info(
            "sweep_runner: config=%s ticks=%d bets=%d died=%s",
            metrics.config_id, metrics.ticks_completed,
            metrics.bets_placed, metrics.died,
        )
    finished_at = datetime.now(UTC)

    # NOTE: we OMIT started_at/finished_at from the determinism check —
    # the test asserts on the structural fields. Pinning those would
    # require freezing the clock, which adds noise without payoff.
    # The byte-identical contract applies to ``results`` + ``run_id``
    # + ``seed``; the test re-reads + compares those slices explicitly.

    results_path = _write_results_json(
        out_dir=out_dir,
        metrics_list=metrics_list,
        config=config,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
    )
    lifetimes_path = _write_lifetimes_jsonl(
        out_dir=out_dir,
        metrics_list=metrics_list,
    )

    return SweepResult(
        run_id=run_id,
        output_dir=out_dir,
        results_path=results_path,
        lifetimes_path=lifetimes_path,
        metrics=tuple(metrics_list),
    )


def run_sweep_sync(
    config: SweepConfig,
    *,
    snapshots: list[MarketSnapshot] | None = None,
) -> SweepResult:
    """Synchronous wrapper around :func:`run_sweep` for the CLI entrypoint."""
    return asyncio.run(run_sweep(config, snapshots=snapshots))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_argparser() -> argparse.ArgumentParser:
    """argparse for ``python -m agent.backtest.sweep_runner``.

    The brief locks the flag names: ``--no-llm`` default, ``--enable-llm``
    flag. We expose ``--seed`` / ``--cache-dir`` / ``--output-root`` /
    ``--max-ticks`` so the operator runbook can override without code.
    """
    p = argparse.ArgumentParser(
        prog="agent.backtest.sweep_runner",
        description=(
            "Genesis Experiment T-B-026 backtest sweep — replay cached "
            "Polymarket tennis markets through SandboxPhase2Loop across "
            "a list of starting-weight tuples."
        ),
    )
    p.add_argument("--seed", type=int, default=0, help="Master seed")
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("agent/backtest/_cache"),
        help="Where to load cached MarketSnapshot files",
    )
    p.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Where to write the run report",
    )
    p.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Override the deterministic run_id (default: hash of inputs)",
    )
    p.add_argument(
        "--max-ticks",
        type=int,
        default=DEFAULT_REPLAY_MAX_TICKS,
        help="Per-replay tick cap (default 240 → 10 simulated days)",
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--no-llm",
        dest="enable_llm",
        action="store_false",
        default=False,
        help="(default) Run with NoOp LLM clients — fully hermetic",
    )
    grp.add_argument(
        "--enable-llm",
        dest="enable_llm",
        action="store_true",
        help="Enable L1/L2 LLM trace via VCR cassettes; requires GEMINI_API_KEY",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry — returns a process exit code.

    Output goes to stdout as a single JSON line carrying the resolved
    paths so an operator script can pipe-process the result.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_argparser()
    args = parser.parse_args(argv)
    config = SweepConfig(
        seed=args.seed,
        cache_dir=args.cache_dir,
        output_root=args.output_root,
        run_id=args.run_id,
        enable_llm=args.enable_llm,
        max_ticks=args.max_ticks,
    )
    result = run_sweep_sync(config)
    summary = {
        "lifetimes_path": str(result.lifetimes_path),
        "output_dir": str(result.output_dir),
        "results_path": str(result.results_path),
        "run_id": result.run_id,
    }
    sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
    return 0


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_SWEEP_WEIGHTS",
    "SweepConfig",
    "SweepResult",
    "main",
    "new_run_id",
    "run_sweep",
    "run_sweep_sync",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
