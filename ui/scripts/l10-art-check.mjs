// Scratch check for L10-login-art. Run from ui/: node --experimental-strip-types scripts/l10-art-check.mjs
import { LOGO_MASK, MASK_COLS, MASK_ROWS, renderFrame } from '../src/ascii/logo-mask.ts';

const COLS = 124;
const ROWS = 41;
const phases = [0, 1.2, 2.7];

const frames = phases.map((p) => renderFrame(p, COLS, ROWS));

for (let i = 0; i < frames.length; i++) {
  console.log(`\n=== phase ${phases[i]} ===`);
  console.log(frames[i].text);
  console.log(`hot cells: ${[...frames[i].hot].filter((c) => c !== ' ' && c !== '\n').length}`);
}

const diff = (a, b) => [...a].reduce((n, c, i) => n + (c !== b[i] ? 1 : 0), 0);

console.log('\n=== checks ===');
console.log(`mask rows: ${MASK_ROWS} (expect 5), mask cols: ${MASK_COLS}, ragged: ${LOGO_MASK.some((r) => r.length !== MASK_COLS)}`);
console.log(`output width: ${frames.map((f) => f.text.split('\n')[0].length).join('/')} cols, viewport cols: ${COLS}`);
console.log(`output height: ${frames.map((f) => f.text.split('\n').length).join('/')} rows, viewport rows: ${ROWS}`);
console.log(`grid cells: ${COLS * ROWS} (budget 6000), ink cells: ${frames.map((f) => f.ink).join('/')}`);
console.log(`diff 0->1.2: ${diff(frames[0].text, frames[1].text)} cells | diff 1.2->2.7: ${diff(frames[1].text, frames[2].text)} cells | diff 0->2.7: ${diff(frames[0].text, frames[2].text)} cells`);
console.log(`diff(0,1.2) hot: ${diff(frames[0].hot, frames[1].hot)} cells`);
console.log(`shape present in all three: ${frames.every((f) => f.ink > 0 && f.text.includes('#'))}`);
console.log(`settle: reveal 0 ink=${renderFrame(0, COLS, ROWS, 0).ink}, reveal 0.5 ink=${renderFrame(0, COLS, ROWS, 0.5).ink}, reveal 1 ink=${renderFrame(0, COLS, ROWS, 1).ink}`);
