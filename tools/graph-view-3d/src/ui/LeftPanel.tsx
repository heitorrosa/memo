import { motion } from "motion/react";
import { Eye, EyeOff, Search } from "lucide-react";
import { REL_COLORS, TYPE_COLORS, type GraphModel } from "../data";

export interface Counts {
  nodes: number;
  totalNodes: number;
  edges: number;
  totalEdges: number;
  ns: string;
  note: string;
  layoutMs: number;
  iterations: number;
}

interface Props {
  model: GraphModel;
  nsFilter: string;
  setNsFilter: (ns: string) => void;
  rels: Record<string, boolean>;
  toggleRel: (rel: string) => void;
  relList: string[];
  search: string;
  setSearch: (s: string) => void;
  onSearch: () => void;
  counts: Counts;
}

const SWATCH: Record<string, string> = {
  preference: TYPE_COLORS.preference,
  analysis: TYPE_COLORS.analysis,
  feedback: TYPE_COLORS.feedback,
  context: TYPE_COLORS.context,
};

export default function LeftPanel({
  model,
  nsFilter,
  setNsFilter,
  rels,
  toggleRel,
  relList,
  search,
  setSearch,
  onSearch,
  counts,
}: Props) {
  return (
    <motion.aside
      initial={{ x: -24, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="pointer-events-auto flex w-[300px] max-h-full flex-col gap-3 overflow-y-auto rounded-lg border border-cyan-400/20 bg-slate-950/80 p-3 font-mono text-[12px] text-slate-200 shadow-2xl backdrop-blur"
    >
      <div>
        <h1 className="text-[13px] font-semibold tracking-wide text-cyan-200">memo graph 3d</h1>
        <p className="mt-1 text-[11px] leading-snug text-slate-400">
          read-only, fully local. the picked file is parsed in this page; it never leaves the machine.
        </p>
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">search (key or value)</span>
        <span className="flex gap-1">
          <span className="relative flex-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSearch();
              }}
              placeholder="focus a node"
              className="w-full rounded border border-slate-700 bg-slate-900/80 py-1 pl-7 pr-2 text-slate-100 outline-none focus:border-cyan-400"
            />
          </span>
          <button
            onClick={onSearch}
            className="rounded border border-cyan-400/40 bg-cyan-500/10 px-2 py-1 text-cyan-200 hover:bg-cyan-500/20"
          >
            focus
          </button>
        </span>
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">namespace</span>
        <select
          value={nsFilter}
          onChange={(e) => setNsFilter(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900/80 px-2 py-1 text-slate-100 outline-none focus:border-cyan-400"
        >
          <option value="*">* (all)</option>
          {model.namespaces.map((ns) => (
            <option key={ns} value={ns}>
              {ns}
            </option>
          ))}
        </select>
      </label>

      <div className="flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wide text-slate-500">relations</span>
        <div className="flex flex-wrap gap-1">
          {relList.map((rel) => {
            const on = rels[rel] !== false;
            return (
              <button
                key={rel}
                onClick={() => toggleRel(rel)}
                className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${
                  on ? "border-slate-600 bg-slate-800/80 text-slate-100" : "border-slate-800 bg-slate-900/40 text-slate-500"
                }`}
              >
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: REL_COLORS[rel] ?? "#8a94a6" }} />
                {on ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
                {rel}
              </button>
            );
          })}
        </div>
      </div>

      <div className="rounded border border-slate-800 bg-slate-900/50 p-2 text-[11px] leading-relaxed text-slate-300">
        <div>
          nodes <span className="text-cyan-200">{counts.nodes}</span>/{counts.totalNodes}
        </div>
        <div>
          edges <span className="text-cyan-200">{counts.edges}</span>/{counts.totalEdges}
        </div>
        <div>
          namespace <span className="text-cyan-200">{counts.ns}</span>
        </div>
        <div className="text-slate-500">
          layout {counts.layoutMs.toFixed(0)} ms / {counts.iterations} iters
        </div>
        {counts.note && <div className="text-amber-300/90">{counts.note}</div>}
      </div>

      <div className="rounded border border-slate-800 bg-slate-900/50 p-2 text-[11px]">
        <div className="mb-1 uppercase tracking-wide text-slate-500">memory type</div>
        <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
          {Object.entries(SWATCH).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1 text-slate-300">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: v }} />
              {k}
            </span>
          ))}
          <span className="flex items-center gap-1 text-slate-400">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: "#9aa0a6" }} />
            superseded
          </span>
          <span className="flex items-center gap-1 text-slate-400">
            <span className="inline-block h-2.5 w-2.5 rotate-45" style={{ background: "#b8bec8" }} />
            virtual entity
          </span>
        </div>
        <div className="mb-1 mt-2 uppercase tracking-wide text-slate-500">relation</div>
        <div className="flex flex-col gap-0.5">
          {relList.map((rel) => (
            <span key={rel} className="flex items-center gap-1 text-slate-300">
              <span
                className="inline-block w-4"
                style={{
                  borderTop: `2px ${rel === "contradicts" ? "dashed" : rel === "same_as" ? "dotted" : "solid"} ${
                    REL_COLORS[rel] ?? "#8a94a6"
                  }`,
                }}
              />
              {rel}
              {rel === "contradicts" && <span className="text-slate-500">(dashed)</span>}
              {rel === "same_as" && <span className="text-slate-500">(alias, dotted)</span>}
            </span>
          ))}
        </div>
      </div>

      <p className="text-[10px] leading-snug text-slate-500">
        drag to orbit, wheel to zoom, click a node to focus. the bottom bar accepts commands.
      </p>
    </motion.aside>
  );
}
