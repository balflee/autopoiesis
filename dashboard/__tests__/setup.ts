import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// React Testing Library tears down after each test so DOM state does not
// leak across the keyboard / auto-play assertions.
afterEach(() => {
  cleanup();
});
