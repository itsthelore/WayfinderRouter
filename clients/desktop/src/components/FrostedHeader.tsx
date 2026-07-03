// The frosted header (WF-DESIGN-0012): ✦ Wayfinder · health dot · savings glance, on the L1
// wash. Status is ambient — the dot, not a sentence. Everything here is rendered from the
// gateway's own health/savings; nothing is computed.
import { StatusDot, type DotStatus } from "@/components/StatusDot";
import { SavingsGlance, type SavingsReport } from "@/components/SavingsGlance";

export function FrostedHeader({
  status,
  missingKeys,
  savings,
}: {
  status: DotStatus;
  missingKeys: string[];
  savings: SavingsReport | null;
}) {
  return (
    <header className="flex items-center justify-between gap-2 bg-background px-3.5 py-2.5">
      <span className="flex items-center gap-1.5 text-[13px] font-semibold">
        <span aria-hidden className="text-primary">
          ✦
        </span>
        Wayfinder
      </span>
      <div className="flex items-center gap-2.5">
        <SavingsGlance report={savings} />
        <StatusDot status={status} missingKeys={missingKeys} />
      </div>
    </header>
  );
}
