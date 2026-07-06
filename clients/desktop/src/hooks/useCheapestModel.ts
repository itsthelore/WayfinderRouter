// The cheapest-tier cache (WF-DESIGN-0012): /router/models lists tiers cheapest-first, so
// models[0] colours the early headers decision before the full wayfinder event arrives.
// Re-fetched when the gateway becomes reachable (the caller flips `enabled`).
import { useEffect, useState } from "react";
import { cheapestModel } from "@wayfinder/shared/gateway";
import { GATEWAY_BASE } from "@/lib/gateway";

export function useCheapestModel({
  baseUrl = GATEWAY_BASE,
  enabled = true,
}: { baseUrl?: string; enabled?: boolean } = {}): string | null {
  const [cheapest, setCheapest] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    void cheapestModel(baseUrl).then((name) => {
      if (alive && name) setCheapest(name);
    });
    return () => {
      alive = false;
    };
  }, [baseUrl, enabled]);
  return cheapest;
}
