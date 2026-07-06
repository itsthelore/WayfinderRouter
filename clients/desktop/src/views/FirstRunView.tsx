// First run (WF-ADR-0042 / WF-DESIGN-0014): never seen a gateway on this machine. The brand
// hero, the install-the-service CTA (the one-click WF-ADR-0038 path — the app asks the service
// manager, it never spawns the gateway itself), and a live scorer demo so the very first thing
// the app does is show a real decision — keyless, no backend, can't fail (WF-ADR-0042). Full
// surface, no header list (WF-DESIGN-0014 keeps this WF-DESIGN-0013 invariant).
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { LocalMirror } from "@/components/LocalMirror";

export function FirstRunView({ onInstallService }: { onInstallService?: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function install() {
    if (!onInstallService) return;
    setBusy(true);
    setError(null);
    try {
      await onInstallService();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-5">
      <div className="flex flex-col items-center gap-1.5 pt-2 text-center">
        <div className="text-[19px] font-bold">Wayfinder</div>
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          Deterministic LLM routing — local vs cloud, decided on-device.
        </p>
      </div>
      <div className="flex flex-col items-center gap-1">
        <Button size="sm" onClick={install} disabled={!onInstallService || busy}>
          {busy ? "Installing…" : "Install the Wayfinder service"}
        </Button>
        {error && (
          <span className="text-center text-[11px]" style={{ color: "var(--destructive)" }}>
            {error}
          </span>
        )}
      </div>
      <div className="text-[13px] font-medium text-muted-foreground">try it now</div>
      <LocalMirror />
    </div>
  );
}
