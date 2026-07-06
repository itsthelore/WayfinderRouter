// The IPC wrappers degrade safely outside a Tauri webview (vitest/jsdom has no
// __TAURI_INTERNALS__): display-only calls no-op so the UI renders in tests and plain browsers,
// while an action that genuinely needs the app rejects with a clear message.
import { describe, expect, it } from "vitest";
import { openSettings, openTarget, quitApp, serviceControl, setTrayState } from "@/lib/ipc";

describe("ipc — safe outside the desktop app", () => {
  it("tray + open calls no-op without throwing", async () => {
    await expect(setTrayState("running", "$1.00")).resolves.toBeUndefined();
    await expect(setTrayState("stopped", null)).resolves.toBeUndefined();
    await expect(openTarget("dashboard")).resolves.toBeUndefined();
  });
  it("Settings window + quit calls no-op without throwing (WF-DESIGN-0014)", async () => {
    await expect(openSettings()).resolves.toBeUndefined();
    await expect(quitApp()).resolves.toBeUndefined();
  });
  it("service control rejects clearly when there's no app", async () => {
    await expect(serviceControl("start")).rejects.toThrow(/desktop app/);
  });
});
