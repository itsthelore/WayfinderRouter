// The /router/models feed behind Settings → Providers (WF-DESIGN-0015). The gateway exposes
// everything the pane needs — the env-var NAME and a key_ok boolean, never a secret (WF-ADR-0025),
// plus per-model context_window/enabled and the scored tier ladder — so no new gateway API and no
// client-side config knowledge. /healthz's missing_keys are MODEL names (display-only); the
// Keychain account is the env var from here.
import { GATEWAY_BASE } from "@/lib/gateway";

export interface GatewayModelInfo {
  name: string;
  endpoint: string;
  model: string;
  api_key_env: string | null;
  key_ok: boolean;
  context_window: number | null;
  enabled: boolean;
  /** Same-tier endpoints tried if this one fails (WF-ADR-0031). The Providers pane edits the
   *  first entry as a single fallback; the CLI can hold more, but this UI manages one. */
  fallbacks: string[];
}

/** One `[[routing.tiers]]` band: the score at/above which this model becomes eligible. */
export interface TierEntry {
  model: string;
  min_score: number;
}

export interface ModelsFeed {
  models: GatewayModelInfo[];
  /** Ascending `min_score` ladder; empty in classifier mode or when no tiers are configured. */
  tiers: TierEntry[];
}

export async function fetchModels(baseUrl: string = GATEWAY_BASE): Promise<ModelsFeed> {
  const res = await fetch(`${baseUrl}/router/models`);
  if (!res.ok) throw new Error(`models ${res.status}`);
  const body = (await res.json()) as { models?: GatewayModelInfo[]; tiers?: TierEntry[] };
  return { models: body.models ?? [], tiers: body.tiers ?? [] };
}
