// The Composer's slash-command overlay (mirrors Claude's own "/" menu): a small list anchored
// above the textarea, filtered as you type, navigated with the arrow keys — never a modal that
// steals focus from the textarea itself. Composer owns the open/filter/selection state; this
// component is the presentation only.
import { cn } from "@/lib/utils";

export interface SlashCommand {
  /** Without the leading slash, e.g. "clear". */
  name: string;
  description: string;
  run: () => void;
}

export function SlashMenu({
  commands,
  selected,
  onSelect,
}: {
  commands: SlashCommand[];
  selected: number;
  onSelect: (command: SlashCommand) => void;
}) {
  if (commands.length === 0) return null;
  return (
    <div
      role="listbox"
      aria-label="slash commands"
      className="absolute inset-x-0 bottom-full z-10 mb-2 flex flex-col overflow-hidden rounded-lg border border-border bg-popover py-1 text-popover-foreground shadow-md"
    >
      {commands.map((c, i) => (
        <button
          key={c.name}
          type="button"
          role="option"
          aria-selected={i === selected}
          // mousedown, not click: firing before the textarea's blur keeps focus in the
          // composer instead of losing it to the button.
          onMouseDown={(e) => {
            e.preventDefault();
            onSelect(c);
          }}
          className={cn(
            "flex items-center justify-between gap-3 px-3 py-1.5 text-left text-[13px]",
            i === selected && "bg-accent",
          )}
        >
          <span className="font-mono font-medium">/{c.name}</span>
          <span className="truncate text-muted-foreground">{c.description}</span>
        </button>
      ))}
    </div>
  );
}
