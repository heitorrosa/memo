import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactElement } from 'react';
import { buildFrame, OVERLAY_ROLES, ROLE_VAR } from './grid';
import type { AsciiEdge, AsciiNode, HitBox, Role } from './grid';

export type AsciiStageProps = {
  nodes: AsciiNode[];
  edges: AsciiEdge[];
  cols?: number;
  rows?: number;
  fps?: number;
  className?: string;
  onSelect?: (key: string) => void;
};

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
  cols = 96,
  rows = 14,
  fps = 12,
  className,
  onSelect,
}: AsciiStageProps): ReactElement {
  const baseRef = useRef<HTMLPreElement | null>(null);
  const layers = useRef<Map<Role, HTMLPreElement>>(new Map());
  const data = useRef({ nodes, edges });
  data.current = { nodes, edges };
  const drawRef = useRef<() => void>(() => {});
  const [hits, setHits] = useState<HitBox[]>([]);

  useEffect(() => {
    const base = baseRef.current;
    if (!base) return;

    const interval = 1000 / Math.max(1, fps);
    let angle = 0;
    let last = performance.now();
    let lastDraw = 0;
    let raf = 0;
    let running = false;
    let tick = 0;

    const draw = () => {
      const frame = buildFrame(data.current.nodes, data.current.edges, cols, rows, angle);
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
      angle += dt * 0.21; // rad/s — the slow breath
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
  }, [nodes, edges]);

  return (
    <div
      className={className}
      style={{
        position: 'relative',
        display: 'block',
        overflow: 'hidden',
        fontFamily: 'var(--font-mono,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace)',
        fontSize: '12.5px',
        lineHeight: 1,
      }}
    >
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
  );
}
