// View tests (WF-DESIGN-0014). Decisions flow through the real decisionFromDebug; the parity
// flag is stubbed per-test so both the local-mirror and the "decisions unavailable" branches are
// exercised. PopoverRoot's mode switch is driven by a URL-routed fetch mock.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { decisionFromDebug } from "@wayfinder/shared/gateway";
import { localPreview, parityVerified } from "@/lib/scorerPreview";
import { MenuHeader } from "@/components/menu/MenuHeader";
import { LocalMirror } from "@/components/LocalMirror";
import { ChatScreen } from "@/views/ChatScreen";
import { UnreachableView } from "@/views/UnreachableView";
import { FirstRunView } from "@/views/FirstRunView";
import { PopoverRoot } from "@/views/PopoverRoot";
import { SEEN_GATEWAY_KEY } from "@/lib/gateway";
import { initialGatewayState, initialTurnState, type GatewayState } from "@/lib/appState";
import type { UseTurn } from "@/hooks/useTurn";

// Mock the ipc boundary so the footer's Settings…/Quit rows and the tray sync are assertable
// without a Tauri runtime.
vi.mock("@/lib/ipc", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ipc")>("@/lib/ipc");
  return {
    ...actual,
    openSettings: vi.fn(async () => {}),
    quitApp: vi.fn(async () => {}),
    setOffline: vi.fn(async () => "gateway.offline = true"),
  };
});
import { openSettings, quitApp, setOffline } from "@/lib/ipc";

function fixture(name: string): string {
  return readFileSync(join(process.cwd(), "src", "test", "fixtures", name), "utf8");
}
const cloud = decisionFromDebug(JSON.parse(fixture("decision-cloud.json")).wayfinder);
const local = decisionFromDebug(JSON.parse(fixture("decision-local.json")).wayfinder);

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  localStorage.clear();
  vi.unstubAllEnvs();
  vi.mocked(openSettings).mockClear();
  vi.mocked(quitApp).mockClear();
  vi.mocked(setOffline).mockClear();
});

function turnStub(over: Partial<UseTurn> = {}): UseTurn {
  return { ...initialTurnState, send: vi.fn(), stop: vi.fn(), reset: vi.fn(), ...over };
}
function gwState(over: Partial<GatewayState> = {}): GatewayState {
  return { ...initialGatewayState(true), health: "ok", ...over };
}

describe("scorerPreview — parity-gated local mirror", () => {
  it("withheld unless VITE_PARITY_OK is set", () => {
    expect(parityVerified()).toBe(false);
    expect(localPreview("anything")).toBeNull();
  });
  it("verified: shapes route + score from the scorer, empty why", () => {
    vi.stubEnv("VITE_PARITY_OK", "1");
    expect(parityVerified()).toBe(true);
    expect(localPreview("   ")).toBeNull(); // empty text still null
    const d = localPreview("hi")!;
    expect(d).not.toBeNull();
    expect(d.mode).toBe("preview");
    expect(d.isLocal).toBe(true); // a 2-word prompt scores 0 -> local
    expect(d.contributions).toEqual([]);
  });
});

describe("MenuHeader — bold name, neutral health text, freshness subtext (mirrors clawrouter-usage.png)", () => {
  it("renders the name and a neutral health label", () => {
    render(<MenuHeader gw={gwState()} updatedText="Updated just now" />);
    expect(screen.getByText("Wayfinder")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Updated just now")).toBeInTheDocument();
  });
  it("degraded: the subtext line names the missing keys instead of the freshness text", () => {
    render(<MenuHeader gw={gwState({ health: "degraded", missingKeys: ["cloud"] })} updatedText="Updated just now" />);
    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText("Missing cloud")).toBeInTheDocument();
    expect(screen.queryByText("Updated just now")).not.toBeInTheDocument();
  });
  it("degraded + onAddKey: the missing-keys line itself is the Keys deep-link — not a menu row", async () => {
    const user = userEvent.setup();
    const onAddKey = vi.fn();
    render(
      <MenuHeader
        gw={gwState({ health: "degraded", missingKeys: ["cloud"] })}
        updatedText="Updated just now"
        onAddKey={onAddKey}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Missing cloud — add key…/ }));
    expect(onAddKey).toHaveBeenCalled();
  });
  it("offline (global config) outranks the ok health label", () => {
    render(<MenuHeader gw={gwState({ offlineConfig: true })} updatedText="Updated just now" />);
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });
  it("the offline switch is GLOBAL: reflects healthz config truth and fires the toggle", async () => {
    const user = userEvent.setup();
    const onOfflineToggle = vi.fn();
    const { rerender } = render(
      <MenuHeader gw={gwState()} updatedText="now" onOfflineToggle={onOfflineToggle} />,
    );
    const sw = screen.getByRole("switch", { name: /offline mode/ });
    expect(sw).not.toBeChecked();
    await user.click(sw);
    expect(onOfflineToggle).toHaveBeenCalledWith(true);
    rerender(
      <MenuHeader gw={gwState({ offlineConfig: true })} updatedText="now" onOfflineToggle={onOfflineToggle} />,
    );
    expect(screen.getByRole("switch", { name: /offline mode/ })).toBeChecked();
    // pending disables the control until the confirming healthz poll lands
    rerender(
      <MenuHeader gw={gwState()} updatedText="now" onOfflineToggle={onOfflineToggle} offlinePending />,
    );
    expect(screen.getByRole("switch", { name: /offline mode/ })).toBeDisabled();
  });
  it("the health label explains itself in a tooltip — per-state copy, offline outranking", async () => {
    const user = userEvent.setup();
    const { rerender, unmount } = render(<MenuHeader gw={gwState()} updatedText="now" />);
    await user.tab(); // the health label is the first focusable — focus opens the tooltip
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "The local gateway is up and routing turns.",
    );
    rerender(<MenuHeader gw={gwState({ offlineConfig: true })} updatedText="now" />);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/nothing leaves this mac/i);
    unmount();
    render(<MenuHeader gw={gwState({ health: "degraded", missingKeys: ["cloud"] })} updatedText="now" />);
    await user.tab(); // past the missing-keys line isn't rendered without onAddKey — label is next
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/provider key is missing/i);
  });
  it("the switch explains machine-wide in a tooltip on focus", async () => {
    const user = userEvent.setup();
    render(<MenuHeader gw={gwState()} updatedText="now" onOfflineToggle={() => {}} />);
    await user.tab(); // health label
    await user.tab(); // switch
    expect(screen.getByRole("switch", { name: /offline mode/ })).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/machine-wide/i);
  });
});

describe("LocalMirror — the two parity branches", () => {
  it("unverified -> 'decisions unavailable', no input", () => {
    render(<LocalMirror />);
    expect(screen.getByText(/decisions unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
  it("verified -> live preview as you type", async () => {
    vi.stubEnv("VITE_PARITY_OK", "1");
    const user = userEvent.setup();
    render(<LocalMirror />);
    const box = screen.getByRole("textbox", { name: "preview a routing decision" });
    await user.type(box, "hello");
    expect(screen.getByText("local mirror")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "complexity score" })).toBeInTheDocument();
  });
});

describe("ChatScreen — adornments + decision summary + reply swap", () => {
  it("degraded: the banner names the missing keys verbatim", () => {
    render(<ChatScreen gw={gwState({ health: "degraded", missingKeys: ["ANTHROPIC_API_KEY"] })} turn={turnStub()} />);
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("ANTHROPIC_API_KEY")).toBeInTheDocument();
  });
  it("offline (global config): the routing chip shows", () => {
    render(<ChatScreen gw={gwState({ offlineConfig: true })} turn={turnStub()} />);
    expect(screen.getByText(/offline — routing to the cheapest tier/)).toBeInTheDocument();
  });
  it("a streamed turn shows the decision summary over the reply", () => {
    render(
      <ChatScreen gw={gwState()} turn={turnStub({ decision: cloud, enriched: true, reply: "hello there", phase: "done" })} />,
    );
    expect(screen.getByText("CLOUD")).toBeInTheDocument();
    expect(screen.getByText("hello there")).toBeInTheDocument();
  });
  it("announces the settled route once, politely, in an sr-only live region", () => {
    render(<ChatScreen gw={gwState()} turn={turnStub({ decision: local, enriched: true, reply: "hi", phase: "done" })} />);
    const live = document.querySelector('[aria-live="polite"]')!;
    expect(live).toHaveTextContent("reply finished, routed locally");
  });
  it("decision-only: OnboardingCard replaces the reply", () => {
    render(
      <ChatScreen gw={gwState()} turn={turnStub({ decision: { ...local, decisionOnly: true }, enriched: true, phase: "done" })} />,
    );
    expect(screen.getByText("wayfinder-router init")).toBeInTheDocument();
    expect(screen.queryByText("hello there")).not.toBeInTheDocument();
  });
});

describe("UnreachableView / FirstRunView — never a dead screen", () => {
  it("Unreachable: no handler leaves the CTA disabled; the preview surface still renders", () => {
    render(<UnreachableView />);
    expect(screen.getByRole("button", { name: "Start Wayfinder" })).toBeDisabled();
    expect(screen.getByText(/decisions unavailable/)).toBeInTheDocument(); // parity unstubbed
  });
  it("Unreachable: wired Start runs the handler and surfaces its error", async () => {
    const user = userEvent.setup();
    const onStartGateway = vi.fn().mockRejectedValue(new Error("install the gateway first"));
    render(<UnreachableView onStartGateway={onStartGateway} />);
    await user.click(screen.getByRole("button", { name: "Start Wayfinder" }));
    expect(onStartGateway).toHaveBeenCalled();
    expect(await screen.findByText("install the gateway first")).toBeInTheDocument();
  });
  it("FirstRun: the scaffold CTA carries the picked preset (hybrid by default)", async () => {
    const user = userEvent.setup();
    const onScaffold = vi.fn().mockResolvedValue(undefined);
    render(<FirstRunView onScaffold={onScaffold} />);
    await user.click(screen.getByRole("button", { name: "Set up routing" }));
    expect(onScaffold).toHaveBeenCalledWith("hybrid");
    await user.click(screen.getByRole("radio", { name: /OpenAI/ }));
    await user.click(screen.getByRole("button", { name: "Set up routing" }));
    expect(onScaffold).toHaveBeenLastCalledWith("openai");
  });
  it("FirstRun: a scaffold failure surfaces its error and re-enables the CTA", async () => {
    const user = userEvent.setup();
    const onScaffold = vi.fn().mockRejectedValue(new Error("couldn't find `wayfinder-router`"));
    render(<FirstRunView onScaffold={onScaffold} />);
    await user.click(screen.getByRole("button", { name: "Set up routing" }));
    expect(await screen.findByText(/couldn't find/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set up routing" })).toBeEnabled();
  });
  it("FirstRun: no handler leaves the CTA disabled; the preview surface still renders", () => {
    render(<FirstRunView />);
    expect(screen.getByRole("button", { name: "Set up routing" })).toBeDisabled();
  });
});

describe("PopoverRoot — the reachable/unreachable/first-run switch, driven by healthz", () => {
  function routedFetch(healthz: () => Promise<Response>) {
    return vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.includes("/healthz")) return healthz();
      if (u.includes("/router/models")) return new Response(JSON.stringify({ models: [{ name: "local" }] }), { status: 200 });
      if (u.includes("/v1/savings")) return new Response(fixture("savings.json"), { status: 200 });
      if (u.includes("/router/recent")) return new Response(fixture("recent.json"), { status: 200 });
      return new Response("{}", { status: 200 });
    });
  }

  it("healthz ok -> the flat Usage list, composer behind the Chat row", async () => {
    globalThis.fetch = routedFetch(async () => new Response(fixture("healthz-ok.json"), { status: 200 })) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByText("Running")).toBeInTheDocument());
    expect(screen.getByTestId("usage")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "message" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Chat" }));
    expect(screen.getByRole("textbox", { name: "message" })).toBeInTheDocument();
  });

  it("healthz rejects + never seen -> FirstRunView with the scaffold CTA", async () => {
    globalThis.fetch = routedFetch(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    render(<PopoverRoot />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Set up routing" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("radio", { name: /Hybrid/ })).toBeInTheDocument();
  });

  it("healthz rejects + previously seen -> UnreachableView", async () => {
    localStorage.setItem(SEEN_GATEWAY_KEY, "1");
    globalThis.fetch = routedFetch(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Start Wayfinder" })).toBeInTheDocument());
  });

  it("healthz degraded -> missing-keys banner behind the Chat row", async () => {
    globalThis.fetch = routedFetch(async () => new Response(fixture("healthz-degraded.json"), { status: 200 })) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Chat" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("the footer's real shortcuts: ⌘, opens Settings, ⌘Q quits — only while the Usage list shows", async () => {
    globalThis.fetch = routedFetch(async () => new Response(fixture("healthz-ok.json"), { status: 200 })) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Settings…/ })).toBeInTheDocument());
    await user.keyboard("{Meta>},{/Meta}");
    expect(openSettings).toHaveBeenCalledTimes(1);
    await user.keyboard("{Meta>}q{/Meta}");
    expect(quitApp).toHaveBeenCalledTimes(1);
  });
});
