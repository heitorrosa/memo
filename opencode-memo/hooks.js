// opencode-memo hooks (Bun, zero deps — fetch + node:crypto are platform).
// Enforcement-only glue. `transform` is a pure string op (no MCP call);
// `onSessionStart` does the session-open sync + file materialization over the
// remote MCP endpoint. The execute.before guard + context/compaction hook bodies
// live in plugin.js.
// No background scheduling, no idle flush, no post-tool auto-capture/auto-save
// (M-030). Failures warn + go stale; the session NEVER fails because memory is
// down (M-026).
import { createHash, timingSafeEqual } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

const endpoint = () => process.env.MEMO_ENDPOINT ?? "http://localhost:8000/mcp";
const token = () => process.env.MEMO_TOKEN ?? "";

// opencode's native global skills dir (where bare `skill`/skill_create resolve).
const defaultSkillsDir = () =>
  path.join(process.env.HOME ?? "~", ".config/opencode/skills");

const HERMES_BLOCK = new URL("./templates/hermes-block.md", import.meta.url);

// Last-resort orientation, used only if templates/hermes-block.md is unreadable,
// so the every-render reminder can never silently vanish.
const FALLBACK_BLOCK = [
  "# memo (opencode) — prose: prompts.json (memo://summary)",
  'skill_sync(harness:"opencode", known:<local name/hash list>)',
  "after a workflow worth repeating (5+ tool calls / non-obvious fix): " +
    "skill_put + write skills/<name>/SKILL.md",
].join("\n");

/** Compaction addendum — persisted on both ends of a compaction. */
export const REMIND_PERSIST =
  "Persist critical facts with memory_save and reusable workflows with " +
  "skill_put before they are lost.";

// Guard reminders (queued by guardBefore, surfaced on the next context render).
const REMIND_SEARCH =
  "memo: no memory_search yet this session — search persistent memory before " +
  "acting (memory_search), and memory_save corrections or preferences as they land.";
const REMIND_SKILL =
  "memo: writing a SKILL.md with no skill_put this session — index it with " +
  "skill_put(name, description, body) so future sessions sync it back.";

async function rpc(method, params) {
  const r = await fetch(endpoint(), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      authorization: `Bearer ${token()}`,
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  if (r.status === 401) throw new Error("memo: unauthorized (check MEMO_TOKEN)");
  if (!r.ok) throw new Error(`memo: HTTP ${r.status}`);
  const text = await r.text();
  const line = text.split("\n").find((l) => l.trim().startsWith("{"));
  return JSON.parse(line ?? text);
}

const sha256 = (s) => createHash("sha256").update(s, "utf8").digest("hex");

async function localKnown(skillsDir) {
  const known = [];
  let names = [];
  try {
    names = await fs.readdir(skillsDir);
  } catch {
    return known;
  }
  for (const n of names) {
    try {
      const t = await fs.readFile(path.join(skillsDir, n, "SKILL.md"), "utf8");
      const body = t.split(/^---$/m).slice(2).join("---");
      known.push({ name: n, hash: sha256(body.trimStart()) });
    } catch { /* missing/unreadable: next sync repairs it */ }
  }
  return known;
}

/** Pure string op: append the hermes block + frozen snapshots. No MCP call.
 *  The block falls back to FALLBACK_BLOCK so orientation is never empty. */
export async function transform(systemText, { memoryMd = "", userMd = "" } = {}) {
  let block = "";
  try {
    block = await fs.readFile(HERMES_BLOCK, "utf8");
  } catch {
    block = "";
  }
  return [systemText.trim(), block.trim() || FALLBACK_BLOCK,
    memoryMd.trim(), userMd.trim()].filter(Boolean).join("\n\n");
}

/** Session open: resource summary + skill_sync deltas (deletes THEN writes).
 *  Returns {summary, applied} or {stale:true, warning} — never throws. */
export async function onSessionStart({ skillsDir, memoryPath, userPath } = {}) {
  const dir = skillsDir ?? defaultSkillsDir();
  try {
    const [sum, sync] = await Promise.all([
      rpc("resources/read", { uri: "memo://summary" }),
      rpc("tools/call", { name: "skill_sync",
        arguments: { harness: "opencode", capabilities: ["persist"],
          known: await localKnown(dir) } }),
    ]);
    const delta = JSON.parse(sync.result.content[0].text);
    for (const f of delta.files_to_delete ?? [])
      await fs.rm(path.join(dir, f.path.replace(/^skills\//, "")), { force: true });
    for (const f of delta.files_to_write ?? []) {
      const p = path.join(dir, f.path.replace(/^skills\//, ""));
      await fs.mkdir(path.dirname(p), { recursive: true });
      await fs.writeFile(p, f.content, "utf8");
    }
    const summary = sum.result.contents[0].text;
    if (memoryPath) await fs.writeFile(memoryPath, summary.slice(0, 4000), "utf8").catch(() => {});
    if (userPath) await fs.writeFile(userPath, summary.slice(0, 2200), "utf8").catch(() => {});
    return { summary, applied: (delta.files_to_write ?? []).length };
  } catch (e) {
    console.warn(`memo session start: ${e.message} — continuing with stale .md`);
    return { stale: true, warning: String(e.message ?? e) };
  }
}

// ─── execute.before guard: reminders, never blocks ────────────────────────────
// Memo's MCP tools arrive as `<server>_<tool>` (or `<server>.<tool>`); match on
// the suffix so the guard survives a server rename.
const MEMORY_TOOLS = [
  "memory_search", "memory_save", "memory_delete",
  "skill_put", "skill_get", "skill_sync", "skill_install", "skill_update",
];

function memoTool(name) {
  return MEMORY_TOOLS.find(
    (t) => name === t || name.endsWith(`_${t}`) || name.endsWith(`.${t}`)) ?? null;
}

/** Per-session guard state — plain data, owned by plugin.js. */
export function createGuard() {
  return { searched: false, put: false, nudged: new Set(), pending: [] };
}

/** Records memory usage and queues at most one reminder per trigger per session.
 *  Never throws, never blocks, never mutates the tool input (M-026 preserved):
 *  the tool runs unchanged and the reminder surfaces on the next context render. */
export function guardBefore(event, g) {
  const tool = String(event?.tool ?? "");
  const hit = memoTool(tool);
  if (hit) {
    if (hit === "memory_search" || hit === "memory_save") g.searched = true;
    if (hit === "skill_put") g.put = true;
    return;
  }
  const input = event?.input ?? {};
  const p = String(input.path ?? input.filePath ?? "").replace(/\\/g, "/");
  if (/\/SKILL\.md$/i.test(p)) {
    if (!g.put && !g.nudged.has("skill")) {
      g.nudged.add("skill");
      g.pending.push(REMIND_SKILL);
    }
    return;
  }
  if (!g.searched && !g.nudged.has("search")) {
    g.nudged.add("search");
    g.pending.push(REMIND_SEARCH);
  }
}

// Constant-time local compare helper (re-exported for tests/consumers).
export function safeEqual(a, b) {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
