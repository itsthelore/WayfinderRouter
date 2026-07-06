// The footer menu (WF-DESIGN-0014): exact NSMenu style — icon, label, a right-aligned ⌘-shortcut
// that is real (wired to a keydown handler by the caller), never a decorative label.
import { cn } from "@/lib/utils";

export function FooterMenuItem({
  label,
  shortcut,
  onClick,
  className,
}: {
  label: string;
  shortcut?: string;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2.5 px-5 py-2.5 text-left text-[16px]",
        "transition-colors duration-[var(--dur-fast)] hover:bg-accent",
        className,
      )}
    >
      <span className="flex-1 truncate">{label}</span>
      {shortcut && <span className="font-mono text-[13px] text-muted-foreground">{shortcut}</span>}
    </button>
  );
}
