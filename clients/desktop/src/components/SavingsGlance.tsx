// "saved $0.42 today" (WF-DESIGN-0012) from /v1/savings. Hidden unless priced with real
// traffic — never "0 relative units". Sub-cent savings render as "<$0.01" rather than $0.00.
import { cn } from "@/lib/utils";

/** The /v1/savings fields the glance surfaces consume (fixture: savings.json). */
export interface SavingsReport {
  saved: number;
  saved_pct: number;
  priced: boolean;
  requests: number;
}

export function formatSaved(saved: number): string {
  if (saved < 0.01) return "<$0.01";
  return `$${saved.toFixed(2)}`;
}

export function SavingsGlance({
  report,
  period = "today",
  className,
}: {
  report: SavingsReport | null;
  period?: string;
  className?: string;
}) {
  if (!report || !report.priced || report.requests <= 0 || report.saved <= 0) return null;
  return (
    <span className={cn("text-[11px] font-medium text-muted-foreground", className)}>
      saved <span className="font-mono tabular-nums">{formatSaved(report.saved)}</span> {period}
    </span>
  );
}
