// Pure, dependency-free rasteriser for the memory-graph ASCII stage.
// No React, no imports. Node positions come from `layout()` (similarity
// relaxation over hash seeds), so the same payload always gives the same
// picture; only the caller's `angle` moves.

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

export type Vec3 = [number, number, number];

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

const TYPE_GLYPH: Record<string, string> = {
  preference: 'P',
  analysis: 'A',
  feedback: 'F',
  context: 'C',
};

const REL_ROLE: Record<string, Role> = {
  mentions: 'faint',
  supports: 'green',
  contradicts: 'coral',
  derived_from: 'violet',
  // similarity strands: texture, not a declared relation
  similar: 'faint',
};

const MAX_NODES = 200;
/** Shortest projected edge worth drawing, in cells. */
const MIN_EDGE_LEN = 3;
/**
 * Similarity strands are local texture: each node keeps at most its top-2
 * links by score (undirected pairs count once), and a strand longer than the
 * row height (STRAND_RATIO x rows) is dropped: it crosses the frame and
 * reads as noise.
 */
const SIMILAR_PER_NODE = 2;
/** Longest allowed strand, as a multiple of the row height. */
const STRAND_RATIO = 1.2;
/**
 * Attraction per link. Every pair repels (n^2 pairs, scaled by REPULSION) and
 * each link pulls with weight x PULL; REPULSION is the tune's lever that keeps
 * clusters 3-8 cells wide instead of 20. Declared edges stay the strongest
 * relation (weight 1, similarity <= its score).
 */
const PULL = 16;
const REPULSION = 0.3;
/**
 * One ring of orthogonal cells around a node glyph, claimed as faint dust.
 * Each node claims the first 2 halo cells it finds free: enough that a cluster
 * reads as a blob, few enough that strands stay subordinate to nodes.
 */
const HALO_MAX = 2;
const HALO: [number, number][] = [
  [-1, 0],
  [1, 0],
  [0, -1],
  [0, 1],
];

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

/** Stable point on the unit sphere, in [-1,1]^3. Same key -> same point, always. */
export function seedPos(key: string): [number, number, number] {
  const r = rng(hashKey(key));
  const z = r() * 2 - 1;
  const t = r() * Math.PI * 2;
  const s = Math.sqrt(Math.max(0, 1 - z * z));
  return [Math.cos(t) * s, z, Math.sin(t) * s];
}

// --- layout ----------------------------------------------------------------

/** Relaxation iterations: the cost is O(n^2) per pass, so it is capped twice. */
const ITER_SMALL = 220;
const ITER_LARGE = 120;

/**
 * Unit-sphere layout that follows the data.
 *
 * Seeds from `seedPos` (deterministic), then runs Fruchterman-Reingold:
 * every pair repels, one link per `similar` pair (weight `score`) and per
 * explicit edge (weight 1) attracts, and each pass renormalises back onto the
 * sphere so `project()` keeps working. No clock, no `Math.random`: the same
 * payload always gives the same points.
 *
 * With no `similar` pairs there is nothing to cluster on, so the hash seeds are
 * returned untouched and the stage looks exactly as it did before.
 *
 * Call it once per graph payload and pass the result to `buildFrame`.
 */
export function layout(
  nodes: AsciiNode[],
  edges: AsciiEdge[],
  similar: AsciiLink[] = [],
): Map<string, Vec3> {
  const list = selectNodes(nodes);
  const out = new Map<string, Vec3>();
  const index = new Map<string, number>();
  list.forEach((n, i) => index.set(n.key, i));
  const pos: Vec3[] = list.map((n) => seedPos(n.key));

  if (similar.length === 0 || list.length < 3) {
    list.forEach((n, i) => out.set(n.key, pos[i]));
    return out;
  }

  type Link = { a: number; b: number; w: number };
  const links: Link[] = [];
  const add = (src: string, dst: string, w: number) => {
    const a = index.get(src);
    const b = index.get(dst);
    if (a === undefined || b === undefined || a === b) return;
    links.push({ a, b, w });
  };
  for (const s of similar) add(s.src_key, s.dst_key, clamp(s.score, 0, 1));
  for (const e of edges) add(e.src_key, e.dst_key, 1);

  const n = list.length;
  const k = 1.6 / Math.sqrt(n);
  const iters = n <= 140 ? ITER_SMALL : ITER_LARGE;
  const disp = new Float64Array(n * 3);

  for (let it = 0; it < iters; it++) {
    disp.fill(0);
    for (let i = 0; i < n; i++) {
      const pi = pos[i];
      for (let j = i + 1; j < n; j++) {
        const pj = pos[j];
        const dx = pi[0] - pj[0];
        const dy = pi[1] - pj[1];
        const dz = pi[2] - pj[2];
        const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-4;
        const f = ((k * k) / d) * REPULSION; // repulsion
        const ux = (dx / d) * f;
        const uy = (dy / d) * f;
        const uz = (dz / d) * f;
        disp[i * 3] += ux;
        disp[i * 3 + 1] += uy;
        disp[i * 3 + 2] += uz;
        disp[j * 3] -= ux;
        disp[j * 3 + 1] -= uy;
        disp[j * 3 + 2] -= uz;
      }
    }
    for (const l of links) {
      const pa = pos[l.a];
      const pb = pos[l.b];
      const dx = pa[0] - pb[0];
      const dy = pa[1] - pb[1];
      const dz = pa[2] - pb[2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-4;
      const f = ((d * d) / k) * l.w * PULL; // attraction
      const ux = (dx / d) * f;
      const uy = (dy / d) * f;
      const uz = (dz / d) * f;
      disp[l.a * 3] -= ux;
      disp[l.a * 3 + 1] -= uy;
      disp[l.a * 3 + 2] -= uz;
      disp[l.b * 3] += ux;
      disp[l.b * 3 + 1] += uy;
      disp[l.b * 3 + 2] += uz;
    }

    const temp = 0.09 * (1 - it / iters) + 0.004;
    for (let i = 0; i < n; i++) {
      const dx = disp[i * 3];
      const dy = disp[i * 3 + 1];
      const dz = disp[i * 3 + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (d < 1e-9) continue;
      const step = Math.min(d, temp) / d;
      let x = pos[i][0] + dx * step;
      let y = pos[i][1] + dy * step;
      let z = pos[i][2] + dz * step;
      const len = Math.sqrt(x * x + y * y + z * z);
      if (len < 1e-9) {
        pos[i] = seedPos(list[i].key);
        continue;
      }
      x /= len;
      y /= len;
      z /= len;
      pos[i] = [x, y, z];
    }
  }

  list.forEach((node, i) => out.set(node.key, pos[i]));
  return out;
}

export type Projected = { col: number; row: number; depth: number };

/** Cheap sphere -> grid projection. `depth` is -1 (far) .. 1 (near). */
export function project(
  p: readonly [number, number, number],
  angle: number,
  cols: number,
  rows: number,
): Projected {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const x = p[0] * c - p[2] * s;
  const z = p[0] * s + p[2] * c;
  const persp = 1 / (2.3 - z * 0.85);
  const sx = clamp(x * persp * 2.9, -1, 1);
  const sy = clamp(p[1] * persp * 2.1, -1, 1);
  return {
    col: clampInt(Math.round(((cols - 1) / 2) * (1 + sx)), 0, cols - 1),
    row: clampInt(Math.round(((rows - 1) / 2) * (1 + sy)), 0, rows - 1),
    depth: z,
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function clampInt(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Middle value of an unsorted list (averaged pair at even length). */
function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
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
  private used = new Set<Role>();

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
    this.used.add(role);
  }

  /** Mark a cell as spoken for without writing it (node cells, before edges). */
  reserve(col: number, row: number): void {
    if (!this.inside(col, row, 'coord')) return;
    this.taken[this.idx(col, row)] = 1;
  }

  /** Occupy-once write for edges. First writer wins; false if the cell is gone. */
  claim(col: number, row: number, ch: string, role: Role): boolean {
    if (!this.inside(col, row, 'coord')) return false;
    const i = this.idx(col, row);
    if (this.taken[i]) return false;
    this.taken[i] = 1;
    this.ch[i] = ch;
    this.role[i] = role;
    this.used.add(role);
    return true;
  }

  render(): { text: string; overlays: { role: Role; text: string }[] } {
    const lines: string[] = new Array(this.rows);
    const active = OVERLAY_ROLES.filter((r) => this.used.has(r));
    const layers = new Map<Role, string[]>();
    for (const r of active) layers.set(r, new Array(this.rows));

    for (let row = 0; row < this.rows; row++) {
      const line: string[] = new Array(this.cols);
      const parts = new Map<Role, string[]>();
      for (const r of active) parts.set(r, new Array(this.cols));
      for (let col = 0; col < this.cols; col++) {
        const i = row * this.cols + col;
        const ch = this.ch[i];
        const role = this.role[i];
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
 * How loudly a node should read: degree leads, then uses, then score. Kept in
 * [0,1] so the sub-letter tiers below stay visibly graded even when the graph
 * is sparse and most degrees are small.
 */
function prominence(n: AsciiNode): number {
  const d = Math.min(1, n.degree / 6);
  const u = Math.min(1, Math.log1p(Math.max(0, n.uses)) / Math.log1p(50));
  const s = clamp(n.score, 0, 1);
  return 0.55 * d + 0.3 * u + 0.15 * s;
}

function nodeGlyph(n: AsciiNode): string {
  if (n.degree >= 3) return TYPE_GLYPH[n.type] ?? '@';
  const p = prominence(n);
  if (p >= 0.55) return 'o';
  if (p >= 0.3) return '+';
  return '.';
}

function edgeGlyph(dx: number, dy: number): string {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax >= ay * 1.8) return '-';
  if (ay >= ax * 1.8) return '|';
  return (dx > 0) === (dy > 0) ? '\\' : '/';
}

// --- drawing ---------------------------------------------------------------

/** Returns the number of cells this edge actually claimed. */
function drawEdge(
  buf: CellBuf,
  a: Projected,
  b: Projected,
  e: AsciiEdge,
): number {
  const dx = b.col - a.col;
  const dy = b.row - a.row;
  const len = Math.max(Math.abs(dx), Math.abs(dy));
  if (len < MIN_EDGE_LEN) return 0;
  const role = REL_ROLE[e.rel] ?? 'faint';
  const contradicts = e.rel === 'contradicts';
  // Heavy edges are continuous backbone; light ones are dust. A strand that
  // cannot overwrite anything stays a strand instead of becoming fill.
  const solid = e.weight >= 1;
  const step = solid ? 1 : e.weight >= 0.5 ? 2 : 3;
  const glyph = solid || contradicts ? edgeGlyph(dx, dy) : '.';
  let written = 0;
  for (let i = 0; i <= len; i += step) {
    const t = i / len;
    const col = Math.round(a.col + dx * t);
    const row = Math.round(a.row + dy * t);
    const ch = contradicts && i % 4 === 0 ? 'x' : glyph;
    if (buf.claim(col, row, ch, contradicts ? 'coral' : role)) written++;
  }
  return written;
}

/**
 * Static, hash-seeded texture drawn under the data when there are too few real
 * edges to be a picture. It is NOT data: it never varies with `edges` or
 * `angle`, and it does not claim cells, so real strands draw over it.
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
      degree: Math.floor(r() * 5),
    });
  }
  return out;
}

function selectNodes(nodes: AsciiNode[]): AsciiNode[] {
  if (nodes.length <= MAX_NODES) return nodes;
  return [...nodes]
    .sort((a, b) => b.degree - a.degree || b.uses - a.uses)
    .slice(0, MAX_NODES);
}

function dimLine(count: number, cols: number): string {
  const t =
    count > 0
      ? `memo graph - ${count} nodes (grid too small)`
      : 'memo graph - no data';
  return t.slice(0, Math.max(0, cols));
}

/**
 * Nearest free cell: rings of cells around `p`, closest first, so a cluster
 * that projects onto one cell still shows three glyphs instead of one.
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

function place(
  taken: Set<number>,
  p: Projected,
  cols: number,
  rows: number,
): Projected {
  if (!taken.has(p.row * cols + p.col)) return p;
  for (const [dx, dy] of SPIRAL) {
    const col = p.col + dx;
    const row = p.row + dy;
    if (col < 0 || row < 0 || col >= cols || row >= rows) continue;
    if (taken.has(row * cols + col)) continue;
    return { col, row, depth: p.depth };
  }
  return p; // every ring taken: the node keeps the shared cell
}

/** A `similar` pair drawn as a strand: dotted dust, or solid when focused. */
function linkEdge(l: AsciiLink, solid: boolean): AsciiEdge {
  return {
    src_key: l.src_key,
    dst_key: l.dst_key,
    rel: 'similar',
    weight: solid ? 1 : 0.5,
  };
}

export type FrameOptions = {
  /** Cosine neighbour pairs; dotted strands under the declared edges. */
  similar?: AsciiLink[];
  /** A memory key: draw it and its neighbours, dim the rest. */
  focus?: string | null;
  /** Precomputed `layout()`; recomputed here only when omitted. */
  pos?: Map<string, Vec3>;
};

/** One frame. Pure: identical args -> identical output. */
export function buildFrame(
  nodes: AsciiNode[],
  edges: AsciiEdge[],
  cols: number,
  rows: number,
  angle: number,
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

  const points = opts.pos ?? layout(list, edges, similar);

  // Ink centring, two deterministic translations: the centre of mass goes to
  // the origin, then a median-cell translation puts the mass in the middle of
  // the pane. The median is robust: clamped outliers cannot drag it.
  const midC = (cols - 1) / 2;
  const midR = (rows - 1) / 2;
  const raw0 = list.map((n) => project(points.get(n.key) ?? seedPos(n.key), angle, cols, rows));
  const meanC = raw0.reduce((s, p) => s + p.col, 0) / raw0.length;
  const meanR = raw0.reduce((s, p) => s + p.row, 0) / raw0.length;
  const comMap = raw0.map((p) => ({
    col: Math.round(midC + p.col - meanC),
    row: Math.round(midR + p.row - meanR),
    depth: p.depth,
  }));
  const medC = median(comMap.map((p) => p.col));
  const medR = median(comMap.map((p) => p.row));
  const shiftC = Math.round(midC - medC);
  const shiftR = Math.round(midR - medR);
  const raw = comMap.map((p) => ({
    col: clampInt(p.col + shiftC, 0, cols - 1),
    row: clampInt(p.row + shiftR, 0, cols - 1),
    depth: p.depth,
  }));

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
    .sort((a, b) => a.p.depth - b.p.depth || a.n.degree - b.n.degree);

  const buf = new CellBuf(cols, rows);

  const strand = (src: string, dst: string) => {
    const a = pos.get(src);
    const b = pos.get(dst);
    if (!a || !b) return null;
    return { a, b, len: Math.max(Math.abs(b.col - a.col), Math.abs(b.row - a.row)) };
  };

  type Strand = { e: AsciiEdge; a: Projected; b: Projected; len: number };
  /** A strand taller than the pane is a scratch, shorter than 3 cells is noise. */
  const maxLen = rows * STRAND_RATIO;
  const fits = (s: { len: number }) => s.len >= MIN_EDGE_LEN && s.len <= maxLen;

  // Declared edges that survive selection and focus, long enough to read.
  const edgeStrands = edges
    .filter((e) => present.has(e.src_key) && present.has(e.dst_key))
    .filter((e) => near(e.src_key) && near(e.dst_key))
    .map((e) => {
      const s = strand(e.src_key, e.dst_key);
      return s ? { e, ...s } : null;
    })
    .filter((s): s is Strand => s !== null && fits(s));

  // Similarity link pass: ranked by score, undirected pairs count once, each
  // node keeps at most its top-2, and only local, short strands survive.
  const linkStrands: (Strand & { score: number })[] = [];
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
      if (!s || !fits(s)) continue;
      seen.add(pair);
      perNode.set(l.src_key, (perNode.get(l.src_key) ?? 0) + 1);
      perNode.set(l.dst_key, (perNode.get(l.dst_key) ?? 0) + 1);
      linkStrands.push({ e: linkEdge(l, focused), score: l.score, ...s });
    }
  }

  // `sparse` is honest: the backdrop only appears when nothing at all survives.
  const sparse = edgeStrands.length === 0 && linkStrands.length === 0;
  if (sparse) drawBackdrop(buf, cols, rows);

  // Nodes claim their cells, then write their glyphs, then claim a 1-cell
  // halo; strands draw last and can never cross a glyph or a halo.
  for (const { p } of ordered) buf.reserve(p.col, p.row);
  for (const { n, p } of ordered) {
    const lit = near(n.key);
    buf.set(p.col, p.row, lit ? nodeGlyph(n) : '.', lit ? nodeRole(n) : 'faint');
  }
  for (const { p } of ordered) {
    let n = 0;
    for (const [dx, dy] of HALO) {
      if (n >= HALO_MAX) break;
      if (buf.claim(p.col + dx, p.row + dy, ':', 'faint')) n++;
    }
  }
  for (const s of edgeStrands) drawEdge(buf, s.a, s.b, s.e);
  for (const s of linkStrands) drawEdge(buf, s.a, s.b, s.e);

  const { text, overlays } = buf.render();
  const hits: HitBox[] = synthetic
    ? []
    : ordered.map(({ n, p }) => ({ key: n.key, col: p.col, row: p.row }));

  return { text, overlays, hits, dim: false, sparse };
}
