// Vitest setup: jest-dom matchers plus explicit RTL cleanup — auto-cleanup only registers
// itself when vitest globals are on, and we keep globals off (explicit imports everywhere).
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(cleanup);
