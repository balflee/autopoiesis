"""T-B-022 offline replay test — hermetic smoke for the sprint 9 Day-0 gate.

Mirrors the live ``agent/llm/_smoke.py`` flow without touching the
network. Three test surfaces, all driven from the same YAML cassette
under ``tests/agent/llm/cassettes/test_gemini_smoke_offline.yaml``:

1. **Happy path** — the cassette's first interaction yields a valid
   :class:`SentimentScore`. The wrapper's composite ``sentiment_score``
   property lands in [-1, 1]. ``warning`` stays ``None``.

2. **Cost-guard tripwire** — the brief's load-bearing graceful-degrade
   contract: 10 calls under a budget of 0.01 USD MUST trip at least
   once AND never crash. Tripped responses set
   ``warning='cost_guard_tripped'`` + ``sentiment_score == 0.0``.

3. **Malformed response fallback** — when the Gemini SDK surfaces a
   :class:`ValueError` (empty body / JSON decode error), the wrapper
   does NOT swallow it; it propagates so the engine layer's
   retry-once-then-fail-soft path can fire. We assert the propagation
   shape here; the engine-side retry behaviour is already covered by
   :mod:`tests.agent.engines.test_sentiment_llm`.

Replay machinery
----------------
We use a stub :class:`_CassetteLLMClient` Protocol-conformant fake
rather than wiring vcrpy against ``google-genai``'s aiohttp transport.
Three reasons (all already accepted precedent in this repo):

* The narrow ``_LLMClient`` Protocol is the engine layer's seam by
  design — :mod:`agent.engines.sentiment_llm` and
  :mod:`agent.llm._smoke` both consume it through a single method
  (``structured_call``). Stubbing at that seam is the same hermetic-replay
  shape vcrpy provides but without aiohttp/grpc compat surface area.
  See ``tests/data/conftest.py`` for the same call-shape precedent.

* Stubbing at the protocol boundary keeps the API key out of the
  cassette entirely. The :func:`tests.agent.llm.conftest.autouse_no_provider_keys`
  fixture deletes ``GEMINI_API_KEY``; the cassette file holds only the
  response body. A future vcrpy-at-aiohttp recording would have to filter
  the ``?key=…`` query parameter; the stub design sidesteps that risk.

* The cassette YAML is human-readable per-interaction, mirrors the
  request/response shape of a real vcrpy cassette, and is what a future
  swap to vcrpy can target directly. ``tests/agent/llm/cassettes/`` is
  the brief's pinned location.

Cassette safety
---------------
The autouse ``no_provider_keys`` fixture from
``tests/agent/llm/conftest.py`` is what enforces zero live calls: even
if a future contributor accidentally wired :class:`GeminiClient` into
this test, the absent ``GEMINI_API_KEY`` would raise
:class:`MissingApiKeyError` BEFORE any network I/O. The
:func:`test_no_real_gemini_client_instantiated` test re-asserts the
invariant at the test-collection level — any subsequent test that
reaches for ``google.genai`` blows up.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from agent.llm._smoke import (
    COST_GUARD_TRIPPED_WARNING,
    PER_CALL_USD_EST,
    TRIPWIRE_BUDGET_USD,
    TRIPWIRE_CALL_COUNT,
    SentimentScore,
    SmokeSentimentScorer,
    _percentile,
    _tennis_prompt,
)
from agent.llm.cost_guard import CostGuard
from agent.llm.gemini_client import DEFAULT_GEMINI_MODEL

# --------------------------------------------------------------------------- #
# Cassette wiring
# --------------------------------------------------------------------------- #

CASSETTE_PATH = (
    Path(__file__).parent / "cassettes" / "test_gemini_smoke_offline.yaml"
)


@dataclass
class _CassetteLLMClient:
    """Protocol-conformant cassette replay stub.

    Loads a YAML file with the same ``interactions: [...]`` outer shape
    a vcrpy cassette uses, but each interaction stores only the parsed
    request envelope (model + prompt) + response body (the parsed JSON
    dict Gemini would have returned in ``response.text``). The
    ``structured_call`` method walks the list in order; if the entry
    is a sentinel ``response.error == 'value_error'``, the stub raises
    :class:`ValueError` to drive the malformed-response branch.

    Attributes
    ----------
    cassette_path:
        Pointer used in error messages so cassette mismatches are
        debuggable.
    interactions:
        Parsed in :meth:`__post_init__`. Each entry shape::

            { "request":  { "model": str, "prompt": str },
              "response": { "body":  dict, "latency_ms": float } }

        or for the malformed-fallback drill::

            { "request": {...},
              "response": { "error": "value_error", "message": "…" } }
    calls:
        Every ``structured_call`` is appended so tests can assert call
        count + per-call model/prompt.

    The :meth:`seek_to_error` helper exists so tests can drive the
    malformed-response branch without reaching into the private ``_idx``
    cursor — keeps the abstraction sealed.
    """

    cassette_path: Path
    interactions: list[dict[str, Any]] = field(init=False)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def __post_init__(self) -> None:
        raw = yaml.safe_load(self.cassette_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "interactions" not in raw:
            raise AssertionError(
                f"cassette {self.cassette_path} missing 'interactions' key"
            )
        self.interactions = list(raw["interactions"])

    def seek_to_error(self, error_kind: str = "value_error") -> int:
        """Advance the cursor to the next interaction carrying
        ``response.error == error_kind``. Returns the index it landed
        on. Raises :class:`AssertionError` if no such interaction
        remains — the cassette is then mis-shaped for the test.
        """
        for i, interaction in enumerate(self.interactions):
            if i < self._idx:
                continue
            if interaction.get("response", {}).get("error") == error_kind:
                self._idx = i
                return i
        raise AssertionError(
            f"no remaining interaction with response.error={error_kind!r} "
            f"in {self.cassette_path}"
        )

    async def structured_call(
        self,
        *,
        model: str,
        prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "prompt": prompt, "schema": schema})
        if self._idx >= len(self.interactions):
            raise AssertionError(
                f"cassette {self.cassette_path} exhausted at call "
                f"#{len(self.calls)} — wire more interactions or "
                "shorten the test."
            )
        offending_idx = self._idx
        interaction = self.interactions[offending_idx]
        self._idx += 1
        response = interaction.get("response", {})
        if response.get("error") == "value_error":
            raise ValueError(response.get("message", "cassette_value_error"))
        body = response.get("body")
        if not isinstance(body, dict):
            raise AssertionError(
                f"cassette interaction #{offending_idx} response.body must "
                f"be a dict (got {type(body).__name__})"
            )
        return cast(dict[str, Any], body)


@pytest.fixture
def cassette_client() -> _CassetteLLMClient:
    """Fresh cassette-backed LLM client per test — independent indices."""
    return _CassetteLLMClient(cassette_path=CASSETTE_PATH)


def _run(coro: Any) -> Any:
    """Async runner — repo has not adopted pytest-asyncio yet (see
    ``tests/agent/llm/conftest.py::run_async`` for the same shape)."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Cassette structural smoke
# --------------------------------------------------------------------------- #


def test_cassette_file_exists_and_has_interactions() -> None:
    """Brief: 'VCR cassette committed under tests/agent/llm/cassettes/'.

    Defence-in-depth — a missing cassette is a packaging bug, not a test
    failure for the unit under test. Surface it explicitly.
    """
    assert CASSETTE_PATH.exists(), (
        f"missing cassette {CASSETTE_PATH} — record via "
        "`python -m agent.llm._smoke --report tests/agent/llm/cassettes/"
        "scratch.md` on a dev box with GEMINI_API_KEY set, then "
        "extract the responses into the YAML format."
    )
    payload = yaml.safe_load(CASSETTE_PATH.read_text(encoding="utf-8"))
    assert "interactions" in payload
    assert len(payload["interactions"]) >= 1, (
        "cassette must contain at least 1 interaction for the happy-path"
    )


# --------------------------------------------------------------------------- #
# Surface 1 — happy path
# --------------------------------------------------------------------------- #


def test_happy_path_returns_valid_sentiment_score(
    cassette_client: _CassetteLLMClient,
) -> None:
    """Brief: 'invokes GeminiClient.structured_call(...) with a sample
    tennis-market sentiment prompt and gets a valid SentimentScore
    Pydantic object back'.

    Read against the cassette (first interaction) — assert the wrapper
    returns a SentimentScore that passes Pydantic validation and the
    composite score lies in [-1, 1].
    """
    guard = CostGuard(hard_cap_usd=20.0)
    scorer = SmokeSentimentScorer(client=cassette_client, cost_guard=guard)

    result = _run(scorer.score(prompt=_tennis_prompt(0)))

    assert isinstance(result, SentimentScore)
    assert result.warning is None
    assert -1.0 <= result.sentiment_score <= 1.0
    assert -1.0 <= result.home_team_sentiment <= 1.0
    assert -1.0 <= result.away_team_sentiment <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    # The wrapper recorded one call's cost.
    assert guard.total_usd == pytest.approx(PER_CALL_USD_EST)
    # And the cassette saw exactly one call with the expected model id.
    assert len(cassette_client.calls) == 1
    assert cassette_client.calls[0]["model"] == DEFAULT_GEMINI_MODEL


# --------------------------------------------------------------------------- #
# Surface 2 — cost-guard tripwire
# --------------------------------------------------------------------------- #


def test_cost_guard_tripwire_returns_warning_not_crash(
    cassette_client: _CassetteLLMClient,
) -> None:
    """Brief: 'call the wrapper 10 times with monthly_budget_usd=0.01
    (i.e. < 10 x per-call cost). On tripwire, the engine returns
    sentiment_score=0.0 + warning='cost_guard_tripped' — it MUST NOT
    crash.'

    With ``hard_cap_usd=0.01`` and per-call cost = ``PER_CALL_USD_EST``
    (0.0015), the guard exhausts after ``ceil(0.01 / 0.0015) = 7``
    real calls; calls 8..10 should short-circuit with the warning.
    None of the 10 calls should raise.
    """
    tiny_guard = CostGuard(hard_cap_usd=TRIPWIRE_BUDGET_USD)
    scorer = SmokeSentimentScorer(client=cassette_client, cost_guard=tiny_guard)

    results: list[SentimentScore] = []
    for i in range(TRIPWIRE_CALL_COUNT):
        results.append(_run(scorer.score(prompt=_tennis_prompt(i))))

    # No call crashed.
    assert all(isinstance(r, SentimentScore) for r in results)

    tripped = [r for r in results if r.warning == COST_GUARD_TRIPPED_WARNING]
    real = [r for r in results if r.warning is None]

    # Brief: must trip at least once.
    assert len(tripped) >= 1, (
        f"cost_guard never tripped after {TRIPWIRE_CALL_COUNT} calls — "
        "either PER_CALL_USD_EST is too low or the budget is too high."
    )
    # Brief: tripped responses have score=0 + warning=cost_guard_tripped.
    for r in tripped:
        assert r.sentiment_score == 0.0
        assert r.warning == COST_GUARD_TRIPPED_WARNING
        # The fallback's sentiment + away components must be exact zeros
        # (no residual from the cassette body) — the wrapper does NOT
        # return a parsed cassette response on the tripped path.
        assert r.home_team_sentiment == 0.0
        assert r.away_team_sentiment == 0.0

    # Real responses are bounded.
    for r in real:
        assert -1.0 <= r.sentiment_score <= 1.0
        assert r.warning is None

    # Cost accounting: cassette saw ``len(real)`` actual calls.
    assert len(cassette_client.calls) == len(real), (
        "tripped path must not touch the SDK"
    )


def test_cost_guard_pre_check_short_circuits_before_sdk(
    cassette_client: _CassetteLLMClient,
) -> None:
    """A guard that starts already-exhausted MUST short-circuit on the
    very first call — zero cassette interactions consumed."""
    # Start with a guard already at the cap. We use the misuse-path
    # bypass (assigning total_usd directly) since CostGuard.record refuses
    # to push past the cap from a fresh state. This mimics a tick that
    # inherited a saturated guard from a prior tick's run.
    saturated = CostGuard(hard_cap_usd=0.01)
    saturated.total_usd = 0.01  # exactly at the cap → is_exhausted() True
    scorer = SmokeSentimentScorer(
        client=cassette_client, cost_guard=saturated,
    )

    result = _run(scorer.score(prompt=_tennis_prompt(0)))

    assert result.warning == COST_GUARD_TRIPPED_WARNING
    assert result.sentiment_score == 0.0
    assert cassette_client.calls == [], (
        "exhausted guard must skip the SDK entirely"
    )


# --------------------------------------------------------------------------- #
# Surface 3 — malformed response fallback
# --------------------------------------------------------------------------- #


def test_malformed_response_propagates_value_error(
    cassette_client: _CassetteLLMClient,
) -> None:
    """The wrapper does NOT retry — that's the engine layer's job.

    The cassette's penultimate interaction is the canonical malformed
    drill: response.error='value_error'. The wrapper should let it
    propagate so :mod:`agent.engines.sentiment_llm`'s retry-once loop
    can catch it. :meth:`_CassetteLLMClient.seek_to_error` positions
    the cursor on the sentinel so we don't reach into private state.
    """
    guard = CostGuard(hard_cap_usd=20.0)
    scorer = SmokeSentimentScorer(client=cassette_client, cost_guard=guard)

    cassette_client.seek_to_error("value_error")
    with pytest.raises(ValueError):
        _run(scorer.score(prompt=_tennis_prompt(0)))


# --------------------------------------------------------------------------- #
# Helper-coverage tests — keep the small utilities pinned.
# --------------------------------------------------------------------------- #


def test_percentile_linear_interpolation_small_n() -> None:
    """:func:`_percentile` is the small-N safe stat helper the CLI uses
    to compute p50/p95/p99 without depending on numpy.

    Uses linear interpolation between sample-order ranks (numpy default).
    For sorted ``[100, 200, 300, 400, 500]`` (N=5, last idx = 4):
      * p50  → pos = 2.0   → s[2]                = 300
      * p95  → pos = 3.8   → s[3] + 0.8*(s[4] - s[3]) = 480
      * p100 → pos = 4.0   → s[4]                = 500
      * p0   → pos = 0.0   → s[0]                = 100
    Linear interpolation is what DataDog / Prometheus histogram_quantile
    use; it is more stable on small N than nearest-rank because a single
    outlier no longer dominates p95.
    """
    values = [100.0, 200.0, 300.0, 400.0, 500.0]
    assert _percentile(values, 50) == pytest.approx(300.0)
    assert _percentile(values, 95) == pytest.approx(480.0)
    assert _percentile(values, 100) == pytest.approx(500.0)
    assert _percentile(values, 0) == pytest.approx(100.0)
    assert _percentile([], 50) == 0.0
    # Single-element series — percentile is the lone sample at any pct.
    assert _percentile([42.0], 95) == pytest.approx(42.0)


def test_tennis_prompt_wraps_pool() -> None:
    """Prompts are deterministic + recycled — the cassette can be sized
    to a fixed number of distinct prompts."""
    first = _tennis_prompt(0)
    again = _tennis_prompt(5)  # idx 5 wraps to idx 0 (pool size 5)
    assert first == again


def test_sentiment_score_composite_clipped_to_unit() -> None:
    """``home - away`` can hit [-2, 2]; the composite divides by 2 then
    clamps."""
    score = SentimentScore(
        home_team_sentiment=1.0,
        away_team_sentiment=-1.0,
        confidence=0.5,
    )
    assert score.sentiment_score == 1.0

    score = SentimentScore(
        home_team_sentiment=-1.0,
        away_team_sentiment=1.0,
        confidence=0.5,
    )
    assert score.sentiment_score == -1.0


# --------------------------------------------------------------------------- #
# Sentinel — no real Gemini SDK touched in this suite.
# --------------------------------------------------------------------------- #


def test_no_real_gemini_client_instantiated() -> None:
    """Brief: 'running pytest tests/agent/llm/test_gemini_smoke_offline.py -x
    performs 0 live calls'.

    The autouse fixture deletes ``GEMINI_API_KEY`` so even if a future
    test accidentally wired :class:`GeminiClient` into the flow, the
    SDK call would raise :class:`MissingApiKeyError` before any I/O. We
    re-assert here so the contract is loud + grep-able.
    """
    import os

    from agent.llm.gemini_client import GeminiClient, MissingApiKeyError

    assert os.environ.get("GEMINI_API_KEY") is None
    client = GeminiClient()
    with pytest.raises(MissingApiKeyError):
        _run(
            client.structured_call(
                model=DEFAULT_GEMINI_MODEL,
                prompt="x",
                schema={"type": "object"},
            )
        )
