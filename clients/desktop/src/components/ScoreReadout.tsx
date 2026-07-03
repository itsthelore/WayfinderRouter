// The hero number (WF-DESIGN-0012): the score at 22px mono tabular-nums over a thin --track
// rail whose fill is the score in the route accent. A rail, not a dial — at 360px a dial is
// ornament. No digit tweening; the rail width transitions (240ms).
import { formatScore } from "@wayfinder/shared/decision";
import type { Decision } from "@wayfinder/shared/gateway";
import { cn } from "@/lib/utils";

export function ScoreReadout({ decision, className }: { decision: Decision; className?: string }) {
  const pct = Math.max(0, Math.min(1, decision.score)) * 100;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="font-mono text-[22px] font-semibold tabular-nums leading-none">
        {formatScore(decision.score)}
      </div>
      <div
        className="h-1 w-full rounded-full bg-track"
        role="meter"
        aria-label="complexity score"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={decision.score}
      >
        <div
          className="h-1 rounded-full transition-[width] duration-[var(--dur-slow)] ease-[var(--ease-standard)]"
          style={{ width: `${pct}%`, background: "var(--route-accent)" }}
        />
      </div>
    </div>
  );
}
