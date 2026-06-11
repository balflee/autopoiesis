/**
 * PlaybackMode.spec.tsx — T-D-005 acceptance suite.
 *
 * Six independent assertions cover the brief's hard invariants:
 *
 *   1. Toggling the button engages PLAYBACK and renders the persistent
 *      banner; the banner sits at z-[9999], above DeathWatch (z-50) and
 *      the LLM activation overlay (z-[60]).
 *   2. Fixture playback drains into the wsStore. With timeScale=0 every
 *      frame ingests synchronously; we verify the store snapshot matches
 *      the curated 5-minute scenario.
 *   3. The MutationObserver anti-tamper guard re-mounts the banner if a
 *      script removes it from the DOM.
 *   4. parseJsonl drops malformed lines + frames that fail isWsMessage.
 *   5. key_moment_capture classify() maps each of the six narrative beats
 *      onto the matching narrative id and rejects unrelated kinds.
 *   6. NarrativeLedger dedups: the same event id is accepted once, then
 *      every subsequent attempt is rejected — independent of frame seq.
 *   7. recorder.ts AST scan — observation-only invariant. The file MUST
 *      NOT contain any forbidden token that would let the recorder
 *      influence the dashboard's state (TECHNICAL_PLAN §12).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

// Setup file — registers per-test cleanup. Repeated import pattern from
// dashboard/__tests__/playback.test.tsx (see vitest.config.ts note).
import "./setup";

import {
  PlaybackMode,
  __TEST__ as PB_INTERNALS,
} from "../components/PlaybackMode";
import { useWsStore } from "../lib/wsStore";
import {
  classify,
  NarrativeLedger,
  NARRATIVE_EVENT_IDS,
  __TEST__ as KMC_INTERNALS,
} from "../ops/key_moment_capture";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const FIXTURE_PATH = resolve(
  __dirname,
  "../../public/playback_fixtures/golden_scenario_5min.jsonl",
);
// Note: when vitest cwd is dashboard/, __dirname is dashboard/__tests__/,
// so ../../ resolves to repo root and we land in public/playback_fixtures/.

function loadFixtureFrames(): unknown[] {
  const raw = readFileSync(FIXTURE_PATH, "utf8");
  return raw
    .split(/\r?\n/)
    .filter((l) => l.trim().length > 0)
    .map((l) => JSON.parse(l));
}

describe("PlaybackMode — engage / disengage", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("renders the toggle button and ENGAGES on click", () => {
    render(<PlaybackMode timeScale={0} fixtureFrames={[]} />);

    // Banner is NOT in the DOM in LIVE mode.
    expect(screen.queryByTestId(PB_INTERNALS.BANNER_TEST_ID)).toBeNull();

    const toggle = screen.getByTestId(PB_INTERNALS.TOGGLE_TEST_ID);
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "true");
    const banner = screen.getByTestId(PB_INTERNALS.BANNER_TEST_ID);
    expect(banner).toBeInTheDocument();
    expect(banner.className).toMatch(/z-\[9999\]/);
    expect(banner.textContent).toMatch(/demo playback/i);
  });

  it("disengaging WIPES the store (operator must reload to resume LIVE)", () => {
    render(
      <PlaybackMode
        timeScale={0}
        fixtureFrames={loadFixtureFrames()}
        initialEngaged={true}
      />,
    );
    // After mount with fixture, the store should be populated.
    expect(useWsStore.getState().vitals).not.toBeNull();

    fireEvent.click(screen.getByTestId(PB_INTERNALS.TOGGLE_TEST_ID));

    // Banner removed AND store reset.
    expect(screen.queryByTestId(PB_INTERNALS.BANNER_TEST_ID)).toBeNull();
    expect(useWsStore.getState().vitals).toBeNull();
  });
});

describe("PlaybackMode — fixture drains into wsStore", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("ingests every frame of the 5-minute scenario when timeScale=0", () => {
    const frames = loadFixtureFrames();
    render(
      <PlaybackMode
        timeScale={0}
        fixtureFrames={frames}
        initialEngaged={true}
      />,
    );

    const s = useWsStore.getState();
    // The fixture closes on a vitals(breath=0) frame at 05:00.
    expect(s.vitals?.breath).toBe(0);
    expect(s.vitals?.phase).toBe("PHASE_4_TERMINAL");
    // All six narrative beats must have landed:
    expect(s.llmActivated).toBe(true);
    expect(s.terminalLucidityEntered).toBe(true);
    expect(s.lastWordsEntry).not.toBeNull();
    expect(s.tombstone?.token_id).toBe("1");
    expect(s.causeOfDeath).toBe("TERMINAL_LUCIDITY_COMPLETED");
  });

  it("parseJsonl drops malformed lines + non-WsMessage objects", () => {
    const sample = [
      '{"kind":"vitals","ts":"2026-05-22T00:00:00.000Z","seq":1,"payload":{"breath":80,"bankroll":150,"countdown_s":3600,"gas_per_min":0.42,"phase":"PHASE_2_APPRENTICE"}}',
      "{not-json}",
      '{"kind":"unknown_kind","ts":"x","seq":1}',
      '{"kind":"thought","ts":"2026-05-22T00:00:05.000Z","seq":2,"text":"ok"}',
      "",
    ].join("\n");

    const parsed = PB_INTERNALS.parseJsonl(sample);
    expect(parsed.map((p) => p.kind)).toEqual(["vitals", "thought"]);
  });
});

describe("PlaybackMode — banner anti-tamper", () => {
  beforeEach(() => useWsStore.getState().reset());

  it("re-mounts the banner after a script removes it from the DOM", async () => {
    render(
      <PlaybackMode timeScale={0} fixtureFrames={[]} initialEngaged={true} />,
    );

    const first = screen.getByTestId(PB_INTERNALS.BANNER_TEST_ID);
    expect(first).toBeInTheDocument();

    // Simulate hostile script: remove the banner from the DOM.
    first.remove();
    expect(document.querySelector(`[data-testid="${PB_INTERNALS.BANNER_TEST_ID}"]`)).toBeNull();

    // The MutationObserver heartbeat fires on the next 500ms tick. We
    // poll because jsdom's MutationObserver is microtask-based but the
    // heartbeat is a setInterval; flush both by waiting on the next
    // macrotask plus the 500ms.
    await new Promise((r) => setTimeout(r, 600));

    const reattached = document.querySelector(
      `[data-testid="${PB_INTERNALS.BANNER_TEST_ID}"]`,
    );
    expect(reattached).not.toBeNull();
  }, 5000);

  it("bannerNodeOK rejects nodes whose inline style hides them", () => {
    const div = document.createElement("div");
    document.body.appendChild(div);
    expect(PB_INTERNALS.bannerNodeOK(div)).toBe(true);

    div.style.display = "none";
    expect(PB_INTERNALS.bannerNodeOK(div)).toBe(false);

    div.style.display = "";
    div.style.visibility = "hidden";
    expect(PB_INTERNALS.bannerNodeOK(div)).toBe(false);

    div.style.visibility = "";
    div.style.opacity = "0.1";
    expect(PB_INTERNALS.bannerNodeOK(div)).toBe(false);

    div.remove();
    expect(PB_INTERNALS.bannerNodeOK(div)).toBe(false);
  });
});

describe("key_moment_capture — classify() + dedup ledger", () => {
  it("maps each of the six narrative beats to its event id", () => {
    expect(classify({
      kind: "llm_activated",
      ts: "2026-05-22T00:00:30.000Z",
      seq: 4,
    })).toBe("llm_activated");

    expect(classify({
      kind: "decision",
      ts: "2026-05-22T00:01:15.000Z",
      seq: 9,
      payload: { action: "BET", side: "BOS_-3.5", size_usd: 40 },
    })).toBe("first_bet");

    expect(classify({
      kind: "decision",
      ts: "2026-05-22T00:01:30.000Z",
      seq: 10,
      payload: { action: "NO_BET" },
    })).toBeNull();

    expect(classify({
      kind: "vitals",
      ts: "2026-05-22T00:02:00.000Z",
      seq: 12,
      payload: {
        breath: 50,
        bankroll: 152,
        countdown_s: 3480,
        gas_per_min: 0.48,
        phase: "PHASE_3_MASTER",
      },
    })).toBe("pressure_half");

    expect(classify({
      kind: "vitals",
      ts: "2026-05-22T00:00:00.000Z",
      seq: 1,
      payload: {
        breath: 80,
        bankroll: 150,
        countdown_s: 3600,
        gas_per_min: 0.42,
        phase: "PHASE_2_APPRENTICE",
      },
    })).toBeNull();

    expect(classify({
      kind: "terminal_lucidity_entered",
      ts: "2026-05-22T00:04:01.000Z",
      seq: 23,
      breath_at_entry: 9.8,
    })).toBe("terminal_lucidity_entered");

    expect(classify({
      kind: "last_words_emitted",
      ts: "2026-05-22T00:04:30.000Z",
      seq: 25,
      text: "...",
    })).toBe("last_words_emitted");

    expect(classify({
      kind: "tombstone_minted",
      ts: "2026-05-22T00:04:45.000Z",
      seq: 26,
      token_id: "1",
      ipfs_degraded: false,
    })).toBe("tombstone_minted");
  });

  it("NarrativeLedger accepts each id exactly once, even across replays", () => {
    const ledger = new NarrativeLedger();
    for (const id of NARRATIVE_EVENT_IDS) {
      expect(ledger.accept(id)).toBe(true);
      // Replay (e.g. React re-render or WS reconnect) is rejected.
      expect(ledger.accept(id)).toBe(false);
      expect(ledger.accept(id)).toBe(false);
    }
    expect(ledger.firedCount).toBe(NARRATIVE_EVENT_IDS.length);
  });

  it("NARRATIVE_EVENT_IDS pin matches the brief's six beats", () => {
    expect([...KMC_INTERNALS.NARRATIVE_EVENT_IDS].sort()).toEqual(
      [
        "first_bet",
        "last_words_emitted",
        "llm_activated",
        "pressure_half",
        "terminal_lucidity_entered",
        "tombstone_minted",
      ].sort(),
    );
  });
});

describe("recorder.ts — observation-only AST invariant", () => {
  const RECORDER_PATH = resolve(__dirname, "../ops/recorder.ts");
  const src = readFileSync(RECORDER_PATH, "utf8");

  // Strip block + line comments — the docstring documents the forbidden
  // list, so a naive substring scan would false-positive. The contract
  // is: forbidden tokens MUST NOT appear in executable code.
  const stripped = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:'"`])\/\/.*$/gm, "$1");

  const FORBIDDEN = [
    "page.evaluate",
    "addInitScript",
    "exposeFunction",
    "exposeBinding",
    "__GENESIS_PUSH_WS__",
    "__GENESIS_MOCK_WS__",
    "useWsStore",
    ".send(",
  ];

  for (const tok of FORBIDDEN) {
    it(`recorder.ts must NOT reference \`${tok}\``, () => {
      expect(stripped.includes(tok)).toBe(false);
    });
  }

  it("recorder.ts uses Playwright's passive recordVideo API", () => {
    // Positive control — we expect SOME playwright usage so the scan
    // does not pass on an empty file.
    expect(stripped).toMatch(/recordVideo/);
    expect(stripped).toMatch(/newContext/);
  });
});
