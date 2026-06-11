# Track E — Data Pipelines

The `data/` package is the **point-in-time-correct** ETL bridge that
feeds both:

- Track B (live agent runtime under `agent/`) at decision time, and
- Track C (calibration sim under `sim/`) on replay.

Per PRD §14.1 and TECHNICAL_PLAN §6, the cardinal invariant is:

> No feature row may be visible to the agent before its `available_at`
> timestamp has elapsed in wall-clock terms.

The `data.etl.pit_correct.assert_no_lookahead` chokepoint enforces
that rule on every join boundary; every `.fetch_*` entrypoint also
requires the `asof_ts` keyword argument and rejects naive timestamps
*before* contacting upstream, so a leak never burns API quota.

## Sprint history

- **T-E-001 (sprint_1)** — package skeleton + stub `fetch_*` raising
  `NotImplementedError('sprint_2')`. Type surface pinned.
- **T-E-002 (sprint_2)** — real fetchers lit up for all four feeds,
  PIT chokepoint body lands (pandas + polars), parquet schemas
  defined, end-to-end orchestrator (`build_training_set`) ships.

## Layout

```
data/
  __init__.py
  sources/
    __init__.py
    _http.py          # shared retry / UA / asof_ts gate
    nba.py            # NBAClient + NBAGame (balldontlie /v1/games)
    polymarket.py     # PolymarketHistoryClient + MarketHistory (READ-ONLY CLOB)
    polygon.py        # PolygonChainClient + ChainEvent (READ-ONLY web3 eth_getLogs)
    reddit.py         # RedditSentimentClient + SentimentSnapshot (public JSON)
  etl/
    __init__.py
    pit_correct.py    # assert_no_lookahead chokepoint + LookaheadError
    build_training_set.py  # 4 fetchers → 1 PIT-validated parquet
  schemas/
    __init__.py
    streams.py        # NBAGameRow / PolymarketSnapshotRow / PolygonEventRow / RedditWindowRow
tests/
  data/
    conftest.py       # FakeSession + fixture loaders
    fixtures/         # JSON fixtures replayed via FakeSession
    test_smoke.py
    test_pit.py
    test_fetchers.py
```

## Hard rules (cross-track contract)

1. **Point-in-time strict slicing** — every `.fetch_*` requires
   `asof_ts=` (keyword-only). Missing / naive timestamps raise
   `LookaheadError` *before* any network call.
2. **READ-ONLY by manifest** — `data/sources/polygon.py` has zero
   signing-library imports, zero send-transaction paths, and asserts
   no signer is wired on every `_w3_lazy()` call. The
   `test_polygon_source_is_read_only_by_grep` test encodes the
   cross-chain auditor's grep.
3. **Schema validation on output** — every parquet column layout is
   pinned by a Pydantic model in `data/schemas/streams.py`.
4. **API rate limits** — `HttpClient` does exponential backoff (1 s,
   2 s, 4 s) over 4 total attempts (3 retries) on `429`/`5xx`.
5. **No prediction logic, no bankroll math, no settlement** — those
   belong to Track B.

## Running tests

```bash
# Hermetic CI tests — replays JSON fixtures via FakeSession.
pytest -x tests/data

# Strict typing on the entire data/ tree.
mypy --strict data/
```

### Live integration tests (opt-in)

The four `@pytest.mark.live` smoke tests hit the real upstreams
(balldontlie, Polymarket CLOB, public Polygon RPC, reddit.com JSON).
They are **skipped by default** and only run when:

```bash
RUN_LIVE_DATA_TESTS=1 pytest -x tests/data -m live
```

Use only when validating a fetcher against a live API change — they
consume rate-limit quota and depend on the upstream being healthy.

## Smart Money wallet identification

Per PRD §7, the offline pass that scores Polygon wallets (NBA bettors
with ≥30 games, win rate ≥60%, profit > $5K) lives downstream of
`PolygonChainClient.fetch_events`. The reducer + the resulting static
whitelist (`data/chain_indexer/smart_money_wallets.json`) ship in
a follow-up task; this sprint provides the event-fetch primitive
it consumes.

## What lands later

- Pushshift backfill behind the same `RedditSentimentClient` signature
  (with resume support, since Pushshift is rate-limited and sometimes
  down).
- Persistent `raw_cache/` under each domain (gitignored already) so
  Track C replays don't repeat upstream calls.
- Smart Money wallet reducer + whitelist (per above).
