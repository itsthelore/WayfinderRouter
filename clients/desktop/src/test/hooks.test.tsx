// Hook tests (WF-DESIGN-0012 "Testing the contract"). The SSE replay drives useTurn with the
// RECORDED transcript + headers fixtures through the real routeTurnStream — gated chunk by
// chunk so the two onDecision fires and their ordering (decision BEFORE the first token,
// enrichment without clearing the reply) are genuinely asserted, not assumed.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useTurn } from "@/hooks/useTurn";
import { useGatewayHealth } from "@/hooks/useGatewayHealth";
import { useCheapestModel } from "@/hooks/useCheapestModel";
import { useSavings } from "@/hooks/useSavings";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { SEEN_GATEWAY_KEY } from "@/lib/gateway";
import type { GatewayEvent } from "@/lib/appState";

function fixture(name: string): string {
  return readFileSync(join(process.cwd(), "src", "test", "fixtures", name), "utf8");
}

const TRANSCRIPT = fixture("sse-transcript.txt");
const SSE_HEADERS = JSON.parse(fixture("sse-headers.json")) as Record<string, string>;
const HEALTHZ_OK = fixture("healthz-ok.json");
const SAVINGS = fixture("savings.json");
const DECISION_LOCAL = JSON.parse(fixture("decision-local.json")) as {
  wayfinder: Record<string, unknown>;
};

/** A ReadableStream whose chunks are released one gate at a time by the test. `abort()`
 *  errors the stream through its controller — cancel() would reject on a locked stream. */
function gatedStream(chunks: string[]) {
  const encoder = new TextEncoder();
  const gates = chunks.map(() => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    return { gate, release };
  });
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  let aborted = false;
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      ctrl = controller;
      for (let i = 0; i < chunks.length; i++) {
        await gates[i].gate;
        if (aborted) return;
        controller.enqueue(encoder.encode(chunks[i]));
      }
      controller.close();
    },
  });
  return {
    stream,
    release: (i: number) => gates[i].release(),
    abort: () => {
      aborted = true;
      ctrl.error(new DOMException("The operation was aborted.", "AbortError"));
    },
  };
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  localStorage.clear();
  vi.useRealTimers();
});

describe("useTurn — SSE replay of the recorded transcript", () => {
  it("decision paints from headers before any token; enrichment lands without clearing", async () => {
    // Split the verbatim transcript at the trailing wayfinder frame.
    const at = TRANSCRIPT.indexOf("event: wayfinder");
    expect(at).toBeGreaterThan(0);
    const { stream, release } = gatedStream([TRANSCRIPT.slice(0, at), TRANSCRIPT.slice(at)]);
    globalThis.fetch = vi.fn(async () => {
      return new Response(stream, {
        status: 200,
        headers: { ...SSE_HEADERS, "content-type": "text/event-stream" },
      });
    }) as typeof fetch;

    const { result } = renderHook(() => useTurn({ cheapest: "local" }));
    let turn!: Promise<void>;
    act(() => {
      turn = result.current.send("hi there");
    });

    // Phase 1: headers only — the decision exists, unenriched, before ANY token.
    await waitFor(() => expect(result.current.decision).not.toBeNull());
    expect(result.current.phase).toBe("streaming");
    expect(result.current.enriched).toBe(false);
    expect(result.current.reply).toBe("");
    expect(result.current.decision!.model).toBe(SSE_HEADERS["x-wayfinder-router-model"]);

    // Phase 2: the delta frames stream the reply below the fixed decision.
    act(() => release(0));
    await waitFor(() => expect(result.current.reply).toBe("Routing decisions stay local."));
    expect(result.current.enriched).toBe(false);

    // Phase 3: the trailing wayfinder event enriches — reply untouched, contributions in.
    act(() => release(1));
    await act(async () => turn);
    expect(result.current.phase).toBe("done");
    expect(result.current.enriched).toBe(true);
    expect(result.current.decision!.contributions.length).toBeGreaterThan(0);
    expect(result.current.reply).toBe("Routing decisions stay local.");
  });

  it("a JSON decision_only response resolves on the same path (WF-ADR-0042)", async () => {
    const body = {
      wayfinder: { ...DECISION_LOCAL.wayfinder, decision_only: true },
    };
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json", "x-wayfinder-router-model": "local" },
      });
    }) as typeof fetch;

    const { result } = renderHook(() => useTurn({ cheapest: "local" }));
    await act(async () => result.current.send("hi"));
    expect(result.current.phase).toBe("done");
    expect(result.current.reply).toBe("");
    expect(result.current.decision!.decisionOnly).toBe(true);
    expect(result.current.enriched).toBe(true); // the JSON payload carries contributions
  });

  it("stop aborts the stream; the decision survives as an error state", async () => {
    const gated = gatedStream([TRANSCRIPT]); // never released — hangs until abort
    globalThis.fetch = vi.fn(async (_url, init?: RequestInit) => {
      // Respect the abort signal the way undici would: error the (locked) stream.
      init?.signal?.addEventListener("abort", () => gated.abort());
      return new Response(gated.stream, {
        status: 200,
        headers: { ...SSE_HEADERS, "content-type": "text/event-stream" },
      });
    }) as typeof fetch;

    const { result } = renderHook(() => useTurn({ cheapest: "local" }));
    let turn!: Promise<void>;
    act(() => {
      turn = result.current.send("hi");
    });
    await waitFor(() => expect(result.current.decision).not.toBeNull());
    act(() => result.current.stop());
    await act(async () => turn);
    expect(result.current.phase).toBe("error");
    expect(result.current.decision).not.toBeNull(); // the decision is the product
  });

  it("offline preference adds the X-Wayfinder-Offline header per turn", async () => {
    const seen: Record<string, string>[] = [];
    globalThis.fetch = vi.fn(async (_url, init?: RequestInit) => {
      seen.push({ ...(init?.headers as Record<string, string>) });
      return new Response(JSON.stringify({ wayfinder: DECISION_LOCAL.wayfinder }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;

    const { result } = renderHook(() => useTurn({ offline: true }));
    await act(async () => result.current.send("hi"));
    expect(seen[0]["X-Wayfinder-Offline"]).toBe("1");
  });
});

describe("useGatewayHealth — mount + interval + focus poll into the gateway machine", () => {
  // Real timers with tight intervals: fake timers fight the fetch/json microtask chain.
  function okFetch() {
    return vi.fn(async () => new Response(HEALTHZ_OK, { status: 200, headers: { "content-type": "application/json" } }));
  }

  it("polls on mount, dispatches HEALTHZ_OK, and persists the seen flag", async () => {
    globalThis.fetch = okFetch() as unknown as typeof fetch;
    const events: GatewayEvent[] = [];
    renderHook(() => useGatewayHealth((e) => events.push(e), { intervalMs: 60_000 }));
    await waitFor(() => expect(events.length).toBeGreaterThan(0));
    expect(events[0].type).toBe("HEALTHZ_OK");
    expect(localStorage.getItem(SEEN_GATEWAY_KEY)).toBe("1");
  });

  it("keeps polling on the interval and reports failures", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(HEALTHZ_OK, { status: 200 }))
      .mockRejectedValue(new Error("refused"));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const events: GatewayEvent[] = [];
    renderHook(() => useGatewayHealth((e) => events.push(e), { intervalMs: 25 }));
    await waitFor(() => expect(events.length).toBeGreaterThanOrEqual(2));
    expect(events[0].type).toBe("HEALTHZ_OK");
    expect(events[1].type).toBe("HEALTHZ_FAILED");
  });

  it("window focus triggers an immediate re-poll", async () => {
    const fetchMock = okFetch();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    renderHook(() => useGatewayHealth(() => {}, { intervalMs: 60_000 }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});

describe("useCheapestModel — models[0] from /router/models", () => {
  it("returns the cheapest tier name", async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ models: [{ name: "local" }, { name: "cloud" }] }), { status: 200 }),
    ) as unknown as typeof fetch;
    const { result } = renderHook(() => useCheapestModel());
    await waitFor(() => expect(result.current).toBe("local"));
  });
});

describe("useSavings — mount fetch + event-driven refresh", () => {
  it("loads the recorded report and refreshes on demand", async () => {
    const fetchMock = vi.fn(async () => new Response(SAVINGS, { status: 200 }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { result } = renderHook(() => useSavings({ intervalMs: 60_000 }));
    await waitFor(() => expect(result.current.report).not.toBeNull());
    expect(result.current.report!.saved).toBeCloseTo(0.0072);
    await act(async () => result.current.refresh());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("useReducedMotion — live media query", () => {
  it("tracks prefers-reduced-motion changes", () => {
    let listener: ((e: { matches: boolean }) => void) | null = null;
    const mql = {
      matches: false,
      addEventListener: (_: string, l: (e: { matches: boolean }) => void) => (listener = l),
      removeEventListener: () => (listener = null),
    };
    vi.stubGlobal("matchMedia", () => mql);
    const { result, unmount } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
    act(() => listener!({ matches: true }));
    expect(result.current).toBe(true);
    unmount();
    vi.unstubAllGlobals();
  });
});
