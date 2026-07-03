// The assistant bubble (WF-DESIGN-0012): append-only text (no per-token animation — jank at
// 30+ tok/s), a soft caret while streaming, aria-busy, selectable. The reply renders below a
// FIXED decision hero: this component never causes the hero to move.
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
        "select-text rounded-lg bg-card p-3 text-[13px] leading-[1.45] text-card-foreground",
        "whitespace-pre-wrap break-words",
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
