import { useCallback, useEffect, useMemo, useState } from "react";
import Graph3D from "./scene/Graph3D";
import LeftPanel, { type Counts } from "./ui/LeftPanel";
import DetailDrawer from "./ui/DetailDrawer";
import CommandBar from "./ui/CommandBar";
import {
  ALIAS_REL,
  DEFAULT_RELS,
  loadGraphFromFile,
  loadGraphFromUrl,
  type GraphModel,
  type GraphNode,
} from "./data";
import { forceLayout } from "./layout";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function App() {
  const [model, setModel] = useState<GraphModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState("");
  const [nsFilter, setNsFilter] = useState("*");
  const [rels, setRels] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);

  const load = useCallback(async (fn: () => Promise<GraphModel>, name: string) => {
    setBusy(true);
    setError(null);
    try {
      const m = await fn();
      setModel(m);
      setFileName(name);
      setNsFilter("*");
      setRels({});
      setFocusId(null);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // ?db=<relative path> loads a served copy - used by the headless verification
  // pass, and handy for anyone who keeps a demo db next to the build.
  useEffect(() => {
    const db = new URLSearchParams(window.location.search).get("db");
    if (db) void load(() => loadGraphFromUrl(db), db);
  }, [load]);

  const layout = useMemo(() => {
    if (!model) return null;
    const idx = new Map(model.nodes.map((n, i) => [n.id, i]));
    const pairs: Array<[number, number]> = [];
    for (const e of model.edges) {
      const a = idx.get(e.from);
      const b = idx.get(e.to);
      if (a != null && b != null) pairs.push([a, b]);
    }
    return forceLayout(model.nodes.length, pairs);
  }, [model]);

  const relList = useMemo(() => {
    if (!model) return [];
    const present = new Set(model.edges.map((e) => e.rel));
    const out = [...DEFAULT_RELS, ALIAS_REL].filter((r, i, arr) => arr.indexOf(r) === i);
    for (const r of present) if (!out.includes(r)) out.push(r);
    return out;
  }, [model]);

  const visibleNodes = useMemo(() => {
    const s = new Set<string>();
    model?.nodes.forEach((n) => {
      if (nsFilter === "*" || n.ns === nsFilter) s.add(n.id);
    });
    return s;
  }, [model, nsFilter]);

  // a filter that hides the focused node drops the focus, so the drawer and the
  // 3D marker never disagree with the counts.
  useEffect(() => {
    if (focusId && !visibleNodes.has(focusId)) setFocusId(null);
  }, [focusId, visibleNodes]);

  const visibleEdges = useMemo(() => {
    if (!model) return [];
    return model.edges.filter(
      (e) => rels[e.rel] !== false && visibleNodes.has(e.from) && visibleNodes.has(e.to),
    );
  }, [model, rels, visibleNodes]);

  const focused = useMemo(
    () => (model && focusId ? model.nodes.find((n) => n.id === focusId) ?? null : null),
    [model, focusId],
  );

  const neighbors = useMemo(() => {
    if (!model || !focused) return [];
    const byId = new Map(model.nodes.map((n) => [n.id, n]));
    const out: Array<{ node: GraphNode; edge: (typeof model.edges)[number]; dir: "out" | "in" }> = [];
    for (const e of visibleEdges) {
      if (e.from === focused.id) {
        const n = byId.get(e.to);
        if (n) out.push({ node: n, edge: e, dir: "out" });
      } else if (e.to === focused.id) {
        const n = byId.get(e.from);
        if (n) out.push({ node: n, edge: e, dir: "in" });
      }
    }
    return out;
  }, [model, focused, visibleEdges]);

  const counts: Counts = {
    nodes: visibleNodes.size,
    totalNodes: model?.nodes.length ?? 0,
    edges: visibleEdges.length,
    totalEdges: model?.edges.length ?? 0,
    ns: nsFilter === "*" ? "all" : nsFilter,
    note: model?.note ?? "",
    layoutMs: layout?.ms ?? 0,
    iterations: layout?.iterations ?? 0,
  };

  const focusByText = useCallback(
    (raw: string) => {
      if (!model) return "no database loaded.";
      const q = raw.trim().toLowerCase();
      if (!q) return "";
      const hit =
        model.nodes.find((n) => n.key.toLowerCase().includes(q)) ??
        model.nodes.find((n) => n.value.toLowerCase().includes(q));
      if (!hit) return `no node matches "${raw.trim()}".`;
      if (nsFilter !== "*" && hit.ns !== nsFilter) setNsFilter(hit.ns);
      setFocusId(hit.id);
      return `focused ${hit.key} (${hit.ns}).`;
    },
    [model, nsFilter],
  );

  const toggleRel = useCallback((rel: string) => {
    setRels((r) => ({ ...r, [rel]: r[rel] === false }));
  }, []);

  const help = useCallback(
    () =>
      [
        "commands:",
        "  help                     this list",
        "  focus <text>             focus the first node whose key or value contains <text>",
        "  filter rel:<rel> [on|off] toggle a relation (e.g. filter rel:contradicts off)",
        `  ns <name|*>              set the namespace filter (available: ${(model?.namespaces ?? []).join(", ") || "none"})`,
        "  clear                    clear the focus",
        "  info                     current counts",
      ].join("\n"),
    [model],
  );

  const runCommand = useCallback(
    (line: string): string => {
      const cmd = line.trim();
      if (!cmd) return "";
      const [head, ...rest] = cmd.split(/\s+/);
      const arg = rest.join(" ");
      switch (head.toLowerCase()) {
        case "help":
        case "?":
          return help();
        case "focus":
          return arg ? focusByText(arg) : "usage: focus <text>";
        case "filter": {
          const m = /^rel:(\S+)(?:\s+(on|off|true|false))?$/i.exec(arg);
          if (!m) return "usage: filter rel:<rel> [on|off]";
          const rel = m[1];
          if (!relList.includes(rel)) return `unknown relation "${rel}" (known: ${relList.join(", ")}).`;
          const on = m[2] ? /^(on|true)$/i.test(m[2]) : rels[rel] === false;
          setRels((r) => ({ ...r, [rel]: on }));
          return `${rel} ${on ? "on" : "off"}.`;
        }
        case "ns":
          if (!arg) return "usage: ns <name|*>";
          if (arg !== "*" && !(model?.namespaces ?? []).includes(arg))
            return `unknown namespace "${arg}" (known: ${(model?.namespaces ?? []).join(", ")}).`;
          setNsFilter(arg);
          return `namespace ${arg}.`;
        case "clear":
          setFocusId(null);
          return "focus cleared.";
        case "info":
          return `nodes ${counts.nodes}/${counts.totalNodes}, edges ${counts.edges}/${counts.totalEdges}, namespace ${counts.ns}, layout ${counts.layoutMs.toFixed(0)} ms.`;
        default:
          return `unknown command "${head}" - type help.`;
      }
    },
    [counts, focusByText, help, model, relList, rels],
  );

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-[#060b16] text-slate-100">
      {model && layout && (
        <Graph3D
          model={model}
          positions={layout.pos}
          visibleNodes={visibleNodes}
          visibleEdges={visibleEdges}
          focusId={focusId}
          onFocus={setFocusId}
        />
      )}

      <div className="pointer-events-none absolute inset-0 flex flex-col gap-2 p-3">
        <div className="flex min-h-0 flex-1 items-start justify-between gap-3">
          {model ? (
            <LeftPanel
              model={model}
              nsFilter={nsFilter}
              setNsFilter={setNsFilter}
              rels={rels}
              toggleRel={toggleRel}
              relList={relList}
              search={search}
              setSearch={setSearch}
              onSearch={() => {
                focusByText(search);
              }}
              counts={counts}
            />
          ) : (
            <div className="pointer-events-auto w-[320px] rounded-lg border border-cyan-400/20 bg-slate-950/85 p-3 font-mono text-[12px] text-slate-200 shadow-2xl backdrop-blur">
              <h1 className="text-[13px] font-semibold text-cyan-200">memo graph 3d</h1>
              <p className="mt-1 text-[11px] leading-snug text-slate-400">
                read-only, fully local. pick a copy of <span className="text-cyan-200">data/memo.db</span> - never the live
                file. nothing is uploaded.
              </p>
              <input
                type="file"
                accept=".db,.sqlite,.sqlite3"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void load(() => loadGraphFromFile(f), f.name);
                }}
                className="mt-2 w-full text-[11px] text-slate-300 file:mr-2 file:rounded file:border file:border-cyan-400/40 file:bg-cyan-500/10 file:px-2 file:py-1 file:text-cyan-200"
              />
              {busy && <p className="mt-2 text-[11px] text-cyan-300">loading and laying out...</p>}
              {error && <p className="mt-2 whitespace-pre-wrap text-[11px] text-rose-300">{error}</p>}
            </div>
          )}

          {focused && (
            <DetailDrawer
              node={focused}
              neighbors={neighbors}
              onFocus={setFocusId}
              onClose={() => setFocusId(null)}
            />
          )}
        </div>

        {model && error && (
          <p className="pointer-events-none mx-auto max-w-[600px] whitespace-pre-wrap rounded border border-rose-400/30 bg-rose-950/70 px-2 py-1 text-center font-mono text-[11px] text-rose-200">
            {error}
          </p>
        )}

        <div className="h-[132px] w-full shrink-0">
          <CommandBar onCommand={runCommand} />
        </div>

        {model && (
          <p className="pointer-events-none text-center font-mono text-[10px] text-slate-500">
            {fileName} - nodes {counts.nodes}/{counts.totalNodes} - edges {counts.edges}/{counts.totalEdges}
          </p>
        )}
      </div>
    </div>
  );
}
