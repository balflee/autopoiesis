"""Sandbox runtime state writer — append-only JSONL + atomic snapshot.

Spec anchors
------------

* CEO sprint_8 sandbox-pivot plan (2026-05-26, locked architecture
  decision #2): persistence under ``state/sandbox/`` as append-only
  JSONL streams (``open_bets``, ``settled_bets``, ``decisions``)
  plus a single ``agent_state.json`` snapshot file. The single-writer
  invariant is what guarantees the JSONL streams are tail-followable
  by the dashboard (Track D T-D-009) without lock coordination.
* PRD §6 + TECHNICAL_PLAN §4.1 — every decision (BET and NO_BET) is a
  ledger event; sandbox replaces the real Polymarket order broadcast
  with :class:`SandboxExecutor.place_order` but preserves the same
  audit trail shape on disk.
* PRD §14.1 look-ahead bias — no ``available_at`` semantics here; this
  module is purely a sink. The auditor scopes itself to ``features/``
  dirs and ``*features*`` filenames so this module is clean by
  location.

Architectural invariants enforced inline
----------------------------------------

* **Single-writer**: only :class:`SandboxStateWriter` may write under
  ``state/sandbox/``. Concurrent appends from multiple writer
  *instances* within the same process are serialised by an
  :class:`threading.Lock` per writer-instance + the ``open(..., 'a')``
  + ``f.write(line + '\\n')`` POSIX append-atomicity guarantee for
  single-line writes ≤ ``PIPE_BUF``. Two threads sharing one writer
  thus interleave whole lines, never half-lines.
* **Append-only**: every accepted record is one ``write(json + '\\n')``
  call against an opened-for-append handle. No truncation, no
  in-place edit, no rename of the JSONL streams. The
  :class:`SandboxStateWriter` exposes no method that opens the JSONL
  files in any mode other than ``'a'``.
* **Atomic snapshot**: ``agent_state.json`` is the one exception —
  it's overwritten in place via the temp-file + ``os.replace``
  pattern. A reader who races a writer either sees the *old*
  complete file or the *new* complete file, never a partial write.
* **No network**: this module imports nothing that touches the
  network at import OR call time. Tests can assert via a
  ``socket.create_connection`` tripwire.

Wire shapes
-----------

All four record types are Pydantic models with ``extra='forbid'`` so a
schema-drift bug surfaces at write time, not three weeks later when
the dashboard parser dies. ``ConfigDict(extra='forbid')`` mirrors the
convention of :mod:`agent.data.polymarket` and friends.

The JSONL line for each record is ``model.model_dump_json()`` followed
by a single ``\\n``. The dump uses pydantic's default settings (UTC
isoformat for ``datetime``, ``None`` round-tripped as ``null``).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from agent.core.state import Weights
from agent.engines._strategy_proposal_schema import StrategyProposal
from agent.engines.reflection import SandboxReflectionRecord

# --------------------------------------------------------------------------- #
# Paths (configurable per writer instance; SANDBOX_DIR is the default).
# --------------------------------------------------------------------------- #

SANDBOX_DIR: Final[Path] = Path("state/sandbox")
"""Default root for sandbox runtime state.

Per CEO sandbox-pivot plan (2026-05-26, locked decision #2).
Writers can override via the ``root`` constructor arg — tests
inject a ``tmp_path`` to avoid touching the repo's real
``state/sandbox/``.
"""

OPEN_BETS_FILENAME: Final[str] = "open_bets.jsonl"
SETTLED_BETS_FILENAME: Final[str] = "settled_bets.jsonl"
DECISIONS_FILENAME: Final[str] = "decisions.jsonl"
SNAPSHOT_FILENAME: Final[str] = "agent_state.json"
# T-B-024 — sandbox reflection JSONL stream. Distinct from the rich MD
# files the ReflectionEngine writes under ``reflections_dir/``.
REFLECTIONS_FILENAME: Final[str] = "reflections.jsonl"
# T-B-025 — L3 meta-optimizer proposal JSONL stream. One line per
# StrategyProposal returned by the StrategyAdvisor on a trigger fire
# (M=100 ticks OR 20 consecutive stable ticks per the brief).
# Sprint_9 NoOpStrategyAdvisor never appends; sprint_10 LLM-backed
# advisor will produce 0..N rows per trigger. The dashboard
# (T-D-010 follow-up) tails this file for the pending-proposals panel.
PROPOSALS_FILENAME: Final[str] = "proposals.jsonl"


# --------------------------------------------------------------------------- #
# Record models — wire-stable shapes the dashboard reads.
# --------------------------------------------------------------------------- #


class BetRecord(BaseModel):
    """One open bet, appended to ``open_bets.jsonl``.

    Fields mirror the T-B-018 acceptance criteria:

    * ``bet_id``              — synthetic UUID4 hex from the executor.
    * ``ts``                  — ISO-8601 UTC string when ``place_order``
      accepted the bet (NOT a payload field).
    * ``market_id``           — Polymarket market id (numeric string).
    * ``side``                — ``YES`` or ``NO`` (Polymarket binary).
    * ``price``               — implied probability at order time, [0, 1].
    * ``size_usd``            — USDC notional.
    * ``expected_settle_ts``  — ISO-8601 UTC string, derived from the
      market's ``end_date_iso`` + the 2-hour heuristic lag from the
      T-B-017 spike (median gameStart→closedTime +4.89 h; the 2 h
      add-on covers the dashboard's "we *should* see this resolved by
      then" UI hint, NOT the agent's polling cadence — the real
      settlement poller in T-B-019 polls on
      ``umaResolutionStatus``, not on this field).
    * ``status``              — ``"open"`` at insertion time (executor) or
      ``"settled"`` after the settlement poller (T-B-019) folds in the
      gamma-api outcome. The append-only invariant locked by the CEO
      2026-05-26 plan forbids in-place edits, so the poller appends a
      NEW line with ``status="settled"`` instead of mutating the
      original. Readers compute the "still-open" view by taking the
      LAST observed status per ``bet_id`` (see
      :func:`agent.runtime.sandbox_settlement_poller.SandboxSettlementPoller._select_due_bets`).
    """

    model_config = ConfigDict(extra="forbid")

    bet_id: str
    ts: str
    market_id: str
    side: Literal["YES", "NO"]
    price: float = Field(ge=0.0, le=1.0)
    size_usd: float = Field(gt=0.0)
    expected_settle_ts: str
    status: Literal["open", "settled"] = "open"
    # Decision-time per-engine signal scores ∈ [-1, 1], keyed by engine
    # name. Carried so the settlement-time weight updater (Task L3) can
    # attribute realized PnL to the engines that drove the bet. Defaults
    # to {} so pre-L3 readers + records stay valid (append-only JSONL —
    # written once at order time, never mutated).
    signal_scores: dict[str, float] = Field(default_factory=dict)
    # A9 storm-kit stamps (plan 2026-06-13): the gate AS APPLIED at
    # placement, for the post-hoc counterfactual ledger. ``None`` =
    # storm off; :func:`bet_record_jsonl_dict` OMITS None keys so
    # flag-off JSONL rows stay byte-identical to the pre-kit shape.
    storm_at_bet: float | None = None
    edge_at_bet: float | None = None
    min_edge_at_bet: float | None = None
    gamma_at_bet: float | None = None
    eff_min_edge_at_bet: float | None = None
    # V1.4 execution-cost stamps (plan-loop 2026-06-17): the ACTUAL fill price +
    # the cost components charged at order time, for cost-NET settlement PnL.
    # Optional/None so legacy + replay rows still load (extra="forbid"); omitted
    # from JSONL when None so flag-off rows stay byte-identical to the pre-V1.4
    # shape. The LIVE / probe settlement path REQUIRES them
    # (:func:`assert_cost_fields_present`); the replay/legacy path tolerates None.
    fill_price: float | None = None
    fee_bps: float | None = None
    spread_paid_usd: float | None = None
    liquidity_cap_usd: float | None = None


# A9 (r13 M-2): the SINGLE source for BetRecord's on-disk JSONL shape.
# The storm-stamp keys are omitted when None so flag-off rows stay
# byte-identical to the pre-kit shape (this repo serializes defaults —
# nullness alone cannot gate the keys). The writer AND the executor
# disk-shape tests both consume this helper; the old
# ``disk JSON == model_dump()`` equality contract is deliberately retired.
_STORM_STAMP_KEYS: Final[tuple[str, ...]] = (
    "storm_at_bet",
    "edge_at_bet",
    "min_edge_at_bet",
    "gamma_at_bet",
    "eff_min_edge_at_bet",
)

# V1.4 — same omit-when-None discipline as the storm stamps, so a pre-V1.4
# (cost-less) row stays byte-identical on disk.
_COST_STAMP_KEYS: Final[tuple[str, ...]] = (
    "fill_price",
    "fee_bps",
    "spread_paid_usd",
    "liquidity_cap_usd",
)


def bet_record_jsonl_dict(bet: BetRecord) -> dict[str, object]:
    """The on-disk JSONL row for one :class:`BetRecord`."""
    row = bet.model_dump()
    for key in (*_STORM_STAMP_KEYS, *_COST_STAMP_KEYS):
        if row.get(key) is None:
            row.pop(key, None)
    return row


def execution_cost_usd(bet: BetRecord) -> float:
    """Total execution cost charged on ``bet`` in USD = fee + spread.

    ``fee_bps`` is basis points of the ``size_usd`` notional; ``spread_paid_usd``
    is already a dollar amount. Returns ``0.0`` when the cost stamps are absent
    (legacy / replay rows) — so cost-NET PnL is byte-identical to the legacy
    formula for those, and only LIVE/probe rows (which set the stamps) take a
    haircut.
    """
    fee = (bet.fee_bps / 10_000.0) * bet.size_usd if bet.fee_bps is not None else 0.0
    spread = bet.spread_paid_usd if bet.spread_paid_usd is not None else 0.0
    return fee + spread


def assert_cost_fields_present(bet: BetRecord) -> None:
    """Fail-closed guard for the LIVE / probe settlement path (V1.4): raise if any
    execution-cost stamp is missing, so a cost-blind row can never be booked as a
    "cost-net" result. The replay/legacy path does NOT call this (it tolerates None)."""
    missing = [k for k in _COST_STAMP_KEYS if getattr(bet, k) is None]
    if missing:
        raise ValueError(
            f"BetRecord {bet.bet_id} is missing execution-cost stamps {missing} — "
            "the LIVE/probe settlement path requires them (fail-closed)."
        )


class SettledBetRecord(BaseModel):
    """One settled bet, appended to ``settled_bets.jsonl``.

    The settlement poller (T-B-019) joins on ``bet_id`` to mark an
    open bet resolved. We record the *outcome* projection from
    :class:`agent.data.polymarket_settlement.SettlementResult` plus
    the realised PnL the agent computed.
    """

    model_config = ConfigDict(extra="forbid")

    bet_id: str
    market_id: str
    settled_ts: str           # ISO-8601 UTC string
    outcome: Literal["yes", "no", "void"]
    winning_price: float = Field(ge=0.0, le=1.0)
    pnl_usd: float
    status: Literal["settled"] = "settled"
    # V1.4b — why a bet was terminally closed without a real resolution, e.g.
    # ``"terminal_query_failed"`` when the agent dies with an open bet and the
    # Gamma settlement query exhausted its retries. ``None`` for a normal
    # resolution-driven settlement; omitted from the JSONL (exclude_none in
    # :meth:`SandboxStateWriter.append_settled_bet`) so existing settled rows
    # stay byte-identical.
    reason: str | None = None


class DecisionRecord(BaseModel):
    """One agent decision tick, appended to ``decisions.jsonl``.

    Mirrors :file:`.dev/contracts/decision_record.v0.2.0.json` shape
    at the field level — the sandbox writer preserves the BET/NO_BET
    tag, the burn class, the fused score, and the bankroll snapshot
    so Track D can replay sessions deterministically. Full schema
    validation happens elsewhere (the decision pipeline still emits
    the canonical envelope into its own JSONL — this is the *sandbox*
    cut, narrower because the dashboard reads it directly).
    """

    model_config = ConfigDict(extra="forbid")

    tick: int = Field(ge=0)
    ts: str                                   # ISO-8601 UTC string
    market_id: str | None                     # None on idle ticks
    kind: Literal["BET", "NO_BET"]
    size_usd: float                           # 0.0 for NO_BET
    side: Literal["YES", "NO"] | None         # None for NO_BET
    edge_pct: float | None                    # may be None on degraded ticks
    no_bet_reason: str | None                 # populated only when kind == NO_BET
    breath_after: float = Field(ge=0.0)
    bankroll_usd_after: float


class AgentStateSnapshot(BaseModel):
    """The full agent state, persisted atomically to ``agent_state.json``.

    This is the snapshot the dashboard reads on a cold-start refresh
    (no JSONL tailing) and the recovery boot uses to rehydrate. It's
    intentionally small — no engine internals, no buffered orderbook
    frames — just the durable scalars + lists of *open* bets.

    ``weights`` (T-B-020 multi-day loop addendum) carries the 6-parameter
    fusion snapshot so a process restart can byte-for-byte rehydrate the
    weight state without replaying the whole tick stream. Optional so
    existing snapshot writers (T-B-018, T-B-019) keep working unchanged
    — only the multi-day driver populates this field. The schema is
    private runtime state per the T-B-020 brief (not a cross-track
    interface), so the extend-without-bump is sanctioned.

    ``desperate`` (T-B-020) latches the PRD §6.5 survival-mode flag
    across restarts so the decision engine reads the same posture after
    rehydration. False on cold start.

    ``pending_proposals`` (T-B-025 L3 scaffold) carries the proposal_ids
    of every :class:`StrategyProposal` the
    :class:`agent.engines.strategy_advisor.StrategyAdvisor` has emitted
    but the operator has not yet resolved (approved / rejected). Empty
    list on cold start; sprint_9 NoOpStrategyAdvisor never appends so
    the list stays empty under default wiring. Sprint_10 LLM-backed
    advisor + the T-D-010 dashboard follow-up bring the list to life.
    Default ``[]`` (via :func:`Field(default_factory=list)`) so existing
    snapshot writers (T-B-018 through T-B-024) keep working unchanged
    — only the L3-aware loop populates this field.

    ``pending_proposal_ids`` (T-B-030 sprint_10) is the canonical name
    for the same data — kept as a :func:`computed_field` so the on-disk
    snapshot carries BOTH keys (the dashboard SSE / status reader is
    happy either way) without storing two copies in memory. The brief
    explicitly calls for ``pending_proposal_ids`` on
    :file:`agent_state.json`; we keep ``pending_proposals`` populated
    too so the sprint_9 swap-test assertions + the T-B-028
    :mod:`agent.server.main` ``data.get("pending_proposals", [])``
    reader stay valid without back-compat shims. Input validation also
    accepts EITHER key via :class:`AliasChoices` so a sprint_10
    follow-up writer that emits only the new name can re-load a
    sprint_9 snapshot without a migration script. The
    :class:`AliasChoices` registers a Pydantic-validation-only alias;
    construction via the ``pending_proposals`` kwarg still works
    because field-name kwargs always bind regardless of the alias
    (Pydantic v2 binds the field name OR any validation_alias entry —
    see :issue:`pydantic/pydantic#8551`).
    """

    model_config = ConfigDict(extra="forbid")

    snapshot_ts: str                          # ISO-8601 UTC string
    phase: Literal[
        "PHASE_1_INFANCY",
        "PHASE_2_APPRENTICE",
        "PHASE_3_MASTER",
        "PHASE_4_TERMINAL",
    ]
    breath: float = Field(ge=0.0)
    bankroll_usd: float
    phase_age_days: float = Field(ge=0.0)
    open_bet_ids: list[str] = Field(default_factory=list)
    last_tick: int = Field(ge=-1, default=-1)
    weights: Weights | None = None
    desperate: bool = False
    pending_proposals: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("pending_proposals", "pending_proposal_ids"),
    )

    @model_validator(mode="before")
    @classmethod
    def _absorb_pending_proposal_ids(cls, data: Any) -> Any:
        """T-B-030 — strip ``pending_proposal_ids`` from input when present.

        The :func:`computed_field` ``pending_proposal_ids`` emits the
        key on serialisation, but with ``extra='forbid'`` the same key
        would cause :class:`ValidationError` on the next
        :meth:`AgentStateSnapshot.model_validate_json` round-trip. We
        absorb it here in mode='before': if the input dict carries
        ``pending_proposal_ids`` AND no ``pending_proposals`` key, we
        promote it; otherwise we drop it (the :class:`AliasChoices`
        validation_alias on ``pending_proposals`` already handles the
        promotion case, but we must defensively pop the key so
        ``extra='forbid'`` doesn't flag it on a round-trip).
        """
        if isinstance(data, dict) and "pending_proposal_ids" in data:
            promoted = data.pop("pending_proposal_ids")
            if "pending_proposals" not in data:
                data["pending_proposals"] = promoted
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pending_proposal_ids(self) -> list[str]:
        """T-B-030 — canonical alias for :attr:`pending_proposals`.

        Returns a fresh :class:`list` copy so a consumer that mutates
        the result (``snap.pending_proposal_ids.append(...)``) cannot
        accidentally aliasing-poke the underlying
        :attr:`pending_proposals` field. The :func:`computed_field`
        decorator ensures the on-disk snapshot carries this key
        alongside the legacy ``pending_proposals`` key so a sprint_10
        dashboard reader can key off the new name without an on-disk
        migration; the :meth:`_absorb_pending_proposal_ids` validator
        handles the round-trip back through
        :meth:`model_validate_json`.
        """
        return list(self.pending_proposals)


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #


class SandboxStateWriter:
    """Single-writer sink for sandbox JSONL streams + snapshot.

    Construct *one* instance per sandbox runtime process and pass it
    into every component that needs to record (executor, settlement
    poller, decision loop). Two writer instances sharing the same
    ``root`` are NOT supported — the architectural single-writer
    invariant is per-process, not per-instance.

    All four record methods are thread-safe within a single writer
    instance via :attr:`_lock`. POSIX append-write atomicity for
    ≤ ``PIPE_BUF`` bytes (4 KB on Linux, larger elsewhere) plus the
    in-process lock guarantees readers tailing the file see one
    well-formed JSON object per line.

    Tests inject a ``tmp_path`` via the ``root`` constructor arg to
    keep the suite hermetic — the writer NEVER touches the repo's
    real ``state/sandbox/`` from inside pytest.
    """

    def __init__(self, *, root: Path | None = None) -> None:
        self._root: Path = root if root is not None else SANDBOX_DIR
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Path accessors — public so tests can re-read the JSONL streams.
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def open_bets_path(self) -> Path:
        return self._root / OPEN_BETS_FILENAME

    @property
    def settled_bets_path(self) -> Path:
        return self._root / SETTLED_BETS_FILENAME

    @property
    def decisions_path(self) -> Path:
        return self._root / DECISIONS_FILENAME

    @property
    def snapshot_path(self) -> Path:
        return self._root / SNAPSHOT_FILENAME

    @property
    def reflections_path(self) -> Path:
        """T-B-024 — sandbox reflection JSONL stream.

        One line per :class:`SandboxReflectionRecord` the
        :class:`agent.runtime.sandbox_phase2_loop.SandboxPhase2Loop`
        appends when a tick_interval or weight_delta trigger fires.
        Same append-only single-writer invariant as the other JSONL
        streams; the dashboard tails the file directly per T-D-010.
        """
        return self._root / REFLECTIONS_FILENAME

    @property
    def proposals_path(self) -> Path:
        """T-B-025 — sandbox L3 strategy-proposal JSONL stream.

        One line per :class:`StrategyProposal` the
        :class:`agent.engines.strategy_advisor.StrategyAdvisor` returns
        on a trigger fire (M=100 ticks OR 20 consecutive stable ticks).
        Sprint_9 NoOpStrategyAdvisor never appends; sprint_10
        LLM-backed advisor produces 0..N rows per trigger. Same
        append-only single-writer invariant as the other JSONL streams;
        the dashboard (T-D-010 follow-up) tails the file directly to
        render the pending-proposals panel.
        """
        return self._root / PROPOSALS_FILENAME

    # ------------------------------------------------------------------
    # Append-only writers — one line per accepted record.
    # ------------------------------------------------------------------

    def append_open_bet(self, bet: BetRecord) -> None:
        """Append one open-bet line to ``open_bets.jsonl``.

        Single ``write(json + '\\n')`` against an append-mode handle —
        POSIX guarantees a process atomically appends ≤ ``PIPE_BUF``
        bytes, so concurrent appenders interleave whole lines never
        half-lines. The :attr:`_lock` belt-and-braces serialises
        in-process callers besides.

        Serialized via :func:`bet_record_jsonl_dict` (A9): None-valued
        storm stamps are omitted so flag-off rows keep the pre-kit shape.
        """
        self._append_jsonl(
            self.open_bets_path, json.dumps(bet_record_jsonl_dict(bet))
        )

    def append_settled_bet(self, bet: SettledBetRecord) -> None:
        """Append one settled-bet line to ``settled_bets.jsonl``.

        ``exclude_none=True`` omits the optional V1.4b ``reason`` when absent;
        all other fields are required (non-None), so a normal settled row stays
        byte-identical to the pre-V1.4b shape.
        """
        self._append_jsonl(
            self.settled_bets_path, bet.model_dump_json(exclude_none=True)
        )

    def append_decision(self, decision: DecisionRecord) -> None:
        """Append one decision line to ``decisions.jsonl``."""
        self._append_jsonl(self.decisions_path, decision.model_dump_json())

    def append_reflection(self, reflection: SandboxReflectionRecord) -> None:
        """Append one reflection line to ``reflections.jsonl`` (T-B-024).

        Same append-atomic POSIX guarantee + in-process lock as the
        other three JSONL streams. The append-only invariant locked by
        the CEO 2026-05-26 plan extends here verbatim — no truncation,
        no in-place edit, the dashboard tails the file safely.
        """
        self._append_jsonl(self.reflections_path, reflection.model_dump_json())

    def append_proposal(self, proposal: StrategyProposal) -> None:
        """Append one L3 strategy-proposal line to ``proposals.jsonl`` (T-B-025).

        Same append-atomic POSIX guarantee + in-process lock as the
        other four JSONL streams. The dashboard pending-proposals panel
        (T-D-010 follow-up) tails this stream to render one card per
        unresolved proposal.
        """
        self._append_jsonl(self.proposals_path, proposal.model_dump_json())

    # ------------------------------------------------------------------
    # Atomic snapshot — temp-file + os.replace.
    # ------------------------------------------------------------------

    def write_snapshot(self, state: AgentStateSnapshot) -> None:
        """Atomically overwrite ``agent_state.json``.

        Uses :func:`tempfile.NamedTemporaryFile` in the *same*
        directory as the target (so :func:`os.replace` stays on the
        same filesystem and is atomic on both POSIX and Windows) and
        then :func:`os.replace` swaps the new file into place. A
        reader who races this call either sees the *old* file or the
        *new* file in full — never a partial write.

        ``flush()`` + ``os.fsync()`` are called before ``replace`` so
        the swap is durable across power loss. Tests don't care about
        durability, but the production sandbox runtime does.
        """
        target = self.snapshot_path
        payload = state.model_dump_json()
        # NamedTemporaryFile with delete=False so we can ``os.replace``
        # it ourselves; otherwise the context manager unlinks on exit
        # and the replace target vanishes. dir= keeps temp + target on
        # the same filesystem (rename atomicity requirement).
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self._root),
                prefix=".agent_state.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            with self._lock:
                os.replace(tmp_path, target)
                tmp_path = None  # transferred ownership
        finally:
            # If anything raised between NamedTemporaryFile.close() and
            # os.replace, the temp file is still on disk — unlink it
            # so we don't leak .agent_state.*.tmp files in the dir.
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:  # pragma: no cover — best-effort
                    pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append_jsonl(self, path: Path, line: str) -> None:
        """One line ↦ one ``write(json + '\\n')`` against append-mode handle.

        :attr:`_lock` covers the open+write+close cycle so two threads
        sharing this writer can't intersperse partial writes (POSIX
        atomicity ALSO covers this for ≤ ``PIPE_BUF`` writes, but the
        lock is cheap insurance plus it serialises the snapshot
        replacer against appenders).
        """
        with self._lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# --------------------------------------------------------------------------- #
# Read helpers — for tests + downstream consumers.
# --------------------------------------------------------------------------- #


def iter_jsonl(path: Path) -> list[dict[str, object]]:
    """Read a JSONL file, return one dict per well-formed line.

    Corrupt lines (truncated writes, non-JSON garbage) are SKIPPED, not
    raised — the dashboard tails these files live and a single bad
    line shouldn't take down the whole UI. The single-writer +
    append-atomicity invariant makes corruption rare in practice; the
    skip path exists for defensive replay tooling.
    """
    out: list[dict[str, object]] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


__all__ = [
    "DECISIONS_FILENAME",
    "OPEN_BETS_FILENAME",
    "PROPOSALS_FILENAME",
    "REFLECTIONS_FILENAME",
    "SANDBOX_DIR",
    "SETTLED_BETS_FILENAME",
    "SNAPSHOT_FILENAME",
    "AgentStateSnapshot",
    "BetRecord",
    "DecisionRecord",
    "SandboxStateWriter",
    "SettledBetRecord",
    "bet_record_jsonl_dict",
    "iter_jsonl",
]
