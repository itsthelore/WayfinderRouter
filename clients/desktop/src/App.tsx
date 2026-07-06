// The app root: routes to the popover or the separate Settings window (WF-DESIGN-0014) by the
// `?window=` query param `commands::open_settings` builds the Settings webview with. Anything
// else (including no param at all, the popover's own URL) is the popover. The body carries a
// data-window attribute so globals.css can give Settings an opaque background — only the
// popover rides the transparent-body-over-vibrancy treatment; a decorated window over a
// transparent body would render dark-mode text on the webview's default white.
import { PopoverRoot } from "@/views/PopoverRoot";
import { SettingsWindow } from "@/views/SettingsWindow";

export function App() {
  const isSettings = new URLSearchParams(window.location.search).get("window") === "settings";
  document.body.dataset.window = isSettings ? "settings" : "popover";
  return isSettings ? <SettingsWindow /> : <PopoverRoot />;
}
