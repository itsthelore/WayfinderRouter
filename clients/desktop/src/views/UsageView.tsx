// The reachable-gateway surface (WF-DESIGN-0014): the flat list CodexBar's ClawRouter popover
// actually is (clawrouter-usage.png) — a MetricRow per stat, hairlines between every section,
// plain icon+label action rows. No cards, no tab-strip (Wayfinder is one entity, not a
// multi-provider aggregator — see WF-DESIGN-0014's Context). Chat has no CodexBar analogue; it
// is reached through a chevron row, the same disclosure affordance CodexBar's own "Cost"
// section uses. Everything here renders gateway truth — nothing is computed beyond shares of
// the gateway's own counts (WF-ADR-0001).
import { ExternalLink, FileText, MessageCircle, Settings2, WifiOff } from "lucide-react";
import type { GatewayState } from "@/lib/appState";
import type { RecentReport } from "@/hooks/useRecent";
import { formatSaved, type SavingsReport } from "@/lib/format";
import { ActionRow } from "@/components/menu/ActionRow";
import { MetricRow } from "@/components/menu/MetricRow";
import { Separator } from "@/components/ui/separator";

export function UsageView({
  gw,
  recent,
  savings,
  cheapest,
  onOfflineToggle,
  onOpenChat,
  onOpenTarget,
}: {
  gw: GatewayState;
  recent: RecentReport | null;
  savings: SavingsReport | null;
  cheapest: string | null;
  onOfflineToggle: (on: boolean) => void;
  onOpenChat: () => void;
  onOpenTarget: (target: "dashboard" | "config" | "logs") => void;
}) {
  const total = recent?.total ?? 0;
  const localCount = cheapest ? (recent?.byModel[cheapest] ?? 0) : 0;
  const cloudCount = total - localCount;
  const localShare = total > 0 ? localCount / total : 0;
  const hasSavings = !!savings && savings.priced && savings.saved > 0;
  const savedPct = hasSavings ? savings!.saved_pct || 0 : 0;
  const offline = gw.offlineConfig || gw.offlineLocal;

  return (
    <div className="flex flex-col" data-testid="usage">
      <MetricRow
        label="Routing"
        fraction={localShare}
        left={total > 0 ? `${Math.round(localShare * 100)}% routed locally` : "No turns yet"}
        right={total > 0 ? `${total} turn${total === 1 ? "" : "s"}` : undefined}
        insight={total > 0 ? `Routed: local: ${localCount} · cloud: ${cloudCount}` : undefined}
      />
      <Separator />
      <MetricRow
        label="Saved"
        fraction={Math.min(1, savedPct / 100)}
        left={hasSavings ? `${formatSaved(savings!.saved)} saved today` : "Not yet available"}
        right={hasSavings && savedPct > 0 ? `${Math.round(savedPct)}% vs always-cloud` : undefined}
      />
      <Separator />

      <ActionRow
        icon={WifiOff}
        label={gw.offlineConfig ? "Offline mode (by config)" : "Offline mode"}
        checked={offline}
        disabled={gw.offlineConfig}
        onClick={gw.offlineConfig ? undefined : () => onOfflineToggle(!gw.offlineLocal)}
      />
      <ActionRow icon={MessageCircle} label="Chat" chevron onClick={onOpenChat} />
      <Separator />
      <ActionRow icon={ExternalLink} label="Open Dashboard" onClick={() => onOpenTarget("dashboard")} />
      <ActionRow icon={Settings2} label="Open Config" onClick={() => onOpenTarget("config")} />
      <ActionRow icon={FileText} label="Open Logs" onClick={() => onOpenTarget("logs")} />
    </div>
  );
}
