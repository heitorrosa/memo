import { KeyValue } from "../components/KeyValue";
import { Line, Panel, SectionHead, type PanelProps } from "../components/Panel";
import { Table, type Column } from "../components/Table";
import { useAsync, type AsyncState } from "../hooks/useAsync";
import { useHotkeys } from "../hooks/useHotkeys";
import {
  listAgents,
  listSkills,
  type Agent,
  type AgentList,
  type SkillSummary,
} from "../lib/api";
import { hash7, relTime, text } from "../lib/format";

const COLUMNS: Column<Agent>[] = [
  {
    label: "AGENT",
    width: "minmax(0,1.6fr)",
    render: (agent) => <span className="text-amber">{agent.agent_id}</span>,
  },
  {
    label: "HARNESS",
    width: "9rem",
    render: (agent) => <span className="text-muted">{text(agent.harness)}</span>,
  },
  {
    label: "CAPS",
    width: "5rem",
    align: "right",
    render: (agent) => agent.capabilities.length,
  },
  {
    label: "SKILLS",
    width: "5rem",
    align: "right",
    render: (agent) => agent.skill_count,
  },
  {
    label: "FIRST SEEN",
    width: "7rem",
    align: "right",
    render: (agent) => relTime(agent.first_seen),
  },
  {
    label: "LAST SEEN",
    width: "7rem",
    align: "right",
    render: (agent) => relTime(agent.last_seen),
  },
];

export function AgentsPanel({
  active,
  keys,
  selected,
  onSelect,
  reloadSignal,
}: PanelProps) {
  const list = useAsync(() => listAgents(), [reloadSignal]);
  // Only to spot a stale skill hash on the agent's side.
  const skills = useAsync(() => listSkills({ limit: 200 }), [reloadSignal]);

  const items = list.state.status === "ready" ? list.state.data.items : [];
  const index = items.findIndex((agent) => agent.agent_id === selected);

  const focus = (next: number) => {
    if (items.length === 0) return;
    const clamped = Math.max(0, Math.min(items.length - 1, next));
    const item = items[clamped];
    if (item) onSelect(item.agent_id);
  };

  useHotkeys(
    {
      j: () => focus(index + 1),
      k: () => focus(index - 1),
      g: () => focus(0),
      G: () => focus(items.length - 1),
      Enter: () => focus(index < 0 ? 0 : index),
      Escape: () => onSelect(null),
    },
    keys,
  );

  const detail = items.find((agent) => agent.agent_id === selected) ?? null;
  const current = skills.state.status === "ready" ? skills.state.data.items : [];

  return (
    <div className={active ? "grid h-full grid-cols-[minmax(0,1.3fr)_minmax(20rem,1fr)]" : "hidden"}>
      <div className="flex min-h-0 flex-col border-r border-line">
        <Panel
          title="AGENTS"
          meta={list.state.status === "ready" ? `${items.length} registered` : undefined}
          className="min-h-0 flex-1"
          bodyClassName="overflow-y-auto"
        >
          {list.state.status === "loading" ? <Line kind="loading">loading ...</Line> : null}
          {list.state.status === "error" ? (
            <Line kind="error">error: {list.state.error}</Line>
          ) : null}
          {list.state.status === "ready" && items.length === 0 ? (
            <Line kind="empty">no agents have registered</Line>
          ) : null}
          {items.length > 0 ? (
            <Table
              columns={COLUMNS}
              rows={items}
              rowKey={(agent) => agent.agent_id}
              selected={selected}
              onSelect={onSelect}
            />
          ) : null}
        </Panel>
        <p className="microlabel shrink-0 border-t border-line-soft px-3 py-1">
          j k move · enter open
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <AgentDetail agent={detail} state={list.state} current={current} />
      </div>
    </div>
  );
}

function AgentDetail({
  agent,
  state,
  current,
}: {
  agent: Agent | null;
  state: AsyncState<AgentList>;
  current: SkillSummary[];
}) {
  if (state.status === "loading") return <Line kind="loading">loading ...</Line>;
  if (state.status === "error") return <Line kind="error">error: {state.error}</Line>;
  if (!agent) return <Line kind="empty">no agent selected</Line>;

  const hashes = new Map(current.map((skill) => [skill.name, skill.hash]));

  return (
    <article className="px-4 pb-6 pt-2">
      <header className="flex flex-wrap items-baseline gap-x-3">
        <h2 className="text-amber">{agent.agent_id}</h2>
        <span className="microlabel">{text(agent.harness)}</span>
      </header>

      <SectionHead meta={agent.capabilities.length}>capabilities</SectionHead>
      <div className="mt-1 flex flex-wrap gap-x-3">
        {agent.capabilities.length === 0 ? (
          <span className="text-faint">-</span>
        ) : (
          agent.capabilities.map((capability) => (
            <span key={capability} className="microlabel text-muted">
              {capability}
            </span>
          ))
        )}
      </div>

      <SectionHead>metadata</SectionHead>
      <KeyValue
        className="mt-1"
        rows={[
          { label: "first seen", value: text(agent.first_seen) },
          { label: "last seen", value: text(agent.last_seen) },
          { label: "skill count", value: text(agent.skill_count) },
        ]}
      />

      <SectionHead meta={agent.skills.length}>agent state</SectionHead>
      {agent.skills.length === 0 ? (
        <p className="text-faint">no skills recorded for this agent</p>
      ) : (
        <ul className="mt-1">
          {agent.skills.map((row, position) => {
            const live = hashes.get(row.skill);
            const stale = live !== undefined && live !== row.hash;
            return (
              <li key={`${row.skill}:${position}`} className="flex items-baseline gap-2">
                <span aria-hidden="true" className="text-line">
                  {position === agent.skills.length - 1 ? "└─" : "├─"}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink">{row.skill}</span>
                <span className="tabular microlabel">v{row.version}</span>
                <span className="tabular text-faint">{hash7(row.hash)}</span>
                <span className="tabular microlabel">{relTime(row.updated_at)}</span>
                {stale ? <span className="microlabel text-coral">hash differs</span> : null}
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
