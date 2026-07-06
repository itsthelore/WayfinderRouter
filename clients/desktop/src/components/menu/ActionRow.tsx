// A plain icon+label row (WF-DESIGN-0014, mirrors CodexBar's "Add Account…" / "Usage Dashboard"
// rows). `checked` renders a leading checkmark instead of the icon (an offline-style toggle —
// the native-menu equivalent of a checkable NSMenuItem); `chevron` renders a trailing `›` for a
// row that pushes a full sub-screen (Chat). Never both — a row toggles state or it navigates.
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export function ActionRow({
  icon: Icon,
  label,
  checked,
  chevron,
  disabled,
  hint,
  onClick,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  label: string;
  checked?: boolean;
  chevron?: boolean;
  disabled?: boolean;
  hint?: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || !onClick}
      aria-pressed={checked}
      className={cn(
        "flex w-full items-center gap-3 px-5 py-3.5 text-left text-[16px]",
        "transition-colors duration-[var(--dur-fast)] disabled:cursor-default",
        onClick && !disabled && "hover:bg-accent",
      )}
    >
      <span aria-hidden className="flex w-5 shrink-0 items-center justify-center text-muted-foreground">
        {checked !== undefined ? (
          checked ? (
            <Check className="size-[18px]" />
          ) : (
            Icon && <Icon className="size-[18px]" />
          )
        ) : (
          Icon && <Icon className="size-[18px]" />
        )}
      </span>
      <span className="flex-1 truncate">{label}</span>
      {hint && <span className="text-[13px] text-muted-foreground">{hint}</span>}
      {chevron && (
        <span aria-hidden className="text-[16px] text-muted-foreground">
          ›
        </span>
      )}
    </button>
  );
}
