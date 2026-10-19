import { useEffect, useState } from "react";
import { Chip } from "../components/Chip";
import { KeyValue } from "../components/KeyValue";
import { Line, Panel, SearchBar, SectionHead, Hints, type PanelProps } from "../components/Panel";
import { Table, type Column } from "../components/Table";
import { useAsync, type AsyncState } from "../hooks/useAsync";
import { useHotkeys } from "../hooks/useHotkeys";
import {
  getSkill,
  listSkills,
  patchSkill,
  writeFailed,
  type Skill,
  type SkillSummary,
} from "../lib/api";
import { bytes, clip, describeError, oneLine, relTime, text } from "../lib/format";

const COLUMNS: Column<SkillSummary>[] = [
  {
    label: "NAME",
    width: "minmax(0,1.4fr)",
    render: (skill) => <span className="text-amber">{skill.name}</span>,
  },
  { label: "VER", width: "4rem", align: "right", render: (skill) => skill.version },
  {
    label: "SOURCE",
    width: "7rem",
    render: (skill) => <span className="text-muted">{skill.source}</span>,
  },
  {
    label: "SIZE",
    width: "5rem",
    align: "right",
    render: (skill) => bytes(skill.body_bytes),
  },
  { label: "REV", width: "4rem", align: "right", render: (skill) => skill.revisions },
  {
    label: "UPDATED",
    width: "5rem",
    align: "right",
    render: (skill) => relTime(skill.updated_at),
  },
];

export function SkillsPanel({
  active,
  keys,
  selected,
  onSelect,
  onToast,
  reloadSignal,
  searchRef,
}: PanelProps) {
  const [search, setSearch] = useState("");
  const [committed, setCommitted] = useState("");
  const [source, setSource] = useState("all");
  const [editing, setEditing] = useState(false);
  const [patch, setPatch] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => setCommitted(search.trim()), 250);
    return () => window.clearTimeout(id);
  }, [search]);

  const list = useAsync(
    () =>
      listSkills({
        q: committed || undefined,
        source: source === "all" ? undefined : source,
      }),
    [committed, source, reloadSignal],
  );

  const detail = useAsync<{ skill: Skill } | null>(
    () => (selected ? getSkill(selected) : Promise.resolve(null)),
    [selected, reloadSignal],
  );

  const items = list.state.status === "ready" ? list.state.data.items : [];
  const sources = [...new Set(items.map((item) => item.source))].sort();
  const index = items.findIndex((item) => item.name === selected);

  const focus = (next: number) => {
    if (items.length === 0) return;
    const clamped = Math.max(0, Math.min(items.length - 1, next));
    const item = items[clamped];
    if (item) onSelect(item.name);
  };

  useHotkeys({ Escape: () => setEditing(false) }, keys && editing, true);

  useHotkeys(
    {
      j: () => focus(index + 1),
      k: () => focus(index - 1),
      g: () => focus(0),
      G: () => focus(items.length - 1),
      Enter: () => focus(index < 0 ? 0 : index),
      Escape: () => {
        setEditing(false);
        onSelect(null);
      },
      e: () => {
        if (!selected) return;
        setPatch("");
        setEditing(true);
      },
    },
    keys && !editing,
  );

  const apply = () => {
    if (!selected || !patch.trim()) {
      onToast("err", "patch is empty");
      return;
    }
    const name = selected;
    setSaving(true);
    patchSkill(name, patch).then(
      (result) => {
        setSaving(false);
        const bad = writeFailed(result);
        if (bad) {
          onToast("err", `patch failed: ${bad}`);
          return;
        }
        onToast("ok", `patched ${name}`);
        setEditing(false);
        setPatch("");
        list.reload();
        detail.reload();
      },
      (error: unknown) => {
        setSaving(false);
        onToast("err", `patch failed: ${describeError(error)}`);
      },
    );
  };

  const meta =
    list.state.status === "ready"
      ? `${list.state.data.total} skills${committed ? ` · q "${clip(committed, 18)}"` : ""}`
      : undefined;

  const open = selected !== null;

  return (
    <div
      className={
        active
          ? `grid h-full ${
              open
                ? "grid-cols-[minmax(0,1.25fr)_minmax(20rem,1fr)]"
                : "grid-cols-[minmax(0,1fr)]"
            }`
          : "hidden"
      }
    >
      <div className={`flex min-h-0 flex-col ${open ? "border-r border-line" : ""}`}>
        <Panel
          title="SKILLS"
          meta={meta}
          className="min-h-0 flex-1"
          bodyClassName="overflow-y-auto"
        >
          <SearchBar
            inputRef={active ? searchRef : undefined}
            value={search}
            onChange={setSearch}
            placeholder="name or description"
          />
          <div className="flex flex-wrap items-center gap-x-3 border-b border-line-soft px-3 py-1">
            <Chip label="all" active={source === "all"} onClick={() => setSource("all")} />
            {sources.map((name) => (
              <Chip
                key={name}
                label={name}
                active={source === name}
                onClick={() => setSource(source === name ? "all" : name)}
              />
            ))}
          </div>

          {list.state.status === "loading" ? <Line kind="loading">loading ...</Line> : null}
          {list.state.status === "error" ? (
            <Line kind="error">error: {list.state.error}</Line>
          ) : null}
          {list.state.status === "ready" && items.length === 0 ? (
            <Line kind="empty">no skills match</Line>
          ) : null}
          {items.length > 0 ? (
            <Table
              columns={COLUMNS}
              rows={items}
              rowKey={(skill) => skill.name}
              selected={selected}
              onSelect={onSelect}
            />
          ) : null}
        </Panel>
        <Hints
          items={[
            ...(selected ? [] : [{ key: "", label: "no skill selected" }]),
            { key: "j k", label: "move" },
            {
              key: "enter",
              label: "open",
              disabled: !selected,
              onPress: () => focus(index < 0 ? 0 : index),
            },
            {
              key: "e",
              label: "patch",
              disabled: !selected || editing,
              onPress: () => {
                setPatch("");
                setEditing(true);
              },
            },
            ...(editing ? [{ key: "enter", label: "apply", disabled: saving, onPress: apply }] : []),
            {
              key: "esc",
              label: "close",
              disabled: !selected,
              onPress: () => {
                setEditing(false);
                onSelect(null);
              },
            },
          ]}
        />
      </div>

      {open ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {editing ? (
            <form
              className="px-4 pb-6 pt-2"
              onSubmit={(event) => {
                event.preventDefault();
                apply();
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  setEditing(false);
                }
              }}
            >
              <SectionHead meta="POST /api/skills/{name}/revisions">
                patch {selected}
              </SectionHead>
              <textarea
                autoFocus
                rows={12}
                value={patch}
                spellCheck={false}
                onChange={(event) => setPatch(event.target.value)}
                placeholder="append-only revision note"
                className="mt-2 w-full resize-y border border-line bg-surface px-2 py-1"
              />
              <div className="mt-3 flex items-center gap-4">
                <button type="submit" className="microlabel text-amber hover:text-ink">
                  {saving ? "saving ..." : "[enter] apply"}
                </button>
                <button
                  type="button"
                  className="microlabel hover:text-ink"
                  onClick={() => setEditing(false)}
                >
                  [esc] cancel
                </button>
              </div>
            </form>
          ) : (
            <SkillDetail state={detail.state} />
          )}
        </div>
      ) : null}
    </div>
  );
}

function SkillDetail({ state }: { state: AsyncState<{ skill: Skill } | null> }) {
  if (state.status === "loading") return <Line kind="loading">loading ...</Line>;
  if (state.status === "error") return <Line kind="error">error: {state.error}</Line>;
  if (!state.data) return <Line kind="empty">no skill selected</Line>;

  const { skill } = state.data;
  return (
    <article className="px-4 pb-6 pt-2">
      <header className="flex flex-wrap items-baseline gap-x-3">
        <h2 className="text-amber">{skill.name}</h2>
        <span className="microlabel">v{skill.version}</span>
        <span className="microlabel">{skill.source}</span>
      </header>

      <p className="mt-2 text-muted">{oneLine(skill.description)}</p>

      <SectionHead meta={bytes(skill.body?.length ?? 0)}>body</SectionHead>
      <pre className="mt-1 max-h-[46vh] overflow-auto whitespace-pre-wrap break-words border border-line bg-surface px-2 py-1">
        {text(skill.body)}
      </pre>

      <SectionHead>metadata</SectionHead>
      <KeyValue
        className="mt-1"
        rows={[
          { label: "hash", value: <span className="break-all">{text(skill.hash)}</span> },
          { label: "created", value: text(skill.created_at) },
          { label: "updated", value: text(skill.updated_at) },
        ]}
      />
    </article>
  );
}
