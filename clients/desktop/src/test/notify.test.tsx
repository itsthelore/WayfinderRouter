// notify + edge-detector tests (WF-DESIGN-0012). The edge notifier must fire ONLY across a real
// health transition from a known prior state, only when enabled, and never on an unchanged poll.

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

// Mock the IPC boundary so we assert on notify() calls without a Tauri runtime.
vi.mock("@/lib/ipc", () => ({ notify: vi.fn(async () => {}) }));
import { notify } from "@/lib/ipc";
import { useEdgeNotifier } from "@/hooks/useEdgeNotifier";
import { initialGatewayState, type GatewayState } from "@/lib/appState";

const notifyMock = vi.mocked(notify);

function gw(over: Partial<GatewayState>): GatewayState {
  return { ...initialGatewayState(true), ...over };
}

afterEach(() => notifyMock.mockClear());

describe("useEdgeNotifier — edges only, off by default", () => {
  it("silent when disabled, even across an edge", () => {
    const { rerender } = renderHook(({ s }) => useEdgeNotifier(s, { enabled: false }), {
      initialProps: { s: gw({ health: "ok" }) },
    });
    rerender({ s: gw({ health: "unreachable" }) });
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it("does not fire on the first poll (no known prior state)", () => {
    renderHook(() => useEdgeNotifier(gw({ health: "ok" }), { enabled: true }));
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it("fires up→down and down→up, once each", () => {
    const { rerender } = renderHook(({ s }) => useEdgeNotifier(s, { enabled: true }), {
      initialProps: { s: gw({ health: "ok" }) },
    });
    rerender({ s: gw({ health: "unreachable" }) });
    expect(notifyMock).toHaveBeenCalledTimes(1);
    expect(notifyMock.mock.calls[0][1]).toMatch(/stopped responding/);
    rerender({ s: gw({ health: "ok" }) });
    expect(notifyMock).toHaveBeenCalledTimes(2);
    expect(notifyMock.mock.calls[1][1]).toMatch(/back/);
  });

  it("fires ok→degraded with the missing keys, and clearing back to ok", () => {
    const { rerender } = renderHook(({ s }) => useEdgeNotifier(s, { enabled: true }), {
      initialProps: { s: gw({ health: "ok" }) },
    });
    rerender({ s: gw({ health: "degraded", missingKeys: ["ANTHROPIC_API_KEY"] }) });
    expect(notifyMock.mock.calls[0][1]).toContain("ANTHROPIC_API_KEY");
    rerender({ s: gw({ health: "ok" }) });
    expect(notifyMock.mock.calls[1][1]).toMatch(/resolved/);
  });

  it("stays silent on an unchanged poll (new state object, same values)", () => {
    const { rerender } = renderHook(({ s }) => useEdgeNotifier(s, { enabled: true }), {
      initialProps: { s: gw({ health: "degraded", missingKeys: ["K"] }) },
    });
    rerender({ s: gw({ health: "degraded", missingKeys: ["K"] }) }); // fresh object, same values
    expect(notifyMock).not.toHaveBeenCalled();
  });
});
