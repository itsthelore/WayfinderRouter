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
import { useState } from "react";
import { MessageCircle, PiggyBank, Route } from "lucide-react";
import type { RecentReport } from "@/hooks/useRecent";
import { formatSaved, type SavingsReport } from "@/lib/format";
import { ActionRow } from "@/components/menu/ActionRow";
import { MetricRow } from "@/components/menu/MetricRow";
import { SplitBar } from "@/components/menu/SplitBar";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function savedLine(prefix: string, report: SavingsReport): string {
  const pct = report.saved_pct || 0;
  return `${prefix}: ${formatSaved(report.saved)}${pct > 0 ? ` · ${Math.round(pct)}% vs always-cloud` : ""}`;
}

type Period = "today" | "7d" | "30d";
const PERIODS: Array<{ value: Period; label: string }> = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

function PeriodToggle({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div role="group" aria-label="routing period" className="flex items-center gap-0.5 rounded-full bg-muted p-0.5">
      {PERIODS.map((p) => (
        <button
          key={p.value}
          type="button"
          aria-pressed={value === p.value}
          onClick={() => onChange(p.value)}
          className={cn(
            "rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors duration-[var(--dur-fast)]",
            value === p.value
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

/** Local vs. cloud counts for one period, from `/v1/savings?period=…`'s `by_route` (the same
 *  day-bucketed ledger the Saved section already reads) — a real day window, unlike
 *  /router/recent's fixed last-N-turns count. Falls back to `recent` when the gateway hasn't
 *  reported `by_route` yet (an older gateway, or the very first poll). */
function routeSplit(
  report: SavingsReport | null,
  recent: RecentReport | null,
  cheapest: string | null,
): { total: number; localCount: number; cloudCount: number } {
  if (report?.by_route) {
    const total = report.requests;
    const localCount = cheapest ? (report.by_route[cheapest]?.requests ?? 0) : 0;
    return { total, localCount, cloudCount: total - localCount };
  }
  const total = recent?.total ?? 0;
  const localCount = cheapest ? (recent?.byModel[cheapest] ?? 0) : 0;
  return { total, localCount, cloudCount: total - localCount };
}

export function UsageView({
  recent,
  savings,
  savings7d,
  savings30d,
  cheapest,
  onOpenChat,
}: {
  recent: RecentReport | null;
  savings: SavingsReport | null;
  savings7d: SavingsReport | null;
  savings30d: SavingsReport | null;
  cheapest: string | null;
  onOpenChat: () => void;
}) {
  const [period, setPeriod] = useState<Period>("today");
  const byPeriod: Record<Period, SavingsReport | null> = { today: savings, "7d": savings7d, "30d": savings30d };
  const { total, localCount, cloudCount } = routeSplit(byPeriod[period], recent, cheapest);
  const localPct = total > 0 ? Math.round((localCount / total) * 100) : 0;

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
      {/* Routing is just the bar now (maintainer steer) — the local/cloud breakdown that used
          to sit as permanent text below it lives in a hover tooltip instead; the period toggle
          picks which day-window the bar (and tooltip) describe. The aria-label SplitBar already
          carries (WF-DESIGN-0014) keeps the same info available without a mouse. */}
      <MetricRow
        icon={Route}
        label="Routing"
        help="Teal ran on the local model; amber went to cloud. Prompts are scored on-device and cloud is used only when needed."
        headerRight={<PeriodToggle value={period} onChange={setPeriod} />}
        bar={
          <Tooltip>
            <TooltipTrigger asChild>
              <div>
                <SplitBar
                  segments={[
                    { label: "local", count: localCount, color: "var(--primary)" },
                    { label: "cloud", count: cloudCount, color: "var(--route-cloud)" },
                  ]}
                />
              </div>
            </TooltipTrigger>
            <TooltipContent>
              {total > 0
                ? `${localPct}% routed locally · ${total} turn${total === 1 ? "" : "s"} — local: ${localCount} · cloud: ${cloudCount}`
                : "No turns yet"}
            </TooltipContent>
          </Tooltip>
        }
      />
      <Separator className="mx-5 w-auto" />
      {/* Saved is cost-like, not a quota — no bar, dark body lines, exactly the form
          CodexBar's own (bar-less) Cost section uses. */}
      <MetricRow
        icon={PiggyBank}
        label="Saved"
        help="Estimated spend avoided vs sending every turn to cloud."
        lines={savedLines.length > 0 ? savedLines : ["Not yet available"]}
      />
      <Separator className="mx-5 w-auto" />

      {/* Offline moved to the header switch (WF-DESIGN-0015 amendment): it is GLOBAL now —
          flipped through `config set gateway.offline` (WF-ADR-0044) — and a machine-wide mode
          belongs beside the machine-wide status, not in the action list. */}
      <ActionRow icon={MessageCircle} label="Wayfinder Chat" chevron onClick={onOpenChat} />
    </div>
  );
}
