// The help layer's one affordance (WF-DESIGN-0014): a small muted (?) that opens a compact
// panel on click. Help appears only when explicitly asked for — hover does nothing, labels
// stay plain. A real button, so it is keyboard-reachable for free. Panel copy keeps to
// WF-ADR-0042 §8's allowed claims and never carries an action.
import { CircleHelp } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function HelpTip({
  label,
  children,
  align = "start",
}: {
  /** Accessible name for the trigger, e.g. "about routing". */
  label: string;
  /** The panel copy — short lines, one sentence per idea. */
  children: React.ReactNode;
  align?: "start" | "center" | "end";
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className="rounded-full text-muted-foreground transition-colors duration-[var(--dur-base)] hover:text-foreground"
        >
          <CircleHelp className="size-3.5" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align={align}
        className="w-auto max-w-[280px] px-3.5 py-3 text-[13px] leading-snug"
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}
