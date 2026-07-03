// First run (WF-DESIGN-0012): never seen a gateway on this machine. The brand hero, the
// install-the-service CTA (the one-click WF-ADR-0038 path, wired in Phase 4 onboarding), and a
// live scorer demo so the very first thing the app does is show a real decision — keyless, no
// backend, can't fail (WF-ADR-0042: the floor is a keyless decision preview).
import { Button } from "@/components/ui/button";
import { LocalMirror } from "@/components/LocalMirror";

export function FirstRunView({ onInstallService }: { onInstallService?: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-3.5">
      <div className="flex flex-col items-center gap-1 pt-2 text-center">
        <div className="text-[22px] font-semibold tracking-tight">
          <span aria-hidden className="text-primary">
            ✦
          </span>{" "}
          Wayfinder
        </div>
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          Deterministic LLM routing — local vs cloud, decided on-device.
        </p>
      </div>
      <div className="flex flex-col items-center gap-1">
        <Button size="sm" onClick={onInstallService} disabled={!onInstallService}>
          Install the Wayfinder service
        </Button>
        {!onInstallService && (
          <span className="text-[11px] text-muted-foreground">
            (one-click install arrives with onboarding)
          </span>
        )}
      </div>
      <div className="text-[11px] font-medium tracking-wide uppercase text-muted-foreground">
        try it now
      </div>
      <LocalMirror />
    </div>
  );
}
