// The single healthz poller (WF-DESIGN-0012): every 15s plus on window focus (the popover
// hides rather than unmounting, so focus is "the user is looking again — check now"). It
// dispatches into the gateway machine and persists the seen-gateway flag that separates
// first-run from unreachable.
import { useEffect } from "react";
import type { GatewayEvent, HealthzBody } from "@/lib/appState";
import { GATEWAY_BASE, SEEN_GATEWAY_KEY } from "@/lib/gateway";

export function useGatewayHealth(
  dispatch: (event: GatewayEvent) => void,
  {
    baseUrl = GATEWAY_BASE,
    intervalMs = 15_000 as number | null, // null: no background interval (manual cadence)
  }: { baseUrl?: string; intervalMs?: number | null } = {},
) {
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const res = await fetch(`${baseUrl}/healthz`);
        if (!res.ok) throw new Error(`healthz ${res.status}`);
        const body = (await res.json()) as HealthzBody;
        if (!alive) return;
        try {
          localStorage.setItem(SEEN_GATEWAY_KEY, "1");
        } catch {
          // private-mode storage failures just mean first-run shows again next launch
        }
        dispatch({ type: "HEALTHZ_OK", body });
      } catch {
        if (alive) dispatch({ type: "HEALTHZ_FAILED" });
      }
    }
    void poll();
    const id = intervalMs != null ? setInterval(() => void poll(), intervalMs) : null;
    const onFocus = () => void poll(); // the focus poll survives every cadence, incl. manual
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      if (id != null) clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [dispatch, baseUrl, intervalMs]);
}

/** Read the persisted seen-gateway flag for initialGatewayState. */
export function readSeenGateway(): boolean {
  try {
    return localStorage.getItem(SEEN_GATEWAY_KEY) === "1";
  } catch {
    return false;
  }
}
