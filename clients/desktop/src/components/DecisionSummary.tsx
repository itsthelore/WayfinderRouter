// The decision, in the same flat grammar as every other row (WF-DESIGN-0014) — no more
// rounded-hero card or 22px mono hero digit (that was WF-DESIGN-0012's ornament; CodexBar's own
// numbers are all inline body text). Decision-first hierarchy survives (WF-ADR-0020): this is
// still the first thing the chat screen renders, above the reply.
import { useState } from "react";
import { routeGlyph, routeKind, routeLabel, routingBadge, topContributions } from "@wayfinder/shared/decision";
import type { Decision } from "@wayfinder/shared/gateway";
import { Bar } from "@/components/menu/Bar";
import { Skeleton } from "@/components/ui/skeleton";

function routeColor(kind: "local" | "cloud"): string {
  return kind === "local" ? "var(--primary)" : "var(--route-cloud)";
}

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
  const [whyOpen, setWhyOpen] = useState(false);
  const kind = routeKind(decision);
  const color = routeColor(kind);
  return (
    <div className="flex flex-col gap-2.5 px-5 py-5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[16px] font-bold">
          <span aria-hidden>{routeGlyph(decision)}</span>{" "}
          <span className="sr-only">{kind === "local" ? "routed locally" : "routed to cloud"}</span>
          {routeLabel(decision)}{" "}
          <span className="font-mono text-[13px] font-normal text-muted-foreground">{decision.model}</span>
        </span>
        <button
          type="button"
          aria-expanded={whyOpen}
          onClick={() => setWhyOpen((v) => !v)}
          className="text-[13px] text-muted-foreground hover:text-foreground"
        >
          {whyOpen ? "⌄ why" : "› why"}
        </button>
      </div>
      <Bar fraction={decision.score} color={color} label="complexity score" />
      <div className="text-[13px] text-muted-foreground">{routingBadge(decision, { offline, cache })}</div>
      {whyOpen && <WhyRows decision={decision} enriched={enriched} color={color} />}
    </div>
  );
}

function WhyRows({ decision, enriched, color }: { decision: Decision; enriched: boolean; color: string }) {
  if (!enriched) {
    return (
      <div className="flex flex-col gap-2 pt-1" aria-hidden>
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-3 w-full" />
        ))}
      </div>
    );
  }
  const rows = topContributions(decision, 4);
  return (
    <ul aria-label="top scoring factors" className="flex flex-col gap-2 pt-1">
      {rows.map((c) => {
        const pct = Math.round(c.share * 100);
        const label = c.name.replace(/_/g, " ");
        return (
          <li key={c.name} aria-label={`${label}, ${pct}% of score`} className="grid grid-cols-[7rem_1fr_auto] items-center gap-2">
            <span aria-hidden className="truncate text-[11px] text-muted-foreground">
              {label}
            </span>
            <Bar fraction={c.share} color={color} label={label} className="h-1" />
            <span aria-hidden className="font-mono text-[11px] tabular-nums text-muted-foreground">
              {c.value}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
