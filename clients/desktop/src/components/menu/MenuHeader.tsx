// The detail header (WF-DESIGN-0014, mirrors the reference): bold name alone on line one;
// line two carries the freshness/status subtext left and — where the reference parks its
// passive tier badge — Wayfinder's one live mode control: the health label plus the GLOBAL
// Offline switch. The switch means machine-wide (it flips `[gateway] offline` through the
// config seam, WF-ADR-0044, affecting every client of the gateway) — which is exactly why it
// earned header placement; the old per-app chat-only toggle it replaces did not. When
// degraded, the subtext line itself is the fix-it affordance ("Missing X — add key…" →
// Settings → Keys). When the chat sub-screen is pushed, the same header swaps to a back
// control.
import type { GatewayState } from "@/lib/appState";
import { HelpTip } from "@/components/menu/HelpTip";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import wordmark from "@/assets/wayfinder-wordmark.png";

const HEALTH_LABEL: Record<GatewayState["health"], string> = {
  ok: "Running",
  degraded: "Degraded",
  unreachable: "Unreachable",
  unknown: "Checking…",
};

// The help layer (WF-DESIGN-0014): the one-word status stays terse; what it means lives in
// the (?) panel — one sentence per idea, WF-ADR-0042 §8's allowed claims only.
const HEALTH_HELP: Record<GatewayState["health"], string> = {
  ok: "Running — the gateway is routing turns.",
  degraded: "Degraded — a provider key is missing (Settings → Keys).",
  unreachable: "Unreachable — the gateway may be stopped (Settings → Gateway).",
  unknown: "Checking — waiting for the first health report.",
};

const OFFLINE_HELP = "Offline — every turn routes to the local model.";

const SWITCH_HELP =
  "The switch flips offline mode machine-wide — every app using Wayfinder follows. Nothing leaves this Mac while it's on.";

export function MenuHeader({
  gw,
  updatedText,
  onAddKey,
  onOfflineToggle,
  offlinePending = false,
  className,
}: {
  gw: GatewayState;
  updatedText: string;
  /** Opens Settings → Keys; the degraded subtext renders as a link only when provided. */
  onAddKey?: () => void;
  /** Flips GLOBAL offline delivery via `config set gateway.offline` (WF-ADR-0044). */
  onOfflineToggle?: (on: boolean) => void;
  /** True while a flip is in flight (until the next healthz poll confirms it). */
  offlinePending?: boolean;
  className?: string;
}) {
  const health = gw.offlineConfig ? "Offline" : HEALTH_LABEL[gw.health];
  const missing = gw.health === "degraded" && gw.missingKeys.length > 0;
  return (
    <header className={cn("flex flex-col gap-1 bg-background px-5 py-5", className)}>
      <img src={wordmark} alt="Wayfinder" className="h-[18px] w-auto self-start" />
      <div className="flex items-center justify-between gap-3">
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
        <span className="flex shrink-0 items-center gap-2">
          <span className="text-[14px] text-muted-foreground">{health}</span>
          {onOfflineToggle && (
            <Switch
              size="sm"
              aria-label="offline mode — everything routes local, machine-wide"
              checked={gw.offlineConfig}
              disabled={offlinePending}
              onCheckedChange={onOfflineToggle}
            />
          )}
          <HelpTip label="about status and offline mode" align="end">
            <p>{gw.offlineConfig ? OFFLINE_HELP : HEALTH_HELP[gw.health]}</p>
            {onOfflineToggle && <p className="mt-1.5">{SWITCH_HELP}</p>}
          </HelpTip>
        </span>
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
      <span className="text-[19px] font-bold">Wayfinder Chat</span>
    </header>
  );
}
