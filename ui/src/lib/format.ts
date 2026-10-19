import { ApiError } from "./api";

export const TYPES = ["preference", "analysis", "feedback", "context"] as const;

const TYPE_COLOR: Record<string, string> = {
  preference: "var(--color-amber)",
  analysis: "var(--color-violet)",
  feedback: "var(--color-coral)",
  context: "var(--color-blue)",
};

export function typeColor(type: string): string {
  return TYPE_COLOR[type] ?? "var(--color-muted)";
}

const REL_COLOR: Record<string, string> = {
  mentions: "var(--color-faint)",
  supports: "var(--color-green)",
  contradicts: "var(--color-coral)",
  derived_from: "var(--color-violet)",
  same_as: "var(--color-blue)",
};

const REL_GLYPH: Record<string, string> = {
  mentions: "-",
  supports: "+",
  contradicts: "x",
  derived_from: "<",
  same_as: "=",
};

export function relColor(rel: string): string {
  return REL_COLOR[rel] ?? "var(--color-muted)";
}

export function relGlyph(rel: string): string {
  return REL_GLYPH[rel] ?? "·";
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function relTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const secs = Math.round((Date.now() - ms) / 1000);
  if (secs < 1) return "now";
  if (secs < MINUTE) return `${secs}s`;
  if (secs < HOUR) return `${Math.floor(secs / MINUTE)}m`;
  if (secs < DAY) return `${Math.floor(secs / HOUR)}h`;
  if (secs < 30 * DAY) return `${Math.floor(secs / DAY)}d`;
  return iso.slice(0, 10);
}

export function bytes(size: number | null | undefined): string {
  if (size === null || size === undefined || !Number.isFinite(size)) return "-";
  if (size < 1000) return `${size}b`;
  if (size < 1_000_000) return `${(size / 1000).toFixed(1)}k`;
  return `${(size / 1_000_000).toFixed(1)}m`;
}

export function pct(value: number, total: number): string {
  if (!total || !Number.isFinite(value) || !Number.isFinite(total)) return "-";
  return `${Math.round((value / total) * 100)}%`;
}

export function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function oneLine(value: string | null | undefined): string {
  if (!value) return "-";
  return value.replace(/\s+/g, " ").trim() || "-";
}

export function clip(value: string, width: number): string {
  if (value.length <= width) return value;
  return `${value.slice(0, Math.max(0, width - 1))}…`;
}

export function hash7(hash: string | null | undefined): string {
  if (!hash) return "-";
  return hash.slice(0, 7);
}

const EIGHTHS = ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"];

export function bar(value: number, max: number, width = 24): string {
  const ceiling = max > 0 ? max : 1;
  const eighths = Number.isFinite(value)
    ? Math.max(0, Math.round((Math.max(0, value) / ceiling) * width * 8))
    : 0;
  const full = Math.min(width, Math.floor(eighths / 8));
  const rest = eighths - full * 8;
  const partial = rest > 0 && full < width ? EIGHTHS[rest - 1] : "";
  const used = full + (partial ? 1 : 0);
  return "█".repeat(full) + partial + "·".repeat(Math.max(0, width - used));
}

export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return `cannot reach the server (${err.message})`;
    if (err.code === `http_${err.status}`) return err.message;
    return `${err.code}: ${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}
