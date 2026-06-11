# Genesis Dashboard — Frontend

Track D's Next.js 15 (App Router) surface for the Genesis Experiment demo.

This README covers the **sprint_3 slice (T-D-002)**: the three week-1 demo-
critical components — VitalsPanel, DualEngineMeter, and LIVE Consciousness-
Stream typewriter — wired through a typed WebSocket client + Zustand store
with a 2-second polling fallback. The Phase 2 Day 4 PLAYBACK takeover from
sprint_1 (T-D-001) is preserved as a fullscreen overlay during the demo arc.

## Repo layout

```
dashboard/                       # Next.js project root
  package.json
  next.config.ts
  tailwind.config.ts
  postcss.config.js
  tsconfig.json
  vitest.config.ts
  playwright.config.ts           # NEW — Playwright smoke harness
  .eslintrc.json                 # NEW — strict next/core-web-vitals
  app/
    layout.tsx                   # root html / body, dark theme
    page.tsx                     # NEW — mounts the three components
    globals.css                  # tailwind base + dark color floor
  components/
    VitalsPanel.tsx              # NEW — BREATH / bankroll / countdown / phase
    DualEngineMeter.tsx          # NEW — W_R/W_S band + α/β₁/ρ chips
    WsBootstrap.tsx              # NEW — boots WsClient, listens to mock seam
    ConsciousnessStream/
      index.tsx                  # PLAYBACK ↔ LIVE switch
      PlaybackTakeover.tsx       # sprint_1 fullscreen narrative arc
      LiveStream.tsx             # NEW — WS-driven typewriter feed
      usePlaybackController.ts   # keyboard / auto-play state machine
  lib/
    memoryBank.ts                # sprint_1 — PLAYBACK snapshot types
    colorTokens.ts               # PRD §8 four-token palette
    types.ts                     # NEW — WsMessage union (TP §5.4)
    ws-client.ts                 # NEW — WS client + 2 s polling fallback
    wsStore.ts                   # NEW — Zustand projection (mock seam)
  scripts/
    lighthouse.mjs               # NEW — perf+a11y gate runner
  screenshots/T-D-002/           # Playwright artefacts (mobile + desktop)
  __tests__/                     # CONSOLIDATED — was tests/dashboard/
    setup.ts
    playback.test.tsx
    components/                  # vitest unit suites
      VitalsPanel.test.tsx
      DualEngineMeter.test.tsx
      ConsciousnessStream.test.tsx
    lib/
      ws-client.test.ts
    playwright/
      dashboard_smoke.spec.ts    # NEW — playwright smoke

public/snapshots/                # repo-root static asset
  phase2_day4_first_twitter_mistake.json   # 5-tick curated arc (≤50 KB)

.dev/contracts/
  dashboard_consciousness_stream.v0.1.0.json   # sprint_1 PLAYBACK contract
  dashboard_ws_message.v0.1.0.json             # NEW — WS wire schema
  _registry.json                                # bumped to add dashboard_ws_message
```

> Test relocation rationale: the sprint_1 setup had `tests/dashboard/` at
> the repo root, but vitest + Next.js could not resolve `vitest` /
> `@testing-library/react` from there because `node_modules/` lives under
> `dashboard/`. Both `npm run build` and `npm test` failed against the
> T-D-001 baseline. T-D-002 relocates the tests to `dashboard/__tests__/`
> (still inside the Track D allowlist — `dashboard/**`).

## Running locally

```bash
cd dashboard
npm install
npm run dev               # http://localhost:3000 — PLAYBACK auto-plays
npm test                  # Vitest unit suite (28 tests)
npm run build && npm run start -- -p 3100
npm run test:e2e          # Playwright smoke (mobile + desktop)
npm run lighthouse        # perf ≥80, a11y ≥95
```

## WebSocket contract

The dashboard consumes a typed message union mirrored at
`.dev/contracts/dashboard_ws_message.v0.1.0.json`. Adding a new kind is a
major-version bump and requires every consumer to update.

| Kind                          | Payload                          |
|-------------------------------|----------------------------------|
| `vitals`                      | breath / bankroll / phase / etc. |
| `thought`                     | text (typewriter feed)           |
| `decision`                    | BET / NO_BET with sizing         |
| `reflection`                  | insight string                   |
| `weights_updated`             | { w_r, w_s, α, β, ρ }            |
| `llm_activated`               | latch event                      |
| `desperate_mode_entered`      | latch event                      |
| `terminal_lucidity_start`     | latch event                      |
| `last_words`                  | text                             |
| `death`                       | cause-of-death enum              |

Each frame carries `ts` (ISO-8601) + `seq` (monotonic dedup key). Frames
with `seq` ≤ last-seen are dropped silently.

### Polling fallback (TP §10 risk 1c)

If the WS is silent for ≥ 5 s, the client transitions to
`polling_fallback` and hits `NEXT_PUBLIC_STATE_POLL_URL` every 2 s until
fresh frames arrive — at which point it demotes back to `open`. The
connection state is surfaced on the VitalsPanel badge so the demo team
can see the failover live.

## Mock seam

Two paths to inject mocks without a real backend:

1. **Vitest** — call `useWsStore.getState().ingest({ kind: …, … })`.
2. **Playwright / Storybook / manual QA** — set
   `window.__GENESIS_MOCK_WS__ = [WsMessage, …]` (e.g. via
   `page.addInitScript`). `WsBootstrap` ingests these synchronously and
   skips the real WS.

## PLAYBACK contract

Sprint_1's PLAYBACK takeover still wins on first load (the demo flow
auto-plays). Keyboard contract per PRD §8:

| Key            | Action                          |
|----------------|---------------------------------|
| `Space`        | Toggle play / pause             |
| `ArrowRight`   | Step forward one tick (pauses)  |
| `ArrowLeft`    | Step back one tick (pauses)     |
| `Escape`       | Exit PLAYBACK, drop into LIVE   |

In LIVE mode the typewriter `LiveStream` renders WS thoughts; the
VitalsPanel + DualEngineMeter remain visible underneath because the
PLAYBACK overlay uses `fixed inset-0 z-40` only while active.

## Color tokens — PRD §8 only

| Token | Hex       | Use                                          |
|-------|-----------|----------------------------------------------|
| BG    | `#0B1426` | Background                                   |
| LOSS  | `#E63946` | LOSS outcomes, BREATH ≤ 10 %                 |
| WIN   | `#06D6A0` | WIN outcomes, BREATH bar                     |
| AMBER | `#FFB703` | Signal dominance, β unlocked, focus rings    |

Grey scale (`INK #F5F7FA`, `INK_MUTED #9FB0C4`) is derived for AAA
contrast against `#0B1426` (16:1 and 5.6:1 respectively).

## Phase 1 β-freeze visual

When the Agent is in Phase 1, Track B ships `weights.beta = 0`. The
DualEngineMeter explicitly renders the β₁ chip as `FROZEN` with reduced
opacity — the audience SEES the unlock the moment Phase 1 → Phase 2.

## Contracts touched (T-D-002)

- **NEW** `dashboard_ws_message` @ `v0.1.0` (status `draft`) — wire
  schema for the WebSocket Message union (10 kinds).
- Existing `dashboard_consciousness_stream` @ `v0.1.0` unchanged.

## What this slice intentionally does NOT do

- Build EvolutionCurve / DecisionFeed / DeathWatch — sprint_4 / 5 scope.
- Read on-chain events via viem — sprint_4 scope (Track A T-A-006 dep).
- Render the memory-bank PLAYBACK V2 mode — sprint_5 scope.
- Connect to a real Polymarket / chain pipe — Dashboard talks to Agent
  ONLY through the WS contract.
