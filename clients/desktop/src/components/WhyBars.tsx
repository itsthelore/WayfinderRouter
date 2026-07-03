// The "why" rows (WF-DESIGN-0012): topContributions(d, 4) — 11px label, a share-width bar in
// the route accent, mono value right-aligned. Skeleton rows until the enriched decision lands
// (the trailing wayfinder event); rows read "word count, 41% of score" to VoiceOver.
import { topContributions } from "@wayfinder/shared/decision";
import type { Decision } from "@wayfinder/shared/gateway";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function WhyBars({
  decision,
  enriched,
  className,
}: {
  decision: Decision;
  enriched: boolean;
  className?: string;
}) {
  if (!enriched) {
    return (
      <div className={cn("flex flex-col gap-2", className)} aria-hidden>
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-3 w-full" />
        ))}
      </div>
    );
  }
  const rows = topContributions(decision, 4);
  return (
    <ul aria-label="top scoring factors" className={cn("flex flex-col gap-2", className)}>
      {rows.map((c) => {
        const pct = Math.round(c.share * 100);
        const label = c.name.replace(/_/g, " ");
        return (
          <li
            key={c.name}
            aria-label={`${label}, ${pct}% of score`}
            className="grid grid-cols-[7rem_1fr_auto] items-center gap-2"
          >
            <span aria-hidden className="truncate text-[11px] font-medium tracking-wide uppercase text-muted-foreground">
              {label}
            </span>
            <span aria-hidden className="h-1 rounded-full bg-track">
              <span
                className="block h-1 rounded-full"
                style={{ width: `${pct}%`, background: "var(--route-accent)" }}
              />
            </span>
            <span aria-hidden className="font-mono text-[11px] tabular-nums text-muted-foreground">
              {c.value}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
