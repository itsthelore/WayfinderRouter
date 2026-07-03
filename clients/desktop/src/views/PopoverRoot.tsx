// The popover root (WF-DESIGN-0012): owns the two state machines + the hooks, and switches the
// six gateway modes onto their view. It never scores or decides — it wires the gateway's health
// and the streamed turn into the frosted header + the mode's view (WF-ADR-0001).
import { useEffect, useMemo, useReducer, useState } from "react";
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
import { GATEWAY_BASE } from "@/lib/gateway";
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
  const reachable = gw.health === "ok" || gw.health === "degraded";
  const cheapest = useCheapestModel({ baseUrl, enabled: reachable });
  const { report: savings, refresh: refreshSavings } = useSavings({ baseUrl, enabled: reachable });
  const turn = useTurn({ baseUrl, cheapest, offline: gw.offlineLocal });

  // Event-driven: when a turn settles, tell the gateway machine whether it was decision-only
  // (drives that mode) and refresh the savings glance the moment money moved.
  useEffect(() => {
    if (turn.phase === "done" || turn.phase === "error") {
      dispatch({ type: "TURN_DECISION", decisionOnly: !!turn.decision?.decisionOnly });
      void refreshSavings();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turn.phase]);

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
      {view === "unreachable" && <UnreachableView />}
      {view === "first-run" && <FirstRunView />}
    </div>
  );
}
