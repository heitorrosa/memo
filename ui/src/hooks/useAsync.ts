import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "../lib/format";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "ready"; data: T };

/**
 * One request per dependency change. `reload()` bumps an internal counter, so
 * panels can refetch after a write without juggling keys.
 */
export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]) {
  const loadRef = useRef(load);
  loadRef.current = load;

  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setState({ status: "loading" });
    loadRef.current().then(
      (data) => {
        if (live) setState({ status: "ready", data });
      },
      (err: unknown) => {
        if (live) setState({ status: "error", error: describeError(err) });
      },
    );
    return () => {
      live = false;
    };
    // deps are the caller's query tuple; tick is the refresh signal.
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  return { state, reload };
}
