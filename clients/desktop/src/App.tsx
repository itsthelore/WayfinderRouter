// The app root: routes to the popover or the separate Settings window (WF-DESIGN-0014) by the
// `?window=` query param `commands::open_settings` builds the Settings webview with. Anything
// else (including no param at all, the popover's own URL) is the popover.
import { PopoverRoot } from "@/views/PopoverRoot";
import { SettingsWindow } from "@/views/SettingsWindow";

export function App() {
  const isSettings = new URLSearchParams(window.location.search).get("window") === "settings";
  return isSettings ? <SettingsWindow /> : <PopoverRoot />;
}
