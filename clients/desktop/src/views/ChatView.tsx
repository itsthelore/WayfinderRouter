// The reachable-gateway surface (WF-DESIGN-0012): the decision hero over the streamed reply
// over the pinned composer, with the degraded banner and offline chip as adornments that
// animate above the scroll region and never push the composer. Covers healthy / degraded /
// decision-only / offline — the mode differences are these adornments plus the reply swap.
import { showDegradedBanner, showOfflineChip, type GatewayState } from "@/lib/appState";
import type { UseTurn } from "@/hooks/useTurn";
import { DecisionCard } from "@/components/DecisionCard";
import { StreamingMessage } from "@/components/StreamingMessage";
import { OnboardingCard } from "@/components/OnboardingCard";
import { OfflineToggle } from "@/components/OfflineToggle";
import { Composer } from "@/components/Composer";
import { ScrollArea } from "@/components/ui/scroll-area";

export function ChatView({
  gw,
  turn,
  onOfflineToggle,
}: {
  gw: GatewayState;
  turn: UseTurn;
  onOfflineToggle: (on: boolean) => void;
}) {
  const offline = showOfflineChip(gw);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {showDegradedBanner(gw) && (
        <div
          role="alert"
          className="bg-[var(--route-cloud-weak)] px-3.5 py-2 text-[11px] leading-[1.45]"
          style={{ color: "var(--route-cloud)" }}
        >
          Missing keys — set{" "}
          <span className="font-mono">{gw.missingKeys.join(", ")}</span> to route to those models.
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-3 p-3.5">
          {offline && (
            <div className="text-[11px] font-medium text-muted-foreground">
              offline — routing to the cheapest tier
            </div>
          )}
          {turn.decision ? (
            <>
              <DecisionCard decision={turn.decision} enriched={turn.enriched} offline={offline} />
              {turn.decision.decisionOnly ? (
                <OnboardingCard />
              ) : turn.reply || turn.phase === "streaming" ? (
                <StreamingMessage reply={turn.reply} streaming={turn.phase === "streaming"} />
              ) : null}
              {turn.phase === "error" && turn.error !== "stopped" && (
                <div className="text-[11px]" style={{ color: "var(--destructive)" }}>
                  reply failed: {turn.error} — the decision above still stands
                </div>
              )}
            </>
          ) : (
            <p className="text-[13px] leading-[1.45] text-muted-foreground">
              Send a message — Wayfinder routes it and shows the score.
            </p>
          )}
        </div>
      </ScrollArea>

      <div className="flex flex-col gap-2 border-t border-border bg-background p-3.5">
        <OfflineToggle
          on={gw.offlineLocal}
          lockedByConfig={gw.offlineConfig}
          onChange={onOfflineToggle}
          className="self-start"
        />
        <Composer
          streaming={turn.phase === "streaming"}
          onSend={(prompt) => void turn.send(prompt)}
          onStop={turn.stop}
        />
      </div>
    </div>
  );
}
