// A plain fill meter (WF-DESIGN-0014, amended): a rounded `--track` rail and a fill, no knob.
// An earlier pass copied CodexBar's slider-thumb bars verbatim, but a thumb reads as a draggable
// control and CodexBar's bars carry quota semantics Wayfinder's data doesn't have (maintainer
// review — see the deviation note in WF-DESIGN-0014). This form is only for true 0..1 scalars:
// the complexity score and a contribution's share. The route split uses SplitBar (composition).
import { cn } from "@/lib/utils";

export function Bar({
  fraction,
  color = "var(--primary)",
  label,
  className,
}: {
  /** 0..1, already clamped by the caller (each caller knows its own empty-state floor). */
  fraction: number;
  color?: string;
  label: string;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(1, fraction)) * 100;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      className={cn("h-1.5 w-full rounded-full bg-track", className)}
    >
      <div
        className="h-full rounded-full transition-[width] duration-[var(--dur-slow)] ease-[var(--ease-standard)]"
        // minWidth floors a nonzero fill at a 12px pill (the reference's 2% bar is a visible
        // pill); a genuine zero stays an empty track, like the reference's "Sonnet 0% used".
        style={{ width: `${pct}%`, minWidth: pct > 0 ? "12px" : undefined, background: color }}
      />
    </div>
  );
}
