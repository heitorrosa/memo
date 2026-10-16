import { motion } from "motion/react";
import { Crosshair, X } from "lucide-react";
import { REL_COLORS, nodeColor, type GraphEdge, type GraphNode } from "../data";

interface Props {
  node: GraphNode;
  neighbors: Array<{ node: GraphNode; edge: GraphEdge; dir: "out" | "in" }>;
  onFocus: (id: string) => void;
  onClose: () => void;
}

export default function DetailDrawer({ node, neighbors, onFocus, onClose }: Props) {
  const row = (k: string, v: string) => (
    <div className="flex gap-2">
      <span className="w-[92px] shrink-0 text-slate-500">{k}</span>
      <span className="min-w-0 flex-1 break-words text-slate-200">{v}</span>
    </div>
  );

  return (
    <motion.aside
      initial={{ y: 24, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="pointer-events-auto flex max-h-full w-[360px] flex-col gap-2 overflow-y-auto rounded-lg border border-cyan-400/20 bg-slate-950/85 p-3 font-mono text-[12px] text-slate-200 shadow-2xl backdrop-blur"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="inline-block h-3 w-3 rounded-full" style={{ background: nodeColor(node) }} />
          <h2 className="break-all text-[13px] font-semibold text-cyan-100">{node.key}</h2>
        </div>
        <button onClick={onClose} className="rounded p-0.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex flex-col gap-0.5">
        {row("kind", node.kind)}
        {node.kind === "memory" ? (
          <>
            {row("type", node.type || "-")}
            {row("status", node.status)}
            {row("uses", String(node.uses))}
            {row("score", node.score.toFixed(2))}
            {row("updated_at", node.updatedAt || "-")}
          </>
        ) : (
          <div className="text-slate-400">virtual entity: referenced by an edge but has no memory row.</div>
        )}
        {row("namespace", node.ns)}
      </div>

      {node.value && (
        <div className="rounded border border-slate-800 bg-slate-900/60 p-2 text-[11px] leading-snug text-slate-300">
          {node.value.length > 600 ? node.value.slice(0, 600) + "..." : node.value}
        </div>
      )}

      <div>
        <div className="mb-1 flex items-center gap-1 uppercase tracking-wide text-slate-500">
          <Crosshair className="h-3 w-3" /> neighbors ({neighbors.length})
        </div>
        {neighbors.length === 0 && <div className="text-slate-500">no links in this view.</div>}
        <div className="flex flex-col gap-0.5">
          {neighbors.map(({ node: n, edge, dir }) => (
            <button
              key={edge.id + n.id}
              onClick={() => onFocus(n.id)}
              className="flex items-center gap-2 rounded border border-slate-800 bg-slate-900/50 px-2 py-1 text-left hover:border-cyan-400/40 hover:bg-slate-800/70"
            >
              <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: nodeColor(n) }} />
              <span className="min-w-0 flex-1 truncate text-slate-200">{n.key}</span>
              <span className="shrink-0 text-[10px]" style={{ color: REL_COLORS[edge.rel] ?? "#8a94a6" }}>
                {dir === "out" ? "->" : "<-"} {edge.rel}
              </span>
            </button>
          ))}
        </div>
      </div>
    </motion.aside>
  );
}
