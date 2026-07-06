// The assistant reply (WF-DESIGN-0014, flattened — no bubble background; CodexBar has nothing to
// mirror here, so this stays plain text under a hairline, consistent with the rest of the flat
// list): append-only (no per-token animation — jank at 30+ tok/s), a soft caret while streaming,
// aria-busy, selectable. Renders below a FIXED decision summary: it never causes that to move.
import { cn } from "@/lib/utils";

export function StreamingMessage({
  reply,
  streaming,
  className,
}: {
  reply: string;
  streaming: boolean;
  className?: string;
}) {
  return (
    <div
      aria-busy={streaming}
      className={cn(
        "select-text whitespace-pre-wrap break-words border-t border-border px-5 py-3.5 text-[13px] leading-[1.45]",
        className,
      )}
    >
      {reply}
      {streaming && (
        <span
          aria-hidden
          className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-pulse rounded-sm bg-muted-foreground"
        />
      )}
    </div>
  );
}
