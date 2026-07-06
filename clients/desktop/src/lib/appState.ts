// The two pure state machines behind the popover (WF-DESIGN-0012 "State machines").
// No React, no I/O — plain reducers over events, so both are table-tested against the
// recorded gateway fixtures. Nothing in here scores or decides (WF-ADR-0001): every
// Decision passing through was produced by the gateway (or, in the unreachable preview
// only, the parity-gated mirror — and that is the caller's concern, not this file's).

import type { Decision } from "@wayfinder/shared/gateway";

// ---------------------------------------------------------------------------- gateway machine

/** The healthz body shape the gateway machine consumes (fixtures: healthz-*.json). */
export interface HealthzBody {
  status: "ok" | "degraded";
  models: string[];
  offline: boolean;
  missing_keys?: string[];
}

export interface GatewayState {
  /** Last poll outcome; "unknown" until the first poll resolves. */
  health: "unknown" | "ok" | "degraded" | "unreachable";
  /** GLOBAL offline-first delivery, from healthz (WF-ADR-0039) — the config file is the one
   *  source of truth; the header switch flips it through `config set` (WF-ADR-0044) and this
   *  field reflects it on the next poll. The old per-app X-Wayfinder-Offline preference is
   *  retired: a control that looks machine-wide must be machine-wide. */
  offlineConfig: boolean;
  /** Env-var names, verbatim from healthz, for the degraded banner / StatusDot tooltip. */
  missingKeys: string[];
  /** Ever seen a live gateway on this machine (persisted as localStorage["wf.seenGateway"]). */
  seenGateway: boolean;
  /** The last turn came back decision-only (live gateway, no models — WF-ADR-0042). */
  lastTurnDecisionOnly: boolean;
}

export type GatewayEvent =
  | { type: "HEALTHZ_OK"; body: HealthzBody }
  | { type: "HEALTHZ_FAILED" }
  | { type: "TURN_DECISION"; decisionOnly: boolean };

export function initialGatewayState(seenGateway: boolean): GatewayState {
  return {
    health: "unknown",
    offlineConfig: false,
    missingKeys: [],
    seenGateway,
    lastTurnDecisionOnly: false,
  };
}

export function gatewayReducer(state: GatewayState, event: GatewayEvent): GatewayState {
  switch (event.type) {
    case "HEALTHZ_OK":
      return {
        ...state,
        health: event.body.status,
        offlineConfig: event.body.offline,
        missingKeys: event.body.missing_keys ?? [],
        seenGateway: true,
      };
    case "HEALTHZ_FAILED":
      return { ...state, health: "unreachable" };
    case "TURN_DECISION":
      return { ...state, lastTurnDecisionOnly: event.decisionOnly };
  }
}

/** The six modes of WF-DESIGN-0012's table. Derived, never stored. */
export type GatewayMode =
  | "healthy"
  | "degraded"
  | "decision-only"
  | "offline"
  | "unreachable"
  | "first-run";

export function gatewayMode(state: GatewayState): GatewayMode {
  if (state.health === "unreachable" || state.health === "unknown")
    return state.seenGateway ? "unreachable" : "first-run";
  if (state.lastTurnDecisionOnly) return "decision-only";
  if (state.offlineConfig) return "offline";
  if (state.health === "degraded") return "degraded";
  return "healthy";
}

/** Which top-level view PopoverRoot renders. All reachable modes share ChatView. */
export function gatewayView(state: GatewayState): "chat" | "unreachable" | "first-run" {
  const mode = gatewayMode(state);
  if (mode === "unreachable") return "unreachable";
  if (mode === "first-run") return "first-run";
  return "chat";
}

/** Adornments over ChatView — banner and chip can co-exist (degraded while offline). */
export const showDegradedBanner = (s: GatewayState) =>
  gatewayView(s) === "chat" && s.health === "degraded";
export const showOfflineChip = (s: GatewayState) =>
  gatewayView(s) === "chat" && s.offlineConfig;

// ------------------------------------------------------------------------------- turn machine

export type TurnPhase = "idle" | "streaming" | "done" | "error";

/** A settled turn, archived into the transcript when the next SUBMIT arrives. */
export interface SettledTurn {
  prompt: string;
  decision: Decision | null;
  enriched: boolean;
  reply: string;
  error: string | null;
}

/** Scrollback bound — old turns fall off the front; the popover is a glance, not an archive. */
const TRANSCRIPT_CAP = 20;

export interface TurnState {
  phase: TurnPhase;
  prompt: string;
  /** Paints early from headers, enriched by the trailing wayfinder event — never cleared
   *  by a reply error: the decision is the product. */
  decision: Decision | null;
  /** True once the trailing enrichment landed (contributions populated, no more updates). */
  enriched: boolean;
  reply: string;
  error: string | null;
  /** The session's settled turns, oldest first. In-memory only — never persisted; quitting
   *  the app is the clear affordance. */
  transcript: SettledTurn[];
}

export type TurnEvent =
  | { type: "SUBMIT"; prompt: string }
  | { type: "DECISION"; decision: Decision }
  | { type: "TOKEN"; delta: string; reply: string }
  | { type: "DONE"; reply: string }
  | { type: "ERROR"; message: string }
  | { type: "RESET" };

export const initialTurnState: TurnState = {
  phase: "idle",
  prompt: "",
  decision: null,
  enriched: false,
  reply: "",
  error: null,
  transcript: [],
};

export function turnReducer(state: TurnState, event: TurnEvent): TurnState {
  switch (event.type) {
    case "SUBMIT": {
      // A new turn resets everything except the transcript — the previous turn, if it
      // settled, collapses into it (the hero re-reserves its slots for the incoming one).
      const settled = state.phase === "done" || state.phase === "error";
      const transcript = settled
        ? [
            ...state.transcript,
            {
              prompt: state.prompt,
              decision: state.decision,
              enriched: state.enriched,
              reply: state.reply,
              error: state.error,
            },
          ].slice(-TRANSCRIPT_CAP)
        : state.transcript;
      return { ...initialTurnState, phase: "streaming", prompt: event.prompt, transcript };
    }
    case "DECISION": {
      // First fire = headers (no contributions); second = the wayfinder enrichment. The
      // enriched flag flips only when contributions arrive, so the UI can fill the why
      // slots exactly once and never re-measure the hero.
      const enriched = event.decision.contributions.length > 0;
      return { ...state, decision: event.decision, enriched: state.enriched || enriched };
    }
    case "TOKEN":
      if (state.phase !== "streaming") return state; // late token after abort/error: drop
      return { ...state, reply: event.reply };
    case "DONE":
      return { ...state, phase: "done", reply: event.reply };
    case "ERROR":
      // The decision persists — a failed reply still shows where it would have routed.
      return { ...state, phase: "error", error: event.message };
    case "RESET":
      return initialTurnState;
  }
}

/** How many settled turns ride along as conversation history on each request — a payload
 *  bound, smaller than the scrollback cap on purpose. */
const HISTORY_CAP = 8;

/** The wire-shaped history for the next send: user/assistant pairs from the last settled
 *  turns. Turns without a reply (errors, decision-only) contribute only their user line —
 *  the model should still see what was asked, but never a fabricated answer. */
export function historyFromTranscript(
  transcript: SettledTurn[],
  maxTurns: number = HISTORY_CAP,
): Array<{ role: string; content: string }> {
  return transcript.slice(-maxTurns).flatMap((turn) => [
    { role: "user", content: turn.prompt },
    ...(turn.reply ? [{ role: "assistant", content: turn.reply }] : []),
  ]);
}
