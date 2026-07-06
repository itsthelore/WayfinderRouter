// The /router/models feed behind Settings → Keys (WF-DESIGN-0015). The gateway already
// exposes everything the Keys UI needs — the env-var NAME and a key_ok boolean, never a secret
// (WF-ADR-0025) — so no new gateway API and no client-side config knowledge. Note /healthz's
// missing_keys are MODEL names (display-only); the Keychain account is the env var from here.
import { GATEWAY_BASE } from "@/lib/gateway";

export interface GatewayModelInfo {
  name: string;
  endpoint: string;
  model: string;
  api_key_env: string | null;
  key_ok: boolean;
}

export async function fetchModels(baseUrl: string = GATEWAY_BASE): Promise<GatewayModelInfo[]> {
  const res = await fetch(`${baseUrl}/router/models`);
  if (!res.ok) throw new Error(`models ${res.status}`);
  const body = (await res.json()) as { models?: GatewayModelInfo[] };
  return body.models ?? [];
}
