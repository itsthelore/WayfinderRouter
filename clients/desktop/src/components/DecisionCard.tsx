// The decision hero (WF-DESIGN-0012): an L2 card at the 18px hero radius carrying the pill,
// the score readout, the routingBadge sub-line (" · decision only" / " · offline" come free
// from the shared helper), and the why-rows behind a disclosure. The whole card takes
// data-route so every accent inside follows the gateway's route.
import { useState } from "react";
import { routeKind, routingBadge } from "@wayfinder/shared/decision";
import type { Decision } from "@wayfinder/shared/gateway";
import { Card, CardContent } from "@/components/ui/card";
import { DecisionPill } from "@/components/DecisionPill";
import { ScoreReadout } from "@/components/ScoreReadout";
import { WhyBars } from "@/components/WhyBars";
import { cn } from "@/lib/utils";

export function DecisionCard({
  decision,
  enriched,
  offline = false,
  cache = false,
  className,
}: {
  decision: Decision;
  enriched: boolean;
  offline?: boolean;
  cache?: boolean;
  className?: string;
}) {
  const [whyOpen, setWhyOpen] = useState(false);
  return (
    <Card data-route={routeKind(decision)} className={cn("rounded-hero", className)}>
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between gap-2">
          <DecisionPill decision={decision} />
          <button
            type="button"
            aria-expanded={whyOpen}
            onClick={() => setWhyOpen((v) => !v)}
            className={cn(
              "rounded-sm px-1.5 py-0.5 text-[11px] font-medium tracking-wide uppercase",
              "text-muted-foreground hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            )}
          >
            {whyOpen ? "⌄ why" : "› why"}
          </button>
        </div>
        <ScoreReadout decision={decision} />
        <div className="text-[11px] font-medium tracking-wide text-muted-foreground">
          {routingBadge(decision, { offline, cache })}
        </div>
        {whyOpen && <WhyBars decision={decision} enriched={enriched} />}
      </CardContent>
    </Card>
  );
}
