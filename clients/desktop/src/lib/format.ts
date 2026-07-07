// Pure formatting helpers shared across the flat-list rows (WF-DESIGN-0014). No component owns
// these — SavingsGlance/DecisionPill/etc. are retired, but the numbers they used to format still
// need one canonical shape.

/** One /v1/savings `by_route` entry — per-model realized/baseline/saved + request count
 *  (`pricing.py`'s `_empty_route()`). "route" here means "the model this turn was sent to",
 *  same convention as /router/recent's `by_model` — just bucketed by real calendar day so it
 *  can be re-queried per period, not a fixed last-N-turns window. */
export interface SavingsRouteStats {
  requests: number;
  realized: number;
  baseline: number;
  saved: number;
  tokens: number;
}

/** The /v1/savings fields the popover consumes (fixture: savings.json). */
export interface SavingsReport {
  saved: number;
  saved_pct: number;
  priced: boolean;
  requests: number;
  by_route?: Record<string, SavingsRouteStats>;
}

/** "saved $0.42" (WF-DESIGN-0012) — sub-cent savings render as "<$0.01", never "$0.00". */
export function formatSaved(saved: number): string {
  if (saved < 0.01) return "<$0.01";
  return `$${saved.toFixed(2)}`;
}

/** The decision-latency stat under Saved (WF-DESIGN-0014 amendment). A route is decided by a
 *  table walk, not a model call (WF-ADR-0001), so sub-millisecond p50s read as "<1 ms"; `null`
 *  (no turn decided yet, or an older gateway) renders as an em dash. Never model latency. */
export function formatDecisionMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1) return "<1 ms";
  return `${Math.round(ms)} ms`;
}

/** A short "Updated {relative}" freshness line for the header's second row (CodexBar's own
 *  wording). `never` renders as an em dash — no gateway contact yet this session. */
export function formatUpdated(lastUpdatedMs: number | null, nowMs: number): string {
  if (lastUpdatedMs == null) return "—";
  const deltaS = Math.max(0, Math.round((nowMs - lastUpdatedMs) / 1000));
  if (deltaS < 10) return "Updated just now";
  if (deltaS < 60) return `Updated ${deltaS}s ago`;
  const deltaM = Math.round(deltaS / 60);
  if (deltaM < 60) return `Updated ${deltaM}m ago`;
  const deltaH = Math.round(deltaM / 60);
  return `Updated ${deltaH}h ago`;
}
