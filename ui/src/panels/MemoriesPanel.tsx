import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Chip } from "../components/Chip";
import { KeyValue } from "../components/KeyValue";
import { Line, Panel, SearchBar, SectionHead, Hints, type PanelProps } from "../components/Panel";
import { Prompt } from "../components/Prompt";
import { Table, type Column } from "../components/Table";
import { useAsync, type AsyncState } from "../hooks/useAsync";
import { useHotkeys } from "../hooks/useHotkeys";
import {
  deleteMemory,
  getMemory,
  listMemories,
  saveMemory,
  writeFailed,
  type Memory,
  type MemoryDetail,
  type MemoryLink,
} from "../lib/api";
import {
  TYPES,
  clip,
  describeError,
  oneLine,
  relColor,
  relGlyph,
  relTime,
  text,
  typeColor,
} from "../lib/format";

const KEY_COL: Column<Memory> = {
  label: "KEY",
  width: "minmax(0,1.2fr)",
  render: (memory) => <span className="text-amber">{memory.key}</span>,
};

const TYPE_COL: Column<Memory> = {
  label: "TYPE",
  width: "6.5rem",
  render: (memory) => (
    <span className="microlabel" style={{ color: typeColor(memory.type) }}>
      {memory.type}
    </span>
  ),
};

const VALUE_COL: Column<Memory> = {
  label: "VALUE",
  width: "minmax(0,2fr)",
  render: (memory) => clip(oneLine(memory.value), 160),
};

const USES_COL: Column<Memory> = {
  label: "USES",
  width: "3rem",
  align: "right",
  render: (memory) => memory.uses,
};

const UPDATED_COL: Column<Memory> = {
  label: "UPDATED",
  width: "5rem",
  align: "right",
  render: (memory) => relTime(memory.updated_at),
};

/**
 * USES and UPDATED go first, then TYPE, as the list narrows: KEY and VALUE are
 * what the pane is read for, and a key truncated to `pr…` is not a key.
 */
function columnsFor(width: number): Column<Memory>[] {
  if (width >= 640) return [KEY_COL, TYPE_COL, VALUE_COL, USES_COL, UPDATED_COL];
  if (width >= 430) return [KEY_COL, TYPE_COL, VALUE_COL];
  return [KEY_COL, VALUE_COL];
}

/**
 * Live width of the list column, so the table can shed columns before cramping.
 * Measured on layout, not with a ResizeObserver: RO callbacks ride animation
 * frames, and a browser that paints no frames never delivers them. `when` is
 * `open` — the only state that changes the list column's width.
 */
function useWidth<T extends HTMLElement>(when: boolean) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => setWidth(node.clientWidth);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [when]);
  return [ref, width] as const;
}

interface Draft {
  key: string;
  value: string;
  type: string;
  links: string;
}

const EMPTY_DRAFT: Draft = { key: "", value: "", type: "context", links: "" };

export function MemoriesPanel({
  active,
  keys,
  selected,
  onSelect,
  onToast,
  reloadSignal,
  searchRef,
  onStats,
}: PanelProps) {
  const [search, setSearch] = useState("");
  const [committed, setCommitted] = useState("");
  const [filter, setFilter] = useState("all");
  const [showSuperseded, setShowSuperseded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  // Nothing selected: the detail column is dropped so the list gets the whole
  // pane instead of paying ~300px for a blank sheet.
  const open = selected !== null || composing;
  const [listRef, listWidth] = useWidth<HTMLDivElement>(open);
  const columns = columnsFor(listWidth);

  useEffect(() => {
    const id = window.setTimeout(() => setCommitted(search.trim()), 250);
    return () => window.clearTimeout(id);
  }, [search]);

  const list = useAsync(
    () =>
      listMemories({
        q: committed || undefined,
        type: filter === "all" ? undefined : filter,
        status: showSuperseded ? undefined : "current",
      }),
    [committed, filter, showSuperseded, reloadSignal],
  );

  const detail = useAsync<MemoryDetail | null>(
    () => (selected ? getMemory(selected) : Promise.resolve(null)),
    [selected, reloadSignal],
  );

  const items = list.state.status === "ready" ? list.state.data.items : [];
  const index = items.findIndex((item) => item.key === selected);

  const focus = (next: number) => {
    if (items.length === 0) return;
    const clamped = Math.max(0, Math.min(items.length - 1, next));
    const item = items[clamped];
    if (item) onSelect(item.key);
  };

  const dismiss = () => {
    setConfirming(false);
    setComposing(false);
    onSelect(null);
  };

  // While a composer or a confirm is open the main bindings are off, and both
  // keep focus inside fields — so dismissal needs the input guard bypassed.
  useHotkeys({ Escape: dismiss }, keys && (confirming || composing), true);

  useHotkeys(
    {
      j: () => focus(index + 1),
      k: () => focus(index - 1),
      g: () => focus(0),
      G: () => focus(items.length - 1),
      Enter: () => focus(index < 0 ? 0 : index),
      Escape: dismiss,
      d: () => {
        if (selected) setConfirming(true);
      },
      n: () => {
        setDraft(EMPTY_DRAFT);
        setComposing(true);
      },
    },
    keys && !confirming && !composing,
  );

  const remove = () => {
    setConfirming(false);
    if (!selected) return;
    const key = selected;
    deleteMemory(key).then(
      (result) => {
        const bad = writeFailed(result);
        if (bad) {
          onToast("err", `delete failed: ${bad}`);
          return;
        }
        onToast("ok", `deleted ${key}`);
        onSelect(null);
        list.reload();
        onStats();
      },
      (error: unknown) => onToast("err", `delete failed: ${describeError(error)}`),
    );
  };

  const submit = () => {
    if (saving) return;
    const key = draft.key.trim();
    if (!key) {
      onToast("err", "key is required");
      return;
    }
    const links: MemoryLink[] = draft.links
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const cut = line.indexOf(":");
        if (cut < 0) return { rel: line, to: "" };
        return { rel: line.slice(0, cut).trim(), to: line.slice(cut + 1).trim() };
      })
      .filter((link) => link.rel && link.to);

    setSaving(true);
    saveMemory({ key, value: draft.value, type: draft.type, links }).then(
      (result) => {
        setSaving(false);
        const bad = writeFailed(result);
        if (bad) {
          onToast("err", `save failed: ${bad}`);
          return;
        }
        onToast("ok", `saved ${key}`);
        setComposing(false);
        setDraft(EMPTY_DRAFT);
        onSelect(key);
        list.reload();
        onStats();
      },
      (error: unknown) => {
        setSaving(false);
        onToast("err", `save failed: ${describeError(error)}`);
      },
    );
  };

  // Enter saves from anywhere in the pane, not only from a field: the composer
  // hint says `enter save`, so it has to be true wherever focus sits.
  useHotkeys(
    {
      Enter: () => {
        if ((document.activeElement as HTMLElement | null)?.tagName === "BUTTON") return;
        submit();
      },
    },
    keys && composing && !saving,
  );

  const meta =
    list.state.status === "ready"
      ? `${list.state.data.total} rows${committed ? ` · q "${clip(committed, 18)}"` : ""}${
          showSuperseded ? "" : " · current"
        }`
      : undefined;

  return (
    <div
      className={
        active
          ? `grid h-full ${
              open
                ? "grid-cols-[minmax(0,1.3fr)_minmax(20rem,1fr)]"
                : "grid-cols-[minmax(0,1fr)]"
            }`
          : "hidden"
      }
    >
      <div
        ref={listRef}
        className={`flex min-h-0 flex-col ${open ? "border-r border-line" : ""}`}
      >
        <Panel
          title="MEMORIES"
          meta={meta}
          className="min-h-0 flex-1"
          bodyClassName="overflow-y-auto"
        >
          <SearchBar
            inputRef={active ? searchRef : undefined}
            value={search}
            onChange={setSearch}
            placeholder="semantic + graph search"
          />
          <div className="flex flex-wrap items-center gap-x-3 border-b border-line-soft px-3 py-1">
            <Chip label="all" active={filter === "all"} onClick={() => setFilter("all")} />
            {TYPES.map((type) => (
              <Chip
                key={type}
                label={type}
                active={filter === type}
                color={typeColor(type)}
                onClick={() => setFilter(filter === type ? "all" : type)}
              />
            ))}
            <span className="flex-1" />
            <Chip
              label="superseded"
              toggle
              active={showSuperseded}
              color="var(--color-muted)"
              onClick={() => setShowSuperseded(!showSuperseded)}
            />
          </div>

          {list.state.status === "loading" ? <Line kind="loading">loading ...</Line> : null}
          {list.state.status === "error" ? (
            <Line kind="error">error: {list.state.error}</Line>
          ) : null}
          {list.state.status === "ready" && items.length === 0 ? (
            <Line kind="empty">no memories match</Line>
          ) : null}
          {items.length > 0 ? (
            <Table
              columns={columns}
              rows={items}
              rowKey={(memory) => memory.key}
              selected={selected}
              onSelect={onSelect}
              rowClassName={(memory) => (memory.status === "superseded" ? "text-faint" : "")}
            />
          ) : null}
        </Panel>
        <Hints
          items={[
            ...(selected ? [] : [{ key: "", label: "no memory selected" }]),
            { key: "j k", label: "move" },
            {
              key: "enter",
              label: "open",
              disabled: !selected,
              onPress: () => focus(index < 0 ? 0 : index),
            },
            {
              key: "n",
              label: "new",
              onPress: () => {
                setDraft(EMPTY_DRAFT);
                setComposing(true);
              },
            },
            {
              key: "d",
              label: "delete",
              disabled: !selected,
              onPress: () => {
                if (selected) setConfirming(true);
              },
            },
            { key: "esc", label: "close", disabled: !selected, onPress: dismiss },
          ]}
        />
      </div>

      {open ? (
        <div className="flex min-h-0 flex-col">
          {confirming ? (
            <Prompt
              danger
              label={`delete ${selected} ?`}
              onYes={remove}
              onNo={() => setConfirming(false)}
            />
          ) : null}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {composing ? (
              <Composer
                draft={draft}
                saving={saving}
                onChange={setDraft}
                onSubmit={submit}
                onCancel={() => setComposing(false)}
              />
            ) : (
              <Detail state={detail.state} onSelect={onSelect} />
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Detail({
  state,
  onSelect,
}: {
  state: AsyncState<MemoryDetail | null>;
  onSelect: (key: string) => void;
}) {
  if (state.status === "loading") return <Line kind="loading">loading ...</Line>;
  if (state.status === "error") return <Line kind="error">error: {state.error}</Line>;
  if (!state.data) return <Line kind="empty">no memory selected</Line>;

  const { memory, edges, aliases, family, declarers } = state.data;
  const related = state.data.related ?? [];
  const groups = new Map<string, typeof edges>();
  for (const edge of edges) {
    const bucket = groups.get(edge.rel);
    if (bucket) bucket.push(edge);
    else groups.set(edge.rel, [edge]);
  }

  return (
    <article className="px-4 pb-6 pt-2">
      <header className="flex flex-wrap items-baseline gap-x-3">
        <h2 className="text-amber">{memory.key}</h2>
        <span className="microlabel" style={{ color: typeColor(memory.type) }}>
          {memory.type}
        </span>
        <span className="microlabel">{text(memory.status)}</span>
      </header>

      <pre className="mt-3 whitespace-pre-wrap break-words font-mono">{text(memory.value)}</pre>

      <SectionHead>metadata</SectionHead>
      <KeyValue
        className="mt-1"
        rows={[
          { label: "namespace", value: text(memory.namespace) },
          { label: "stability", value: text(memory.stability) },
          { label: "score", value: text(memory.score) },
          { label: "uses", value: text(memory.uses) },
          { label: "degree", value: text(memory.degree) },
          { label: "agent", value: text(memory.agent_id) },
          { label: "valid from", value: text(memory.valid_from) },
          { label: "valid until", value: text(memory.valid_until) },
          { label: "superseded by", value: text(memory.superseded_by) },
          { label: "created", value: text(memory.created_at) },
          { label: "updated", value: text(memory.updated_at) },
        ]}
      />

      <SectionHead meta={related.length}>related</SectionHead>
      {related.length === 0 ? (
        <p className="text-faint">no related memories</p>
      ) : (
        <ul className="mt-1">
          {related.map((row, position) => (
            <li key={row.key}>
              <button
                type="button"
                onClick={() => onSelect(row.key)}
                className="flex w-full items-baseline gap-2 hover:text-amber"
              >
                <span aria-hidden="true" className="text-line">
                  {position === related.length - 1 ? "└─" : "├─"}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink">{row.key}</span>
                <span className="tabular microlabel">{row.score.toFixed(2)}</span>
                <span className="microlabel" style={{ color: typeColor(row.type) }}>
                  {row.type}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <SectionHead meta={edges.length}>edges</SectionHead>
      {edges.length === 0 ? (
        <p className="text-faint">no edges</p>
      ) : (
        [...groups.entries()].map(([rel, group]) => (
          <div key={rel} className="mt-2">
            <p className="microlabel" style={{ color: relColor(rel) }}>
              {relGlyph(rel)} {rel} ({group.length})
            </p>
            <ul>
              {group.map((edge, position) => (
                <li key={`${edge.direction}:${edge.src_key}:${edge.dst_key}:${position}`}>
                  <button
                    type="button"
                    onClick={() => onSelect(edge.direction === "out" ? edge.dst_key : edge.src_key)}
                    className="flex w-full items-baseline gap-2 hover:text-amber"
                  >
                    <span aria-hidden="true" className="text-line">
                      {position === group.length - 1 ? "└─" : "├─"}
                    </span>
                    <span className="text-faint">{edge.direction === "out" ? "→" : "←"}</span>
                    <span className="min-w-0 flex-1 truncate">
                      {edge.direction === "out" ? edge.dst_key : edge.src_key}
                    </span>
                    <span className="tabular microlabel">w{edge.weight}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      {/* aliases are 0 rows on the live DB — the empty line is the normal case */}
      <SectionHead meta={aliases.length}>aliases</SectionHead>
      {aliases.length === 0 ? (
        <p className="text-faint">no aliases</p>
      ) : (
        <ul className="mt-1">
          {aliases.map((alias, position) => (
            <li key={`${alias.segment}:${position}`} className="flex items-baseline gap-2">
              <span aria-hidden="true" className="text-line">
                {position === aliases.length - 1 ? "└─" : "├─"}
              </span>
              <span className="text-ink">{alias.segment}</span>
              <span className="text-faint">→</span>
              <span className="text-muted">{alias.canonical}</span>
              <span className="microlabel">{alias.status}</span>
            </li>
          ))}
        </ul>
      )}

      {family.length > 1 ? (
        <>
          <SectionHead meta={family.length}>family</SectionHead>
          <ul className="mt-1">
            {family.map((row, position) => (
              <li key={`${row.key}:${position}`} className="flex items-baseline gap-2">
                <span aria-hidden="true" className="text-line">
                  {position === family.length - 1 ? "└─" : "├─"}
                </span>
                <button
                  type="button"
                  onClick={() => onSelect(row.key)}
                  className={
                    row.status === "superseded"
                      ? "text-faint line-through hover:text-muted"
                      : row.key === memory.key
                        ? "text-amber"
                        : "text-ink hover:text-amber"
                  }
                >
                  {row.key}
                </button>
                <span className="microlabel">{text(row.relation_type)}</span>
                <span className="tabular microlabel">{relTime(row.valid_from)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {declarers.length > 0 ? (
        <>
          <SectionHead meta={declarers.length}>declarers</SectionHead>
          <ul className="mt-1">
            {declarers.map((row, position) => (
              <li key={`${row.src_key}:${row.rel}:${position}`} className="flex items-baseline gap-2">
                <span aria-hidden="true" className="text-line">
                  {position === declarers.length - 1 ? "└─" : "├─"}
                </span>
                <span className="truncate text-muted">
                  {row.src_key} {relGlyph(row.rel)} {row.dst_key}
                </span>
                <span className="microlabel">
                  {row.declarers.length ? row.declarers.join(",") : "-"}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </article>
  );
}

function Composer({
  draft,
  saving,
  onChange,
  onSubmit,
  onCancel,
}: {
  draft: Draft;
  saving: boolean;
  onChange: (draft: Draft) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const field = "border-b border-line bg-transparent py-0.5";
  return (
    <form
      className="px-4 pb-6 pt-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
          return;
        }
        if (event.key !== "Enter" || event.shiftKey) return;
        const target = event.target as HTMLElement;
        // links are one `rel:key` per line, so Enter there stays a newline
        if (target.id === "memory-links" || target.tagName === "BUTTON") return;
        if (target.tagName !== "INPUT" && target.tagName !== "TEXTAREA") return;
        event.preventDefault();
        onSubmit();
      }}
    >
      <SectionHead meta="POST /api/memories">new memory</SectionHead>

      <label className="microlabel mt-2 block" htmlFor="memory-key">
        key
      </label>
      <input
        id="memory-key"
        autoFocus
        value={draft.key}
        spellCheck={false}
        onChange={(event) => onChange({ ...draft, key: event.target.value })}
        placeholder="namespace.slug"
        className={`w-full ${field} text-amber`}
      />

      <div className="mt-2 flex flex-wrap items-center gap-x-3">
        <span className="microlabel">type</span>
        {TYPES.map((type) => (
          <Chip
            key={type}
            label={type}
            active={draft.type === type}
            color={typeColor(type)}
            onClick={() => onChange({ ...draft, type })}
          />
        ))}
      </div>

      <label className="microlabel mt-3 block" htmlFor="memory-value">
        value
      </label>
      <textarea
        id="memory-value"
        rows={9}
        value={draft.value}
        onChange={(event) => onChange({ ...draft, value: event.target.value })}
        className="w-full resize-y border border-line bg-surface px-2 py-1"
      />
      <span className="microlabel mt-1 block">enter saves · shift+enter newline</span>

      <label className="microlabel mt-3 block" htmlFor="memory-links">
        links — one `rel:key` per line
      </label>
      <textarea
        id="memory-links"
        rows={3}
        value={draft.links}
        spellCheck={false}
        onChange={(event) => onChange({ ...draft, links: event.target.value })}
        placeholder="supports:other.memory.key"
        className="w-full resize-y border border-line bg-surface px-2 py-1"
      />

      <div className="mt-3 flex items-center gap-4">
        <button type="submit" className="microlabel text-amber hover:text-ink">
          {saving ? "saving ..." : "[enter] save"}
        </button>
        <button type="button" className="microlabel hover:text-ink" onClick={onCancel}>
          [esc] cancel
        </button>
      </div>
    </form>
  );
}
