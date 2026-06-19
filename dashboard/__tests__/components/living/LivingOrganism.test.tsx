import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

import "../../setup";

import { useWsStore } from "@/lib/wsStore";
import { LivingOrganism } from "@/components/living/LivingOrganism";

describe("LivingOrganism", () => {
  beforeEach(() => useWsStore.getState().reset());
  it("renders breath, bankroll, incarnation, alive state", () => {
    useWsStore.setState({ vitals: { breath: 72, bankroll: 1240, countdown_s: 0, gas_per_min: 0, phase: "PHASE_2_APPRENTICE" }, incarnationNumber: 3 } as any);
    render(<LivingOrganism />);
    expect(screen.getByTestId("organism-breath").textContent).toContain("72");
    expect(screen.getByTestId("organism-bankroll").textContent).toContain("1,240");
    expect(screen.getByTestId("organism-incarnation").textContent).toContain("3");
    expect(screen.getByTestId("organism-state").textContent).toMatch(/ALIVE/i);
  });
  it("shows DYING when breath <= 10", () => {
    useWsStore.setState({ vitals: { breath: 8, bankroll: 5, countdown_s: 0, gas_per_min: 0, phase: "PHASE_2_APPRENTICE" } } as any);
    render(<LivingOrganism />);
    expect(screen.getByTestId("organism-state").textContent).toMatch(/DYING/i);
  });
});
