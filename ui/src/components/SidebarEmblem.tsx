import { LOGO_MASK } from "../ascii/logo-mask";

/**
 * The mask is drawn 1:1, never resampled: every earlier attempt shrank the 66x37
 * art into a smaller char grid (28x10 max-density, then 48x27 nearest-cell) and
 * the thin facets turned into a blob. The size comes from the font instead:
 * 4px cells put the whole mark in ~158px, inside the 264px rail.
 */
const EMBLEM = LOGO_MASK.map((row) =>
  row.replace(/[#+. ]/g, (cell) => (cell === "#" ? "█" : cell === "+" ? "▒" : " ")),
).join("\n");

/** Static sidebar mark: no props, no animation, no rAF. */
export function SidebarEmblem({ className = "" }: { className?: string }) {
  return (
    <pre
      aria-hidden="true"
      className={`select-none whitespace-pre text-[4px] leading-[4px] tracking-normal text-amber/90 ${className}`}
    >
      {EMBLEM}
    </pre>
  );
}
