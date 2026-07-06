// Transition-edge notifications (WF-DESIGN-0012): fire ONLY on a health edge — gateway up↔down,
// ok↔degraded, missing-keys appearing/clearing — never on the token stream and never on an
// unchanged poll. OFF by default (the Settings toggle lands in Phase 4); wired dormant so the
// mechanism exists and is tested. The first poll and the "unknown" seed never notify.
import { useEffect, useRef } from "react";
import type { GatewayState } from "@/lib/appState";
import { notify } from "@/lib/ipc";

type Snapshot = { health: GatewayState["health"]; missing: string };

export function useEdgeNotifier(gw: GatewayState, { enabled = false }: { enabled?: boolean } = {}) {
  const prev = useRef<Snapshot | null>(null);

  useEffect(() => {
    const cur: Snapshot = { health: gw.health, missing: [...gw.missingKeys].sort().join(",") };
    const before = prev.current;
    prev.current = cur;

    // Only notify across a real edge from a known prior state, and only when enabled.
    if (!enabled || !before || before.health === "unknown" || cur.health === "unknown") return;
    if (before.health === cur.health && before.missing === cur.missing) return;

    const wasUp = before.health === "ok" || before.health === "degraded";
    const isUp = cur.health === "ok" || cur.health === "degraded";

    if (wasUp && !isUp) {
      void notify("Wayfinder", "The gateway stopped responding.");
    } else if (!wasUp && isUp) {
      void notify("Wayfinder", "The gateway is back.");
    } else if (before.health !== "degraded" && cur.health === "degraded") {
      void notify("Wayfinder", `Degraded — set ${cur.missing || "the missing keys"} to route fully.`);
    } else if (before.health === "degraded" && cur.health === "ok") {
      void notify("Wayfinder", "Keys resolved — routing to all tiers.");
    }
  }, [gw.health, gw.missingKeys, enabled]);
}
