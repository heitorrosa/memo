// Pure, dependency-free rasteriser for the memory-graph ASCII stage.
// No React, no imports. Every position derives from hash(key) so the same
// props always give the same layout; only the caller's `angle` moves.

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
};

const MAX_NODES = 200;
/** Real-edge count below which the frame gets the backdrop texture. */
const SPARSE_EDGES = 15;
/** Fraction of the grid that edge strands may claim. */
const EDGE_BUDGET = 0.2;
/** Shortest projected edge worth drawing, in cells. */
const MIN_EDGE_LEN = 3;

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
  budget: number,
): number {
  const dx = b.col - a.col;
  const dy = b.row - a.row;
  const len = Math.max(Math.abs(dx), Math.abs(dy));
  if (len < MIN_EDGE_LEN || budget <= 0) return 0;
  const role = REL_ROLE[e.rel] ?? 'faint';
  const contradicts = e.rel === 'contradicts';
  // Heavy edges are continuous backbone; light ones are dust. A strand that
  // cannot overwrite anything stays a strand instead of becoming fill.
  const solid = e.weight >= 1;
  const step = solid ? 1 : e.weight >= 0.5 ? 2 : 3;
  const glyph = solid || contradicts ? edgeGlyph(dx, dy) : '.';
  let written = 0;
  for (let i = 0; i <= len; i += step) {
    if (written >= budget) break;
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

/** One frame. Pure: identical args -> identical output. */
export function buildFrame(
  nodes: AsciiNode[],
  edges: AsciiEdge[],
  cols: number,
  rows: number,
  angle: number,
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

  const pos = new Map<string, Projected>();
  for (const n of list) pos.set(n.key, project(seedPos(n.key), angle, cols, rows));

  const ordered = list
    .map((n) => ({ n, p: pos.get(n.key)! }))
    .sort((a, b) => a.p.depth - b.p.depth || a.n.degree - b.n.degree);

  const buf = new CellBuf(cols, rows);

  // Real edges that survive selection, long enough to read, nearest-last.
  const strands = synthetic
    ? []
    : edges
        .filter((e) => present.has(e.src_key) && present.has(e.dst_key))
        .map((e) => {
          const a = pos.get(e.src_key)!;
          const b = pos.get(e.dst_key)!;
          return { e, a, b, len: Math.max(Math.abs(b.col - a.col), Math.abs(b.row - a.row)) };
        })
        .filter((s) => s.len >= MIN_EDGE_LEN);

  const sparse = strands.length < SPARSE_EDGES;
  if (sparse) drawBackdrop(buf, cols, rows);

  // Nodes first reserve their cells so no strand can run through a glyph.
  for (const { p } of ordered) buf.reserve(p.col, p.row);

  // Budget is spent on cells, not on the node count, and in weight order.
  const budget = Math.max(8, Math.round(cols * rows * EDGE_BUDGET));
  let spent = 0;
  const byWeight = [...strands].sort(
    (x, y) => y.e.weight - x.e.weight || x.a.depth + x.b.depth - (y.a.depth + y.b.depth),
  );
  for (const s of byWeight) {
    if (spent >= budget) break;
    spent += drawEdge(buf, s.a, s.b, s.e, budget - spent);
  }

  // Nodes last, unconditional: they always win their own cell.
  for (const { n, p } of ordered) buf.set(p.col, p.row, nodeGlyph(n), nodeRole(n));

  const { text, overlays } = buf.render();
  const hits: HitBox[] = synthetic
    ? []
    : ordered.map(({ n, p }) => ({ key: n.key, col: p.col, row: p.row }));

  return { text, overlays, hits, dim: false, sparse };
}
