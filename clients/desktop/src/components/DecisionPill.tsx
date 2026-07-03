// The route pill (WF-DESIGN-0012): glyph in a fixed 1ch slot + uppercase route + model in
// mono. Colour flows from data-route -> --route-accent; the glyph never reflows on a route
// flip (fixed slot, colour crossfade only). Renders what the gateway decided — never scores.
import { routeGlyph, routeKind, routeLabel } from "@wayfinder/shared/decision";
import type { Decision } from "@wayfinder/shared/gateway";
import { cn } from "@/lib/utils";

export function DecisionPill({ decision, className }: { decision: Decision; className?: string }) {
  const kind = routeKind(decision);
  return (
    <span
      data-route={kind}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5",
        "text-[11px] font-medium tracking-wide uppercase",
        "transition-colors duration-[var(--dur-base)] ease-[var(--ease-standard)]",
        className,
      )}
      style={{
        color: "var(--route-accent)",
        background: kind === "local" ? "var(--accent)" : "var(--route-cloud-weak)",
      }}
    >
      <span aria-hidden className="inline-block w-[1ch] text-center">
        {routeGlyph(decision)}
      </span>
      <span className="sr-only">{kind === "local" ? "routed locally" : "routed to cloud"}</span>
      {routeLabel(decision)}
      <span className="font-mono text-[11px] font-normal normal-case text-muted-foreground">
        {decision.model}
      </span>
    </span>
  );
}
