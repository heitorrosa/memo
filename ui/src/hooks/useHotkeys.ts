import { useEffect, useRef } from "react";

export type HotkeyHandler = (event: KeyboardEvent) => void;

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return (
    el.tagName === "INPUT" ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    el.isContentEditable
  );
}

/**
 * Window-level bindings keyed by `event.key`. Ignored while the event target is
 * a form field, so typing never fires a hotkey. `enabled` lets a pane yield to
 * an overlay.
 */
export function useHotkeys(
  bindings: Record<string, HotkeyHandler>,
  enabled = true,
  allowInInput = false,
): void {
  const ref = useRef(bindings);
  ref.current = bindings;

  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (!allowInInput && isTyping(event.target)) return;
      const handler = ref.current[event.key];
      if (!handler) return;
      event.preventDefault();
      handler(event);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, allowInInput]);
}

/** Locks window focus onto an element for as long as it is mounted. */
export function useAutoFocus<T extends HTMLElement>(active = true) {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    if (active) ref.current?.focus();
  }, [active]);
  return ref;
}
