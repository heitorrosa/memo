/**
 * Login-gate ascii logo mask + the pure frame function behind it.
 *
 * FORMAT — to swap in a real logo (e.g. derived from an SVG by thresholding):
 *   '#' = spine (outline, drawn at full accent)
 *   '+' = body (fill, drawn dimmer)
 *   '.' = hole (transparent)
 * Rows may be ragged; the renderer pads them to the widest row and centres the
 * block. The map is scaled to the viewport grid by one uniform integer factor,
 * so keep it wide: about 6 cols per row here reads as ~3:1 on screen at the
 * 0.6em mono advance. Nothing else needs to change: paste the rows, keep the
 * legend.
 *
 * SOURCE — the current map is the repo emblem, converted verbatim from
 * C:\Users\Administrator\Documents\Repositories\memo\assets\memo-ascii.txt
 * (rows 26-62, columns 43-109 trimmed window): U+2588 (solid) -> '#',
 * U+2591 (light shade) -> '+', space -> '.'. 37 rows x 66 cols.
 *
 * Everything here is pure and seeded — no Math.random, no DOM — so the same
 * phase always paints the same frame (see scripts/l10-art-check.mjs).
 */

export const LOGO_MASK: string[] = [
  '........................+++#+++....+++#++++.......................',
  '.....................+++#######+..+#######+++.....................',
  '..................+++###########..###########+++..................',
  '...............+++##############..##############+++...............',
  '............+++######++########+..#################+++............',
  '.........+++######++++...+#####+..+######...#+++######+++.........',
  '........+#######+++......+######..+######......+++#######+........',
  '......++######++.........+######..#######.........++######+.......',
  '......+#######...........+######..#######...........+######+......',
  '......+######............+#####+..######+............######+......',
  '......+######............+#####+..+#####+............+#####+......',
  '...+++######+............+#####+..+#####+............+#####++++...',
  '.+++######++.............+#####+..+######.............+++#####+++.',
  '++#####+++...............######+..+######...............+++#####+.',
  '+#####++.................######+..#######.................+######+',
  '+#####+.........#+.......+#####+..#######.......++.........######+',
  '+######........+##+......+#####+..#######......+##+........######+',
  '+######.......++##+++....+#####+..#######....#####++.......######+',
  '+######...++++######++++.+######..######+.#+++######++++...######+',
  '+#####+...++++######+++#.#######..######+.++++######+++....######+',
  '+######.......++##++.....+######..######+.....++##++.......######+',
  '+#####+........++#+......+######..######+......+#++........######+',
  '+######.........++.......+######..######+.......++........+######+',
  '+#########...............+######..######+...............+++######+',
  '.++#########.............+######..######+.............+++######++.',
  '...++########............+######..######+............+#######++...',
  '.....++######............+######..######+............######++.....',
  '......+######............+######..######+............######+......',
  '......+######+...........+######..######+...........+######+......',
  '......++#####+++.........+######..######+.........+++#####++......',
  '.......+++######+++......######+..#######......+++######+++.......',
  '.........++++######+++...######+..#######...+++######++++.........',
  '............++++###############+..################++++............',
  '...............++++############+..+############++++...............',
  '..................+++##########+..+##########+++..................',
  '.....................+++#####++....++######++.....................',
  '........................++++++......++++++........................',
];

export const MASK_ROWS = LOGO_MASK.length;
export const MASK_COLS = Math.max(...LOGO_MASK.map((row) => row.length));

/** Mask cells settle in over this window on mount. */
export const SETTLE_MS = 1100;
/** One scan pass every this many milliseconds. */
export const SWEEP_MS = 6000;
/** Mono advance width in em — the grid geometry depends on it. */
export const CELL_W = 0.6;

const TAU = Math.PI * 2;
/** Half-width of the scan band, in grid cells. */
const BAND = 5;
/** Visual slope of the diagonal sweep (cells are ~0.6:1, so 1.7 reads as 45°). */
const SLOPE = 1.7;

export type Frame = {
  /** Every visible cell, at the dim tier. */
  text: string;
  /** Only the cells inside the scan band; same dimensions as `text`. */
  hot: string;
  cols: number;
  rows: number;
  /** Number of ink cells (mask cells x scale^2), for the cell budget check. */
  ink: number;
};

/** Seeded hash in [0,1): one step of mulberry32 keyed by cell index. */
function rand(i: number): number {
  let t = (i + 0x6d2b79f5) >>> 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

const isInk = (x: number, y: number): boolean => {
  const glyph = LOGO_MASK[y]?.[x];
  return glyph === '#' || glyph === '+';
};

/**
 * Connected-glyph id per mask cell, flooded once at load. Cells of one glyph
 * share a drift orbit so a stroke never shears away from its letter — works for
 * any pasted mask without knowing where the letters are.
 */
const GLYPH: number[] = (() => {
  const ids = new Array<number>(MASK_ROWS * MASK_COLS).fill(-1);
  let next = 0;
  for (let y0 = 0; y0 < MASK_ROWS; y0++) {
    for (let x0 = 0; x0 < MASK_COLS; x0++) {
      if (ids[y0 * MASK_COLS + x0] !== -1 || !isInk(x0, y0)) continue;
      const stack: number[][] = [[x0, y0]];
      ids[y0 * MASK_COLS + x0] = next;
      while (stack.length) {
        const [x, y] = stack.pop() as number[];
        for (const [nx, ny] of [
          [x + 1, y],
          [x - 1, y],
          [x, y + 1],
          [x, y - 1],
        ]) {
          if (nx < 0 || ny < 0 || nx >= MASK_COLS || ny >= MASK_ROWS) continue;
          if (ids[ny * MASK_COLS + nx] !== -1 || !isInk(nx, ny)) continue;
          ids[ny * MASK_COLS + nx] = next;
          stack.push([nx, ny]);
        }
      }
      next++;
    }
  }
  return ids;
})();

/**
 * Paint one frame on a cols x rows character grid.
 *
 * @param phase  seconds since mount (drives orbit + sweep, both periodic)
 * @param reveal 0..1 settle progress; 1 = fully assembled
 */
export function renderFrame(phase: number, cols: number, rows: number, reveal = 1): Frame {
  const W = Math.max(1, Math.floor(cols));
  const H = Math.max(1, Math.floor(rows));
  const dim = Array.from({ length: H }, () => new Array<string>(W).fill(' '));
  const hot = Array.from({ length: H }, () => new Array<string>(W).fill(' '));

  const scale = Math.max(1, Math.floor(Math.min((W - 2) / MASK_COLS, (H - 2) / MASK_ROWS)));
  const originX = Math.floor((W - MASK_COLS * scale) / 2);
  const originY = Math.floor((H - MASK_ROWS * scale) / 2);

  const sweep = ((phase % (SWEEP_MS / 1000)) + SWEEP_MS / 1000) % (SWEEP_MS / 1000);
  const travel = sweep / (SWEEP_MS / 1000);
  const front = -BAND + travel * (W + H * SLOPE + BAND * 2);

  let ink = 0;

  for (let my = 0; my < MASK_ROWS; my++) {
    const row = LOGO_MASK[my];
    for (let mx = 0; mx < row.length; mx++) {
      const glyph = row[mx];
      if (glyph !== '#' && glyph !== '+') continue;

      const cell = my * MASK_COLS + mx;
      // Staggered assemble per cell, orbit per glyph.
      if (reveal < rand(cell * 4 + 1)) continue;
      const glyphId = GLYPH[cell];
      const period = 4 + rand(glyphId * 3 + 1) * 5;
      const angle = phase * (TAU / period) + rand(glyphId * 3 + 2) * TAU;
      const dx = Math.round(Math.cos(angle));
      const dy = Math.round(Math.sin(angle * 1.7));

      for (let sy = 0; sy < scale; sy++) {
        const y = originY + my * scale + sy + dy;
        if (y < 0 || y >= H) continue;
        for (let sx = 0; sx < scale; sx++) {
          const x = originX + mx * scale + sx + dx;
          if (x < 0 || x >= W) continue;
          dim[y][x] = glyph;
          ink++;
          const d = x + y * SLOPE;
          if (Math.abs(d - front) <= BAND) hot[y][x] = glyph;
        }
      }
    }
  }

  return {
    text: dim.map((r) => r.join('')).join('\n'),
    hot: hot.map((r) => r.join('')).join('\n'),
    cols: W,
    rows: H,
    ink,
  };
}
