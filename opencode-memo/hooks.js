// opencode-memo hooks (Bun, zero deps — fetch + node:crypto are platform).
// transform: pure string op, no MCP call. onSessionStart / onAfterWrite:
// remote-MCP calls; failures warn + stale, session NEVER fails (M-026).
import { createHash, timingSafeEqual } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

const endpoint = () => process.env.MEMO_ENDPOINT ?? "http://localhost:8000/mcp";
const token = () => process.env.MEMO_TOKEN ?? "";

// opencode's native global skills dir (where bare `skill`/skill_create resolve).
const defaultSkillsDir = () =>
  path.join(process.env.HOME ?? "~", ".config/opencode/skills");

const HERMES_BLOCK = new URL("./templates/hermes-block.md", import.meta.url);

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

/** Pure string op: append the hermes block + frozen snapshots. No MCP call. */
export async function transform(systemText, { memoryMd = "", userMd = "" } = {}) {
  let block = "";
  try {
    block = await fs.readFile(HERMES_BLOCK, "utf8");
  } catch {
    block = "";
  }
  return [systemText.trim(), block.trim(),
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
        arguments: { harness: "opencode", known: await localKnown(dir) } }),
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

const _timers = new Map();

/** Map either a file-write path (string) or a native skill tool event
 *  ({tool:"skill_create"|"skill_update", input:{name}}) to a SKILL.md path. */
function resolveSkillPath(event, skillsDir) {
  if (typeof event === "string") {
    return event.includes(`${path.sep}skills${path.sep}`) && event.endsWith("SKILL.md")
      ? event : null;
  }
  const tool = event?.tool;
  if ((tool === "skill_create" || tool === "skill_update") && event?.input?.name) {
    return path.join(skillsDir ?? defaultSkillsDir(), `${event.input.name}`, "SKILL.md");
  }
  return null;
}

/** After hook: skills/**\/SKILL.md writes AND native skill_create/skill_update
 *  tool events -> skill_put(source='local-hook'). Debounced 2s per resolved
 *  path; logs, never throws. */
export function onAfterWrite(event, { skillsDir } = {}) {
  const filePath = resolveSkillPath(event, skillsDir);
  if (!filePath) return;
  clearTimeout(_timers.get(filePath));
  _timers.set(filePath, setTimeout(async () => {
    try {
      const text = await fs.readFile(filePath, "utf8");
      const meta = Object.fromEntries(
        (text.split("---")[1] ?? "").split("\n").filter((l) => l.includes(":"))
          .map((l) => { const i = l.indexOf(":"); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }));
      const body = text.split(/^---$/m).slice(2).join("---").trimStart();
      if (!meta.name || !meta.description) return;
      await rpc("tools/call", { name: "skill_put",
        arguments: { name: meta.name, description: meta.description, body, source: "local-hook" } });
    } catch (e) {
      console.warn(`memo after-write: ${e.message}`);
    }
  }, 2000));
}

// Constant-time local compare helper (re-exported for tests/consumers).
export function safeEqual(a, b) {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
