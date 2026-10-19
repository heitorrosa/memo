// Frame check for the ASCII stage: no test runner, no dependencies.
//   node --experimental-strip-types scripts/frame-check.mjs
// Live-shaped fixture (137 nodes, 274 similar, 4 declared edges) at 243x22,
// asserts the ink/legibility tune, exits non-zero on any failed assert.
import assert from 'node:assert/strict';
import { buildFrame, layout, seedPos } from '../src/ascii/grid.ts';

let checks = 0;
const ok = (name) => {
  checks++;
  console.log(`  ok ${checks} - ${name}`);
};

const COLS = 243;
const ROWS = 22;
const TOTAL = COLS * ROWS + ROWS - 1;
const MAX_LEN = Math.ceil(ROWS * 1.2);

/* ------------------------------------------------------------- fixture -- */
// Live shape: 137 nodes (preference 21, context 102, feedback 10, analysis 4),
// similarity links as a per-type ring + skip, 4 declared edges.

const nodes = [];
const members = { preference: [], context: [], feedback: [], analysis: [] };
for (const [type, m] of [['preference', 21], ['context', 102], ['feedback', 10], ['analysis', 4]]) {
  for (let i = 0; i < m; i++) {
    const key = `${type[0]}${nodes.length}`;
    members[type].push(key);
    nodes.push({
      key,
      type,
      status: type === 'analysis' && i === 0 ? 'superseded' : 'current',
      uses: (i * 5) % 40,
      score: 0.3 + ((i * 7) % 10) / 14,
      degree: 1 + (i % 4),
    });
  }
}

const similar = [];
for (const keys of Object.values(members)) {
  for (let i = 0; i < keys.length; i++) {
    similar.push({ src_key: keys[i], dst_key: keys[(i + 1) % keys.length], score: 0.82 });
    similar.push({ src_key: keys[i], dst_key: keys[(i + 2) % keys.length], score: 0.61 });
  }
}

const edges = [
  { src_key: members.context[0], dst_key: members.context[30], rel: 'supports', weight: 1 },
  { src_key: members.context[1], dst_key: members.context[60], rel: 'mentions', weight: 1 },
  { src_key: members.preference[0], dst_key: members.preference[8], rel: 'derived_from', weight: 1 },
  { src_key: members.context[2], dst_key: members.feedback[3], rel: 'contradicts', weight: 1 },
];

const OPTS = { similar };
const cell = (frame, key) => frame.hits.find((h) => h.key === key);
const lines = (frame) => frame.text.split('\n');
const at = (frame, row, col) => lines(frame)[row][col];

/** Per-frame ink stats from the frame + hits alone. */
function stats(frame) {
  const grid = lines(frame);
  const nodeCells = new Set(frame.hits.map((h) => h.row * COLS + h.col));
  let nonSpace = 0;
  let glyph = 0;
  let halo = 0;
  let strand = 0;
  let longest = 0;
  for (let row = 0; row < ROWS; row++) {
    let run = 0;
    for (let col = 0; col < COLS; col++) {
      const ch = grid[row][col];
      if (ch === ' ') { run = 0; continue; }
      nonSpace++;
      run++;
      longest = Math.max(longest, run);
      const i = row * COLS + col;
      if (nodeCells.has(i)) glyph++;
      else if (ch === ':') halo++;
      else strand++;
    }
  }
  for (let col = 0; col < COLS; col++) {
    let run = 0;
    for (let row = 0; row < ROWS; row++) {
      if (grid[row][col] === ' ') { run = 0; continue; }
      run++;
      longest = Math.max(longest, run);
    }
  }
  const median = (xs) => {
    const s = [...xs].sort((a, b) => a - b);
    return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
  };
  return {
    occupancy: nonSpace / TOTAL,
    node: glyph,
    halo,
    strand,
    longest,
    medCol: median(frame.hits.map((h) => h.col)),
    medRow: median(frame.hits.map((h) => h.row)),
  };
}

/* --------------------------------------------------------- determinism -- */

const first = buildFrame(nodes, edges, COLS, ROWS, 0, OPTS);
const again = buildFrame(nodes, edges, COLS, ROWS, 0, OPTS);
assert.equal(again.text, first.text);
assert.deepEqual(again.overlays, first.overlays);
assert.deepEqual(again.hits, first.hits);
assert.deepEqual(layout(nodes, edges, similar), layout(nodes, edges, similar));
ok('same payload, same frame (text, overlays, hits, layout)');

/* ------------------------------------------------------------ structure -- */

const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const mean = (xs) => xs.reduce((sum, x) => sum + x, 0) / xs.length;
const points = layout(nodes, edges, similar);

const intra = [];
const cross = [];
const types = Object.keys(members);
const swap = (t) => types[(types.indexOf(t) + 1) % types.length];
for (const t of types) {
  for (let i = 0; i < members[t].length; i++) {
    for (let j = i + 1; j < members[t].length; j++) {
      intra.push(dist3(points.get(members[t][i]), points.get(members[t][j])));
    }
    for (const u of types) {
      if (u === t) continue;
      const other = members[u][Math.min(i, members[u].length - 1)];
      cross.push(dist3(points.get(members[t][i]), points.get(other)));
    }
  }
}
const intraMean = mean(intra);
const crossMean = mean(cross);
assert.ok(intraMean < crossMean * 0.75, `clusters overlap: ${intraMean.toFixed(2)} vs ${crossMean.toFixed(2)}`);
ok(`clusters separate in 3D (intra ${intraMean.toFixed(2)} vs cross ${crossMean.toFixed(2)})`);

const span2 = (frame, a, b) => {
  const ca = cell(frame, a);
  const cb = cell(frame, b);
  return Math.hypot(ca.col - cb.col, ca.row - cb.row);
};
let worst = Infinity;
const sampleA = members.preference.slice(0, 12);
for (let turn = 0; turn < 16; turn++) {
  const frame = buildFrame(nodes, edges, COLS, ROWS, (turn * Math.PI) / 16, OPTS);
  let nearestIn = 0;
  let nearestOut = 0;
  for (const t of ['preference', 'context']) {
    const other = swap(t);
    for (let i = 0; i < sampleA.length; i++) {
      const key = members[t][i];
      if (!cell(frame, key)) continue;
      let mine = Infinity;
      let theirs = Infinity;
      for (let j = 0; j < members[t].length; j++) {
        if (j === i) continue;
        mine = Math.min(mine, span2(frame, key, members[t][j]));
      }
      for (let j = 0; j < members[other].length; j += 3) {
        theirs = Math.min(theirs, span2(frame, key, members[other][j]));
      }
      nearestIn += mine;
      nearestOut += theirs;
    }
  }
  worst = Math.min(worst, nearestOut / nearestIn);
  assert.ok(nearestIn < nearestOut, `clusters overlap on screen at ${((turn * Math.PI) / 16).toFixed(2)} rad`);
}
ok(`neighbours project closer at all 16 rotations (worst ratio ${worst.toFixed(2)}x)`);

/* ------------------------------------------------------------- geometry -- */

assert.equal(first.text.length, TOTAL);
for (const layer of first.overlays) {
  assert.equal(layer.text.length, TOTAL);
  const rows = layer.text.split('\n');
  rows.forEach((line, row) => {
    [...line].forEach((ch, col) => {
      if (ch === ' ') return;
      assert.equal(at(first, row, col), ch, `overlay ${layer.role} disagrees at ${row},${col}`);
    });
  });
}
ok(`frame + ${first.overlays.length} overlays are exact ${COLS}x${ROWS} layers, cell for cell`);
assert.equal(first.sparse, false, 'live frame must not get the backdrop');
assert.equal(first.hits.length, nodes.length);
assert.equal(new Set(first.hits.map((h) => `${h.col},${h.row}`)).size, first.hits.length, 'hit cells collide');
ok('sparse stays off and hit cells are unique');

/* --------------------------------------------------- centring + ink tune -- */

let report = 'angle  occ%   node halo strand  longest  medCol/Row\n';
for (const angle of [0, 1, 2]) {
  const frame = buildFrame(nodes, edges, COLS, ROWS, angle, OPTS);
  const s = stats(frame);
  assert.equal(frame.sparse, false);
  assert.ok(s.occupancy >= 0.06 && s.occupancy <= 0.12, `angle ${angle}: occupancy ${(s.occupancy * 100).toFixed(1)}% outside 6-12%`);
  assert.ok(s.strand < s.node + s.halo, `angle ${angle}: strand ${s.strand} >= node ink ${s.node + s.halo}`);
  assert.ok(s.longest <= MAX_LEN, `angle ${angle}: run ${s.longest} > ${MAX_LEN}`);
  assert.ok(Math.abs(s.medCol - (COLS - 1) / 2) <= 4, `angle ${angle}: median col ${s.medCol} off centre`);
  assert.ok(Math.abs(s.medRow - (ROWS - 1) / 2) <= 3, `angle ${angle}: median row ${s.medRow} off centre`);
  report += `${angle}     ${(s.occupancy * 100).toFixed(1)}  ${String(s.node).padStart(4)} ${String(s.halo).padStart(4)} ${String(s.strand).padStart(6)}  ${String(s.longest).padStart(7)}  ${s.medCol}/${s.medRow}\n`;
}
console.log(`\n  ink at angles 0/1/2:\n${report}`);
ok('occupancy 6-12%, strand ink < node ink, runs <= 26, mass centred at 0/1/2');

/* ----------------------------------------------------------------- focus -- */

const focusKey = members.context[0];
const focused = buildFrame(nodes, edges, COLS, ROWS, 0, { ...OPTS, focus: focusKey });
const farKey = members.preference[10];
const dim = cell(focused, farKey);
assert.equal(at(focused, dim.row, dim.col), '.', 'non-neighbour is not dimmed');
const faint = focused.overlays.find((o) => o.role === 'faint');
assert.ok(faint, 'no faint overlay when focused');
assert.equal(faint.text.split('\n')[dim.row][dim.col], '.');
ok('focus dims a non-neighbour to a faint dot');

const lit = cell(focused, members.context[1]);
assert.notEqual(at(focused, lit.row, lit.col), '.');
ok('focus keeps a neighbour at full strength');

// The focus node's own strands are solid: walk the sampled focus->neighbour
// line and expect at least one claimed cell beyond the two glyphs.
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
for (const l of similar.filter((l) => l.src_key === focusKey || l.dst_key === focusKey).slice(0, 8)) {
  const otherKey = l.src_key === focusKey ? l.dst_key : l.src_key;
  const other = cell(focused, otherKey);
  if (other) strandCells = Math.max(strandCells, walk(cell(focused, focusKey), other));
}
assert.ok(strandCells > 0, 'focus node has no drawn strand');
ok(`focus node strands drawn (${strandCells} cells on a focus line)`);

/* ------------------------------------------------------------- fallback -- */

const legacy = buildFrame(nodes, edges, COLS, ROWS, 0);
const empty = buildFrame(nodes, edges, COLS, ROWS, 0, { similar: [] });
assert.equal(legacy.text, empty.text);
// no similarity data -> the hash seeds, byte for byte
const seeds = new Map(nodes.map((n) => [n.key, seedPos(n.key)]));
const pinned = buildFrame(nodes, edges, COLS, ROWS, 0, { pos: seeds });
assert.equal(legacy.text, pinned.text);
ok('absent similar falls back to the hash seed (no throw, identical frame)');

/* ---------------------------------------------------- sparse + rotation -- */

const bare = buildFrame(nodes, [], COLS, ROWS, 0);
assert.equal(bare.sparse, true, 'no strands, no edges -> backdrop must draw');
assert.ok(bare.text.includes(':'), 'backdrop hairlines missing');
ok('sparse mode: nothing survives -> dust + hairlines, honestly marked');

const angles = [0, 1, 2, 3, 4, 5, 6];
const frames = angles.map((a) => buildFrame(nodes, edges, COLS, ROWS, a, OPTS).text);
assert.equal(new Set(frames).size, 7, `only ${new Set(frames).size} distinct frames over 7 angles`);
ok('7 angles -> 7 distinct frames (incl. pairwise-distinct 0/1/2)');

/* --------------------------------------------------------------- frames -- */

console.log(`\n--- angle 0 (243x22) ---`);
console.log(frames[0]);
console.log(`\n--- angle 1 ---`);
console.log(frames[1]);
console.log(`\n--- angle 2 ---`);
console.log(frames[2]);
console.log(`\n${checks} checks passed`);
