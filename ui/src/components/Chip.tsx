export function Chip({
  label,
  active,
  color,
  count,
  toggle = false,
  onClick,
}: {
  label: string;
  active?: boolean;
  color?: string;
  count?: number;
  toggle?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className="microlabel hover:text-ink"
      style={active ? { color: color ?? "var(--color-ink)" } : undefined}
    >
      {toggle ? (active ? "[x] " : "[ ] ") : active ? "▸ " : ""}
      {label}
      {count === undefined ? "" : ` ${count}`}
    </button>
  );
}
