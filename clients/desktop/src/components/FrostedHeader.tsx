// The frosted header (WF-DESIGN-0012): ✦ Wayfinder · savings glance · health dot · settings
// gear, on the L1 wash. Status is ambient — the dot, not a sentence. Everything here is
// rendered from the gateway's own health/savings; nothing is computed.
import { StatusDot, type DotStatus } from "@/components/StatusDot";
import { SavingsGlance, type SavingsReport } from "@/components/SavingsGlance";

export function FrostedHeader({
  status,
  missingKeys,
  savings,
  onSettings,
}: {
  status: DotStatus;
  missingKeys: string[];
  savings: SavingsReport | null;
  onSettings?: () => void;
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
        {onSettings && (
          <button
            aria-label="settings"
            onClick={onSettings}
            className="text-[13px] leading-none text-muted-foreground transition-colors duration-[var(--dur-base)] hover:text-foreground"
          >
            ⚙
          </button>
        )}
      </div>
    </header>
  );
}
