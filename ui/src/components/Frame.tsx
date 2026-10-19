import type { ReactNode } from "react";

const WORDMARK = [
  "█▀▄▀█ █▀▀ █▀▄▀█ █▀█",
  "█ ▀ █ █▀▀ █ ▀ █ █ █",
  "▀   ▀ ▀▀▀ ▀   ▀ ▀▀▀",
].join("\n");

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <pre aria-hidden="true" className={`select-none leading-[1.1] ${className}`}>
      {WORDMARK}
    </pre>
  );
}

/** Outermost chrome: the corner glyphs that make the viewport read as a box. */
export function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="relative h-full overflow-hidden bg-bg">
      {children}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 select-none text-line"
      >
        <span className="absolute left-0 top-0">┌</span>
        <span className="absolute right-0 top-0">┐</span>
        <span className="absolute bottom-0 left-0">└</span>
        <span className="absolute bottom-0 right-0">┘</span>
      </div>
    </div>
  );
}
