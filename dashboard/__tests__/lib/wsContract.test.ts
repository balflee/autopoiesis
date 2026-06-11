import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import "../setup";

import {
  KNOWN_KINDS_V0_2_0,
  WS_CONTRACT_VERSION,
} from "../../lib/wsContract";
import { KNOWN_KINDS } from "../../lib/types";
import { DEATH_WATCH_KINDS, DEATH_WATCH_CONTRACT_VERSION } from "../../lib/wsEvents";

/**
 * Verifies the TypeScript mirror in wsContract.ts + types.ts + wsEvents.ts
 * has not drifted from the two canonical wire schemas:
 *   - .dev/contracts/dashboard_ws_message.v0.3.0.json (12 main kinds)
 *   - .dev/contracts/dashboard_death_watch.v0.1.0.json (4 death-watch kinds)
 *
 * Acceptance criterion (T-D-003): "WS contract types in
 * dashboard/lib/wsContract.ts byte-identical to backend Pydantic
 * models — diff check against .dev/contracts/dashboard_ws_message.v0.3.0.json"
 *
 * Acceptance criterion (T-D-004): the death-watch type guards in
 * wsEvents.ts mirror the four kinds in dashboard_death_watch.v0.1.0.json.
 *
 * Concretely: each schema lists `oneOf` discriminants carrying a `kind`
 * const. We extract those consts and compare against KNOWN_KINDS_V0_2_0,
 * KNOWN_KINDS (= v0.2.0 + death-watch), and DEATH_WATCH_KINDS.
 */

function extractKinds(schemaPath: string): string[] {
  const raw = readFileSync(schemaPath, "utf8");
  const schema = JSON.parse(raw) as {
    version: string;
    oneOf: Array<{ $ref: string }>;
    $defs: Record<string, { properties?: { kind?: { const?: string } } }>;
  };
  const out: string[] = [];
  for (const o of schema.oneOf) {
    const defName = o.$ref.replace("#/$defs/", "");
    const k = schema.$defs[defName]?.properties?.kind?.const;
    if (typeof k === "string") out.push(k);
  }
  return out;
}

describe("wsContract drift check vs .dev/contracts schemas", () => {
  it("v0.2.0 main schema kinds match KNOWN_KINDS_V0_2_0", () => {
    // __dirname → dashboard/__tests__/lib ; up 3 = worktree root.
    const schemaPath = resolve(
      __dirname,
      "../../../.dev/contracts/dashboard_ws_message.v0.3.0.json",
    );
    const raw = readFileSync(schemaPath, "utf8");
    const schema = JSON.parse(raw) as { version: string };
    expect(schema.version).toBe(WS_CONTRACT_VERSION);
    const refKinds = extractKinds(schemaPath).sort();
    const tsKinds = [...KNOWN_KINDS_V0_2_0].sort();
    expect(tsKinds).toEqual(refKinds);
  });

  it("death_watch v0.1.0 schema kinds match DEATH_WATCH_KINDS", () => {
    const schemaPath = resolve(
      __dirname,
      "../../../.dev/contracts/dashboard_death_watch.v0.1.0.json",
    );
    const raw = readFileSync(schemaPath, "utf8");
    const schema = JSON.parse(raw) as { version: string };
    expect(schema.version).toBe(DEATH_WATCH_CONTRACT_VERSION);
    const refKinds = extractKinds(schemaPath).sort();
    const tsKinds = [...DEATH_WATCH_KINDS].sort();
    expect(tsKinds).toEqual(refKinds);
  });

  it("types.ts KNOWN_KINDS = v0.2.0 main kinds ∪ death_watch kinds", () => {
    const mainPath = resolve(
      __dirname,
      "../../../.dev/contracts/dashboard_ws_message.v0.3.0.json",
    );
    const deathPath = resolve(
      __dirname,
      "../../../.dev/contracts/dashboard_death_watch.v0.1.0.json",
    );
    const expected = [
      ...extractKinds(mainPath),
      ...extractKinds(deathPath),
    ].sort();
    const tsTypesKinds = [...KNOWN_KINDS].sort();
    expect(tsTypesKinds).toEqual(expected);
  });
});
