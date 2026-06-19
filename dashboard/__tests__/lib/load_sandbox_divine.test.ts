import { describe, it, expect } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";

import { loadSandboxBundle } from "@/lib/load_sandbox_state.server";

async function tmpRoot(): Promise<string> {
  return await fs.mkdtemp(path.join(os.tmpdir(), "living-"));
}

describe("loadSandboxBundle divine streams", () => {
  it("reads treasury + deaths, computes cumulative + lineage", async () => {
    const root = await tmpRoot();
    await fs.writeFile(
      path.join(root, "gods_treasury.jsonl"),
      [
        JSON.stringify({ type: "tithe", tithe_id: "h1", ts: "t", tick: 20, paid_usd: 20, breath_cost: 0, breath_after: 80, bankroll_after: 980 }),
        JSON.stringify({ type: "tribute", tribute_id: "t1", ts: "t", tick: 40, amount_usd: 2000, success: true, breath_after: 35, bankroll_after: 0, dice_roll: 0.5 }),
        JSON.stringify({ type: "tribute", tribute_id: "t2", ts: "t", tick: 50, amount_usd: 600, success: false, breath_after: 0, bankroll_after: 0, dice_roll: 0.9 }),
      ].join("\n") + "\n",
    );
    await fs.writeFile(
      path.join(root, "deaths.jsonl"),
      JSON.stringify({ death_id: "d1", ts: "t", incarnation_number: 0, agent_id: "a", last_tick: 50, cause: "breath_zero", final_bankroll_usd: 0 }) + "\n",
    );
    const bundle = await loadSandboxBundle({ root });
    // cumulative = successful tributes ($2000) + cash tithes ($20); failed tribute NOT counted
    expect(bundle.gods_revenue_cumulative_usd).toBe(2020);
    expect(bundle.recent_gods_treasury).toHaveLength(3);
    expect(bundle.incarnation_lineage).toHaveLength(1);
    expect(bundle.incarnation_lineage[0]?.cause).toBe("breath_zero");
  });

  it("missing streams → empty, no throw", async () => {
    const root = await tmpRoot();
    const bundle = await loadSandboxBundle({ root });
    expect(bundle.recent_gods_treasury).toEqual([]);
    expect(bundle.gods_revenue_cumulative_usd).toBe(0);
    expect(bundle.incarnation_lineage).toEqual([]);
    expect(bundle.incarnation_number).toBe(0);
  });
});
