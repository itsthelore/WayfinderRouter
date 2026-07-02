// Phase 1 checkpoint composition (WF-DESIGN-0012): a token/component swatch over the vibrant
// popover so light + dark both prove the Wayfinder palette (teal = brand/LOCAL/interactive,
// amber = CLOUD route accent only). The real decision-first popover replaces this in Phase 2.
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

function RoutePill({ kind }: { kind: "local" | "cloud" }) {
  // Route accent flows through data-route → var(--route-accent); glyphs per decision.js.
  return (
    <span
      data-route={kind}
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium tracking-wide uppercase"
      style={{
        color: "var(--route-accent)",
        background: kind === "local" ? "var(--accent)" : "var(--route-cloud-weak)",
      }}
    >
      <span aria-hidden>{kind === "local" ? "●" : "◆"}</span>
      {kind}
    </span>
  );
}

export function App() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between bg-background px-3.5 py-2.5">
        <span className="text-[13px] font-semibold">✦ Wayfinder</span>
        <Badge variant="secondary" className="text-[11px]">
          design system
        </Badge>
      </header>
      <Separator />

      <main className="flex flex-1 flex-col gap-3 p-3.5">
        <Card className="rounded-hero">
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex items-center gap-2">
              <RoutePill kind="local" />
              <RoutePill kind="cloud" />
            </div>
            <div className="font-mono text-[22px] font-semibold tabular-nums">0.18</div>
            <div className="h-1 w-full rounded-full bg-track">
              <div className="h-1 w-[18%] rounded-full bg-primary" />
            </div>
            <p className="text-[13px] text-muted-foreground">
              Tokens over vibrancy — L2 card, hairlines, tabular score. The decision-first
              popover lands in Phase 2.
            </p>
          </CardContent>
        </Card>

        <div className="flex items-center gap-2">
          <Button size="sm">Primary</Button>
          <Button size="sm" variant="secondary">
            Secondary
          </Button>
          <Button size="sm" variant="ghost">
            Ghost
          </Button>
        </div>
      </main>
    </div>
  );
}
