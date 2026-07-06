// The parity-gated local preview (WF-ADR-0042 §2 / WF-DESIGN-0014, flattened — no card wrapper):
// type and see the decision the scorer would make, computed by the byte-for-byte JS mirror —
// unmissably framed as a preview, never a routed decision. Withheld entirely ("decisions
// unavailable") when the build-time parity gate isn't green, so a drifted scorer can never
// quietly lie.
import { useState } from "react";
import { localPreview, parityVerified } from "@/lib/scorerPreview";
import { routeGlyph, routeKind, routeLabel } from "@wayfinder/shared/decision";
import { Bar } from "@/components/menu/Bar";
import { Textarea } from "@/components/ui/textarea";

export function LocalMirror() {
  const [text, setText] = useState("");
  const verified = parityVerified();
  const preview = localPreview(text);

  return (
    <div className="flex flex-col gap-2">
      {!verified ? (
        <p className="text-[11px] text-muted-foreground">
          decisions unavailable — the local scorer is withheld until its parity check passes
        </p>
      ) : (
        <>
          <Textarea
            value={text}
            rows={2}
            aria-label="preview a routing decision"
            placeholder="type a prompt — Wayfinder scores it locally…"
            onChange={(e) => setText(e.target.value)}
            className="min-h-0 resize-none rounded-lg text-[13px] leading-[1.45]"
          />
          {preview &&
            (() => {
              const kind = routeKind(preview);
              const color = kind === "local" ? "var(--primary)" : "var(--route-cloud)";
              return (
                <div className="flex flex-col gap-1.5 border-t border-border pt-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px] font-medium">
                      <span aria-hidden>{routeGlyph(preview)}</span> {routeLabel(preview)}
                    </span>
                    <span className="text-[11px] text-muted-foreground">local mirror</span>
                  </div>
                  <Bar fraction={preview.score} color={color} label="complexity score" />
                  <p className="text-[11px] text-muted-foreground">
                    a preview from the on-device scorer — start the gateway for a real routed decision
                  </p>
                </div>
              );
            })()}
        </>
      )}
    </div>
  );
}
