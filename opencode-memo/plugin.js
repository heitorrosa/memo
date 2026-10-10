// opencode-memo plugin manifest + hook registration (Bun, zero deps).
// Talks to memo ONLY over its MCP endpoint — never opens the DB file (M-032).
// Config (env, with defaults): MEMO_ENDPOINT (default
// http://localhost:8000/mcp), MEMO_TOKEN (required for --http).
export const name = "opencode-memo";
export const version = "3.0.0";

export { transform, onSessionStart, onAfterWrite } from "./hooks.js";

export const config = {
  endpoint: process.env.MEMO_ENDPOINT ?? "http://localhost:8000/mcp",
  tokenEnv: "MEMO_TOKEN",
};
