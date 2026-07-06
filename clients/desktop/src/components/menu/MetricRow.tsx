// A repeated metric section (WF-DESIGN-0014, mirrors the reference's section rhythm): bold
// label, an optional bar (a SplitBar for compositions, a Bar for true 0..1 scalars, or
// nothing — CodexBar's own Cost section is bar-less), then EITHER a left/right value line OR
// stacked body `lines` (the Cost form). Hierarchy is the reference's own: the LEFT value and
// body lines are dark foreground; only the RIGHT value and the insight line are muted.
import { HelpTip } from "@/components/menu/HelpTip";

export function MetricRow({
  icon: Icon,
  label,
  help,
  headerRight,
  bar,
  left,
  right,
  lines,
  insight,
}: {
  /** A small glyph before the label — purely decorative (WF-DESIGN-0014 icon pass). */
  icon?: React.ComponentType<{ className?: string }>;
  label: string;
  /** What this stat means — one short sentence per idea, shown in a (?) panel beside the
      label on click (WF-DESIGN-0014's help layer). The label itself stays plain. */
  help?: string;
  /** Right-aligned header content, e.g. the Routing row's Today/7d/30d period toggle. */
  headerRight?: React.ReactNode;
  bar?: React.ReactNode;
  /** The dark headline value under the bar ("2% used" form). Ignored when `lines` is set. */
  left?: string;
  /** The muted right-aligned counterpart ("Resets in 3h 53m" form). */
  right?: string;
  /** Cost-style stacked dark body lines ("Today: … / Last 30 days: …"). Replaces left/right. */
  lines?: string[];
  insight?: string;
}) {
  return (
    <div className="flex flex-col gap-3 px-5 py-5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[16px] font-bold">
          {Icon && <Icon aria-hidden className="size-4 text-muted-foreground" />}
          {label}
          {help && <HelpTip label={`about ${label.toLowerCase()}`}>{help}</HelpTip>}
        </span>
        {headerRight}
      </div>
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
