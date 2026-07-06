// The decision-only / first-run nudge (WF-DESIGN-0014, flattened — no card wrapper): the
// gateway scored the turn but has no model to answer with. Replaces the reply when the turn
// came back decisionOnly (WF-ADR-0042).
import { useState } from "react";
import { Button } from "@/components/ui/button";

const SNIPPET = "wayfinder-router init";

export function OnboardingCard() {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-2.5 px-5 py-5">
      <div className="text-[16px] font-bold">Wayfinder scored this turn</div>
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
    </div>
  );
}
