// The reachable-gateway surface (WF-DESIGN-0014): the flat list CodexBar's ClawRouter popover
// actually is (clawrouter-usage.png) — a MetricRow per stat, hairlines between every section,
// plain icon+label action rows. No cards, no tab-strip (Wayfinder is one entity, not a
// multi-provider aggregator — see WF-DESIGN-0014's Context). Chat has no CodexBar analogue; it
// is reached through a chevron row, the same disclosure affordance CodexBar's own "Cost"
// section uses. Everything here renders gateway truth — nothing is computed beyond shares of
// the gateway's own counts (WF-ADR-0001).
//
// Deliberately ONLY behavior here (maintainer review, twice): Chat. Every open-something and
// fix-something action lives inside Settings — dashboard/logs/config under Gateway, keys under
// Keys (the degraded header's missing-keys line is the deep-link) — and the global Offline
// switch lives in the header beside the status it changes, so the popover never re-grows a
// scattered menu next to the one "Settings…" door.
import { MessageCircle } from "lucide-react";
import type { RecentReport } from "@/hooks/useRecent";
import { formatSaved, type SavingsReport } from "@/lib/format";
import { ActionRow } from "@/components/menu/ActionRow";
import { MetricRow } from "@/components/menu/MetricRow";
import { SplitBar } from "@/components/menu/SplitBar";
import { Separator } from "@/components/ui/separator";

function savedLine(prefix: string, report: SavingsReport): string {
  const pct = report.saved_pct || 0;
  return `${prefix}: ${formatSaved(report.saved)}${pct > 0 ? ` · ${Math.round(pct)}% vs always-cloud` : ""}`;
}

export function UsageView({
  recent,
  savings,
  savings30d,
  cheapest,
  onOpenChat,
}: {
  recent: RecentReport | null;
  savings: SavingsReport | null;
  savings30d: SavingsReport | null;
  cheapest: string | null;
  onOpenChat: () => void;
}) {
  const total = recent?.total ?? 0;
  const localCount = cheapest ? (recent?.byModel[cheapest] ?? 0) : 0;
  const cloudCount = total - localCount;
  const localShare = total > 0 ? localCount / total : 0;
  const hasSavings = !!savings && savings.priced && savings.saved > 0;
  const has30d = !!savings30d && savings30d.priced && savings30d.saved > 0;

  // The reference's Cost form: stacked dark body lines, today then the 30-day window. Each
  // line renders only when its period is priced with real savings (never "0 relative units").
  const savedLines = [
    ...(hasSavings ? [savedLine("Today", savings!)] : []),
    ...(has30d ? [savedLine("Last 30 days", savings30d!)] : []),
  ];

  return (
    <div className="flex flex-col" data-testid="usage">
      <MetricRow
        label="Routing"
        bar={
          <SplitBar
            segments={[
              { label: "local", count: localCount, color: "var(--primary)" },
              { label: "cloud", count: cloudCount, color: "var(--route-cloud)" },
            ]}
          />
        }
        left={total > 0 ? `${Math.round(localShare * 100)}% routed locally` : "No turns yet"}
        right={total > 0 ? `${total} turn${total === 1 ? "" : "s"}` : undefined}
        insight={total > 0 ? `Routed: local: ${localCount} · cloud: ${cloudCount}` : undefined}
      />
      <Separator className="mx-5 w-auto" />
      {/* Saved is cost-like, not a quota — no bar, dark body lines, exactly the form
          CodexBar's own (bar-less) Cost section uses. */}
      <MetricRow
        label="Saved"
        lines={savedLines.length > 0 ? savedLines : ["Not yet available"]}
      />
      <Separator className="mx-5 w-auto" />

      {/* Offline moved to the header switch (WF-DESIGN-0015 amendment): it is GLOBAL now —
          flipped through `config set gateway.offline` (WF-ADR-0044) — and a machine-wide mode
          belongs beside the machine-wide status, not in the action list. */}
      <ActionRow icon={MessageCircle} label="Chat" chevron onClick={onOpenChat} />
    </div>
  );
}
