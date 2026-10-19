import { useEffect, useRef } from 'react';
import type { CSSProperties } from 'react';
import { CELL_W, SETTLE_MS, renderFrame } from './logo-mask';

/**
 * Mask-driven ascii art behind the login gate. One rAF loop at 12fps writes
 * textContent on two <pre> layers (dim body, bright scan band) — React never
 * re-renders per frame. The mask lives in ./logo-mask.ts.
 */

const FPS = 12;
/** Grid cells to aim for at any viewport size (budget: <= 6000). */
const TARGET_CELLS = 5200;

/** Soft radial fade; the centre is dimmed further so the gate card stays readable. */
const FADE =
  'radial-gradient(ellipse 72% 68% at 50% 48%, rgb(0 0 0 / 0.34) 0%, #000 50%, transparent 100%)';

const BASE: CSSProperties = {
  position: 'absolute',
  inset: 0,
  margin: 0,
  padding: 0,
  overflow: 'hidden',
  whiteSpace: 'pre',
  fontFamily: 'var(--font-mono)',
  fontSize: '12px',
  lineHeight: 1,
  letterSpacing: 0,
  fontVariantLigatures: 'none',
  color: 'var(--color-amber)',
};

const prefersReduced = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function LogoArt() {
  const dimRef = useRef<HTMLPreElement | null>(null);
  const hotRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    const dim = dimRef.current;
    const hot = hotRef.current;
    if (!dim || !hot) return;

    let cols = 0;
    let rows = 0;

    // Grid sized to the viewport for TARGET_CELLS cells, then stretched by font
    // size so the art is full-bleed. shortcut: assumes a 0.6em mono advance.
    const fit = () => {
      const w = dim.clientWidth || window.innerWidth || 1;
      const h = dim.clientHeight || window.innerHeight || 1;
      cols = Math.max(24, Math.round(Math.sqrt(((w / h) * TARGET_CELLS) / CELL_W)));
      const fontSize = w / (cols * CELL_W);
      rows = Math.max(6, Math.floor(h / fontSize));
      dim.style.fontSize = `${fontSize}px`;
      hot.style.fontSize = `${fontSize}px`;
    };

    const paint = (phase: number, reveal: number) => {
      const frame = renderFrame(phase, cols, rows, reveal);
      dim.textContent = frame.text;
      hot.textContent = frame.hot;
    };

    fit();

    if (prefersReduced()) {
      // Final frame, no rAF at all.
      const still = () => {
        fit();
        paint(3, 1);
      };
      still();
      window.addEventListener('resize', still);
      return () => window.removeEventListener('resize', still);
    }

    paint(0, 0);
    window.addEventListener('resize', fit);

    const start = performance.now();
    let last = 0;
    let raf = 0;
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      if (now - last < 1000 / FPS) return;
      last = now;
      const elapsed = now - start;
      paint(elapsed / 1000, Math.min(1, elapsed / SETTLE_MS));
    };
    raf = requestAnimationFrame(step);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', fit);
    };
  }, []);

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 select-none"
      style={{ opacity: 0.55, maskImage: FADE, WebkitMaskImage: FADE }}
    >
      <pre ref={dimRef} style={{ ...BASE, opacity: 0.45 }} />
      <pre ref={hotRef} style={BASE} />
    </div>
  );
}
