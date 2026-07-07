// View tests (WF-DESIGN-0014). Decisions flow through the real decisionFromDebug; the parity
// flag is stubbed per-test so both the local-mirror and the "decisions unavailable" branches are
// exercised. PopoverRoot's mode switch is driven by a URL-routed fetch mock.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByRole("img", { name: "Wayfinder" })).toBeInTheDocument();
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
  it("the header (?) opens one panel: per-state status copy plus the machine-wide switch line", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <MenuHeader gw={gwState()} updatedText="now" onOfflineToggle={() => {}} />,
    );
    const help = () => screen.getByRole("button", { name: "about status and offline mode" });
    // Help only appears when asked for.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(help());
    const panel = await screen.findByRole("dialog");
    expect(panel).toHaveTextContent("Running — the gateway is routing turns.");
    expect(panel).toHaveTextContent(/machine-wide/);
    unmount();

    // Offline outranks the health copy; degraded points at Settings → Keys.
    const offline = render(
      <MenuHeader gw={gwState({ offlineConfig: true })} updatedText="now" onOfflineToggle={() => {}} />,
    );
    await user.click(help());
    expect(await screen.findByRole("dialog")).toHaveTextContent(/every turn routes to the local model/);
    offline.unmount();

    render(<MenuHeader gw={gwState({ health: "degraded", missingKeys: ["cloud"] })} updatedText="now" />);
    await user.click(help());
    const degradedPanel = await screen.findByRole("dialog");
    expect(degradedPanel).toHaveTextContent(/provider key is missing/);
    // No switch rendered -> the panel doesn't describe one.
    expect(degradedPanel).not.toHaveTextContent(/machine-wide/);
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
    expect(screen.getByText("Route: Cloud")).toBeInTheDocument();
    expect(screen.getByText("hello there")).toBeInTheDocument();
  });
  it("the live prompt has a copy button that writes it to the clipboard", () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(
      <ChatScreen
        gw={gwState()}
        turn={turnStub({ decision: cloud, enriched: true, reply: "hi", phase: "done", prompt: "copy me" })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "copy prompt" }));
    expect(writeText).toHaveBeenCalledWith("copy me");
    // The button flips to a confirmed state so the click has feedback.
    expect(screen.getByRole("button", { name: "prompt copied" })).toBeInTheDocument();
  });
  it("announces the settled route once, politely, in an sr-only live region", () => {
    render(<ChatScreen gw={gwState()} turn={turnStub({ decision: local, enriched: true, reply: "hi", phase: "done" })} />);
    const live = document.querySelector('[aria-live="polite"]')!;
    expect(live).toHaveTextContent("reply finished, routed locally");
  });
  it("streaming with no decision yet: a Routing… marker fills the gap instead of nothing", () => {
    render(
      <ChatScreen gw={gwState()} turn={turnStub({ phase: "streaming", prompt: "a fresh prompt", decision: null })} />,
    );
    expect(screen.getByText("a fresh prompt")).toBeInTheDocument();
    expect(screen.getByText("Routing…")).toBeInTheDocument();
  });

  it("decision-only: OnboardingCard replaces the reply", () => {
    render(
      <ChatScreen gw={gwState()} turn={turnStub({ decision: { ...local, decisionOnly: true }, enriched: true, phase: "done" })} />,
    );
    expect(screen.getByText("wayfinder-router init")).toBeInTheDocument();
    expect(screen.queryByText("hello there")).not.toBeInTheDocument();
  });

  // ------------------------------------------------------------------ the session transcript
  const settledOk = { prompt: "earlier question", decision: cloud, enriched: true, reply: "earlier answer", error: null };
  const settledErr = { prompt: "failed question", decision: local, enriched: false, reply: "", error: "upstream 502" };

  it("scrollback: settled turns render prompt, a one-line decision, and the reply", () => {
    render(
      <ChatScreen
        gw={gwState()}
        turn={turnStub({ transcript: [settledOk], decision: local, enriched: true, reply: "live reply", phase: "done" })}
      />,
    );
    expect(screen.getByText("earlier question")).toBeInTheDocument();
    expect(screen.getByText("earlier answer")).toBeInTheDocument();
    // The live turn shows its prompt too, plus the full hero — the scrollback decision is one
    // compact line, so the score meter appears exactly once.
    expect(screen.getByRole("meter", { name: "complexity score" })).toBeInTheDocument();
    expect(screen.getByText("live reply")).toBeInTheDocument();
  });

  it("scrollback: an error turn shows its error line instead of a reply", () => {
    render(<ChatScreen gw={gwState()} turn={turnStub({ transcript: [settledErr] })} />);
    expect(screen.getByText("failed question")).toBeInTheDocument();
    expect(screen.getByText(/reply failed: upstream 502/)).toBeInTheDocument();
  });

  it("send carries the transcript as history — user/assistant pairs", async () => {
    const user = userEvent.setup();
    const send = vi.fn(async () => {});
    render(<ChatScreen gw={gwState()} turn={turnStub({ transcript: [settledOk], send })} />);
    await user.type(screen.getByRole("textbox", { name: "message" }), "follow-up");
    await user.keyboard("{Enter}");
    expect(send).toHaveBeenCalledWith("follow-up", [
      { role: "user", content: "earlier question" },
      { role: "assistant", content: "earlier answer" },
    ]);
  });

  it("the empty-state hint shows only when there is neither a live decision nor scrollback", () => {
    const { rerender } = render(<ChatScreen gw={gwState()} turn={turnStub()} />);
    expect(screen.getByText(/Send a message — Wayfinder routes it/)).toBeInTheDocument();
    rerender(<ChatScreen gw={gwState()} turn={turnStub({ transcript: [settledOk] })} />);
    expect(screen.queryByText(/Send a message — Wayfinder routes it/)).not.toBeInTheDocument();
  });

  // ------------------------------------------------------------------- slash commands
  it("/clear resets the turn; /settings opens Settings; /offline is absent without a handler", async () => {
    const user = userEvent.setup();
    const reset = vi.fn();
    render(<ChatScreen gw={gwState()} turn={turnStub({ reset })} />);
    const box = screen.getByRole("textbox", { name: "message" });
    await user.type(box, "/");
    expect(screen.getAllByRole("option").map((o) => within(o).getByText(/^\//).textContent)).toEqual([
      "/clear",
      "/settings",
    ]);
    await user.click(screen.getByRole("option", { name: /clear/ }));
    expect(reset).toHaveBeenCalled();

    await user.type(box, "/settings");
    await user.keyboard("{Enter}");
    expect(vi.mocked(openSettings)).toHaveBeenCalled();
  });

  it("/offline appears when a toggle handler is supplied, and flips the current config state", async () => {
    const user = userEvent.setup();
    const onOfflineToggle = vi.fn();
    render(<ChatScreen gw={gwState()} turn={turnStub()} onOfflineToggle={onOfflineToggle} />);
    const box = screen.getByRole("textbox", { name: "message" });
    await user.type(box, "/offline");
    expect(screen.getByRole("option", { name: /Turn on global offline mode/ })).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(onOfflineToggle).toHaveBeenCalledWith(true);
  });

  it("/offline is a no-op while a toggle is already pending", async () => {
    const user = userEvent.setup();
    const onOfflineToggle = vi.fn();
    render(
      <ChatScreen gw={gwState()} turn={turnStub()} onOfflineToggle={onOfflineToggle} offlinePending />,
    );
    const box = screen.getByRole("textbox", { name: "message" });
    await user.type(box, "/offline");
    await user.keyboard("{Enter}");
    expect(onOfflineToggle).not.toHaveBeenCalled();
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
    await user.click(screen.getByRole("button", { name: "Wayfinder Chat" }));
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
    await waitFor(() => expect(screen.getByRole("button", { name: "Wayfinder Chat" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Wayfinder Chat" }));
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
