import { KeyValue } from "../components/KeyValue";
import { Line, Panel, SectionHead, type PanelProps } from "../components/Panel";
import { useAsync } from "../hooks/useAsync";
import { getHealth, getStats, type Stats } from "../lib/api";
import { bar, pct, relTime, text, typeColor } from "../lib/format";

export function AppPanel({ active, reloadSignal }: PanelProps) {
  const stats = useAsync(() => getStats(), [reloadSignal]);
  const health = useAsync(() => getHealth(), [reloadSignal]);

  return (
    <div
      className={
        active
          ? "grid h-full grid-cols-[minmax(0,22rem)_minmax(0,1fr)] overflow-y-auto"
          : "hidden"
      }
    >
      <div className="min-h-0 border-r border-line pb-6">
        <Panel
          title="APP"
          meta={
            stats.state.status === "ready"
              ? `memo v${stats.state.data.version}`
              : undefined
          }
        >
          {stats.state.status === "loading" ? <Line kind="loading">loading ...</Line> : null}
          {stats.state.status === "error" ? (
            <Line kind="error">error: {stats.state.error}</Line>
          ) : null}
          {stats.state.status === "ready" ? <Identity stats={stats.state.data} /> : null}
        </Panel>

        <div className="px-3">
          <SectionHead meta="GET /api/health">health</SectionHead>
          {health.state.status === "loading" ? (
            <p className="text-muted">loading ...</p>
          ) : health.state.status === "error" ? (
            <p className="text-coral">error: {health.state.error}</p>
          ) : (
            <p className={health.state.data.ok ? "text-green" : "text-coral"}>
              {health.state.data.ok ? "ok" : "not ok"} · v{health.state.data.version} ·{" "}
              {health.state.data.vec}
            </p>
          )}
        </div>
      </div>

      <div className="min-h-0 px-4 pb-6 pt-2">
        {stats.state.status === "ready" ? <Breakdown stats={stats.state.data} /> : null}
      </div>
    </div>
  );
}

function Identity({ stats }: { stats: Stats }) {
  const { counts } = stats;
  return (
    <div className="px-3">
      <SectionHead>runtime</SectionHead>
      <KeyValue
        className="mt-1"
        rows={[
          { label: "version", value: `v${stats.version}` },
          { label: "vector store", value: text(stats.vec) },
          { label: "namespaces", value: stats.namespaces.join(", ") || "-" },
          { label: "last activity", value: relTime(stats.last_activity) },
          { label: "db path", value: <span className="break-all">{text(stats.db_path)}</span> },
          {
            label: "ui bundle",
            value: stats.ui ? "served from disk" : "not installed (404 at /ui)",
          },
        ]}
      />

      <SectionHead meta="GET /api/stats">counts</SectionHead>
      <KeyValue
        className="mt-1"
        rows={[
          { label: "memories", value: counts.memories },
          {
            label: "current",
            value: `${counts.current} · ${pct(counts.current, counts.memories)}`,
          },
          { label: "superseded", value: counts.superseded },
          { label: "archived", value: counts.archived },
          { label: "edges", value: counts.edges },
          { label: "aliases", value: counts.aliases },
          { label: "episodes", value: counts.episodes },
          { label: "skills", value: counts.skills },
          { label: "agents", value: counts.agents },
        ]}
      />
    </div>
  );
}

function Breakdown({ stats }: { stats: Stats }) {
  return (
    <>
      <BarGroup title="by type" data={stats.by_type} colored />
      <BarGroup title="by status" data={stats.by_status} />
      <BarGroup title="by source" data={stats.by_source} />
      <p className="microlabel mt-4">
        bars are proportional to the largest value in each group
      </p>
    </>
  );
}

function BarGroup({
  title,
  data,
  colored = false,
}: {
  title: string;
  data: Record<string, number>;
  colored?: boolean;
}) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = rows.reduce((best, [, value]) => Math.max(best, value), 0);
  const total = rows.reduce((sum, [, value]) => sum + value, 0);

  return (
    <>
      <SectionHead meta={total}>{title}</SectionHead>
      {rows.length === 0 ? (
        <p className="text-faint">-</p>
      ) : (
        <ul className="mt-1 space-y-px">
          {rows.map(([label, value]) => (
            <li key={label} className="flex items-baseline gap-3">
              <span
                className="microlabel w-[8rem] shrink-0 truncate"
                style={colored ? { color: typeColor(label) } : undefined}
              >
                {label}
              </span>
              <span aria-hidden="true" className="tabular truncate text-amber">
                {bar(value, max, 30)}
              </span>
              <span className="tabular w-[4rem] shrink-0 text-right">{value}</span>
              <span className="tabular microlabel w-[3rem] shrink-0 text-right">
                {pct(value, total)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
