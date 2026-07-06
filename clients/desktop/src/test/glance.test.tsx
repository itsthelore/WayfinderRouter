// Glance-pivot tests: the route-split feed (recorded fixture) and the tray-meter quantizer.
// The GlanceView tile tests join this file in the next slice.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderHook, waitFor } from "@testing-library/react";
import { useRecent } from "@/hooks/useRecent";
import { quantizeFill } from "@/lib/meter";

const RECENT = readFileSync(join(process.cwd(), "src", "test", "fixtures", "recent.json"), "utf8");

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
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
