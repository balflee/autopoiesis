# Greek letters mirror PRD §4.1 / §6.6 notation; see agent/core/agent.py.
"""Weight updater — softmax-reparameterised gradient descent on the
6 fusion parameters (W_R, α₁, α₂, α₃, β₁, ρ).

PRD §4.2 + TECHNICAL_PLAN §4.2 specify a 2-layer hierarchical softmax
parameterisation so the three simplex constraints

* ``α₁ + α₂ + α₃ = 1``
* ``β₁ + β₂ = 1``
* ``W_R + W_S = 1``

are satisfied **structurally** rather than by projection / clipping.
Each simplex lives as unconstrained logits ``u`` in ℝⁿ; we apply
softmax to recover the probability vector. Pure NumPy, ~150 lines.

Phase 1 freeze (HARD RULE — brief acceptance criterion):

* ``β₁`` is **byte-identical** across every update (frozen to 0).
* ``W_R / W_S`` are **byte-identical** across every update.

Phase 1 trains only ``(α₁, α₂, α₃, ρ)`` per PRD §4.2 ("Phase 1: only
the Rational stream's α mix + Kelly scaler ρ train"). The W_R / W_S
freeze is *additional* to that spec but is the only sound choice when
β₁ is pinned to 0 — see ``delivery_report.md`` for the rationale.

Phase 3 (Master) + Phase 4 (Terminal) are normally frozen — mastery
requires committed weights per PRD §4.5. The update method short-
circuits and returns the input weights unchanged for those phases.

Desperate Mode (PRD §6.9, TP §4.7 "绝境觉醒"):

* Effective learning rate doubles: η×2.
* The Phase 3 Mastery freeze is LIFTED — β/ρ + α all train again
  because survival pressure overrides the committed-weights
  rationale. Phase 4 stays frozen even under Desperate.
* In Phase 1/2 the doubled rate applies on top of the normal-phase
  freeze list (β₁ remains frozen in Phase 1; everything else moves
  2× as fast).

Sprint_5 (T-B-009) extends the return surface: callers that need the
:class:`WeightDelta` audit record (DegradedMode tag, step L1, η used)
go through :meth:`update_with_delta`; legacy callers stay on
:meth:`update` which still returns only the new :class:`Weights`.

Look-ahead discipline
---------------------

The look-ahead auditor pays particular attention to this module. The
loss function MUST consume only decision-time features. PRD §6.8
flags ``settled_at``, ``resolved_at``, ``outcome``, and ``payout``
columns explicitly; we refuse keys matching those prefixes upfront
rather than silently consuming them.

EMA over feature history
------------------------

A naive per-tick SGD step would track every noisy feature spike. We
keep a per-instance EMA buffer with tau = 0.1 (10-tick effective
window) so the weight trajectory is smooth and reviewers can replay a
calibration sweep deterministically. The EMA state is internal — it
does NOT participate in the persisted Weights schema, which only
holds the post-update fusion parameters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np

from agent.core.state import Phase, Weights
from agent.engines.decision import RATIONAL_ENGINES, SENTIENT_ENGINES

# ── Constants ────────────────────────────────────────────────────────

# Default learning rate. Conservative — early calibration runs found
# η ≥ 0.1 made the softmax flap between corners of the α simplex on
# every other tick. η = 0.05 is the smallest value that still moves
# the weight trajectory measurably in a 100-tick window.
DEFAULT_LEARNING_RATE: Final[float] = 0.05

# Desperate-mode learning-rate multiplier per TP §4.7.
DESPERATE_LR_MULTIPLIER: Final[float] = 2.0

# EMA smoothing constant. ema ← τ·new + (1-τ)·ema. τ=0.1 ≈ 10-tick
# effective window — slow enough that a one-tick noise spike does not
# move the weights, fast enough that a real regime shift is captured
# inside the same phase.
DEFAULT_EMA_TAU: Final[float] = 0.1

# Maximum absolute logit magnitude. Without a clip, the softmax can
# collapse onto a single corner of the simplex (one weight ≈ 1.0,
# others ≈ 0.0) which forfeits the diversification ensemble + makes
# the constraint check effectively meaningless. ±5 keeps every weight
# in roughly [0.007, 0.993] post-softmax.
LOGIT_CLIP: Final[float] = 5.0

# PRD §6.8 forbidden feature column prefixes. The look-ahead auditor
# greps for these in this module — keeping them as a tuple here lets
# the auditor verify ALL four are checked.
_LOOKAHEAD_FORBIDDEN_PREFIXES: Final[tuple[str, ...]] = (
    "settled_at",
    "resolved_at",
    "outcome",
    "payout",
)
_LOOKAHEAD_FORBIDDEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^(" + "|".join(re.escape(p) for p in _LOOKAHEAD_FORBIDDEN_PREFIXES) + r")(_|$)"
)


class DegradedMode(StrEnum):
    """Per-update tag attached to :class:`WeightDelta` records.

    PRD §6.9 names the Desperate-Mode branch — η×2, β/ρ unlocked even in
    Phase 3 — and reflection / dashboard layers want to know WHY a
    particular gradient step had that posture. This enum is the
    on-the-wire discriminator carried on the decision_record JSON
    (``decision_record.v0.2.0.json`` adds an optional ``degraded_mode``
    enum field {none, desperate}).
    """

    NONE = "none"
    DESPERATE = "desperate"


@dataclass(frozen=True)
class WeightDelta:
    """Per-update audit record emitted alongside the new :class:`Weights`.

    The reflection engine + dashboard bridge both want a stable handle
    on (a) which mode the updater ran in this tick and (b) how big the
    step was. Without an explicit record they would have to diff the
    pre- and post-snapshots themselves; that diff is what this dataclass
    is.

    Fields
    ------

    ``mode``:
        :class:`DegradedMode` — ``DESPERATE`` only when the agent main
        loop passed ``desperate=True`` to :meth:`WeightUpdater.update`.

    ``effective_learning_rate``:
        The actual η used this tick. Equals ``learning_rate`` in normal
        mode, ``learning_rate * DESPERATE_LR_MULTIPLIER`` when desperate.

    ``phase``:
        The phase the update ran in. Captured here so a downstream
        consumer can pin a Desperate-mode step to Phase 3 without
        looking at the persisted tick payload.

    ``alpha_l1``, ``beta_l1``, ``w_l1``, ``rho_delta``:
        Step-size diagnostics — pre/post L1 deltas of the simplex
        vectors + |Δρ|. Surfaced in ``raw_features`` of the reflection
        prompt so the LLM can quote them ("α drifted 0.02 toward
        NBA").
    """

    mode: DegradedMode
    effective_learning_rate: float
    phase: Phase
    alpha_l1: float
    beta_l1: float
    w_l1: float
    rho_delta: float

# Engine names that index alpha[0..2] / beta[0..1] respectively. decision.py is
# the import-safe single source of truth (it imports only agent.core.state +
# agent.engines.base, neither of which imports back here — no cycle), so we
# DERIVE rather than re-hardcode: a slot rename then touches one definition site.
# Sprint_7 sport pivot: α[0] is the tennis technical engine (was nba_technical
# pre-pivot). Parity is locked by tests/agent/engines/test_engine_slot_parity.py.
_ALPHA_ENGINES = RATIONAL_ENGINES
_BETA_ENGINES = SENTIENT_ENGINES


def _assert_no_lookahead_keys(features: dict[str, float]) -> None:
    """Refuse feature keys matching the PRD §6.8 forbidden prefixes.

    The check is per-key prefix match — settled_at_outcome,
    payout_usd, resolved_at_block all rejected. The ML look-ahead
    auditor verifies this function is called from
    :meth:`WeightUpdater.update` before any gradient math runs.
    """
    bad = [k for k in features if _LOOKAHEAD_FORBIDDEN_RE.match(k)]
    if bad:
        raise ValueError(
            f"weight_updater refuses post-settlement features "
            f"(PRD §6.8): {sorted(bad)}"
        )


def _logit_from_simplex(p: np.ndarray) -> np.ndarray:
    """Inverse of softmax for a strictly-positive simplex vector.

    Softmax is invariant under additive shifts; we return logits
    centred at zero (subtract the mean) to keep the values bounded.
    Handles zero entries by replacing them with a tiny floor so log
    is finite — Phase 1 frozen β=[0, 1] needs this path.
    """
    safe = np.maximum(p, 1e-12)
    log_safe: np.ndarray = np.log(safe)
    return log_safe - float(log_safe.mean())


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax (subtract max before exp)."""
    shifted = logits - float(logits.max())
    exp = np.exp(shifted)
    total = float(exp.sum())
    return exp / total if total > 0.0 else np.full_like(exp, 1.0 / len(exp))


def _sigmoid(x: float) -> float:
    """Bounded sigmoid for the ρ scaler."""
    # Clip the input so exp(-x) stays finite for any (clipped) logit.
    x_clipped = max(-50.0, min(50.0, x))
    return 1.0 / (1.0 + math.exp(-x_clipped))


def _logit_scalar(p: float) -> float:
    """Inverse sigmoid with clipping so p ∈ {0, 1} maps to a finite
    logit. ρ is stored in [0, 1] after the engine clamp."""
    p_safe = min(1.0 - 1e-6, max(1e-6, p))
    return math.log(p_safe / (1.0 - p_safe))


def _gradient_from_features(
    *,
    names: tuple[str, ...],
    features: dict[str, float],
    suffix: str,
) -> np.ndarray:
    """Per-engine gradient signal derived from decision-time features.

    The convention: a feature key shaped ``"<engine>_<suffix>"`` carries
    the gradient for that engine. Missing keys ⇒ zero gradient (the
    engine had nothing new to learn this tick).

    Example: ``alpha_grad[i] = features.get(f"{names[i]}_quality", 0.0)``.

    Sign convention: HIGHER quality ⇒ HIGHER logit ⇒ MORE weight after
    softmax. The agent_loop computes "quality" as a decision-time
    signal — typically ``confidence * |score|`` from the engine's
    output — so the weight updater rewards engines whose signal had
    both magnitude and self-rated reliability that tick.
    """
    return np.array(
        [float(features.get(f"{n}_{suffix}", 0.0)) for n in names],
        dtype=np.float64,
    )


class WeightUpdater:
    """Runs the per-tick weight update.

    Stateful — owns the EMA buffer + the unconstrained logit
    parameters. The :class:`Weights` Pydantic model is the *external*
    snapshot; the internal logits are the *trainable* parameters.

    Construction parameters
    -----------------------

    ``learning_rate``:
        Base η before desperate-mode scaling. Default 0.05.

    ``ema_tau``:
        Smoothing constant for the per-feature EMA buffer. Default 0.1.

    The first call to :meth:`update` initialises the internal logits
    from the input :class:`Weights`; subsequent calls update logits in
    place and re-emit the snapshot.
    """

    name = "weight_updater"

    def __init__(
        self,
        *,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        ema_tau: float = DEFAULT_EMA_TAU,
    ) -> None:
        if learning_rate <= 0.0:
            raise ValueError(f"learning_rate must be > 0 (got {learning_rate})")
        if not 0.0 < ema_tau <= 1.0:
            raise ValueError(f"ema_tau must be in (0, 1] (got {ema_tau})")
        self._lr = learning_rate
        self._tau = ema_tau
        # EMA buffer: lazily allocated on first update so the keyset
        # comes from real call sites, not a hard-coded list that would
        # silently desync.
        self._ema: dict[str, float] = {}

    async def update(
        self,
        *,
        current: Weights,
        phase: Phase,
        features: dict[str, float],
        desperate: bool = False,
    ) -> Weights:
        """Run one SGD step on the 6 fusion parameters.

        Phase 1 freezes β₁ + W_R / W_S — only α and ρ train.
        Phase 2 trains all 6 parameters.
        Phase 3 (Master) normally returns ``current`` unchanged
        (mastery weights are committed per PRD §4.5) — but the
        Desperate-Mode branch (PRD §6.9, ``desperate=True``) UNLOCKS
        β / ρ and applies η×2 because survival pressure overrides the
        mastery freeze.
        Phase 4 (Terminal) always returns ``current`` unchanged — once
        Terminal Lucidity engages there is no learning left to do.

        ``features``:
            Decision-time feature dict. Keys ``settled_at*``,
            ``resolved_at*``, ``outcome*``, ``payout*`` are rejected
            upfront per PRD §6.8 (look-ahead discipline).
            Per-engine quality signal lives under
            ``"<engine_name>_quality"``; a global rho gradient lives
            under ``"rho_quality"``.

        ``desperate``:
            Doubles the effective learning rate (TP §4.7). The freeze
            list is unchanged in Phase 1/2 — the doubled rate only
            amplifies the channels that are NOT frozen. In Phase 3 the
            Desperate branch *unlocks* β/ρ on top of the η×2 boost
            (PRD §6.9 — committed weights are unfrozen because the
            normal-mode Mastery rationale no longer applies once
            survival is at stake).
        """
        new_weights, _ = await self.update_with_delta(
            current=current,
            phase=phase,
            features=features,
            desperate=desperate,
        )
        return new_weights

    async def update_from_settlement(
        self,
        *,
        current: Weights,
        phase: Phase,
        pnl_usd: float,
        size_usd: float,
        signal_scores: dict[str, float],
        bet_direction: float,
        desperate: bool = False,
    ) -> Weights:
        """Run one settlement-PnL gradient step (Task L3, Plan 2).

        Unlike :meth:`update` — which consumes decision-time features and
        REFUSES post-settlement keys (PRD §6.8 look-ahead discipline) — this
        entrypoint is the *settlement* channel: the gradient signal IS the
        realised win/loss, which the per-tick decision path is forbidden to
        see. It is called from the settlement poller (a non-``features/``
        location) so the look-ahead auditor stays clean.

        Credit assignment (direction-aware, Plan-2 Round-3/5 fix)
        --------------------------------------------------------
        A NO bet is taken when the fused score is negative, so the engines
        that drove a *correct* NO bet had NEGATIVE scores. The naive
        ``sign(pnl)*score`` would PUNISH them. We multiply in the bet's
        direction so we reward engines whose signal AGREED with the bet:

            engine_quality = sign(pnl) * bet_direction * signal_score[engine]

        ``bet_direction`` is ``+1`` for a YES bet, ``-1`` for a NO bet —
        mandatory (the caller never defaults it; see the settlement adapter).

        Risk scaler (signed, Plan-2 Round-5/6 fix)
        -----------------------------------------
        ``rho_quality = tanh(pnl_usd / max(size_usd, 1e-6))`` is SIGNED and
        bounded. The gradient ADDS it to the rho logit, so a positive value
        raises risk and a negative value cuts it: win → raise/hold risk,
        loss → cut risk. ``size_usd`` is required because the normaliser is
        per-stake.

        Stream weights (Plan-2 Round-3 MED-1 fix)
        ----------------------------------------
        ``w_r / w_s`` only train when the gradient layer sees
        ``rational_stream_quality`` / ``sentient_stream_quality``. We
        aggregate the rational group (``tennis_technical`` +
        ``market_momentum`` + ``smart_money``) and the sentient group
        (``sentiment_llm`` + ``crowd_volume``) into those two keys.

        Honors the phase freeze list + ``desperate`` LR exactly like
        :meth:`update` (delegates to it).
        """
        pnl_sign = 0.0 if pnl_usd == 0.0 else math.copysign(1.0, pnl_usd)

        features: dict[str, float] = {}
        for engine, score in signal_scores.items():
            features[f"{engine}_quality"] = pnl_sign * bet_direction * float(score)

        # Stream-level quality: aggregate each group's per-engine credit so
        # the W_R / W_S gradient (which reads ONLY these two keys) trains.
        rational_quality = sum(
            features.get(f"{e}_quality", 0.0) for e in _ALPHA_ENGINES
        )
        sentient_quality = sum(
            features.get(f"{e}_quality", 0.0) for e in _BETA_ENGINES
        )
        features["rational_stream_quality"] = rational_quality
        features["sentient_stream_quality"] = sentient_quality

        # Signed, bounded risk gradient — see docstring.
        features["rho_quality"] = math.tanh(pnl_usd / max(size_usd, 1e-6))

        return await self.update(
            current=current,
            phase=phase,
            features=features,
            desperate=desperate,
        )

    async def update_with_delta(
        self,
        *,
        current: Weights,
        phase: Phase,
        features: dict[str, float],
        desperate: bool = False,
    ) -> tuple[Weights, WeightDelta]:
        """Same as :meth:`update` but also returns a :class:`WeightDelta`.

        The delta carries the ``DegradedMode`` tag the reflection +
        dashboard layers consume. Existing call sites that only want
        the new weights stay on :meth:`update`; sprint_5+ callers that
        need the audit record use this variant.
        """
        # Look-ahead chokepoint MUST run first so a leaky caller fails
        # fast before any side effects (EMA update) happen.
        _assert_no_lookahead_keys(features)

        mode = DegradedMode.DESPERATE if desperate else DegradedMode.NONE
        eta = self._lr * (DESPERATE_LR_MULTIPLIER if desperate else 1.0)

        # Phase 3 / 4 — full freeze in normal mode. The Desperate branch
        # in Phase 3 unlocks β/ρ + α per PRD §6.9; Phase 4 stays frozen
        # (Terminal Lucidity is a one-way street — there is no learning
        # left to do).
        if phase == Phase.PHASE_4_TERMINAL or (
            phase == Phase.PHASE_3_MASTER and not desperate
        ):
            empty_delta = WeightDelta(
                mode=mode,
                effective_learning_rate=eta,
                phase=phase,
                alpha_l1=0.0,
                beta_l1=0.0,
                w_l1=0.0,
                rho_delta=0.0,
            )
            return current, empty_delta

        # ── EMA buffer ────────────────────────────────────────────────
        # Initialised lazily on the first key we see; subsequent keys
        # join with their first observed value (no warm-up bias). The
        # gradient helpers are read-only on the buffer (only `.get`),
        # so the older defence-copy was wasted work — drop it.
        for k, v in features.items():
            self._ema[k] = self._tau * float(v) + (1.0 - self._tau) * self._ema.get(k, float(v))
        smoothed: dict[str, float] = self._ema

        # ── Build current logits from the snapshot ────────────────────
        u_alpha = _logit_from_simplex(np.asarray(current.alpha, dtype=np.float64))
        u_beta = _logit_from_simplex(np.asarray(current.beta, dtype=np.float64))
        u_w = _logit_from_simplex(np.asarray([current.w_r, current.w_s], dtype=np.float64))
        u_rho = _logit_scalar(max(0.0, min(1.0, current.rho)))

        # ── Gradients from smoothed features ──────────────────────────
        grad_alpha = _gradient_from_features(
            names=_ALPHA_ENGINES, features=smoothed, suffix="quality"
        )
        grad_beta = _gradient_from_features(
            names=_BETA_ENGINES, features=smoothed, suffix="quality"
        )
        # W_R / W_S gradient: stream-level quality signal. Convention:
        # ``rational_stream_quality`` raises W_R; ``sentient_stream_quality``
        # raises W_S. Absence ⇒ zero gradient (no update this tick).
        grad_w = np.array(
            [
                float(smoothed.get("rational_stream_quality", 0.0)),
                float(smoothed.get("sentient_stream_quality", 0.0)),
            ],
            dtype=np.float64,
        )
        grad_rho = float(smoothed.get("rho_quality", 0.0))

        # ── Apply gradient step in logit space ────────────────────────
        # α + ρ always train in Phase 1 + 2 (Phase 3 normal-mode + Phase 4
        # short-circuited above). Phase 1 additionally freezes β + W —
        # we skip those gradients entirely and re-emit the frozen input
        # so the softmax round-trip cannot perturb the byte-identical
        # guarantee. Phase 3 Desperate matches Phase 2: ALL six channels
        # unlock + η×2 (PRD §6.9).
        u_alpha = np.clip(u_alpha + eta * grad_alpha, -LOGIT_CLIP, LOGIT_CLIP)
        u_rho = max(-LOGIT_CLIP, min(LOGIT_CLIP, u_rho + eta * grad_rho))
        new_alpha = _softmax(u_alpha)
        new_rho = _sigmoid(u_rho)

        if phase == Phase.PHASE_1_INFANCY:
            new_beta = np.asarray(current.beta, dtype=np.float64)
            new_w = np.asarray([current.w_r, current.w_s], dtype=np.float64)
        else:
            u_beta = np.clip(u_beta + eta * grad_beta, -LOGIT_CLIP, LOGIT_CLIP)
            u_w = np.clip(u_w + eta * grad_w, -LOGIT_CLIP, LOGIT_CLIP)
            new_beta = _softmax(u_beta)
            new_w = _softmax(u_w)

        # ── Renormalise + emit ────────────────────────────────────────
        # Softmax is exact up to fp error; the Pydantic validator
        # accepts ±1e-6, so a final renormalise step is defence in
        # depth against accumulating drift over thousands of ticks.
        new_alpha = new_alpha / float(new_alpha.sum())
        new_beta = new_beta / float(new_beta.sum())
        new_w = new_w / float(new_w.sum())

        new_weights = Weights(
            w_r=float(new_w[0]),
            w_s=float(new_w[1]),
            alpha=[float(x) for x in new_alpha],
            beta=[float(x) for x in new_beta],
            rho=float(new_rho),
        )

        delta = WeightDelta(
            mode=mode,
            effective_learning_rate=eta,
            phase=phase,
            alpha_l1=float(
                np.abs(np.asarray(new_weights.alpha) - np.asarray(current.alpha)).sum()
            ),
            beta_l1=float(
                np.abs(np.asarray(new_weights.beta) - np.asarray(current.beta)).sum()
            ),
            w_l1=float(abs(new_weights.w_r - current.w_r) + abs(new_weights.w_s - current.w_s)),
            rho_delta=float(abs(new_weights.rho - current.rho)),
        )
        return new_weights, delta


__all__ = [
    "DEFAULT_EMA_TAU",
    "DEFAULT_LEARNING_RATE",
    "DESPERATE_LR_MULTIPLIER",
    "LOGIT_CLIP",
    "DegradedMode",
    "WeightDelta",
    "WeightUpdater",
]
