// Vitest setup: jest-dom matchers plus explicit RTL cleanup — auto-cleanup only registers
// itself when vitest globals are on, and we keep globals off (explicit imports everywhere).
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);

// jsdom has no ResizeObserver; Radix tooltip content measures itself with one. A no-op stub
// is enough — the tests assert content and roles, never measured geometry.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
