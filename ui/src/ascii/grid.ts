// Pure, dependency-free rasteriser for the memory-graph ASCII stage.
// No React, no imports. Node positions come from `layout()` — a 2D
// force-directed graph computed once per payload — so the picture is a stable
// chart: the caller's `phase` only slides a brightness band, never a position.

export type AsciiNode = {
  key: string;
  type: string;
  status: string;
  uses: number;
  score: number;
  degree: number;
};

export type AsciiEdge = {
  src_key: string;
  dst_key: string;
  rel: string;
  weight: number;
};

/** A cosine neighbour pair: `score` is similarity in [0,1]. */
export type AsciiLink = {
  src_key: string;
  dst_key: string;
  score: number;
};

/** World position, in cell units: `x` in [0, cols], `y` in [0, rows*2]. */
export type Vec2 = [number, number];

export type Role =
  | 'ink'
  | 'muted'
  | 'faint'
  | 'amber'
  | 'violet'
  | 'coral'
  | 'blue'
  | 'green';

export type HitBox = { key: string; col: number; row: number };

export type Frame = {
  /** The whole grid as one string. Written verbatim into the single <pre>. */
  text: string;
  /** Same-size layers holding only one colour role's cells; spaces elsewhere. */
  overlays: { role: Role; text: string }[];
  /** Screen cells of drawn (non-synthetic) nodes, for the focusable hit-boxes. */
  hits: HitBox[];
  /** True when the grid was too small to draw, `text` is a single dim line. */
  dim: boolean;
  /** True when the faint backdrop texture was drawn (few real edges). */
  sparse: boolean;
};

/** CSS colour per role. Fallbacks keep the art visible before styles.css loads. */
export const ROLE_VAR: Record<Role, string> = {
  ink: 'var(--color-ink,#ece6da)',
  muted: 'var(--color-muted,#a29a8b)',
  faint: 'var(--color-faint,#6d665c)',
  amber: 'var(--color-amber,#e0a52c)',
  violet: 'var(--color-violet,#a585c9)',
  coral: 'var(--color-coral,#e2603f)',
  blue: 'var(--color-blue,#6f9ec4)',
  green: 'var(--color-green,#7fa650)',
};

/** Overlay layers, painted in order. `ink` is the base layer, so it is excluded. */
export const OVERLAY_ROLES: Role[] = [
  'faint',
  'muted',
  'green',
  'blue',
  'violet',
  'amber',
  'coral',
];

const TYPE_ROLE: Record<string, Role> = {
  preference: 'amber',
  analysis: 'violet',
  feedback: 'coral',
  context: 'blue',
};

const REL_ROLE: Record<string, Role> = {
  mentions: 'faint',
  supports: 'green',
  contradicts: 'coral',
  derived_from: 'violet',
};

const MAX_NODES = 200;
/** Shortest drawn segment worth reading, in cells. */
const MIN_EDGE_LEN = 3;
/**
 * A line longer than the row height x 2.5 crosses most of the frame and reads
 * as noise, so it is dropped. Similarity strands also step 2 cells, which
 * keeps them subordinate to the declared edges and the node glyphs.
 */
const LINE_RATIO = 2.5;
const SIMILAR_STEP = 2;
/**
 * Similarity strands are local texture: each node keeps at most its top-2
 * links by score (undirected pairs count once).
 */
const SIMILAR_PER_NODE = 2;
/** Width either side of the brightness band, in diagonal cells. */
const BAND_HALF = 10;
/** Labels: hubs only, highest degree first, whole-or-nothing. */
const LABEL_DEGREE = 3;

/** Glyphs a line writes; a second line hitting one of them makes a `+`. */
const LINE_GLYPHS = '-|/\\+';

// --- determinism -----------------------------------------------------------

/** FNV-1a, 32-bit. The only source of pseudo-randomness in this module. */
export function hashKey(key: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

/** Hash of a grid cell — used only for the static backdrop texture. */
function cellHash(col: number, row: number): number {
  let h = Math.imul(col + 1, 73856093) ^ Math.imul(row + 1, 19349663);
  h ^= h >>> 13;
  return Math.imul(h, 1274126177) >>> 0;
}

function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Type -> quadrant centre in [0,1]^2 (y grows downward, like rows). */
const QUADRANT: Record<string, [number, number]> = {
  preference: [0.25, 0.25],
  context: [0.75, 0.25],
  feedback: [0.25, 0.75],
  analysis: [0.75, 0.75],
};

/**
 * Stable start point in world cells: the type's quadrant centre plus a hash
 * jitter of half a quadrant, so same-type nodes begin as a loose cluster and
 * never as one dot. Same key -> same point, always.
 */
export function seedPos(n: AsciiNode, cols: number, rows: number): Vec2 {
  const r = rng(hashKey(n.key));
  const [qx, qy] = QUADRANT[n.type] ?? [0.5, 0.5];
  return [(qx + (r() - 0.5) * 0.5) * cols, (qy + (r() - 0.5) * 0.5) * rows * 2];
}

// --- layout ----------------------------------------------------------------

/** Cooling passes, then a fixed low-temperature settle so the field stops dead. */
const ITERS = 250;
const SETTLE = 40;
/**
 * Force tune, in units of the ideal spacing `k = sqrt(area / nodes)`.
 * REPULSION is local only (`CUTOFF * k`): long-range repulsion is what makes a
 * strongly-linked group inflate into a ring around the frame edge.
 * PULL is the link strength; ANCHOR ties each node near its seeded quadrant so
 * the four type clusters stay apart instead of merging into one ball.
 */
const REPULSION = 1;
const PULL = 1.2;
const ANCHOR = 0.5;
const CUTOFF = 1.5;

/**
 * 2D force-directed layout (Fruchterman-Reingold), computed once per payload.
 *
 * World space is cell space (`x` in [0, cols], `y` in [0, rows*2] because a
 * character cell is about twice as tall as it is wide). Seeds come from
 * `seedPos`; every `similar` pair pulls with `score * 0.9`, every declared
 * edge with `1.6`, every nearby pair repels with `k*k/d`, and every node is
 * held near its seed. The result is exactly what the rotation destroyed:
 * linked nodes stay near each other, same-type nodes stay grouped, and nothing
 * moves between frames. No clock, no `Math.random`.
 *
 * With no links at all there is nothing to cluster on, so the seeds are
 * returned untouched and an old server still draws a deterministic field.
 */
export function layout(
  nodes: AsciiNode[],
  edges: AsciiEdge[],
  similar: AsciiLink[] = [],
  cols = 96,
  rows = 14,
): Map<string, Vec2> {
  const list = selectNodes(nodes);
  const out = new Map<string, Vec2>();
  const seeds: Vec2[] = list.map((n) => seedPos(n, cols, rows));
  const pos: Vec2[] = seeds.map((p): Vec2 => [p[0], p[1]]);
  list.forEach((n, i) => out.set(n.key, pos[i]));
  if (list.length < 3) return out;

  const index = new Map<string, number>();
  list.forEach((n, i) => index.set(n.key, i));

  type Spring = { a: number; b: number; w: number };
  const springs: Spring[] = [];
  const add = (src: string, dst: string, w: number) => {
    const a = index.get(src);
    const b = index.get(dst);
    if (a === undefined || b === undefined || a === b) return;
    springs.push({ a, b, w });
  };
  for (const s of similar) add(s.src_key, s.dst_key, clamp(s.score, 0, 1) * 0.9);
  for (const e of edges) add(e.src_key, e.dst_key, 1.6);
  if (springs.length === 0) return out; // nothing to cluster on: keep the seeds

  const n = list.length;
  const W = Math.max(1, cols);
  const H = Math.max(1, rows * 2);
  const k = Math.sqrt((W * H) / n);
  const reach = k * CUTOFF;
  const reach2 = reach * reach;
  const disp = new Float64Array(n * 2);
  const total = ITERS + SETTLE;

  for (let it = 0; it < total; it++) {
    disp.fill(0);
    for (let i = 0; i < n; i++) {
      const pi = pos[i];
      for (let j = i + 1; j < n; j++) {
        const pj = pos[j];
        const dx = pi[0] - pj[0];
        const dy = pi[1] - pj[1];
        const dd = dx * dx + dy * dy;
        if (dd > reach2) continue; // far pairs already read as separate clusters
        const d = Math.sqrt(dd) || 1e-4;
        const f = ((k * k) / d) * REPULSION;
        const ux = (dx / d) * f;
        const uy = (dy / d) * f;
        disp[i * 2] += ux;
        disp[i * 2 + 1] += uy;
        disp[j * 2] -= ux;
        disp[j * 2 + 1] -= uy;
      }
    }
    for (const s of springs) {
      const pa = pos[s.a];
      const pb = pos[s.b];
      const dx = pa[0] - pb[0];
      const dy = pa[1] - pb[1];
      const d = Math.sqrt(dx * dx + dy * dy) || 1e-4;
      const f = ((d * d) / k) * s.w * PULL;
      const ux = (dx / d) * f;
      const uy = (dy / d) * f;
      disp[s.a * 2] -= ux;
      disp[s.a * 2 + 1] -= uy;
      disp[s.b * 2] += ux;
      disp[s.b * 2 + 1] += uy;
    }
    for (let i = 0; i < n; i++) {
      disp[i * 2] += (seeds[i][0] - pos[i][0]) * ANCHOR;
      disp[i * 2 + 1] += (seeds[i][1] - pos[i][1]) * ANCHOR;
    }

    // Linear cool-down; the settle passes move almost nothing.
    const temp = it < ITERS ? k * 0.6 * (1 - it / ITERS) + 0.02 : 0.4;
    for (let i = 0; i < n; i++) {
      const dx = disp[i * 2];
      const dy = disp[i * 2 + 1];
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < 1e-9) continue;
      const step = Math.min(d, temp) / d;
      pos[i] = [
        clamp(pos[i][0] + dx * step, 1, W - 1),
        clamp(pos[i][1] + dy * step, 2, H - 2),
      ];
    }
  }

  // Normalise the settled cloud onto the full grid: a per-axis min/max fit, so
  // the picture covers the pane instead of sitting in a centred ellipse. One
  // cell of padding each side keeps glyphs and labels off the frame edge.
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const [x, y] of pos) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  const x0 = 1;
  const x1 = Math.max(1, cols - 2);
  const y0 = 2;
  const y1 = Math.max(2, (rows - 2) * 2);
  const sx = maxX - minX > 1e-6 ? (x1 - x0) / (maxX - minX) : 0;
  const sy = maxY - minY > 1e-6 ? (y1 - y0) / (maxY - minY) : 0;
  for (let i = 0; i < n; i++) {
    pos[i] = [
      sx ? x0 + (pos[i][0] - minX) * sx : (cols - 1) / 2,
      sy ? y0 + (pos[i][1] - minY) * sy : rows - 1,
    ];
  }

  list.forEach((node, i) => out.set(node.key, pos[i]));
  return out;
}

let memo: { sig: string; pos: Map<string, Vec2> } | null = null;

/**
 * Payload signature: node count, edge/link counts, the keys themselves and the
 * pane size. `phase`, `focus` and glyphs never enter it.
 */
function payloadSig(
  list: AsciiNode[],
  edges: AsciiEdge[],
  similar: AsciiLink[],
  cols: number,
  rows: number,
): string {
  let h = 7;
  for (const n of list) h = (Math.imul(h, 31) + hashKey(n.key)) | 0;
  return `${list.length}:${edges.length}:${similar.length}:${cols}x${rows}:${h}`;
}

/** One relaxation per payload; every later frame reuses the cells. */
function cachedLayout(
  list: AsciiNode[],
  edges: AsciiEdge[],
  similar: AsciiLink[],
  cols: number,
  rows: number,
): Map<string, Vec2> {
  const sig = payloadSig(list, edges, similar, cols, rows);
  if (memo && memo.sig === sig) return memo.pos;
  memo = { sig, pos: layout(list, edges, similar, cols, rows) };
  return memo.pos;
}

export type Projected = { col: number; row: number };

/** World cells -> grid cell. `y` is halved because a cell is about 2:1. */
export function cellOf(p: Vec2, cols: number, rows: number): Projected {
  return {
    col: clampInt(Math.round(p[0]), 0, cols - 1),
    row: clampInt(Math.round(p[1] / 2), 0, rows - 1),
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function clampInt(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}


// --- DEV guards ------------------------------------------------------------

const warned = new Set<string>();

function devWarn(kind: string, msg: string): void {
  const env = (import.meta as unknown as { env?: { DEV?: boolean } }).env;
  if (!env?.DEV || warned.has(kind)) return;
  warned.add(kind);
  console.warn(`[ascii/grid] ${kind}: ${msg}`);
}

// --- cell buffer -----------------------------------------------------------

class CellBuf {
  readonly cols: number;
  readonly rows: number;
  private ch: string[];
  private role: Role[];
  private taken: Uint8Array;

  constructor(cols: number, rows: number) {
    this.cols = cols;
    this.rows = rows;
    const n = cols * rows;
    this.ch = new Array(n).fill(' ');
    this.role = new Array(n).fill('ink');
    this.taken = new Uint8Array(n);
  }

  private idx(col: number, row: number): number {
    return row * this.cols + col;
  }

  private inside(col: number, row: number, kind: string): boolean {
    if (!Number.isFinite(col) || !Number.isFinite(row)) {
      devWarn(kind, 'non-finite cell coordinate');
      return false;
    }
    if (col < 0 || row < 0 || col >= this.cols || row >= this.rows) {
      devWarn(kind, 'cell index out of bounds');
      return false;
    }
    return true;
  }

  /** Unconditional write (backdrop, then nodes last). */
  set(col: number, row: number, ch: string, role: Role): void {
    if (!this.inside(col, row, 'coord')) return;
    const i = this.idx(col, row);
    this.ch[i] = ch;
    this.role[i] = role;
  }

  /** Occupy-once write for lines. First writer wins; false if the cell is gone. */
  claim(col: number, row: number, ch: string, role: Role): boolean {
    if (!this.inside(col, row, 'coord')) return false;
    const i = this.idx(col, row);
    if (this.taken[i]) return false;
    this.taken[i] = 1;
    this.ch[i] = ch;
    this.role[i] = role;
    return true;
  }

  /** Upgrade an already-claimed line cell to a junction. */
  cross(col: number, row: number): boolean {
    if (!this.inside(col, row, 'coord')) return false;
    const i = this.idx(col, row);
    if (!LINE_GLYPHS.includes(this.ch[i])) return false;
    this.ch[i] = '+';
    return true;
  }

  /**
   * Render one frame. `phase` moves the brightness band only: the band is a
   * diagonal stripe `col + row*2`, and cells within `BAND_HALF` of it are
   * lifted (`faint` -> `amber`, everything else -> `ink`). Glyphs and
   * positions never depend on it, so the stripped frame is identical at every
   * phase.
   */
  render(phase: number): { text: string; overlays: { role: Role; text: string }[] } {
    const span = this.cols + this.rows * 2;
    const band = (((phase * 12) % span) + span) % span;
    const n = this.cols * this.rows;
    const eff: Role[] = new Array(n);
    const used = new Set<Role>();
    for (let i = 0; i < n; i++) {
      const col = i % this.cols;
      const row = (i - col) / this.cols;
      let d = Math.abs(col + row * 2 - band);
      if (d > span / 2) d = span - d;
      const r = this.role[i];
      eff[i] = d > BAND_HALF ? r : r === 'faint' ? 'amber' : 'ink';
      used.add(eff[i]);
    }

    const lines: string[] = new Array(this.rows);
    const active = OVERLAY_ROLES.filter((r) => used.has(r));
    const layers = new Map<Role, string[]>();
    for (const r of active) layers.set(r, new Array(this.rows));

    for (let row = 0; row < this.rows; row++) {
      const line: string[] = new Array(this.cols);
      const parts = new Map<Role, string[]>();
      for (const r of active) parts.set(r, new Array(this.cols));
      for (let col = 0; col < this.cols; col++) {
        const i = row * this.cols + col;
        const ch = this.ch[i];
        const role = eff[i];
        line[col] = ch;
        for (const r of active) parts.get(r)![col] = role === r ? ch : ' ';
      }
      lines[row] = line.join('');
      for (const r of active) layers.get(r)![row] = parts.get(r)!.join('');
    }

    return {
      text: lines.join('\n'),
      overlays: active.map((role) => ({ role, text: layers.get(role)!.join('\n') })),
    };
  }
}

// --- glyphs ----------------------------------------------------------------

function nodeRole(n: AsciiNode): Role {
  if (n.status === 'superseded') return 'faint';
  return TYPE_ROLE[n.type] ?? 'muted';
}

/**
 * Degree-sized glyph: a hub is obvious before any line is read.
 * Hubs (degree >= 4) are a 2x2 block of `█`, drawn by `drawNodes`.
 */
function nodeGlyph(n: AsciiNode): string {
  if (n.degree >= 2) return '●';
  if (n.degree >= 1) return '•';
  return '·';
}

/** Label text: the tail of the key after the last dot, or the key itself. */
function labelOf(key: string): string {
  const dot = key.lastIndexOf('.');
  return dot >= 0 && dot < key.length - 1 ? key.slice(dot + 1) : key;
}

// --- drawing ---------------------------------------------------------------

/**
 * Straight segment: one glyph per step, horizontal `-`, vertical `|`, diagonal
 * `/` `\`, and `+` where the cell already holds a line. Returns cells inked.
 */
function drawLine(buf: CellBuf, a: Projected, b: Projected, role: Role, step: number): number {
  const dx = b.col - a.col;
  const dy = b.row - a.row;
  const len = Math.max(Math.abs(dx), Math.abs(dy));
  if (len < MIN_EDGE_LEN) return 0;
  let written = 0;
  let pc = a.col;
  let pr = a.row;
  for (let i = 1; i < len; i += step) {
    const t = i / len;
    const col = Math.round(a.col + dx * t);
    const row = Math.round(a.row + dy * t);
    if (col === pc && row === pr) continue;
    const ddx = col - pc;
    const drow = row - pr;
    pc = col;
    pr = row;
    const ch = drow === 0 ? '-' : ddx === 0 ? '|' : ddx > 0 === drow > 0 ? '\\' : '/';
    if (buf.claim(col, row, ch, role)) written++;
    else if (buf.cross(col, row)) written++;
  }
  return written;
}

/**
 * Static, hash-seeded texture drawn under the data when there are too few real
 * edges to be a picture. It is NOT data: it never varies with `edges` or
 * `phase`, and it does not claim cells, so real strands draw over it.
 */
function drawBackdrop(buf: CellBuf, cols: number, rows: number): void {
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      if (col % 12 === 0) {
        if (row % 2 === 0) buf.set(col, row, ':', 'faint');
        continue;
      }
      if (cellHash(col, row) % 29 === 0) buf.set(col, row, '.', 'faint');
    }
  }
}

const EMPTY_FIELD = 44;

/** Deterministic stand-in so an empty graph still breathes. */
function synthField(): AsciiNode[] {
  const out: AsciiNode[] = [];
  for (let i = 0; i < EMPTY_FIELD; i++) {
    const r = rng(hashKey(`field:${i}`));
    out.push({
      key: `field:${i}`,
      type: 'field',
      status: 'current',
      uses: Math.floor(r() * 12),
      score: r(),
      degree: Math.floor(r() * 6),
    });
  }
  return out;
}

function selectNodes(nodes: AsciiNode[]): AsciiNode[] {
  if (nodes.length <= MAX_NODES) return nodes;
  return [...nodes].sort((a, b) => b.degree - a.degree || b.uses - a.uses).slice(0, MAX_NODES);
}

function dimLine(count: number, cols: number): string {
  const t = count > 0 ? `memo graph - ${count} nodes (grid too small)` : 'memo graph - no data';
  return t.slice(0, Math.max(0, cols));
}

/**
 * Nearest free cell: rings of cells around `p`, closest first, so a cluster
 * that lands on one cell still shows three glyphs instead of one.
 */
const SPIRAL: [number, number][] = (() => {
  const out: [number, number][] = [];
  for (let r = 1; r <= 3; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        out.push([dx, dy]);
      }
    }
  }
  // insertion order breaks ties: the ordering above is already deterministic
  return out.sort((a, b) => a[0] * a[0] + a[1] * a[1] - (b[0] * b[0] + b[1] * b[1]));
})();

function place(taken: Set<number>, p: Projected, cols: number, rows: number): Projected {
  if (!taken.has(p.row * cols + p.col)) return p;
  for (const [dx, dy] of SPIRAL) {
    const col = p.col + dx;
    const row = p.row + dy;
    if (col < 0 || row < 0 || col >= cols || row >= rows) continue;
    if (taken.has(row * cols + col)) continue;
    return { col, row };
  }
  return p; // every ring taken: the node keeps the shared cell
}

export type FrameOptions = {
  /** Cosine neighbour pairs; faint strands under the declared edges. */
  similar?: AsciiLink[];
  /** A memory key: draw it and its neighbours, dim the rest. */
  focus?: string | null;
  /** Precomputed `layout()`; recomputed here only when omitted. */
  pos?: Map<string, Vec2>;
};

/** One frame. Pure: identical args -> identical output. */
export function buildFrame(
  nodes: AsciiNode[],
  edges: AsciiEdge[],
  cols: number,
  rows: number,
  phase: number,
  opts: FrameOptions = {},
): Frame {
  if (!Number.isFinite(cols) || !Number.isFinite(rows) || cols < 8 || rows < 4) {
    return {
      text: dimLine(nodes.length, Math.max(1, cols)),
      overlays: [],
      hits: [],
      dim: true,
      sparse: false,
    };
  }

  const selected = selectNodes(nodes);
  const synthetic = selected.length === 0;
  const list = synthetic ? synthField() : selected;
  const present = new Set(list.map((n) => n.key));
  const similar = synthetic ? [] : (opts.similar ?? []);

  const lights = new Set<string>();
  if (!synthetic && opts.focus && present.has(opts.focus)) {
    lights.add(opts.focus);
    for (const e of edges) {
      if (e.src_key === opts.focus) lights.add(e.dst_key);
      else if (e.dst_key === opts.focus) lights.add(e.src_key);
    }
    for (const s of similar) {
      if (s.src_key === opts.focus) lights.add(s.dst_key);
      else if (s.dst_key === opts.focus) lights.add(s.src_key);
    }
  }
  const focused = lights.size > 0;
  const near = (key: string): boolean => !focused || lights.has(key);

  const points = opts.pos ?? cachedLayout(list, edges, similar, cols, rows);

  const raw = list.map((n) => cellOf(points.get(n.key) ?? seedPos(n, cols, rows), cols, rows));

  // Cells, nudged off each other so a cluster never hides a node.
  const taken = new Set<number>();
  const pos = new Map<string, Projected>();
  list.forEach((n, i) => {
    const cell = place(taken, raw[i], cols, rows);
    taken.add(cell.row * cols + cell.col);
    pos.set(n.key, cell);
  });

  const ordered = list
    .map((n) => ({ n, p: pos.get(n.key)! }))
    .sort((a, b) => a.n.degree - b.n.degree);

  const buf = new CellBuf(cols, rows);

  const strand = (src: string, dst: string) => {
    const a = pos.get(src);
    const b = pos.get(dst);
    if (!a || !b) return null;
    return { a, b, len: Math.max(Math.abs(b.col - a.col), Math.abs(b.row - a.row)) };
  };

  type Strand = { a: Projected; b: Projected; len: number };
  /** A line across the frame is a scratch, shorter than 3 cells is noise. */
  const maxLen = rows * LINE_RATIO;
  const fits = (s: { len: number }, max = maxLen) => s.len >= MIN_EDGE_LEN && s.len <= max;

  // Declared edges that survive selection and focus, long enough to read.
  const edgeStrands: (Strand & { role: Role })[] = edges
    .filter((e) => present.has(e.src_key) && present.has(e.dst_key))
    .filter((e) => near(e.src_key) && near(e.dst_key))
    .map((e) => {
      const s = strand(e.src_key, e.dst_key);
      return s ? { ...s, role: REL_ROLE[e.rel] ?? 'faint' } : null;
    })
    .filter((s): s is Strand & { role: Role } => s !== null && fits(s));

  // Similarity pass: ranked by score, undirected pairs count once, each node
  // keeps at most its top-2, and only local, short strands survive.
  const linkStrands: Strand[] = [];
  {
    const seen = new Set<string>();
    const perNode = new Map<string, number>();
    const ranked = [...similar]
      .filter((l) => present.has(l.src_key) && present.has(l.dst_key))
      .filter((l) => near(l.src_key) && near(l.dst_key))
      .sort((x, y) => y.score - x.score);
    for (const l of ranked) {
      const pair = l.src_key < l.dst_key ? `${l.src_key}>${l.dst_key}` : `${l.dst_key}>${l.src_key}`;
      if (seen.has(pair)) continue;
      if ((perNode.get(l.src_key) ?? 0) >= SIMILAR_PER_NODE) continue;
      if ((perNode.get(l.dst_key) ?? 0) >= SIMILAR_PER_NODE) continue;
      const s = strand(l.src_key, l.dst_key);
      if (!s || !fits(s, rows)) continue; // only genuinely local pairs
      seen.add(pair);
      perNode.set(l.src_key, (perNode.get(l.src_key) ?? 0) + 1);
      perNode.set(l.dst_key, (perNode.get(l.dst_key) ?? 0) + 1);
      linkStrands.push(s);
    }
  }

  // `sparse` is honest: the backdrop only appears when nothing at all survives.
  const sparse = edgeStrands.length === 0 && linkStrands.length === 0;
  if (sparse) drawBackdrop(buf, cols, rows);

  // Ink hierarchy: lines first, so a node or a label always wins the cell.
  for (const s of linkStrands) drawLine(buf, s.a, s.b, 'faint', SIMILAR_STEP);
  for (const s of edgeStrands) drawLine(buf, s.a, s.b, s.role, 1);

  // Nodes: a hub is a 2x2 block (all four cells claimed, else a single `●`),
  // everybody else one glyph sized by degree. Node cells never collide.
  const occupied = new Set<number>();
  const isHub = (n: AsciiNode): boolean => n.degree >= 4;
  for (const { n, p } of ordered) {
    const lit = near(n.key);
    const ch = lit ? nodeGlyph(n) : '.';
    const role = lit ? nodeRole(n) : 'faint';
    let block = false;
    if (lit && isHub(n) && p.col + 1 < cols && p.row + 1 < rows) {
      block = true;
      for (const [dc, dr] of [
        [1, 0],
        [0, 1],
        [1, 1],
      ]) {
        if (occupied.has((p.row + dr) * cols + p.col + dc)) {
          block = false;
          break;
        }
      }
    }
    if (block) {
      for (const [dc, dr] of [
        [0, 0],
        [1, 0],
        [0, 1],
        [1, 1],
      ]) {
        buf.set(p.col + dc, p.row + dr, '█', role);
        occupied.add((p.row + dr) * cols + p.col + dc);
      }
    } else {
      buf.set(p.col, p.row, ch, role);
      occupied.add(p.row * cols + p.col);
    }
  }

  // Labels on hubs: one row, one space to the right of the glyph, faint so
  // they never compete with the dots. A label is claimed whole or skipped
  // whole — no half words.
  const hubs = ordered
    .filter(({ n }) => n.degree >= LABEL_DEGREE && near(n.key))
    .sort((a, b) => b.n.degree - a.n.degree || (a.n.key < b.n.key ? -1 : 1));
  for (const { n, p } of hubs) {
    const start = p.col + (isHub(n) ? 3 : 2);
    const room = cols - start;
    if (room < 2) continue;
    const text = labelOf(n.key).slice(0, room);
    if (!text) continue;
    let ok = true;
    for (let i = 0; i < text.length; i++) {
      const i2 = p.row * cols + start + i;
      if (occupied.has(i2)) {
        ok = false;
        break;
      }
    }
    if (!ok) continue;
    if (!occupied.has(p.row * cols + start - 1)) buf.set(start - 1, p.row, ' ', 'faint');
    for (let i = 0; i < text.length; i++) {
      buf.set(start + i, p.row, text[i], 'faint');
      occupied.add(p.row * cols + start + i);
    }
  }

  const { text, overlays } = buf.render(phase);
  const hits: HitBox[] = synthetic
    ? []
    : ordered.map(({ n, p }) => ({ key: n.key, col: p.col, row: p.row }));

  return { text, overlays, hits, dim: false, sparse };
}








