// opencode-memo plugin manifest + hook registration (Bun, zero deps).
// Talks to memo ONLY over its MCP endpoint — never opens the DB file (M-032).
// Config (env, with defaults): MEMO_ENDPOINT (default
// http://localhost:8000/mcp), MEMO_TOKEN (required for --http).
import { Plugin } from "@opencode/plugin";
import { transform, onSessionStart, onAfterWrite } from "./hooks.js";

export const name = "opencode-memo";
export const version = "3.0.0";

// Named re-exports stay for tests/consumers that import drives directly.
export { transform, onSessionStart, onAfterWrite };

export const config = {
  endpoint: process.env.MEMO_ENDPOINT ?? "http://localhost:8000/mcp",
  tokenEnv: "MEMO_TOKEN",
};

const SNAPSHOT_CACHE_MAX = 64;

// V2 entrypoint: the bare named exports above are inert — opencode loads the
// default `Plugin.define` and calls setup(ctx). Three registrations:
//   1. context inject  — hermes block + session snapshot (transform, frozen)
//   2. session-start sync — onSessionStart over remote MCP (frozen per session)
//   3. after-write push — file writes under skills/** + native skill_create/update
export default Plugin.define({
  id: "opencode-memo",
  async setup(ctx) {
    // Frozen per-session snapshot. onSessionStart runs once per session (sync +
    // skill delta apply); every later context render replays the same bytes so
    // the prompt cache stays stable. Map keyed by sessionID.
    const snapshots = new Map();

    await ctx.session.hook("context", async (event) => {
      const sid = String(event.sessionID ?? "default");
      let pending = snapshots.get(sid);
      if (!pending) {
        pending = onSessionStart({})
          .then((r) => transform("", { memoryMd: r?.summary ?? "" }));
        snapshots.set(sid, pending);
        if (snapshots.size > SNAPSHOT_CACHE_MAX) {
          snapshots.delete(snapshots.keys().next().value);
        }
      }
      event.system.push({ type: "text", text: await pending });
    });

    await ctx.tool.hook("execute.after", (event) => {
      if (event.status !== "completed") return;
      const input = event.input ?? {};
      if (event.tool === "skill_create" || event.tool === "skill_update") {
        onAfterWrite({ tool: event.tool, input });
      } else if (event.tool === "write" || event.tool === "edit" || event.tool === "patch") {
        const p = input.path ?? input.filePath;
        if (p) onAfterWrite(String(p));
      }
    });
  },
});
