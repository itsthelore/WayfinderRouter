// View + preview tests (WF-DESIGN-0012). Decisions flow through the real decisionFromDebug;
// the parity flag is stubbed per-test so both the local-mirror and the "decisions unavailable"
// branches are exercised. PopoverRoot's mode switch is driven by a URL-routed fetch mock.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { decisionFromDebug } from "@wayfinder/shared/gateway";
import { localPreview, parityVerified } from "@/lib/scorerPreview";
import { FrostedHeader } from "@/components/FrostedHeader";
import { LocalMirror } from "@/components/LocalMirror";
import { ChatView } from "@/views/ChatView";
import { UnreachableView } from "@/views/UnreachableView";
import { PopoverRoot } from "@/views/PopoverRoot";
import { SEEN_GATEWAY_KEY } from "@/lib/gateway";
import { initialGatewayState, initialTurnState, type GatewayState } from "@/lib/appState";
import type { UseTurn } from "@/hooks/useTurn";
import type { SavingsReport } from "@/components/SavingsGlance";

function fixture(name: string): string {
  return readFileSync(join(process.cwd(), "src", "test", "fixtures", name), "utf8");
}
const cloud = decisionFromDebug(JSON.parse(fixture("decision-cloud.json")).wayfinder);
const local = decisionFromDebug(JSON.parse(fixture("decision-local.json")).wayfinder);
const savings = JSON.parse(fixture("savings.json")) as SavingsReport;

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  localStorage.clear();
  vi.unstubAllEnvs();
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

describe("FrostedHeader — brand, savings, ambient dot", () => {
  it("renders brand + dot + savings glance", () => {
    render(<FrostedHeader status="ok" missingKeys={[]} savings={savings} />);
    expect(screen.getByText("Wayfinder")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "gateway running" })).toBeInTheDocument();
    expect(screen.getByText("<$0.01")).toBeInTheDocument();
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

describe("ChatView — adornments + decision hero + reply swap", () => {
  it("degraded: amber banner names the missing keys verbatim", () => {
    render(
      <ChatView
        gw={gwState({ health: "degraded", missingKeys: ["ANTHROPIC_API_KEY"] })}
        turn={turnStub()}
        onOfflineToggle={() => {}}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("ANTHROPIC_API_KEY")).toBeInTheDocument();
  });
  it("offline (local toggle): the routing chip shows", () => {
    render(
      <ChatView gw={gwState({ offlineLocal: true })} turn={turnStub()} onOfflineToggle={() => {}} />,
    );
    expect(screen.getByText(/offline — routing to the cheapest tier/)).toBeInTheDocument();
  });
  it("a streamed turn shows the decision hero over the reply", () => {
    render(
      <ChatView
        gw={gwState()}
        turn={turnStub({ decision: cloud, enriched: true, reply: "hello there", phase: "done" })}
        onOfflineToggle={() => {}}
      />,
    );
    expect(screen.getByText("CLOUD")).toBeInTheDocument();
    expect(screen.getByText("hello there")).toBeInTheDocument();
  });
  it("announces the settled route once, politely, in an sr-only live region", () => {
    render(
      <ChatView
        gw={gwState()}
        turn={turnStub({ decision: local, enriched: true, reply: "hi", phase: "done" })}
        onOfflineToggle={() => {}}
      />,
    );
    const live = document.querySelector('[aria-live="polite"]')!;
    expect(live).toHaveTextContent("reply finished, routed locally");
  });
  it("decision-only: OnboardingCard replaces the reply", () => {
    render(
      <ChatView
        gw={gwState()}
        turn={turnStub({ decision: { ...local, decisionOnly: true }, enriched: true, phase: "done" })}
        onOfflineToggle={() => {}}
      />,
    );
    expect(screen.getByText("wayfinder-router init")).toBeInTheDocument();
    expect(screen.queryByText("hello there")).not.toBeInTheDocument();
  });
});

describe("UnreachableView — never a dead screen", () => {
  it("no handler: the CTA is disabled; the preview surface still renders", () => {
    render(<UnreachableView />);
    expect(screen.getByRole("button", { name: "Start Wayfinder" })).toBeDisabled();
    expect(screen.getByText(/decisions unavailable/)).toBeInTheDocument(); // parity unstubbed
  });
  it("wired: clicking Start runs the handler and surfaces its error", async () => {
    const user = userEvent.setup();
    const onStartGateway = vi.fn().mockRejectedValue(new Error("install the gateway first"));
    render(<UnreachableView onStartGateway={onStartGateway} />);
    await user.click(screen.getByRole("button", { name: "Start Wayfinder" }));
    expect(onStartGateway).toHaveBeenCalled();
    expect(await screen.findByText("install the gateway first")).toBeInTheDocument();
  });
});

describe("PopoverRoot — the six-mode switch, driven by healthz", () => {
  function routedFetch(healthz: () => Promise<Response>) {
    return vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.includes("/healthz")) return healthz();
      if (u.includes("/router/models")) return new Response(JSON.stringify({ models: [{ name: "local" }] }), { status: 200 });
      if (u.includes("/v1/savings")) return new Response(fixture("savings.json"), { status: 200 });
      return new Response("{}", { status: 200 });
    });
  }

  it("healthz ok -> tabbed surface, glance first, composer behind the chat tab", async () => {
    globalThis.fetch = routedFetch(async () => new Response(fixture("healthz-ok.json"), { status: 200 })) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    // Two status dots once reachable (header + the glance gateway tile).
    await waitFor(() =>
      expect(screen.getAllByRole("status", { name: "gateway running" }).length).toBeGreaterThan(0),
    );
    // Glance is the default tab; the composer lives behind the chat tab (hidden, not unmounted).
    expect(screen.getByRole("tab", { name: "glance", selected: true })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "message" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "chat" }));
    expect(screen.getByRole("textbox", { name: "message" })).toBeInTheDocument();
  });

  it("healthz rejects + never seen -> FirstRunView", async () => {
    globalThis.fetch = routedFetch(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    render(<PopoverRoot />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Install the Wayfinder service" })).toBeInTheDocument(),
    );
  });

  it("healthz rejects + previously seen -> UnreachableView", async () => {
    localStorage.setItem(SEEN_GATEWAY_KEY, "1");
    globalThis.fetch = routedFetch(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Start Wayfinder" })).toBeInTheDocument());
  });

  it("healthz degraded -> missing-keys banner behind the chat tab", async () => {
    globalThis.fetch = routedFetch(async () => new Response(fixture("healthz-degraded.json"), { status: 200 })) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "chat" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "chat" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
