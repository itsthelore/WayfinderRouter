// The route-split bar (WF-DESIGN-0014, amended): one track, proportional segments — teal local,
// amber cloud — because the routing stat is a composition, not a quota. CodexBar's fill bars
// measure "% of a limit used"; rendered that way Wayfinder's split misread (a half-full Routing
// bar looked half-done). Colour follows the route accents (WF-ADR-0020: teal = local, amber =
// money left the machine). Zero-count segments render nothing; an empty split is just the track.
import { cn } from "@/lib/utils";

export interface SplitSegment {
  label: string;
  count: number;
  color: string;
}

export function SplitBar({ segments, className }: { segments: SplitSegment[]; className?: string }) {
  const total = segments.reduce((n, s) => n + s.count, 0);
  const desc = segments
    .map((s) => `${s.label}: ${s.count}${total > 0 ? ` (${Math.round((s.count / total) * 100)}%)` : ""}`)
    .join(", ");
  return (
    <div
      role="img"
      aria-label={`route split — ${desc}`}
      className={cn("flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full bg-track", className)}
    >
      {total > 0 &&
        segments
          .filter((s) => s.count > 0)
          .map((s) => (
            <div
              key={s.label}
              className="h-full rounded-full transition-[width] duration-[var(--dur-slow)] ease-[var(--ease-standard)]"
              style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
            />
          ))}
    </div>
  );
}
