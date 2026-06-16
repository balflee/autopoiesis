"""Shared Pydantic request models for the FastAPI control plane — T-B-037.

This module owns the dashboard-facing wire shapes the sprint_11 workshop
loop drives:

* :class:`StartingWeightConfig` — the canonical operator-facing weight
  shape, registry-tracked under
  ``.dev/contracts/starting_weight_config.v1.0.0.json``. Carries the
  collapsed scalar form ``{label, w_r, w_s, alpha, beta, rho}`` the
  dashboard's config builder emits. The 3-vector / 2-vector
  :class:`agent.core.state.Weights` is constructed from this via
  :meth:`StartingWeightConfig.to_weights` so the sweep runner and the
  agent loop both consume the SAME native shape they already validate
  against PRD §4.1.

* :class:`BacktestRunRequest` — typed body for
  ``POST /api/backtest/run``. Accepts an OPTIONAL ``configs`` list; an
  empty list (or absent body) falls back to the canonical 4-config
  sweep so the existing dashboard "RUN BACKTEST" button keeps working
  without per-request tuning.

* :class:`AgentConfigureRequest` — typed body for
  ``POST /api/agent/configure``. Single :class:`StartingWeightConfig`
  the operator wants to PROMOTE to the next live agent run.

Why these models live in their own module (vs being added to
``agent.server.main``):

1. The mypy --strict surface is narrower — main.py already carries the
   route handlers + lifecycle plumbing; adding three more models would
   push it past 1300 lines and hurt review density.
2. Both Track D (dashboard) and a future Track E reconciler can import
   the shapes without pulling the full FastAPI app + the heavy
   ``agent.runtime`` deps the main module's create_app re-exports.
3. The registry tracks ``starting_weight_config`` as its own contract
   id — keeping the model module-scoped lets the JSON Schema mirror
   in ``.dev/contracts/`` line up with a single producer file.

PRD anchors: §8 (Dashboard workshop input loop),
TECHNICAL_PLAN §5.1 (typed request bodies), §5.4 (data contracts),
CEO direction D-S11-001 §scope-decisions §4-6.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.core.state import Weights

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# StartingWeightConfig — registry-tracked operator-facing weight shape
# --------------------------------------------------------------------------- #


WEIGHT_SUM_TOLERANCE: float = 0.01
"""Loose tolerance for the ``w_r + w_s ≈ 1.0`` check.

The brief locks ``rho ∈ [-1, 1]`` as a HARD validation (reject 400
outside range) but treats the w_r + w_s normalisation as a WARN-only
contract — the workshop UI may emit ``(0.8, 0.4)`` while the operator
is mid-tweak, and bouncing the request would break the editor flow.

The downstream :func:`StartingWeightConfig.to_weights` projection
renormalises so the canonical :class:`Weights` invariant
(``w_r + w_s == 1.0``) still holds when the value is fed to the sweep
runner. The on-disk + on-wire StartingWeightConfig keeps the operator's
ORIGINAL ratio so a round-trip read (`/api/agent/status` → editor)
doesn't silently rewrite their intent.
"""


class StartingWeightConfig(BaseModel):
    """Operator-facing starting-weight shape — registry-tracked.

    Fields
    ------

    ``label``
        Short human-readable tag the dashboard renders in the workshop
        comparison cards. Must be non-empty so a multi-config sweep's
        ``results.json`` carries a per-row archetype identifier the
        config-comparison view can de-duplicate against.

    ``w_r``, ``w_s``
        Rational / sentient mixing weights. Constrained to ``[0, 1]``;
        ``abs(w_r + w_s - 1.0) > 0.01`` triggers a WARN log via the
        ``check_weight_sum`` validator (the brief locks "warns but
        accepts"). The :meth:`to_weights` projection renormalises
        before the canonical :class:`Weights` validator runs so the
        Weights invariant ``w_r + w_s == 1.0`` stays enforced
        downstream.

    ``alpha``
        Collapsed scalar form of the 3-vector rational-layer weights
        (PRD §4.1 ``α₁ / α₂ / α₃``). Interpreted as ``α₁`` (the
        tennis-technical engine's share); the remaining mass
        ``(1 - alpha)`` is split evenly across ``α₂`` (market_momentum)
        and ``α₃`` (surface_advantage) in the :meth:`to_weights`
        projection. The dashboard's Phase-2 demo fixture uses the same
        collapse, so the wire shape mirrors what the operator already
        sees.

    ``beta``
        Collapsed scalar form of the 2-vector sentient-layer weights
        (PRD §4.1 ``β₁ / β₂``). Interpreted as ``β₁``; ``β₂`` is
        ``1 - beta``. Phase-1 frozen at β₁=1.0 per PRD §4.1
        sentient-layer-degenerate carve-out — when the operator
        configures a Phase-1 agent the dashboard pre-fills 1.0 and
        renders the slider read-only.

    ``rho``
        Inter-layer correlation parameter (PRD §4.1). HARD validated
        to ``[-1, 1]`` — outside the range raises ``ValueError`` which
        FastAPI surfaces as a 422 (the route catches it and returns
        400 per the brief).

    Why a separate model vs reusing :class:`Weights`:

    The dashboard's workshop builder emits the scalar form because
    Phase-1 / Phase-2 collapse the engine bucket mixing to a single
    knob — exposing the full 3-vector would force the operator to
    manually keep three numbers summing to 1.0 every keystroke. The
    scalar form is the operator's ergonomic surface; the 3-vector
    :class:`Weights` is the math's canonical form. The two coexist;
    :meth:`to_weights` is the one-way projection. Workshop reads run
    the OPPOSITE direction via :meth:`from_weights`.
    """

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=64)]
    w_r: Annotated[float, Field(ge=0.0, le=1.0)]
    w_s: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(ge=0.0, le=1.0)]
    beta: Annotated[float, Field(ge=0.0, le=1.0)]
    rho: float

    @field_validator("rho")
    @classmethod
    def _validate_rho_range(cls, value: float) -> float:
        """HARD: ``rho ∈ [-1, 1]`` per PRD §4.1. Outside → ValueError.

        FastAPI turns the ValueError into a 422 by default; the route
        translates it to 400 per the brief so the dashboard's editor
        can surface a clean "rho out of range" message inline.
        """
        if not (-1.0 <= value <= 1.0):
            raise ValueError(
                f"rho={value} outside [-1, 1] — PRD §4.1 inter-layer "
                "correlation parameter is bounded by Cauchy-Schwarz"
            )
        return value

    def check_weight_sum(self) -> None:
        """WARN-only: ``w_r + w_s ≈ 1.0``. Mirrors the brief's "warns
        but accepts" rule. Called by :meth:`AgentConfigureRequest` +
        the backtest route after the model parses cleanly.

        Why not a model-level validator: a model-level validator that
        called ``logger.warning`` would fire on every model_validate
        including round-trip reads from disk, which would spam the
        operator log on every `/api/agent/status` poll. Keeping the
        warn-side as an EXPLICIT method lets the routes opt in at
        request time only.
        """
        delta = abs(self.w_r + self.w_s - 1.0)
        if delta > WEIGHT_SUM_TOLERANCE:
            logger.warning(
                "starting_weight_config: w_r + w_s = %.4f drifted from "
                "1.0 by %.4f (> %.4f tolerance); to_weights() will "
                "renormalise before downstream consumers see it",
                self.w_r + self.w_s,
                delta,
                WEIGHT_SUM_TOLERANCE,
            )

    def to_weights(self) -> Weights:
        """Project to the canonical :class:`Weights` 6-vector.

        Renormalises ``(w_r, w_s)`` if they drift from 1.0 (per the
        WARN-only contract); the 3-vector / 2-vector expansion is:

        * ``alpha → [α, (1-α)/2, (1-α)/2]`` (technical-led collapse)
        * ``beta  → [β, 1-β]``

        The resulting :class:`Weights` runs through ITS OWN PRD §4.1
        invariant check so a malformed projection (e.g. a future
        decomposition that drifts the per-vector sum) still raises.
        """
        total = self.w_r + self.w_s
        if total > 0.0:
            w_r = self.w_r / total
            w_s = self.w_s / total
        else:
            # Edge case: both 0.0. Fall back to 50/50 — the projection
            # is still well-defined and the brief's WARN-only rule
            # explicitly tolerates this case at the wire layer.
            w_r = 0.5
            w_s = 0.5
        remainder = 1.0 - self.alpha
        alpha_vec = [self.alpha, remainder / 2.0, remainder / 2.0]
        beta_vec = [self.beta, 1.0 - self.beta]
        return Weights(
            w_r=w_r,
            w_s=w_s,
            alpha=alpha_vec,
            beta=beta_vec,
            rho=self.rho,
        )

    @classmethod
    def from_weights(cls, label: str, weights: Weights) -> StartingWeightConfig:
        """Inverse projection — convenience for dashboard reads.

        The collapse is lossy: a 3-vector ``[0.4, 0.4, 0.2]`` collapses
        to ``alpha=0.4`` losing the α₂ vs α₃ asymmetry. This is the
        intended ergonomic trade — the dashboard surface is scalar.
        Round-trip ``cfg → weights → cfg'`` preserves ``w_r / w_s / rho``
        exactly and preserves ``alpha / beta`` iff the input weights
        were already in the technical-led collapse shape.
        """
        return cls(
            label=label,
            w_r=weights.w_r,
            w_s=weights.w_s,
            alpha=weights.alpha[0],
            beta=weights.beta[0],
            rho=weights.rho,
        )


# --------------------------------------------------------------------------- #
# Request envelopes
# --------------------------------------------------------------------------- #


class BacktestRunRequest(BaseModel):
    """``POST /api/backtest/run`` typed request body (T-B-037).

    All fields OPTIONAL — an empty body (or ``{}``) falls back to the
    canonical 4-config default sweep so the existing dashboard "RUN
    BACKTEST" button keeps working unchanged (backward-compat locked
    by the brief).

    Fields
    ------

    ``configs``
        List of :class:`StartingWeightConfig` to sweep over. Empty
        list → falls back to
        :data:`agent.backtest.sweep_runner.DEFAULT_SWEEP_WEIGHTS`.

    ``start_date`` / ``end_date``
        Optional cache-date window. Threaded through to the sweep
        runner via :class:`SweepConfig` — sprint_11 doesn't enforce
        them (the cache loader uses every available snapshot) but
        accepting them now keeps the wire stable so the sprint_12
        time-window override can land without a registry bump.

    ``operator_note``
        Free-form audit annotation. Persisted alongside the
        ``results.json`` start row so the operator can identify what
        they were trying when they read the file back. Not consumed
        by the sweep itself.
    """

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    configs: list[StartingWeightConfig] = Field(default_factory=list)
    operator_note: str | None = None


class AgentConfigureRequest(BaseModel):
    """``POST /api/agent/configure`` typed request body (T-B-037).

    Carries the :class:`StartingWeightConfig` the operator wants the
    next ``/api/agent/start`` to pick up. The route persists this to
    ``state/sandbox/agent_config.json`` atomically (temp + os.replace)
    and returns 202 — the running agent (if any) does NOT take effect
    immediately; the dashboard surfaces the staged config until the
    operator re-starts.

    Why a wrapper around a single field rather than the bare
    :class:`StartingWeightConfig`: the registry contract for the
    configure endpoint is a separate id (``agent_configure_request``)
    so a future ``threshold_overrides`` field can land additively
    without re-bumping the more widely-imported
    ``starting_weight_config`` schema.
    """

    model_config = ConfigDict(extra="forbid")

    starting_weights: StartingWeightConfig


# --------------------------------------------------------------------------- #
# Response envelopes
# --------------------------------------------------------------------------- #


class AgentConfigureResponse(BaseModel):
    """``POST /api/agent/configure`` 202 response — echoes the persisted
    config so the dashboard can update its local mirror without an
    extra GET round-trip.

    ``persisted_path`` is the absolute filesystem path of the on-disk
    snapshot the next ``/api/agent/start`` will rehydrate from. The
    dashboard renders it in the "staged config" pane so the operator
    can confirm the write landed where they expected.
    """

    model_config = ConfigDict(extra="forbid")

    starting_weights: StartingWeightConfig
    persisted_path: str
    status: Literal["accepted"] = "accepted"


class BacktestCancelResponse(BaseModel):
    """``POST /api/backtest/{run_id}/cancel`` 200 response.

    ``cancelled`` is True iff the cancel flag was newly set on the
    record (idempotent: a second cancel call on an already-cancelled
    run still returns 200 + cancelled=True; the flag is a latch).
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    cancelled: bool = True
    status: Literal["cancelling"] = "cancelling"


__all__ = [
    "WEIGHT_SUM_TOLERANCE",
    "AgentConfigureRequest",
    "AgentConfigureResponse",
    "BacktestCancelResponse",
    "BacktestRunRequest",
    "StartingWeightConfig",
]
