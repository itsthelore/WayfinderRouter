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

// Mock the ipc boundary so the Gateway/Keys/shortcut actions are assertable without a Tauri
// runtime (outside one, openTarget no-ops and the key/shortcut actions reject).
vi.mock("@/lib/ipc", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ipc")>("@/lib/ipc");
  return {
    ...actual,
    openTarget: vi.fn(async () => {}),
    setShortcut: vi.fn(async () => {}),
    storeProviderKey: vi.fn(async () => "stored"),
    deleteProviderKey: vi.fn(async () => "removed"),
    addModel: vi.fn(async () => "added"),
    detectLocalProviders: vi.fn(async () => []),
  };
});
import {
  addModel,
  deleteProviderKey,
  detectLocalProviders,
  openTarget,
  setShortcut,
  storeProviderKey,
} from "@/lib/ipc";

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

  it("still no provider search box — models come from /router/models, nothing to search", () => {
    render(<SettingsWindow />);
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("Gateway section: endpoint, config file, dashboard, and logs — all the open-targets live HERE, not as popover rows", async () => {
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Gateway" }));
    expect(screen.getByText("Endpoint")).toBeInTheDocument();
    expect(screen.getByText("127.0.0.1:8088")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open in Finder" }));
    expect(openTarget).toHaveBeenCalledWith("config");
    await user.click(screen.getByRole("button", { name: "Open in Browser" }));
    expect(openTarget).toHaveBeenCalledWith("dashboard");
    await user.click(screen.getByRole("button", { name: "Show in Finder" }));
    expect(openTarget).toHaveBeenCalledWith("logs");
  });

  it("shortcut select persists the choice and invokes the rebind", async () => {
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.selectOptions(screen.getByRole("combobox", { name: "popover shortcut" }), "ctrl+alt+w");
    expect(loadSettings().shortcut).toBe("ctrl+alt+w");
    expect(setShortcut).toHaveBeenCalledWith("ctrl+alt+w");
  });

  it("a rejected rebind rolls the choice back and shows the reason", async () => {
    vi.mocked(setShortcut).mockRejectedValueOnce(new Error("combo already claimed"));
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.selectOptions(screen.getByRole("combobox", { name: "popover shortcut" }), "cmd+shift+w");
    await waitFor(() => expect(loadSettings().shortcut).toBe("alt+w")); // rolled back
    expect(await screen.findByText("combo already claimed")).toBeInTheDocument();
  });

  it("?section=keys deep-links straight to the Keys section", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(fixture("router-models.json"), { status: 200 }),
    ) as unknown as typeof fetch;
    window.history.replaceState(null, "", "?window=settings&section=keys");
    render(<SettingsWindow />);
    expect(await screen.findByRole("heading", { name: "Keys" })).toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });
});

describe("SettingsWindow — Keys section (WF-DESIGN-0015: keyed models from /router/models)", () => {
  function mockModels() {
    globalThis.fetch = vi.fn(async () =>
      new Response(fixture("router-models.json"), { status: 200 }),
    ) as unknown as typeof fetch;
  }

  it("lists only keyed models, with env var + status; keyless local is absent", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    expect(await screen.findByText("cloud")).toBeInTheDocument();
    expect(screen.getByText(/\$ANTHROPIC_API_KEY.*Key missing/)).toBeInTheDocument();
    expect(screen.queryByText("llama3.1")).not.toBeInTheDocument();
    // The key input is a password field and the honesty note names the Keychain + api_key_cmd.
    expect(screen.getByLabelText("ANTHROPIC_API_KEY key")).toHaveAttribute("type", "password");
    expect(screen.getByText(/macOS Keychain/)).toBeInTheDocument();
    expect(screen.getByText(/api_key_cmd/)).toBeInTheDocument();
  });

  it("Save stores the key via ipc and clears the input (never lingers in JS state)", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    const input = await screen.findByLabelText("ANTHROPIC_API_KEY key");
    await user.type(input, "sk-ant-test");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(storeProviderKey).toHaveBeenCalledWith("ANTHROPIC_API_KEY", "sk-ant-test");
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("a present key offers Remove, which deletes via ipc", async () => {
    const present = JSON.parse(fixture("router-models.json")) as {
      models: Array<Record<string, unknown>>;
    };
    present.models = present.models.map((m) =>
      m.name === "cloud" ? { ...m, key_ok: true } : m,
    );
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify(present), { status: 200 }),
    ) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "Remove" }));
    expect(deleteProviderKey).toHaveBeenCalledWith("ANTHROPIC_API_KEY");
  });

  it("an unreachable gateway degrades to an honest message", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    expect(await screen.findByText(/isn’t reachable/)).toBeInTheDocument();
  });
});

describe("SettingsWindow — Keys section: Add Provider or Model (WF-ADR-0044 amendment)", () => {
  afterEach(() => {
    vi.mocked(addModel).mockClear();
    vi.mocked(detectLocalProviders).mockClear();
  });

  function mockModels() {
    globalThis.fetch = vi.fn(async () =>
      new Response(fixture("router-models.json"), { status: 200 }),
    ) as unknown as typeof fetch;
  }

  it("picking a preset fills the form; Add calls the config seam and closes it", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    await user.click(screen.getByRole("button", { name: "Anthropic" }));
    expect(screen.getByLabelText("provider name")).toHaveValue("anthropic");
    expect(screen.getByLabelText("base URL")).toHaveValue("https://api.anthropic.com/v1");
    expect(screen.getByLabelText("API key env var")).toHaveValue("ANTHROPIC_API_KEY");

    await user.type(screen.getByLabelText("model id"), "claude-opus-4-8");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(addModel).toHaveBeenCalledWith(
      "anthropic",
      "https://api.anthropic.com/v1",
      "claude-opus-4-8",
      "ANTHROPIC_API_KEY",
    );
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument(),
    );
  });

  it("the same provider can be added twice under different names — the name field is freely editable", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    await user.click(screen.getByRole("button", { name: "Anthropic" }));
    const nameInput = screen.getByLabelText("provider name");
    await user.clear(nameInput);
    await user.type(nameInput, "anthropic-fast");
    await user.type(screen.getByLabelText("model id"), "claude-haiku-4-5");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(addModel).toHaveBeenCalledWith(
      "anthropic-fast",
      "https://api.anthropic.com/v1",
      "claude-haiku-4-5",
      "ANTHROPIC_API_KEY",
    );
  });

  it("Custom leaves every field blank and keyless is allowed (Ollama/LM Studio, no api key env)", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    await user.click(screen.getByRole("button", { name: "Ollama" }));
    expect(screen.getByLabelText("API key env var")).toHaveValue("");
    await user.type(screen.getByLabelText("model id"), "llama3.1");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(addModel).toHaveBeenCalledWith(
      "ollama",
      "http://127.0.0.1:11434/v1",
      "llama3.1",
      undefined,
    );
  });

  it("rejects an invalid name before ever calling the seam", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    await user.click(screen.getByRole("button", { name: "Custom" }));
    await user.type(screen.getByLabelText("provider name"), "Not Valid!");
    await user.type(screen.getByLabelText("base URL"), "https://example.com/v1");
    await user.type(screen.getByLabelText("model id"), "some-model");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(/lowercase letters/)).toBeInTheDocument();
    expect(addModel).not.toHaveBeenCalled();
  });

  it("surfaces the seam's rejection reason (e.g. a name collision) without closing the form", async () => {
    mockModels();
    vi.mocked(addModel).mockRejectedValueOnce(
      new Error("a model named 'anthropic' already exists in this config"),
    );
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    await user.click(screen.getByRole("button", { name: "Anthropic" }));
    await user.type(screen.getByLabelText("model id"), "claude-opus-4-8");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("Cancel closes the form without calling the seam", async () => {
    mockModels();
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    await user.click(screen.getByRole("button", { name: "Custom" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("provider name")).not.toBeInTheDocument();
    expect(addModel).not.toHaveBeenCalled();
  });

  it("marks detected local runners with a bullet and a caption", async () => {
    mockModels();
    vi.mocked(detectLocalProviders).mockResolvedValueOnce([
      { id: "ollama", baseUrl: "http://127.0.0.1:11434/v1" },
    ]);
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Keys" }));
    await user.click(await screen.findByRole("button", { name: "+ Add Provider or Model" }));
    expect(await screen.findByRole("button", { name: "Ollama •" })).toBeInTheDocument();
    expect(screen.getByText(/detected running on this Mac/)).toBeInTheDocument();
  });
});

describe("SettingsWindow — Privacy section (verify-lite, WF-ADR-0042 §8: honest claims only)", () => {
  it("states the three claims and the no-telemetry line without overclaiming", async () => {
    const user = userEvent.setup();
    render(<SettingsWindow />);
    await user.click(screen.getByRole("button", { name: "Privacy" }));
    expect(screen.getByText(/decision is computed on your machine/)).toBeInTheDocument();
    expect(screen.getByText(/only to the provider you route to/)).toBeInTheDocument();
    expect(screen.getByText(/Offline mode is the only nothing-leaves guarantee/)).toBeInTheDocument();
    expect(screen.getByText("No telemetry")).toBeInTheDocument();
    // The one overclaim the design bans must never appear:
    expect(screen.queryByText(/your data never leaves your machine/i)).not.toBeInTheDocument();
  });
});
