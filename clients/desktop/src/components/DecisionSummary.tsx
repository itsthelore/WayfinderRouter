// The live turn's decision, restyled to the mockup's "prompt analysis" grammar (WF-DESIGN-0014
// amendment): a big score numeral in the route accent, a route pill, and the five feature rows
// behind the score with a one-line "why". Decision-first (WF-ADR-0020), deterministic and offline
// (WF-ADR-0001) — this reads the decision the gateway already made and NEVER scores. The feature
// rows and the "why" line need the enriched debug payload (the header-only decision carries no
// contributions), so they skeleton until enrichment lands; the score, bar, and route paint at once.
import type { ComponentType } from "react";
import { CircleCheck, Cloud, Code, List, Monitor, Rows3, Sigma, Type } from "lucide-react";
import { featureRows, formatScore, routeKind, whyLine } from "@wayfinder/shared/decision";
import type { Decision } from "@wayfinder/shared/gateway";
import { Bar } from "@/components/menu/Bar";
import { Skeleton } from "@/components/ui/skeleton";

function routeColor(kind: "local" | "cloud"): string {
  return kind === "local" ? "var(--primary)" : "var(--route-cloud)";
}

// featureRows keys → the icon that fronts each row. Decorative (aria-hidden); the label carries
// the meaning for assistive tech.
const FEATURE_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  words: Type,
  lists: List,
  code: Code,
  sections: Rows3,
  lexical: Sigma,
};

export function DecisionSummary({
  decision,
  enriched,
  offline = false,
  cache = false,
}: {
  decision: Decision;
  enriched: boolean;
  offline?: boolean;
  cache?: boolean;
}) {
  const kind = routeKind(decision);
  const color = routeColor(kind);
  const RouteIcon = kind === "local" ? Monitor : Cloud;
  const routeName = kind === "local" ? "Local" : "Cloud";
  const pillBg = kind === "local" ? "var(--accent)" : "var(--route-cloud-weak)";
  // The decision is deterministic and keyless by construction (WF-ADR-0001); the delivery-state
  // markers ride the same caption when they apply.
  const caption = ["Deterministic · No model call"];
  if (decision.decisionOnly) caption.push("decision only");
  if (offline) caption.push("offline");
  if (cache) caption.push("cache hit");

  return (
    <div className="flex flex-col gap-3 px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Complexity score
          </span>
          <span className="text-[32px] font-bold leading-none tabular-nums" style={{ color }}>
            {formatScore(decision.score)}
          </span>
        </div>
        <div
          className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-semibold"
          style={{ background: pillBg, color }}
        >
          <RouteIcon aria-hidden className="size-4" />
          <span className="sr-only">{kind === "local" ? "routed locally" : "routed to cloud"}</span>
          Route: {routeName}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <span aria-hidden className="text-[11px] tabular-nums text-muted-foreground">
          0
        </span>
        <Bar fraction={decision.score} color={color} label="complexity score" />
        <span aria-hidden className="text-[11px] tabular-nums text-muted-foreground">
          1
        </span>
      </div>

      <span className="text-[11px] text-muted-foreground">{caption.join(" · ")}</span>

      {enriched ? <FeatureChecklist decision={decision} color={color} /> : <FeatureSkeleton />}
    </div>
  );
}

function FeatureChecklist({ decision, color }: { decision: Decision; color: string }) {
  const rows = featureRows(decision);
  return (
    <div className="flex flex-col gap-2.5 border-t border-border pt-3">
      <ul aria-label="prompt features" className="flex flex-col gap-1.5">
        {rows.map((r) => {
          const Icon = FEATURE_ICONS[r.key];
          return (
            <li key={r.key} aria-label={`${r.label}: ${r.value}`} className="flex items-center gap-2 text-[13px]">
              <Icon aria-hidden className="size-3.5 text-muted-foreground" />
              <span aria-hidden>{r.label}</span>
              <span aria-hidden className="ml-auto tabular-nums text-muted-foreground">
                {r.value}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="flex items-start gap-1.5 text-[12px] text-muted-foreground">
        <CircleCheck aria-hidden className="mt-px size-3.5 shrink-0" style={{ color }} />
        <span>
          <span className="font-medium text-foreground">Why:</span> {whyLine(decision)}
        </span>
      </p>
    </div>
  );
}

function FeatureSkeleton() {
  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3" aria-hidden>
      {Array.from({ length: 5 }, (_, i) => (
        <Skeleton key={i} className="h-3.5 w-full" />
      ))}
    </div>
  );
}
