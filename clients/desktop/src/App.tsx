// The popover entry point (WF-DESIGN-0012): the decision-first menu-bar surface. PopoverRoot
// owns the gateway/turn state machines and switches the six modes; this shell is just the
// mount. The window is hidden (not unmounted) on blur, so this tree — and any composer draft —
// survives the next ⌥W.
import { PopoverRoot } from "@/views/PopoverRoot";

export function App() {
  return <PopoverRoot />;
}
