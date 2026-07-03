// The webview's entire Rust command surface (WF-ADR-0042 §3: minimal + auditable). Data never
// goes through Rust — the webview fetches the gateway directly; these are only the things a
// webview can't do: drive the tray, control the service, open fixed local targets. Outside a
// Tauri webview (vitest, a plain browser) the display-only calls no-op so the UI still renders.
import { invoke } from "@tauri-apps/api/core";

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export type TrayState = "running" | "degraded" | "stopped";

/** Update the tray from the healthz poll: the W shape carries health, the title the savings $. */
export async function setTrayState(state: TrayState, title: string | null): Promise<void> {
  if (!inTauri()) return;
  try {
    await invoke("set_tray_state", { state, title });
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
