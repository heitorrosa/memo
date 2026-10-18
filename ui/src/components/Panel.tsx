import type { ReactNode, RefObject } from "react";

export interface PanelProps {
  /** Pane is the visible one; inactive panes stay mounted to keep their state. */
  active: boolean;
  /** False while the keymap overlay owns the keyboard. */
  keys: boolean;
  selected: string | null;
  onSelect: (key: string | null) => void;
  onToast: (kind: "ok" | "err", text: string) => void;
  /** Bumped by `r`; panels fold it into their request deps. */
  reloadSignal: number;
  searchRef: RefObject<HTMLInputElement | null>;
  /** Refetch the shell counters after a write. */
  onStats: () => void;
}

/**
 * A titled region: `TITLE ──────── meta`. The leader rule is the divider —
 * no card, no shadow, no radius.
 */
export function Panel({
  title,
  meta,
  children,
  className = "",
  bodyClassName = "",
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`flex min-h-0 flex-col ${className}`}>
      <header className="flex shrink-0 items-center gap-2 px-3 pt-1.5">
        <span className="microlabel text-muted">{title}</span>
        <span aria-hidden="true" className="h-px min-w-4 flex-1 bg-line" />
        {meta ? <span className="microlabel tabular">{meta}</span> : null}
      </header>
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/** The only loading/empty/error rendering: one terse line, never a spinner. */
export function Line({
  kind,
  children,
  className = "",
}: {
  kind: "loading" | "error" | "empty";
  children: ReactNode;
  className?: string;
}) {
  const tone =
    kind === "error" ? "text-coral" : kind === "empty" ? "text-faint" : "text-muted";
  return <p className={`px-3 py-1.5 ${tone} ${className}`}>{children}</p>;
}

/** Detail-sheet sub-heading: `TITLE ─────── meta`. */
export function SectionHead({ children, meta }: { children: ReactNode; meta?: ReactNode }) {
  return (
    <div className="mt-4 flex items-center gap-2 border-t border-line-soft pt-1">
      <span className="microlabel text-muted">{children}</span>
      <span aria-hidden="true" className="h-px flex-1 bg-line-soft" />
      {meta ? <span className="microlabel tabular">{meta}</span> : null}
    </div>
  );
}

/** The `/` search field every list pane shares. */
export function SearchBar({
  inputRef,
  value,
  onChange,
  placeholder,
}: {
  inputRef?: RefObject<HTMLInputElement | null>;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div className="flex items-center gap-2 border-b border-line-soft px-3 py-1">
      <span aria-hidden="true" className="text-faint">
        /
      </span>
      <input
        ref={inputRef}
        value={value}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          // Leave the field and drop the filter: otherwise a keyboard-only user
          // can never reach n/d/? again — the field swallows every keystroke.
          event.preventDefault();
          event.currentTarget.blur();
          onChange("");
        }}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent placeholder:text-faint"
      />
      {value ? (
        <button type="button" className="microlabel hover:text-ink" onClick={() => onChange("")}>
          clear
        </button>
      ) : null}
    </div>
  );
}
