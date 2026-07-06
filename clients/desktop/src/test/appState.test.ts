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
  historyFromTranscript,
  initialGatewayState,
  initialTurnState,
  showDegradedBanner,
  showOfflineChip,
  turnReducer,
  type GatewayEvent,
  type GatewayState,
  type HealthzBody,
  type SettledTurn,
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
      name: "offline flips back off when a later poll reports it cleared",
      seen: false,
      events: [
        { type: "HEALTHZ_OK", body: healthzOffline },
        { type: "HEALTHZ_OK", body: healthzOk },
      ],
      mode: "healthy",
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

    // Degraded + offline together: offline (the config's truth) picks the mode/chip, but the
    // missing-keys banner still shows — the two facts co-exist.
    const both = gatewayReducer(degraded, {
      type: "HEALTHZ_OK",
      body: { ...healthzDegraded, offline: true },
    });
    expect(gatewayMode(both)).toBe("offline");
    expect(showDegradedBanner(both)).toBe(true);
    expect(showOfflineChip(both)).toBe(true);
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

describe("transcript — settled turns collapse into scrollback on the next SUBMIT", () => {
  const local = decisionFromDebug(fixture<{ wayfinder: Record<string, unknown> }>("decision-local.json").wayfinder);
  const cloud = decisionFromDebug(fixture<{ wayfinder: Record<string, unknown> }>("decision-cloud.json").wayfinder);

  function settle(state = initialTurnState, prompt = "q", reply = "a") {
    let s = turnReducer(state, { type: "SUBMIT", prompt });
    s = turnReducer(s, { type: "DECISION", decision: cloud });
    s = turnReducer(s, { type: "TOKEN", delta: reply, reply });
    return turnReducer(s, { type: "DONE", reply });
  }

  it("done -> SUBMIT archives the turn; the live slots reset", () => {
    const done = settle(initialTurnState, "first question", "first answer");
    const s = turnReducer(done, { type: "SUBMIT", prompt: "second" });
    expect(s.transcript).toHaveLength(1);
    expect(s.transcript[0]).toMatchObject({
      prompt: "first question",
      reply: "first answer",
      decision: cloud,
      error: null,
    });
    expect(s.prompt).toBe("second");
    expect(s.reply).toBe("");
    expect(s.decision).toBeNull();
  });

  it("error -> SUBMIT archives too, error kept — the decision is still the product", () => {
    let s = turnReducer(initialTurnState, { type: "SUBMIT", prompt: "p" });
    s = turnReducer(s, { type: "DECISION", decision: local });
    s = turnReducer(s, { type: "ERROR", message: "upstream 502" });
    const next = turnReducer(s, { type: "SUBMIT", prompt: "again" });
    expect(next.transcript[0]).toMatchObject({ prompt: "p", error: "upstream 502", decision: local, reply: "" });
  });

  it("the first SUBMIT (from idle) archives nothing; streaming interrupted by SUBMIT is dropped", () => {
    const first = turnReducer(initialTurnState, { type: "SUBMIT", prompt: "p" });
    expect(first.transcript).toHaveLength(0);
    // A re-submit mid-stream (the hook aborts the old turn) never archives a half-turn.
    const resubmit = turnReducer(first, { type: "SUBMIT", prompt: "p2" });
    expect(resubmit.transcript).toHaveLength(0);
  });

  it("scrollback caps at 20 — oldest turns fall off the front", () => {
    let s = initialTurnState;
    for (let i = 0; i < 25; i++) s = settle(s, `q${i}`, `a${i}`);
    const final = turnReducer(s, { type: "SUBMIT", prompt: "last" });
    expect(final.transcript).toHaveLength(20);
    expect(final.transcript[0]!.prompt).toBe("q5");
    expect(final.transcript[19]!.prompt).toBe("q24");
  });

  it("RESET clears the transcript with everything else", () => {
    const done = settle();
    const s = turnReducer(turnReducer(done, { type: "SUBMIT", prompt: "x" }), { type: "RESET" });
    expect(s.transcript).toHaveLength(0);
  });
});

describe("historyFromTranscript — the wire-shaped conversation for the next send", () => {
  const settled = (prompt: string, reply: string): SettledTurn => ({
    prompt,
    reply,
    decision: null,
    enriched: false,
    error: reply ? null : "boom",
  });

  it("builds user/assistant pairs, oldest first", () => {
    expect(historyFromTranscript([settled("q1", "a1"), settled("q2", "a2")])).toEqual([
      { role: "user", content: "q1" },
      { role: "assistant", content: "a1" },
      { role: "user", content: "q2" },
      { role: "assistant", content: "a2" },
    ]);
  });

  it("turns without a reply contribute only their user line — never a fabricated answer", () => {
    expect(historyFromTranscript([settled("failed", "")])).toEqual([
      { role: "user", content: "failed" },
    ]);
  });

  it("caps at the last 8 turns", () => {
    const long = Array.from({ length: 12 }, (_, i) => settled(`q${i}`, `a${i}`));
    const history = historyFromTranscript(long);
    expect(history).toHaveLength(16);
    expect(history[0]).toEqual({ role: "user", content: "q4" });
  });
});
