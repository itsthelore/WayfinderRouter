// The one bar primitive every metric row and the chat decision score use (WF-DESIGN-0014): a
// thin rounded `--track` rail, a fill, and a round knob at the fill's edge — CodexBar's own
// bar shape (clawrouter-usage.png), not a rail-only WF-DESIGN-0012 leftover. Colour is the one
// place this file spends it (WF-DESIGN-0014 "Colour is used only for bar fills and the
// tab-strip"); everything around a Bar stays neutral text.
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
      className={cn("relative h-1.5 w-full rounded-full bg-track", className)}
    >
      <div
        className="h-1.5 rounded-full transition-[width] duration-[var(--dur-slow)] ease-[var(--ease-standard)]"
        style={{ width: `${pct}%`, background: color }}
      />
      <span
        aria-hidden
        className="absolute top-1/2 size-2.5 -translate-y-1/2 rounded-full"
        style={{
          left: `calc(${pct}% - 5px)`,
          background: color,
          boxShadow: "0 0 0 2px var(--popover)",
        }}
      />
    </div>
  );
}
