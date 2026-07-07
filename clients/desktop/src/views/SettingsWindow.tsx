// Settings (WF-DESIGN-0014 / WF-DESIGN-0015): a separate, resizable, decorated native window —
// never an in-popover slide-over (that was WF-DESIGN-0013's `SettingsView`, retired). The nav is
// a horizontal icon-tab strip across the top (the maintainer's product mockup), five sections:
// General (the app's own preferences, incl. the rebindable popover shortcut), Providers (the
// per-model master-detail pane — status, routing threshold, fallback, enable, and the Keychain
// key flow, WF-ADR-0044/0004), Display (menu-bar + appearance), Advanced (the gateway endpoint +
// the door to the router's config file — the app opens it, never edits it, WF-ADR-0042/0044),
// and About (the wordmark, version, and the verify-lite privacy claims, WF-ADR-0042 §8 — honest
// claims only). The popover deep-links a section via `?section=` (open_settings command, applied
// on window creation only); legacy ids (keys/gateway/privacy) remap to their new homes.
import { useCallback, useEffect, useState } from "react";
import type { Cadence, Settings, ShortcutId } from "@/lib/settings";
import { loadSettings, saveSettings, SHORTCUT_LABELS } from "@/lib/settings";
import {
  addModel,
  autostartEnabled,
  deleteProviderKey,
  detectLocalProviders,
  openTarget,
  setAutostart,
  setModelEnabled,
  setModelFallback,
  setShortcut,
  setTierThreshold,
  storeProviderKey,
  type DetectedProvider,
} from "@/lib/ipc";
import { fetchModels, type GatewayModelInfo, type ModelsFeed, type TierEntry } from "@/lib/models";
import { useSavings } from "@/hooks/useSavings";
import type { SavingsReport } from "@/lib/format";
import { Bar } from "@/components/menu/Bar";
import { GATEWAY_BASE } from "@/lib/gateway";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import { Info, Monitor, Server, SlidersHorizontal, Wrench } from "lucide-react";
import wordmark from "@/assets/wayfinder-wordmark.png";

const CADENCES: Array<{ value: Cadence; label: string }> = [
  { value: "auto", label: "Automatic (15s)" },
  { value: "manual", label: "Manual" },
  { value: "1m", label: "Every minute" },
  { value: "5m", label: "Every 5 minutes" },
  { value: "15m", label: "Every 15 minutes" },
];

const SECTIONS = [
  { id: "general", label: "General", icon: SlidersHorizontal },
  { id: "providers", label: "Providers", icon: Server },
  { id: "display", label: "Display", icon: Monitor },
  { id: "advanced", label: "Advanced", icon: Wrench },
  { id: "about", label: "About", icon: Info },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

// Legacy `?section=` ids from before the mockup restructure still deep-link cleanly: the popover
// pointed at keys/gateway/privacy, which now live inside providers/advanced/about.
const LEGACY_SECTIONS: Record<string, SectionId> = {
  keys: "providers",
  gateway: "advanced",
  privacy: "about",
};

function initialSection(): SectionId {
  const s = new URLSearchParams(window.location.search).get("section") ?? "";
  if (SECTIONS.some((x) => x.id === s)) return s as SectionId;
  return LEGACY_SECTIONS[s] ?? "general";
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
    <Item className="items-start gap-6 px-0 py-3">
      <ItemContent className="max-w-[60%] gap-0.5">
        <ItemTitle className="text-[13px] font-medium">{label}</ItemTitle>
        <ItemDescription className="text-[11px] leading-[1.45]">{description}</ItemDescription>
      </ItemContent>
      {children && <ItemActions className="pt-0.5">{children}</ItemActions>}
    </Item>
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
        <NativeSelect
          size="sm"
          aria-label="refresh cadence"
          value={settings.cadence}
          onChange={(e) => onChange({ ...settings, cadence: e.target.value as Cadence })}
        >
          {CADENCES.map(({ value, label }) => (
            <NativeSelectOption key={value} value={value}>
              {label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
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
          <NativeSelect
            size="sm"
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
            className="font-mono"
          >
            {(Object.keys(SHORTCUT_LABELS) as ShortcutId[]).map((id) => (
              <NativeSelectOption key={id} value={id}>
                {SHORTCUT_LABELS[id]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
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

function AdvancedSection() {
  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">Advanced</h2>

      <FormRow
        label="Gateway endpoint"
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
      <Separator />

      <FormRow
        label="Dashboard"
        description="The gateway's own web dashboard — routing history, costs, and the live threshold."
      >
        <Button size="sm" variant="secondary" onClick={() => void openTarget("dashboard")}>
          Open in Browser
        </Button>
      </FormRow>
      <Separator />

      <FormRow label="Logs" description="The gateway service's log files, for when something misbehaves.">
        <Button size="sm" variant="secondary" onClick={() => void openTarget("logs")}>
          Show in Finder
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

type ProviderPreset = {
  id: string;
  label: string;
  baseUrl: string;
  apiKeyEnv?: string;
};

// Open-ended on purpose (WF-ADR-0044 amendment): "provider" means any OpenAI-compatible
// endpoint, not a fixed enum — these are just the common quick-picks. Custom covers everything
// else, including things like a HuggingFace-hosted inference endpoint.
const PROVIDER_PRESETS: ProviderPreset[] = [
  { id: "anthropic", label: "Anthropic", baseUrl: "https://api.anthropic.com/v1", apiKeyEnv: "ANTHROPIC_API_KEY" },
  { id: "openai", label: "OpenAI", baseUrl: "https://api.openai.com/v1", apiKeyEnv: "OPENAI_API_KEY" },
  {
    id: "gemini",
    label: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    apiKeyEnv: "GEMINI_API_KEY",
  },
  { id: "ollama", label: "Ollama", baseUrl: "http://127.0.0.1:11434/v1" },
  { id: "lmstudio", label: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1" },
];

const NAME_RE = /^[a-z][a-z0-9_-]{0,63}$/;

function AddProviderForm({ onAdded, onCancel }: { onAdded: () => void; onCancel: () => void }) {
  const [detected, setDetected] = useState<DetectedProvider[]>([]);
  const [presetId, setPresetId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void detectLocalProviders().then(setDetected);
  }, []);

  function pick(preset: ProviderPreset) {
    setPresetId(preset.id);
    setName(preset.id);
    setBaseUrl(preset.baseUrl);
    setApiKeyEnv(preset.apiKeyEnv ?? "");
    setModel("");
    setError(null);
  }

  function pickCustom() {
    setPresetId("custom");
    setName("");
    setBaseUrl("");
    setModel("");
    setApiKeyEnv("");
    setError(null);
  }

  async function submit() {
    if (!NAME_RE.test(name)) {
      setError("name must be lowercase letters, numbers, - or _, starting with a letter");
      return;
    }
    if (!baseUrl.trim() || !model.trim()) {
      setError("base URL and model are both required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addModel(name, baseUrl.trim(), model.trim(), apiKeyEnv.trim() || undefined);
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-input bg-muted/40 p-3">
      <div className="flex flex-wrap gap-1.5">
        {PROVIDER_PRESETS.map((preset) => (
          <Button
            key={preset.id}
            type="button"
            size="sm"
            variant={presetId === preset.id ? "default" : "secondary"}
            onClick={() => pick(preset)}
          >
            {preset.label}
            {detected.some((d) => d.id === preset.id) && " •"}
          </Button>
        ))}
        <Button
          type="button"
          size="sm"
          variant={presetId === "custom" ? "default" : "secondary"}
          onClick={pickCustom}
        >
          Custom
        </Button>
      </div>
      {detected.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          • detected running on this Mac
        </p>
      )}

      {presetId && (
        <>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            Name (unique — lets you add the same provider more than once)
            <input
              aria-label="provider name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="h-8 rounded-md border border-input bg-background px-2 font-mono text-[13px] text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            Base URL
            <input
              aria-label="base URL"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="h-8 rounded-md border border-input bg-background px-2 font-mono text-[13px] text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            Model
            <input
              aria-label="model id"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="e.g. claude-opus-4-8"
              className="h-8 rounded-md border border-input bg-background px-2 font-mono text-[13px] text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
            API key environment variable (leave blank if keyless)
            <input
              aria-label="API key env var"
              value={apiKeyEnv}
              onChange={(e) => setApiKeyEnv(e.target.value.toUpperCase())}
              placeholder="e.g. ANTHROPIC_API_KEY"
              className="h-8 rounded-md border border-input bg-background px-2 font-mono text-[13px] text-foreground"
            />
          </label>

          {error && (
            <span className="text-[11px]" style={{ color: "var(--destructive)" }}>
              {error}
            </span>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
            <Button type="button" size="sm" disabled={busy} onClick={() => void submit()}>
              {busy ? "Adding…" : "Add"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

type TierPlacement = { inLadder: boolean; isBase: boolean; minScore: number };

/** Where a model sits in the scored ladder (WF-ADR-0002), for the read-only eligibility line and
 *  to decide whether a threshold control makes sense (the base tier's 0.0 boundary is structural
 *  — the gateway always rejects moving it, so no slider is offered for it). */
function tierPlacement(name: string, tiers: TierEntry[]): TierPlacement {
  const sorted = [...tiers].sort((a, b) => a.min_score - b.min_score);
  const idx = sorted.findIndex((t) => t.model === name);
  if (idx < 0) return { inLadder: false, isBase: false, minScore: 0 };
  return { inLadder: true, isBase: idx === 0, minScore: sorted[idx].min_score };
}

function eligibilityLabel(p: TierPlacement): string {
  if (!p.inLadder) return "Not in the routing ladder";
  if (p.isBase) return "Base tier";
  return `Score ≥ ${p.minScore.toFixed(2)}`;
}

function eligibilityDescription(p: TierPlacement): string {
  if (!p.inLadder) {
    return "Reachable by direct name or as another model's fallback — it receives no automatically-scored traffic until a human places it in a tier (WF-ADR-0002).";
  }
  if (p.isBase) {
    return "The lowest-complexity prompts route here; its upper boundary is the next tier's threshold.";
  }
  return "Prompts scoring at or above this boundary escalate to this tier.";
}

/** Share of the last 7 days' routed prompts that went to `name` (from /v1/savings by_route). */
function routedPercent(name: string, savings: SavingsReport | null): number | null {
  if (!savings || !savings.by_route || savings.requests <= 0) return null;
  const r = savings.by_route[name]?.requests ?? 0;
  return Math.round((r / savings.requests) * 100);
}

function routedCounts(name: string, savings: SavingsReport | null): { requests: number; total: number } | null {
  if (!savings || !savings.by_route || savings.requests <= 0) return null;
  return { requests: savings.by_route[name]?.requests ?? 0, total: savings.requests };
}

/** The list dot: teal healthy, amber key-missing, gray disabled — the same route-accent grammar
 *  the popover uses (WF-ADR-0020). */
function statusColor(m: GatewayModelInfo): string {
  if (!m.enabled) return "var(--muted-foreground)";
  if (m.api_key_env && !m.key_ok) return "var(--route-cloud)";
  return "var(--primary)";
}

function ProviderDetail({
  model,
  tiers,
  allModels,
  savings,
  onChanged,
  onKeyDone,
}: {
  model: GatewayModelInfo;
  tiers: TierEntry[];
  allModels: GatewayModelInfo[];
  savings: SavingsReport | null;
  /** Delivery/threshold edits hot-reload — a single refetch confirms them. */
  onChanged: () => void;
  /** Key save/remove rides a gateway restart — bounded retries until key_ok settles. */
  onKeyDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const placement = tierPlacement(model.name, tiers);
  const currentFallback = model.fallbacks[0] ?? null;
  // Local slider value for smooth dragging; the write only fires on release (onPointerUp).
  const [threshold, setThreshold] = useState(placement.minScore);
  useEffect(() => setThreshold(placement.minScore), [placement.minScore]);

  async function act(fn: () => Promise<string>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const status = !model.enabled ? "Disabled" : model.key_ok ? "Healthy" : "Key missing";
  const counts = routedCounts(model.name, savings);

  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between pb-1">
        <h3 className="text-[15px] font-semibold">{model.name}</h3>
        <Switch
          aria-label={`${model.name} enabled`}
          checked={model.enabled}
          disabled={busy}
          onCheckedChange={(on) => void act(() => setModelEnabled(model.name, on))}
        />
      </div>

      <FormRow label="Status" description="Healthy means enabled and, if it needs a key, the key is present.">
        <span className="text-[13px] text-muted-foreground">{status}</span>
      </FormRow>
      <Separator />
      <FormRow label="Endpoint" description="The OpenAI-compatible base URL this model's requests go to.">
        <span className="font-mono text-[13px] text-muted-foreground">{model.endpoint}</span>
      </FormRow>
      <Separator />
      <FormRow label="Model" description="The upstream model id forwarded in each request.">
        <span className="font-mono text-[13px] text-muted-foreground">{model.model}</span>
      </FormRow>
      {model.context_window != null && (
        <>
          <Separator />
          <FormRow label="Context window" description="Prompts estimated to exceed this skip this endpoint (WF-ADR-0031).">
            <span className="text-[13px] text-muted-foreground">
              {model.context_window.toLocaleString()} tokens
            </span>
          </FormRow>
        </>
      )}
      <Separator />
      <FormRow label="Route eligibility" description={eligibilityDescription(placement)}>
        <span className="text-[13px] text-muted-foreground">{eligibilityLabel(placement)}</span>
      </FormRow>

      {placement.inLadder && !placement.isBase && (
        <>
          <Separator />
          <FormRow
            label="Routing threshold"
            description={`Prompts scoring ≥ ${threshold.toFixed(2)} escalate to ${model.name}.`}
          >
            <div className="flex items-center gap-2">
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={threshold}
                disabled={busy}
                aria-label={`${model.name} routing threshold`}
                onChange={(e) => setThreshold(Number(e.target.value))}
                onPointerUp={() => {
                  if (threshold !== placement.minScore) void act(() => setTierThreshold(model.name, threshold));
                }}
                className="w-40 accent-[var(--primary)]"
              />
              <span className="w-10 text-right font-mono text-[13px] tabular-nums">{threshold.toFixed(2)}</span>
            </div>
          </FormRow>
        </>
      )}

      <Separator />
      <FormRow label="Fallback" description="If this endpoint fails, delivery retries the model you pick here (same-tier, WF-ADR-0031).">
        <NativeSelect
          size="sm"
          aria-label={`${model.name} fallback`}
          value={currentFallback ?? ""}
          disabled={busy}
          onChange={(e) => {
            const v = e.target.value;
            void act(() => setModelFallback(model.name, v || null));
          }}
        >
          <NativeSelectOption value="">None</NativeSelectOption>
          {allModels
            .filter((m) => m.name !== model.name)
            .map((m) => (
              <NativeSelectOption key={m.name} value={m.name}>
                {m.name}
              </NativeSelectOption>
            ))}
        </NativeSelect>
      </FormRow>

      <Separator />
      <FormRow
        label="Usage (last 7 days)"
        description={counts ? `${counts.requests} of ${counts.total} routed prompts` : "No routed traffic yet."}
      >
        <div className="w-40">
          <Bar
            fraction={(routedPercent(model.name, savings) ?? 0) / 100}
            label={`${model.name} share of routed prompts`}
          />
        </div>
      </FormRow>

      {model.api_key_env && (
        <>
          <Separator />
          <KeyRow model={model} onDone={onKeyDone} />
          <p className="text-[11px] leading-[1.45] text-muted-foreground">
            Keys live in the macOS Keychain (service “wayfinder-router”), never in a file or in this
            app. Saving restarts the gateway so the key takes effect.
          </p>
        </>
      )}

      {error && (
        <span className="pt-2 text-[11px]" style={{ color: "var(--destructive)" }}>
          {error}
        </span>
      )}
    </div>
  );
}

function ProvidersSection() {
  const [feed, setFeed] = useState<ModelsFeed | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const { report: savings } = useSavings({ period: "7d" });

  const refresh = useCallback(async () => {
    try {
      const f = await fetchModels();
      setFeed(f);
      setUnreachable(false);
      // Keep the current selection if it still exists, else fall to the first model.
      setSelected((cur) => (cur && f.models.some((m) => m.name === cur) ? cur : (f.models[0]?.name ?? null)));
    } catch {
      setUnreachable(true);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A key save/remove rides a gateway restart (resolve_keys runs at startup only), so key_ok
  // flips a beat later — bounded retries rather than one hopeful refetch.
  const refreshUntilSettled = useCallback(() => {
    let attempts = 0;
    const tick = () => {
      attempts += 1;
      void refresh();
      if (attempts < 5) setTimeout(tick, 1200);
    };
    tick();
  }, [refresh]);

  const models = feed?.models ?? [];
  const tiers = feed?.tiers ?? [];
  const selectedModel = models.find((m) => m.name === selected) ?? null;

  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between pb-3">
        <h2 className="text-[15px] font-semibold">Providers</h2>
        {!adding && (
          <Button size="sm" variant="secondary" onClick={() => setAdding(true)}>
            + Add Provider or Model
          </Button>
        )}
      </div>
      {adding && (
        <div className="pb-3">
          <AddProviderForm
            onAdded={() => {
              setAdding(false);
              refreshUntilSettled();
            }}
            onCancel={() => setAdding(false)}
          />
        </div>
      )}
      {unreachable ? (
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          The gateway isn’t reachable — start it from the popover to manage providers.
        </p>
      ) : feed === null ? (
        <p className="text-[13px] text-muted-foreground">Loading…</p>
      ) : models.length === 0 ? (
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          No models configured yet — add one above.
        </p>
      ) : (
        <div className="flex min-h-0 gap-4">
          <ul aria-label="configured providers" className="flex w-[38%] shrink-0 flex-col gap-0.5">
            {models.map((m) => {
              const pct = routedPercent(m.name, savings);
              return (
                <li key={m.name}>
                  <button
                    type="button"
                    aria-label={m.name}
                    aria-current={selected === m.name}
                    data-current={selected === m.name}
                    onClick={() => setSelected(m.name)}
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-accent/50 data-[current=true]:bg-accent"
                  >
                    <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: statusColor(m) }} />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-[13px] font-medium">{m.name}</span>
                      <span className="truncate text-[11px] text-muted-foreground">
                        {pct == null ? "no traffic yet" : `${pct}% routed`}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="min-w-0 flex-1 border-l border-border pl-4">
            {selectedModel && (
              <ProviderDetail
                model={selectedModel}
                tiers={tiers}
                allModels={models}
                savings={savings}
                onChanged={() => void refresh()}
                onKeyDone={refreshUntilSettled}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function DisplaySection({ settings, onChange }: { settings: Settings; onChange: (next: Settings) => void }) {
  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">Display</h2>

      <FormRow
        label="Show savings in the menu bar"
        description="Display the estimated $ saved beside the tray icon. Off leaves only the routing health and the local-share meter."
      >
        <Switch
          aria-label="show savings in menu bar"
          checked={settings.trayShowSavings}
          onCheckedChange={(on) => onChange({ ...settings, trayShowSavings: on })}
        />
      </FormRow>
      <Separator />

      <FormRow
        label="Appearance follows the system"
        description="Wayfinder matches your macOS light or dark setting automatically — there is no in-app theme toggle (WF-DESIGN-0012)."
      />
    </div>
  );
}

// The verify-lite privacy claims (WF-ADR-0042 §8: honest claims only) live here, alongside the
// wordmark and app version — the About panel WF-DESIGN-0014 deferred. Claim copy is unchanged;
// the banned overclaim ("your data never leaves your machine") is still asserted absent in tests.
function AboutSection() {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return;
    void import("@tauri-apps/api/app")
      .then((m) => m.getVersion())
      .then(setVersion)
      .catch(() => {});
  }, []);

  return (
    <div className="flex flex-col">
      <h2 className="pb-2 text-[15px] font-semibold">About</h2>
      <div className="flex flex-col items-start gap-1 pb-3">
        <img src={wordmark} alt="Wayfinder" className="h-6 w-auto" />
        <p className="text-[12px] text-muted-foreground">
          Deterministic prompt router{version ? ` · v${version}` : ""}
        </p>
      </div>
      <Separator />

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
    <div className="flex h-full flex-col">
      <nav
        aria-label="settings sections"
        className="flex shrink-0 items-stretch justify-center gap-1 border-b border-border bg-muted px-3 py-2"
      >
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-current={section === s.id}
            data-current={section === s.id}
            onClick={() => setSection(s.id)}
            className="flex w-[72px] flex-col items-center gap-1 rounded-md px-2 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors duration-[var(--dur-fast)] hover:text-foreground data-[current=true]:bg-accent data-[current=true]:text-accent-foreground"
          >
            <s.icon className="size-[18px]" aria-hidden />
            {s.label}
          </button>
        ))}
      </nav>
      <main className="min-w-0 flex-1 overflow-y-auto p-6">
        {section === "general" && <GeneralSection settings={settings} onChange={onChange} />}
        {section === "providers" && <ProvidersSection />}
        {section === "display" && <DisplaySection settings={settings} onChange={onChange} />}
        {section === "advanced" && <AdvancedSection />}
        {section === "about" && <AboutSection />}
      </main>
    </div>
  );
}

