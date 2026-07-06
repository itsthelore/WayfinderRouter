// A repeated metric section (WF-DESIGN-0014, mirrors clawrouter-usage.png's "Monthly budget"):
// bold label, a Bar, a left/right value line, and an optional muted insight line underneath.
import { Bar } from "@/components/menu/Bar";

export function MetricRow({
  label,
  fraction,
  color,
  left,
  right,
  insight,
}: {
  label: string;
  fraction: number;
  color?: string;
  left: string;
  right?: string;
  insight?: string;
}) {
  return (
    <div className="flex flex-col gap-2.5 px-5 py-5">
      <span className="text-[16px] font-bold">{label}</span>
      <Bar fraction={fraction} color={color} label={label} />
      <div className="flex items-center justify-between gap-2 text-[13px] text-muted-foreground">
        <span>{left}</span>
        {right && <span>{right}</span>}
      </div>
      {insight && <p className="text-[12px] text-muted-foreground">{insight}</p>}
    </div>
  );
}
