import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import React from "react";

import "./setup";

import SurvivalJourneyShell from "../app/survival/SurvivalJourneyShell";
import SurvivalModeToggle from "../app/survival/SurvivalModeToggle";
import {
  validateSurvivalJourney,
  type SurvivalJourneyFixture,
} from "@/lib/load_survival_journey";

/**
 * E-toggle UI gate — the Numerical/AI survival-journey switch.
 *
 * Asserts the two-option toggle:
 *   - shows both Numerical + AI options;
 *   - DISABLES the AI option (with a pending hint) when the AI journey
 *     is unavailable, and does NOT fire onChange when clicked;
 *   - switches to AI when available;
 * and that the shell re-feeds the headline from the selected journey.
 */

function weights(): Record<string, number> {
  return {
    w_r: 0.5,
    w_s: 0.5,
    alpha_0: 0.4,
    alpha_1: 0.35,
    alpha_2: 0.25,
    beta_0: 0.45,
    beta_1: 0.55,
    rho: 0.2,
  };
}
function signals(): Record<string, number> {
  return {
    tennis_technical: 0.6,
    market_momentum: 0.1,
    surface_advantage: -0.2,
    head_to_head: 1,
    rest_recency: 0,
  };
}
function market(id: string): Record<string, unknown> {
  return {
    entry_price: 0.6,
    market_id: id,
    outcome: "yes",
    players: ["alpha", "bravo"],
    slug: `wta-${id}`,
    surface: "Hard",
  };
}

/** A 1-life / 1-step fixture with a chosen learner final P&L for headline checks. */
function buildFixture(learnerPnl: number): SurvivalJourneyFixture {
  return validateSurvivalJourney({
    seed: { max_breath_risk_pct: 0.9, min_bet_size_usd: 4, min_confidence: 0.05, weights: weights() },
    summary: {
      best_life: 0,
      deaths: 0,
      learner_final_pnl: learnerPnl,
      learning_vs_static_delta: learnerPnl,
      lives: 1,
      static_final_pnl: 0,
      total_steps: 1,
    },
    lives: [
      {
        idx: 0,
        bets: 1,
        pnl: learnerPnl,
        death: null,
        final_bankroll_usd: 100 + learnerPnl,
        final_breath: 30,
        settlements: 1,
        start_ts: "2024-09-01T00:00:00+00:00",
      },
    ],
    steps: [
      {
        life_idx: 0,
        market: market("m1"),
        side: "YES",
        size: 4,
        pnl: learnerPnl,
        cum_pnl: learnerPnl,
        breath: 30,
        win_rate: 1,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
    ],
    baselines: {
      static: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      random: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      always_favorite: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
    },
  });
}

describe("SurvivalModeToggle", () => {
  it("renders both Numerical and AI options", () => {
    render(<SurvivalModeToggle mode="numerical" onChange={() => {}} aiAvailable />);
    const toggle = screen.getByTestId("survival-mode-toggle");
    expect(within(toggle).getByTestId("survival-mode-numerical").textContent).toContain("Numerical");
    expect(within(toggle).getByTestId("survival-mode-ai").textContent).toContain("AI");
  });

  it("disables AI with a pending hint when unavailable, and ignores clicks", () => {
    const onChange = vi.fn();
    render(<SurvivalModeToggle mode="numerical" onChange={onChange} aiAvailable={false} />);
    const ai = screen.getByTestId("survival-mode-ai") as HTMLButtonElement;
    expect(ai.disabled).toBe(true);
    expect(screen.getByTestId("survival-mode-ai-pending")).toBeInTheDocument();
    fireEvent.click(ai);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("fires onChange(ai) when AI is available and clicked", () => {
    const onChange = vi.fn();
    render(<SurvivalModeToggle mode="numerical" onChange={onChange} aiAvailable />);
    fireEvent.click(screen.getByTestId("survival-mode-ai"));
    expect(onChange).toHaveBeenCalledWith("ai");
  });
});

describe("SurvivalJourneyShell — toggle re-feeds the view", () => {
  it("defaults to numerical and shows the numerical headline", () => {
    render(<SurvivalJourneyShell numerical={buildFixture(11)} ai={buildFixture(99)} />);
    const numBtn = screen.getByTestId("survival-mode-numerical");
    expect(numBtn.getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("survival-headline").textContent).toContain("$11");
  });

  it("switches headline + view to the AI journey on toggle", () => {
    render(<SurvivalJourneyShell numerical={buildFixture(11)} ai={buildFixture(99)} />);
    fireEvent.click(screen.getByTestId("survival-mode-ai"));
    expect(screen.getByTestId("survival-mode-ai").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("survival-headline").textContent).toContain("$99");
  });

  it("keeps AI disabled + stays numerical when the AI journey is absent", () => {
    render(<SurvivalJourneyShell numerical={buildFixture(11)} ai={null} />);
    const ai = screen.getByTestId("survival-mode-ai") as HTMLButtonElement;
    expect(ai.disabled).toBe(true);
    fireEvent.click(ai);
    expect(screen.getByTestId("survival-mode-numerical").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("survival-headline").textContent).toContain("$11");
  });
});

describe("Finetune log — archived run1 exhibits", () => {
  /** A fixture whose summary carries the v2 rule-disclosure keys. */
  function buildV2Fixture(learnerPnl: number): SurvivalJourneyFixture {
    const raw = JSON.parse(JSON.stringify({
      seed: { max_breath_risk_pct: 0.9, min_bet_size_usd: 4, min_confidence: 0.05, weights: weights() },
      summary: {
        best_life: 0, deaths: 0, learner_final_pnl: learnerPnl,
        learning_vs_static_delta: learnerPnl, lives: 1, static_final_pnl: 0,
        total_steps: 1, entry_price_floor: 0.05, max_bet_pnl_usd: 100,
        proposals_applied: 7,
      },
      lives: [{ idx: 0, bets: 1, pnl: learnerPnl, death: null, final_bankroll_usd: 100 + learnerPnl, final_breath: 30, settlements: 1, start_ts: "2024-09-01T00:00:00+00:00" }],
      steps: [{ life_idx: 0, market: market("m1"), side: "YES", size: 4, pnl: learnerPnl, cum_pnl: learnerPnl, breath: 30, win_rate: 1, weights: weights(), weights_before: weights(), signals: signals() }],
      baselines: {
        static: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
        random: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
        always_favorite: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      },
    })) as unknown;
    return validateSurvivalJourney(raw);
  }

  it("renders no run1 toggle options and no v1 cards when archives are absent", () => {
    render(<SurvivalJourneyShell numerical={buildFixture(11)} ai={buildFixture(99)} />);
    expect(screen.queryByTestId("survival-mode-numerical_run1")).toBeNull();
    expect(screen.queryByTestId("survival-mode-ai_run1")).toBeNull();
    // The log still renders the current (v2) cards.
    expect(screen.getByTestId("survival-finetune-log")).toBeInTheDocument();
    expect(screen.queryByTestId("finetune-card-numerical_run1")).toBeNull();
    expect(screen.getByTestId("finetune-card-numerical")).toBeInTheDocument();
  });

  it("shows v1 toggle options + cards, with rules labels per version", () => {
    render(
      <SurvivalJourneyShell
        numerical={buildV2Fixture(11)}
        ai={buildV2Fixture(99)}
        numericalRun1={buildFixture(11879)}
        aiRun1={buildFixture(17469)}
      />,
    );
    // Toggle gains the archived options.
    expect(screen.getByTestId("survival-mode-numerical_run1")).toBeInTheDocument();
    expect(screen.getByTestId("survival-mode-ai_run1")).toBeInTheDocument();
    // v1 card: no rule keys → "no realism rules"; v2 card: discloses floor + cap.
    expect(screen.getByTestId("finetune-rules-numerical_run1").textContent).toContain("no realism rules");
    expect(screen.getByTestId("finetune-rules-numerical").textContent).toContain("0.05");
    expect(screen.getByTestId("finetune-rules-numerical").textContent).toContain("$100");
    // v2 AI card surfaces the applied-proposal count.
    expect(screen.getByTestId("finetune-card-ai").textContent).toContain("7");
  });

  it("clicking a v1 card switches the headline to the archived run", () => {
    render(
      <SurvivalJourneyShell
        numerical={buildV2Fixture(11)}
        ai={buildV2Fixture(99)}
        numericalRun1={buildFixture(11879)}
        aiRun1={buildFixture(17469)}
      />,
    );
    fireEvent.click(screen.getByTestId("finetune-card-ai_run1"));
    expect(screen.getByTestId("survival-mode-ai_run1").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("survival-headline").textContent).toContain("$17,469");
  });

  it("shows the Gemini provider leg as a toggle option + card when present", () => {
    render(
      <SurvivalJourneyShell
        numerical={buildV2Fixture(11)}
        ai={buildV2Fixture(99)}
        aiGemini={buildV2Fixture(2510)}
      />,
    );
    // Toggle gains the Gemini option; absent run1 archives stay hidden.
    expect(screen.getByTestId("survival-mode-ai_gemini")).toBeInTheDocument();
    expect(screen.queryByTestId("survival-mode-numerical_run1")).toBeNull();
    // Card present; clicking switches the headline to the Gemini run.
    fireEvent.click(screen.getByTestId("finetune-card-ai_gemini"));
    expect(screen.getByTestId("survival-mode-ai_gemini").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("survival-headline").textContent).toContain("$2,510");
  });

  it("hides the Gemini option when its artifact is absent", () => {
    render(<SurvivalJourneyShell numerical={buildFixture(11)} ai={buildFixture(99)} />);
    expect(screen.queryByTestId("survival-mode-ai_gemini")).toBeNull();
    expect(screen.queryByTestId("finetune-card-ai_gemini")).toBeNull();
  });
});


describe("Realism v3 — eight modes + typed summary keys + chapter 3", () => {
  it("renders all eight toggle entries when every artifact is available", () => {
    render(
      <SurvivalModeToggle
        mode="numerical"
        onChange={() => {}}
        aiAvailable
        aiGeminiAvailable
        numericalRun1Available
        aiRun1Available
        numericalRun2Available
        aiRun2Available
        aiGeminiRun2Available
      />,
    );
    const toggle = screen.getByTestId("survival-mode-toggle");
    const buttons = within(toggle).getAllByRole("radio");
    expect(buttons).toHaveLength(8);
    for (const id of [
      "survival-mode-numerical",
      "survival-mode-ai",
      "survival-mode-ai_gemini",
      "survival-mode-numerical_run1",
      "survival-mode-ai_run1",
      "survival-mode-numerical_run2",
      "survival-mode-ai_run2",
      "survival-mode-ai_gemini_run2",
    ]) {
      expect(within(toggle).getByTestId(id)).toBeInTheDocument();
    }
  });

  it("validates + surfaces the six v3 summary keys on the finetune card", () => {
    const v3 = validateSurvivalJourney({
      ...JSON.parse(JSON.stringify(fixtureRawForV3())),
    });
    expect(v3.summary.side_correct_pricing).toBe(true);
    expect(v3.summary.value_betting).toBe(true);
    expect(v3.summary.min_edge).toBeCloseTo(0.035);
    expect(v3.summary.kappa).toBeCloseTo(0.49);
    expect(v3.summary.effective_entry_price_floor).toBe(0.05);
    expect(v3.summary.min_effective_entry_price).toBe(0.05);

    render(
      <SurvivalJourneyShell numerical={v3} ai={null} />,
    );
    const log = screen.getByTestId("survival-finetune-log");
    const rules = within(log).getByTestId("finetune-rules-numerical");
    expect(rules.textContent).toContain("side-correct pricing");
    expect(rules.textContent).toContain("EV-gated");
    // The living-system markers: still-learning chip + open-ended coda.
    expect(screen.getByTestId("survival-still-learning").textContent).toContain(
      "still learning",
    );
    expect(screen.getByTestId("finetune-ongoing").textContent).toContain(
      "open-ended",
    );
    // Chapter-3 narrative block is present.
    expect(screen.getByTestId("finetune-chapter-3").textContent).toContain(
      "EV-gated value betting",
    );
  });

  it("rejects a non-boolean side_correct_pricing (typed validation, r4 L-1)", () => {
    const raw = fixtureRawForV3() as { summary: Record<string, unknown> };
    raw.summary.side_correct_pricing = "yes";
    expect(() => validateSurvivalJourney(raw)).toThrow(/expected boolean/);
  });
});

/** Raw (un-validated) v3 fixture JSON with the six new summary keys. */
function fixtureRawForV3(): unknown {
  return {
    seed: {
      max_breath_risk_pct: 0.38,
      min_bet_size_usd: 4,
      min_confidence: 0.08,
      weights: weights(),
    },
    summary: {
      best_life: 0,
      deaths: 0,
      learner_final_pnl: 42,
      learning_vs_static_delta: 42,
      lives: 1,
      static_final_pnl: 0,
      total_steps: 1,
      entry_price_floor: 0.05,
      max_bet_pnl_usd: 100,
      side_correct_pricing: true,
      value_betting: true,
      min_edge: 0.035,
      kappa: 0.49,
      effective_entry_price_floor: 0.05,
      min_effective_entry_price: 0.05,
    },
    lives: [
      {
        idx: 0,
        bets: 1,
        pnl: 42,
        death: null,
        final_bankroll_usd: 142,
        final_breath: 30,
        settlements: 1,
        start_ts: "2024-09-01T00:00:00+00:00",
      },
    ],
    steps: [
      {
        life_idx: 0,
        market: market("m1"),
        side: "YES",
        size: 4,
        pnl: 42,
        cum_pnl: 42,
        breath: 30,
        win_rate: 1,
        weights: weights(),
        weights_before: weights(),
        signals: signals(),
      },
    ],
    baselines: {
      static: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      random: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
      always_favorite: [{ idx: 0, cum_pnl: 0, pnl: 0, is_bet: false, market_id: "m1", side: null, size: 0 }],
    },
  };
}

describe("Phase framing — the Phase-1 banner", () => {
  it("renders the phase-1 chip + the /reincarnation cross-link", () => {
    render(<SurvivalJourneyShell numerical={buildFixture(11)} ai={null} />);
    const banner = screen.getByTestId("survival-phase1-banner");
    expect(banner.textContent).toMatch(/phase 1/i);
    const link = within(banner).getByRole("link");
    expect(link.getAttribute("href")).toBe("/reincarnation");
  });
});
