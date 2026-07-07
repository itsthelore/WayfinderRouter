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
import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { routeGlyph, routeLabel } from "@wayfinder/shared/decision";
import {
  historyFromTranscript,
  showDegradedBanner,
  showOfflineChip,
  type GatewayState,
  type SettledTurn,
} from "@/lib/appState";
import type { UseTurn } from "@/hooks/useTurn";
import { openSettings } from "@/lib/ipc";
import { DecisionSummary } from "@/components/DecisionSummary";
import { StreamingMessage } from "@/components/StreamingMessage";
import { OnboardingCard } from "@/components/OnboardingCard";
import { Composer } from "@/components/Composer";
import type { SlashCommand } from "@/components/SlashMenu";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";

/** The user's message, in scrollback and above the live hero alike: dark, with a muted
 *  prompt marker — the flat list's answer to a chat bubble. The live turn gets a copy button
 *  (mockup top-right); scrollback turns don't, to keep the compact rows quiet. */
function PromptLine({ prompt, copyable = false }: { prompt: string; copyable?: boolean }) {
  return (
    <div className="flex items-start gap-2 px-5 pt-3.5">
      <div className="min-w-0 flex-1 select-text whitespace-pre-wrap break-words text-[13px] font-medium leading-[1.45]">
        <span aria-hidden className="mr-1.5 text-muted-foreground">
          ›
        </span>
        {prompt}
      </div>
      {copyable && <CopyButton text={prompt} />}
    </div>
  );
}

/** Copy the prompt to the clipboard (webview-safe, no Rust — WF-ADR-0042). Flips to a check for a
 *  beat so the click has feedback; a blocked clipboard just no-ops. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "prompt copied" : "copy prompt"}
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      }}
      className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:text-foreground"
    >
      {copied ? <Check aria-hidden className="size-3.5" /> : <Copy aria-hidden className="size-3.5" />}
    </button>
  );
}

/** One settled turn in the scrollback: prompt, a one-line decision, the reply (or the error).
 *  Compact on purpose — the score bar and why rows belong to the live turn only. */
function TranscriptTurn({ turn }: { turn: SettledTurn }) {
  return (
    <div className="flex flex-col border-b border-border pb-3.5">
      <PromptLine prompt={turn.prompt} />
      {turn.decision && (
        <div className="px-5 pt-1">
          <Marker className="text-[11px]">
            <MarkerIcon aria-hidden>{routeGlyph(turn.decision)}</MarkerIcon>
            <MarkerContent>
              {routeLabel(turn.decision)} <span className="font-mono">{turn.decision.model}</span>
            </MarkerContent>
          </Marker>
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
  onOfflineToggle,
  offlinePending = false,
}: {
  gw: GatewayState;
  turn: UseTurn;
  /** Flips GLOBAL offline delivery (WF-ADR-0044) — the same handler the header switch uses;
   *  the "/offline" slash command is just another door to it. */
  onOfflineToggle?: (on: boolean) => void;
  offlinePending?: boolean;
}) {
  const offline = showOfflineChip(gw);
  const hasLiveTurn = !!turn.decision || turn.phase === "streaming";

  // Slash commands (WF-DESIGN-0014 amendment): a small, curated set — this stays a
  // routing-inspection surface, not a general command palette.
  const commands: SlashCommand[] = useMemo(() => {
    const list: SlashCommand[] = [
      { name: "clear", description: "Clear this conversation", run: () => turn.reset() },
    ];
    if (onOfflineToggle) {
      list.push({
        name: "offline",
        description: gw.offlineConfig ? "Turn off global offline mode" : "Turn on global offline mode",
        run: () => {
          if (!offlinePending) onOfflineToggle(!gw.offlineConfig);
        },
      });
    }
    list.push({ name: "settings", description: "Open Settings…", run: () => void openSettings() });
    return list;
  }, [turn, onOfflineToggle, offlinePending, gw.offlineConfig]);

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

      {/* message-scroller (WF-DESIGN-0014) drives auto-follow itself — the live turn is the
          scrollAnchor, so it keeps pace as tokens stream in without a manual scrollIntoView
          effect. The scroll-to-bottom button only shows once you've scrolled away from it. */}
      <MessageScrollerProvider autoScroll>
        <MessageScroller className="min-h-0 flex-1">
          <MessageScrollerViewport>
            <MessageScrollerContent className="gap-0 px-0">
              {offline && (
                <div className="px-5 pt-2 text-[11px] text-muted-foreground">
                  offline — routing to the cheapest tier
                </div>
              )}
              {turn.transcript.map((settled, i) => (
                <MessageScrollerItem key={i}>
                  <TranscriptTurn turn={settled} />
                </MessageScrollerItem>
              ))}
              {hasLiveTurn ? (
                <MessageScrollerItem scrollAnchor>
                  {turn.decision ? (
                    <>
                      <PromptLine prompt={turn.prompt} copyable />
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
                    // Headers (and so `turn.decision`) usually land with the first byte of the
                    // reply — this only shows for that brief gap, but it's a real gap: without
                    // it, submitting a second turn in an existing conversation left nothing
                    // rendered below the scrollback until the decision painted.
                    <>
                      <PromptLine prompt={turn.prompt} copyable />
                      <div className="px-5 pt-1">
                        <Marker className="text-[11px]">
                          <MarkerIcon aria-hidden>
                            <span className="block size-1.5 animate-pulse rounded-full bg-muted-foreground" />
                          </MarkerIcon>
                          <MarkerContent>Routing…</MarkerContent>
                        </Marker>
                      </div>
                    </>
                  )}
                </MessageScrollerItem>
              ) : turn.transcript.length === 0 ? (
                <p className="px-5 py-2.5 text-[13px] leading-[1.45] text-muted-foreground">
                  Send a message — Wayfinder routes it and shows the score.
                </p>
              ) : null}
            </MessageScrollerContent>
          </MessageScrollerViewport>
          <MessageScrollerButton />
        </MessageScroller>
      </MessageScrollerProvider>

      <div className="flex flex-col gap-2 border-t border-border bg-background p-5">
        <Composer
          streaming={turn.phase === "streaming"}
          commands={commands}
          onSend={(prompt) => void turn.send(prompt, historyFromTranscript(turn.transcript))}
          onStop={turn.stop}
        />
      </div>
    </div>
  );
}
