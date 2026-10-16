import initSqlJs, { type Database } from "sql.js";
import wasmUrl from "sql.js/dist/sql-wasm.wasm?url";

export interface GraphNode {
  id: string;
  key: string;
  ns: string;
  kind: "memory" | "entity";
  type: string;
  status: string;
  uses: number;
  score: number;
  updatedAt: string;
  value: string;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  rel: string;
  weight: number;
  ns: string;
  label: string;
  dashed: boolean;
}

export interface GraphModel {
  nodes: GraphNode[];
  edges: GraphEdge[];
  namespaces: string[];
  note: string;
}

export const TYPE_COLORS: Record<string, string> = {
  preference: "#4c8bf5",
  analysis: "#9c6ade",
  feedback: "#e8833a",
  context: "#2fa36b",
};

export const REL_COLORS: Record<string, string> = {
  mentions: "#8a94a6",
  supports: "#2fa36b",
  contradicts: "#e04a4a",
  derived_from: "#9c6ade",
  same_as: "#6b7280",
};

export const DEFAULT_RELS = ["mentions", "supports", "contradicts", "derived_from"];
export const ALIAS_REL = "same_as";
export const SUPERSEDED_COLOR = "#9aa0a6";
export const ENTITY_COLOR = "#b8bec8";
export const NODE_CAP = 1500;

export function nodeColor(n: GraphNode): string {
  if (n.kind === "entity") return ENTITY_COLOR;
  if (n.status === "superseded") return SUPERSEDED_COLOR;
  return TYPE_COLORS[n.type] ?? "#6b7280";
}

type Row = Record<string, string | number | null>;

function query(db: Database, sql: string): Row[] {
  const res = db.exec(sql);
  if (!res.length) return [];
  const cols = res[0].columns;
  return res[0].values.map((v) => {
    const o: Row = {};
    cols.forEach((c, i) => {
      o[c] = v[i] as string | number | null;
    });
    return o;
  });
}

// Same tolerance trick as tools/graph-view.html: never assume a column exists.
function colSet(db: Database, table: string): Set<string> {
  return new Set(query(db, `SELECT name FROM pragma_table_info('${table}')`).map((r) => String(r.name)));
}

function tableSet(db: Database): Set<string> {
  return new Set(query(db, "SELECT name FROM sqlite_master WHERE type='table'").map((r) => String(r.name)));
}

function nodeId(ns: string, key: string): string {
  return ns + "\u0000" + key;
}

export function buildModel(db: Database): GraphModel {
  const tables = tableSet(db);
  if (!tables.has("memories")) {
    throw new Error(
      "no 'memories' table in this file - pick a copy of a memo database (data/memo.db).",
    );
  }

  const mc = colSet(db, "memories");
  const memSel = [
    '"key"',
    mc.has("namespace") ? '"namespace"' : "'default' AS namespace",
    '"value"',
    '"type"',
    '"uses"',
    '"score"',
    '"updated_at"',
    mc.has("status") ? '"status"' : "'current' AS status",
  ].join(", ");
  const memories = query(db, `SELECT ${memSel} FROM memories`);

  const ec = tables.has("edges") ? colSet(db, "edges") : new Set<string>();
  const edges = tables.has("edges")
    ? query(
        db,
        `SELECT ${["src_key", "dst_key", "rel", "weight", ec.has("namespace") ? "namespace" : "'default' AS namespace"].join(", ")} FROM edges`,
      )
    : [];

  const ac = tables.has("aliases") ? colSet(db, "aliases") : new Set<string>();
  const aliases = tables.has("aliases")
    ? query(
        db,
        `SELECT ${["segment", "canonical", "status", ac.has("namespace") ? "namespace" : "'default' AS namespace"].join(", ")} FROM aliases`,
      )
    : [];

  const nodes: GraphNode[] = [];
  const seen = new Set<string>();

  for (const m of memories) {
    const ns = m.namespace == null ? "default" : String(m.namespace);
    const key = String(m.key);
    const id = nodeId(ns, key);
    if (seen.has(id)) continue;
    seen.add(id);
    nodes.push({
      id,
      key,
      ns,
      kind: "memory",
      type: String(m.type ?? ""),
      status: String(m.status ?? "current"),
      uses: Number(m.uses) || 0,
      score: Number(m.score) || 0,
      updatedAt: String(m.updated_at ?? ""),
      value: String(m.value ?? ""),
    });
  }

  // An edge/alias endpoint with no memory row becomes a small entity marker.
  const ensureEntity = (ns: string | null, key: string): string => {
    const n = ns == null ? "default" : ns;
    const id = nodeId(n, key);
    if (seen.has(id)) return id;
    seen.add(id);
    nodes.push({
      id,
      key,
      ns: n,
      kind: "entity",
      type: "",
      status: "entity",
      uses: 0,
      score: 0,
      updatedAt: "",
      value: "",
    });
    return id;
  };

  const graphEdges: GraphEdge[] = [];
  edges.forEach((e, i) => {
    const ns = e.namespace == null ? "default" : String(e.namespace);
    const rel = String(e.rel);
    const from = ensureEntity(ns, String(e.src_key));
    const to = ensureEntity(ns, String(e.dst_key));
    const w = Number(e.weight);
    graphEdges.push({
      id: "e" + i,
      from,
      to,
      rel,
      weight: isFinite(w) ? w : 0,
      ns,
      label: rel === "contradicts" ? `contradicts (w=${(isFinite(w) ? w : 0).toFixed(2)})` : rel,
      dashed: rel === "contradicts",
    });
  });
  aliases.forEach((a, i) => {
    const ns = a.namespace == null ? "default" : String(a.namespace);
    const from = ensureEntity(ns, String(a.segment));
    const to = ensureEntity(ns, String(a.canonical));
    graphEdges.push({
      id: "a" + i,
      from,
      to,
      rel: ALIAS_REL,
      weight: 0,
      ns,
      label: `same_as (${String(a.status ?? "")})`,
      dashed: true,
    });
  });

  // FPS-safe cap: keep the most-used nodes, drop edges with a dropped endpoint.
  let note = "";
  if (nodes.length > NODE_CAP) {
    const ranked = [...nodes].sort((a, b) => b.uses - a.uses || (a.id < b.id ? -1 : 1));
    const keep = new Set(ranked.slice(0, NODE_CAP).map((n) => n.id));
    note = `sampled ${NODE_CAP}/${nodes.length} nodes (top by uses)`;
    const keptNodes = nodes.filter((n) => keep.has(n.id));
    const keptEdges = graphEdges.filter((e) => keep.has(e.from) && keep.has(e.to));
    const namespaces = [...new Set(keptNodes.map((n) => n.ns))].sort();
    return { nodes: keptNodes, edges: keptEdges, namespaces, note };
  }

  const namespaces = [...new Set(nodes.map((n) => n.ns))].sort();
  return { nodes, edges: graphEdges, namespaces, note };
}

let sqlPromise: ReturnType<typeof initSqlJs> | null = null;
function getSql() {
  if (!sqlPromise) sqlPromise = initSqlJs({ locateFile: () => wasmUrl });
  return sqlPromise;
}

export async function loadGraphFromBuffer(data: ArrayBuffer): Promise<GraphModel> {
  const SQL = await getSql();
  const db = new SQL.Database(new Uint8Array(data));
  try {
    return buildModel(db);
  } finally {
    db.close();
  }
}

export async function loadGraphFromFile(file: File): Promise<GraphModel> {
  return loadGraphFromBuffer(await file.arrayBuffer());
}

// Used by the headless verification pass (?db=<relative path>) and by anyone who
// wants to point the app at a served copy instead of the file picker.
export async function loadGraphFromUrl(url: string): Promise<GraphModel> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`could not fetch ${url} (HTTP ${res.status})`);
  return loadGraphFromBuffer(await res.arrayBuffer());
}
