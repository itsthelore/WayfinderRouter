// The popover's persisted preferences (glance pivot; refresh-cadence presets inspired by
// CodexBar). Stored as one JSON blob in localStorage — the same persistence the seen-gateway
// flag uses; no new deps. Launch-at-login is NOT here: the autostart plugin is its source of
// truth (lib/ipc.ts wraps it).

export type Cadence = "auto" | "manual" | "1m" | "5m" | "15m";

export interface Settings {
  /** Poll cadence for healthz/savings/recent. "auto" = the checkpointed 15s default;
   *  "manual" = no background interval (initial fetch + focus/turn refreshes only). */
  cadence: Cadence;
  /** Transition-edge notifications (WF-DESIGN-0012: edge-only) — off by default. */
  notifications: boolean;
}

export const SETTINGS_KEY = "wf.settings.v1";

export const DEFAULT_SETTINGS: Settings = {
  cadence: "auto",
  notifications: false,
};

const CADENCE_MS: Record<Cadence, number | null> = {
  auto: 15_000,
  manual: null,
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
};

/** The interval a cadence preset drives; null = no background interval. */
export function cadenceToMs(cadence: Cadence): number | null {
  return CADENCE_MS[cadence];
}

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      cadence: parsed.cadence && parsed.cadence in CADENCE_MS ? parsed.cadence : DEFAULT_SETTINGS.cadence,
      notifications: typeof parsed.notifications === "boolean" ? parsed.notifications : DEFAULT_SETTINGS.notifications,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: Settings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // private-mode storage failure: settings just reset next launch
  }
}
