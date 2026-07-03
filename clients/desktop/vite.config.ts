import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// Tauri wants a fixed dev port and a quiet console. The webview is macOS WKWebView on a
// 14.0-minimum bundle, so the build targets Safari 16.4 — Tailwind v4's floor (WF-ADR-0042 §6).
// `dist/` is what tauri.conf.json's frontendDist points at.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  clearScreen: false,
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 1420,
    strictPort: true,
  },
  build: {
    target: "safari16.4",
    outDir: "dist",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/test/**/*.test.{ts,tsx}"],
  },
});
