// The chat sub-screen (WF-DESIGN-0014): pushed from the Usage list via the "Chat" chevron row.
// CodexBar has no chat surface to mirror, so this keeps WF-DESIGN-0012's turn semantics (the
// decision paints early from headers and is never cleared by a reply error) but renders the
// decision in the same flat row grammar as the rest of the popover — no floating card, no 22px
// hero digit (WF-DESIGN-0014's typography amendment).
import { showDegradedBanner, showOfflineChip, type GatewayState } from "@/lib/appState";
import type { UseTurn } from "@/hooks/useTurn";
import { DecisionSummary } from "@/components/DecisionSummary";
import { StreamingMessage } from "@/components/StreamingMessage";
import { OnboardingCard } from "@/components/OnboardingCard";
import { Composer } from "@/components/Composer";
import { ScrollArea } from "@/components/ui/scroll-area";

export function ChatScreen({
  gw,
  turn,
}: {
  gw: GatewayState;
  turn: UseTurn;
}) {
  const offline = showOfflineChip(gw);
  // One polite announcement on completion (WF-DESIGN-0012) — never a live region on the token
  // stream. Empty while streaming/idle so only the settled result speaks.
  const announcement =
    turn.phase === "done" && turn.decision
      ? `reply finished, routed ${turn.decision.isLocal ? "locally" : "to cloud"}`
      : "";
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
      {showDegradedBanner(gw) && (
        <div role="alert" className="px-5 py-2 text-[11px] leading-[1.45] text-muted-foreground">
          Missing keys — set <span className="font-mono">{gw.missingKeys.join(", ")}</span> to
          route to those models.
        </div>
      )}

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col">
          {offline && (
            <div className="px-5 pt-2 text-[11px] text-muted-foreground">
              offline — routing to the cheapest tier
            </div>
          )}
          {turn.decision ? (
            <>
              <DecisionSummary decision={turn.decision} enriched={turn.enriched} offline={offline} />
              {turn.decision.decisionOnly ? (
                <OnboardingCard />
              ) : turn.reply || turn.phase === "streaming" ? (
                <StreamingMessage reply={turn.reply} streaming={turn.phase === "streaming"} />
              ) : null}
              {turn.phase === "error" && turn.error !== "stopped" && (
                <div className="px-5 py-2 text-[11px]" style={{ color: "var(--destructive)" }}>
                  reply failed: {turn.error} — the decision above still stands
                </div>
              )}
            </>
          ) : (
            <p className="px-5 py-2.5 text-[13px] leading-[1.45] text-muted-foreground">
              Send a message — Wayfinder routes it and shows the score.
            </p>
          )}
        </div>
      </ScrollArea>

      <div className="flex flex-col gap-2 border-t border-border bg-background p-5">
        <Composer
          streaming={turn.phase === "streaming"}
          onSend={(prompt) => void turn.send(prompt)}
          onStop={turn.stop}
        />
      </div>
    </div>
  );
}
