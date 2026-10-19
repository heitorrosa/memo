import { createHash, timingSafeEqual } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

const endpoint = () => process.env.MEMO_ENDPOINT ?? "http://localhost:8000/mcp";
const token = () => process.env.MEMO_TOKEN ?? "";

const defaultSkillsDir = () =>
  path.join(process.env.HOME ?? process.env.USERPROFILE ?? "~", ".config/opencode/skills");

const HERMES_BLOCK = new URL("./templates/hermes-block.md", import.meta.url);

const FALLBACK_BLOCK = [
  "# memo (opencode) — prose: prompts.json (memo://summary)",
  'skill_sync(harness:"opencode", known:<local name/hash list>)',
  "new skill: skill_create(name, description, body) -> writes " +
    "skills/<name>/SKILL.md + auto skill_put",
].join("\n");

export const REMIND_PERSIST =
  "Persist critical facts with memory_save and reusable workflows with " +
  "skill_put before they are lost.";

const REMIND_SEARCH =
  "memo: no memory_search yet this session — search persistent memory before " +
  "acting (memory_search), and memory_save corrections or preferences as they land.";
const REMIND_SKILL =
  "memo: writing a SKILL.md with no skill_put this session — index it with " +
  "skill_put(name, description, body) so future sessions sync it back.";
export const REMIND_DRIFT = (name) =>
  `memo: local ${name}/SKILL.md differs from the indexed copy — re-run ` +
  `skill_sync (or skill_put) to converge before relying on it.`;

let _sid = null;
let _ready = false;
let _tail = Promise.resolve();

async function rpcPost(method, params, sid) {
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    authorization: `Bearer ${token()}`,
  };
  if (sid) headers["mcp-session-id"] = sid;
  const r = await fetch(endpoint(), {
    method: "POST", headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  return { status: r.status, sid: r.headers.get("mcp-session-id"), text: await r.text() };
}

async function openSession() {
  const r = await rpcPost("initialize", {
    protocolVersion: "2024-11-05", capabilities: {},
    clientInfo: { name: "opencode-memo", version: "3.0.0" },
  }, null);
  if (r.status === 401) throw new Error("memo: unauthorized (check MEMO_TOKEN)");
  if (r.status !== 200) throw new Error(`memo: HTTP ${r.status}`);
  _sid = r.sid ?? null;
  _ready = true;
}

async function rpcCall(method, params) {
  if (!_ready) await openSession();
  let out = await rpcPost(method, params, _sid);
  if (out.status === 400 || out.status === 404) {
    await openSession();
    out = await rpcPost(method, params, _sid);
  }
  if (out.status === 401) throw new Error("memo: unauthorized (check MEMO_TOKEN)");
  if (out.status !== 200) throw new Error(`memo: HTTP ${out.status}`);
  return parseRpc(out.text);
}

function rpc(method, params) {
  const run = _tail.then(() => rpcCall(method, params), () => rpcCall(method, params));
  _tail = run.catch(() => {});
  return run;
}

function parseRpc(text) {
  const line = text.split(/\r?\n/).map((l) => l.trim())
    .find((l) => l.startsWith("{") || l.startsWith("data:"));
  const payload = line && line.startsWith("data:") ? line.slice(5).trim() : (line ?? text);
  return JSON.parse(payload);
}

const sha256 = (s) => createHash("sha256").update(s, "utf8").digest("hex");

const normHash = (body) => sha256(String(body).trimStart().replace(/\s+$/, ""));

export function skillBodyHash(text) {
  return normHash(String(text).split(/^---$/m).slice(2).join("---"));
}

function frontmatterVersion(text) {
  const parts = String(text).split(/^---$/m);
  const fm = parts.length >= 2 ? parts[1] : "";
  const m = fm.match(/^version:\s*(\d+)\s*$/m);
  return m ? Number(m[1]) : null;
}

const SNAPSHOT_NAME = ".memo-sync.json";
const snapshotPath = (skillsDir) => path.join(path.dirname(skillsDir), SNAPSHOT_NAME);

async function readSnapshot(skillsDir) {
  try {
    const snap = JSON.parse(await fs.readFile(snapshotPath(skillsDir), "utf8"));
    if (snap && typeof snap === "object" && snap.skills
        && typeof snap.skills === "object") return snap;
  } catch {  }
  return { version: 1, synced_at: null, agent_id: null, skills: {} };
}

async function scanLocal(skillsDir) {
  const out = {};
  let names = [];
  try {
    names = await fs.readdir(skillsDir);
  } catch {
    return out;
  }
  for (const n of names) {
    try {
      const t = await fs.readFile(path.join(skillsDir, n, "SKILL.md"), "utf8");
      out[n] = { hash: skillBodyHash(t), version: frontmatterVersion(t) };
    } catch {  }
  }
  return out;
}

export async function classifySync(skillsDir, snapshot) {
  const local = await scanLocal(skillsDir);
  const known = [];
  const deleted = [];
  for (const [name, snap] of Object.entries(snapshot?.skills ?? {})) {
    const l = local[name];
    if (!l) { deleted.push(name); continue; }
    known.push({ name, hash: l.hash === snap.hash ? snap.hash : l.hash });
  }
  return { known, deleted };
}

function deletedNudge(deleted) {
  if (!deleted.length) return "";
  return `memo: skill file(s) gone locally but still tracked: ${deleted.join(", ")}. ` +
    "Local deletion is never pushed; if it was intended, tombstone them " +
    "explicitly (memory_delete/skill tombstone path), else the next sync restores them.";
}

export async function driftNudge(skillsDir) {
  const dir = skillsDir ?? defaultSkillsDir();
  const { deleted } = await classifySync(dir, await readSnapshot(dir));
  return deletedNudge(deleted);
}

async function writeSnapshot(skillsDir, delta, prevAgentId) {
  const writes = new Map();
  for (const f of delta.files_to_write ?? []) {
    writes.set(f.path.replace(/^skills\//, "").split("/")[0], f);
  }
  const local = await scanLocal(skillsDir);
  const skills = {};
  for (const [name, info] of Object.entries(local)) {
    const w = writes.get(name);
    skills[name] = { hash: w ? w.hash : info.hash, version: info.version };
  }
  const snap = { version: 1, synced_at: new Date().toISOString(),
    agent_id: delta.agent_id ?? prevAgentId ?? null, skills };
  try {
    await fs.writeFile(snapshotPath(skillsDir), JSON.stringify(snap, null, 2), "utf8");
  } catch {  }
}

async function syncApply(dir) {
  const snap = await readSnapshot(dir);
  const { known, deleted } = await classifySync(dir, snap);
  const args = { harness: "opencode", capabilities: ["persist"], known };
  if (snap.agent_id) args.agent_id = snap.agent_id;
  const sync = await rpc("tools/call", { name: "skill_sync", arguments: args });
  const delta = JSON.parse(sync.result.content[0].text);
  for (const f of delta.files_to_delete ?? [])
    await fs.rm(path.join(dir, f.path.replace(/^skills\//, "")), { force: true });
  for (const f of delta.files_to_write ?? []) {
    const p = path.join(dir, f.path.replace(/^skills\//, ""));
    await fs.mkdir(path.dirname(p), { recursive: true });
    await fs.writeFile(p, f.content, "utf8");
  }
  await writeSnapshot(dir, delta, snap.agent_id);
  return { delta, deleted };
}

async function pushSkill(filePath) {
  const text = await fs.readFile(filePath, "utf8");
  const meta = Object.fromEntries(
    (text.split("---")[1] ?? "").split("\n").filter((l) => l.includes(":"))
      .map((l) => { const i = l.indexOf(":"); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }));
  const body = text.split(/^---$/m).slice(2).join("---").trimStart();
  if (!meta.name || !meta.description) return null;
  const res = await rpc("tools/call", { name: "skill_put",
    arguments: { name: meta.name, description: meta.description, body, source: "local-hook" } });
  try { return JSON.parse(res.result.content[0].text); } catch { return res; }
}

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

export async function onSessionStart({ skillsDir, memoryPath, userPath } = {}) {
  const dir = skillsDir ?? defaultSkillsDir();
  try {
    const [sum, { delta, deleted }] = await Promise.all([
      rpc("resources/read", { uri: "memo://summary" }),
      syncApply(dir),
    ]);
    const summary = sum.result.contents[0].text;
    if (memoryPath) await fs.writeFile(memoryPath, summary.slice(0, 4000), "utf8").catch(() => {});
    if (userPath) await fs.writeFile(userPath, summary.slice(0, 2200), "utf8").catch(() => {});
    return { summary, applied: (delta.files_to_write ?? []).length,
      nudge: deletedNudge(deleted) };
  } catch (e) {
    console.warn(`memo session start: ${e.message} — continuing with stale .md`);
    return { stale: true, warning: String(e.message ?? e) };
  }
}

export function createSkillCreateTool({ skillsDir } = {}) {
  return {
    name: "skill_create",
    description: `Create a reusable skill: write <skills-dir>/<name>/SKILL.md locally, then index it on the memo server.

Use after a workflow worth repeating (5+ tool calls, a non-obvious fix, a multi-step process likely needed again).
The file is written locally, pushed with skill_put, and the session skill view is refreshed — no separate skill_put call needed.`,
    input: {
      type: "object",
      properties: {
        name: {
          type: "string",
          pattern: "^[a-z0-9]+(-[a-z0-9]+)*$",
          maxLength: 64,
          description: "Skill name: lowercase, hyphen-separated (e.g. 'deploy-to-aws')",
        },
        description: {
          type: "string",
          minLength: 10,
          maxLength: 200,
          description: "One-line description shown for skill selection (be specific)",
        },
        body: {
          type: "string",
          minLength: 50,
          description: "Full skill content in Markdown: steps, commands, pitfalls.",
        },
      },
      required: ["name", "description", "body"],
      additionalProperties: false,
    },
    async execute(args) {
      const dir = skillsDir ?? defaultSkillsDir();
      const name = String(args?.name ?? "");
      const description = String(args?.description ?? "");
      const body = String(args?.body ?? "");
      const filePath = path.join(dir, name, "SKILL.md");
      await fs.mkdir(path.dirname(filePath), { recursive: true });
      await fs.writeFile(filePath,
        `---\nname: ${name}\ndescription: ${description}\n---\n${body}`, "utf8");
      let receipt = null;
      try {
        receipt = await pushSkill(filePath);
        await syncApply(dir);
      } catch (e) {
        console.warn(`memo skill_create: ${e.message} — file written, push deferred`);
      }
      const where = receipt
        ? `indexed (v${receipt.version}, ${String(receipt.hash ?? "").slice(0, 12)})`
        : "written; server push deferred (offline)";
      return { content: `Skill '${name}' ${where} at ${filePath}.` };
    },
  };
}

const MEMORY_TOOLS = [
  "memory_search", "memory_save", "memory_delete",
  "skill_create", "skill_put", "skill_get", "skill_sync", "skill_install",
  "skill_update",
];

function memoTool(name) {
  return MEMORY_TOOLS.find(
    (t) => name === t || name.endsWith(`_${t}`) || name.endsWith(`.${t}`)) ?? null;
}

function isSkillUse(tool) {
  const t = String(tool);
  return t === "skill" || t.endsWith("_skill") || t.endsWith(".skill");
}

export function createGuard() {
  return { searched: false, put: false, nudged: new Set(), pending: [] };
}

async function checkDrift(name, skillsDir, g) {
  try {
    const dir = skillsDir ?? defaultSkillsDir();
    const snap = (await readSnapshot(dir))?.skills?.[name] ?? null;
    let localHash = null;
    try {
      localHash = skillBodyHash(
        await fs.readFile(path.join(dir, name, "SKILL.md"), "utf8"));
    } catch {  }

    if (snap && localHash !== null && localHash !== snap.hash) {
      if (!g.nudged.has(`drift:${name}`)) {
        g.nudged.add(`drift:${name}`);
        g.pending.push(REMIND_DRIFT(name));
      }
      return;
    }
    const res = await rpc("tools/call", { name: "skill_get", arguments: { name } });
    const server = JSON.parse(res.result.content[0].text);

    const serverHash = typeof server?.body === "string" ? normHash(server.body) : server?.hash;
    if (serverHash && serverHash !== localHash
        && !g.nudged.has(`drift:${name}`)) {
      g.nudged.add(`drift:${name}`);
      g.pending.push(REMIND_DRIFT(name));
    }
  } catch {  }
}

function maybeDrift(event, g, skillsDir) {
  const name = String(event?.input?.name ?? event?.input?.skill ?? "").trim();
  if (name) checkDrift(name, skillsDir, g);
}

export function guardBefore(event, g, { skillsDir } = {}) {
  const tool = String(event?.tool ?? "");
  const hit = memoTool(tool);
  if (hit) {
    if (hit === "memory_search" || hit === "memory_save") g.searched = true;
    if (hit === "skill_put" || hit === "skill_create") g.put = true;
    if (hit === "skill_get") maybeDrift(event, g, skillsDir);
    return;
  }
  if (isSkillUse(tool)) maybeDrift(event, g, skillsDir);
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

const _timers = new Map();

export function resolveSkillPath(event, skillsDir) {
  if (typeof event === "string") {
    return event.replace(/\\/g, "/").includes("/skills/") && event.endsWith("SKILL.md")
      ? event : null;
  }
  const tool = event?.tool;
  if ((tool === "skill_create" || tool === "skill_update") && event?.input?.name) {
    return path.join(skillsDir ?? defaultSkillsDir(), `${event.input.name}`, "SKILL.md");
  }
  return null;
}

export function onAfterWrite(event, { skillsDir } = {}) {
  const filePath = resolveSkillPath(event, skillsDir);
  if (!filePath) return;
  clearTimeout(_timers.get(filePath));
  _timers.set(filePath, setTimeout(async () => {
    try {
      await pushSkill(filePath);
    } catch (e) {
      console.warn(`memo after-write: ${e.message}`);
    }
  }, 2000));
}

export function safeEqual(a, b) {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
