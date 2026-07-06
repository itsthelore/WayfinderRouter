// The webview's entire Rust command surface (WF-ADR-0042 §3: minimal + auditable). Data never
// goes through Rust — the webview fetches the gateway directly; these are only the things a
// webview can't do: drive the tray, control the service, open fixed local targets. Outside a
// Tauri webview (vitest, a plain browser) the display-only calls no-op so the UI still renders.
import { invoke } from "@tauri-apps/api/core";

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export type TrayState = "running" | "degraded" | "stopped";

/** Update the tray from the healthz poll: the W shape carries health, `fill` makes the running
 *  W a live meter (local-routing share, 0–1; quantized upstream), and the title carries the
 *  savings $ only. Health outranks the meter — degraded/stopped ignore `fill`. */
export async function setTrayState(
  state: TrayState,
  title: string | null,
  fill: number | null = null,
): Promise<void> {
  if (!inTauri()) return;
  try {
    await invoke("set_tray_state", { state, title, fill });
  } catch (err) {
    console.warn("set_tray_state failed", err);
  }
}

export type ServiceAction = "install" | "uninstall" | "start" | "stop";

/** Drive the service-first lifecycle; resolves with the Rust message or rejects with its error. */
export async function serviceControl(action: ServiceAction): Promise<string> {
  if (!inTauri()) throw new Error("service control needs the desktop app");
  return invoke<string>("service_control", { action });
}

export type OpenTarget = "dashboard" | "config" | "logs";

export async function openTarget(target: OpenTarget): Promise<void> {
  if (!inTauri()) return;
  try {
    await invoke("open_target", { target });
  } catch (err) {
    console.warn("open_target failed", err);
  }
}

/** A transition-edge notification (off by default; the edge detector gates it). No-ops outside
 *  the desktop app so tests and the plain webview stay silent. */
export async function notify(title: string, body: string): Promise<void> {
  if (!inTauri()) return;
  try {
    await invoke("notify", { title, body });
  } catch (err) {
    console.warn("notify failed", err);
  }
}

/** Launch-at-login for the APP (tauri-plugin-autostart) — the gateway has its own agent
 *  (WF-ADR-0038); see docs/desktop-lifecycle.md. null = unknown (outside the desktop app). */
export async function autostartEnabled(): Promise<boolean | null> {
  if (!inTauri()) return null;
  try {
    const { isEnabled } = await import("@tauri-apps/plugin-autostart");
    return await isEnabled();
  } catch (err) {
    console.warn("autostart isEnabled failed", err);
    return null;
  }
}

export async function setAutostart(on: boolean): Promise<void> {
  if (!inTauri()) return;
  try {
    const plugin = await import("@tauri-apps/plugin-autostart");
    await (on ? plugin.enable() : plugin.disable());
  } catch (err) {
    console.warn("autostart set failed", err);
  }
}
