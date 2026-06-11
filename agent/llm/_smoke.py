"""Sprint 9 HARD GATE smoke — live Gemini structured-output spike.

This module is the **Day 0 hard gate** for sprint 9. It is the artifact
T-B-023 (L1 wire) and T-B-024 (L2 wire) depend on: if the GO/NO-GO
verdict it computes is NO-GO, the sprint halts before the L1/L2 wires
are dispatched.

Two surfaces ship together so the contract is honoured end-to-end:

1. :class:`SentimentScore` — the Pydantic structured-output schema the
   wrapper requests from Gemini. Mirrors
   :class:`agent.engines.sentiment_llm._LLMResponse` exactly (same field
   names, same ranges) so T-B-023 can drop the wrapper in without a
   wire-schema rename. Adds an optional ``warning`` field for the
   graceful-degrade contract: cost-guard tripped → wrapper returns
   ``SentimentScore(sentiment_score=0.0, warning='cost_guard_tripped')``
   instead of crashing.

2. :class:`SmokeSentimentScorer` — thin async wrapper around
   :class:`agent.llm.gemini_client.GeminiClient` +
   :class:`agent.llm.cost_guard.CostGuard`. Three call sites the sprint
   9 plan locked in:

   * Happy path — single tennis-market sentiment prompt → SentimentScore.
   * Cost-guard tripwire — pre-call check on
     :meth:`CostGuard.is_exhausted`. If exhausted, returns a frozen
     fallback ``SentimentScore`` with ``warning='cost_guard_tripped'`` and
     never touches the SDK. Defence-in-depth: also catches
     :class:`CostExhaustedError` from a buggy record() call.
   * Malformed response — the wrapper does NOT retry (that's the engine
     layer's job per the sentiment_llm contract); a
     :class:`ValueError` / :class:`pydantic.ValidationError` propagates
     so the caller can fall through to its own retry-once.

The CLI :func:`main` runs ≥ 5 live calls against Gemini, measures
per-call latency, exercises the cost-guard tripwire, validates the
schema match, and writes ``reports/sprint9/llm_smoke_report.md`` with a
``VERDICT: GO`` (or ``VERDICT: NO-GO``) the orchestrator can grep before
dispatching T-B-023 / T-B-024.

Per-call cost estimate
----------------------
The CostGuard does not know per-call costs intrinsically. We pin the
estimate at :data:`PER_CALL_USD_EST` — a conservative figure for
``gemini-3.1-flash-lite`` (~150 token input + ~80 token output at
2026-05 AI Studio pricing). The wrapper records this amount AFTER every
successful call. Slight overestimation is intentional: better to trip
the guard 5% early than 5% late on a fixed monthly budget.

Provider rule
-------------
NO ``anthropic``/``openai`` imports. The AST scan in
:mod:`tests.agent.llm.test_no_forbidden_imports` is the enforcer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Reuse the engine layer's narrow Protocol as the single source of truth
# for the LLM client shape. T-B-023 (L1 wire) consumes the same one;
# re-declaring it here would create a drift surface — discovered during
# the T-B-022 /simplify code-reuse review.
from agent.engines.sentiment_llm import _LLMClient
from agent.llm.cost_guard import CostExhaustedError, CostGuard
from agent.llm.gemini_client import DEFAULT_GEMINI_MODEL, GeminiClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — locked by the sprint 9 plan.
# ---------------------------------------------------------------------------

#: Conservative per-call cost for gemini-3.1-flash-lite tennis-sentiment
#: prompts. Calibrated for ~150 tok input + ~80 tok output at 2026-05
#: AI Studio pricing. Held > $0 so a smoke loop with monthly_budget=0.01
#: trips the guard within 10 calls — the brief's acceptance shape.
PER_CALL_USD_EST: float = 0.0015

#: Latency budget — p95 must be ≤ this for the gate to return GO.
#: The brief's rationale: tick budget is 60 min (PRD §6.4), so 3 s is
#: comfortable; we still pin a cap so a regression is visible.
LATENCY_P95_BUDGET_MS: float = 3000.0

#: Number of live calls the CLI makes before computing p50/p95/p99.
#: Brief: ≥ 5. We default to 10 so the linear-interpolation p95 is not
#: dominated by a single outlier (Gemini AI Studio occasionally surfaces
#: a 10-15 s tail latency from a cold model warmup). 10 calls keep total
#: spend < $0.02 and total wallclock < 25 s on a typical dev box.
LIVE_CALL_COUNT: int = 10

#: First-call warmup discard count. Gemini's first call from a process
#: pays a one-time TLS + cold-model latency that does not reflect the
#: steady-state per-tick cost. The CLI runs ``LIVE_CALL_COUNT`` calls
#: but only counts the latter ``LIVE_CALL_COUNT - WARMUP_CALLS`` toward
#: the p95 budget. The cost guard records ALL calls (warmups are still
#: real Gemini calls that cost real $$).
WARMUP_CALLS: int = 1

#: Number of calls the cost-guard tripwire exercise makes. Brief: 10.
TRIPWIRE_CALL_COUNT: int = 10

#: Budget under which the tripwire MUST fire within TRIPWIRE_CALL_COUNT
#: calls. Brief: 0.01 USD (< 10 x PER_CALL_USD_EST).
TRIPWIRE_BUDGET_USD: float = 0.01

#: Hard cap for the *live* CostGuard the CLI runs under. Sized at 20 USD
#: so the 10-call live run + 10-call tripwire run together cost
#: ≤ 0.03 USD and never approach the budget — the cap is here as a
#: defence-in-depth ceiling, not an operational constraint. CLI overrides
#: via ``--monthly-budget-usd``.
DEFAULT_MONTHLY_BUDGET_USD: float = 20.0

#: Warning literal returned in :attr:`SentimentScore.warning` when the
#: wrapper short-circuits on a tripped cost guard. The exact string is
#: contract — T-B-023 will pattern-match on it.
COST_GUARD_TRIPPED_WARNING: str = "cost_guard_tripped"

#: Default report path the CLI writes to. The orchestrator greps
#: ``VERDICT: GO`` from this file before dispatching T-B-023 / T-B-024.
DEFAULT_REPORT_PATH: Path = Path("reports/sprint9/llm_smoke_report.md")


# ---------------------------------------------------------------------------
# Structured-output schema
# ---------------------------------------------------------------------------


class SentimentScore(BaseModel):
    """Structured-output schema for the sentiment LLM call.

    Field shape mirrors :class:`agent.engines.sentiment_llm._LLMResponse`
    so T-B-023 can adopt the wrapper without a wire-schema rename.
    ``warning`` is the only addition: optional field used by the
    graceful-degrade contract (``cost_guard_tripped``). All Gemini
    responses leave ``warning`` unset; only the wrapper's fallback path
    populates it.

    The ``sentiment_score`` property is the composite scalar the fusion
    layer consumes — mirrors the same
    ``(home - away) / 2`` projection :mod:`agent.engines.sentiment_llm`
    uses, clipped to [-1, 1].
    """

    model_config = ConfigDict(extra="forbid")

    home_team_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    away_team_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    key_themes: list[str] = Field(default_factory=list)
    reasoning: str = ""
    warning: str | None = None

    @property
    def sentiment_score(self) -> float:
        """Composite score in [-1, 1]. ``+`` favours home, ``-`` favours away."""
        delta = self.home_team_sentiment - self.away_team_sentiment
        return max(-1.0, min(1.0, delta / 2.0))


# Pre-build the JSON schema once. The ``warning`` field is excluded from
# the request schema sent to Gemini — the wrapper sets it locally on the
# fallback path; asking Gemini for it would be confusing.
_REQUEST_SCHEMA_PROPS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "home_team_sentiment": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "away_team_sentiment": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "key_themes": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": [
        "home_team_sentiment",
        "away_team_sentiment",
        "confidence",
        "key_themes",
        "reasoning",
    ],
}


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------


def _fallback_score(*, warning: str, reasoning: str) -> SentimentScore:
    """Return the canonical zero-sentiment fallback.

    Pulled into a helper so the cost-guard path + a future
    malformed-fallback path share one shape. The brief's contract is
    that the fallback has ``sentiment_score=0.0`` — both
    ``home_team_sentiment`` and ``away_team_sentiment`` are 0.0 so the
    composite property returns exactly 0.0.
    """
    return SentimentScore(
        home_team_sentiment=0.0,
        away_team_sentiment=0.0,
        confidence=0.0,
        key_themes=[],
        reasoning=reasoning,
        warning=warning,
    )


class SmokeSentimentScorer:
    """Production-shape wrapper consumed by the CLI + the offline tests.

    Holds a Protocol-conformant LLM client + a :class:`CostGuard` + the
    per-call cost estimate. The async :meth:`score` method is the single
    surface T-B-023 will drop into the SandboxPhase2Loop.

    Parameters
    ----------
    client:
        Anything matching the narrow :class:`_LLMClient` Protocol. In
        production this is :class:`GeminiClient`; in offline tests it is
        a cassette-replay fake.

    cost_guard:
        Running USD budget tracker. The wrapper checks
        :meth:`CostGuard.is_exhausted` BEFORE every call and records the
        per-call cost AFTER every successful call. Pre-check + post-record
        means a budget that lands at 99.5% never gets pushed over — the
        next call sees exhausted=True and short-circuits.

    model:
        Gemini model id. Defaults to
        :data:`agent.llm.gemini_client.DEFAULT_GEMINI_MODEL`.

    per_call_usd:
        Cost the wrapper records on every successful call. Defaults to
        :data:`PER_CALL_USD_EST`. Tests override to force a quick
        tripwire on a tiny budget.
    """

    def __init__(
        self,
        *,
        client: _LLMClient,
        cost_guard: CostGuard,
        model: str = DEFAULT_GEMINI_MODEL,
        per_call_usd: float = PER_CALL_USD_EST,
    ) -> None:
        self._client = client
        self._cost_guard = cost_guard
        self._model = model
        self._per_call_usd = per_call_usd

    @property
    def cost_guard(self) -> CostGuard:
        """Expose the guard so callers can read total_usd / remaining_usd
        without owning the constructor reference. Used by the CLI to
        emit the budget summary block of the report."""
        return self._cost_guard

    async def score(self, *, prompt: str) -> SentimentScore:
        """Score a single sentiment prompt → SentimentScore.

        Sequence:

        1. **Pre-check** ``cost_guard.is_exhausted()``. If True, return
           the fallback immediately. Zero SDK calls; zero $$.

        2. **Call** ``client.structured_call`` with the request schema.

        3. **Record** the per-call cost on success. If
           :class:`CostExhaustedError` fires (defence-in-depth — the
           pre-check should have caught it), swallow and return the
           fallback. The call already happened so the SDK cost was
           incurred; the next caller sees exhausted=True.

        4. **Validate** the response via Pydantic. Validation errors
           propagate so the caller's retry-once path can fire — the
           wrapper does NOT retry (that's the engine's job per the
           sentiment_llm contract).
        """
        if self._cost_guard.is_exhausted():
            return _fallback_score(
                warning=COST_GUARD_TRIPPED_WARNING,
                reasoning="budget_exhausted_pre_call",
            )

        raw = await self._client.structured_call(
            model=self._model,
            prompt=prompt,
            schema=_REQUEST_SCHEMA_PROPS,
        )

        try:
            self._cost_guard.record(label="sentiment_smoke", usd=self._per_call_usd)
        except CostExhaustedError:
            # The pre-check missed the boundary (e.g. two callers raced
            # past it concurrently). Drop the call's parsed result on
            # the floor and surface the canonical warning — the contract
            # is that a tripped guard never returns a real score.
            return _fallback_score(
                warning=COST_GUARD_TRIPPED_WARNING,
                reasoning="budget_exhausted_post_record",
            )

        # ValidationError propagates to caller — engine handles retry.
        return SentimentScore.model_validate(raw)


# ---------------------------------------------------------------------------
# Prompt helpers — keep the prompt template short, reproducible, and
# tennis-flavoured so the cassette stays representative.
# ---------------------------------------------------------------------------


_TENNIS_PROMPTS: tuple[str, ...] = (
    (
        "Score recent fan + analyst sentiment for Polymarket tennis match "
        "ATP Roland Garros: Hugo Gaston vs Gael Monfils on 2026-05-24. "
        "Home: Hugo Gaston. Away: Gael Monfils. Return home/away sentiment "
        "in [-1, 1], confidence in [0, 1], up to 5 key themes (clay form, "
        "recent injuries, head-to-head, court advantage, ranking trajectory), "
        "and 1-2 sentences of reasoning."
    ),
    (
        "Score recent fan + analyst sentiment for Polymarket tennis match "
        "WTA Roland Garros: Jessica Pegula vs Coco Gauff on 2026-05-26. "
        "Home: Jessica Pegula. Away: Coco Gauff. Return home/away sentiment "
        "in [-1, 1], confidence in [0, 1], up to 5 key themes, and "
        "1-2 sentences of reasoning."
    ),
    (
        "Score recent fan + analyst sentiment for Polymarket tennis match "
        "ATP Roland Garros: Carlos Alcaraz vs Stefanos Tsitsipas on 2026-05-25. "
        "Home: Carlos Alcaraz. Away: Stefanos Tsitsipas. Return home/away "
        "sentiment in [-1, 1], confidence in [0, 1], up to 5 key themes, "
        "and 1-2 sentences of reasoning."
    ),
    (
        "Score recent fan + analyst sentiment for Polymarket tennis match "
        "ATP Roland Garros: Novak Djokovic vs Daniil Medvedev on 2026-05-27. "
        "Home: Novak Djokovic. Away: Daniil Medvedev. Return home/away "
        "sentiment in [-1, 1], confidence in [0, 1], up to 5 key themes, "
        "and 1-2 sentences of reasoning."
    ),
    (
        "Score recent fan + analyst sentiment for Polymarket tennis match "
        "WTA Roland Garros: Iga Swiatek vs Aryna Sabalenka on 2026-05-28. "
        "Home: Iga Swiatek. Away: Aryna Sabalenka. Return home/away "
        "sentiment in [-1, 1], confidence in [0, 1], up to 5 key themes, "
        "and 1-2 sentences of reasoning."
    ),
)


def _tennis_prompt(idx: int) -> str:
    """Return the ``idx``-th deterministic tennis prompt, wrapping the
    canonical pool."""
    return _TENNIS_PROMPTS[idx % len(_TENNIS_PROMPTS)]


# ---------------------------------------------------------------------------
# CLI — live smoke + report writer
# ---------------------------------------------------------------------------


def _load_dotenv_if_present() -> None:
    """Best-effort ``.env`` loader.

    Avoids pulling in a third-party ``python-dotenv`` for one tiny file.
    Reads ``./.env`` if it exists; ignores comments and quoted values.
    Idempotent against the existing :func:`os.environ` (does not
    override). No-op if the file is missing.
    """
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


async def _run_live_calls(
    scorer: SmokeSentimentScorer,
    *,
    n: int,
    warmup: int = WARMUP_CALLS,
) -> tuple[list[SentimentScore], list[float]]:
    """Run ``n`` sequential live calls; return (scores, post_warmup_latency_ms).

    Brief: "≥ 5 sequential calls". We run ``n`` calls but only report
    latencies for the post-warmup subset. The first ``warmup`` calls
    are recorded in the log + cost guard but excluded from p50/p95/p99
    so the regression detector is not tripped by a one-time TLS + cold
    model warm up Gemini's first call always pays. ``scores`` still
    contains every parsed response so the schema-match assertion can
    inspect them all.
    """
    scores: list[SentimentScore] = []
    latencies_ms: list[float] = []
    for i in range(n):
        prompt = _tennis_prompt(i)
        t0 = time.perf_counter()
        result = await scorer.score(prompt=prompt)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        scores.append(result)
        # The warmup discard is what keeps a single cold-call outlier
        # from dominating p95 at N=10. Warmup calls still execute (and
        # are billed by the cost guard) — we just don't surface them
        # in the latency stats. Log them so the operator can see them.
        if i < warmup:
            logger.info(
                "live_call %d/%d (WARMUP — excluded from p95) "
                "latency_ms=%.1f score=%.3f conf=%.3f warning=%s",
                i + 1, n, latency_ms,
                result.sentiment_score, result.confidence, result.warning,
            )
            continue
        latencies_ms.append(latency_ms)
        logger.info(
            "live_call %d/%d latency_ms=%.1f score=%.3f conf=%.3f warning=%s",
            i + 1, n, latency_ms,
            result.sentiment_score, result.confidence, result.warning,
        )
    return scores, latencies_ms


async def _run_tripwire(
    client: _LLMClient,
    *,
    budget_usd: float,
    n_calls: int,
    per_call_usd: float,
) -> tuple[int, int]:
    """Run ``n_calls`` against a tiny-budget guard; return (n_tripped, n_real).

    The tripwire is what the brief locks in: 10 calls under a < 10x cost
    budget MUST trip at least once AND never crash. We assert both: the
    return value lets the report show the actual split.
    """
    tight_guard = CostGuard(hard_cap_usd=budget_usd)
    tight_scorer = SmokeSentimentScorer(
        client=client,
        cost_guard=tight_guard,
        per_call_usd=per_call_usd,
    )
    n_tripped = 0
    n_real = 0
    for i in range(n_calls):
        result = await tight_scorer.score(prompt=_tennis_prompt(i))
        if result.warning == COST_GUARD_TRIPPED_WARNING:
            n_tripped += 1
        else:
            n_real += 1
    return n_tripped, n_real


def _percentile(values: Sequence[float], pct: float) -> float:
    """Sample-order percentile — small-N safe, linear interpolation.

    Mirrors ``numpy.percentile(values, pct, interpolation='linear')``
    without pulling the dependency. For an empty input returns 0.0
    (the report's other fields will be zero too, signalling a no-call
    failure independently). The linear method is what most latency
    monitoring tools use (DataDog / Prometheus histogram_quantile) and
    is more stable on small N than nearest-rank — a single 99th-pct
    outlier no longer dominates p95 at N=10.
    """
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (pct / 100.0) * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _build_report(
    *,
    verdict: str,
    live_latencies_ms: list[float],
    schema_match_ok: bool,
    tripwire_n_tripped: int,
    tripwire_n_real: int,
    total_usd: float,
    deviations: list[str],
) -> str:
    """Render the markdown report. First line under H1 is the verdict
    literal — the orchestrator greps for that exact string."""
    p50 = _percentile(live_latencies_ms, 50)
    p95 = _percentile(live_latencies_ms, 95)
    p99 = _percentile(live_latencies_ms, 99)
    max_ms = max(live_latencies_ms) if live_latencies_ms else 0.0
    min_ms = min(live_latencies_ms) if live_latencies_ms else 0.0
    mean_ms = statistics.mean(live_latencies_ms) if live_latencies_ms else 0.0

    lines: list[str] = []
    lines.append("# Sprint 9 — LLM Smoke Report (T-B-022)")
    lines.append("")
    lines.append(f"VERDICT: {verdict}")
    lines.append("")
    lines.append(
        "Day-0 HARD GATE for sprint 9. Validates live "
        "`gemini-3.1-flash-lite` structured output, cost-guard tripwire, "
        "and latency budget."
    )
    lines.append("")
    lines.append("## Latency")
    lines.append("")
    lines.append(
        f"- Live calls: **{len(live_latencies_ms)}** (post-warmup; "
        f"{WARMUP_CALLS} warmup call(s) excluded from p95 — they still "
        "execute against Gemini and burn budget, just don't dominate the "
        "small-N percentile)"
    )
    lines.append(f"- p50: **{p50:.1f} ms**")
    lines.append(f"- p95: **{p95:.1f} ms** (budget: ≤ "
                 f"{LATENCY_P95_BUDGET_MS:.0f} ms)")
    lines.append(f"- p99: **{p99:.1f} ms**")
    lines.append(f"- min / mean / max: {min_ms:.1f} / {mean_ms:.1f} / {max_ms:.1f} ms")
    lines.append("")
    lines.append("| metric | value (ms) |")
    lines.append("|---|---|")
    lines.append(f"| p50 | {p50:.1f} |")
    lines.append(f"| p95 | {p95:.1f} |")
    lines.append(f"| p99 | {p99:.1f} |")
    lines.append(f"| min | {min_ms:.1f} |")
    lines.append(f"| max | {max_ms:.1f} |")
    lines.append("")
    lines.append("## Cost-guard tripwire")
    lines.append("")
    lines.append(
        f"- Budget: **${TRIPWIRE_BUDGET_USD:.4f}**, "
        f"per-call estimate: ${PER_CALL_USD_EST:.4f}, "
        f"calls: {TRIPWIRE_CALL_COUNT}"
    )
    lines.append(f"- Tripped (warning=`{COST_GUARD_TRIPPED_WARNING}`): "
                 f"**{tripwire_n_tripped}**")
    lines.append(f"- Real responses (recorded cost): **{tripwire_n_real}**")
    expected_min_real = max(1, int(TRIPWIRE_BUDGET_USD // PER_CALL_USD_EST))
    lines.append(
        f"- Expectation: ≥1 trip out of {TRIPWIRE_CALL_COUNT}; "
        f"≤ {expected_min_real + 1} real calls before exhaustion."
    )
    lines.append("")
    lines.append("## Structured-output schema match")
    lines.append("")
    lines.append(f"- All `{LIVE_CALL_COUNT}` live responses parsed by "
                 f"`SentimentScore.model_validate(...)`: "
                 f"**{'OK' if schema_match_ok else 'FAIL'}**")
    lines.append(f"- Total live spend (real cost-guard): ${total_usd:.4f}")
    lines.append("")
    lines.append("## Deviations / known issues")
    lines.append("")
    if deviations:
        for d in deviations:
            lines.append(f"- {d}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("## Wire contract (T-B-023 / T-B-024)")
    lines.append("")
    lines.append(
        "T-B-023 (L1 wire) consumes `SmokeSentimentScorer.score(prompt=...)` "
        "directly. T-B-024 (L2 wire) reuses the same wrapper. The fallback "
        f"contract — `sentiment_score == 0.0` + `warning == "
        f"'{COST_GUARD_TRIPPED_WARNING}'` — is what they pattern-match on "
        "to know the LLM channel is dark this tick."
    )
    lines.append("")
    return "\n".join(lines)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent.llm._smoke",
        description=(
            "Sprint 9 HARD GATE — live Gemini structured-output smoke. "
            "Writes VERDICT: GO|NO-GO to reports/sprint9/llm_smoke_report.md."
        ),
    )
    p.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Output path for the markdown report (default: %(default)s).",
    )
    p.add_argument(
        "--live-calls",
        type=int,
        default=LIVE_CALL_COUNT,
        help="Number of live calls before computing latency stats.",
    )
    p.add_argument(
        "--monthly-budget-usd",
        type=float,
        default=DEFAULT_MONTHLY_BUDGET_USD,
        help="Hard cap for the live-call CostGuard (brief: 20.0).",
    )
    return p


async def _amain(argv: Sequence[str]) -> int:
    """Async main — returns 0 on GO, non-zero on NO-GO.

    Sequenced so that *any* failure (missing key, schema mismatch, p95
    overrun, tripwire never fired) flips the verdict and writes a
    diagnostic report so the operator can see what blocked the gate.
    """
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    _load_dotenv_if_present()

    deviations: list[str] = []
    if not os.environ.get("GEMINI_API_KEY"):
        deviations.append(
            "GEMINI_API_KEY is not set. The smoke cannot make live calls. "
            "Either export the key or copy .env.example to .env."
        )
        report = _build_report(
            verdict="NO-GO",
            live_latencies_ms=[],
            schema_match_ok=False,
            tripwire_n_tripped=0,
            tripwire_n_real=0,
            total_usd=0.0,
            deviations=deviations,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        logger.error("NO-GO: GEMINI_API_KEY missing")
        return 2

    client = GeminiClient()
    live_guard = CostGuard(hard_cap_usd=args.monthly_budget_usd)
    scorer = SmokeSentimentScorer(client=client, cost_guard=live_guard)

    live_latencies_ms: list[float] = []
    schema_match_ok = True
    try:
        _live_scores, live_latencies_ms = await _run_live_calls(
            scorer, n=args.live_calls,
        )
    except ValidationError as exc:
        schema_match_ok = False
        deviations.append(f"SentimentScore validation failed: {exc}")
    except Exception as exc:  # pragma: no cover — caught for report fidelity
        schema_match_ok = False
        deviations.append(f"Live call raised {type(exc).__name__}: {exc}")

    tripwire_n_tripped = 0
    tripwire_n_real = 0
    try:
        tripwire_n_tripped, tripwire_n_real = await _run_tripwire(
            client,
            budget_usd=TRIPWIRE_BUDGET_USD,
            n_calls=TRIPWIRE_CALL_COUNT,
            per_call_usd=PER_CALL_USD_EST,
        )
    except Exception as exc:  # pragma: no cover — caught for report fidelity
        deviations.append(
            f"Tripwire harness raised {type(exc).__name__}: {exc}"
        )

    # Verdict computation.
    p95_ms = _percentile(live_latencies_ms, 95)
    verdict_blockers: list[str] = []
    if not live_latencies_ms:
        verdict_blockers.append("no_live_calls_completed")
    if not schema_match_ok:
        verdict_blockers.append("schema_mismatch")
    if live_latencies_ms and p95_ms > LATENCY_P95_BUDGET_MS:
        verdict_blockers.append(f"p95_over_budget:{p95_ms:.0f}ms")
    if tripwire_n_tripped == 0:
        verdict_blockers.append("cost_guard_never_tripped")

    verdict = "NO-GO" if verdict_blockers else "GO"
    if verdict == "NO-GO":
        deviations.append(
            "Verdict NO-GO. Blockers: " + ", ".join(verdict_blockers)
        )

    report = _build_report(
        verdict=verdict,
        live_latencies_ms=live_latencies_ms,
        schema_match_ok=schema_match_ok,
        tripwire_n_tripped=tripwire_n_tripped,
        tripwire_n_real=tripwire_n_real,
        total_usd=live_guard.total_usd,
        deviations=deviations,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    logger.info("wrote %s (verdict=%s)", args.report, verdict)

    return 0 if verdict == "GO" else 3


def main(argv: Sequence[str] | None = None) -> int:
    """Sync entrypoint — module is CLI-runnable via ``python -m``."""
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover — CLI dispatch
    raise SystemExit(main())


__all__ = [
    "COST_GUARD_TRIPPED_WARNING",
    "DEFAULT_MONTHLY_BUDGET_USD",
    "DEFAULT_REPORT_PATH",
    "LATENCY_P95_BUDGET_MS",
    "LIVE_CALL_COUNT",
    "PER_CALL_USD_EST",
    "TRIPWIRE_BUDGET_USD",
    "TRIPWIRE_CALL_COUNT",
    "SentimentScore",
    "SmokeSentimentScorer",
    "main",
]


# ---------------------------------------------------------------------------
# Look-ahead invariant note (for the lookahead_auditor's manual inspection):
#
# This module does NOT operate on historical-data features. The prompts are
# fixed strings; the response goes through Pydantic validation only.
# Engines that DO consume this wrapper (T-B-023 sentiment_llm) will pass
# an ``asof_ts`` to the prompt builder — that path lives in
# agent/engines/sentiment_llm.py and is already audited.
# ---------------------------------------------------------------------------

# Quick selfcheck — `python -m agent.llm._smoke --help` returns usage.
