"""FastAPI control plane — seven routes the dashboard / platform talk to.

Routes
------

* ``POST /api/agent/start``    — spawn the sandbox loop (202 + run_id;
  409 if already running)
* ``POST /api/agent/stop``     — graceful halt (200 + final state path;
  idempotent)
* ``GET  /api/agent/status``   — read-only state read from disk
* ``POST /api/backtest/run``   — kick off a sweep (202 + run_id)
* ``GET  /api/backtest/{id}``  — fetch ``results.json`` (404 if unknown
  AND no file on disk)
* ``GET  /api/state/stream``   — SSE tailing
  decisions.jsonl + reflections.jsonl + proposals.jsonl
* ``GET  /healthz``            — liveness probe (T-B-028). Unauthed so
  Railway's healthcheck + Docker ``HEALTHCHECK`` can hit it without a
  bearer token. Returns ``{status, uptime_s, last_tick_ts}``.

Every API route (``/api/...``) is bearer-token authed via
:mod:`agent.server.auth`. ``/healthz`` is intentionally unauthed — see
the route docstring + T-B-028 brief. CORS is configured to ALLOW
``https://*.vercel.app`` + ``http://localhost:*`` and reject anything
else — no wildcard ``*``.

The app is constructed via :func:`create_app` (factory pattern) so tests
can inject a fake :class:`agent.server.runner.AgentRunner` /
:class:`agent.server.runner.BacktestRegistry` per test instance without
sharing state via a module-level global. PRD anchors: §8 +
TECHNICAL_PLAN §5.1, §5.4.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

if TYPE_CHECKING:
    from agent.backtest.historical_fetcher import MarketSnapshotProvider
    from agent.backtest.replay_runner import _SignalSource
    from agent.engines.reflection import ReflectionEngine

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.core.memory_bank import MemoryBank
from agent.core.state import ActionKind, Phase, Weights
from agent.data._realtime_buffer import Clock, UtcClock
from agent.data.polymarket_sandbox_executor import (
    MarketInfo,
    MarketResolver,
    SandboxExecutor,
)
from agent.data.polymarket_settlement import SettlementResult
from agent.data.sandbox_state import (
    DECISIONS_FILENAME,
    OPEN_BETS_FILENAME,
    PROPOSALS_FILENAME,
    REFLECTIONS_FILENAME,
    SETTLED_BETS_FILENAME,
    SNAPSHOT_FILENAME,
    SandboxStateWriter,
)
from agent.engines._strategy_proposal_schema import (
    PROPOSAL_STATUS_APPROVED,
    PROPOSAL_STATUS_PENDING,
    PROPOSAL_STATUS_REJECTED,
    StrategyProposal,
)
from agent.engines.strategy_advisor import NoOpStrategyAdvisor, StrategyAdvisor
from agent.runtime.agent_runner import AgentRunner as RuntimeAgentRunner
from agent.runtime.phase2_launch import Phase2LaunchOrchestrator
from agent.runtime.sandbox_phase2_loop import (
    DeathReceipt,
    SandboxLoopChainAdapter,
    SandboxPhase2Loop,
    TickInputs,
    TickInputSource,
    WeightUpdaterPhase,
)
from agent.runtime.sandbox_settlement_poller import SettlementClient, _real_sleep
from agent.server.auth import AuthDep
from agent.server.bootstrap import (
    BACKTEST_CACHE_DIR_ENV_VAR,
    BACKTEST_OUTPUT_ROOT_ENV_VAR,
    PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR,
    PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN,
    PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX,
    PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR,
    PROD_LOOP_TIME_COMPRESSION_ENV_VAR,
    SANDBOX_STATE_DIR_ENV_VAR,
    ResolvedProdLoopConfig,
    prime_volume_cache,
    resolve_prod_loop_config,
    resolve_state_paths,
    validate_state_paths,
)
from agent.server.models import (
    AgentConfigureRequest,
    AgentConfigureResponse,
    BacktestCancelResponse,
    BacktestRunRequest,
    StartingWeightConfig,
)
from agent.server.runner import (
    AGENT_ERROR_FILENAME,
    AgentAlreadyRunningError,
    AgentRunner,
    BacktestRegistry,
    RegistryError,
    RegistryErrorEnvelope,
)

AGENT_CONFIG_FILENAME: Final[str] = "agent_config.json"
"""T-B-037 — operator-staged starting-weight snapshot.

Written by ``POST /api/agent/configure`` to ``<state_dir>/agent_config.json``
via an atomic temp+rename. The next ``/api/agent/start`` (sprint_12 wiring)
will rehydrate the loop's ``initial_weights`` from this file when present
and fall back to the engine-side default Weights when absent.
"""

AGENT_CONFIG_CONSUMING_FILENAME: Final[str] = "agent_config.consuming.json"
"""In-progress claim path for the transactional consume (PROMOTE pipeline).

:func:`_consume_staged_config` atomically MOVES ``agent_config.json`` to
this path FIRST, then reads + resets + renames-to-applied. Binding the
consumed file to a single claimed version closes the read->rename TOCTOU
(a concurrent ``POST /api/agent/configure`` cannot swap the file
mid-consume). On restart an orphaned consuming file (crash mid-consume)
is recovered so the promote is never lost.
"""

AGENT_CONFIG_APPLIED_FILENAME: Final[str] = "agent_config.applied.json"
"""Renamed-to marker after ``/api/agent/start`` consumes a staged config.

PROMOTE semantics: a fresh ``agent_config.json`` (written by
``/api/agent/configure``) means "start a NEW agent life with these
backtest-winning weights". Once a start consumes it, we rename it to this
applied marker so a plain restart (no fresh promote) reconstructs the
running life normally instead of resetting it again.
"""


PROPOSAL_TODOS_FILENAME: Final[str] = "proposal_todos.jsonl"
"""T-B-031 — operator-approval queue for non-weight_delta proposals.

The PRD §11 sprint_10 enclosure locks CEO decision 6: only weight_delta
proposals auto-apply to the running agent on approve; new_signal_idea +
prompt_tweak get written to this TODO file for manual sprint_11
processing.

The approve route appends ONE line per non-``weight_delta`` approval here;
sprint_11 follow-up tooling will drain the file into per-kind backlogs.
The file is created on first append (no cold-start touch) so the disk
layout stays clean on installs that never see a new_signal_idea /
prompt_tweak approval."""


STREAM_NAMES: tuple[str, str, str] = ("decisions", "reflections", "proposals")
"""Canonical SSE event names = the JSONL file stems.

Single source of truth for the three runtime streams the dashboard tails
via :func:`stream_state`. The corresponding filenames are imported from
:mod:`agent.data.sandbox_state` so a rename there doesn't silently desync.
"""


_STREAM_FILENAMES: dict[str, str] = {
    "decisions": DECISIONS_FILENAME,
    "reflections": REFLECTIONS_FILENAME,
    "proposals": PROPOSALS_FILENAME,
}


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# CORS configuration — locked per TECHNICAL_PLAN §5.1
# --------------------------------------------------------------------------- #


CORS_ORIGIN_REGEX: str = r"^(https://[a-z0-9-]+\.vercel\.app|http://localhost(:\d+)?)$"
"""Single regex covering both allowed origin families.

* ``https://<sub>.vercel.app`` — production dashboard preview URLs.
* ``http://localhost`` / ``http://localhost:<port>`` — local dev.

Two-part regex lives in ONE string because FastAPI's
:class:`fastapi.middleware.cors.CORSMiddleware` accepts a single
``allow_origin_regex`` parameter — passing two would silently drop one.
"""


CORS_ALLOWED_METHODS: tuple[str, ...] = ("GET", "POST", "OPTIONS")
"""Brief defines six routes total — only GET + POST are needed plus the
OPTIONS preflight. Hand-listing instead of ``["*"]`` so a future route
that adds DELETE / PATCH must consciously opt in to CORS coverage."""


CORS_ALLOWED_HEADERS: tuple[str, ...] = ("Authorization", "Content-Type", "Accept")
"""Hand-listed for the same reason as the method list."""


# --------------------------------------------------------------------------- #
# State container — wired into the FastAPI app via dependency overrides
# --------------------------------------------------------------------------- #


@dataclass
class ServerState:
    """Runtime dependencies the routes resolve via ``request.app.state``.

    Held as a single dataclass on ``app.state.deps`` rather than as
    separate attributes so test fixtures can swap in a fresh instance
    in one assignment. The route handlers reach into this via
    :func:`_get_state` which is a 1-line cast for mypy --strict.

    ``runtime_agent`` (T-B-031) is the thread-safe weight-delta queue
    the ``/api/proposals/{id}/approve`` route writes to when an
    approved proposal carries ``kind == "weight_delta"``. The sandbox
    loop drains the queue per tick — see :class:`RuntimeAgentRunner`
    docstring for the threading model.
    """

    agent_runner: AgentRunner
    backtest_registry: BacktestRegistry
    runtime_agent: RuntimeAgentRunner


def _get_state(request: Request) -> ServerState:
    """Resolve the per-app :class:`ServerState` from ``request.app.state``.

    Centralised so the routes share ONE typed lookup. ``app.state`` is
    a ``starlette.datastructures.State`` (a property-bag) so mypy needs
    the cast.
    """
    state_obj = request.app.state.deps
    if not isinstance(state_obj, ServerState):  # defensive
        raise RuntimeError("server state not initialised — call create_app() first")
    return state_obj


StateDep = Annotated[ServerState, Depends(_get_state)]


# --------------------------------------------------------------------------- #
# Pydantic models — request + response envelopes
# --------------------------------------------------------------------------- #


class StartResponse(BaseModel):
    """``POST /api/agent/start`` 202 response."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: Literal["accepted"] = "accepted"


class StartConflictResponse(BaseModel):
    """``POST /api/agent/start`` 409 response — includes existing run_id
    so the dashboard can resume without spawning a duplicate."""

    model_config = ConfigDict(extra="forbid")
    detail: Literal["agent already running"] = "agent already running"
    run_id: str


class StopResponse(BaseModel):
    """``POST /api/agent/stop`` 200 response. ``final_state_path`` is
    ``None`` only on a brand-new install that has never persisted a
    snapshot."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["stopped"] = "stopped"
    final_state_path: str | None


class StatusResponse(BaseModel):
    """``GET /api/agent/status`` 200 response.

    Field shape matches the brief exactly. ``current_weights`` may be
    ``None`` on a cold-start install where no snapshot has been written
    yet; the dashboard renders a "—" placeholder in that case.

    T-B-034 added two optional fields. They are ``None`` for healthy
    runs and let the dashboard distinguish "idle" from "crashed":

    * ``last_run_status`` — terminal disposition of the most recent
      loop run as captured by :func:`_safe_run`. ``"failed"`` /
      ``"cancelled"`` only when ``state_dir/agent_error.json`` exists.
    * ``error`` — the :class:`RegistryError` payload from that file
      when ``last_run_status == "failed"``. ``None`` for healthy and
      for cancelled runs.
    """

    model_config = ConfigDict(extra="forbid")
    phase: str | None
    breath: float | None
    last_tick_ts: str | None
    current_weights: dict[str, Any] | None
    llm_cost_usd_this_month: float
    pending_proposals_count: int
    running: bool
    run_id: str | None
    last_run_status: Literal["failed", "cancelled"] | None = None
    error: RegistryError | None = None


class BacktestRunResponse(BaseModel):
    """``POST /api/backtest/run`` 202 response."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: Literal["accepted"] = "accepted"


class HealthzResponse(BaseModel):
    """``GET /healthz`` 200 response — Railway + Docker liveness probe.

    Fields locked by the T-B-028 brief:

    * ``status``        — always the literal ``"ok"`` on a 200. A failure
      mode that can still parse + serialise this model would have
      already raised before reaching the response phase, so the probe's
      contract is "HTTP 200 + this body shape == alive".
    * ``uptime_s``      — integer seconds since :func:`create_app` ran.
      Computed from a monotonic clock baseline captured in the closure
      so wall-clock changes (NTP step, container clock drift) don't
      poison the value. Truncated to whole seconds so Railway's poller
      can graph it cleanly.
    * ``last_tick_ts``  — ISO-8601 UTC string from the durable snapshot
      (``agent_state.json::snapshot_ts``) OR ``None`` on a cold-start
      install where the sandbox loop hasn't persisted a snapshot yet.

    This route is deliberately UNAUTHED — Railway's healthcheck runs
    inside the deploy's VPC without an Authorization header, and
    leaking ``uptime_s`` + ``last_tick_ts`` is not a credential.
    """

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    uptime_s: int = Field(ge=0)
    last_tick_ts: str | None


# T-B-037 — :class:`BacktestRunRequest` moved to :mod:`agent.server.models`
# so the typed wire surface is shared with the dashboard contract tests
# (and the next sprint's reconciler). The legacy single-field shape
# (only ``note``) was replaced by a richer body carrying the optional
# ``configs`` list — see the model docstring for the backward-compat
# semantics around an empty list.



# --------------------------------------------------------------------------- #
# T-B-031 — Proposal approve / reject wire shapes.
# --------------------------------------------------------------------------- #


class ProposalRejectRequest(BaseModel):
    """``POST /api/proposals/{id}/reject`` request body.

    The ``reason`` field is optional — the dashboard's reject button can
    submit an empty body for snap rejections, OR a free-form string when
    the operator wants the audit trail to carry their reasoning.

    ``extra='forbid'`` so a typo'd key (e.g. ``"resaon"``) surfaces as
    HTTP 422 at parse time rather than silently dropping on the floor.
    """

    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(
        default=None,
        description=(
            "Free-form operator note for the rejection. Persisted to "
            "``proposals.jsonl`` on the status='rejected' audit row; the "
            "dashboard renders the latest reason on hover."
        ),
    )


class ProposalActionResponse(BaseModel):
    """``POST /api/proposals/{id}/(approve|reject)`` 200 response.

    Returned for BOTH actions — the ``status`` field disambiguates which
    transition the handler made. Empirically the dashboard only needs
    ``proposal_id`` + ``status``; ``applied_to_runtime`` is included so
    the operator UI can render a distinct "delta queued" affordance for
    weight_delta approvals vs the TODO-write fallback for the other two
    kinds.
    """

    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    status: Literal["approved", "rejected"]
    applied_to_runtime: bool = Field(
        description=(
            "True iff the approval triggered a weight-delta enqueue on "
            "the runtime seam. False for rejections AND for approvals "
            "of ``new_signal_idea`` / ``prompt_tweak`` proposals "
            "(which get written to ``proposal_todos.jsonl`` for "
            "manual processing — PRD §11 sprint_10 CEO decision 6)."
        ),
    )


# --------------------------------------------------------------------------- #
# Helpers — disk reads for /status + JSONL tail for /stream
# --------------------------------------------------------------------------- #


def _read_agent_error_envelope(
    *, state_dir: Path
) -> RegistryErrorEnvelope | None:
    """Read ``agent_error.json`` (T-B-034) or return ``None``.

    The envelope is written by :func:`agent.server.runner._safe_run`
    when the agent loop coroutine raises an :class:`Exception` or sees
    :class:`asyncio.CancelledError`. ``None`` is returned for:

    * a healthy run that has never crashed (file missing);
    * a torn / malformed envelope (file unreadable or fails Pydantic
      validation) — we degrade gracefully because /status MUST NOT 5xx
      on a poll just because the error file is half-written.

    The envelope's success-path counterpart is the loop's own snapshot
    write to ``agent_state.json`` — the two files coexist independently;
    a crash-then-recover cycle leaves both on disk, with the snapshot
    showing the last healthy tick and the error envelope showing the
    crash that followed.
    """
    error_path = state_dir / AGENT_ERROR_FILENAME
    try:
        raw = error_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("status: failed to read agent_error at %s", error_path)
        return None
    try:
        return RegistryErrorEnvelope.model_validate_json(raw)
    except Exception:
        # Schema-drift or torn-write on the envelope file: log and
        # collapse to None so /status keeps serving the snapshot view.
        logger.warning(
            "status: failed to validate agent_error envelope at %s", error_path
        )
        return None


def _read_status_from_disk(
    *,
    runner: AgentRunner,
    llm_cost_usd_this_month: float,
) -> StatusResponse:
    """Build a :class:`StatusResponse` by reading the durable snapshot.

    The route is read-only against ``agent_state.json`` — we DON'T poke
    the in-memory loop instance because:

    1. The loop's writer holds an in-process lock during writes; reading
       the file in append-atomic POSIX semantics is safe and lock-free.
    2. The dashboard polls /status frequently (every few seconds) and
       must NOT slow the loop down with attribute reads against a
       lock-held instance.

    A missing OR un-parseable snapshot collapses to placeholder fields.
    The route never raises.

    T-B-034 — also reads ``state_dir/agent_error.json``. When that file
    exists and parses, ``last_run_status`` + ``error`` are populated so
    the dashboard can render the crash shape on a polled /status.
    """
    snapshot_path = runner.state_dir / SNAPSHOT_FILENAME
    data: dict[str, Any] | None = None
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Cold-start install — the loop has never persisted a snapshot.
        pass
    except (OSError, json.JSONDecodeError):
        # Half-written snapshot (the writer uses os.replace so this is
        # rare but not impossible on exotic FS) must not crash the
        # dashboard's /status poll.
        logger.warning("status: failed to read snapshot at %s", snapshot_path)

    error_envelope = _read_agent_error_envelope(state_dir=runner.state_dir)
    last_run_status: Literal["failed", "cancelled"] | None = (
        error_envelope.status if error_envelope is not None else None
    )
    error_payload: RegistryError | None = (
        error_envelope.error if error_envelope is not None else None
    )

    if not isinstance(data, dict):
        return StatusResponse(
            phase=None,
            breath=None,
            last_tick_ts=None,
            current_weights=None,
            llm_cost_usd_this_month=llm_cost_usd_this_month,
            pending_proposals_count=0,
            running=runner.is_running,
            run_id=runner.current_run_id,
            last_run_status=last_run_status,
            error=error_payload,
        )

    weights_raw = data.get("weights")
    pending = data.get("pending_proposals", [])
    return StatusResponse(
        phase=data.get("phase"),
        breath=data.get("breath"),
        last_tick_ts=data.get("snapshot_ts"),
        current_weights=weights_raw if isinstance(weights_raw, dict) else None,
        llm_cost_usd_this_month=llm_cost_usd_this_month,
        pending_proposals_count=len(pending) if isinstance(pending, list) else 0,
        running=runner.is_running,
        run_id=runner.current_run_id,
        last_run_status=last_run_status,
        error=error_payload,
    )


def _load_proposal_latest(*, state_dir: Path, proposal_id: str) -> StrategyProposal | None:
    """Latest-status-wins fold of ``proposals.jsonl`` for one ``proposal_id``.

    Mirrors :func:`agent.runtime.sandbox_phase2_loop._fold_pending_proposals_from_jsonl`
    semantics — walks the JSONL left-to-right, keeps the LAST row per
    ``proposal_id``, parses through :class:`StrategyProposal` for type
    safety. Returns ``None`` if the file is missing, empty, has no row
    matching ``proposal_id``, OR every matching row fails Pydantic
    validation (the schema uses ``extra='ignore'`` so non-fatal field
    drift survives; only structural breakage trips this path).

    Why this lives here instead of being shared with the loop fold
    helper:

    * The loop helper returns ``list[str]`` (proposal_ids only) — the
      route needs the full :class:`StrategyProposal` to dispatch on
      ``kind``.
    * Centralising the parse here keeps the route handler small (and
      auditable for the bearer-token + validate-status + dispatch
      ordering invariants the brief calls out).
    """
    path = state_dir / PROPOSALS_FILENAME
    latest: StrategyProposal | None = None
    try:
        fh = path.open(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("proposals: failed to open %s", path)
        return None
    with fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("proposal_id") != proposal_id:
                continue
            try:
                latest = StrategyProposal.model_validate(obj)
            except Exception:
                # Schema-drift on a single row must not break the
                # whole fold — preserve any earlier valid match.
                logger.warning(
                    "proposals: skipping un-parseable row for %s in %s",
                    proposal_id,
                    path,
                )
                continue
    return latest


def _load_pending_proposal_or_raise(
    *, state_dir: Path, proposal_id: str
) -> StrategyProposal:
    """Shared preflight for the approve + reject handlers (T-B-031).

    Loads via :func:`_load_proposal_latest`, then raises:

    * 404 when no row matches ``proposal_id``.
    * 409 when the LAST row's status is not ``"pending"`` — an
      ``approved``/``rejected`` row already won the latest-status-wins
      fold and the audit-trail correctness rule locks the transition
      as one-way (re-applying a delta on a second approve would
      silently re-rewrite the weights).

    Returning the validated proposal lets each route dispatch on
    ``kind`` + ``proposed_change`` without an extra fold.
    """
    proposal = _load_proposal_latest(
        state_dir=state_dir, proposal_id=proposal_id
    )
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown proposal_id",
        )
    if proposal.status != PROPOSAL_STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"proposal already {proposal.status}",
        )
    return proposal


def _write_agent_config_atomic(
    *, state_dir: Path, config: StartingWeightConfig
) -> Path:
    """Atomic JSON snapshot of the staged starting-weight config (T-B-037).

    Writes ``<state_dir>/agent_config.json`` via the same temp+os.replace
    pattern :meth:`agent.core.state.AgentState.save_json` and
    :func:`agent.server.runner._write_error_envelope` use. The contract
    is "either the previous valid snapshot or the new valid snapshot is
    on disk" — never a torn write — so the next
    ``/api/agent/start`` boot path can read the file unconditionally.

    Returns the resolved on-disk path so the route can echo it back in
    the 202 response body.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / AGENT_CONFIG_FILENAME
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = config.model_dump_json(indent=2)
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------- #
# PROMOTE pipeline — backtest-winning config → fresh live agent life.
# --------------------------------------------------------------------------- #

_DURABLE_LIFE_FILENAMES: Final[tuple[str, ...]] = (
    SNAPSHOT_FILENAME,
    OPEN_BETS_FILENAME,
    SETTLED_BETS_FILENAME,
    DECISIONS_FILENAME,
    REFLECTIONS_FILENAME,
    PROPOSALS_FILENAME,
)
"""The runtime files one agent life writes under ``state_dir``.

Archived by :func:`_reset_durable_life` when a PROMOTE starts a fresh
life so :meth:`SandboxPhase2Loop._reconstruct_from_disk` sees no snapshot
and cold-starts with the promoted weights.
"""

_MEMORY_BANK_DIRNAME: Final[str] = "_mb"


def _reset_durable_life(state_dir: Path) -> None:
    """Archive the current agent life (snapshot + JSONL streams + memory
    bank) into a unique ``state_dir/_prev_life/<UTC-ts>/`` backup so the
    next reconstruction cold-starts with the promoted weights.

    PROMOTE = run a NEW agent life with backtest-winning weights. The loop
    cold-starts ONLY when ``agent_state.json`` is absent
    (:meth:`SandboxPhase2Loop._reconstruct_from_disk`), so a fresh promote
    must clear the prior snapshot + streams. We MOVE (not delete) into a
    per-reset timestamped subdir — repeated promotes keep every prior
    life's archive instead of overwriting a single ``_prev_life``. The
    ``_mb`` MemoryBank dir is per-life provenance and is archived too so a
    fresh life starts with empty memory. ``agent_config.json`` is NOT
    life-state and is left untouched.
    """
    backup: Path | None = None

    def _ensure_backup() -> Path:
        nonlocal backup
        if backup is None:
            base = state_dir / "_prev_life"
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
            cand = base / stamp
            suffix = 0
            # Exclusive create so a same-stamp collision NEVER overwrites a
            # prior archive. exist_ok=False + suffix retry avoids both the
            # data-loss (exist_ok=True overwrite) and the crash (bare
            # exist_ok=False) failure modes.
            while True:
                try:
                    cand.mkdir(parents=True, exist_ok=False)
                    break
                except FileExistsError:
                    suffix += 1
                    cand = base / f"{stamp}_{suffix}"
            backup = cand
        return backup

    for name in _DURABLE_LIFE_FILENAMES:
        src = state_dir / name
        if src.exists():
            os.replace(src, _ensure_backup() / name)
    mb = state_dir / _MEMORY_BANK_DIRNAME
    if mb.exists():
        os.replace(mb, _ensure_backup() / _MEMORY_BANK_DIRNAME)
    if backup is not None:
        logger.info(
            "prod loop: reset durable life — archived prior state to %s "
            "(PROMOTE fresh-life cold-start)",
            backup,
        )


def _consume_staged_config(state_dir: Path) -> Weights | None:
    """Adopt a promoted ``agent_config.json`` for a fresh agent life.

    Completes the workshop->PROMOTE->live dogfood pipeline. The dashboard
    PROMOTE button -> ``POST /api/agent/configure`` ->
    :func:`_write_agent_config_atomic` stages a
    :class:`agent.server.models.StartingWeightConfig` at
    ``<state_dir>/agent_config.json``. Each ``/api/agent/start`` calls THIS.

    The consume is a FILE-STATE TRANSITION, not read-then-rename, to close
    the TOCTOU where a concurrent ``/api/agent/configure`` swaps the file
    between the read and the rename:

      0. Recover an orphaned ``<consuming>`` file from a prior crash, else
         atomically CLAIM ``agent_config.json -> <consuming>`` (binds the
         exact version we consume; a later configure writes a NEW
         ``agent_config.json`` that the NEXT start picks up — never lost).
      1. read + project the claimed config to :class:`Weights`,
      2. :func:`_reset_durable_life` so reconstruction cold-starts with the
         promoted weights (a live snapshot would override them),
      3. rename ``<consuming> -> AGENT_CONFIG_APPLIED_FILENAME``.

    Crash-safety: a crash after step 0 (or after step 2, before step 3)
    leaves ``<consuming>`` on disk; the next start recovers it, re-resets
    (no-op — life already archived), and renames to applied. So the
    promote is never lost and the cold-start always lands.

    Returns the promoted :class:`Weights`, or ``None`` when nothing is
    staged (-> caller passes ``initial_weights=None`` -> the loop's
    ``_phase2_default_weights`` default; unchanged behaviour).
    """
    cfg_path = state_dir / AGENT_CONFIG_FILENAME
    consuming = state_dir / AGENT_CONFIG_CONSUMING_FILENAME
    if not consuming.exists():
        if not cfg_path.exists():
            return None
        os.replace(cfg_path, consuming)  # atomic claim — binds the version
    config = StartingWeightConfig.model_validate_json(
        consuming.read_text(encoding="utf-8")
    )
    weights = config.to_weights()
    _reset_durable_life(state_dir)
    os.replace(consuming, state_dir / AGENT_CONFIG_APPLIED_FILENAME)
    logger.info(
        "prod loop: adopted promoted config label=%r (w_r=%.3f rho=%.3f) "
        "— fresh life cold-start",
        config.label, weights.w_r, weights.rho,
    )
    return weights


def _append_jsonl_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSON line to ``path``. POSIX-atomic for ≤ PIPE_BUF.

    Used by the approve/reject routes to write the status row to
    ``proposals.jsonl`` and the TODO row to ``proposal_todos.jsonl``.
    The single-writer invariant locked by the CEO 2026-05-26 plan
    (see :mod:`agent.data.sandbox_state`) extends to these routes:
    the FastAPI handler is the ONLY writer of approve/reject audit
    rows; the loop appends ``status='pending'`` rows via the
    :class:`SandboxStateWriter` and never touches the status field
    after the initial emit.

    ``path.parent.mkdir(parents=True, exist_ok=True)`` is defensive:
    the sandbox state dir is created at app construction time, but
    on a tmp_path test that pivots dirs we want the route to keep
    working without re-running setup.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def _read_last_tick_ts(*, state_dir: Path) -> str | None:
    """Read ``snapshot_ts`` from the durable snapshot, or ``None``.

    Mirrors the failure modes of :func:`_read_status_from_disk` so the
    /healthz route can't crash on a torn or missing snapshot. Returns:

    * the ``snapshot_ts`` string when the snapshot exists, parses as
      JSON, and carries that field;
    * ``None`` on every other path (missing file, half-written file,
      malformed JSON, schema drift).

    Pulled out of :func:`_read_status_from_disk` rather than calling it
    because /healthz must NOT touch the runner instance — Railway hits
    it tens of times per minute and we want a guaranteed-cheap FS-only
    code path.
    """
    snapshot_path = state_dir / SNAPSHOT_FILENAME
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        logger.debug("healthz: failed to read snapshot at %s", snapshot_path)
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("snapshot_ts")
    return value if isinstance(value, str) else None


def _read_complete_lines(path: Path, offset: int) -> tuple[list[bytes], int]:
    """Read whole lines appended since ``offset``; return (lines, new_offset).

    A trailing unterminated chunk (the writer was mid-line when we read)
    stays unread — the next poll will re-read it once the terminating
    ``\\n`` lands. That preserves at-least-once delivery without the
    torn-line drop the naive ``offset += len(chunk)`` would cause.

    Missing file → ``([], offset)``. OSError → ``([], offset)`` + warn.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
    except FileNotFoundError:
        return [], offset
    except OSError:
        logger.warning("sse: failed to read %s", path)
        return [], offset
    if not chunk:
        return [], offset
    last_newline = chunk.rfind(b"\n")
    if last_newline < 0:
        # No complete line yet — leave offset where it was.
        return [], offset
    complete = chunk[: last_newline + 1]
    new_offset = offset + len(complete)
    lines = [line for line in complete.split(b"\n") if line.strip()]
    return lines, new_offset


async def _sse_event_stream(
    *,
    state_dir: Path,
    poll_interval_seconds: float,
    stop_after_seconds: float | None,
) -> AsyncIterator[bytes]:
    """Tail decisions.jsonl + reflections.jsonl + proposals.jsonl as SSE.

    Each newly-appended whole line in any of the three files yields one
    SSE event with ``event:`` set to the file STEM (per
    :data:`STREAM_NAMES`) and ``data:`` set to the JSON line. Mid-line
    bytes are deferred to the next poll — see :func:`_read_complete_lines`.

    ``stop_after_seconds`` is ``None`` in prod (run forever); tests pass
    a finite bound so :class:`fastapi.testclient.TestClient` doesn't
    block indefinitely on the streaming response.
    """
    streams: dict[str, Path] = {
        name: state_dir / _STREAM_FILENAMES[name] for name in STREAM_NAMES
    }
    offsets: dict[str, int] = {name: 0 for name in streams}
    started_monotonic = asyncio.get_running_loop().time()

    while True:
        for name, path in streams.items():
            lines, offsets[name] = _read_complete_lines(path, offsets[name])
            for line_bytes in lines:
                try:
                    parsed = json.loads(line_bytes)
                except json.JSONDecodeError:
                    # A complete line that doesn't parse as JSON is true
                    # garbage (not a torn write); skip + log loudly.
                    logger.debug("sse: skipping malformed line in %s", path)
                    continue
                yield (
                    f"event: {name}\ndata: {json.dumps(parsed, sort_keys=True)}\n\n"
                ).encode()

        if stop_after_seconds is not None:
            elapsed = asyncio.get_running_loop().time() - started_monotonic
            if elapsed >= stop_after_seconds:
                return

        await asyncio.sleep(poll_interval_seconds)


# --------------------------------------------------------------------------- #
# create_app — the factory
# --------------------------------------------------------------------------- #


LLMCostProvider = Callable[[], float]
"""Callable returning month-to-date LLM spend in USD.

Wired to :class:`agent.llm.cost_guard.CostGuard` in production; tests
inject a constant.
"""


def _zero_llm_cost() -> float:
    """Default :class:`LLMCostProvider` — reports zero spend.

    Used when :func:`create_app` is called without an explicit provider.
    Sprint_9 has the cost guard scaffold but the FastAPI wiring lands
    in sprint_10 alongside the operator dashboard cost panel.
    """
    return 0.0


def create_app(
    *,
    agent_runner: AgentRunner,
    backtest_registry: BacktestRegistry,
    runtime_agent: RuntimeAgentRunner | None = None,
    llm_cost_provider: LLMCostProvider | None = None,
    sse_poll_interval_seconds: float = 0.2,
    sse_stop_after_seconds: float | None = None,
) -> FastAPI:
    """Construct a fully-wired :class:`fastapi.FastAPI` app.

    Parameters
    ----------

    agent_runner
        The single-tenant :class:`AgentRunner` instance.

    backtest_registry
        The :class:`BacktestRegistry` instance.

    runtime_agent
        The :class:`RuntimeAgentRunner` weight-delta queue (T-B-031).
        ``None`` → a fresh instance is constructed; explicit injection
        in tests + production lets the SAME queue be shared between
        the FastAPI route (producer) and the sandbox loop (consumer).

    llm_cost_provider
        Optional callable returning the current month-to-date USD spend
        on LLM calls. ``None`` (default) → /status reports 0.0. Sprint_10
        wires this to :class:`agent.llm.cost_guard.CostGuard`.

    sse_poll_interval_seconds
        How often the SSE stream re-reads the JSONL streams. Default
        200 ms — fast enough that the dashboard feels live, slow enough
        that the FS doesn't churn. Tests use a tighter value.

    sse_stop_after_seconds
        Outer time bound for the SSE generator. ``None`` → run forever.
        Tests pass a finite bound (the FastAPI TestClient otherwise
        blocks indefinitely on the streaming response).
    """
    app = FastAPI(
        title="Genesis Experiment Agent — Control Plane",
        version="0.1.0",
        description=(
            "Single-tenant REST + SSE surface for the sandbox Phase 2 loop. "
            "Auth: bearer token via DASHBOARD_API_TOKEN. "
            "PRD §8 + TECHNICAL_PLAN §5.4."
        ),
    )

    # CORS — explicit allow-list per TECHNICAL_PLAN §5.1. ``*`` wildcard
    # is forbidden by the brief.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],  # all matching goes through allow_origin_regex
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=list(CORS_ALLOWED_METHODS),
        allow_headers=list(CORS_ALLOWED_HEADERS),
    )

    deps = ServerState(
        agent_runner=agent_runner,
        backtest_registry=backtest_registry,
        runtime_agent=(
            runtime_agent if runtime_agent is not None else RuntimeAgentRunner()
        ),
    )
    app.state.deps = deps

    # Held on the closure so the /status route reads through a cheap
    # callable rather than reaching into app.state for every poll.
    cost_fn: LLMCostProvider = (
        llm_cost_provider if llm_cost_provider is not None else _zero_llm_cost
    )

    # Monotonic baseline captured at app construction. ``/healthz`` reads
    # ``time.monotonic() - app_started_monotonic`` so wall-clock changes
    # (NTP step, container clock drift) can't poison ``uptime_s``. We do
    # NOT use ``asyncio.get_running_loop().time()`` here because
    # :func:`create_app` is called outside the event loop in production
    # (uvicorn instantiates the app, then runs the loop).
    app_started_monotonic: float = time.monotonic()

    # ---- Routes ---------------------------------------------------------- #

    @app.post(
        "/api/agent/start",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=StartResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_409_CONFLICT: {
                "model": StartConflictResponse,
                "description": "agent already running",
            },
        },
    )
    async def start_agent(_: AuthDep, state: StateDep) -> StartResponse:
        """Spawn the sandbox loop as a background task. Returns 202 + run_id."""
        try:
            run_id = await state.agent_runner.start()
        except AgentAlreadyRunningError as exc:
            # Hand-rolled 409 so the body is the StartConflictResponse
            # shape (FastAPI's default HTTPException only carries
            # ``detail`` — we need run_id too).
            return JSONResponse(  # type: ignore[return-value]
                status_code=status.HTTP_409_CONFLICT,
                content=StartConflictResponse(run_id=exc.run_id).model_dump(),
            )
        return StartResponse(run_id=run_id)

    @app.post(
        "/api/agent/stop",
        status_code=status.HTTP_200_OK,
        response_model=StopResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
        },
    )
    async def stop_agent(_: AuthDep, state: StateDep) -> StopResponse:
        """Gracefully halt the sandbox loop. Idempotent."""
        final_path = await state.agent_runner.stop()
        return StopResponse(
            final_state_path=str(final_path) if final_path is not None else None,
        )

    @app.get(
        "/api/agent/status",
        status_code=status.HTTP_200_OK,
        response_model=StatusResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
        },
    )
    async def get_status(_: AuthDep, state: StateDep) -> StatusResponse:
        """Read durable agent state from disk. No side effects."""
        return _read_status_from_disk(
            runner=state.agent_runner,
            llm_cost_usd_this_month=cost_fn(),
        )

    @app.post(
        "/api/agent/configure",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=AgentConfigureResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_400_BAD_REQUEST: {
                "description": "invalid weight config (rho out of range)",
            },
        },
    )
    async def configure_agent(
        _: AuthDep,
        state: StateDep,
        raw_body: Annotated[dict[str, Any], Body(...)],
    ) -> AgentConfigureResponse:
        """Persist a staged starting-weight config (T-B-037).

        Sequence:

        1. Manually run :meth:`AgentConfigureRequest.model_validate`
           on the raw body and translate any
           :class:`pydantic.ValidationError` into HTTP **400** —
           NOT the default FastAPI 422. The brief locks 400 here
           because the dashboard's editor surfaces the response code
           and "rho out of range" is the canonical 400 case (the
           operator can fix and re-submit, vs a malformed JSON 422
           which is a programming bug).
        2. WARN-only ``w_r + w_s`` drift via
           :meth:`StartingWeightConfig.check_weight_sum`.
        3. Atomic write to ``<state_dir>/agent_config.json``.
        4. If the agent is currently running, log a WARNING — the
           operator's brief locks "config takes effect on next
           /api/agent/start".
        5. 202 + the persisted config + absolute path. The dashboard
           uses the path for its "staged config" pane.
        """
        try:
            body = AgentConfigureRequest.model_validate(raw_body)
        except ValidationError as exc:
            # ``include_context=False`` drops the ``ctx.error`` slot
            # (the original ``ValueError`` instance is not
            # JSON-serialisable) while keeping ``loc`` / ``msg`` /
            # ``type`` / ``input`` so the dashboard's error renderer
            # can pin the failure to a single field.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "validation_errors": exc.errors(
                        include_url=False,
                        include_context=False,
                        include_input=True,
                    ),
                },
            ) from exc
        body.starting_weights.check_weight_sum()
        if state.agent_runner.is_running:
            logger.warning(
                "agent_configure: agent is currently running (run_id=%s)"
                " — staged config takes effect on next /api/agent/start",
                state.agent_runner.current_run_id,
            )
        persisted = _write_agent_config_atomic(
            state_dir=state.agent_runner.state_dir,
            config=body.starting_weights,
        )
        return AgentConfigureResponse(
            starting_weights=body.starting_weights,
            persisted_path=str(persisted),
        )

    @app.post(
        "/api/backtest/run",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BacktestRunResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_400_BAD_REQUEST: {"description": "invalid weight config"},
        },
    )
    async def submit_backtest(
        _: AuthDep,
        state: StateDep,
        body: BacktestRunRequest | None = None,
    ) -> BacktestRunResponse:
        """Kick off a backtest sweep. Returns 202 + run_id immediately.

        T-B-037 — accepts a typed :class:`BacktestRunRequest`:

        * Empty body OR ``configs=[]`` → falls back to the canonical
          4-config default sweep (backward-compat locked by the brief
          + CEO D-S11-001 §scope-decisions §4).
        * Non-empty ``configs`` → each entry is validated by the
          :class:`StartingWeightConfig` field validators (HARD on
          ``rho``, WARN on ``w_r + w_s``) then forwarded as-is to
          the sweep runner via the registry; the runner projects
          to :class:`agent.core.state.Weights` and builds a
          :class:`SweepConfig` against THAT list instead of the
          default.

        The route surfaces a 422 for malformed bodies (FastAPI's
        default Pydantic ValidationError mapping) which the dashboard
        already handles; the brief's "400 outside [-1,1]" gate fires
        through the :meth:`StartingWeightConfig._validate_rho_range`
        validator at parse time.
        """
        configs = body.configs if body is not None else []
        operator_note = body.operator_note if body is not None else None
        if body is not None:
            for cfg in body.configs:
                cfg.check_weight_sum()
        run_id = state.backtest_registry.submit(
            configs=list(configs),
            operator_note=operator_note,
        )
        return BacktestRunResponse(run_id=run_id)

    @app.post(
        "/api/backtest/{run_id}/cancel",
        status_code=status.HTTP_200_OK,
        response_model=BacktestCancelResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_404_NOT_FOUND: {"description": "unknown run_id"},
        },
    )
    async def cancel_backtest(
        _: AuthDep, state: StateDep, run_id: str
    ) -> BacktestCancelResponse:
        """Cooperative-cancel handler for a running sweep (T-B-037).

        Sets the cancel latch on the :class:`BacktestRecord`. The
        sweep runner polls the latch between tick boundaries — when
        set it raises :class:`asyncio.CancelledError` and
        :func:`agent.server.runner._safe_run` writes the
        ``status='cancelled'`` envelope to ``results.json``. Per the
        brief the route returns 200 within milliseconds; the actual
        cancellation lands within ≤5s (one tick boundary on the test
        sweep, one config boundary on the production sweep until the
        sprint_12 per-tick hook lands inside :func:`run_replay`).

        404 — surfaced when no record exists for ``run_id``. Unknown
        ids must NOT 500 (the dashboard polls cancel on stale ids
        from its history pane); the brief locks this disposition.
        """
        if not state.backtest_registry.cancel(run_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="unknown run_id",
            )
        return BacktestCancelResponse(run_id=run_id)

    @app.get(
        "/api/backtest/{run_id}",
        status_code=status.HTTP_200_OK,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_404_NOT_FOUND: {"description": "unknown run_id"},
        },
    )
    async def get_backtest_result(
        _: AuthDep, state: StateDep, run_id: str
    ) -> JSONResponse:
        """Return the ``results.json`` body for a given run_id.

        404 if (a) the registry has no in-memory record for the id AND
        (b) no ``results.json`` exists on disk under
        ``output_root/<run_id>/``. 200 with the file contents otherwise.

        If the run is in flight, the file may not yet exist — we still
        return 404 (the dashboard polls + retries). The 200/404 boundary
        is "file present on disk" so a finished run survives a process
        restart that drops the in-memory record.
        """
        path = state.backtest_registry.result_path(run_id)
        if path is None:
            # Distinguish "unknown id" vs "still running" only via the
            # in-memory registry — both collapse to 404 per the brief.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="unknown run_id" if not state.backtest_registry.is_known(run_id)
                else "result not ready",
            )
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("backtest_result: failed to read %s", path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to read result: {exc}",
            ) from exc
        return JSONResponse(status_code=status.HTTP_200_OK, content=content)

    # ---- T-B-031 — Proposal approve / reject ----------------------------- #

    @app.post(
        "/api/proposals/{proposal_id}/approve",
        status_code=status.HTTP_200_OK,
        response_model=ProposalActionResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_404_NOT_FOUND: {"description": "unknown proposal_id"},
            status.HTTP_409_CONFLICT: {
                "description": "proposal already approved or rejected",
            },
        },
    )
    async def approve_proposal(
        _: AuthDep, state: StateDep, proposal_id: str
    ) -> ProposalActionResponse:
        """Operator-approval handler for one L3 proposal (T-B-031).

        Sequence:

        1. Load the LATEST row for ``proposal_id`` from
           ``proposals.jsonl`` (latest-status-wins fold).
        2. 404 if no matching row exists.
        3. 409 if the latest status is NOT ``"pending"`` (an
           ``approved``/``rejected`` row already wins the fold).
        4. Dispatch on ``kind``:
           * ``weight_delta`` → enqueue the ``proposed_change`` dict on
             the runtime seam (:class:`RuntimeAgentRunner`). The loop
             drains the queue on the next tick.
           * ``new_signal_idea`` / ``prompt_tweak`` → append a TODO row
             to ``state/sandbox/proposal_todos.jsonl`` for manual
             sprint_11 processing (PRD §11 sprint_10 CEO decision 6).
        5. Append a NEW row to ``proposals.jsonl`` with
           ``status="approved"`` for the audit trail.

        The status row is appended AFTER the dispatch so a runtime-seam
        failure (today: impossible — the seam is in-memory) does NOT
        leave the audit trail stamped before the side-effect lands.
        """
        state_dir = state.agent_runner.state_dir
        proposal = _load_pending_proposal_or_raise(
            state_dir=state_dir, proposal_id=proposal_id
        )

        applied_to_runtime = False
        if proposal.kind == "weight_delta":
            # Producer side of the thread-safe seam — the loop drains
            # on its next tick. Per PRD §11 sprint_10 CEO decision 6
            # this is the ONLY auto-apply path.
            state.runtime_agent.apply_weight_delta(dict(proposal.proposed_change))
            applied_to_runtime = True
        else:
            _append_jsonl_atomic(
                state_dir / PROPOSAL_TODOS_FILENAME,
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "proposal_id": proposal.proposal_id,
                    "kind": proposal.kind,
                    "rationale": proposal.rationale,
                    "proposed_change": proposal.proposed_change,
                    "expected_impact": proposal.expected_impact,
                    "confidence_pct": proposal.confidence_pct,
                },
            )

        approved_row = proposal.model_copy(
            update={"status": PROPOSAL_STATUS_APPROVED}
        )
        _append_jsonl_atomic(
            state_dir / PROPOSALS_FILENAME,
            json.loads(approved_row.model_dump_json()),
        )
        return ProposalActionResponse(
            proposal_id=proposal.proposal_id,
            status="approved",
            applied_to_runtime=applied_to_runtime,
        )

    @app.post(
        "/api/proposals/{proposal_id}/reject",
        status_code=status.HTTP_200_OK,
        response_model=ProposalActionResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
            status.HTTP_404_NOT_FOUND: {"description": "unknown proposal_id"},
            status.HTTP_409_CONFLICT: {
                "description": "proposal already approved or rejected",
            },
        },
    )
    async def reject_proposal(
        _: AuthDep,
        state: StateDep,
        proposal_id: str,
        body: ProposalRejectRequest | None = None,
    ) -> ProposalActionResponse:
        """Operator-rejection handler for one L3 proposal (T-B-031).

        Sequence mirrors :func:`approve_proposal` but never touches the
        runtime seam:

        1. Load the LATEST row for ``proposal_id`` (404 if missing).
        2. 409 if the latest status is NOT ``"pending"``.
        3. Append a ``status="rejected"`` row to ``proposals.jsonl``,
           folding the optional ``reason`` into the audit trail via
           the schema's ``proposed_change`` field — the L3 proposal
           schema uses ``extra='ignore'`` so an extra ``reject_reason``
           key would silently drop on re-validation; we therefore
           stamp the reason into ``proposed_change`` under a
           ``reject_reason`` key (a sprint_11 dashboard reader can
           pluck it without a schema bump).
        """
        state_dir = state.agent_runner.state_dir
        proposal = _load_pending_proposal_or_raise(
            state_dir=state_dir, proposal_id=proposal_id
        )

        reason = body.reason if body is not None else None
        # Fold the reason into proposed_change so the audit trail
        # carries it without needing a schema field bump.
        merged_change = dict(proposal.proposed_change)
        if reason is not None:
            merged_change["reject_reason"] = reason
        rejected_row = proposal.model_copy(
            update={
                "status": PROPOSAL_STATUS_REJECTED,
                "proposed_change": merged_change,
            }
        )
        _append_jsonl_atomic(
            state_dir / PROPOSALS_FILENAME,
            json.loads(rejected_row.model_dump_json()),
        )
        return ProposalActionResponse(
            proposal_id=proposal.proposal_id,
            status="rejected",
            applied_to_runtime=False,
        )

    @app.get(
        "/api/state/stream",
        responses={
            status.HTTP_401_UNAUTHORIZED: {"description": "unauthorized"},
        },
    )
    async def stream_state(_: AuthDep, state: StateDep) -> StreamingResponse:
        """SSE endpoint tailing the three runtime JSONL streams.

        ``event:`` is the file stem; ``data:`` is the parsed JSON line.
        The connection stays open until the client disconnects OR the
        configured ``sse_stop_after_seconds`` bound elapses.
        """
        generator = _sse_event_stream(
            state_dir=state.agent_runner.state_dir,
            poll_interval_seconds=sse_poll_interval_seconds,
            stop_after_seconds=sse_stop_after_seconds,
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx hint
            },
        )

    @app.get(
        "/healthz",
        status_code=status.HTTP_200_OK,
        response_model=HealthzResponse,
        include_in_schema=True,
    )
    async def healthz(state: StateDep) -> HealthzResponse:
        """Liveness probe — unauthed, lightweight, no chain / LLM calls.

        Railway pings this every 30 s per the project's ``railway.toml``;
        Docker's ``HEALTHCHECK`` directive hits it from inside the
        container. Both run WITHOUT a bearer token, so this route is
        explicitly outside the :data:`AuthDep` dependency tree. Leaking
        the snapshot timestamp + uptime is not a credential — the only
        sensitive surface (decisions / reflections / proposals) stays
        behind /api auth.

        Implementation notes:

        * ``uptime_s`` uses monotonic time so a wall-clock NTP step
          can't make the value go negative.
        * ``last_tick_ts`` reads the durable snapshot via the same
          ``state_dir`` the /status route already uses — a missing or
          un-parseable snapshot collapses to ``None`` without raising.
        """
        return HealthzResponse(
            uptime_s=max(0, int(time.monotonic() - app_started_monotonic)),
            last_tick_ts=_read_last_tick_ts(state_dir=state.agent_runner.state_dir),
        )

    return app


# --------------------------------------------------------------------------- #
# Production module-level app — what `uvicorn agent.server.main:app` imports.
# --------------------------------------------------------------------------- #


# T-B-038 — the three env-var name constants + the resolver / gate /
# prime helpers all live in :mod:`agent.server.bootstrap` (canonical
# source). They are re-exported here so importers of
# ``agent.server.main.SANDBOX_STATE_DIR_ENV_VAR`` (pre-T-B-038 contract)
# keep working without churn.


class _PlaceholderLoop:
    """Sprint_9 deploy-gate stub — **DE-WIRED** by sprint_13 T-B-041.

    HISTORY
    -------
    Sprint_9 (T-B-028) deploy gate needed a :class:`agent.server.runner.LoopHandle`
    that booted, emitted a marker event, then slept until cancelled —
    enough to prove /healthz + SSE + the backtest sweep surface without
    a full Polymarket / chain / LLM wiring. The boot event carried
    ``placeholder: True`` so an operator inspecting the stream could
    spot sprint_9 wiring at a glance.

    STATUS (sprint_13 T-B-041)
    --------------------------
    The class is **retained but no longer wired** into
    :func:`_build_default_app`. The real :class:`SandboxPhase2Loop`
    boots in its place via :func:`_build_production_loop_factory`. The
    one-line rollback seam — should the production loop trip in prod —
    is to flip ``_build_default_app``'s ``loop_factory=`` argument back
    to :func:`_placeholder_loop_factory(state_dir=state_dir)`. Keeping
    the class defined (rather than deleting it) is the brief-locked
    rollback contract.

    DO NOT delete this class without a CEO-plan amendment — the
    sprint_13 rollback path depends on it being import-safe.

    TODO(post-T-B-042): once :class:`agent.chain.RhChainAdapter` ships
    AND the prod loop has accumulated ≥ 1 sprint of live-run stability
    data, drop this class + :func:`_placeholder_loop_factory` together.
    """

    def __init__(self, *, state_dir: Path) -> None:
        self._state_dir: Path = Path(state_dir)

    async def run(self) -> object:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        _append_loop_boot_row(
            decisions_path=self._state_dir / DECISIONS_FILENAME,
            loop_name="placeholder",
            placeholder=True,
            note=(
                "sprint_9 placeholder loop — full SandboxPhase2Loop wiring "
                "lands in sprint_10. /healthz, /api/state/stream, and "
                "/api/backtest/* are fully wired and tested."
            ),
        )
        # Park until cancelled by AgentRunner.stop() — the brief's 65 s
        # graceful-stop window applies. asyncio.CancelledError flows
        # straight through the sleep; the runner's _await_task swallows
        # it as the expected stop path.
        await asyncio.sleep(10**9)
        return None


def _placeholder_loop_factory(*, state_dir: Path) -> Callable[[], _PlaceholderLoop]:
    """0-arg factory the :class:`AgentRunner` calls per /api/agent/start.

    DE-WIRED by sprint_13 T-B-041 — retained as the one-line rollback
    seam. See :class:`_PlaceholderLoop` docstring for the rollback
    contract."""

    def _factory() -> _PlaceholderLoop:
        return _PlaceholderLoop(state_dir=state_dir)

    return _factory


# --------------------------------------------------------------------------- #
# T-B-041 (sprint_13) — production loop factory + sandbox-safe scaffolds
# --------------------------------------------------------------------------- #


_SANDBOX_COLD_START_BREATH_USD: Final[float] = 100.0
"""Cold-start BREATH + bankroll seed for the sandbox-kind production loop.

Used in THREE places that must stay in lock-step:

* :meth:`_SandboxChainAdapter.__init__` default ``initial_breath`` — the
  in-memory chain fake's starting balance.
* :func:`_build_production_loop_factory` ``initial_breath=`` arg — the
  loop's cold-start hint (overridden on the first tick by the chain
  adapter's ``read_breath`` per the reconstruction step 4 contract).
* :func:`_build_production_loop_factory` ``initial_bankroll_usd=`` arg —
  the loop's cold-start bankroll hint.

Lifting to a module-level constant keeps the three call sites coupled by
reference; the original brief uses 100 USD as the deterministic Phase 2
sandbox starting position so a future operator change is a one-line
edit at this anchor.
"""


class _SandboxChainAdapter:
    """In-memory :class:`SandboxLoopChainAdapter` for sprint_13 sandbox kind.

    The default value of :data:`PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR`
    is ``'sandbox'``; that resolves to a fresh instance of this class.
    The real Polygon RPC-backed adapter lands in sprint_13 T-B-042
    under ``PROD_LOOP_CHAIN_ADAPTER_KIND=rh_chain``; THIS scaffold
    survives as the test-side fake forever.

    BREATH lives on a single mutable scalar:

    * :meth:`update_breath_from_pnl` adds the realised PnL delta (clamps
      at 0 — death triggers when the chain-side balance bottoms out).
    * :meth:`read_breath` returns the current scalar verbatim — the
      reconstruction step 4 "chain as source of truth" refresh.
    * :meth:`kill_and_mint_tombstone` returns a deterministic placeholder
      :class:`DeathReceipt`. No state mutation, so a smoke run that
      drives breath → 0 (via :data:`SANDBOX_FORCE_TERMINAL`) still
      satisfies the loop's "death is one-way" invariant against the
      same JSONL writer.

    Mirrors :class:`agent.backtest.replay_runner._ReplayChainAdapter`
    intentionally — the two share a Protocol surface and the
    test-suite fake convention is uniform across the prod-loop seam +
    the backtest replay seam.
    """

    def __init__(
        self, *, initial_breath: float = _SANDBOX_COLD_START_BREATH_USD
    ) -> None:
        self.current_breath: float = float(initial_breath)

    async def update_breath_from_pnl(self, pnl_usd: float) -> None:
        self.current_breath = max(0.0, self.current_breath + pnl_usd)

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
        # Deterministic placeholder receipt — never read by /api/agent/status;
        # the brief-locked sprint_13 contract is "death path exercises, but
        # the on-chain mint moves to T-B-042's real adapter".
        return DeathReceipt(
            kill_tx_hash="0x" + "0" * 64,
            tombstone_token_id="0",
            tombstone_tx_hash="0x" + "0" * 64,
        )


class _IdleTickInputSource:
    """Sandbox-safe :class:`TickInputSource` that emits NO_BET every tick.

    Returns ``None`` from :meth:`inputs_for` unconditionally → the loop
    routes to a NO_BET decision with ``no_bet_reason="no_eligible_market"``
    AND still consumes BREATH per the PRD §6 action-gating hard rule.

    The real production wiring (per-tick market ranker + 5-engine fanout)
    lands in a downstream sprint_13 task; this scaffold is what the
    sandbox-kind chain adapter pairs with so the prod loop boots
    end-to-end against deterministic inputs. Tests inject scripted
    fakes through the same Protocol when they need to exercise the
    BET path.

    The brief-locked invariant is "NO_BET is NOT a free skip" — the
    loop's tick body calls ``chain.consumeAction`` on every tick (BET
    or NO_BET); this idle source preserves that posture by never
    short-circuiting the tick.
    """

    def inputs_for(
        self,
        *,
        asof_ts: datetime,
        tick: int,
    ) -> TickInputs | None:
        return None


class _NoopSettlementClient:
    """Sandbox-safe :class:`SettlementClient` — always returns ``None``.

    No bets land in the production loop sandbox scaffold (the paired
    :class:`_IdleTickInputSource` emits only NO_BETs), so the settlement
    poller has nothing to resolve. Returning ``None`` from
    :meth:`resolve_market` keeps the poller's per-tick walk a no-op
    without raising — same convention as
    :class:`agent.backtest.replay_runner._ReplaySettlementClient`'s
    pre-resolution branch.

    A future sprint_13 task that flips the tick input source to real
    market ranking will pair with a real settlement client; the prod
    loop factory's seam is symmetric so the swap is a single-line
    change in :func:`_build_default_app`.
    """

    async def resolve_market(self, market_id: str) -> SettlementResult | None:
        return None


class _NoopWeightUpdater:
    """Sandbox-safe :class:`WeightUpdater` — every settlement is a no-op.

    The settlement-time gradient feedback channel (PRD §4.2) takes a
    realised outcome + signals + phase and mutates the loop's weights
    accordingly. The sprint_13 sandbox scaffold has no real settlements
    (see :class:`_NoopSettlementClient`), so wiring a real updater
    would be dead code; this stub satisfies the Protocol without I/O.

    When the production tick input source + settlement client ship,
    swap in the real :class:`agent.engines.weight_updater.WeightUpdater`
    inside :func:`_build_default_app` — the loop factory itself stays
    untouched.
    """

    async def update(
        self,
        *,
        phase: str,
        signals: dict[str, float],
        outcome: SettlementResult,
    ) -> None:
        return None


class _NoopStateHook:
    """Operator-visibility hook that drops every event on the floor.

    Sprint_13 deliberately routes the loop's structured emit() calls to
    /dev/null — the dashboard pulls visibility from the JSONL streams
    (decisions / reflections / proposals) via SSE, not from the in-process
    hook. The hook becomes meaningful when an out-of-band operator
    monitor (e.g. PagerDuty bridge) lands; until then the no-op keeps
    the Protocol satisfied without I/O.
    """

    def emit(self, *, kind: str, **payload: Any) -> None:
        return None


class _NoopPhaseReader:
    """Stub :class:`agent.runtime.phase2_launch._PhaseManagerReader`.

    :class:`Phase2LaunchOrchestrator`'s ``read_phase`` is consulted only
    by :meth:`Phase2LaunchOrchestrator.boot`. The prod loop factory
    uses the orchestrator solely as the loop's ``base`` wrap-target
    (composition; see :class:`SandboxPhase2Loop` docstring) and never
    invokes its ``boot()`` method, so the unused phase-read surface can
    be a deterministic constant."""

    def read_phase(self) -> Phase:
        return Phase.PHASE_2_APPRENTICE


class _NoopDecisionLog:
    """Stub :class:`agent.runtime.phase2_launch._DecisionLogWriter`.

    Same rationale as :class:`_NoopPhaseReader` — the orchestrator's
    ``append`` is only called by its private ``boot()`` path. Returning
    a fixed sentinel from this scaffold keeps the Protocol satisfied
    without I/O."""

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


def _build_chain_adapter(
    *, kind: str, env: dict[str, str] | None = None
) -> SandboxLoopChainAdapter:
    """Resolve the :class:`SandboxLoopChainAdapter` for a given config kind.

    ``kind`` is the resolved value of
    :data:`PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR` (case-folded by
    :func:`agent.server.bootstrap.resolve_prod_loop_config`):

    * ``'sandbox'`` → :class:`_SandboxChainAdapter` — in-memory fake.
    * ``'rh_chain'`` → :class:`agent.runtime.rh_chain_adapter.RhChainAdapter`
      via :func:`agent.runtime.rh_chain_adapter.build_from_env`. Reads
      5 required env vars (``RH_CHAIN_RPC_URL``,
      ``RH_CHAIN_ENERGY_CONTROLLER_ADDRESS``,
      ``RH_CHAIN_AGENT_LIFECYCLE_ADDRESS``,
      ``RH_CHAIN_TOMBSTONE_NFT_ADDRESS``,
      ``RH_CHAIN_SIGNER_PRIVATE_KEY``) and surfaces a typed
      :class:`RuntimeError` listing the missing keys when any are
      blank — keeps the operator runbook deterministic.

    ``env`` defaults to ``os.environ``; tests inject a dict to keep
    the resolver hermetic. The signature is keyword-only so an
    accidental positional call (T-B-041's contract) still passes.
    """
    if kind == PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX:
        # mypy: structural Protocol satisfaction — _SandboxChainAdapter
        # implements every method on SandboxLoopChainAdapter with the
        # same signatures, so a runtime cast is sufficient.
        return _SandboxChainAdapter()
    if kind == PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN:
        # Deferred import — keeps the web3 dependency off the hot
        # boot path for sandbox-kind deploys (T-B-041 default).
        from agent.runtime.rh_chain_adapter import (
            build_from_env as _build_rh_chain_from_env,
        )

        # Structural Protocol satisfaction — RhChainAdapter implements
        # update_breath_from_pnl / read_breath / kill_and_mint_tombstone
        # with the exact signatures SandboxLoopChainAdapter declares.
        return _build_rh_chain_from_env(env=env)
    raise RuntimeError(
        f"unrecognised PROD_LOOP_CHAIN_ADAPTER_KIND={kind!r}; expected "
        f"one of {{{PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX!r}, "
        f"{PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN!r}}}."
    )


LOOP_BOOT_MARKER_LOOP_NAME: Final[str] = "sandbox_phase2_real"
"""Value of the ``loop`` field on the real-loop boot marker row.

T-B-043 — distinct from the sprint_9 ``"placeholder"`` value used by
:class:`_PlaceholderLoop`. The SUBMISSION smoke
(``agent/scripts/sprint13_boot_smoke.py``) asserts on this constant so
a regression that silently re-wires the placeholder factory fails the
gate at boot-time visibility, not just at the unit-test level."""


def _append_loop_boot_row(
    *,
    decisions_path: Path,
    loop_name: str,
    placeholder: bool = False,
    note: str | None = None,
) -> None:
    """Append ONE ``kind=='loop_boot'`` row to ``decisions.jsonl``.

    Single source of truth for the two callers that emit this marker:

    * :meth:`_PlaceholderLoop.run` — sprint_9 placeholder boot
      (``placeholder=True``, note set, ``loop_name="placeholder"``).
    * :func:`_build_production_loop_factory` per /api/agent/start (via
      ``_factory``) — sprint_13 real-loop boot
      (``placeholder=False``, ``loop_name=LOOP_BOOT_MARKER_LOOP_NAME``).

    Locking the shape here keeps the two writers' rows shape-comparable
    by construction — a future contributor cannot drift one without the
    other, and the T-B-043 SUBMISSION smoke's "event #1 ``kind ==
    'loop_boot'`` AND ``placeholder`` key absent" invariant survives
    refactors. Reconstruction safety
    (:meth:`SandboxPhase2Loop._reconstruct_from_disk` step 3 only
    counts ``tick: int`` rows) is preserved because this row never sets
    a ``tick`` field.

    Caller MUST ensure ``decisions_path.parent`` exists — this helper
    does NOT mkdir (the placeholder path mkdirs explicitly; the
    production path's :class:`SandboxStateWriter` mkdirs at construction
    so the directory is live before the factory closure runs).
    """
    row: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "kind": "loop_boot",
        "loop": loop_name,
    }
    if placeholder:
        row["placeholder"] = True
    if note is not None:
        row["note"] = note
    with decisions_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _resolve_decision_cadence(
    *, tick_interval_seconds: float, time_compression: float
) -> timedelta:
    """Compute the loop's effective ``decision_cadence`` from env knobs.

    Formula: ``timedelta(seconds=tick_interval_seconds / time_compression)``.
    Both operands are guaranteed strictly-positive + finite by
    :func:`agent.server.bootstrap.resolve_prod_loop_config` — that helper
    rejects ≤ 0 / NaN / inf at parse time, so the division here cannot
    produce a non-positive or non-finite result.
    """
    return timedelta(
        seconds=float(tick_interval_seconds) / float(time_compression)
    )


def _null_market_resolver(market_id: str) -> MarketInfo | None:
    """Idle-fallback :class:`MarketResolver` — every market is unknown.

    Used when the prod loop boots with no cassettes loaded so the
    :class:`SandboxExecutor` still wires end-to-end (every lookup yields
    ``None`` → the sizer short-circuits to NO_BET).
    """
    return None


def _make_prod_strategy_advisor() -> StrategyAdvisor:
    """Select the prod loop's L3 advisor (Plan 2 / Task L2).

    Default (flag OFF) → :class:`NoOpStrategyAdvisor`, preserving the
    frozen-config smoke contract ("loop boots and ticks", not "L3 generates
    proposals") byte-for-byte.

    ``GENESIS_REAL_STRATEGY_ADVISOR=1`` → the real Gemini-backed
    :class:`StrategyAdvisorImpl`. ``GeminiClient()`` constructs without a key
    (it raises :class:`MissingApiKeyError` only when *called*,
    ``gemini_client.py:153``); the fail-soft lives in
    ``StrategyAdvisorImpl.review_window`` which catches that and returns ``[]``
    (``strategy_advisor_impl.py:230-236``). So the loop never crashes on a
    missing key — proposals simply collapse to ``[]`` until ``GEMINI_API_KEY``
    is supplied. Backtest replay intentionally KEEPS the NoOp (a sweep has no
    operator-approval loop), so this un-stub is prod-only.
    """
    if os.environ.get("GENESIS_REAL_STRATEGY_ADVISOR") == "1":
        from agent.engines.strategy_advisor_impl import StrategyAdvisorImpl
        from agent.llm.cost_guard import L3CostGuard
        from agent.llm.factory import make_llm_client

        return StrategyAdvisorImpl(
            llm_client=make_llm_client(),
            cost_guard=L3CostGuard.from_env(),
        )
    return NoOpStrategyAdvisor()


def _l6_reflection_optimize_enabled() -> bool:
    """Phase B / B2 (codex R7) — is the L6 reflect→optimize CLOSURE on?

    Reflection ALONE emits no proposals: the loop appends reflections but
    the advisor still defaults to :class:`NoOpStrategyAdvisor`, whose
    ``review_window`` returns ``[]`` (``strategy_advisor.py:139-151``). So
    the closed reflect→learn→optimize loop requires BOTH halves wired —
    the real :class:`ReflectionEngine` (produces the reflections the B1
    fold reads) AND the real :class:`StrategyAdvisorImpl` (turns the
    reflection-informed window into proposals). The L6 mode is therefore
    the AND of the two established flags (each exact ``"1"`` per the
    ``GENESIS_REAL_*`` convention):

    * ``GENESIS_REAL_REFLECTION=1`` — wire the real reflection engine +
      flip the B1 advisor-window population seam, and
    * ``GENESIS_REAL_STRATEGY_ADVISOR=1`` — un-stub the real advisor.

    Either flag alone leaves L6 OFF: ADVISOR-alone is the pre-B2 L2 path
    (real advisor, empty reflection window — byte-unchanged); REFLECTION-
    alone would only append reflections with no proposal consumer. Default
    (neither) keeps the frozen-config smoke contract byte-unchanged.
    """
    return (
        os.environ.get("GENESIS_REAL_REFLECTION") == "1"
        and os.environ.get("GENESIS_REAL_STRATEGY_ADVISOR") == "1"
    )


def _make_prod_reflection_engine(*, state_dir: Path) -> ReflectionEngine | None:
    """Select the prod loop's L2 reflection engine (Phase B / B2).

    Returns ``None`` unless the L6 closure is enabled (see
    :func:`_l6_reflection_optimize_enabled`) — a ``None`` engine leaves the
    loop's reflection-trigger pathway a no-op
    (``sandbox_phase2_loop.py:1639``), preserving the frozen-config smoke
    contract byte-for-byte. When L6 is ON, construct the real
    :class:`ReflectionEngine` backed by a lazy :class:`GeminiClient` (which
    constructs without ``GEMINI_API_KEY`` and only raises on its first
    *call*, ``gemini_client.py:152``). The engine's own ``reflect`` is
    fully fail-soft — a missing key / dead LLM is absorbed into a
    deterministic ``fail_soft_unreachable`` record (``reflection.py:212``),
    so the loop never crashes on a missing key; on Railway ``GEMINI_API_KEY``
    is set so it runs real Gemini. The engine writes its narrative MD bodies
    under the loop's MemoryBank reflections dir; the JSONL stream the B1
    advisor-window fold reads is the SEPARATE ``reflections.jsonl`` the loop
    itself appends via ``SandboxStateWriter.append_reflection``.

    Backtest replay intentionally keeps the engine OFF (a sweep has no
    operator-approval loop + must never touch live Gemini), so this is
    prod-only — exactly mirroring :func:`_make_prod_strategy_advisor`.
    """
    if not _l6_reflection_optimize_enabled():
        return None
    from agent.engines.reflection import ReflectionEngine
    from agent.llm.factory import make_llm_client

    return ReflectionEngine(
        llm_client=make_llm_client(),
        reflections_dir=state_dir / "_mb" / "reflections",
    )


def _build_production_loop_factory(
    *,
    state_dir: Path,
    chain_adapter: SandboxLoopChainAdapter,
    tick_input_source: TickInputSource,
    wall_clock: Clock,
    time_compression: float,
    tick_interval_seconds: float,
    settlement_client: SettlementClient | None = None,
    market_resolver: MarketResolver | None = None,
    runtime_agent: RuntimeAgentRunner | None = None,
) -> Callable[[], SandboxPhase2Loop]:
    """Build the 0-arg :class:`agent.server.runner.LoopFactoryProto` for
    the real :class:`SandboxPhase2Loop`.

    T-B-041 (sprint_13 Day 0) — this seam replaces
    :func:`_placeholder_loop_factory`. The returned factory is what
    :class:`AgentRunner` calls per ``/api/agent/start`` to spin up a
    fresh loop handle; reconstruction from disk happens inside
    :meth:`SandboxPhase2Loop.run`, so each /start gets a clean instance
    that hydrates against the durable JSONL streams.

    The factory closes over the externally-injected deps:

    Parameters
    ----------
    state_dir
        Filesystem root for the sandbox loop's JSONL streams + snapshot.
        Resolved by :func:`agent.server.bootstrap.resolve_state_paths`
        from :data:`SANDBOX_STATE_DIR_ENV_VAR`. Each factory call
        re-uses this path so the loop's reconstruction reads the same
        on-disk corpus across start/stop cycles.

    chain_adapter
        Pre-constructed :class:`SandboxLoopChainAdapter`. Sprint_13
        T-B-041 default is :class:`_SandboxChainAdapter` (in-memory);
        T-B-042 will inject the real Polygon RPC adapter. The factory
        does NOT construct this — :func:`_build_default_app` reads
        :data:`PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR` + calls
        :func:`_build_chain_adapter`, then threads the result here.

    tick_input_source
        Pre-constructed :class:`TickInputSource`. Sprint_13 default is
        :class:`_IdleTickInputSource` (emits NO_BETs every tick). Real
        per-tick market ranker + 5-engine fanout lands in a downstream
        sprint_13 task that swaps the injection in
        :func:`_build_default_app` — the factory signature does not
        change.

    wall_clock
        :class:`Clock` Protocol implementation. Production:
        :class:`UtcClock`. Tests inject a deterministic fake.

    time_compression
        Speed-up factor applied to the per-tick wall-clock sleep. 1.0 =
        literal seconds; >1.0 = faster ticks (sandbox smoke / integration
        tests). The factory closes over the divided cadence so every
        constructed loop sees the same compressed-time view; mutating
        the env var mid-process requires a restart.

    tick_interval_seconds
        Pre-divisor wall-clock cadence between ticks. Default 60.

    Returns
    -------
    A :class:`agent.server.runner.LoopFactoryProto` (0-arg callable
    returning a :class:`SandboxPhase2Loop`). Each call constructs a
    fresh loop instance — the brief-locked LoopFactoryProto contract
    requires per-call cleanliness so in-memory state (tick counter,
    breath cache) does NOT bleed across stop/start cycles.

    Implementation notes
    --------------------
    Scaffold deps not yet exposed as seams (executor, settlement client,
    weight updater, state hook, base orchestrator, strategy advisor):
    constructed per-call with sandbox-safe in-memory defaults. The
    sandbox executor + writer share ``state_dir`` so the JSONL streams
    + snapshot land where the SSE + status routes expect them.
    Reconstruction inside :meth:`SandboxPhase2Loop.run` is what carries
    durable state across factory invocations — the in-memory scaffolds
    are stateless across calls by design.
    """
    decision_cadence = _resolve_decision_cadence(
        tick_interval_seconds=tick_interval_seconds,
        time_compression=time_compression,
    )

    def _factory() -> SandboxPhase2Loop:
        # PROMOTE pipeline (workshop backtest → /api/agent/configure → live):
        # consume any staged agent_config.json BEFORE constructing the writer
        # so the fresh-life reset clears the prior snapshot/streams and the
        # loop cold-starts with the promoted weights. Returns None when no
        # fresh config is staged → default-weight behaviour (unchanged).
        promoted_weights = _consume_staged_config(state_dir)
        # Shared single-writer per process — both executor + loop write
        # through THIS instance (single-writer invariant locked by
        # SandboxStateWriter docstring). Constructing the writer first
        # mkdirs ``state_dir`` so the T-B-043 boot-marker append below
        # cannot hit a missing-directory race.
        writer = SandboxStateWriter(root=state_dir)
        # T-B-043 (sprint_13 boot smoke) — structural ``loop_boot`` marker
        # the SSE-side SUBMISSION smoke proves the REAL
        # :class:`SandboxPhase2Loop` is on the wire (i.e. the seam swap
        # from sprint_9 ``_PlaceholderLoop`` landed). The row shape
        # mirrors the placeholder marker MINUS ``placeholder: True`` —
        # both writers go through :func:`_append_loop_boot_row` so the
        # two shapes cannot drift. Reconstruction tolerates the row
        # (step 3 of :meth:`SandboxPhase2Loop._reconstruct_from_disk`
        # only counts rows whose ``tick`` is an int).
        _append_loop_boot_row(
            decisions_path=writer.decisions_path,
            loop_name=LOOP_BOOT_MARKER_LOOP_NAME,
        )
        executor = SandboxExecutor(
            state_writer=writer,
            # Pseudo-bet wiring (sprint_13 follow-up): when the cassette-
            # backed market table is injected, the resolver returns real
            # MarketInfo so the mock executor places + settles bets against
            # real Polymarket markets. Falls back to a None-resolver (idle
            # scaffold) when no cassettes are available so the loop still
            # boots end-to-end.
            market_resolver=market_resolver or _null_market_resolver,
            clock=wall_clock,
        )
        base = Phase2LaunchOrchestrator(
            memory_bank=MemoryBank(root=state_dir / "_mb"),
            phase_reader=_NoopPhaseReader(),
            decision_log=_NoopDecisionLog(),
            engine_signals=None,
        )
        loop = SandboxPhase2Loop(
            base=base,
            state_dir=state_dir,
            weight_updater_phase=WeightUpdaterPhase.PHASE_2_EXTENDED,
            executor=executor,
            settlement_client=settlement_client or _NoopSettlementClient(),
            weight_updater=_NoopWeightUpdater(),
            chain_adapter=chain_adapter,
            tick_inputs=tick_input_source,
            state_hook=_NoopStateHook(),
            state_writer=writer,
            clock=wall_clock,
            sleeper=_real_sleep,
            decision_cadence=decision_cadence,
            initial_phase=Phase.PHASE_2_APPRENTICE,
            # PROMOTE pipeline: a promoted backtest config (consumed above)
            # cold-starts the loop with operator-chosen weights; None →
            # the loop's _phase2_default_weights default (unchanged path).
            initial_weights=promoted_weights,
            # Cold-start hints — overridden on the first tick by the chain
            # adapter's read_breath per the reconstruction step 4 contract.
            initial_breath=_SANDBOX_COLD_START_BREATH_USD,
            initial_bankroll_usd=_SANDBOX_COLD_START_BREATH_USD,
            # L3 advisor (Plan 2 / L2): default NoOp keeps the sprint_13
            # smoke contract ("loop boots and ticks", not "L3 generates
            # proposals") byte-unchanged. GENESIS_REAL_STRATEGY_ADVISOR=1
            # un-stubs the real Gemini-backed StrategyAdvisorImpl; without
            # GEMINI_API_KEY the advisor's review_window fail-soft collapses
            # every trigger to [] (so the loop never crashes on a missing
            # key). Prod-only — backtest replay keeps the NoOp.
            strategy_advisor=_make_prod_strategy_advisor(),
            # L2 reflection engine + L6 reflect→optimize closure (Phase B /
            # B2, codex R7). Default OFF → None engine (reflection trigger is
            # a no-op) AND populate_reflection_window=False (the B1 advisor-
            # window fold is skipped), so the advisor input + frozen-config
            # smoke stay byte-unchanged. Only the COMBINED L6 gate
            # (GENESIS_REAL_REFLECTION=1 AND GENESIS_REAL_STRATEGY_ADVISOR=1)
            # wires the real ReflectionEngine AND flips the population seam —
            # so the reflections the engine produces are folded into the
            # window the (now real, via _make_prod_strategy_advisor) advisor
            # reviews, closing reflect→learn→optimize. We pass the seam value
            # EXPLICITLY rather than letting the loop ctor read the env var,
            # so REFLECTION-alone cannot flip the prod fold without its
            # advisor half (which would emit no proposals). Proposals still
            # flow through the existing L1 approval queue (runtime_agent
            # below) — the advisor is NOT called from _fire_reflection.
            reflection_engine=_make_prod_reflection_engine(state_dir=state_dir),
            populate_reflection_window=_l6_reflection_optimize_enabled(),
            # T-B-031 queue wiring (Plan 2 / L1): thread the SHARED
            # RuntimeAgentRunner so operator-approved weight deltas
            # enqueued by the FastAPI approve route actually reach this
            # loop's _drain_and_apply_weight_deltas consumer. None →
            # the loop ctor falls back to a fresh queue (the pre-fix,
            # default-OFF path — behaviour byte-unchanged).
            runtime_agent=runtime_agent,
        )
        # Settlement-time self-learning (Plan 2 / L3): default OFF keeps the
        # _NoopWeightUpdater above so settlements are inert and the
        # frozen-config smoke contract is byte-unchanged. GENESIS_REAL_LEARNING=1
        # swaps the real settlement-learning bridge onto the poller (Option-B:
        # built AFTER the loop so it can hold the loop as its weights_holder and
        # re-assign loop._weights from realized PnL). The WeightUpdater is
        # constructed here so its EMA state is fresh for this loop instance.
        if os.environ.get("GENESIS_REAL_LEARNING") == "1":
            from agent.backtest.settlement_learner import (
                _SettlementLearningWeightUpdater,
            )
            from agent.engines.weight_updater import WeightUpdater as _RealWeightUpdater

            loop._poller.weight_updater = _SettlementLearningWeightUpdater(
                inner=_RealWeightUpdater(),
                weights_holder=loop,
            )
        return loop

    return _factory


def _build_production_sweep_runner(
    *, cache_dir: Path
) -> Callable[..., Any]:
    """Return a :class:`agent.server.runner.SweepRunnerProto` bound to ``cache_dir``.

    T-B-038 — the cassette source moved from the image-baked
    ``agent/backtest/_cache`` (read-only inside the runtime stage) to
    the volume-mounted ``BACKTEST_CACHE_DIR`` (writable, primed by
    :func:`agent.server.bootstrap.prime_volume_cache` on first boot).
    The sweep runner needs the resolved path threaded into every
    :class:`SweepConfig` it builds; closing over the path here keeps
    the per-request adapter signature unchanged.
    """

    async def _runner(
        *,
        output_dir: Path,
        run_id: str,
        configs: list[Any] | None = None,
        operator_note: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Adapter from :class:`agent.server.runner.SweepRunnerProto` to
        :func:`agent.backtest.sweep_runner.run_sweep`.

        The registry hands us ``(output_dir, run_id, configs, operator_note,
        cancel_event)``. We:

        1. Project each :class:`StartingWeightConfig` (when present) to
           the canonical :class:`agent.core.state.Weights` 6-vector via
           :meth:`StartingWeightConfig.to_weights`. Empty list / ``None``
           → fall back to the default sweep so the existing dashboard
           "RUN BACKTEST" path keeps working.
        2. Build a :class:`SweepConfig` carrying the resolved weights +
           the closed-over ``cache_dir`` and drive :func:`run_sweep`. The
           sweep runner already wipes the per-run state tree on entry,
           so we don't need to pre-clean.
        3. ``operator_note`` is logged (the sprint_11 sweep runner doesn't
           carry an audit field; the sprint_12 follow-up will thread it
           through the results.json envelope).
        4. ``cancel_event`` is honoured between configs via the
           :func:`agent.backtest.sweep_runner.run_sweep` ``cancel_event``
           kwarg (added in this task). Per-tick checking lives inside
           the sweep runner — the registry-side latch is the OUTER seam.

        Lazy-imported so module-import of :mod:`agent.server.main` doesn't
        pull in the heavy sweep dependency tree (pyarrow, the full ETL
        surface) at uvicorn boot. The first /api/backtest/run pays the
        import cost, subsequent runs hit the warm import cache.
        """
        from agent.backtest.sweep_runner import (
            DEFAULT_SWEEP_WEIGHTS,
            SweepConfig,
            run_sweep,
        )

        weights_tuple = (
            tuple(cfg.to_weights() for cfg in configs)
            if configs
            else DEFAULT_SWEEP_WEIGHTS
        )
        # T-B-040 — thread the operator-facing label tuple in parallel
        # with the weights so the sweep runner can hand it to each
        # ReplayConfig → ReplayMetrics → results.json row. Default sweep
        # (no typed body) → None so existing UI fallback to config_id
        # keeps working.
        labels_tuple: tuple[str | None, ...] | None = (
            tuple(cfg.label for cfg in configs) if configs else None
        )

        if operator_note:
            logger.info(
                "backtest sweep run_id=%s — operator_note=%r",
                run_id, operator_note,
            )

        config = SweepConfig(
            run_id=run_id,
            output_root=output_dir.parent,
            starting_weights=weights_tuple,
            cache_dir=cache_dir,
            config_labels=labels_tuple,
        )
        await run_sweep(config, cancel_event=cancel_event)

    return _runner


def _make_prod_signal_source(provider: MarketSnapshotProvider) -> _SignalSource:
    """Select the prod mock-bet loop's per-tick signal source (Task D1).

    Default (flag unset) → the synthetic
    :class:`agent.backtest.replay_runner._DeterministicSignalSource`
    (hash-derived fake signals) that the loop has always used. This keeps
    the production behaviour byte-identical unless the operator opts in,
    so the real-signal path is reversible by simply NOT setting the env
    var.

    ``GENESIS_REAL_SIGNALS=1`` → the real
    :class:`agent.backtest.real_signal_source.RealSignalSource` — real
    market momentum on every cassette + the 4 Sackmann facets on the
    ~65.8% of markets whose slug resolves to two Sackmann players.

    (codex fix) ``_DeterministicSignalSource`` is imported EXPLICITLY here
    (it is otherwise only imported inside :func:`_build_default_app`), so
    this module-level helper does not ``NameError`` on the default branch.

    (A0 correction) The real source's loader is built against
    ``DEFAULT_CORPUS_DIR`` (the full re-vendored 2024–2026 corpus) — NOT
    a bare ``SackmannLoader()``, whose default snapshot dir holds only the
    small SYNTHETIC test fixtures (would miss 2026 and silently fall back
    to a slow online GitHub fetch).
    """
    from agent.backtest.replay_runner import _DeterministicSignalSource

    if os.environ.get("GENESIS_REAL_SIGNALS") == "1":
        from agent.backtest.real_signal_source import RealSignalSource
        from agent.backtest.tennis_match_resolver import TennisMatchResolver
        from data.sources.tennis_sackmann import DEFAULT_CORPUS_DIR, SackmannLoader

        loader = SackmannLoader(snapshot_dir=DEFAULT_CORPUS_DIR)
        return RealSignalSource(
            provider=provider,
            resolver=TennisMatchResolver.from_sackmann_loader(
                loader, year_range=(2024, 2026)
            ),
            loader=loader,
        )
    return _DeterministicSignalSource(seed=0)


def _build_default_app() -> FastAPI:
    """Build the FastAPI app uvicorn imports as ``agent.server.main:app``.

    T-B-038 wiring:

    1. Resolve the three state-path env vars via
       :func:`agent.server.bootstrap.resolve_state_paths`.
    2. Run :func:`agent.server.bootstrap.validate_state_paths` —
       raises a clear :class:`RuntimeError` with a remediation hint
       when any path defaults to ``/data/...`` but the Railway volume
       is not mounted. CEO-locked: NO silent fallback to ``/tmp`` or
       ``cwd`` (D-S11-001 §scope-decisions §7).
    3. ``mkdir(parents=True, exist_ok=True)`` for each of the three —
       safe because step 2 already gated the missing-volume case.
    4. :func:`agent.server.bootstrap.prime_volume_cache` one-shot
       copies seed cassettes from ``agent/backtest/_cache/`` into the
       resolved ``BACKTEST_CACHE_DIR`` on first boot. Subsequent boots
       are mtime-no-op.

    T-B-041 (sprint_13 Day 0) wiring — the agent loop factory swap:

    5. :func:`agent.server.bootstrap.resolve_prod_loop_config` reads
       the three :data:`PROD_LOOP_*` env knobs (tick interval,
       time compression, chain adapter kind) — defaults are pinned to
       the canonical sprint_13 sandbox scaffold so an out-of-the-box
       Railway deploy boots without operator config.
    6. :func:`_build_chain_adapter` resolves the chain-adapter kind to
       either :class:`_SandboxChainAdapter` (default) or — once
       T-B-042 lands — the real Polygon RPC-backed adapter.
    7. :func:`_build_production_loop_factory` wraps the real
       :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`
       with the resolved cadence + injected chain adapter + idle
       tick input source. THIS factory is what
       :func:`AgentRunner.start` calls per ``/api/agent/start``.

    Rollback: if the production loop trips in prod, swap line ``loop_factory=``
    in this function back to ``_placeholder_loop_factory(state_dir=state_dir)``.
    The placeholder class is intentionally retained — see
    :class:`_PlaceholderLoop` docstring for the rollback contract.

    The backtest runner remains built via
    :func:`_build_production_sweep_runner` against the resolved cache dir.
    """
    paths = resolve_state_paths()
    validate_state_paths(paths)

    state_dir = paths.sandbox_state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    backtest_root = paths.backtest_output_root
    backtest_root.mkdir(parents=True, exist_ok=True)
    backtest_cache = paths.backtest_cache_dir
    prime_volume_cache(target=backtest_cache)

    prod_loop_config: ResolvedProdLoopConfig = resolve_prod_loop_config()
    chain_adapter = _build_chain_adapter(kind=prod_loop_config.chain_adapter_kind)

    # Pseudo-bet wiring (sprint_13 follow-up 2026-06-01). CEO requirement:
    # the agent must be ALIVE and placing MOCK bets settled against REAL
    # Polymarket market outcomes (sandbox path locked 2026-05-26) — NOT
    # idle. The T-B-041 scaffold shipped an _IdleTickInputSource (NO_BET
    # every tick) which never trades, so breath only drained via passive
    # burn and the "agent lives/dies on its tennis-market results" story
    # never ran. We close that gap by reusing the backtest replay
    # components already proven by the workshop sweep (52% win-rate, real
    # PnL):
    #   * _ReplayTickInputSource round-robins the cached Polymarket markets
    #     + a deterministic 5-engine signal source → real BET decisions.
    #   * _ReplaySettlementClient resolves each bet against the cached real
    #     outcome (sha256-parity synthetic fallback for legacy cassettes
    #     that pre-date umaResolutionStatus).
    #   * SandboxExecutor records the order WITHOUT touching real funds.
    # Lazy import (matches _build_production_sweep_runner) so module import
    # of agent.server.main stays light. Empty cache (fresh deploy before
    # prime_volume_cache, or a hermetic test) → idle fallback so the loop
    # still boots.
    from agent.backtest.historical_fetcher import (
        MarketSnapshotProvider,
        load_all_cached_markets,
    )
    from agent.backtest.replay_runner import (
        _market_table_from_snapshots,
        _ReplaySettlementClient,
        _ReplayTickInputSource,
    )

    wall_clock = UtcClock()
    _snapshots = load_all_cached_markets(cache_dir=backtest_cache)
    tick_input_source: TickInputSource
    settlement_client: SettlementClient | None
    market_resolver: MarketResolver | None
    if _snapshots:
        _provider = MarketSnapshotProvider(_snapshots)
        _market_table = _market_table_from_snapshots(_snapshots)
        tick_input_source = _ReplayTickInputSource(
            provider=_provider,
            signal_source=_make_prod_signal_source(_provider),
            selected_market_ids=_provider.market_ids,
        )
        settlement_client = _ReplaySettlementClient(
            provider=_provider, clock=wall_clock,
        )

        def _cassette_market_resolver(market_id: str) -> MarketInfo | None:
            return _market_table.get(market_id)

        market_resolver = _cassette_market_resolver
        logger.info(
            "prod loop: pseudo-bet wiring active — %d cached markets "
            "(mock orders + real-outcome settlement)",
            len(_snapshots),
        )
    else:
        tick_input_source = _IdleTickInputSource()
        settlement_client = None
        market_resolver = None
        logger.warning(
            "prod loop: no cached markets under %s — idle fallback "
            "(agent boots but places no bets)",
            backtest_cache,
        )

    # T-B-031 — runtime weight-delta seam. Single instance per process;
    # the SAME instance is threaded into BOTH the FastAPI approve/reject
    # routes (producer, via create_app) AND the SandboxPhase2Loop the
    # factory builds (consumer, via _build_production_loop_factory) so
    # operator-approved deltas actually reach the loop's
    # _drain_and_apply_weight_deltas path (Plan 2 / L1 queue-wiring fix).
    # Constructed BEFORE the factory so it can be threaded into the
    # closure.
    runtime_agent = RuntimeAgentRunner()
    loop_factory = _build_production_loop_factory(
        state_dir=state_dir,
        chain_adapter=chain_adapter,
        tick_input_source=tick_input_source,
        settlement_client=settlement_client,
        market_resolver=market_resolver,
        wall_clock=wall_clock,
        time_compression=prod_loop_config.time_compression,
        tick_interval_seconds=prod_loop_config.tick_interval_seconds,
        runtime_agent=runtime_agent,
    )

    runner = AgentRunner(
        loop_factory=loop_factory,
        state_dir=state_dir,
    )
    registry = BacktestRegistry(
        sweep_runner=_build_production_sweep_runner(cache_dir=backtest_cache),
        output_root=backtest_root,
    )
    return create_app(
        agent_runner=runner,
        backtest_registry=registry,
        runtime_agent=runtime_agent,
    )


# Module-level app uvicorn imports as ``agent.server.main:app`` (see
# ``agent/server/railway.toml`` start command). Lazy initialisation
# guards under :data:`_BUILD_APP_ENV_VAR` so unit tests that import
# this module DON'T pay the side-effect of mkdir'ing the state dir +
# constructing a runner — the tests build their own app via
# :func:`create_app` against ``tmp_path``.
_BUILD_APP_ENV_VAR: Final[str] = "GENESIS_SERVER_AUTOBUILD"


def _should_autobuild_app() -> bool:
    """True when this module should build the production app at import time.

    Default: True (uvicorn boots the container by importing this module
    and expecting ``app`` to exist). The test suite sets
    ``GENESIS_SERVER_AUTOBUILD=0`` via the conftest fixture so pytest
    collection doesn't touch the filesystem under the repo root.
    """
    return os.environ.get(_BUILD_APP_ENV_VAR, "1").strip().lower() not in {"0", "false", ""}


app: FastAPI | None = _build_default_app() if _should_autobuild_app() else None
"""Module-level FastAPI instance uvicorn imports. ``None`` only when the
test suite opts out via :data:`_BUILD_APP_ENV_VAR`."""


__all__ = [
    "AGENT_CONFIG_FILENAME",
    "BACKTEST_CACHE_DIR_ENV_VAR",
    "BACKTEST_OUTPUT_ROOT_ENV_VAR",
    "CORS_ALLOWED_HEADERS",
    "CORS_ALLOWED_METHODS",
    "CORS_ORIGIN_REGEX",
    "LOOP_BOOT_MARKER_LOOP_NAME",
    "PROD_LOOP_CHAIN_ADAPTER_KIND_ENV_VAR",
    "PROD_LOOP_CHAIN_ADAPTER_KIND_RH_CHAIN",
    "PROD_LOOP_CHAIN_ADAPTER_KIND_SANDBOX",
    "PROD_LOOP_TICK_INTERVAL_SECONDS_ENV_VAR",
    "PROD_LOOP_TIME_COMPRESSION_ENV_VAR",
    "PROPOSAL_TODOS_FILENAME",
    "SANDBOX_STATE_DIR_ENV_VAR",
    "AgentConfigureRequest",
    "AgentConfigureResponse",
    "BacktestCancelResponse",
    "BacktestRunRequest",
    "BacktestRunResponse",
    "HealthzResponse",
    "LLMCostProvider",
    "ProposalActionResponse",
    "ProposalRejectRequest",
    "ServerState",
    "StartConflictResponse",
    "StartResponse",
    "StartingWeightConfig",
    "StatusResponse",
    "StopResponse",
    "app",
    "create_app",
]
