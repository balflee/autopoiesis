"use client";

/**
 * configureStore — Zustand latch tracking whether the operator has staged
 * a starting-weight config the live agent has not yet picked up.
 *
 * Why this lives in a tiny module of its own (vs being folded into
 * `wsStore`):
 *
 *   * `wsStore` carries WS-projected vitals and is rebuilt on every WS
 *     frame; this store is set by ONE caller (`/workshop` after a
 *     successful `configureAgent()`) and read by ONE caller
 *     (`<AgentControls>` for the 'Awaiting config' pill). Putting it
 *     alongside the WS projection would over-couple two unrelated
 *     write paths.
 *
 *   * Until Track B promotes the `agent_config.json` existence check to
 *     `/api/agent/status` (`pending_config?: boolean` — reserved in
 *     `api_client.ts::StatusBody` — see T-D-015 proposed_spec_change),
 *     the dashboard needs SOMEWHERE to remember that the operator just
 *     hit PROMOTE. A module-scoped Zustand store survives route
 *     navigation (workshop → / live dashboard) where local React state
 *     would not.
 *
 *   * The store reflects the LATEST `configureAgent()` response. A
 *     successful re-PROMOTE overwrites the previous staged config;
 *     `/api/agent/start` SUCCESS clears it (the operator pressed start →
 *     the agent now owns the config). The pill flips off on the next
 *     `/status` poll that reports `running: true` after a started
 *     timestamp later than the staged timestamp.
 *
 * Test seam: tests call `useConfigureStore.getState().setStaged(...)`
 * directly to bypass the network in the AgentControls Playwright test
 * for the 'Awaiting config' pill.
 */

import { create } from "zustand";

import type { StartingWeightConfig } from "./api_client";

export interface StagedConfig {
  readonly starting_weights: StartingWeightConfig;
  readonly persisted_path: string;
  /** ms epoch when configureAgent() returned 202 — used for monotonic clear. */
  readonly staged_at: number;
}

interface ConfigureState {
  /** null until the operator's first successful PROMOTE in this session. */
  readonly staged: StagedConfig | null;
  setStaged: (staged: StagedConfig) => void;
  /** Clear when the agent picks up the staged config (start succeeds). */
  clearStaged: () => void;
}

export const useConfigureStore = create<ConfigureState>((set) => ({
  staged: null,
  setStaged: (staged) => set({ staged }),
  clearStaged: () => set({ staged: null }),
}));
