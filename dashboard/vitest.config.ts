import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * Vitest config — runs from the dashboard/ directory.
 *
 * Tests live INSIDE dashboard/__tests__/ (relocated in T-D-002 from the
 * earlier tests/dashboard/ location) so node resolution from a test file
 * can find dashboard/node_modules. Playwright specs live alongside under
 * dashboard/__tests__/playwright/ but are excluded from the vitest pool —
 * they run via `npm run test:e2e` against `playwright.config.ts`.
 *
 * NOTE on setup: Vitest 2 on Vite 5 fails to load setupFiles whose
 * absolute path contains spaces (URL-encodes ' ' to '%20' then can't
 * fopen). We therefore import the setup hooks at the top of each test
 * file instead of declaring `setupFiles` here.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  // Vite's dev server walls off file reads outside the project root by
  // default. T-D-004 added cross-track shared specs under repo-root
  // `tests/dashboard/` (so the T-B-010 dashboard_bridge producer can
  // import the same golden fixture), so we explicitly open that path.
  server: {
    fs: {
      allow: [
        path.resolve(__dirname, "."),
        path.resolve(__dirname, ".."),
      ],
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: [
      "__tests__/**/*.test.ts",
      "__tests__/**/*.test.tsx",
      // T-D-005 sprint_5 — PlaybackMode acceptance suite uses the
      // `.spec.tsx` suffix (per task brief). React-based specs must
      // live under dashboard/__tests__/ so the @testing-library/react
      // import resolves through dashboard/node_modules.
      "__tests__/**/*.spec.ts",
      "__tests__/**/*.spec.tsx",
      // T-D-004 sprint_5 — golden death-watch specs live under the
      // repo-root tests/dashboard/ so the T-B-010 dashboard_bridge
      // producer can share the fixture file. Vitest resolves these
      // relative to the dashboard/ cwd via "../tests/dashboard/**".
      "../tests/dashboard/**/*.spec.ts",
      "../tests/dashboard/**/*.spec.tsx",
    ],
    exclude: [
      "__tests__/playwright/**",
      "node_modules/**",
    ],
    pool: "forks",
    css: false,
  },
});
