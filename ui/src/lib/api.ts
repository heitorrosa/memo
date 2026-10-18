/**
 * memo HTTP client — typed against the frozen swarm contract §3.
 * native fetch only: no axios, no react-query.
 */

const TOKEN_KEY = "memo.token";
const NAMESPACE = "default";

/* ---------------------------------------------------------------- types -- */

export interface Counts {
  memories: number;
  current: number;
  superseded: number;
  archived: number;
  skills: number;
  agents: number;
  edges: number;
  aliases: number;
  episodes: number;
}

export interface Stats {
  version: number;
  vec: string;
  ui: boolean;
  db_path: string;
  counts: Counts;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  namespaces: string[];
  last_activity: string | null;
}

export interface Memory {
  key: string;
  namespace: string;
  value: string;
  type: string;
  stability: number;
  score: number;
  uses: number;
  status: string;
  agent_id: string | null;
  family_id: string | null;
  relation_type: string | null;
  superseded_by: string | null;
  created_at: string;
  updated_at: string;
  valid_from: string | null;
  valid_to: string | null;
  valid_until: string | null;
  degree: number;
}

export interface MemoryEdge {
  rel: string;
  src_key: string;
  dst_key: string;
  weight: number;
  declarers: string[];
  status: string;
  direction: "out" | "in";
}

export interface Alias {
  segment: string;
  canonical: string;
  status: string;
  evidence: string | null;
  declarers: string[];
}

export interface FamilyRow {
  key: string;
  status: string;
  valid_from: string | null;
  valid_to: string | null;
  superseded_by: string | null;
  relation_type: string | null;
  relation_to_subject: string | null;
}

export interface Declarer {
  src_key: string;
  dst_key: string;
  rel: string;
  declarers: string[];
  status: string;
  evidence: string | null;
}

export interface MemoryList {
  total: number;
  limit: number;
  offset: number;
  items: Memory[];
}

export interface MemoryDetail {
  memory: Memory;
  edges: MemoryEdge[];
  aliases: Alias[];
  family: FamilyRow[];
  declarers: Declarer[];
}

export interface SkillSummary {
  name: string;
  description: string;
  version: number;
  source: string;
  hash: string;
  created_at: string;
  updated_at: string;
  body_bytes: number;
  revisions: number;
}

export interface SkillList {
  total: number;
  items: SkillSummary[];
}

export interface Skill {
  name: string;
  description: string;
  body: string;
  hash: string;
  version: number;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface AgentSkillRef {
  skill: string;
  hash: string;
  version: number;
  updated_at: string;
}

export interface Agent {
  agent_id: string;
  harness: string;
  capabilities: string[];
  first_seen: string;
  last_seen: string;
  skill_count: number;
  skills: AgentSkillRef[];
}

export interface AgentList {
  items: Agent[];
}

export interface GraphNode {
  key: string;
  type: string;
  status: string;
  uses: number;
  score: number;
  degree: number;
}

export interface GraphEdge {
  src_key: string;
  dst_key: string;
  rel: string;
  weight: number;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Health {
  ok: boolean;
  version: number;
  vec: string;
}

export interface MemoryLink {
  to: string;
  rel: string;
}

export interface MemoryAlias {
  segment: string;
  canonical: string;
}

export interface SaveMemoryPayload {
  key: string;
  value: string;
  type: string;
  namespace?: string;
  links?: MemoryLink[];
  aliases?: MemoryAlias[];
  agent_id?: string;
  valid_from?: string;
  valid_until?: string;
}

/** Write helpers answer with the do_* result verbatim: shape is server-owned. */
export interface WriteResult {
  [key: string]: unknown;
}

/* --------------------------------------------------------------- errors -- */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/* ---------------------------------------------------------- token state -- */

let token = "";
let unauthorized: (() => void) | null = null;

export function setToken(value: string): void {
  token = value.trim();
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function storedToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function onUnauthorized(fn: (() => void) | null): void {
  unauthorized = fn;
}

/** `?token=` is consumed once on load, then dropped from the address bar. */
export function tokenFromUrl(): string {
  const url = new URL(location.href);
  const found = url.searchParams.get("token");
  if (!found) return "";
  url.searchParams.delete("token");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  return found;
}

/* ------------------------------------------------------------- plumbing -- */

function parseJson(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function failure(data: unknown, status: number): ApiError {
  const outer = data as { error?: unknown } | undefined;
  const inner = outer?.error as { code?: unknown; message?: unknown } | undefined;
  const code = typeof inner?.code === "string" ? inner.code : `http_${status}`;
  const message = typeof inner?.message === "string" ? inner.message : `HTTP ${status}`;
  return new ApiError(status, code, message);
}

async function req<T>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers["content-type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch (cause) {
    throw new ApiError(0, "network", cause instanceof Error ? cause.message : "network error");
  }

  const data = parseJson(await res.text());
  if (!res.ok) {
    if (res.status === 401) unauthorized?.();
    throw failure(data, res.status);
  }
  if (data === undefined) throw new ApiError(res.status, "bad_response", "response was not JSON");
  return data as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

/* ------------------------------------------------------------- read API -- */

export function getStats(): Promise<Stats> {
  return req<Stats>("/api/stats");
}

export function getHealth(): Promise<Health> {
  return req<Health>("/api/health");
}

export interface MemoryQuery {
  q?: string;
  type?: string;
  status?: string;
  namespace?: string;
  limit?: number;
  offset?: number;
  sort?: string;
}

export function listMemories(query: MemoryQuery = {}): Promise<MemoryList> {
  return req<MemoryList>(
    `/api/memories${qs({
      q: query.q,
      type: query.type,
      status: query.status,
      namespace: query.namespace ?? NAMESPACE,
      limit: query.limit ?? 100,
      offset: query.offset ?? 0,
      sort: query.sort,
    })}`,
  );
}

export function getMemory(key: string, namespace: string = NAMESPACE): Promise<MemoryDetail> {
  return req<MemoryDetail>(`/api/memories/${encodeURIComponent(key)}${qs({ namespace })}`);
}

export function saveMemory(payload: SaveMemoryPayload): Promise<WriteResult> {
  return req<WriteResult>("/api/memories", {
    method: "POST",
    body: { namespace: NAMESPACE, ...payload },
  });
}

export function deleteMemory(key: string, namespace: string = NAMESPACE): Promise<WriteResult> {
  return req<WriteResult>(`/api/memories/${encodeURIComponent(key)}${qs({ namespace })}`, {
    method: "DELETE",
  });
}

export interface SkillQuery {
  q?: string;
  source?: string;
  limit?: number;
  offset?: number;
}

export function listSkills(query: SkillQuery = {}): Promise<SkillList> {
  return req<SkillList>(
    `/api/skills${qs({
      q: query.q,
      source: query.source,
      limit: query.limit ?? 200,
      offset: query.offset ?? 0,
    })}`,
  );
}

export function getSkill(name: string, version?: number): Promise<{ skill: Skill }> {
  return req<{ skill: Skill }>(
    `/api/skills/${encodeURIComponent(name)}${qs({ version: version ?? undefined })}`,
  );
}

export function putSkill(payload: {
  name: string;
  description: string;
  body: string;
  source?: string;
}): Promise<WriteResult> {
  return req<WriteResult>("/api/skills", { method: "POST", body: payload });
}

export function patchSkill(name: string, patch: string): Promise<WriteResult> {
  return req<WriteResult>(`/api/skills/${encodeURIComponent(name)}/revisions`, {
    method: "POST",
    body: { patch },
  });
}

export function listAgents(): Promise<AgentList> {
  return req<AgentList>("/api/agents");
}

export function getGraph(limit = 200, namespace: string = NAMESPACE): Promise<Graph> {
  return req<Graph>(`/api/graph${qs({ limit, namespace })}`);
}

/* ------------------------------------------------------------ UX helpers -- */

/** A write answered with `{"error": ...}` (200/500 body) still counts as failed. */
export function writeFailed(result: WriteResult): string | null {
  const err = result["error"];
  if (typeof err === "string") return err;
  if (err && typeof err === "object") {
    const message = (err as { message?: unknown }).message;
    if (typeof message === "string") return message;
    return "write failed";
  }
  const ok = result["ok"];
  if (ok === false) return "write failed";
  return null;
}
