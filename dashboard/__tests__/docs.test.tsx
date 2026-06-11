import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import React from "react";

import "./setup";

import DocsPage from "../app/docs/page";
import RoadmapPage from "../app/roadmap/page";

/**
 * Smoke test — the /docs paper-trail page (contracts, runs, provenance).
 *
 * Same static-server-component pattern as the /mechanism smoke suite: RTL +
 * `import "./setup"`. Asserts the page renders under the .abyss scope, lists
 * all five deployed contract addresses with both explorer links, carries the
 * zero-mints honest note, the realism rules, the five-run record, and is
 * cross-linked from the roadmap landing.
 */

const ADDRESSES = [
  "0xDE6178D892AA9F80f748a399f07B588b08Faec2f", // TombstoneNFT
  "0x125929f6451e5e5Fa9C64b498646793CaF5b4128", // AgentLifecycle
  "0x3e58BE777F8fe7F1B81dfBdFA716295D0EF89818", // DecisionLog
  "0xeb504449195b0491F52b455650056f0763A54525", // EnergyController
  "0x20e07db0169E35553a66608736161f433d8E44E0", // PhaseManager
];

describe("DocsPage — paper-trail smoke", () => {
  it("renders under the .abyss design scope with the hero title", () => {
    render(<DocsPage />);
    const page = screen.getByTestId("docs-route");
    expect(page).toBeInTheDocument();
    expect(page).toHaveClass("abyss");
    expect(screen.getByRole("heading", { name: /docs/i })).toBeInTheDocument();
  });

  it("lists all five deployed contract addresses", () => {
    render(<DocsPage />);
    const contracts = screen.getByTestId("docs-contracts");
    for (const addr of ADDRESSES) {
      expect(within(contracts).getByText(addr)).toBeInTheDocument();
    }
  });

  it("links every contract to both explorers", () => {
    render(<DocsPage />);
    const contracts = screen.getByTestId("docs-contracts");
    const links = within(contracts)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href") ?? "");
    for (const addr of ADDRESSES) {
      expect(
        links.some(
          (h) =>
            h ===
            `https://explorer.testnet.chain.robinhood.com/address/${addr}`,
        ),
      ).toBe(true);
      expect(
        links.some((h) => h === `https://sepolia.arbiscan.io/address/${addr}`),
      ).toBe(true);
    }
  });

  it("carries the honest note that simulated deaths mint no NFTs", () => {
    render(<DocsPage />);
    const note = screen.getByTestId("docs-tombstone-note");
    expect(within(note).getByText(/no mints yet/i)).toBeInTheDocument();
    expect(
      within(note).getByText(/kill_and_mint_tombstone/i),
    ).toBeInTheDocument();
  });

  it("documents the realism rules (floor 0.05, cap $100)", () => {
    render(<DocsPage />);
    const realism = screen.getByTestId("docs-realism");
    expect(
      within(realism).getByText(/entry-price floor ≥ 0.05/i),
    ).toBeInTheDocument();
    expect(
      within(realism).getByText(/per-bet profit cap of \$100/i),
    ).toBeInTheDocument();
  });

  it("keeps every run on the record, including the v1 fluke runs", () => {
    render(<DocsPage />);
    const runs = screen.getByTestId("docs-runs");
    expect(within(runs).getByText("$17,469")).toBeInTheDocument(); // v1 AI
    expect(within(runs).getByText("$2,757")).toBeInTheDocument(); // v2 MiniMax
    expect(within(runs).getByText("$2,510")).toBeInTheDocument(); // v2 Gemini
    expect(within(runs).getByText("$1,668")).toBeInTheDocument(); // v2 numerical
  });

  it("documents the buildathon timeline with the provenance log link", () => {
    render(<DocsPage />);
    const timeline = screen.getByTestId("docs-timeline");
    expect(
      within(timeline).getByText(/480 commits across 22 active days/i),
    ).toBeInTheDocument();
    const prov = within(timeline).getByRole("link", {
      name: /provenance\.md/i,
    });
    expect(prov.getAttribute("href")).toBe(
      "https://github.com/balflee/autopoiesis/blob/main/PROVENANCE.md",
    );
  });

  it("renders all seven threaded sections and a ◂ lifeline back-link", () => {
    render(<DocsPage />);
    for (const id of [
      "docs-contracts",
      "docs-tombstone-note",
      "docs-realism",
      "docs-runs",
      "docs-data",
      "docs-timeline",
      "docs-stack",
    ]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    const back = screen.getByTestId("docs-back-link");
    expect(back.getAttribute("href")).toBe("/roadmap");
  });
});

describe("RoadmapPage — links to /docs", () => {
  it("renders a 'verify everything' link to /docs", () => {
    render(<RoadmapPage />);
    const link = screen.getByTestId("roadmap-docs-link");
    expect(link.getAttribute("href")).toBe("/docs");
    expect(link).toHaveTextContent(/verify everything/i);
  });
});
