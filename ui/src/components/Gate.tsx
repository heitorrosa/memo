import { useState } from "react";
import { LogoArt } from "../ascii/LogoArt";
import { Wordmark } from "./Frame";
import { useAutoFocus } from "../hooks/useHotkeys";

/**
 * Full-screen token gate. Nothing renders behind it: no data is fetched until
 * a token is stored.
 */
export function Gate({
  onConnect,
  message,
  busy = false,
}: {
  onConnect: (token: string) => void;
  message?: string;
  busy?: boolean;
}) {
  const [value, setValue] = useState("");
  const inputRef = useAutoFocus<HTMLInputElement>();

  return (
    <div className="relative flex h-full flex-col items-center justify-center gap-7 px-8">
      <LogoArt />
      <div className="relative z-10 flex w-full flex-col items-center gap-7">
        <div className="flex flex-col items-center gap-3">
          <Wordmark className="text-[15px] text-amber-dim" />
          <p className="microlabel">memories · skills · agents</p>
        </div>

        <form
          className="w-full max-w-[27rem] border-t border-line pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            onConnect(value);
          }}
        >
          <label className="microlabel block" htmlFor="memo-token">
            MEMO_TOKEN
          </label>
          <div className="flex items-baseline gap-2">
            <span aria-hidden="true" className="text-amber">
              ›
            </span>
            <input
              id="memo-token"
              ref={inputRef}
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="bearer token"
              className="min-w-0 flex-1 border-b border-line bg-transparent py-1 tracking-[0.18em]"
            />
            <button type="submit" className="microlabel text-amber hover:text-ink">
              [enter] connect
            </button>
          </div>
        </form>

        <div className="flex w-full max-w-[27rem] flex-col gap-1">
          {busy ? <p className="text-muted">connecting ...</p> : null}
          {message ? <p className="text-coral">{message}</p> : null}
        </div>
      </div>
    </div>
  );
}
