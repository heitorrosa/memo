export interface Pane {
  id: PaneId;
  n: string;
  label: string;
  search: boolean;
}

export type PaneId = "memories" | "skills" | "agents" | "app";

export const PANES: Pane[] = [
  { id: "memories", n: "1", label: "MEMORIES", search: true },
  { id: "skills", n: "2", label: "SKILLS", search: true },
  { id: "agents", n: "3", label: "AGENTS", search: false },
  { id: "app", n: "4", label: "APP", search: false },
];

export interface Binding {
  keys: string;
  label: string;
}

export const KEYMAP: Binding[] = [
  { keys: "1 2 3 4", label: "switch pane" },
  { keys: "/", label: "focus search" },
  { keys: "j k", label: "move selection" },
  { keys: "g G", label: "first / last" },
  { keys: "enter", label: "open selected" },
  { keys: "esc", label: "back / close / leave field" },
  { keys: "r", label: "refresh pane" },
  { keys: "d", label: "delete selected" },
  { keys: "n", label: "new memory" },
  { keys: "e", label: "edit skill revision" },
  { keys: "?", label: "this keymap" },
];
