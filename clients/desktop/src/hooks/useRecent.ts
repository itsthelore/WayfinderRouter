// The route-split feed (glance pivot): /router/recent's by_model aggregate → the local share
// that fills the tray meter and the route-split tile. The cheapest tier (models[0] from
// /router/models, via useCheapestModel) is the "local" numerator — the same convention the
// decision renderer uses. Rendered, never computed: the gateway did the routing (WF-ADR-0001).
import { useCallback, useEffect, useState } from "react";
import { GATEWAY_BASE } from "@/lib/gateway";

export interface RecentReport {
  total: number;
  byModel: Record<string, number>;
  /** Fraction of recent turns routed to the cheapest tier; null until there is data. */
  localShare: number | null;
}

export function useRecent({
  baseUrl = GATEWAY_BASE,
  cheapest = null as string | null,
  intervalMs = 15_000,
  enabled = true,
}: { baseUrl?: string; cheapest?: string | null; intervalMs?: number | null; enabled?: boolean } = {}) {
  const [report, setReport] = useState<RecentReport | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${baseUrl}/router/recent`);
      if (!res.ok) throw new Error(`recent ${res.status}`);
      const body = (await res.json()) as { total: number; by_model: Record<string, number> };
      const total = body.total ?? 0;
      const localShare =
        cheapest && total > 0 ? (body.by_model?.[cheapest] ?? 0) / total : total > 0 ? null : null;
      setReport({ total, byModel: body.by_model ?? {}, localShare });
    } catch {
      setReport(null); // unreachable gateway: no meter, no tile — the views degrade
    }
  }, [baseUrl, cheapest]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    if (intervalMs == null) return; // manual cadence: initial fetch + event-driven refreshes only
    const id = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs, enabled]);

  return { report, refresh };
}
