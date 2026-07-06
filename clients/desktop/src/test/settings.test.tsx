// Settings tests (glance pivot): persistence round-trip, cadence→interval mapping, the manual
// cadence disabling background polls, and the settings surface preserving the main tree.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { cadenceToMs, DEFAULT_SETTINGS, loadSettings, saveSettings, SETTINGS_KEY } from "@/lib/settings";
import { useSavings } from "@/hooks/useSavings";
import { SettingsView } from "@/views/SettingsView";
import { PopoverRoot } from "@/views/PopoverRoot";

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
    saveSettings({ cadence: "5m", notifications: true });
    expect(loadSettings()).toEqual({ cadence: "5m", notifications: true });
    localStorage.setItem(SETTINGS_KEY, "not json");
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ cadence: "yearly" }));
    expect(loadSettings().cadence).toBe("auto"); // unknown preset falls back
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

describe("SettingsView — the rows drive the settings object", () => {
  it("cadence radios + notifications switch call onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={onChange} onClose={() => {}} />);
    await user.click(screen.getByRole("radio", { name: "5m" }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_SETTINGS, cadence: "5m" });
    await user.click(screen.getByRole("switch", { name: "edge notifications" }));
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_SETTINGS, notifications: true });
  });

  it("Escape closes settings (before any global handling)", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<SettingsView settings={DEFAULT_SETTINGS} onChange={() => {}} onClose={onClose} />);
    screen.getByRole("dialog", { name: "settings" }).focus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("PopoverRoot + settings — slide over without unmounting the main surface", () => {
  it("gear opens settings; done returns; the glance surface survives underneath", async () => {
    globalThis.fetch = vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.includes("/healthz")) return new Response(fixture("healthz-ok.json"), { status: 200 });
      if (u.includes("/router/models"))
        return new Response(JSON.stringify({ models: [{ name: "local" }] }), { status: 200 });
      if (u.includes("/router/recent")) return new Response(fixture("recent.json"), { status: 200 });
      if (u.includes("/v1/savings")) return new Response(fixture("savings.json"), { status: 200 });
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("button", { name: "settings" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "settings" }));
    expect(screen.getByRole("dialog", { name: "settings" })).toBeInTheDocument();
    expect(screen.getByTestId("glance")).not.toBeVisible(); // hidden, still mounted
    await user.click(screen.getByRole("button", { name: "close settings" }));
    expect(screen.queryByRole("dialog", { name: "settings" })).not.toBeInTheDocument();
    expect(screen.getByTestId("glance")).toBeVisible();
  });
});
