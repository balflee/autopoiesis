import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import SurvivalModeToggle from "../app/survival/SurvivalModeToggle";
import {
  BackLinkDim,
  NextLink,
  StageShell,
} from "../components/lifeline/StageShell";
import { STAGE_META } from "@/lib/lifeline";

/**
 * G4 — ACCESSIBILITY suite for the four abyss pages + shared shell.
 *
 * Three concerns are pinned here:
 *   1. ARIA — the Numerical/AI toggle exposes its selected state as a radiogroup
 *      (role=radio + aria-checked) and every option has a discernible name;
 *      the shared shell's lifeline back-link + the footer cross-links carry
 *      aria-labels (their visible text is glyph-prefixed: ◂ / ▸).
 *   2. SKIP-LINK — the shared layout renders a skip-to-content link as the
 *      first focusable element, targeting <main id="main-content">.
 *   3. REDUCED-MOTION — globals.css neutralizes EVERY abyss animation under
 *      `prefers-reduced-motion: reduce`, including the names added since the
 *      original block (the Tailwind animate-* utilities riding on reused
 *      widgets + the skip-link tween).
 *
 * (1)+(2-shell) are DOM assertions; (2-layout)+(3) are source/structure
 * checks (the layout renders <html>/<body>, which RTL cannot mount, and the
 * media-query coverage is a CSS fact, not a rendered one).
 */

const REPO_DASHBOARD = path.resolve(__dirname, "..");
const readSrc = (rel: string): string =>
  readFileSync(path.join(REPO_DASHBOARD, rel), "utf8");

/* ------------------------------------------------------------------ */
/* 1. ARIA — the survival toggle exposes its selected state            */
/* ------------------------------------------------------------------ */

describe("a11y · SurvivalModeToggle aria state", () => {
  it("is a radiogroup with radio options exposing aria-checked", () => {
    render(
      <SurvivalModeToggle mode="numerical" onChange={() => {}} aiAvailable />,
    );
    const group = screen.getByRole("radiogroup", {
      name: /numerical vs ai/i,
    });
    expect(group).toBeInTheDocument();

    const numerical = within(group).getByTestId("survival-mode-numerical");
    const ai = within(group).getByTestId("survival-mode-ai");

    // role=radio + the selected option is checked, the other is not.
    expect(numerical).toHaveAttribute("role", "radio");
    expect(ai).toHaveAttribute("role", "radio");
    expect(numerical).toHaveAttribute("aria-checked", "true");
    expect(ai).toHaveAttribute("aria-checked", "false");
  });

  it("moves aria-checked onto the AI option when AI is the active mode", () => {
    render(<SurvivalModeToggle mode="ai" onChange={() => {}} aiAvailable />);
    expect(screen.getByTestId("survival-mode-ai")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("survival-mode-numerical")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("gives every option a discernible accessible name", () => {
    render(
      <SurvivalModeToggle mode="numerical" onChange={() => {}} aiAvailable />,
    );
    // The visible label is a short token (Numerical / AI); the aria-label
    // carries the full human-readable name so AT does not announce a bare
    // token with no context.
    const numerical = screen.getByTestId("survival-mode-numerical");
    const ai = screen.getByTestId("survival-mode-ai");
    expect(numerical.getAttribute("aria-label")).toMatch(/Numerical/);
    expect(ai.getAttribute("aria-label")).toMatch(/AI/);
  });

  it("marks the disabled AI option as pending in its accessible name", () => {
    render(
      <SurvivalModeToggle
        mode="numerical"
        onChange={() => {}}
        aiAvailable={false}
      />,
    );
    const ai = screen.getByTestId("survival-mode-ai") as HTMLButtonElement;
    expect(ai.disabled).toBe(true);
    expect(ai.getAttribute("aria-label")).toMatch(/pending/i);
  });
});

/* ------------------------------------------------------------------ */
/* 1b. ARIA — the shared shell + cross-links have discernible names     */
/* ------------------------------------------------------------------ */

describe("a11y · shared shell aria-labels", () => {
  it("the lifeline back-link in the shell has an aria-label + points to /roadmap", () => {
    render(
      <StageShell meta={STAGE_META.survival}>
        <p>body</p>
      </StageShell>,
    );
    const back = screen.getByTestId(STAGE_META.survival.backLinkTestId);
    expect(back).toHaveAttribute("aria-label", "Back to the lifeline overview");
    expect(back).toHaveAttribute("href", "/roadmap");
  });

  it("the <main> carries id=main-content so the skip-link can target it", () => {
    render(
      <StageShell meta={STAGE_META.survival}>
        <p>body</p>
      </StageShell>,
    );
    expect(
      screen.getByTestId(STAGE_META.survival.testId).getAttribute("id"),
    ).toBe("main-content");
  });

  it("NextLink + BackLinkDim forward an aria-label (their text is glyph-prefixed)", () => {
    render(
      <>
        <NextLink href="/survival" ariaLabel="Next stage: learning to survive">
          next · learning to survive ▸
        </NextLink>
        <BackLinkDim href="/backtest" ariaLabel="Back to the seed">
          ◂ back to the seed
        </BackLinkDim>
      </>,
    );
    expect(
      screen.getByRole("link", { name: "Next stage: learning to survive" }),
    ).toHaveAttribute("href", "/survival");
    expect(
      screen.getByRole("link", { name: "Back to the seed" }),
    ).toHaveAttribute("href", "/backtest");
  });
});

/* ------------------------------------------------------------------ */
/* 2. SKIP-LINK — present in the shared layout + styled in globals      */
/* ------------------------------------------------------------------ */

describe("a11y · skip-to-content link", () => {
  const layoutSrc = readSrc("app/layout.tsx");
  const cssSrc = readSrc("app/globals.css");

  it("the shared layout renders a skip-to-content anchor targeting #main-content", () => {
    expect(layoutSrc).toMatch(/className="skip-to-content"/);
    expect(layoutSrc).toMatch(/href="#main-content"/);
  });

  it("globals.css defines the .skip-to-content style with a visible :focus state", () => {
    expect(cssSrc).toMatch(/\.skip-to-content\s*\{/);
    expect(cssSrc).toMatch(/\.skip-to-content:focus/);
  });
});

/* ------------------------------------------------------------------ */
/* 3. REDUCED-MOTION — the abyss block covers every animation name      */
/* ------------------------------------------------------------------ */

describe("a11y · reduced-motion coverage in globals.css", () => {
  const cssSrc = readSrc("app/globals.css");

  // Isolate the `.abyss` prefers-reduced-motion block so we assert names are
  // neutralized THERE (not merely defined somewhere in the file).
  const reducedBlock = (() => {
    const idx = cssSrc.indexOf("prefers-reduced-motion: reduce");
    // grab from the first reduced-motion query to end of file (the abyss block
    // is the last one in globals.css).
    return idx >= 0 ? cssSrc.slice(idx) : "";
  })();

  it("contains an abyss prefers-reduced-motion block", () => {
    expect(reducedBlock).not.toBe("");
    expect(reducedBlock).toMatch(/\.abyss/);
  });

  // Every animation that can run on an abyss page must be referenced (by the
  // class that consumes its keyframes) inside the reduced-motion block.
  const SELECTORS = [
    ".abyss::after", // ab-sweep telemetry sheen
    ".abyss .ab-breath-line", // ab-breath-draw / ab-breath-glow heartbeat
    ".abyss .ab-pulse-node", // ab-node-pulse active node
    ".abyss .ab-pulse-dot", // ab-dot-pulse active dot
    ".abyss .ab-reveal", // ab-reveal-in staggered reveal
    ".abyss .ab-hero-in", // ab-hero-in hero entrance
    ".abyss .animate-pulse", // Tailwind caret + skeleton loaders (/mock)
  ];

  it.each(SELECTORS)("neutralizes %s under reduced motion", (selector) => {
    expect(reducedBlock).toContain(selector);
  });

  it("forces animation:none and tames transitions in the abyss block", () => {
    expect(reducedBlock).toMatch(/animation:\s*none\s*!important/);
    // The blanket `.abyss *` transition clamp kills per-step / hover tweens.
    expect(reducedBlock).toMatch(/\.abyss \*/);
    expect(reducedBlock).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });

  it("pins the reveal/hero entrances to their end state (so content is visible)", () => {
    expect(reducedBlock).toMatch(/opacity:\s*1/);
    expect(reducedBlock).toMatch(/transform:\s*none/);
  });
});
