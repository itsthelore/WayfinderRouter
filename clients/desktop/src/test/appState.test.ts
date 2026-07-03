// Table-driven tests for the two pure state machines (WF-DESIGN-0012), fed by the RECORDED
// gateway fixtures — healthz-*.json drive the gateway machine, decision-*.json flow through
// the real decisionFromDebug parser (the same code the app runs) into the turn machine.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { decisionFromDebug } from "@wayfinder/shared/gateway";
import {
  gatewayMode,
  gatewayReducer,
  gatewayView,
  initialGatewayState,
  initialTurnState,
  offlineLockedByConfig,
  showDegradedBanner,
  showOfflineChip,
  turnReducer,
  type GatewayEvent,
  type GatewayState,
  type HealthzBody,
} from "@/lib/appState";

function fixture<T = Record<string, unknown>>(name: string): T {
  const path = join(process.cwd(), "src", "test", "fixtures", name);
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

const healthzOk = fixture<HealthzBody>("healthz-ok.json");
const healthzDegraded = fixture<HealthzBody>("healthz-degraded.json");
const healthzOffline = fixture<HealthzBody>("healthz-offline.json");

function run(seen: boolean, events: GatewayEvent[]): GatewayState {
  return events.reduce(gatewayReducer, initialGatewayState(seen));
}

describe("gateway machine — the six modes, from recorded healthz shapes", () => {
  const table: Array<{
    name: string;
    seen: boolean;
    events: GatewayEvent[];
    mode: ReturnType<typeof gatewayMode>;
    view: ReturnType<typeof gatewayView>;
  }> = [
    {
      name: "ok healthz -> healthy ChatView",
      seen: false,
      events: [{ type: "HEALTHZ_OK", body: healthzOk }],
      mode: "healthy",
      view: "chat",
    },
    {
      name: "degraded healthz -> degraded ChatView",
      seen: false,
      events: [{ type: "HEALTHZ_OK", body: healthzDegraded }],
      mode: "degraded",
      view: "chat",
    },
    {
      name: "offline-config healthz -> offline ChatView",
      seen: false,
      events: [{ type: "HEALTHZ_OK", body: healthzOffline }],
      mode: "offline",
      view: "chat",
    },
    {
      name: "local toggle -> offline even on an ok gateway",
      seen: false,
      events: [
        { type: "HEALTHZ_OK", body: healthzOk },
        { type: "OFFLINE_TOGGLED", on: true },
      ],
      mode: "offline",
      view: "chat",
    },
    {
      name: "decision-only turn outranks offline",
      seen: false,
      events: [
        { type: "HEALTHZ_OK", body: healthzOffline },
        { type: "TURN_DECISION", decisionOnly: true },
      ],
      mode: "decision-only",
      view: "chat",
    },
    {
      name: "unreachable after a seen gateway -> UnreachableView",
      seen: false,
      events: [
        { type: "HEALTHZ_OK", body: healthzOk },
        { type: "HEALTHZ_FAILED" },
      ],
      mode: "unreachable",
      view: "unreachable",
    },
    {
      name: "unreachable, never seen -> FirstRunView",
      seen: false,
      events: [{ type: "HEALTHZ_FAILED" }],
      mode: "first-run",
      view: "first-run",
    },
    {
      name: "previously seen (persisted) skips first-run",
      seen: true,
      events: [{ type: "HEALTHZ_FAILED" }],
      mode: "unreachable",
      view: "unreachable",
    },
  ];

  for (const row of table) {
    it(row.name, () => {
      const state = run(row.seen, row.events);
      expect(gatewayMode(state)).toBe(row.mode);
      expect(gatewayView(state)).toBe(row.view);
    });
  }

  it("missing_keys land verbatim for the banner, and the banner can co-exist with offline", () => {
    const degraded = run(false, [{ type: "HEALTHZ_OK", body: healthzDegraded }]);
    expect(degraded.missingKeys).toEqual(healthzDegraded.missing_keys);
    expect(showDegradedBanner(degraded)).toBe(true);

    const both = gatewayReducer(degraded, { type: "OFFLINE_TOGGLED", on: true });
    expect(gatewayMode(both)).toBe("offline"); // mode picks the chip…
    expect(showDegradedBanner(both)).toBe(true); // …but the banner still shows
    expect(showOfflineChip(both)).toBe(true);
  });

  it("offline from config locks the toggle; local offline does not", () => {
    const byConfig = run(false, [{ type: "HEALTHZ_OK", body: healthzOffline }]);
    expect(offlineLockedByConfig(byConfig)).toBe(true);
    const byToggle = run(false, [
      { type: "HEALTHZ_OK", body: healthzOk },
      { type: "OFFLINE_TOGGLED", on: true },
    ]);
    expect(offlineLockedByConfig(byToggle)).toBe(false);
  });
});

describe("turn machine — decision paints early, enriches once, survives errors", () => {
  const local = decisionFromDebug(fixture<{ wayfinder: Record<string, unknown> }>("decision-local.json").wayfinder);
  const cloud = decisionFromDebug(fixture<{ wayfinder: Record<string, unknown> }>("decision-cloud.json").wayfinder);

  // The headers-shaped early decision: what decisionFromHeaders produces (no contributions).
  const early = { ...cloud, contributions: [], targets: ["local"] };

  it("SUBMIT -> streaming with everything reset", () => {
    const s = turnReducer({ ...initialTurnState, reply: "old" }, { type: "SUBMIT", prompt: "hi" });
    expect(s.phase).toBe("streaming");
    expect(s.reply).toBe("");
    expect(s.decision).toBeNull();
  });

  it("headers decision paints before any token; enrichment flips exactly once", () => {
    let s = turnReducer(initialTurnState, { type: "SUBMIT", prompt: "p" });
    s = turnReducer(s, { type: "DECISION", decision: early });
    expect(s.decision).not.toBeNull();
    expect(s.enriched).toBe(false); // headers fire has no contributions
    s = turnReducer(s, { type: "TOKEN", delta: "Routing", reply: "Routing" });
    s = turnReducer(s, { type: "DECISION", decision: cloud }); // trailing wayfinder event
    expect(s.enriched).toBe(true);
    expect(s.decision?.contributions.length).toBeGreaterThan(0);
    expect(s.reply).toBe("Routing"); // enrichment never clears the streamed reply
  });

  it("fixture decisions parse to the routes the gateway scored", () => {
    expect(local.isLocal).toBe(true);
    expect(cloud.isLocal).toBe(false);
    expect(cloud.score).toBeGreaterThanOrEqual(0.5);
  });

  it("ERROR keeps the decision — the decision is the product", () => {
    let s = turnReducer(initialTurnState, { type: "SUBMIT", prompt: "p" });
    s = turnReducer(s, { type: "DECISION", decision: cloud });
    s = turnReducer(s, { type: "ERROR", message: "upstream 502" });
    expect(s.phase).toBe("error");
    expect(s.decision).toEqual(cloud);
  });

  it("late tokens after error/abort are dropped", () => {
    let s = turnReducer(initialTurnState, { type: "SUBMIT", prompt: "p" });
    s = turnReducer(s, { type: "ERROR", message: "aborted" });
    const after = turnReducer(s, { type: "TOKEN", delta: "x", reply: "x" });
    expect(after.reply).toBe("");
  });
});
