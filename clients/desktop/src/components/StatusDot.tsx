// The 7px health dot (WF-DESIGN-0012): teal ok / amber degraded / muted unreachable, with a
// tooltip listing missing_keys verbatim (env-var names, mono — never values, WF-ADR-0025).
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type DotStatus = "ok" | "degraded" | "unreachable";

const DOT_LABEL: Record<DotStatus, string> = {
  ok: "gateway running",
  degraded: "gateway degraded",
  unreachable: "gateway unreachable",
};

export function StatusDot({
  status,
  missingKeys = [],
  className,
}: {
  status: DotStatus;
  missingKeys?: string[];
  className?: string;
}) {
  const dot = (
    <span
      role="status"
      aria-label={DOT_LABEL[status]}
      className={cn("inline-block size-[7px] rounded-full", className)}
      style={{
        background:
          status === "ok"
            ? "var(--primary)"
            : status === "degraded"
              ? "var(--route-cloud)"
              : "var(--muted-foreground)",
      }}
    />
  );
  if (status !== "degraded" || missingKeys.length === 0) return dot;
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{dot}</TooltipTrigger>
        <TooltipContent side="bottom">
          <div className="text-[11px]">missing keys</div>
          {missingKeys.map((k) => (
            <div key={k} className="font-mono text-[11px]">
              {k}
            </div>
          ))}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
