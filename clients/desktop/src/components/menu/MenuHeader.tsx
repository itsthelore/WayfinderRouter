// The detail header (WF-DESIGN-0014, mirrors clawrouter-usage.png): bold name + a right-aligned
// health label on line one, a freshness/status subtext on line two. Health renders as plain
// neutral text — CodexBar's own tier badge ("Max") is gray, not coloured; colour lives only in
// bar fills. When degraded, the subtext line itself is the fix-it affordance ("Missing X — add
// key…" → Settings → Keys): the status that names the problem carries the click, so the action
// list never grows an extra menu row (WF-DESIGN-0015, maintainer review). When the chat
// sub-screen is pushed, the same header swaps to a back control.
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
  onAddKey,
  className,
}: {
  gw: GatewayState;
  updatedText: string;
  /** Opens Settings → Keys; the degraded subtext renders as a link only when provided. */
  onAddKey?: () => void;
  className?: string;
}) {
  const offline = gw.offlineConfig || gw.offlineLocal;
  const health = offline ? "Offline" : HEALTH_LABEL[gw.health];
  const missing = gw.health === "degraded" && gw.missingKeys.length > 0;
  return (
    // Two explicit rows (the reference's own structure): the bold name stands alone on line
    // one; line two carries the freshness/status subtext left and the health label right on a
    // shared baseline — exactly where CodexBar's "Updated just now … Max" pair sits.
    <header className={cn("flex flex-col gap-1 bg-background px-5 py-5", className)}>
      <span className="text-[19px] font-bold leading-tight">Wayfinder</span>
      <div className="flex items-baseline justify-between gap-3">
        {missing && onAddKey ? (
          <button
            type="button"
            onClick={onAddKey}
            className="truncate text-left text-[14px] text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
          >
            Missing {gw.missingKeys.join(", ")} — add key…
          </button>
        ) : (
          <span className="truncate text-[14px] text-muted-foreground">
            {missing ? `Missing ${gw.missingKeys.join(", ")}` : updatedText}
          </span>
        )}
        <span className="shrink-0 text-[14px] text-muted-foreground">{health}</span>
      </div>
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
