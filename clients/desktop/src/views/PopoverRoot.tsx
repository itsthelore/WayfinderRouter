// The popover root (WF-DESIGN-0012): owns the two state machines + the hooks, and switches the
// six gateway modes onto their view. It never scores or decides — it wires the gateway's health
// and the streamed turn into the frosted header + the mode's view (WF-ADR-0001).
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import {
  gatewayReducer,
  gatewayView,
  initialGatewayState,
  type GatewayState,
} from "@/lib/appState";
import { useGatewayHealth, readSeenGateway } from "@/hooks/useGatewayHealth";
import { useCheapestModel } from "@/hooks/useCheapestModel";
import { useSavings } from "@/hooks/useSavings";
import { useTurn } from "@/hooks/useTurn";
import { useRecent } from "@/hooks/useRecent";
import { useEdgeNotifier } from "@/hooks/useEdgeNotifier";
import { GATEWAY_BASE } from "@/lib/gateway";
import { quantizeFill } from "@/lib/meter";
import { serviceControl, setTrayState, type TrayState } from "@/lib/ipc";
import { formatSaved } from "@/components/SavingsGlance";
import type { DotStatus } from "@/components/StatusDot";
import { FrostedHeader } from "@/components/FrostedHeader";
import { Separator } from "@/components/ui/separator";
import { ChatView } from "@/views/ChatView";
import { UnreachableView } from "@/views/UnreachableView";
import { FirstRunView } from "@/views/FirstRunView";

function dotStatus(gw: GatewayState): DotStatus {
  if (gw.health === "ok") return "ok";
  if (gw.health === "degraded") return "degraded";
  return "unreachable";
}

export function PopoverRoot({ baseUrl = GATEWAY_BASE }: { baseUrl?: string } = {}) {
  const [seen] = useState(readSeenGateway);
  const [gw, dispatch] = useReducer(gatewayReducer, seen, initialGatewayState);

  useGatewayHealth(dispatch, { baseUrl });
  // Transition-edge notifications, dormant for now — the Settings toggle (Phase 4) flips `enabled`.
  useEdgeNotifier(gw, { enabled: false });
  const reachable = gw.health === "ok" || gw.health === "degraded";
  const cheapest = useCheapestModel({ baseUrl, enabled: reachable });
  const { report: savings, refresh: refreshSavings } = useSavings({ baseUrl, enabled: reachable });
  const { report: recent, refresh: refreshRecent } = useRecent({ baseUrl, cheapest, enabled: reachable });
  const turn = useTurn({ baseUrl, cheapest, offline: gw.offlineLocal });

  // Event-driven: when a turn settles, tell the gateway machine whether it was decision-only
  // (drives that mode) and refresh the glance feeds the moment the numbers moved.
  useEffect(() => {
    if (turn.phase === "done" || turn.phase === "error") {
      dispatch({ type: "TURN_DECISION", decisionOnly: !!turn.decision?.decisionOnly });
      void refreshSavings();
      void refreshRecent();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turn.phase]);

  // Tray sync: the W shape follows health, its fill is the local-routing share (the live meter,
  // savings-forward: the $ rides in the title, never a route). Quantized so poll noise never
  // re-renders the icon; one source of tray truth (WF-DESIGN-0012 + glance amendment).
  const traySaved =
    savings && savings.priced && savings.saved > 0 ? formatSaved(savings.saved) : null;
  const trayFill = quantizeFill(recent?.localShare ?? null);
  useEffect(() => {
    const state: TrayState =
      gw.health === "ok" ? "running" : gw.health === "degraded" ? "degraded" : "stopped";
    void setTrayState(state, traySaved, trayFill);
  }, [gw.health, traySaved, trayFill]);

  // Service-first CTAs (WF-ADR-0042 §4): the app never spawns the gateway — it asks the service
  // to. Errors (e.g. the gateway isn't installed) propagate to the view for display; the next
  // healthz poll flips the mode once the service is up.
  const onStartGateway = useCallback(async () => {
    await serviceControl("start");
  }, []);
  const onInstallService = useCallback(async () => {
    await serviceControl("install");
  }, []);

  const view = gatewayView(gw);
  const status = useMemo(() => dotStatus(gw), [gw]);

  return (
    <div className="flex h-full flex-col">
      <FrostedHeader status={status} missingKeys={gw.missingKeys} savings={savings} />
      <Separator />
      {view === "chat" && (
        <ChatView
          gw={gw}
          turn={turn}
          onOfflineToggle={(on) => dispatch({ type: "OFFLINE_TOGGLED", on })}
        />
      )}
      {view === "unreachable" && <UnreachableView onStartGateway={onStartGateway} />}
      {view === "first-run" && <FirstRunView onInstallService={onInstallService} />}
    </div>
  );
}
