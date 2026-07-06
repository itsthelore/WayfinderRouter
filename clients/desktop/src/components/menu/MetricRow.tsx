// A repeated metric section (WF-DESIGN-0014, mirrors the reference's section rhythm): bold
// label, an optional bar (a SplitBar for compositions, a Bar for true 0..1 scalars, or
// nothing — CodexBar's own Cost section is bar-less), then EITHER a left/right value line OR
// stacked body `lines` (the Cost form). Hierarchy is the reference's own: the LEFT value and
// body lines are dark foreground; only the RIGHT value and the insight line are muted.
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function MetricRow({
  label,
  help,
  bar,
  left,
  right,
  lines,
  insight,
}: {
  label: string;
  /** Hover/focus explanation of what this stat means (WF-DESIGN-0014's tooltip layer). The
      label stays terse; the semantics live here — shown on hover and on keyboard focus. */
  help?: string;
  bar?: React.ReactNode;
  /** The dark headline value under the bar ("2% used" form). Ignored when `lines` is set. */
  left?: string;
  /** The muted right-aligned counterpart ("Resets in 3h 53m" form). */
  right?: string;
  /** Cost-style stacked dark body lines ("Today: … / Last 30 days: …"). Replaces left/right. */
  lines?: string[];
  insight?: string;
}) {
  const labelClass = "text-[16px] font-bold";
  return (
    <div className="flex flex-col gap-3 px-5 py-5">
      {help ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className={`${labelClass} cursor-help self-start rounded-sm`}>
              {label}
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom" align="start" className="max-w-[300px]">
            {help}
          </TooltipContent>
        </Tooltip>
      ) : (
        <span className={labelClass}>{label}</span>
      )}
      {bar}
      {lines ? (
        <div className="flex flex-col gap-1.5 text-[14px]">
          {lines.map((line) => (
            <span key={line}>{line}</span>
          ))}
        </div>
      ) : (
        <div className="flex items-baseline justify-between gap-2 text-[14px]">
          <span>{left}</span>
          {right && <span className="text-muted-foreground">{right}</span>}
        </div>
      )}
      {insight && <p className="text-[13px] text-muted-foreground">{insight}</p>}
    </div>
  );
}
