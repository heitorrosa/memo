
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

export type AsciiLink = {
  src_key: string;
  dst_key: string;
  score: number;
};

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
  text: string;
  overlays: { role: Role; text: string }[];
  hits: HitBox[];
  dim: boolean;
  sparse: boolean;
};

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
const MIN_EDGE_LEN = 3;
const SIMILAR_STEP = 2;
const STRAND_RATIO = 0.045;

function strandMax(cols: number, rows: number): number {
  return Math.max(rows, cols / 6);
}

function labelMax(cols: number, rows: number): number {
  return clampInt(Math.round(Math.min(cols / 10, rows * 1.1)), 6, 24);
}

function labelCount(cols: number): number {
  return clampInt(Math.round(cols / 10), 8, 24);
}

const BAND_HALF = 10;

const LINE_GLYPHS = '-|/\\+';

export function hashKey(key: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

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

const QUADRANT: Record<string, [number, number]> = {
  preference: [0.25, 0.25],
  context: [0.75, 0.25],
  feedback: [0.25, 0.75],
  analysis: [0.75, 0.75],
};

export function seedPos(n: AsciiNode, cols: number, rows: number): Vec2 {
  const r = rng(hashKey(n.key));
  const [qx, qy] = QUADRANT[n.type] ?? [0.5, 0.5];
  return [(qx + (r() - 0.5) * 0.5) * cols, (qy + (r() - 0.5) * 0.5) * rows * 2];
}

const ITERS = 250;
const SETTLE = 40;
const REPULSION = 1;
const PULL = 1.2;
const ANCHOR = 0.5;
const CUTOFF = 1.5;

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
  if (springs.length === 0) return out;

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
        if (dd > reach2) continue;
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

const warned = new Set<string>();

function devWarn(kind: string, msg: string): void {
  const env = (import.meta as unknown as { env?: { DEV?: boolean } }).env;
  if (!env?.DEV || warned.has(kind)) return;
  warned.add(kind);
  console.warn(`[ascii/grid] ${kind}: ${msg}`);
}

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

  set(col: number, row: number, ch: string, role: Role): void {
    if (!this.inside(col, row, 'coord')) return;
    const i = this.idx(col, row);
    this.ch[i] = ch;
    this.role[i] = role;
  }

  claim(col: number, row: number, ch: string, role: Role): boolean {
    if (!this.inside(col, row, 'coord')) return false;
    const i = this.idx(col, row);
    if (this.taken[i]) return false;
    this.taken[i] = 1;
    this.ch[i] = ch;
    this.role[i] = role;
    return true;
  }

  cross(col: number, row: number): boolean {
    if (!this.inside(col, row, 'coord')) return false;
    const i = this.idx(col, row);
    if (!LINE_GLYPHS.includes(this.ch[i])) return false;
    this.ch[i] = '+';
    return true;
  }

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

function nodeRole(n: AsciiNode): Role {
  if (n.status === 'superseded') return 'faint';
  return TYPE_ROLE[n.type] ?? 'muted';
}

function nodeGlyph(n: AsciiNode): string {
  if (n.degree >= 6) return '■';
  if (n.degree >= 3) return '●';
  if (n.degree >= 1) return '•';
  return '·';
}

function labelOf(key: string): string {
  const dot = key.lastIndexOf('.');
  return dot >= 0 && dot < key.length - 1 ? key.slice(dot + 1) : key;
}

function labelFor(key: string, cap: number): string | null {
  const tail = labelOf(key);
  if (tail.length <= cap) return tail;
  const head = tail.slice(0, cap);
  const cut = Math.max(head.lastIndexOf("-"), head.lastIndexOf(" "));
  if (cut >= 4) return head.slice(0, cut);
  const word = tail.split(/[-\s]+/).pop() ?? "";
  return word.length >= 4 && word.length <= cap ? word : null;
}

function drawLine(buf: CellBuf, a: Projected, b: Projected, role: Role, step: number, max = Infinity): number {
  const dx = b.col - a.col;
  const dy = b.row - a.row;
  const len = Math.max(Math.abs(dx), Math.abs(dy));
  if (len < MIN_EDGE_LEN) return 0;
  let written = 0;
  let pc = a.col;
  let pr = a.row;
  for (let i = 1; i < len; i += step) {
    if (written >= max) break;
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
  return p;
}

export type FrameOptions = {
  similar?: AsciiLink[];
  focus?: string | null;
  pos?: Map<string, Vec2>;
};

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
  const similar = synthetic ? [] : (opts.similar ?? []);

  const linked = new Map<string, number>();
  if (!synthetic) {
    for (const e of edges) {
      linked.set(e.src_key, (linked.get(e.src_key) ?? 0) + 1);
      linked.set(e.dst_key, (linked.get(e.dst_key) ?? 0) + 1);
    }
    for (const s of similar) {
      linked.set(s.src_key, (linked.get(s.src_key) ?? 0) + 1);
      linked.set(s.dst_key, (linked.get(s.dst_key) ?? 0) + 1);
    }
  }
  const list = (synthetic ? synthField() : selected).map((n) => ({
    ...n,
    degree: n.degree + (linked.get(n.key) ?? 0),
  }));
  const present = new Set(list.map((n) => n.key));

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
  const maxLen = strandMax(cols, rows);
  const fits = (s: { len: number }) => s.len >= MIN_EDGE_LEN && s.len <= maxLen;

  const edgeStrands: (Strand & { role: Role })[] = edges
    .filter((e) => present.has(e.src_key) && present.has(e.dst_key))
    .filter((e) => near(e.src_key) && near(e.dst_key))
    .map((e) => {
      const s = strand(e.src_key, e.dst_key);
      return s ? { ...s, role: REL_ROLE[e.rel] ?? 'faint' } : null;
    })
    .filter((s): s is Strand & { role: Role } => s !== null && fits(s));

  const linkStrands: Strand[] = [];
  {
    const seen = new Set<string>();
    const ranked = [...similar]
      .filter((l) => present.has(l.src_key) && present.has(l.dst_key))
      .filter((l) => near(l.src_key) && near(l.dst_key))
      .sort((x, y) => y.score - x.score);
    for (const l of ranked) {
      const pair = l.src_key < l.dst_key ? `${l.src_key}>${l.dst_key}` : `${l.dst_key}>${l.src_key}`;
      if (seen.has(pair)) continue;
      const s = strand(l.src_key, l.dst_key);
      if (!s || !fits(s)) continue;
      seen.add(pair);
      linkStrands.push(s);
    }
  }

  const hasLinks = edges.some((e) => present.has(e.src_key) && present.has(e.dst_key))
    || similar.some((s) => present.has(s.src_key) && present.has(s.dst_key));
  const sparse = synthetic || !hasLinks;

  const isHub = (n: AsciiNode): boolean => n.degree >= 6;
  const occupied = new Set<number>();
  const body = new Map<string, [number, number][]>();
  for (const { n, p } of ordered) {
    const cells: [number, number][] = [[p.col, p.row]];
    occupied.add(p.row * cols + p.col);
    body.set(n.key, cells);
  }
  for (const cells of body.values()) {
    for (const [c, r] of cells) buf.claim(c, r, ' ', 'ink');
  }

  if (sparse) drawBackdrop(buf, cols, rows);

  const budget = Math.floor(STRAND_RATIO * cols * rows);
  let spent = 0;
  for (const s of edgeStrands) spent += drawLine(buf, s.a, s.b, s.role, 1, budget - spent);
  for (const s of linkStrands) spent += drawLine(buf, s.a, s.b, 'faint', SIMILAR_STEP, budget - spent);

  for (const { n, p } of ordered) {
    const lit = near(n.key);
    const role = lit ? nodeRole(n) : 'faint';
    const glyph = nodeGlyph(n);
    buf.set(p.col, p.row, glyph, role);
  }

  const labelLen = labelMax(cols, rows);
  const labelFit = (text: string, p: Projected, gap: number, dir: number): number => {
    let len = 0;
    while (len < text.length) {
      const col = dir > 0 ? p.col + gap + len : p.col - gap - (len + 1);
      if (col < 0 || col >= cols || occupied.has(p.row * cols + col)) break;
      len++;
    }
    return len;
  };
  const labelled = list
    .filter((n) => near(n.key))
    .sort((a, b) => b.uses - a.uses || b.degree - a.degree || b.score - a.score || (a.key < b.key ? -1 : 1))
    .slice(0, labelCount(cols));
  for (const n of labelled) {
    const p = pos.get(n.key)!;
    const gap = isHub(n) ? 3 : 2;
    const text = labelFor(n.key, labelLen);
    if (!text) continue;
    const l = labelFit(text, p, gap, -1);
    const r = labelFit(text, p, gap, 1);
    if (Math.max(l, r) < text.length) continue;
    const fromLeft = l > r;
    const len = text.length;
    const start = fromLeft ? p.col - gap - len : p.col + gap;
    const sep = fromLeft ? start + len : start - 1;
    if (sep >= 0 && sep < cols && !occupied.has(p.row * cols + sep)) buf.set(sep, p.row, ' ', 'faint');
    for (let i = 0; i < len; i++) {
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
