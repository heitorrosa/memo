import type { ReactElement, ReactNode, RefObject } from "react";

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
    <section className={`flex min-h-0 min-w-0 flex-col ${className}`}>
      <header className="flex min-w-0 shrink-0 items-center gap-2 px-3 pt-1.5">
        <span className="microlabel text-muted">{title}</span>
        <span aria-hidden="true" className="h-px min-w-4 flex-1 bg-line" />
        {meta ? <span className="microlabel min-w-0 truncate tabular">{meta}</span> : null}
      </header>
      <div className={`min-h-0 min-w-0 flex-1 ${bodyClassName}`}>{children}</div>
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

export type Hint = {
  /** Empty string renders the label alone — for the leading state item. */
  key: string;
  label: string;
  onPress?: () => void;
  disabled?: boolean;
  title?: string;
};

/**
 * Footer hint row. An item with `onPress` is a real button (the mouse twin of
 * its hotkey); one without stays plain text, which is how a pure keybinding
 * like `j k move` remains informational.
 */
export function Hints({ items }: { items: Hint[] }): ReactElement {
  return (
    <div className="microlabel flex shrink-0 flex-wrap items-center gap-x-1 gap-y-0.5 border-t border-line-soft px-3 py-1">
      {items.map((item, position) => {
        const face = item.key ? (
          <>
            <span aria-hidden="true">[{item.key}]</span> {item.label}
          </>
        ) : (
          item.label
        );
        return (
          <span key={`${item.key}:${item.label}:${position}`} className="flex items-center gap-x-1">
            {position > 0 ? (
              <span aria-hidden="true" className="text-faint/60">
                ·
              </span>
            ) : null}
            {item.onPress ? (
              <button
                type="button"
                disabled={item.disabled}
                title={item.title}
                onClick={item.onPress}
                className={`whitespace-nowrap px-1 ${
                  item.disabled
                    ? "cursor-default text-faint/60"
                    : "text-muted hover:bg-surface-2 hover:text-amber focus-visible:bg-surface-2 focus-visible:text-amber"
                }`}
              >
                {face}
              </button>
            ) : (
              <span className="whitespace-nowrap">{face}</span>
            )}
          </span>
        );
      })}
    </div>
  );
}
