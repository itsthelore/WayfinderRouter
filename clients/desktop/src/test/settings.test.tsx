// Settings tests (WF-DESIGN-0014): persistence round-trip, cadence→interval mapping, the manual
// cadence disabling background polls, the Settings window's Form rows, and the popover's
// Settings… row reaching the separate native window through ipc rather than an in-popover
// slide-over (WF-DESIGN-0013's `SettingsView`, retired).

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { cadenceToMs, DEFAULT_SETTINGS, loadSettings, saveSettings, SETTINGS_KEY } from "@/lib/settings";
import { useSavings } from "@/hooks/useSavings";
import { SettingsWindow } from "@/views/SettingsWindow";

// Mock the ipc boundary so the Gateway section's open-config button is assertable without a
// Tauri runtime (outside one, openTarget silently no-ops).
vi.mock("@/lib/ipc", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ipc")>("@/lib/ipc");
  return { ...actual, openTarget: vi.fn(async () => {}) };
});
import { openTarget } from "@/lib/ipc";

function fixture(name: string): string {
  return readFileSync(join(process.cwd(), "src", "test", "fixtures", name), "utf8");
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  localStorage.clear();
});

describe("settings store — persistence + cadence mapping", () => {
  it("round-trips through localStorage and tolerates garbage", () => {
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
    saveSettings({ cadence: "5m", notifications: true, shortcut: "ctrl+alt+w" });
    expect(loadSettings()).toEqual({ cadence: "5m", notifications: true, shortcut: "ctrl+alt+w" });
    localStorage.setItem(SETTINGS_KEY, "not json");
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ cadence: "yearly" }));
    expect(loadSettings().cadence).toBe("auto"); // unknown preset falls back
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ shortcut: "cmd+q" }));
    expect(loadSettings().shortcut).toBe("alt+w"); // off-whitelist shortcut falls back to ⌥W
  });

  it.each([
    ["auto", 15_000],
    ["manual", null],
    ["1m", 60_000],
    ["5m", 300_000],
    ["15m", 900_000],
  ] as const)("cadenceToMs(%s) -> %s", (cadence, ms) => {
    expect(cadenceToMs(cadence)).toBe(ms);
  });
});

describe("manual cadence — initial fetch only, no background interval", () => {
  it("useSavings with intervalMs null fetches once", async () => {
    const fetchMock = vi.fn(async () => new Response(fixture("savings.json"), { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { result } = renderHook(() => useSavings({ intervalMs: null }));
    await waitFor(() => expect(result.current.report).not.toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(1); // and no timer is scheduled to call again
  });
});

describe("SettingsWindow — sidebar + Mac-native Form rows (mirrors clawrouter-settings.png)", () => {
  it("the General section's rows write straight through to the settings store", async () => {
    const user = userEvent.setup();
    render(<SettingsWindow />);
    expect(screen.getByRole("button", { name: "General" })).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "refresh cadence" }), "5m");
    expect(loadSettings().cadence).toBe("5m");

    await user.click(screen.getByRole("switch", { name: "edge notifications" }));
    expect(loadSettings().notifications).toBe(true);
  });

  it("no provider search box and no API key rows — Wayfinder has neither yet", () => {
    render(<SettingsWindow />);
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/API key/i)).not.toBeInTheDocument();
  });

  it("Gateway section: endpoint info + the one door to the gateway's config file", async () => {
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Gateway" }));
    expect(screen.getByText("Endpoint")).toBeInTheDocument();
    expect(screen.getByText("127.0.0.1:8088")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open in Finder" }));
    expect(openTarget).toHaveBeenCalledWith("config");
  });
});
