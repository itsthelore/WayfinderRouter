// The offline switch (WF-DESIGN-0012 / WF-ADR-0039): when healthz says offline came from
// config it renders on + disabled ("offline by config" — the gateway owns it); otherwise it
// toggles the client preference that adds X-Wayfinder-Offline per turn. Teal when on: the
// switch is an interactive control, and offline is the guarantee, not a route.
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

export function OfflineToggle({
  on,
  lockedByConfig,
  onChange,
  className,
}: {
  on: boolean;
  lockedByConfig: boolean;
  onChange: (on: boolean) => void;
  className?: string;
}) {
  return (
    <label className={cn("flex items-center gap-2", className)}>
      <span className="text-[11px] font-medium tracking-wide uppercase text-muted-foreground">
        {lockedByConfig ? "offline by config" : "offline"}
      </span>
      <Switch
        size="sm"
        checked={on || lockedByConfig}
        disabled={lockedByConfig}
        onCheckedChange={onChange}
        aria-label="offline mode"
      />
    </label>
  );
}
