import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri wants a fixed dev port and a quiet console. The webview is macOS WKWebView,
// so we target a Safari baseline. `dist/` is what tauri.conf.json's frontendDist points at.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  build: {
    target: "safari15",
    outDir: "dist",
    emptyOutDir: true,
  },
});
