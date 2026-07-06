// Glance-pivot tests: the route-split feed (recorded fixture), the tray-meter quantizer, and
// the GlanceView tiles.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRecent, type RecentReport } from "@/hooks/useRecent";
import { quantizeFill } from "@/lib/meter";
import { GlanceView } from "@/views/GlanceView";
import { PopoverRoot } from "@/views/PopoverRoot";
import { initialGatewayState, type GatewayState } from "@/lib/appState";
import type { SavingsReport } from "@/components/SavingsGlance";

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

describe("useRecent — the route split behind the meter and the tile", () => {
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

  it("unreachable gateway -> null report, views degrade quietly", async () => {
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

describe("GlanceView — the tiles", () => {
  it("route split renders both bars with counts from the recorded split", () => {
    render(
      <GlanceView gw={gwState()} status="ok" recent={RECENT_REPORT} savings={SAVINGS} cheapest="local" />,
    );
    expect(screen.getByLabelText("local: 1 turns, 50%")).toBeInTheDocument();
    expect(screen.getByLabelText("cloud: 1 turns, 50%")).toBeInTheDocument();
  });

  it("savings tile shows the priced figure + percent", () => {
    render(
      <GlanceView gw={gwState()} status="ok" recent={RECENT_REPORT} savings={SAVINGS} cheapest="local" />,
    );
    expect(screen.getByText("<$0.01")).toBeInTheDocument();
    expect(screen.getByText(/29% vs always-frontier/)).toBeInTheDocument();
  });

  it("empty states: no turns yet, unpriced savings", () => {
    render(
      <GlanceView
        gw={gwState()}
        status="ok"
        recent={{ total: 0, byModel: {}, localShare: null }}
        savings={{ ...SAVINGS, priced: false }}
        cheapest="local"
      />,
    );
    expect(screen.getByText(/No turns yet/)).toBeInTheDocument();
    expect(screen.getByText(/once priced turns land/)).toBeInTheDocument();
  });

  it("degraded: the gateway tile names the missing keys", () => {
    render(
      <GlanceView
        gw={gwState({ health: "degraded", missingKeys: ["ANTHROPIC_API_KEY"] })}
        status="degraded"
        recent={RECENT_REPORT}
        savings={SAVINGS}
        cheapest="local"
      />,
    );
    expect(screen.getByText("ANTHROPIC_API_KEY")).toBeInTheDocument();
  });
});

describe("PopoverRoot tabs — draft survives a tab flip (hidden, not unmounted)", () => {
  it("keeps the composer draft across glance/chat flips", async () => {
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
    await waitFor(() => expect(screen.getByRole("tab", { name: "chat" })).toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "chat" }));
    await user.type(screen.getByRole("textbox", { name: "message" }), "half a thought");
    await user.click(screen.getByRole("tab", { name: "glance" }));
    expect(screen.getByTestId("glance")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "chat" }));
    expect(screen.getByRole("textbox", { name: "message" })).toHaveValue("half a thought");
  });
});
