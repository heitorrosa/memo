import { useEffect, useRef } from 'react';
import type { CSSProperties, ReactElement } from 'react';

export type AsciiBannerProps = { text?: string; className?: string };

// 5-row block font. '#' is ink, ' ' is ground, '@' is the shimmer highlight.
const FONT: Record<string, string[]> = {
  A: [' ## ', '#  #', '####', '#  #', '#  #'],
  B: ['### ', '#  #', '### ', '#  #', '### '],
  C: [' ###', '#   ', '#   ', '#   ', ' ###'],
  D: ['### ', '#  #', '#  #', '#  #', '### '],
  E: ['####', '#   ', '### ', '#   ', '####'],
  F: ['####', '#   ', '### ', '#   ', '#   '],
  G: [' ###', '#   ', '# ##', '#  #', ' ###'],
  H: ['#  #', '#  #', '####', '#  #', '#  #'],
  I: ['###', ' # ', ' # ', ' # ', '###'],
  J: ['  ##', '   #', '   #', '#  #', ' ## '],
  K: ['#  #', '# # ', '##  ', '# # ', '#  #'],
  L: ['#   ', '#   ', '#   ', '#   ', '####'],
  M: ['#   #', '## ##', '# # #', '#   #', '#   #'],
  N: ['#  #', '## #', '# ##', '#  #', '#  #'],
  O: [' ## ', '#  #', '#  #', '#  #', ' ## '],
  P: ['### ', '#  #', '### ', '#   ', '#   '],
  Q: [' ## ', '#  #', '#  #', '# ##', ' ###'],
  R: ['### ', '#  #', '### ', '# # ', '#  #'],
  S: [' ###', '#   ', ' ## ', '   #', '### '],
  T: ['####', ' #  ', ' #  ', ' #  ', ' #  '],
  U: ['#  #', '#  #', '#  #', '#  #', ' ## '],
  V: ['#  #', '#  #', '#  #', ' ## ', ' ## '],
  W: ['#   #', '#   #', '# # #', '## ##', '#   #'],
  X: ['#  #', ' ## ', ' ## ', ' ## ', '#  #'],
  Y: ['#  #', '#  #', ' ## ', ' #  ', ' #  '],
  Z: ['####', '  # ', ' #  ', '#   ', '####'],
  '0': [' ## ', '#  #', '#  #', '#  #', ' ## '],
  '1': [' # ', '## ', ' # ', ' # ', '###'],
  '2': ['### ', '   #', ' ## ', '#   ', '####'],
  '3': ['### ', '   #', ' ## ', '   #', '### '],
  '4': ['#  #', '#  #', '####', '   #', '   #'],
  '5': ['####', '#   ', '### ', '   #', '### '],
  '6': [' ###', '#   ', '### ', '#  #', ' ###'],
  '7': ['####', '   #', '  # ', ' #  ', ' #  '],
  '8': [' ## ', '#  #', ' ## ', '#  #', ' ## '],
  '9': [' ## ', '#  #', ' ###', '   #', '### '],
  ' ': ['  ', '  ', '  ', '  ', '  '],
  '-': ['    ', '    ', '####', '    ', '    '],
  '.': ['  ', '  ', '  ', '  ', ' #'],
  ':': [' ', '#', ' ', '#', ' '],
  '/': ['   #', '  # ', ' #  ', '#   ', '    '],
};

const ROWS = 5;
const TYPE_MS = 34;
const SWEEP_MS = 26;

/** Text -> ROWS block lines. Unknown glyphs become a blank cell. */
export function bannerRows(text: string): string[] {
  const glyphs = [...text.toUpperCase()].map((c) => FONT[c] ?? FONT[' ']);
  const rows = new Array<string>(ROWS).fill('');
  for (let r = 0; r < ROWS; r++) rows[r] = glyphs.map((g) => g[r]).join(' ');
  return rows;
}

const prefersReduced = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const STYLE: CSSProperties = {
  margin: 0,
  padding: 0,
  fontFamily: 'var(--font-mono,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace)',
  fontSize: '12px',
  lineHeight: 1,
  letterSpacing: 0,
  whiteSpace: 'pre',
  color: 'var(--color-amber,#e0a52c)',
  overflow: 'hidden',
};

export function AsciiBanner({ text = 'MEMO', className }: AsciiBannerProps): ReactElement {
  const ref = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const rows = bannerRows(text);
    const width = rows[0].length;
    const total = Math.max(0, width - 1);

    const paint = (revealed: number, sweep: number) => {
      const out = rows.map((row) => {
        const cut = row.slice(0, revealed);
        if (sweep < 0 || sweep >= cut.length || row[sweep] !== '#') return cut;
        return cut.slice(0, sweep) + '@' + cut.slice(sweep + 1);
      });
      el.textContent = out.join('\n');
    };

    if (prefersReduced()) {
      paint(width, -1);
      return;
    }

    let raf = 0;
    const start = performance.now();
    const step = (now: number) => {
      const t = now - start;
      const revealed = Math.min(width, Math.floor(t / TYPE_MS));
      const sweepStart = total * TYPE_MS + 140;
      const sweep = t < sweepStart ? -1 : Math.floor((t - sweepStart) / SWEEP_MS);
      paint(revealed, sweep);
      if (sweep > revealed) return; // one pass, then stop
      raf = requestAnimationFrame(step);
    };
    paint(0, -1);
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [text]);

  return <pre ref={ref} className={className} aria-label={text} style={STYLE} />;
}
