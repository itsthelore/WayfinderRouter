// The gateway-unreachable surface (WF-DESIGN-0012): a machine that has seen a gateway before
// but can't reach it now. Unmissably a preview — the local mirror carries the routing, and the
// primary affordance is starting the service back up (the app never spawns it — WF-ADR-0042 §4).
// No dead screen: you can still preview decisions.
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { LocalMirror } from "@/components/LocalMirror";

export function UnreachableView({ onStartGateway }: { onStartGateway?: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    if (!onStartGateway) return;
    setBusy(true);
    setError(null);
    try {
      await onStartGateway();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3.5">
      <div className="flex flex-col gap-1">
        <div className="text-[15px] font-semibold">Wayfinder isn’t running</div>
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          The gateway on <span className="font-mono">127.0.0.1:8088</span> isn’t responding. Start
          it to route for real — meanwhile here’s the on-device preview.
        </p>
      </div>
      <div className="flex flex-col gap-1">
        <Button size="sm" onClick={start} disabled={!onStartGateway || busy}>
          {busy ? "Starting…" : "Start Wayfinder"}
        </Button>
        {error && (
          <span className="text-[11px]" style={{ color: "var(--destructive)" }}>
            {error}
          </span>
        )}
      </div>
      <LocalMirror />
    </div>
  );
}
