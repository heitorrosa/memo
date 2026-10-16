// Build a seeded demo copy for local + headless checks. Never writes to the file
// you pass in: it reads it and exports a new one.
//
//   node scripts/seed-demo.mjs <path-to-a-memo.db-copy> <out.db>
//
// Usage note: always feed it a COPY of data/memo.db, never the live file.
import initSqlJs from "sql.js";
import { readFileSync, writeFileSync } from "node:fs";

const [src, out] = process.argv.slice(2);
if (!src || !out) {
  console.error("usage: node scripts/seed-demo.mjs <memo.db copy> <out.db>");
  process.exit(2);
}

const SQL = await initSqlJs();
const db = new SQL.Database(readFileSync(src));

const tableExists = (t) => {
  const r = db.exec(`SELECT name FROM sqlite_master WHERE type='table' AND name='${t}'`);
  return r.length > 0 && r[0].values.length > 0;
};
const columns = (t) => {
  const r = db.exec(`SELECT name FROM pragma_table_info('${t}')`);
  return new Set(r.length ? r[0].values.map((v) => String(v[0])) : []);
};

const EDGES = [
  ["Design preferences for Mansa UI", "Response style and working preferences", "supports", 0.7],
  ["Design preferences for Mansa UI", "preference.frontend-build-gitignored", "mentions", 0.4],
  ["Agent swarm and Memo architecture experiments", "Local AI, memory, and agent systems", "mentions", 0.6],
  ["Local AI, memory, and agent systems", "Agent swarm and Memo architecture experiments", "derived_from", 0.5],
  ["Response style and working preferences", "Design preferences for Mansa UI", "contradicts", 0.8],
  ["correction.single-token-secret-no-rotation", "User profile and technical background", "derived_from", 0.3],
  ["project.mansa.integration-interest", "Agent swarm and Memo architecture experiments", "mentions", 0.5],
  ["User profile and technical background", "User's name", "mentions", 0.9],
  ["virtual.topic.graph3d", "Design preferences for Mansa UI", "mentions", 0.35],
  ["virtual.topic.embeddings", "Local AI, memory, and agent systems", "supports", 0.45],
];
const ALIASES = [
  ["memo", "Local AI, memory, and agent systems", "confirmed", 3],
  ["R3F", "Design preferences for Mansa UI", "candidate", 1],
];

const now = new Date().toISOString();
let nEdges = 0;
let nAliases = 0;
const hadEdges = tableExists("edges");
const hadAliases = tableExists("aliases");

if (hadEdges) {
  const cols = columns("edges");
  const stmt = db.prepare(
    `INSERT OR REPLACE INTO edges (src_key,dst_key,rel,weight${cols.has("created_at") ? ",created_at" : ""}) VALUES (?,?,?,?${cols.has("created_at") ? ",?" : ""})`,
  );
  for (const [a, b, rel, w] of EDGES) {
    stmt.run(cols.has("created_at") ? [a, b, rel, w, now] : [a, b, rel, w]);
    nEdges++;
  }
  stmt.free();
}

if (hadAliases) {
  const cols = columns("aliases");
  const extra = ["declarers", "status", "evidence", "first_seen", "last_seen"].filter((c) => cols.has(c));
  const stmt = db.prepare(
    `INSERT OR REPLACE INTO aliases (segment,canonical${extra.length ? "," + extra.join(",") : ""}) VALUES (?,?${extra.map(() => ",?").join("")})`,
  );
  for (const [seg, canon, status, ev] of ALIASES) {
    const values = { declarers: "demo-seed", status, evidence: ev, first_seen: now, last_seen: now };
    stmt.run([seg, canon, ...extra.map((c) => values[c])]);
    nAliases++;
  }
  stmt.free();
}

writeFileSync(out, Buffer.from(db.export()));
db.close();
console.log(`wrote ${out}: ${nEdges} edges, ${nAliases} aliases (source: ${src})`);
if (!hadEdges || !hadAliases) {
  console.log("(note: this source has no edges/aliases tables - only memories were copied)");
}
