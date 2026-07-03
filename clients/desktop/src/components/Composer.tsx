// The pinned composer (WF-DESIGN-0012): a textarea that grows 1 -> 4 rows, Enter sends,
// Shift+Enter newlines, the one teal primary action. While a turn streams the send button
// becomes Stop, which aborts through the wire client's AbortSignal (the caller owns it).
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export function Composer({
  streaming,
  disabled = false,
  onSend,
  onStop,
  className,
}: {
  streaming: boolean;
  disabled?: boolean;
  onSend: (prompt: string) => void;
  onStop: () => void;
  className?: string;
}) {
  const [value, setValue] = useState("");
  const rows = Math.min(4, Math.max(1, value.split("\n").length));

  function send() {
    const prompt = value.trim();
    if (!prompt || streaming || disabled) return;
    onSend(prompt);
    setValue("");
  }

  return (
    <div className={cn("flex items-end gap-2", className)}>
      <Textarea
        value={value}
        rows={rows}
        autoFocus
        disabled={disabled}
        placeholder="Send a message — Wayfinder routes it…"
        aria-label="message"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
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
        <Button size="sm" onClick={send} disabled={disabled || !value.trim()} aria-label="send">
          ›
        </Button>
      )}
    </div>
  );
}
