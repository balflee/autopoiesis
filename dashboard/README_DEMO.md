# Demo Capture Pipeline — Operator's One-Pager

**Scope:** T-D-005 sprint_5. Three Node scripts + one React component +
one curated fixture deliver the Demo §9 5-minute recording window.

Everything here is observation-only by design (TECHNICAL_PLAN §12).
No script touches the agent runtime, the chain, or the wsStore in a
write direction. The PLAYBACK mode is the ONLY surface that ingests
into the store, and it only ingests a committed, immutable fixture.

---

## Pieces

| File | Role |
|---|---|
| `dashboard/ops/recorder.ts` | Drives Playwright to capture a 1080p60 WebM of the live dashboard for a configurable window. |
| `dashboard/ops/key_moment_capture.ts` | Listens to the Agent WS as a read-only subscriber; fires a full-window screenshot + 5-second pre-roll clip on each of the six Demo §9 narrative beats. Each beat fires AT MOST ONCE. |
| `dashboard/ops/build_playback_fixture.ts` | Deterministic transform from Track B's E2E dry-run log (`data/fixtures/phase3_e2e_dry_run.jsonl`) to the committed curated scenario. Falls through to the in-skeleton authored scenario when the source log is absent. |
| `public/playback_fixtures/golden_scenario_5min.jsonl` | The committed 29-frame curated scenario. Bytewise reproducible from the transform. |
| `dashboard/components/PlaybackMode.tsx` | Upper-right toggle that, when engaged, drains the fixture into the wsStore + pins a persistent `Demo Playback — Not Live` banner above every other surface. |

---

## During the recording window (D19 per TECHNICAL_PLAN §8)

1. Boot the dashboard against the live agent:

       cd dashboard
       npm run dev          # or `npm run build && npm run start`

2. Start the recorder in a second terminal. The window the demo team
   actually cares about is the 5-minute Demo §9 storyboard — pad
   slightly so the cut-room has room:

       npx tsx dashboard/ops/recorder.ts \
           --url http://localhost:3000 \
           --duration-s 360 \
           --out dashboard/screenshots/demo_main_run

   On exit it writes `metadata.json` + a single `recording_<iso>.webm`.

3. In parallel, start the key-moment capture daemon. It opens its own
   read-only WS to Track B (no `send`, no DOM mutation) and fires
   on each of the six beats:

       npx tsx dashboard/ops/key_moment_capture.ts \
           --url http://localhost:3000 \
           --ws ws://localhost:8000/ws \
           --out dashboard/screenshots/demo_main_run/key_moments

   Output: one PNG + one 5-second WebM clip per beat, plus a
   `manifest.json` ledger of what fired and when.

---

## If LIVE goes silent during the recording

The operator flips the `PLAYBACK` toggle (top-right of the dashboard).
Three things happen, all simultaneously:

1. A persistent red banner anchors at the top of the viewport with the
   text **Demo Playback — Not Live**. The banner is z-index 9999 (above
   every other surface, including Death Watch + LLM activation
   overlays). It is imperatively maintained — a MutationObserver +
   250 ms heartbeat re-creates it if anything (CSS, script, browser
   extension) removes or hides it.
2. The wsStore is reset and re-seeded from the curated 5-minute
   fixture. Every projection (vitals, weights, decisions, Death Watch,
   tombstone) lights up exactly as it did during the original Track B
   dry run — because that's literally what the fixture is.
3. The toggle's `aria-pressed=true` and its label flips to
   `Exit Playback`. Exiting wipes the store and the operator must
   reload to resume LIVE — there is intentionally no auto-resume so
   the audience cannot be tricked into thinking a stale fixture is
   current.

Per TECHNICAL_PLAN §12 the Permadeath trustless narrative is preserved
because: the fixture is a faithful replay of a real Track B run, and
Track D never invents a frame it didn't observe upstream.

---

## Regenerating the fixture

If Track B's E2E dry-run is updated, regenerate the committed fixture:

    npx tsx dashboard/ops/build_playback_fixture.ts

Or to verify the committed file is current without writing:

    npx tsx dashboard/ops/build_playback_fixture.ts --check

The transform is deterministic — same input ⇒ byte-identical output.
The committed file is what tests + the smoke gate check against.

---

## Six narrative beats covered

| # | Beat | WS frame | Operator-facing label |
|---|---|---|---|
| 1 | β₁ activation | `llm_activated` | `llm_activated` |
| 2 | First BET | `decision` where `payload.action === "BET"` | `first_bet` |
| 3 | Pressure ≥ 0.5 | `vitals.payload.breath` ≤ 50 (= 1 − breath/100 ≥ 0.5) | `pressure_half` |
| 4 | Terminal lucidity engaged | `terminal_lucidity_entered` | `terminal_lucidity_entered` |
| 5 | Last words on-chain | `last_words_emitted` | `last_words_emitted` |
| 6 | Tombstone minted | `tombstone_minted` | `tombstone_minted` |

The dedup ledger is a Set keyed by the operator-facing label, so
re-renders, WS reconnects, and polling-fallback overlap never cause
duplicate captures.

---

## What the smoke gate checks

- `npm run lint` exits 0
- `npm run build` exits 0
- `npm test` (vitest) — `PlaybackMode.spec.tsx` validates toggle,
  fixture ingest, banner anti-tamper, the recorder AST observation-only
  invariant, the `classify()` mapping, and the `NarrativeLedger` dedup.
- `npm run test:e2e` — `playback_smoke.spec.ts` verifies the toggle
  surfaces the banner with z-index ≥ 9999 against a real Chromium and
  that the fixture is served from `/playback_fixtures/`.

Failures here are blocking on T-D-005 acceptance.
