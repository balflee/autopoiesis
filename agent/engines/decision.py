# Greek letters mirror PRD §4.1 / §6.6 notation; see agent/core/agent.py.
"""Decision engine — 2-layer fusion + Kelly + 4-constraint bet sizing.

T-B-003 ships the production body of step 4 of the agent_loop
(TECHNICAL_PLAN §4.1):

1. **Rational stream**::

       raw_R = α₁·Tennis·conf_Tennis
             + α₂·MM·conf_MM
             + α₃·SM·conf_SM

   Each engine's score is weighted by its self-rated confidence
   BEFORE the α mix so a high-confidence small-edge signal is not
   drowned by a low-confidence large-edge one.

2. **Sentient stream**::

       raw_S = β₁·LLM·conf_LLM + β₂·CV·conf_CV

3. **Fused score**::

       fused = W_R·raw_R + W_S·raw_S        # ∈ [-1, 1]

   Sign chooses the side (YES if fused > 0, NO if fused < 0).

4. **Kelly fraction (fractional)**::

       k = clamp(|fused| / (1 - |fused|), 0, 1)

5. **ρ_effective**::

       ρ_eff = clamp(ρ, 0, 1)

   Weights.rho ∈ [-1, 1] per the persisted schema, but PRD §6.6 only
   admits a non-negative Kelly scaler — a negative ρ would invert the
   bet direction at the very end of the pipeline, which is the failure
   mode this clamp prevents.

6. **4-constraint min** (PRD §6.6)::

       desired      = ρ_eff · k · mean_confidence · bankroll
       breath_cap   = breath · MAX_BREATH_RISK_PCT / CONVERSION_RATE
       bankroll_cap = bankroll · bet_size_cap_fraction   # 0.30 / 0.50
       liquidity_cap = liquidity_cap_usd

       size = min(desired, breath_cap, bankroll_cap, liquidity_cap)

   ``bet_size_cap_fraction`` is **0.30** in normal mode and **0.50**
   in Desperate Mode (TP §4.7). The flag is an input — the decision
   engine never decides whether desperate mode is on; that's the
   responsibility of :mod:`agent.core.lifecycle` reading on-chain
   BREATH against ``desperate_threshold``.

7. **NO_BET fallthrough**: any of {``size < min_bet_size``, ``k == 0``,
   ``mean_confidence < min_confidence``, fused == 0, missing engine
   signal} routes to a NO_BET with a structured ``no_bet_reason`` so
   the reflection layer + Track D dashboard can render WHY.

Both branches emit an :class:`agent.core.state.Action`. Both BET and
NO_BET consume BREATH downstream (PRD §6 — NO_BET is NOT a free skip).

NB: the decision engine itself is *pure* — it never calls the chain,
never enqueues an order, and never burns BREATH. Those side effects
are step 5 of the loop (:func:`agent.core.agent.agent_loop`). Pure
math here means tests don't need mocks.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Final

from agent.core.state import Action, ActionKind, Side
from agent.engines.base import EngineSignal

# Engine-name constants — keep the dict-key strings in one place so a
# typo in the agent_loop fanout surfaces at import time, not at runtime.
#
# Sprint_7 sport pivot (PRD §15 已决 #8): the canonical α₁ stream is
# TENNIS_TECHNICAL post-pivot. The NBA_TECHNICAL constant + the
# nba_technical.py engine module are deleted in lockstep so there is
# one unambiguous source of truth for α₁'s identity.
#
# ⚠️ SLOT NAME ≠ BACKTEST PAYLOAD — read before trusting one of these names.
# These 5 keys name GENUINE production engines (smart_money = on-chain
# smart-money WALLET alignment, sentiment_llm = Gemini LLM sentiment,
# crowd_volume = Reddit volume; see agent/engines/<name>.py). BUT in the path
# we actually run — backtest, and prod when the RealSignalSource flag is on —
# those live signals don't exist, so agent/backtest/real_signal_source.py
# SUBSTITUTES a Sackmann/CLOB proxy into each slot (key unchanged, payload
# differs): smart_money -> surface advantage, sentiment_llm -> head-to-head,
# crowd_volume -> rest/recency. => In ANY backtest artifact / sim / the 能学
# demo, a slot key means the SLOT (carrying a Sackmann proxy), NOT its namesake
# engine. Don't repeat the "smart_money == smart money" misread (it isn't, here).
TENNIS_TECHNICAL: Final[str] = "tennis_technical"
MARKET_MOMENTUM: Final[str] = "market_momentum"
SMART_MONEY: Final[str] = "smart_money"
SENTIMENT_LLM: Final[str] = "sentiment_llm"
CROWD_VOLUME: Final[str] = "crowd_volume"

# Ordered tuples — used to verify alpha[i] / beta[i] index ↔ engine
# name mapping in tests. Re-ordering these is a BREAKING change.
RATIONAL_ENGINES: Final[tuple[str, str, str]] = (
    TENNIS_TECHNICAL,
    MARKET_MOMENTUM,
    SMART_MONEY,
)
SENTIENT_ENGINES: Final[tuple[str, str]] = (
    SENTIMENT_LLM,
    CROWD_VOLUME,
)

# Bet-size cap fractions per TP §4.7. The desperate-mode flip is an
# input to :meth:`DecisionEngine.decide`; this module does NOT decide
# the threshold (lifecycle does).
NORMAL_BET_SIZE_CAP: Final[float] = 0.30
DESPERATE_BET_SIZE_CAP: Final[float] = 0.50

# PRD §6 BREATH economy constants. Sourced from sim.params defaults
# (T-C-001/002) — when calibration ships selected_params.json a
# follow-up task will pipe these through; for now they're the placebos
# matching PRD §6.7 placeholders so the formula is exercisable.
DEFAULT_MAX_BREATH_RISK_PCT: Final[float] = 0.30  # 30% of breath per bet
DEFAULT_CONVERSION_RATE: Final[float] = 1.0  # 1 BREATH = 1 USD (placeholder)
DEFAULT_MIN_BET_SIZE_USD: Final[float] = 5.0  # matches sim.params.min_bet_size
DEFAULT_MIN_CONFIDENCE: Final[float] = 0.05  # below this, abstain

# NO_BET reason strings — bound here so Track D can switch on them
# without parsing free-form text.
NO_BET_MISSING_SIGNAL: Final[str] = "missing_engine_signal"
NO_BET_BELOW_MIN_SIZE: Final[str] = "size_below_min_bet"
NO_BET_LOW_CONFIDENCE: Final[str] = "mean_confidence_below_threshold"
NO_BET_ZERO_KELLY: Final[str] = "zero_kelly_fraction"
NO_BET_NEUTRAL_FUSED: Final[str] = "fused_score_neutral"

# Value-betting mode (realism v3, plan 2026-06-11-value-betting-physics).
# ``p_model = clamp(price + kappa * fused, 0, 1)`` — the model anchors on the
# MARKET price and tilts by the fused signal. A (1+fused)/2 anchor would make
# zero-signal imply p=0.5 and systematically fade every non-0.5 price on no
# information; the price anchor makes zero signal ⇒ zero edge ⇒ abstain.
DEFAULT_KAPPA: Final[float] = 0.25
NO_BET_NO_EDGE: Final[str] = "edge_below_min"
NO_BET_PRICE_FLOOR: Final[str] = "effective_price_below_floor"


@dataclass(frozen=True)
class GateDiagnostics:
    """What the value-mode edge gate saw on the last :meth:`decide` call.

    A9 storm kit (plan 2026-06-13): the loop stamps these five values
    onto the BetRecord at placement so the post-hoc counterfactual
    ledger can replay the gate AS APPLIED. Value-mode only — legacy
    mode has no min-edge gate, so the field stays ``None`` there and on
    every pre-edge abstain path (missing signals / low confidence).
    """

    storm: float
    edge_abs: float
    min_edge_base: float
    gamma: float
    eff_min_edge: float


@dataclass(frozen=True)
class FusionResult:
    """Intermediate breakdown emitted by :func:`_fuse_signals`.

    Public so the agent_loop step 9 can persist these as raw_features
    on the TickPayload + Track D dashboard can render the dual-engine
    meter (PRD §8) without recomputing the fusion math.
    """

    raw_rational: float
    raw_sentient: float
    fused: float
    mean_confidence: float


class DecisionEngine:
    """Owns the fusion + bet-sizing arithmetic.

    Stateless — every parameter that affects the output is passed in
    via :meth:`decide`. The async signature is preserved so the
    agent_loop's ``await`` machinery is unchanged; the body itself does
    no IO and could be sync. Async also reserves room for a future
    LLM-driven veto step at the fusion boundary (PRD §4.4) without a
    second interface migration.
    """

    name = "decision"

    def __init__(
        self,
        *,
        max_breath_risk_pct: float = DEFAULT_MAX_BREATH_RISK_PCT,
        conversion_rate: float = DEFAULT_CONVERSION_RATE,
        min_bet_size_usd: float = DEFAULT_MIN_BET_SIZE_USD,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        min_edge: float = 0.0,
        kappa: float = DEFAULT_KAPPA,
        kappa_xm: float = 0.0,
        entry_price_floor: float | None = None,
        gate_storm_sensitivity: float = 0.0,
        risk_storm_sensitivity: float = 0.0,
        exploration_epsilon: float = 0.0,
        exploration_rng: random.Random | None = None,
    ) -> None:
        if max_breath_risk_pct <= 0.0 or max_breath_risk_pct > 1.0:
            raise ValueError(
                f"max_breath_risk_pct must be in (0, 1] (got {max_breath_risk_pct})"
            )
        if conversion_rate <= 0.0:
            raise ValueError(f"conversion_rate must be > 0 (got {conversion_rate})")
        if min_bet_size_usd < 0.0:
            raise ValueError(
                f"min_bet_size_usd must be ≥ 0 (got {min_bet_size_usd})"
            )
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0, 1] (got {min_confidence})"
            )
        if not 0.0 <= min_edge <= 1.0:
            raise ValueError(f"min_edge must be in [0, 1] (got {min_edge})")
        if kappa <= 0.0 or kappa > 1.0:
            raise ValueError(f"kappa must be in (0, 1] (got {kappa})")
        if not 0.0 <= kappa_xm <= 1.0:
            raise ValueError(f"kappa_xm must be in [0, 1] (got {kappa_xm})")
        if entry_price_floor is not None and not 0.0 <= entry_price_floor < 1.0:
            raise ValueError(
                f"entry_price_floor must be in [0, 1) (got {entry_price_floor})"
            )
        for label, gamma in (
            ("gate_storm_sensitivity", gate_storm_sensitivity),
            ("risk_storm_sensitivity", risk_storm_sensitivity),
        ):
            if not math.isfinite(gamma) or abs(gamma) > 1.0:
                raise ValueError(
                    f"{label} must be finite with |x| <= 1 (got {gamma})"
                )
        if not 0.0 <= exploration_epsilon <= 1.0:
            raise ValueError(
                f"exploration_epsilon must be in [0, 1] (got {exploration_epsilon})"
            )
        self._max_breath_risk_pct = max_breath_risk_pct
        self._conversion_rate = conversion_rate
        self._min_bet_size_usd = min_bet_size_usd
        self._min_confidence = min_confidence
        self._min_edge = min_edge
        self._kappa = kappa
        self._kappa_xm = kappa_xm
        self._entry_price_floor = entry_price_floor
        self._gate_storm_sensitivity = gate_storm_sensitivity
        self._risk_storm_sensitivity = risk_storm_sensitivity
        # Exploration floor (Active Survival Hand-1, Task 4). When the agent
        # would freeze (abstain) on an EXPLORABLE no-bet, a flat-stake probe
        # fires with probability ``epsilon`` so the policy keeps sampling
        # instead of dying doing nothing. The gate is closed unless BOTH
        # ``epsilon > 0`` AND ``rng is not None`` — the live path + frozen
        # baseline pass rng=None so the decision stays byte-identical (the
        # RNG is consumed ONLY inside the explore branch). ``epsilon=0`` is
        # the default ⇒ every existing caller is byte-unchanged.
        self._exploration_epsilon = exploration_epsilon
        self._exploration_rng = exploration_rng
        # Observer telemetry, read by the loop immediately post-decide
        # (single-threaded per-life engine). Cleared at decide() entry.
        self.last_gate_diagnostics: GateDiagnostics | None = None

    async def decide(
        self,
        *,
        signals: dict[str, EngineSignal],
        weights_alpha: tuple[float, float, float],
        weights_beta: tuple[float, float],
        w_r: float,
        w_s: float,
        rho: float,
        bankroll_usd: float,
        breath: float,
        liquidity_cap_usd: float,
        market_id: str,
        desperate: bool = False,
        price: float | None = None,
        storm: float = 0.0,
        cross_market_signal: float = 0.0,
    ) -> Action:
        """Run fusion + bet sizing for one tick.

        Returns either a BET :class:`Action` (with market_id, side,
        size_usd, edge_pct) or a NO_BET :class:`Action` (with a
        structured ``no_bet_reason``).

        ``price`` (optional, default ``None`` = the legacy signal-betting
        mode, byte-identical to the pre-value behavior): the market YES-mid
        at decision time. When given, the engine runs VALUE mode —
        ``p_model = clamp(price + kappa*fused)``, side = sign of the price
        edge, ``min_edge`` gate, side-aware effective-price floor, and
        odds-aware Kelly. ``edge_pct`` then carries the true price edge
        (|fused| in legacy mode).

        ``storm`` (A9 regime percept, default 0.0 = byte-identical):
        normalized to finite [0, 1]; tightens the value-mode edge gate by
        ``gate_storm_sensitivity·storm`` and scales ``rho_eff`` by
        ``(1 − risk_storm_sensitivity·storm)``. With both sensitivities
        at their 0.0 defaults the arithmetic is the identity.
        """
        # A9: normalize storm + clear stale diagnostics BEFORE any
        # early return (r7 L-4 — pre-edge abstains must never leave a
        # previous call's gate snapshot behind).
        if not math.isfinite(storm):
            storm = 0.0
        storm = max(0.0, min(1.0, storm))
        # B′: sanitize cross_market_signal — non-finite → 0.0, then clamp
        # to [-1, 1] (mirrors storm sanitization).
        if not math.isfinite(cross_market_signal):
            cross_market_signal = 0.0
        cross_market_signal = max(-1.0, min(1.0, cross_market_signal))
        self.last_gate_diagnostics = None

        # ── 1. Missing-signal guard ───────────────────────────────────
        # Every engine must have produced a signal — a missing one is
        # a topology error (engine crashed mid-fanout) so we route to
        # NO_BET rather than zero-imputing.
        missing = [
            n
            for n in (*RATIONAL_ENGINES, *SENTIENT_ENGINES)
            if n not in signals
        ]
        if missing:
            return Action(
                kind=ActionKind.NO_BET,
                no_bet_reason=f"{NO_BET_MISSING_SIGNAL}:{','.join(missing)}",
            )

        # ── 2. Fuse the 5 engines into a single signed edge ────────────
        fusion = _fuse_signals(
            signals=signals,
            alpha=weights_alpha,
            beta=weights_beta,
            w_r=w_r,
            w_s=w_s,
        )

        # ── 3. Confidence floor ───────────────────────────────────────
        if fusion.mean_confidence < self._min_confidence:
            # Explorable abstain (Task 4): resolve the value-mode side from
            # the price edge so a flat-stake probe can keep the policy
            # sampling. ``_value_side`` returns None in legacy mode (no
            # price) or on a neutral edge ⇒ the probe is skipped.
            return self._explore_or(
                Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=NO_BET_LOW_CONFIDENCE,
                ),
                side=_value_side(
                    price=price,
                    fused=fusion.fused,
                    kappa=self._kappa,
                    kappa_xm=self._kappa_xm,
                    cross_market_signal=cross_market_signal,
                ),
                market_id=market_id,
                bankroll_usd=bankroll_usd,
                breath=breath,
                liquidity_cap_usd=liquidity_cap_usd,
            )

        # ── 4. Direction + Kelly ──────────────────────────────────────
        if price is None:
            # Legacy signal-betting mode — byte-identical to the
            # pre-value behavior: side = sign(fused), even-money Kelly.
            if fusion.fused == 0.0:
                return Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=NO_BET_NEUTRAL_FUSED,
                )
            side = Side.YES if fusion.fused > 0.0 else Side.NO
            edge_abs = abs(fusion.fused)
            kelly = _kelly_fraction(edge_abs)
        else:
            # ── 4v. Value mode: market prior + signal tilt ────────────
            # p_model anchors on the PRICE so zero signal ⇒ zero edge ⇒
            # abstain (a (1+fused)/2 anchor would systematically fade
            # favorites on no information).
            p_model = max(
                0.0,
                min(
                    1.0,
                    price
                    + self._kappa * fusion.fused
                    + self._kappa_xm * cross_market_signal,
                ),
            )
            edge_yes = p_model - price
            if edge_yes == 0.0:
                return Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=NO_BET_NEUTRAL_FUSED,
                )
            side = Side.YES if edge_yes > 0.0 else Side.NO
            edge_abs = abs(edge_yes)
            eff = price if side is Side.YES else 1.0 - price
            if (
                self._entry_price_floor is not None
                and eff < self._entry_price_floor
            ):
                return Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=f"{NO_BET_PRICE_FLOOR}:{eff:.4f}",
                )
            # A9: the storm-conditional gate (γ=0 ⇒ x + 0·storm == x).
            # Diagnostics are populated BEFORE the gate fires so gated
            # abstains also record what the gate saw.
            eff_min_edge = max(
                0.0, self._min_edge + self._gate_storm_sensitivity * storm
            )
            self.last_gate_diagnostics = GateDiagnostics(
                storm=storm,
                edge_abs=edge_abs,
                min_edge_base=self._min_edge,
                gamma=self._gate_storm_sensitivity,
                eff_min_edge=eff_min_edge,
            )
            if edge_abs < eff_min_edge:
                return self._explore_or(
                    Action(
                        kind=ActionKind.NO_BET,
                        no_bet_reason=f"{NO_BET_NO_EDGE}:{edge_abs:.4f}",
                    ),
                    side=side,
                    market_id=market_id,
                    bankroll_usd=bankroll_usd,
                    breath=breath,
                    liquidity_cap_usd=liquidity_cap_usd,
                )
            kelly = _value_kelly_fraction(edge=edge_abs, effective_price=eff)
        if kelly == 0.0:
            return self._explore_or(
                Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=NO_BET_ZERO_KELLY,
                ),
                side=side,
                market_id=market_id,
                bankroll_usd=bankroll_usd,
                breath=breath,
                liquidity_cap_usd=liquidity_cap_usd,
            )

        # ── 5. 4-constraint min ───────────────────────────────────────
        # A9: storm scales the Kelly damper (γ2=0 ⇒ identity).
        rho_eff = max(
            0.0, min(1.0, rho * (1.0 - self._risk_storm_sensitivity * storm))
        )
        desired = rho_eff * kelly * fusion.mean_confidence * bankroll_usd
        bet_size_cap = (
            DESPERATE_BET_SIZE_CAP if desperate else NORMAL_BET_SIZE_CAP
        )
        size = self._clamped_size(
            desired=desired,
            breath=breath,
            max_breath_risk_pct=self._max_breath_risk_pct,
            conversion_rate=self._conversion_rate,
            bankroll_usd=bankroll_usd,
            bet_size_cap=bet_size_cap,
            liquidity_cap_usd=liquidity_cap_usd,
        )

        # ── 6. Min-bet floor — micro-bets are NO_BET ──────────────────
        # ``size <= 0`` catches the rho_eff=0 / kelly=0 / liquidity=0 cases
        # which would otherwise route a zero-size BET into the Action
        # validator (which rejects them with size_usd > 0).
        if size <= 0.0 or size < self._min_bet_size_usd:
            return self._explore_or(
                Action(
                    kind=ActionKind.NO_BET,
                    no_bet_reason=f"{NO_BET_BELOW_MIN_SIZE}:{size:.4f}",
                ),
                side=side,
                market_id=market_id,
                bankroll_usd=bankroll_usd,
                breath=breath,
                liquidity_cap_usd=liquidity_cap_usd,
            )

        # ── 7. BET ────────────────────────────────────────────────────
        return Action(
            kind=ActionKind.BET,
            market_id=market_id,
            side=side,
            size_usd=size,
            edge_pct=edge_abs,
        )

    def _clamped_size(
        self,
        *,
        desired: float,
        breath: float,
        max_breath_risk_pct: float,
        conversion_rate: float,
        bankroll_usd: float,
        bet_size_cap: float,
        liquidity_cap_usd: float,
    ) -> float:
        """The PRD §6.6 4-constraint clamp, shared by BOTH the normal BET
        path and the exploration probe.

        ``size = min(desired, breath_cap, bankroll_cap, liquidity_cap)``.
        Extracting it keeps the normal path BYTE-IDENTICAL (same operands,
        same order) while letting the exploration branch reuse the exact
        same caps with a FLAT ``desired`` stake.
        """
        breath_cap = breath * max_breath_risk_pct / conversion_rate
        bankroll_cap = bankroll_usd * bet_size_cap
        liquidity_cap = max(0.0, liquidity_cap_usd)
        return min(desired, breath_cap, bankroll_cap, liquidity_cap)

    def _explore_or(
        self,
        nobet_action: Action,
        *,
        side: Side | None,
        market_id: str,
        bankroll_usd: float,
        breath: float,
        liquidity_cap_usd: float,
    ) -> Action:
        """Exploration floor: with probability ``epsilon`` turn an
        EXPLORABLE abstain into a flat-stake probe; else return the
        original NO_BET.

        Gate (byte-identical OFF guarantee): explore ONLY if
        ``epsilon > 0`` AND ``rng is not None``. The RNG is consumed ONLY
        inside this branch, so ``epsilon=0`` / ``rng=None`` never draws ⇒
        the decision is identical to the frozen baseline.

        Probe sizing uses a FLAT minimum stake (``min_bet_size_usd``) as
        ``desired`` — NOT Kelly, which is undefined / ~0 at the no-edge and
        price-floor abstains and would collapse the probe. The same
        4-constraint clamp the normal path uses then applies; the probe is
        emitted only when the clamped size clears the min-bet floor (so a
        sub-floor liquidity cap correctly rejects it). ``side is None``
        (legacy mode / neutral edge) ⇒ no resolvable leg ⇒ NO_BET.
        """
        if self._exploration_epsilon <= 0.0 or self._exploration_rng is None:
            return nobet_action
        if side is None:
            return nobet_action
        if self._exploration_rng.random() >= self._exploration_epsilon:
            return nobet_action
        size = self._clamped_size(
            desired=self._min_bet_size_usd,
            breath=breath,
            max_breath_risk_pct=self._max_breath_risk_pct,
            conversion_rate=self._conversion_rate,
            bankroll_usd=bankroll_usd,
            # Normal-mode cap (0.30); a flat min stake never approaches it,
            # but it keeps the clamp identical in shape to the BET path.
            bet_size_cap=NORMAL_BET_SIZE_CAP,
            liquidity_cap_usd=liquidity_cap_usd,
        )
        if size < self._min_bet_size_usd or size <= 0.0:
            return nobet_action
        return Action(
            kind=ActionKind.BET,
            market_id=market_id,
            side=side,
            size_usd=size,
            edge_pct=0.0,
        )


# ---------------------------------------------------------------------------
# Pure helpers — module-level so tests can exercise the math directly
# without instantiating the engine.
# ---------------------------------------------------------------------------


def _fuse_signals(
    *,
    signals: dict[str, EngineSignal],
    alpha: tuple[float, float, float],
    beta: tuple[float, float],
    w_r: float,
    w_s: float,
) -> FusionResult:
    """2-layer fusion with confidence-weighted engine scores.

    Each engine's score is multiplied by its self-rated confidence
    before the α / β mix. This means a confident small-edge signal
    contributes more to the stream output than an over-eager
    low-confidence large-edge one — matching PRD §4.1's intent that
    confidence be a first-class fusion input, not just a post-hoc
    sizing knob.
    """
    rational = sum(
        alpha[i] * signals[RATIONAL_ENGINES[i]].score * signals[RATIONAL_ENGINES[i]].confidence
        for i in range(3)
    )
    sentient = sum(
        beta[i] * signals[SENTIENT_ENGINES[i]].score * signals[SENTIENT_ENGINES[i]].confidence
        for i in range(2)
    )
    fused = w_r * rational + w_s * sentient
    # mean_confidence used by Kelly + the confidence floor — flat mean
    # over the 5 engines so every channel contributes equally to the
    # sizing decision (the α/β/W mix already handles their per-channel
    # influence on direction).
    confs = [
        signals[n].confidence for n in (*RATIONAL_ENGINES, *SENTIENT_ENGINES)
    ]
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return FusionResult(
        raw_rational=rational,
        raw_sentient=sentient,
        fused=fused,
        mean_confidence=mean_conf,
    )


def _kelly_fraction(edge_abs: float) -> float:
    """Fractional-Kelly proxy: ``k = clamp(|e| / (1 - |e|), 0, 1)``.

    For a binary outcome with no fee and even payoff (b=1), classical
    Kelly says bet f* = 2p - 1 ≈ edge when edge is small. The formula
    ``e/(1-e)`` is the standard fractional-Kelly proxy used in PRD
    §4.1 / §6.6 — equivalent for small edges, saturating to 1 as the
    edge approaches 1.

    Returns 0 if edge is non-positive or NaN-ish.
    """
    if edge_abs <= 0.0:
        return 0.0
    # ``edge_abs >= 1`` would divide by zero; saturate to 1.0.
    if edge_abs >= 1.0:
        return 1.0
    return min(1.0, edge_abs / (1.0 - edge_abs))


def _value_kelly_fraction(*, edge: float, effective_price: float) -> float:
    """Odds-aware Kelly for a binary leg costing ``q = effective_price``.

    With model win-probability ``p = q + edge`` and payout odds
    ``b = (1-q)/q``, classical Kelly ``f* = (p*b - (1-p))/b`` reduces to
    ``f* = edge / (1 - q)``. Clamped to [0, 1]; non-positive edge ⇒ 0;
    ``q >= 1`` saturates to 1 (degenerate — the effective-price floor
    gate fires long before this in practice).
    """
    if edge <= 0.0:
        return 0.0
    if effective_price >= 1.0:
        return 1.0
    return min(1.0, edge / (1.0 - effective_price))


def _value_side(
    *,
    price: float | None,
    fused: float,
    kappa: float,
    kappa_xm: float,
    cross_market_signal: float,
) -> Side | None:
    """Resolve the value-mode leg from the price edge, for the EXPLORATION
    probe at abstains where ``side`` isn't otherwise computed (low-conf).

    ``side = sign(p_model - price)`` with the SAME ``p_model`` the value
    branch builds (price-anchored, kappa-tilted, kappa_xm cross-market).
    Returns ``None`` when ``price is None`` (legacy signal mode — no value
    leg) OR when ``p_model == price`` (neutral edge — no resolvable side),
    so the caller skips the probe rather than betting an undefined leg.
    """
    if price is None:
        return None
    p_model = max(
        0.0,
        min(1.0, price + kappa * fused + kappa_xm * cross_market_signal),
    )
    edge_yes = p_model - price
    if edge_yes == 0.0:
        return None
    return Side.YES if edge_yes > 0.0 else Side.NO


__all__ = [
    "CROWD_VOLUME",
    "DEFAULT_CONVERSION_RATE",
    "DEFAULT_KAPPA",
    "DEFAULT_MAX_BREATH_RISK_PCT",
    "DEFAULT_MIN_BET_SIZE_USD",
    "DEFAULT_MIN_CONFIDENCE",
    "DESPERATE_BET_SIZE_CAP",
    "MARKET_MOMENTUM",
    "NORMAL_BET_SIZE_CAP",
    "NO_BET_BELOW_MIN_SIZE",
    "NO_BET_LOW_CONFIDENCE",
    "NO_BET_MISSING_SIGNAL",
    "NO_BET_NEUTRAL_FUSED",
    "NO_BET_NO_EDGE",
    "NO_BET_PRICE_FLOOR",
    "NO_BET_ZERO_KELLY",
    "RATIONAL_ENGINES",
    "SENTIENT_ENGINES",
    "SENTIMENT_LLM",
    "SMART_MONEY",
    "TENNIS_TECHNICAL",
    "DecisionEngine",
    "FusionResult",
    "GateDiagnostics",
]
