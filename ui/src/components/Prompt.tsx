import { useEffect } from "react";
import { useAutoFocus } from "../hooks/useHotkeys";

/**
 * Inline `[y/N]` confirm. Takes the keyboard while it is mounted (no input
 * guard: the point is that a stray keystroke in a field cannot confirm).
 */
export function Prompt({
  label,
  onYes,
  onNo,
  danger = false,
}: {
  label: string;
  onYes: () => void;
  onNo: () => void;
  danger?: boolean;
}) {
  const ref = useAutoFocus<HTMLDivElement>();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "y") {
        event.preventDefault();
        onYes();
      } else if (key === "n" || event.key === "Escape") {
        event.preventDefault();
        onNo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onYes, onNo]);

  return (
    <div
      ref={ref}
      tabIndex={-1}
      className="flex items-baseline gap-3 border-t border-line bg-surface-2 px-3 py-1 outline-none"
    >
      <span className={danger ? "text-coral" : "text-ink"}>{label}</span>
      <span className="text-faint">[y/N]</span>
    </div>
  );
}
