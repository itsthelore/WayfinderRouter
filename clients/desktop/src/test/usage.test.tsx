// The Usage screen (WF-DESIGN-0014, formerly the "glance" tiles): the route-split feed
// (recorded fixture), the tray-meter quantizer, and the flat MetricRow/ActionRow list.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRecent, type RecentReport } from "@/hooks/useRecent";
import { quantizeFill } from "@/lib/meter";
import { UsageView } from "@/views/UsageView";
import { PopoverRoot } from "@/views/PopoverRoot";
import { initialGatewayState, type GatewayState } from "@/lib/appState";
import type { SavingsReport } from "@/lib/format";

function fixture(name: string): string {
  return readFileSync(join(process.cwd(), "src", "test", "fixtures", name), "utf8");
}
const RECENT = fixture("recent.json");
const SAVINGS = JSON.parse(fixture("savings.json")) as SavingsReport;
const RECENT_REPORT: RecentReport = { total: 2, byModel: { local: 1, cloud: 1 }, localShare: 0.5 };

function gwState(over: Partial<GatewayState> = {}): GatewayState {
  return { ...initialGatewayState(true), health: "ok", ...over };
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  localStorage.clear();
});

describe("useRecent — the route split behind the meter and the Routing row", () => {
  it("computes the local share from the recorded by_model split", async () => {
    globalThis.fetch = vi.fn(async () => new Response(RECENT, { status: 200 })) as unknown as typeof fetch;
    const { result } = renderHook(() => useRecent({ cheapest: "local", intervalMs: 60_000 }));
    await waitFor(() => expect(result.current.report).not.toBeNull());
    expect(result.current.report!.total).toBe(2);
    expect(result.current.report!.localShare).toBe(0.5);
    expect(result.current.report!.byModel).toEqual({ local: 1, cloud: 1 });
  });

  it("no cheapest tier yet -> share is null (meter stays off)", async () => {
    globalThis.fetch = vi.fn(async () => new Response(RECENT, { status: 200 })) as unknown as typeof fetch;
    const { result } = renderHook(() => useRecent({ cheapest: null, intervalMs: 60_000 }));
    await waitFor(() => expect(result.current.report).not.toBeNull());
    expect(result.current.report!.localShare).toBeNull();
  });

  it("unreachable gateway -> null report, the screen degrades quietly", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    const { result } = renderHook(() => useRecent({ cheapest: "local", intervalMs: 60_000 }));
    await waitFor(() => expect(result.current.report).toBeNull());
  });
});

describe("quantizeFill — 5% steps so poll noise never re-renders the tray", () => {
  it.each([
    [null, null],
    [Number.NaN, null],
    [0, 0],
    [0.49, 0.5],
    [0.512, 0.5],
    [0.537, 0.55],
    [1, 1],
    [1.7, 1], // clamped
    [-0.2, 0], // clamped
  ])("quantizeFill(%s) -> %s", (input, expected) => {
    expect(quantizeFill(input as number | null)).toBe(expected);
  });
});

describe("UsageView — the flat list (mirrors clawrouter-usage.png)", () => {
  const noop = () => {};
  it("Routing row shows the local share, count, and the literal 'Routed: local: N · cloud: N' line", () => {
    render(
      <UsageView
        gw={gwState()}
        recent={RECENT_REPORT}
        savings={SAVINGS}
        cheapest="local"
        onOfflineToggle={noop}
        onOpenChat={noop}
        onOpenTarget={noop}
      />,
    );
    expect(screen.getByText("50% routed locally")).toBeInTheDocument();
    expect(screen.getByText("2 turns")).toBeInTheDocument();
    expect(screen.getByText("Routed: local: 1 · cloud: 1")).toBeInTheDocument();
    // The bar is a composition (local vs cloud split), not a quota fill (WF-DESIGN-0014).
    expect(
      screen.getByRole("img", { name: "route split — local: 1 (50%), cloud: 1 (50%)" }),
    ).toBeInTheDocument();
  });

  it("Saved row is a plain value line — cost-like, no bar (CodexBar's own Cost section form)", () => {
    render(
      <UsageView
        gw={gwState()}
        recent={RECENT_REPORT}
        savings={SAVINGS}
        cheapest="local"
        onOfflineToggle={noop}
        onOpenChat={noop}
        onOpenTarget={noop}
      />,
    );
    expect(screen.getByText("Today: <$0.01 · 29% vs always-cloud")).toBeInTheDocument();
    // Exactly one bar on the whole screen: the routing split. Saved has none.
    expect(screen.getAllByRole("img").length).toBe(1);
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("empty states: no turns yet, unpriced savings", () => {
    render(
      <UsageView
        gw={gwState()}
        recent={{ total: 0, byModel: {}, localShare: null }}
        savings={{ ...SAVINGS, priced: false }}
        cheapest="local"
        onOfflineToggle={noop}
        onOpenChat={noop}
        onOpenTarget={noop}
      />,
    );
    expect(screen.getByText("No turns yet")).toBeInTheDocument();
    expect(screen.getByText("Not yet available")).toBeInTheDocument();
  });

  it("offline row toggles the local preference, and locks when config owns it", async () => {
    const user = userEvent.setup();
    const onOfflineToggle = vi.fn();
    const { rerender } = render(
      <UsageView
        gw={gwState()}
        recent={RECENT_REPORT}
        savings={SAVINGS}
        cheapest="local"
        onOfflineToggle={onOfflineToggle}
        onOpenChat={noop}
        onOpenTarget={noop}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Offline mode" }));
    expect(onOfflineToggle).toHaveBeenCalledWith(true);

    rerender(
      <UsageView
        gw={gwState({ offlineConfig: true })}
        recent={RECENT_REPORT}
        savings={SAVINGS}
        cheapest="local"
        onOfflineToggle={onOfflineToggle}
        onOpenChat={noop}
        onOpenTarget={noop}
      />,
    );
    expect(screen.getByRole("button", { name: "Offline mode (by config)" })).toBeDisabled();
  });

  it("Chat row pushes the chat screen; Open rows call onOpenTarget", async () => {
    const user = userEvent.setup();
    const onOpenChat = vi.fn();
    const onOpenTarget = vi.fn();
    render(
      <UsageView
        gw={gwState()}
        recent={RECENT_REPORT}
        savings={SAVINGS}
        cheapest="local"
        onOfflineToggle={noop}
        onOpenChat={onOpenChat}
        onOpenTarget={onOpenTarget}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Chat" }));
    expect(onOpenChat).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Open Dashboard" }));
    expect(onOpenTarget).toHaveBeenCalledWith("dashboard");
    // No "Open Config" row — Config-vs-Settings read as synonyms as sibling entries; the
    // gateway's config file is reached via Settings → Gateway instead (WF-DESIGN-0014).
    expect(screen.queryByRole("button", { name: "Open Config" })).not.toBeInTheDocument();
  });
});

describe("PopoverRoot — chat push/back, draft survives (hidden, not unmounted)", () => {
  it("keeps the composer draft across a Chat push and back", async () => {
    globalThis.fetch = vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.includes("/healthz")) return new Response(fixture("healthz-ok.json"), { status: 200 });
      if (u.includes("/router/models"))
        return new Response(JSON.stringify({ models: [{ name: "local" }] }), { status: 200 });
      if (u.includes("/router/recent")) return new Response(RECENT, { status: 200 });
      if (u.includes("/v1/savings")) return new Response(fixture("savings.json"), { status: 200 });
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    const user = userEvent.setup();
    render(<PopoverRoot />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Chat" }));
    await user.type(screen.getByRole("textbox", { name: "message" }), "half a thought");
    await user.click(screen.getByRole("button", { name: "back to Wayfinder" }));
    expect(screen.getByTestId("usage")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Chat" }));
    expect(screen.getByRole("textbox", { name: "message" })).toHaveValue("half a thought");
  });
});
