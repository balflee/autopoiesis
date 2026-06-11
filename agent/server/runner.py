"""Asyncio background-task lifecycle for the sandbox loop + sweeps — T-B-027.

This module owns three independent lifecycles:

1. **AgentRunner** — at most ONE :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`
   running at a time. ``start`` spawns the loop as a background
   :class:`asyncio.Task`; ``stop`` requests a graceful halt (the loop
   honours ``asyncio.CancelledError`` only between ticks per the
   T-B-020 brief, so ``stop`` waits up to ``stop_timeout`` for the
   in-flight tick to finish) then snapshots final state.

2. **BacktestRegistry** — fire-and-forget sweep jobs keyed by ``run_id``.
   ``submit`` schedules :func:`agent.backtest.sweep_runner.run_sweep` as
   a background task and returns immediately with the assigned ``run_id``;
   ``result_path`` resolves the ``results.json`` location for that
   ``run_id`` (or returns ``None`` if unknown OR the sweep is still
   running and the file hasn't materialised yet).

3. **(implicit)** — the FastAPI request task that orchestrates the above.

The runner takes a ``loop_factory`` callable (and ``sweep_factory``)
rather than constructing :class:`SandboxPhase2Loop` itself. Production
wiring imports a factory from ``agent.runtime.phase2_launch`` (sprint_9
follow-up will land that); the unit tests inject a tiny fake that just
appends synthetic decisions to the JSONL stream so the SSE + status
routes have something real to read.

T-B-034 — background-task error capture
---------------------------------------

Both the agent loop and the backtest sweep are launched as
``asyncio.create_task`` background tasks. Without explicit handling an
exception in either coroutine vanishes into the task — the registry
keeps reporting "not ready" and the dashboard never sees a failure.
The :func:`_safe_run` helper wraps the inner coroutine so:

* Any :class:`Exception` is captured, persisted to a per-task error
  file as :class:`RegistryErrorEnvelope` (``status='failed'``), and
  logged at ``ERROR``.
* :class:`asyncio.CancelledError` writes a ``status='cancelled'``
  envelope, logs at ``INFO`` (expected stop path, not a failure), and
  re-raises so :mod:`asyncio` keeps its cancellation invariants.
* :class:`BaseException` (KeyboardInterrupt, SystemExit) propagates
  unchanged — the brief explicitly bars catching it so a Ctrl-C still
  terminates the dev runner cleanly.

Traceback strings are tail-truncated to :data:`MAX_TRACEBACK_BYTES`
(2 KiB) because the bottom of a traceback shows the exception type +
message + originating frame, which is the highest-signal portion to
keep when truncating.
"""

from __future__ import annotations

import asyncio
import logging
import traceback as tb_module
import uuid
from collections import OrderedDict
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from agent.data.sandbox_state import SNAPSHOT_FILENAME

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# T-B-034 — background-task error capture
# --------------------------------------------------------------------------- #


MAX_TRACEBACK_BYTES: Final[int] = 2048
"""Hard cap on the stored traceback string's UTF-8 byte length.

The brief locks ``len(error.traceback.encode()) <= 2048``. We pick 2 KiB
because (a) it comfortably fits the bottom 8-10 frames of a typical
Python traceback — enough to identify the failing line + exception
chain — and (b) it keeps the JSON envelope small enough that the
``/api/backtest/{run_id}`` + ``/api/agent/status`` responses don't
balloon when many failures stack up across a long-running deploy.
"""


TRACEBACK_TRUNC_MARKER: Final[str] = "[...truncated...]\n"
"""Sentinel prepended when the traceback is tail-truncated.

Without the marker an operator reading the error file couldn't
distinguish "the failure had a 12-frame traceback we kept the tail of"
from "the failure had a 4-frame traceback we kept whole". The marker
disambiguates and costs 18 bytes off the 2 KiB budget.
"""


AGENT_ERROR_FILENAME: Final[str] = "agent_error.json"
"""Filename :class:`AgentRunner` writes its failure envelope to under
``state_dir``. ``/api/agent/status`` reads this file so the dashboard
can render the loop-crash shape (PRD §8 demo loop invariant)."""


BACKTEST_RESULT_FILENAME: Final[str] = "results.json"
"""Filename :class:`BacktestRegistry` writes its failure envelope to.

Reuses the same filename the success path uses (the sweep itself
writes ``results.json`` on success) so the
``GET /api/backtest/{run_id}`` route logic is uniform — file present
→ 200 with whatever shape the writer chose; file absent → 404.
"""


class RegistryError(BaseModel):
    """The error sub-object persisted by :func:`_safe_run`.

    Lives at ``results.json::error`` for backtest sweeps and at
    ``agent_error.json::error`` for the agent loop. The shape is
    intentionally narrow — three string fields — so dashboards can
    render it without conditional schema sniffing.

    * ``type`` — exception class name (e.g. ``"ValueError"``).
    * ``message`` — ``str(exc)`` truncated implicitly by the
      ``model_dump_json`` envelope to whatever the underlying string
      content is; not byte-capped here because messages are typically
      short and ALSO useful in full.
    * ``traceback`` — formatted via :func:`traceback.format_exception`
      then tail-truncated to :data:`MAX_TRACEBACK_BYTES` UTF-8 bytes.
    """

    model_config = ConfigDict(extra="forbid")
    type: str
    message: str
    traceback: str


class RegistryErrorEnvelope(BaseModel):
    """Outer envelope written to the per-task error file.

    Two terminal states:

    * ``status='failed'`` + ``error`` populated — the inner coroutine
      raised an :class:`Exception` subclass; :func:`_safe_run` captured
      it.
    * ``status='cancelled'`` + ``error=None`` — the inner coroutine
      saw :class:`asyncio.CancelledError`. This is the EXPECTED stop
      path (see :meth:`AgentRunner.stop`) and is NOT a failure; the
      envelope is written purely for auditability so operators can
      tell a cancelled run apart from one that simply never started.

    ``completed_at`` is the wall-clock ISO-8601 UTC timestamp the
    envelope was written. It complements but does NOT replace the
    success path's own timestamp; both can co-exist in the rare race
    where a sweep finishes writing ``results.json`` and is then
    cancelled mid-cleanup (we'll overwrite with the cancellation
    envelope).
    """

    model_config = ConfigDict(extra="forbid")
    status: Literal["failed", "cancelled"]
    error: RegistryError | None
    completed_at: str


def _truncate_traceback(text: str) -> str:
    """Tail-truncate ``text`` to :data:`MAX_TRACEBACK_BYTES` UTF-8 bytes.

    Returns ``text`` unchanged if it already fits. Otherwise prepends
    :data:`TRACEBACK_TRUNC_MARKER` to the last
    ``MAX_TRACEBACK_BYTES - len(marker)`` bytes. UTF-8 partial-byte
    decoding at the slice boundary falls back to ``replace`` so an
    invalid byte at the cut doesn't crash the error-capture path
    (which is itself an error-handling path — defence in depth).
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_TRACEBACK_BYTES:
        return text
    marker_bytes = TRACEBACK_TRUNC_MARKER.encode("utf-8")
    keep = MAX_TRACEBACK_BYTES - len(marker_bytes)
    if keep <= 0:
        # Pathological: the marker itself doesn't fit. Return a raw
        # truncation. Shouldn't trigger with the locked 2 KiB cap.
        return encoded[:MAX_TRACEBACK_BYTES].decode("utf-8", errors="replace")
    tail = encoded[-keep:]
    return TRACEBACK_TRUNC_MARKER + tail.decode("utf-8", errors="replace")


def _build_registry_error(exc: BaseException) -> RegistryError:
    """Capture ``type``, ``message``, and a truncated ``traceback``.

    Accepts :class:`BaseException` so a programmer using the helper
    outside :func:`_safe_run` can also stamp a captured exception (this
    is the rule the brief locks for the persisted shape, not for the
    catch boundary inside ``_safe_run``).
    """
    formatted = "".join(
        tb_module.format_exception(type(exc), exc, exc.__traceback__)
    )
    return RegistryError(
        type=type(exc).__name__,
        message=str(exc),
        traceback=_truncate_traceback(formatted),
    )


def _write_error_envelope(
    path: Path,
    *,
    status: Literal["failed", "cancelled"],
    error: RegistryError | None,
) -> None:
    """Write a :class:`RegistryErrorEnvelope` to ``path`` atomically.

    Uses a ``<path>.tmp`` + :meth:`pathlib.Path.replace` pair so a
    process-level crash mid-write doesn't leave a torn JSON file the
    status route would choke on. Creates ``path.parent`` if missing —
    defensive for the test harness which uses fresh ``tmp_path`` dirs.
    """
    envelope = RegistryErrorEnvelope(
        status=status,
        error=error,
        completed_at=datetime.now(UTC).isoformat(),
    )
    payload = envelope.model_dump_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


async def _safe_run(
    *,
    run_id: str,
    coro: Awaitable[object],
    error_file: Path,
    label: str,
) -> None:
    """Wrap a background coroutine and persist any exception to disk.

    See module docstring for the rationale + the three terminal cases:

    * :class:`Exception` → log ``ERROR``, write ``status='failed'``,
      RETURN (do NOT re-raise; the task ends cleanly so a downstream
      ``await task`` doesn't surface the swallowed exception).
    * :class:`asyncio.CancelledError` → log ``INFO``, write
      ``status='cancelled'``, RE-RAISE (asyncio cancellation semantics
      require it to propagate so the awaiter sees the cancellation).
    * :class:`BaseException` → propagate unchanged. Only ``Exception``
      is caught — KeyboardInterrupt / SystemExit still terminate.

    A secondary failure during envelope persistence (disk full, etc.)
    is logged but never re-raised on the Exception path, so a flaky
    filesystem can't escalate one task failure into a second one. On
    the cancellation path the persistence failure is also swallowed
    BEFORE the re-raise so the cancellation propagates regardless.
    """
    try:
        await coro
    except asyncio.CancelledError:
        logger.info("%s: cancelled run_id=%s", label, run_id)
        try:
            _write_error_envelope(error_file, status="cancelled", error=None)
        except Exception:
            logger.exception(
                "%s: failed to persist cancel envelope run_id=%s",
                label,
                run_id,
            )
        raise
    except Exception as exc:
        logger.error(
            "%s: failed run_id=%s with %s",
            label,
            run_id,
            type(exc).__name__,
            exc_info=exc,
        )
        try:
            _write_error_envelope(
                error_file,
                status="failed",
                error=_build_registry_error(exc),
            )
        except Exception:
            logger.exception(
                "%s: failed to persist error envelope run_id=%s",
                label,
                run_id,
            )


DEFAULT_STOP_TIMEOUT_SECONDS = 65.0
"""Brief acceptance criterion: stop completes within 65 seconds.

The loop's per-tick decision cadence is 60 minutes in prod and 0 in
tests, but a tick can still spend wall-clock on chain RPCs + LLM calls.
65 s is the brief-locked outer bound — beyond that we hard-cancel the
task and surface the timeout in the response.
"""


class LoopHandle(Protocol):
    """Minimal Protocol the AgentRunner needs from a sandbox loop.

    Production: :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`
    satisfies this via duck-typing on its public surface. Tests inject
    a fake that records calls + appends synthetic JSONL rows.
    """

    async def run(self) -> object:
        """Drive the loop until cancellation OR a self-decided stop.

        Production callers don't pass ``until`` / ``max_ticks`` so the
        loop runs until the caller cancels its task. Test fakes return
        an arbitrary summary object the runner ignores.
        """
        ...


class LoopFactoryProto(Protocol):
    """Callable that constructs a fresh :class:`LoopHandle` per run.

    The factory is called inside :meth:`AgentRunner.start` so each run
    gets a clean handle — the loop holds in-memory state (tick counter,
    breath cache) which must NOT bleed across stop/start cycles.
    Reconstruction from disk happens inside :meth:`LoopHandle.run` so
    durable state survives.
    """

    def __call__(self) -> LoopHandle: ...


class SweepRunnerProto(Protocol):
    """Async callable that runs one backtest sweep.

    Production: :func:`agent.backtest.sweep_runner.run_sweep` partially
    applied with a SweepConfig. Tests inject a fake that writes a
    minimal ``results.json`` and exits.

    T-B-037 widened the Protocol with three NEW kwargs:

    * ``configs`` — optional list of :class:`StartingWeightConfig`
      from the typed ``BacktestRunRequest`` body. ``None`` falls back
      to the canonical 4-config default sweep (backward-compat locked
      by the brief).
    * ``operator_note`` — free-form audit annotation persisted alongside
      the sweep run record.
    * ``cancel_event`` — :class:`asyncio.Event` set by
      :meth:`BacktestRegistry.cancel`. Implementations check the
      event between configs (and ideally between ticks); when set
      they raise :class:`asyncio.CancelledError` so :func:`_safe_run`
      can write the ``status='cancelled'`` envelope per the CEO-locked
      "no SIGKILL — set the cancel flag, let the loop check" rule.

    All three kwargs default to ``None`` so existing call sites stay
    source-compatible; the registry passes the kwargs unconditionally.
    """

    async def __call__(
        self,
        *,
        output_dir: Path,
        run_id: str,
        configs: list[Any] | None = None,
        operator_note: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None: ...


# --------------------------------------------------------------------------- #
# AgentRunner
# --------------------------------------------------------------------------- #


@dataclass
class AgentRunState:
    """Bookkeeping for the currently-running agent.

    ``run_id`` is uuid4 hex assigned at :meth:`AgentRunner.start` time;
    ``task`` is the background :class:`asyncio.Task` so :meth:`AgentRunner.stop`
    can cancel it.
    """

    run_id: str
    task: asyncio.Task[object]


class AgentRunner:
    """Single-tenant lifecycle wrapper for the sandbox loop.

    Concurrency invariants
    ----------------------

    * At most ONE active loop at any time. Calling :meth:`start` while
      :attr:`is_running` returns True raises :class:`AgentAlreadyRunningError`
      which the FastAPI layer maps to HTTP 409.

    * :meth:`stop` is idempotent. Calling stop when nothing is running
      returns ``None`` immediately; calling it while a stop is already
      in flight piggybacks on the existing graceful-shutdown task.

    The runner takes a ``state_dir`` so /status can read
    ``agent_state.json`` directly without going through the loop
    instance (which may be mid-tick and holding a lock on the writer).
    The /status route is read-only against the durable JSON — no shared
    mutable state across the route → runner boundary.
    """

    def __init__(
        self,
        *,
        loop_factory: LoopFactoryProto,
        state_dir: Path,
        stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._loop_factory: LoopFactoryProto = loop_factory
        self._state_dir: Path = Path(state_dir)
        self._stop_timeout_seconds: float = stop_timeout_seconds
        self._state: AgentRunState | None = None
        # asyncio.Lock guards the start/stop transition so two concurrent
        # /start requests can't both win the "no current run" check. The
        # FastAPI default event loop is single-threaded so a Lock is
        # sufficient (no need for a threading.Lock).
        self._transition_lock: asyncio.Lock = asyncio.Lock()

    @property
    def state_dir(self) -> Path:
        """Filesystem root the loop writes JSONL streams + snapshot into."""
        return self._state_dir

    @property
    def is_running(self) -> bool:
        """True iff a loop task is alive and not yet finished."""
        st = self._state
        return st is not None and not st.task.done()

    @property
    def current_run_id(self) -> str | None:
        """Run id of the in-flight loop; ``None`` when idle."""
        return self._state.run_id if self._state is not None else None

    async def start(self) -> str:
        """Spawn a fresh loop as a background task. Returns the run_id.

        Raises :class:`AgentAlreadyRunningError` if a loop is already
        running. The FastAPI layer translates that to HTTP 409 + the
        existing run_id in the response body so the dashboard can resume
        without spawning a duplicate.
        """
        async with self._transition_lock:
            if self.is_running:
                raise AgentAlreadyRunningError(
                    run_id=self._state.run_id if self._state is not None else "",
                )
            run_id = uuid.uuid4().hex
            loop_handle = self._loop_factory()
            # T-B-034: wrap the loop coroutine in `_safe_run` so an
            # unhandled exception inside ``loop_handle.run()`` lands in
            # ``state_dir/agent_error.json`` instead of vanishing into
            # the background task. /api/agent/status reads that file
            # and surfaces the failure shape to the dashboard.
            error_file = self._state_dir / AGENT_ERROR_FILENAME
            # Clear any stale envelope from a PRIOR run BEFORE starting.
            # Otherwise a /api/agent/start after a crash leaves the old
            # failure envelope on disk, /status keeps reporting
            # last_run_status='failed' alongside a healthy running task,
            # AND re-parses the stale JSON on every poll. The unlink is
            # idempotent (missing_ok) so first-start sees no error.
            try:
                error_file.unlink(missing_ok=True)
            except OSError:
                # Stale envelope persists. Not fatal — the new run will
                # overwrite it on terminal disposition. Log for ops.
                logger.warning(
                    "agent_runner: failed to clear stale error file at %s",
                    error_file,
                )
            task = asyncio.create_task(
                _safe_run(
                    run_id=run_id,
                    coro=loop_handle.run(),
                    error_file=error_file,
                    label="agent_runner",
                ),
                name=f"sandbox-loop-{run_id}",
            )
            self._state = AgentRunState(run_id=run_id, task=task)
            logger.info("agent_runner: started run_id=%s", run_id)
            return run_id

    async def stop(self) -> Path | None:
        """Cancel the running loop and wait for graceful shutdown.

        Returns the path to the final state snapshot (``agent_state.json``)
        if it exists on disk after the stop, ``None`` otherwise. The
        return shape is idempotent: calling stop when nothing is
        running ALSO returns the snapshot path if it exists (the
        dashboard's "stop & inspect" button must work after a crash
        recovery where nothing is technically running).

        The cancellation propagates as :class:`asyncio.CancelledError`
        into the loop's ``await self._sleeper(...)`` between ticks — the
        loop's brief locks "the loop honours cancellation between ticks
        only". We tolerate up to ``stop_timeout_seconds`` of in-flight
        tick time before falling back to a hard cancel.
        """
        async with self._transition_lock:
            current = self._state
            if current is None or current.task.done():
                # Idempotent: nothing to stop. Clear the slot so the
                # next /start gets a fresh state.
                self._state = None
                return self._final_state_path_if_exists()

            current.task.cancel()
            try:
                # `asyncio.wait_for` swallows the inner CancelledError
                # but re-raises TimeoutError if the loop exceeds the
                # graceful window. We re-raise neither here — the
                # FastAPI route surfaces the wall-clock outcome.
                await asyncio.wait_for(
                    asyncio.shield(self._await_task(current.task)),
                    timeout=self._stop_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "agent_runner: stop timed out after %.1fs — task is "
                    "still alive but the runner slot is cleared",
                    self._stop_timeout_seconds,
                )

            self._state = None
            return self._final_state_path_if_exists()

    @staticmethod
    async def _await_task(task: asyncio.Task[object]) -> None:
        """Await a task without re-raising :class:`asyncio.CancelledError`.

        ``asyncio.Task`` re-raises whatever the coroutine raised on
        ``await``; we want the runner to treat cancellation as the
        expected stop path, not propagate it up to FastAPI as a 500.
        """
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("agent_runner: loop task raised on shutdown")

    def _final_state_path_if_exists(self) -> Path | None:
        """Return the snapshot path if it exists, else ``None``."""
        candidate = self._state_dir / SNAPSHOT_FILENAME
        return candidate if candidate.exists() else None


class AgentAlreadyRunningError(RuntimeError):
    """Raised when :meth:`AgentRunner.start` is called while running.

    Carries the current ``run_id`` so the FastAPI 409 response can
    surface it back to the dashboard without an extra round-trip.
    """

    def __init__(self, *, run_id: str) -> None:
        super().__init__(f"agent already running (run_id={run_id})")
        self.run_id: str = run_id


# --------------------------------------------------------------------------- #
# BacktestRegistry
# --------------------------------------------------------------------------- #


@dataclass
class BacktestRecord:
    """Tracking entry for one submitted sweep.

    ``output_dir`` is where :func:`agent.backtest.sweep_runner.run_sweep`
    writes ``results.json`` + ``lifetimes.jsonl``. We hold it so a later
    GET ``/api/backtest/{run_id}`` can return the results without
    knowing the runner's per-job filesystem layout.

    T-B-037 — ``cancel_event`` is the cooperative-cancellation latch the
    :meth:`BacktestRegistry.cancel` route sets. The sweep runner checks
    it between tick boundaries (and at minimum between configs in
    serial mode) and raises :class:`asyncio.CancelledError` when set;
    :func:`_safe_run` writes the resulting ``status='cancelled'``
    envelope to ``output_dir/results.json``. CEO direction
    D-S11-001 §scope-decisions §5 locks "graceful — set the task's
    cancel flag, the replay loop checks at each tick boundary. No
    SIGKILL." The flag is a one-way latch; clearing it on a second
    cancel call has no observable effect because the underlying task
    has already entered shutdown by then.
    """

    run_id: str
    output_dir: Path
    task: asyncio.Task[None]
    cancel_event: asyncio.Event


class BacktestRegistry:
    """In-memory registry mapping ``run_id`` → background sweep task.

    Single-process: the registry lives for the lifetime of the FastAPI
    app. A process restart loses the in-memory map BUT the artefacts
    on disk survive — the dashboard can still GET ``/api/backtest/{run_id}``
    against a known ``run_id`` because :func:`result_path` checks the
    filesystem too.
    """

    DEFAULT_MAX_RECORDS: int = 1024
    """LRU bound on the in-memory record map. A long-lived control plane
    serves many backtest requests over its lifetime; without a bound the
    map grows unbounded. Eviction is safe because :meth:`result_path`
    falls back to the on-disk artefact under ``output_root/<run_id>/``
    whenever the in-memory record is gone."""

    def __init__(
        self,
        *,
        sweep_runner: SweepRunnerProto,
        output_root: Path,
        max_records: int | None = None,
    ) -> None:
        self._sweep_runner: SweepRunnerProto = sweep_runner
        self._output_root: Path = Path(output_root)
        self._max_records: int = max_records or self.DEFAULT_MAX_RECORDS
        self._records: OrderedDict[str, BacktestRecord] = OrderedDict()

    def submit(
        self,
        *,
        configs: list[Any] | None = None,
        operator_note: str | None = None,
    ) -> str:
        """Schedule a sweep as a background task. Returns ``run_id``.

        The run_id is uuid4 hex — independent of the deterministic
        ``new_run_id`` derivation in :func:`agent.backtest.sweep_runner.new_run_id`
        because here we're just naming the request, not the sweep
        contents. The sweep itself chooses its own deterministic run_id
        when it writes; the API records the REQUEST id and the sweep's
        artefact dir is keyed by it for clean separation.

        T-B-037 — accepts an optional ``configs`` list (the typed
        :class:`BacktestRunRequest` body, projected to
        :class:`StartingWeightConfig`) and an optional
        ``operator_note``. Both are forwarded to the
        :class:`SweepRunnerProto` so the production sweep can build a
        request-specific :class:`SweepConfig` instead of the canonical
        4-config default. Empty / ``None`` ``configs`` triggers the
        backward-compat default sweep at the runner side.

        Always also constructs a fresh :class:`asyncio.Event` which is
        the cooperative-cancellation latch the new
        ``/api/backtest/{run_id}/cancel`` route flips via
        :meth:`cancel`. The event is threaded into the sweep coroutine
        so the runner can check it between ticks.
        """
        run_id = uuid.uuid4().hex
        output_dir = self._output_root / run_id
        cancel_event = asyncio.Event()
        # T-B-034: wrap the sweep coroutine in `_safe_run`. On any
        # Exception the helper writes a failed envelope to
        # ``output_dir/results.json`` so the GET route returns 200 with
        # the failure shape instead of 404 "result not ready". The
        # production sweep's lazy ImportError surfaces through this
        # same path — see ``agent.server.main._production_sweep_runner``.
        # T-B-037: thread ``configs`` + ``operator_note`` + ``cancel_event``
        # through to the sweep runner. Existing fakes that accepted only
        # ``output_dir`` + ``run_id`` keep working because the new kwargs
        # all default to ``None`` on the Protocol.
        coro = self._sweep_runner(
            output_dir=output_dir,
            run_id=run_id,
            configs=configs,
            operator_note=operator_note,
            cancel_event=cancel_event,
        )
        error_file = output_dir / BACKTEST_RESULT_FILENAME
        task = asyncio.create_task(
            _safe_run(
                run_id=run_id,
                coro=coro,
                error_file=error_file,
                label="backtest_registry",
            ),
            name=f"backtest-{run_id}",
        )
        self._records[run_id] = BacktestRecord(
            run_id=run_id,
            output_dir=output_dir,
            task=task,
            cancel_event=cancel_event,
        )
        # LRU eviction — preserve the most recent ``max_records`` entries.
        while len(self._records) > self._max_records:
            self._records.popitem(last=False)
        logger.info("backtest_registry: submitted run_id=%s", run_id)
        return run_id

    def cancel(self, run_id: str) -> bool:
        """Set the cancel latch on the record for ``run_id`` (T-B-037).

        Returns ``True`` iff a record exists; the route maps the
        return to 200 / 404. Calling cancel on a run that has already
        finished still returns True (the latch is idempotent and the
        underlying task ignores a set-after-completion).

        The CEO-locked rule (D-S11-001 §scope-decisions §5) forbids
        :meth:`asyncio.Task.cancel` here — the sweep runner MUST poll
        the latch itself. The acceptance criterion locks the
        per-tick check to ≤5s wall-clock so a long-running config
        can't hold the cancel hostage for the full 60-second sweep
        budget.
        """
        record = self._records.get(run_id)
        if record is None:
            return False
        record.cancel_event.set()
        logger.info("backtest_registry: cancel requested run_id=%s", run_id)
        return True

    def result_path(self, run_id: str) -> Path | None:
        """Return the ``results.json`` path if it exists, else None.

        Existence check is filesystem-based so a process restart that
        loses the in-memory record can still serve the result IF the
        caller still has the run_id. ``None`` means "not yet ready"
        OR "unknown run_id" — the route distinguishes the two via the
        in-memory registry.
        """
        record = self._records.get(run_id)
        # If we have an in-memory record, use ITS output_dir (which is
        # output_root / run_id). If we don't, fall back to the same
        # convention so a restart-recovered caller can still hit the
        # file IF it knows the run_id.
        out_dir = record.output_dir if record else self._output_root / run_id
        candidate = out_dir / "results.json"
        return candidate if candidate.exists() else None

    def is_known(self, run_id: str) -> bool:
        """True iff the registry has an in-memory record for ``run_id``."""
        return run_id in self._records


__all__ = [
    "AGENT_ERROR_FILENAME",
    "BACKTEST_RESULT_FILENAME",
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "MAX_TRACEBACK_BYTES",
    "TRACEBACK_TRUNC_MARKER",
    "AgentAlreadyRunningError",
    "AgentRunState",
    "AgentRunner",
    "BacktestRecord",
    "BacktestRegistry",
    "LoopFactoryProto",
    "LoopHandle",
    "RegistryError",
    "RegistryErrorEnvelope",
    "SweepRunnerProto",
]
