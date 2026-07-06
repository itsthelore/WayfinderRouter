// The detail header (WF-DESIGN-0014, mirrors clawrouter-usage.png): bold name + a right-aligned
// health label on line one, a freshness/status subtext on line two. Health renders as plain
// neutral text — CodexBar's own tier badge ("Max") is gray, not coloured; colour lives only in
// bar fills. When the chat sub-screen is pushed, the same header swaps to a back control.
import type { GatewayState } from "@/lib/appState";
import { cn } from "@/lib/utils";

const HEALTH_LABEL: Record<GatewayState["health"], string> = {
  ok: "Running",
  degraded: "Degraded",
  unreachable: "Unreachable",
  unknown: "Checking…",
};

export function MenuHeader({
  gw,
  updatedText,
  className,
}: {
  gw: GatewayState;
  updatedText: string;
  className?: string;
}) {
  const offline = gw.offlineConfig || gw.offlineLocal;
  const health = offline ? "Offline" : HEALTH_LABEL[gw.health];
  const subtext =
    gw.health === "degraded" && gw.missingKeys.length > 0
      ? `Missing ${gw.missingKeys.join(", ")}`
      : updatedText;
  return (
    <header className={cn("flex items-center justify-between gap-3 bg-background px-5 py-5", className)}>
      <div className="flex flex-col gap-1">
        <span className="text-[19px] font-bold leading-tight">Wayfinder</span>
        <span className="truncate text-[13px] text-muted-foreground">{subtext}</span>
      </div>
      <span className="shrink-0 text-[13px] text-muted-foreground">{health}</span>
    </header>
  );
}

export function ChatHeader({ onBack }: { onBack: () => void }) {
  return (
    <header className="flex items-center gap-2 bg-background px-5 py-5">
      <button
        type="button"
        onClick={onBack}
        aria-label="back to Wayfinder"
        className="text-[17px] text-muted-foreground transition-colors duration-[var(--dur-base)] hover:text-foreground"
      >
        ‹
      </button>
      <span className="text-[19px] font-bold">Chat</span>
    </header>
  );
}
