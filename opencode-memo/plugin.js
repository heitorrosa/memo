import { Plugin } from "@opencode/plugin";
import {
  transform, onSessionStart, onAfterWrite, createGuard, guardBefore,
  createSkillCreateTool, REMIND_PERSIST,
} from "./hooks.js";

export const name = "opencode-memo";
export const version = "3.0.0";

export { transform, onSessionStart, onAfterWrite, guardBefore, createSkillCreateTool };

export const config = {
  endpoint: process.env.MEMO_ENDPOINT ?? "http://localhost:8000/mcp",
  tokenEnv: "MEMO_TOKEN",
};

const CACHE_MAX = 64;

export default Plugin.define({
  id: "opencode-memo",
  async setup(ctx) {

    const snapshots = new Map();

    const guards = new Map();

    function snapshot(sid) {
      let e = snapshots.get(sid);
      if (e) return e;
      e = { promise: null, text: null };
      e.promise = onSessionStart({})
        .then((r) => transform("", {
          memoryMd: [r?.summary, r?.nudge].filter(Boolean).join("\n\n") }));
      snapshots.set(sid, e);
      if (snapshots.size > CACHE_MAX) snapshots.delete(snapshots.keys().next().value);
      return e;
    }

    function guard(sid) {
      let g = guards.get(sid);
      if (g) return g;
      g = createGuard();
      guards.set(sid, g);
      if (guards.size > CACHE_MAX) guards.delete(guards.keys().next().value);
      return g;
    }

    await ctx.tool.transform((editor) => {
      editor.add(createSkillCreateTool());
    });

    await ctx.session.hook("context", async (event) => {
      const sid = String(event.sessionID ?? "default");
      const e = snapshot(sid);
      if (e.text === null) e.text = await e.promise;
      event.system.push({ type: "text", text: e.text });
      const g = guard(sid);
      if (g.pending.length > 0) {
        event.system.push({ type: "text", text: g.pending.join("\n") });
        g.pending.length = 0;
      }
    });

    await ctx.tool.hook("execute.before", (event) => {
      guardBefore(event, guard(String(event.sessionID ?? "default")), {});
    });

    await ctx.session.hook("compaction", async (event) => {
      const sid = String(event.sessionID ?? "default");
      const e = snapshots.get(sid);
      if (!e) return;
      try {
        if (e.text === null) e.text = await e.promise;
        event.result = {
          summary: `## Session State Summary\n${e.text}\n\n${REMIND_PERSIST}`,
        };
      } catch {

      }
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
