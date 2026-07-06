// The pinned composer (WF-DESIGN-0012, unchanged behaviour): a textarea that grows 1 -> 4 rows,
// Enter sends, Shift+Enter newlines, the one teal primary action. While a turn streams the send
// button becomes Stop, which aborts through the wire client's AbortSignal (the caller owns it).
//
// Slash commands (WF-DESIGN-0014 amendment): typing "/" as the message's first token opens the
// SlashMenu overlay, filtered as you keep typing — the same interaction Claude's own composer
// uses. Arrow keys move the highlight, Enter runs the highlighted command, Escape dismisses.
// The menu never steals focus from the textarea; it is a plain positioned list, not a modal.
import { useEffect, useState } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { SlashMenu, type SlashCommand } from "@/components/SlashMenu";
import { cn } from "@/lib/utils";

/** The value is in slash-command mode only while it's a single token starting with "/" — a
 *  space (or newline) commits back to a plain message and closes the menu. */
function slashQuery(value: string): string | null {
  const m = /^\/(\S*)$/.exec(value);
  return m ? m[1].toLowerCase() : null;
}

export function Composer({
  streaming,
  disabled = false,
  commands = [],
  onSend,
  onStop,
  className,
}: {
  streaming: boolean;
  disabled?: boolean;
  /** Available slash commands (WF-DESIGN-0014). Empty by default — plain send-only composer. */
  commands?: SlashCommand[];
  onSend: (prompt: string) => void;
  onStop: () => void;
  className?: string;
}) {
  const [value, setValue] = useState("");
  const [selected, setSelected] = useState(0);
  const rows = Math.min(4, Math.max(1, value.split("\n").length));

  const query = slashQuery(value);
  const filtered = query !== null ? commands.filter((c) => c.name.startsWith(query)) : [];
  const menuOpen = filtered.length > 0;

  // Re-anchor the highlight to the top whenever the filter itself changes (each keystroke),
  // so it never points past the end of a shrinking list.
  useEffect(() => setSelected(0), [query]);

  function send() {
    const prompt = value.trim();
    if (!prompt || streaming || disabled) return;
    onSend(prompt);
    setValue("");
  }

  function runCommand(command: SlashCommand) {
    command.run();
    setValue("");
  }

  return (
    <div className={cn("relative flex items-end gap-2", className)}>
      {menuOpen && <SlashMenu commands={filtered} selected={selected} onSelect={runCommand} />}
      <Textarea
        value={value}
        rows={rows}
        autoFocus
        disabled={disabled}
        placeholder="Send a message — Wayfinder routes it…"
        aria-label="message"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (menuOpen) {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSelected((s) => (s + 1) % filtered.length);
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setSelected((s) => (s - 1 + filtered.length) % filtered.length);
              return;
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              runCommand(filtered[selected]!);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setValue("");
              return;
            }
          }
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
        className="min-h-0 resize-none rounded-lg text-[13px] leading-[1.45]"
      />
      {streaming ? (
        <Button size="sm" variant="secondary" onClick={onStop} aria-label="stop streaming">
          stop
        </Button>
      ) : (
        <Button
          size="icon-sm"
          className="rounded-full"
          onClick={send}
          disabled={disabled || !value.trim()}
          aria-label="send"
        >
          <ArrowUp className="size-4" aria-hidden />
        </Button>
      )}
    </div>
  );
}
