// Settings (WF-DESIGN-0014 / WF-DESIGN-0015): a separate, resizable, decorated native window —
// never an in-popover slide-over (that was WF-DESIGN-0013's `SettingsView`, retired). Layout
// mirrors CodexBar's ClawRouter settings pane (clawrouter-settings.png): a sidebar list on the
// left, a detail pane on the right of Mac-native Form rows (bold label + gray description on
// the left, the control flush right). Four sidebar entries: General (the app's own
// preferences, incl. the rebindable popover shortcut), Gateway (endpoint + the door to the
// router's config file — the app opens it, never edits it, WF-ADR-0042/0044), Keys (provider
// keys to the macOS Keychain — the key crosses to Rust once and never persists in JS state,
// WF-ADR-0004), and Privacy (the verify-lite panel, WF-ADR-0042 §8 — honest claims only). The
// popover deep-links a section via `?section=` (open_settings command, applied on window
// creation only). Still no provider search box — ClawRouter's searches its own provider list;
// Wayfinder's models come straight from /router/models.
import { useCallback, useEffect, useState } from "react";
import type { Cadence, Settings, ShortcutId } from "@/lib/settings";
import { loadSettings, saveSettings, SHORTCUT_LABELS } from "@/lib/settings";
import {
  autostartEnabled,
  deleteProviderKey,
  openTarget,
  setAutostart,
  setShortcut,
  storeProviderKey,
} from "@/lib/ipc";
import { fetchModels, type GatewayModelInfo } from "@/lib/models";
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
  { id: "keys", label: "Keys" },
  { id: "privacy", label: "Privacy" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

function initialSection(): SectionId {
  const s = new URLSearchParams(window.location.search).get("section");
  return SECTIONS.some((x) => x.id === s) ? (s as SectionId) : "general";
}

function FormRow({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children?: React.ReactNode;
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
  const [shortcutError, setShortcutError] = useState<string | null>(null);
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

      <FormRow
        label="Toggle popover"
        description="Global shortcut to show or hide Wayfinder from anywhere. Rolled back if the combo can't register (already claimed by another app)."
      >
        <div className="flex flex-col items-end gap-1">
          <select
            aria-label="popover shortcut"
            value={settings.shortcut}
            onChange={(e) => {
              const previous = settings.shortcut;
              const next = e.target.value as ShortcutId;
              setShortcutError(null);
              onChange({ ...settings, shortcut: next });
              setShortcut(next).catch((err) => {
                onChange({ ...settings, shortcut: previous }); // roll back UI + storage
                setShortcutError(err instanceof Error ? err.message : String(err));
              });
            }}
            className="h-8 rounded-md border border-input bg-background px-2 font-mono text-[13px]"
          >
            {(Object.keys(SHORTCUT_LABELS) as ShortcutId[]).map((id) => (
              <option key={id} value={id}>
                {SHORTCUT_LABELS[id]}
              </option>
            ))}
          </select>
          {shortcutError && (
            <span className="text-[11px]" style={{ color: "var(--destructive)" }}>
              {shortcutError}
            </span>
          )}
        </div>
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

function KeyRow({ model, onDone }: { model: GatewayModelInfo; onDone: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const envVar = model.api_key_env!;

  async function act(fn: () => Promise<string>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setValue(""); // the key never lingers in JS state (WF-ADR-0004)
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <FormRow
        label={model.name}
        description={`${model.model} — reads $${envVar}. ${model.key_ok ? "Key present." : "Key missing."}`}
      >
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-2">
            <input
              type="password"
              aria-label={`${envVar} key`}
              value={value}
              placeholder={model.key_ok ? "replace key…" : "paste key…"}
              onChange={(e) => setValue(e.target.value)}
              className="h-8 w-52 rounded-md border border-input bg-background px-2 font-mono text-[13px]"
            />
            <Button
              size="sm"
              disabled={busy || !value.trim()}
              onClick={() => void act(() => storeProviderKey(envVar, value))}
            >
              {busy ? "Saving…" : "Save"}
            </Button>
            {model.key_ok && (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => void act(() => deleteProviderKey(envVar))}
              >
                Remove
              </Button>
            )}
          </div>
          {error && (
            <span className="text-[11px]" style={{ color: "var(--destructive)" }}>
              {error}
            </span>
          )}
        </div>
      </FormRow>
      <Separator />
    </>
  );
}

function KeysSection() {
  const [models, setModels] = useState<GatewayModelInfo[] | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setModels(await fetchModels());
      setUnreachable(false);
    } catch {
      setUnreachable(true);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Saving a key restarts the gateway (resolve_keys runs at startup only), so key_ok flips a
  // beat later — bounded retries rather than one hopeful refetch.
  const refreshUntilSettled = useCallback(() => {
    let attempts = 0;
    const tick = () => {
      attempts += 1;
      void refresh();
      if (attempts < 5) setTimeout(tick, 1200);
    };
    tick();
  }, [refresh]);

  const keyed = (models ?? []).filter((m) => m.api_key_env);

  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">Keys</h2>
      {unreachable ? (
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          The gateway isn’t reachable — start it from the popover to manage keys.
        </p>
      ) : models === null ? (
        <p className="text-[13px] text-muted-foreground">Loading…</p>
      ) : keyed.length === 0 ? (
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          Every configured model is keyless — nothing to manage.
        </p>
      ) : (
        <>
          {keyed.map((m) => (
            <KeyRow key={m.name} model={m} onDone={refreshUntilSettled} />
          ))}
          <p className="pt-2 text-[11px] leading-[1.45] text-muted-foreground">
            Keys are stored in the macOS Keychain (service “wayfinder-router”), never in a file
            or in this app. The gateway reads them through the <span className="font-mono">api_key_cmd</span>{" "}
            reference that scaffolded configs contain — a hand-written config without that line
            won’t see them (add it, or use Gateway → Open in Finder). Saving restarts the
            gateway so the key takes effect.
          </p>
        </>
      )}
    </div>
  );
}

function PrivacySection() {
  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">Privacy</h2>
      <FormRow
        label="The routing decision is computed on your machine"
        description="Scoring is deterministic, offline, and keyless — no model call, no network, no credential is involved in deciding where a prompt goes (WF-ADR-0001)."
      />
      <Separator />
      <FormRow
        label="Prompts go only to the provider you route to"
        description="Under your own keys, from the local gateway. This app holds no keys and sends nothing anywhere itself — it renders what the gateway did."
      />
      <Separator />
      <FormRow
        label="Offline mode is the only nothing-leaves guarantee"
        description="One click in the popover: the gateway serves the cheapest/local tier and never calls a cloud tier. Outside offline mode, routed prompts do reach the provider you configured."
      />
      <Separator />
      <FormRow label="No telemetry" description="Ever. The app talks to 127.0.0.1:8088 and nothing else." />
    </div>
  );
}

export function SettingsWindow() {
  const [settings, setSettingsState] = useState(loadSettings);
  const [section, setSection] = useState<SectionId>(initialSection);

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
        {section === "keys" && <KeysSection />}
        {section === "privacy" && <PrivacySection />}
      </main>
    </div>
  );
}

