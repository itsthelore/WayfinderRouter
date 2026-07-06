// Settings (glance pivot; cadence presets inspired by CodexBar). A slide-over surface: refresh
// cadence, edge notifications, launch-at-login. Esc returns to the popover (handled here, before
// any global key handling). This view is Phase 4's future home — privacy panel, ⌥W rebind, and
// key management land as rows here.
import { useEffect, useRef, useState } from "react";
import type { Cadence, Settings } from "@/lib/settings";
import { autostartEnabled, setAutostart } from "@/lib/ipc";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";

const CADENCES: Array<{ value: Cadence; label: string }> = [
  { value: "auto", label: "auto" },
  { value: "manual", label: "manual" },
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
];

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="flex flex-col">
        <span className="text-[13px]">{label}</span>
        {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function SettingsView({
  settings,
  onChange,
  onClose,
}: {
  settings: Settings;
  onChange: (next: Settings) => void;
  onClose: () => void;
}) {
  const [autostart, setAutostartState] = useState<boolean | null>(null);
  useEffect(() => {
    void autostartEnabled().then(setAutostartState);
  }, []);
  // A dialog takes focus on open so Esc closes it immediately (and before any global handling).
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-label="settings"
      tabIndex={-1}
      className="flex min-h-0 flex-1 flex-col p-3.5 outline-none"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="flex items-center justify-between pb-1">
        <span className="text-[15px] font-semibold">Settings</span>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="close settings">
          done
        </Button>
      </div>

      <Row label="Refresh cadence" hint="how often the popover polls the gateway">
        <div role="radiogroup" aria-label="refresh cadence" className="flex gap-1">
          {CADENCES.map(({ value, label }) => (
            <button
              key={value}
              role="radio"
              aria-checked={settings.cadence === value}
              onClick={() => onChange({ ...settings, cadence: value })}
              className="rounded-full px-2 py-0.5 text-[11px] font-medium tracking-wide uppercase"
              style={
                settings.cadence === value
                  ? { color: "var(--accent-foreground)", background: "var(--accent)" }
                  : { color: "var(--muted-foreground)" }
              }
            >
              {label}
            </button>
          ))}
        </div>
      </Row>
      <Separator />

      <Row label="Notifications" hint="only on changes — gateway up/down, keys missing/resolved">
        <Switch
          aria-label="edge notifications"
          checked={settings.notifications}
          onCheckedChange={(on) => onChange({ ...settings, notifications: on })}
        />
      </Row>
      <Separator />

      <Row label="Launch at login" hint="the app only — the gateway service has its own agent">
        <Switch
          aria-label="launch at login"
          checked={autostart ?? false}
          disabled={autostart === null}
          onCheckedChange={(on) => {
            setAutostartState(on);
            void setAutostart(on);
          }}
        />
      </Row>
      <Separator />

      <Row label="Shortcut" hint="rebinding lands with onboarding">
        <span className="font-mono text-[13px] text-muted-foreground">⌥W</span>
      </Row>
    </div>
  );
}
