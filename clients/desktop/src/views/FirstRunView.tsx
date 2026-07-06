// First run (WF-ADR-0042 / WF-DESIGN-0015): never seen a gateway on this machine. The brand
// hero, a preset picker + one "Set up routing" CTA (scaffold config through the gateway's own
// `init --preset --keychain` — the config seam, WF-ADR-0044 — then install + start the
// service), and a live scorer demo so the very first thing the app does is show a real
// decision — keyless, no backend, can't fail (WF-ADR-0042). Exit is organic: the next healthz
// poll sees the gateway and flips the view (decision-only/degraded until a key lands via
// Settings → Keys). Full surface, no header list (the WF-DESIGN-0013/0014 invariant).
import { useState } from "react";
import type { Preset } from "@/lib/ipc";
import { Button } from "@/components/ui/button";
import { LocalMirror } from "@/components/LocalMirror";
import { cn } from "@/lib/utils";
import wordmark from "@/assets/wayfinder-wordmark.png";

const PRESETS: Array<{ id: Preset; label: string; summary: string }> = [
  { id: "hybrid", label: "Hybrid (recommended)", summary: "keyless local Ollama → Anthropic cloud" },
  { id: "openai", label: "OpenAI", summary: "two cost tiers: gpt-4o-mini → gpt-4o" },
  { id: "gemini", label: "Gemini", summary: "two cost tiers: gemini-2.5-flash → pro" },
];

export function FirstRunView({
  onScaffold,
}: {
  onScaffold?: (preset: Preset) => Promise<void>;
}) {
  const [preset, setPreset] = useState<Preset>("hybrid");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function scaffold() {
    if (!onScaffold) return;
    setBusy(true);
    setError(null);
    try {
      await onScaffold(preset);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-5">
      <div className="flex flex-col items-center gap-1.5 pt-2 text-center">
        <img src={wordmark} alt="Wayfinder" className="h-6 w-auto" />
        <p className="text-[13px] leading-[1.45] text-muted-foreground">
          Deterministic LLM routing — local vs cloud, decided on-device.
        </p>
      </div>

      <div role="radiogroup" aria-label="starter preset" className="flex flex-col gap-1">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={preset === p.id}
            onClick={() => setPreset(p.id)}
            className={cn(
              "flex items-baseline justify-between gap-2 rounded-md border px-3 py-2 text-left",
              preset === p.id ? "border-ring" : "border-border",
            )}
          >
            <span className="text-[13px] font-medium">{p.label}</span>
            <span className="text-[11px] text-muted-foreground">{p.summary}</span>
          </button>
        ))}
      </div>

      <div className="flex flex-col items-center gap-1">
        <Button size="sm" onClick={scaffold} disabled={!onScaffold || busy}>
          {busy ? "Setting up…" : "Set up routing"}
        </Button>
        <p className="text-center text-[11px] text-muted-foreground">
          Writes a starter config via <span className="font-mono">wayfinder-router init</span> and
          installs the service. Keys come later, stored in the Keychain — never in a file.
        </p>
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
