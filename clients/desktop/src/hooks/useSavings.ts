// The savings glance feed (WF-DESIGN-0012): /v1/savings on mount + a slow interval, plus a
// manual refresh the views call when a turn finishes (event-driven, so the header updates
// the moment money is saved rather than on the next tick).
import { useCallback, useEffect, useState } from "react";
import type { SavingsReport } from "@/lib/format";
import { GATEWAY_BASE } from "@/lib/gateway";

export function useSavings({
  baseUrl = GATEWAY_BASE,
  period = "today",
  intervalMs = 15_000,
  enabled = true,
}: { baseUrl?: string; period?: string; intervalMs?: number | null; enabled?: boolean } = {}) {
  const [report, setReport] = useState<SavingsReport | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${baseUrl}/v1/savings?period=${encodeURIComponent(period)}`);
      if (!res.ok) throw new Error(`savings ${res.status}`);
      setReport((await res.json()) as SavingsReport);
    } catch {
      setReport(null); // an unreachable gateway simply hides the glance
    }
  }, [baseUrl, period]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    if (intervalMs == null) return; // manual cadence: initial fetch + event-driven refreshes only
    const id = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs, enabled]);

  return { report, refresh };
}
