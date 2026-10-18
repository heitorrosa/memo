// Frame check for the ASCII stage: no test runner, no dependencies.
//   node --experimental-strip-types scripts/frame-check.mjs
// Prints two frames, asserts the layout follows the data, exits non-zero on
// any failed assert.
import assert from 'node:assert/strict';
import { buildFrame, layout, seedPos } from '../src/ascii/grid.ts';

let checks = 0;
const ok = (name) => {
  checks++;
  console.log(`  ok ${checks} - ${name}`);
};

const COLS = 96;
const ROWS = 14;

/* ------------------------------------------------------------- fixture -- */

const nodes = [];
const similar = [];
const edges = [];

for (const [prefix, type] of [
  ['a', 'context'],
  ['b', 'preference'],
]) {
  for (let i = 0; i < 6; i++) {
    nodes.push({ key: `${prefix}${i}`, type, status: 'current', uses: 10 + i, score: 0.5, degree: 2 });
  }
  // a ring of similar pairs: every node close to its two neighbours
  for (let i = 0; i < 6; i++) {
    similar.push({ src_key: `${prefix}${i}`, dst_key: `${prefix}${(i + 1) % 6}`, score: 0.82 });
    similar.push({ src_key: `${prefix}${i}`, dst_key: `${prefix}${(i + 2) % 6}`, score: 0.61 });
  }
}

// two declared edges, one inside a cluster and one across
edges.push({ src_key: 'a0', dst_key: 'a3', rel: 'supports', weight: 1 });
edges.push({ src_key: 'a0', dst_key: 'b0', rel: 'mentions', weight: 1 });

const cell = (frame, key) => frame.hits.find((h) => h.key === key);
const lines = (frame) => frame.text.split('\n');
const at = (frame, row, col) => lines(frame)[row][col];

/* --------------------------------------------------------- determinism -- */

const first = buildFrame(nodes, edges, COLS, ROWS, 0, { similar });
const again = buildFrame(nodes, edges, COLS, ROWS, 0, { similar });
assert.equal(again.text, first.text);
assert.deepEqual(again.overlays, first.overlays);
assert.deepEqual(again.hits, first.hits);
assert.deepEqual(
  layout(nodes, edges, similar),
  layout(nodes, edges, similar),
);
ok('same payload, same frame (text, overlays, hits, layout)');

/* ------------------------------------------------------------ structure -- */

const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const mean = (xs) => xs.reduce((sum, x) => sum + x, 0) / xs.length;
const points = layout(nodes, edges, similar);

const intra = [];
const cross = [];
for (let i = 0; i < 6; i++) {
  for (let j = i + 1; j < 6; j++) {
    intra.push(dist3(points.get(`a${i}`), points.get(`a${j}`)));
    cross.push(dist3(points.get(`a${i}`), points.get(`b${j}`)));
  }
}
assert.ok(mean(intra) < mean(cross) * 0.75, `clusters overlap: ${mean(intra).toFixed(2)} vs ${mean(cross).toFixed(2)}`);
ok(`clusters separate in 3D (mean intra ${mean(intra).toFixed(2)} < cross ${mean(cross).toFixed(2)})`);

// ...and on the grid, at every point of the rotation: a node's nearest
// cluster-mate is nearer than its nearest stranger.
let worst = Infinity;
for (let turn = 0; turn < 16; turn++) {
  const frame = buildFrame(nodes, edges, COLS, ROWS, (turn * Math.PI) / 16, { similar });
  const cells = new Map(frame.hits.map((h) => [h.key, h]));
  const span = (a, b) => {
    const ca = cells.get(a);
    const cb = cells.get(b);
    return Math.hypot(ca.col - cb.col, ca.row - cb.row);
  };
  let nearestIn = 0;
  let nearestOut = 0;
  for (const prefix of ['a', 'b']) {
    const other = prefix === 'a' ? 'b' : 'a';
    for (let i = 0; i < 6; i++) {
      let mine = Infinity;
      let theirs = Infinity;
      for (let j = 0; j < 6; j++) {
        if (j === i) continue;
        mine = Math.min(mine, span(`${prefix}${i}`, `${prefix}${j}`));
        theirs = Math.min(theirs, span(`${prefix}${i}`, `${other}${j}`));
      }
      nearestIn += mine;
      nearestOut += theirs;
    }
  }
  worst = Math.min(worst, nearestOut / nearestIn);
  assert.ok(nearestIn < nearestOut, `clusters overlap on screen at ${((turn * Math.PI) / 16).toFixed(2)} rad`);
}
ok(`neighbours project closer at all 16 rotations (worst ratio ${worst.toFixed(2)}x)`);

/* ------------------------------------------------------------- overlays -- */

for (const layer of first.overlays) {
  const rows = layer.text.split('\n');
  rows.forEach((line, row) => {
    [...line].forEach((ch, col) => {
      if (ch === ' ') return;
      assert.equal(at(first, row, col), ch, `overlay ${layer.role} disagrees at ${row},${col}`);
    });
  });
  ok(`overlay ${layer.role} agrees cell for cell`);
}

/* ----------------------------------------------------------------- focus -- */

const focused = buildFrame(nodes, edges, COLS, ROWS, 0, { similar, focus: 'a0' });
const dim = cell(focused, 'b4');
assert.equal(at(focused, dim.row, dim.col), '.', 'non-neighbour is not dimmed');
const faint = focused.overlays.find((o) => o.role === 'faint');
assert.ok(faint, 'no faint overlay when focused');
assert.equal(faint.text.split('\n')[dim.row][dim.col], '.');
ok('focus dims a non-neighbour to a faint dot');

const lit = cell(focused, 'a1');
assert.notEqual(at(focused, lit.row, lit.col), '.');
ok('focus keeps a neighbour at full strength');

// The focus node's own strands are solid: walk the focus -> neighbour line the
// same way drawEdge samples it and expect at least one claimed cell.
const walk = (from, to) => {
  const dx = to.col - from.col;
  const dy = to.row - from.row;
  const len = Math.max(Math.abs(dx), Math.abs(dy));
  let seen = 0;
  for (let i = 0; i <= len; i++) {
    const col = Math.round(from.col + dx * (i / len));
    const row = Math.round(from.row + dy * (i / len));
    if (i === 0 || i === len) continue;
    if (at(focused, row, col) !== ' ') seen++;
  }
  return seen;
};
let strandCells = 0;
for (const key of ['a1', 'a2']) {
  strandCells = Math.max(strandCells, walk(cell(focused, 'a0'), cell(focused, key)));
}
assert.ok(strandCells > 0, 'focus node has no drawn strand');
ok(`focus node strands drawn (${strandCells} cells on the a0 line)`);

const nearFocus = new Set(['a0']);
for (const l of similar) {
  if (l.src_key === 'a0') nearFocus.add(l.dst_key);
  else if (l.dst_key === 'a0') nearFocus.add(l.src_key);
}
for (const e of edges) {
  if (e.src_key === 'a0') nearFocus.add(e.dst_key);
  else if (e.dst_key === 'a0') nearFocus.add(e.src_key);
}
for (const h of focused.hits) {
  const want = nearFocus.has(h.key) ? 'lit' : 'dim';
  const isDim = at(focused, h.row, h.col) === '.' && faint.text.split('\n')[h.row][h.col] === '.';
  assert.equal(isDim ? 'dim' : 'lit', want, `${h.key} rendered ${isDim ? 'dim' : 'lit'}`);
}
ok(`focus dims ${focused.hits.length - nearFocus.size} nodes and keeps ${nearFocus.size - 1} neighbours lit`);

/* ------------------------------------------------------------- fallback -- */

const legacy = buildFrame(nodes, edges, COLS, ROWS, 0);
const empty = buildFrame(nodes, edges, COLS, ROWS, 0, { similar: [] });
assert.equal(legacy.text, empty.text);
// no similarity data -> the hash seeds, byte for byte
const seeds = new Map(nodes.map((n) => [n.key, seedPos(n.key)]));
const pinned = buildFrame(nodes, edges, COLS, ROWS, 0, { pos: seeds });
assert.equal(legacy.text, pinned.text);
ok('absent similar falls back to the hash seed (no throw, identical frame)');

/* --------------------------------------------------------------- frames -- */

console.log('\n--- 12 nodes, two similar clusters ---');
console.log(first.text);
console.log('\n--- focus a0 ---');
console.log(focused.text);
console.log(`\n${checks} checks passed`);
