import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactElement } from 'react';
import { buildFrame, hashKey, layout, OVERLAY_ROLES, ROLE_VAR } from './grid';
import type { AsciiEdge, AsciiLink, AsciiNode, HitBox, Role } from './grid';

export type AsciiStageProps = {
  nodes: AsciiNode[];
  edges: AsciiEdge[];
  /** Cosine neighbour pairs. Absent (old server) -> hash layout, no strands. */
  similar?: AsciiLink[];
  /** Selected memory key: it and its neighbours stay lit, the rest dims. */
  focus?: string | null;
  /** Optional overrides. Absent -> measured from the pane. */
  cols?: number;
  rows?: number;
  fps?: number;
  className?: string;
  onSelect?: (key: string) => void;
};

/** Pre-measurement fallback: 12.5px monospace, the historical default. */
const CELL0 = { charW: 7.5, lineH: 12.5, cols: 96, rows: 14 };

const prefersReduced = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const CELL: CSSProperties = {
  margin: 0,
  padding: 0,
  font: 'inherit',
  letterSpacing: 0,
  whiteSpace: 'pre',
  tabSize: 1,
};

export function AsciiStage({
  nodes,
  edges,
  similar = [],
  focus = null,
  cols: colsProp,
  rows: rowsProp,
  fps = 12,
  className,
  onSelect,
}: AsciiStageProps): ReactElement {
  const outerRef = useRef<HTMLDivElement | null>(null);
  const measureRef = useRef<HTMLSpanElement | null>(null);
  const baseRef = useRef<HTMLPreElement | null>(null);
  const layers = useRef<Map<Role, HTMLPreElement>>(new Map());
  const [hits, setHits] = useState<HitBox[]>([]);
  const [measured, setMeasured] = useState(CELL0);

  // Measurement pass: cell metrics from a 10-char probe, pane size from the
  // outer box. Re-run on resize, on font load, and rAF-throttled; only commit
  // when an integer changes so a drag does not spawn a layout storm.
  useEffect(() => {
    const outer = outerRef.current;
    const probe = measureRef.current;
    if (!outer || !probe) return;
    let raf = 0;
    const measure = () => {
      raf = 0;
      const charW = probe.getBoundingClientRect().width / 10;
      if (!charW) return;
      const pre = baseRef.current;
      let lineH = pre ? parseFloat(getComputedStyle(pre).lineHeight) : NaN;
      if (Number.isNaN(lineH)) lineH = charW * 1.6;
      const next = {
        charW,
        lineH,
        cols: Math.min(320, Math.max(40, Math.floor(outer.clientWidth / charW))),
        rows: Math.max(8, Math.floor(outer.clientHeight / lineH)),
      };
      setMeasured((prev) =>
        Math.round(prev.charW) === Math.round(next.charW) &&
        Math.round(prev.lineH) === Math.round(next.lineH) &&
        prev.cols === next.cols &&
        prev.rows === next.rows
          ? prev
          : next,
      );
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };
    measure();
    const ro = new ResizeObserver(schedule);
    ro.observe(outer);
    if (typeof document !== 'undefined' && document.fonts) document.fonts.ready.then(schedule);
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  const cols = colsProp ?? measured.cols;
  const rows = rowsProp ?? measured.rows;
  const { charW, lineH } = measured;

  // One layout per payload: node identity, link count, the keys themselves and
  // the pane size decide it, so phase, focus and toasts never pay for a
  // relaxation pass. The layout is a fixed chart, not a rotating field.
  const signature = useMemo(
    () =>
      `${nodes.length}:${edges.length}:${similar.length}:${nodes.reduce(
        (h, n) => (Math.imul(h, 31) + hashKey(n.key)) | 0,
        7,
      )}`,
    [nodes, edges, similar],
  );
  const points = useMemo(() => layout(nodes, edges, similar, cols, rows), [signature, cols, rows]);
  const data = useRef({ nodes, edges, similar, focus, points });
  data.current = { nodes, edges, similar, focus, points };

  const drawRef = useRef<() => void>(() => {});

  useEffect(() => {
    const base = baseRef.current;
    if (!base) return;

    const interval = 1000 / Math.max(1, fps);
    let phase = 0;
    let last = performance.now();
    let lastDraw = 0;
    let raf = 0;
    let running = false;
    let tick = 0;

    const draw = () => {
      const frame = buildFrame(data.current.nodes, data.current.edges, cols, rows, phase, {
        similar: data.current.similar,
        focus: data.current.focus,
        pos: data.current.points,
      });
      base.textContent = frame.text;
      for (const role of OVERLAY_ROLES) {
        const el = layers.current.get(role);
        if (!el) continue;
        const layer = frame.overlays.find((o) => o.role === role);
        el.textContent = layer ? layer.text : '';
      }
      if (onSelect && !frame.dim && ++tick % 3 === 0) setHits(frame.hits);
    };
    drawRef.current = draw;

    const step = (now: number) => {
      if (!running) return;
      raf = requestAnimationFrame(step);
      const dt = (now - last) / 1000;
      last = now;
      // rad/s: the brightness band slides 12 cells per radian (about 25s
      // across a 243x22 pane). Nothing else moves.
      phase += dt * 0.9;
      if (now - lastDraw >= interval) {
        lastDraw = now;
        draw();
      }
    };

    const start = () => {
      if (running) return;
      running = true;
      last = performance.now();
      raf = requestAnimationFrame(step);
    };
    const stop = () => {
      running = false;
      cancelAnimationFrame(raf);
    };
    const onVisibility = () => (document.hidden ? stop() : start());

    draw();
    if (!prefersReduced()) {
      start();
      document.addEventListener('visibilitychange', onVisibility);
    }
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [cols, rows, fps, onSelect]);

  // Static (reduced-motion) path still tracks prop changes.
  useEffect(() => {
    drawRef.current();
  }, [nodes, edges, similar, focus, points]);

  return (
    <div
      ref={outerRef}
      className={className}
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        // min-width 0 + max-width 100% keep the exact-size child from inflating
        // an ancestor auto track (which used to strand the art off-pane).
        width: '100%',
        minWidth: 0,
        maxWidth: '100%',
        // height 100% is load-bearing: an auto-height outer is sized BY the
        // exact-size child, so the row count would confirm its own guess.
        height: '100%',
        overflow: 'hidden',
        fontFamily: 'var(--font-mono,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace)',
        fontSize: '12.5px',
        lineHeight: 1,
      }}
    >
      <span
        ref={measureRef}
        aria-hidden="true"
        style={{ position: 'absolute', visibility: 'hidden', whiteSpace: 'pre', pointerEvents: 'none' }}
      >
        0000000000
      </span>
      {/* Exact box: one child sized cols x rows cells, so the hit layer's
          percentages are percentages of the grid, not of the pane. */}
      <div style={{ position: 'relative', width: cols * charW, height: rows * lineH }}>
        <pre ref={baseRef} aria-hidden="true" style={{ ...CELL, position: 'relative', color: ROLE_VAR.ink }} />
        {OVERLAY_ROLES.map((role) => (
          <pre
            key={role}
            aria-hidden="true"
            ref={(el) => {
              if (el) layers.current.set(role, el);
              else layers.current.delete(role);
            }}
            style={{
              ...CELL,
              position: 'absolute',
              inset: 0,
              pointerEvents: 'none',
              color: ROLE_VAR[role],
            }}
          />
        ))}
        {onSelect && (
          <div style={{ position: 'absolute', inset: 0 }}>
            {hits.map((h) => (
              <button
                key={h.key}
                type="button"
                aria-label={h.key}
                onClick={() => onSelect(h.key)}
                style={{
                  position: 'absolute',
                  left: `${(h.col / cols) * 100}%`,
                  top: `${(h.row / rows) * 100}%`,
                  width: `${100 / cols}%`,
                  height: `${100 / rows}%`,
                  margin: 0,
                  padding: 0,
                  border: 0,
                  background: 'transparent',
                  color: 'transparent',
                  cursor: 'pointer',
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
