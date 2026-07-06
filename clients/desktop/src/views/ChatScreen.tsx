// The chat sub-screen (WF-DESIGN-0014): pushed from the Usage list via the "Chat" chevron row.
// CodexBar has no chat surface to mirror, so this keeps WF-DESIGN-0012's turn semantics (the
// decision paints early from headers and is never cleared by a reply error) but renders the
// decision in the same flat row grammar as the rest of the popover — no floating card, no 22px
// hero digit (WF-DESIGN-0014's typography amendment).
//
// Chat holds a SESSION TRANSCRIPT (WF-DESIGN-0014 amendment): settled turns collapse into
// compact scrollback rows above the live turn — prompt, a one-line routing decision, the
// reply — and each send carries the recent history so the gateway sees a conversation, not a
// bag of one-offs. In-memory only; quitting the app is the clear affordance. The full decision
// hero (score bar, why rows) stays reserved for the live turn.
import { useEffect, useRef } from "react";
import { routeGlyph, routeLabel } from "@wayfinder/shared/decision";
import {
  historyFromTranscript,
  showDegradedBanner,
  showOfflineChip,
  type GatewayState,
  type SettledTurn,
} from "@/lib/appState";
import type { UseTurn } from "@/hooks/useTurn";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { DecisionSummary } from "@/components/DecisionSummary";
import { StreamingMessage } from "@/components/StreamingMessage";
import { OnboardingCard } from "@/components/OnboardingCard";
import { Composer } from "@/components/Composer";
import { ScrollArea } from "@/components/ui/scroll-area";

/** The user's message, in scrollback and above the live hero alike: dark, with a muted
 *  prompt marker — the flat list's answer to a chat bubble. */
function PromptLine({ prompt }: { prompt: string }) {
  return (
    <div className="select-text whitespace-pre-wrap break-words px-5 pt-3.5 text-[13px] font-medium leading-[1.45]">
      <span aria-hidden className="mr-1.5 text-muted-foreground">
        ›
      </span>
      {prompt}
    </div>
  );
}

/** One settled turn in the scrollback: prompt, a one-line decision, the reply (or the error).
 *  Compact on purpose — the score bar and why rows belong to the live turn only. */
function TranscriptTurn({ turn }: { turn: SettledTurn }) {
  return (
    <div className="flex flex-col border-b border-border pb-3.5">
      <PromptLine prompt={turn.prompt} />
      {turn.decision && (
        <div className="px-5 pt-1 text-[11px] text-muted-foreground">
          <span aria-hidden>{routeGlyph(turn.decision)}</span> {routeLabel(turn.decision)}{" "}
          <span className="font-mono">{turn.decision.model}</span>
        </div>
      )}
      {turn.reply ? (
        <div className="select-text whitespace-pre-wrap break-words px-5 pt-2 text-[13px] leading-[1.45]">
          {turn.reply}
        </div>
      ) : turn.error && turn.error !== "stopped" ? (
        <div className="px-5 pt-2 text-[11px]" style={{ color: "var(--destructive)" }}>
          reply failed: {turn.error}
        </div>
      ) : null}
    </div>
  );
}

export function ChatScreen({
  gw,
  turn,
}: {
  gw: GatewayState;
  turn: UseTurn;
}) {
  const offline = showOfflineChip(gw);
  const reducedMotion = useReducedMotion();
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Auto-follow: keep the newest content in view as turns settle and tokens stream. jsdom has
  // no scrollIntoView; real WebKit does — hence the optional call.
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: reducedMotion ? "instant" : "smooth" });
  }, [turn.transcript.length, turn.reply.length, reducedMotion]);

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
          {turn.transcript.map((settled, i) => (
            <TranscriptTurn key={i} turn={settled} />
          ))}
          {turn.decision ? (
            <>
              <PromptLine prompt={turn.prompt} />
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
          ) : turn.transcript.length === 0 ? (
            <p className="px-5 py-2.5 text-[13px] leading-[1.45] text-muted-foreground">
              Send a message — Wayfinder routes it and shows the score.
            </p>
          ) : null}
          <div ref={bottomRef} aria-hidden />
        </div>
      </ScrollArea>

      <div className="flex flex-col gap-2 border-t border-border bg-background p-5">
        <Composer
          streaming={turn.phase === "streaming"}
          onSend={(prompt) => void turn.send(prompt, historyFromTranscript(turn.transcript))}
          onStop={turn.stop}
        />
      </div>
    </div>
  );
}
