// A repeated metric section (WF-DESIGN-0014, mirrors clawrouter-usage.png's section rhythm):
// bold label, an optional bar (a SplitBar for compositions, a Bar for true 0..1 scalars, or
// nothing — CodexBar's own Cost section is bar-less), a left/right value line, and an optional
// muted insight line underneath.
export function MetricRow({
  label,
  bar,
  left,
  right,
  insight,
}: {
  label: string;
  bar?: React.ReactNode;
  left: string;
  right?: string;
  insight?: string;
}) {
  return (
    <div className="flex flex-col gap-2.5 px-5 py-5">
      <span className="text-[16px] font-bold">{label}</span>
      {bar}
      <div className="flex items-center justify-between gap-2 text-[13px] text-muted-foreground">
        <span>{left}</span>
        {right && <span>{right}</span>}
      </div>
      {insight && <p className="text-[12px] text-muted-foreground">{insight}</p>}
    </div>
  );
}
