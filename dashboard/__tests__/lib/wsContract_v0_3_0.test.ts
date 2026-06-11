import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import "../setup";

import { WS_CONTRACT_VERSION } from "../../lib/wsContract";
import type {
  DecisionFeedEntry,
  DecisionPayload,
} from "../../lib/types";
import { isWsMessage } from "../../lib/types";

/**
 * F0 lockstep — verifies the TS mirrors (wsContract.ts + types.ts) carry
 * the v0.3.0 additive fields (market_id / bet_id / signals on BOTH the
 * decision payload and the decision_feed entry) and stay in sync with
 * .dev/contracts/dashboard_ws_message.v0.3.0.json.
 *
 * The three new fields are OPTIONAL, so every v0.2.0-shaped object still
 * type-checks; this suite asserts the new fields are ACCEPTED (compile-
 * time) and that the runtime isWsMessage guard is unaffected (additive
 * fields don't change the discriminant set).
 */

const SCHEMA_PATH = resolve(
  __dirname,
  "../../../.dev/contracts/dashboard_ws_message.v0.3.0.json",
);

const ENGINE_KEYS = [
  "tennis_technical",
  "market_momentum",
  "smart_money",
  "sentiment_llm",
  "crowd_volume",
] as const;

describe("wsContract v0.3.0 additive fields", () => {
  it("WS_CONTRACT_VERSION is bumped to 0.3.0", () => {
    expect(WS_CONTRACT_VERSION).toBe("0.3.0");
  });

  it("schema file version matches the TS contract version constant", () => {
    const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf8")) as {
      version: string;
    };
    expect(schema.version).toBe(WS_CONTRACT_VERSION);
  });

  it("schema declares market_id/bet_id/signals on BOTH decision objects", () => {
    const schema = JSON.parse(readFileSync(SCHEMA_PATH, "utf8")) as {
      $defs: Record<
        string,
        { properties?: Record<string, unknown>; additionalProperties?: boolean }
      >;
    };
    for (const def of ["decision_payload", "decision_feed_entry"]) {
      const props = schema.$defs[def]?.properties ?? {};
      expect(Object.keys(props)).toEqual(
        expect.arrayContaining(["market_id", "bet_id", "signals"]),
      );
      // additionalProperties:false stays — unknown keys still reject.
      expect(schema.$defs[def]?.additionalProperties).toBe(false);
    }
  });

  it("DecisionPayload type accepts the new optional fields", () => {
    const withFields: DecisionPayload = {
      action: "BET",
      side: "YES",
      size_usd: 40,
      market_id: "0xmarket",
      bet_id: "uuid-abc",
      signals: Object.fromEntries(ENGINE_KEYS.map((k) => [k, 0.5])),
    };
    expect(withFields.market_id).toBe("0xmarket");
    expect(withFields.bet_id).toBe("uuid-abc");
    expect(withFields.signals?.tennis_technical).toBe(0.5);

    // v0.2.0 shape still type-checks (fields are optional).
    const legacy: DecisionPayload = { action: "NO_BET" };
    expect(legacy.market_id).toBeUndefined();
  });

  it("DecisionFeedEntry type accepts the new optional fields", () => {
    const entry: DecisionFeedEntry = {
      id: "uuid-abc",
      ts: "2026-05-23T12:00:00+00:00",
      action: "BET",
      market_id: "0xmarket",
      bet_id: "uuid-abc",
      signals: { smart_money: 0.9 },
    };
    expect(entry.bet_id).toBe("uuid-abc");
    expect(entry.signals?.smart_money).toBe(0.9);
  });

  it("isWsMessage still guards a decision frame carrying the new fields", () => {
    const frame = {
      kind: "decision",
      ts: "2026-05-23T12:00:00+00:00",
      seq: 0,
      payload: {
        action: "BET",
        market_id: "0xmarket",
        bet_id: "uuid-abc",
        signals: { tennis_technical: 0.5 },
      },
    };
    expect(isWsMessage(frame)).toBe(true);
    // Garbage with an unknown kind is still rejected.
    expect(isWsMessage({ kind: "nope", ts: "x", seq: 0 })).toBe(false);
    expect(isWsMessage({ ts: "x", seq: 0 })).toBe(false);
  });
});
