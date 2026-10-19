import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { AsciiStage } from "./ascii";
import { Frame, Wordmark } from "./components/Frame";
import { Gate } from "./components/Gate";
import { Line, Panel } from "./components/Panel";
import { SidebarEmblem } from "./components/SidebarEmblem";
import { Toast, type ToastMsg } from "./components/Toast";
import { useAsync } from "./hooks/useAsync";
import { useHotkeys } from "./hooks/useHotkeys";
import { getGraph, getStats, onUnauthorized, setToken, storedToken, type Stats } from "./lib/api";
import { KEYMAP, PANES, type PaneId } from "./lib/keymap";
import { AgentsPanel } from "./panels/AgentsPanel";
import { AppPanel } from "./panels/AppPanel";
import { MemoriesPanel } from "./panels/MemoriesPanel";
import { SkillsPanel } from "./panels/SkillsPanel";

const EMPTY_SELECTION: Record<PaneId, string | null> = {
  memories: null,
  skills: null,
  agents: null,
  app: null,
};

function RailButton({
  label,
  connector,
  count,
  active,
  onClick,
}: {
  label: string;
  connector: string;
  count: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active}
      className={`flex w-full items-center gap-2.5 rounded-[2px] px-3.5 py-2.5 transition-colors duration-150 ${
        active ? "bg-surface-2 text-amber" : "text-muted hover:bg-surface/70 hover:text-ink"
      }`}
    >
      <span aria-hidden="true" className={active ? undefined : "invisible"}>
        ▌
      </span>
      <span aria-hidden="true" className="text-line">
        {connector}
      </span>
      <span className="microlabel text-inherit text-[11.5px]">{label}</span>
      <span
        className={`tabular microlabel ml-auto text-[10.5px] ${
          active ? "text-amber" : "text-faint"
        }`}
      >
        {count}
      </span>
    </button>
  );
}

function KeymapOverlay({ onClose }: { onClose: () => void }) {
  useHotkeys({ Escape: onClose, "?": onClose }, true, true);
  return (
    <div
      className="absolute inset-0 z-20 flex items-center justify-center bg-bg/85"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="keymap"
        className="w-[34rem] max-w-[92vw] border border-line bg-surface px-5 py-4"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-2">
          <span className="microlabel text-muted">keymap</span>
          <span aria-hidden="true" className="h-px flex-1 bg-line" />
          <span className="microlabel">esc close</span>
        </div>
        <dl className="mt-3 grid grid-cols-[6.5rem_1fr_6.5rem_1fr] gap-x-4 gap-y-0.5">
          {KEYMAP.map((binding) => (
            <Fragment key={binding.keys}>
              <dt className="tabular text-amber">{binding.keys}</dt>
              <dd className="text-muted">{binding.label}</dd>
            </Fragment>
          ))}
        </dl>
      </div>
    </div>
  );
}

export default function App() {
  const [token, setTokenState] = useState(() => storedToken());
  const [gateMessage, setGateMessage] = useState("");
  const [pane, setPane] = useState<PaneId>("memories");
  const [selection, setSelection] = useState(EMPTY_SELECTION);
  const [overlay, setOverlay] = useState(false);
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const [reloadSignal, setReloadSignal] = useState(0);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    onUnauthorized(() => {
      setToken("");
      setTokenState("");
      setGateMessage("token rejected — check MEMO_TOKEN");
    });
    return () => onUnauthorized(null);
  }, []);

  const stats = useAsync<Stats | null>(
    () => (token ? getStats() : Promise.resolve(null)),
    [token, reloadSignal],
  );
  const graph = useAsync(
    () => (token ? getGraph(200) : Promise.resolve(null)),
    [token, reloadSignal],
  );

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(id);
  }, [toast]);

  const flash = useCallback((kind: "ok" | "err", text: string) => {
    setToast({ kind, text });
  }, []);

  const focusSearch = useCallback(() => {
    const target = PANES.find((item) => item.id === pane);
    if (target?.search) {
      searchRef.current?.focus();
      return;
    }
    setPane("memories");
    window.requestAnimationFrame(() => searchRef.current?.focus());
  }, [pane]);

  useHotkeys(
    {
      "1": () => setPane("memories"),
      "2": () => setPane("skills"),
      "3": () => setPane("agents"),
      "4": () => setPane("app"),
      "/": focusSearch,
      "?": () => setOverlay((open) => !open),
      r: () => setReloadSignal((value) => value + 1),
    },
    token !== "" && !overlay,
  );

  const connect = useCallback((value: string) => {
    setToken(value);
    setGateMessage("");
    setTokenState(value.trim());
  }, []);

  if (!token) {
    return (
      <Frame>
        <Gate onConnect={connect} message={gateMessage} />
      </Frame>
    );
  }

  const statsData = stats.state.status === "ready" ? stats.state.data : null;
  const counts = statsData?.counts ?? null;
  const keyboard = !overlay;

  return (
    <Frame>
      <div className="grid h-full grid-rows-[minmax(0,1fr)]">
        <div className="grid min-h-0 grid-cols-[264px_minmax(0,1fr)]">
          <aside className="flex min-h-0 flex-col border-r border-line">
            <div className="flex flex-col items-center justify-center gap-3 border-b border-line-soft px-5 py-5">
              <SidebarEmblem />
              <Wordmark className="text-[12px] text-amber" />
            </div>

            <div className="flex min-h-0 flex-1 items-center px-4 py-6">
              <nav className="flex w-full flex-col gap-1.5">
                {PANES.map((item, index) => (
                  <RailButton
                    key={item.id}
                    label={item.label}
                    connector={index === PANES.length - 1 ? "└─" : "├─"}
                    active={item.id === pane}
                    onClick={() => setPane(item.id)}
                    count={
                      counts === null
                        ? "·"
                        : item.id === "memories"
                          ? String(counts.memories)
                          : item.id === "skills"
                            ? String(counts.skills)
                            : item.id === "agents"
                              ? String(counts.agents)
                              : `v${statsData?.version ?? 3}`
                    }
                  />
                ))}
              </nav>
            </div>
          </aside>

          <div className="grid min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)]">
            <Panel
              title="MEMORY GRAPH"
              className="scan shrink-0 border-b border-line"
              meta={
                graph.state.status === "ready" && graph.state.data
                  ? `${graph.state.data.nodes.length} nodes · ${graph.state.data.edges.length} edges${
                      graph.state.data.similar
                        ? ` · ${graph.state.data.similar.length} links`
                        : ""
                    }`
                  : undefined
              }
            >
              <div className="h-[clamp(12rem,30vh,22rem)] w-full min-w-0 overflow-hidden px-3 pb-1">
                {graph.state.status === "loading" ? (
                  <Line kind="loading">loading ...</Line>
                ) : graph.state.status === "error" ? (
                  <Line kind="error">
                    graph unavailable: {graph.state.error} — the stage stays empty on purpose
                  </Line>
                ) : (
                  <AsciiStage
                    nodes={graph.state.data?.nodes ?? []}
                    edges={graph.state.data?.edges ?? []}
                    similar={graph.state.data?.similar}
                    focus={selection.memories}
                    onSelect={(key) => {
                      setPane("memories");
                      setSelection((current) => ({ ...current, memories: key }));
                    }}
                  />
                )}
              </div>
            </Panel>

            <div className="relative min-h-0">
              <MemoriesPanel
                active={pane === "memories"}
                keys={keyboard}
                selected={selection.memories}
                onSelect={(key) => setSelection((current) => ({ ...current, memories: key }))}
                onToast={flash}
                reloadSignal={reloadSignal}
                searchRef={searchRef}
                onStats={stats.reload}
              />
              <SkillsPanel
                active={pane === "skills"}
                keys={keyboard}
                selected={selection.skills}
                onSelect={(key) => setSelection((current) => ({ ...current, skills: key }))}
                onToast={flash}
                reloadSignal={reloadSignal}
                searchRef={searchRef}
                onStats={stats.reload}
              />
              <AgentsPanel
                active={pane === "agents"}
                keys={keyboard}
                selected={selection.agents}
                onSelect={(key) => setSelection((current) => ({ ...current, agents: key }))}
                onToast={flash}
                reloadSignal={reloadSignal}
                searchRef={searchRef}
                onStats={stats.reload}
              />
              <AppPanel
                active={pane === "app"}
                keys={keyboard}
                selected={selection.app}
                onSelect={(key) => setSelection((current) => ({ ...current, app: key }))}
                onToast={flash}
                reloadSignal={reloadSignal}
                searchRef={searchRef}
                onStats={stats.reload}
              />
            </div>
          </div>
        </div>
      </div>

      {toast ? (
        <div role="status" className="pointer-events-none fixed bottom-2 right-3 z-30">
          <Toast toast={toast} />
        </div>
      ) : null}

      {overlay ? <KeymapOverlay onClose={() => setOverlay(false)} /> : null}
    </Frame>
  );
}
