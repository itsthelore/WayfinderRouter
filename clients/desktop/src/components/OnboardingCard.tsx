// The decision-only / first-run nudge (WF-DESIGN-0012): the gateway scored the turn but has
// no model to answer with — say so plainly and hand over the one command that fixes it.
// Replaces StreamingMessage when the turn came back decisionOnly (WF-ADR-0042).
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const SNIPPET = "wayfinder-router init";

export function OnboardingCard({ className }: { className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Card className={cn("rounded-lg", className)}>
      <CardContent className="flex flex-col gap-2 p-3">
        <div className="text-[15px] font-semibold">Wayfinder scored this turn</div>
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          Connect a model to get replies — the routing decision above is already real.
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 select-text rounded-sm bg-secondary px-2 py-1 font-mono text-[11px]">
            {SNIPPET}
          </code>
          <Button
            size="xs"
            variant="secondary"
            onClick={() => {
              void navigator.clipboard?.writeText(SNIPPET);
              setCopied(true);
              setTimeout(() => setCopied(false), 1600);
            }}
          >
            {copied ? "copied" : "copy"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
