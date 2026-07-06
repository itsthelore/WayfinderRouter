// The popover root (WF-ADR-0042 / WF-DESIGN-0014): owns the two state machines + the hooks, and
// switches the gateway's reachable/unreachable/first-run modes onto their screen. It never
// scores or decides — it wires the gateway's health and the streamed turn into the flat list
// (WF-ADR-0001). The reachable mode has two screens, Usage and Chat, navigated by a push/back
// (no tab-strip — see WF-DESIGN-0014's Context for why that doesn't apply to a single-entity
// popover) rather than WF-DESIGN-0013's segmented control.
import { useCallback, useEffect, useReducer, useState } from "react";
import {
  gatewayReducer,
  gatewayView,
  initialGatewayState,
  type GatewayEvent,
} from "@/lib/appState";
import { useGatewayHealth, readSeenGateway } from "@/hooks/useGatewayHealth";
import { useCheapestModel } from "@/hooks/useCheapestModel";
import { useSavings } from "@/hooks/useSavings";
import { useTurn } from "@/hooks/useTurn";
import { useRecent } from "@/hooks/useRecent";
import { useEdgeNotifier } from "@/hooks/useEdgeNotifier";
import { GATEWAY_BASE } from "@/lib/gateway";
import { quantizeFill } from "@/lib/meter";
import {
  serviceControl,
  scaffoldConfig,
  setOffline,
  setShortcut,
  setTrayState,
  openSettings,
  quitApp,
  type Preset,
  type TrayState,
} from "@/lib/ipc";
import { formatSaved, formatUpdated } from "@/lib/format";
import { MenuHeader, ChatHeader } from "@/components/menu/MenuHeader";
import { FooterMenuItem } from "@/components/menu/FooterMenuItem";
import { Separator } from "@/components/ui/separator";
import { UsageView } from "@/views/UsageView";
import { ChatScreen } from "@/views/ChatScreen";
import { UnreachableView } from "@/views/UnreachableView";
import { FirstRunView } from "@/views/FirstRunView";
import { cadenceToMs, loadSettings } from "@/lib/settings";

export function PopoverRoot({ baseUrl = GATEWAY_BASE }: { baseUrl?: string } = {}) {
  const [seen] = useState(readSeenGateway);
  const [gw, rawDispatch] = useReducer(gatewayReducer, seen, initialGatewayState);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const dispatch = useCallback((event: GatewayEvent) => {
    rawDispatch(event);
    if (event.type === "HEALTHZ_OK") setLastUpdated(Date.now());
  }, []);

  // Persisted preferences: written by the separate Settings window (WF-DESIGN-0014), read here.
  // The `storage` event fires in every OTHER window on a localStorage write, so a cadence or
  // notifications change in Settings takes effect on the popover's very next render.
  const [settings, setSettings] = useState(loadSettings);
  useEffect(() => {
    const onStorage = () => setSettings(loadSettings());
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  const intervalMs = cadenceToMs(settings.cadence);

  // The rebindable popover toggle (WF-DESIGN-0015): the persisted choice is the source of
  // truth — Rust holds no shortcut state — so re-apply it on mount and whenever the Settings
  // window changes it. Failures only log: the default ⌥W from setup stays live.
  useEffect(() => {
    setShortcut(settings.shortcut).catch((e) => console.warn("shortcut rebind failed", e));
  }, [settings.shortcut]);

  const pollHealth = useGatewayHealth(dispatch, { baseUrl, intervalMs });
  useEdgeNotifier(gw, { enabled: settings.notifications });
  const reachable = gw.health === "ok" || gw.health === "degraded";
  const cheapest = useCheapestModel({ baseUrl, enabled: reachable });
  const { report: savings, refresh: refreshSavings } = useSavings({ baseUrl, enabled: reachable, intervalMs });
  // The reference's Cost section pairs a today line with a 30-day line — same here for Saved.
  const { report: savings30d, refresh: refreshSavings30d } = useSavings({
    baseUrl,
    period: "30d",
    enabled: reachable,
    intervalMs,
  });
  const { report: recent, refresh: refreshRecent } = useRecent({ baseUrl, cheapest, enabled: reachable, intervalMs });
  // No per-turn offline header: offline is the gateway's own global mode now — when it is
  // on, the gateway pins delivery local for every client without being asked (WF-ADR-0039).
  const turn = useTurn({ baseUrl, cheapest });

  // Event-driven: when a turn settles, tell the gateway machine whether it was decision-only
  // (drives that mode) and refresh the usage feeds the moment the numbers moved.
  useEffect(() => {
    if (turn.phase === "done" || turn.phase === "error") {
      dispatch({ type: "TURN_DECISION", decisionOnly: !!turn.decision?.decisionOnly });
      void refreshSavings();
      void refreshSavings30d();
      void refreshRecent();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turn.phase]);

  // Tray sync: the W shape follows health, its fill is the local-routing share (the live meter,
  // savings-forward: the $ rides in the title, never a route). Quantized so poll noise never
  // re-renders the icon (WF-DESIGN-0013's meter, kept unchanged per WF-DESIGN-0014's Context).
  const traySaved =
    savings && savings.priced && savings.saved > 0 ? formatSaved(savings.saved) : null;
  const trayFill = quantizeFill(recent?.localShare ?? null);
  useEffect(() => {
    const state: TrayState =
      gw.health === "ok" ? "running" : gw.health === "degraded" ? "degraded" : "stopped";
    void setTrayState(state, traySaved, trayFill);
  }, [gw.health, traySaved, trayFill]);

  // Service-first CTAs (WF-ADR-0042 §4): the app never spawns the gateway — it asks the service
  // to. Errors (e.g. the gateway isn't installed) propagate to the view for display; the next
  // healthz poll flips the mode once the service is up. First-run scaffolds through the config
  // seam (WF-ADR-0044): the gateway's own init writes the config, then install + start.
  const onStartGateway = useCallback(async () => {
    await serviceControl("start");
  }, []);
  const onScaffold = useCallback(async (preset: Preset) => {
    await scaffoldConfig(preset);
  }, []);

  // The header's GLOBAL offline switch: flip through the config seam, then poll healthz so the
  // switch reflects the gateway's own truth rather than an optimistic guess. `pending` disables
  // the switch until the confirming poll lands (hot-reload applies on the gateway's next
  // request, so the immediate poll is also what triggers it).
  const [offlinePending, setOfflinePending] = useState(false);
  const onOfflineToggle = useCallback(
    (on: boolean) => {
      setOfflinePending(true);
      setOffline(on)
        .catch((e) => console.warn("offline toggle failed", e))
        .finally(() => {
          void pollHealth().finally(() => setOfflinePending(false));
        });
    },
    [pollHealth],
  );

  const view = gatewayView(gw);
  const [screen, setScreen] = useState<"usage" | "chat">("usage");

  // The footer's real ⌘-shortcuts (WF-DESIGN-0014: never decorative) — only live while the
  // footer itself is visible, so they can't steal a keystroke out of the chat composer.
  const showFooter = view === "chat" && screen === "usage";
  useEffect(() => {
    if (!showFooter) return;
    function onKeyDown(e: KeyboardEvent) {
      if (!e.metaKey) return;
      if (e.key === "r") {
        e.preventDefault();
        void pollHealth();
        void refreshSavings();
        void refreshSavings30d();
        void refreshRecent();
      } else if (e.key === ",") {
        e.preventDefault();
        void openSettings();
      } else if (e.key === "q") {
        e.preventDefault();
        void quitApp();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showFooter, pollHealth, refreshSavings, refreshSavings30d, refreshRecent]);

  return (
    <div className="flex h-full flex-col">
      {view === "chat" &&
        (screen === "chat" ? (
          <ChatHeader onBack={() => setScreen("usage")} />
        ) : (
          <MenuHeader
            gw={gw}
            updatedText={formatUpdated(lastUpdated, Date.now())}
            onAddKey={() => void openSettings("keys")}
            onOfflineToggle={onOfflineToggle}
            offlinePending={offlinePending}
          />
        ))}
      {view === "chat" && <Separator className="mx-5 w-auto" />}
      <div className="flex min-h-0 flex-1 flex-col">
        {/* Both screens stay mounted — hidden, never unmounted — the same invariant hide-on-blur
            already relies on, so the composer's draft and any streaming turn survive a push/back
            (WF-DESIGN-0014 keeps this WF-DESIGN-0013 behaviour, just for two screens instead of
            two tabs). */}
        {view === "chat" && (
          <div hidden={screen !== "usage"} className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 overflow-y-auto">
              <UsageView
                recent={recent}
                savings={savings}
                savings30d={savings30d}
                cheapest={cheapest}
                onOpenChat={() => setScreen("chat")}
              />
            </div>
            <Separator className="mx-5 w-auto" />
            <div className="py-1">
              <FooterMenuItem
                label="Refresh"
                shortcut="⌘R"
                onClick={() => {
                  void pollHealth();
                  void refreshSavings();
                  void refreshSavings30d();
                  void refreshRecent();
                }}
              />
              <FooterMenuItem label="Settings…" shortcut="⌘," onClick={() => void openSettings()} />
              <FooterMenuItem label="Quit Wayfinder" shortcut="⌘Q" onClick={() => void quitApp()} />
            </div>
          </div>
        )}
        {view === "chat" && (
          <div hidden={screen !== "chat"} className="flex min-h-0 flex-1 flex-col">
            <ChatScreen gw={gw} turn={turn} />
          </div>
        )}
        {view === "unreachable" && <UnreachableView onStartGateway={onStartGateway} />}
        {view === "first-run" && <FirstRunView onScaffold={onScaffold} />}
      </div>
    </div>
  );
}
