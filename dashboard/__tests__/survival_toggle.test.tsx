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
    smart_money: -0.2,
    sentiment_llm: 1,
    crowd_volume: 0,
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
