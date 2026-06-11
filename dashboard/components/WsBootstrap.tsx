"use client";

/**
 * WsBootstrap — client component that mounts the {@link useAgentWebSocket}
 * hook and renders the degraded banner above its children.
 *
 * Before T-D-003 this component carried its own boot logic; that has
 * since been factored into the hook so multiple call-sites (page root,
 * Storybook, integration tests) can subscribe with one shape.
 *
 * Path 1 — `window.__GENESIS_MOCK_WS__`: Playwright / Storybook / manual
 * QA inject frames synchronously; the hook re-uses the same parsing
 * pipeline as the real client.
 *
 * Path 2 — `buildWsClientFromEnv`: env URLs drive the real WS client.
 *
 * Path 3 — explicit config override (`config` prop): vitest tests can
 * inject a mock WebSocket constructor to exercise reconnect determinism.
 */

import { type JSX, type ReactNode } from "react";

import { useAgentWebSocket } from "@/hooks/useAgentWebSocket";
import { DegradedFeedBanner } from "@/components/DegradedFeedBanner";

export function WsBootstrap(props: {
  readonly children?: ReactNode;
}): JSX.Element {
  const { degraded } = useAgentWebSocket();
  return (
    <>
      <DegradedFeedBanner degraded={degraded} />
      {props.children}
    </>
  );
}

export default WsBootstrap;
