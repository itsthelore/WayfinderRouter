// Settings (WF-DESIGN-0014): a separate, resizable, decorated native window — never an
// in-popover slide-over (that was WF-DESIGN-0013's `SettingsView`, retired). Layout mirrors
// CodexBar's ClawRouter settings pane (clawrouter-settings.png): a sidebar list on the left, a
// detail pane on the right of Mac-native Form rows (bold label + gray description on the left,
// the control flush right). Two sidebar entries: General (the app's own preferences) and
// Gateway (the router's side — endpoint, and the door to its config file, which the app opens
// but never edits, WF-ADR-0042/0004). "Config" and "Settings" as sibling popover entries read
// as synonyms (maintainer review), so Settings is the single entry point and the distinction
// is made explicit here instead. No provider search box and no API key/Base URL rows —
// ClawRouter's search its own provider list, and Wayfinder's key handling is still Phase 4;
// both are recorded in WF-DESIGN-0014's Later section rather than faked here.
import { useEffect, useState } from "react";
import type { Cadence, Settings } from "@/lib/settings";
import { loadSettings, saveSettings } from "@/lib/settings";
import { autostartEnabled, openTarget, setAutostart } from "@/lib/ipc";
import { GATEWAY_BASE } from "@/lib/gateway";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";

const CADENCES: Array<{ value: Cadence; label: string }> = [
  { value: "auto", label: "Automatic (15s)" },
  { value: "manual", label: "Manual" },
  { value: "1m", label: "Every minute" },
  { value: "5m", label: "Every 5 minutes" },
  { value: "15m", label: "Every 15 minutes" },
];

const SECTIONS = [
  { id: "general", label: "General" },
  { id: "gateway", label: "Gateway" },
] as const;

function FormRow({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-6 py-3">
      <div className="flex max-w-[60%] flex-col gap-0.5">
        <span className="text-[13px] font-medium">{label}</span>
        <span className="text-[11px] leading-[1.45] text-muted-foreground">{description}</span>
      </div>
      <div className="pt-0.5">{children}</div>
    </div>
  );
}

function GeneralSection({ settings, onChange }: { settings: Settings; onChange: (next: Settings) => void }) {
  const [autostart, setAutostartState] = useState<boolean | null>(null);
  useEffect(() => {
    void autostartEnabled().then(setAutostartState);
  }, []);

  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">General</h2>

      <FormRow label="Refresh cadence" description="How often the popover polls the gateway for health, savings, and routing.">
        <select
          aria-label="refresh cadence"
          value={settings.cadence}
          onChange={(e) => onChange({ ...settings, cadence: e.target.value as Cadence })}
          className="h-8 rounded-md border border-input bg-background px-2 text-[13px]"
        >
          {CADENCES.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </FormRow>
      <Separator />

      <FormRow label="Notifications" description="Notify on gateway transitions — up/down, degraded, keys missing or resolved.">
        <Switch
          aria-label="edge notifications"
          checked={settings.notifications}
          onCheckedChange={(on) => onChange({ ...settings, notifications: on })}
        />
      </FormRow>
      <Separator />

      <FormRow label="Launch at login" description="Starts Wayfinder in the background when you log in. The gateway has its own separate agent.">
        <Switch
          aria-label="launch at login"
          checked={autostart ?? false}
          disabled={autostart === null}
          onCheckedChange={(on) => {
            setAutostartState(on);
            void setAutostart(on);
          }}
        />
      </FormRow>
      <Separator />

      <FormRow label="Toggle popover" description="Global shortcut to show or hide Wayfinder from anywhere. Rebinding lands with onboarding.">
        <span className="font-mono text-[13px] text-muted-foreground">⌥W</span>
      </FormRow>
    </div>
  );
}

function GatewaySection() {
  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">Gateway</h2>

      <FormRow
        label="Endpoint"
        description="The local gateway this app renders from. Loopback only — the popover never talks to anything else."
      >
        <span className="font-mono text-[13px] text-muted-foreground">
          {GATEWAY_BASE.replace(/^https?:\/\//, "")}
        </span>
      </FormRow>
      <Separator />

      <FormRow
        label="Configuration file"
        description="Routing tiers, models, keys, and budgets live in the gateway's own config file. The app opens it for you but never edits it — the gateway hot-reloads your changes."
      >
        <Button size="sm" variant="secondary" onClick={() => void openTarget("config")}>
          Open in Finder
        </Button>
      </FormRow>
    </div>
  );
}

export function SettingsWindow() {
  const [settings, setSettingsState] = useState(loadSettings);
  const [section, setSection] = useState<(typeof SECTIONS)[number]["id"]>("general");

  function onChange(next: Settings) {
    setSettingsState(next);
    saveSettings(next);
  }

  return (
    <div className="flex h-full">
      <nav aria-label="settings sections" className="flex w-[180px] shrink-0 flex-col gap-0.5 border-r border-border bg-muted p-2">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-current={section === s.id}
            onClick={() => setSection(s.id)}
            className="rounded-md px-2.5 py-1.5 text-left text-[13px] data-[current=true]:bg-accent"
            data-current={section === s.id}
          >
            {s.label}
          </button>
        ))}
      </nav>
      <main className="min-w-0 flex-1 overflow-y-auto p-6">
        {section === "general" && <GeneralSection settings={settings} onChange={onChange} />}
        {section === "gateway" && <GatewaySection />}
      </main>
    </div>
  );
}
