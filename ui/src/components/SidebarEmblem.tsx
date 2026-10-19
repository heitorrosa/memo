import { LOGO_MASK, MASK_COLS, MASK_ROWS } from "../ascii/logo-mask";

/**
 * Source mask is 66 cells x 37 lines, so the drawing is ~1.78 cells wide per
 * line; the rail cell is ~1.8x taller than wide, which is why the char grid has
 * to keep that 1.78 ratio or the emblem squashes into a blob. 48x27 does.
 */
const COLS = 48;
const ROWS = 27;

/**
 * Nearest-cell sample of LOGO_MASK (the centre cell of the source block it
 * covers). Earlier attempts used a density max over the whole block, which
 * filled every thin diagonal and turned the emblem into a solid lump.
 */
const EMBLEM = (() => {
  const glyph = (cell: string | undefined): string =>
    cell === "#" ? "█" : cell === "+" ? "▒" : " ";
  return Array.from({ length: ROWS }, (_, oy) => {
    const sy = Math.min(MASK_ROWS - 1, Math.floor(((oy + 0.5) * MASK_ROWS) / ROWS));
    let line = "";
    for (let ox = 0; ox < COLS; ox++) {
      const sx = Math.min(MASK_COLS - 1, Math.floor(((ox + 0.5) * MASK_COLS) / COLS));
      line += glyph(LOGO_MASK[sy]?.[sx]);
    }
    return line;
  }).join("\n");
})();

/** Static sidebar mark: no props, no animation, no rAF. */
export function SidebarEmblem({ className = "" }: { className?: string }) {
  return (
    <pre
      aria-hidden="true"
      className={`select-none text-[6px] leading-[6px] tracking-normal text-amber/90 ${className}`}
    >
      {EMBLEM}
    </pre>
  );
}
