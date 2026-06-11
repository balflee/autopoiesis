"""Runtime seam between FastAPI handlers and the sandbox loop — T-B-031.

The :class:`AgentRunner` defined here is the **thread-safe weight-delta
queue** the FastAPI ``/api/proposals/{id}/approve`` handler writes to
when the operator approves a ``kind == "weight_delta"`` proposal. The
sandbox loop drains the queue at the START of every decision tick and
hands each delta to the on-loop weight updater on the way through.

Threading model
---------------

Two participants share the queue:

* **Producer** — the FastAPI route handler. In production the route is
  ``async def`` and runs on the FastAPI / uvicorn event loop. A handler
  that dispatches a blocking ``await`` to a thread pool (e.g. via
  ``run_in_threadpool``) will still call back into this queue from a
  worker thread, so the producer side MUST be thread-safe rather than
  merely async-safe.

* **Consumer** — the sandbox runtime loop. In the sprint_10 wiring this
  is :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`,
  driven by the same FastAPI event loop (one ``asyncio.create_task`` per
  ``/api/agent/start``). The loop calls :meth:`drain_pending_deltas`
  once per tick and applies the returned list in FIFO order before
  the engine fanout.

The producer and consumer COULD live on the same event loop today
(uvicorn is single-threaded by default), but the brief locks
"thread-safe queue" as the seam — see TECHNICAL_PLAN §4.1 line
"loop 线程读, FastAPI 线程写". A :class:`threading.Lock` covers both
sides so a future deployment that runs uvicorn with multiple workers
OR a sync-thread loop driver does NOT need to relitigate the seam.

Why "thread-safe queue" and not "atomic file write"
---------------------------------------------------

The brief offers both as locked alternatives. We pick the in-memory
queue because:

1. **Operator approval rate is bounded** — ~ one per minute peak
   (PRD §11 sprint_10 enclosure: "L3 提议必须人审"). The queue depth
   never exceeds a few tens of items.

2. **A process restart MUST drop the queue.** An approval that races
   ``/api/agent/stop`` and survives a restart with no on-disk audit
   trail of the apply moment would be a silent rewrite of the
   weights — exactly the kind of operator-hostile failure the
   "human approval required" PRD anchor rules out. The on-disk
   ``proposals.jsonl`` is the durable audit trail (status=approved
   row is appended BEFORE the seam call); the queue is a transient
   delivery channel between the handler and the loop, not state.

3. **No extra failure modes.** An atomic-file queue would need its own
   replay / dedup logic on restart, which is risk surface for zero
   product win — the brief calls out the "9-step main loop already
   reads pending deltas" responsibility on the consumer side.

Public surface
--------------

* :class:`AgentRunner` — the queue itself. Construct ONE instance per
  process; FastAPI wires it into :class:`agent.server.main.ServerState`
  and the loop wires it as a constructor dependency.

* :data:`WeightDelta` — the wire shape the producer enqueues. Matches
  :attr:`agent.engines._strategy_proposal_schema.StrategyProposal.proposed_change`
  for ``kind == "weight_delta"`` rows; the queue stores a shallow copy
  so producer-side mutation after the call cannot poison the queued
  payload.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

WeightDelta = dict[str, Any]
"""Free-form weight-delta payload the producer enqueues.

The wire shape mirrors
:attr:`agent.engines._strategy_proposal_schema.StrategyProposal.proposed_change`
for ``kind == "weight_delta"`` rows — typically
``{"key": "w_r", "delta": 0.03}``. The consumer (loop) interprets the
shape; the queue itself is opaque and only guarantees FIFO + shallow
copy semantics.
"""


class AgentRunner:
    """Thread-safe FIFO weight-delta queue (sprint_10 T-B-031 seam).

    The FastAPI ``/api/proposals/{id}/approve`` route calls
    :meth:`apply_weight_delta` when the approved proposal carries
    ``kind == "weight_delta"``. The sandbox loop drains the queue via
    :meth:`drain_pending_deltas` once per tick and applies the result.

    Concurrency invariants
    ----------------------

    * Both :meth:`apply_weight_delta` and :meth:`drain_pending_deltas`
      take the same :class:`threading.Lock`. Two producers AND a
      consumer can call concurrently from different threads; each call
      sees a consistent view of the deque.

    * :meth:`apply_weight_delta` stores ``dict(delta)`` (shallow copy)
      so a producer that mutates the original dict after the call
      cannot retroactively change the queued payload. The deque ITSELF
      holds the copy; the consumer's drained list ALSO contains the
      same copy references — the consumer is the only thread that ever
      touches the queued dicts after the producer hands them off, so
      shallow is sufficient.

    * :meth:`drain_pending_deltas` is non-blocking — it returns
      whatever is currently queued in FIFO order and empties the
      deque. A consumer that runs faster than the producer sees an
      empty list; a consumer that runs slower never loses deltas
      (the deque has no bound).

    Test injection
    --------------

    The brief explicitly requires the test suite to exercise the loop
    pickup, so the runner is dependency-injectable end to end:

    * FastAPI: ``create_app(..., runtime_agent=AgentRunner())`` (the
      ``ServerState`` dataclass holds the instance and the route
      handler resolves it via the existing ``request.app.state.deps``
      seam).
    * Loop: pass the SAME instance into
      :class:`SandboxPhase2Loop`'s constructor (sprint_10 follow-up
      wires this; the T-B-031 test uses a fake "tick" that calls
      :meth:`drain_pending_deltas` directly to prove the pickup).
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._pending: deque[WeightDelta] = deque()

    def apply_weight_delta(self, delta: WeightDelta) -> None:
        """Enqueue one operator-approved weight delta. Thread-safe.

        Parameters
        ----------
        delta
            Free-form payload — see :data:`WeightDelta`. A shallow
            copy is stored so caller-side mutation cannot leak into
            the queued payload.

        Notes
        -----
        The method is intentionally synchronous (NOT ``async def``)
        because:

        * The lock-protected deque op is O(1) microseconds — yielding
          to the event loop would add orders of magnitude more
          latency than the operation itself.
        * FastAPI happily awaits a sync dependency under
          ``run_in_threadpool``; a synchronous seam keeps the
          loop-thread consumer side from needing ``asyncio`` either.
        """
        with self._lock:
            self._pending.append(dict(delta))

    def drain_pending_deltas(self) -> list[WeightDelta]:
        """Drain + return the queued deltas in FIFO order. Thread-safe.

        Returns
        -------
        list[WeightDelta]
            A fresh list of the queued payloads in producer-arrival
            order. Empties the internal deque atomically under the
            lock, so a second drain immediately after returns ``[]``.

        Notes
        -----
        The list ITEMS are the same shallow-copy dicts the producer
        handed off — consumers may mutate them freely because the
        producer's reference is already detached.
        """
        with self._lock:
            out = list(self._pending)
            self._pending.clear()
            return out

    @property
    def pending_count(self) -> int:
        """Snapshot of the current queue depth — for /status + tests.

        Returned under the lock so a concurrent producer / consumer
        cannot make the value lie. Read-only — callers cannot mutate
        the queue through this property.
        """
        with self._lock:
            return len(self._pending)


__all__ = [
    "AgentRunner",
    "WeightDelta",
]
