// The UI's written contract with the untyped `@wayfinder/shared` package (WF-ROADMAP-0009
// Phase 1). These declarations mirror the real exports of clients/shared/src/*.js — if a shape
// changes there, this file is where the compiler finds out. The client never scores
// (WF-ADR-0001): `Decision` is always something the gateway said (or, in the unreachable
// degraded mode only, the parity-gated mirror).

declare module "@wayfinder/shared/gateway" {
  /** One scoring factor's share of the decision, as the gateway explains it. */
  export interface Contribution {
    name: string;
    value: number;
    share: number;
  }

  /** A routing decision as rendered by the clients — produced by the gateway, never computed. */
  export interface Decision {
    model: string;
    score: number;
    mode: string;
    isLocal: boolean;
    contributions: Contribution[];
    targets: string[];
    /** Live gateway with no models configured — decision without a reply (WF-ADR-0042). */
    decisionOnly?: boolean;
    /** Explicit dry-run — decision without a model call. */
    dryRun?: boolean;
  }

  export interface RouteTurnOptions {
    baseUrl?: string;
    model?: string;
    threshold?: number | null;
    signal?: AbortSignal | null;
  }

  export interface RouteTurnResult {
    decision: Decision | null;
    reply: string | null;
    requestId: string | null;
  }

  export interface RouteTurnStreamOptions extends RouteTurnOptions {
    /** models[0] from /router/models — colours the route before the full decision arrives. */
    cheapest?: string | null;
    /** Fires twice: headers (early, no contributions) then the trailing wayfinder event. */
    onDecision?: (decision: Decision) => void;
    onToken?: (delta: string, reply: string) => void;
    /** Extra per-turn wayfinder headers (e.g. X-Wayfinder-Offline from the toggle). */
    headers?: Record<string, string>;
  }

  export function decisionFromDebug(wayfinder: Record<string, unknown>): Decision;
  export function decisionFromHeaders(headers: Headers, cheapest: string | null): Decision;
  export function routeTurn(
    messages: Array<{ role: string; content: string }>,
    options?: RouteTurnOptions,
  ): Promise<RouteTurnResult>;
  export function routeTurnStream(
    messages: Array<{ role: string; content: string }>,
    options?: RouteTurnStreamOptions,
  ): Promise<string>;
  export function cheapestModel(baseUrl?: string): Promise<string | null>;
}

declare module "@wayfinder/shared/decision" {
  import type { Decision, Contribution } from "@wayfinder/shared/gateway";

  export const LOCAL_GLYPH: string;
  export const CLOUD_GLYPH: string;
  export function routeKind(decision: Decision | null | undefined): "local" | "cloud";
  export function routeLabel(decision: Decision | null | undefined): string;
  export function routeGlyph(decision: Decision | null | undefined): string;
  export function formatScore(score: number): string;
  export function topContributions(
    decision: Decision | null | undefined,
    n?: number,
  ): Contribution[];
  export function routingBadge(
    decision: Decision | null | undefined,
    flags?: { cache?: boolean; offline?: boolean },
  ): string;
}

declare module "@wayfinder/shared/theme" {
  export interface Theme {
    bg: string;
    panel: string;
    elev: string;
    text: string;
    muted: string;
    line: string;
    lineStrong: string;
    user: string;
    accent: string;
    accentWeak: string;
    cloud: string;
    cloudWeak: string;
    btn: string;
    btnText: string;
    track: string;
    radius: string;
    radiusSm: string;
    pill: string;
    font: string;
    mono: string;
  }

  export const light: Theme;
  export const dark: Theme;
  export function routeColor(theme: Theme, isLocal: boolean): string;
}

declare module "@wayfinder/shared/scorer" {
  /** The parity-gated local mirror (degraded mode ONLY — never a routed decision). */
  export interface ScoredComplexity {
    score: number;
    recommendation: string;
    features: Record<string, number>;
  }

  export const FEATURE_ORDER: string[];
  export const DEFAULT_WEIGHTS: Record<string, number>;
  export function pyRound2(x: number): number;
  export function extractFeatures(text: string): Record<string, number>;
  export function scalarScore(
    features: Record<string, number>,
    weights?: Record<string, number>,
  ): number;
  export function recommendTier(score: number, tiers?: Array<[number, string]>): string;
  export function scoreComplexity(text: string): ScoredComplexity;
}
