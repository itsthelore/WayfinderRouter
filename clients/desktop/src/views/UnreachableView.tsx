// The gateway-unreachable surface (WF-DESIGN-0012): a machine that has seen a gateway before
// but can't reach it now. Unmissably a preview — the local mirror carries the routing, and the
// primary affordance is starting the service back up (wired to the tray/service control in
// Phase 3). No dead screen: you can still preview decisions.
import { Button } from "@/components/ui/button";
import { LocalMirror } from "@/components/LocalMirror";

export function UnreachableView({ onStartGateway }: { onStartGateway?: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3.5">
      <div className="flex flex-col gap-1">
        <div className="text-[15px] font-semibold">Wayfinder isn’t running</div>
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          The gateway on <span className="font-mono">127.0.0.1:8088</span> isn’t responding. Start
          it to route for real — meanwhile here’s the on-device preview.
        </p>
      </div>
      <div>
        <Button size="sm" onClick={onStartGateway} disabled={!onStartGateway}>
          Start Wayfinder
        </Button>
        {!onStartGateway && (
          <span className="ml-2 text-[11px] text-muted-foreground">
            (service control arrives with the tray menu)
          </span>
        )}
      </div>
      <LocalMirror />
    </div>
  );
}
